"""Session management — tmux session lifecycle."""

import datetime
import glob
import json
import os
import re
import subprocess
import time
import threading

from config import SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, SHELL_BIN

# Stores error messages for sessions that failed to start.
# Key: session name, Value: (error string, timestamp).
# Errors expire after ERROR_TTL seconds.
_session_errors = {}
_session_errors_lock = threading.Lock()
ERROR_TTL = 30


def get_all_session_errors():
    """Return all unexpired session errors and clean up old ones."""
    now = time.time()
    with _session_errors_lock:
        expired = [k for k, (_, ts) in _session_errors.items() if now - ts > ERROR_TTL]
        for k in expired:
            del _session_errors[k]
        return {k: msg for k, (msg, _) in _session_errors.items()}


def _store_session_error(name, error):
    with _session_errors_lock:
        _session_errors[name] = (error, time.time())


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


def _is_rc_active(session_name):
    """Check if the status bar shows 'Remote Control active' (not connecting/failed/reconnecting)."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-e", "-p",
             "-S", "-200", "-E", "200"],
            capture_output=True, text=True, timeout=5,
        )
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', r.stdout)
        return "Remote Control active" in clean
    except Exception:
        return False


def get_active_rc_session():
    """Return the name of the session that currently has remote-control active, or None.
    Only one remote-control session can be active at a time per account."""
    r = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    for line in r.stdout.strip().splitlines():
        name = line.strip()
        if not name.startswith(SESSION_PREFIX):
            continue
        if _is_rc_active(name):
            return name
    return None


def get_url(session_name):
    """Extract the claude.ai URL from a tmux session's pane output.
    Only returns a URL if remote-control is actually active (not connecting/failed)."""
    # First check if remote-control is in a healthy state
    # If it's "connecting", "reconnecting", or "failed", URL is not usable
    if not _is_rc_active(session_name):
        # Still store/return URL for internal use (setup_session needs it)
        # but mark it via env var so callers know it's not confirmed
        return None

    # Scan pane output for the most recent URL (check recent first, then deeper)
    for history_lines in ("-50", "-500"):
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", session_name, "-p", "-S", history_lines, "-J"],
                capture_output=True, text=True, timeout=5,
            )
            text = r.stdout.replace("\n", " ")
            # Find ALL URLs and return the last (most recent) one
            matches = re.findall(r'(https://claude\.ai/code/session_[^\s]+)', text)
            if matches:
                url = matches[-1]
                # Update stored env var if it changed
                stored = get_session_env(session_name, "RC_URL")
                if stored != url:
                    subprocess.run(
                        ["tmux", "set-environment", "-t", session_name, "RC_URL", url],
                        capture_output=True,
                    )
                return url
        except Exception:
            pass
    # Fall back to stored env var (survives scrollback overflow)
    stored = get_session_env(session_name, "RC_URL")
    if stored and stored.startswith("https://claude.ai/code/session_"):
        return stored
    return None


def _get_url_internal(session_name):
    """Like get_url but skips the active check — for internal setup_session use."""
    for history_lines in ("-50", "-500"):
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", session_name, "-p", "-S", history_lines, "-J"],
                capture_output=True, text=True, timeout=5,
            )
            text = r.stdout.replace("\n", " ")
            matches = re.findall(r'(https://claude\.ai/code/session_[^\s]+)', text)
            if matches:
                return matches[-1]
        except Exception:
            pass
    stored = get_session_env(session_name, "RC_URL")
    if stored and stored.startswith("https://claude.ai/code/session_"):
        return stored
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


def _capture_pane_text(session_name):
    """Capture pane output, return stripped text or empty string.
    Filters out tmux's 'Pane is dead' message from remain-on-exit."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in r.stdout.strip().splitlines()
                 if not l.startswith("Pane is dead")]
        return "\n".join(lines).strip()
    except Exception:
        return ""


def _kill_dead_session(session_name):
    """Kill a tmux session that has remain-on-exit keeping it alive."""
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)


def setup_session(session_name, display_name, mode):
    """Handle trust prompt, send /remote-control, wait for URL, then /rename."""
    # Keep the tmux PTY output flowing by piping to /dev/null.
    # This prevents output buffering issues in detached sessions that can
    # cause Claude's WebSocket (remote-control) to stall or disconnect.
    subprocess.run(
        ["tmux", "pipe-pane", "-t", session_name, "cat > /dev/null"],
        capture_output=True,
    )
    last_output = ""
    for check in range(10):
        time.sleep(0.3)
        if not session_exists(session_name):
            error = last_output or "Session exited immediately"
            print(f"  {session_name}: session died after {(check+1)*0.3:.1f}s — {error}")
            _store_session_error(session_name, error)
            return
        status = get_session_status(session_name)
        last_output = _capture_pane_text(session_name)
        if status == "dead":
            error = last_output or "Session process exited"
            print(f"  {session_name}: session died after {(check+1)*0.3:.1f}s — {error}")
            _store_session_error(session_name, error)
            _kill_dead_session(session_name)
            return

    # Check if the wrapper shell caught an error (claude exited, bash is sleeping)
    pane_check = _capture_pane_text(session_name)
    if pane_check and "Claude Code" not in pane_check and "❯" not in pane_check:
        # Pane has content but no Claude TUI — likely an error message
        lines = [l.strip() for l in pane_check.splitlines() if l.strip()]
        if lines and not any(kw in pane_check for kw in ["Accessing workspace", "Bypass Permissions"]):
            error = "\n".join(lines)
            print(f"  {session_name}: process failed — {error}")
            _store_session_error(session_name, error)
            _kill_dead_session(session_name)
            return

    print(f"  {session_name}: waiting for Claude to be ready...")
    prompt_found = False
    for attempt in range(30):
        time.sleep(2)
        if not session_exists(session_name):
            error = last_output or "Session exited while loading"
            print(f"  {session_name}: session died while loading — {error}")
            _store_session_error(session_name, error)
            return
        if get_session_status(session_name) == "dead":
            last_output = _capture_pane_text(session_name) or last_output
            error = last_output or "Session process exited while loading"
            print(f"  {session_name}: session died while loading — {error}")
            _store_session_error(session_name, error)
            _kill_dead_session(session_name)
            return
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        text = r.stdout
        last_output = text.strip() or last_output
        # Handle bypass permissions confirmation prompt
        if "Bypass Permissions mode" in text and "Yes, I accept" in text:
            print(f"  {session_name}: accepting bypass permissions prompt")
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Down"], capture_output=True)
            time.sleep(0.3)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            # Verify the dialog was dismissed; retry if still showing
            for _retry in range(3):
                time.sleep(1)
                recheck = subprocess.run(
                    ["tmux", "capture-pane", "-t", session_name, "-p"],
                    capture_output=True, text=True, timeout=5,
                ).stdout
                if "Bypass Permissions mode" not in recheck or "Yes, I accept" not in recheck:
                    break
                print(f"  {session_name}: bypass prompt still showing, retrying...")
                subprocess.run(["tmux", "send-keys", "-t", session_name, "Down"], capture_output=True)
                time.sleep(0.3)
                subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            continue
        # Accept trust folder prompt in all modes
        if not prompt_found and "trust" in text.lower() and "Yes, I trust" in text:
            print(f"  {session_name}: accepting trust prompt")
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            continue
        # Handle --resume session picker (must check BEFORE prompt detection,
        # because the picker uses ❯ as a cursor which looks like the Claude prompt)
        if "Resume Session" in text or ("Search" in text and "Ctrl+" in text):
            resume_search = get_session_env(session_name, "RC_RESUME_SEARCH")
            if resume_search:
                # Only type if we haven't already (check if search box has our text)
                if resume_search not in text:
                    print(f"  {session_name}: resume picker detected, searching for '{resume_search}'")
                    subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", resume_search], capture_output=True)
                    time.sleep(2)
                else:
                    print(f"  {session_name}: resume picker filtered, selecting session")
            else:
                print(f"  {session_name}: resume picker detected, selecting first session")
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            time.sleep(2)
            continue
        # Look for Claude's interactive prompt "❯ " (but not menu items like "❯ 1.")
        for line in text.split("\n"):
            stripped = line.strip()
            # Match standalone prompt: line is just "❯" or "❯ " (empty input)
            if stripped in ("❯", "\u276f"):
                prompt_found = True
                break
            # Match "❯ " not followed by a digit (which would be a menu item)
            if re.search(r'[❯\u276f]\s*$', stripped):
                prompt_found = True
                break
        if prompt_found:
            prompt_found = True
            break
    else:
        print(f"  {session_name}: timed out waiting for Claude prompt")
        return

    # Check if another session already has remote-control active
    # (only one RC session allowed per account — a second one destabilizes both)
    existing_rc = get_active_rc_session()
    if existing_rc and existing_rc != session_name:
        print(f"  {session_name}: skipping /remote-control — {existing_rc} already has it active")
        _store_session_error(session_name,
            f"Remote control not started: {existing_rc} already has an active connection. "
            "Only one remote-control session is allowed at a time. "
            "Stop that session first, or use it instead.")
        # Still rename the session
        time.sleep(1)
        subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", f"/rename {display_name}"], capture_output=True)
        time.sleep(0.5)
        subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
        return

    # Wait for CLI to fully initialize after showing prompt
    # (prompt appears before internal WebSocket/API connections are ready)
    print(f"  {session_name}: prompt found, waiting for CLI to fully initialize...")
    time.sleep(10)

    # Check if remote-control is already active (status bar shows "Remote Control active")
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-e", "-p", "-S", "-200", "-E", "200"],
            capture_output=True, text=True, timeout=5,
        )
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', r.stdout)
        if "Remote Control active" in clean:
            url = _get_url_internal(session_name)
            if url:
                print(f"  {session_name}: remote-control already active → {url}")
                subprocess.run(
                    ["tmux", "set-environment", "-t", session_name, "RC_URL", url],
                    capture_output=True,
                )
                time.sleep(1)
                subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", f"/rename {display_name}"], capture_output=True)
                time.sleep(0.5)
                subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
                return
    except Exception:
        pass

    def _send_rc(sname):
        """Send /remote-control and press Enter."""
        subprocess.run(["tmux", "send-keys", "-t", sname, "-l", "/remote-control"], capture_output=True)
        time.sleep(1)
        subprocess.run(["tmux", "send-keys", "-t", sname, "Enter"], capture_output=True)

    def _wait_for_rc_active(sname, timeout=60):
        """Poll until 'Remote Control active' appears in status bar.
        Returns the URL if successful, None if timed out/failed.
        Handles menus and failures along the way."""
        rc_menu_handled = False
        for _ in range(timeout // 2):
            time.sleep(2)
            if not session_exists(sname):
                print(f"  {sname}: session died while waiting for URL")
                return None
            pane = _capture_pane_text(sname)
            # Handle "Enable Remote Control" menu (first-time setup)
            if not rc_menu_handled and "Enable Remote Control" in pane:
                print(f"  {sname}: accepting enable remote-control menu")
                subprocess.run(["tmux", "send-keys", "-t", sname, "Enter"], capture_output=True)
                rc_menu_handled = True
                continue
            # Handle the "already active" menu — just press Escape to dismiss
            if "Disconnect this session" in pane:
                print(f"  {sname}: RC menu appeared, dismissing (already connected)")
                subprocess.run(["tmux", "send-keys", "-t", sname, "Escape"], capture_output=True)
                time.sleep(1)
                # Check if it's already active
                if _is_rc_active(sname):
                    url = _get_url_internal(sname)
                    if url:
                        return url
                continue
            # Check status bar for definitive state
            if _is_rc_active(sname):
                url = _get_url_internal(sname)
                if url:
                    return url
            # Check for failure — return None to trigger retry
            try:
                sr = subprocess.run(
                    ["tmux", "capture-pane", "-t", sname, "-e", "-p", "-S", "-200", "-E", "200"],
                    capture_output=True, text=True, timeout=5,
                )
                clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', sr.stdout)
                if "Remote Control failed" in clean:
                    print(f"  {sname}: remote-control failed")
                    return None
            except Exception:
                pass
        return None

    # Try /remote-control up to 3 times
    for attempt in range(3):
        print(f"  {session_name}: sending /remote-control (attempt {attempt + 1}/3)")
        _send_rc(session_name)
        url = _wait_for_rc_active(session_name, timeout=30)
        if url:
            print(f"  {session_name}: remote-control active → {url}")
            subprocess.run(
                ["tmux", "set-environment", "-t", session_name, "RC_URL", url],
                capture_output=True,
            )
            time.sleep(1)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", f"/rename {display_name}"], capture_output=True)
            time.sleep(0.5)
            subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"], capture_output=True)
            return
        # Wait before retry
        print(f"  {session_name}: attempt {attempt + 1} failed, waiting before retry...")
        time.sleep(5)
    print(f"  {session_name}: timed out after 3 attempts")


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


def _find_session_uuid(tmux_name, workdir):
    """Find the Claude session UUID for a tmux session by matching the
    session title (set via /rename) in the project's JSONL files."""
    claude_projects = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(claude_projects):
        return None

    # Build ordered list of project dirs — prefer ones matching the workdir
    all_proj_dirs = sorted(glob.glob(os.path.join(claude_projects, "*")),
                           key=os.path.getmtime, reverse=True)
    # Claude encodes workdir as project dir name (e.g. /root → -root)
    workdir_encoded = workdir.replace("/", "-") if workdir else ""
    matching = [d for d in all_proj_dirs if os.path.basename(d) == workdir_encoded]
    other = [d for d in all_proj_dirs if os.path.basename(d) != workdir_encoded]
    proj_dirs = matching + other

    for proj_dir in proj_dirs:
        if not os.path.isdir(proj_dir):
            continue
        for f in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl")),
                        key=os.path.getmtime, reverse=True)[:10]:
            try:
                # Check head (first 20 lines) — covers resumed sessions
                # and tail (last 50 lines) — covers /rename written mid-session
                with open(f) as fh:
                    head_lines = []
                    for i, line in enumerate(fh):
                        if i >= 20:
                            break
                        head_lines.append(line)
                # Check head
                for line in head_lines:
                    try:
                        d = json.loads(line.strip())
                        if d.get("customTitle") == tmux_name:
                            return os.path.splitext(os.path.basename(f))[0]
                    except (json.JSONDecodeError, KeyError):
                        continue
                # Check tail — read last 50 lines for /rename written later
                with open(f, "rb") as fh:
                    fh.seek(0, 2)
                    fsize = fh.tell()
                    # Read last ~64KB to get tail lines
                    chunk_size = min(fsize, 65536)
                    fh.seek(fsize - chunk_size)
                    tail_text = fh.read().decode("utf-8", errors="ignore")
                    tail_lines = tail_text.splitlines()[-50:]
                for line in tail_lines:
                    try:
                        d = json.loads(line.strip())
                        if d.get("customTitle") == tmux_name:
                            return os.path.splitext(os.path.basename(f))[0]
                    except (json.JSONDecodeError, KeyError):
                        continue
            except Exception:
                continue
    return None


def restart_session(name, mode=None, workdir=None, model=None, sandbox=False,
                    resume=True):
    """Restart a dead or stale session. Kills the old tmux session and creates
    a new one with the same parameters. If resume=True, passes --resume to
    Claude so conversation context is preserved."""
    from config import CLAUDE_BIN, RC_FLAGS, MODEL_MAP, SESSION_PREFIX, SHELL_BIN

    # Read existing session env before killing
    if mode is None:
        mode = get_session_env(name, "RC_MODE") or "c"
    if workdir is None:
        workdir = get_session_env(name, "RC_WORKDIR") or "."

    session_dir = os.path.abspath(workdir)

    # Find the Claude session UUID BEFORE killing (so JSONL is still fresh)
    resume_id = None
    if resume:
        resume_id = _find_session_uuid(name, session_dir)
        print(f"  {name}: UUID lookup → {resume_id[:8] if resume_id else 'not found'}")

    # Kill the old session
    if session_exists(name):
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
        # Brief wait for tmux cleanup
        time.sleep(0.5)

    claude_flags = RC_FLAGS.get(mode, RC_FLAGS["c"])
    claude_args = claude_flags.split()

    if resume:
        if resume_id:
            claude_args.extend(["--resume", resume_id])
        else:
            claude_args.append("--resume")

    model_flag = MODEL_MAP.get(model) if model else None
    if model_flag:
        claude_args.extend(["--model", model_flag])

    # Use the display name to search in the picker if no exact UUID found
    display_name = name.replace(SESSION_PREFIX, "")
    env_flags = [
        "-e", f"RC_MODE={mode}",
        "-e", f"RC_WORKDIR={session_dir}",
        "-e", f"RC_RESUME_SEARCH={display_name}",
        "-e", "DISPLAY=:1",
        "-e", "TERM=xterm-256color",
    ]
    if sandbox or os.geteuid() == 0:
        env_flags.extend(["-e", "IS_SANDBOX=1"])

    claude_cmd = " ".join([f"CLAUDECODE= {CLAUDE_BIN}"] + claude_args)
    wrapper = f'{claude_cmd} 2>&1 || {{ echo ""; sleep 30; }}'
    cmd = [
        "tmux", "new-session", "-d", "-s", name,
        "-c", session_dir,
        "-x", "200", "-y", "50",
        *env_flags,
        "bash", "-c", wrapper,
    ]
    print(f"  Restarting session: {name} (mode={mode}, resume={resume}, dir={session_dir})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = f"tmux failed: {result.stderr.strip()}"
        print(f"  ERROR: {error}")
        return False, error

    display_name = name
    threading.Thread(
        target=setup_session, args=(name, display_name, mode), daemon=True
    ).start()
    return True, "Restarting"


def _project_dir_to_path(dirname):
    """Convert Claude's project dir name back to a filesystem path.
    e.g. '-root--claude-rc-app' → '/root/.claude-rc/app'"""
    # Claude encodes paths by replacing / with - and . with nothing (roughly)
    # We'll try to resolve it, but it's a best-effort mapping.
    return dirname.replace("-", "/").replace("//", "/-")


def list_resumable_sessions():
    """Scan Claude's session storage and return resumable sessions grouped by project."""
    claude_projects = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(claude_projects):
        return []

    projects = []
    for proj_dir in sorted(glob.glob(os.path.join(claude_projects, "*")),
                           key=os.path.getmtime, reverse=True):
        if not os.path.isdir(proj_dir):
            continue
        proj_name = os.path.basename(proj_dir)

        session_files = sorted(
            glob.glob(os.path.join(proj_dir, "*.jsonl")),
            key=os.path.getmtime, reverse=True,
        )
        if not session_files:
            continue

        sessions = []
        for f in session_files:
            size = os.path.getsize(f)
            if size < 10:
                continue  # Skip empty/trivial files
            mtime = os.path.getmtime(f)
            session_id = os.path.splitext(os.path.basename(f))[0]
            name = None
            branch = None
            cwd = None

            try:
                with open(f) as fh:
                    for line_num, line in enumerate(fh):
                        if line_num > 20:
                            break
                        try:
                            d = json.loads(line.strip())
                            if d.get("type") == "custom-title" and d.get("customTitle"):
                                name = d["customTitle"]
                            if d.get("gitBranch") and not branch:
                                branch = d["gitBranch"]
                            if d.get("cwd") and not cwd:
                                cwd = d["cwd"]
                        except (json.JSONDecodeError, KeyError):
                            continue
                # Also check tail for /rename written mid-session (latest title wins)
                with open(f, "rb") as fh:
                        fh.seek(0, 2)
                        fsize = fh.tell()
                        chunk_size = min(fsize, 65536)
                        fh.seek(fsize - chunk_size)
                        tail_text = fh.read().decode("utf-8", errors="ignore")
                        for tline in reversed(tail_text.splitlines()):
                            try:
                                d = json.loads(tline.strip())
                                if d.get("type") == "custom-title" and d.get("customTitle"):
                                    name = d["customTitle"]
                                    break
                            except (json.JSONDecodeError, KeyError):
                                continue
            except OSError:
                continue

            # Skip tiny unnamed sessions (likely failed starts)
            if not name and size < 1024:
                continue
            sessions.append({
                "id": session_id,
                "name": name,
                "branch": branch or "HEAD",
                "size": size,
                "size_label": f"{size / 1024:.0f}KB" if size < 1048576 else f"{size / 1048576:.1f}MB",
                "updated": datetime.datetime.fromtimestamp(mtime).isoformat(),
                "cwd": cwd,
            })

        if sessions:
            projects.append({
                "project": proj_name,
                "sessions": sessions,
            })

    return projects


def resume_session(session_name, session_title, project_dir, mode="c"):
    """Launch a new tmux session with claude --resume, select the target session
    in the picker by searching for its title, and set up remote control."""
    from config import CLAUDE_BIN, RC_FLAGS, MODEL_MAP, SESSION_PREFIX

    if mode not in RC_FLAGS:
        mode = "c"

    # Sanitize session_name: must be a valid UUID-like string (alphanumeric + hyphens)
    session_name = re.sub(r'[^a-zA-Z0-9_-]', '', session_name)
    if not session_name:
        return False, "Invalid session ID", ""

    # Sanitize project_dir: strip path separators to prevent traversal
    project_dir = re.sub(r'[/\\]', '', project_dir)

    # Build tmux session name from the session title
    tmux_name = session_title or session_name[:8]
    if not tmux_name.startswith(SESSION_PREFIX):
        tmux_name = SESSION_PREFIX + tmux_name
    tmux_name = re.sub(r'[^a-zA-Z0-9_-]', '', tmux_name)

    if session_exists(tmux_name):
        return True, "Already running", tmux_name

    # Resolve working directory from project dir name
    claude_projects = os.path.expanduser("~/.claude/projects")
    proj_path = os.path.join(claude_projects, project_dir)
    # Verify resolved path is under claude projects dir
    proj_path = os.path.realpath(proj_path)
    if not proj_path.startswith(os.path.realpath(claude_projects) + os.sep):
        return False, "Invalid project", tmux_name
    # Try to get cwd from the session file
    session_file = os.path.join(proj_path, f"{session_name}.jsonl")
    session_dir = None
    if os.path.isfile(session_file):
        try:
            with open(session_file) as fh:
                for line in fh:
                    d = json.loads(line.strip())
                    if d.get("cwd"):
                        session_dir = d["cwd"]
                        break
        except Exception:
            pass
    if not session_dir or not os.path.isdir(session_dir):
        session_dir = os.path.expanduser("~")

    claude_flags = RC_FLAGS[mode]
    # Pass session UUID directly to --resume to skip the picker
    claude_args = claude_flags.split() + ["--resume", session_name]

    env_flags = [
        "-e", f"RC_MODE={mode}",
        "-e", f"RC_WORKDIR={session_dir}",
        "-e", "DISPLAY=:1",
        "-e", "TERM=xterm-256color",
    ]
    if os.geteuid() == 0:
        env_flags.extend(["-e", "IS_SANDBOX=1"])

    claude_cmd = " ".join([f"CLAUDECODE= {CLAUDE_BIN}"] + claude_args)
    wrapper = f'{claude_cmd} 2>&1 || {{ echo ""; sleep 30; }}'
    cmd = [
        "tmux", "new-session", "-d", "-s", tmux_name,
        "-c", session_dir,
        "-x", "200", "-y", "50",
        *env_flags,
        "bash", "-c", wrapper,
    ]

    print(f"  Resume: starting session {tmux_name} (resume={session_name[:8]}, dir={session_dir})")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = f"tmux failed: {result.stderr.strip()}"
        print(f"  ERROR: {error}")
        return False, error, tmux_name

    threading.Thread(
        target=setup_session, args=(tmux_name, tmux_name, mode), daemon=True
    ).start()
    return True, "Resuming", tmux_name
