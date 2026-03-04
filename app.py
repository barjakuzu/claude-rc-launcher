#!/usr/bin/env python3
"""Simple web app to start/stop claude remote-control sessions."""

import atexit
import base64
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
import time

VERSION = "1.0.0"

HOST = os.environ.get("RC_HOST", "0.0.0.0")
PORT = int(os.environ.get("RC_PORT", "8200"))
SESSION_PREFIX = os.environ.get("RC_PREFIX", "rc-")
WORKING_DIR = os.environ.get("RC_WORKING_DIR", ".")
CLAUDE_BIN = os.environ.get("RC_CLAUDE_BIN", "claude")
AUTH_USER = os.environ.get("RC_AUTH_USER", "")
AUTH_PASS = os.environ.get("RC_AUTH_PASS", "")
SHELL_BIN = os.environ.get("SHELL", "/bin/bash")

# Resolve relative working dir to absolute
WORKING_DIR = os.path.abspath(WORKING_DIR)

RC_FLAGS = {
    "c": "--dangerously-skip-permissions --verbose",
    "ci": "--dangerously-skip-permissions --teammate-mode in-process --verbose",
    "safe": "--verbose",
}

# --- Cloudflare Tunnel state ---
_tunnel_proc = None
_tunnel_url = None
_tunnel_lock = threading.Lock()


def _cloudflared_available():
    """Check if cloudflared binary is on PATH."""
    return shutil.which("cloudflared") is not None


def _start_tunnel():
    """Start a cloudflared quick tunnel pointing at our server."""
    global _tunnel_proc, _tunnel_url
    with _tunnel_lock:
        if _tunnel_proc and _tunnel_proc.poll() is None:
            return  # already running
        _tunnel_url = None
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _tunnel_proc = proc

    def _read_stderr():
        global _tunnel_url
        for line in proc.stderr:
            text = line.decode("utf-8", errors="replace").strip()
            m = re.search(r'(https://[a-z0-9-]+\.trycloudflare\.com)', text)
            if m:
                _tunnel_url = m.group(1)
                print(f"  Tunnel URL: {_tunnel_url}")

    t = threading.Thread(target=_read_stderr, daemon=True)
    t.start()


def _stop_tunnel():
    """Stop the cloudflared tunnel if running."""
    global _tunnel_proc, _tunnel_url
    with _tunnel_lock:
        if _tunnel_proc is None:
            return
        try:
            _tunnel_proc.terminate()
            _tunnel_proc.wait(timeout=5)
        except Exception:
            try:
                _tunnel_proc.kill()
            except Exception:
                pass
        _tunnel_proc = None
        _tunnel_url = None


atexit.register(_stop_tunnel)


# --- Project folder helpers ---

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


# --- Session helpers ---

def _list_rc_sessions():
    """Return list of rc-* tmux sessions with name, mode, URL, and workdir."""
    r = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return []

    sessions = []
    for line in r.stdout.strip().splitlines():
        name = line.strip()
        if not name.startswith(SESSION_PREFIX):
            continue
        mode = _get_session_env(name, "RC_MODE") or "c"
        workdir = _get_session_env(name, "RC_WORKDIR")
        url = _get_url(name)
        s = {"name": name, "mode": mode, "url": url}
        if workdir:
            s["workdir"] = workdir
            s["project"] = os.path.basename(workdir.rstrip("/"))
        sessions.append(s)
    return sessions


def _get_session_env(session_name, var_name):
    """Read an env var set on a tmux session."""
    try:
        r = subprocess.run(
            ["tmux", "show-environment", "-t", session_name, var_name],
            capture_output=True, text=True, timeout=5,
        )
        if "=" in r.stdout:
            return r.stdout.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


def _get_url(session_name):
    """Extract the claude.ai URL from a tmux session's pane output."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-50", "-J"],
            capture_output=True, text=True, timeout=5,
        )
        text = r.stdout.replace("\n", " ")
        m = re.search(r'(https://claude\.ai/code/session_[^\s]+)', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _session_exists(name):
    r = subprocess.run(
        ["tmux", "has-session", "-t", name],
        capture_output=True,
    )
    return r.returncode == 0


def _setup_session(session_name, display_name, mode):
    """Handle trust prompt, send /remote-control, wait for URL, then /rename."""
    # Check quickly if session is alive, capture output if it dies
    for check in range(10):
        time.sleep(0.3)
        if not _session_exists(session_name):
            print(f"  {session_name}: session died after {(check+1)*0.3:.1f}s")
            return
    # Capture pane to see what's on screen
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p"],
        capture_output=True, text=True, timeout=5,
    )
    print(f"  {session_name}: pane content: {repr(r.stdout[:300])}")
    time.sleep(2)
    if not _session_exists(session_name):
        print(f"  {session_name}: session died within 5s")
        return
    # Handle the "trust this folder" prompt — press Enter to accept
    print(f"  {session_name}: accepting trust prompt")
    subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)

    # Wait for Claude's interactive prompt to appear before sending commands
    print(f"  {session_name}: waiting for Claude to be ready...")
    for _ in range(30):
        time.sleep(2)
        if not _session_exists(session_name):
            print(f"  {session_name}: session died while loading")
            return
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        # Claude is ready when we see the prompt character
        if "\u276f" in r.stdout or "❯" in r.stdout or ">" in r.stdout.split("\n")[-5:]:
            break
    else:
        print(f"  {session_name}: timed out waiting for Claude prompt")
        return

    print(f"  {session_name}: sending /remote-control")
    subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", "/remote-control"], capture_output=True)
    time.sleep(0.5)
    subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
    for i in range(30):
        time.sleep(2)
        if not _session_exists(session_name):
            print(f"  {session_name}: session died while waiting for URL")
            return
        url = _get_url(session_name)
        if url:
            print(f"  {session_name}: got URL → {url}")
            time.sleep(1)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", f"/rename {display_name}"], capture_output=True)
            time.sleep(0.5)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            return
    print(f"  {session_name}: timed out waiting for URL")


def _stop_session(name):
    """Gracefully stop a session: Ctrl-C twice, then force kill if needed."""
    if not _session_exists(name):
        return
    subprocess.run(["tmux", "send-keys", "-t", name, "C-c"], capture_output=True)
    time.sleep(2)
    if not _session_exists(name):
        return
    subprocess.run(["tmux", "send-keys", "-t", name, "C-c"], capture_output=True)
    for _ in range(8):
        time.sleep(1)
        if not _session_exists(name):
            return
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


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


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude RC Launcher</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, 'Segoe UI', sans-serif; background: #0b1120; color: #e2e8f0; min-height: 100vh; padding: 1.5rem 1rem; }
  .container { max-width: 480px; margin: 0 auto; }

  /* Header */
  .logo { text-align: center; margin-bottom: 1.5rem; }
  .logo svg { width: 36px; height: 36px; margin-bottom: 0.5rem; }
  h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .subtitle { color: #475569; font-size: 0.75rem; margin-top: 0.2rem; letter-spacing: 0.05em; text-transform: uppercase; }

  /* Card */
  .card { background: #131a2b; border: 1px solid rgba(255,255,255,0.06); border-radius: 16px; padding: 1.5rem; margin-bottom: 1.25rem; }

  /* Form */
  .form-group { margin-bottom: 1.2rem; }
  label { display: block; font-size: 0.75rem; color: #64748b; margin-bottom: 0.35rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  input[type="text"] { width: 100%; padding: 0.6rem 0.8rem; border: 1px solid #1e293b; border-radius: 10px; background: #0b1120; color: #e2e8f0; font-size: 0.9rem; transition: all 0.2s; }
  input[type="text"]:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.12); }
  input[type="text"]::placeholder { color: #334155; }

  /* Custom select */
  .select-wrap { position: relative; margin-bottom: 1.2rem; }
  .custom-select { width: 100%; padding: 0.6rem 2.5rem 0.6rem 0.8rem; border: 1px solid #1e293b; border-radius: 10px; background: #0b1120; color: #e2e8f0; font-size: 0.9rem; appearance: none; -webkit-appearance: none; cursor: pointer; transition: all 0.2s; }
  .custom-select:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.12); }
  .select-arrow { position: absolute; right: 0.8rem; top: 50%; transform: translateY(-50%); pointer-events: none; color: #475569; font-size: 0.7rem; }
  .mode-info { display: flex; align-items: center; gap: 0.5rem; padding: 0.55rem 0.75rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.04); border-radius: 8px; margin-top: 0.5rem; transition: all 0.3s; }
  .mode-icon { font-size: 1rem; flex-shrink: 0; }
  .mode-detail { font-size: 0.75rem; color: #94a3b8; line-height: 1.4; }
  .mode-detail strong { color: #cbd5e1; font-weight: 600; }

  /* Launch button */
  .btn-launch { width: 100%; padding: 0.75rem; border: none; border-radius: 10px; font-size: 0.95rem; font-weight: 700; cursor: pointer; transition: all 0.25s; display: flex; align-items: center; justify-content: center; gap: 0.5rem; letter-spacing: 0.01em; }
  .btn-launch:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-launch:hover:not(:disabled) { transform: translateY(-1px); }
  .btn-launch:active:not(:disabled) { transform: translateY(0); }
  .btn-launch.mode-c { background: linear-gradient(135deg, #10b981, #059669); color: white; box-shadow: 0 4px 15px rgba(16,185,129,0.25); }
  .btn-launch.mode-c:hover:not(:disabled) { box-shadow: 0 6px 20px rgba(16,185,129,0.35); }
  .btn-launch.mode-ci { background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; box-shadow: 0 4px 15px rgba(59,130,246,0.25); }
  .btn-launch.mode-ci:hover:not(:disabled) { box-shadow: 0 6px 20px rgba(59,130,246,0.35); }
  .btn-launch.mode-safe { background: linear-gradient(135deg, #8b5cf6, #7c3aed); color: white; box-shadow: 0 4px 15px rgba(139,92,246,0.25); }
  .btn-launch.mode-safe:hover:not(:disabled) { box-shadow: 0 6px 20px rgba(139,92,246,0.35); }
  .btn-icon { font-size: 1.1rem; }

  /* Stop button */
  .btn-stop { background: rgba(239,68,68,0.12); color: #f87171; font-size: 0.78rem; padding: 0.4rem 0.85rem; width: auto; border: 1px solid rgba(239,68,68,0.2); border-radius: 6px; cursor: pointer; font-weight: 600; transition: all 0.2s; }
  .btn-stop:hover:not(:disabled) { background: rgba(239,68,68,0.2); }

  /* Sessions */
  .sessions-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
  #sessions-title { font-size: 0.8rem; color: #475569; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .session-card { background: #131a2b; border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 1rem 1.15rem; margin-bottom: 0.5rem; transition: border-color 0.2s; }
  .session-card:hover { border-color: rgba(255,255,255,0.1); }
  .session-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
  .session-name { font-weight: 600; font-size: 0.9rem; display: flex; align-items: center; gap: 0.4rem; }
  .badge { font-size: 0.6rem; font-weight: 700; padding: 0.18rem 0.5rem; border-radius: 5px; text-transform: uppercase; letter-spacing: 0.04em; }
  .badge-c { background: rgba(16,185,129,0.12); color: #34d399; border: 1px solid rgba(16,185,129,0.2); }
  .badge-ci { background: rgba(59,130,246,0.12); color: #60a5fa; border: 1px solid rgba(59,130,246,0.2); }
  .badge-safe { background: rgba(139,92,246,0.12); color: #a78bfa; border: 1px solid rgba(139,92,246,0.2); }
  .perm-tag { font-size: 0.6rem; padding: 0.15rem 0.45rem; border-radius: 4px; font-weight: 600; }
  .perm-skip { background: rgba(251,191,36,0.1); color: #fbbf24; border: 1px solid rgba(251,191,36,0.15); }
  .perm-normal { background: rgba(139,92,246,0.1); color: #c4b5fd; border: 1px solid rgba(139,92,246,0.15); }
  .session-url { background: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 0.5rem 0.75rem; margin-bottom: 0.65rem; word-break: break-all; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.75rem; }
  .session-url a { color: #60a5fa; text-decoration: none; }
  .session-url a:hover { text-decoration: underline; }
  .waiting { color: #64748b; font-style: italic; }
  .empty { text-align: center; color: #334155; padding: 2rem 0; font-size: 0.85rem; }
  .project-label { font-size: 0.7rem; color: #475569; margin-bottom: 0.5rem; }

  /* Remote Access card */
  .share-card { background: #131a2b; border: 1px solid rgba(255,255,255,0.06); border-radius: 16px; padding: 1.25rem; margin-bottom: 1.25rem; }
  .share-card h3 { font-size: 0.8rem; color: #475569; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.75rem; }
  .share-url { background: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 0.5rem 0.75rem; margin-bottom: 0.65rem; word-break: break-all; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.75rem; display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
  .share-url a { color: #60a5fa; text-decoration: none; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .btn-copy { background: rgba(59,130,246,0.12); color: #60a5fa; border: 1px solid rgba(59,130,246,0.2); border-radius: 6px; padding: 0.3rem 0.6rem; font-size: 0.7rem; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .btn-copy:hover { background: rgba(59,130,246,0.2); }
  .btn-share { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; border: none; border-radius: 10px; padding: 0.6rem 1.2rem; font-size: 0.85rem; font-weight: 700; cursor: pointer; transition: all 0.25s; width: 100%; }
  .btn-share:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(245,158,11,0.3); }
  .btn-share:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-share-stop { background: rgba(239,68,68,0.12); color: #f87171; border: 1px solid rgba(239,68,68,0.2); border-radius: 10px; padding: 0.6rem 1.2rem; font-size: 0.85rem; font-weight: 700; cursor: pointer; transition: all 0.2s; width: 100%; margin-top: 0.5rem; }
  .btn-share-stop:hover { background: rgba(239,68,68,0.2); }
  .share-warning { background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.2); border-radius: 8px; padding: 0.55rem 0.75rem; margin-bottom: 0.65rem; font-size: 0.75rem; color: #fbbf24; line-height: 1.4; }
  .share-dimmed { opacity: 0.5; }
  .share-dimmed .btn-share { pointer-events: none; }
  .share-install-hint { font-size: 0.75rem; color: #64748b; margin-top: 0.5rem; }
  .share-install-hint a { color: #60a5fa; text-decoration: none; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.2); border-top-color: white; border-radius: 50%; animation: spin 0.6s linear infinite; vertical-align: middle; margin-right: 0.4rem; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Custom path input */
  #custom-path-wrap { display: none; margin-top: 0.5rem; }
</style>
</head>
<body>
<div class="container">
  <div class="logo">
    <svg viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="10" fill="url(#g1)"/>
      <path d="M10 18.5L15.5 24L26 13" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <defs><linearGradient id="g1" x1="0" y1="0" x2="36" y2="36"><stop stop-color="#3b82f6"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>
    </svg>
    <h1>Claude Remote Control</h1>
    <div class="subtitle">Session Launcher</div>
  </div>

  <div class="card">
    <div class="form-group" id="project-group" style="display:none;">
      <label for="project-select">Project</label>
      <div class="select-wrap">
        <select id="project-select" class="custom-select" onchange="onProjectChange()">
        </select>
        <span class="select-arrow">&#9660;</span>
      </div>
      <div id="custom-path-wrap">
        <input type="text" id="custom-path" placeholder="/path/to/project" />
      </div>
    </div>

    <div class="form-group">
      <label for="session-name">Session Name</label>
      <input type="text" id="session-name" placeholder="" />
    </div>

    <div class="form-group">
      <label for="mode-select">Launch Mode</label>
      <div class="select-wrap">
        <select id="mode-select" class="custom-select" onchange="updateMode()">
          <option value="c">Standard RC &mdash; skip permissions</option>
          <option value="ci">Teammate (in-process) &mdash; skip permissions</option>
          <option value="safe">Standard RC &mdash; with permissions</option>
        </select>
        <span class="select-arrow">&#9660;</span>
      </div>
      <div class="mode-info" id="mode-info">
        <span class="mode-icon" id="mode-icon">&#9889;</span>
        <span class="mode-detail" id="mode-detail"><strong>Unrestricted</strong> &mdash; runs with --dangerously-skip-permissions, no approval prompts</span>
      </div>
    </div>

    <button class="btn-launch mode-c" id="btn-launch" onclick="startSession()">
      <span class="btn-icon" id="btn-launch-icon">&#9654;</span>
      Launch Session
    </button>
  </div>

  <div class="sessions-header">
    <div id="sessions-title">Running Sessions</div>
    <button class="btn-stop" id="btn-stop-all" style="display:none;" onclick="stopAll()">Stop All</button>
  </div>
  <div id="sessions"></div>

  <div id="share-section"></div>
</div>
<script>
const MODES = {
  c:    { icon: String.fromCodePoint(0x26A1), cls: 'mode-c',    detail: '<strong>Unrestricted</strong> &mdash; runs with --dangerously-skip-permissions, no approval prompts' },
  ci:   { icon: String.fromCodePoint(0x1F91D), cls: 'mode-ci',   detail: '<strong>Teammate in-process</strong> &mdash; skip permissions, teammate mode enabled' },
  safe: { icon: String.fromCodePoint(0x1F512), cls: 'mode-safe', detail: '<strong>Safe mode</strong> &mdash; standard permissions, requires user approvals' },
};

let tunnelState = { available: false, running: false, url: null, auth_configured: false };

function updateMode() {
  const mode = document.getElementById('mode-select').value;
  const m = MODES[mode];
  document.getElementById('mode-icon').textContent = m.icon;
  document.getElementById('mode-detail').innerHTML = m.detail;
  const btn = document.getElementById('btn-launch');
  btn.className = 'btn-launch ' + m.cls;
}

function defaultName() {
  const d = new Date();
  return 'rc-' + String(d.getHours()).padStart(2,'0') + String(d.getMinutes()).padStart(2,'0') + String(d.getSeconds()).padStart(2,'0');
}

document.getElementById('session-name').placeholder = defaultName();

async function api(method, path, body) {
  const opts = { method, headers: {'Content-Type': 'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch('/rc' + path, opts);
  return r.json();
}

function onProjectChange() {
  const sel = document.getElementById('project-select');
  const wrap = document.getElementById('custom-path-wrap');
  wrap.style.display = sel.value === '__custom__' ? 'block' : 'none';
}

async function loadProjects() {
  try {
    const data = await api('GET', '/projects');
    const projects = data.projects || [];
    if (projects.length === 0) return;
    const group = document.getElementById('project-group');
    group.style.display = 'block';
    const sel = document.getElementById('project-select');
    sel.innerHTML = '<option value="">Default (' + escHtml(data.default_name) + ')</option>';
    projects.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.path;
      opt.textContent = p.name + (p.exists ? '' : ' (missing)');
      opt.title = p.path;
      if (!p.exists) opt.disabled = true;
      sel.appendChild(opt);
    });
    const custom = document.createElement('option');
    custom.value = '__custom__';
    custom.textContent = 'Custom path...';
    sel.appendChild(custom);
  } catch(e) {}
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function getSelectedWorkdir() {
  const group = document.getElementById('project-group');
  if (group.style.display === 'none') return undefined;
  const sel = document.getElementById('project-select');
  if (sel.value === '__custom__') {
    return document.getElementById('custom-path').value.trim() || undefined;
  }
  return sel.value || undefined;
}

async function refresh() {
  const data = await api('GET', '/sessions');
  const el = document.getElementById('sessions');
  document.getElementById('session-name').placeholder = defaultName();

  const stopAllBtn = document.getElementById('btn-stop-all');
  if (!data.sessions || data.sessions.length === 0) {
    el.innerHTML = '<div class="empty">No sessions running</div>';
    stopAllBtn.style.display = 'none';
  } else {
    stopAllBtn.style.display = data.sessions.length > 1 ? 'block' : 'none';
    stopAllBtn.disabled = false;
    el.innerHTML = data.sessions.map(s => {
      const badgeClass = s.mode === 'ci' ? 'badge-ci' : s.mode === 'safe' ? 'badge-safe' : 'badge-c';
      const permTag = s.mode === 'safe'
        ? '<span class="perm-tag perm-normal">safe</span>'
        : '<span class="perm-tag perm-skip">skip-perms</span>';
      const modeLabel = s.mode === 'ci' ? 'teammate' : s.mode === 'safe' ? 'safe' : 'standard';
      const urlHtml = s.url
        ? '<div class="session-url"><a href="' + escHtml(s.url) + '" target="_blank">' + escHtml(s.url) + '</a></div>'
        : '<div class="session-url waiting">Waiting for URL...</div>';
      const projectHtml = s.project
        ? '<div class="project-label" title="' + escHtml(s.workdir || '') + '">' + escHtml(s.project) + '</div>'
        : '';
      return '<div class="session-card">' +
        '<div class="session-header">' +
          '<span class="session-name">' + escHtml(s.name) + '</span>' +
          '<span style="display:flex;gap:0.35rem;align-items:center;">' + permTag +
            '<span class="badge ' + badgeClass + '">' + modeLabel + '</span>' +
          '</span>' +
        '</div>' +
        projectHtml +
        urlHtml +
        '<button class="btn-stop" onclick="stopSession(\\'' + escHtml(s.name) + '\\')">Stop</button>' +
      '</div>';
    }).join('');
  }

  // Update tunnel status
  await refreshTunnel();
}

async function refreshTunnel() {
  try {
    tunnelState = await api('GET', '/tunnel/status');
  } catch(e) {
    tunnelState = { available: false, running: false, url: null, auth_configured: false };
  }
  renderShare();
}

function renderShare() {
  const el = document.getElementById('share-section');
  if (!tunnelState.available) {
    el.innerHTML = '<div class="share-card share-dimmed"><h3>Remote Access</h3>' +
      '<button class="btn-share" disabled>Share</button>' +
      '<div class="share-install-hint">Requires <a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" target="_blank">cloudflared</a> to be installed.</div></div>';
    return;
  }
  let body = '';
  if (tunnelState.running && tunnelState.url) {
    if (!tunnelState.auth_configured) {
      body += '<div class="share-warning">Warning: No authentication configured (RC_AUTH_USER). Anyone with this link can launch sessions.</div>';
    }
    body += '<div class="share-url"><a href="' + escHtml(tunnelState.url) + '" target="_blank">' + escHtml(tunnelState.url) + '</a>' +
      '<button class="btn-copy" onclick="copyUrl()">Copy</button></div>' +
      '<button class="btn-share-stop" onclick="stopTunnel()">Stop Sharing</button>';
  } else if (tunnelState.running) {
    body += '<button class="btn-share" disabled><span class="spinner"></span>Starting tunnel...</button>';
  } else {
    body += '<button class="btn-share" onclick="startTunnel()">Share</button>';
  }
  el.innerHTML = '<div class="share-card"><h3>Remote Access</h3>' + body + '</div>';
}

async function startTunnel() {
  await api('POST', '/tunnel/start');
  tunnelState.running = true;
  tunnelState.url = null;
  renderShare();
}

async function stopTunnel() {
  await api('POST', '/tunnel/stop');
  tunnelState.running = false;
  tunnelState.url = null;
  renderShare();
}

function copyUrl() {
  if (tunnelState.url) {
    navigator.clipboard.writeText(tunnelState.url).then(() => {
      const btn = document.querySelector('.btn-copy');
      if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); }
    });
  }
}

async function startSession() {
  const mode = document.getElementById('mode-select').value;
  const btn = document.getElementById('btn-launch');
  const input = document.getElementById('session-name');
  const name = input.value.trim() || input.placeholder;
  const workdir = getSelectedWorkdir();
  input.value = '';
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">&#8987;</span> Launching...';
  const body = { name, mode };
  if (workdir) body.workdir = workdir;
  await api('POST', '/start', body);
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const data = await api('GET', '/sessions');
    const s = (data.sessions || []).find(s => s.name === name);
    if (s && s.url) break;
  }
  btn.disabled = false;
  btn.innerHTML = '<span class="btn-icon" id="btn-launch-icon">&#9654;</span> Launch Session';
  refresh();
}

async function stopSession(name) {
  await api('POST', '/stop', { name });
  refresh();
}

async function stopAll() {
  document.getElementById('btn-stop-all').disabled = true;
  await api('POST', '/stop-all');
  refresh();
}

loadProjects();
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


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
            self._html(HTML_PAGE)
        elif path == "/sessions":
            sessions = _list_rc_sessions()
            self._json({"sessions": sessions})
        elif path == "/projects":
            projects = _parse_projects()
            self._json({
                "projects": projects,
                "default": WORKING_DIR,
                "default_name": os.path.basename(WORKING_DIR.rstrip("/")),
            })
        elif path == "/tunnel/status":
            global _tunnel_proc, _tunnel_url
            running = _tunnel_proc is not None and _tunnel_proc.poll() is None
            if not running and _tunnel_proc is not None:
                # Tunnel crashed — clean up
                _tunnel_proc = None
                _tunnel_url = None
            self._json({
                "available": _cloudflared_available(),
                "running": running,
                "url": _tunnel_url if running else None,
                "auth_configured": bool(AUTH_USER and AUTH_PASS),
            })
        elif path == "/status":
            # Backwards compat
            sessions = _list_rc_sessions()
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
            workdir = body.get("workdir", "").strip()

            if not name:
                name = SESSION_PREFIX + time.strftime("%H%M%S")

            # Ensure name has prefix
            if not name.startswith(SESSION_PREFIX):
                name = SESSION_PREFIX + name

            # Sanitize name
            name = re.sub(r'[^a-zA-Z0-9_-]', '', name)

            if mode not in RC_FLAGS:
                self._json({"ok": False, "message": f"Invalid mode: {mode}"}, 400)
                return

            # Validate workdir
            if workdir and os.path.isdir(workdir):
                session_dir = os.path.abspath(workdir)
            else:
                session_dir = WORKING_DIR

            if _session_exists(name):
                self._json({"ok": True, "message": "Already running", "name": name})
                return

            # Start interactive claude session with appropriate flags
            claude_flags = RC_FLAGS[mode]
            # Run claude directly — no need for shell wrapper
            # tmux will run the command in its own pty
            cmd = [
                "tmux", "new-session", "-d", "-s", name,
                "-c", session_dir,
                "-e", f"RC_MODE={mode}",
                "-e", f"RC_WORKDIR={session_dir}",
                CLAUDE_BIN, *claude_flags.split(),
            ]
            print(f"  Starting session: {name} (mode={mode}, dir={session_dir}, shell={SHELL_BIN})")
            print(f"  CMD: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: tmux failed: {result.stderr.strip()}")
            else:
                print(f"  Session {name} created")
            # Background: send /remote-control, wait for URL, then /rename
            threading.Thread(
                target=_setup_session, args=(name, name, mode), daemon=True
            ).start()
            self._json({"ok": True, "message": "Started", "name": name})

        elif path == "/stop":
            body = self._read_body()
            name = body.get("name", "").strip()

            if not name:
                self._json({"ok": False, "message": "Missing session name"}, 400)
                return

            if _session_exists(name):
                _stop_session(name)
            self._json({"ok": True, "message": "Stopped"})

        elif path == "/stop-all":
            for s in _list_rc_sessions():
                _stop_session(s["name"])
            self._json({"ok": True, "message": "All stopped"})

        elif path == "/tunnel/start":
            if not _cloudflared_available():
                self._json({"ok": False, "message": "cloudflared not installed"}, 400)
                return
            _start_tunnel()
            self._json({"ok": True, "message": "Tunnel starting"})

        elif path == "/tunnel/stop":
            _stop_tunnel()
            self._json({"ok": True, "message": "Tunnel stopped"})

        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        # Log non-polling requests (skip noisy status/session polls)
        path = self.path.split("?")[0]
        if path in ("/rc/sessions", "/rc/tunnel/status", "/rc/projects"):
            return
        print(f"  {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    print(f"Claude RC Launcher v{VERSION}")
    print(f"Listening on {HOST}:{PORT}")
    print(f"Working directory: {WORKING_DIR}")
    print(f"Claude binary: {CLAUDE_BIN}")
    if AUTH_USER:
        print("Basic auth: enabled")
    if _cloudflared_available():
        print("Cloudflared: available")
    server = http.server.HTTPServer((HOST, PORT), Handler)
    server.serve_forever()
