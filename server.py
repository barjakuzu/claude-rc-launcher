"""HTTP handler and routing."""

import base64
import hmac
import http.server
import json
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlparse, parse_qs

from config import (
    VERSION, HOST, PORT, SESSION_PREFIX, WORKING_DIR, CLAUDE_BIN,
    AUTH_USER, AUTH_PASS, RC_FLAGS, MODEL_MAP, SHELL_BIN,
)
from sessions import (
    list_rc_sessions, session_exists, setup_session, stop_session,
    restart_session, list_resumable_sessions, resume_session,
    get_all_session_errors,
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


def _check_auth(handler):
    """Return True if auth passes (or auth not configured)."""
    if not AUTH_USER or not AUTH_PASS:
        return True
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
    """Send 401 response requesting basic auth."""
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Claude RC Launcher"')
    handler.send_header("Content-Type", "text/plain")
    handler.end_headers()
    handler.wfile.write(b"Authentication required")


def _load_html():
    """Load the frontend HTML from static/index.html."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(html_path, "r") as f:
        return f.read()


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
            self._html(_load_html())

        elif path == "/sessions":
            sessions = list_rc_sessions()
            errors = get_all_session_errors()
            resp = {"sessions": sessions}
            if errors:
                resp["errors"] = errors
            self._json(resp)

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
            # Restrict browsing to home directory and /tmp
            allowed_roots = [os.path.expanduser("~"), "/tmp"]
            if not any(browse_path == root or browse_path.startswith(root + os.sep) for root in allowed_roots):
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
            dirs = sorted(
                [e for e in entries if not e.startswith(".") and os.path.isdir(os.path.join(browse_path, e))],
                key=str.lower
            )
            parent = os.path.dirname(browse_path) if browse_path != "/" else None
            self._json({"path": browse_path, "parent": parent, "dirs": dirs})

        elif path == "/tunnel/status":
            self._json(get_tunnel_status(AUTH_USER, AUTH_PASS))

        elif path == "/version":
            self._json({"version": VERSION})

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

        else:
            self.send_error(404)

    def do_POST(self):
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

    def log_message(self, fmt, *args):
        path = self.path.split("?")[0]
        if path in ("/rc/sessions", "/rc/tunnel/status", "/rc/projects",
                     "/rc/browse", "/rc/schedules", "/rc/version",
                     "/rc/resume/sessions") or \
                path.startswith("/rc/static/") or path.startswith("/static/"):
            return
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")
