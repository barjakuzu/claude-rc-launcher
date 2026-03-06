"""HTTP handler and routing."""

import base64
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
        return user == AUTH_USER and password == AUTH_PASS
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


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(content.encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def do_GET(self):
        if not _check_auth(self):
            return _send_auth_required(self)

        path = self.path
        if path.startswith("/rc"):
            path = path[3:] or "/"

        if path == "/":
            self._html(_load_html())

        elif path == "/sessions":
            sessions = list_rc_sessions()
            self._json({"sessions": sessions})

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
            browse_path = os.path.abspath(browse_path)
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
            cmd = [
                "tmux", "new-session", "-d", "-s", name,
                "-c", session_dir,
                "-e", f"RC_MODE={mode}",
                "-e", f"RC_WORKDIR={session_dir}",
                "-e", "DISPLAY=:1",
                CLAUDE_BIN, *claude_args,
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
            if session_exists(name):
                stop_session(name)
            self._json({"ok": True, "message": "Stopped"})

        elif path == "/stop-all":
            for s in list_rc_sessions():
                stop_session(s["name"])
            self._json({"ok": True, "message": "All stopped"})

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
            if AUTH_USER and AUTH_PASS:
                auth_header = f"-u '{AUTH_USER}:{AUTH_PASS}'"
            else:
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
                CLAUDE_BIN, *claude_flags.split(),
            ]
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
                     "/rc/browse", "/rc/schedules", "/rc/version"):
            return
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")
