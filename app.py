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


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude RC Launcher</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, 'Segoe UI', sans-serif; background: #0a0a0a; color: #e2e8f0; min-height: 100vh; padding: 1.5rem 1rem; }
  .container { max-width: 480px; margin: 0 auto; }

  .logo { text-align: center; margin-bottom: 1.5rem; }
  .logo svg { width: 36px; height: 36px; margin-bottom: 0.5rem; }
  h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em; background: linear-gradient(135deg, #d4d4d4, #a3a3a3); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
  .subtitle { color: #475569; font-size: 0.75rem; margin-top: 0.2rem; letter-spacing: 0.05em; text-transform: uppercase; }

  .card { background: #141414; border: 1px solid rgba(255,255,255,0.06); border-radius: 16px; padding: 1.5rem; margin-bottom: 1.25rem; }
  .form-group { margin-bottom: 1.2rem; }
  label { display: block; font-size: 0.75rem; color: #64748b; margin-bottom: 0.35rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  input[type="text"] { width: 100%; padding: 0.6rem 0.8rem; border: 1px solid #262626; border-radius: 10px; background: #0a0a0a; color: #e2e8f0; font-size: 0.9rem; transition: all 0.2s; }
  input[type="text"]:focus { outline: none; border-color: #525252; box-shadow: 0 0 0 3px rgba(82,82,82,0.15); }
  input[type="text"]::placeholder { color: #334155; }

  .select-wrap { position: relative; }
  .custom-select { width: 100%; padding: 0.6rem 2.5rem 0.6rem 0.8rem; border: 1px solid #262626; border-radius: 10px; background: #0a0a0a; color: #e2e8f0; font-size: 0.9rem; appearance: none; -webkit-appearance: none; cursor: pointer; transition: all 0.2s; }
  .custom-select:focus { outline: none; border-color: #525252; box-shadow: 0 0 0 3px rgba(82,82,82,0.15); }
  .select-arrow { position: absolute; right: 0.8rem; top: 50%; transform: translateY(-50%); pointer-events: none; color: #475569; }
  .select-arrow svg { width: 12px; height: 12px; }
  .mode-info { display: flex; align-items: center; gap: 0.5rem; padding: 0.55rem 0.75rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.04); border-radius: 8px; margin-top: 0.75rem; }
  .mode-icon { flex-shrink: 0; display: flex; align-items: center; }
  .mode-icon svg { width: 16px; height: 16px; }
  .mode-detail { font-size: 0.75rem; color: #94a3b8; line-height: 1.4; }
  .mode-detail strong { color: #cbd5e1; font-weight: 600; }

  /* Buttons */
  .btn-launch { width: 100%; padding: 0.75rem; border: none; border-radius: 10px; font-size: 0.95rem; font-weight: 700; cursor: pointer; transition: all 0.25s; display: flex; align-items: center; justify-content: center; gap: 0.5rem; }
  .btn-launch:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-launch:hover:not(:disabled) { transform: translateY(-1px); }
  .btn-launch:active:not(:disabled) { transform: translateY(0); }
  .btn-launch svg { width: 16px; height: 16px; }
  .btn-launch.mode-c { background: linear-gradient(135deg, #262626, #1a1a1a); color: #e5e5e5; box-shadow: 0 4px 15px rgba(0,0,0,0.3); border: 1px solid #333; }
  .btn-launch.mode-c:hover:not(:disabled) { box-shadow: 0 6px 20px rgba(0,0,0,0.4); border-color: #444; }
  .btn-launch.mode-ci { background: linear-gradient(135deg, #262626, #1a1a1a); color: #e5e5e5; box-shadow: 0 4px 15px rgba(0,0,0,0.3); border: 1px solid #333; }
  .btn-launch.mode-ci:hover:not(:disabled) { box-shadow: 0 6px 20px rgba(0,0,0,0.4); border-color: #444; }
  .btn-launch.mode-safe { background: linear-gradient(135deg, #262626, #1a1a1a); color: #e5e5e5; box-shadow: 0 4px 15px rgba(0,0,0,0.3); border: 1px solid #333; }
  .btn-launch.mode-safe:hover:not(:disabled) { box-shadow: 0 6px 20px rgba(0,0,0,0.4); border-color: #444; }

  .btn-stop { background: rgba(239,68,68,0.12); color: #f87171; font-size: 0.78rem; padding: 0.4rem 0.85rem; width: auto; border: 1px solid rgba(239,68,68,0.2); border-radius: 6px; cursor: pointer; font-weight: 600; transition: all 0.2s; display: inline-flex; align-items: center; gap: 0.35rem; }
  .btn-stop:hover:not(:disabled) { background: rgba(239,68,68,0.2); }
  .btn-stop:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-stop svg { width: 12px; height: 12px; }

  /* Sessions */
  .sessions-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
  .section-title { font-size: 0.8rem; color: #475569; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: flex; align-items: center; gap: 0.4rem; }
  .section-title svg { width: 14px; height: 14px; }
  .session-card { background: #141414; border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 1rem 1.15rem; margin-bottom: 0.5rem; transition: border-color 0.2s; }
  .session-card:hover { border-color: rgba(255,255,255,0.1); }
  .session-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
  .session-name { font-weight: 600; font-size: 0.9rem; display: flex; align-items: center; gap: 0.4rem; }
  .badge { font-size: 0.6rem; font-weight: 700; padding: 0.18rem 0.5rem; border-radius: 5px; text-transform: uppercase; letter-spacing: 0.04em; }
  .badge-c { background: rgba(163,163,163,0.1); color: #a3a3a3; border: 1px solid rgba(163,163,163,0.2); }
  .badge-ci { background: rgba(163,163,163,0.1); color: #a3a3a3; border: 1px solid rgba(163,163,163,0.2); }
  .badge-safe { background: rgba(163,163,163,0.1); color: #a3a3a3; border: 1px solid rgba(163,163,163,0.2); }
  .perm-tag { font-size: 0.6rem; padding: 0.15rem 0.45rem; border-radius: 4px; font-weight: 600; }
  .perm-skip { background: rgba(163,163,163,0.08); color: #a3a3a3; border: 1px solid rgba(163,163,163,0.15); }
  .perm-normal { background: rgba(163,163,163,0.08); color: #a3a3a3; border: 1px solid rgba(163,163,163,0.15); }
  .session-url { background: #0a0a0a; border: 1px solid #262626; border-radius: 8px; padding: 0.5rem 0.75rem; margin-bottom: 0.65rem; word-break: break-all; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.75rem; }
  .session-url a { color: #d4d4d4; text-decoration: none; }
  .session-url a:hover { text-decoration: underline; }
  .waiting { color: #64748b; font-style: italic; display: flex; align-items: center; gap: 0.4rem; }
  .waiting svg { width: 14px; height: 14px; animation: spin 1.5s linear infinite; }
  .empty { text-align: center; color: #334155; padding: 2rem 0; font-size: 0.85rem; }
  .project-label { font-size: 0.7rem; color: #475569; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.3rem; }
  .project-label svg { width: 11px; height: 11px; }

  /* Share / Remote Access */
  .share-card { background: #141414; border: 1px solid rgba(255,255,255,0.06); border-radius: 16px; padding: 1.25rem; margin-top: 1.25rem; }
  .share-card h3 { font-size: 0.8rem; color: #475569; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.4rem; }
  .share-card h3 svg { width: 14px; height: 14px; }
  .share-url { background: #0a0a0a; border: 1px solid #262626; border-radius: 8px; padding: 0.5rem 0.75rem; margin-bottom: 0.65rem; word-break: break-all; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.75rem; display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
  .share-url a { color: #d4d4d4; text-decoration: none; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .btn-copy { background: rgba(163,163,163,0.1); color: #a3a3a3; border: 1px solid rgba(163,163,163,0.2); border-radius: 6px; padding: 0.3rem 0.6rem; font-size: 0.7rem; font-weight: 600; cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; gap: 0.3rem; }
  .btn-copy:hover { background: rgba(163,163,163,0.2); }
  .btn-copy svg { width: 12px; height: 12px; }
  .btn-share { background: linear-gradient(135deg, #262626, #1a1a1a); color: #e5e5e5; border: 1px solid #333; border-radius: 10px; padding: 0.6rem 1.2rem; font-size: 0.85rem; font-weight: 700; cursor: pointer; transition: all 0.25s; width: 100%; display: flex; align-items: center; justify-content: center; gap: 0.4rem; }
  .btn-share:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.4); border-color: #444; }
  .btn-share:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
  .btn-share svg { width: 16px; height: 16px; }
  .btn-share-stop { background: rgba(239,68,68,0.12); color: #f87171; border: 1px solid rgba(239,68,68,0.2); border-radius: 10px; padding: 0.6rem 1.2rem; font-size: 0.85rem; font-weight: 700; cursor: pointer; transition: all 0.2s; width: 100%; margin-top: 0.5rem; display: flex; align-items: center; justify-content: center; gap: 0.4rem; }
  .btn-share-stop:hover { background: rgba(239,68,68,0.2); }
  .btn-share-stop svg { width: 14px; height: 14px; }
  .share-warning { background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.2); border-radius: 8px; padding: 0.55rem 0.75rem; margin-bottom: 0.65rem; font-size: 0.75rem; color: #fbbf24; line-height: 1.4; display: flex; align-items: flex-start; gap: 0.4rem; }
  .share-warning svg { width: 14px; height: 14px; flex-shrink: 0; margin-top: 1px; }
  .share-dimmed { opacity: 0.5; }
  .share-dimmed .btn-share { pointer-events: none; }
  .share-install-hint { font-size: 0.75rem; color: #64748b; margin-top: 0.5rem; }
  .share-install-hint a { color: #a3a3a3; text-decoration: none; }

  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.2); border-top-color: currentColor; border-radius: 50%; animation: spin 0.6s linear infinite; }
  .spinner-sm { width: 12px; height: 12px; border-width: 1.5px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  #custom-path-wrap { display: none; margin-top: 0.5rem; }

  /* Directory browser */
  .dir-browser-wrap { position: relative; }
  .dir-browser-input { width: 100%; padding: 0.6rem 0.8rem; border: 1px solid #262626; border-radius: 10px; background: #0a0a0a; color: #e2e8f0; font-size: 0.9rem; cursor: pointer; transition: all 0.2s; }
  .dir-browser-input:hover { border-color: #333; }
  .dir-browser-input:focus { outline: none; border-color: #525252; box-shadow: 0 0 0 3px rgba(82,82,82,0.15); }
  .dir-browser { display: none; position: absolute; top: 100%; left: 0; right: 0; z-index: 50; margin-top: 4px; background: #141414; border: 1px solid #262626; border-radius: 10px; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }
  .dir-browser.open { display: block; }
  .dir-breadcrumb { display: flex; flex-wrap: wrap; align-items: center; gap: 2px; padding: 0.5rem 0.75rem; border-bottom: 1px solid #1e1e1e; font-size: 0.75rem; font-family: 'SF Mono', 'Fira Code', monospace; }
  .dir-breadcrumb-seg { color: #64748b; cursor: pointer; padding: 1px 3px; border-radius: 3px; transition: color 0.15s, background 0.15s; }
  .dir-breadcrumb-seg:hover { color: #e2e8f0; background: rgba(255,255,255,0.06); }
  .dir-breadcrumb-sep { color: #333; margin: 0 1px; user-select: none; }
  .dir-list { max-height: 200px; overflow-y: auto; padding: 0.25rem 0; }
  .dir-list::-webkit-scrollbar { width: 6px; }
  .dir-list::-webkit-scrollbar-track { background: transparent; }
  .dir-list::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
  .dir-item { display: flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0.75rem; cursor: pointer; font-size: 0.82rem; color: #cbd5e1; transition: background 0.12s; }
  .dir-item:hover { background: rgba(255,255,255,0.06); }
  .dir-item svg { width: 14px; height: 14px; flex-shrink: 0; color: #475569; }
  .dir-empty { padding: 0.75rem; text-align: center; color: #475569; font-size: 0.78rem; font-style: italic; }
  .dir-select-btn { display: block; width: calc(100% - 1rem); margin: 0.35rem 0.5rem 0.5rem; padding: 0.45rem; border: 1px solid #333; border-radius: 8px; background: linear-gradient(135deg, #262626, #1a1a1a); color: #e5e5e5; font-size: 0.8rem; font-weight: 600; cursor: pointer; text-align: center; transition: all 0.2s; }
  .dir-select-btn:hover { border-color: #444; }

  .version { text-align: center; color: #1e293b; font-size: 0.65rem; margin-top: 1.5rem; }
</style>
</head>
<body>
<div class="container">
  <div class="logo">
    <svg viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="10" fill="url(#g1)"/>
      <path d="M10 18.5L15.5 24L26 13" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <defs><linearGradient id="g1" x1="0" y1="0" x2="36" y2="36"><stop stop-color="#333"/><stop offset="1" stop-color="#1a1a1a"/></linearGradient></defs>
    </svg>
    <h1>Claude Remote Control</h1>
    <div class="subtitle">Session Launcher</div>
  </div>

  <div class="card">
    <div class="form-group" id="project-group">
      <label>Working Directory</label>
      <div id="project-select-wrap" class="select-wrap" style="display:none;">
        <select id="project-select" class="custom-select" onchange="onProjectChange()"></select>
        <span class="select-arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></span>
      </div>
      <div id="dir-browser-wrap" class="dir-browser-wrap" style="display:none;">
        <input type="text" id="dir-browser-input" class="dir-browser-input" readonly onclick="toggleBrowser()" />
        <div id="dir-browser" class="dir-browser">
          <div id="dir-breadcrumb" class="dir-breadcrumb"></div>
          <div id="dir-list" class="dir-list"></div>
          <button class="dir-select-btn" onclick="selectDir(currentBrowsePath)">Use this folder</button>
        </div>
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
          <option value="c">Standard RC / skip permissions</option>
          <option value="ci">Teammate (in-process) / skip permissions</option>
          <option value="safe">Standard RC / with permissions</option>
        </select>
        <span class="select-arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></span>
      </div>
      <div class="mode-info" id="mode-info">
        <span class="mode-icon" id="mode-icon"><svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span>
        <span class="mode-detail" id="mode-detail"><strong>Unrestricted</strong> / skip permissions, no approval prompts</span>
      </div>
    </div>

    <button class="btn-launch mode-c" id="btn-launch" onclick="startSession()">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
      Launch Session
    </button>
  </div>

  <div class="sessions-header">
    <div class="section-title">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
      Running Sessions
    </div>
    <button class="btn-stop" id="btn-stop-all" style="display:none;" onclick="stopAll()">
      <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
      Stop All
    </button>
  </div>
  <div id="sessions"></div>

  <div id="share-section"></div>

  <div class="version" id="version-label"></div>
</div>
<script>
/* SVG icon templates */
const ICN = {
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="#a3a3a3" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>',
  loader: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>',
  share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
};

const MODES = {
  c:    { icon: ICN.bolt,   cls: 'mode-c',    detail: '<strong>Unrestricted</strong> / skip permissions, no approval prompts' },
  ci:   { icon: ICN.users,  cls: 'mode-ci',   detail: '<strong>Teammate in-process</strong> / skip permissions, teammate mode' },
  safe: { icon: ICN.shield, cls: 'mode-safe', detail: '<strong>Safe mode</strong> / standard permissions, requires approvals' },
};

let tunnelState = { available: false, running: false, url: null, auth_configured: false };
let stoppingSet = new Set(); /* track sessions being stopped */

function updateMode() {
  const mode = document.getElementById('mode-select').value;
  const m = MODES[mode];
  document.getElementById('mode-icon').innerHTML = m.icon;
  document.getElementById('mode-detail').innerHTML = m.detail;
  document.getElementById('btn-launch').className = 'btn-launch ' + m.cls;
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

let currentBrowsePath = null;
let selectedWorkdir = null;
let browserOpen = false;
let hasProjects = false;

function onProjectChange() {
  const sel = document.getElementById('project-select');
  const browserWrap = document.getElementById('dir-browser-wrap');
  if (sel.value === '__browse__') {
    browserWrap.style.display = 'block';
    selectedWorkdir = null;
    browseTo(currentBrowsePath || selectedWorkdir);
  } else {
    browserWrap.style.display = 'none';
    closeBrowser();
  }
}

async function loadProjects() {
  try {
    const data = await api('GET', '/projects');
    const projects = data.projects || [];
    const selWrap = document.getElementById('project-select-wrap');
    const browserWrap = document.getElementById('dir-browser-wrap');
    selectedWorkdir = null;
    currentBrowsePath = data.default || '/';
    if (projects.length === 0) {
      hasProjects = false;
      selWrap.style.display = 'none';
      browserWrap.style.display = 'block';
      document.getElementById('dir-browser-input').value = data.default || '/';
      browseTo(data.default || '/');
    } else {
      hasProjects = true;
      selWrap.style.display = '';
      browserWrap.style.display = 'none';
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
      const browse = document.createElement('option');
      browse.value = '__browse__';
      browse.textContent = 'Browse\u2026';
      sel.appendChild(browse);
    }
  } catch(e) {}
}

function toggleBrowser() {
  const panel = document.getElementById('dir-browser');
  if (browserOpen) {
    closeBrowser();
  } else {
    panel.classList.add('open');
    browserOpen = true;
    browseTo(currentBrowsePath || selectedWorkdir || '/');
  }
}

function closeBrowser() {
  document.getElementById('dir-browser').classList.remove('open');
  browserOpen = false;
}

async function browseTo(path) {
  if (!path) path = '/';
  currentBrowsePath = path;
  const listEl = document.getElementById('dir-list');
  const crumbEl = document.getElementById('dir-breadcrumb');
  listEl.innerHTML = '<div class="dir-empty">Loading\u2026</div>';
  try {
    const data = await api('GET', '/browse?path=' + encodeURIComponent(path));
    currentBrowsePath = data.path;
    // Render breadcrumb
    const parts = data.path.split('/').filter(Boolean);
    let crumbHtml = '<span class="dir-breadcrumb-seg" onclick="browseTo(\'/\')">/</span>';
    let accumulated = '';
    parts.forEach((part, i) => {
      accumulated += '/' + part;
      const p = accumulated;
      crumbHtml += '<span class="dir-breadcrumb-sep">/</span><span class="dir-breadcrumb-seg" onclick="browseTo(\'' + escHtml(p.replace(/'/g, "\\\\'")) + '\')">' + escHtml(part) + '</span>';
    });
    crumbEl.innerHTML = crumbHtml;
    // Render dirs
    if (data.dirs.length === 0) {
      listEl.innerHTML = '<div class="dir-empty">No subfolders</div>';
    } else {
      listEl.innerHTML = data.dirs.map(d => {
        const full = (data.path === '/' ? '/' : data.path + '/') + d;
        return '<div class="dir-item" onclick="browseTo(\'' + escHtml(full.replace(/'/g, "\\\\'")) + '\')">' + ICN.folder + ' ' + escHtml(d) + '</div>';
      }).join('');
    }
  } catch(e) {
    listEl.innerHTML = '<div class="dir-empty">Error loading directory</div>';
  }
}

function selectDir(path) {
  selectedWorkdir = path || currentBrowsePath;
  document.getElementById('dir-browser-input').value = selectedWorkdir;
  closeBrowser();
}

document.addEventListener('click', function(e) {
  if (!browserOpen) return;
  const wrap = document.getElementById('dir-browser-wrap');
  if (!wrap.contains(e.target)) closeBrowser();
});

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function getSelectedWorkdir() {
  const selWrap = document.getElementById('project-select-wrap');
  if (selWrap.style.display === 'none') {
    return selectedWorkdir || undefined;
  }
  const sel = document.getElementById('project-select');
  if (sel.value === '__browse__') return selectedWorkdir || undefined;
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
    stoppingSet.clear();
  } else {
    /* Clear stoppingSet for sessions that no longer exist */
    const names = new Set(data.sessions.map(s => s.name));
    for (const n of stoppingSet) { if (!names.has(n)) stoppingSet.delete(n); }

    stopAllBtn.style.display = data.sessions.length > 1 ? 'inline-flex' : 'none';
    stopAllBtn.disabled = false;
    el.innerHTML = data.sessions.map(s => {
      const badgeClass = s.mode === 'ci' ? 'badge-ci' : s.mode === 'safe' ? 'badge-safe' : 'badge-c';
      const permTag = s.mode === 'safe'
        ? '<span class="perm-tag perm-normal">safe</span>'
        : '<span class="perm-tag perm-skip">skip-perms</span>';
      const modeLabel = s.mode === 'ci' ? 'teammate' : s.mode === 'safe' ? 'safe' : 'standard';
      const urlHtml = s.url
        ? '<div class="session-url"><a href="' + escHtml(s.url) + '" target="_blank">' + escHtml(s.url) + '</a></div>'
        : '<div class="session-url"><span class="waiting">' + ICN.loader + ' Waiting for URL\u2026</span></div>';
      const projectHtml = s.project
        ? '<div class="project-label" title="' + escHtml(s.workdir || '') + '">' + ICN.folder + ' ' + escHtml(s.project) + '</div>'
        : '';
      const isStopping = stoppingSet.has(s.name);
      const stopBtn = isStopping
        ? '<button class="btn-stop" disabled><span class="spinner spinner-sm"></span> Stopping\u2026</button>'
        : '<button class="btn-stop" onclick="stopSession(\'' + s.name.replace(/'/g, "\\'") + '\')">' + ICN.stop + ' Stop</button>';
      return '<div class="session-card">' +
        '<div class="session-header">' +
          '<span class="session-name">' + escHtml(s.name) + '</span>' +
          '<span style="display:flex;gap:0.35rem;align-items:center;">' + permTag +
            '<span class="badge ' + badgeClass + '">' + modeLabel + '</span>' +
          '</span>' +
        '</div>' +
        projectHtml + urlHtml + stopBtn +
      '</div>';
    }).join('');
  }
  await refreshTunnel();
}

async function refreshTunnel() {
  try { tunnelState = await api('GET', '/tunnel/status'); } catch(e) {
    tunnelState = { available: false, running: false, url: null, auth_configured: false };
  }
  renderShare();
}

function renderShare() {
  const el = document.getElementById('share-section');
  if (!tunnelState.available) {
    el.innerHTML = '<div class="share-card share-dimmed"><h3>' + ICN.globe + ' Remote Access</h3>' +
      '<button class="btn-share" disabled>' + ICN.share + ' Share</button>' +
      '<div class="share-install-hint">Requires <a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" target="_blank">cloudflared</a></div></div>';
    return;
  }
  let body = '';
  if (tunnelState.running && tunnelState.url) {
    if (!tunnelState.auth_configured) {
      body += '<div class="share-warning">' + ICN.warn + ' <span>No authentication configured. Anyone with this link can launch sessions.</span></div>';
    }
    body += '<div class="share-url"><a href="' + escHtml(tunnelState.url) + '" target="_blank">' + escHtml(tunnelState.url) + '</a>' +
      '<button class="btn-copy" onclick="copyUrl()">' + ICN.copy + ' Copy</button></div>' +
      '<button class="btn-share-stop" onclick="stopTunnel()">' + ICN.x + ' Stop Sharing</button>';
  } else if (tunnelState.running) {
    body += '<button class="btn-share" disabled><span class="spinner"></span> Starting tunnel\u2026</button>';
  } else {
    body += '<button class="btn-share" onclick="startTunnel()">' + ICN.share + ' Share</button>';
  }
  el.innerHTML = '<div class="share-card"><h3>' + ICN.globe + ' Remote Access</h3>' + body + '</div>';
}

async function startTunnel() {
  await api('POST', '/tunnel/start');
  tunnelState.running = true; tunnelState.url = null;
  renderShare();
}

async function stopTunnel() {
  await api('POST', '/tunnel/stop');
  tunnelState.running = false; tunnelState.url = null;
  renderShare();
}

function copyUrl() {
  if (!tunnelState.url) return;
  navigator.clipboard.writeText(tunnelState.url).then(() => {
    const btn = document.querySelector('.btn-copy');
    if (btn) { btn.innerHTML = ICN.check + ' Copied!'; setTimeout(() => { btn.innerHTML = ICN.copy + ' Copy'; }, 1500); }
  });
}

async function startSession() {
  const mode = document.getElementById('mode-select').value;
  const btn = document.getElementById('btn-launch');
  const input = document.getElementById('session-name');
  const name = input.value.trim() || input.placeholder;
  const workdir = getSelectedWorkdir();
  input.value = '';
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Launching\u2026';
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
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Launch Session';
  refresh();
}

async function stopSession(name) {
  stoppingSet.add(name);
  renderSessions();
  await api('POST', '/stop', { name });
  refresh();
}

function renderSessions() {
  /* Re-render just the stop buttons without full refresh */
  document.querySelectorAll('.btn-stop[onclick]').forEach(btn => {
    const m = btn.getAttribute('onclick').match(/stopSession\('(.+?)'\)/);
    if (m && stoppingSet.has(m[1])) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner spinner-sm"></span> Stopping\u2026';
    }
  });
}

async function stopAll() {
  const btn = document.getElementById('btn-stop-all');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner spinner-sm"></span> Stopping\u2026';
  await api('POST', '/stop-all');
  refresh();
}

loadProjects();
refresh();
setInterval(refresh, 5000);
fetch('/rc/tunnel/status').then(r=>r.json()).then(d => {
  document.getElementById('version-label').textContent = 'v""" + VERSION + r"""';
}).catch(()=>{});
document.getElementById('version-label').textContent = 'v""" + VERSION + r"""';
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
        elif path.startswith("/browse"):
            from urllib.parse import urlparse, parse_qs
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
        if path in ("/rc/sessions", "/rc/tunnel/status", "/rc/projects", "/rc/browse"):
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
