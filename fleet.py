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
PRUNE_INTERVAL_SECONDS = 3600  # launcher-side path for events.prune (see
                                # hooks/rc-hook for the launcher-independent one)

_cache = {}  # key: (since, role) -> {"at": float, "result": dict}
             # bounded to at most one entry per role (see _remember)

_METADATA_SESSION_FIELDS = ("session_id", "name", "state", "started_at", "kind", "status")
_METADATA_EVENT_FIELDS = ("ts", "event")

EVENTS_LIMIT = 500

_last_prune_at = None


def _events_root():
    return os.path.join(config.RC_HOME, "events")


def _hash(value, salt):
    return hashlib.sha256((salt + "|" + str(value)).encode()).hexdigest()


def _redact_session(row, salt):
    out = {k: row.get(k) for k in _METADATA_SESSION_FIELDS}
    if out.get("session_id"):
        out["session_id"] = _hash(out["session_id"], salt)
    if out.get("name"):
        out["name"] = _hash(out["name"], salt)
    return out


def _redact_event(row):
    return {k: row.get(k) for k in _METADATA_EVENT_FIELDS}


def _remember(cache_key, role, result, now):
    """Store this snapshot, keeping at most one cache entry per role so
    the hub polling every device with a fresh `since` cursor every 30 s
    doesn't grow _cache unboundedly."""
    for key in list(_cache.keys()):
        if key[1] == role and key != cache_key:
            del _cache[key]
    _cache[cache_key] = {"at": now, "result": result}


def _maybe_prune(root, now):
    """At most once per PRUNE_INTERVAL_SECONDS, delete old spool files.
    This is the launcher-side path; hooks/rc-hook also self-prunes so
    retention still works when the launcher isn't running. Guarded so a
    prune failure never breaks build_fleet."""
    global _last_prune_at
    if _last_prune_at is not None and now - _last_prune_at < PRUNE_INTERVAL_SECONDS:
        return
    _last_prune_at = now
    try:
        events.prune(root, now_fn=lambda: now)
    except Exception:
        pass


def build_fleet(since=None, role=None, events_root=None, now_fn=time.time):
    """One device's fleet snapshot. role defaults to config.RC_ROLE.
    Cached CACHE_TTL_SECONDS per (since, role)."""
    role = role or getattr(config, "RC_ROLE", "full")
    cache_key = (since, role)
    cached = _cache.get(cache_key)
    now = now_fn()
    if cached and now - cached["at"] < CACHE_TTL_SECONDS:
        return cached["result"]

    errors_raw = []  # list of (label, Exception)
    try:
        raw_sessions = sessions.list_rc_sessions()
    except Exception as e:
        raw_sessions, errors_raw = [], errors_raw + [("sessions", e)]

    root = events_root or _events_root()
    _maybe_prune(root, now)
    try:
        raw_events, cursor = events.read_events(root, since_cursor=since, limit=EVENTS_LIMIT)
    except Exception as e:
        raw_events, cursor, errors_raw = [], since, errors_raw + [("events", e)]

    caps = compat.get_caps()
    salt = getattr(config, "RC_HASH_SALT", "") or os.environ.get("RC_HASH_SALT", "")

    if role == "metadata":
        out_sessions = [_redact_session(s, salt) for s in raw_sessions]
        out_events = [_redact_event(e) for e in raw_events]
        # Never leak raw exception text (e.g. an OSError embedding the
        # home path/username) once role gates cwd/session identity too.
        errors = [f"{label}: {type(e).__name__}" for label, e in errors_raw]
    else:
        out_sessions = raw_sessions
        out_events = raw_events
        errors = [f"{label}: {e}" for label, e in errors_raw]

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
    _remember(cache_key, role, result, now)
    return result
