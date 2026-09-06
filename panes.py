"""tmux pane inventory + claude-pid-to-pane mapping.

sessions.list_rc_sessions() uses this to "adopt" an external (non-launcher)
Claude Code session: given the pid `claude agents --json` reports for it,
walk that pid's ancestors until one matches a live tmux pane's shell pid,
proving the session runs inside a tmux pane the launcher can attach a
terminal WebSocket to (ws.serve_terminal) and send keys/resize to, the same
way it already does for its own rc-* sessions.
"""
import subprocess
import time

CACHE_TTL_SECONDS = 15
MAX_WALK = 25

_cache = {"panes": [], "at": 0.0}


def list_panes(run=subprocess.run, now_fn=time.time):
    """Every live tmux pane on this device: {session_name, pane_id,
    pane_pid, window_index}. Cached CACHE_TTL_SECONDS; a failed refresh
    (no tmux, timeout, non-zero exit) serves the last good cache instead
    of raising or returning garbage."""
    now = now_fn()
    if now - _cache["at"] < CACHE_TTL_SECONDS:
        return list(_cache["panes"])
    try:
        r = run(
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{window_index}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return list(_cache["panes"])
    if r.returncode != 0:
        return list(_cache["panes"])
    rows = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        session_name, pane_id, pane_pid_raw, window_index = parts
        if not pane_pid_raw.isdigit():
            continue
        rows.append({
            "session_name": session_name,
            "pane_id": pane_id,
            "pane_pid": int(pane_pid_raw),
            "window_index": window_index,
        })
    _cache["panes"], _cache["at"] = rows, now
    return list(rows)


def _default_read_ppid(pid, run=subprocess.run):
    """Parent pid of `pid`, or None if it can't be determined. Linux: parse
    /proc/<pid>/status's PPid line (fast, no subprocess). macOS (and any
    platform without /proc, or a pid /proc can't see): fall back to
    `ps -o ppid= -p <pid>`."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    try:
        r = run(["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return int(out) if out.isdigit() else None


def pane_for_pid(pid, panes=None, read_ppid=_default_read_ppid, run=subprocess.run):
    """The pane dict (same shape as one list_panes() entry) that `pid` -
    or one of its ancestors, walked up to MAX_WALK hops - runs under, or
    None if no ancestor matches any live pane's pane_pid within that
    bound (also protects against a parent-pid cycle)."""
    if panes is None:
        panes = list_panes(run=run)
    by_pid = {p["pane_pid"]: p for p in panes}
    current = pid
    seen = set()
    for _ in range(MAX_WALK):
        if current in by_pid:
            return by_pid[current]
        if current is None or current <= 1 or current in seen:
            return None
        seen.add(current)
        current = read_ppid(current, run=run)
    return None
