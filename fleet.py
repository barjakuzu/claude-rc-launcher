"""Device-side fleet snapshot: sessions (claude agents --json + tmux,
via sessions.list_rc_sessions) merged with recent hook events (events.py),
role-gated. Called in-process by the hub for itself, and over HTTP
(GET /fleet, proxied as GET /rc/fleet) for every other device.
"""
import hashlib
import os
import time

import compat
import config
import devices
import events
import sessions

CACHE_TTL_SECONDS = 5

_cache = {}  # key: (since, role) -> {"at": float, "result": dict}

_METADATA_SESSION_FIELDS = ("session_id", "name", "state", "started_at", "kind")
_METADATA_EVENT_FIELDS = ("ts", "event")

EVENTS_LIMIT = 500


def _events_root():
    return os.path.join(config.RC_HOME, "events")


def _hash(value, salt):
    return hashlib.sha256((salt + "|" + str(value)).encode()).hexdigest()


def _redact_session(row, salt):
    out = {k: row.get(k) for k in _METADATA_SESSION_FIELDS}
    if out.get("session_id"):
        out["session_id"] = _hash(out["session_id"], salt)
    return out


def _redact_event(row):
    return {k: row.get(k) for k in _METADATA_EVENT_FIELDS}


def build_fleet(since=None, role=None, events_root=None, now_fn=time.time):
    """One device's fleet snapshot. role defaults to config.RC_ROLE.
    Cached CACHE_TTL_SECONDS per (since, role)."""
    role = role or getattr(config, "RC_ROLE", "full")
    cache_key = (since, role)
    cached = _cache.get(cache_key)
    now = now_fn()
    if cached and now - cached["at"] < CACHE_TTL_SECONDS:
        return cached["result"]

    errors = []
    try:
        raw_sessions = sessions.list_rc_sessions()
    except Exception as e:
        raw_sessions, errors = [], errors + [f"sessions: {e}"]

    root = events_root or _events_root()
    try:
        raw_events, cursor = events.read_events(root, since_cursor=since, limit=EVENTS_LIMIT)
    except Exception as e:
        raw_events, cursor, errors = [], since, errors + [f"events: {e}"]

    caps = compat.get_caps()
    salt = os.environ.get("RC_HASH_SALT", "")

    if role == "metadata":
        out_sessions = [_redact_session(s, salt) for s in raw_sessions]
        out_events = [_redact_event(e) for e in raw_events]
    else:
        out_sessions = raw_sessions
        out_events = raw_events

    result = {
        "device_name": devices.get_local_name(),
        "role": role,
        "version": config.VERSION,
        "claude_version": caps.get("version"),
        "caps": caps,
        "sessions": out_sessions,
        "events": out_events,
        "cursor": cursor,
        "generated_at": now,
        "errors": errors,
    }
    _cache[cache_key] = {"at": now, "result": result}
    return result
