"""Cron parser and scheduler thread."""

import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

from config import SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, MODEL_MAP
from sessions import session_exists, setup_session, get_url
from schedules import load_schedules, save_schedules, add_history_entry


WIZARD_PROMPT = """I want to create a scheduled task for the Claude RC Launcher.

Here's what I have so far:
- **Task:** {description}
- **Schedule:** {schedule_label} ({cron})
- **Working directory:** {workdir}
- **Mode:** {mode}

Help me refine this into a great task prompt. The prompt will be sent to a fresh Claude Code session each time the schedule fires. That session will:
- Start in the working directory above
- Have the prompt as its first message
- Run autonomously (no human interaction)

Things to consider when writing the prompt:
1. Be specific about what to do, not vague
2. Include success criteria — how does Claude know it's done?
3. Mention any files, tools, or resources Claude should use
4. Add error handling — what should Claude do if something goes wrong?
5. Keep it focused — one clear objective per scheduled task

Once we've refined the prompt together, save the schedule by running this curl command (fill in the final prompt):

```bash
curl -s -X POST {api_url}/schedules \\
  {auth_header} \\
  -H 'Content-Type: application/json' \\
  -d '{{"name": "{schedule_name}", "cron": "{cron}", "prompt": "<FINAL PROMPT HERE>", "workdir": "{workdir}", "mode": "{mode_code}", "enabled": true}}'
```

Let's start — what do you think of the task description? Any questions before we refine it?"""


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
    """Validate a cron expression. Returns None if valid, error string if invalid."""
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
    """Calculate next run time for a cron expression. Returns ISO string or None."""
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


# --- Session lifecycle tracking ---

_active_scheduled_sessions = {}  # session_name -> {schedule_id, started_at, schedule_safe_name}


def _monitor_scheduled_sessions():
    """Check if any tracked scheduled sessions have ended."""
    if not _active_scheduled_sessions:
        return

    ended = []
    for session_name, info in _active_scheduled_sessions.items():
        # Check if tmux session still exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True
        )
        if result.returncode != 0:
            # Session ended
            ended.append(session_name)
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
                info["schedule_id"],
                "completed",
                summary,
                duration_minutes=round(duration, 1)
            )

    for name in ended:
        del _active_scheduled_sessions[name]


# --- Fire mechanism ---

def _fire_schedule(schedule):
    """Spawn a new Claude session for a scheduled task."""
    name = schedule.get("name", "task")
    # Generate unique session name (include date to prevent collisions across days)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name.replace(" ", "-"))
    ts = time.strftime("%m%d-%H%M%S")
    session_name = f"{SESSION_PREFIX}sched-{safe_name}-{ts}"

    # Kill stale sessions from previous runs of this schedule
    stale_prefix = f"{SESSION_PREFIX}sched-{safe_name}-"
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for line in result.stdout.strip().splitlines():
            sname = line.strip()
            if sname.startswith(stale_prefix) and sname != session_name:
                subprocess.run(["tmux", "kill-session", "-t", sname], capture_output=True)
                print(f"  Scheduler: killed stale session {sname}")

    workdir = schedule.get("workdir", "/tmp")
    mode = schedule.get("mode", "c")
    model = schedule.get("model")

    if mode not in RC_FLAGS:
        mode = "c"

    if not os.path.isdir(workdir):
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
        add_history_entry(schedule["id"], "error", "No prompt or instructions file")
        return

    # Create tmux session (use sandbox workaround for root, same as server.py)
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
        add_history_entry(schedule["id"], "error", f"tmux failed: {result.stderr.strip()}")
        print(f"  Scheduler: tmux failed: {result.stderr.strip()}")
        return

    # Setup session in background (trust prompt, /remote-control, /rename)
    # Then send the task prompt after setup completes
    def _setup_and_send():
        setup_session(session_name, session_name, mode)
        # After setup, send the prompt
        if not session_exists(session_name):
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

        # Register session for lifecycle monitoring
        _active_scheduled_sessions[session_name] = {
            "schedule_id": schedule["id"],
            "started_at": datetime.now(),
            "schedule_safe_name": safe_name,
        }

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
        schedules = load_schedules()

        for schedule in schedules:
            if not schedule.get("enabled", False):
                continue

            cron_expr = schedule.get("cron", "")
            try:
                if not cron_matches(cron_expr, now):
                    continue
            except ValueError:
                continue

            # Check last_run to prevent double-fire
            last_run = schedule.get("last_run")
            if last_run:
                try:
                    last_dt = datetime.fromisoformat(last_run)
                    if (last_dt.year == now.year and last_dt.month == now.month and
                            last_dt.day == now.day and last_dt.hour == now.hour and
                            last_dt.minute == now.minute):
                        continue
                except (ValueError, TypeError):
                    pass

            print(f"  Scheduler: cron match for '{schedule.get('name')}'")
            _fire_schedule(schedule)

        # Check if any tracked scheduled sessions have ended
        _monitor_scheduled_sessions()


def start_scheduler():
    """Start the scheduler daemon thread."""
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    return t
