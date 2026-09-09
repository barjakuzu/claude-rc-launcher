"""Cron parser and scheduler thread."""

import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta

import stats
from config import (SESSION_PREFIX, RC_FLAGS,
                    resolve_claude_mode, RC_MAX_SESSIONS)
from sessions import (session_exists, setup_session, get_url, list_rc_sessions,
                      get_session_env, build_tmux_command, count_launcher_sessions)
from schedules import (load_schedules, save_schedules, add_history_entry, update_schedule,
                       get_schedule_by_id)
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
            if len(fields) == 0:
                # Empty/whitespace-only: the UI sends "" when a preset was
                # never picked and no manual cron was typed. The generic
                # "got 0" field-count message is confusing there.
                return "Pick a schedule preset, enter a 5-field cron, or choose Manual"
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


# --- limit_reset trigger (task-l3) ------------------------------------------
#
# Fires a task once when a Claude usage window (five_hour / seven_day)
# rolls over, instead of on a cron schedule. The reset is OBSERVED, not
# predicted: limits.py polls the account's usage windows and
# store.limits_view() exposes the freshest reading as `primary` - this
# module never calls the API itself. A reset is inferred purely from
# `resets_at` moving forward between two observations of the same window.
#
# Firing rule (task-l3 brief): fire once per reset, never twice, never a
# burst of catch-up runs. Store the last `resets_at` this task fired for
# (or, for a brand new task, merely observed). Fire when the current
# `resets_at` differs from the stored one AND the stored one is already in
# the past. Then record the new value - before firing, not after, so a
# crash between "record" and "fire" costs at most one missed fire rather
# than risking a duplicate on the next tick (worse per the brief: a task
# firing twice is worse than not having the feature at all).
#
# Because the marker lives in schedules.json (survives a hub restart) and
# the comparison only ever looks at "current vs. last stored", a hub that
# was down across one or several resets naturally fires exactly once, for
# the latest reset, the next time it gets to check - no special "was the
# hub down" detection needed for that part. `catch_up: "none"` opts OUT of
# that specific behavior (see _apply_limit_reset_trigger below).
#
# Fix round 1 (Opus review, task-l3-findings-r1.md) changed three things:
# Critical 1, the catch_up: "none" allowance used to be consumed by ANY
# tick, including a no-op one, so it was gone by the time the real fire
# needed it - now consumed only at the moment a genuine fire decision is
# reached (_apply_limit_reset_trigger). Critical 2, a disabled schedule
# used to be skipped entirely, so its marker rotted and re-enabling it
# looked exactly like a missed reset - every trigger schedule is now
# checked on every tick regardless of `enabled`, with a would-be fire
# downgraded to a silent marker refresh while disabled
# (_check_limit_reset_schedules). Critical 3, concurrent ticks (or, in a
# stress probe, concurrent calls) could each read the same stale marker
# before any of them wrote their update and each decide to fire - the
# decide-then-persist step is now atomic, reusing _fire_schedule's own
# claim lock rather than a new one.

# In-memory only, deliberately never persisted: which schedule ids have
# already had at least one limit_reset check performed THIS PROCESS
# lifetime. Reset to empty on every restart. Used solely so catch_up:
# "none" can tell "a reset this process already watched happen live" apart
# from "a reset that could have happened at any point while this process
# was not running" - see _apply_limit_reset_trigger.
_limit_reset_seen_this_process = set()


def _limit_reset_decision(trigger, current_resets_at, now_epoch):
    """Pure firing-rule decision for one limit_reset trigger check, given
    the window's current resets_at (or None/empty when limits data isn't
    available right now) and the current wall clock (epoch seconds).

    Returns (action, new_marker):
      ("skip", None) - do nothing; any existing marker is left untouched.
      ("seed", ts)   - persist ts as last_seen_resets_at; do not fire.
                       Covers both "brand new task" (brief: never fire
                       just because the marker is empty - seed and fire
                       on the NEXT reset) and "reset observed but still
                       inside its delay_minutes window" (marker is
                       deliberately NOT advanced there either - see
                       below - so this same case is folded into "skip").
      ("fire", ts)   - persist ts as last_seen_resets_at, then fire.

    One test per brief case lives in tests/test_scheduler.py under
    LimitResetDecisionTest."""
    if not current_resets_at:
        # limits unavailable (no credentials, network down, or simply no
        # device has ever reported a reading) - brief: fire nothing, and
        # do not lose the stored marker.
        return ("skip", None)

    stored = trigger.get("last_seen_resets_at")
    if not stored:
        # Brand new task (or a marker some prior version never set).
        return ("seed", current_resets_at)

    if current_resets_at == stored:
        # The normal case: the API is still reporting the same window
        # boundary it reported last poll. Nothing happened.
        return ("skip", None)

    stored_epoch = schedules_module.iso_to_epoch(stored)
    current_epoch = schedules_module.iso_to_epoch(current_resets_at)
    if stored_epoch is None or current_epoch is None:
        # Unparseable timestamp somewhere - stay conservative rather than
        # act on a value this function can't actually reason about.
        return ("skip", None)

    if current_epoch <= stored_epoch:
        # Clock skew, or resets_at moving backwards - brief: ignore it,
        # do not fire. The marker is left alone (not advanced to this
        # backwards value) so a later, forward-moving reading is still
        # compared against the last value actually trusted.
        return ("skip", None)

    if stored_epoch >= now_epoch:
        # The previously-stored boundary has not actually passed yet by
        # wall clock even though the API already reports a new value for
        # it - brief requires "the stored one is in the past" before
        # firing. Wait rather than fire early.
        return ("skip", None)

    delay_seconds = (trigger.get("delay_minutes") or 0) * 60
    fire_at = stored_epoch + delay_seconds
    if now_epoch < fire_at:
        # Reset observed, but delay_minutes (brief: "fires a little after
        # the reset, so a user can avoid every device stampeding the
        # same instant") hasn't elapsed yet. The marker is deliberately
        # NOT advanced here: if it were, the next tick would see
        # current_resets_at == stored and silently skip the fire this
        # delay window still owes. Re-evaluated every tick until the
        # delay elapses, then falls through to "fire" below.
        return ("skip", None)

    return ("fire", current_resets_at)


def _apply_limit_reset_trigger(schedule_id, trigger, current_resets_at, now_epoch):
    """_limit_reset_decision, adjusted for trigger.catch_up.

    catch_up: "latest" (default) is the behavior described above: a hub
    that was down across one or more resets fires once, for the latest,
    the first time it checks after restart. catch_up: "none" opts a task
    out of exactly that: on THIS process's first-ever genuine fire
    decision for this schedule, it is downgraded to a silent reseed
    instead (brief: "not at all if the task's catch_up is none"). Every
    later fire within the same process lifetime behaves normally - only
    the first, potentially-stale-spanning-downtime one is softened.

    Fix round 1 (Critical 1): "first-ever" is judged ONLY against ticks
    that actually reached a "fire" decision - a tick that resolved to
    skip/seed for any other reason (limits unavailable, a stale-cached
    reading that happened to equal the marker right after a restart,
    still waiting on delay_minutes, ...) no longer consumes the
    allowance. The previous version marked the schedule "seen" on every
    tick regardless of outcome, so a single no-op tick right after
    restart (e.g. the hub's SQLite-cached limits row not having rolled
    over yet) silently spent the one-time suppression before the real
    missed-reset fire was even computed."""
    action, new_marker = _limit_reset_decision(trigger, current_resets_at, now_epoch)
    if action != "fire":
        return (action, new_marker)
    catch_up = trigger.get("catch_up", "latest")
    if catch_up == "none":
        already_fired_this_process = schedule_id in _limit_reset_seen_this_process
        _limit_reset_seen_this_process.add(schedule_id)
        if not already_fired_this_process:
            return ("seed", new_marker)
    return ("fire", new_marker)


def _get_limits_view():
    """store.limits_view() via the hub's shared Store (server.HUB_STORE),
    for the limit_reset trigger check. Imported lazily, at call time, not
    at this module's top level: server.py does `from scheduler import
    validate_cron, next_cron_run, _fire_schedule` at ITS OWN module load
    time, so a top-level `import server` here would try to import
    server.py back while it is still mid-load, before those names exist
    on this module yet, and crash. Never raises: a store hiccup here is
    treated exactly like limits.py's own available=False - the caller
    (_check_limit_reset_schedules) already treats None the same as "no
    data this tick"."""
    try:
        import server as server_module
        hub_store = getattr(server_module, "HUB_STORE", None)
        if hub_store is None:
            return None
        return hub_store.limits_view()
    except Exception:
        return None


def _check_limit_reset_schedules(schedules_list, now_epoch, get_limits_view=_get_limits_view):
    """Runs once per scheduler tick (see _scheduler_loop). For every
    schedule carrying a `trigger` (enabled or not - see Critical 2
    below), reads the hub's current limits view ONCE for the whole tick
    (not once per schedule), decides whether to fire via
    _apply_limit_reset_trigger, and persists the outcome. Never raises:
    the whole per-schedule body is guarded, and a malformed trigger
    (unknown kind/window, bad delay - whether from a hand-edited
    schedules.json or an unexpected runtime failure) disables that one
    task with a history entry instead of ever propagating out of this
    function and killing the scheduler thread (brief: "The scheduler
    loop must never raise. A malformed trigger must disable that one
    task, loudly in its history, not stop the loop").

    Fix round 1 (Critical 2): schedules used to be filtered to
    `enabled` ones before this loop even started, so a disabled
    schedule's marker never moved while resets kept happening in the
    background - re-enabling it later looked exactly like a missed
    reset and fired within one tick. A task must never fire because of
    a purely administrative action. Every trigger-carrying schedule is
    now evaluated on every tick regardless of `enabled`, so its marker
    stays current the whole time it's disabled; a decision that would
    fire is downgraded to a silent marker refresh instead of an actual
    fire whenever the schedule is disabled at decision time, so
    re-enabling it later sees an already-current marker and does
    nothing until the NEXT genuine reset.

    Fix round 1 (Critical 3): the read-decide-persist step for one
    schedule is now atomic under _active_scheduled_sessions_lock - the
    same claim lock _fire_schedule already uses for run-level dedup,
    reused here rather than inventing a second one. Each schedule is
    also re-fetched fresh from disk INSIDE that lock rather than reused
    from the `schedules_list` snapshot this function was called with:
    under concurrent ticks (or, in a stress probe, concurrent direct
    calls), several evaluators could otherwise all read the same
    pre-write marker and each independently decide to fire before any
    of them had persisted their update. Re-reading under the lock means
    only the first evaluator ever sees the pre-write value; everyone
    else sees its result and skips. The lock is released before
    _fire_schedule is ever called - that function claims the same lock
    itself for its own session-level dedup, and holding it across the
    call would deadlock."""
    limit_schedules = [s for s in schedules_list if s.get("trigger") is not None]
    if not limit_schedules:
        return

    try:
        view = get_limits_view()
    except Exception:
        view = None
    primary = view.get("primary") if isinstance(view, dict) else None

    for candidate in limit_schedules:
        schedule_id = candidate.get("id")
        fire_name = candidate.get("name")
        fire_window = None
        is_enabled = None
        to_fire = None
        try:
            with _active_scheduled_sessions_lock:
                _, schedule = get_schedule_by_id(schedule_id)
                if schedule is None:
                    continue  # deleted since this tick's snapshot was loaded
                fire_name = schedule.get("name")
                is_enabled = schedule.get("enabled", False)

                raw_trigger = schedule.get("trigger")
                if raw_trigger is None:
                    # Cleared concurrently since this tick's candidate
                    # list was built (an edit removed the trigger, or
                    # converted the task back to cron/manual) - nothing
                    # to do, and NOT an error: only a genuinely
                    # non-dict, non-None value below is malformed.
                    continue
                if not isinstance(raw_trigger, dict):
                    raise ValueError(f"trigger must be an object, got {type(raw_trigger).__name__}")
                if raw_trigger.get("kind") != "limit_reset":
                    raise ValueError(f"unsupported trigger kind: {raw_trigger.get('kind')!r}")
                window = raw_trigger.get("window")
                if window not in schedules_module.TRIGGER_WINDOWS:
                    raise ValueError(f"unknown trigger window: {window!r}")
                delay = raw_trigger.get("delay_minutes", 0)
                if delay is None:
                    delay = 0
                if (isinstance(delay, bool) or not isinstance(delay, (int, float))
                        or not (0 <= delay <= schedules_module.TRIGGER_MAX_DELAY_MINUTES)):
                    raise ValueError(f"invalid delay_minutes: {delay!r}")
                catch_up = raw_trigger.get("catch_up", "latest")
                if catch_up not in schedules_module.TRIGGER_CATCH_UP_MODES:
                    raise ValueError(f"invalid catch_up: {catch_up!r}")

                current_resets_at = None
                if isinstance(primary, dict):
                    bucket = primary.get(window)
                    if isinstance(bucket, dict):
                        ra = bucket.get("resets_at")
                        if isinstance(ra, str) and ra:
                            current_resets_at = ra

                action, new_marker = _apply_limit_reset_trigger(
                    schedule_id, raw_trigger, current_resets_at, now_epoch)

                if action == "fire" and not is_enabled:
                    # Critical 2: never actually fire a disabled task -
                    # only keep its marker current so re-enabling it
                    # later doesn't see a stale marker and fire.
                    action = "seed"

                if action == "skip":
                    continue

                new_trigger = dict(raw_trigger)
                new_trigger["last_seen_resets_at"] = new_marker
                update_schedule(schedule_id, {"trigger": new_trigger})

                if action == "fire":
                    to_fire = schedule
                    fire_window = window
        except Exception as e:
            print(f"  Scheduler: limit_reset trigger check failed for "
                  f"'{fire_name}': {type(e).__name__}: {e}")
            # Only log + disable on the transition INTO broken - an
            # already-disabled schedule (is_enabled is False, never
            # True) would otherwise get re-logged and re-written on
            # every single tick forever for the same standing problem.
            if is_enabled:
                try:
                    add_history_entry(schedule_id, "error",
                                       f"Disabled: limit_reset trigger check failed ({e})")
                    update_schedule(schedule_id, {"enabled": False})
                except Exception:
                    # A second failure here (disk full, etc.) must still
                    # not escape this loop - see the module docstring above.
                    pass
            continue

        if to_fire is not None:
            print(f"  Scheduler: limit reset for '{fire_name}' ({fire_window})")
            _fire_schedule(to_fire)


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
    # The session-cap check lives in this same critical section so the count
    # and the claim are atomic too (the server's own /start, /resume/start
    # cap check is advisory only - it can't hold this lock across a request).
    with _active_scheduled_sessions_lock:
        if RC_MAX_SESSIONS > 0 and count_launcher_sessions(list_rc_sessions()) >= RC_MAX_SESSIONS:
            add_history_entry(schedule_id, "skipped", f"Session cap reached ({RC_MAX_SESSIONS})")
            print(f"  Scheduler: skipped '{name}', session cap reached ({RC_MAX_SESSIONS})")
            return

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
    # A scheduled run always needs Claude, never a plain shell. Routed
    # through build_tmux_command (shared with /start, restart_session,
    # resume_session) so a scheduled run gets the same UUID pre-assignment
    # and native --session-id/--name/--remote-control when the installed
    # claude supports it; sandbox=True unconditionally to preserve this
    # function's previous always-on IS_SANDBOX=1 behavior.
    mode = resolve_claude_mode(mode)
    session_id = str(uuid.uuid4())
    cmd = build_tmux_command(
        session_name, workdir, mode, model=model, sandbox=True,
        session_id=session_id, title=safe_name,
        extra_env=["-e", f"RC_SCHEDULE_ID={schedule_id}"],
    )
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
        # safe_name matches the title build_tmux_command used for
        # --name/--remote-control above, so a native launch's RC_TITLE and
        # the /rename fallback both land on the same display name.
        setup_session(session_name, safe_name, mode)
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

        # limit_reset trigger check - see _check_limit_reset_schedules for
        # the firing rule. Belt-and-suspenders try/except on top of that
        # function's own internal per-schedule guard: this loop must never
        # die, no matter what.
        try:
            _check_limit_reset_schedules(schedules, time.time())
        except Exception:
            print("  Scheduler: limit_reset trigger check failed unexpectedly")

        # Check if any tracked scheduled sessions have ended
        _monitor_scheduled_sessions()


def _adopt_live_sessions(schedules=None):
    """Adopt tmux sessions from a previous scheduler run that are still
    alive after a launcher restart, so `_active_scheduled_sessions` (which
    only lives in memory) reflects reality again: concurrency=skip still
    skips, concurrency=kill has something to kill, and the lifecycle
    monitor can report on them when they finish.

    Recognizes two session-name shapes:
    - rc-run-<hex>: v3 scheduled runs. The schedule id is read back from
      the RC_SCHEDULE_ID env var set on the session at launch time.
    - rc-sched-<safe_name>-...: pre-v3 legacy sessions, matched by the
      sanitized schedule name (same sanitization _fire_schedule uses).

    Returns the number of sessions adopted.
    """
    if schedules is None:
        schedules = load_schedules()

    by_id = {s["id"]: s for s in schedules if s.get("id")}
    by_safe_name = {}
    for s in schedules:
        safe = re.sub(r'[^a-zA-Z0-9_-]', '', s.get("name", "task").replace(" ", "-"))
        by_safe_name.setdefault(safe, s)

    adopted = 0
    for sess in list_rc_sessions():
        session_name = sess["name"]
        if not session_name.startswith(SESSION_PREFIX):
            continue
        rest = session_name[len(SESSION_PREFIX):]

        schedule = None
        safe_name = None
        if rest.startswith("run-"):
            schedule_id = get_session_env(session_name, "RC_SCHEDULE_ID")
            if not schedule_id:
                continue
            schedule = by_id.get(schedule_id)
            if schedule:
                safe_name = re.sub(r'[^a-zA-Z0-9_-]', '',
                                    schedule.get("name", "task").replace(" ", "-"))
        elif rest.startswith("sched-"):
            remainder = rest[len("sched-"):]
            for safe, candidate in sorted(by_safe_name.items(),
                                           key=lambda kv: len(kv[0]), reverse=True):
                if remainder == safe or remainder.startswith(safe + "-"):
                    schedule = candidate
                    safe_name = safe
                    break

        if not schedule:
            continue

        schedule_id = schedule["id"]
        with _active_scheduled_sessions_lock:
            if schedule_id in _active_scheduled_sessions:
                continue
            _active_scheduled_sessions[schedule_id] = {
                "session_name": session_name,
                "started_at": datetime.now(),
                "schedule_safe_name": safe_name,
                "adopted": True,
            }
        adopted += 1

    if adopted:
        print(f"  Scheduler: adopted {adopted} live session(s) after restart")
    return adopted


def start_scheduler():
    """Start the scheduler daemon thread."""
    _adopt_live_sessions()
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    return t
