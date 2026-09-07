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


# SQLite's INTEGER storage class (and, more fundamentally, Python's own
# sqlite3 C binding for ANY int parameter, regardless of the destination
# column's declared type/affinity) is a 64-bit signed integer. Fix round 3
# (review Important 1): math.isfinite() alone is the wrong magnitude gate
# for this -- it only rejects a Python int too large to convert to a C
# double (roughly 309+ digits), which is a much looser bound than 64-bit
# signed range (~19 digits). An int in between those two bounds (2**63,
# 10**30, a 20-digit numeric string) passes _coerce_number's old check
# cleanly, then raises OverflowError out of sqlite3 at conn.execute() time
# -- too late to skip just that row, since earlier rows in the same batch
# have already been INSERTed into this call's shared, uncommitted
# transaction, so the OverflowError rolls back the WHOLE batch, losing
# every good row sitting beside the one bad value. It also directly
# contradicts this function's own "never raises" promise.
_SQLITE_INT64_MIN = -(2 ** 63)
_SQLITE_INT64_MAX = 2 ** 63 - 1


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
    number") AND, if it's an int, within SQLite's signed 64-bit range
    (fix round 3, review Important 1) -- a float this large already lost
    integer precision and isn't the same failure mode, since a float
    binds as SQLite REAL with no 64-bit restriction regardless of
    magnitude. A numeric-looking string ("123", "12.5") is coerced, since
    JSON has no separate "numeric string" type worth punishing a device
    for -- including one that parses to an out-of-range int, which is
    rejected the same as a native Python int would be. bool is rejected
    even though it's technically an int subclass -- `True`/`False` are
    never a legitimate token count. Anything else (None, a list, a dict,
    a non-numeric string) returns None.

    Never raises: like _valid_started_at, math.isfinite() itself can raise
    OverflowError on an integer too large to convert to a C double, caught
    here rather than left to blow up the write -- and the explicit 64-bit
    range check above never raises at all, since Python's int comparison
    has no magnitude limit."""
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
        if not math.isfinite(num):
            return None
    except OverflowError:
        return None
    if isinstance(num, int) and not (_SQLITE_INT64_MIN <= num <= _SQLITE_INT64_MAX):
        return None
    return num


def _coerce_sparse_fields(row, fields):
    """Coerce only the numeric `fields` of `row` that are actually present
    and non-None, via _coerce_number; a field that's absent OR explicitly
    None is left as None (stored as SQL NULL) without being treated as an
    error. Fix round 3 (review Important 2, a regression fix round 2's own
    change introduced): requiring every field to be present, rather than
    only rejecting a present-but-bad value, silently dropped exactly the
    sparse shapes CONTRACT.md section 1 documents for the metadata role --
    {"session_id", "effective"} for per-session usage, {"day", "effective"}
    for cost -- so a metadata device contributed nothing at all, directly
    contradicting "contributes to per-device totals only, never to the
    projects table". It also dropped any row merely missing `last_ts`.
    Before fix round 2's coercion existed, an absent field's None simply
    became a stored NULL that cost_view's `r["input"] or 0` already
    handles -- this restores that for "absent", while still rejecting a
    field that IS present but garbage (the actual bug fix round 2 fixed).

    Returns (coerced_dict, name_of_first_bad_field_or_None). The caller
    skips the whole row only when the second element is not None -- a bad
    element midway through `fields` short-circuits the rest, same as
    before, since a partially-coerced dict for a row about to be dropped
    is never used."""
    coerced = {}
    for f in fields:
        raw = row.get(f)
        if raw is None:
            coerced[f] = None
            continue
        value = _coerce_number(raw)
        if value is None:
            return coerced, f
        coerced[f] = value
    return coerced, None


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
-- Fix round 1 (Minor): dedicated covering index for
-- last_event_ts_map()'s `SELECT device_id, session_id, MAX(ts) ...
-- GROUP BY device_id, session_id`, run once per poll cycle by the guard
-- step. idx_events_unique below (device_id, session_id, ts, event)
-- already happens to satisfy this as a covering, pre-sorted scan, but
-- that's an accident of the unique constraint's column order, not a
-- guarantee -- a future change to that constraint's shape (e.g. dropping
-- `event` from the natural key) would silently regress this hot poll-
-- loop query. This index exists for exactly this access pattern and
-- nothing else, so it stays correct independent of that.
CREATE INDEX IF NOT EXISTS idx_events_last_ts ON session_events(device_id, session_id, ts);
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
CREATE TABLE IF NOT EXISTS account_limits (
    device_id TEXT PRIMARY KEY,
    available INTEGER, fetched_at REAL, payload_json TEXT, updated_at REAL
);
"""
# account_limits (CONTRACT.md section 4) is a brand-new table, same as
# session_usage/cost_daily/alerts were when the comment below them was
# first written -- CREATE TABLE IF NOT EXISTS above is sufficient on its
# own, no additive column migration needed, because no existing hub.db
# can already have a differently-shaped version of this table.
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


# Phase 3 wiring (W4/integration): GET /api/fleet publishes `usage_partial`
# per device (CONTRACT.md section 3) and the guard poll-loop enrichment
# step needs the SAME fact to decide whether a device's sessions are safe
# to hand cost rules (CONTRACT.md's "guard must not fire cost rules on
# partial data" amendment). Neither server.py nor fleetpoll.py can share an
# in-process FleetPoller instance (app.py constructs one and never stores
# it anywhere else), so the fact has to live somewhere both can reach it
# from just a Store handle -- the devices table, alongside every other
# per-device fact this store already tracks. Additive (only ALTER TABLE ADD
# COLUMN when absent), same pattern as _NEW_SESSION_COLUMNS/_NEW_ALERT_COLUMNS.
_NEW_DEVICE_COLUMNS = (
    ("usage_partial", "INTEGER"),
)


def _migrate_device_columns(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
    for col, coltype in _NEW_DEVICE_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {coltype}")


def _json_or_none(value):
    return None if value is None else json.dumps(value)


def _encode_cwd_as_project(cwd):
    """Claude Code's ~/.claude/projects/<encoded> directory-name encoding
    for a project cwd: both '/' and '.' become '-' (same rule
    sessions._encode_project_dir applies device-side). Duplicated here,
    not imported, on purpose: store.py has no dependency on any other
    project module (it is deliberately stdlib-only -- see the module
    docstring), and importing sessions.py just for this one pure string
    transform would drag in subprocess/tmux/agents/panes/config for a
    hub-side aggregation query. CONTRACT.md's amendment for
    cost_view().sessions needs a `project` label per session, but
    session_usage carries no project column (cost_daily's project is
    keyed by day, not by session) -- so it is derived here from the
    session's own stored `cwd`, the exact same string usage.rollup()
    would have encoded on the device that reported it. A metadata-role
    session never reports cwd at all (fleet.py's _redact_session drops
    it), so this returns "" for it -- matching cost_daily's own
    empty-string convention for "no project reported"."""
    return (cwd or "").replace("/", "-").replace(".", "-")


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
            _migrate_device_columns(init_conn)
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
        """`row` may carry an optional `usage_partial` (bool or None) -- the
        device's own usage_meta.partial from its latest fleet snapshot
        (CONTRACT.md section 1/3). None (the default: most callers, e.g.
        _mark_unreachable's offline placeholder, don't know this fact and
        don't pass the key at all) means "leave whatever is already
        stored alone" rather than resetting a known-partial device back to
        false on every unrelated field update -- COALESCE against the
        existing column value on conflict, and default to 0 (not-partial)
        only for a genuinely new row that has never reported anything."""
        def _do(conn):
            raw_partial = row.get("usage_partial")
            partial_val = None if raw_partial is None else (1 if raw_partial else 0)
            conn.execute(
                "INSERT INTO devices (id, name, role, version, claude_version, last_seen, "
                "online, usage_partial) VALUES (?, ?, ?, ?, ?, ?, 1, COALESCE(?, 0)) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role, "
                "version=excluded.version, claude_version=excluded.claude_version, "
                "last_seen=excluded.last_seen, online=1, "
                "usage_partial=COALESCE(?, usage_partial)",
                (row["id"], row.get("name", row["id"]), row.get("role", "full"),
                 row.get("version"), row.get("claude_version"), now_fn(),
                 partial_val, partial_val))
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
        with device_id) and there is no safe fallback key.

        Rows may be sparse -- CONTRACT.md section 1's metadata role sends
        exactly {"session_id", "effective"}, nothing else -- so a numeric
        field that is simply absent (or explicitly None) is stored as
        NULL, not treated as an error; only a field that IS present and
        fails to coerce via _coerce_number (NaN, +/-inf, a non-numeric
        type, or an int outside SQLite's 64-bit range) skips the whole
        row, logged in full, rather than writing it with a partial value.
        See _coerce_sparse_fields."""
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
                coerced, bad_field = _coerce_sparse_fields(r, self._USAGE_NUMERIC_FIELDS)
                if bad_field is not None:
                    skipped += 1
                    _LOG.warning(
                        "upsert_session_usage: skipping row for device %r session %r "
                        "with a non-numeric %r field: %r", device_id, sid, bad_field, r)
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
        "" rather than skipped.

        Rows may be sparse -- CONTRACT.md section 1's metadata role sends
        exactly {"day", "effective"}, nothing else -- so a numeric field
        that is simply absent (or explicitly None) is stored as NULL, not
        treated as an error; only a field that IS present and fails to
        coerce via _coerce_number skips the whole row, logged in full,
        same as upsert_session_usage above -- see that method's docstring,
        _coerce_sparse_fields and _coerce_number itself for exactly what
        counts as coercible."""
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
                coerced, bad_field = _coerce_sparse_fields(r, self._COST_NUMERIC_FIELDS)
                if bad_field is not None:
                    skipped += 1
                    _LOG.warning(
                        "upsert_cost_daily: skipping row for device %r day %r "
                        "with a non-numeric %r field: %r", device_id, day, bad_field, r)
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

    def upsert_account_limits(self, device_id, limits_payload, now_fn=time.time):
        """Replace this device's stored account-limits reading (CONTRACT.md
        sections 2-4) with `limits_payload` -- fleet.build_fleet()'s own
        `limits` key, taken and stored WHOLE as JSON, not reshaped into
        columns: CONTRACT.md section 4 says "payload_json is the `limits`
        object above" verbatim, and it is server.py's /api/limits route
        (section 5), not this write path, that decides which of its
        fields the hub API actually surfaces.

        One row per device (PRIMARY KEY device_id) -- account_limits holds
        the freshest reading per device, not a history, so this always
        overwrites, never appends, same as upsert_device/
        upsert_session_usage above.

        `available`/`fetched_at` are pulled out of the payload into their
        own columns too, alongside payload_json, purely so limits_view()
        can filter/sort/compare in SQL without json.loads-ing every row
        first -- they are NOT a second source of truth: on a read, the
        payload (parsed from payload_json) is what callers actually use.

        A payload that isn't a dict at all is skipped and logged, same
        treatment upsert_cost_daily gives a row with no `day` -- a
        malformed shape from one device's fleet snapshot must not raise
        out of a poll cycle that is also ingesting that device's
        sessions/events/usage in the same call."""
        def _do(conn):
            if not isinstance(limits_payload, dict):
                _LOG.warning(
                    "upsert_account_limits: skipping non-dict payload for device %r: %r",
                    device_id, type(limits_payload).__name__)
                return {"skipped": True}
            now = now_fn()
            available = 1 if limits_payload.get("available") else 0
            fetched_at = limits_payload.get("fetched_at")
            if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
                fetched_at = None
            conn.execute(
                "INSERT INTO account_limits (device_id, available, fetched_at, "
                "payload_json, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET available=excluded.available, "
                "fetched_at=excluded.fetched_at, payload_json=excluded.payload_json, "
                "updated_at=excluded.updated_at",
                (device_id, available, fetched_at, json.dumps(limits_payload), now))
            return {"skipped": False}
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
        report it).

        `value`/`threshold` are coerced via _coerce_number before binding
        (fix round 4, review residual 1): guard.py's own _finite_or_none
        rejects NaN/inf but never checks SQLite's 64-bit range the way
        _coerce_number does, so an operator typo in guard.json (an
        over-int64 threshold, say) reaches this method as a plain finite
        Python int and previously raised OverflowError at conn.execute()
        time, dropping the ENTIRE alerts batch -- silently vanishing
        every OTHER finding in the same poll cycle, the exact failure
        this phase exists to prevent, even though the bad value
        originated from hub-local config rather than device input."""
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
                value = _coerce_number(f.get("value"))
                threshold = _coerce_number(f.get("threshold"))
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
                     value, threshold, f.get("since"), now, now,
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
        (and the UI) must be able to tell apart.

        A session_usage row can itself have every one of those six
        columns NULL -- upsert_session_usage's sparse-row handling (fix
        round 3) accepts a row like {"session_id": "s1"} with no numeric
        fields at all, which writes exactly that all-NULL row. Fix round
        4, review residual 2: such a row is joined here as None, not as
        {"input": None, ...} -- CONTRACT.md section 1 requires None for
        "no data", and a dict whose every field is null is the same fact
        wearing a different shape. Two representations of "no data" is
        exactly the ambiguity every round of this lane (and the UI lane)
        has been removing; a row with at least one real value still comes
        back as a dict, nulls and all, same as before."""
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
            usage_map = {}
            for u in conn.execute(
                    "SELECT device_id, session_id, input, cache_read, cache_write, "
                    "output, effective, last_ts FROM session_usage"):
                usage = {
                    "input": u["input"], "cache_read": u["cache_read"],
                    "cache_write": u["cache_write"], "output": u["output"],
                    "effective": u["effective"], "last_ts": u["last_ts"],
                }
                if all(v is None for v in usage.values()):
                    usage = None
                usage_map[(u["device_id"], u["session_id"])] = usage
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
        sessions: [{"device_id","session_id","name","project","effective",
                   "last_ts","ended"}, ...sorted by effective descending,
                   capped at 50] -- CONTRACT.md amendment ("/api/cost
                   gains a sessions array"): drawn from session_usage
                   LEFT JOINed to sessions (not INNER -- see below) so a
                   large ENDED session still appears, unlike /api/fleet
                   which only ever has live sessions. `project` is
                   derived from the session's own cwd (see
                   _encode_cwd_as_project); `name` is null when the
                   sessions row is gone (a LEFT JOIN, not an INNER JOIN,
                   is what makes that possible instead of silently
                   dropping the row -- CONTRACT.md's own wording, "name
                   may be null for a session whose row has been pruned",
                   only makes sense against a LEFT JOIN). Note this array
                   is NOT scoped to the `days` window the way devices/
                   projects are: session_usage holds one row per session
                   (lifetime totals, most recently updated), not a daily
                   series, so there is no `day` column to filter by here.

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

            # W4/integration fix: an empty `project` means "this device
            # did not report a project breakdown" (CONTRACT.md section 2:
            # the empty string when the device did not report one, e.g.
            # metadata role) -- never a real project. CONTRACT.md's
            # per-project-daily-totals amendment is explicit that such a
            # device "contributes to per-device totals only, never to the
            # projects table", so these rows are excluded here even
            # though they were already summed into `by_device_day` above
            # for devices_out. Skipping "" is also what keeps a metadata
            # device and a legacy pre-fleet device (fleetpoll.py's own
            # project="" fallback for either) from ever showing up in
            # this table as a phantom empty-named "project".
            by_project = {}
            for r in rows:
                if not r["project"]:
                    continue
                key = (r["device_id"], r["project"])
                by_project[key] = by_project.get(key, 0) + (r["effective"] or 0)
            projects_out = sorted(
                ({"device_id": device_id, "project": project, "effective": effective}
                 for (device_id, project), effective in by_project.items()),
                key=lambda p: p["effective"], reverse=True)[:50]

            # LEFT JOIN (not INNER): a session_usage row must still be
            # returned even if its `sessions` counterpart is momentarily
            # absent, so `name`/`cwd`/`ended_at` come back NULL rather than
            # the whole row vanishing -- see the docstring above. ORDER BY
            # + LIMIT in SQL rather than sorting the full table in Python:
            # NULL `effective` (an all-sparse session_usage row) sorts last
            # in DESC order, so it naturally falls out of the top 50
            # instead of needing a separate filter.
            session_rows = conn.execute(
                "SELECT su.device_id AS device_id, su.session_id AS session_id, "
                "su.effective AS effective, su.last_ts AS last_ts, "
                "s.name AS name, s.cwd AS cwd, s.ended_at AS ended_at, "
                "s.session_id AS joined_session_id "
                "FROM session_usage su LEFT JOIN sessions s "
                "ON s.device_id = su.device_id AND s.session_id = su.session_id "
                "ORDER BY su.effective DESC LIMIT 50")
            sessions_out = [
                {
                    "device_id": r["device_id"],
                    "session_id": r["session_id"],
                    "name": r["name"],
                    "project": _encode_cwd_as_project(r["cwd"]),
                    "effective": r["effective"],
                    "last_ts": r["last_ts"],
                    # Fix round 1 (Minor): `joined_session_id` is the
                    # LEFT JOIN's own match column -- NULL if and only if
                    # no `sessions` row matched at all (an orphan
                    # session_usage row), as opposed to a real row whose
                    # `ended_at` happens to be NULL (a genuinely live
                    # session). `bool(r["ended_at"])` alone can't tell
                    # those two apart and reported "ended: false" for an
                    # orphan, which is backwards: a session_usage row with
                    # no sessions counterpart at all is far more likely to
                    # be something that ended (and was later pruned) than
                    # something still live, so treat "unknown" as ended
                    # rather than as live for this field.
                    "ended": True if r["joined_session_id"] is None else bool(r["ended_at"]),
                }
                for r in session_rows
            ]

            return {"devices": devices_out, "projects": projects_out,
                    "sessions": sessions_out, "generated_at": now}
        finally:
            conn.close()

    # A five_hour.percent spread beyond this many points between two
    # AVAILABLE devices flips limits_view()'s `divergent` flag (CONTRACT.md
    # section 4). All devices share one Claude account, so any real spread
    # is a staleness artifact (one device's reading is a poll or two behind
    # the other), not two different truths -- 5 is generous enough to
    # absorb that normal skew without flagging on it constantly, while
    # still catching a device that is actually stuck on stale data.
    LIMITS_DIVERGENCE_THRESHOLD = 5

    def limits_view(self):
        """Hub-wide account_limits aggregation (CONTRACT.md section 4).

        Returns {"rows": [...], "primary": <payload dict or None>,
        "primary_device_id": <str or None>, "divergent": bool}.

        rows: one entry per device that has ever reported a limits
        reading -- {"device_id", "available" (bool), "fetched_at",
        "payload" (the stored `limits` object, or None if payload_json
        somehow failed to parse), "updated_at"}.

        `primary`/`primary_device_id`: the freshest (highest fetched_at)
        AVAILABLE row's payload and the device_id it came from, or
        (None, None) if no device has ever reported available data --
        CONTRACT.md section 5: "primary is null when no device has
        data... Never invent zeros." Returned as two separate values
        (not primary embedded with device_id already merged in) because
        that merge is server.py's /api/limits route's job, same division
        of labor as cost_view() leaving `days`/`totals` to the API layer.

        `divergent`: True when at least two AVAILABLE rows' five_hour.percent
        differ by more than LIMITS_DIVERGENCE_THRESHOLD points. A row with
        no usable five_hour reading (never fetched, or a payload that
        failed to parse) is excluded from the comparison -- it has nothing
        to disagree WITH, not evidence either way."""
        conn = self._read_conn()
        try:
            now = time.time()
            rows_raw = conn.execute(
                "SELECT device_id, available, fetched_at, payload_json, updated_at "
                "FROM account_limits").fetchall()
            rows = []
            for r in rows_raw:
                rows.append({
                    "device_id": r["device_id"],
                    "available": bool(r["available"]),
                    "fetched_at": r["fetched_at"],
                    "payload": _parse_json_or_none(r["payload_json"]),
                    "updated_at": r["updated_at"],
                })

            available_rows = [
                row for row in rows
                if row["available"] and isinstance(row["payload"], dict)
            ]
            primary = None
            primary_device_id = None
            if available_rows:
                def _sort_key(row):
                    fetched_at = row["fetched_at"]
                    return fetched_at if isinstance(fetched_at, (int, float)) else float("-inf")
                freshest = max(available_rows, key=_sort_key)
                primary = freshest["payload"]
                primary_device_id = freshest["device_id"]

            five_hour_percents = []
            for row in available_rows:
                bucket = row["payload"].get("five_hour")
                if not isinstance(bucket, dict):
                    continue
                pct = bucket.get("percent")
                if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                    five_hour_percents.append(pct)
            divergent = (
                len(five_hour_percents) >= 2
                and (max(five_hour_percents) - min(five_hour_percents))
                > self.LIMITS_DIVERGENCE_THRESHOLD
            )

            return {
                "rows": rows, "primary": primary,
                "primary_device_id": primary_device_id, "divergent": divergent,
                "generated_at": now,
            }
        finally:
            conn.close()

    def last_event_ts_map(self):
        """{(device_id, session_id): max(ts)} across every session_events
        row. Built for the guard poll-loop enrichment step (CONTRACT.md
        section 4): the `stalled` rule needs each live session's most
        recent event timestamp, and computing that per-session (one query
        per session, once per poll cycle) doesn't scale the way one
        aggregate GROUP BY query does. A session with no events at all
        simply has no key in the returned dict -- callers use .get() and
        treat that the same as "no event timestamp available", exactly
        the way a missing usage row already means "no data" elsewhere in
        this file."""
        conn = self._read_conn()
        try:
            rows = conn.execute(
                "SELECT device_id, session_id, MAX(ts) AS ts FROM session_events "
                "GROUP BY device_id, session_id")
            return {(r["device_id"], r["session_id"]): r["ts"] for r in rows}
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
