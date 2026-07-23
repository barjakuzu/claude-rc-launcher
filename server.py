"""HTTP handler and routing."""

import base64
import hmac
import http.server
import json
import os
import re
import secrets
import stats
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

from config import (
    VERSION, HOST, PORT, SESSION_PREFIX, WORKING_DIR, CLAUDE_BIN,
    AUTH_USER, AUTH_PASS, RC_FLAGS, MODEL_MAP, SHELL_BIN, BROWSE_ROOTS,
)
from sessions import (
    list_rc_sessions, session_exists, setup_session, stop_session,
    restart_session, list_resumable_sessions, resume_session,
    get_all_session_errors, unstick_session, get_transcript,
)
from tunnel import (
    cloudflared_available, start_tunnel, stop_tunnel, get_tunnel_status,
)
from schedules import (
    load_schedules, create_schedule, update_schedule, delete_schedule,
)
from scheduler import validate_cron, next_cron_run, _fire_schedule, WIZARD_PROMPT
from devices import (
    get_device, list_devices_public, load_devices, get_local_name, rename_device,
)
import overview


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
    with _preview_lock:
        now = time.time()
        live = {v: s for v, s in _preview_viewers.get(name, {}).items()
                if now - s[2] < _PREVIEW_VIEWER_TTL}
        if live:
            _preview_viewers[name] = live
            cols, rows, _, _ = max(live.values(), key=lambda s: s[3])
        else:
            _preview_viewers.pop(name, None)
            cols, rows = 200, 50
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


def _record_failed_login(ip):
    """Record a failed login attempt."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)


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
        if p.startswith("/static/") or p == "/devices" or p == "/devices/rename":
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
            self.send_header("Set-Cookie", f"csrf={csrf}; Path=/; HttpOnly; SameSite=Strict")
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

        # Route to a remote device if one is selected.
        dev_id = self._target_device()
        if self._should_proxy(dev_id):
            device = get_device(dev_id)
            if device is None:
                return self._json({"error": "unknown device"}, 404)
            return self._proxy_to_device(device)

        # Serve static files (handles both /static/* and /rc/static/*)
        static_path = self.path.split('?')[0]
        if static_path.startswith("/rc"):
            static_path = static_path[3:]
        if static_path.startswith("/static/"):
            return self._serve_static(static_path)

        path = self.path
        if path.startswith("/rc"):
            path = path[3:] or "/"

        if path == "/":
            return self._serve_static("/static/dist/index.html")

        elif path == "/legacy":
            auth_hdr = self.headers.get("Authorization", "")
            return self._html(_load_html(auth_hdr))

        elif path == "/sessions":
            sessions = list_rc_sessions()
            errors = get_all_session_errors()
            resp = {"sessions": sessions}
            if errors:
                resp["errors"] = errors
            self._json(resp)

        elif path.startswith("/sessions/") and path.endswith("/transcript"):
            name = path[len("/sessions/"):-len("/transcript")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
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
            if not name or ".." in name:
                self.send_error(404)
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
            self._json({"version": VERSION})

        elif path == "/stats":
            sess = list_rc_sessions()
            s = stats.system_stats()
            s["token_history"] = stats.token_history()
            s["tokens_now"] = sum(x.get("tokens", 0) for x in sess)
            s["sessions"] = len(sess)
            self._json(s)

        elif path == "/overview":
            local_sess = list_rc_sessions()
            local_stats = {**stats.system_stats(), "token_history": stats.token_history()}
            local_card = {"id": "local", "name": get_local_name(), "base_url": ""}
            cards = overview.build_overview(local_card, local_sess, local_stats, load_devices())
            self._json({"devices": cards})

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
            schedules = load_schedules()
            # Enrich with next_run
            for s in schedules:
                if s.get("enabled") and s.get("cron"):
                    s["next_run"] = next_cron_run(s["cron"])
                else:
                    s["next_run"] = None
            self._json({"schedules": schedules})

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
            # Backwards compat
            sessions = list_rc_sessions()
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
            client_ip = self.headers.get("X-Real-IP", self.client_address[0])
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
                _record_failed_login(client_ip)
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
                secure = "Secure; " if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_header("Set-Cookie",
                    f"rc_session={token}; Path=/; HttpOnly; SameSite=Lax; {secure}Max-Age=2592000")
                # Clear CSRF cookie
                self.send_header("Set-Cookie", "csrf=; Path=/; Max-Age=0; HttpOnly")
                self.end_headers()
            else:
                _record_failed_login(client_ip)
                self.send_response(302)
                self.send_header("Location", "/login?err=1")
                self.end_headers()
            return

        if not _check_auth(self):
            return _send_auth_required(self)

        # Route to a remote device if one is selected.
        dev_id = self._target_device()
        if self._should_proxy(dev_id):
            device = get_device(dev_id)
            if device is None:
                return self._json({"error": "unknown device"}, 404)
            return self._proxy_to_device(device)

        path = self.path
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

            claude_flags = RC_FLAGS[mode]
            model_flag = MODEL_MAP.get(model) if model else None
            claude_args = claude_flags.split()
            if model_flag:
                claude_args.extend(["--model", model_flag])
            env_flags = [
                "-e", f"RC_MODE={mode}",
                "-e", f"RC_WORKDIR={session_dir}",
                "-e", "DISPLAY=:1",
                "-e", "TERM=xterm-256color",
            ]
            if sandbox or os.geteuid() == 0:
                env_flags.extend(["-e", "IS_SANDBOX=1"])
            # Wrap command in shell: run claude, and if it exits non-zero,
            # print stderr and sleep so setup_session can read the error
            claude_cmd = " ".join(
                [f"CLAUDECODE= {CLAUDE_BIN}"] + claude_args
            )
            wrapper = f'{claude_cmd} 2>&1 || {{ echo ""; sleep 30; }}'
            cmd = [
                "tmux", "new-session", "-d", "-s", name,
                "-c", session_dir,
                "-x", "200", "-y", "50",
                *env_flags,
                "bash", "-c", wrapper,
            ]
            print(f"  Starting session: {name} (mode={mode}, model={model_flag}, dir={session_dir})")
            print(f"  CMD: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: tmux failed: {result.stderr.strip()}")
            else:
                print(f"  Session {name} created")
            threading.Thread(
                target=setup_session,
                args=(name, display_name or name, mode), daemon=True,
            ).start()
            self._json({"ok": True, "message": "Started", "name": name})

        elif path == "/devices/rename":
            body = self._read_body()
            ok, message = rename_device(
                body.get("id", ""), body.get("name", ""),
            )
            self._json({"ok": ok, "message": message}, 200 if ok else 400)

        elif path == "/stop":
            body = self._read_body()
            name = body.get("name", "").strip()
            if not name:
                self._json({"ok": False, "message": "Missing session name"}, 400)
                return
            if not name.startswith(SESSION_PREFIX):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            if session_exists(name):
                stop_session(name)
            self._json({"ok": True, "message": "Stopped"})

        elif path == "/unstick":
            body = self._read_body()
            name = body.get("name", "").strip()
            if not name or not name.startswith(SESSION_PREFIX):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            result = unstick_session(name)
            self._json({"ok": result["unstuck"], "message": result["detail"]})

        elif path == "/stop-all":
            for s in list_rc_sessions():
                stop_session(s["name"])
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
            self._json({"ok": ok, "message": msg, "name": name})

        elif path.startswith("/sessions/") and path.endswith("/preview-bye"):
            # A viewer closed its preview: drop it from the registry and
            # re-apply the effective size (restores 200×50 when none remain).
            name = path[len("/sessions/"):-len("/preview-bye")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
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
            if not name or ".." in name or "/" in name:
                self.send_error(404)
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
            r = subprocess.run(
                ["tmux", "resize-window", "-t", name, "-x", str(cols), "-y", str(rows)],
                capture_output=True, text=True, timeout=5,
            )
            self._json({"ok": r.returncode == 0,
                        "message": r.stderr.strip() if r.returncode != 0 else "Resized"})

        elif path.startswith("/sessions/") and path.endswith("/keys"):
            name = path[len("/sessions/"):-len("/keys")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
                return
            body = self._read_body()
            keys = body.get("keys")
            special = body.get("special")
            if not session_exists(name):
                self._json({"ok": False, "message": "Session not found"}, 404)
                return
            try:
                if special:
                    cmd = ["tmux", "send-keys", "-t", name, *special]
                    subprocess.run(cmd, capture_output=True, check=False, timeout=5)
                if keys:
                    cmd = ["tmux", "send-keys", "-t", name, "-l", keys]
                    subprocess.run(cmd, capture_output=True, check=False, timeout=5)
                self._json({"ok": True})
            except subprocess.SubprocessError as e:
                self._json({"ok": False, "message": str(e)}, 500)

        elif path == "/resume/start":
            body = self._read_body()
            session_id = body.get("session_id", "").strip()
            session_title = body.get("title", "").strip()
            project = body.get("project", "").strip()
            mode = body.get("mode", "c")
            if not session_id or not project:
                self._json({"ok": False, "message": "Missing session_id or project"}, 400)
                return
            ok, msg, name = resume_session(session_id, session_title, project, mode)
            self._json({"ok": ok, "message": msg, "name": name})

        elif path == "/tunnel/start":
            if not cloudflared_available():
                self._json({"ok": False, "message": "cloudflared not installed"}, 400)
                return
            start_tunnel()
            self._json({"ok": True, "message": "Tunnel starting"})

        elif path == "/tunnel/stop":
            stop_tunnel()
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
                self._json({"ok": True, "message": "Deleted"})
            else:
                self._json({"ok": False, "message": "Schedule not found"}, 404)

        elif path == "/update":
            # Pull latest code from git and restart the service
            app_dir = os.path.dirname(os.path.abspath(__file__))
            git_dir = os.path.join(app_dir, ".git")
            if not os.path.isdir(git_dir):
                self._json({"ok": False, "message": "Not a git install. Re-run the install script."}, 400)
                return
            # Get old version
            old_ver = VERSION
            # Git pull
            result = subprocess.run(
                ["git", "-C", app_dir, "pull", "--ff-only"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                self._json({"ok": False, "message": f"git pull failed: {result.stderr.strip()}"}, 500)
                return
            # Read new version
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
            self._json({"ok": True, "old": old_ver, "new": new_ver,
                         "message": f"Updated {old_ver} → {new_ver}. Restarting..."})
            # Schedule restart in background so the response gets sent first
            def _restart():
                time.sleep(1)
                os.execv("/usr/bin/systemctl", ["systemctl", "restart", "claude-rc-launcher"])
            threading.Thread(target=_restart, daemon=True).start()

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
            self._json({"ok": True, "message": f"Firing schedule '{schedule.get('name')}'"})

        elif path == "/schedules/wizard":
            body = self._read_body()
            description = body.get("description", "").strip()
            schedule_label = body.get("schedule_label", "")
            cron = body.get("cron", "")
            workdir = body.get("workdir", "").strip()
            mode = body.get("mode", "c")
            name = body.get("name", "").strip()

            if not description:
                self._json({"ok": False, "message": "Missing task description"}, 400)
                return

            if mode not in RC_FLAGS:
                mode = "c"

            if not name:
                name = SESSION_PREFIX + "wizard-" + time.strftime("%H%M%S")
            elif not name.startswith(SESSION_PREFIX):
                name = SESSION_PREFIX + name
            name = re.sub(r'[^a-zA-Z0-9_-]', '', name)

            if workdir and os.path.isdir(workdir):
                session_dir = os.path.abspath(workdir)
            else:
                session_dir = WORKING_DIR

            if session_exists(name):
                self._json({"ok": True, "message": "Already running", "name": name})
                return

            mode_labels = {"c": "Standard RC", "ci": "Teammate", "safe": "Safe mode"}
            api_url = f"http://localhost:{PORT}/rc"
            # Use a one-time token file for wizard auth instead of embedding credentials
            wizard_token = os.urandom(16).hex()
            token_file = os.path.join(os.path.expanduser("~/.claude-rc"), f".wizard-token-{wizard_token}")
            try:
                with open(token_file, "w") as tf:
                    if AUTH_USER and AUTH_PASS:
                        tf.write(f"{AUTH_USER}:{AUTH_PASS}")
                os.chmod(token_file, 0o600)
                auth_header = f"-u \"$(cat {token_file})\""
            except Exception:
                auth_header = ""
            prompt = WIZARD_PROMPT.format(
                description=description,
                schedule_label=schedule_label,
                cron=cron,
                workdir=session_dir,
                mode=mode_labels.get(mode, mode),
                mode_code=mode,
                api_url=api_url,
                auth_header=auth_header,
                schedule_name=name.replace(SESSION_PREFIX, ""),
            )

            claude_flags = RC_FLAGS[mode]
            cmd = [
                "tmux", "new-session", "-d", "-s", name,
                "-c", session_dir,
                "-e", f"RC_MODE={mode}",
                "-e", f"RC_WORKDIR={session_dir}",
                "-e", "RC_WIZARD=1",
                "-e", "DISPLAY=:1",
                "-e", "IS_SANDBOX=1",
            ]
            wiz_claude_cmd = " ".join(
                [f"CLAUDECODE= {CLAUDE_BIN}"] + claude_flags.split()
            )
            wiz_wrapper = f'{wiz_claude_cmd} 2>&1 || {{ echo ""; sleep 30; }}'
            cmd.extend(["bash", "-c", wiz_wrapper])
            print(f"  Wizard: starting session {name} (mode={mode}, dir={session_dir})")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self._json({"ok": False, "message": f"tmux failed: {result.stderr.strip()}"}, 500)
                return

            def _setup_and_send():
                setup_session(name, name, mode)
                if not session_exists(name):
                    return
                time.sleep(2)
                subprocess.run(
                    ["tmux", "send-keys", "-t", name, "-l", prompt],
                    capture_output=True,
                )
                time.sleep(0.5)
                subprocess.run(
                    ["tmux", "send-keys", "-t", name, "Enter"],
                    capture_output=True,
                )
                print(f"  Wizard: prompt sent to {name}")

            threading.Thread(target=_setup_and_send, daemon=True).start()
            self._json({"ok": True, "message": "Wizard session started", "name": name})

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
                     "/rc/resume/sessions", "/rc/stats", "/rc/overview") or \
                path.startswith("/rc/static/") or path.startswith("/static/") or \
                path.startswith("/rc/jobs/") or "/preview" in path:
            return
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")
