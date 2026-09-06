"""Wraps `claude agents --json` — the device-local source of truth for
every live Claude Code session, launcher-started or not."""
import json
import subprocess
import time
from typing import Optional

from config import CLAUDE_BIN

CACHE_TTL_SECONDS = 30

_cache = {"rows": [], "at": 0.0}


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
    }


def list_claude_sessions(claude_bin=None, run=subprocess.run, now_fn=time.time):
    """Every live session `claude agents --json` reports on this device,
    normalized, cached for CACHE_TTL_SECONDS. Returns [] on any failure —
    missing binary, timeout, non-zero exit, malformed JSON — never raises.
    """
    now = now_fn()
    if now - _cache["at"] < CACHE_TTL_SECONDS:
        return _cache["rows"]

    bin_path = claude_bin or CLAUDE_BIN
    try:
        r = run([bin_path, "agents", "--json"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        _cache["rows"], _cache["at"] = [], now
        return []
    if r.returncode != 0:
        _cache["rows"], _cache["at"] = [], now
        return []
    try:
        parsed = json.loads(r.stdout)
    except (ValueError, TypeError):
        _cache["rows"], _cache["at"] = [], now
        return []
    if not isinstance(parsed, list):
        _cache["rows"], _cache["at"] = [], now
        return []

    rows = [n for n in (_normalize(row) for row in parsed if isinstance(row, dict)) if n]
    _cache["rows"], _cache["at"] = rows, now
    return rows
