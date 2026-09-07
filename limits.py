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
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

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

# Module-level cache, same shape/convention as fleet.py's own _cache:
# cleared via _cache.clear() in tests (see tests/test_limits.py setUp).
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


def _bucket(raw_bucket):
    """five_hour/seven_day: {"utilization": <num>, "resets_at": <iso>} ->
    {"percent": <float>, "resets_at": <iso or None>}. None (not a
    half-filled dict) when the source isn't usable at all, so a caller
    never has to distinguish "0%" from "missing" via a sentinel."""
    if not isinstance(raw_bucket, dict):
        return None
    percent = _num_or_none(raw_bucket.get("utilization"))
    if percent is None:
        return None
    return {"percent": percent, "resets_at": _str_or_none(raw_bucket.get("resets_at"))}


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
    if not isinstance(raw_spend, dict):
        return None
    used = raw_spend.get("used")
    used = used if isinstance(used, dict) else {}
    percent = _num_or_none(raw_spend.get("percent"))
    return {
        "used_minor": _int_or_none(used.get("amount_minor")),
        "currency": _str_or_none(used.get("currency")),
        "exponent": _int_or_none(used.get("exponent")),
        "limit_minor": _int_or_none(raw_spend.get("limit")),
        "percent": percent if percent is not None else 0.0,
        "severity": _str_or_none(raw_spend.get("severity")),
    }


def _extra_usage(raw_extra):
    if not isinstance(raw_extra, dict):
        return None
    return {
        "enabled": bool(raw_extra.get("is_enabled")),
        "utilization": _num_or_none(raw_extra.get("utilization")),
        "spend_limit_reached": bool(raw_extra.get("spend_limit_reached")),
    }


def _parse_usage_response(raw, now):
    """Raw /api/oauth/usage JSON -> CONTRACT.md section 3 shape. Reads
    ONLY the keys CONTRACT.md section 1 documents; every other key
    (`tangelo`, `nimbus_quill`, ...) is ignored outright, never even
    looked at. Raises only if `raw` itself isn't a JSON object -- every
    individual field below degrades to None/[] rather than raising, so a
    single malformed sub-object never aborts the whole parse."""
    if not isinstance(raw, dict):
        raise ValueError("usage response is not a JSON object")
    return {
        "available": True,
        "fetched_at": now,
        "five_hour": _bucket(raw.get("five_hour")),
        "seven_day": _bucket(raw.get("seven_day")),
        "scoped": _scoped_rows(raw.get("limits")),
        "spend": _spend(raw.get("spend")),
        "extra_usage": _extra_usage(raw.get("extra_usage")),
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
    temp-file fixture here rather than relying on the real default."""
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


def _read_token_macos(runner=None):
    """Reads the login Keychain entry via `security find-generic-password
    -s "Claude Code-credentials" -w`, which prints the same JSON envelope
    the Linux credentials file holds (see statusline-command.sh). `runner`
    is an injection seam for tests (defaults to subprocess.run) so a test
    never shells out to a real `security` binary or touches a real
    Keychain."""
    run = runner or subprocess.run
    try:
        proc = run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE_NAME, "-w"],
            capture_output=True, timeout=FETCH_TIMEOUT_SECONDS, check=True,
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


def _read_token(path=None, runner=None):
    if sys.platform == "darwin":
        return _read_token_macos(runner=runner)
    return _read_token_linux(path=path)


def _fetch_from_api(timeout, read_token_fn=None):
    """The one outbound HTTPS call this module ever makes. Returns the
    parsed raw JSON response; raises on any failure (no credentials,
    network error, non-2xx, oversized body, unparseable body) -- the
    caller (_do_fetch) is what turns that into
    unavailable_result(..., error=type(e).__name__), never this
    function, so the exception itself never needs to be caught more than
    once or passed around."""
    read_token = read_token_fn or _read_token
    token = read_token()
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    except Exception as e:
        return unavailable_result(now, error=type(e).__name__)
    try:
        return _parse_usage_response(raw, now)
    except Exception as e:
        return unavailable_result(now, error=type(e).__name__)


def get_limits(now_fn=time.time, timeout=FETCH_TIMEOUT_SECONDS, fetch_fn=None):
    """CONTRACT.md section 3's `limits` object for this device's account.
    Never raises and never blocks longer than `timeout` (the one urlopen
    call inside _fetch_from_api is the only blocking operation on the
    fetch path; the Keychain read on macOS carries its own timeout too).

    Caches the fetch for CACHE_TTL_SECONDS: a call within that window of
    the last attempt returns the exact same value, no I/O. On a fresh
    attempt that fails, the last available=true result (if any) is
    returned instead, UNCHANGED -- same dict, same original fetched_at --
    so a caller/UI can compute staleness from fetched_at rather than being
    handed a fresh failure that masks how long the account has actually
    gone unread.

    `fetch_fn`, `(timeout) -> dict`, is the injection seam CONTRACT.md
    section 2 asks for ("Inject the fetch"): every test in
    tests/test_limits.py drives this through fetch_fn, never through a
    real credentials file or network call. Defaults to _fetch_from_api,
    which does the real token read + HTTPS call."""
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
