"""Account-level Claude rate limits and spend (CONTRACT.md section 2/3).

Mirrors the working reference implementation at
~/.claude/statusline-command.sh (lines ~144-216 as read for this task): one
authenticated GET to the same endpoint, the same 60 second cache, the same
two credential sources (macOS Keychain, Linux credentials file).

    GET https://api.anthropic.com/api/oauth/usage
    Authorization: Bearer <oauth access token>
    anthropic-beta: oauth-2025-04-20

SECURITY (public repo, CONTRACT.md section 2) -- read this before touching
anything below:

  - The token is read locally by _read_token() and used for exactly one
    outbound HTTPS call inside _fetch_from_api(). Nothing in this module
    ever returns the token, logs it, stores it, or places it in a dict
    that crosses the wire (get_limits()'s return value never contains a
    "token" key, under any name).
  - Every except clause anywhere near the token formats the caught
    exception as type(e).__name__ ONLY, never str(e) and never the
    exception object itself (not even to logging.exception/%s, which would
    stringify it) -- a urllib.error.HTTPError/URLError can embed the
    request, headers included, in its string form.
  - CredentialsUnavailable (below) is the one exception this module
    defines on purpose: "not logged in on this device" is a normal state,
    not a fault, and reporting its TYPE NAME as `error` lets a caller
    (and a human reading the payload) tell that apart from a real fetch
    failure without ever needing a message string.
  - No function in this module reads a file or calls the OS keychain
    unless a caller (get_limits(), ultimately fleet.build_fleet()) asks
    it to. Every seam a test needs is an explicit parameter
    (fetch_fn/read_token_fn/path/runner) with the real thing as the
    default -- so a test can exercise every code path here without ever
    touching ~/.claude/.credentials.json or a real Keychain.
  - The fetch is made through noredirect.NO_REDIRECT_OPENER, never bare
    urllib.request.urlopen: urlopen's default opener follows redirects
    and RE-SENDS the Authorization header to wherever the redirect
    points, which would hand the bearer token to a foreign origin. See
    noredirect.py and _fetch_from_api.

Fix round 1 (security review) changed: redirects are now refused outright
(Critical); the 60s cache/fetch is now single-flight under a lock
(Important 1); the token read and the HTTPS call now share ONE overall
timeout budget instead of getting a full one each (Important 2); a 200
response with none of the recognised keys is no longer reported as
available (Important 3); a socket-level timeout's error string is
normalised across Python versions (Minor); five_hour/seven_day now carry
`severity`, sourced from the matching `limits[]` row (contract amendment).

Fix round 2 changed: _spend/_extra_usage now return None (not a
zero/false-filled dict) when every field they'd extract is None, closing
the same "invented reading becomes primary" gap Important 3 closed for
the top-level object (Low); the no-redirect opener moved into its own
noredirect.py module so fleetpoll.py, overview.py and server.py -- which
had the exact same bare-urlopen bug sending the hub's device Basic auth
password -- can share it instead of each duplicating it (see noredirect.py
docstring); _read_token_linux's docstring now states its timeout residual
explicitly rather than growing timeout machinery for a plain file read.

Task L5 (live bug, single fetcher + 429 handling) changed: WHO calls
get_limits() at all is now decided by the caller -- fleet.py's
is_limits_hub() -- not by anything in this module; this module still
never knows or cares whether it is "the" fetcher, it only fetches when
asked, exactly as before. What DID change here is 429 handling: a 429
response is no longer just another failure. _fetch_from_api raises the
new RateLimited (carrying only a parsed, non-negative retry_after float
or None -- never the response body, never the request) instead of
letting urllib.error.HTTPError fall through to the generic branch;
_do_fetch reports it as unavailable_result(..., error="RateLimited")
(same TYPE-NAME-only idiom CredentialsUnavailable already uses) and
additionally returns the parsed retry_after to get_limits(), which uses
it -- or, absent a usable Retry-After header, an escalating backoff
starting above the normal cache interval and capped at
RATE_LIMIT_BACKOFF_CEILING_SECONDS -- to hold off the NEXT attempt far
longer than the normal 60s throttle, while every call in between keeps
returning the last good reading with its true, growing age (the
existing last-good-on-failure path below, unchanged). A run of 429s
never escalates past the cap; the first non-429 outcome (success or any
other failure) resets the backoff to normal. None of this touches the
single-flight lock, the no-redirect opener, the 5s fetch budget, or the
unrecognised-shape rejection above -- all four are exactly as three
review rounds left them.
"""
import json
import logging
import math
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import noredirect

_LOG = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS_PATH_LINUX = "~/.claude/.credentials.json"
KEYCHAIN_SERVICE_NAME = "Claude Code-credentials"

CACHE_TTL_SECONDS = 60
FETCH_TIMEOUT_SECONDS = 5

# Task-m3: the two window lengths the Anthropic usage endpoint reports a
# percent for. Used by server.py to ask store.Store.effective_tokens_in_hourly_window()
# (five_hour) / store.Store.effective_tokens_in_daily_window() (seven_day,
# Task L5 follow-up) for the matching lookback, and by
# estimate_window_tokens() below to turn that measurement into a token
# budget alongside the percent.
FIVE_HOUR_WINDOW_SECONDS = 5 * 3600
SEVEN_DAY_WINDOW_SECONDS = 7 * 24 * 3600

# Task-m3: below this percent, `budget = consumed / (percent / 100)`
# amplifies whatever measurement noise `consumed` carries by more than
# 10x (at percent=10, a 1-point error in `consumed` becomes a 10-point
# error in `budget`; at percent=1 it becomes 100x). CONTRACT.md's own
# wording for this feature is explicit that a wild guess is worse than
# no number, so a percent this low simply does not get a derived budget
# -- the UI keeps showing the real, Anthropic-reported percent on its
# own, same as it always has.
TOKEN_ESTIMATE_PERCENT_FLOOR = 10.0

# Task L5: a 429 backs off far more patiently than a normal failure (the
# brief: "a ceiling of at least 15 minutes"). 900s is the escalating
# no-Retry-After backoff's own ceiling, AND (fix round 3) the upper clamp
# on an explicit Retry-After header too -- see _clamp_retry_after and
# get_limits. Also doubles as CACHE_TTL_SECONDS' partner as the LOWER
# clamp on an explicit Retry-After (fix round 3): round 0 honoured
# Retry-After verbatim on the reasoning that "respect it" meant exactly,
# with no ceiling either -- two real defects proved both ends of that
# wrong. See _clamp_retry_after's own docstring for the measured numbers.
RATE_LIMIT_BACKOFF_CEILING_SECONDS = 900

# Task L5 fix round 3: set to the epoch time of the most recent 429 while
# this device is still within the backoff that 429 triggered; None once
# recovered (any non-429 outcome, success or otherwise, clears it -- see
# get_limits). A last-good reading served during that window looks
# identical to a genuine fresh success in the returned dict itself
# (available=True, error=None -- CONTRACT.md task-l5: "a 429 should not
# look like a failure"), which is exactly why this exists: nothing in
# that dict says a rate limit is the reason fetched_at stopped advancing.
# Same pattern guard.py's LAST_LOAD_ERROR already uses for "the last
# thing that went wrong, queryable without re-triggering it" -- a caller
# (fleet.py, or a future one) can check this directly; get_limits() also
# logs a WARNING every time a real fetch attempt is actually rate
# limited (see there), so this is visible in ~/.claude-rc/logs/claude-rc.log
# without a manual probe of the real endpoint, which is how this bug was
# found in the first place. Never anything beyond a float: no exception,
# no message, nothing token-adjacent.
LAST_RATE_LIMITED_AT = None

# A misbehaving/compromised endpoint returning a huge body must not be
# read into memory in full -- same defensive cap fleetpoll.py applies to
# a device's /rc/fleet response, sized down since this response is a
# handful of small numbers, never a bulk payload.
MAX_RESPONSE_BYTES = 1 * 1024 * 1024

# Fix round 2: was this module's own _NoRedirectHandler/_NO_REDIRECT_OPENER,
# now shared via noredirect.py (fleetpoll.py, overview.py and server.py all
# had the same bare-urlopen redirect bug against a DIFFERENT credential --
# the hub's own Basic auth password to a device -- so one shared opener
# replaces four separate copies). Kept under this name so every existing
# call site and test in this module (limits._NO_REDIRECT_OPENER) still
# resolves without change.
_NO_REDIRECT_OPENER = noredirect.NO_REDIRECT_OPENER

# Important 1 (fix round 1): guards the whole cache-check-and-maybe-fetch
# body in get_limits(), single-flight style -- same idea agents.py's
# list_claude_sessions() already uses for `claude agents --json`. The
# hub's ThreadingHTTPServer means concurrent callers are real (a UI poll
# of /api/fleet landing mid fleetpoll cycle, say); without this lock each
# one independently sees a stale cache and makes its OWN real fetch,
# multiplying both credential reads and authenticated calls to Anthropic
# for what should be one shared 60s reading.
_cache_lock = threading.Lock()

# Module-level cache, same shape/convention as fleet.py's own _cache:
# cleared via _cache.clear() in tests (see tests/test_limits.py setUp).
# Always accessed under _cache_lock.
# "result": the last value get_limits() returned (success or failure).
# "attempted_at": when that value was computed -- informational (see
#   "next_attempt_at" below for the actual throttle gate).
# "next_attempt_at": the earliest time get_limits() will attempt a fresh
#   fetch again -- normally attempted_at + CACHE_TTL_SECONDS (a failing
#   fetch is throttled the same as a success, at most once per
#   CACHE_TTL_SECONDS; CONTRACT.md doesn't say this explicitly for the
#   failure case; see task-l1-report.md for why throttling failures the
#   same as successes is the safer reading in a long-running poll loop,
#   unlike the statusline script this mirrors, which is invoked fresh
#   per render and effectively retries on every failure until it next
#   succeeds), but pushed much further out after a 429 (Task L5; see
#   _next_rate_limit_backoff and get_limits) so a rate limit is not
#   simply retried into another rate limit every 60s.
# "rate_limit_streak": Task L5, count of CONSECUTIVE 429s with no usable
#   Retry-After header, used only to escalate the backoff above; reset
#   to 0 the moment an attempt is anything other than a 429 (an explicit
#   Retry-After also resets it -- that backoff comes from the header,
#   not from this streak).
# "last_good": the most recent available=True result, held onto
#   independent of the throttle above so a run of failures always has
#   something to fall back to, with its ORIGINAL fetched_at intact
#   (CONTRACT.md: "serve the last good value with its original
#   fetched_at so the UI can show how stale it is").
_cache = {}


class CredentialsUnavailable(Exception):
    """Raised when no usable OAuth token could be read: the credentials
    file/Keychain entry is absent, unreadable, malformed, or has no
    accessToken. This is CONTRACT.md's "normal state" (a device may not
    be logged in) -- callers report its TYPE NAME as `error`, never a
    message, exactly like any other fetch failure."""


class RateLimited(Exception):
    """Raised by _fetch_from_api when the usage endpoint answers 429.
    Task L5: a 429 means "you called too often", not "something is
    broken" -- kept as its own type (reported as error="RateLimited",
    same TYPE-NAME-only idiom CredentialsUnavailable uses above) so
    get_limits() can back off far more patiently than a normal failure,
    and so a caller/UI can tell the two apart without ever needing a
    message string.

    `retry_after` is the ALREADY-PARSED Retry-After header value in
    seconds (a non-negative float), or None if the header was absent or
    not a plain delta-seconds value -- never the raw header string, and
    never anything derived from the request. No message is ever passed
    to Exception.__init__ (str(e) stays empty), consistent with every
    other exception this module raises near the token: only .retry_after
    is ever read off this by a caller, exactly like CredentialsUnavailable
    carries nothing but its own type name."""

    def __init__(self, retry_after=None):
        super().__init__()
        self.retry_after = retry_after


def unavailable_result(now, error=None):
    """The CONTRACT.md section 3 shape for available=false. Public (no
    leading underscore) because fleet.py's own outer safety net
    (build_fleet must never raise) constructs this same shape directly
    if get_limits() itself somehow raises -- see fleet.py."""
    return {
        "available": False,
        "fetched_at": now,
        "five_hour": None,
        "seven_day": None,
        "scoped": [],
        "spend": None,
        "extra_usage": None,
        "error": error,
    }


def _num_or_none(value):
    """float(value) for a real (non-bool) int/float, else None. Never
    raises -- a malformed numeric field from the API degrades to null
    rather than aborting the whole parse (CONTRACT.md section 1: tolerate
    unexpected shapes; only the listed keys are ever read, and even those
    are read defensively)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int_or_none(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _str_or_none(value):
    return value if isinstance(value, str) else None


def _parse_retry_after_seconds(value):
    """A 429's `Retry-After` header -> seconds (non-negative float), or
    None. RFC 7231 allows either delta-seconds (a plain non-negative
    integer) or an HTTP-date; only delta-seconds is handled here -- a
    stated residual, same style as _read_token_linux's timeout residual
    above, not a silent gap: every usage-endpoint response seen in
    practice sends delta-seconds, and correctly resolving an HTTP-date
    would mean comparing it against wall-clock time fetched via a SECOND
    clock read inside a function whose caller is already budgeting a
    hard timeout -- meaningfully heavier than the rest of this parse for
    a form that has not actually been observed. An HTTP-date (or any
    other unparseable value) returns None here, exactly like a missing
    header: get_limits() then falls back to its own escalating backoff
    instead of trusting a value it couldn't parse. Never raises."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or not value.isdigit():
        return None
    try:
        return float(int(value))
    except (ValueError, OverflowError):
        return None


def _severity_by_kind(raw_limits, kind):
    """Contract amendment 1 (fix round 1): five_hour/seven_day carry no
    severity of their own in the raw response -- only the matching row in
    the `limits` array does (kind="session" for five_hour, kind=
    "weekly_all" for seven_day, per the amendment). Returns that row's
    severity, or None if the array is missing/malformed or no row
    matches -- never raises, same tolerance every other helper here
    gives a malformed sub-shape."""
    if not isinstance(raw_limits, list):
        return None
    for entry in raw_limits:
        if isinstance(entry, dict) and entry.get("kind") == kind:
            return _str_or_none(entry.get("severity"))
    return None


def _bucket(raw_bucket, severity):
    """five_hour/seven_day: {"utilization": <num>, "resets_at": <iso>} ->
    {"percent": <float>, "resets_at": <iso or None>, "severity": <str or
    None>}. `severity` is looked up by the caller from the matching
    `limits[]` row (_severity_by_kind) since the bucket itself never
    carries one -- contract amendment 1, fix round 1: without it the UI
    has no severity for the two most important numbers on screen and
    falls back to percent-band colouring, which CONTRACT.md section 6
    forbids. Returns None (not a half-filled dict) when the bucket source
    isn't usable at all, so a caller never has to distinguish "0%" from
    "missing" via a sentinel."""
    if not isinstance(raw_bucket, dict):
        return None
    percent = _num_or_none(raw_bucket.get("utilization"))
    if percent is None:
        return None
    return {
        "percent": percent,
        "resets_at": _str_or_none(raw_bucket.get("resets_at")),
        "severity": severity,
    }


def _scoped_rows(raw_limits):
    """CONTRACT.md's `limits` array -> the `scoped` list. The raw array
    mixes overall figures already surfaced via five_hour/seven_day
    (kind="session", kind="weekly_all", both scope=null in the verified
    response) with true per-model breakdowns (kind="weekly_scoped",
    scope={"model": {...}}). Filtering on "has a scope" rather than
    hardcoding the two kind names to exclude is deliberate: a future kind
    sharing the same scope=null "this is one of the overall numbers"
    convention is excluded automatically, without this module needing to
    know its name in advance -- CONTRACT.md section 1 asks exactly this
    ("tolerate every other key changing")."""
    if not isinstance(raw_limits, list):
        return []
    out = []
    for entry in raw_limits:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope")
        if not isinstance(scope, dict):
            continue
        percent = _num_or_none(entry.get("percent"))
        if percent is None:
            continue
        model = scope.get("model")
        label = model.get("display_name") if isinstance(model, dict) else None
        out.append({
            "kind": _str_or_none(entry.get("kind")),
            "group": _str_or_none(entry.get("group")),
            "percent": percent,
            "severity": _str_or_none(entry.get("severity")),
            "resets_at": _str_or_none(entry.get("resets_at")),
            "label": _str_or_none(label),
            "is_active": bool(entry.get("is_active")),
        })
    return out


def _spend(raw_spend):
    """{"used": {...}, "limit": ..., "percent": ..., "severity": ...} ->
    the CONTRACT.md `spend` shape, or None. Low (fix round 2): every field
    here already degrades to None on its own for a missing/malformed
    source value (_int_or_none/_str_or_none/_num_or_none never invent a
    number or string) -- the ONE exception used to be `percent`, which
    defaulted to 0.0 whenever it couldn't be read. That meant a body like
    {"spend": {}} produced {"used_minor": None, ..., "percent": 0.0,
    ...}: a dict that LOOKS like real data (a real, if zero, spend
    reading) even though nothing was actually present, exactly the
    "invented reading becomes primary and displaces a good one" failure
    Important 3 (fix round 1) already closed for the top-level object.
    Dropping the 0.0 default makes every field here uniformly None when
    the source is empty, so the "return None when every field is None"
    check below now actually fires instead of being permanently
    defeated by one field that could never be null."""
    if not isinstance(raw_spend, dict):
        return None
    used = raw_spend.get("used")
    used = used if isinstance(used, dict) else {}
    result = {
        "used_minor": _int_or_none(used.get("amount_minor")),
        "currency": _str_or_none(used.get("currency")),
        "exponent": _int_or_none(used.get("exponent")),
        "limit_minor": _int_or_none(raw_spend.get("limit")),
        "percent": _num_or_none(raw_spend.get("percent")),
        "severity": _str_or_none(raw_spend.get("severity")),
    }
    if all(v is None for v in result.values()):
        return None
    return result


def _extra_usage(raw_extra):
    """{"is_enabled": ..., "utilization": ..., "spend_limit_reached": ...}
    -> the CONTRACT.md `extra_usage` shape, or None. Low (fix round 2):
    `enabled`/`spend_limit_reached` are real, non-nullable booleans in
    the CONTRACT shape once data is present (an explicit "is_enabled":
    false IS a real reading, not a missing one), so they cannot simply be
    left as None the way `_spend`'s fields are above -- bool(None) is
    False, not None, which would defeat an all-None check run against
    the COERCED output the same way the removed 0.0 default defeated
    `_spend`'s. The "is every field actually absent" check below runs
    against the RAW values instead (an absent key's .get() default IS
    None; an explicitly-present `false` is NOT None), so {"extra_usage":
    {}} correctly returns None while {"extra_usage": {"is_enabled":
    false}} still returns a real dict with enabled=False."""
    if not isinstance(raw_extra, dict):
        return None
    raw_enabled = raw_extra.get("is_enabled")
    raw_utilization = raw_extra.get("utilization")
    raw_spend_limit_reached = raw_extra.get("spend_limit_reached")
    if raw_enabled is None and raw_utilization is None and raw_spend_limit_reached is None:
        return None
    return {
        "enabled": bool(raw_enabled),
        "utilization": _num_or_none(raw_utilization),
        "spend_limit_reached": bool(raw_spend_limit_reached),
    }


def _parse_usage_response(raw, now):
    """Raw /api/oauth/usage JSON -> CONTRACT.md section 3 shape. Reads
    ONLY the keys CONTRACT.md section 1 documents; every other key
    (`tangelo`, `nimbus_quill`, ...) is ignored outright, never even
    looked at. Every individual field degrades to None/[] rather than
    raising on its own, so a single malformed sub-object never aborts the
    whole parse -- but see the Important 3 check below for what happens
    when EVERY field degrades at once."""
    if not isinstance(raw, dict):
        raise ValueError("usage response is not a JSON object")
    raw_limits = raw.get("limits")
    five_hour = _bucket(raw.get("five_hour"), _severity_by_kind(raw_limits, "session"))
    seven_day = _bucket(raw.get("seven_day"), _severity_by_kind(raw_limits, "weekly_all"))
    scoped = _scoped_rows(raw_limits)
    spend = _spend(raw.get("spend"))
    extra_usage = _extra_usage(raw.get("extra_usage"))
    # Important 3 (fix round 1): a 200 whose body is a JSON object but
    # carries none of the keys this module knows how to read used to come
    # back as available=True with every field null. Being the freshest
    # reading, that would become `primary` in limits_view() and silently
    # displace a genuinely good reading from another device. Require at
    # least one recognised, successfully-parsed field before calling this
    # available -- an object with truly nothing recognisable in it is
    # exactly as useless as a fetch failure, so it is treated as one.
    if (five_hour is None and seven_day is None and not scoped
            and spend is None and extra_usage is None):
        raise ValueError("usage response carries none of the recognised keys")
    return {
        "available": True,
        "fetched_at": now,
        "five_hour": five_hour,
        "seven_day": seven_day,
        "scoped": scoped,
        "spend": spend,
        "extra_usage": extra_usage,
        "error": None,
    }


def _extract_token(data):
    """{"claudeAiOauth": {"accessToken": "..."}} -> the token string, or
    None for any other shape. Both credential sources (the Linux file and
    the macOS Keychain blob) use this same envelope -- see
    statusline-command.sh's identical `jq -r '.claudeAiOauth.accessToken'`
    against either source."""
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _read_token_linux(path=None):
    """Reads ~/.claude/.credentials.json. `path` is an injection seam for
    tests ONLY -- CONTRACT.md: "No test may read a real credentials
    file"; a test that wants to exercise this function passes its own
    temp-file fixture here rather than relying on the real default.

    Timeout residual (Low, fix round 2): this takes no `timeout` argument
    on purpose. `open()`/`json.load()` are plain stdlib file I/O with no
    built-in deadline, and there is no portable, dependency-free way to
    bound an arbitrary blocking syscall from pure Python without real
    machinery (a watchdog thread killing the read out from under itself,
    a signal-based alarm that does not exist on Windows and is unsafe to
    mix with threads, or a subprocess wrapper) -- meaningfully heavier
    than the 25-30 lines the rest of this module spends on the actual
    feature. A stalled local read (a wedged NFS/network home directory
    mount, say) can therefore overrun the 5 second budget Important 2
    (fix round 1) otherwise holds `_fetch_from_api` to on Linux, where
    this is the only credential source. This is accepted as a known,
    stated residual rather than solved: it is the same class of risk any
    other synchronous file read in this codebase already carries (see
    usage.py's transcript reads, sessions.py's session-state reads),
    none of which are timeout-bounded either, and unlike the macOS
    Keychain path (a subprocess, which DOES have a real, cheap timeout
    parameter -- see _read_token_macos) there is no equally cheap fix
    available here."""
    resolved = path if path is not None else os.path.expanduser(CREDENTIALS_PATH_LINUX)
    try:
        with open(resolved, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise CredentialsUnavailable()
    token = _extract_token(data)
    if not token:
        raise CredentialsUnavailable()
    return token


def _read_token_macos(runner=None, timeout=FETCH_TIMEOUT_SECONDS):
    """Reads the login Keychain entry via `security find-generic-password
    -s "Claude Code-credentials" -w`, which prints the same JSON envelope
    the Linux credentials file holds (see statusline-command.sh). `runner`
    is an injection seam for tests (defaults to subprocess.run) so a test
    never shells out to a real `security` binary or touches a real
    Keychain. `timeout` bounds the subprocess -- Important 2 (fix round
    1): the caller (_fetch_from_api) passes whatever budget remains of
    the OVERALL fetch, not always FETCH_TIMEOUT_SECONDS, so this step can
    never by itself consume the entire contract timeout and still leave
    the HTTPS call its own full budget on top."""
    run = runner or subprocess.run
    try:
        proc = run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE_NAME, "-w"],
            capture_output=True, timeout=timeout, check=True,
        )
    except Exception:
        # Covers: security not installed, item not found (not logged in),
        # timeout, non-zero exit -- all "no usable token", never logged
        # or inspected (stdout/stderr may echo the token back; neither is
        # ever read except via the narrow parse below, and never logged).
        raise CredentialsUnavailable()
    try:
        stdout = proc.stdout
        text = stdout.decode("utf-8") if isinstance(stdout, (bytes, bytearray)) else stdout
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError, AttributeError):
        raise CredentialsUnavailable()
    token = _extract_token(data)
    if not token:
        raise CredentialsUnavailable()
    return token


def _read_token(path=None, runner=None, timeout=FETCH_TIMEOUT_SECONDS):
    if sys.platform == "darwin":
        return _read_token_macos(runner=runner, timeout=timeout)
    return _read_token_linux(path=path)


def _fetch_from_api(timeout, read_token_fn=None):
    """The one outbound HTTPS call this module ever makes. Returns the
    parsed raw JSON response; raises on any failure (no credentials,
    network error, non-2xx, oversized body, unparseable body) -- the
    caller (_do_fetch) is what turns that into
    unavailable_result(..., error=type(e).__name__), never this
    function, so the exception itself never needs to be caught more than
    once or passed around.

    Important 2 (fix round 1): `timeout` is the TOTAL budget for this
    whole function, not a per-step allowance. On macOS the token read is
    itself a subprocess with its own timeout; giving it the full budget
    and THEN giving urlopen the full budget again could double the worst
    case to 2x timeout (measured: 5.83s against a hung endpoint before
    this fix), blowing past both CONTRACT.md's hard 5s cap and the hub
    poll's own ~10s remote-device timeout. The token read is allowed up
    to `timeout` seconds; whatever remains is what's left for the HTTPS
    call, and if the token read alone exhausts the whole budget, this
    raises a TimeoutError rather than ever attempting the HTTPS call.

    `read_token_fn`, if given, must accept a `timeout` keyword argument
    -- every test double in tests/test_limits.py does; defaults to
    _read_token, which forwards it to the macOS branch only (a Linux
    file read has no use for it -- see _read_token_linux's own docstring
    for the timeout residual that leaves on Linux specifically).

    Task L5: a 429 response raises RateLimited (defined above), not a
    plain HTTPError -- every other non-2xx status still surfaces as
    urllib.error.HTTPError, unchanged from before this task."""
    read_token = read_token_fn or _read_token
    start = time.monotonic()
    token = read_token(timeout=timeout)
    remaining = timeout - (time.monotonic() - start)
    if remaining <= 0:
        raise TimeoutError("token read consumed the entire fetch budget")
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
        },
    )
    # Critical (fix round 1): _NO_REDIRECT_OPENER, never bare
    # urllib.request.urlopen -- see _NoRedirectHandler above. A 3xx here
    # surfaces as urllib.error.HTTPError, never a second request.
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=remaining) as resp:
            data = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Task L5: distinguished from every other HTTPError so
            # get_limits() can back off far more patiently than a normal
            # failure. e.headers is the RESPONSE's headers (whatever
            # Anthropic sent back, Retry-After included if present) --
            # reading it carries none of the risk this module's own
            # docstring warns about for str(e) on THIS SAME exception
            # type (a urllib HTTPError's string form can embed the
            # REQUEST, Authorization header included); only e.code and
            # one named response header are ever read here, never the
            # exception's message/string form, and never the request.
            headers = e.headers
            raw_retry_after = headers.get("Retry-After") if headers is not None else None
            raise RateLimited(retry_after=_parse_retry_after_seconds(raw_retry_after))
        raise
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("usage response exceeded size limit")
    return json.loads(data)


def _do_fetch(now, timeout, fetch_fn):
    """One fetch-and-parse attempt. Never raises: any exception from the
    fetch (including CredentialsUnavailable) or from parsing its response
    becomes unavailable_result(now, error=type(e).__name__) -- section 2's
    "type name only, never str(e)" rule applied at the one place that
    ever sees the raw exception.

    Returns (result, rate_limit_retry_after): the second element is None
    for every outcome except a 429 (RateLimited), in which case it is
    RateLimited.retry_after (a parsed, non-negative float, or None if no
    usable Retry-After header was sent) -- Task L5: get_limits() uses it
    to schedule the NEXT attempt, without ever inspecting an exception
    itself; every exception this module raises near the token is still
    only ever caught, named and discarded right here."""
    fetch = fetch_fn or _fetch_from_api
    try:
        raw = fetch(timeout)
    except RateLimited as e:
        return unavailable_result(now, error="RateLimited"), e.retry_after
    except socket.timeout:
        # Minor (fix round 1): a socket-level timeout's type name is
        # "timeout" on 3.9 and "TimeoutError" on 3.10+ (socket.timeout
        # became a plain alias of the builtin there) -- same failure, two
        # different strings depending on which Python this device
        # happens to run. Normalised to one value so the UI never has to
        # know the difference. socket.timeout IS TimeoutError on 3.10+,
        # so this branch also catches the plain TimeoutError
        # _fetch_from_api raises itself when the token read exhausts the
        # whole budget -- nothing extra needed for that case.
        return unavailable_result(now, error="TimeoutError"), None
    except Exception as e:
        return unavailable_result(now, error=type(e).__name__), None
    try:
        return _parse_usage_response(raw, now), None
    except Exception as e:
        return unavailable_result(now, error=type(e).__name__), None


def _next_rate_limit_backoff(streak):
    """Task L5: the backoff to use for the NEXT attempt after a 429 with
    no usable Retry-After header, given `streak` PRIOR consecutive such
    429s (0 on the first one). Starts above the normal CACHE_TTL_SECONDS
    cadence and doubles on every consecutive occurrence, capped at
    RATE_LIMIT_BACKOFF_CEILING_SECONDS (>= 15 minutes, per the brief) --
    reaches the cap within a handful of consecutive 429s rather than
    creeping up over hours. A real Retry-After header (see get_limits)
    is used as-is instead of this, and resets `streak` back to 0: this
    escalation is only for the case Anthropic did NOT tell us how long
    to wait."""
    return min(CACHE_TTL_SECONDS * (2 ** (streak + 1)), RATE_LIMIT_BACKOFF_CEILING_SECONDS)


def _clamp_retry_after(seconds):
    """Task L5 fix round 3: an explicit Retry-After header is honoured,
    but clamped to [CACHE_TTL_SECONDS, RATE_LIMIT_BACKOFF_CEILING_SECONDS]
    rather than trusted verbatim. Round 0 deliberately did not clamp
    this, reasoning that "respect Retry-After" meant honouring it
    exactly; two real defects, both against values
    _parse_retry_after_seconds already correctly accepts as valid
    non-negative numbers, proved that reasoning wrong on both ends:

      - Retry-After: 0 set next_attempt_at = now + 0. The throttle gate
        is `now < next_attempt_at`, already false the instant it was
        set, so EVERY subsequent call re-fetched immediately -- a server
        saying "you are rate limited" made this module hammer it harder
        than before this feature existed (measured: 20 outbound calls in
        a 5s window, against 1 correctly). rate_limit_streak also never
        left 0 on this path, so nothing could even escalate out of it on
        its own.
      - An arbitrarily large value ("99999999999999999999" measured)
        stopped this device from ever fetching again for 3.2e12 years,
        recoverable only by restarting the process.

    Both ends are clamped to the same window the no-Retry-After
    escalating backoff (_next_rate_limit_backoff above) already lives
    in, so an explicit header can still shorten or lengthen the wait
    relative to this module's own guess, but never past what it already
    treats as a sane range for holding off one account-limits fetch."""
    return min(max(seconds, CACHE_TTL_SECONDS), RATE_LIMIT_BACKOFF_CEILING_SECONDS)


def get_limits(now_fn=time.time, timeout=FETCH_TIMEOUT_SECONDS, fetch_fn=None):
    """CONTRACT.md section 3's `limits` object for this device's account.
    Never raises and never blocks longer than `timeout` (the token read
    and the HTTPS call now share that one budget -- see _fetch_from_api's
    Important 2 fix round 1 note; the Keychain read on macOS no longer
    carries its own separate full timeout).

    Caches the fetch for CACHE_TTL_SECONDS: a call within that window of
    the last attempt returns the exact same value, no I/O. On a fresh
    attempt that fails, the last available=true result (if any) is
    returned instead, UNCHANGED -- same dict, same original fetched_at --
    so a caller/UI can compute staleness from fetched_at rather than being
    handed a fresh failure that masks how long the account has actually
    gone unread.

    Task L5: a 429 extends that throttle far past the normal
    CACHE_TTL_SECONDS -- respecting Retry-After when the response sent
    one (fix round 3: clamped to [CACHE_TTL_SECONDS,
    RATE_LIMIT_BACKOFF_CEILING_SECONDS], see _clamp_retry_after), otherwise
    the escalating backoff above -- so a rate limit does not simply retry
    every 60s and get rate limited again. Every call made before the
    extended throttle elapses still returns the SAME cached result as any
    other throttled call (last-good-on-failure, unchanged, right below),
    so the UI keeps showing the last good reading with its true, growing
    age, never a fresh-looking failure. The first outcome that is NOT a
    429 (success or any other failure) resets the backoff to normal --
    "recover automatically" per the brief -- and clears
    LAST_RATE_LIMITED_AT (fix round 3), the one place a caller can tell a
    fresh-looking last-good result apart from an actually-fresh one.

    Important 1 (fix round 1): the whole cache-check-and-maybe-fetch body
    runs under _cache_lock, single-flight style (same idea agents.py's
    list_claude_sessions() already uses for `claude agents --json`). The
    hub's ThreadingHTTPServer makes concurrent callers real -- eight
    concurrent calls used to make eight real fetches, multiplying both
    credential reads and authenticated calls to Anthropic. A follower
    blocked on the lock pays at most the leader's own fetch time (already
    hard-capped at `timeout`), then reads the now-fresh cache the leader
    just published -- never a fetch of its own.

    `fetch_fn`, `(timeout) -> dict`, is the injection seam CONTRACT.md
    section 2 asks for ("Inject the fetch"): every test in
    tests/test_limits.py drives this through fetch_fn, never through a
    real credentials file or network call. Defaults to _fetch_from_api,
    which does the real token read + HTTPS call."""
    global LAST_RATE_LIMITED_AT
    with _cache_lock:
        now = now_fn()
        next_attempt_at = _cache.get("next_attempt_at")
        if next_attempt_at is not None and now < next_attempt_at:
            return _cache["result"]

        fresh_result, rate_limit_retry_after = _do_fetch(now, timeout, fetch_fn)
        was_rate_limited = fresh_result["error"] == "RateLimited"
        if fresh_result["available"]:
            _cache["last_good"] = fresh_result
            result = fresh_result
        else:
            last_good = _cache.get("last_good")
            result = last_good if last_good is not None else fresh_result

        if was_rate_limited:
            if rate_limit_retry_after is not None:
                # Fix round 3: clamped, not honoured verbatim -- see
                # _clamp_retry_after's docstring for why (Retry-After: 0
                # used to remove the throttle entirely; an absurd value
                # used to wedge this device for millennia).
                backoff = _clamp_retry_after(rate_limit_retry_after)
                _cache["rate_limit_streak"] = 0
            else:
                streak = _cache.get("rate_limit_streak") or 0
                backoff = _next_rate_limit_backoff(streak)
                _cache["rate_limit_streak"] = streak + 1
            next_attempt_at = now + backoff
            # Fix round 3: LAST_RATE_LIMITED_AT + a log line are the only
            # place this state is visible -- the returned `result` itself
            # looks identical to a genuine success (available=True,
            # error=None) whenever a last_good reading exists, by design
            # (CONTRACT.md task-l5: "a 429 should not look like a
            # failure"). This fires once per REAL fetch attempt that hits
            # a 429 (the throttle above means that is once per backoff
            # window, never once per throttled call), so it never spams.
            LAST_RATE_LIMITED_AT = now
            _LOG.warning(
                "limits: rate limited (429) by the usage endpoint, "
                "backing off %.0fs before the next attempt (streak=%d)",
                backoff, _cache["rate_limit_streak"])
        else:
            _cache["rate_limit_streak"] = 0
            LAST_RATE_LIMITED_AT = None
            next_attempt_at = now + CACHE_TTL_SECONDS

        _cache["result"] = result
        _cache["attempted_at"] = now
        _cache["next_attempt_at"] = next_attempt_at
        return result


def estimate_window_tokens(percent, consumed):
    """Task-m3 (2026-09-09-usability): derives a token budget/consumed/
    remaining reading for one usage window (five_hour or seven_day) from
    two numbers this hub already has -- Anthropic's own utilization
    `percent` for that window, and `consumed`, this hub's OWN measurement
    of effective tokens spent inside that same window (see store.py's
    record_usage_sample/effective_tokens_in_window, built on the
    already-tracked cost_daily table).

    The Anthropic usage endpoint never reports a token budget, only a
    percentage (see this module's own module docstring) -- there is no
    real number to fetch. This is an ESTIMATE, arrived at by treating
    `percent` as "consumed is this fraction of the true budget" and
    solving for the budget: budget = consumed / (percent / 100), then
    remaining = budget - consumed. Every caller that surfaces this must
    label it as approximate; the returned dict carries
    "approximate": True for exactly that reason, not as a note to the
    reader alone but as a machine-checkable flag a UI can key off of.

    Returns None -- deliberately, never a fabricated dict with zeros --
    whenever the result would not be trustworthy:
      - `percent`/`consumed` are missing or not a real (non-bool)
        number: nothing to compute from.
      - `consumed` <= 0: either genuinely no usage was measured (in
        which case there is nothing to divide by that means anything)
        or, per effective_tokens_in_window's own contract, the caller
        already turned an unreliable delta into None before this was
        ever called -- either way, this function never receives a
        reason to invent a number.
      - `percent` is below TOKEN_ESTIMATE_PERCENT_FLOOR: dividing by a
        small percentage amplifies noise in `consumed` into a wildly
        swinging budget -- see that constant's own comment. This is the
        one CONTRACT.md explicitly asks for: suppress rather than print
        a wild guess.

    Never raises."""
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return None
    if not isinstance(consumed, (int, float)) or isinstance(consumed, bool):
        return None
    # NaN/inf pass the plain isinstance/type checks above (float is
    # float), but every comparison against a NaN is False -- silently
    # skipping the floor rejection below -- and either one turns the
    # division into a value round() cannot convert to an int. Rejected
    # here explicitly rather than left to raise past this function.
    if not math.isfinite(percent) or not math.isfinite(consumed):
        return None
    if consumed <= 0 or percent < TOKEN_ESTIMATE_PERCENT_FLOOR:
        return None
    budget = consumed / (percent / 100.0)
    remaining = max(0.0, budget - consumed)
    return {
        "consumed": int(round(consumed)),
        "budget": int(round(budget)),
        "remaining": int(round(remaining)),
        "approximate": True,
    }


def estimates_are_coherent(shorter, longer):
    """Fix round 1 (coordinator review, 2026-09-09): a live bug caught
    exactly the failure this checks for -- the five_hour and seven_day
    windows reported the SAME `consumed` figure, and dividing it by two
    different percents produced a five_hour `budget` LARGER than the
    seven_day one. Both are impossible on their face: the 5-hour
    window's activity is a strict subset of the 7-day window's, since
    it is the trailing slice of the same account, so a SHORTER window
    can never show more consumed tokens, nor imply a bigger budget,
    than a LONGER window that contains it.

    `shorter`/`longer` are estimate_window_tokens() results (or None) for
    two windows where `shorter`'s duration is contained within
    `longer`'s (five_hour vs seven_day, here). Returns True when there
    is nothing to compare (either is None) or the relationship holds;
    False when it is violated -- which is proof at least one of the two
    was computed from a contaminated measurement (the root cause found
    here: a device's usage cache still converging after a restart,
    whose catch-up growth looks identical to real usage in whichever
    window happens to be sampled while it is happening, regardless of
    that window's own length -- see store.py's effective_tokens_in_
    hourly_window/effective_tokens_in_daily_window (and the
    _window_has_unsettled_rows check both use) for the fix at the
    source. This is a second, independent backstop, not a substitute
    for that fix: it catches an incoherent PAIR even if some future
    change introduces a different way for one side to go bad on its
    own.

    Never raises."""
    if shorter is None or longer is None:
        return True
    return shorter["consumed"] <= longer["consumed"] and shorter["budget"] <= longer["budget"]
