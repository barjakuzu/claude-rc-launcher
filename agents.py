"""Wraps `claude agents --json` — the device-local source of truth for
every live Claude Code session, launcher-started or not."""
import json
import subprocess
import threading
import time

import compat
from config import CLAUDE_BIN

CACHE_TTL_SECONDS = 30

# Cache is keyed on the claude_bin it was fetched for (a caller passing a
# different binary must not be served rows fetched for another one).
_cache = {"rows": [], "at": 0.0, "bin": None}

# Single-flight bookkeeping: at most one `claude agents --json` spawn in
# flight at a time, no matter how many threads call list_claude_sessions()
# concurrently. _refresh_cond both guards _cache/_refreshing and lets a
# thread that has no data to serve yet wait for the in-flight refresh
# instead of starting a second one.
_refresh_cond = threading.Condition()
_refreshing = False
# Test seam only: the most recently started background-refresh thread, so
# a test can .join() it instead of racing a daemon thread.
_last_refresh_thread = None


def _normalize(raw):
    session_id = raw.get("sessionId")
    if not session_id:
        return None
    return {
        "session_id": session_id,
        "name": raw.get("name"),
        "cwd": raw.get("cwd"),
        "kind": raw.get("kind"),
        "status": raw.get("status"),
        "started_at": raw.get("startedAt"),
        "pid": raw.get("pid"),
        "waiting_for": raw.get("waitingFor"),
        # Background-row-only field (working|blocked|done|failed|stopped).
        # None for interactive rows / older claude builds. Carried through
        # so callers (sessions.list_rc_sessions) can surface it as
        # row["claude"]["state"] for needs_attention derivation.
        "state": raw.get("state"),
    }


def _fetch_rows(bin_path, run):
    """Actually spawn `claude agents --json` and normalize its output.
    Returns [] on any failure — missing binary, timeout, non-zero exit,
    malformed JSON — never raises."""
    try:
        r = run([bin_path, "agents", "--json"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    try:
        parsed = json.loads(r.stdout)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [n for n in (_normalize(row) for row in parsed if isinstance(row, dict)) if n]


def _do_refresh(bin_path, run, now_fn):
    """Run the fetch (outside any lock — a subprocess spawn must never
    hold up other threads reading stale cache), then publish the result
    and release anyone waiting on it."""
    global _refreshing
    try:
        rows = _fetch_rows(bin_path, run)
        with _refresh_cond:
            _cache["rows"], _cache["at"], _cache["bin"] = rows, now_fn(), bin_path
    finally:
        with _refresh_cond:
            _refreshing = False
            _refresh_cond.notify_all()


def list_claude_sessions(claude_bin=None, run=subprocess.run, now_fn=time.time):
    """Every live session `claude agents --json` reports on this device,
    normalized, cached for CACHE_TTL_SECONDS, keyed on the resolved binary.

    Gated on compat.get_caps()["agents_json"] — an older `claude` build
    doesn't support `agents --json` at all, so there is nothing to spawn.

    Concurrency/latency contract:
    - A fresh cache hit never spawns anything.
    - A stale cache with data already in it is served immediately, and a
      background thread refreshes it (so a hung `claude` can't stall a
      caller for the 10s subprocess timeout on every /sessions poll).
    - The very first call ever (nothing cached yet) has nothing to serve,
      so it blocks for one synchronous fetch.
    - Only one `claude agents --json` spawn is ever in flight at a time
      (single-flight): concurrent callers either get the stale rows or,
      if there's truly nothing yet, wait on the one in-flight fetch
      instead of starting their own.
    """
    global _refreshing, _last_refresh_thread
    if not compat.get_caps().get("agents_json"):
        return []

    bin_path = claude_bin or CLAUDE_BIN
    with _refresh_cond:
        now = now_fn()
        if _cache["bin"] == bin_path and now - _cache["at"] < CACHE_TTL_SECONDS:
            return _cache["rows"]
        have_data = _cache["at"] > 0 and _cache["bin"] == bin_path
        if _refreshing:
            if have_data:
                return _cache["rows"]
            # Nothing to serve yet and someone else is already fetching —
            # wait for them rather than spawning a second `claude`.
            _refresh_cond.wait(timeout=15)
            return _cache["rows"]
        _refreshing = True
        if have_data:
            # Stale but usable: serve it now, refresh in the background.
            t = threading.Thread(target=_do_refresh, args=(bin_path, run, now_fn), daemon=True)
            _last_refresh_thread = t
            t.start()
            return _cache["rows"]

    # Nothing cached at all for this bin yet: block for one fetch.
    _do_refresh(bin_path, run, now_fn)
    with _refresh_cond:
        return _cache["rows"]
