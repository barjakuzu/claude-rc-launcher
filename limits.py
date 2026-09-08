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
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import noredirect

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS_PATH_LINUX = "~/.claude/.credentials.json"
KEYCHAIN_SERVICE_NAME = "Claude Code-credentials"

CACHE_TTL_SECONDS = 60
FETCH_TIMEOUT_SECONDS = 5

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
# "attempted_at": when that value was computed -- gates the 60s throttle
#   for BOTH outcomes, so a failing fetch is retried at most once per
#   CACHE_TTL_SECONDS too, not on every poll (CONTRACT.md doesn't say
#   this explicitly for the failure case; see task-l1-report.md for why
#   throttling failures the same as successes is the safer reading in a
#   long-running poll loop, unlike the statusline script this mirrors,
#   which is invoked fresh per render and effectively retries on every
#   failure until it next succeeds).
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
    for the timeout residual that leaves on Linux specifically)."""
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
    with _NO_REDIRECT_OPENER.open(req, timeout=remaining) as resp:
        data = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("usage response exceeded size limit")
    return json.loads(data)


def _do_fetch(now, timeout, fetch_fn):
    """One fetch-and-parse attempt. Never raises: any exception from the
    fetch (including CredentialsUnavailable) or from parsing its response
    becomes unavailable_result(now, error=type(e).__name__) -- section 2's
    "type name only, never str(e)" rule applied at the one place that
    ever sees the raw exception."""
    fetch = fetch_fn or _fetch_from_api
    try:
        raw = fetch(timeout)
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
        return unavailable_result(now, error="TimeoutError")
    except Exception as e:
        return unavailable_result(now, error=type(e).__name__)
    try:
        return _parse_usage_response(raw, now)
    except Exception as e:
        return unavailable_result(now, error=type(e).__name__)


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
    with _cache_lock:
        now = now_fn()
        attempted_at = _cache.get("attempted_at")
        if attempted_at is not None and now - attempted_at < CACHE_TTL_SECONDS:
            return _cache["result"]

        result = _do_fetch(now, timeout, fetch_fn)
        if result["available"]:
            _cache["last_good"] = result
        else:
            last_good = _cache.get("last_good")
            if last_good is not None:
                result = last_good
        _cache["result"] = result
        _cache["attempted_at"] = now
        return result
