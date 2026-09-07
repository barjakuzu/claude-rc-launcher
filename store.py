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
import os
import queue
import sqlite3
import threading
import time

_LOG = logging.getLogger(__name__)

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
            drain_error = self._close_error or RuntimeError("store is closed")
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
            raise RuntimeError("store is closed")
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
        upserted (started_at kept from the first sighting); rows in the DB
        for this device but absent from `rows` get ended_at set, exactly
        once (only rows that were still live, ended_at IS NULL, transition).

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
                    continue
                seen_ids.add(sid)
                existing = conn.execute(
                    "SELECT started_at FROM sessions WHERE device_id=? AND session_id=?",
                    (device_id, sid)).fetchone()
                started_at = existing["started_at"] if existing else (r.get("started_at") or now)
                conn.execute(
                    "INSERT INTO sessions (device_id, session_id, name, cwd, kind, state, "
                    "started_at, ended_at, last_seen, external) VALUES (?,?,?,?,?,?,?,NULL,?,?) "
                    "ON CONFLICT(device_id, session_id) DO UPDATE SET name=excluded.name, "
                    "cwd=excluded.cwd, kind=excluded.kind, state=excluded.state, "
                    "last_seen=excluded.last_seen, ended_at=NULL, external=excluded.external",
                    (device_id, sid, r.get("name"), r.get("cwd"), r.get("kind"),
                     r.get("state"), started_at, now, int(bool(r.get("external")))))
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
        rows."""
        def _do(conn):
            for r in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO session_events "
                    "(device_id, session_id, ts, event, extra_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (device_id, r.get("session_id"), r.get("ts"), r.get("event"),
                     json.dumps(r.get("extra") or {})))
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

    def fleet_view(self):
        conn = self._read_conn()
        try:
            devices_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM devices ORDER BY name")]
            session_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM sessions ORDER BY last_seen DESC")]
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
        self._close_error = RuntimeError("store is closed")
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
