# RC Launcher v3 Phase 0: Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the hub scheduler reliable again (atomic storage, self-healing on corruption), add a manual (on-demand) task kind and per-schedule concurrency control, close the worst security holes in `server.py`, and bring the repo up to public-OSS hygiene (CI, secret/identifier scanning, no personal data in tracked files).

**Architecture:** Every change is additive or surgical inside the existing single-process, stdlib-only Python backend (`schedules.py`, `scheduler.py`, `server.py`, `sessions.py`, `config.py`) plus the existing React/Vite SPA under `frontend/`. No new services, no new runtime dependencies, no database. Corruption-prone JSON storage gets atomic writes and validation; the scheduler gets a testable decision core (`_due_to_fire`, concurrency tracking keyed by schedule id); the HTTP handler gets small pure helper functions extracted specifically so they can be unit tested without a live socket.

**Tech Stack:** Python 3.9+ stdlib (`http.server`, `subprocess`, `json`, `tempfile`, `threading`), tmux, React 18 + TypeScript + Vite (frontend, prebuilt to `static/dist` and committed).

**Spec:** External planning note from the 2026-09-06 planning session (not tracked in this repo, and not referenced by path here, because it is a private document living outside the repo under the operator's home directory — copying that path into a tracked file would itself violate Global Constraint 3 below). This plan is self-contained: every requirement it implements is captured in the Global Constraints and the 26 tasks below. A matching operator-facing checklist for the box-side steps that are explicitly NOT part of this repo lives in the private Obsidian vault.

## Global Constraints

- Python 3.9+ stdlib only at runtime. No new pip dependencies.
- Every file write under `~/.claude-rc` is atomic (temp file in the same directory + `os.replace`) and mode `0600`.
- No personal hostnames, tailnet names, IPs, or `/root/...` paths anywhere in tracked files. Use `~/.claude-rc` and generic placeholders (e.g. `alice`, `/home/user/...`, `example.com`).
- All subprocess calls stay argv lists. Never `shell=True`.
- Existing tests keep passing: baseline is `python3 -m unittest discover tests` -> `Ran 25 tests ... OK` (recorded 2026-09-06 on branch `v3-phase0` at the tip of this checkout).
- Each task ends with a commit on `v3-phase0`.
- Nothing in this plan restarts services, touches `/root/.claude-rc/` or `/var/www/rc-launcher` (the main checkout), or pushes to a remote. All work happens inside this worktree (`/var/www/rc-launcher-v3`) only.

---

### Task 1: Atomic, 0600 `save_schedules` with a rolling backup

**Files:**
- Modify: `schedules.py`
- Test: `tests/test_schedules.py` (new)

**Interfaces:**
- Produces: `schedules.save_schedules(schedules: list) -> None` (same signature, now atomic). Writes `SCHEDULES_FILE` and rolls the previous contents to `SCHEDULES_FILE + ".bak"` before each write.
- Consumes: `config.SCHEDULES_FILE` (existing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schedules.py`:

```python
"""Tests for schedules.py's storage layer: atomic writes, rolling backup,
load validation, and the manual-task (cron: null) data model."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedules


class SaveSchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sched_file = os.path.join(self.tmpdir, "schedules.json")
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = self.sched_file

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_creates_file_mode_0600(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        mode = stat.S_IMODE(os.stat(self.sched_file).st_mode)
        self.assertEqual(mode, 0o600)
        with open(self.sched_file) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_save_writes_rolling_backup_of_previous_contents(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        schedules.save_schedules([{"id": "b", "name": "B"}])
        bak_path = self.sched_file + ".bak"
        self.assertTrue(os.path.isfile(bak_path))
        with open(bak_path) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_no_backup_written_on_first_ever_save(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        self.assertFalse(os.path.isfile(self.sched_file + ".bak"))

    def test_save_leaves_no_tmp_files_behind(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        leftovers = [f for f in os.listdir(self.tmpdir) if ".tmp" in f]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /var/www/rc-launcher-v3 && python3 -m unittest tests.test_schedules -v`
Expected: `test_save_creates_file_mode_0600` and `test_save_writes_rolling_backup_of_previous_contents` FAIL (current `save_schedules` neither sets mode nor writes a backup). `test_no_backup_written_on_first_ever_save` and `test_save_leaves_no_tmp_files_behind` currently PASS by accident (no backup logic exists yet, and the write isn't via a temp file) — that's fine, they lock in the desired behavior going forward.

- [ ] **Step 3: Make `save_schedules` atomic with a rolling backup**

In `schedules.py`, add imports at the top (after `import json`):

```python
import json
import os
import shutil
import tempfile
import threading
import uuid
```

Replace the `save_schedules` function:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /var/www/rc-launcher-v3 && python3 -m unittest tests.test_schedules -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK` (25 previous + 4 new = 29 tests).

```bash
git add schedules.py tests/test_schedules.py
git commit -m "fix: make save_schedules atomic, 0600, with a rolling backup"
```

---

### Task 2: `load_schedules` validation and a module-level `LAST_LOAD_ERROR`

**Files:**
- Modify: `schedules.py`
- Test: `tests/test_schedules.py`

**Interfaces:**
- Produces: `schedules.LAST_LOAD_ERROR: str | None` (module-level, updated on every `load_schedules()` call). `schedules._validate_schedules(data) -> (list, str | None)`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedules.py` (before the `if __name__ == "__main__":` line):

```python
class LoadSchedulesValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sched_file = os.path.join(self.tmpdir, "schedules.json")
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = self.sched_file

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_raw(self, text):
        with open(self.sched_file, "w") as f:
            f.write(text)

    def test_missing_file_is_not_an_error(self):
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNone(schedules.LAST_LOAD_ERROR)

    def test_valid_file_clears_last_load_error(self):
        self._write_raw('[{"id": "a", "name": "A", "cron": null}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [{"id": "a", "name": "A", "cron": None}])
        self.assertIsNone(schedules.LAST_LOAD_ERROR)

    def test_corrupt_json_sets_last_load_error_and_returns_empty(self):
        self._write_raw('[{"id": "a"}]]')  # the real-world failure mode
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_non_list_top_level_sets_last_load_error(self):
        self._write_raw('{"not": "a list"}')
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_entry_missing_id_is_dropped_but_others_survive(self):
        self._write_raw('[{"name": "no id"}, {"id": "b", "name": "B"}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [{"id": "b", "name": "B"}])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_entry_with_non_string_cron_is_dropped(self):
        self._write_raw('[{"id": "a", "name": "A", "cron": 5}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_schedules.LoadSchedulesValidationTest -v`
Expected: FAIL on every test except `test_missing_file_is_not_an_error` — `schedules.LAST_LOAD_ERROR` does not exist yet (AttributeError), and corrupt/invalid data currently returns `[]` unconditionally with no per-entry filtering.

- [ ] **Step 3: Implement validation and `LAST_LOAD_ERROR`**

In `schedules.py`, add the module-level variable right after `_schedules_lock = threading.Lock()`:

```python
_schedules_lock = threading.Lock()

LAST_LOAD_ERROR = None
```

Add a validation helper and replace `load_schedules`:

```python
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
        valid.append(entry)
    error = "; ".join(problems) if problems else None
    return valid, error


def load_schedules():
    """Load schedules from JSON file. Returns list of schedule dicts.

    Sets the module-level LAST_LOAD_ERROR to a description of what went
    wrong (JSON parse failure, or per-entry validation problems) so callers
    (GET /schedules, the scheduler loop) can surface it instead of it
    looking indistinguishable from "no schedules configured". Cleared to
    None on a fully clean load.
    """
    global LAST_LOAD_ERROR
    with _schedules_lock:
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
```

This replaces the entire original `load_schedules` function (the one that only checked `isinstance(data, list)` and returned `[]` on any exception).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_schedules -v`
Expected: all tests PASS (11 total in this file so far).

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add schedules.py tests/test_schedules.py
git commit -m "feat: validate schedules.json on load, expose LAST_LOAD_ERROR"
```

---

### Task 3: `GET /schedules` surfaces the load error; `next_run` enrichment extracted and null-safe

**Files:**
- Modify: `server.py`
- Test: `tests/test_server_helpers.py` (new)

**Interfaces:**
- Produces: `server._enrich_next_run(schedule: dict) -> dict` (pure, returns a copy).
- Consumes: `schedules.LAST_LOAD_ERROR` (Task 2), `scheduler.next_cron_run` (existing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_helpers.py`:

```python
"""Pure-helper unit tests for server.py. The request handler itself needs a
live socket to construct, so logic worth covering gets extracted into small
functions and tested directly here instead."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


class EnrichNextRunTest(unittest.TestCase):
    def test_manual_schedule_has_no_next_run(self):
        s = server._enrich_next_run({"enabled": True, "cron": None})
        self.assertIsNone(s["next_run"])

    def test_disabled_schedule_has_no_next_run(self):
        s = server._enrich_next_run({"enabled": False, "cron": "0 9 * * *"})
        self.assertIsNone(s["next_run"])

    def test_enabled_cron_schedule_gets_a_next_run(self):
        s = server._enrich_next_run({"enabled": True, "cron": "0 9 * * *"})
        self.assertIsNotNone(s["next_run"])

    def test_does_not_mutate_the_input(self):
        original = {"enabled": True, "cron": None}
        server._enrich_next_run(original)
        self.assertNotIn("next_run", original)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_enrich_next_run'`.

- [ ] **Step 3: Extract `_enrich_next_run` and surface `LAST_LOAD_ERROR`**

In `server.py`, change the schedules import block (this also sets up the module-level access needed to read a live-updated `LAST_LOAD_ERROR`, which a `from schedules import LAST_LOAD_ERROR` would NOT give you since that copies the value at import time):

```python
from schedules import (
    load_schedules, create_schedule, update_schedule, delete_schedule,
)
```

becomes:

```python
import schedules
from schedules import (
    load_schedules, create_schedule, update_schedule, delete_schedule,
)
```

Add `_enrich_next_run` near `_parse_projects` (top of the file, after its closing brace):

```python
def _enrich_next_run(schedule):
    """Return a copy of `schedule` with next_run computed. Disabled and
    manual (cron: null) schedules always get next_run: None."""
    s = dict(schedule)
    if s.get("enabled") and s.get("cron"):
        s["next_run"] = next_cron_run(s["cron"])
    else:
        s["next_run"] = None
    return s
```

Replace the `/schedules` GET handler:

```python
        elif path == "/schedules":
            schedules = load_schedules()
            # Enrich with next_run
            for s in schedules:
                if s.get("enabled") and s.get("cron"):
                    s["next_run"] = next_cron_run(s["cron"])
                else:
                    s["next_run"] = None
            self._json({"schedules": schedules})
```

with:

```python
        elif path == "/schedules":
            sched_list = [_enrich_next_run(s) for s in load_schedules()]
            resp = {"schedules": sched_list}
            if schedules.LAST_LOAD_ERROR:
                resp["error"] = schedules.LAST_LOAD_ERROR
            self._json(resp)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add server.py tests/test_server_helpers.py
git commit -m "feat: surface schedules.json load errors from GET /schedules"
```

---

### Task 4: Scheduler loop logs a schedules-load failure once per distinct message

**Files:**
- Modify: `scheduler.py`
- Test: `tests/test_scheduler.py` (new)

**Interfaces:**
- Produces: `scheduler._schedule_error_to_log(err: str | None) -> str | None`.
- Consumes: `schedules.LAST_LOAD_ERROR` (Task 2), imported as `schedules_module` to avoid colliding with the local variable `schedules` already used inside `_scheduler_loop`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler.py`:

```python
"""Tests for scheduler.py."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler


class ScheduleErrorLoggingTest(unittest.TestCase):
    def setUp(self):
        scheduler._last_logged_schedule_error = None

    def tearDown(self):
        scheduler._last_logged_schedule_error = None

    def test_first_occurrence_is_logged(self):
        msg = scheduler._schedule_error_to_log("Extra data: line 5 column 1")
        self.assertIn("Extra data", msg)

    def test_repeated_identical_error_is_not_logged_again(self):
        scheduler._schedule_error_to_log("boom")
        self.assertIsNone(scheduler._schedule_error_to_log("boom"))

    def test_a_different_error_is_logged_again(self):
        scheduler._schedule_error_to_log("boom")
        msg = scheduler._schedule_error_to_log("a different boom")
        self.assertIsNotNone(msg)

    def test_clearing_then_recurring_logs_again(self):
        scheduler._schedule_error_to_log("boom")
        scheduler._schedule_error_to_log(None)  # file loaded cleanly
        msg = scheduler._schedule_error_to_log("boom")
        self.assertIsNotNone(msg)

    def test_no_error_returns_none(self):
        self.assertIsNone(scheduler._schedule_error_to_log(None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_scheduler -v`
Expected: FAIL with `AttributeError: module 'scheduler' has no attribute '_last_logged_schedule_error'`.

- [ ] **Step 3: Implement dedup logging and wire it into `_scheduler_loop`**

In `scheduler.py`, change the schedules import line:

```python
from schedules import load_schedules, save_schedules, add_history_entry
```

to:

```python
from schedules import load_schedules, save_schedules, add_history_entry
import schedules as schedules_module
```

Add the dedup state and helper right after the imports, before `WIZARD_PROMPT = """...`:

```python
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
```

In `_scheduler_loop`, change:

```python
        schedules = load_schedules()

        for schedule in schedules:
```

to:

```python
        schedules = load_schedules()
        msg = _schedule_error_to_log(schedules_module.LAST_LOAD_ERROR)
        if msg:
            print(f"  {msg}")

        for schedule in schedules:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_scheduler -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: log schedules.json load failures once per distinct message"
```

---

### Task 5: `tools/repair_schedules.py` - recover the longest valid JSON prefix

**Files:**
- Create: `tools/repair_schedules.py`
- Test: `tests/test_repair_schedules.py` (new)

**Interfaces:**
- Produces: `repair_schedules.repair(path: str) -> bool`, `repair_schedules.recover_longest_prefix(text: str) -> (value, trailing_text)`. CLI: `python3 tools/repair_schedules.py <path>`.
- Consumes: nothing from the app; this is a standalone offline tool.

Never run this tool against a live file as part of this plan - it operates only on temp-directory fixtures in its tests.

- [ ] **Step 1: Create the tools directory and write the failing tests**

```bash
mkdir -p /var/www/rc-launcher-v3/tools
```

Create `tests/test_repair_schedules.py`:

```python
"""Tests for tools/repair_schedules.py. Never run this tool against a real
schedules.json from a test - only against temp-directory fixtures."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import repair_schedules


class RepairSchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def test_recovers_array_with_trailing_stray_bracket(self):
        self._write('[{"id": "a", "name": "A"}]]')
        ok = repair_schedules.repair(self.path)
        self.assertTrue(ok)
        with open(self.path) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_backs_up_original_before_rewriting(self):
        self._write('[{"id": "a"}]]')
        repair_schedules.repair(self.path)
        backups = [f for f in os.listdir(self.tmpdir) if f.startswith("schedules.json.corrupt-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmpdir, backups[0])) as f:
            self.assertEqual(f.read(), '[{"id": "a"}]]')

    def test_clean_file_is_left_untouched(self):
        self._write('[{"id": "a"}]')
        before = os.stat(self.path).st_mtime_ns
        ok = repair_schedules.repair(self.path)
        self.assertTrue(ok)
        after = os.stat(self.path).st_mtime_ns
        self.assertEqual(before, after)
        backups = [f for f in os.listdir(self.tmpdir) if "corrupt" in f]
        self.assertEqual(backups, [])

    def test_unrecoverable_garbage_is_left_untouched(self):
        self._write('not json at all {{{')
        ok = repair_schedules.repair(self.path)
        self.assertFalse(ok)
        with open(self.path) as f:
            self.assertEqual(f.read(), 'not json at all {{{')

    def test_recovered_value_that_is_not_a_list_is_left_untouched(self):
        self._write('{"a": 1}extra')
        ok = repair_schedules.repair(self.path)
        self.assertFalse(ok)

    def test_recovered_file_is_mode_0600(self):
        self._write('[{"id": "a"}]]')
        repair_schedules.repair(self.path)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_repair_schedules -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'repair_schedules'`.

- [ ] **Step 3: Write `tools/repair_schedules.py`**

```python
#!/usr/bin/env python3
"""Repair a corrupted schedules.json by recovering the longest valid JSON
prefix. The observed failure mode is trailing garbage after a valid array
(e.g. a stray ']' appended past the closing ']'), which makes json.load
raise "Extra data" on the whole file even though the real content parses
fine up to that point.

Usage: python3 tools/repair_schedules.py <path-to-schedules.json>

Backs up the original file to <path>.corrupt-<timestamp>, then atomically
rewrites <path> with the recovered, re-serialized JSON (mode 0600). Leaves
the file untouched if it already parses cleanly, or if no valid JSON list
can be recovered at all.
"""
import json
import os
import shutil
import sys
import tempfile
import time


def recover_longest_prefix(text):
    """Return (value, trailing_text) for the longest valid JSON value
    parseable from the start of `text`, or (None, None) if nothing parses."""
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None, None
    return value, text[end:]


def repair(path):
    with open(path, "r") as f:
        original = f.read()

    try:
        json.loads(original)
        print(f"{path} already parses cleanly; nothing to do.")
        return True
    except json.JSONDecodeError as e:
        print(f"{path} failed to parse: {e}")

    value, trailing = recover_longest_prefix(original)
    if value is None:
        print("Could not recover any valid JSON prefix. Not touching the file.")
        return False
    if not isinstance(value, list):
        print(f"Recovered value is a {type(value).__name__}, not a list. Not touching the file.")
        return False

    backup_path = f"{path}.corrupt-{time.strftime('%Y%m%dT%H%M%S')}"
    shutil.copyfile(path, backup_path)
    print(f"Backed up original to {backup_path}")
    if trailing.strip():
        print(f"Discarded trailing data: {trailing.strip()[:200]!r}")

    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(prefix=".schedules-repair-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"Recovered {len(value)} schedule(s) and rewrote {path}.")
    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 tools/repair_schedules.py <path-to-schedules.json>", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"No such file: {path}", file=sys.stderr)
        sys.exit(1)
    ok = repair(path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_repair_schedules -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add tools/repair_schedules.py tests/test_repair_schedules.py
git commit -m "feat: add tools/repair_schedules.py to recover a corrupt schedules.json"
```

Do not run this tool against `~/.claude-rc/schedules.json` as part of this plan - that box-side repair is tracked separately in the operator's ops checklist.

---

### Task 6: Regression tests locking in `cron: null` round-tripping through `create_schedule`/`update_schedule`

**Files:**
- Modify: `tests/test_schedules.py`

**Interfaces:**
- Consumes: `schedules.create_schedule`, `schedules.update_schedule` (existing, unchanged).

No production code changes in this task. `create_schedule` and `update_schedule` already store whatever value is given for `cron` via generic `dict.get(...)` calls with no cron-specific logic - `data.get("cron") ` correctly yields `None` when the caller explicitly sends `{"cron": null}` (a default only applies when the key is *absent*, not when it is `None`). This task adds tests that lock that existing behavior in, so a future refactor of `schedules.py` cannot silently break manual-task storage.

- [ ] **Step 1: Write the tests**

Append to `tests/test_schedules.py` (before `if __name__ == "__main__":`):

```python
class ManualTaskCronTest(unittest.TestCase):
    """schedules.py stores whatever it is given, with no cron-specific
    logic, so these already pass with zero production code changes - this
    locks the behavior in against future refactors."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_schedule_preserves_null_cron(self):
        s = schedules.create_schedule({"name": "ad hoc", "cron": None, "prompt": "hi"})
        self.assertIsNone(s["cron"])

    def test_update_schedule_can_set_cron_to_null(self):
        s = schedules.create_schedule({"name": "was cron'd", "cron": "0 9 * * *"})
        updated = schedules.update_schedule(s["id"], {"cron": None})
        self.assertIsNone(updated["cron"])

    def test_update_schedule_can_set_cron_back_to_a_string(self):
        s = schedules.create_schedule({"name": "manual", "cron": None})
        updated = schedules.update_schedule(s["id"], {"cron": "0 9 * * *"})
        self.assertEqual(updated["cron"], "0 9 * * *")
```

- [ ] **Step 2: Run the tests to verify they pass immediately**

Run: `python3 -m unittest tests.test_schedules.ManualTaskCronTest -v`
Expected: all 3 PASS with no code changes - this confirms the finding above rather than exercising a red-green cycle.

- [ ] **Step 3: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add tests/test_schedules.py
git commit -m "test: lock in cron:null round-tripping through create/update_schedule"
```

---

### Task 7: `validate_cron` and `next_cron_run` are null-safe

**Files:**
- Modify: `scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `scheduler.validate_cron(None) -> None`, `scheduler.next_cron_run(None) -> None` (both already existed; behavior extended).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler.py` (before `if __name__ == "__main__":`):

```python
class NullSafeCronTest(unittest.TestCase):
    def test_validate_cron_accepts_null(self):
        self.assertIsNone(scheduler.validate_cron(None))

    def test_next_cron_run_returns_none_for_null(self):
        self.assertIsNone(scheduler.next_cron_run(None))

    def test_validate_cron_still_rejects_bad_strings(self):
        self.assertIsNotNone(scheduler.validate_cron("not a cron"))

    def test_validate_cron_still_accepts_good_strings(self):
        self.assertIsNone(scheduler.validate_cron("0 9 * * *"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_scheduler.NullSafeCronTest -v`
Expected: `test_validate_cron_accepts_null` and `test_next_cron_run_returns_none_for_null` FAIL with `AttributeError: 'NoneType' object has no attribute 'strip'`.

- [ ] **Step 3: Add the null guards**

In `scheduler.py`, change:

```python
def validate_cron(expr):
    """Validate a cron expression. Returns None if valid, error string if invalid."""
    try:
```

to:

```python
def validate_cron(expr):
    """Validate a cron expression. Returns None if valid (including the
    null cron of a manual task), error string if invalid."""
    if expr is None:
        return None
    try:
```

And change:

```python
def next_cron_run(expr, after_dt=None):
    """Calculate next run time for a cron expression. Returns ISO string or None."""
    if after_dt is None:
```

to:

```python
def next_cron_run(expr, after_dt=None):
    """Calculate next run time for a cron expression. Returns ISO string or
    None (always None for a manual task's null cron)."""
    if expr is None:
        return None
    if after_dt is None:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_scheduler -v`
Expected: all tests in the file PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "fix: validate_cron and next_cron_run are null-safe for manual tasks"
```

---

### Task 8: `_due_to_fire` extraction - manual tasks never fire on a timer

**Files:**
- Modify: `scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `scheduler._due_to_fire(schedule: dict, now: datetime) -> bool`. Also introduces the shared test harness (`FakeRun`, `ImmediateThread`, `_patch_scheduler`, `_restore_scheduler`) that Tasks 10 and 17 reuse.
- Consumes: `scheduler.cron_matches` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler.py`. First add `from datetime import datetime` to the top imports (change `import scheduler` line's neighborhood):

```python
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler
```

Then append the test harness and test classes (before `if __name__ == "__main__":`):

```python
class FakeRun:
    """Records every subprocess.run call scheduler.py makes and answers
    'tmux has-session' / 'tmux list-sessions' according to a settable set
    of "alive" session names. Same approach as tests/test_setup_session.py's
    FakeRun, extended for the session-lifecycle calls scheduler.py makes."""

    def __init__(self):
        self.calls = []
        self.alive = set()

    def __call__(self, cmd, *a, **kw):
        self.calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if isinstance(cmd, list) and cmd[:2] == ["tmux", "has-session"]:
            name = cmd[cmd.index("-t") + 1]
            R.returncode = 0 if name in self.alive else 1
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "new-session"]:
            name = cmd[cmd.index("-s") + 1]
            self.alive.add(name)
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "kill-session"]:
            name = cmd[cmd.index("-t") + 1]
            self.alive.discard(name)
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "list-sessions"]:
            R.stdout = "\n".join(self.alive)
        return R

    def new_session_names(self):
        return [c[c.index("-s") + 1] for c in self.calls
                if isinstance(c, list) and c[:2] == ["tmux", "new-session"]]


class ImmediateThread:
    """Runs its target synchronously instead of in a background thread, so
    scheduler tests don't race _fire_schedule's async setup-and-send step."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _patch_scheduler(fake):
    """Patch scheduler.py's IO surface with `fake`, returning the original
    values so a test's tearDown can restore them via _restore_scheduler."""
    saved = {
        "run": scheduler.subprocess.run,
        "sleep": scheduler.time.sleep,
        "exists": scheduler.session_exists,
        "setup": scheduler.setup_session,
        "thread": scheduler.threading.Thread,
        "list_sessions": scheduler.list_rc_sessions,
        "add_history": scheduler.add_history_entry,
    }
    scheduler.subprocess.run = fake
    scheduler.time.sleep = lambda *_: None
    scheduler.session_exists = lambda n: n in fake.alive
    scheduler.setup_session = lambda *a, **kw: None
    scheduler.threading.Thread = ImmediateThread
    scheduler.list_rc_sessions = lambda: [{"name": n} for n in fake.alive]
    scheduler._active_scheduled_sessions.clear()
    return saved


def _restore_scheduler(saved):
    scheduler.subprocess.run = saved["run"]
    scheduler.time.sleep = saved["sleep"]
    scheduler.session_exists = saved["exists"]
    scheduler.setup_session = saved["setup"]
    scheduler.threading.Thread = saved["thread"]
    scheduler.list_rc_sessions = saved["list_sessions"]
    scheduler.add_history_entry = saved["add_history"]
    scheduler._active_scheduled_sessions.clear()


class DueToFireTest(unittest.TestCase):
    def test_manual_task_is_never_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertFalse(scheduler._due_to_fire({"cron": None, "enabled": True}, now))

    def test_empty_string_cron_is_never_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertFalse(scheduler._due_to_fire({"cron": "", "enabled": True}, now))

    def test_matching_cron_with_no_last_run_is_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertTrue(scheduler._due_to_fire({"cron": "0 9 * * *", "enabled": True}, now))

    def test_matching_cron_already_run_this_minute_is_not_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        schedule = {"cron": "0 9 * * *", "enabled": True, "last_run": now.isoformat()}
        self.assertFalse(scheduler._due_to_fire(schedule, now))

    def test_non_matching_cron_is_not_due(self):
        now = datetime(2026, 9, 6, 9, 1)
        self.assertFalse(scheduler._due_to_fire({"cron": "0 9 * * *", "enabled": True}, now))

    def test_invalid_cron_string_is_not_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertFalse(scheduler._due_to_fire({"cron": "garbage", "enabled": True}, now))


class ManualTaskFiresTest(unittest.TestCase):
    """POST /schedules/fire (which calls _fire_schedule directly) must work
    for a manual task even though _due_to_fire would never call it."""

    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def test_fire_schedule_works_with_no_cron(self):
        scheduler._fire_schedule({"id": "manual-1", "name": "ad hoc", "cron": None,
                                   "workdir": "/tmp", "prompt": "do the thing"})
        self.assertEqual(len(self.fake.new_session_names()), 1)
        self.assertIn(("manual-1", "ok"), [(h[0], h[1]) for h in self.history])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_scheduler.DueToFireTest -v`
Expected: FAIL with `AttributeError: module 'scheduler' has no attribute '_due_to_fire'`. `ManualTaskFiresTest` should already PASS (nothing in `_fire_schedule` looks at `cron` today) - confirm with `python3 -m unittest tests.test_scheduler.ManualTaskFiresTest -v`.

- [ ] **Step 3: Extract `_due_to_fire` and use it in `_scheduler_loop`**

In `scheduler.py`, add `_due_to_fire` right after `next_cron_run`, before the `# --- Session lifecycle tracking ---` comment:

```python
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
```

In `_scheduler_loop`, replace:

```python
        schedules = load_schedules()
        msg = _schedule_error_to_log(schedules_module.LAST_LOAD_ERROR)
        if msg:
            print(f"  {msg}")

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
```

with:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_scheduler -v`
Expected: all tests in the file PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "refactor: extract _due_to_fire; manual tasks never fire on a timer"
```

---

### Task 9: `concurrency` field in `create_schedule`/`update_schedule`

**Files:**
- Modify: `schedules.py`
- Test: `tests/test_schedules.py`

**Interfaces:**
- Produces: schedule dicts now carry `"concurrency": "skip" | "kill"` (default `"skip"`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedules.py` (before `if __name__ == "__main__":`):

```python
class ConcurrencyFieldTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_schedule_defaults_concurrency_to_skip(self):
        s = schedules.create_schedule({"name": "task"})
        self.assertEqual(s["concurrency"], "skip")

    def test_create_schedule_preserves_explicit_kill(self):
        s = schedules.create_schedule({"name": "task", "concurrency": "kill"})
        self.assertEqual(s["concurrency"], "kill")

    def test_update_schedule_can_change_concurrency(self):
        s = schedules.create_schedule({"name": "task"})
        updated = schedules.update_schedule(s["id"], {"concurrency": "kill"})
        self.assertEqual(updated["concurrency"], "kill")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_schedules.ConcurrencyFieldTest -v`
Expected: FAIL with `KeyError: 'concurrency'` (the key does not exist on the created dict yet).

- [ ] **Step 3: Add the field**

In `schedules.py`, in `create_schedule`, change:

```python
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
```

to:

```python
    schedule = {
        "id": uuid.uuid4().hex[:12],
        "name": data.get("name", "Untitled"),
        "cron": data.get("cron", ""),
        "prompt": data.get("prompt", ""),
        "instructions_file": data.get("instructions_file"),
        "workdir": data.get("workdir", "/tmp"),
        "mode": data.get("mode", "c"),
        "model": data.get("model"),
        "concurrency": data.get("concurrency", "skip"),
        "enabled": data.get("enabled", True),
        "last_run": None,
        "created_at": datetime.now().isoformat() + 'Z',
        "history": [],
    }
```

In `update_schedule`, change:

```python
            allowed = {"name", "cron", "prompt", "instructions_file", "workdir",
                       "mode", "model", "enabled", "last_run", "history"}
```

to:

```python
            allowed = {"name", "cron", "prompt", "instructions_file", "workdir",
                       "mode", "model", "concurrency", "enabled", "last_run", "history"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_schedules.ConcurrencyFieldTest -v`
Expected: all 3 PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add schedules.py tests/test_schedules.py
git commit -m "feat: add concurrency field (skip|kill) to schedules"
```

---

### Task 10: `rc-run-<hex>` session naming and per-schedule concurrency control

**Files:**
- Modify: `scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: session names for fired schedules are now `rc-run-<12 hex chars>` instead of `rc-sched-<slug>-<timestamp>`. `_active_scheduled_sessions` is now keyed by **schedule id** (was: tmux session name) and stores `{session_name, started_at, schedule_safe_name}`.
- Consumes: the `FakeRun`/`ImmediateThread`/`_patch_scheduler`/`_restore_scheduler` harness from Task 8.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler.py` (before `if __name__ == "__main__":`):

```python
class FireScheduleNamingTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def test_session_name_matches_rc_run_hex_pattern(self):
        scheduler._fire_schedule({"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        names = self.fake.new_session_names()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r'^rc-run-[0-9a-f]{12}$')


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def _schedule(self, **overrides):
        s = {"id": "s1", "name": "task", "cron": "0 9 * * *", "workdir": "/tmp", "prompt": "hi"}
        s.update(overrides)
        return s

    def test_default_skip_does_not_fire_a_second_time_while_running(self):
        schedule = self._schedule()
        scheduler._fire_schedule(schedule)
        first_names = self.fake.new_session_names()
        self.assertEqual(len(first_names), 1)

        scheduler._fire_schedule(schedule)
        self.assertEqual(self.fake.new_session_names(), first_names)  # no new session
        self.assertIn(("s1", "skipped"), [(h[0], h[1]) for h in self.history])

    def test_kill_stops_the_old_session_and_starts_a_new_one(self):
        schedule = self._schedule(concurrency="kill")
        scheduler._fire_schedule(schedule)
        first_names = self.fake.new_session_names()
        self.assertEqual(len(first_names), 1)
        self.assertIn(first_names[0], self.fake.alive)

        scheduler._fire_schedule(schedule)
        second_names = self.fake.new_session_names()
        self.assertEqual(len(second_names), 2)
        self.assertNotIn(first_names[0], self.fake.alive)  # old one killed
        self.assertIn(second_names[1], self.fake.alive)

    def test_fires_normally_when_nothing_is_tracked_yet(self):
        scheduler._fire_schedule(self._schedule())
        self.assertEqual(len(self.fake.new_session_names()), 1)
        self.assertIn(("s1", "ok"), [(h[0], h[1]) for h in self.history])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_scheduler.FireScheduleNamingTest tests.test_scheduler.ConcurrencyTest -v`
Expected: `test_session_name_matches_rc_run_hex_pattern` FAILS (current name is `rc-sched-<slug>-<MMDD-HHMMSS>`). `test_default_skip_does_not_fire_a_second_time_while_running` and `test_kill_stops_the_old_session_and_starts_a_new_one` FAIL (no concurrency tracking exists yet, so a second fire always starts a new session).

- [ ] **Step 3: Implement session-id naming and concurrency control**

In `scheduler.py`, add `import uuid` to the top imports:

```python
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
```

Change the module-level tracking dict comment:

```python
_active_scheduled_sessions = {}  # session_name -> {schedule_id, started_at, schedule_safe_name}
```

to:

```python
_active_scheduled_sessions = {}  # schedule_id -> {session_name, started_at, schedule_safe_name}
```

Replace the start of `_fire_schedule` (name generation and the stale-session-kill block, which the concurrency dict now supersedes):

```python
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
```

with:

```python
def _fire_schedule(schedule):
    """Spawn a new Claude session for a scheduled task."""
    name = schedule.get("name", "task")
    schedule_id = schedule["id"]
    concurrency = schedule.get("concurrency", "skip")

    existing = _active_scheduled_sessions.get(schedule_id)
    if existing and session_exists(existing["session_name"]):
        if concurrency == "kill":
            subprocess.run(["tmux", "kill-session", "-t", existing["session_name"]],
                            capture_output=True)
            print(f"  Scheduler: killed running session {existing['session_name']} "
                  f"for '{name}' (concurrency=kill)")
            del _active_scheduled_sessions[schedule_id]
        else:
            add_history_entry(schedule_id, "skipped",
                               f"Still running as {existing['session_name']} (concurrency=skip)")
            print(f"  Scheduler: skipped '{name}', already running as {existing['session_name']}")
            return

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name.replace(" ", "-"))
    session_name = f"{SESSION_PREFIX}run-{uuid.uuid4().hex[:12]}"

    workdir = schedule.get("workdir", "/tmp")
```

Further down in `_fire_schedule`, change the `_setup_and_send` registration:

```python
        # Register session for lifecycle monitoring
        _active_scheduled_sessions[session_name] = {
            "schedule_id": schedule["id"],
            "started_at": datetime.now(),
            "schedule_safe_name": safe_name,
        }
```

to:

```python
        # Register session for lifecycle monitoring and concurrency control
        _active_scheduled_sessions[schedule_id] = {
            "session_name": session_name,
            "started_at": datetime.now(),
            "schedule_safe_name": safe_name,
        }
```

Replace `_monitor_scheduled_sessions` (now keyed by schedule id, not session name):

```python
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
```

with:

```python
def _monitor_scheduled_sessions():
    """Check if any tracked scheduled sessions have ended."""
    if not _active_scheduled_sessions:
        return

    ended = []
    for schedule_id, info in _active_scheduled_sessions.items():
        session_name = info["session_name"]
        # Check if tmux session still exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True
        )
        if result.returncode != 0:
            # Session ended
            ended.append(schedule_id)
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

    for schedule_id in ended:
        del _active_scheduled_sessions[schedule_id]
```

Note: `queue` concurrency is explicitly deferred to Tasks v2 - only `skip` (default) and `kill` are implemented here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_scheduler -v`
Expected: all tests in the file PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: rc-run-<hex> session naming and skip|kill concurrency control"
```

---

### Task 11: Enforce the `rc-` prefix on session-scoped endpoints

**Files:**
- Modify: `server.py`
- Test: `tests/test_server_helpers.py`

**Interfaces:**
- Produces: `server._valid_session_name(name: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_helpers.py` (before `if __name__ == "__main__":`):

```python
class ValidSessionNameTest(unittest.TestCase):
    def test_accepts_a_normal_rc_session_name(self):
        self.assertTrue(server._valid_session_name("rc-portugal"))

    def test_rejects_missing_prefix(self):
        self.assertFalse(server._valid_session_name("portugal"))

    def test_rejects_empty(self):
        self.assertFalse(server._valid_session_name(""))

    def test_rejects_path_traversal(self):
        self.assertFalse(server._valid_session_name("rc-../../etc/passwd"))

    def test_rejects_embedded_slash(self):
        self.assertFalse(server._valid_session_name("rc-foo/bar"))

    def test_rejects_dotdot_even_with_prefix(self):
        self.assertFalse(server._valid_session_name("rc-..secret"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.ValidSessionNameTest -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_valid_session_name'`.

- [ ] **Step 3: Add the helper and apply it to the six session-scoped endpoints**

In `server.py`, add near the other module-level helpers (e.g. right before the `Handler` class):

```python
def _valid_session_name(name):
    """True if `name` is safe to use as a tmux session identifier: non-empty,
    no path traversal or separators, and carries our 'rc-' prefix so a
    logged-in browser can only reach sessions the launcher itself created."""
    return bool(name) and ".." not in name and "/" not in name and name.startswith(SESSION_PREFIX)
```

Apply it at each of the six call sites, replacing the old ad hoc checks. **1. `/ws` (do_GET):**

```python
            name = clean[len("/sessions/"):-len("/ws")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
                return
            if not session_exists(name):
```

becomes:

```python
            name = clean[len("/sessions/"):-len("/ws")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            if not session_exists(name):
```

**2. `/transcript` (do_GET):**

```python
            name = path[len("/sessions/"):-len("/transcript")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
                return
            if not session_exists(name):
```

becomes:

```python
            name = path[len("/sessions/"):-len("/transcript")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
            if not session_exists(name):
```

**3. `/preview` (do_GET) - note the original check here was even weaker (no `/` test at all):**

```python
            name = clean[len("/sessions/"):-len("/preview")]
            if not name or ".." in name:
                self.send_error(404)
                return
```

becomes:

```python
            name = clean[len("/sessions/"):-len("/preview")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
```

**4. `/preview-bye` (do_POST):**

```python
            name = path[len("/sessions/"):-len("/preview-bye")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
                return
```

becomes:

```python
            name = path[len("/sessions/"):-len("/preview-bye")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
```

**5. `/resize` (do_POST):**

```python
            name = path[len("/sessions/"):-len("/resize")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
                return
```

becomes:

```python
            name = path[len("/sessions/"):-len("/resize")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
```

**6. `/keys` (do_POST):**

```python
            name = path[len("/sessions/"):-len("/keys")]
            if not name or ".." in name or "/" in name:
                self.send_error(404)
                return
```

becomes:

```python
            name = path[len("/sessions/"):-len("/keys")]
            if not _valid_session_name(name):
                self._json({"ok": False, "message": "Invalid session name"}, 400)
                return
```

Each of these six blocks has unique surrounding context (different endpoint suffix in the preceding line), so each is a distinct, unambiguous edit.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: all tests PASS.

- [ ] **Step 5: Manually verify the six call sites**

Run: `grep -n "_valid_session_name" server.py`
Expected: 7 matches (1 definition + 6 call sites).

- [ ] **Step 6: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add server.py tests/test_server_helpers.py
git commit -m "fix: enforce rc- prefix on all session-scoped endpoints"
```

---

### Task 12: Remove the `/schedules/wizard` endpoint and its backend plumbing

**Files:**
- Modify: `server.py`, `scheduler.py`, `sessions.py`

**Interfaces:**
- Removes: `POST /schedules/wizard` route, `scheduler.WIZARD_PROMPT`, the `wizard`/`RC_WIZARD` fields from `sessions.list_rc_sessions`.

Ruling: the endpoint is removed, not fixed - the SPA (`static/dist`) never calls it, and it is the last place `~/.claude-rc/.wizard-token-*` files get written. This also permanently retires `RC_WIZARD=1`, so the `wizard` badge plumbing in `sessions.py` is dead code once this lands; Task 13 removes the matching legacy-UI badge code.

- [ ] **Step 1: Confirm current usage (no test framework needed for a pure deletion)**

Run: `grep -rn "schedules/wizard\|WIZARD_PROMPT\|RC_WIZARD" server.py scheduler.py sessions.py`
Expected: several matches (this is the baseline to diff against after deletion).

- [ ] **Step 2: Remove the route and its import from `server.py`**

Change:

```python
from scheduler import validate_cron, next_cron_run, _fire_schedule, WIZARD_PROMPT
```

to:

```python
from scheduler import validate_cron, next_cron_run, _fire_schedule
```

Delete the entire `elif path == "/schedules/wizard":` branch. Its exact boundary: it starts right after the `/schedules/fire` branch's closing `self._json({"ok": True, "message": f"Firing schedule '{schedule.get('name')}'"})` line, and its own last line is `self._json({"ok": True, "message": "Wizard session started", "name": name})`, immediately followed by the do_POST catch-all:

```python
        elif path == "/schedules/wizard":
            body = self._read_body()
            description = body.get("description", "").strip()
```

... (the full ~95-line branch, matching what is currently at this location) ...

```python
            threading.Thread(target=_setup_and_send, daemon=True).start()
            self._json({"ok": True, "message": "Wizard session started", "name": name})

        else:
            self.send_error(404)
```

Delete everything from `        elif path == "/schedules/wizard":` through `            self._json({"ok": True, "message": "Wizard session started", "name": name})` inclusive, and the blank line right after it, leaving:

```python
        else:
            self.send_error(404)
```

as the immediate next code after the `/schedules/fire` branch.

- [ ] **Step 3: Remove `WIZARD_PROMPT` from `scheduler.py`**

Delete the `WIZARD_PROMPT = """..."""` constant (it sits between the `schedules` import line and the `# --- Cron expression parser ---` comment). After deletion, the top of the file reads:

```python
from schedules import load_schedules, save_schedules, add_history_entry
import schedules as schedules_module

_last_logged_schedule_error = None


def _schedule_error_to_log(err):
    ...


# --- Cron expression parser ---
```

(i.e. the multi-line `WIZARD_PROMPT` string and the blank lines that isolated it are gone; the `_schedule_error_to_log` helper from Task 4 and the comment that follows are unaffected.)

- [ ] **Step 4: Remove the `wizard`/`RC_WIZARD` fields from `sessions.py`**

In `list_rc_sessions`, change:

```python
        mode = get_session_env(name, "RC_MODE") or "c"
        workdir = get_session_env(name, "RC_WORKDIR")
        wizard = get_session_env(name, "RC_WIZARD")
        is_sh = mode == SHELL_MODE
        url = None if is_sh else get_url(name)
        status = get_session_status(name)
        tokens = None if is_sh else get_tokens(name)
        s = {"name": name, "mode": mode, "url": url, "status": status}
        if tokens is not None:
            s["tokens"] = tokens
        if wizard:
            s["wizard"] = True
        if workdir:
```

to:

```python
        mode = get_session_env(name, "RC_MODE") or "c"
        workdir = get_session_env(name, "RC_WORKDIR")
        is_sh = mode == SHELL_MODE
        url = None if is_sh else get_url(name)
        status = get_session_status(name)
        tokens = None if is_sh else get_tokens(name)
        s = {"name": name, "mode": mode, "url": url, "status": status}
        if tokens is not None:
            s["tokens"] = tokens
        if workdir:
```

- [ ] **Step 5: Verify the removal and run the full suite**

Run: `grep -rn "schedules/wizard\|WIZARD_PROMPT\|RC_WIZARD" server.py scheduler.py sessions.py`
Expected: no matches.

Run: `python3 -m unittest discover tests`
Expected: `OK` (confirms nothing else imports the removed names).

- [ ] **Step 6: Commit**

```bash
git add server.py scheduler.py sessions.py
git commit -m "fix: remove POST /schedules/wizard and its backend plumbing"
```

---

### Task 13: Remove the legacy `static/app.js` wizard UI

**Files:**
- Modify: `static/app.js`, `static/index.html`, `static/style.css`

**Interfaces:**
- Removes: the wizard modal, its JS state/functions, and its CSS. `openNewSchedule()` is redefined to open the existing plain "New/Edit Schedule" modal directly in create mode (that modal and its `saveSchedule()` create-vs-update branch already exist and are unaffected).

This only touches the legacy, non-SPA UI (`/legacy` route, `static/app.js` + `static/index.html` + `static/style.css`). The default `/` route serves `static/dist` (the React SPA), which never called the wizard endpoint.

- [ ] **Step 1: Confirm current usage**

Run: `grep -n "wizard\|Wizard" static/app.js static/index.html static/style.css | wc -l`
Expected: a nonzero count (baseline to diff against after deletion).

- [ ] **Step 2: Remove the wizard badge/hint from the session card renderer in `static/app.js`**

Change:

```js
      const modeLabel = s.mode === 'ci' ? 'teammate' : s.mode === 'safe' ? 'safe' : 'standard';
      const wizardBadge = s.wizard ? ' <span class="badge badge-wizard">wizard</span>' : '';
      const wizardHint = (s.wizard && s.url && s.status !== 'dead')
        ? '<div style="font-size:0.72rem;color:#fbbf24;margin-bottom:0.5rem;">Open this session to finalize your scheduled task with Claude</div>'
        : '';
      const isDead = s.status === 'dead';
```

to:

```js
      const modeLabel = s.mode === 'ci' ? 'teammate' : s.mode === 'safe' ? 'safe' : 'standard';
      const isDead = s.status === 'dead';
```

Change:

```js
      return '<div class="session-card' + (isDead ? ' session-card-dead' : '') + '">' +
        '<div class="session-header">' +
          '<span class="session-name">' + escHtml(s.name) + wizardBadge + '</span>' +
          '<span style="display:flex;gap:0.3rem;align-items:center;">' + permTag +
            '<span class="badge ' + badgeClass + '">' + modeLabel + '</span>' +
          '</span>' +
        '</div>' +
        projectHtml + tokenHtml + wizardHint + urlHtml +
        '<div class="session-actions"><div class="session-actions-left">' + previewBtn + restartBtn + stopBtn + '</div>' + (nudgeBtn || copyBtn) + '</div>' +
      '</div>';
```

to:

```js
      return '<div class="session-card' + (isDead ? ' session-card-dead' : '') + '">' +
        '<div class="session-header">' +
          '<span class="session-name">' + escHtml(s.name) + '</span>' +
          '<span style="display:flex;gap:0.3rem;align-items:center;">' + permTag +
            '<span class="badge ' + badgeClass + '">' + modeLabel + '</span>' +
          '</span>' +
        '</div>' +
        projectHtml + tokenHtml + urlHtml +
        '<div class="session-actions"><div class="session-actions-left">' + previewBtn + restartBtn + stopBtn + '</div>' + (nudgeBtn || copyBtn) + '</div>' +
      '</div>';
```

- [ ] **Step 3: Delete the wizard section of `static/app.js` and redefine `openNewSchedule()`**

Run: `grep -n "^/\* --- Wizard --- \*/\|^async function openEditSchedule" static/app.js`
Expected: `954:/* --- Wizard --- */` and `1142:async function openEditSchedule(id) {`

Delete every line from `954` (`/* --- Wizard --- */`) through `1140` (the closing `}` of `wizardCreate()`), leaving the blank line at 1141 and `async function openEditSchedule(id) {` at 1142 as the next code. Verify before deleting with `sed -n '954,1141p' static/app.js` so you can see exactly what you are removing, and after with `sed -n '948,960p' static/app.js` to confirm the file now goes straight from the code before the wizard section into `openEditSchedule`.

In its place (i.e. somewhere before `async function openEditSchedule`, at module scope), add a plain, non-wizard `openNewSchedule()` that reuses the existing `#schedule-modal` (the same modal `openEditSchedule` uses, which already supports create-vs-update via `editingScheduleId`):

```js
function openNewSchedule() {
  editingScheduleId = null;
  document.getElementById('modal-title').textContent = 'New Schedule';
  document.getElementById('sched-name').value = '';
  document.getElementById('sched-cron').value = '';
  document.getElementById('sched-cron-preset').value = '';
  document.getElementById('sched-prompt').value = '';
  document.getElementById('sched-file').value = '';
  document.getElementById('sched-mode').value = 'c';
  document.getElementById('sched-model').value = '';
  updateCronPreview();
  populateSchedProjects('');
  document.getElementById('schedule-modal').style.display = 'flex';
}
```

This keeps the two existing "+ Add" buttons in `static/index.html` (`onclick="openNewSchedule()"`) working unchanged - they now open the plain modal instead of the wizard, matching what the SPA's `ScheduleModal.tsx` already does.

- [ ] **Step 4: Delete the wizard modal markup from `static/index.html`**

Run: `grep -n "Wizard Modal\|Resume Modal" static/index.html`
Expected: `325:  <!-- Wizard Modal -->` and a `<!-- Resume Modal -->` line shortly after.

Delete every line from `<!-- Wizard Modal -->` through the `</div>` that closes `#wizard-modal`, immediately before the `<!-- Resume Modal -->` comment. Verify before deleting with `sed -n '325,428p' static/index.html`, and after with `grep -n "wizard" static/index.html` (expect no matches).

- [ ] **Step 5: Delete the wizard CSS from `static/style.css`**

Run: `grep -n "/\* Wizard \*/\|badge-wizard" static/style.css`
Expected: the `/* Wizard */` comment line and the `.badge-wizard {...}` rule shortly after it.

Delete every line from `/* Wizard */` through the `.badge-wizard { ... }` rule inclusive. Verify with `grep -n "wizard" static/style.css` afterward (expect no matches).

- [ ] **Step 6: Verify and run the full suite**

Run: `grep -rn "wizard" static/app.js static/index.html static/style.css`
Expected: no matches.

Run: `python3 -m unittest discover tests`
Expected: `OK` (this task touches no Python).

- [ ] **Step 7: Commit**

```bash
git add static/app.js static/index.html static/style.css
git commit -m "fix: remove the legacy wizard UI from the /legacy app"
```

---

### Task 14: Trust `X-Real-IP`/`X-Forwarded-For` only from configured proxies

**Files:**
- Modify: `config.py`, `server.py`
- Test: `tests/test_server_helpers.py`

**Interfaces:**
- Produces: `config.RC_TRUSTED_PROXIES: set[str]` (env `RC_TRUSTED_PROXIES`, default `{"127.0.0.1", "::1"}`). `server._resolve_client_ip(peer_ip, real_ip_header, forwarded_for_header, trusted_proxies) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_helpers.py`. Add `import config` to the top imports:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import server
```

Then append (before `if __name__ == "__main__":`):

```python
class ResolveClientIpTest(unittest.TestCase):
    def test_untrusted_peer_is_used_as_is_even_with_headers(self):
        ip = server._resolve_client_ip("203.0.113.9", "1.2.3.4", "", {"127.0.0.1"})
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_peer_uses_x_real_ip(self):
        ip = server._resolve_client_ip("127.0.0.1", "203.0.113.9", "", {"127.0.0.1"})
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_peer_falls_back_to_x_forwarded_for(self):
        ip = server._resolve_client_ip("127.0.0.1", "", "203.0.113.9, 10.0.0.1", {"127.0.0.1"})
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_peer_with_no_headers_uses_peer(self):
        ip = server._resolve_client_ip("127.0.0.1", "", "", {"127.0.0.1"})
        self.assertEqual(ip, "127.0.0.1")

    def test_default_trusted_proxies_include_loopback(self):
        self.assertIn("127.0.0.1", config.RC_TRUSTED_PROXIES)
        self.assertIn("::1", config.RC_TRUSTED_PROXIES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.ResolveClientIpTest -v`
Expected: FAIL with `AttributeError` (`RC_TRUSTED_PROXIES` and `_resolve_client_ip` do not exist yet).

- [ ] **Step 3: Add `RC_TRUSTED_PROXIES` to `config.py`**

Change:

```python
SHELL_BIN = os.environ.get("RC_SHELL_BIN") or os.environ.get("SHELL") or "/bin/bash"
```

to:

```python
SHELL_BIN = os.environ.get("RC_SHELL_BIN") or os.environ.get("SHELL") or "/bin/bash"
RC_TRUSTED_PROXIES = set(
    p.strip() for p in os.environ.get("RC_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if p.strip()
)
```

- [ ] **Step 4: Add `_resolve_client_ip` to `server.py` and use it for login rate limiting**

Add `RC_TRUSTED_PROXIES` to the config import:

```python
from config import (
    VERSION, HOST, PORT, SESSION_PREFIX, WORKING_DIR, CLAUDE_BIN,
    AUTH_USER, AUTH_PASS, RC_FLAGS, MODEL_MAP, SHELL_BIN, SHELL_MODE,
    resolve_claude_mode,
    BROWSE_ROOTS, RC_TRUSTED_PROXIES,
)
```

Add the helper near `_valid_session_name`:

```python
def _resolve_client_ip(peer_ip, real_ip_header, forwarded_for_header, trusted_proxies):
    """Return the IP to use for login rate limiting.

    X-Real-IP / X-Forwarded-For are attacker-controlled unless the request
    actually came through a proxy we trust (nginx on the same box, by
    default) - otherwise anyone can spoof them to dodge the lockout."""
    if peer_ip not in trusted_proxies:
        return peer_ip
    if real_ip_header:
        return real_ip_header
    if forwarded_for_header:
        return forwarded_for_header.split(",")[0].strip()
    return peer_ip
```

In `do_POST`, change:

```python
        if raw_path in ("/login", "/rc/login"):
            client_ip = self.headers.get("X-Real-IP", self.client_address[0])
```

to:

```python
        if raw_path in ("/login", "/rc/login"):
            client_ip = _resolve_client_ip(
                self.client_address[0],
                self.headers.get("X-Real-IP", ""),
                self.headers.get("X-Forwarded-For", ""),
                RC_TRUSTED_PROXIES,
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add config.py server.py tests/test_server_helpers.py
git commit -m "fix: trust X-Real-IP/X-Forwarded-For only from configured proxies"
```

---

### Task 15: `RC_BEHIND_TLS` forces `Secure` cookies; stable `AUTH FAIL` log line

**Files:**
- Modify: `config.py`, `server.py`
- Test: `tests/test_server_helpers.py`

**Interfaces:**
- Produces: `config.RC_BEHIND_TLS: bool` (env `RC_BEHIND_TLS=1`). `server._cookie_secure_flag(behind_tls, forwarded_proto) -> bool`. `server._record_failed_login(ip, user="")` now also prints `AUTH FAIL ip=<ip> user=<user>` to stdout.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_helpers.py` (before `if __name__ == "__main__":`):

```python
class CookieSecureFlagTest(unittest.TestCase):
    def test_true_when_behind_tls_env_set(self):
        self.assertTrue(server._cookie_secure_flag(True, None))

    def test_true_when_forwarded_proto_is_https(self):
        self.assertTrue(server._cookie_secure_flag(False, "https"))

    def test_false_over_plain_http_with_no_tls_env(self):
        self.assertFalse(server._cookie_secure_flag(False, None))
        self.assertFalse(server._cookie_secure_flag(False, "http"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.CookieSecureFlagTest -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_cookie_secure_flag'`.

- [ ] **Step 3: Add `RC_BEHIND_TLS` to `config.py`**

Change:

```python
RC_TRUSTED_PROXIES = set(
    p.strip() for p in os.environ.get("RC_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if p.strip()
)
```

to:

```python
RC_TRUSTED_PROXIES = set(
    p.strip() for p in os.environ.get("RC_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if p.strip()
)
RC_BEHIND_TLS = os.environ.get("RC_BEHIND_TLS", "") == "1"
```

- [ ] **Step 4: Add `_cookie_secure_flag`, wire it into both cookies, and log `AUTH FAIL`**

Add `RC_BEHIND_TLS` to the config import:

```python
    BROWSE_ROOTS, RC_TRUSTED_PROXIES,
)
```

becomes:

```python
    BROWSE_ROOTS, RC_TRUSTED_PROXIES, RC_BEHIND_TLS,
)
```

Add the helper near `_resolve_client_ip`:

```python
def _cookie_secure_flag(behind_tls, forwarded_proto):
    """True if the Secure cookie attribute should be set: either the
    operator has explicitly said we sit behind TLS termination, or the
    proxy told us this particular request arrived over https."""
    return bool(behind_tls) or forwarded_proto == "https"
```

In the `GET /login` handler, change:

```python
            self.send_header("Set-Cookie", f"csrf={csrf}; Path=/; HttpOnly; SameSite=Strict")
```

to:

```python
            csrf_secure = "; Secure" if _cookie_secure_flag(RC_BEHIND_TLS, self.headers.get("X-Forwarded-Proto")) else ""
            self.send_header("Set-Cookie", f"csrf={csrf}; Path=/; HttpOnly; SameSite=Strict{csrf_secure}")
```

In the `POST /login` handler, change:

```python
                secure = "Secure; " if self.headers.get("X-Forwarded-Proto") == "https" else ""
```

to:

```python
                secure = "Secure; " if _cookie_secure_flag(RC_BEHIND_TLS, self.headers.get("X-Forwarded-Proto")) else ""
```

Change `_record_failed_login` to accept and log the attempted user:

```python
def _record_failed_login(ip):
    """Record a failed login attempt."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)
```

to:

```python
def _record_failed_login(ip, user=""):
    """Record a failed login attempt and log a stable line fail2ban can
    match (see docs/fail2ban/claude-rc.conf)."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)
    print(f"AUTH FAIL ip={ip} user={user}")
```

Update both call sites (the CSRF-failure branch and the bad-credentials branch of `POST /login`) - both occurrences of the exact text `_record_failed_login(client_ip)` become `_record_failed_login(client_ip, user)` (the `user` variable is already in scope at both call sites, parsed earlier in the same function). Since both occurrences need the identical change, use `replace_all` when editing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add config.py server.py tests/test_server_helpers.py
git commit -m "feat: RC_BEHIND_TLS forces Secure cookies; log AUTH FAIL for fail2ban"
```

---

### Task 16: Harden `POST /update` - explicit confirmation and service-unit detection

**Files:**
- Modify: `server.py`
- Test: `tests/test_server_helpers.py`

**Interfaces:**
- Produces: `server._update_confirmed(confirm: str, remote_sha: str) -> bool`. `server._pick_restart_command(system_unit_active, user_unit_active, is_macos, uid) -> list[str] | None`. `server._detect_and_restart() -> str` (impure; calls the two pure helpers above plus `subprocess`/`sys.platform`/`os.getuid`).
- API change: `POST /update` now requires `{"confirm": "<sha>"}` matching `git rev-parse origin/main` after a `git fetch`; returns 409 with `{"remote_sha", "pending_commits"}` otherwise. `GET /update-check` is unchanged and still the only place that compares against the GitHub-published `VERSION` string.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_helpers.py` (before `if __name__ == "__main__":`):

```python
class UpdateConfirmedTest(unittest.TestCase):
    def test_matching_sha_confirms(self):
        self.assertTrue(server._update_confirmed("abc123", "abc123"))

    def test_missing_confirm_does_not_confirm(self):
        self.assertFalse(server._update_confirmed("", "abc123"))

    def test_wrong_sha_does_not_confirm(self):
        self.assertFalse(server._update_confirmed("wrong", "abc123"))

    def test_empty_remote_sha_never_confirms(self):
        self.assertFalse(server._update_confirmed("", ""))


class PickRestartCommandTest(unittest.TestCase):
    def test_prefers_active_system_unit(self):
        cmd = server._pick_restart_command(True, True, True, 501)
        self.assertEqual(cmd, ["systemctl", "restart", "claude-rc-launcher"])

    def test_falls_back_to_user_unit(self):
        cmd = server._pick_restart_command(False, True, True, 501)
        self.assertEqual(cmd, ["systemctl", "--user", "restart", "claude-rc"])

    def test_falls_back_to_launchd_on_macos(self):
        cmd = server._pick_restart_command(False, False, True, 501)
        self.assertEqual(cmd, ["launchctl", "kickstart", "-k", "gui/501/com.claude-rc.launcher"])

    def test_none_when_nothing_detected(self):
        self.assertIsNone(server._pick_restart_command(False, False, False, 501))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.UpdateConfirmedTest tests.test_server_helpers.PickRestartCommandTest -v`
Expected: FAIL with `AttributeError` for both new functions.

- [ ] **Step 3: Add `import sys` and the two pure helpers**

In `server.py`, add `import sys` to the top-level imports:

```python
import subprocess
import sys
import threading
```

Add the helpers near `_valid_session_name`:

```python
def _update_confirmed(confirm, remote_sha):
    """True if the client explicitly confirmed the exact commit to update
    to. Prevents a single stray POST /update from silently deploying
    whatever happens to be on origin/main at that instant."""
    return bool(remote_sha) and confirm == remote_sha


def _pick_restart_command(system_unit_active, user_unit_active, is_macos, uid):
    """Pick the command to restart the launcher, in priority order: an
    active system unit, then a user unit, then macOS launchd. Returns None
    if none apply - the operator restarts manually."""
    if system_unit_active:
        return ["systemctl", "restart", "claude-rc-launcher"]
    if user_unit_active:
        return ["systemctl", "--user", "restart", "claude-rc"]
    if is_macos:
        return ["launchctl", "kickstart", "-k", f"gui/{uid}/com.claude-rc.launcher"]
    return None


def _detect_and_restart():
    """Restart the launcher via whichever install mechanism is active, and
    return a human-readable status message."""
    system_active = subprocess.run(
        ["systemctl", "is-active", "--quiet", "claude-rc-launcher"],
        capture_output=True,
    ).returncode == 0
    user_active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "claude-rc"],
        capture_output=True,
    ).returncode == 0
    cmd = _pick_restart_command(system_active, user_active, sys.platform == "darwin", os.getuid())
    if cmd is None:
        return "Restart manually to apply the update."

    def _run_delayed():
        time.sleep(1)
        subprocess.run(cmd, capture_output=True)

    threading.Thread(target=_run_delayed, daemon=True).start()
    return f"Restarting ({' '.join(cmd)})..."
```

- [ ] **Step 4: Replace the `/update` handler**

Change:

```python
        elif path == "/update":
            # Pull latest code from git and restart the service
            app_dir = os.path.dirname(os.path.abspath(__file__))
            git_dir = os.path.join(app_dir, ".git")
            if not os.path.isdir(git_dir):
                self._json({"ok": False, "message": "Not a git install. Re-run the install script."}, 400)
                return
            # Get old version
            old_ver = VERSION
            # Git pull
            result = subprocess.run(
                ["git", "-C", app_dir, "pull", "--ff-only"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                self._json({"ok": False, "message": f"git pull failed: {result.stderr.strip()}"}, 500)
                return
            # Read new version
            new_ver = old_ver
            try:
                cfg_path = os.path.join(app_dir, "config.py")
                with open(cfg_path) as f:
                    for line in f:
                        if line.startswith("VERSION"):
                            new_ver = line.split('"')[1]
                            break
            except Exception:
                pass
            self._json({"ok": True, "old": old_ver, "new": new_ver,
                         "message": f"Updated {old_ver} → {new_ver}. Restarting..."})
            # Schedule restart in background so the response gets sent first
            def _restart():
                time.sleep(1)
                os.execv("/usr/bin/systemctl", ["systemctl", "restart", "claude-rc-launcher"])
            threading.Thread(target=_restart, daemon=True).start()
```

to:

```python
        elif path == "/update":
            app_dir = os.path.dirname(os.path.abspath(__file__))
            git_dir = os.path.join(app_dir, ".git")
            if not os.path.isdir(git_dir):
                self._json({"ok": False, "message": "Not a git install. Re-run the install script."}, 400)
                return
            fetch = subprocess.run(
                ["git", "-C", app_dir, "fetch", "origin", "main"],
                capture_output=True, text=True, timeout=30,
            )
            if fetch.returncode != 0:
                self._json({"ok": False, "message": f"git fetch failed: {fetch.stderr.strip()}"}, 500)
                return
            head = subprocess.run(
                ["git", "-C", app_dir, "rev-parse", "origin/main"],
                capture_output=True, text=True, timeout=10,
            )
            if head.returncode != 0:
                self._json({"ok": False, "message": f"git rev-parse failed: {head.stderr.strip()}"}, 500)
                return
            remote_sha = head.stdout.strip()
            body = self._read_body()
            if not _update_confirmed(body.get("confirm", ""), remote_sha):
                log = subprocess.run(
                    ["git", "-C", app_dir, "log", "--oneline", f"HEAD..{remote_sha}"],
                    capture_output=True, text=True, timeout=10,
                )
                pending = log.stdout.strip().splitlines() if log.returncode == 0 else []
                self._json({"ok": False,
                             "message": "Confirm the exact commit to update to (see remote_sha / pending_commits).",
                             "remote_sha": remote_sha, "pending_commits": pending}, 409)
                return
            old_ver = VERSION
            merge = subprocess.run(
                ["git", "-C", app_dir, "merge", "--ff-only", remote_sha],
                capture_output=True, text=True, timeout=30,
            )
            if merge.returncode != 0:
                self._json({"ok": False, "message": f"git merge failed: {merge.stderr.strip()}"}, 500)
                return
            new_ver = old_ver
            try:
                cfg_path = os.path.join(app_dir, "config.py")
                with open(cfg_path) as f:
                    for line in f:
                        if line.startswith("VERSION"):
                            new_ver = line.split('"')[1]
                            break
            except Exception:
                pass
            restart_msg = _detect_and_restart()
            self._json({"ok": True, "old": old_ver, "new": new_ver,
                         "message": f"Updated {old_ver} → {new_ver}. {restart_msg}"})
```

`GET /update-check` needs no changes - it already exists and serves a different purpose (comparing `VERSION` against the GitHub-published value for the "update available" badge).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: all tests PASS.

- [ ] **Step 6: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add server.py tests/test_server_helpers.py
git commit -m "fix: require explicit commit confirmation for POST /update; detect service unit"
```

---

### Task 17: `RC_MAX_SESSIONS` cap across `/start`, `/resume/start`, and the scheduler

**Files:**
- Modify: `config.py`, `server.py`, `scheduler.py`
- Test: `tests/test_server_helpers.py`, `tests/test_scheduler.py`

**Interfaces:**
- Produces: `config.RC_MAX_SESSIONS: int` (env `RC_MAX_SESSIONS`, default `10`). `server._session_cap_message(current_count, max_sessions) -> str | None`. `GET /stats` gains `"max_sessions"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_helpers.py` (before `if __name__ == "__main__":`):

```python
class SessionCapMessageTest(unittest.TestCase):
    def test_none_when_under_cap(self):
        self.assertIsNone(server._session_cap_message(3, 10))

    def test_message_when_at_cap(self):
        msg = server._session_cap_message(10, 10)
        self.assertIsNotNone(msg)
        self.assertIn("10", msg)

    def test_message_when_over_cap(self):
        self.assertIsNotNone(server._session_cap_message(11, 10))

    def test_default_max_sessions_is_ten(self):
        self.assertEqual(config.RC_MAX_SESSIONS, 10)
```

Append to `tests/test_scheduler.py` (before `if __name__ == "__main__":`):

```python
class SessionCapTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))
        self._orig_max = scheduler.RC_MAX_SESSIONS

    def tearDown(self):
        scheduler.RC_MAX_SESSIONS = self._orig_max
        _restore_scheduler(self._saved)

    def test_skips_firing_when_at_session_cap(self):
        scheduler.RC_MAX_SESSIONS = 2
        scheduler.list_rc_sessions = lambda: [{"name": "rc-a"}, {"name": "rc-b"}]
        scheduler._fire_schedule({"id": "s1", "name": "task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        self.assertEqual(self.fake.new_session_names(), [])
        self.assertEqual(len(self.history), 1)
        self.assertEqual(self.history[0][1], "skipped")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.SessionCapMessageTest tests.test_scheduler.SessionCapTest -v`
Expected: FAIL with `AttributeError` (`RC_MAX_SESSIONS` and `_session_cap_message` do not exist yet).

- [ ] **Step 3: Add `RC_MAX_SESSIONS` to `config.py`**

Change:

```python
RC_BEHIND_TLS = os.environ.get("RC_BEHIND_TLS", "") == "1"
```

to:

```python
RC_BEHIND_TLS = os.environ.get("RC_BEHIND_TLS", "") == "1"
RC_MAX_SESSIONS = int(os.environ.get("RC_MAX_SESSIONS", "10"))
```

- [ ] **Step 4: Enforce the cap in `server.py`**

Add `RC_MAX_SESSIONS` to the config import:

```python
    BROWSE_ROOTS, RC_TRUSTED_PROXIES, RC_BEHIND_TLS,
)
```

becomes:

```python
    BROWSE_ROOTS, RC_TRUSTED_PROXIES, RC_BEHIND_TLS, RC_MAX_SESSIONS,
)
```

Add the helper near `_valid_session_name`:

```python
def _session_cap_message(current_count, max_sessions):
    """None if under the session cap, else the 429 message to return."""
    if current_count >= max_sessions:
        return f"Session cap reached ({max_sessions}). Stop a session first."
    return None
```

In `/start`, change:

```python
            if session_exists(name):
                self._json({"ok": True, "message": "Already running", "name": name})
                return

            cmd = build_tmux_command(name, session_dir, mode, model=model,
                                     sandbox=sandbox)
```

to:

```python
            if session_exists(name):
                self._json({"ok": True, "message": "Already running", "name": name})
                return

            cap_msg = _session_cap_message(len(list_rc_sessions()), RC_MAX_SESSIONS)
            if cap_msg:
                self._json({"ok": False, "message": cap_msg}, 429)
                return

            cmd = build_tmux_command(name, session_dir, mode, model=model,
                                     sandbox=sandbox)
```

In `/resume/start`, change:

```python
            if not session_id or not project:
                self._json({"ok": False, "message": "Missing session_id or project"}, 400)
                return
            ok, msg, name = resume_session(session_id, session_title, project, mode)
```

to:

```python
            if not session_id or not project:
                self._json({"ok": False, "message": "Missing session_id or project"}, 400)
                return
            cap_msg = _session_cap_message(len(list_rc_sessions()), RC_MAX_SESSIONS)
            if cap_msg:
                self._json({"ok": False, "message": cap_msg}, 429)
                return
            ok, msg, name = resume_session(session_id, session_title, project, mode)
```

In `/stats`, change:

```python
        elif path == "/stats":
            sess = list_rc_sessions()
            s = stats.system_stats()
            s["token_history"] = stats.token_history()
            s["tokens_now"] = sum(x.get("tokens", 0) for x in sess)
            s["sessions"] = len(sess)
            self._json(s)
```

to:

```python
        elif path == "/stats":
            sess = list_rc_sessions()
            s = stats.system_stats()
            s["token_history"] = stats.token_history()
            s["tokens_now"] = sum(x.get("tokens", 0) for x in sess)
            s["sessions"] = len(sess)
            s["max_sessions"] = RC_MAX_SESSIONS
            self._json(s)
```

- [ ] **Step 5: Enforce the cap in `scheduler.py`**

Add `RC_MAX_SESSIONS` to the config import:

```python
from config import (SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, MODEL_MAP,
                    resolve_claude_mode)
```

becomes:

```python
from config import (SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, MODEL_MAP,
                    resolve_claude_mode, RC_MAX_SESSIONS)
```

In `_fire_schedule`, change:

```python
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name.replace(" ", "-"))
    session_name = f"{SESSION_PREFIX}run-{uuid.uuid4().hex[:12]}"

    workdir = schedule.get("workdir", "/tmp")
```

to:

```python
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name.replace(" ", "-"))
    session_name = f"{SESSION_PREFIX}run-{uuid.uuid4().hex[:12]}"

    if len(list_rc_sessions()) >= RC_MAX_SESSIONS:
        add_history_entry(schedule_id, "skipped", f"Session cap reached ({RC_MAX_SESSIONS})")
        print(f"  Scheduler: skipped '{name}', session cap reached ({RC_MAX_SESSIONS})")
        return

    workdir = schedule.get("workdir", "/tmp")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers tests.test_scheduler -v`
Expected: all tests PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `python3 -m unittest discover tests`
Expected: `OK`.

```bash
git add config.py server.py scheduler.py tests/test_server_helpers.py tests/test_scheduler.py
git commit -m "feat: RC_MAX_SESSIONS cap on /start, /resume/start, and scheduled fires"
```

---

### Task 18: `.gitignore` hardening

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the missing patterns**

Change:

```
.env
__pycache__/
*.pyc
.DS_Store
devices.json
frontend/node_modules
frontend/.vite
*.tsbuildinfo
frontend/vite.config.js
frontend/vite.config.d.ts
.env.bak*
```

to:

```
.env
__pycache__/
*.pyc
.DS_Store
devices.json
frontend/node_modules
frontend/.vite
*.tsbuildinfo
frontend/vite.config.js
frontend/vite.config.d.ts
.env.bak*
*.log
auth-tokens.json
hub.db*
events/
jobs/
.wizard-token*
.superpowers/
docs/private/
```

- [ ] **Step 2: Verify**

Run: `cd /var/www/rc-launcher-v3 && git check-ignore -v auth-tokens.json hub.db logs/claude-rc.log jobs/foo events/2026-09-06.jsonl docs/private/notes.md`
Expected: every path listed by `.gitignore` with the matching pattern.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore runtime state, logs, and private docs"
```

---

### Task 19: Remove personal identifiers from tracked files

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/components/ScheduleModal.tsx`, `sessions.py`, `docs/superpowers/plans/2026-05-22-react-tsx-ops-console-redesign.md`

Ruling: `docker-compose.yml`'s `${HOME}/.claude:/root/.claude` volume mount is a container-internal path (this Dockerfile has no `USER` directive, so `/root` is genuinely where the containerized process's home directory is) - it is not a personal machine path, so it is intentionally left as-is and excluded from the CI identifier grep in Task 20. `barjakuzu` (the public GitHub org/user for this OSS repo, e.g. in README.md and install.sh) is likewise left untouched - it is the intended public identity of the project, not a personal leak.

- [ ] **Step 1: Confirm the full set of matches**

Run:
```bash
cd /var/www/rc-launcher-v3
grep -rniE 'barjazz|tail82c219|tbarjadze|hetzner|tba-lin|100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|/root/' \
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=dist -- .
```
Expected output (4 files, matching what this plan was written against):
- `frontend/src/types.ts` (2 lines, "barjazz" in doc comments)
- `frontend/src/components/ScheduleModal.tsx` (1 line, a placeholder string)
- `sessions.py` (1 line, a docstring example)
- `docs/superpowers/plans/2026-05-22-react-tsx-ops-console-redesign.md` (2 lines)
- `docker-compose.yml` (1 line - leave this one, see ruling above)

- [ ] **Step 2: Fix `frontend/src/types.ts`**

Change:

```ts
  /** Process user on the device, e.g. "barjazz" or "root". May be empty if device on older code. */
```

to:

```ts
  /** Process user on the device, e.g. "alice" or "root". May be empty if device on older code. */
```

Change:

```ts
  /** Home directory on the device, e.g. "/home/barjazz" or "/root". May be empty. */
```

to:

```ts
  /** Home directory on the device, e.g. "/home/alice" or "/root". May be empty. */
```

- [ ] **Step 3: Fix `frontend/src/components/ScheduleModal.tsx`**

Change:

```tsx
              placeholder="/root/.claude-rc/jobs/my-task/instructions.md"
```

to:

```tsx
              placeholder="~/.claude-rc/jobs/my-task/instructions.md"
```

(`static/dist` will pick this up automatically when Task 26 rebuilds it - no separate action needed there.)

- [ ] **Step 4: Fix `sessions.py`**

Change:

```python
    e.g. '-root--claude-rc-app' → '/root/.claude-rc/app'"""
```

to:

```python
    e.g. '-home-user--claude-rc-app' → '/home/user/.claude-rc/app'"""
```

- [ ] **Step 5: Fix the historical plan doc**

In `docs/superpowers/plans/2026-05-22-react-tsx-ops-console-redesign.md`, change every occurrence of `/root/.claude-rc/` to `~/.claude-rc/` (two lines: one with `grep ^RC_AUTH_USER= /root/.claude-rc/env` and `grep ^RC_AUTH_PASS= /root/.claude-rc/env` on the same line, and one with `cd /root/.claude-rc/app`).

- [ ] **Step 6: Verify**

Run:
```bash
grep -rniE 'barjazz|tail82c219|tbarjadze|hetzner|tba-lin|100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|/root/' \
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=dist --exclude=docker-compose.yml -- .
```
Expected: no matches.

Run: `python3 -m unittest discover tests`
Expected: `OK` (this task touches one Python docstring, no behavior).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/ScheduleModal.tsx sessions.py docs/superpowers/plans/2026-05-22-react-tsx-ops-console-redesign.md
git commit -m "chore: replace personal identifiers with generic placeholders"
```

---

### Task 20: `.github/workflows/ci.yml`

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  pull_request:
  push:
    branches-ignore: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.9"

      - name: Python tests
        run: python3 -m unittest discover tests

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Frontend build
        run: |
          cd frontend
          npm ci
          npm run build

      - name: Gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: No personal identifiers outside docs/private
        run: |
          set -e
          if grep -rniE 'barjazz|tail82c219|tbarjadze|hetzner|tba-lin|100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|/root/' \
              --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=docs/private \
              --exclude-dir=dist --exclude=docker-compose.yml -- .; then
            echo "Found personal identifiers outside docs/private/ - see matches above."
            exit 1
          fi
          echo "No personal identifiers found."
```

Note: `docker-compose.yml` is excluded from the identifier grep for the same reason given in Task 19 - its `/root/.claude` volume target is a container-internal path, not a personal machine path.

- [ ] **Step 2: Sanity-check the YAML by eye**

Run: `cat -A .github/workflows/ci.yml | grep -n '\$' | grep -P '\t'`
Expected: no output (no literal tabs, which would be invalid YAML indentation). GitHub Actions itself validates the workflow syntax on the next push - there is no local `push` in this plan, so this is the practical verification available here.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add CI workflow for PRs and non-main pushes"
```

---

### Task 21: `release.yml` depends on the same checks

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Add a `checks` job and make `release` depend on it**

Change:

```yaml
jobs:
  release:
    runs-on: ubuntu-latest
    # Skip if the commit was made by this workflow (prevent infinite loop)
    if: "!contains(github.event.head_commit.message, '[skip ci]')"
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}
```

to:

```yaml
jobs:
  checks:
    runs-on: ubuntu-latest
    if: "!contains(github.event.head_commit.message, '[skip ci]')"
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.9"

      - name: Python tests
        run: python3 -m unittest discover tests

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Frontend build
        run: |
          cd frontend
          npm ci
          npm run build

  release:
    needs: checks
    runs-on: ubuntu-latest
    # Skip if the commit was made by this workflow (prevent infinite loop)
    if: "!contains(github.event.head_commit.message, '[skip ci]')"
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}
```

Everything after that (the `Get current version and auto-bump`, `Update config.py if bumped`, and `Create release` steps) is unchanged.

- [ ] **Step 2: Sanity-check indentation**

Run: `grep -n "^  [a-z]*:$" .github/workflows/release.yml`
Expected: `checks:` and `release:` both at 2-space indentation, matching `jobs:`'s children.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: gate release.yml on the same tests and build as ci.yml"
```

---

### Task 22: fail2ban filter for `AUTH FAIL`

**Files:**
- Create: `docs/fail2ban/claude-rc.conf`

**Interfaces:**
- Consumes: the `AUTH FAIL ip=<ip> user=<user>` log line added in Task 15, which lands in `~/.claude-rc/logs/claude-rc.log` via the existing `claude-rc.service`'s `StandardOutput=append:...` redirection.

- [ ] **Step 1: Create the directory and the filter**

```bash
mkdir -p /var/www/rc-launcher-v3/docs/fail2ban
```

Create `docs/fail2ban/claude-rc.conf`:

```ini
# fail2ban filter for Claude RC Launcher login failures.
#
# The launcher prints one stable line per failed login attempt to stdout,
# which systemd (or launchd) redirects to ~/.claude-rc/logs/claude-rc.log:
#
#   AUTH FAIL ip=203.0.113.9 user=admin
#
# Install:
#   sudo cp claude-rc.conf /etc/fail2ban/filter.d/claude-rc.conf
#   Add the [claude-rc] jail below to /etc/fail2ban/jail.local
#   sudo systemctl restart fail2ban

[Definition]
failregex = ^AUTH FAIL ip=<HOST> user=.*$
ignoreregex =

# --- Jail example (goes in /etc/fail2ban/jail.local, not this file) ---
#
# [claude-rc]
# enabled  = true
# filter   = claude-rc
# logpath  = /home/<user>/.claude-rc/logs/claude-rc.log
# port     = http,https
# maxretry = 5
# findtime = 300
# bantime  = 3600
```

- [ ] **Step 2: Verify the regex against a sample line**

Run:
```bash
python3 - <<'EOF'
import re
pattern = re.compile(r'^AUTH FAIL ip=(?P<host>\S+) user=.*$')
sample = "AUTH FAIL ip=203.0.113.9 user=admin"
m = pattern.match(sample.replace("<HOST>", r"(?P<host>\S+)"))
assert re.match(r'^AUTH FAIL ip=\S+ user=.*$', sample)
print("regex matches the sample line")
EOF
```
Expected: `regex matches the sample line`.

- [ ] **Step 3: Commit**

```bash
git add docs/fail2ban/claude-rc.conf
git commit -m "docs: add fail2ban filter for AUTH FAIL login failures"
```

---

### Task 23: Frontend `Schedule` type supports manual tasks and concurrency

**Files:**
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Produces: `Schedule.cron: string | null` (was `string`), `Schedule.concurrency?: string`.

- [ ] **Step 1: Update the type**

Change:

```ts
export interface Schedule {
  id: string; name: string; cron: string; enabled: boolean;
  prompt?: string; instructions_file?: string; mode?: string; model?: string; workdir?: string; next_run?: string; device?: string;
  schedule_label?: string;
  history?: ScheduleHistoryEntry[];
}
```

to:

```ts
export interface Schedule {
  id: string; name: string; cron: string | null; enabled: boolean;
  prompt?: string; instructions_file?: string; mode?: string; model?: string; workdir?: string; next_run?: string; device?: string;
  schedule_label?: string;
  concurrency?: string;
  history?: ScheduleHistoryEntry[];
}
```

- [ ] **Step 2: Commit**

No build step here - Task 26 does one full build at the end, after every frontend source task (23-25) is in. If `npm run build` at that point reports a type error pointing at a file this task touched, come back and fix it here.

```bash
git add frontend/src/types.ts
git commit -m "feat(frontend): Schedule.cron is nullable, add concurrency field"
```

---

### Task 24: `ScheduleModal.tsx` - manual preset and concurrency select

**Files:**
- Modify: `frontend/src/components/ScheduleModal.tsx`

**Interfaces:**
- Consumes: `Schedule.cron: string | null`, `Schedule.concurrency?: string` (Task 23).

- [ ] **Step 1: Add the manual preset sentinel**

Change:

```tsx
// ── Cron presets ──────────────────────────────────────────────────────────────
const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'Choose a preset…', value: '' },
  { label: 'Every hour',          value: '0 * * * *' },
  { label: 'Every 2 hours',       value: '0 */2 * * *' },
  { label: 'Every 6 hours',       value: '0 */6 * * *' },
  { label: 'Daily at 9 AM',       value: '0 9 * * *' },
  { label: 'Daily at noon',       value: '0 12 * * *' },
  { label: 'Daily at midnight',   value: '0 0 * * *' },
  { label: 'Weekdays at 9 AM',    value: '0 9 * * 1-5' },
  { label: 'Weekly on Monday',    value: '0 9 * * 1' },
  { label: 'Monthly on the 1st',  value: '0 0 1 * *' },
];
```

to:

```tsx
// ── Cron presets ──────────────────────────────────────────────────────────────
const MANUAL_PRESET = '__manual__';

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'Choose a preset…', value: '' },
  { label: 'Manual (run on demand)', value: MANUAL_PRESET },
  { label: 'Every hour',          value: '0 * * * *' },
  { label: 'Every 2 hours',       value: '0 */2 * * *' },
  { label: 'Every 6 hours',       value: '0 */6 * * *' },
  { label: 'Daily at 9 AM',       value: '0 9 * * *' },
  { label: 'Daily at noon',       value: '0 12 * * *' },
  { label: 'Daily at midnight',   value: '0 0 * * *' },
  { label: 'Weekdays at 9 AM',    value: '0 9 * * 1-5' },
  { label: 'Weekly on Monday',    value: '0 9 * * 1' },
  { label: 'Monthly on the 1st',  value: '0 0 1 * *' },
];
```

- [ ] **Step 2: Initialize `preset` from `initial.cron` and add `concurrency` state**

Change:

```tsx
  const [preset,       setPreset]       = useState('');
```

to:

```tsx
  const [preset,       setPreset]       = useState(initial && !initial.cron ? MANUAL_PRESET : '');
```

Change:

```tsx
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
```

to:

```tsx
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [concurrency, setConcurrency] = useState<'skip' | 'kill'>(
    (initial?.concurrency as 'skip' | 'kill') ?? 'skip',
  );
```

- [ ] **Step 3: Update `handlePreset` to clear cron on manual selection**

Change:

```tsx
  function handlePreset(value: string) {
    setPreset(value);
    if (value) setCron(value);
  }
```

to:

```tsx
  function handlePreset(value: string) {
    setPreset(value);
    if (value === MANUAL_PRESET) {
      setCron('');
    } else if (value) {
      setCron(value);
    }
  }
```

- [ ] **Step 4: Send `cron: null` and `concurrency` on save**

Change:

```tsx
      const body = {
        name,
        cron,
        prompt,
        instructions_file: instructionsFile || undefined,
        workdir,
        mode:    MODE_TO_API[mode],
        model:   MODEL_TO_API[model],
        enabled,
      };
```

to:

```tsx
      const body = {
        name,
        cron: preset === MANUAL_PRESET ? null : cron,
        prompt,
        instructions_file: instructionsFile || undefined,
        workdir,
        mode:    MODE_TO_API[mode],
        model:   MODEL_TO_API[model],
        concurrency,
        enabled,
      };
```

- [ ] **Step 5: Hide the cron text input when Manual is selected**

Change:

```tsx
          {/* Cron + preset */}
          <div>
            <label style={labelStyle}>Cron expression</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={cron}
                onChange={(e) => { setCron(e.target.value); setPreset(''); }}
                placeholder="0 9 * * *"
                style={{ ...fieldStyle, flex: 1 }}
              />
              <select
                value={preset}
                onChange={(e) => handlePreset(e.target.value)}
                style={{
                  ...fieldStyle,
                  width: 'auto',
                  flex: 'none',
                  cursor: 'pointer',
                  fontSize: 12,
                  paddingRight: 6,
                }}
              >
                {CRON_PRESETS.map((p) => (
                  <option key={p.value || '__placeholder'} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>
```

to:

```tsx
          {/* Cron + preset */}
          <div>
            <label style={labelStyle}>Cron expression</label>
            <div style={{ display: 'flex', gap: 6 }}>
              {preset !== MANUAL_PRESET && (
                <input
                  value={cron}
                  onChange={(e) => { setCron(e.target.value); setPreset(''); }}
                  placeholder="0 9 * * *"
                  style={{ ...fieldStyle, flex: 1 }}
                />
              )}
              <select
                value={preset}
                onChange={(e) => handlePreset(e.target.value)}
                style={{
                  ...fieldStyle,
                  width: preset === MANUAL_PRESET ? '100%' : 'auto',
                  flex: preset === MANUAL_PRESET ? 1 : 'none',
                  cursor: 'pointer',
                  fontSize: 12,
                  paddingRight: 6,
                }}
              >
                {CRON_PRESETS.map((p) => (
                  <option key={p.value || '__placeholder'} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            {preset === MANUAL_PRESET && (
              <div style={{ fontSize: 11, color: RT.textLow, marginTop: 5, fontFamily: FONT_MONO }}>
                Manual task - runs only when triggered with "Run now".
              </div>
            )}
          </div>
```

- [ ] **Step 6: Add the concurrency select below Mode/Model**

Change:

```tsx
          {/* Mode + Model (side by side) */}
          <div style={{ display: 'flex', gap: 10 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Mode</label>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as ModeKey)}
                style={{ ...fieldStyle, cursor: 'pointer' }}
              >
                <option value="STANDARD">STANDARD</option>
                <option value="TEAMMATE">TEAMMATE</option>
                <option value="SAFE">SAFE</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Model</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value as ModelKey)}
                style={{ ...fieldStyle, cursor: 'pointer' }}
              >
                <option value="DEFAULT">Default (Opus 4.8)</option>
                <option value="SONNET">Sonnet 5</option>
                <option value="HAIKU">Haiku 4.5</option>
                <option value="FABLE">Fable 5</option>
              </select>
            </div>
          </div>
```

to:

```tsx
          {/* Mode + Model (side by side) */}
          <div style={{ display: 'flex', gap: 10 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Mode</label>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as ModeKey)}
                style={{ ...fieldStyle, cursor: 'pointer' }}
              >
                <option value="STANDARD">STANDARD</option>
                <option value="TEAMMATE">TEAMMATE</option>
                <option value="SAFE">SAFE</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Model</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value as ModelKey)}
                style={{ ...fieldStyle, cursor: 'pointer' }}
              >
                <option value="DEFAULT">Default (Opus 4.8)</option>
                <option value="SONNET">Sonnet 5</option>
                <option value="HAIKU">Haiku 4.5</option>
                <option value="FABLE">Fable 5</option>
              </select>
            </div>
          </div>

          {/* Concurrency */}
          <div>
            <label style={labelStyle}>If already running</label>
            <select
              value={concurrency}
              onChange={(e) => setConcurrency(e.target.value as 'skip' | 'kill')}
              style={{ ...fieldStyle, cursor: 'pointer' }}
            >
              <option value="skip">Skip this run</option>
              <option value="kill">Kill the running one, then start</option>
            </select>
          </div>
```

- [ ] **Step 7: Commit**

Same note as Task 23 - no build here, Task 26 builds everything at once.

```bash
git add frontend/src/components/ScheduleModal.tsx
git commit -m "feat(frontend): manual task preset and concurrency select in ScheduleModal"
```

---

### Task 25: "Tasks" label and manual-row display across the schedule lists

**Files:**
- Modify: `frontend/src/components/MobileNav.tsx`, `frontend/src/components/AllScheduled.tsx`, `frontend/src/components/ScheduledRow.tsx`

Note on scope: only the mobile cross-device tab label (`MobileNav.tsx`) and the cross-device list's own title (`AllScheduled.tsx`) are renamed to "Tasks", matching exactly what was asked. The desktop per-device tab bar (`PanelTabs.tsx`, which still says "Scheduled") is intentionally left unchanged - it was not named in the request, and touching it would be scope creep beyond a 2-5 minute task.

- [ ] **Step 1: Rename the mobile tab label**

In `frontend/src/components/MobileNav.tsx`, change:

```tsx
    { id: 'scheduled', label: 'Scheduled', icon: Icons.clock,    count: counts.scheduled },
```

to:

```tsx
    { id: 'scheduled', label: 'Tasks',     icon: Icons.clock,    count: counts.scheduled },
```

- [ ] **Step 2: Rename the `AllScheduled.tsx` header and show "manual" for null-cron rows**

In `frontend/src/components/AllScheduled.tsx`, change:

```tsx
      <MobileHeader
        subtitle={`${items.length} task${items.length !== 1 ? 's' : ''} across devices`}
        title="Scheduled"
```

to:

```tsx
      <MobileHeader
        subtitle={`${items.length} task${items.length !== 1 ? 's' : ''} across devices`}
        title="Tasks"
```

Change:

```tsx
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>
                <Icons.clock size={10} stroke={RT.textLow} />
                <span>{s.cron}</span>
                {s.schedule_label && <span style={{ color: RT.borderHi }}>({s.schedule_label})</span>}
              </div>
```

to:

```tsx
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_MONO, fontSize: 11, color: RT.textLow }}>
                <Icons.clock size={10} stroke={RT.textLow} />
                <span>{s.cron || 'manual'}</span>
                {s.schedule_label && <span style={{ color: RT.borderHi }}>({s.schedule_label})</span>}
              </div>
```

- [ ] **Step 3: Show "manual" for null-cron rows in the per-device list**

In `frontend/src/components/ScheduledRow.tsx`, change:

```tsx
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icons.clock size={10} stroke={RT.textLow} /> {s.cron}
          </span>
```

to:

```tsx
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icons.clock size={10} stroke={RT.textLow} /> {s.cron || 'manual'}
          </span>
```

The existing "Run now" button (`V5IconButton` calling `handleFire`) is untouched - it already works for any schedule regardless of cron.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MobileNav.tsx frontend/src/components/AllScheduled.tsx frontend/src/components/ScheduledRow.tsx
git commit -m "feat(frontend): rename Scheduled to Tasks, show manual for on-demand tasks"
```

---

### Task 26: Frontend build and commit `static/dist`

**Files:**
- Modify: `static/dist/assets/*` (regenerated), `static/dist/index.html` (regenerated)

This is the single point where the frontend source changes from Tasks 23-25 (and the `ScheduleModal.tsx` identifier fix from Task 19) actually take effect for users, since `/` serves the prebuilt `static/dist`, not the TSX source.

- [ ] **Step 1: Install frontend dependencies if needed**

`frontend/node_modules` is absent in this checkout (confirmed 2026-09-06). Run:

```bash
cd /var/www/rc-launcher-v3/frontend
[ -d node_modules ] || npm ci
```

This is the one place in this plan that installs packages - it is required for `npm run build` (which runs `tsc -b && vite build`) to have a compiler and bundler available at all, and `npm ci` installs exactly what `package-lock.json` already pins (no new dependencies, no lockfile changes expected).

- [ ] **Step 2: Build**

```bash
npm run build
```

Expected: `tsc -b` reports no type errors, `vite build` completes and (re)writes `static/dist/index.html` and `static/dist/assets/*`. If `tsc -b` reports an error, it will point at the exact file:line in whichever of Tasks 19/23/24/25 introduced it - fix it there, not by loosening types.

- [ ] **Step 3: Verify the identifier fix made it into the compiled bundle**

Run: `cd /var/www/rc-launcher-v3 && grep -c '/root/.claude-rc' static/dist/assets/*.js`
Expected: `0` (the placeholder string fixed in Task 19 no longer appears in the freshly built bundle; the old bundle had exactly one occurrence).

- [ ] **Step 4: Run the Python suite once more (frontend changes should not affect it) and commit**

Run: `cd /var/www/rc-launcher-v3 && python3 -m unittest discover tests`
Expected: `OK`.

```bash
cd /var/www/rc-launcher-v3
git add static/dist
git status  # confirm frontend/package-lock.json did not change; if it did, add it too
git commit -m "chore: rebuild static/dist with manual tasks, concurrency, and Tasks label"
```

---

## Self-Review Notes

- **Spec coverage:** every bullet in the operator's 8-item scope list maps to at least one task above (schedules.py hardening -> 1-5; manual task kind -> 6-8; concurrency -> 9-10; server.py security -> 11-15; `/update` hardening -> 16; `RC_MAX_SESSIONS` -> 17; repo hygiene/CI -> 18-22; frontend -> 23-26).
- **Ambiguities resolved (see also the final report to the operator):** (a) `load_schedules` validation drops individually-invalid entries rather than nuking the whole file to `[]`, since that is strictly more resilient and still surfaces `LAST_LOAD_ERROR`; (b) `docker-compose.yml`'s `/root/.claude` container path and the public `barjakuzu` GitHub identity are excluded from the personal-identifier sweep and its CI check, since neither is actually personal; (c) the concurrency select lives in `ScheduleModal.tsx` (the actual edit form `ScheduledRow.tsx`'s Edit button opens), not in `ScheduledRow.tsx` itself; (d) `PanelTabs.tsx`'s desktop "Scheduled" tab is left unrenamed since only the mobile tab and `AllScheduled.tsx` were named in scope; (e) `queue` concurrency is explicitly out of scope (deferred to Tasks v2) - only `skip`/`kill` are implemented.
- **Type consistency:** `_active_scheduled_sessions` is keyed by schedule id everywhere after Task 10 (both `_fire_schedule` and `_monitor_scheduled_sessions`); `Schedule.cron` is `string | null` everywhere in the frontend after Task 23; `concurrency` is `"skip" | "kill"` consistently across `schedules.py`, `scheduler.py`, and the frontend.
