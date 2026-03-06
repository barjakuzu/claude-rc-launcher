"""Session management — tmux session lifecycle."""

import os
import re
import subprocess
import time

from config import SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, SHELL_BIN


def list_rc_sessions():
    """Return list of rc-* tmux sessions with name, mode, URL, workdir, and status."""
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
        mode = get_session_env(name, "RC_MODE") or "c"
        workdir = get_session_env(name, "RC_WORKDIR")
        wizard = get_session_env(name, "RC_WIZARD")
        url = get_url(name)
        status = get_session_status(name)
        tokens = get_tokens(name)
        s = {"name": name, "mode": mode, "url": url, "status": status}
        if tokens is not None:
            s["tokens"] = tokens
        if wizard:
            s["wizard"] = True
        if workdir:
            s["workdir"] = workdir
            s["project"] = os.path.basename(workdir.rstrip("/"))
        sessions.append(s)
    return sessions


def get_session_env(session_name, var_name):
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


def get_url(session_name):
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


def get_tokens(session_name):
    """Extract token count from Claude Code's TUI status bar."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-e", "-p",
             "-S", "-200", "-E", "200"],
            capture_output=True, text=True, timeout=5,
        )
        # Strip ANSI escape sequences
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', r.stdout)
        # Match raw token count (e.g. "86368 tokens")
        m = re.search(r'(\d[\d,]*)\s*tokens', clean)
        if m:
            return int(m.group(1).replace(',', ''))
    except Exception:
        pass
    return None


def get_session_status(session_name):
    """Check if the process inside a tmux session is still alive.

    Returns 'running', 'dead', or 'unknown'.
    """
    try:
        r = subprocess.run(
            ["tmux", "list-panes", "-t", session_name,
             "-F", "#{pane_dead} #{pane_current_command}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return "unknown"
        parts = r.stdout.strip().split(" ", 1)
        if parts[0] == "1":
            return "dead"
        return "running"
    except Exception:
        return "unknown"


def session_exists(name):
    r = subprocess.run(
        ["tmux", "has-session", "-t", name],
        capture_output=True,
    )
    return r.returncode == 0


def setup_session(session_name, display_name, mode):
    """Handle trust prompt, send /remote-control, wait for URL, then /rename."""
    for check in range(10):
        time.sleep(0.3)
        if not session_exists(session_name):
            print(f"  {session_name}: session died after {(check+1)*0.3:.1f}s")
            return

    print(f"  {session_name}: waiting for Claude to be ready...")
    prompt_found = False
    for attempt in range(30):
        time.sleep(2)
        if not session_exists(session_name):
            print(f"  {session_name}: session died while loading")
            return
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        text = r.stdout
        # If there's a trust prompt (only in safe mode), accept it
        if not prompt_found and ("Trust" in text or "trust" in text) and mode == "safe":
            print(f"  {session_name}: accepting trust prompt")
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            continue
        if "\u276f" in text or "❯" in text or ">" in text.split("\n")[-5:]:
            prompt_found = True
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
        if not session_exists(session_name):
            print(f"  {session_name}: session died while waiting for URL")
            return
        url = get_url(session_name)
        if url:
            print(f"  {session_name}: got URL → {url}")
            time.sleep(1)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", f"/rename {display_name}"], capture_output=True)
            time.sleep(0.5)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            return
    print(f"  {session_name}: timed out waiting for URL")


def stop_session(name):
    """Gracefully stop a session: Ctrl-C twice, then force kill if needed."""
    if not session_exists(name):
        return
    subprocess.run(["tmux", "send-keys", "-t", name, "C-c"], capture_output=True)
    time.sleep(2)
    if not session_exists(name):
        return
    subprocess.run(["tmux", "send-keys", "-t", name, "C-c"], capture_output=True)
    for _ in range(8):
        time.sleep(1)
        if not session_exists(name):
            return
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
