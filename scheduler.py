"""Cron parser and scheduler thread."""

import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta

import stats
from config import (SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, MODEL_MAP,
                    resolve_claude_mode)
from sessions import session_exists, setup_session, get_url, list_rc_sessions
from schedules import load_schedules, save_schedules, add_history_entry
import schedules as schedules_module

_last_logged_schedule_error = None


def _schedule_error_to_log(err):
    """Return the message to print for a schedules-load failure, or None if
    `err` is falsy or already the last one logged (so a persistent failure
    logs once, not every 60 seconds)."""
    global _last_logged_schedule_error
    if not err:
        _last_logged_schedule_error = None
        return None
    if err == _last_logged_schedule_error:
        return None
    _last_logged_schedule_error = err
    return f"Scheduler: schedules.json failed to load: {err}"


# --- Cron expression parser ---

def _parse_cron_field(field, min_val, max_val):
    """Parse a single cron field into a set of valid integers.

    Supports: * (all), N (single), N-M (range), */N (step), N-M/S (range+step),
    comma-separated combinations.
    """
    result = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue

        step = None
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)

        if part == "*":
            start, end = min_val, max_val
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            val = int(part)
            if step is None:
                result.add(val)
                continue
            start, end = val, max_val

        if step:
            result.update(range(start, end + 1, step))
        else:
            result.update(range(start, end + 1))

    return result


def cron_matches(expr, dt):
    """Check if datetime dt matches a 5-field cron expression.

    Fields: minute hour day-of-month month day-of-week
    Day-of-week: 0=Sun, 1=Mon, ..., 6=Sat (7 also accepted as Sun)
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron expression: expected 5 fields, got {len(fields)}")

    minutes = _parse_cron_field(fields[0], 0, 59)
    hours = _parse_cron_field(fields[1], 0, 23)
    days = _parse_cron_field(fields[2], 1, 31)
    months = _parse_cron_field(fields[3], 1, 12)
    dows = _parse_cron_field(fields[4], 0, 7)
    # Normalize: 7 → 0 (both mean Sunday)
    if 7 in dows:
        dows.add(0)
        dows.discard(7)

    # Python: weekday() returns 0=Mon, isoweekday() returns 1=Mon.
    # Convert to cron convention: 0=Sun, 1=Mon, ..., 6=Sat
    cron_dow = (dt.weekday() + 1) % 7

    return (dt.minute in minutes and
            dt.hour in hours and
            dt.day in days and
            dt.month in months and
            cron_dow in dows)


def validate_cron(expr):
    """Validate a cron expression. Returns None if valid (including the
    null cron of a manual task), error string if invalid."""
    if expr is None:
        return None
    try:
        fields = expr.strip().split()
        if len(fields) != 5:
            return f"Expected 5 fields, got {len(fields)}"
        _parse_cron_field(fields[0], 0, 59)
        _parse_cron_field(fields[1], 0, 23)
        _parse_cron_field(fields[2], 1, 31)
        _parse_cron_field(fields[3], 1, 12)
        _parse_cron_field(fields[4], 0, 7)
        return None
    except Exception as e:
        return str(e)


def next_cron_run(expr, after_dt=None):
    """Calculate next run time for a cron expression. Returns ISO string or
    None (always None for a manual task's null cron)."""
    if expr is None:
        return None
    if after_dt is None:
        after_dt = datetime.now()
    dt = after_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Iterate up to 1 year (525600 minutes)
    for _ in range(525600):
        try:
            if cron_matches(expr, dt):
                return dt.isoformat() + 'Z'
        except ValueError:
            return None
        dt += timedelta(minutes=1)
    return None


def _due_to_fire(schedule, now):
    """True if `schedule` should fire at `now`. Manual tasks (cron: null or
    empty) never fire on a timer - only via POST /schedules/fire."""
    cron_expr = schedule.get("cron")
    if not cron_expr:
        return False
    try:
        if not cron_matches(cron_expr, now):
            return False
    except ValueError:
        return False
    last_run = schedule.get("last_run")
    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            if (last_dt.year == now.year and last_dt.month == now.month and
                    last_dt.day == now.day and last_dt.hour == now.hour and
                    last_dt.minute == now.minute):
                return False
        except (ValueError, TypeError):
            pass
    return True


# --- Session lifecycle tracking ---

_active_scheduled_sessions = {}  # schedule_id -> {session_name, started_at, schedule_safe_name}
_active_scheduled_sessions_lock = threading.Lock()


def _monitor_scheduled_sessions():
    """Check if any tracked scheduled sessions have ended."""
    with _active_scheduled_sessions_lock:
        items = list(_active_scheduled_sessions.items())
    if not items:
        return

    ended = []
    for schedule_id, info in items:
        session_name = info["session_name"]
        # Check if tmux session still exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True
        )
        if result.returncode != 0:
            # Session ended
            ended.append((schedule_id, session_name))
            duration = (datetime.now() - info["started_at"]).total_seconds() / 60

            # Try to read the run report if it exists
            runs_dir = os.path.expanduser(f"~/.claude-rc/jobs/{info['schedule_safe_name']}/runs")
            summary = f"Session ended after {int(duration)} minutes"

            # Look for a run report written by the session
            if os.path.isdir(runs_dir):
                reports = sorted(os.listdir(runs_dir), reverse=True)
                if reports:
                    try:
                        with open(os.path.join(runs_dir, reports[0])) as f:
                            import json as _json
                            report = _json.load(f)
                            if report.get("summary"):
                                summary = report["summary"]
                    except Exception:
                        pass

            add_history_entry(
                schedule_id,
                "completed",
                summary,
                duration_minutes=round(duration, 1)
            )

    with _active_scheduled_sessions_lock:
        for schedule_id, ended_session_name in ended:
            current = _active_scheduled_sessions.get(schedule_id)
            if current and current["session_name"] == ended_session_name:
                del _active_scheduled_sessions[schedule_id]


# --- Fire mechanism ---

def _fire_schedule(schedule):
    """Spawn a new Claude session for a scheduled task."""
    name = schedule.get("name", "task")
    schedule_id = schedule["id"]
    concurrency = schedule.get("concurrency", "skip")

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name.replace(" ", "-"))
    session_name = f"{SESSION_PREFIX}run-{uuid.uuid4().hex[:12]}"

    # Check-and-claim must be atomic: the entry is registered here, under the
    # lock, before any tmux command runs, so a second _fire_schedule call for
    # the same schedule (e.g. a double-click on POST /schedules/fire) that
    # arrives while setup/send is still in flight sees the claim and is
    # skipped/killed instead of racing a duplicate session into existence.
    with _active_scheduled_sessions_lock:
        existing = _active_scheduled_sessions.get(schedule_id)
        if existing and session_exists(existing["session_name"]):
            if concurrency == "kill":
                subprocess.run(["tmux", "kill-session", "-t", existing["session_name"]],
                                capture_output=True)
                print(f"  Scheduler: killed running session {existing['session_name']} "
                      f"for '{name}' (concurrency=kill)")
            else:
                add_history_entry(schedule_id, "skipped",
                                   f"Still running as {existing['session_name']} (concurrency=skip)")
                print(f"  Scheduler: skipped '{name}', already running as {existing['session_name']}")
                return
        _active_scheduled_sessions[schedule_id] = {
            "session_name": session_name,
            "started_at": datetime.now(),
            "schedule_safe_name": safe_name,
        }

    def _release_claim():
        """Undo the claim above when the launch fails before the session is
        actually up, so a failed fire doesn't permanently block later ones."""
        with _active_scheduled_sessions_lock:
            current = _active_scheduled_sessions.get(schedule_id)
            if current and current["session_name"] == session_name:
                del _active_scheduled_sessions[schedule_id]

    workdir = schedule.get("workdir", "/tmp")
    mode = schedule.get("mode", "c")
    model = schedule.get("model")

    if mode not in RC_FLAGS:
        mode = "c"

    if not os.path.isdir(workdir):
        _release_claim()
        add_history_entry(schedule["id"], "error", f"Workdir not found: {workdir}")
        print(f"  Scheduler: workdir not found: {workdir}")
        return

    # Build prompt text
    prompt = schedule.get("prompt", "")
    instructions_file = schedule.get("instructions_file")
    use_file_ref = False
    if instructions_file and os.path.isfile(instructions_file):
        # Restrict to files under home directory or workdir
        real_path = os.path.realpath(instructions_file)
        allowed_roots = [os.path.expanduser("~")]
        if workdir:
            allowed_roots.append(os.path.realpath(workdir))
        if any(real_path.startswith(root + os.sep) or real_path == root for root in allowed_roots):
            # For large files, send a reference instead of the content
            # tmux send-keys has a ~4KB limit
            file_size = os.path.getsize(instructions_file)
            if file_size > 3000:
                prompt = f"Read and execute the instructions in {instructions_file} - this is your task. Follow every section exactly. Start now."
                use_file_ref = True
            else:
                try:
                    with open(instructions_file, "r") as f:
                        prompt = f.read()
                except Exception as e:
                    print(f"  Scheduler: failed to read instructions file: {e}")
        else:
            print(f"  Scheduler: instructions_file outside allowed directories: {instructions_file}")

    if not prompt:
        _release_claim()
        add_history_entry(schedule["id"], "error", "No prompt or instructions file")
        return

    # Create tmux session (use sandbox workaround for root, same as server.py)
    # A scheduled run always needs Claude, never a plain shell.
    mode = resolve_claude_mode(mode)
    claude_flags = RC_FLAGS[mode]
    model_flag = MODEL_MAP.get(model) if model else None
    claude_args = claude_flags.split()
    if model_flag:
        claude_args.extend(["--model", model_flag])
    claude_cmd = " ".join(
        [f"CLAUDECODE= {CLAUDE_BIN}"] + claude_args
    )
    wrapper = f'{claude_cmd} 2>&1 || {{ echo ""; sleep 30; }}'
    cmd = [
        "tmux", "new-session", "-d", "-s", session_name,
        "-c", workdir,
        "-x", "200", "-y", "50",
        "-e", f"RC_MODE={mode}",
        "-e", f"RC_WORKDIR={workdir}",
        "-e", "DISPLAY=:1",
        "-e", "IS_SANDBOX=1",
        "bash", "-c", wrapper,
    ]
    print(f"  Scheduler: firing '{name}' → session {session_name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _release_claim()
        add_history_entry(schedule["id"], "error", f"tmux failed: {result.stderr.strip()}")
        print(f"  Scheduler: tmux failed: {result.stderr.strip()}")
        return

    # Setup session in background (trust prompt, /remote-control, /rename)
    # Then send the task prompt after setup completes
    def _setup_and_send():
        setup_session(session_name, session_name, mode)
        # After setup, send the prompt
        if not session_exists(session_name):
            _release_claim()
            add_history_entry(schedule["id"], "error", "Session died during setup")
            return

        # Pipe tmux output to a log file
        log_dir = os.path.expanduser(f"~/.claude-rc/jobs/{safe_name}/logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{session_name}.log")
        subprocess.run(
            ["tmux", "pipe-pane", "-t", session_name, f"cat >> {log_file}"],
            capture_output=True,
        )

        # Wait a moment for /rename to complete
        time.sleep(2)
        # Send the prompt via tmux send-keys
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "-l", prompt],
            capture_output=True,
        )
        time.sleep(0.5)
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Enter"],
            capture_output=True,
        )

        # The claim made before tmux new-session already registered this
        # session for lifecycle monitoring and concurrency control.
        add_history_entry(schedule["id"], "ok", f"Session {session_name} started")
        print(f"  Scheduler: task '{name}' prompt sent to {session_name}")

    threading.Thread(target=_setup_and_send, daemon=True).start()


# --- Scheduler thread ---

def _scheduler_loop():
    """Main scheduler loop. Checks every 60 seconds at minute boundaries."""
    print("  Scheduler: started")
    while True:
        # Sleep until next minute boundary
        now = datetime.now()
        seconds_to_next = 60 - now.second
        time.sleep(seconds_to_next)

        now = datetime.now().replace(second=0, microsecond=0)

        try:
            stats.sample_tokens(lambda: sum(s.get("tokens", 0) for s in list_rc_sessions()))
        except Exception:
            pass

        schedules = load_schedules()
        msg = _schedule_error_to_log(schedules_module.LAST_LOAD_ERROR)
        if msg:
            print(f"  {msg}")

        for schedule in schedules:
            if not schedule.get("enabled", False):
                continue
            if not _due_to_fire(schedule, now):
                continue
            print(f"  Scheduler: cron match for '{schedule.get('name')}'")
            _fire_schedule(schedule)

        # Check if any tracked scheduled sessions have ended
        _monitor_scheduled_sessions()


def start_scheduler():
    """Start the scheduler daemon thread."""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    return t
