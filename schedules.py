"""Schedule storage — CRUD for scheduled tasks."""

import json
import os
import shutil
import tempfile
import threading
import uuid

from config import SCHEDULES_FILE

_schedules_lock = threading.Lock()


def load_schedules():
    """Load schedules from JSON file. Returns list of schedule dicts."""
    with _schedules_lock:
        if not os.path.isfile(SCHEDULES_FILE):
            return []
        try:
            with open(SCHEDULES_FILE, "r") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"  Warning: failed to load schedules: {e}")
            return []


def save_schedules(schedules):
    """Write schedules list to JSON file atomically (temp file in the same
    directory + os.replace), mode 0600. Keeps one rolling backup of the
    previous contents at SCHEDULES_FILE + '.bak' before overwriting."""
    with _schedules_lock:
        directory = os.path.dirname(SCHEDULES_FILE)
        os.makedirs(directory, exist_ok=True)
        if os.path.isfile(SCHEDULES_FILE):
            try:
                shutil.copyfile(SCHEDULES_FILE, SCHEDULES_FILE + ".bak")
            except OSError as e:
                print(f"  Warning: could not update schedules.json.bak: {e}")
        fd, tmp_path = tempfile.mkstemp(
            prefix=".schedules-", suffix=".json.tmp", dir=directory)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(schedules, f, indent=2)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, SCHEDULES_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def get_schedule_by_id(schedule_id):
    """Find a schedule by its id. Returns (index, schedule) or (None, None)."""
    schedules = load_schedules()
    for i, s in enumerate(schedules):
        if s.get("id") == schedule_id:
            return i, s
    return None, None


def create_schedule(data):
    """Create a new schedule. Returns the created schedule dict."""
    from datetime import datetime
    schedule = {
        "id": uuid.uuid4().hex[:12],
        "name": data.get("name", "Untitled"),
        "cron": data.get("cron", ""),
        "prompt": data.get("prompt", ""),
        "instructions_file": data.get("instructions_file"),
        "workdir": data.get("workdir", "/tmp"),
        "mode": data.get("mode", "c"),
        "model": data.get("model"),
        "enabled": data.get("enabled", True),
        "last_run": None,
        "created_at": datetime.now().isoformat() + 'Z',
        "history": [],
    }
    schedules = load_schedules()
    schedules.append(schedule)
    save_schedules(schedules)
    return schedule


def update_schedule(schedule_id, updates):
    """Update a schedule by id. Returns updated schedule or None."""
    schedules = load_schedules()
    for i, s in enumerate(schedules):
        if s.get("id") == schedule_id:
            allowed = {"name", "cron", "prompt", "instructions_file", "workdir",
                       "mode", "model", "enabled", "last_run", "history"}
            for k, v in updates.items():
                if k in allowed:
                    s[k] = v
            save_schedules(schedules)
            return s
    return None


def delete_schedule(schedule_id):
    """Delete a schedule by id. Returns True if found and deleted."""
    schedules = load_schedules()
    new_schedules = [s for s in schedules if s.get("id") != schedule_id]
    if len(new_schedules) < len(schedules):
        save_schedules(new_schedules)
        return True
    return False


def add_history_entry(schedule_id, status, message, **kwargs):
    """Add a history entry to a schedule, capped at 50 entries."""
    from datetime import datetime
    schedules = load_schedules()
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
            save_schedules(schedules)
            return
