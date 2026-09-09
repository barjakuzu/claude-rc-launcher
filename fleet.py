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
import limits
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

# Task L5 (live bug): the account-limits endpoint describes the ACCOUNT,
# not the machine. Before this, every device called it independently
# from inside build_fleet() every poll -- three devices sharing one
# account made three calls a minute for one number, on top of each
# device's own Claude Code status line polling the same endpoint
# independently. Only the fleet's elected single fetcher calls it now;
# see is_limits_hub() and its use below.
#
# Fix round 1 (coordinator): the first version of this function defaulted
# to "fetch" for every device and used devices.json non-emptiness to
# detect the hub. That correctly fixed the reported multi-device fleet,
# but devices.json alone cannot tell a satellite (empty devices.json,
# but polled by someone else's hub) apart from a genuinely standalone
# single-device install (empty devices.json, no fleet at all) -- both
# look identical from a device's own local state, and defaulting the
# ambiguous case to "do not fetch" broke the common, zero-config
# single-device install to fix the less common multi-device one.
#
# Fix round 2 (coordinator): replaced the devices.json inference with a
# real signal instead of a better guess -- the hub already polls every
# device on a schedule, so have it say so directly. Every outbound poll
# from fleetpoll.py's _default_http_get now carries HUB_POLL_HEADER; a
# device that receives it on its /fleet route (server.py) records the
# time via note_hub_poll() below. is_limits_hub() then treats "polled by
# a hub within HUB_POLL_STALE_SECONDS" as authoritative -- not an
# inference from credentials or remote addresses (both rejected in round
# 1: unreliable, and this product's own PWA/mobile remote-access feature
# specifically defeats "reached from a non-loopback address" as a
# satellite signal), but a positive statement from the only party that
# actually knows the answer. The default is inverted from round 1: fetch
# UNLESS a hub has said otherwise, so a standalone install (nobody ever
# polls it) needs zero configuration again, and a satellite stops within
# one poll cycle of its hub reaching it, also with zero configuration.
RC_FETCH_LIMITS_ENV = "RC_FETCH_LIMITS"
_FETCH_LIMITS_ON_VALUES = frozenset(("1", "true", "yes", "on"))
_FETCH_LIMITS_OFF_VALUES = frozenset(("0", "false", "no", "off"))

# Sent on every outbound poll fleetpoll.py's _default_http_get makes
# (see there): marks the request as coming from a hub polling this
# device as part of a fleet, as opposed to a browser, a health check, or
# any other caller of the same authenticated /fleet route. The value
# carries no information beyond its presence -- no device identity, no
# credential -- so there is nothing here for a log line or an error to
# leak even by accident.
HUB_POLL_HEADER = "X-RC-Hub-Poll"

# How long a single hub-poll marker is trusted before this device falls
# back to fetching on its own. Generous relative to FleetPoller's default
# 30s poll interval (fleetpoll.py) so a hub that is briefly down or
# restarting does not make every satellite it was polling start fetching
# within one missed cycle -- several satellites all resuming duplicate
# fetches at once over a short hub outage is exactly the failure this
# whole feature exists to avoid. At 10x the default interval, a hub has
# to be unreachable for a full 5 minutes before a satellite reverts to
# fetching on its own; it reverts to not-fetching again automatically
# within one poll cycle of the hub coming back.
HUB_POLL_STALE_SECONDS = 300

# Fix round 3 (coordinator): HUB_POLL_STALE_SECONDS alone is the SAME
# fixed window for every device. A hub polls its satellites in the same
# cycle (fleetpoll.poll_once() loops over devices.load_devices() one
# poll_once() call after another, seconds apart at most), so their
# markers all land within moments of each other -- and if the hub then
# goes down for exactly that long, every satellite it was polling lapses
# within the same instant and all resume fetching together: the original
# many-callers bug this whole feature exists to fix, rebuilt in degraded
# mode. Each device adds a small, STABLE, per-device jitter on top of the
# base window so simultaneous markings lapse at spread-out times instead
# of one instant. Derived by hashing config.RC_HASH_SALT (already a
# unique value generated once per device into ~/.claude-rc/env, see
# config.py) rather than plain randomness, so it needs no new persisted
# state, is stable across restarts (a device does not get a new jitter,
# and therefore a new resume time, every time it restarts), and is
# reproducible for tests.
HUB_POLL_JITTER_SECONDS = 60

# Advisory only, not behind a lock: the worst case of a race between two
# concurrent /fleet requests updating this is one poll cycle's worth of
# imprecision in is_limits_hub()'s staleness check (an extra fetch, or
# one skipped a poll early), never a correctness or security issue --
# nothing sensitive is gated on the exact value, only whether this
# device independently fetches its own account's usage percentages.
_last_hub_poll_at = None


def note_hub_poll(now=None, now_fn=time.time):
    """Record that a hub just polled THIS device -- called from
    server.py's /fleet route when HUB_POLL_HEADER is present on the
    request, nowhere else. `now`/`now_fn` are injection seams for
    tests; real callers pass neither and get time.time()."""
    global _last_hub_poll_at
    _last_hub_poll_at = now if now is not None else now_fn()


def _hub_poll_jitter(seed=None):
    """A stable, non-negative offset in [0, HUB_POLL_JITTER_SECONDS) for
    THIS device (fix round 3), derived by hashing `seed` (defaults to
    config.RC_HASH_SALT). Two devices with different salts get different
    offsets; the SAME device gets the SAME offset every time, including
    across restarts -- this is spreading, not randomising, the resume
    time. Never raises, never negative."""
    if seed is None:
        seed = getattr(config, "RC_HASH_SALT", "") or ""
    digest = hashlib.sha256(("hub-poll-jitter|" + str(seed)).encode()).hexdigest()
    return (int(digest[:8], 16) % (HUB_POLL_JITTER_SECONDS * 1000)) / 1000.0


def _polled_by_hub_recently(now_fn=time.time, last_poll_at=None, stale_seconds=None):
    """True iff note_hub_poll() (or the `last_poll_at` override, for
    tests) was called within the last HUB_POLL_STALE_SECONDS plus this
    device's own jitter (_hub_poll_jitter() above) -- `stale_seconds` is
    an injection seam (tests) to bypass jitter for exact-boundary
    assertions; real callers leave it unset."""
    if last_poll_at is None:
        last_poll_at = _last_hub_poll_at
    if last_poll_at is None:
        return False
    if stale_seconds is None:
        stale_seconds = HUB_POLL_STALE_SECONDS + _hub_poll_jitter()
    return (now_fn() - last_poll_at) < stale_seconds


def is_limits_hub(env=None, polled_by_hub_recently=None):
    """Whether THIS device is elected to call limits.get_limits() at all
    this poll (Task L5: "a device only calls the API when it is acting
    as the hub").

    Precedence:
    1. RC_FETCH_LIMITS in the environment, if it is one of
       _FETCH_LIMITS_ON_VALUES/_FETCH_LIMITS_OFF_VALUES, wins outright --
       an explicit, operator-visible override in either direction (same
       convention as RC_ROLE in config.py; documented in docs/DEVICES.md).
       An unset or unrecognised value falls through to the signal below.
    2. Otherwise: fetch UNLESS a hub has told this device it is a
       satellite recently (_polled_by_hub_recently() above, fix round 2).
       This is the safe default direction: absent any signal at all (a
       standalone install nobody ever polls, or a satellite whose hub
       hasn't reached it yet -- see the rollout note below), this device
       fetches, exactly as it always did before Task L5.

    Rollout: a device running this code that is NOT yet reached by an
    upgraded hub (the hub itself not yet upgraded, or simply hasn't
    polled yet) keeps fetching -- the 429 this task fixes persists a
    little longer in that window, which is the right trade against a
    standalone install silently losing the feature. The moment its hub
    (also upgraded) polls it once, it stops within HUB_POLL_STALE_SECONDS
    of that poll going stale should the hub then disappear, and resumes
    automatically if the hub keeps polling.

    `env`/`polled_by_hub_recently` are injection seams for tests (default
    to os.environ / _polled_by_hub_recently()) so every branch can be
    driven without touching the real environment or the real module-level
    poll-marker state."""
    env = os.environ if env is None else env
    raw = (env.get(RC_FETCH_LIMITS_ENV) or "").strip().lower()
    if raw in _FETCH_LIMITS_ON_VALUES:
        return True
    if raw in _FETCH_LIMITS_OFF_VALUES:
        return False
    if polled_by_hub_recently is None:
        polled_by_hub_recently = _polled_by_hub_recently()
    return not polled_by_hub_recently


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
    try:
        # Computed here, not before the try: _today_str does real
        # datetime math on `now` (fromtimestamp/date/isoformat), and a
        # pathological `now` (a clock past year 9999, a NaN) can raise on
        # its own, before usage.rollup() is even called. That is exactly
        # the class of bug Important 1 exists to prevent, so this must
        # fail HERE too, not outside the guard (fix round 3, Minor).
        today_str = _today_str(now)
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

    # CONTRACT.md section 2/3 + Task L5: account-level rate limits and
    # spend, fetched only when is_limits_hub() elects this device as the
    # fleet's single fetcher for it -- see that function's docstring
    # above. limits_result stays None (key omitted below, never sent as
    # available=False) when this device is not the fetcher: absent means
    # "not my job", available=False means "I tried and failed", and
    # those are different facts a caller must not conflate.
    #
    # polled_by_hub_recently is computed against THIS snapshot's own
    # `now` (fix round 2), not is_limits_hub()'s own now_fn=time.time
    # default, same reasoning as usage/limits.get_limits's now_fn just
    # below: a caller driving build_fleet() with an injected clock (every
    # test in this module, and any future one) gets a staleness check
    # that actually respects it, rather than one silently falling back to
    # the real wall clock underneath an otherwise fully-deterministic call.
    #
    # When this device IS the fetcher: limits.get_limits() is documented
    # to never raise on its own (every failure inside it -- no
    # credentials, network error, malformed response -- already comes
    # back as an available=False dict), but this is wrapped anyway, same
    # as sessions/events/usage above, so a future bug inside limits.py
    # can never be the reason build_fleet itself raises. now_fn is
    # pinned to this snapshot's own `now`, same pattern as the usage
    # block above, so limits.fetched_at (on a fresh fetch) lines up with
    # the rest of this payload's timestamps.
    limits_result = None
    if is_limits_hub(polled_by_hub_recently=_polled_by_hub_recently(now_fn=lambda: now)):
        try:
            limits_result = limits.get_limits(now_fn=lambda: now)
            if not isinstance(limits_result, dict):
                raise TypeError(
                    f"limits.get_limits returned {type(limits_result).__name__}, expected dict")
        except Exception as e:
            errors_raw = errors_raw + [("limits", e)]
            # SECURITY (CONTRACT.md section 2): unlike every other error
            # source above, `limits`' own exceptions must NEVER reach the
            # errors list as str(e) -- not even under "full" role, further
            # down -- because an exception raised this close to the OAuth
            # token read/HTTPS call can embed the request (headers included)
            # in its string form. unavailable_result() + type(e).__name__
            # here is the same shape limits.get_limits() itself would have
            # returned had this exception happened one frame further in.
            limits_result = limits.unavailable_result(now, error=type(e).__name__)

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
        # SECURITY (CONTRACT.md section 2): every OTHER error source here
        # is allowed a real message under full role, but "limits" is
        # exceptional -- see the try/except above -- so it is forced to
        # type(e).__name__ regardless of role, defense in depth beyond
        # limits.py's own internal never-raise guarantee.
        errors = [
            f"{label}: {type(e).__name__}" if label == "limits" else f"{label}: {e}"
            for label, e in errors_raw
        ]

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
    # CONTRACT.md section 3: when present, sent unchanged under EVERY
    # role, including metadata -- "it describes the account, not the
    # machine, and contains no project, path or identity data", so
    # unlike sessions/events/usage_daily above it needs no role-gated
    # reshaping. Task L5: present at all only when is_limits_hub() elected
    # this device to fetch (limits_result is not None) -- a non-fetching
    # device's payload has no "limits" key under any role, matching
    # fleetpoll._ingest's existing "absent means nothing to store" handling.
    if limits_result is not None:
        result["limits"] = limits_result
    # A project name is the cwd by another name, so under metadata role
    # it is dropped entirely (key absent), not merely reduced the way
    # usage/usage_daily are above: there is no aggregate-only shape of
    # "which project" that doesn't itself identify the project.
    if role != "metadata":
        result["usage_daily_by_project"] = usage_daily_by_project_full
    _remember(cache_key, role, result, now)
    return result
