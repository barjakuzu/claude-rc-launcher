"""HTTP handler and routing."""

import base64
import compat
import fleet
import hmac
import http.server
import ipaddress
import json
import logging
import os
import re
import secrets
import signal
import stats
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

import config
from config import (
    VERSION, HOST, PORT, SESSION_PREFIX, WORKING_DIR, CLAUDE_BIN,
    AUTH_USER, AUTH_PASS, RC_FLAGS, MODEL_MAP, SHELL_BIN, SHELL_MODE,
    resolve_claude_mode,
    BROWSE_ROOTS, RC_TRUSTED_PROXIES, RC_BEHIND_TLS, RC_MAX_SESSIONS,
)
from sessions import (
    list_rc_sessions, session_exists, setup_session, stop_session,
    restart_session, list_resumable_sessions, resume_session,
    get_all_session_errors, unstick_session, get_transcript,
    build_tmux_command, count_launcher_sessions, get_url_with_source,
    invalidate_adopted_url_cache, capture_adopted_window_size,
    restore_window_size,
)
from tunnel import (
    cloudflared_available, start_tunnel, stop_tunnel, get_tunnel_status,
)
import schedules
from schedules import (
    load_schedules, create_schedule, update_schedule, delete_schedule,
)
from scheduler import validate_cron, next_cron_run, _fire_schedule
from devices import (
    get_device, list_devices_public, load_devices, get_local_name, rename_device,
)
import configreport
import overview
import store
import ws as ws_terminal


def _parse_projects():
    """Parse RC_PROJECTS env var into list of {name, path, exists}."""
    raw = os.environ.get("RC_PROJECTS", "").strip()
    if not raw:
        return []
    projects = []
    for p in raw.split(","):
        p = p.strip()
        if not p:
            continue
        projects.append({
            "name": os.path.basename(p.rstrip("/")),
            "path": os.path.abspath(p),
            "exists": os.path.isdir(p),
        })
    return projects


def _enrich_next_run(schedule):
    """Return a copy of `schedule` with next_run computed. Disabled and
    manual (cron: null) schedules always get next_run: None."""
    s = dict(schedule)
    if s.get("enabled") and s.get("cron"):
        s["next_run"] = next_cron_run(s["cron"])
    else:
        s["next_run"] = None
    return s


# Login tokens persist across launcher restarts (frequent self-updates used
# to log every browser out). {token: expiry_epoch}, chmod 600.
from config import RC_HOME as _RC_HOME
_AUTH_TOKENS_FILE = os.path.join(_RC_HOME, "auth-tokens.json")
_AUTH_TOKEN_TTL = 30 * 86400  # 30 days


def _load_auth_tokens():
    try:
        with open(_AUTH_TOKENS_FILE) as f:
            data = json.load(f)
        now = time.time()
        if isinstance(data, dict):
            return {t: exp for t, exp in data.items()
                    if isinstance(exp, (int, float)) and exp > now}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {}


def _save_auth_tokens():
    try:
        # Create with 0600 atomically — never expose tokens via a
        # default-umask window between open() and chmod().
        fd = os.open(_AUTH_TOKENS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(_auth_tokens, f)
        os.chmod(_AUTH_TOKENS_FILE, 0o600)  # correct pre-existing files too
    except OSError:
        pass


_auth_tokens = _load_auth_tokens()  # {token: expiry_epoch}

_LOG = logging.getLogger(__name__)

HUB_STORE = None  # set by app.py at startup; store.Store instance

SSE_HEARTBEAT_SECONDS = 20

# RC_ROLE="metadata" enforcement: a metadata device serves only fleet
# roll-up/health data, never session control, terminal, or transcript
# content. See docs/DEVICES.md.
METADATA_ALLOWED_GET_PATHS = {"/fleet", "/version", "/stats", "/config-report"}
METADATA_REFUSED_POST_PATHS_PREFIXES = ("/start", "/keys", "/resize", "/enable-rc", "/schedules")
# Session-scoped routes are shaped "/sessions/<id>/<action>" — a prefix
# match on METADATA_REFUSED_POST_PATHS_PREFIXES never matches these, since
# the path starts with "/sessions/" not "/keys" etc. Gate on the action
# suffix instead. Exact-path routes with no session id in between are
# matched directly.
METADATA_REFUSED_POST_PATH_SUFFIXES = ("/keys", "/resize", "/enable-rc", "/preview-bye")
METADATA_REFUSED_POST_EXACT_PATHS = (
    "/resume/start", "/tunnel/start", "/tunnel/stop", "/devices/rename", "/update",
)


def _metadata_post_refused(local_path):
    """True if a metadata-role device must refuse this POST path with 403."""
    if local_path.startswith(METADATA_REFUSED_POST_PATHS_PREFIXES):
        return True
    if local_path in METADATA_REFUSED_POST_EXACT_PATHS:
        return True
    if local_path.startswith("/sessions/") and local_path.endswith(METADATA_REFUSED_POST_PATH_SUFFIXES):
        return True
    return False
# ThreadingHTTPServer spins up one thread per open connection; an
# unbounded number of open /api/fleet/stream connections is an easy way
# to exhaust threads. Above this many concurrent subscribers, new
# connections are turned away with 503 so the client falls back to
# polling GET /api/fleet instead.
SSE_MAX_SUBSCRIBERS = 32
FLEET_CHANGE_SUBSCRIBERS = set()
_fleet_change_lock = threading.Lock()


def _sse_capacity_exceeded():
    """True when SSE_MAX_SUBSCRIBERS open /api/fleet/stream connections
    are already being served -- a new one should get 503 instead of
    piling on another thread. Pure/unit-testable directly; the route
    still does the actual add-under-lock (see FLEET_CHANGE_SUBSCRIBERS)
    to avoid a check-then-add race letting two connections both slip in
    at exactly the limit."""
    with _fleet_change_lock:
        return len(FLEET_CHANGE_SUBSCRIBERS) >= SSE_MAX_SUBSCRIBERS


def notify_fleet_changed():
    """Called by fleetpoll.FleetPoller after a successful ingest. Wakes
    every open /api/fleet/stream connection."""
    with _fleet_change_lock:
        subs = list(FLEET_CHANGE_SUBSCRIBERS)
    for ev in subs:
        ev.set()


def _resolve_actor(handler):
    """Actor for the audit log: the login token id if cookie auth was
    used, "basic" for Basic auth, "unknown" otherwise. Never the
    password -- only the token/cookie value's own id (itself a random
    secret, but that's the existing session identifier, not a
    credential to protect further than the cookie already is)."""
    cookie_header = getattr(handler, "headers", {}).get("Cookie", "") if hasattr(handler, "headers") else ""
    if cookie_header and "rc_session=" in str(cookie_header):
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            if "rc_session" in cookie:
                return cookie["rc_session"].value[:12]  # short id, not a secret disclosure surface
        except Exception:
            pass
    auth_hdr = getattr(handler, "headers", {}).get("Authorization", "") if hasattr(handler, "headers") else ""
    if str(auth_hdr).startswith("Basic "):
        return "basic"
    return "unknown"


def _audit(handler, action, target, device_id="local", detail=""):
    """Write one audit_log row. A no-op (never raises) when HUB_STORE is
    None -- a device running without a hub role, or a test that hasn't
    set it up."""
    if HUB_STORE is None:
        return
    try:
        HUB_STORE.add_audit(actor=_resolve_actor(handler), action=action,
                             target=target or "", device_id=device_id or "local", detail=detail)
    except Exception:
        _LOG.exception("audit log write failed for action=%r target=%r", action, target)


def _auth_token_valid(token):
    exp = _auth_tokens.get(token)
    if exp is None:
        return False
    if exp < time.time():
        _auth_tokens.pop(token, None)
        _save_auth_tokens()
        return False
    return True

# Live preview viewers per session:
# {session: {viewer_id: (cols, rows, ts_seen, ts_active)}}.
# Each /preview poll refreshes ts_seen; opening the preview or typing bumps
# ts_active. The window takes the size of the MOST RECENTLY ACTIVE live
# viewer (tmux 'window-size latest' behavior) — a phone glancing at a
# session doesn't shrink the desktop for good, and whoever interacts last
# gets a native layout. Restored to 200×50 when the last viewer leaves.
_preview_viewers = {}
_preview_applied = {}   # {session: (cols, rows)} last size we set
_PREVIEW_VIEWER_TTL = 6  # seconds without a poll → viewer considered gone
_preview_lock = threading.Lock()


def _apply_preview_size(name):
    """Recompute and apply the effective window size for a session."""
    if not name.startswith(SESSION_PREFIX):
        # First touch of an adopted (foreign) session's window: remember
        # its size before we ever resize it, so the "no live viewers"
        # branch below restores THAT instead of our 200x50 default.
        capture_adopted_window_size(name)
    with _preview_lock:
        now = time.time()
        live = {v: s for v, s in _preview_viewers.get(name, {}).items()
                if now - s[2] < _PREVIEW_VIEWER_TTL}
        if live:
            _preview_viewers[name] = live
            cols, rows, _, _ = max(live.values(), key=lambda s: s[3])
        else:
            _preview_viewers.pop(name, None)
            size = restore_window_size(name)
            if size is None:
                # Adopted session with no captured size (capture failed):
                # leave its window alone rather than guessing 200x50.
                return
            cols, rows = size
        if _preview_applied.get(name) == (cols, rows):
            return
        _preview_applied[name] = (cols, rows)
    subprocess.run(
        ["tmux", "resize-window", "-t", name, "-x", str(cols), "-y", str(rows)],
        capture_output=True, timeout=5,
    )


def _preview_viewer_seen(name, viewer, cols, rows, active=False):
    with _preview_lock:
        prev = _preview_viewers.get(name, {}).get(viewer)
        ts_active = time.time() if (active or prev is None) else prev[3]
        _preview_viewers.setdefault(name, {})[viewer] = (cols, rows, time.time(), ts_active)
    _apply_preview_size(name)


def _preview_viewer_bye(name, viewer):
    with _preview_lock:
        _preview_viewers.get(name, {}).pop(viewer, None)
    _apply_preview_size(name)


# Rate limiting for login attempts: {ip: [(timestamp, ...)] }
_login_attempts = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 300      # 5 minutes
_LOGIN_LOCKOUT = 900     # 15 minutes


def _is_rate_limited(ip):
    """Check if an IP is rate-limited for login attempts."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Clean old attempts
    attempts = [t for t in attempts if now - t < _LOGIN_LOCKOUT]
    _login_attempts[ip] = attempts
    # Count recent attempts within window
    recent = [t for t in attempts if now - t < _LOGIN_WINDOW]
    return len(recent) >= _LOGIN_MAX_ATTEMPTS


_LOG_SAFE_RE = re.compile(r"[^A-Za-z0-9._@:-]")


def _log_safe(value, max_len=64):
    """Sanitize a value for inclusion in a single log line: strip anything
    that isn't alphanumeric or one of `._@:-` (in particular newlines, so a
    caller can't inject extra log lines or forge fields), then truncate."""
    return _LOG_SAFE_RE.sub("_", value)[:max_len]


def _record_failed_login(ip, user=""):
    """Record a failed login attempt and log a stable line fail2ban can
    match (see docs/fail2ban/claude-rc.conf). Both fields are attacker
    controlled (user comes straight from the login form; ip may come from a
    trusted-but-misconfigured proxy) so both are sanitized before logging."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)
    print(f"AUTH FAIL ip={_log_safe(ip)} user={_log_safe(user)}")


def _check_auth(handler):
    """Return True if auth passes (cookie, Basic Auth, or auth not configured)."""
    if not AUTH_USER or not AUTH_PASS:
        return True
    # Check session cookie first
    cookie_header = handler.headers.get("Cookie", "")
    if cookie_header:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        if "rc_session" in cookie and _auth_token_valid(cookie["rc_session"].value):
            return True
    # Fall back to Basic Auth (for curl/API)
    auth_header = handler.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            user, password = decoded.split(":", 1)
            return hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(password, AUTH_PASS)
        except Exception:
            pass
    return False


def _check_basic_auth(handler):
    """Check only Basic Auth header. Returns True if valid."""
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        user, password = decoded.split(":", 1)
        return hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(password, AUTH_PASS)
    except Exception:
        return False


def _send_auth_required(handler):
    """Send 401 or redirect to login depending on request type.

    We deliberately do NOT send a `WWW-Authenticate: Basic` header on the 401
    path: that would cause browsers (especially Mobile Safari) to show their
    native HTTP-Basic credentials dialog over our custom /login page. The SPA
    catches 401 in api.ts and navigates to /login itself; curl/API consumers
    send Basic Auth proactively (Authorization header still works server-side).
    """
    accept = handler.headers.get("Accept", "")
    # Browser navigation gets a redirect; XHR/fetch from the SPA gets 401 JSON.
    if "text/html" in accept and not handler.headers.get("Authorization"):
        handler.send_response(302)
        handler.send_header("Location", "/login")
        handler.end_headers()
    else:
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"error":"auth required"}')


def _login_html(csrf_token="", error=""):
    """Generate the login page HTML with CSRF token. V5 design system."""
    err_style = "display:flex" if error else "display:none"
    err_msg = error or "Invalid credentials"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Claude RC — Sign in</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='%231a1a18'/><rect x='5' y='5' width='54' height='54' rx='11' fill='none' stroke='%233d3d39' stroke-width='2'/><text x='31' y='43' font-family='ui-monospace,Menlo,monospace' font-size='28' font-weight='700' fill='%23e8e7e3' text-anchor='middle'>rc</text><circle cx='50' cy='15' r='5' fill='%234ade80'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500;600&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:        oklch(0.155 0.004 80);
  --bgRaised:  oklch(0.195 0.006 80);
  --panel:     oklch(0.215 0.006 80);
  --card:      oklch(0.225 0.006 80);
  --border:    oklch(0.28 0.007 80);
  --borderHi:  oklch(0.36 0.009 80);
  --text:      oklch(0.96 0.004 80);
  --textDim:   oklch(0.72 0.006 80);
  --textLow:   oklch(0.52 0.007 80);
  --accent:    oklch(0.70 0.10 250);
  --red:       oklch(0.62 0.12 25);
  --redSoft:   oklch(0.62 0.12 25 / 0.12);
  --redEdge:   oklch(0.62 0.12 25 / 0.35);
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ height: 100%; background: var(--bg); color: var(--text); }}
body {{
  font-family: 'Inter', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}}
.wrap {{
  width: 100%; max-width: 400px;
  display: flex; flex-direction: column; gap: 18px;
}}
.brand {{
  display: flex; align-items: center; gap: 10px; justify-content: center;
  margin-bottom: 6px;
}}
.brand .mark {{
  width: 28px; height: 28px; border-radius: 7px;
  border: 1px solid var(--borderHi); background: var(--panel);
  display: flex; align-items: center; justify-content: center;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; font-weight: 600; letter-spacing: .02em;
  color: var(--text);
}}
.brand .name {{ font-size: 15px; font-weight: 600; letter-spacing: -.005em; }}
.card {{
  background: var(--bgRaised);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px 26px;
  box-shadow: 0 12px 40px rgba(0,0,0,.35);
}}
.title {{
  font-size: 20px; font-weight: 600; letter-spacing: -.015em;
  margin-bottom: 4px;
}}
.subtitle {{
  font-size: 11px; color: var(--textLow);
  font-family: 'Geist Mono', ui-monospace, monospace;
  letter-spacing: .14em; text-transform: uppercase;
  margin-bottom: 22px;
}}
.error {{
  {err_style}; align-items: center; gap: 8px;
  background: var(--redSoft); border: 1px solid var(--redEdge);
  color: var(--red); border-radius: 8px;
  padding: 9px 12px; font-size: 12.5px;
  margin-bottom: 14px;
}}
.field {{ margin-bottom: 12px; }}
.field label {{
  display: block; font-size: 9.5px; color: var(--textLow);
  font-family: 'Geist Mono', ui-monospace, monospace;
  letter-spacing: .14em; text-transform: uppercase;
  margin-bottom: 6px;
}}
.field input {{
  width: 100%; padding: 11px 13px;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text); font-size: 14px; font-family: inherit;
  outline: none; color-scheme: dark;
  transition: border-color .12s, background .12s;
}}
.field input:hover {{ border-color: var(--borderHi); }}
.field input:focus {{ border-color: var(--accent); background: var(--card); }}
.field input:-webkit-autofill {{
  -webkit-box-shadow: 0 0 0 30px var(--panel) inset !important;
  -webkit-text-fill-color: var(--text) !important;
  border-color: var(--border) !important;
}}
.btn {{
  width: 100%; padding: 12px 16px; margin-top: 10px;
  background: var(--text); color: var(--bg);
  border: none; border-radius: 8px;
  font-family: inherit; font-size: 13.5px; font-weight: 600;
  letter-spacing: -.005em; cursor: pointer;
  transition: opacity .12s;
}}
.btn:hover {{ opacity: 0.88; }}
.btn:active {{ opacity: 0.78; }}
.foot {{
  text-align: center; font-size: 10.5px; color: var(--textLow);
  font-family: 'Geist Mono', ui-monospace, monospace;
  letter-spacing: .04em;
}}
.foot a {{ color: var(--textDim); text-decoration: none; }}
.foot a:hover {{ color: var(--text); }}
</style></head>
<body>
<div class="wrap">
  <div class="brand">
    <div class="mark">rc</div>
    <div class="name">Claude RC</div>
  </div>
  <form class="card" method="POST" action="/login" autocomplete="on">
    <input type="hidden" name="csrf" value="{csrf_token}">
    <div class="title">Sign in</div>
    <div class="subtitle">Session launcher</div>
    <div class="error">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
      <span>{err_msg}</span>
    </div>
    <div class="field"><label for="user">Username</label><input id="user" name="user" type="text" autofocus required autocomplete="username"></div>
    <div class="field"><label for="pass">Password</label><input id="pass" name="pass" type="password" required autocomplete="current-password"></div>
    <button type="submit" class="btn">Sign in</button>
  </form>
  <div class="foot">claude-rc · <a href="https://github.com/barjakuzu/claude-rc-launcher" rel="noopener">github</a></div>
</div>
</body></html>"""


def _load_html(auth_header=""):
    """Load the frontend HTML from static/index.html, injecting auth token."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(html_path, "r") as f:
        html = f.read()
    # Inject the auth token so JS can attach it to API calls.
    # Mobile Safari doesn't forward Basic Auth on XHR/fetch.
    if auth_header:
        token_script = f'<script>window.__RC_AUTH="{auth_header}";</script>'
        html = html.replace("</head>", token_script + "</head>", 1)
    return html


_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".html": "text/html",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _valid_session_name(name):
    """True if `name` is safe to use as a tmux session identifier: non-empty,
    no path traversal or separators, and carries our 'rc-' prefix so a
    logged-in browser can only reach sessions the launcher itself created."""
    return bool(name) and ".." not in name and "/" not in name and name.startswith(SESSION_PREFIX)


def _adopted_tmux_names(rows=None):
    """Tmux session names of every currently-adopted external session —
    an external row from list_rc_sessions() whose "tmux" mapping is not
    None. This is the ONLY set of non-rc-* names /preview, /ws, /keys,
    and /resize may ever address; it is recomputed per request (never
    cached) so a session that stops being adopted (process exits, pane
    closes) can't be reached a moment later on a stale allowlist."""
    if rows is None:
        rows = list_rc_sessions()
    return {s["tmux"]["session_name"] for s in rows if s.get("external") and s.get("tmux")}


def _adopted_pane_id(name, rows=None):
    """pane_id (e.g. "%7") of the adopted external row whose tmux session
    name is `name`, or None if `name` isn't currently adopted. enable-rc
    must target this pane_id, never the raw session name from the URL and
    never anything from the request body — the pane_id comes only from
    our own freshly-recomputed adoption set."""
    if rows is None:
        rows = list_rc_sessions()
    for s in rows:
        if s.get("external") and s.get("tmux") and s["tmux"].get("session_name") == name:
            return s["tmux"].get("pane_id")
    return None


def _session_name_allowed(name, adopted=None):
    """True if `name` is safe to address for /preview, /ws, /keys, /resize:
    either an rc-* launcher session (_valid_session_name, unchanged), or a
    tmux session name currently in the adoption allowlist. `adopted` is
    injectable for tests; production callers leave it unset and it is
    computed lazily (only when the rc-* check fails) via
    _adopted_tmux_names()."""
    if _valid_session_name(name):
        return True
    if adopted is None:
        adopted = _adopted_tmux_names()
    return bool(name) and ".." not in name and "/" not in name and name in adopted


def _new_session_id():
    """One session id per /start call, passed to build_tmux_command so a
    session that supports --session-id gets a known UUID from birth
    instead of one discovered later by scanning JSONL titles."""
    return str(uuid.uuid4())


def _update_confirmed(confirm, remote_sha):
    """True if the client explicitly confirmed the exact commit to update
    to. Prevents a single stray POST /update from silently deploying
    whatever happens to be on origin/main at that instant."""
    return bool(remote_sha) and confirm == remote_sha


def _do_git_update_phase(app_dir, confirm, run=subprocess.run):
    """Run the git fetch/rev-parse/(log)/merge sequence for POST /update.

    Extracted so a hung or missing git binary can't drop the connection: a
    subprocess.TimeoutExpired (a git call outliving its timeout) or OSError
    (e.g. git not installed) is caught here and turned into a normal error
    response instead of propagating out of the request handler.

    Returns (http_status, response_dict, merged_sha). merged_sha is the
    commit that was ff-only merged, or None if nothing was merged (error,
    or confirmation still needed)."""
    try:
        fetch = run(["git", "-C", app_dir, "fetch", "origin", "main"],
                    capture_output=True, text=True, timeout=30)
        if fetch.returncode != 0:
            return 500, {"ok": False, "message": f"git fetch failed: {fetch.stderr.strip()}"}, None

        head = run(["git", "-C", app_dir, "rev-parse", "origin/main"],
                   capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return 500, {"ok": False, "message": f"git rev-parse failed: {head.stderr.strip()}"}, None
        remote_sha = head.stdout.strip()

        if not _update_confirmed(confirm, remote_sha):
            log = run(["git", "-C", app_dir, "log", "--oneline", f"HEAD..{remote_sha}"],
                      capture_output=True, text=True, timeout=10)
            pending = log.stdout.strip().splitlines() if log.returncode == 0 else []
            return 409, {"ok": False,
                         "message": "Confirm the exact commit to update to (see remote_sha / pending_commits).",
                         "remote_sha": remote_sha, "pending_commits": pending}, None

        merge = run(["git", "-C", app_dir, "merge", "--ff-only", remote_sha],
                    capture_output=True, text=True, timeout=30)
        if merge.returncode != 0:
            return 500, {"ok": False, "message": f"git merge failed: {merge.stderr.strip()}"}, None

        return 200, {"ok": True}, remote_sha
    except (subprocess.TimeoutExpired, OSError) as e:
        return 500, {"ok": False, "message": f"git failed: {e}"}, None


def _version_response():
    """Body for GET /version - the launcher version plus the detected
    capabilities of the installed `claude` binary, extracted so it's
    testable without a live socket. Calls compat.get_caps() (lazy,
    memoized) rather than reading compat.CAPS directly so this works
    correctly whether or not detection has run yet."""
    caps = compat.get_caps()
    return {"version": VERSION, "claude_version": caps.get("version"), "caps": caps}


def _pick_restart_command(system_unit_active, user_unit_active, launchd_active, uid):
    """Pick the command to restart the launcher, in priority order: an
    active system unit, then a user unit, then a detected launchd agent.
    Returns None if none apply - the operator restarts manually."""
    if system_unit_active:
        return ["systemctl", "restart", "claude-rc-launcher"]
    if user_unit_active:
        return ["systemctl", "--user", "restart", "claude-rc"]
    if launchd_active:
        return ["launchctl", "kickstart", "-k", f"gui/{uid}/com.claude-rc.launcher"]
    return None


def _detect_active(run, cmd, timeout=10):
    """True if `cmd` runs and exits 0. Each detection command is isolated:
    a missing binary or a hang for one mechanism (e.g. no systemctl on
    macOS) must not prevent trying the others."""
    try:
        return run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _detect_and_restart(run=None):
    """Restart the launcher via whichever install mechanism is active, and
    return a human-readable status message. Detection tries, in order: an
    active system systemd unit, an active user systemd unit, and (via
    `launchctl print gui/<uid>/com.claude-rc.launcher`) a registered macOS
    launchd agent. Falls back to a manual-restart message only when all
    three detection attempts fail, rather than raising out of the /update
    handler. `run` is injectable for tests; defaults to subprocess.run."""
    if run is None:
        run = subprocess.run
    uid = os.getuid()
    system_active = _detect_active(run, ["systemctl", "is-active", "--quiet", "claude-rc-launcher"])
    user_active = _detect_active(run, ["systemctl", "--user", "is-active", "--quiet", "claude-rc"])
    launchd_active = sys.platform == "darwin" and _detect_active(
        run, ["launchctl", "print", f"gui/{uid}/com.claude-rc.launcher"])

    cmd = _pick_restart_command(system_active, user_active, launchd_active, uid)
    if cmd is None:
        return "Restart manually to apply the update."

    def _run_delayed():
        time.sleep(1)
        try:
            run(cmd, capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    threading.Thread(target=_run_delayed, daemon=True).start()
    return f"Restarting ({' '.join(cmd)})..."


def _session_cap_message(current_count, max_sessions):
    """None if under the session cap or the cap is disabled (max_sessions
    <= 0), else the 429 message to return."""
    if max_sessions <= 0:
        return None
    if current_count >= max_sessions:
        return f"Session cap reached ({max_sessions}). Stop a session first."
    return None


_config_report_cache = {"report": None, "at": 0.0}
_CONFIG_REPORT_TTL_SECONDS = 60


def _get_cached_config_report(now_fn=time.time):
    """GET /config-report backing store: collect_config_report() shells out
    to git/find plugin dirs, so cache the result in-process for 60s rather
    than re-collecting on every hub fan-out request."""
    now = now_fn()
    if _config_report_cache["report"] is not None and now - _config_report_cache["at"] < _CONFIG_REPORT_TTL_SECONDS:
        return _config_report_cache["report"]
    report = configreport.collect_config_report()
    _config_report_cache["report"], _config_report_cache["at"] = report, now
    return report


# A launcher session younger than this still reports status unknown/None
# while its pane settles — after this age, an unknown status is treated as
# idle rather than starting forever (a session that's been "starting" for
# minutes almost certainly just never finished booting).
STARTING_GRACE_SECONDS = 90


def _derive_session_state(session_row, now=None):
    """One of starting|busy|idle|needs_attention|ended from a session row
    (either shape list_rc_sessions returns: a launcher rc-* row with
    status running|dead|unknown, or an external row with status from
    claude agents --json: idle|busy|ended). `now` is injectable for tests;
    defaults to time.time()."""
    if session_row.get("waiting_for"):
        return "needs_attention"
    if (session_row.get("claude") or {}).get("state") == "blocked":
        return "needs_attention"
    status = session_row.get("status")
    if status == "dead" or status == "ended":
        return "ended"
    if status == "busy":
        return "busy"
    if status in ("unknown", None) and session_row.get("kind") != "external":
        created_at = session_row.get("created_at")
        if created_at is None:
            # tmux `#{session_created}` was unavailable — treat as past the
            # grace period rather than staying "starting" forever (the
            # claude row's waiting_for/blocked checks above still take
            # precedence when they apply).
            return "idle"
        if now is None:
            now = time.time()
        if now - created_at < STARTING_GRACE_SECONDS:
            return "starting"
        return "idle"
    return "idle"


# CONTROLLER RULING (overrides the original task-8 brief): the hook set
# was cut to five events -- StopFailure, Notification, SubagentStop,
# PreCompact, SessionEnd -- so Stop/UserPromptSubmit no longer produce
# events and "no later Stop/UserPromptSubmit" can't be used as the
# clearing signal. needs_attention instead comes primarily from each
# session's own polled state (fleetpoll._ingest sets sessions[i]["state"]
# via _derive_session_state before writing to the store, so the store's
# `state` column already reflects waiting_for/blocked between hub polls).
# A `Notification` event raises it immediately without waiting for the
# next 30s poll; a `SessionEnd` event clears it immediately. Any other
# event type (StopFailure, SubagentStop, PreCompact) is not decisive and
# is skipped when scanning for the newest relevant event.
def _derive_needs_attention(events):
    """events: iterable of {session_id, ts, event, extra}. Returns
    {session_id: bool}, present only for sessions with a decisive event
    (the newest Notification or SessionEnd for that session) -- absent
    entries mean "no override, use the session's polled state instead"
    (see the /api/fleet and /api/fleet/stream routes, which fall back to
    session["state"] == "needs_attention" via .get(session_id, base))."""
    by_session = {}
    for e in events:
        sid = e.get("session_id")
        if not sid:
            continue
        by_session.setdefault(sid, []).append(e)
    result = {}
    for sid, rows in by_session.items():
        rows = sorted(rows, key=lambda r: r.get("ts") or 0, reverse=True)
        for r in rows:
            ev = r.get("event")
            if ev == "Notification":
                result[sid] = True
                break
            if ev == "SessionEnd":
                result[sid] = False
                break
    return result


def _apply_needs_attention(sessions_rows, events):
    """Mutates each row in sessions_rows (store.fleet_view()["sessions"]
    shape) in place, adding needs_attention: bool. Base comes from the
    session's own polled state; a session-scoped Notification/SessionEnd
    event can override it between polls."""
    overrides = _derive_needs_attention(events)
    for s in sessions_rows:
        sid = s.get("session_id")
        base = s.get("state") == "needs_attention"
        s["needs_attention"] = overrides.get(sid, base)
    return sessions_rows


def _stoppable_session_names(rows):
    """Names to `stop_session` (tmux kill-session) for /stop-all. External
    rows have no rc-* tmux session of their own — stop_session would
    `tmux kill-session -t <name>` against whatever tmux session (if any)
    happens to share that row's synthesized/claude name, which is not
    this row's process at all, so external rows are excluded here. An
    external session is only stoppable explicitly, via POST /stop
    {external: true, pid: ...}."""
    return [s["name"] for s in rows if not s.get("external")]


def _validate_stop_pid(raw_pid):
    """Validate a pid from a POST /stop {external: true, pid: ...} body
    before it ever reaches /proc: (pid, None) if it parses as a positive
    int that isn't 1 (init) or our own process, else (None, reason)."""
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return None, "Invalid pid"
    if pid <= 1 or pid == os.getpid():
        return None, "Invalid pid"
    return pid, None


ENABLE_RC_POLL_SECONDS = 20
ENABLE_RC_POLL_INTERVAL = 0.5

# Guards against two overlapping POST /sessions/<name>/enable-rc calls for
# the same session (e.g. a double click, or two browser tabs) racing to
# type "/remote-control" into the same pane twice. {name: started_ts}.
_enable_rc_in_flight = {}
_enable_rc_in_flight_lock = threading.Lock()


_PANE_ID_RE = re.compile(r"^%\d+$")


def _keys_target(name, rows=None):
    """tmux target to address for send-keys against `name`: for an adopted
    external row, its validated pane_id (_adopted_pane_id + _valid_pane_id)
    so keystrokes land on the exact pane we adopted rather than whatever
    tmux resolves `name` to (a session name can outlive/mismatch the pane
    once other windows exist); for an rc-* launcher session, `name` itself
    unchanged. Falls back to `name` if the adoption record has no valid
    pane_id (e.g. it vanished between lookup and use) — the caller already
    checked session_exists(name), so this still addresses something real."""
    if name.startswith(SESSION_PREFIX):
        return name
    pane_id = _adopted_pane_id(name, rows)
    return pane_id if _valid_pane_id(pane_id) else name


def _valid_pane_id(pane_id):
    """True only for a well-formed tmux pane_id like "%7". Guards the
    enable-rc route: pane_id must be present and match this shape before
    it is ever placed into a tmux send-keys argv, so a None (adoption
    record vanished between lookup and use) or malformed pane_id can
    never reach subprocess."""
    return isinstance(pane_id, str) and bool(_PANE_ID_RE.match(pane_id))


def _enable_rc_for_adopted(name, pane_id, run=subprocess.run, sleep=time.sleep, now_fn=time.time):
    """POST /sessions/<name>/enable-rc backing logic for an already-adopted
    external tmux session (`name` is the tmux session name, validated by
    the caller against _adopted_tmux_names() before this is ever reached):
    type '/remote-control' into the pane, press Enter, then poll
    get_url_with_source(name) for up to ENABLE_RC_POLL_SECONDS for an
    'osc8' URL (the only trustworthy signal RC actually activated).
    Never sends anything but that literal command string.

    Both send-keys calls target `pane_id` (e.g. "%7"), not `name` — the
    caller resolves pane_id from our own freshly-recomputed adoption
    record (_adopted_pane_id), never from the request body, so this can
    never be pointed at an arbitrary pane."""
    r = run(["tmux", "send-keys", "-t", pane_id, "-l", "/remote-control"],
            capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return {"ok": False, "message": "Could not send /remote-control to the pane"}
    r2 = run(["tmux", "send-keys", "-t", pane_id, "Enter"],
             capture_output=True, text=True, timeout=5)
    if r2.returncode != 0:
        return {"ok": False, "message": "Could not send Enter to the pane"}
    # This endpoint polls get_url_with_source directly (bypassing
    # sessions._cached_adopted_url), but a stale cached miss for this
    # session would otherwise still serve /sessions and /overview callers
    # for up to ADOPTION_URL_CACHE_MISS_TTL seconds after RC activates.
    invalidate_adopted_url_cache(name)
    deadline = now_fn() + ENABLE_RC_POLL_SECONDS
    while now_fn() < deadline:
        url, source = get_url_with_source(name)
        if source == "osc8" and url:
            invalidate_adopted_url_cache(name)
            return {"ok": True, "url": url}
        sleep(ENABLE_RC_POLL_INTERVAL)
    return {"ok": False,
            "message": f"Remote Control did not activate within {ENABLE_RC_POLL_SECONDS}s"}


def _stop_external_pid(pid, run=subprocess.run):
    """Stop a non-launcher (external) session by signaling its process
    directly, only after verifying /proc/<pid>/cmdline's first argv token
    is literally 'claude' — this is the only guard between "stop any
    session shown in the UI" and "kill an arbitrary pid a browser named",
    since external rows have no rc-* tmux session to scope the request to.
    `run` is accepted for interface symmetry with other server.py helpers
    that inject subprocess.run for testability, but the actual check reads
    /proc directly (Linux-only) rather than shelling out.

    Caller must pass a validated pid (positive int, not 1, not our own
    pid) — this function does not re-derive that, it only verifies the
    /proc identity check before signaling.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return False, "Process not found"
    except PermissionError:
        return False, "Permission denied"
    except OSError:
        return False, "Process not found"
    parts = raw.split(b"\x00")
    argv0 = parts[0].decode(errors="replace") if parts else ""
    if os.path.basename(argv0) != "claude":
        return False, "Not a claude process"
    try:
        os.kill(pid, signal.SIGTERM)
    except PermissionError:
        return False, "Permission denied"
    except ProcessLookupError:
        return False, "Process not found"
    return True, "Stopped"


def _cookie_secure_flag(behind_tls, forwarded_proto):
    """True if the Secure cookie attribute should be set: either the
    operator has explicitly said we sit behind TLS termination, or the
    proxy told us this particular request arrived over https."""
    return bool(behind_tls) or forwarded_proto == "https"


def _is_valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _resolve_client_ip(peer_ip, real_ip_header, forwarded_for_header, trusted_proxies):
    """Return the IP to use for login rate limiting.

    X-Real-IP / X-Forwarded-For are attacker-controlled unless the request
    actually came through a proxy we trust (nginx on the same box, by
    default) - otherwise anyone can spoof them to dodge the lockout. Even
    from a trusted proxy the header value must parse as a real IP address,
    or a misconfigured/compromised proxy could forward garbage straight
    into the rate limiter and the AUTH FAIL log."""
    if peer_ip not in trusted_proxies:
        return peer_ip
    candidate = real_ip_header or (forwarded_for_header.split(",")[0].strip() if forwarded_for_header else "")
    if candidate and _is_valid_ip(candidate):
        return candidate
    return peer_ip


class Handler(http.server.BaseHTTPRequestHandler):
    def _serve_static(self, path):
        """Serve a file from the static/ directory."""
        # Strip leading /static/
        rel = path[len("/static/"):]
        if not rel:
            self.send_error(403)
            return
        static_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))
        filepath = os.path.realpath(os.path.join(static_dir, rel))
        if not filepath.startswith(static_dir + os.sep):
            self.send_error(403)
            return
        if not os.path.isfile(filepath):
            self.send_error(404)
            return
        ext = os.path.splitext(filepath)[1].lower()
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _sse_write(self, text):
        """Write one raw SSE chunk. Returns False (and swallows the error)
        if the client has disconnected, so the stream loop can exit
        cleanly instead of raising into the request-handling thread."""
        try:
            self.wfile.write(text.encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _sse_send_fleet_snapshot(self):
        """Tri-state: True on a successful write, False only on an actual
        client disconnect (caller should stop the stream), None on a
        store-side read error (caller should keep the subscriber and
        retry on the next heartbeat/change -- a store hiccup is not the
        client going away)."""
        if HUB_STORE is None:
            view = {"devices": [], "sessions": []}
        else:
            try:
                view = HUB_STORE.fleet_view()
                recent = HUB_STORE.recent_events(limit=500)
                _apply_needs_attention(view["sessions"], recent)
            except Exception:
                # A store hiccup (e.g. a transient StoreClosed during
                # shutdown) must not be treated as "client disconnected"
                # -- it isn't a transport error, so don't kill the SSE
                # loop or discard the subscriber over it. Skip this one
                # snapshot; the next heartbeat/change retries.
                _LOG.exception("fleet stream: snapshot read failed")
                return None
        return self._sse_write(f"data: {json.dumps(view)}\n\n")

    def _html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(content.encode())

    def _read_body(self, max_size=1_000_000):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > max_size:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _target_device(self):
        """Device id the request targets, from X-RC-Device header or ?device=."""
        dev_id = self.headers.get("X-RC-Device", "")
        if not dev_id:
            qs = parse_qs(urlparse(self.path).query)
            dev_id = qs.get("device", [""])[0]
        return dev_id

    def _should_proxy(self, dev_id):
        """True if this request should be forwarded to a remote device.

        Static assets and the hub's own device list are always served locally.
        """
        if not dev_id or dev_id == "local":
            return False
        p = self.path.split('?')[0]
        if p.startswith("/rc"):
            p = p[3:]
        if (p.startswith("/static/") or p == "/devices" or p == "/devices/rename"
                or p == "/api/config-matrix" or p == "/api/fleet" or p == "/api/fleet/stream"
                or p == "/api/audit" or p.startswith("/api/sessions/")):
            return False
        return True

    def _proxy_to_device(self, device):
        """Forward the current request to a remote device's app and relay back."""
        target = device["base_url"].rstrip("/") + self.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length > 0 else None
        req = urllib.request.Request(target, data=body, method=self.command)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        # Use the device's own credentials, never the hub session/cookie.
        user, pw = device.get("auth_user", ""), device.get("auth_pass", "")
        if user or pw:
            token = base64.b64encode(f"{user}:{pw}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data, status = resp.read(), resp.status
                resp_ct = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, status = e.read(), e.code
            resp_ct = e.headers.get("Content-Type", "application/json")
        except Exception as e:
            return self._json({"error": "device unreachable", "detail": str(e)}, 502)
        self.send_response(status)
        self.send_header("Content-Type", resp_ct)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Login/logout routes — no auth required
        raw_path = self.path.split('?')[0]
        if raw_path in ("/login", "/rc/login"):
            csrf = secrets.token_hex(16)
            error = "Invalid credentials" if "err=1" in self.path else ""
            if "err=2" in self.path:
                error = "Too many attempts. Try again later."
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            csrf_secure = "; Secure" if _cookie_secure_flag(RC_BEHIND_TLS, self.headers.get("X-Forwarded-Proto")) else ""
            self.send_header("Set-Cookie", f"csrf={csrf}; Path=/; HttpOnly; SameSite=Strict{csrf_secure}")
            self.end_headers()
            self.wfile.write(_login_html(csrf, error).encode())
            return
        if raw_path in ("/logout", "/rc/logout"):
            cookie_header = self.headers.get("Cookie", "")
            if cookie_header:
                cookie = SimpleCookie()
                cookie.load(cookie_header)
                if "rc_session" in cookie:
                    _auth_tokens.pop(cookie["rc_session"].value, None)
                    _save_auth_tokens()
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "rc_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            return

        # Public app icons + manifest — iOS fetches the home-screen icon
        # WITHOUT cookies, so these must not require auth. Nothing sensitive.
        icon_path = raw_path[3:] if raw_path.startswith("/rc/") else raw_path
        if icon_path in ("/static/apple-touch-icon.png", "/static/icon-192.png",
                         "/static/icon-512.png", "/static/manifest.json",
                         "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            if icon_path.startswith("/apple-touch-icon"):
                icon_path = "/static/apple-touch-icon.png"
            return self._serve_static(icon_path)

        if not _check_auth(self):
            return _send_auth_required(self)

        if config.RC_ROLE == "metadata":
            clean = self.path.split('?')[0]
            local_path = clean[3:] if clean.startswith("/rc") else clean
            # "/" and "/legacy" serve the dashboard shell itself (static
            # HTML that then calls the allowed JSON endpoints from the
            # browser) -- there's no session data in the page markup, so
            # they're safe to allow through even though they aren't in
            # METADATA_ALLOWED_GET_PATHS.
            is_dashboard_shell = local_path in ("/", "/legacy")
            is_static_asset = (local_path.startswith("/static/")
                                or local_path.startswith("/rc/static/"))
            is_allowed_route = local_path in METADATA_ALLOWED_GET_PATHS
            not_generally_allowed = (
                not is_allowed_route and not is_dashboard_shell and not is_static_asset)
            # /ws (terminal socket) and /preview (transcript/pane replay)
            # carry live session content and must always be refused, even
            # for an otherwise-allowed path prefix.
            is_session_content = (
                local_path.endswith("/ws") or local_path.endswith("/preview"))
            if not_generally_allowed or is_session_content:
                return self._json({"ok": False, "message": "This device is metadata-role only"}, 403)

        # Route to a remote device if one is selected.
        dev_id = self._target_device()
        if self._should_proxy(dev_id):
            device = get_device(dev_id)
            if device is None:
                return self._json({"error": "unknown device"}, 404)
            # WebSocket upgrades can't go through urllib — raw TCP tunnel.
            if (self.headers.get("Upgrade", "").lower() == "websocket"
                    and self.path.split('?')[0].endswith("/ws")):
                self.close_connection = True
                return ws_terminal.tunnel_to_device(self, device)
            return self._proxy_to_device(device)

        # Serve static files (handles both /static/* and /rc/static/*)
        static_path = self.path.split('?')[0]
        if static_path.startswith("/rc"):
            static_path = static_path[3:]
        if static_path.startswith("/static/"):
            return self._serve_static(static_path)

        path = self.path.split('?')[0]
        if path.startswith("/rc"):
            path = path[3:] or "/"

        if path == "/":
            return self._serve_static("/static/dist/index.html")

        elif path == "/legacy":
            auth_hdr = self.headers.get("Authorization", "")
            return self._html(_load_html(auth_hdr))

        elif path == "/sessions":
            sessions = list_rc_sessions()
            for s in sessions:
                s["state"] = _derive_session_state(s)
            errors = get_all_session_errors()
            resp = {"sessions": sessions}
            if errors:
                resp["errors"] = errors
            self._json(resp)

        elif path.split('?')[0] == "/fleet":
            qs = parse_qs(urlparse(self.path).query)
            since = qs.get("since", [None])[0]
            self._json(fleet.build_fleet(since=since))

        elif path == "/api/fleet":
            if HUB_STORE is None:
                return self._json({"devices": [], "sessions": []})
            view = HUB_STORE.fleet_view()
            recent = HUB_STORE.recent_events(limit=500)
            _apply_needs_attention(view["sessions"], recent)
            self._json(view)

        elif path == "/api/fleet/stream":
            my_event = threading.Event()
            with _fleet_change_lock:
                if len(FLEET_CHANGE_SUBSCRIBERS) >= SSE_MAX_SUBSCRIBERS:
                    self.send_response(503)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Retry-After", "5")
                    body = b"too many open fleet streams, poll GET /api/fleet instead\n"
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                FLEET_CHANGE_SUBSCRIBERS.add(my_event)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                if self._sse_send_fleet_snapshot() is False:
                    return
                while True:
                    changed = my_event.wait(timeout=SSE_HEARTBEAT_SECONDS)
                    if changed:
                        my_event.clear()
                        if self._sse_send_fleet_snapshot() is False:
                            break
                    else:
                        # A heartbeat as an SSE comment line never
                        # reaches EventSource.onmessage -- the
                        # client's staleness watchdog has no way to tell
                        # "no changes" from "connection silently died",
                        # so it fires on a quiet-but-healthy stream and
                        # falls into permanent polling. Send it as a real
                        # data frame (tagged so the client can tell it
                        # apart from a fleet snapshot) instead.
                        heartbeat = json.dumps({"type": "heartbeat", "ts": time.time()})
                        if not self._sse_write(f"data: {heartbeat}\n\n"):
                            break
            finally:
                with _fleet_change_lock:
                    FLEET_CHANGE_SUBSCRIBERS.discard(my_event)

        elif path.split('?')[0].startswith("/api/sessions/") and path.split('?')[0].endswith("/events"):
            clean = path.split('?')[0]
            parts = clean[len("/api/sessions/"):-len("/events")].strip("/").split("/", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return self._json({"ok": False, "message": "Malformed path"}, 400)
            device_id, session_id = parts
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = min(500, max(1, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            if HUB_STORE is None:
                return self._json({"events": []})
            rows = HUB_STORE.recent_events(session_id=session_id, device_id=device_id, limit=limit)
            self._json({"events": rows})

        elif path == "/api/audit":
            if HUB_STORE is None:
                return self._json({"audit": []})
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = min(200, max(1, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            self._json({"audit": HUB_STORE.recent_audit(limit=limit)})

        elif path.split('?')[0].startswith("/sessions/") and path.split('?')[0].endswith("/ws"):
            # Live terminal WebSocket (see ws.py). Takes over the socket.
            clean = path.split('?')[0]
            name = clean[len("/sessions/"):-len("/ws")]
            if not _session_name_allowed(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            if not session_exists(name):
                self.send_error(404)
                return
            if self.headers.get("Upgrade", "").lower() != "websocket":
                self._json({"ok": False, "message": "WebSocket upgrade required"}, 400)
                return
            self.close_connection = True
            ws_terminal.serve_terminal(self, name, keys_target=_keys_target(name))

        elif path.startswith("/sessions/") and path.endswith("/transcript"):
            name = path[len("/sessions/"):-len("/transcript")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            if not session_exists(name):
                self._json({"ok": False, "message": "Session not found"}, 404)
                return
            data = get_transcript(name)
            if data is None:
                self._json({"ok": False, "message": "No transcript found for this session"}, 404)
                return
            self._json({"ok": True, **data})

        elif path.split('?')[0].startswith("/sessions/") and path.split('?')[0].endswith("/preview"):
            clean = path.split('?')[0]
            name = clean[len("/sessions/"):-len("/preview")]
            if not _session_name_allowed(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            # Viewer size negotiation: each poll reports its terminal size;
            # the window is sized to the min across live viewers.
            qs = parse_qs(urlparse(self.path).query)
            viewer = qs.get("viewer", [""])[0]
            try:
                v_cols = int(qs.get("cols", ["0"])[0])
                v_rows = int(qs.get("rows", ["0"])[0])
            except ValueError:
                v_cols = v_rows = 0
            if viewer and 40 <= v_cols <= 500 and 10 <= v_rows <= 200:
                active = qs.get("active", ["0"])[0] == "1"
                _preview_viewer_seen(name, viewer, v_cols, v_rows, active)
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", name, "-e", "-p", "-S", "-2000"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                self._json({"error": "Session not found"}, 404)
                return
            # Real cursor position (relative to the visible pane) so the
            # browser terminal can place its cursor where tmux's actually is.
            cursor = None
            alt_screen = False
            cur = subprocess.run(
                ["tmux", "display-message", "-p", "-t", name,
                 "#{cursor_x} #{cursor_y} #{cursor_flag} #{alternate_on}"],
                capture_output=True, text=True,
            )
            if cur.returncode == 0:
                parts = cur.stdout.split()
                if len(parts) == 4 and all(p.isdigit() for p in parts):
                    cursor = {"x": int(parts[0]), "y": int(parts[1]),
                              "visible": parts[2] == "1"}
                    alt_screen = parts[3] == "1"
            self._json({"name": name, "output": result.stdout,
                        "cursor": cursor, "alt": alt_screen,
                        "status": "running"})

        elif path == "/devices":
            self._json({"devices": [{"id": "local", "name": get_local_name()}]
                        + list_devices_public()})

        elif path == "/projects":
            projects = _parse_projects()
            self._json({
                "projects": projects,
                "default": WORKING_DIR,
                "default_name": os.path.basename(WORKING_DIR.rstrip("/")),
            })

        elif path.startswith("/browse"):
            qs = parse_qs(urlparse(self.path).query)
            browse_path = qs.get("path", [WORKING_DIR])[0]
            browse_path = os.path.realpath(browse_path)
            # Restrict browsing to allowed roots (configurable via RC_BROWSE_ROOTS)
            allowed_roots = BROWSE_ROOTS
            if browse_path == "/":
                # Show allowed roots as virtual directory listing
                dirs = sorted(set(
                    r.strip("/").split("/")[0] for r in allowed_roots
                ), key=str.lower)
                self._json({"path": "/", "parent": None, "dirs": dirs})
                return
            if not any(browse_path == root or browse_path.startswith(root + os.sep) for root in allowed_roots):
                # Allow intermediate paths (e.g. /var) if they lead to an allowed root
                if any(root.startswith(browse_path + os.sep) for root in allowed_roots):
                    dirs = sorted(set(
                        root[len(browse_path):].strip("/").split("/")[0]
                        for root in allowed_roots
                        if root.startswith(browse_path + os.sep)
                    ), key=str.lower)
                    parent = os.path.dirname(browse_path) if browse_path != "/" else None
                    self._json({"path": browse_path, "parent": parent, "dirs": dirs})
                    return
                self._json({"error": "Access denied"}, 403)
                return
            if not os.path.isdir(browse_path):
                self._json({"error": "Not a directory"}, 400)
                return
            try:
                entries = os.listdir(browse_path)
            except PermissionError:
                self._json({"error": "Permission denied"}, 400)
                return
            # Show non-hidden dirs, plus any hidden dirs that are allowed roots
            allowed_hidden = set()
            for root in allowed_roots:
                if root.startswith(browse_path + os.sep):
                    child = root[len(browse_path):].strip("/").split("/")[0]
                    if child.startswith("."):
                        allowed_hidden.add(child)
            dirs = sorted(
                [e for e in entries
                 if os.path.isdir(os.path.join(browse_path, e))
                 and (not e.startswith(".") or e in allowed_hidden)],
                key=str.lower
            )
            parent = os.path.dirname(browse_path) if browse_path != "/" else None
            self._json({"path": browse_path, "parent": parent, "dirs": dirs})

        elif path == "/tunnel/status":
            self._json(get_tunnel_status(AUTH_USER, AUTH_PASS))

        elif path == "/version":
            self._json(_version_response())

        elif path == "/stats":
            sess = list_rc_sessions()
            s = stats.system_stats()
            s["token_history"] = stats.token_history()
            s["tokens_now"] = sum(x.get("tokens", 0) for x in sess)
            s["sessions"] = count_launcher_sessions(sess)
            s["max_sessions"] = RC_MAX_SESSIONS
            s["version"] = VERSION
            caps = compat.get_caps()
            s["claude_version"] = caps.get("version")
            s["caps"] = caps
            self._json(s)

        elif path == "/config-report":
            self._json(_get_cached_config_report())

        elif path == "/overview":
            local_sess = list_rc_sessions()
            local_stats = {**stats.system_stats(), "token_history": stats.token_history()}
            local_stats["version"] = VERSION
            local_stats["claude_version"] = compat.get_caps().get("version")
            local_card = {"id": "local", "name": get_local_name(), "base_url": ""}
            cards = overview.build_overview(local_card, local_sess, local_stats, load_devices())
            self._json({"devices": cards})

        elif path == "/api/config-matrix":
            hub_report = _get_cached_config_report()
            matrix = overview.build_config_matrix(hub_report, load_devices())
            self._json(matrix)

        elif path == "/update-check":
            # Check latest version from GitHub API (cached for 10 min)
            import urllib.request
            latest = None
            try:
                if not hasattr(Handler, '_update_cache') or \
                        time.time() - Handler._update_cache.get('ts', 0) > 600:
                    req = urllib.request.Request(
                        "https://api.github.com/repos/barjakuzu/claude-rc-launcher/contents/config.py",
                        headers={"User-Agent": "claude-rc-launcher",
                                 "Accept": "application/vnd.github.v3.raw"},
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        for line in resp.read().decode().splitlines():
                            if line.startswith("VERSION"):
                                latest = line.split('"')[1]
                                break
                    Handler._update_cache = {'ts': time.time(), 'latest': latest}
                else:
                    latest = Handler._update_cache.get('latest')
            except Exception:
                pass
            # Compare semver: update only if latest > current
            update_available = False
            if latest and latest != VERSION:
                try:
                    cur = tuple(int(x) for x in VERSION.split('.'))
                    lat = tuple(int(x) for x in latest.split('.'))
                    update_available = lat > cur
                except (ValueError, TypeError):
                    update_available = False
            self._json({
                "current": VERSION,
                "latest": latest,
                "update_available": update_available,
            })

        elif path == "/schedules":
            sched_list = [_enrich_next_run(s) for s in load_schedules()]
            resp = {"schedules": sched_list}
            if schedules.LAST_LOAD_ERROR:
                resp["error"] = schedules.LAST_LOAD_ERROR
            self._json(resp)

        elif path.startswith("/schedules/") and path.endswith("/instructions"):
            # Read the instructions_file content for a schedule on this device,
            # so the cross-device copy/move can inline it as the target's prompt.
            sid = path[len("/schedules/"):-len("/instructions")]
            if not sid or "/" in sid or ".." in sid:
                self.send_error(404)
                return
            sched = next((s for s in load_schedules() if s.get("id") == sid), None)
            if not sched:
                self._json({"error": "schedule not found"}, 404)
                return
            ipath = sched.get("instructions_file") or ""
            if not ipath:
                self._json({"content": "", "path": ""})
                return
            try:
                with open(os.path.expanduser(ipath)) as f:
                    self._json({"content": f.read(), "path": ipath})
            except OSError as e:
                self._json({"error": str(e), "path": ipath}, 404)

        elif path == "/resume/sessions":
            projects = list_resumable_sessions()
            self._json({"projects": projects})

        elif path == "/status":
            # Backwards compat. External rows have no url/terminal and
            # predate this legacy shape's contract, so they're excluded.
            sessions = [s for s in list_rc_sessions() if not s.get("external")]
            if sessions:
                self._json({"running": True, "url": sessions[0].get("url")})
            else:
                self._json({"running": False, "url": None})

        elif path.startswith("/jobs/"):
            self._handle_job_route(path)

        else:
            self.send_error(404)

    def do_POST(self):
        # Login route — no auth required
        raw_path = self.path.split('?')[0]
        if raw_path in ("/login", "/rc/login"):
            client_ip = _resolve_client_ip(
                self.client_address[0],
                self.headers.get("X-Real-IP", ""),
                self.headers.get("X-Forwarded-For", ""),
                RC_TRUSTED_PROXIES,
            )
            # Rate limiting
            if _is_rate_limited(client_ip):
                self.send_response(302)
                self.send_header("Location", "/login?err=2")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            params = dict(p.split("=", 1) for p in body.split("&") if "=" in p)
            from urllib.parse import unquote_plus
            user = unquote_plus(params.get("user", ""))
            password = unquote_plus(params.get("pass", ""))
            csrf_form = unquote_plus(params.get("csrf", ""))
            # Validate CSRF token from cookie
            csrf_ok = False
            cookie_header = self.headers.get("Cookie", "")
            if cookie_header:
                cookie = SimpleCookie()
                cookie.load(cookie_header)
                if "csrf" in cookie and hmac.compare_digest(cookie["csrf"].value, csrf_form):
                    csrf_ok = True
            if not csrf_ok:
                _record_failed_login(client_ip, user)
                self.send_response(302)
                self.send_header("Location", "/login?err=1")
                self.end_headers()
                return
            if (AUTH_USER and AUTH_PASS and
                    hmac.compare_digest(user, AUTH_USER) and
                    hmac.compare_digest(password, AUTH_PASS)):
                token = secrets.token_hex(32)
                _auth_tokens[token] = time.time() + _AUTH_TOKEN_TTL
                _save_auth_tokens()
                self.send_response(302)
                self.send_header("Location", "/")
                secure = "Secure; " if _cookie_secure_flag(RC_BEHIND_TLS, self.headers.get("X-Forwarded-Proto")) else ""
                self.send_header("Set-Cookie",
                    f"rc_session={token}; Path=/; HttpOnly; SameSite=Lax; {secure}Max-Age=2592000")
                # Clear CSRF cookie
                self.send_header("Set-Cookie", "csrf=; Path=/; Max-Age=0; HttpOnly")
                self.end_headers()
            else:
                _record_failed_login(client_ip, user)
                self.send_response(302)
                self.send_header("Location", "/login?err=1")
                self.end_headers()
            return

        if not _check_auth(self):
            return _send_auth_required(self)

        if config.RC_ROLE == "metadata":
            clean = self.path.split('?')[0]
            local_path = clean[3:] if clean.startswith("/rc") else clean
            if _metadata_post_refused(local_path) or local_path in (
                    "/stop", "/stop-all", "/restart", "/unstick", "/ws", "/preview"):
                return self._json({"ok": False, "message": "This device is metadata-role only"}, 403)

        # Route to a remote device if one is selected.
        dev_id = self._target_device()
        if self._should_proxy(dev_id):
            device = get_device(dev_id)
            if device is None:
                return self._json({"error": "unknown device"}, 404)
            return self._proxy_to_device(device)

        path = self.path.split('?')[0]
        if path.startswith("/rc"):
            path = path[3:]

        if path == "/start":
            body = self._read_body()
            name = body.get("name", "").strip()
            # Keep the user's original name (spaces and all) for /rename —
            # `name` below gets prefixed and sanitized for tmux.
            display_name = name
            mode = body.get("mode", "c")
            model = body.get("model")
            workdir = body.get("workdir", "").strip()
            sandbox = body.get("sandbox", False)

            if not name:
                name = SESSION_PREFIX + time.strftime("%H%M%S")

            if not name.startswith(SESSION_PREFIX):
                name = SESSION_PREFIX + name

            name = re.sub(r'[^a-zA-Z0-9_-]', '', name)

            if mode not in RC_FLAGS:
                self._json({"ok": False, "message": f"Invalid mode: {mode}"}, 400)
                return

            if workdir and os.path.isdir(workdir):
                session_dir = os.path.abspath(workdir)
            else:
                session_dir = WORKING_DIR

            if session_exists(name):
                self._json({"ok": True, "message": "Already running", "name": name})
                return

            cap_msg = _session_cap_message(count_launcher_sessions(list_rc_sessions()), RC_MAX_SESSIONS)
            if cap_msg:
                self._json({"ok": False, "message": cap_msg}, 429)
                return

            session_id = _new_session_id()
            cmd = build_tmux_command(name, session_dir, mode, model=model,
                                     sandbox=sandbox, session_id=session_id)
            print(f"  Starting session: {name} (mode={mode}, model={model}, dir={session_dir})")
            print(f"  CMD: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: tmux failed: {result.stderr.strip()}")
            else:
                print(f"  Session {name} created")
            # A shell session has no trust prompt, no /remote-control handshake
            # and no /rename — it is usable the moment tmux returns.
            if mode != SHELL_MODE:
                threading.Thread(
                    target=setup_session,
                    args=(name, display_name or name, mode), daemon=True,
                ).start()
            _audit(self, action="start", target=name, detail=f"mode={mode}")
            self._json({"ok": True, "message": "Started", "name": name})

        elif path == "/devices/rename":
            body = self._read_body()
            ok, message = rename_device(
                body.get("id", ""), body.get("name", ""),
            )
            if ok:
                _audit(self, action="devices/rename",
                       target=body.get("id", "") or "local", device_id=body.get("id", "") or "local",
                       detail=f"name={body.get('name', '')}")
            self._json({"ok": ok, "message": message}, 200 if ok else 400)

        elif path == "/stop":
            body = self._read_body()
            if body.get("external") and body.get("pid") is not None:
                pid, err = _validate_stop_pid(body.get("pid"))
                if err:
                    self._json({"ok": False, "message": err}, 400)
                    return
                ok, reason = _stop_external_pid(pid)
                self._json({"ok": ok, "message": reason}, 200 if ok else 400)
                return
            name = body.get("name", "").strip()
            if not name:
                self._json({"ok": False, "message": "Missing session name"}, 400)
                return
            if not name.startswith(SESSION_PREFIX):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            if session_exists(name):
                stop_session(name)
            _audit(self, action="stop", target=name)
            self._json({"ok": True, "message": "Stopped"})

        elif path == "/unstick":
            body = self._read_body()
            name = body.get("name", "").strip()
            if not name or not name.startswith(SESSION_PREFIX):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            result = unstick_session(name)
            _audit(self, action="unstick", target=name, detail=result.get("detail", ""))
            self._json({"ok": result["unstuck"], "message": result["detail"]})

        elif path == "/stop-all":
            stopped = _stoppable_session_names(list_rc_sessions())
            for name in stopped:
                stop_session(name)
            _audit(self, action="stop-all", target="", detail=f"count={len(stopped)}")
            self._json({"ok": True, "message": "All stopped"})

        elif path == "/restart":
            body = self._read_body()
            name = body.get("name", "").strip()
            if not name:
                self._json({"ok": False, "message": "Missing session name"}, 400)
                return
            if not name.startswith(SESSION_PREFIX):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            resume = body.get("resume", True)
            ok, msg = restart_session(name, resume=resume)
            _audit(self, action="restart", target=name, detail=f"resume={resume}")
            self._json({"ok": ok, "message": msg, "name": name})

        elif path.startswith("/sessions/") and path.endswith("/preview-bye"):
            # A viewer closed its preview: drop it from the registry and
            # re-apply the effective size (restores the launcher default,
            # or an adopted row's own captured size, once none remain).
            name = path[len("/sessions/"):-len("/preview-bye")]
            if not _session_name_allowed(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            body = self._read_body()
            viewer = str(body.get("viewer", ""))
            if viewer and session_exists(name):
                _preview_viewer_bye(name, viewer)
            self._json({"ok": True})

        elif path.startswith("/sessions/") and path.endswith("/resize"):
            # Resize the tmux window to match the browser terminal so the
            # TUI renders at the viewer's real cols/rows (no wrap artifacts).
            name = path[len("/sessions/"):-len("/resize")]
            if not _session_name_allowed(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            body = self._read_body()
            try:
                cols = max(40, min(500, int(body.get("cols"))))
                rows = max(10, min(200, int(body.get("rows"))))
            except (TypeError, ValueError):
                self._json({"ok": False, "message": "Invalid size"}, 400)
                return
            if not session_exists(name):
                self._json({"ok": False, "message": "Session not found"}, 404)
                return
            # First touch of an adopted (foreign) session's window: remember
            # its size before this resize ever changes it. /resize can be
            # the very first request for a session (before any /preview or
            # /ws poll), so this capture can't be left to those paths alone.
            if not name.startswith(SESSION_PREFIX):
                capture_adopted_window_size(name)
            r = subprocess.run(
                ["tmux", "resize-window", "-t", name, "-x", str(cols), "-y", str(rows)],
                capture_output=True, text=True, timeout=5,
            )
            self._json({"ok": r.returncode == 0,
                        "message": r.stderr.strip() if r.returncode != 0 else "Resized"})

        elif path.startswith("/sessions/") and path.endswith("/keys"):
            name = path[len("/sessions/"):-len("/keys")]
            if not _session_name_allowed(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            body = self._read_body()
            keys = body.get("keys")
            special = body.get("special")
            if not session_exists(name):
                self._json({"ok": False, "message": "Session not found"}, 404)
                return
            target = _keys_target(name)
            try:
                if special:
                    cmd = ["tmux", "send-keys", "-t", target, *special]
                    subprocess.run(cmd, capture_output=True, check=False, timeout=5)
                if keys:
                    cmd = ["tmux", "send-keys", "-t", target, "-l", keys]
                    subprocess.run(cmd, capture_output=True, check=False, timeout=5)
                _audit(self, action="keys", target=name)
                self._json({"ok": True})
            except subprocess.SubprocessError as e:
                self._json({"ok": False, "message": str(e)}, 500)

        elif path.startswith("/sessions/") and path.endswith("/enable-rc"):
            name = path[len("/sessions/"):-len("/enable-rc")]
            rows = list_rc_sessions()
            if name not in _adopted_tmux_names(rows):
                self._json({"ok": False, "message": "Not an adopted external session"}, 400)
                return
            pane_id = _adopted_pane_id(name, rows)
            if not _valid_pane_id(pane_id):
                self._json({"ok": False, "message": "adopted session has no pane id"}, 400)
                return
            with _enable_rc_in_flight_lock:
                if name in _enable_rc_in_flight:
                    self._json({"ok": False, "message": "enable-rc already in progress"}, 409)
                    return
                _enable_rc_in_flight[name] = time.time()
            try:
                result = _enable_rc_for_adopted(name, pane_id)
            finally:
                with _enable_rc_in_flight_lock:
                    _enable_rc_in_flight.pop(name, None)
            _audit(self, action="enable-rc", target=name)
            self._json(result, 200 if result.get("ok") else 502)

        elif path == "/resume/start":
            body = self._read_body()
            session_id = body.get("session_id", "").strip()
            session_title = body.get("title", "").strip()
            project = body.get("project", "").strip()
            mode = body.get("mode", "c")
            if not session_id or not project:
                self._json({"ok": False, "message": "Missing session_id or project"}, 400)
                return
            cap_msg = _session_cap_message(count_launcher_sessions(list_rc_sessions()), RC_MAX_SESSIONS)
            if cap_msg:
                self._json({"ok": False, "message": cap_msg}, 429)
                return
            ok, msg, name = resume_session(session_id, session_title, project, mode)
            _audit(self, action="resume/start", target=name or session_id)
            self._json({"ok": ok, "message": msg, "name": name})

        elif path == "/tunnel/start":
            if not cloudflared_available():
                self._json({"ok": False, "message": "cloudflared not installed"}, 400)
                return
            start_tunnel()
            _audit(self, action="tunnel/start", target="")
            self._json({"ok": True, "message": "Tunnel starting"})

        elif path == "/tunnel/stop":
            stop_tunnel()
            _audit(self, action="tunnel/stop", target="")
            self._json({"ok": True, "message": "Tunnel stopped"})

        elif path == "/schedules":
            body = self._read_body()
            # Validate cron
            cron = body.get("cron", "")
            err = validate_cron(cron)
            if err:
                self._json({"ok": False, "message": f"Invalid cron: {err}"}, 400)
                return
            schedule = create_schedule(body)
            _audit(self, action="schedules", target=str(schedule.get("id", "")))
            self._json({"ok": True, "schedule": schedule})

        elif path == "/schedules/update":
            body = self._read_body()
            sid = body.pop("id", None)
            if not sid:
                self._json({"ok": False, "message": "Missing schedule id"}, 400)
                return
            # Validate cron if provided
            if "cron" in body:
                err = validate_cron(body["cron"])
                if err:
                    self._json({"ok": False, "message": f"Invalid cron: {err}"}, 400)
                    return
            result = update_schedule(sid, body)
            if result:
                _audit(self, action="schedules/update", target=str(sid))
                self._json({"ok": True, "schedule": result})
            else:
                self._json({"ok": False, "message": "Schedule not found"}, 404)

        elif path == "/schedules/delete":
            body = self._read_body()
            sid = body.get("id")
            if not sid:
                self._json({"ok": False, "message": "Missing schedule id"}, 400)
                return
            if delete_schedule(sid):
                _audit(self, action="schedules/delete", target=str(sid))
                self._json({"ok": True, "message": "Deleted"})
            else:
                self._json({"ok": False, "message": "Schedule not found"}, 404)

        elif path == "/update":
            app_dir = os.path.dirname(os.path.abspath(__file__))
            git_dir = os.path.join(app_dir, ".git")
            if not os.path.isdir(git_dir):
                self._json({"ok": False, "message": "Not a git install. Re-run the install script."}, 400)
                return
            body = self._read_body()
            status, result, _merged_sha = _do_git_update_phase(app_dir, body.get("confirm", ""))
            if not result.get("ok"):
                self._json(result, status)
                return
            old_ver = VERSION
            new_ver = old_ver
            try:
                cfg_path = os.path.join(app_dir, "config.py")
                with open(cfg_path) as f:
                    for line in f:
                        if line.startswith("VERSION"):
                            new_ver = line.split('"')[1]
                            break
            except Exception:
                pass
            restart_msg = _detect_and_restart()
            _audit(self, action="update", target="", detail=f"{old_ver}->{new_ver}")
            self._json({"ok": True, "old": old_ver, "new": new_ver,
                         "message": f"Updated {old_ver} → {new_ver}. {restart_msg}"})

        elif path == "/schedules/fire":
            body = self._read_body()
            sid = body.get("id")
            if not sid:
                self._json({"ok": False, "message": "Missing schedule id"}, 400)
                return
            from schedules import get_schedule_by_id
            idx, schedule = get_schedule_by_id(sid)
            if not schedule:
                self._json({"ok": False, "message": "Schedule not found"}, 404)
                return
            _fire_schedule(schedule)
            _audit(self, action="schedules/fire", target=str(sid))
            self._json({"ok": True, "message": f"Firing schedule '{schedule.get('name')}'"})

        else:
            self.send_error(404)

    def _handle_job_route(self, path):
        """Route /jobs/{name}/runs and /jobs/{name}/logs sub-endpoints."""
        from config import RC_HOME
        qs = parse_qs(urlparse(self.path).query)
        clean_path = path.split("?")[0]
        parts = clean_path[len("/jobs/"):].split("/")
        if len(parts) < 2:
            self.send_error(404)
            return
        job_name = parts[0]
        sub = parts[1]
        # Sanitize job name
        if not re.match(r'^[a-zA-Z0-9_-]+$', job_name):
            self.send_error(400)
            return
        jobs_dir = os.path.join(RC_HOME, "jobs", job_name)

        if sub == "runs":
            runs_dir = os.path.join(jobs_dir, "runs")
            if len(parts) == 3:
                # GET /jobs/{name}/runs/{filename} — single run report
                filename = parts[2]
                if not re.match(r'^[a-zA-Z0-9_.T:-]+$', filename):
                    self.send_error(400)
                    return
                filepath = os.path.join(runs_dir, filename)
                if not os.path.isfile(filepath):
                    self.send_error(404)
                    return
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    data["_filename"] = filename
                    self._json(data)
                except Exception:
                    self.send_error(500)
            else:
                # GET /jobs/{name}/runs — list run reports
                if not os.path.isdir(runs_dir):
                    self._json([])
                    return
                limit = int(qs.get("limit", ["10"])[0])
                files = sorted(os.listdir(runs_dir), reverse=True)[:limit]
                runs = []
                for fname in files:
                    try:
                        with open(os.path.join(runs_dir, fname)) as f:
                            data = json.load(f)
                        data["_filename"] = fname
                        runs.append(data)
                    except Exception:
                        pass
                self._json(runs)

        elif sub == "logs":
            logs_dir = os.path.join(jobs_dir, "logs")
            if len(parts) < 3:
                self.send_error(404)
                return
            filename = parts[2]
            if not re.match(r'^[a-zA-Z0-9_.-]+$', filename):
                self.send_error(400)
                return
            filepath = os.path.join(logs_dir, filename)
            if not os.path.isfile(filepath):
                self.send_error(404)
                return
            tail = int(qs.get("tail", ["100"])[0])
            try:
                import subprocess as sp
                r = sp.run(["wc", "-l", filepath], capture_output=True, text=True, timeout=5)
                total_lines = int(r.stdout.strip().split()[0]) if r.returncode == 0 else 0
                r = sp.run(["tail", "-n", str(tail), filepath], capture_output=True, text=True, timeout=10)
                # Strip ANSI escape codes
                clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', r.stdout)
                self._json({"filename": filename, "content": clean, "total_lines": total_lines})
            except Exception:
                self.send_error(500)
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        path = self.path.split("?")[0]
        if path in ("/rc/sessions", "/rc/tunnel/status", "/rc/projects",
                     "/rc/browse", "/rc/schedules", "/rc/version",
                     "/rc/resume/sessions", "/rc/stats", "/rc/overview",
                     "/rc/config-report", "/api/config-matrix", "/rc/fleet", "/api/fleet") or \
                path.startswith("/rc/static/") or path.startswith("/static/") or \
                path.startswith("/rc/jobs/") or "/preview" in path:
            return
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")
