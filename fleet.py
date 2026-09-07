"""Device-side fleet snapshot: sessions (claude agents --json + tmux,
via sessions.list_rc_sessions) merged with recent hook events (events.py),
role-gated. Called in-process by the hub for itself, and over HTTP
(GET /fleet, proxied as GET /rc/fleet) for every other device.
"""
import datetime
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


def _today_str(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date().isoformat()


def _usage_by_session(usage_result):
    """usage.rollup()['sessions'], eagerly reshaped to the contract's
    per-session usage dict, keyed by session id. This runs INSIDE
    build_fleet's usage try/except (see there), and reads every field of
    every entry right here rather than lazily later: a malformed
    rollup() return (sessions not a dict, an entry that isn't a dict,
    missing/wrong-typed fields) must raise HERE, where it is caught and
    turned into one ("usage", e) error, not later while building session
    rows outside any guard (fix round 1, Important 1)."""
    sessions_in = usage_result["sessions"]
    if not isinstance(sessions_in, dict):
        raise TypeError("usage.rollup()['sessions'] is not a dict")
    out = {}
    for session_id, entry in sessions_in.items():
        out[session_id] = {
            "input": entry["input"],
            "cache_read": entry["cache_read"],
            "cache_write": entry["cache_write"],
            "output": entry["output"],
            "effective": entry["effective"],
            "last_ts": entry["last_ts"],
        }
    return out


def _usage_daily_rows(usage_result, today_str):
    """usage.rollup()['daily'], eagerly reshaped to the usage_daily list:
    newest day first, capped at USAGE_DAILY_MAX_DAYS. Any day after
    `today_str` is dropped BEFORE sorting/capping so a handful of
    future-dated records (clock skew on some device) can't evict real
    days out of the capped window (fix round 1, Minor)."""
    daily_in = usage_result["daily"]
    if not isinstance(daily_in, dict):
        raise TypeError("usage.rollup()['daily'] is not a dict")
    rows = []
    for day, bucket in daily_in.items():
        if not isinstance(day, str) or day > today_str:
            continue
        rows.append({
            "day": day,
            "input": bucket["input"],
            "cache_read": bucket["cache_read"],
            "cache_write": bucket["cache_write"],
            "output": bucket["output"],
            "effective": bucket["effective"],
        })
    rows.sort(key=lambda row: row["day"], reverse=True)
    return rows[:USAGE_DAILY_MAX_DAYS]


def _usage_daily_by_project_rows(usage_result, today_str):
    """usage.rollup()['daily_by_project'], eagerly reshaped and validated
    the same way as _usage_daily_rows, including the same future-date
    guard. NOT capped at USAGE_DAILY_MAX_DAYS by row count: several
    projects can share one day, so that cap would cut real rows instead
    of just bounding a per-day list. The 30 day WINDOW is already
    enforced by usage.rollup() itself (same cutoff as daily)."""
    rows_in = usage_result["daily_by_project"]
    if not isinstance(rows_in, list):
        raise TypeError("usage.rollup()['daily_by_project'] is not a list")
    rows = []
    for row in rows_in:
        day = row["day"]
        if not isinstance(day, str) or day > today_str:
            continue
        rows.append({
            "day": day,
            "project": row["project"],
            "input": row["input"],
            "cache_read": row["cache_read"],
            "cache_write": row["cache_write"],
            "output": row["output"],
            "effective": row["effective"],
        })
    # Stable sort twice: project ascending first, then day descending, so
    # rows for the same day come out project-ascending (day is the only
    # order the contract actually asks for; project order is just for a
    # deterministic, testable payload).
    rows.sort(key=lambda row: row["project"])
    rows.sort(key=lambda row: row["day"], reverse=True)
    return rows


def _usage_meta(usage_result):
    return {
        "files": usage_result["files"],
        "skipped": usage_result["skipped"],
        "partial": usage_result["partial"],
        "generated_at": usage_result["generated_at"],
        # usage.rollup() caps daily_by_project at MAX_DAILY_BY_PROJECT_ENTRIES
        # total rows, keeping the highest-effective ones (fix round 2):
        # this says whether THIS poll's list was actually truncated, so a
        # truncated projects list never quietly looks complete. Distinct
        # from `partial` on purpose: `partial` means the read itself was
        # incomplete (numbers may be LOW); this means the read was fully
        # complete but the per-project breakdown was cut for size, which
        # says nothing about whether the totals are trustworthy.
        "projects_capped": usage_result["daily_by_project_capped"],
    }


def _failed_usage_meta(now):
    """usage_meta must always be a dict, never None: a hub reading
    `(meta or {}).get("partial")` would otherwise see "complete" at
    exactly the moment usage.rollup() failed and the device actually
    knows nothing (fix round 1, Important 2). partial=True and files=0
    both say the same thing here: no usable usage data this poll, not
    "zero files exist". projects_capped is False here, not unknown: the
    empty usage_daily_by_project this failure also produces really is
    everything (nothing), not a truncated view of something bigger."""
    return {
        "files": 0, "skipped": 0, "partial": True, "generated_at": now,
        "projects_capped": False,
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
    #
    # Everything this function hands out about usage (per-session usage,
    # usage_daily, usage_daily_by_project, usage_meta) is built INSIDE
    # this try, not just the rollup() call itself: a malformed return
    # (wrong types, missing keys, a session entry that isn't even a
    # dict) must fail HERE, where it becomes one ("usage", e) error like
    # any other, never as an uncaught KeyError/TypeError while building
    # session rows further down (fix round 1, Important 1). Treated as
    # all-or-nothing on purpose: a poll where usage.rollup() only
    # half-validates is exactly as unusable to a guard cost rule as one
    # that raised outright, so there is no reason to keep a partially
    # validated result around.
    today_str = _today_str(now)
    try:
        usage_result = usage.rollup(
            max_bytes_per_call=usage.DEFAULT_MAX_BYTES_PER_CALL,
            now_fn=lambda: now,
            days=USAGE_DAILY_MAX_DAYS,
        )
        if not isinstance(usage_result, dict):
            raise TypeError(f"usage.rollup returned {type(usage_result).__name__}, expected dict")
        usage_by_session = _usage_by_session(usage_result)
        usage_daily_full = _usage_daily_rows(usage_result, today_str)
        usage_daily_by_project_full = _usage_daily_by_project_rows(usage_result, today_str)
        usage_meta = _usage_meta(usage_result)
    except Exception as e:
        errors_raw = errors_raw + [("usage", e)]
        usage_by_session = {}
        usage_daily_full = []
        usage_daily_by_project_full = []
        usage_meta = _failed_usage_meta(now)

    caps = compat.get_caps()
    salt = getattr(config, "RC_HASH_SALT", "") or os.environ.get("RC_HASH_SALT", "")

    if role == "metadata":
        out_sessions = [
            dict(_redact_session(s, salt), usage=_redact_usage(usage_by_session.get(s.get("session_id"))))
            for s in raw_sessions
        ]
        out_events = [_redact_event(e) for e in raw_events]
        usage_daily = [{"day": d["day"], "effective": d["effective"]} for d in usage_daily_full]
        # Never leak raw exception text (e.g. an OSError embedding the
        # home path/username) once role gates cwd/session identity too.
        errors = [f"{label}: {type(e).__name__}" for label, e in errors_raw]
    else:
        out_sessions = [dict(s, usage=usage_by_session.get(s.get("session_id"))) for s in raw_sessions]
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
    # A project name is the cwd by another name, so under metadata role
    # it is dropped entirely (key absent), not merely reduced the way
    # usage/usage_daily are above: there is no aggregate-only shape of
    # "which project" that doesn't itself identify the project.
    if role != "metadata":
        result["usage_daily_by_project"] = usage_daily_by_project_full
    _remember(cache_key, role, result, now)
    return result
