"""Schedule storage — CRUD for scheduled tasks."""

import json
import os
import shutil
import tempfile
import threading
import uuid

from config import SCHEDULES_FILE

_schedules_lock = threading.Lock()

LAST_LOAD_ERROR = None

# --- limit_reset trigger (task-l3) -----------------------------------------
#
# A task's `trigger` field is a pure addition alongside `cron`: a task with
# no `trigger` key (every task that existed before this feature) behaves
# exactly as before. When present, it currently supports exactly one kind:
#
#   {"kind": "limit_reset", "window": "five_hour" | "seven_day",
#    "delay_minutes": 0, "catch_up": "latest", "last_seen_resets_at": null}
#
# `delay_minutes` and `catch_up` are optional on input (defaulted below);
# `last_seen_resets_at` is scheduler-owned bookkeeping for the firing rule
# (see scheduler.py's _check_limit_reset_schedules) and is never something
# a client is expected to set directly.
TRIGGER_WINDOWS = ("five_hour", "seven_day")
TRIGGER_CATCH_UP_MODES = ("latest", "none")
TRIGGER_MAX_DELAY_MINUTES = 240
# Fix round 1 (Important 4, task-l3-findings-r1.md): the only fields a
# client is ever allowed to send in a `trigger`. `last_seen_resets_at`
# is deliberately absent from this set - it is server state (the
# firing rule's own bookkeeping, written only by scheduler.py's
# internal update_schedule() calls, never by an HTTP request) and must
# never be settable by a caller. Before this fix, validate_trigger
# didn't reject unknown keys at all, so a client could include
# last_seen_resets_at in a POST /schedules/update body and
# _normalize_trigger_for_update would trust it verbatim, forcing an
# immediate fire on the next tick - this hub sits on the internet
# behind a password, so the API is the trust boundary, not the modal.
TRIGGER_ALLOWED_FIELDS = frozenset({"kind", "window", "delay_minutes", "catch_up"})


def iso_to_epoch(ts):
    """ISO-8601 string (accepts a trailing "Z", which Python 3.9's
    datetime.fromisoformat does not) -> epoch seconds, or None for
    anything that isn't a non-empty string or doesn't parse. A timestamp
    with no offset/Z at all is treated as UTC, matching usage.py's own
    _parse_timestamp helper, which reads the same shape of timestamp from
    a different source (session transcripts there, limits.py's resets_at
    here). Shared by scheduler.py (the limit_reset firing rule) and
    server.py (the next-fire-time shown in the task list) so both read a
    resets_at string exactly the same way."""
    if not isinstance(ts, str) or not ts:
        return None
    from datetime import datetime, timezone
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def validate_trigger(trigger):
    """Validate a task's `trigger` field for create_schedule/
    update_schedule (the API layer, server.py's /schedules and
    /schedules/update routes). Returns None when valid - including
    trigger being None/absent, which is always valid (a task with no
    trigger key behaves precisely as before). Deliberately strict
    otherwise: an unknown kind or window is rejected rather than
    silently ignored (task-l3 brief). This is the one gate a client
    request passes through before something lands in schedules.json;
    scheduler.py's own defensive re-check exists for a trigger that
    reached the file some other way (a hand-edit, an older/newer build)
    and disables that one task rather than trusting it blindly."""
    if trigger is None:
        return None
    if not isinstance(trigger, dict):
        return "trigger must be an object or null"
    unknown = set(trigger.keys()) - TRIGGER_ALLOWED_FIELDS
    if unknown:
        return f"unknown trigger field(s): {', '.join(sorted(unknown))}"
    if trigger.get("kind") != "limit_reset":
        return f"unsupported trigger kind: {trigger.get('kind')!r}"
    if trigger.get("window") not in TRIGGER_WINDOWS:
        return f"trigger.window must be one of {TRIGGER_WINDOWS}"
    delay = trigger.get("delay_minutes", 0)
    if delay is None:
        delay = 0
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        return "trigger.delay_minutes must be a number"
    if not (0 <= delay <= TRIGGER_MAX_DELAY_MINUTES):
        return f"trigger.delay_minutes must be between 0 and {TRIGGER_MAX_DELAY_MINUTES}"
    catch_up = trigger.get("catch_up", "latest")
    if catch_up not in TRIGGER_CATCH_UP_MODES:
        return f"trigger.catch_up must be one of {TRIGGER_CATCH_UP_MODES}"
    return None


def _normalize_trigger_for_create(trigger):
    """Validated trigger -> stored shape for a brand-new schedule.
    last_seen_resets_at always starts None regardless of what the caller
    sent: task-l3 brief, a brand new task must not fire immediately just
    because its marker is empty - it seeds on first observation and
    fires on the NEXT reset. Caller must already have run
    validate_trigger()."""
    if trigger is None:
        return None
    return {
        "kind": trigger.get("kind"),
        "window": trigger.get("window"),
        "delay_minutes": trigger.get("delay_minutes", 0) or 0,
        "catch_up": trigger.get("catch_up", "latest"),
        "last_seen_resets_at": None,
    }


def _normalize_trigger_for_update(trigger, previous):
    """Validated trigger -> stored shape when updating an existing
    schedule. Two distinct callers share this path:

    - The API layer (server.py's /schedules/update): its payload never
      includes last_seen_resets_at, since the client doesn't know about
      or manage it, so the existing marker survives an edit to
      delay_minutes/catch_up. It is reset to None only when kind/window
      actually changed - a marker recorded for a different window means
      nothing for the new one, and re-seeding avoids firing on a stale
      value that was never actually observed for this window.
    - scheduler.py's own firing-rule bookkeeping, which explicitly sets
      last_seen_resets_at as the entire point of the call - trusted
      verbatim whenever the key is present.

    Caller must already have run validate_trigger()."""
    if trigger is None:
        return None
    if "last_seen_resets_at" in trigger:
        marker = trigger.get("last_seen_resets_at")
    elif (isinstance(previous, dict)
          and previous.get("kind") == trigger.get("kind")
          and previous.get("window") == trigger.get("window")):
        marker = previous.get("last_seen_resets_at")
    else:
        marker = None
    return {
        "kind": trigger.get("kind"),
        "window": trigger.get("window"),
        "delay_minutes": trigger.get("delay_minutes", 0) or 0,
        "catch_up": trigger.get("catch_up", "latest"),
        "last_seen_resets_at": marker,
    }


def _validate_schedules(data):
    """Validate the raw JSON loaded from SCHEDULES_FILE.

    Returns (schedules, error). `schedules` is the list of entries that
    passed validation - invalid entries are dropped rather than discarding
    the whole file. `error` is None when every entry validated cleanly,
    otherwise a human-readable summary of what was dropped and why.
    """
    if not isinstance(data, list):
        return [], f"expected a JSON array, got {type(data).__name__}"
    valid = []
    problems = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            problems.append(f"entry {i}: not an object")
            continue
        sid = entry.get("id")
        if not sid or not isinstance(sid, str):
            problems.append(f"entry {i}: missing or invalid 'id'")
            continue
        if "name" in entry and not isinstance(entry["name"], str):
            problems.append(f"entry {i} ({sid}): 'name' must be a string")
            continue
        cron = entry.get("cron")
        if cron is not None and not isinstance(cron, str):
            problems.append(f"entry {i} ({sid}): 'cron' must be a string or null")
            continue
        # Structural check only (dict or null) - same shallow treatment as
        # cron's type check above. Deep validation (unknown kind/window/
        # out-of-range delay) is deliberately NOT done at load time: a
        # task that fails that check should still load and stay visible
        # in the UI so its history can say why it is disabled, rather
        # than vanishing the way a dropped entry does here. That deeper
        # check lives in scheduler.py's per-tick defensive re-validation.
        #
        # Fix round 1 (Minor, task-l3-findings-r1.md): a non-dict
        # `trigger` used to drop the WHOLE entry here (`continue`, never
        # reaching `valid`), the same as a missing id would - but unlike
        # a missing id, the rest of the entry (name/cron/prompt/...) is
        # perfectly usable. Dropping the entry meant it survived only
        # until the next unrelated write (any other schedule's
        # create/update/delete): save_schedules() persists exactly the
        # `valid` list, so that next write would silently and
        # permanently erase this schedule from disk. Given this file's
        # own history (silently corrupt for three months, nobody
        # noticed), losing a whole task to a single bad field is exactly
        # the kind of quiet data loss to avoid - so only the bad field is
        # dropped here; the entry survives, downgraded to whatever
        # `cron` says (null cron -> manual) until a human fixes it, and
        # the problem is still surfaced via LAST_LOAD_ERROR.
        if "trigger" in entry and entry["trigger"] is not None and not isinstance(entry["trigger"], dict):
            problems.append(f"entry {i} ({sid}): 'trigger' must be an object or null, field dropped")
            entry = dict(entry)
            entry["trigger"] = None
        valid.append(entry)
    error = "; ".join(problems) if problems else None
    return valid, error


def _load_schedules_locked():
    """The actual load logic. Caller MUST already hold _schedules_lock -
    this is the shared body behind the public load_schedules() (a
    standalone read, which acquires the lock itself) and every CRUD
    function below, whose entire load-modify-save must happen under ONE
    lock acquisition rather than three separate ones (see fix round 3,
    task-l3-findings-r3.md, in save_schedules()'s docstring)."""
    global LAST_LOAD_ERROR
    if not os.path.isfile(SCHEDULES_FILE):
        LAST_LOAD_ERROR = None
        return []
    try:
        with open(SCHEDULES_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        LAST_LOAD_ERROR = f"failed to parse {SCHEDULES_FILE}: {e}"
        print(f"  Warning: failed to load schedules: {e}")
        return []
    valid, error = _validate_schedules(data)
    LAST_LOAD_ERROR = error
    if error:
        print(f"  Warning: schedules.json has invalid entries: {error}")
    return valid


def load_schedules():
    """Load schedules from JSON file. Returns list of schedule dicts.

    Sets the module-level LAST_LOAD_ERROR to a description of what went
    wrong (JSON parse failure, or per-entry validation problems) so callers
    (GET /schedules, the scheduler loop) can surface it instead of it
    looking indistinguishable from "no schedules configured". Cleared to
    None on a fully clean load.
    """
    with _schedules_lock:
        return _load_schedules_locked()


def _save_schedules_locked(schedules):
    """The actual atomic-write logic (temp file + os.replace, rolling
    .bak). Caller MUST already hold _schedules_lock - see
    _load_schedules_locked's docstring.

    Fix round 4 (task-l3-findings-r4.md): the temp file is fsync'd
    before the rename. os.replace() is atomic against a PARTIAL rename
    (a reader never sees a half-written file), but says nothing about
    the file's actual bytes reaching disk - without an explicit fsync,
    the freshly-written content can still be sitting in the page cache,
    unwritten to physical storage, at the moment the rename itself
    commits. A power loss in that window can leave the renamed file
    referencing data the OS never actually flushed. Given this exact
    file was silently corrupt for three months in this project,
    durability here is worth the extra syscall."""
    directory = os.path.dirname(SCHEDULES_FILE)
    os.makedirs(directory, exist_ok=True)
    if os.path.isfile(SCHEDULES_FILE):
        try:
            shutil.copyfile(SCHEDULES_FILE, SCHEDULES_FILE + ".bak")
            try:
                os.chmod(SCHEDULES_FILE + ".bak", 0o600)
            except OSError:
                pass
        except OSError as e:
            print(f"  Warning: could not update schedules.json.bak: {e}")
    fd, tmp_path = tempfile.mkstemp(
        prefix=".schedules-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(schedules, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, SCHEDULES_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_schedules(schedules):
    """Write schedules list to JSON file atomically (temp file in the same
    directory + os.replace), mode 0600. Keeps one rolling backup of the
    previous contents at SCHEDULES_FILE + '.bak' before overwriting.

    Fix round 3 (task-l3-findings-r3.md): every CRUD function below used
    to call the public load_schedules() and this function separately -
    each acquiring and releasing _schedules_lock on its own - with a
    window in between, while the caller mutated its own in-memory copy,
    where the lock was free. A second caller's ENTIRE load-modify-save
    could complete inside that window; whichever caller's save landed
    LAST then won outright and silently discarded the other's change
    (a lost update), since each save persists a full snapshot built from
    its own, by-then-stale, load. For the limit_reset trigger marker
    specifically this meant an unrelated HTTP edit (rename, workdir
    change, anything) whose load happened to precede a firing tick's
    save could revert the marker the tick had just written - and the
    NEXT tick would then see a stale marker again and fire a SECOND real
    session for the same reset. The identical shape affects the cron
    path's last_run dedup (_due_to_fire), meaning it can also produce a
    duplicate cron-triggered run. Every CRUD function now holds
    _schedules_lock ONCE across its whole load-modify-save via
    _load_schedules_locked/_save_schedules_locked, closing the window
    entirely rather than narrowing it."""
    with _schedules_lock:
        _save_schedules_locked(schedules)


def get_schedule_by_id(schedule_id):
    """Find a schedule by its id. Returns (index, schedule) or (None, None)."""
    schedules = load_schedules()
    for i, s in enumerate(schedules):
        if s.get("id") == schedule_id:
            return i, s
    return None, None


def create_schedule(data):
    """Create a new schedule. Returns the created schedule dict.

    `trigger` (task-l3) is expected to have already passed
    validate_trigger() at the caller (server.py's POST /schedules route) -
    this only normalizes it into storage shape. A trigger, when present,
    always wins over `cron` for firing purposes (scheduler.py's cron path
    only ever looks at `cron`, and a limit_reset schedule's `cron` is
    forced to null here) so the two mechanisms can never both drive the
    same task.

    Fix round 3: load-modify-save happens under ONE _schedules_lock
    acquisition (via _load_schedules_locked/_save_schedules_locked), not
    two separate ones - see save_schedules()'s docstring for why."""
    from datetime import datetime
    trigger = _normalize_trigger_for_create(data.get("trigger"))
    schedule = {
        "id": uuid.uuid4().hex[:12],
        "name": data.get("name", "Untitled"),
        "cron": None if trigger is not None else data.get("cron", ""),
        "prompt": data.get("prompt", ""),
        "instructions_file": data.get("instructions_file"),
        "workdir": data.get("workdir", "/tmp"),
        "mode": data.get("mode", "c"),
        "model": data.get("model"),
        "concurrency": data.get("concurrency", "skip"),
        "enabled": data.get("enabled", True),
        "trigger": trigger,
        "last_run": None,
        "created_at": datetime.now().isoformat() + 'Z',
        "history": [],
    }
    with _schedules_lock:
        schedules = _load_schedules_locked()
        schedules.append(schedule)
        _save_schedules_locked(schedules)
    return schedule


def update_schedule(schedule_id, updates):
    """Update a schedule by id. Returns updated schedule or None.

    `trigger` (task-l3), like `cron`, is applied verbatim from `updates`
    via _normalize_trigger_for_update - see that function for how the
    stored last_seen_resets_at marker is kept or reset. Whenever the
    schedule ends up with a non-null trigger (whether this call set one
    or it already had one), `cron` is forced to null so cron and trigger
    can never both be live for the same task, even if a caller's payload
    included a stale non-null cron alongside a new trigger.

    Fix round 2 (Critical 2, task-l3-findings-r2.md): every
    disabled -> enabled transition for a limit_reset task clears
    last_seen_resets_at back to None, REGARDLESS of what else this call
    changes. The marker is only ever kept current by scheduler ticks
    while a task is enabled; a disabled task's marker can go stale in
    ways no tick ever gets a chance to correct before this call runs -
    a hub restart that spanned a reset, re-enabled in the window before
    the first tick, or limits staying unavailable the whole time the
    task was disabled, re-enabled before they recover. Either way,
    enabling the task itself must never be what causes it to fire.
    Reusing the existing "brand new task" guarantee (a None marker
    always seeds silently on the next observation and only fires on the
    reset AFTER that - see scheduler.py's _limit_reset_decision) closes
    both paths at the transition, without needing a live limits fetch
    synchronized with this call.

    Fix round 3 (task-l3-findings-r3.md): load-modify-save happens under
    ONE _schedules_lock acquisition, not two separate ones (see
    save_schedules()'s docstring). Before this fix, a concurrent call
    (another HTTP edit, or the scheduler's own tick) could load its own
    snapshot in the window this function's load and save used to leave
    open, and whichever call's save landed last silently discarded the
    other's change - for the trigger marker specifically, that meant a
    firing tick's marker advance could be reverted by an unrelated edit,
    and the next tick would then fire a second real session for the same
    reset."""
    allowed = {"name", "cron", "prompt", "instructions_file", "workdir",
               "mode", "model", "concurrency", "enabled", "last_run", "history",
               "trigger"}
    with _schedules_lock:
        schedules = _load_schedules_locked()
        for i, s in enumerate(schedules):
            if s.get("id") == schedule_id:
                was_enabled = s.get("enabled", False)
                for k, v in updates.items():
                    if k not in allowed:
                        continue
                    if k == "trigger":
                        s["trigger"] = _normalize_trigger_for_update(v, s.get("trigger"))
                    else:
                        s[k] = v
                if isinstance(s.get("trigger"), dict):
                    s["cron"] = None
                trigger = s.get("trigger")
                if (not was_enabled and s.get("enabled", False)
                        and isinstance(trigger, dict) and trigger.get("kind") == "limit_reset"):
                    trigger = dict(trigger)
                    trigger["last_seen_resets_at"] = None
                    s["trigger"] = trigger
                _save_schedules_locked(schedules)
                return s
    return None


def delete_schedule(schedule_id):
    """Delete a schedule by id. Returns True if found and deleted.

    Fix round 3: load-modify-save happens under ONE _schedules_lock
    acquisition - see save_schedules()'s docstring."""
    with _schedules_lock:
        schedules = _load_schedules_locked()
        new_schedules = [s for s in schedules if s.get("id") != schedule_id]
        if len(new_schedules) < len(schedules):
            _save_schedules_locked(new_schedules)
            return True
        return False


def add_history_entry(schedule_id, status, message, **kwargs):
    """Add a history entry to a schedule, capped at 50 entries.

    Fix round 3 (task-l3-findings-r3.md): load-modify-save happens under
    ONE _schedules_lock acquisition, not two separate ones (see
    save_schedules()'s docstring) - this is the cron half of the same
    defect the trigger marker fix closes: `last_run`, written here, is
    what _due_to_fire() reads to dedup a cron schedule within the same
    minute, so a lost update here could produce a duplicate cron-
    triggered session the same way a lost update to the trigger marker
    produces a duplicate reset-triggered one."""
    from datetime import datetime
    with _schedules_lock:
        schedules = _load_schedules_locked()
        for s in schedules:
            if s.get("id") == schedule_id:
                history = s.get("history", [])
                entry = {
                    "timestamp": datetime.now().isoformat() + 'Z',
                    "status": status,
                    "message": message,
                }
                # Add any extra fields (e.g. duration_minutes, summary)
                for k, v in kwargs.items():
                    entry[k] = v
                history.append(entry)
                s["history"] = history[-50:]
                s["last_run"] = datetime.now().isoformat() + 'Z'
                _save_schedules_locked(schedules)
                return
