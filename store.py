"""Hub-side SQLite store: devices, sessions, session_events, audit_log,
session_usage, cost_daily, alerts.

One writer thread drains a queue.Queue (SQLite/WAL allows one writer at a
time; funneling every write through one thread avoids "database is locked"
under concurrency). Reads use short-lived per-thread connections opened
directly against the file -- safe to read concurrently with the writer
under WAL. Schema creation is idempotent (CREATE TABLE IF NOT EXISTS).

No lock is ever held while sleeping/blocking: the writer thread owns its
own connection exclusively and callers block only on a per-call
threading.Event, not on any shared mutex.
"""
import json
import logging
import math
import os
import queue
import sqlite3
import threading
import time

_LOG = logging.getLogger(__name__)


def _valid_started_at(value):
    """True if `value` is a usable started_at: a finite, positive number.
    0, negative, NaN and inf (and None, and non-numbers) are all "not
    set" and must never be treated as a real timestamp.
    Never raises: math.isfinite() itself raises OverflowError on an int
    with roughly 308+ digits (too large to convert to a C double), which
    is caught here rather than left to escape upsert_sessions."""
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _coerce_number(value):
    """Best-effort coercion of a device-reported numeric field (token
    counts, effective cost, a transcript timestamp) to a plain int/float,
    or None if it cannot be trusted at all. Fix round 2 (review Important
    2): this hub trusts nothing that crosses the device/network boundary --
    upsert_session_usage and upsert_cost_daily run every numeric field
    through this before it ever reaches a query, the same way
    _valid_started_at gates started_at.

    A real int/float is accepted if finite (NaN/+-inf rejected -- Python's
    own json.loads happily parses the bare tokens `NaN`/`Infinity` by
    default, so "arrived via json.loads" does not imply "is a real
    number"). A numeric-looking string ("123", "12.5") is coerced, since
    JSON has no separate "numeric string" type worth punishing a device
    for. bool is rejected even though it's technically an int subclass --
    `True`/`False` are never a legitimate token count. Anything else
    (None, a list, a dict, a non-numeric string) returns None.

    Never raises: like _valid_started_at, math.isfinite() itself can raise
    OverflowError on an integer too large to convert to a C double, caught
    here rather than left to blow up the write."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = value
    elif isinstance(value, str):
        try:
            num = int(value)
        except ValueError:
            try:
                num = float(value)
            except ValueError:
                return None
    else:
        return None
    try:
        return num if math.isfinite(num) else None
    except OverflowError:
        return None


# A row can go missing from a single poll's `rows` without the underlying
# session actually having stopped -- e.g. `claude agents --json` timing out
# (agents._fetch_rows returns [] on a timeout) drops every external row for
# that poll, which the end-of-sweep below marks ended_at=now even though
# nothing really ended. If the very next poll(s) also report an invalid
# started_at (an older `claude` build simply omitting startedAt, say), a
# recently-ended EXTERNAL row's own started_at is still the honest answer
# -- it's the same session flickering, not a new one reusing the id. A row
# that's been ended for longer than this grace window IS treated as a new
# session (see upsert_sessions). This is a FLOOR, not the effective value:
# upsert_sessions takes a `grace_seconds` argument so the real value stays
# coupled to the caller's actual poll interval (see fleetpoll.py, which
# passes max(ENDED_ROW_GRACE_SECONDS, 3 * self.interval) -- a
# longer-than-default interval must widen the window, or a single missed
# poll would silently disable the fix). 90s is 3x fleetpoll's own default
# 30s interval: a couple of missed polls' worth of jitter/backoff without
# resurrecting a genuinely dead session's clock onto its replacement.
ENDED_ROW_GRACE_SECONDS = 90


class StoreClosed(RuntimeError):
    """Raised by any Store call made after close() (or racing it), so
    callers can distinguish "the store is shut down" from any other
    RuntimeError a write might raise."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, name TEXT, role TEXT, version TEXT,
    claude_version TEXT, last_seen REAL, online INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    device_id TEXT, session_id TEXT, name TEXT, cwd TEXT, kind TEXT,
    state TEXT, started_at REAL, ended_at REAL, last_seen REAL,
    external INTEGER DEFAULT 0,
    PRIMARY KEY (device_id, session_id)
);
CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, session_id TEXT,
    ts REAL, event TEXT, extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_device ON session_events(device_id, ts);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT,
    target TEXT, device_id TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS session_usage (
    device_id TEXT, session_id TEXT,
    input INTEGER, cache_read INTEGER, cache_write INTEGER,
    output INTEGER, effective INTEGER,
    last_ts REAL, updated_at REAL,
    PRIMARY KEY (device_id, session_id)
);
CREATE TABLE IF NOT EXISTS cost_daily (
    device_id TEXT, day TEXT, project TEXT,
    input INTEGER, cache_read INTEGER, cache_write INTEGER,
    output INTEGER, effective INTEGER, updated_at REAL,
    PRIMARY KEY (device_id, day, project)
);
CREATE INDEX IF NOT EXISTS idx_cost_daily_day ON cost_daily(day);
CREATE TABLE IF NOT EXISTS alerts (
    device_id TEXT, session_id TEXT, rule TEXT,
    severity TEXT, message TEXT, value REAL, threshold REAL,
    since REAL, first_seen REAL, last_seen REAL,
    target_type TEXT, name TEXT,
    PRIMARY KEY (device_id, session_id, rule)
);
"""
# session_usage, cost_daily and alerts (Phase 3 wiring, CONTRACT.md section
# 2) were brand-new tables when this comment was first written, so CREATE
# TABLE IF NOT EXISTS above was safe on its own. `alerts`'s target_type/name
# (fix round 1) are in the CREATE TABLE above AND behind the additive
# migration below (fix round 2, review Minor 3): CONTRACT.md's DDL is
# binding and another lane reads it, so it must show the true end state for
# a genuinely fresh database, not rely on the migration alone -- but any
# hub.db that already ran a store.py from between the original 10-column
# alerts table and fix round 1 has the table without these columns, and
# CREATE TABLE IF NOT EXISTS is a no-op against it, so the migration is
# still required for that case. Both together, run unconditionally on every
# init, is idempotent and strictly better than either alone.

# session_events natural key: (device_id, session_id, ts, event). A poller
# cursor rewind re-submits the same rows; INSERT OR IGNORE against this
# unique index makes that a no-op instead of duplicating rows. Applied as a
# separate migration step (not in _SCHEMA) because an existing DB may
# already have duplicate rows that must be removed first, or the unique
# index creation would fail.
_DEDUP_EVENTS = """
DELETE FROM session_events WHERE id NOT IN (
    SELECT MIN(id) FROM session_events
    GROUP BY device_id, session_id, ts, event
);
"""
_UNIQUE_EVENTS_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique "
    "ON session_events(device_id, session_id, ts, event);"
)

# v2.1.6/v2.1.7 UI actions (Preview, Keys, Stop for external sessions,
# Enable RC, Open on claude.ai) need these fields alongside the
# identity/state columns already in _SCHEMA. Added as an additive
# migration (ALTER TABLE ADD COLUMN, only when absent) rather than folded
# into _SCHEMA so an existing hub.db predating this change gains the
# columns in place -- CREATE TABLE IF NOT EXISTS is a no-op against it, so
# these columns would otherwise never appear.
_NEW_SESSION_COLUMNS = (
    ("pid", "INTEGER"),
    ("tmux", "TEXT"),        # JSON: {"session_name": ..., "pane_id": ...} or NULL
    ("rc_url", "TEXT"),
    ("tokens", "INTEGER"),
    ("claude", "TEXT"),      # JSON: the claude agents sub-object, or NULL
    # The row's raw status (e.g. "busy"/"idle"/"dead"), distinct from the
    # `state` column which holds the DERIVED state (needs_attention
    # outranks busy). Persisted so a stall-detection rule can later tell a
    # session that is wedged-while-busy from one that is genuinely idle --
    # nothing reads this column yet.
    ("status", "TEXT"),
)


def _migrate_session_columns(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    for col, coltype in _NEW_SESSION_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {coltype}")


# Fix round 1 (CONTRACT.md amendment): an alert is a record of what was
# observed at the moment it fired. The session it names may well have
# ended, or the device gone offline, by the time anyone reads the alerts
# table -- a runaway session that has since died is exactly the case that
# matters most. Resolving `name`/`target_type` via a join against
# `sessions`/`devices` at read time would lose that identity in precisely
# that case, so both are stored directly on the row instead. Additive
# (ALTER TABLE ADD COLUMN, only when absent) rather than folded into the
# `alerts` CREATE TABLE above, because any hub.db that ran a store.py from
# between the original alerts table (no target_type/name) and this fix
# already has the table -- CREATE TABLE IF NOT EXISTS is a no-op against
# it, so these columns would otherwise never appear there.
_NEW_ALERT_COLUMNS = (
    ("target_type", "TEXT"),  # "session" or "device", from the finding itself
    ("name", "TEXT"),         # the session/device name at the moment the finding fired
)


def _migrate_alert_columns(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    for col, coltype in _NEW_ALERT_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {col} {coltype}")


def _json_or_none(value):
    return None if value is None else json.dumps(value)


def _parse_json_or_none(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


class _Write:
    __slots__ = ("fn", "args", "kwargs", "result", "error", "done")

    def __init__(self, fn, args, kwargs):
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.result, self.error, self.done = None, None, threading.Event()


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        # Create the file with 0600 perms up front, before any connection
        # writes to it, so no window exists where the DB is world/group
        # readable.
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(db_path, 0o600)

        init_conn = self._connect()
        try:
            init_conn.executescript(_SCHEMA)
            init_conn.commit()
            _migrate_session_columns(init_conn)
            _migrate_alert_columns(init_conn)
            init_conn.commit()
            # De-duplicate any pre-existing rows (from a DB created before
            # this unique index existed) before creating the index, then
            # create it idempotently.
            init_conn.executescript(_DEDUP_EVENTS)
            init_conn.execute(_UNIQUE_EVENTS_INDEX)
            init_conn.commit()
        finally:
            init_conn.close()

        self._q = queue.Queue()
        self._stop = threading.Event()
        self._closed = False
        self._close_error = None
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _writer_loop(self):
        # This connection is used ONLY on this thread -- never shared.
        conn = self._connect()
        try:
            while True:
                try:
                    item = self._q.get(timeout=0.5)
                except queue.Empty:
                    if self._stop.is_set():
                        break
                    continue
                if item is None:
                    break
                try:
                    item.result = item.fn(conn, *item.args, **item.kwargs)
                    conn.commit()
                except Exception as e:  # reported back to the calling thread
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _LOG.exception(
                        "store write failed: %s", getattr(item.fn, "__qualname__", item.fn))
                    item.error = e
                finally:
                    item.done.set()
        finally:
            conn.close()
            # Fail anything still queued behind the sentinel (submitted
            # concurrently with close(), or left over if the loop exited
            # early) so no caller blocks for the full _write timeout.
            drain_error = self._close_error or StoreClosed("store is closed")
            while True:
                try:
                    leftover = self._q.get_nowait()
                except queue.Empty:
                    break
                if leftover is None:
                    continue
                leftover.error = drain_error
                leftover.done.set()

    def _write(self, fn, *args, **kwargs):
        if self._closed:
            raise StoreClosed("store is closed")
        item = _Write(fn, args, kwargs)
        self._q.put(item)
        if not item.done.wait(timeout=10):
            raise TimeoutError("store write timed out (writer thread crashed?)")
        if item.error is not None:
            raise item.error
        return item.result

    def _read_conn(self):
        # Short-lived, per-call connection -- never shared across threads,
        # never held across a call boundary.
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    # -- writes --------------------------------------------------------

    def upsert_device(self, row, now_fn=time.time):
        def _do(conn):
            conn.execute(
                "INSERT INTO devices (id, name, role, version, claude_version, last_seen, online) "
                "VALUES (?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role, "
                "version=excluded.version, claude_version=excluded.claude_version, "
                "last_seen=excluded.last_seen, online=1",
                (row["id"], row.get("name", row["id"]), row.get("role", "full"),
                 row.get("version"), row.get("claude_version"), now_fn()))
        return self._write(_do)

    def mark_device_offline(self, device_id):
        def _do(conn):
            conn.execute("UPDATE devices SET online=0 WHERE id=?", (device_id,))
        return self._write(_do)

    def upsert_sessions(self, device_id, rows, now_fn=time.time,
                         grace_seconds=ENDED_ROW_GRACE_SECONDS):
        """Replace this device's live-session view: rows present are
        upserted; rows in the DB for this device but absent from `rows`
        get ended_at set, exactly once (only rows that were still live,
        ended_at IS NULL, transition).

        `grace_seconds` is how long a recently-ended EXTERNAL row's
        started_at stays trustworthy -- see below. Defaults to
        ENDED_ROW_GRACE_SECONDS (a floor); the real caller (fleetpoll.py)
        passes max(ENDED_ROW_GRACE_SECONDS, 3 * its own poll interval) so
        a longer-than-default interval can't silently disable the fix.

        started_at resolution (reported = r's started_at; existing = the
        started_at of a stored row for this (device_id, session_id), only
        trusted per the ended-row rule below; both run through
        _valid_started_at first):
        - reported valid: reported WINS OUTRIGHT, whether it's earlier or
          later than whatever is already stored. This is deliberate, not
          a min()/earliest-of-two rule (that rule was tried and rejected
          at the device layer in agents.py/sessions.py, and reapplying it
          here -- across polls instead of across sources -- reintroduces
          the exact same bug: restart-in-place reuses RC_SESSION_ID, so
          the row never goes through "ended" between the restart and the
          next poll, and a stale stored value would otherwise permanently
          outrank the new session's own, correct, much more recent
          started_at. The reported value comes from claude's own
          startedAt, which is stable per session, so trusting it outright
          is the right side to err on: the alternative (under-reporting a
          session's age for one poll if a device ever reports a jittery
          timestamp) is far cheaper than a false runaway flag or an
          unwanted kill from a stale value that never gets corrected.
        - reported invalid, existing valid AND trustworthy: existing,
          unchanged. This is the backfill path that repairs a row stored
          before this field existed (or before a real timestamp was ever
          reported) -- it stays correct because it is reached only when
          THIS poll didn't carry a usable value.
        - reported invalid, no trustworthy existing value: now.

        "Trustworthy existing" means a row that is either still live
        (ended_at IS NULL, any kind), or is EXTERNAL and was ended within
        `grace_seconds` of `now`. A row can go missing from a single
        poll's `rows` without the underlying session having actually
        stopped (a `claude agents --json` timeout drops every external
        row for that poll -- see agents._fetch_rows), which the
        end-of-sweep below marks ended_at=now even though nothing really
        ended; if the very next poll(s) also report an invalid
        started_at, the recently-ended row's own started_at is still the
        honest answer for the SAME session flickering. This grace window
        applies only to external rows: a launcher (tmux-derived) row does
        not go missing from `rows` the way an external row does (tmux
        list-sessions either shows it or it's genuinely gone), so a
        launcher row reported with no usable started_at right after
        ending is presumed to be a genuinely new session -- inheriting a
        stale launcher row's started_at even for a few seconds would be
        exactly the false-runaway/false-kill risk this whole feature
        exists to avoid, which the grace window must never reintroduce.
        Any row (external or not) ended longer ago than the grace window
        is treated as a genuinely new session reusing the id (the common
        case: a synthetic tmux:<name> id reassigned once a tmux session
        of that name is recreated) and gets its own clock -- its stale
        started_at must not be inherited, whether kept outright or (as it
        was before this fix) min()'d against a real reported value.

        A row without `session_id` is not a valid natural key (falling back
        to `name` risks colliding two distinct sessions, or an accidental
        rename ending one and starting another) -- such rows are skipped
        entirely: not upserted, and not counted as "seen" for the end-of-
        session sweep. Returns {"skipped": <count>}.

        Note: two rows sharing a session_id within the SAME `rows` batch
        (nothing produces this today -- every real caller keys its rows by
        session_id upstream) resolve in list order, last one wins, since
        "reported wins outright" makes each duplicate's INSERT/ON CONFLICT
        independent of the others. This is intentionally not min()'d back
        to order-independence; see fix round 3 notes for why."""
        def _do(conn):
            now = now_fn()
            seen_ids = set()
            skipped = 0
            for r in rows:
                sid = r.get("session_id")
                if not sid:
                    skipped += 1
                    _LOG.warning(
                        "upsert_sessions: skipping row for device %r with no session_id: %r",
                        device_id, r.get("name"))
                    continue
                seen_ids.add(sid)
                reported_started = r.get("started_at")
                reported_valid = reported_started if _valid_started_at(reported_started) else None
                if reported_valid is not None:
                    started_at = reported_valid
                else:
                    existing = conn.execute(
                        "SELECT started_at, ended_at, external FROM sessions "
                        "WHERE device_id=? AND session_id=?",
                        (device_id, sid)).fetchone()
                    trustworthy = existing is not None and (
                        existing["ended_at"] is None
                        or (existing["external"]
                            and (now - existing["ended_at"]) <= grace_seconds)
                    )
                    existing_started = existing["started_at"] if trustworthy else None
                    existing_valid = (
                        existing_started if _valid_started_at(existing_started) else None
                    )
                    started_at = existing_valid if existing_valid is not None else now
                conn.execute(
                    "INSERT INTO sessions (device_id, session_id, name, cwd, kind, state, "
                    "started_at, ended_at, last_seen, external, pid, tmux, rc_url, tokens, "
                    "claude, status) VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(device_id, session_id) DO UPDATE SET name=excluded.name, "
                    "cwd=excluded.cwd, kind=excluded.kind, state=excluded.state, "
                    "started_at=excluded.started_at, "
                    "last_seen=excluded.last_seen, ended_at=NULL, external=excluded.external, "
                    "pid=excluded.pid, tmux=excluded.tmux, rc_url=excluded.rc_url, "
                    "tokens=excluded.tokens, claude=excluded.claude, status=excluded.status",
                    (device_id, sid, r.get("name"), r.get("cwd"), r.get("kind"),
                     r.get("state"), started_at, now, int(bool(r.get("external"))),
                     r.get("pid"), _json_or_none(r.get("tmux")), r.get("rc_url"),
                     r.get("tokens"), _json_or_none(r.get("claude")), r.get("status")))
            existing_ids = [row["session_id"] for row in conn.execute(
                "SELECT session_id FROM sessions WHERE device_id=? AND ended_at IS NULL",
                (device_id,))]
            for sid in existing_ids:
                if sid not in seen_ids:
                    conn.execute(
                        "UPDATE sessions SET ended_at=? WHERE device_id=? AND session_id=? AND ended_at IS NULL",
                        (now, device_id, sid))
            return {"skipped": skipped}
        return self._write(_do)

    def add_events(self, device_id, rows):
        """Insert events, ignoring exact duplicates of an existing row on
        (device_id, session_id, ts, event) -- makes a poller cursor rewind
        that resubmits an already-seen batch a no-op instead of duplicating
        rows.

        A row without `session_id` is rejected (not silently inserted as
        NULL): the unique index above treats NULL as distinct from every
        other NULL (SQL NULL != NULL), so such rows would never dedupe and
        would pile up forever on every cursor replay."""
        def _do(conn):
            skipped = 0
            for r in rows:
                sid = r.get("session_id")
                if not sid:
                    skipped += 1
                    _LOG.warning(
                        "add_events: rejecting event for device %r with no session_id: %r",
                        device_id, r.get("event"))
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO session_events "
                    "(device_id, session_id, ts, event, extra_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (device_id, sid, r.get("ts"), r.get("event"),
                     json.dumps(r.get("extra") or {})))
            return {"skipped": skipped}
        return self._write(_do)

    def add_audit(self, actor, action, target, device_id, detail="", now_fn=time.time):
        def _do(conn):
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, target, device_id, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_fn(), actor, action, target, device_id, detail))
        return self._write(_do)

    # Numeric fields validated/coerced by _coerce_number before either
    # upsert below writes a row (fix round 2, review Important 2): a device
    # payload arrives over a network and is untrusted like any other input
    # crossing that boundary, and cost_view sums these fields with plain
    # Python arithmetic -- one un-coerced bad value (a string, None, NaN,
    # a list) reaching the table would raise out of cost_view/fleet_view
    # for every caller, indefinitely, since the bad row persists until
    # someone edits the database by hand.
    _USAGE_NUMERIC_FIELDS = ("input", "cache_read", "cache_write", "output",
                              "effective", "last_ts")
    _COST_NUMERIC_FIELDS = ("input", "cache_read", "cache_write", "output", "effective")

    def upsert_session_usage(self, device_id, rows, now_fn=time.time):
        """Upsert per-session token-usage totals for `device_id` (usage.py's
        per-session accounting, one row per live/known session). Each row
        in `rows` is a dict with session_id, input, cache_read,
        cache_write, output, effective, last_ts.

        A row with no session_id is skipped and logged, matching
        upsert_sessions: session_id is the natural key here too (paired
        with device_id) and there is no safe fallback key. A row where any
        of the numeric fields fails to coerce via _coerce_number (missing,
        None, NaN, +/-inf, or a non-numeric type) is likewise skipped and
        logged in full, not written with a partial/zeroed value."""
        def _do(conn):
            now = now_fn()
            skipped = 0
            for r in rows:
                sid = r.get("session_id")
                if not sid:
                    skipped += 1
                    _LOG.warning(
                        "upsert_session_usage: skipping row for device %r with no session_id: %r",
                        device_id, r)
                    continue
                coerced = {f: _coerce_number(r.get(f)) for f in self._USAGE_NUMERIC_FIELDS}
                if any(v is None for v in coerced.values()):
                    skipped += 1
                    _LOG.warning(
                        "upsert_session_usage: skipping row for device %r session %r "
                        "with a non-numeric field: %r", device_id, sid, r)
                    continue
                conn.execute(
                    "INSERT INTO session_usage (device_id, session_id, input, cache_read, "
                    "cache_write, output, effective, last_ts, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(device_id, session_id) DO UPDATE SET "
                    "input=excluded.input, cache_read=excluded.cache_read, "
                    "cache_write=excluded.cache_write, output=excluded.output, "
                    "effective=excluded.effective, last_ts=excluded.last_ts, "
                    "updated_at=excluded.updated_at",
                    (device_id, sid, coerced["input"], coerced["cache_read"],
                     coerced["cache_write"], coerced["output"], coerced["effective"],
                     coerced["last_ts"], now))
            return {"skipped": skipped}
        return self._write(_do)

    def upsert_cost_daily(self, device_id, rows, now_fn=time.time):
        """Replace (never accumulate) this device's per-project daily cost
        totals. Each row in `rows` is a dict with day, project, input,
        cache_read, cache_write, output, effective. The device reports
        CUMULATIVE totals for a given day on every poll, so this upsert
        overwrites the stored values for (device_id, day, project) rather
        than adding to them -- adding would multiply every number by the
        poll count.

        A row with no `day` is skipped and logged: (device_id, day,
        project) is the natural key, and unlike session_id above, SQLite
        does not even reject a NULL day here (PRIMARY KEY alone does not
        imply NOT NULL for non-INTEGER columns), so an unguarded NULL
        would silently pile up its own duplicate rows instead of erroring
        -- skip-and-log makes the failure visible instead. `project`
        missing/empty is valid (CONTRACT.md: the empty string when the
        device did not report one, e.g. metadata role) and is stored as
        "" rather than skipped. A row where any of the numeric fields
        fails to coerce via _coerce_number is skipped and logged in full,
        same as upsert_session_usage above -- see that method's docstring
        and _coerce_number itself for what counts as coercible."""
        def _do(conn):
            now = now_fn()
            skipped = 0
            for r in rows:
                day = r.get("day")
                if not day:
                    skipped += 1
                    _LOG.warning(
                        "upsert_cost_daily: skipping row for device %r with no day: %r",
                        device_id, r)
                    continue
                coerced = {f: _coerce_number(r.get(f)) for f in self._COST_NUMERIC_FIELDS}
                if any(v is None for v in coerced.values()):
                    skipped += 1
                    _LOG.warning(
                        "upsert_cost_daily: skipping row for device %r day %r "
                        "with a non-numeric field: %r", device_id, day, r)
                    continue
                project = r.get("project") or ""
                conn.execute(
                    "INSERT INTO cost_daily (device_id, day, project, input, cache_read, "
                    "cache_write, output, effective, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(device_id, day, project) DO UPDATE SET "
                    "input=excluded.input, cache_read=excluded.cache_read, "
                    "cache_write=excluded.cache_write, output=excluded.output, "
                    "effective=excluded.effective, updated_at=excluded.updated_at",
                    (device_id, day, project, coerced["input"], coerced["cache_read"],
                     coerced["cache_write"], coerced["output"], coerced["effective"], now))
            return {"skipped": skipped}
        return self._write(_do)

    def replace_alerts(self, findings, now_fn=time.time):
        """Make the alerts table equal `findings` (guard.evaluate()'s
        output) exactly: every finding upserts its row, preserving
        first_seen and moving last_seen to now, and then every row NOT
        present in this batch is deleted -- so after this call the table
        holds precisely the current findings, nothing stale.

        Both halves run inside this single _do, which is the writer
        thread's one transaction (see _writer_loop: conn.commit() only
        after `fn` returns without raising, conn.rollback() otherwise),
        so a crash or exception partway through leaves the table exactly
        as it was before this call -- never a mix of old and new rows.

        A finding's session_id is None for device-targeted findings
        (guard._device_finding always sets it to None); stored here as
        the empty string, never NULL. SQLite does not enforce uniqueness
        across multiple NULLs in a composite PRIMARY KEY (SQL NULL !=
        NULL), so a device finding upserted twice with session_id=NULL
        would insert a second row instead of replacing the first -- the
        same failure mode the session_events unique index works around
        elsewhere in this file, just hit here via PRIMARY KEY instead of
        a UNIQUE index.

        A finding missing `device_id` or `rule` cannot form the primary
        key at all; such findings are skipped and logged rather than
        raising, since evaluate() itself is documented to never raise and
        replace_alerts must not turn a malformed finding into a lost
        poll cycle for every other finding in the same batch.

        `target_type` and `name` (fix round 1) are stored on the row, not
        resolved later via a join to sessions/devices: a session named by
        an alert may well have ended (the runaway-that-has-since-died
        case is exactly the one that matters) by the time anyone reads
        the alerts table, and a join at read time would lose that
        identity in precisely that case. Both are updated on every
        re-observation, same as severity/message/value -- unlike
        first_seen, there is no reason to freeze a finding's name/type to
        whatever it happened to be the first time it fired.

        Returns {"count": <current findings written>, "skipped": <findings
        dropped for missing device_id/rule>} -- `skipped` added in fix
        round 2 (review Minor 6) to match every sibling upsert
        (upsert_sessions, upsert_session_usage, upsert_cost_daily all
        report it)."""
        def _do(conn):
            now = now_fn()
            current_keys = set()
            skipped = 0
            for f in findings:
                device_id = f.get("device_id")
                rule = f.get("rule")
                if not device_id or not rule:
                    skipped += 1
                    _LOG.warning("replace_alerts: skipping malformed finding: %r", f)
                    continue
                session_id = f.get("session_id") or ""
                current_keys.add((device_id, session_id, rule))
                conn.execute(
                    "INSERT INTO alerts (device_id, session_id, rule, severity, message, "
                    "value, threshold, since, first_seen, last_seen, target_type, name) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(device_id, session_id, rule) DO UPDATE SET "
                    "severity=excluded.severity, message=excluded.message, "
                    "value=excluded.value, threshold=excluded.threshold, "
                    "since=excluded.since, last_seen=excluded.last_seen, "
                    "target_type=excluded.target_type, name=excluded.name",
                    (device_id, session_id, rule, f.get("severity"), f.get("message"),
                     f.get("value"), f.get("threshold"), f.get("since"), now, now,
                     f.get("target_type"), f.get("name")))
            existing_keys = [
                (row["device_id"], row["session_id"], row["rule"])
                for row in conn.execute("SELECT device_id, session_id, rule FROM alerts")]
            for key in existing_keys:
                if key not in current_keys:
                    device_id, session_id, rule = key
                    # A legacy row can hold session_id IS NULL (from before
                    # this method guaranteed "" instead) -- `session_id=?`
                    # with a NULL parameter matches nothing in SQL (NULL is
                    # never "=" to anything, not even another NULL), which
                    # would leave such a row un-deletable forever no matter
                    # how many times it stops appearing in a batch (review
                    # Minor 6). IS NULL is required to actually match it.
                    if session_id is None:
                        conn.execute(
                            "DELETE FROM alerts WHERE device_id=? AND session_id IS NULL "
                            "AND rule=?", (device_id, rule))
                    else:
                        conn.execute(
                            "DELETE FROM alerts WHERE device_id=? AND session_id=? AND rule=?",
                            key)
            return {"count": len(current_keys), "skipped": skipped}
        return self._write(_do)

    def prune(self, days=14, now_fn=time.time, cost_days=35):
        """Delete old rows. `days` (default 14) governs session_events,
        audit_log and ended sessions, same as before this parameter
        existed. `cost_daily` uses its OWN cutoff, `cost_days` (default
        35), not `days` -- fix round 2, review Important 1: fleetpoll.py
        calls prune() bare, hourly, so a shared 14-day cutoff would delete
        every cost_daily row older than 14 days on every poll, which is
        exactly the retrospective data /api/cost?days=30 and its 30-day
        sparkline need. A device re-reporting its own rolling window
        usually papers over this within a poll or two, so the visible
        symptom is churn, not an obvious break -- but any day whose
        transcript has since rotated off the device is gone from the hub
        for good, and the hub is the only copy. cost_days=35 gives
        /api/cost's default 30-day window headroom without depending on
        prune() and the API's default `days` being hand-kept in sync
        (either can change independently; cost_days only needs to stay
        >= the largest `days` a caller actually asks cost_view() for).

        session_usage's orphan sweep is unaffected by either cutoff -- it
        deletes by non-existence in `sessions`, not by age.

        Boundary note: both cutoffs here are "delete strictly older than
        the cutoff day/timestamp" (`<`), which errs toward keeping one
        extra day/interval of data rather than deleting it -- the safe
        direction for a prune() whose whole purpose is retention, given
        Important 1 above. cost_view()'s own `days` window (a query, not
        a delete) is the one documented as exact."""
        def _do(conn):
            now = now_fn()
            cutoff = now - days * 86400
            cost_cutoff_day = time.strftime("%Y-%m-%d", time.gmtime(now - cost_days * 86400))
            ev = conn.execute("DELETE FROM session_events WHERE ts < ?", (cutoff,)).rowcount
            au = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,)).rowcount
            se = conn.execute(
                "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,)).rowcount
            # Orphans: a session_usage row whose (device_id, session_id) no
            # longer has a sessions row at all -- including one just pruned
            # by `se` above, in the same transaction, since this SELECT
            # sees this connection's own uncommitted deletes.
            su = conn.execute(
                "DELETE FROM session_usage WHERE NOT EXISTS ("
                "SELECT 1 FROM sessions s WHERE s.device_id = session_usage.device_id "
                "AND s.session_id = session_usage.session_id)").rowcount
            cd = conn.execute(
                "DELETE FROM cost_daily WHERE day < ?", (cost_cutoff_day,)).rowcount
            return {"events": ev, "audit_log": au, "sessions": se,
                    "session_usage": su, "cost_daily": cd}
        return self._write(_do)

    # -- reads -----------------------------------------------------------

    def fleet_view(self, include_ended=False):
        """Rows for the live Sessions tab: ended sessions are excluded by
        default so a session that stopped hours ago doesn't linger in the
        list forever (nothing ever called prune() from the poll loop
        before, so store.py alone couldn't rely on rows disappearing).
        Pass include_ended=True for callers that still need dead sessions
        -- e.g. activity/history views keyed off session_id.

        Each session row gains `usage`, joined from session_usage on
        (device_id, session_id): a dict with input/cache_read/
        cache_write/output/effective/last_ts when a row exists there,
        else None -- never a dict of zeros, since "no transcript data
        yet" and "confirmed zero usage" are different facts a guard rule
        (and the UI) must be able to tell apart."""
        conn = self._read_conn()
        try:
            devices_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM devices ORDER BY name")]
            if include_ended:
                session_rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM sessions ORDER BY last_seen DESC")]
            else:
                session_rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY last_seen DESC")]
            usage_map = {
                (u["device_id"], u["session_id"]): {
                    "input": u["input"], "cache_read": u["cache_read"],
                    "cache_write": u["cache_write"], "output": u["output"],
                    "effective": u["effective"], "last_ts": u["last_ts"],
                }
                for u in conn.execute(
                    "SELECT device_id, session_id, input, cache_read, cache_write, "
                    "output, effective, last_ts FROM session_usage")
            }
            for s in session_rows:
                s["tmux"] = _parse_json_or_none(s.get("tmux"))
                s["claude"] = _parse_json_or_none(s.get("claude"))
                s["usage"] = usage_map.get((s["device_id"], s["session_id"]))
            return {"devices": devices_rows, "sessions": session_rows}
        finally:
            conn.close()

    def cost_view(self, days=30):
        """Hub-wide cost aggregation over the last `days` days, built from
        cost_daily. Returns {"devices": [...], "projects": [...],
        "generated_at": float} -- CONTRACT.md section 2/3: this is the
        already-aggregated "devices"/"projects" portion of the /api/cost
        response; the API layer wraps it with the request's own `days`
        and a `totals` rollup rather than reshaping what comes back here.

        devices: [{"device_id", "name", "total_effective",
                   "daily": [{"day","input","cache_read","cache_write",
                              "output","effective"}, ...newest first]},
                  ...sorted by total_effective descending]
        projects: [{"device_id","project","effective"}, ...sorted by
                   effective descending, capped at 50]

        No now_fn parameter (per CONTRACT.md): cost_daily.day is a
        calendar-date string, not an epoch, written once per real day by
        the poll loop -- there is no meaningful way to fake "now" here
        independent of also faking every seeded row's own day string, so
        tests control the window by choosing real day strings relative to
        the actual wall clock instead.

        The window is exactly `days` calendar days ending today (review
        Minor 5): subtracting `days * 86400` seconds from `now` lands on
        the same time-of-day exactly `days` days earlier, so that day
        itself is the day BEFORE the earliest one this call should
        include -- the boundary comparison below is therefore `>`
        (exclusive of that day), not `>=`, or `days=30` would return 31
        distinct days, not 30."""
        conn = self._read_conn()
        try:
            now = time.time()
            cutoff_day = time.strftime("%Y-%m-%d", time.gmtime(now - days * 86400))
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM cost_daily WHERE day > ? ORDER BY day DESC", (cutoff_day,))]
            device_names = {r["id"]: r["name"] for r in conn.execute(
                "SELECT id, name FROM devices")}

            # Multiple projects can contribute to the same (device_id, day);
            # collapse those before building the per-device daily list.
            by_device_day = {}
            for r in rows:
                key = (r["device_id"], r["day"])
                agg = by_device_day.setdefault(key, {
                    "day": r["day"], "input": 0, "cache_read": 0, "cache_write": 0,
                    "output": 0, "effective": 0})
                agg["input"] += r["input"] or 0
                agg["cache_read"] += r["cache_read"] or 0
                agg["cache_write"] += r["cache_write"] or 0
                agg["output"] += r["output"] or 0
                agg["effective"] += r["effective"] or 0

            devices_map = {}
            for (device_id, _day), agg in by_device_day.items():
                dev = devices_map.setdefault(device_id, {
                    "device_id": device_id,
                    "name": device_names.get(device_id, device_id),
                    "total_effective": 0, "daily": []})
                dev["daily"].append(agg)
                dev["total_effective"] += agg["effective"]
            for dev in devices_map.values():
                dev["daily"].sort(key=lambda d: d["day"], reverse=True)
            devices_out = sorted(
                devices_map.values(), key=lambda d: d["total_effective"], reverse=True)

            by_project = {}
            for r in rows:
                key = (r["device_id"], r["project"])
                by_project[key] = by_project.get(key, 0) + (r["effective"] or 0)
            projects_out = sorted(
                ({"device_id": device_id, "project": project, "effective": effective}
                 for (device_id, project), effective in by_project.items()),
                key=lambda p: p["effective"], reverse=True)[:50]

            return {"devices": devices_out, "projects": projects_out, "generated_at": now}
        finally:
            conn.close()

    def live_alerts(self):
        """All current findings, most severe first (alert before warn),
        then oldest first_seen first within a severity, per CONTRACT.md's
        live_alerts() spec and the brief -- a caller doesn't need to
        re-sort. Fix round 2 (review Minor 4): this shares guard._sort_key's
        severity ranking but NOT its tie-breaker -- guard._sort_key sorts
        by `since` (a per-observation rule value guard.evaluate() computes
        fresh every poll and never persists), while this sorts by
        `first_seen` (the persisted "how long has this alert been open"
        this table tracks across polls). The two are usually close but not
        the same field; a docstring here previously claimed they matched,
        which was wrong -- the code was always correct per the brief, only
        that comment was not.

        Returns the raw alerts rows as dicts: device_id, session_id,
        rule, severity, message, value, threshold, since, first_seen,
        last_seen, target_type, name -- everything /api/alerts publishes,
        with no join needed at read time (fix round 1: target_type/name
        are stored on the row by replace_alerts, not resolved against
        sessions/devices, since the session an alert names may well have
        ended by the time this is read)."""
        conn = self._read_conn()
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM alerts")]
            rank = {"alert": 0, "warn": 1}
            rows.sort(key=lambda r: (
                rank.get(r["severity"], 99),
                r["first_seen"] if r["first_seen"] is not None else float("inf")))
            return rows
        finally:
            conn.close()

    def recent_events(self, session_id=None, device_id=None, limit=50):
        conn = self._read_conn()
        try:
            clauses, params = [], []
            if session_id:
                clauses.append("session_id=?")
                params.append(session_id)
            if device_id:
                clauses.append("device_id=?")
                params.append(device_id)
            # Only the literal clause fragments ("session_id=?", "device_id=?")
            # are interpolated into the SQL string here; all actual values
            # stay parameterized in `params`, passed separately to execute().
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM session_events {where} ORDER BY ts DESC LIMIT ?",
                params + [limit]).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["extra"] = json.loads(d.pop("extra_json") or "{}")
                except ValueError:
                    d["extra"] = {}
                out.append(d)
            return out
        finally:
            conn.close()

    def recent_audit(self, limit=50):
        conn = self._read_conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))]
        finally:
            conn.close()

    def close(self):
        if self._closed:
            return
        # Set _closed BEFORE draining/joining so any write racing with
        # close() is rejected immediately with a clear error instead of
        # being queued and left to hang.
        self._closed = True
        self._close_error = StoreClosed("store is closed")
        self._stop.set()
        self._q.put(None)
        self._thread.join(timeout=5)
        # Defensive: fail any item that was queued after the writer thread
        # had already read the sentinel and exited its loop (a narrow
        # check-then-put race in _write), so no caller ever blocks for the
        # full _write timeout.
        while True:
            try:
                leftover = self._q.get_nowait()
            except queue.Empty:
                break
            if leftover is None:
                continue
            leftover.error = self._close_error
            leftover.done.set()
