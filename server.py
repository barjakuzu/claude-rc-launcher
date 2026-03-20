"""HTTP handler and routing."""

import base64
import hmac
import http.server
import json
import os
import re
import secrets
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

from config import (
    VERSION, HOST, PORT, SESSION_PREFIX, WORKING_DIR, CLAUDE_BIN,
    AUTH_USER, AUTH_PASS, RC_FLAGS, MODEL_MAP, SHELL_BIN, BROWSE_ROOTS,
)
from sessions import (
    list_rc_sessions, session_exists, setup_session, stop_session,
    restart_session, list_resumable_sessions, resume_session,
    get_all_session_errors, unstick_session,
)
from tunnel import (
    cloudflared_available, start_tunnel, stop_tunnel, get_tunnel_status,
)
from schedules import (
    load_schedules, create_schedule, update_schedule, delete_schedule,
)
from scheduler import validate_cron, next_cron_run, _fire_schedule, WIZARD_PROMPT


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


_auth_tokens = set()  # valid session cookie tokens

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
        if "rc_session" in cookie and cookie["rc_session"].value in _auth_tokens:
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
    """Send 401 or redirect to login depending on request type."""
    accept = handler.headers.get("Accept", "")
    # API/curl requests get 401 JSON; browser requests get redirected to login
    if "text/html" in accept and not handler.headers.get("Authorization"):
        handler.send_response(302)
        handler.send_header("Location", "/login")
        handler.end_headers()
    else:
        handler.send_response(401)
        handler.send_header("WWW-Authenticate", 'Basic realm="Claude RC Launcher"')
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(b"Authentication required")


def _login_html(csrf_token="", error=""):
    """Generate the login page HTML with CSRF token."""
    err_style = "display:block" if error else "display:none"
    err_msg = error or "Invalid credentials"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude RC — Login</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.login-card{{background:#141414;border:1px solid #2a2a2a;border-radius:16px;padding:40px;width:100%;max-width:380px;text-align:center}}
.login-card h1{{font-size:22px;margin-bottom:6px;color:#e2e8f0}}
.login-card p{{font-size:13px;color:#64748b;margin-bottom:28px}}
.field{{margin-bottom:16px;text-align:left}}
.field label{{display:block;font-size:12px;color:#94a3b8;margin-bottom:4px}}
.field input{{width:100%;padding:10px 12px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;color:#e2e8f0;font-size:14px;outline:none;color-scheme:dark}}
.field input:focus{{border-color:#555}}
.field input:-webkit-autofill{{-webkit-box-shadow:0 0 0 30px #1a1a1a inset !important;-webkit-text-fill-color:#e2e8f0 !important;border-color:#2a2a2a !important}}
.btn{{width:100%;padding:12px;background:#222;color:#e2e8f0;border:1px solid #333;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;margin-top:8px}}
.btn:hover{{background:#333}}
.error{{color:#ef4444;font-size:13px;margin-bottom:12px;{err_style}}}
</style></head><body>
<form class="login-card" method="POST" action="/login">
<input type="hidden" name="csrf" value="{csrf_token}">
<h1>Claude RC</h1><p>Session Launcher</p>
<div class="error">{err_msg}</div>
<div class="field"><label>Username</label><input name="user" type="text" autofocus required></div>
<div class="field"><label>Password</label><input name="pass" type="password" required></div>
<button type="submit" class="btn">Sign In</button>
</form>
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
                    _auth_tokens.discard(cookie["rc_session"].value)
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "rc_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            return

        if not _check_auth(self):
            return _send_auth_required(self)

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
            auth_hdr = self.headers.get("Authorization", "")
            self._html(_load_html(auth_hdr))

        elif path == "/sessions":
            sessions = list_rc_sessions()
            errors = get_all_session_errors()
            resp = {"sessions": sessions}
            if errors:
                resp["errors"] = errors
            self._json(resp)

        elif path.startswith("/sessions/") and path.endswith("/preview"):
            name = path[len("/sessions/"):-len("/preview")]
            if not name or ".." in name:
                self.send_error(404)
                return
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", name, "-p", "-S", "-50"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                self._json({"error": "Session not found"}, 404)
                return
            self._json({"name": name, "output": result.stdout, "status": "running"})

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

        elif path == "/update-check":
            # Check latest version from GitHub (cached for 1 hour)
            import urllib.request
            latest = None
            try:
                if not hasattr(Handler, '_update_cache') or \
                        time.time() - Handler._update_cache.get('ts', 0) > 3600:
                    req = urllib.request.Request(
                        "https://raw.githubusercontent.com/barjakuzu/claude-rc-launcher/main/config.py",
                        headers={"User-Agent": "claude-rc-launcher"},
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
            self._json({
                "current": VERSION,
                "latest": latest,
                "update_available": latest is not None and latest != VERSION,
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
                _auth_tokens.add(token)
                self.send_response(302)
                self.send_header("Location", "/")
                secure = "Secure; " if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_header("Set-Cookie",
                    f"rc_session={token}; Path=/; HttpOnly; SameSite=Lax; {secure}Max-Age=604800")
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

        path = self.path
        if path.startswith("/rc"):
            path = path[3:]

        if path == "/start":
            body = self._read_body()
            name = body.get("name", "").strip()
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
                target=setup_session, args=(name, name, mode), daemon=True
            ).start()
            self._json({"ok": True, "message": "Started", "name": name})

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
                     "/rc/resume/sessions") or \
                path.startswith("/rc/static/") or path.startswith("/static/") or \
                path.startswith("/rc/jobs/") or "/preview" in path:
            return
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")
