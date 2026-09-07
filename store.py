"""Hub-side SQLite store: devices, sessions, session_events, audit_log.

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


# A row can go missing from a single poll's `rows` without the underlying
# session actually having stopped -- e.g. `claude agents --json` timing out
# (agents._fetch_rows returns [] on a timeout) drops every external row for
# that poll, which the end-of-sweep below marks ended_at=now even though
# nothing really ended. If the very next poll(s) also report an invalid
# started_at (an older `claude` build simply omitting startedAt, say), a
# recently-ended row's own started_at is still the honest answer -- it's
# the same session flickering, not a new one reusing the id. A row that's
# been ended for longer than this grace window IS treated as a new session
# (see upsert_sessions): fleetpoll's default poll interval is 30s, so this
# covers a couple of missed polls' worth of jitter/backoff without
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
"""

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

    def upsert_sessions(self, device_id, rows, now_fn=time.time):
        """Replace this device's live-session view: rows present are
        upserted; rows in the DB for this device but absent from `rows`
        get ended_at set, exactly once (only rows that were still live,
        ended_at IS NULL, transition).

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
        (ended_at IS NULL) or was ended within ENDED_ROW_GRACE_SECONDS of
        `now`. A row can go missing from a single poll's `rows` without
        the underlying session having actually stopped (a `claude agents
        --json` timeout drops every external row for that poll -- see
        agents._fetch_rows), which the end-of-sweep below marks
        ended_at=now even though nothing really ended; if the very next
        poll(s) also report an invalid started_at, the recently-ended
        row's own started_at is still the honest answer for the SAME
        session flickering. A row ended longer ago than the grace window
        is instead treated as a genuinely new session reusing the id (the
        common case: a synthetic tmux:<name> id reassigned once a tmux
        session of that name is recreated) and gets its own clock -- its
        stale started_at must not be inherited, whether kept outright or
        (as it was before this fix) min()'d against a real reported value.

        A row without `session_id` is not a valid natural key (falling back
        to `name` risks colliding two distinct sessions, or an accidental
        rename ending one and starting another) -- such rows are skipped
        entirely: not upserted, and not counted as "seen" for the end-of-
        session sweep. Returns {"skipped": <count>}."""
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
                        "SELECT started_at, ended_at FROM sessions "
                        "WHERE device_id=? AND session_id=?",
                        (device_id, sid)).fetchone()
                    trustworthy = existing is not None and (
                        existing["ended_at"] is None
                        or (now - existing["ended_at"]) <= ENDED_ROW_GRACE_SECONDS
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

    def prune(self, days=14, now_fn=time.time):
        def _do(conn):
            cutoff = now_fn() - days * 86400
            ev = conn.execute("DELETE FROM session_events WHERE ts < ?", (cutoff,)).rowcount
            au = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,)).rowcount
            se = conn.execute(
                "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,)).rowcount
            return {"events": ev, "audit_log": au, "sessions": se}
        return self._write(_do)

    # -- reads -----------------------------------------------------------

    def fleet_view(self, include_ended=False):
        """Rows for the live Sessions tab: ended sessions are excluded by
        default so a session that stopped hours ago doesn't linger in the
        list forever (nothing ever called prune() from the poll loop
        before, so store.py alone couldn't rely on rows disappearing).
        Pass include_ended=True for callers that still need dead sessions
        -- e.g. activity/history views keyed off session_id."""
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
            for s in session_rows:
                s["tmux"] = _parse_json_or_none(s.get("tmux"))
                s["claude"] = _parse_json_or_none(s.get("claude"))
            return {"devices": devices_rows, "sessions": session_rows}
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
