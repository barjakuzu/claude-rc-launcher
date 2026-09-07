"""Cost-guardrail rule engine: flags runaway and stalled Claude sessions
across the fleet. Pure function over a snapshot (the same shape
store.fleet_view(include_ended=False) returns, plus a `usage` and
`last_event_ts` a later task attaches) - this module never imports store,
never touches the filesystem except through load_rules(), never shells
out, and never reads the wall clock outside the injected now_fn. That is
what makes evaluate() deterministic and testable, and what lets a later
task run it inside the poll loop without new failure modes.

This module only flags. No stopping, no notifying, no killing - those are
separate later tasks, deliberately, because auto-stopping the wrong
session is worse than the leak it would have caught.

Built on 2026-09-07 after a hub burned 120.6 M effective tokens in one day
from four forgotten `--resume` loops, one alive since 2026-08-20, and
nothing noticed.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time

import config

# ---------------------------------------------------------------------------
# Config: defaults, merge, load (mtime-cached, thread-safe)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = os.path.join(config.RC_HOME, "guard.json")

# Fixed rule order: both the source of truth for DEFAULT_RULES and the
# order evaluate() walks rules in, so finding order (before the final sort)
# is deterministic run to run for identical input.
_RULE_ORDER = (
    "session_age",
    "token_rate",
    "token_total",
    "stalled",
    "device_concurrency",
    "device_offline",
)

# Per-rule default config. "enabled" is a bool everywhere; every other key
# is numeric (int or float) - the merge validator below relies on that
# split to type-check a file's values.
_DEFAULTS = {
    "session_age": {"enabled": True, "max_age_hours": 24},
    "token_rate": {
        "enabled": True,
        "max_effective_per_hour": 5000000,
        "min_age_hours_for_rate": 0.25,
    },
    "token_total": {"enabled": True, "max_effective_total": 50000000},
    "stalled": {"enabled": True, "stalled_minutes": 30},
    "device_concurrency": {"enabled": True, "max_sessions_per_device": 8},
    # Off by default: a laptop being closed is normal. Exists so it can be
    # turned on per-fleet (or later, per-device) once that's wired up.
    "device_offline": {"enabled": False, "offline_minutes": 60},
}

DEFAULT_RULES = {
    "rules": {name: dict(_DEFAULTS[name]) for name in _RULE_ORDER},
    "ignore": {"devices": [], "sessions": [], "names": []},
}

# Same pattern schedules.py uses for LAST_LOAD_ERROR: None on a clean load
# (file absent, or every key validated), a human-readable summary
# otherwise. Callers can surface it instead of a bad guard.json looking
# indistinguishable from "no guardrails configured".
LAST_LOAD_ERROR = None

# Reset at the start of every evaluate() call, then appended to as rules
# run. Each entry is (rule_name, exception_type_name) - never the
# exception message, which could embed a path or other session detail.
LAST_RULE_ERRORS = []

_lock = threading.Lock()
# Memoizes the last successfully parsed-and-merged config, keyed by the
# resolved path and its mtime at read time. A failed parse never writes
# here (see load_rules) so it can't poison a good prior memo, and a
# changed mtime always forces a re-read.
_cache = {"path": None, "mtime": None, "rules": None}


def default_rules():
    """A fresh deep copy of the all-defaults rule config. Useful as a
    starting point for callers (including tests) that want to run
    evaluate() against specific thresholds without touching a file."""
    return copy.deepcopy(DEFAULT_RULES)


def _merge_rules(raw):
    """Merge one parsed JSON value over the defaults.

    Returns (merged, error). `merged` is always a complete, valid rules
    dict (unknown or bad pieces of `raw` are simply left at their
    defaults) - callers never have to guard against a partial result.
    `error` is None when every part of `raw` validated cleanly, otherwise
    a human-readable summary of what was ignored or fell back, in the
    same "keep what's valid, report what wasn't" spirit as
    schedules._validate_schedules.
    """
    merged = default_rules()
    if not isinstance(raw, dict):
        return merged, f"expected a JSON object, got {type(raw).__name__}"

    problems = []

    rules_raw = raw.get("rules")
    if rules_raw is not None:
        if not isinstance(rules_raw, dict):
            problems.append("'rules' must be an object, kept all defaults")
        else:
            for rule_name, rule_cfg in rules_raw.items():
                if rule_name not in merged["rules"]:
                    continue  # unknown rule name: forward-compatible, ignored
                if not isinstance(rule_cfg, dict):
                    problems.append(
                        f"rules.{rule_name} must be an object, kept defaults")
                    continue
                target = merged["rules"][rule_name]
                for key, value in rule_cfg.items():
                    if key not in target:
                        continue  # unknown key within a known rule: ignored
                    if key == "enabled":
                        if isinstance(value, bool):
                            target[key] = value
                        else:
                            problems.append(
                                f"rules.{rule_name}.enabled must be a bool, "
                                f"kept default")
                    else:
                        # Numeric threshold. bool is technically a subclass
                        # of int in Python - excluded explicitly so a stray
                        # `true` doesn't silently become 1.
                        if isinstance(value, bool) or not isinstance(value, (int, float)):
                            problems.append(
                                f"rules.{rule_name}.{key} must be a number, "
                                f"kept default")
                        else:
                            target[key] = value

    ignore_raw = raw.get("ignore")
    if ignore_raw is not None:
        if not isinstance(ignore_raw, dict):
            problems.append("'ignore' must be an object, kept defaults")
        else:
            for field in ("devices", "sessions", "names"):
                if field not in ignore_raw:
                    continue
                value = ignore_raw[field]
                if isinstance(value, list) and all(isinstance(x, str) for x in value):
                    merged["ignore"][field] = list(value)
                else:
                    problems.append(
                        f"ignore.{field} must be an array of strings, "
                        f"kept default")

    error = "; ".join(problems) if problems else None
    return merged, error


def load_rules(path=None):
    """Load and merge ~/.claude-rc/guard.json (or `path`) over the
    defaults. Returns a complete rules dict, always - this never raises.

    Memoized per resolved path, keyed on mtime: an unchanged file is
    served from cache, a changed mtime forces a re-read. A file that
    fails to parse is never written into the cache (a failed parse must
    not poison a good prior memo, and must not be remembered as if it
    were valid config) - it just returns fresh defaults every time it's
    asked for until the file parses again.
    """
    global LAST_LOAD_ERROR
    resolved = path if path is not None else DEFAULT_CONFIG_PATH
    with _lock:
        try:
            mtime = os.path.getmtime(resolved)
        except OSError:
            # Missing (or unreadable) file is not an error - same as
            # schedules.load_schedules() for a missing schedules.json.
            LAST_LOAD_ERROR = None
            if _cache["path"] == resolved:
                _cache["path"] = None
                _cache["mtime"] = None
                _cache["rules"] = None
            return default_rules()

        if (_cache["path"] == resolved and _cache["mtime"] == mtime
                and _cache["rules"] is not None):
            return copy.deepcopy(_cache["rules"])

        try:
            with open(resolved, "r") as f:
                raw = json.load(f)
        except Exception as e:
            LAST_LOAD_ERROR = f"failed to parse {resolved}: {e}"
            return default_rules()

        merged, error = _merge_rules(raw)
        LAST_LOAD_ERROR = error
        _cache["path"] = resolved
        _cache["mtime"] = mtime
        _cache["rules"] = merged
        return copy.deepcopy(merged)


# ---------------------------------------------------------------------------
# Number formatting (small, tested helpers - messages never carry raw
# unformatted floats)
# ---------------------------------------------------------------------------

def _fmt_count(n):
    """Compact human string for a count: '1.2 M', '43.4 M', '820 k', or a
    plain int/one-decimal float below 1000. Used for token counts and for
    any other plain number embedded in a finding message."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1_000_000_000:
        return f"{sign}{a / 1_000_000_000:.1f} B"
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.1f} M"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.1f} k"
    if a == int(a):
        return f"{sign}{int(a)}"
    return f"{sign}{a:.1f}"


def _fmt_hours(h):
    """Compact human string for an hours duration: '6.1 h'."""
    try:
        h = float(h)
    except (TypeError, ValueError):
        return str(h)
    return f"{h:.1f} h"


# ---------------------------------------------------------------------------
# Finding construction
# ---------------------------------------------------------------------------

def _session_finding(rule, severity, s, message, value, threshold, since):
    return {
        "rule": rule,
        "severity": severity,
        "target_type": "session",
        "device_id": s.get("device_id"),
        "session_id": s.get("session_id"),
        "name": s.get("name"),
        "message": message,
        "value": value,
        "threshold": threshold,
        "since": since,
    }


def _device_finding(rule, severity, d, message, value, threshold, since):
    return {
        "rule": rule,
        "severity": severity,
        "target_type": "device",
        "device_id": d.get("id"),
        "session_id": None,
        "name": d.get("name"),
        "message": message,
        "value": value,
        "threshold": threshold,
        "since": since,
    }


def _is_live(s):
    """A session with ended_at set produces no findings from any rule."""
    return s.get("ended_at") is None


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _rule_session_age(s, cfg, now):
    if not _is_live(s):
        return None
    started_at = s.get("started_at")
    if not _is_number(started_at):
        return None
    max_age_hours = cfg.get("max_age_hours", _DEFAULTS["session_age"]["max_age_hours"])
    age_hours = (now - started_at) / 3600.0
    if age_hours <= max_age_hours:
        return None
    message = f"session age {_fmt_hours(age_hours)}, over the {_fmt_hours(max_age_hours)} limit"
    return _session_finding("session_age", "warn", s, message, age_hours, max_age_hours, started_at)


def _rule_token_rate(s, cfg, now):
    if not _is_live(s):
        return None
    usage = s.get("usage")
    if not isinstance(usage, dict):
        return None
    effective = usage.get("effective")
    if not _is_number(effective):
        return None
    started_at = s.get("started_at")
    if not _is_number(started_at):
        return None
    age_hours = (now - started_at) / 3600.0
    min_age = cfg.get("min_age_hours_for_rate", _DEFAULTS["token_rate"]["min_age_hours_for_rate"])
    denom = max(age_hours, min_age)
    if denom <= 0:
        return None
    rate = effective / denom
    max_rate = cfg.get("max_effective_per_hour", _DEFAULTS["token_rate"]["max_effective_per_hour"])
    if rate <= max_rate:
        return None
    message = f"{_fmt_count(rate)} effective tokens/hour over {_fmt_hours(age_hours)}"
    return _session_finding("token_rate", "alert", s, message, rate, max_rate, started_at)


def _rule_token_total(s, cfg, now):
    if not _is_live(s):
        return None
    usage = s.get("usage")
    if not isinstance(usage, dict):
        return None
    effective = usage.get("effective")
    if not _is_number(effective):
        return None
    max_total = cfg.get("max_effective_total", _DEFAULTS["token_total"]["max_effective_total"])
    if effective <= max_total:
        return None
    message = f"{_fmt_count(effective)} effective tokens, over the {_fmt_count(max_total)} limit"
    return _session_finding("token_total", "alert", s, message, effective, max_total, s.get("started_at"))


def _rule_stalled(s, cfg, now):
    if not _is_live(s):
        return None
    if s.get("state") != "busy":
        return None
    usage = s.get("usage")
    usage_last_ts = usage.get("last_ts") if isinstance(usage, dict) else None
    candidates = [t for t in (s.get("last_event_ts"), usage_last_ts, s.get("last_seen")) if _is_number(t)]
    if not candidates:
        return None
    last_activity = max(candidates)
    stalled_minutes = cfg.get("stalled_minutes", _DEFAULTS["stalled"]["stalled_minutes"])
    elapsed_minutes = (now - last_activity) / 60.0
    if elapsed_minutes <= stalled_minutes:
        return None
    message = (f"busy with no activity for {_fmt_count(elapsed_minutes)} min, "
               f"over the {_fmt_count(stalled_minutes)} min limit")
    return _session_finding("stalled", "warn", s, message, elapsed_minutes, stalled_minutes, last_activity)


def _rule_device_concurrency(d, sessions, cfg, now):
    device_id = d.get("id")
    max_n = cfg.get("max_sessions_per_device", _DEFAULTS["device_concurrency"]["max_sessions_per_device"])
    count = 0
    earliest = None
    for s in sessions:
        if not isinstance(s, dict):
            continue
        if s.get("device_id") != device_id:
            continue
        if not _is_live(s):
            continue
        count += 1
        sa = s.get("started_at")
        if _is_number(sa) and (earliest is None or sa < earliest):
            earliest = sa
    if count <= max_n:
        return None
    message = f"{_fmt_count(count)} live sessions, over the limit of {_fmt_count(max_n)}"
    return _device_finding("device_concurrency", "warn", d, message, count, max_n, earliest)


def _rule_device_offline(d, cfg, now):
    online = d.get("online")
    last_seen = d.get("last_seen")
    has_online_signal = online is not None
    has_last_seen = _is_number(last_seen)
    if not has_online_signal and not has_last_seen:
        return None

    minutes_offline = (now - last_seen) / 60.0 if has_last_seen else None
    offline_minutes = cfg.get("offline_minutes", _DEFAULTS["device_offline"]["offline_minutes"])

    fired = False
    if has_online_signal and not online:
        fired = True
    if has_last_seen and minutes_offline > offline_minutes:
        fired = True
    if not fired:
        return None

    if minutes_offline is not None:
        message = f"device offline for {_fmt_hours(minutes_offline / 60.0)}"
    else:
        message = "device reporting offline"
    return _device_finding("device_offline", "warn", d, message, minutes_offline, offline_minutes, last_seen)


_SESSION_RULES = (
    ("session_age", _rule_session_age),
    ("token_rate", _rule_token_rate),
    ("token_total", _rule_token_total),
    ("stalled", _rule_stalled),
)

_DEVICE_RULES = (
    ("device_concurrency", _rule_device_concurrency),
    ("device_offline", _rule_device_offline),
)

_SEVERITY_RANK = {"alert": 0, "warn": 1}


def _sort_key(f):
    since = f.get("since")
    since_val = since if _is_number(since) else float("inf")
    return (_SEVERITY_RANK.get(f.get("severity"), 99), since_val)


def _is_ignored(f, ignore_devices, ignore_sessions, ignore_names):
    if f.get("device_id") in ignore_devices:
        return True
    sid = f.get("session_id")
    if sid is not None and sid in ignore_sessions:
        return True
    name = f.get("name")
    if name is not None and name in ignore_names:
        return True
    return False


def evaluate(snapshot, rules=None, now_fn=time.time):
    """Evaluate every enabled rule against `snapshot`.

    `snapshot` is the plain dict store.fleet_view(include_ended=False)
    returns (this module never imports store, so it takes the dict, not
    the store). `rules` is a merged rules dict in the same shape
    load_rules()/default_rules() return; when None, load_rules() is
    called to read ~/.claude-rc/guard.json. `now_fn` is injected so tests
    (and callers) control "now" instead of this module reading the wall
    clock directly.

    Returns a list of finding dicts, most severe first (alert before
    warn) then oldest first (by `since`; findings with no `since` sort
    last within their severity). Never raises: a malformed snapshot or
    rules dict degrades to "no findings" for the parts that don't make
    sense, and a rule function that throws is caught, skipped, and
    recorded in LAST_RULE_ERRORS rather than aborting the whole
    evaluation.
    """
    global LAST_RULE_ERRORS
    LAST_RULE_ERRORS = []

    if isinstance(rules, dict):
        cfg = rules
    elif rules is None:
        try:
            cfg = load_rules()
        except Exception:
            cfg = default_rules()
    else:
        # Not None and not a dict is a caller bug; degrade to defaults
        # rather than either raising or silently going to disk.
        cfg = default_rules()

    devices = snapshot.get("devices") if isinstance(snapshot, dict) else None
    sessions = snapshot.get("sessions") if isinstance(snapshot, dict) else None
    devices = devices if isinstance(devices, list) else []
    sessions = sessions if isinstance(sessions, list) else []

    rules_cfg = cfg.get("rules") if isinstance(cfg, dict) else None
    rules_cfg = rules_cfg if isinstance(rules_cfg, dict) else {}
    ignore_cfg = cfg.get("ignore") if isinstance(cfg, dict) else None
    ignore_cfg = ignore_cfg if isinstance(ignore_cfg, dict) else {}

    def _str_set(value):
        return set(x for x in (value or []) if isinstance(x, str))

    ignore_devices = _str_set(ignore_cfg.get("devices"))
    ignore_sessions = _str_set(ignore_cfg.get("sessions"))
    ignore_names = _str_set(ignore_cfg.get("names"))

    now = now_fn()
    findings = []

    for name, func in _SESSION_RULES:
        rc = rules_cfg.get(name)
        rc = rc if isinstance(rc, dict) else {}
        if not rc.get("enabled", _DEFAULTS[name]["enabled"]):
            continue
        for s in sessions:
            if not isinstance(s, dict):
                continue
            try:
                f = func(s, rc, now)
            except Exception as e:
                LAST_RULE_ERRORS.append((name, type(e).__name__))
                continue
            if f:
                findings.append(f)

    for name, func in _DEVICE_RULES:
        rc = rules_cfg.get(name)
        rc = rc if isinstance(rc, dict) else {}
        if not rc.get("enabled", _DEFAULTS[name]["enabled"]):
            continue
        for d in devices:
            if not isinstance(d, dict):
                continue
            try:
                if name == "device_concurrency":
                    f = func(d, sessions, rc, now)
                else:
                    f = func(d, rc, now)
            except Exception as e:
                LAST_RULE_ERRORS.append((name, type(e).__name__))
                continue
            if f:
                findings.append(f)

    findings = [f for f in findings if not _is_ignored(f, ignore_devices, ignore_sessions, ignore_names)]
    findings.sort(key=_sort_key)
    return findings


def summarize(findings):
    """{"alert": 2, "warn": 3, "rules": {"token_rate": 1, ...}} - counts
    only, never values, safe to log or show in a UI badge."""
    out = {"alert": 0, "warn": 0, "rules": {}}
    for f in findings or []:
        sev = f.get("severity")
        if sev in out:
            out[sev] += 1
        rule = f.get("rule")
        if rule:
            out["rules"][rule] = out["rules"].get(rule, 0) + 1
    return out
