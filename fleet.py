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
import usage

CACHE_TTL_SECONDS = 5
PRUNE_INTERVAL_SECONDS = 3600  # launcher-side path for events.prune (see
                                # hooks/rc-hook for the launcher-independent one)

_cache = {}  # key: (since, role) -> {"at": float, "result": dict}
             # bounded to at most one entry per role (see _remember)

_METADATA_SESSION_FIELDS = ("session_id", "name", "state", "started_at", "kind", "status")
_METADATA_EVENT_FIELDS = ("ts", "event")

EVENTS_LIMIT = 500

# usage_daily is capped at this many entries regardless of how many
# usage.rollup() happens to return (it already honours its own `days`
# default, but the cap is enforced here too so this module doesn't rely
# on that default never changing underneath it).
USAGE_DAILY_MAX_DAYS = usage.DEFAULT_DAYS

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


def _redact_usage(usage_row):
    """Metadata role keeps only the effective score, never the raw
    input/cache/output split or last_ts, which is closer to an activity
    timestamp than the role wants to reveal."""
    if usage_row is None:
        return None
    return {"effective": usage_row["effective"]}


def _usage_lookup(usage_result):
    """session_id -> the contract's per-session usage shape, or a
    lookup that always returns None if rollup() itself failed."""
    usage_sessions = usage_result["sessions"] if usage_result is not None else {}

    def usage_for(session_id):
        # usage.rollup() keys sessions by the UUID carried in the
        # transcript record itself. A launcher/external row's session_id
        # matches that UUID, but an adopted row may carry a tmux-derived
        # id (e.g. "tmux:rc-foo") that no transcript will ever have.
        # dict.get() returns None for both "no transcript yet" and "this
        # id will never have one", which is exactly the distinction the
        # contract wants: None means unknown, never a dict of zeros.
        entry = usage_sessions.get(session_id)
        if entry is None:
            return None
        return {
            "input": entry["input"],
            "cache_read": entry["cache_read"],
            "cache_write": entry["cache_write"],
            "output": entry["output"],
            "effective": entry["effective"],
            "last_ts": entry["last_ts"],
        }

    return usage_for


def _usage_daily_rows(usage_result):
    """Full-shape usage_daily list, newest day first, capped at
    USAGE_DAILY_MAX_DAYS. Empty when rollup() failed (see build_fleet):
    an empty list, not 30 zero-filled days, because no data was read at
    all, which is a different fact than every day being genuinely zero."""
    if usage_result is None:
        return []
    daily_items = sorted(usage_result["daily"].items(), key=lambda kv: kv[0], reverse=True)
    return [
        {
            "day": day,
            "input": bucket["input"],
            "cache_read": bucket["cache_read"],
            "cache_write": bucket["cache_write"],
            "output": bucket["output"],
            "effective": bucket["effective"],
        }
        for day, bucket in daily_items[:USAGE_DAILY_MAX_DAYS]
    ]


def _usage_meta(usage_result):
    if usage_result is None:
        return None
    return {
        "files": usage_result["files"],
        "skipped": usage_result["skipped"],
        "partial": usage_result["partial"],
        "generated_at": usage_result["generated_at"],
    }


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

    # Called once per build_fleet, never once per session, and inside this
    # function's own CACHE_TTL_SECONDS cache below, same as sessions/events
    # above. now_fn is pinned to this snapshot's `now` (same pattern as
    # _maybe_prune's events.prune call above) so usage_meta.generated_at
    # matches the rest of the payload instead of drifting a few ms apart.
    # max_bytes_per_call is passed explicitly at usage.py's own default so
    # a future reader sees the budget was a deliberate choice, not an
    # accident of whatever the default happens to be later.
    try:
        usage_result = usage.rollup(
            max_bytes_per_call=usage.DEFAULT_MAX_BYTES_PER_CALL,
            now_fn=lambda: now,
            days=USAGE_DAILY_MAX_DAYS,
        )
    except Exception as e:
        usage_result, errors_raw = None, errors_raw + [("usage", e)]

    caps = compat.get_caps()
    salt = getattr(config, "RC_HASH_SALT", "") or os.environ.get("RC_HASH_SALT", "")

    usage_for = _usage_lookup(usage_result)
    usage_daily_full = _usage_daily_rows(usage_result)
    usage_meta = _usage_meta(usage_result)

    if role == "metadata":
        out_sessions = [
            dict(_redact_session(s, salt), usage=_redact_usage(usage_for(s.get("session_id"))))
            for s in raw_sessions
        ]
        out_events = [_redact_event(e) for e in raw_events]
        usage_daily = [{"day": d["day"], "effective": d["effective"]} for d in usage_daily_full]
        # Never leak raw exception text (e.g. an OSError embedding the
        # home path/username) once role gates cwd/session identity too.
        errors = [f"{label}: {type(e).__name__}" for label, e in errors_raw]
    else:
        out_sessions = [dict(s, usage=usage_for(s.get("session_id"))) for s in raw_sessions]
        out_events = raw_events
        usage_daily = usage_daily_full
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
        "usage_daily": usage_daily,
        "usage_meta": usage_meta,
        "errors": errors,
    }
    _remember(cache_key, role, result, now)
    return result
