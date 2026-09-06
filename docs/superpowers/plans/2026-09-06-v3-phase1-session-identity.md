# RC Launcher v3 Phase 1: Session Identity on Native Rails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop faking session identity and Remote Control activation with keystroke scraping. Every launcher-started session gets a pre-assigned UUID and display name from `claude` itself, Remote Control is turned on at launch instead of typed in, and the launcher discovers sessions Claude Code already knows about (`claude agents --json`) instead of only ones it started.

**Architecture:** Additive, feature-gated changes inside the existing single-process stdlib Python backend. A new `compat.py` probes the installed `claude` binary once at startup and exposes a `CAPS` dict; every other module consults `CAPS` and falls back to the old keystroke-based path when a capability is missing (older `claude`, or a device that hasn't upgraded yet). A new `agents.py` wraps `claude agents --json` and merges its rows with the existing tmux-session view. No new services, no new runtime dependencies, no database.

**Tech Stack:** Python 3.9+ stdlib (`subprocess`, `json`, `re`, `shutil`), tmux, React 18 + TypeScript + Vite (frontend, prebuilt to `static/dist` and committed).

**Spec:** External planning note from the 2026-09-06 planning session (not tracked in this repo, and not referenced by path here, because it is a private document living outside the repo under the operator's home directory — copying that path into a tracked file would itself violate Global Constraint 3 below). This plan is self-contained: every requirement it implements is captured in the Global Constraints and the 20 tasks below (17 core tasks plus 3 frontend tasks added after a live QA pass on v2.1.3, folded in as Tasks 15-17). A matching operator-facing checklist for the box-side steps that are explicitly NOT part of this repo lives in the private Obsidian vault, mirroring Phase 0's convention. Facts about Claude Code 2.1.263 used throughout (verified on this box, not re-derived): `claude --session-id <uuid>` pre-assigns the session UUID; `-n/--name <name>` sets the display name (prompt box, `/resume` picker, terminal title; also names the Remote Control session); `claude --remote-control [name]` (alias `--rc`) starts an interactive session with Remote Control already enabled and named; `claude agents --json` prints a JSON array of live sessions on the device with fields `pid, cwd, kind (interactive|background), startedAt (epoch ms), sessionId, name, status (idle|busy)`, plus `state, waitingFor` for background sessions — no TTY needed, spawns a Node process, so it should not be polled faster than every 30s; the transcript lives at `~/.claude/projects/<cwd with / replaced by ->/<uuid>.jsonl` (and, from directly inspecting `~/.claude/projects` on this box, `.` in the cwd is *also* replaced by `-`: `~/.claude-rc` encodes as `-home-user--claude-rc` for a home directory of `/home/user`); `--max-budget-usd` is print-mode only; `--permission-mode` accepts `acceptEdits|auto|bypassPermissions|manual|dontAsk|plan`.

## Global Constraints

- Python 3.9+ stdlib only at runtime. No new pip dependencies. Use `Optional[X]` (with `from typing import Optional`), never a bare runtime `X | None` union — `stats.py`'s `from __future__ import annotations` trick is not to be relied on in new modules; import `Optional` explicitly instead.
- Every file write under `~/.claude-rc` is atomic (temp file in the same directory + `os.replace`) and mode `0600`.
- No personal hostnames, tailnet names, IPs, or `/root/...` paths anywhere in tracked files. Use `~/.claude-rc` and generic placeholders (e.g. `alice`, `/home/user/...`, `example.com`).
- All subprocess calls stay argv lists. Never `shell=True`.
- Existing tests keep passing: baseline is `python3 -m unittest discover tests` -> `Ran 134 tests ... OK` (recorded 2026-09-06 on branch `v3-phase1` at the tip of this worktree, `/var/www/rc-launcher-p1`).
- Each task ends with a commit on `v3-phase1`.
- Nothing in this plan restarts services, touches `~/.claude-rc` on this box, `/var/www/rc-launcher`, or pushes to a remote. All work happens inside this worktree (`/var/www/rc-launcher-p1`) only.

---

### Task 0a: `/update` error key rename and clearer git-timeout message

**Files:**
- Modify: `server.py:486` (`_do_git_update_phase`)
- Test: `tests/test_server_helpers.py` (extend `GitUpdatePhaseTest`)

**Interfaces:**
- Produces: `server._do_git_update_phase(app_dir, confirm, run=subprocess.run)` keeps its `(status, result_dict, merged_sha)` return shape; on a `TimeoutExpired`/`OSError` the dict now uses key `message` (matching every other branch in this function, and matching what the frontend renders — `message`, not `error`) with text `"git failed: <reason>"`.

- [ ] **Step 1: Write the failing tests**

Replace the two existing timeout/OSError tests in `tests/test_server_helpers.py`'s `GitUpdatePhaseTest` (currently asserting `{"ok": False, "error": "git timed out"}`) with:

```python
    def test_hung_fetch_times_out_cleanly(self):
        import subprocess as sp

        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

        status, result, merged_sha = server._do_git_update_phase(
            "/some/app-dir", "confirm-sha", run=fake_run)

        self.assertEqual(status, 500)
        self.assertEqual(result["ok"], False)
        self.assertIn("message", result)
        self.assertNotIn("error", result)
        self.assertTrue(result["message"].startswith("git failed:"))
        self.assertIsNone(merged_sha)

    def test_missing_git_binary_returns_error_not_exception(self):
        def fake_run(cmd, **kw):
            raise OSError("git not found")

        status, result, merged_sha = server._do_git_update_phase(
            "/some/app-dir", "confirm-sha", run=fake_run)

        self.assertEqual(status, 500)
        self.assertEqual(result["ok"], False)
        self.assertIn("message", result)
        self.assertIn("git not found", result["message"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.GitUpdatePhaseTest -v`
Expected: FAIL — `KeyError: 'message'` (or the old `error` key is present instead).

- [ ] **Step 3: Fix the implementation**

In `server.py`, change the `except` clause of `_do_git_update_phase` (currently `server.py:485-486`):

```python
    except (subprocess.TimeoutExpired, OSError) as e:
        return 500, {"ok": False, "message": f"git failed: {e}"}, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers.GitUpdatePhaseTest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_helpers.py
git commit -m "fix: /update git-timeout response uses 'message' key like every other branch"
```

---

### Task 0b: remove unused `merged_sha` binding in the `/update` handler

**Files:**
- Modify: `server.py` (`/update` branch of `do_POST`, around `server.py:1302-1324`)

**Interfaces:**
- Consumes: `server._do_git_update_phase` (Task 0a's signature, unchanged shape).
- No new interfaces produced — this is a dead-code trim only.

- [ ] **Step 1: Confirm it is actually unused**

Run: `grep -n "merged_sha" server.py`
Expected: two hits — the `_do_git_update_phase` definition/return, and the unpacking line `status, result, merged_sha = _do_git_update_phase(...)` inside the `/update` POST branch. Nothing after that unpacking line in the branch references `merged_sha`.

- [ ] **Step 2: Trim the binding**

In the `/update` POST branch (`server.py`, inside `elif path == "/update":`), change:

```python
            status, result, merged_sha = _do_git_update_phase(app_dir, body.get("confirm", ""))
```

to:

```python
            status, result, _merged_sha = _do_git_update_phase(app_dir, body.get("confirm", ""))
```

(Keep the 3-tuple unpacking — `_do_git_update_phase`'s return shape is exercised directly by `GitUpdatePhaseTest` and other callers may be added later — just rename the unused local to the underscore-prefixed convention already used elsewhere in this codebase for intentionally-unused values.)

- [ ] **Step 3: Run the full suite to confirm nothing broke**

Run: `python3 -m unittest discover tests`
Expected: `Ran 134 tests ... OK`

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "chore: rename unused merged_sha local in /update handler"
```

---

### Task 0c: legacy `rc-sched-<safe_name>-*` adoption tries the longest safe_name first

**Files:**
- Modify: `scheduler.py:425-491` (`_adopt_live_sessions`)
- Test: `tests/test_scheduler.py` (extend `AdoptLiveSessionsTest`)

**Interfaces:**
- Produces: `scheduler._adopt_live_sessions(schedules=None) -> int` (unchanged signature/return); the matching loop over `by_safe_name` for `rc-sched-<safe>-*` names now visits longer `safe` values first, so a schedule named `deploy` does not shadow one named `deploy-prod` when both prefixes match the same legacy session name.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scheduler.py`'s `AdoptLiveSessionsTest` class (check the existing class first with `grep -n "class AdoptLiveSessionsTest" -A5 tests/test_scheduler.py` for the exact fixture/patching style already in use — mirror it):

```python
    def test_prefers_longest_matching_safe_name(self):
        # Two schedules whose sanitized names are prefixes of one another.
        # A legacy tmux session "rc-sched-deploy-prod-0906-1200" must adopt
        # into "deploy-prod", not "deploy" (the shorter, earlier-seen match).
        schedules = [
            {"id": "short-id", "name": "deploy"},
            {"id": "long-id", "name": "deploy-prod"},
        ]
        sessions = [{"name": "rc-sched-deploy-prod-0906-1200", "mode": "c",
                     "url": None, "status": "running"}]
        self._patch_list_rc_sessions(sessions)  # match this test file's existing patch helper
        self._patch_session_env({})             # RC_SCHEDULE_ID irrelevant for legacy names

        adopted = scheduler._adopt_live_sessions(schedules=schedules)

        self.assertEqual(adopted, 1)
        with scheduler._active_scheduled_sessions_lock:
            self.assertIn("long-id", scheduler._active_scheduled_sessions)
            self.assertNotIn("short-id", scheduler._active_scheduled_sessions)
```

(If `AdoptLiveSessionsTest` does not already expose `_patch_list_rc_sessions`/`_patch_session_env` helpers, read its `setUp`/existing test bodies and inline the same monkeypatching pattern they use for `scheduler.list_rc_sessions` and `scheduler.get_session_env` instead of inventing new helper names.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_scheduler.AdoptLiveSessionsTest.test_prefers_longest_matching_safe_name -v`
Expected: FAIL — the shorter `deploy` schedule wins because dict iteration order (insertion order) is tried first and `remainder.startswith(safe + "-")` matches it before `deploy-prod`.

- [ ] **Step 3: Fix the implementation**

In `scheduler.py`, inside `_adopt_live_sessions`, change the `elif rest.startswith("sched-"):` block (`scheduler.py:466-472`) from:

```python
        elif rest.startswith("sched-"):
            remainder = rest[len("sched-"):]
            for safe, candidate in by_safe_name.items():
                if remainder == safe or remainder.startswith(safe + "-"):
                    schedule = candidate
                    safe_name = safe
                    break
```

to:

```python
        elif rest.startswith("sched-"):
            remainder = rest[len("sched-"):]
            for safe, candidate in sorted(by_safe_name.items(),
                                           key=lambda kv: len(kv[0]), reverse=True):
                if remainder == safe or remainder.startswith(safe + "-"):
                    schedule = candidate
                    safe_name = safe
                    break
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_scheduler -v`
Expected: all `AdoptLiveSessionsTest` tests PASS, including the new one.

- [ ] **Step 5: Commit**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "fix: legacy rc-sched adoption prefers the longest matching schedule name"
```

---

### Task 1: `compat.py` — capability detection for the installed `claude`

**Files:**
- Create: `compat.py`
- Create: `tests/fakes/claude` (executable fake, new-style capabilities)
- Create: `tests/fakes/claude_old` (executable fake, old-style — no `--session-id`/`--name`/`--remote-control`, and `agents` subcommand fails)
- Test: `tests/test_compat.py` (new)

**Interfaces:**
- Produces:
  - `compat.detect_caps(claude_bin: str, run=subprocess.run) -> dict` — pure-ish function (subprocess injectable), does the actual probing. Returns a dict with exactly these keys, all bool except `version`:
    `{"session_id_flag": bool, "name_flag": bool, "remote_control_flag": bool, "permission_mode_flag": bool, "agents_json": bool, "version": Optional[str]}`
  - `compat.CAPS: dict` — module-level, computed once at import time by calling `detect_caps(config.CLAUDE_BIN)` and cached in memory (a plain module global, no explicit lock needed — it is written once at import and read-only after).
  - `compat.claude_version() -> Optional[str]` — returns `CAPS["version"]`.
  - `compat.refresh_caps(claude_bin=None) -> dict` — re-runs detection and updates `CAPS` in place (for tests; production code never needs to call this after import).
- Consumes: `config.CLAUDE_BIN` (existing, `config.py:14`).

- [ ] **Step 1: Write the fake `claude` binaries**

Create `tests/fakes/claude` (new-style, everything supported):

```bash
#!/bin/bash
# Fake `claude` for compat.py tests: supports --session-id, -n/--name,
# --remote-control, --permission-mode, and `claude agents --json`.
if [ "$1" = "--help" ]; then
  cat <<'EOF'
Usage: claude [options] [command] [prompt]
Options:
  -n, --name <name>                     Set a display name for this session
  --permission-mode <mode>              Permission mode to use for the session
  --remote-control [name]               Start an interactive session with Remote
                                        Control enabled (optionally named)
  --session-id <uuid>                   Use a specific session ID for the
                                        conversation (must be a valid UUID)
EOF
  exit 0
fi
if [ "$1" = "agents" ] && [ "$2" = "--json" ]; then
  echo '[]'
  exit 0
fi
if [ "$1" = "--version" ]; then
  echo "2.1.263 (Claude Code)"
  exit 0
fi
exit 0
```

Make it executable: `chmod +x tests/fakes/claude`

Create `tests/fakes/claude_old` (pre-v3, none of the new flags, `agents` unsupported):

```bash
#!/bin/bash
# Fake `claude` for compat.py tests: an old build with none of the v3
# session-identity flags and no `agents` subcommand.
if [ "$1" = "--help" ]; then
  cat <<'EOF'
Usage: claude [options] [command] [prompt]
Options:
  -c, --continue                        Continue the most recent conversation
  --dangerously-skip-permissions        Bypass all permission checks.
EOF
  exit 0
fi
if [ "$1" = "agents" ]; then
  echo "error: unknown command 'agents'" >&2
  exit 1
fi
if [ "$1" = "--version" ]; then
  echo "1.9.0 (Claude Code)"
  exit 0
fi
exit 0
```

Make it executable: `chmod +x tests/fakes/claude_old`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_compat.py`:

```python
"""compat.py: feature-detect the installed `claude` binary from --help
output plus a live `agents --json` probe, once, cached in CAPS."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat

FAKES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakes")
NEW_CLAUDE = os.path.join(FAKES_DIR, "claude")
OLD_CLAUDE = os.path.join(FAKES_DIR, "claude_old")


class DetectCapsNewClaudeTest(unittest.TestCase):
    def test_all_flags_detected(self):
        caps = compat.detect_caps(NEW_CLAUDE)
        self.assertTrue(caps["session_id_flag"])
        self.assertTrue(caps["name_flag"])
        self.assertTrue(caps["remote_control_flag"])
        self.assertTrue(caps["permission_mode_flag"])
        self.assertTrue(caps["agents_json"])
        self.assertEqual(caps["version"], "2.1.263")


class DetectCapsOldClaudeTest(unittest.TestCase):
    def test_no_flags_detected(self):
        caps = compat.detect_caps(OLD_CLAUDE)
        self.assertFalse(caps["session_id_flag"])
        self.assertFalse(caps["name_flag"])
        self.assertFalse(caps["remote_control_flag"])
        self.assertFalse(caps["permission_mode_flag"])
        self.assertFalse(caps["agents_json"])
        self.assertEqual(caps["version"], "1.9.0")


class DetectCapsMissingBinaryTest(unittest.TestCase):
    def test_missing_binary_returns_all_false(self):
        caps = compat.detect_caps("/no/such/claude/binary")
        self.assertFalse(caps["session_id_flag"])
        self.assertFalse(caps["agents_json"])
        self.assertIsNone(caps["version"])


class ClaudeVersionTest(unittest.TestCase):
    def test_reads_from_caps(self):
        compat.refresh_caps(NEW_CLAUDE)
        self.assertEqual(compat.claude_version(), "2.1.263")
        compat.refresh_caps(OLD_CLAUDE)
        self.assertEqual(compat.claude_version(), "1.9.0")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_compat -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compat'`

- [ ] **Step 4: Write `compat.py`**

```python
"""Feature-detects the installed `claude` binary once at startup.

Older Claude Code builds lack --session-id, -n/--name, --remote-control,
--permission-mode, and `claude agents --json`. Every module that wants one
of those behaviors checks CAPS first and falls back to the pre-v3
keystroke-based path when the flag isn't there — this is the single place
that knows how to tell the difference, so an upgrade or downgrade of the
`claude` binary can't leave two modules disagreeing about what's supported.
"""
import re
import subprocess
from typing import Optional

VERSION_RE = re.compile(r'(\d+\.\d+\.\d+)')


def detect_caps(claude_bin, run=subprocess.run):
    """Probe one `claude` binary for the v3 session-identity flags.

    Parses `claude --help` for flag names (cheap, no session created) and
    separately probes `claude agents --json` (also cheap: it lists live
    sessions, it does not start one). Any failure to run the binary at all
    (missing, not executable, times out) yields every flag False and
    version None rather than raising — callers must be able to trust CAPS
    even when claude isn't installed yet.
    """
    caps = {
        "session_id_flag": False,
        "name_flag": False,
        "remote_control_flag": False,
        "permission_mode_flag": False,
        "agents_json": False,
        "version": None,
    }
    help_text = _run_capture(run, [claude_bin, "--help"])
    if help_text is not None:
        caps["session_id_flag"] = "--session-id" in help_text
        caps["name_flag"] = bool(re.search(r'(^|\s)(-n,\s*)?--name(\s|,|<)', help_text, re.M))
        caps["remote_control_flag"] = "--remote-control" in help_text
        caps["permission_mode_flag"] = "--permission-mode" in help_text

    agents_out = _run_capture(run, [claude_bin, "agents", "--json"])
    if agents_out is not None:
        try:
            import json
            parsed = json.loads(agents_out)
            caps["agents_json"] = isinstance(parsed, list)
        except (ValueError, TypeError):
            caps["agents_json"] = False

    version_out = _run_capture(run, [claude_bin, "--version"])
    if version_out is not None:
        m = VERSION_RE.search(version_out)
        if m:
            caps["version"] = m.group(1)

    return caps


def _run_capture(run, cmd, timeout=10):
    """Run one probe command, returning stdout text or None on any failure
    (missing binary, non-zero exit for --help/--version, timeout)."""
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 and "agents" not in cmd:
        # --help/--version failing outright means this isn't a usable binary.
        return None
    return r.stdout


def refresh_caps(claude_bin=None):
    """Re-run detection and update the module-level CAPS in place. Tests
    use this to swap in a fake binary; production code calls it once,
    implicitly, at import time below."""
    global CAPS
    from config import CLAUDE_BIN
    CAPS = detect_caps(claude_bin or CLAUDE_BIN)
    return CAPS


def claude_version():
    return CAPS.get("version")


from config import CLAUDE_BIN as _CLAUDE_BIN  # noqa: E402  (after refresh_caps def, before use)
CAPS = detect_caps(_CLAUDE_BIN)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_compat -v`
Expected: PASS. Then run the full suite to make sure importing `compat` (which shells out once) doesn't break anything: `python3 -m unittest discover tests` -> `Ran 138 tests ... OK` (134 baseline + 4 new).

- [ ] **Step 6: Wire `claude_version` and `caps` into `GET /version` and `/stats`**

Read `server.py:899-902` (`/version`) and the `/stats` branch below it (`server.py:902` onward, and `stats.system_stats()`'s call site) to find the exact response-building code. Add `import compat` to `server.py`'s import block near the top (alongside the existing `import stats`). Change the `/version` branch:

```python
        elif path == "/version":
            self._json({"version": VERSION, "claude_version": compat.claude_version(),
                        "caps": compat.CAPS})
```

For `/stats`, find where the response dict is assembled (it calls `stats.system_stats()` and merges in token history — read the exact lines first with `sed -n '900,918p' server.py`) and add the same two keys to that dict: `"claude_version": compat.claude_version()` and `"caps": compat.CAPS`.

- [ ] **Step 7: Write a test for the new response fields**

Add to `tests/test_server_helpers.py` (find the existing pattern for testing response-building helpers — if `/version`'s body is built inline in `do_GET` with no extracted helper, add a small one so it's testable without a live socket):

```python
class VersionResponseTest(unittest.TestCase):
    def test_includes_claude_version_and_caps(self):
        import compat
        body = server._version_response()
        self.assertEqual(body["version"], server.VERSION)
        self.assertIn("claude_version", body)
        self.assertEqual(body["caps"], compat.CAPS)
```

Extract the body-building into `server._version_response()` (returns the dict; the `/version` handler becomes `self._json(_version_response())`) so the test above works without instantiating the HTTP handler. Same treatment for `/stats` is optional here — Task 1 only requires `/version` and `/stats` to *include* the fields; if `/stats`'s response-building is already behind a testable helper (check for one before adding a new one), extend that instead of inlining.

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 139 tests ... OK`

- [ ] **Step 9: Commit**

```bash
git add compat.py tests/fakes/claude tests/fakes/claude_old tests/test_compat.py server.py tests/test_server_helpers.py
git commit -m "feat: compat.py detects claude binary capabilities, exposed via /version and /stats"
```

---

### Task 2: `permission_mode` replaces `RC_FLAGS`'s hardcoded flag strings

**Files:**
- Modify: `config.py:37-52` (`RC_FLAGS`, `resolve_claude_mode`)
- Test: `tests/test_shell_mode.py` or a new `tests/test_config.py` — check `grep -rn "RC_FLAGS" tests/` first and extend whichever file already imports `config.RC_FLAGS`

**Interfaces:**
- Produces: `config.PERMISSION_MODE = {"c": "bypassPermissions", "ci": "bypassPermissions", "safe": "acceptEdits"}` (new dict, one entry per non-shell mode key already in `RC_FLAGS`). `config.EXTRA_FLAGS = {"ci": ["--teammate-mode", "in-process"]}` (new dict, mode -> list of extra argv tokens beyond `--permission-mode`, empty for modes with none). `config.RC_FLAGS` is **kept**, unchanged in value, purely so any external caller (e.g. `mcp_server.py`, or a device on an older Phase-0 build during a rolling upgrade) that still reads it as a string keeps working; nothing in this repo constructs the launch command from `RC_FLAGS` after this task lands — `RC_FLAGS`-consuming call sites move to `PERMISSION_MODE`/`EXTRA_FLAGS` in Task 3.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Run `grep -rn "RC_FLAGS" tests/` first to find the existing test file(s) that import it (likely `tests/test_shell_mode.py`, since `RC_FLAGS[SHELL_MODE]` is the sentinel that keeps `mode not in RC_FLAGS` valid for shell mode). Add to that file (or create `tests/test_config.py` if none reference it directly):

```python
class PermissionModeMappingTest(unittest.TestCase):
    def test_every_claude_mode_has_a_permission_mode(self):
        for mode in ("c", "ci", "safe"):
            self.assertIn(mode, config.PERMISSION_MODE)

    def test_c_and_ci_bypass_safe_accepts_edits(self):
        self.assertEqual(config.PERMISSION_MODE["c"], "bypassPermissions")
        self.assertEqual(config.PERMISSION_MODE["ci"], "bypassPermissions")
        self.assertEqual(config.PERMISSION_MODE["safe"], "acceptEdits")

    def test_ci_carries_teammate_mode_extra_flag(self):
        self.assertEqual(config.EXTRA_FLAGS.get("ci"), ["--teammate-mode", "in-process"])
        self.assertEqual(config.EXTRA_FLAGS.get("c", []), [])
        self.assertEqual(config.EXTRA_FLAGS.get("safe", []), [])

    def test_shell_mode_has_no_permission_mode(self):
        self.assertNotIn(config.SHELL_MODE, config.PERMISSION_MODE)

    def test_rc_flags_still_present_for_back_compat(self):
        # Old callers (e.g. a rolling upgrade of a device still on Phase 0)
        # that read RC_FLAGS as a string must keep working.
        self.assertEqual(config.RC_FLAGS["c"], "--dangerously-skip-permissions --verbose")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_shell_mode -v` (or `tests.test_config`, whichever file you added the class to)
Expected: FAIL — `AttributeError: module 'config' has no attribute 'PERMISSION_MODE'`

- [ ] **Step 3: Add the mapping to `config.py`**

After the existing `RC_FLAGS` dict (`config.py:37-42`), add:

```python
# Permission mode per launch, replacing the RC_FLAGS strings as the source
# of truth for what sessions.build_tmux_command actually passes. RC_FLAGS
# itself stays defined above, unchanged, for any external caller still
# reading it as a flag string during a rolling upgrade.
PERMISSION_MODE = {
    "c": "bypassPermissions",
    "ci": "bypassPermissions",
    "safe": "acceptEdits",
}

# Extra argv tokens per mode beyond --permission-mode, in the same style
# tmux command lists use everywhere else in this codebase (no shell
# quoting needed — these become individual argv entries).
EXTRA_FLAGS = {
    "ci": ["--teammate-mode", "in-process"],
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 144 tests ... OK` (139 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_shell_mode.py  # or tests/test_config.py, whichever you edited
git commit -m "feat: config.PERMISSION_MODE and EXTRA_FLAGS, RC_FLAGS kept for back-compat"
```

---

### Task 3: `build_tmux_command` uses native flags when `compat.CAPS` allows

**Files:**
- Modify: `sessions.py:28-70` (`build_tmux_command`)
- Test: `tests/test_setup_session.py` (new test class, or a new `tests/test_build_tmux_command.py` — check whether `build_tmux_command` already has dedicated tests with `grep -rln "build_tmux_command" tests/` and extend that file if one exists)

**Interfaces:**
- Consumes: `compat.CAPS` (Task 1), `config.PERMISSION_MODE`, `config.EXTRA_FLAGS`, `config.RC_FLAGS` (Task 2, all present).
- Produces: `sessions.build_tmux_command(name, session_dir, mode, model=None, sandbox=False, resume=False, resume_id=None, resume_search=None, session_id=None)` — **one new keyword arg, `session_id`**, defaulting to `None`. When `session_id` is given, `mode != SHELL_MODE`, and `compat.CAPS["session_id_flag"]` is true, the built argv includes `--session-id <session_id>`. When `mode != SHELL_MODE` and `compat.CAPS["name_flag"]` is true, the argv includes `-n <display_name>` where `display_name` is `name` with `config.SESSION_PREFIX` stripped (matching the display name `_send_rename` has always used). When `mode != SHELL_MODE` and `compat.CAPS["remote_control_flag"]` is true, the argv includes `--remote-control <display_name>` (same display name) instead of relying on a later `/remote-control` keystroke. When `compat.CAPS["permission_mode_flag"]` is true, `--permission-mode <config.PERMISSION_MODE[mode]>` plus `config.EXTRA_FLAGS.get(mode, [])` replace the `config.RC_FLAGS[mode]` string entirely; when it is false, `config.RC_FLAGS[mode]` is used exactly as before (full backward-compat path for an old `claude` binary). The tmux env flags (`-e ...`) gain two additions **whenever `session_id` is not None**: `RC_SESSION_ID=<session_id>` and `RC_TITLE=<display_name>` — written at session-creation time via tmux's `-e`, not by a later `set-environment` call, so they exist from the first moment the session is queryable.
- The function's return type and the meaning of every existing parameter are unchanged; this is purely additive.

- [ ] **Step 1: Write the failing tests**

Run `grep -rln "build_tmux_command" tests/` to see if a dedicated test file exists; if not, add a new class to `tests/test_setup_session.py` (it already imports `sessions` and is the natural home). First, read `compat.py`'s `CAPS` shape once more so the monkeypatch below matches it exactly.

```python
class BuildTmuxCommandNativeFlagsTest(unittest.TestCase):
    """build_tmux_command adds --session-id/-n/--remote-control/
    --permission-mode only when compat.CAPS says the installed claude
    supports them, and always falls back to config.RC_FLAGS otherwise."""

    def setUp(self):
        import compat
        self._orig_caps = dict(compat.CAPS)

    def tearDown(self):
        import compat
        compat.CAPS = self._orig_caps

    def test_new_claude_gets_native_flags(self):
        import compat
        compat.CAPS = {
            "session_id_flag": True, "name_flag": True,
            "remote_control_flag": True, "permission_mode_flag": True,
            "agents_json": True, "version": "2.1.263",
        }
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        self.assertIn("--session-id", cmd)
        self.assertIn("0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        self.assertIn("-n", cmd)
        self.assertIn("portugal", cmd)
        self.assertIn("--remote-control", cmd)
        self.assertIn("--permission-mode", cmd)
        self.assertIn("bypassPermissions", cmd)
        # env vars set at creation
        self.assertIn("-e", cmd)
        self.assertIn("RC_SESSION_ID=0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        self.assertIn("RC_TITLE=portugal", cmd)

    def test_old_claude_falls_back_to_rc_flags_and_no_env(self):
        import compat
        compat.CAPS = {
            "session_id_flag": False, "name_flag": False,
            "remote_control_flag": False, "permission_mode_flag": False,
            "agents_json": False, "version": "1.9.0",
        }
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--remote-control", cmd)
        self.assertNotIn("RC_SESSION_ID=0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        # The RC_FLAGS string still ends up in the joined claude_cmd (bash -c argv)
        joined = " ".join(cmd)
        self.assertIn("--dangerously-skip-permissions", joined)

    def test_ci_mode_native_flags_carry_teammate_mode(self):
        import compat
        compat.CAPS = {
            "session_id_flag": True, "name_flag": True,
            "remote_control_flag": True, "permission_mode_flag": True,
            "agents_json": True, "version": "2.1.263",
        }
        cmd = sessions.build_tmux_command(
            "rc-ci-job", "/home/user/project", "ci",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertIn("--permission-mode", cmd)
        self.assertIn("bypassPermissions", cmd)
        self.assertIn("--teammate-mode", cmd)
        self.assertIn("in-process", cmd)

    def test_shell_mode_never_gets_native_flags_even_with_full_caps(self):
        import compat
        compat.CAPS = {
            "session_id_flag": True, "name_flag": True,
            "remote_control_flag": True, "permission_mode_flag": True,
            "agents_json": True, "version": "2.1.263",
        }
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "sh",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--remote-control", cmd)
        self.assertNotIn("-n", cmd)

    def test_no_session_id_means_no_rc_env_vars(self):
        import compat
        compat.CAPS = {
            "session_id_flag": True, "name_flag": True,
            "remote_control_flag": True, "permission_mode_flag": True,
            "agents_json": True, "version": "2.1.263",
        }
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c")  # no session_id passed
        self.assertFalse(any(str(x).startswith("RC_SESSION_ID=") for x in cmd))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_session.BuildTmuxCommandNativeFlagsTest -v`
Expected: FAIL — `TypeError: build_tmux_command() got an unexpected keyword argument 'session_id'`

- [ ] **Step 3: Implement**

Add `import compat` to `sessions.py`'s imports (near `from config import (...)`). Rewrite `build_tmux_command` (`sessions.py:28-70`):

```python
def build_tmux_command(name, session_dir, mode, model=None, sandbox=False,
                       resume=False, resume_id=None, resume_search=None,
                       session_id=None):
    """Build the `tmux new-session` argv for a session.

    Shared by /start and restart_session so both paths stay identical.
    In SHELL_MODE the pane runs a login shell directly; every other mode
    wraps Claude Code in bash so a startup failure leaves its message on
    screen long enough for setup_session to read it.

    When session_id is given and the installed claude supports it
    (compat.CAPS), the session's identity and Remote Control activation
    are established natively at launch: --session-id, -n <name>, and
    --remote-control <name> replace the /rename and /remote-control
    keystroke dance that setup_session otherwise has to do. RC_SESSION_ID
    and RC_TITLE are written into the tmux environment at creation time so
    later lookups (get_transcript, restart_session, resume_session) never
    have to scan JSONL titles for a session launched this way.
    """
    display_name = name[len(SESSION_PREFIX):] if name.startswith(SESSION_PREFIX) else name

    env_flags = [
        "-e", f"RC_MODE={mode}",
        "-e", f"RC_WORKDIR={session_dir}",
        "-e", "DISPLAY=:1",
        "-e", "TERM=xterm-256color",
    ]
    if resume_search:
        # After RC_WORKDIR, matching the ordering restart_session has always used.
        env_flags[4:4] = ["-e", f"RC_RESUME_SEARCH={resume_search}"]
    if sandbox or os.geteuid() == 0:
        env_flags.extend(["-e", "IS_SANDBOX=1"])
    if session_id and mode != SHELL_MODE:
        env_flags.extend(["-e", f"RC_SESSION_ID={session_id}"])
        env_flags.extend(["-e", f"RC_TITLE={display_name}"])

    if mode == SHELL_MODE:
        # A shell takes no Claude flags: model and resume do not apply.
        payload = [SHELL_BIN, "-l"]
    else:
        if compat.CAPS.get("permission_mode_flag"):
            claude_args = ["--permission-mode", PERMISSION_MODE.get(mode, "bypassPermissions")]
            claude_args.extend(EXTRA_FLAGS.get(mode, []))
        else:
            claude_args = RC_FLAGS.get(mode, RC_FLAGS["c"]).split()
        if session_id and compat.CAPS.get("session_id_flag"):
            claude_args.extend(["--session-id", session_id])
        if compat.CAPS.get("name_flag"):
            claude_args.extend(["-n", display_name])
        if compat.CAPS.get("remote_control_flag"):
            claude_args.extend(["--remote-control", display_name])
        if resume:
            claude_args.append("--resume")
            if resume_id:
                claude_args.append(resume_id)
        model_flag = MODEL_MAP.get(model) if model else None
        if model_flag:
            claude_args.extend(["--model", model_flag])
        claude_cmd = " ".join([f"CLAUDECODE= {CLAUDE_BIN}"] + claude_args)
        payload = ["bash", "-c", f'{claude_cmd} 2>&1 || {{ echo ""; sleep 30; }}']

    return [
        "tmux", "new-session", "-d", "-s", name,
        "-c", session_dir,
        "-x", "200", "-y", "50",
        *env_flags,
        *payload,
    ]
```

Update the `from config import (...)` line at the top of `sessions.py` (`sessions.py:12-13`) to also import `PERMISSION_MODE` and `EXTRA_FLAGS`:

```python
from config import (SESSION_PREFIX, CLAUDE_BIN, RC_FLAGS, MODEL_MAP,
                    SHELL_BIN, SHELL_MODE, PERMISSION_MODE, EXTRA_FLAGS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 149 tests ... OK` (144 + 5 new). Double check the pre-existing `test_shell_mode.py` tests for `build_tmux_command` still pass unmodified — they call it without `session_id`, which is the new default and produces the old behavior.

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_setup_session.py
git commit -m "feat: build_tmux_command adds native --session-id/-n/--remote-control/--permission-mode when supported"
```

---

### Task 4: `/start` and `resume_session` pre-assign a UUID and pass it through

**Files:**
- Modify: `server.py` (the `/start` POST branch — find it with `grep -n 'elif path == "/start"' server.py`)
- Modify: `sessions.py:918-1000` (`resume_session`)
- Test: `tests/test_server_helpers.py` and/or `tests/test_setup_session.py`

**Interfaces:**
- Consumes: `sessions.build_tmux_command(..., session_id=...)` (Task 3).
- Produces: `server._new_session_id() -> str` — a tiny wrapper around `str(uuid.uuid4())`, extracted to a one-line helper purely so tests can assert `/start` calls it (it needs no injection point beyond that — `uuid.uuid4()` is already deterministic-enough to not need mocking in the assertions below, which only check the argv shape). The `/start` handler now always generates a session id and passes `session_id=` into `build_tmux_command` regardless of `compat.CAPS` (Task 3 already no-ops it when the flag isn't supported, and Task 6 relies on `RC_SESSION_ID` being present for launcher-started sessions whenever it can be).

- [ ] **Step 1: Read the current `/start` handler**

Run: `grep -n 'elif path == "/start"' server.py` then read that whole branch (likely 30-60 lines) with `sed -n '<line>,<line+60>p' server.py`. Note exactly how it currently calls `sessions.build_tmux_command` and what it does after (calls `setup_session` in a thread, same as `restart_session`/`resume_session`).

- [ ] **Step 2: Write the failing test**

Add to `tests/test_server_helpers.py`:

```python
class NewSessionIdTest(unittest.TestCase):
    def test_returns_a_uuid_string(self):
        import uuid
        sid = server._new_session_id()
        self.assertIsInstance(sid, str)
        # Round-trips through uuid.UUID without raising -> it's a valid UUID.
        uuid.UUID(sid)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server_helpers.NewSessionIdTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_new_session_id'`

- [ ] **Step 4: Implement**

Add near the top of `server.py`, alongside the other small helper functions (near `_update_confirmed` etc.), and add `import uuid` to the top-level imports if not already present (check with `grep -n "^import uuid" server.py` first):

```python
def _new_session_id():
    """One session id per /start call, passed to build_tmux_command so a
    session that supports --session-id gets a known UUID from birth
    instead of one discovered later by scanning JSONL titles."""
    return str(uuid.uuid4())
```

In the `/start` branch, find the call to `sessions.build_tmux_command(...)` and add `session_id=_new_session_id()` as a keyword argument. Example (adjust to the actual local variable names found in Step 1):

```python
            session_id = _new_session_id()
            cmd = sessions.build_tmux_command(
                name, session_dir, mode, model=model, sandbox=sandbox,
                session_id=session_id,
            )
```

Do the analogous change in `sessions.resume_session` (`sessions.py:918-1000`): it currently builds its own tmux command inline (not via `build_tmux_command` — read `sessions.py:966-987` again) rather than sharing `build_tmux_command`. Refactor it to call `build_tmux_command` instead of duplicating the argv construction, passing `resume=True, resume_id=session_name` (the UUID being resumed is already known here — it's the function's own `session_name` parameter, so **no new UUID is generated on resume**; the existing session id is reused via `session_id=session_name`):

```python
    claude_flags = RC_FLAGS[mode]
    cmd = build_tmux_command(
        tmux_name, session_dir, mode, model=None, sandbox=(os.geteuid() == 0),
        resume=True, resume_id=session_name, session_id=session_name,
    )
```

Remove the now-dead manual `claude_args`/`env_flags`/`claude_cmd`/`wrapper`/`cmd` construction block that `build_tmux_command` replaces (`sessions.py:966-987` in the original file — re-check line numbers after Task 3's edits before deleting). Keep everything else in `resume_session` (sanitization, `session_exists` check, the `setup_session` thread launch) unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 150 tests ... OK`. Also run `python3 -m unittest tests.test_setup_session -v` specifically since `resume_session` changed its internals — confirm no existing resume-related test broke.

- [ ] **Step 6: Commit**

```bash
git add server.py sessions.py tests/test_server_helpers.py
git commit -m "feat: /start and resume_session pre-assign/reuse a session UUID via build_tmux_command"
```

---

### Task 5: `setup_session` skips the RC keystroke dance when native flags were used

**Files:**
- Modify: `sessions.py:312-587` (`_send_rename`, `setup_session`, `_RC_ACTIVE_MARKERS`, `_is_rc_active`)
- Test: `tests/test_setup_session.py`

**Interfaces:**
- Produces: `sessions.setup_session(session_name, display_name, mode)` — unchanged signature. Internally, right after the trust/bypass-prompt loop and the "Claude prompt found" wait (unchanged), it now checks `get_session_env(session_name, "RC_SESSION_ID")`: if that env var is set (meaning `build_tmux_command` used `--session-id`/`-n`/`--remote-control` for this session — Task 3's guarantee), `setup_session` returns immediately without ever sending `/remote-control` or `/rename`, and without polling `_is_rc_active`/`_wait_for_rc_active`. If `RC_SESSION_ID` is not set (old `claude`, or `compat.CAPS` said no at launch time — the pre-v3 fallback path), it falls through to exactly the old behavior. `_RC_ACTIVE_MARKERS` and `_is_rc_active` are **removed** — `get_active_rc_session` (the only other caller of `_is_rc_active`, `sessions.py:161-176`) is also updated to use a still-required capability: since it's only used to find which session currently owns Remote Control on this device (used by callers outside this task's scope — check `grep -n "get_active_rc_session" *.py` before deleting `_is_rc_active`; if it has live callers, keep `_is_rc_active`/`_RC_ACTIVE_MARKERS` as private helpers used only by `get_active_rc_session`, and only remove their use from `setup_session`/`get_url`). Re-verify this call graph in Step 1 before deleting anything.

- [ ] **Step 1: Check what else calls `_is_rc_active` / `_RC_ACTIVE_MARKERS` / `get_active_rc_session`**

Run: `grep -rn "_is_rc_active\|_RC_ACTIVE_MARKERS\|get_active_rc_session" --include=*.py .`

If `get_active_rc_session` has live callers outside `sessions.py` (e.g. `server.py`), **keep** `_is_rc_active` and `_RC_ACTIVE_MARKERS` defined (they still back `get_active_rc_session` and `get_url`'s fallback path), and scope this task's removal to just `setup_session`'s use of them. If `get_active_rc_session` turns out to have no callers at all, it and its private helpers can be deleted entirely — note which case applies before writing the diff below, and adjust the "Files" list accordingly (do not delete `_is_rc_active`/`_RC_ACTIVE_MARKERS` if `get_url`, updated in Task 6, still needs a fallback for sessions without `RC_SESSION_ID`... but Task 6 replaces `get_url`'s detection with OSC-8 parsing that does not depend on `_is_rc_active` at all, so by the time Task 6 lands `_is_rc_active` is only needed for `get_active_rc_session`, if anything).

- [ ] **Step 2: Write the failing test**

Add to `tests/test_setup_session.py` (it already has `FakeRun`/`READY_PANE`/`OSC_LINK` fixtures from Task 3's neighbor tests — reuse them):

```python
class SetupSessionNativeIdentitySkipsRcDanceTest(unittest.TestCase):
    """When build_tmux_command already used --session-id/-n/--remote-control
    (signalled by RC_SESSION_ID being set on the session), setup_session
    must return immediately after the prompt/init wait: no /remote-control
    keystrokes, no /rename keystrokes, no polling loop."""

    def setUp(self):
        self.fake = FakeRun()
        self._patched = {
            "subprocess": sessions.subprocess.run,
            "sleep": sessions.time.sleep,
            "exists": sessions.session_exists,
            "status": sessions.get_session_status,
            "env": sessions.get_session_env,
        }
        sessions.subprocess.run = self.fake
        sessions.time.sleep = lambda *_: None
        sessions.session_exists = lambda name: True
        sessions.get_session_status = lambda name: "running"
        sessions.get_session_env = lambda name, var: (
            "0d3b8b1a-1111-4a2b-9c3d-abcdef012345" if var == "RC_SESSION_ID" else None
        )

    def tearDown(self):
        sessions.subprocess.run = self._patched["subprocess"]
        sessions.time.sleep = self._patched["sleep"]
        sessions.session_exists = self._patched["exists"]
        sessions.get_session_status = self._patched["status"]
        sessions.get_session_env = self._patched["env"]

    def test_no_remote_control_or_rename_keystrokes_sent(self):
        sessions.setup_session("rc-portugal", "portugal", "c")
        sent = self.fake.sent_text()
        self.assertNotIn("/remote-control", sent)
        self.assertFalse(any(s.startswith("/rename") for s in sent))
```

(Match this test's setup/teardown exactly to whatever pattern the existing `SetupSessionRenameTest` in the same file already uses — read its `setUp`/`tearDown` first, since it patches the same five names, and reuse rather than duplicate if it's already a shared `setUp` you can subclass or a helper you can call.)

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m unittest tests.test_setup_session.SetupSessionNativeIdentitySkipsRcDanceTest -v`
Expected: FAIL — `/remote-control` and `/rename` are both sent, since `setup_session` doesn't check `RC_SESSION_ID` yet.

- [ ] **Step 4: Implement**

In `sessions.py`, `setup_session` (`sessions.py:350-587`), right after the existing block that waits for the CLI to fully initialize (`time.sleep(10)` following "prompt found, waiting for CLI to fully initialize..."), insert an early return:

```python
    # Wait for CLI to fully initialize after showing prompt
    # (prompt appears before internal WebSocket/API connections are ready)
    print(f"  {session_name}: prompt found, waiting for CLI to fully initialize...")
    time.sleep(10)

    # If build_tmux_command already used --session-id/-n/--remote-control,
    # RC_SESSION_ID is set on the session from creation: identity and RC
    # activation are already done natively, so there's nothing left for the
    # keystroke dance below to accomplish. Sending /remote-control again
    # would just toggle it off.
    if get_session_env(session_name, "RC_SESSION_ID"):
        print(f"  {session_name}: native session identity in use, skipping /remote-control and /rename")
        return

    # Check if remote-control is already active (status bar shows "Remote Control active")
```

(The rest of the function — the `_is_rc_active`-marker check, `_send_rc`, `_wait_for_rc_active`, the retry loop, and the final `_send_rename` call — is left in place *only* as the pre-v3 fallback path, reached exclusively when `RC_SESSION_ID` is absent. Do not delete it in this task unless Step 1 established that `_is_rc_active`/`_RC_ACTIVE_MARKERS` have no other callers — if they do have other callers, this task changes nothing about them beyond adding the early return above.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 151 tests ... OK`. Confirm the pre-existing `SetupSessionRenameTest` (which does *not* set `RC_SESSION_ID` in its fake env) still passes unmodified — it exercises the fallback path this task must not break.

- [ ] **Step 6: Commit**

```bash
git add sessions.py tests/test_setup_session.py
git commit -m "feat: setup_session skips /remote-control and /rename keystrokes for natively-identified sessions"
```

---

### Task 6: `get_url` strips OSC 8 hyperlinks and reads the claude.ai link from the target

**Files:**
- Modify: `sessions.py:179-238` (`get_url`, `_get_url_internal`)
- Test: `tests/test_setup_session.py`

**Interfaces:**
- Produces: `sessions._strip_osc8(text: str) -> str` — new pure helper. Strips both OSC 8 hyperlink forms: the *close* sequence `\x1b]8;;\x1b\\` (or its BEL-terminated variant `\x1b]8;;\x07`) and the *open* sequence `\x1b]8;id=...;URL\x1b\\` (or BEL-terminated), replacing an open sequence with the URL it carries followed by a space (so a `claude.ai/code/session_...` URL that was only ever present as a hyperlink *target* — never as visible text — becomes matchable plain text), and dropping close sequences entirely. `sessions.get_url(session_name)` and `sessions._get_url_internal(session_name)` both call `_strip_osc8` on the captured pane text **before** the existing ANSI-escape ("`\x1b\[...`") handling and before the `re.findall(r'https://claude\.ai/code/session_[^\s]+', ...)` match, instead of matching on raw pane text. `get_url` no longer depends on `_is_rc_active` to decide whether to return a URL — the presence of a matched, well-formed `claude.ai/code/session_...` URL from an OSC 8 target *is* the signal that Remote Control is active (a stale/failed RC never anchors a hyperlink to that URL pattern), which also means `get_url` and `_get_url_internal` can be **merged into one function** if `_get_url_internal`'s only reason to exist was skipping the `_is_rc_active` gate (check both bodies once more before merging — if `setup_session`'s fallback path in Task 5 still calls `_get_url_internal` directly, keep it as a thin alias of `get_url` rather than deleting the name).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_setup_session.py` (reuse the module's existing `OSC_LINK` constant — it is exactly the regression-test byte sequence the spec calls for):

```python
class StripOsc8Test(unittest.TestCase):
    def test_strips_close_sequence(self):
        raw = "hello\x1b]8;;\x1b\\world"
        self.assertEqual(sessions._strip_osc8(raw), "hello world" if False else "helloworld")

    def test_open_sequence_becomes_its_url_target(self):
        raw = "status: \x1b]8;id=1dcslmk;https://claude.ai/code/session_01HuRGXzwUppFqzPmUXZQZ6J?from=cli\x1b\\/rc\x1b]8;;\x1b\\"
        cleaned = sessions._strip_osc8(raw)
        self.assertIn("https://claude.ai/code/session_01HuRGXzwUppFqzPmUXZQZ6J?from=cli", cleaned)
        # The close sequence must not leave escape-code litter behind.
        self.assertNotIn("\x1b", cleaned)

    def test_never_captures_escape_junk_as_part_of_the_url(self):
        # Regression test with the real status-bar bytes from a v2.1.263
        # session: OSC_LINK is the visible-label-only form ("/rc" is the
        # label; the URL lives in the hyperlink target, not the text).
        cleaned = sessions._strip_osc8(OSC_LINK)
        matches = re.findall(r'https://claude\.ai/code/session_[^\s\x1b]+', cleaned)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], "https://claude.ai/code/session_01FxTHitxTgkXbpbQEDJaR4R?from=cli")


class GetUrlOsc8Test(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun(pane=(
            "  ~/career-ops | Opus 5 | Tokens: 0/1.0M (0%)          " + OSC_LINK + "\n"
        ))
        self._patched_run = sessions.subprocess.run
        self._patched_env = sessions.get_session_env
        self._patched_shell = sessions.is_shell_session
        sessions.subprocess.run = self.fake
        sessions.get_session_env = lambda name, var: None
        sessions.is_shell_session = lambda name: False

    def tearDown(self):
        sessions.subprocess.run = self._patched_run
        sessions.get_session_env = self._patched_env
        sessions.is_shell_session = self._patched_shell

    def test_extracts_url_from_hyperlink_target_only(self):
        url = sessions.get_url("rc-portugal")
        self.assertEqual(url, "https://claude.ai/code/session_01FxTHitxTgkXbpbQEDJaR4R?from=cli")
```

`re` must already be imported in `tests/test_setup_session.py` — check `grep -n "^import re" tests/test_setup_session.py` and add it if missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_setup_session.StripOsc8Test tests.test_setup_session.GetUrlOsc8Test -v`
Expected: FAIL — `AttributeError: module 'sessions' has no attribute '_strip_osc8'` and `get_url` returning `None` (it currently gates on `_is_rc_active`, which the fake pane text no longer satisfies since the old marker strings are gone from real TUI output).

- [ ] **Step 3: Implement**

Add near the top of `sessions.py`, after the existing module-level regex-using code (or right before `get_url`):

```python
_OSC8_OPEN_RE = re.compile(r'\x1b\]8;[^;]*;(\S*)\x1b\\')
_OSC8_OPEN_BEL_RE = re.compile(r'\x1b\]8;[^;]*;(\S*)\x07')
_OSC8_CLOSE_RE = re.compile(r'\x1b\]8;;\x1b\\')
_OSC8_CLOSE_BEL_RE = re.compile(r'\x1b\]8;;\x07')


def _strip_osc8(text):
    """Strip OSC 8 hyperlink escape sequences, replacing an *open* sequence
    with the URL it targets (so a link whose visible label is just '/rc'
    still yields the real claude.ai URL as plain matchable text) and
    dropping *close* sequences entirely. Must run before both the ANSI
    CSI-sequence strip and the claude.ai URL regex match — the status bar
    since Claude Code ~2.1 renders the RC indicator as a hyperlink whose
    label never contains the URL, only its target does.
    """
    text = _OSC8_OPEN_RE.sub(r'\1 ', text)
    text = _OSC8_OPEN_BEL_RE.sub(r'\1 ', text)
    text = _OSC8_CLOSE_RE.sub('', text)
    text = _OSC8_CLOSE_BEL_RE.sub('', text)
    return text
```

Rewrite `get_url` (`sessions.py:179-218`) to strip OSC 8 first and to stop gating on `_is_rc_active` — a matched URL from a hyperlink target is itself the "RC is active" signal:

```python
def get_url(session_name):
    """Extract the claude.ai URL from a tmux session's pane output.
    Shell sessions have no URL. A URL is only returned when one can
    actually be found in the current pane (via its OSC 8 hyperlink target
    or as plain text) or in the RC_URL env var this function has
    previously cached — a session that has never shown the URL yet, or
    whose Remote Control never activated, correctly returns None."""
    if is_shell_session(session_name):
        return None
    for history_lines in ("-50", "-500"):
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", session_name, "-p", "-S", history_lines, "-J"],
                capture_output=True, text=True, timeout=5,
            )
            text = _strip_osc8(r.stdout).replace("\n", " ")
            matches = re.findall(r'(https://claude\.ai/code/session_[^\s\x1b]+)', text)
            if matches:
                url = matches[-1]
                stored = get_session_env(session_name, "RC_URL")
                if stored != url:
                    subprocess.run(
                        ["tmux", "set-environment", "-t", session_name, "RC_URL", url],
                        capture_output=True,
                    )
                return url
        except Exception:
            pass
    stored = get_session_env(session_name, "RC_URL")
    if stored and stored.startswith("https://claude.ai/code/session_"):
        return stored
    return None
```

`_get_url_internal` becomes a thin alias so Task 5's fallback path (and any other caller found in Task 5 Step 1) keeps working unchanged:

```python
def _get_url_internal(session_name):
    """Alias kept for setup_session's pre-v3 fallback path — get_url no
    longer gates on RC-active detection, so there is nothing left that
    only _get_url_internal could do."""
    return get_url(session_name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 155 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_setup_session.py
git commit -m "fix: get_url strips OSC 8 hyperlinks and reads the claude.ai URL from the link target"
```

---

### Task 7: `transcript_path` pure helper for UUID-based transcript lookup

**Files:**
- Create: nothing new — add to `sessions.py`, near `_find_session_uuid` (`sessions.py:641-707`)
- Test: `tests/test_setup_session.py` or a new `tests/test_transcript_path.py` (new file is cleaner given the fixture-inspection nature of these tests — prefer the new file)

**Interfaces:**
- Produces: `sessions.transcript_path(workdir: str, session_id: str) -> str` — pure function, no I/O. Encodes `workdir` the way Claude Code names its `~/.claude/projects/<encoded>` directories: replace every `/` with `-` and every `.` with `-` (verified against this box's actual `~/.claude/projects` listing: a home directory's `~/.claude-rc` encodes with the leading dot turned into an extra hyphen, e.g. `-home-user--claude-rc`, and a plain path with no dots, e.g. `/home/user/project`, encodes as `-home-user-project`, confirming `/`->`-` alone for the no-dot case). Returns `os.path.expanduser(os.path.join("~/.claude/projects", encoded_workdir, session_id + ".jsonl"))`. Does not check the file exists — callers do that.
- Consumes: nothing beyond stdlib `os`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript_path.py`:

```python
"""transcript_path: pure encoding of (workdir, session_id) -> the JSONL
path Claude Code writes to. Encoding verified against this box's actual
~/.claude/projects listing (2026-09-06): '/' and '.' both become '-'."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sessions


class TranscriptPathTest(unittest.TestCase):
    def test_simple_path_no_dots(self):
        p = sessions.transcript_path("/home/user/project", "abc123")
        self.assertEqual(
            p, os.path.expanduser("~/.claude/projects/-home-user-project/abc123.jsonl"))

    def test_path_with_a_dot_segment(self):
        # Matches the real encoding of ~/.claude-rc on this box (leading
        # dot of .claude-rc becomes an extra hyphen, i.e. '.' -> '-' just
        # like '/' -> '-').
        p = sessions.transcript_path("/home/user/.claude-rc", "abc123")
        self.assertEqual(
            p, os.path.expanduser("~/.claude/projects/-home-user--claude-rc/abc123.jsonl"))

    def test_nested_path(self):
        p = sessions.transcript_path("/var/www/rc-launcher-p1", "abc123")
        self.assertEqual(
            p, os.path.expanduser("~/.claude/projects/-var-www-rc-launcher-p1/abc123.jsonl"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_transcript_path -v`
Expected: FAIL — `AttributeError: module 'sessions' has no attribute 'transcript_path'`

- [ ] **Step 3: Implement**

Add to `sessions.py`, right before `_find_session_uuid`:

```python
def transcript_path(workdir, session_id):
    """The JSONL transcript path Claude Code writes for (workdir, session_id).

    Pure/no I/O — callers check existence themselves. Claude Code encodes a
    project's cwd into its ~/.claude/projects/<encoded> directory name by
    replacing both '/' and '.' with '-' (verified directly against this
    box's ~/.claude/projects listing, not from documentation — the
    transcript path format is explicitly undocumented and unstable).
    """
    encoded = workdir.replace("/", "-").replace(".", "-")
    return os.path.expanduser(os.path.join("~/.claude/projects", encoded, session_id + ".jsonl"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 158 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_transcript_path.py
git commit -m "feat: sessions.transcript_path — pure workdir+session_id to JSONL path encoding"
```

---

### Task 8: `get_transcript` prefers `RC_SESSION_ID`, falls back to title scan

**Files:**
- Modify: `sessions.py:710-768` (`get_transcript`)
- Test: `tests/test_setup_session.py` or `tests/test_transcript_path.py`

**Interfaces:**
- Produces: `sessions.get_transcript(tmux_name, limit=300)` — unchanged signature and return shape (`{"sessionId": uuid, "messages": [...]}` or `None`). Internally: reads `RC_SESSION_ID` from `get_session_env(tmux_name, "RC_SESSION_ID")` first; if present, builds the path with `transcript_path(workdir, session_id)` (Task 7) and uses it directly if the file exists, skipping `_find_session_uuid` entirely. Only when `RC_SESSION_ID` is absent, or the file it points to doesn't exist (e.g. stale env from a killed-and-recreated tmux session that reused a name), does it fall back to the existing `_find_session_uuid` title-scan path — unchanged.
- Consumes: `sessions.transcript_path` (Task 7).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_transcript_path.py`:

```python
class GetTranscriptUsesSessionIdFirstTest(unittest.TestCase):
    """get_transcript must use RC_SESSION_ID (via transcript_path) instead
    of scanning JSONL titles when the env var is present and the file
    exists — the title scan is the pre-v3 fallback only."""

    def setUp(self):
        self._patched_env = sessions.get_session_env
        self._patched_shell = sessions.is_shell_session
        self._patched_find_uuid = sessions._find_session_uuid
        self._patched_expanduser = os.path.expanduser
        self.find_uuid_calls = []
        sessions._find_session_uuid = lambda *a, **kw: (
            self.find_uuid_calls.append((a, kw)) or None
        )

    def tearDown(self):
        sessions.get_session_env = self._patched_env
        sessions.is_shell_session = self._patched_shell
        sessions._find_session_uuid = self._patched_find_uuid

    def test_skips_title_scan_when_rc_session_id_present_and_file_exists(self):
        import tempfile, json as jsonlib

        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "proj")
            os.makedirs(workdir)
            session_id = "abc123"
            proj_dir_name = workdir.replace("/", "-").replace(".", "-")
            claude_projects = os.path.join(tmp, ".claude", "projects", proj_dir_name)
            os.makedirs(claude_projects)
            transcript_file = os.path.join(claude_projects, session_id + ".jsonl")
            with open(transcript_file, "w") as f:
                f.write(jsonlib.dumps({
                    "type": "user", "message": {"role": "user", "content": "hi"},
                    "timestamp": "2026-09-06T00:00:00Z",
                }) + "\n")

            sessions.is_shell_session = lambda name: False
            sessions.get_session_env = lambda name, var: {
                "RC_SESSION_ID": session_id, "RC_WORKDIR": workdir,
            }.get(var)

            real_expanduser = os.path.expanduser
            fake_home = tmp

            def fake_expanduser(p):
                if p.startswith("~"):
                    return fake_home + p[1:]
                return real_expanduser(p)
            os.path.expanduser = fake_expanduser
            try:
                result = sessions.get_transcript("rc-portugal")
            finally:
                os.path.expanduser = real_expanduser

            self.assertIsNotNone(result)
            self.assertEqual(result["sessionId"], session_id)
            self.assertEqual(len(result["messages"]), 1)
            self.assertEqual(self.find_uuid_calls, [])  # title scan never invoked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_transcript_path.GetTranscriptUsesSessionIdFirstTest -v`
Expected: FAIL — `find_uuid_calls` is non-empty (the current implementation always calls `_find_session_uuid`).

- [ ] **Step 3: Implement**

Rewrite the start of `get_transcript` (`sessions.py:710-728`):

```python
def get_transcript(tmux_name, limit=300):
    """Parse the session's JSONL transcript into displayable messages.

    Claude Code runs in the alternate screen with no tmux history, so the
    browser can't scroll the terminal. The full conversation lives in
    ~/.claude/projects/<dir>/<uuid>.jsonl — serve it for a natively
    scrollable history view. Returns None if the session can't be mapped —
    including shell sessions, which write no JSONL at all.

    Prefers RC_SESSION_ID (set at launch by build_tmux_command for
    sessions using native identity) over the title-scan fallback
    (_find_session_uuid), which stays for pre-v3 sessions and for a stale
    RC_SESSION_ID whose file no longer exists.
    """
    if is_shell_session(tmux_name):
        return None
    workdir = get_session_env(tmux_name, "RC_WORKDIR") or ""
    uuid = None
    path = None
    rc_session_id = get_session_env(tmux_name, "RC_SESSION_ID")
    if rc_session_id:
        candidate = transcript_path(workdir, rc_session_id)
        if os.path.isfile(candidate):
            uuid = rc_session_id
            path = candidate
    if not uuid:
        uuid = _find_session_uuid(tmux_name, workdir)
        if not uuid:
            return None
        paths = glob.glob(os.path.expanduser(
            os.path.join("~/.claude/projects", "*", uuid + ".jsonl")))
        if not paths:
            return None
        path = paths[0]
    messages = []
    try:
        with open(path) as fh:
```

(Everything from `for line in fh:` onward in the original body — the parsing loop — is unchanged; only the variable that used to be `paths[0]` is now `path`, already set above by either branch. Delete the old `uuid = _find_session_uuid(...)` / `paths = glob.glob(...)` lines that this replaces.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 159 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_transcript_path.py
git commit -m "feat: get_transcript prefers RC_SESSION_ID over the JSONL title scan"
```

---

### Task 9: `restart_session` preserves and reuses `RC_SESSION_ID`

**Files:**
- Modify: `sessions.py:771-821` (`restart_session`)
- Test: `tests/test_setup_session.py`

**Interfaces:**
- Produces: `sessions.restart_session(name, mode=None, workdir=None, model=None, sandbox=False, resume=True)` — unchanged signature/return (`(bool, message)`). Internally: reads `RC_SESSION_ID` from the tmux env **before** killing the old session (same timing constraint the function already respects for `resume_id` via `_find_session_uuid`). If `RC_SESSION_ID` is present, it is passed straight through as `resume_id` to `build_tmux_command` (via the new `session_id=` kwarg too, so the *same* UUID is kept across the restart rather than a new one being minted) — `_find_session_uuid` is only consulted when `RC_SESSION_ID` is absent, exactly mirroring Task 8's fallback ordering.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_setup_session.py`:

```python
class RestartSessionPrefersRcSessionIdTest(unittest.TestCase):
    """restart_session must reuse RC_SESSION_ID as both the resume target
    and the new session's --session-id, without ever calling
    _find_session_uuid, when the env var is present."""

    def setUp(self):
        self.fake = FakeRun()
        self._patched = {
            "run": sessions.subprocess.run,
            "sleep": sessions.time.sleep,
            "exists": sessions.session_exists,
            "env": sessions.get_session_env,
            "find_uuid": sessions._find_session_uuid,
            "setup": sessions.setup_session,
        }
        self.find_uuid_calls = []
        sessions.subprocess.run = self.fake
        sessions.time.sleep = lambda *_: None
        sessions.session_exists = lambda name: True
        sessions.get_session_env = lambda name, var: {
            "RC_MODE": "c", "RC_WORKDIR": "/home/user/project",
            "RC_SESSION_ID": "0d3b8b1a-1111-4a2b-9c3d-abcdef012345",
        }.get(var)
        sessions._find_session_uuid = lambda *a, **kw: (
            self.find_uuid_calls.append((a, kw)) or None
        )
        sessions.setup_session = lambda *a, **kw: None

    def tearDown(self):
        sessions.subprocess.run = self._patched["run"]
        sessions.time.sleep = self._patched["sleep"]
        sessions.session_exists = self._patched["exists"]
        sessions.get_session_env = self._patched["env"]
        sessions._find_session_uuid = self._patched["find_uuid"]
        sessions.setup_session = self._patched["setup"]

    def test_reuses_rc_session_id_without_title_scan(self):
        ok, msg = sessions.restart_session("rc-portugal")
        self.assertTrue(ok)
        self.assertEqual(self.find_uuid_calls, [])
        # The new-session tmux command must carry the reused UUID both as
        # --resume target and as --session-id.
        new_session_calls = [c for c in self.fake.calls
                              if isinstance(c, list) and "new-session" in c]
        self.assertEqual(len(new_session_calls), 1)
        cmd = new_session_calls[0]
        self.assertIn("0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_setup_session.RestartSessionPrefersRcSessionIdTest -v`
Expected: FAIL — `find_uuid_calls` is non-empty (current code always calls `_find_session_uuid` when `resume=True`).

- [ ] **Step 3: Implement**

In `restart_session` (`sessions.py:771-821`), replace the "Find the Claude session UUID BEFORE killing" block:

```python
    # Find the Claude session UUID BEFORE killing (so JSONL is still fresh).
    # RC_SESSION_ID (set at launch by build_tmux_command for natively-
    # identified sessions) is authoritative and skips the title scan
    # entirely; _find_session_uuid is the pre-v3 fallback.
    resume_id = None
    if resume:
        resume_id = get_session_env(name, "RC_SESSION_ID")
        if resume_id:
            print(f"  {name}: reusing RC_SESSION_ID {resume_id[:8]}")
        else:
            resume_id = _find_session_uuid(name, session_dir)
            print(f"  {name}: UUID lookup → {resume_id[:8] if resume_id else 'not found'}")
```

Then update the `build_tmux_command` call a few lines below to also pass `session_id=resume_id` (reusing the same UUID rather than minting a new one on restart):

```python
    cmd = build_tmux_command(
        name, session_dir, mode, model=model, sandbox=sandbox,
        resume=resume, resume_id=resume_id,
        resume_search=name.replace(SESSION_PREFIX, ""),
        session_id=resume_id,
    )
```

Note `resume_id` may be `None` (e.g. `resume=False`, or neither `RC_SESSION_ID` nor the title scan found anything) — `build_tmux_command` already treats `session_id=None` as "don't add native identity flags" (Task 3), so this is safe.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 160 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_setup_session.py
git commit -m "feat: restart_session reuses RC_SESSION_ID as both resume target and new --session-id"
```

---

### Task 10: `list_resumable_sessions` reads `RC_SESSION_ID` context (no title-scan change needed, but add a regression test)

**Files:**
- Test only: `tests/test_setup_session.py` — no production code change

**Interfaces:**
- None new. This task is a deliberate no-op on `list_resumable_sessions` (`sessions.py:832-915`): it already keys entirely off the JSONL files under `~/.claude/projects` (scanning `customTitle`/`gitBranch`/`cwd` from file contents, not from tmux env), so it has no dependency on `RC_SESSION_ID` to begin with and needs no change. This task exists only to pin that down with a test, since the spec calls out `list_resumable_sessions` by name alongside the three functions Tasks 8-9 did change, and a future refactor could otherwise accidentally break its independence from tmux state.

- [ ] **Step 1: Write the pinning test**

Add to `tests/test_setup_session.py`:

```python
class ListResumableSessionsIsTmuxIndependentTest(unittest.TestCase):
    """list_resumable_sessions must not read tmux env at all — it only
    scans ~/.claude/projects/*/*.jsonl. Pinned here so a future change
    doesn't accidentally couple it to RC_SESSION_ID."""

    def test_does_not_call_get_session_env(self):
        import unittest.mock as mock
        with mock.patch.object(sessions, "get_session_env") as m:
            sessions.list_resumable_sessions()
            m.assert_not_called()
```

- [ ] **Step 2: Run test to verify it passes as-is**

Run: `python3 -m unittest tests.test_setup_session.ListResumableSessionsIsTmuxIndependentTest -v`
Expected: PASS immediately (no implementation change needed — this documents existing correct behavior). If it unexpectedly fails, that means `list_resumable_sessions` has grown a tmux dependency since the spec was written; stop and re-read `sessions.py:832-915` before proceeding, since that would mean this task needs a real fix rather than a pin.

- [ ] **Step 3: Commit**

```bash
git add tests/test_setup_session.py
git commit -m "test: pin list_resumable_sessions as tmux-independent (JSONL-only scan)"
```

---

### Task 11: `agents.py` — wrap `claude agents --json`

**Files:**
- Create: `agents.py`
- Test: `tests/test_agents.py` (new)

**Interfaces:**
- Produces:
  - `agents.list_claude_sessions(claude_bin=None, run=subprocess.run, now_fn=time.time) -> list[dict]` — runs `claude agents --json` (argv `[claude_bin or config.CLAUDE_BIN, "agents", "--json"]`), timeout 10s, cached in-process for 30s (module-level cache keyed by nothing — one device has one `claude agents --json` view; `now_fn` is injectable for tests). Returns `[]` on any failure (missing binary, timeout, non-zero exit, invalid JSON, `compat.CAPS["agents_json"]` is False) rather than raising. Each returned dict is normalized to exactly: `{"session_id": str, "name": Optional[str], "cwd": Optional[str], "kind": "interactive"|"background", "status": Optional[str], "started_at": Optional[int], "pid": Optional[int], "waiting_for": Optional[str]}` — reading from the raw `claude agents --json` fields `sessionId, name, cwd, kind, status, startedAt, pid, waitingFor` respectively (`state` from a background row is not surfaced as a separate field here — `waiting_for` alone is enough for Task 13's `needs_attention` derivation, and any row missing `sessionId` entirely is dropped as unusable).
  - `agents.CACHE_TTL_SECONDS = 30` (module constant, matches the "poll no faster than every 30s" fact from the spec).
- Consumes: `config.CLAUDE_BIN`, `compat.CAPS["agents_json"]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agents.py`:

```python
"""agents.py: wraps `claude agents --json`, normalizes rows, caches 30s."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents


class FakeRun:
    def __init__(self, stdout="[]", returncode=0, raises=None):
        self.stdout = stdout
        self.returncode = returncode
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.raises:
            raise self.raises

        class R:
            pass
        r = R()
        r.returncode = self.returncode
        r.stdout = self.stdout
        r.stderr = ""
        return r


RAW_JSON = '''[
  {"pid": 111, "cwd": "/home/user/proj", "kind": "interactive",
   "startedAt": 1757100000000, "sessionId": "abc-123", "name": "portugal",
   "status": "idle"},
  {"pid": 222, "cwd": "/home/user/proj2", "kind": "background",
   "startedAt": 1757100005000, "sessionId": "def-456", "name": "bg-task",
   "status": "busy", "state": "running", "waitingFor": null},
  {"pid": 333, "cwd": "/home/user/proj3", "kind": "background",
   "startedAt": 1757100010000, "sessionId": "ghi-789", "name": null,
   "status": "idle", "state": "waiting", "waitingFor": "permission_prompt"}
]'''


class ListClaudeSessionsTest(unittest.TestCase):
    def test_normalizes_rows(self):
        fake = FakeRun(stdout=RAW_JSON)
        rows = agents.list_claude_sessions(claude_bin="claude", run=fake)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {
            "session_id": "abc-123", "name": "portugal", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "idle", "started_at": 1757100000000,
            "pid": 111, "waiting_for": None,
        })
        self.assertEqual(rows[2]["waiting_for"], "permission_prompt")
        self.assertIsNone(rows[2]["name"])

    def test_empty_on_nonzero_exit(self):
        fake = FakeRun(stdout="", returncode=1)
        self.assertEqual(agents.list_claude_sessions(claude_bin="claude", run=fake), [])

    def test_empty_on_invalid_json(self):
        fake = FakeRun(stdout="not json")
        self.assertEqual(agents.list_claude_sessions(claude_bin="claude", run=fake), [])

    def test_empty_on_timeout(self):
        import subprocess as sp
        fake = FakeRun(raises=sp.TimeoutExpired(cmd=["claude"], timeout=10))
        self.assertEqual(agents.list_claude_sessions(claude_bin="claude", run=fake), [])

    def test_empty_on_missing_binary(self):
        fake = FakeRun(raises=OSError("not found"))
        self.assertEqual(agents.list_claude_sessions(claude_bin="/no/claude", run=fake), [])

    def test_row_without_session_id_is_dropped(self):
        fake = FakeRun(stdout='[{"pid": 1, "name": "x"}]')
        self.assertEqual(agents.list_claude_sessions(claude_bin="claude", run=fake), [])

    def test_caches_for_30_seconds(self):
        fake = FakeRun(stdout=RAW_JSON)
        clock = {"t": 1000.0}
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        clock["t"] += 5
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 1)  # second call served from cache
        clock["t"] += 30
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 2)  # cache expired, re-ran
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_agents -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents'`

- [ ] **Step 3: Implement**

Create `agents.py`:

```python
"""Wraps `claude agents --json` — the device-local source of truth for
every live Claude Code session, launcher-started or not."""
import json
import subprocess
import time
from typing import Optional

from config import CLAUDE_BIN

CACHE_TTL_SECONDS = 30

_cache = {"rows": [], "at": 0.0}


def _normalize(raw):
    session_id = raw.get("sessionId")
    if not session_id:
        return None
    return {
        "session_id": session_id,
        "name": raw.get("name"),
        "cwd": raw.get("cwd"),
        "kind": raw.get("kind"),
        "status": raw.get("status"),
        "started_at": raw.get("startedAt"),
        "pid": raw.get("pid"),
        "waiting_for": raw.get("waitingFor"),
    }


def list_claude_sessions(claude_bin=None, run=subprocess.run, now_fn=time.time):
    """Every live session `claude agents --json` reports on this device,
    normalized, cached for CACHE_TTL_SECONDS. Returns [] on any failure —
    missing binary, timeout, non-zero exit, malformed JSON — never raises.
    """
    now = now_fn()
    if now - _cache["at"] < CACHE_TTL_SECONDS:
        return _cache["rows"]

    bin_path = claude_bin or CLAUDE_BIN
    try:
        r = run([bin_path, "agents", "--json"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        _cache["rows"], _cache["at"] = [], now
        return []
    if r.returncode != 0:
        _cache["rows"], _cache["at"] = [], now
        return []
    try:
        parsed = json.loads(r.stdout)
    except (ValueError, TypeError):
        _cache["rows"], _cache["at"] = [], now
        return []
    if not isinstance(parsed, list):
        _cache["rows"], _cache["at"] = [], now
        return []

    rows = [n for n in (_normalize(row) for row in parsed if isinstance(row, dict)) if n]
    _cache["rows"], _cache["at"] = rows, now
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 167 tests ... OK` (160 + 7 new). Note the cache test relies on shared module state (`_cache`) — if other tests in the file run first and warm the cache with a different `now_fn` clock, the 30s-cache test could see stale data; if that happens, reset `agents._cache = {"rows": [], "at": 0.0}` in a `setUp` for `ListClaudeSessionsTest`.

- [ ] **Step 5: Commit**

```bash
git add agents.py tests/test_agents.py
git commit -m "feat: agents.py wraps claude agents --json, normalized rows, 30s cache"
```

---

### Task 12: `list_rc_sessions` merges in external (non-launcher) sessions

**Files:**
- Modify: `sessions.py:88-115` (`list_rc_sessions`)
- Test: `tests/test_setup_session.py` or `tests/test_agents.py`

**Interfaces:**
- Produces: `sessions.list_rc_sessions()` — unchanged for existing `rc-*` tmux rows (every field it already returns stays), **plus**: it now also calls `agents.list_claude_sessions()` and, for each normalized row whose `session_id` does **not** match any `RC_SESSION_ID` already found on an `rc-*` tmux session (matched via `get_session_env(name, "RC_SESSION_ID")` for every session already in the result list), appends a new dict shaped `{"name": <claude session name or a synthesized "external-<session_id[:8]>">, "mode": None, "url": None, "status": row["status"] or "unknown", "kind": "external", "external": True, "session_id": row["session_id"], "cwd": row["cwd"], "pid": row["pid"], "waiting_for": row["waiting_for"]}`. Existing `rc-*` rows are **not** given `kind`/`external` keys by this task (Task 13 adds `state` to all rows uniformly; scope here is strictly "make external sessions appear at all").
- Consumes: `agents.list_claude_sessions` (Task 11).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agents.py`:

```python
class ListRcSessionsMergesExternalTest(unittest.TestCase):
    def setUp(self):
        import sessions
        self.sessions = sessions
        self._patched_run = sessions.subprocess.run
        self._patched_env = sessions.get_session_env
        self._patched_list_claude = agents.list_claude_sessions

    def tearDown(self):
        self.sessions.subprocess.run = self._patched_run
        self.sessions.get_session_env = self._patched_env
        agents.list_claude_sessions = self._patched_list_claude

    def test_external_session_appears_alongside_launcher_sessions(self):
        class FakeRun:
            def __call__(self, cmd, **kw):
                class R:
                    returncode = 0
                    stderr = ""
                    stdout = "rc-portugal\n" if "list-sessions" in cmd else ""
                return R()
        self.sessions.subprocess.run = FakeRun()
        self.sessions.get_session_env = lambda name, var: {
            "RC_MODE": "c", "RC_WORKDIR": "/home/user/proj",
            "RC_SESSION_ID": "known-uuid-1",
        }.get(var)
        agents.list_claude_sessions = lambda: [
            {"session_id": "known-uuid-1", "name": "portugal", "cwd": "/home/user/proj",
             "kind": "interactive", "status": "idle", "started_at": 1, "pid": 1, "waiting_for": None},
            {"session_id": "external-uuid-2", "name": "hand-started", "cwd": "/home/user/other",
             "kind": "interactive", "status": "busy", "started_at": 2, "pid": 2, "waiting_for": None},
        ]

        result = self.sessions.list_rc_sessions()

        names = [s["name"] for s in result]
        self.assertIn("rc-portugal", names)
        external_rows = [s for s in result if s.get("kind") == "external"]
        self.assertEqual(len(external_rows), 1)
        self.assertEqual(external_rows[0]["session_id"], "external-uuid-2")
        self.assertTrue(external_rows[0]["external"])
        self.assertEqual(external_rows[0]["name"], "hand-started")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_agents.ListRcSessionsMergesExternalTest -v`
Expected: FAIL — `external_rows` is empty (`list_rc_sessions` doesn't consult `agents` yet).

- [ ] **Step 3: Implement**

Add `import agents` to `sessions.py`'s imports. Rewrite `list_rc_sessions` (`sessions.py:88-115`):

```python
def list_rc_sessions():
    """Return rc-* tmux sessions (name, mode, URL, workdir, status) plus
    any Claude Code session claude agents --json knows about that this
    launcher didn't start — those get kind: "external", no terminal."""
    r = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    sessions_list = []
    known_session_ids = set()
    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            name = line.strip()
            if not name.startswith(SESSION_PREFIX):
                continue
            mode = get_session_env(name, "RC_MODE") or "c"
            workdir = get_session_env(name, "RC_WORKDIR")
            is_sh = mode == SHELL_MODE
            url = None if is_sh else get_url(name)
            status = get_session_status(name)
            tokens = None if is_sh else get_tokens(name)
            rc_session_id = get_session_env(name, "RC_SESSION_ID")
            if rc_session_id:
                known_session_ids.add(rc_session_id)
            s = {"name": name, "mode": mode, "url": url, "status": status}
            if tokens is not None:
                s["tokens"] = tokens
            if workdir:
                s["workdir"] = workdir
                s["project"] = os.path.basename(workdir.rstrip("/"))
            sessions_list.append(s)

    for row in agents.list_claude_sessions():
        if row["session_id"] in known_session_ids:
            continue
        sessions_list.append({
            "name": row["name"] or f"external-{row['session_id'][:8]}",
            "mode": None,
            "url": None,
            "status": row["status"] or "unknown",
            "kind": "external",
            "external": True,
            "session_id": row["session_id"],
            "cwd": row["cwd"],
            "pid": row["pid"],
            "waiting_for": row["waiting_for"],
        })

    return sessions_list
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 168 tests ... OK`. Also run `python3 -m unittest tests.test_scheduler -v` specifically — `_adopt_live_sessions` (Task 0c) calls `list_rc_sessions()`, so confirm its fakes still supply a compatible shape (they mock `list_rc_sessions` directly, per Task 0c's fixture note, so this should be unaffected, but verify).

- [ ] **Step 5: Commit**

```bash
git add sessions.py tests/test_agents.py
git commit -m "feat: list_rc_sessions merges in non-launcher sessions from claude agents --json"
```

---

### Task 13: `GET /sessions` derives a `state` field per row

**Files:**
- Modify: `server.py` (find the `/sessions` GET branch: `grep -n 'elif path == "/sessions"' server.py`)
- Test: `tests/test_server_helpers.py`

**Interfaces:**
- Produces: `server._derive_session_state(session_row: dict) -> str` — pure function. Returns one of `"starting" | "busy" | "idle" | "needs_attention" | "ended"`:
  - `"needs_attention"` if `session_row.get("waiting_for")` is truthy (checked first — overrides everything else).
  - `"ended"` if `session_row.get("status") == "dead"` (matches the existing `get_session_status` vocabulary for launcher rows) or (`session_row.get("kind") == "external"` and `session_row.get("status") == "ended"`).
  - `"busy"` if `session_row.get("status") == "busy"` (external/agents-json rows) or `session_row.get("status") == "running"` and the row additionally reports high token/activity — **kept simple per spec scope**: `"busy"` whenever `status` is exactly `"busy"`.
  - `"starting"` if `session_row.get("status") in ("unknown", None)` and the row is *not* external (a brand-new launcher session mid-`setup_session` reports `"unknown"` from `get_session_status` before the pane settles).
  - `"idle"` otherwise (the default/healthy resting state — covers launcher `status == "running"` and external `status == "idle"`).
  Every `GET /sessions` row (both `list_rc_sessions` shapes from Task 12) gets a `"state"` key added via this function before the response is serialized.
- Consumes: nothing beyond the row dict shape already produced by `sessions.list_rc_sessions` (Task 12).

- [ ] **Step 1: Read the current `/sessions` handler**

Run: `grep -n 'elif path == "/sessions"' server.py` then read that branch fully.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_server_helpers.py`:

```python
class DeriveSessionStateTest(unittest.TestCase):
    def test_waiting_for_wins_over_everything(self):
        row = {"status": "busy", "waiting_for": "permission_prompt"}
        self.assertEqual(server._derive_session_state(row), "needs_attention")

    def test_dead_launcher_session_is_ended(self):
        row = {"status": "dead"}
        self.assertEqual(server._derive_session_state(row), "ended")

    def test_busy_status_is_busy(self):
        row = {"status": "busy", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "busy")

    def test_unknown_status_non_external_is_starting(self):
        row = {"status": "unknown"}
        self.assertEqual(server._derive_session_state(row), "starting")

    def test_none_status_non_external_is_starting(self):
        row = {"status": None}
        self.assertEqual(server._derive_session_state(row), "starting")

    def test_unknown_status_external_is_not_starting(self):
        row = {"status": "unknown", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_running_launcher_session_is_idle(self):
        row = {"status": "running"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_idle_external_session_is_idle(self):
        row = {"status": "idle", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_ended_external_session(self):
        row = {"status": "ended", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "ended")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.DeriveSessionStateTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_derive_session_state'`

- [ ] **Step 4: Implement**

Add to `server.py`, near the other small pure helpers (e.g. near `_session_cap_message`):

```python
def _derive_session_state(session_row):
    """One of starting|busy|idle|needs_attention|ended from a session row
    (either shape list_rc_sessions returns: a launcher rc-* row with
    status running|dead|unknown, or an external row with status from
    claude agents --json: idle|busy|ended)."""
    if session_row.get("waiting_for"):
        return "needs_attention"
    status = session_row.get("status")
    if status == "dead" or status == "ended":
        return "ended"
    if status == "busy":
        return "busy"
    if status in ("unknown", None) and session_row.get("kind") != "external":
        return "starting"
    return "idle"
```

In the `/sessions` GET branch, after calling `sessions.list_rc_sessions()` (or whatever the existing local variable is named — confirm from Step 1's read), add one line before serializing the response:

```python
            for s in rc_sessions:  # use the actual variable name from Step 1
                s["state"] = _derive_session_state(s)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 177 tests ... OK` (168 + 9 new).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server_helpers.py
git commit -m "feat: GET /sessions rows carry a derived state (starting/busy/idle/needs_attention/ended)"
```

---

### Task 14: `POST /stop` on an external session signals the process directly

**Files:**
- Modify: `server.py` (find the `/stop` POST branch: `grep -n 'elif path == "/stop"' server.py`)
- Test: `tests/test_server_helpers.py`

**Interfaces:**
- Produces: `server._stop_external_pid(pid: int, run=subprocess.run) -> tuple[bool, str]` — new helper. Reads `/proc/<pid>/cmdline` (via `open`, not `run` — no subprocess needed for this check on Linux; on a non-Linux/no-`/proc` platform where the read fails, treat as "cannot verify, refuse") and only sends `SIGTERM` (via `os.kill(pid, signal.SIGTERM)`, itself wrapped so `run` isn't actually used for the kill either — `run` is accepted only for symmetry/testability of a future subprocess-based verification and is unused in the Linux `/proc` path; document this in the docstring) when the cmdline's first argv token's basename is exactly `"claude"`. Returns `(True, "Stopped")` on success, `(False, "<reason>")` otherwise (`"Not a claude process"`, `"Process not found"`, `"Permission denied"`). The `/stop` POST branch, when the target session name is not an `rc-*` prefixed name (i.e. this is a stop request for an external session, identified by the client sending `{"session_id": "...", "pid": N}` instead of `{"name": "rc-..."}` — read the existing `/stop` branch in Step 1 to match its actual request-body contract and extend it rather than replace it) calls `_stop_external_pid` instead of the existing tmux-based stop path, and is rejected with 400 when the pid's cmdline doesn't verify as claude.

- [ ] **Step 1: Read the current `/stop` handler and the `rc-` prefix enforcement pattern**

Run: `grep -n 'elif path == "/stop"' server.py` and read that branch. Also read how `server.py:1034` (`/keys`), `:1010` (`/resize`) currently enforce the `SESSION_PREFIX` check (mentioned in the spec background) — Phase 0 task 0's prefix-enforcement work already landed on `main` per the spec's "Native versus hub" note that Phase 0 shipped as v2.1.3, so this pattern should already exist in this worktree; find and reuse it rather than inventing a new one. Confirm with: `grep -n "SESSION_PREFIX" server.py`.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_server_helpers.py`:

```python
class StopExternalPidTest(unittest.TestCase):
    def test_refuses_non_claude_process(self):
        import unittest.mock as mock
        with mock.patch("builtins.open", mock.mock_open(read_data=b"/usr/bin/python3\x00script.py\x00")):
            ok, reason = server._stop_external_pid(99999)
        self.assertFalse(ok)
        self.assertEqual(reason, "Not a claude process")

    def test_refuses_when_cmdline_unreadable(self):
        import unittest.mock as mock
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            ok, reason = server._stop_external_pid(99999)
        self.assertFalse(ok)
        self.assertEqual(reason, "Process not found")

    def test_stops_a_verified_claude_process(self):
        import unittest.mock as mock
        with mock.patch("builtins.open", mock.mock_open(read_data=b"/usr/local/bin/claude\x00--resume\x00")), \
             mock.patch("os.kill") as fake_kill:
            ok, reason = server._stop_external_pid(12345)
        self.assertTrue(ok)
        self.assertEqual(reason, "Stopped")
        fake_kill.assert_called_once()
        import signal
        self.assertEqual(fake_kill.call_args[0], (12345, signal.SIGTERM))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.StopExternalPidTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_stop_external_pid'`

- [ ] **Step 4: Implement**

Add `import signal` to `server.py`'s top-level imports if not already present (`grep -n "^import signal" server.py`). Add the helper near the other small pure/near-pure helpers:

```python
def _stop_external_pid(pid, run=subprocess.run):
    """Stop a non-launcher (external) session by signaling its process
    directly, only after verifying /proc/<pid>/cmdline's first argv token
    is literally 'claude' — this is the only guard between "stop any
    session shown in the UI" and "kill an arbitrary pid a browser named",
    since external rows have no rc-* tmux session to scope the request to.
    `run` is accepted for interface symmetry with other server.py helpers
    that inject subprocess.run for testability, but the actual check reads
    /proc directly (Linux-only) rather than shelling out.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except (FileNotFoundError, OSError):
        return False, "Process not found"
    parts = raw.split(b"\x00")
    argv0 = parts[0].decode(errors="replace") if parts else ""
    if os.path.basename(argv0) != "claude":
        return False, "Not a claude process"
    try:
        os.kill(pid, signal.SIGTERM)
    except PermissionError:
        return False, "Permission denied"
    except ProcessLookupError:
        return False, "Process not found"
    return True, "Stopped"
```

In the `/stop` POST branch, after the existing `rc-` prefix / session-name handling (read exactly how the branch currently extracts `name`/pid from `self._read_body()` in Step 1), add a case for an external-session stop request:

```python
        elif path == "/stop":
            body = self._read_body()
            if body.get("external") and body.get("pid"):
                ok, reason = _stop_external_pid(int(body["pid"]))
                self._json({"ok": ok, "message": reason}, 200 if ok else 400)
                return
            # ... existing rc-* tmux stop path unchanged below ...
```

(Match this insertion to the exact existing branch structure found in Step 1 — the pseudocode above shows the *shape* of the addition; use the real variable names and control flow already present so the existing tmux-stop path for `rc-*` sessions is untouched.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: `Ran 180 tests ... OK` (177 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server_helpers.py
git commit -m "feat: POST /stop on an external session SIGTERMs its pid after verifying it's a claude process"
```

---

### Task 15: Frontend — desktop "Tasks" and "Sessions" cross-device tabs

**Files:**
- Modify: `frontend/src/App.tsx` (`OverviewGrid` call site and the surrounding view-mode state)
- Test: none (no frontend test runner in this repo; verified by `npm run build` and manual read-through, matching Tasks 16-19's verification style)

**Interfaces:**
- Produces: a new piece of state in `App.tsx`, `desktopView: 'devices' | 'tasks' | 'sessions'` (default `'devices'`), shown only when `!openCard && !layout.mobile` (the desktop "All devices" overview — mobile already has this via `MobileNav`'s `scheduled`/`sessions` tabs, wired to `AllScheduled`/`AllSessions` in the existing `mTab !== 'devices'` branch, which this task does not touch). A small tab strip (three buttons: "Devices", "Tasks", "Sessions") renders above `OverviewGrid` on desktop; selecting "Tasks" renders `<AllScheduled cards={cards} />` in place of `OverviewGrid`, selecting "Sessions" renders `<AllSessions cards={cards} onOpenDevice={handleOpenDevice} />` (same components the mobile path already uses — `AllScheduled`/`AllSessions` are cards-driven and layout-agnostic despite the `Mobile*`-prefixed child components they use internally, e.g. `MobileHeader`, which is in fact just a generic page header, not actually mobile-specific; confirmed by reading both files — neither branches on `layout.mobile`). This closes the original ask: "consolidated tab to see all scheduled on all devices," previously desktop-only reachable by opening each device individually.
- Consumes: `AllScheduled` (existing, `frontend/src/components/AllScheduled.tsx`, cards-only prop), `AllSessions` (existing, `frontend/src/components/AllSessions.tsx`, `cards`+`onOpenDevice` props), `useAllSchedules`/`useAllSessions` (existing, `frontend/src/useCrossDevice.ts` — both already used internally by the two components above, not called directly by `App.tsx`).

- [ ] **Step 1: Read the current desktop overview branch and confirm `handleOpenDevice` already exists**

Run: `grep -n "handleOpenDevice\|OverviewGrid\|openCard" frontend/src/App.tsx`. The mobile branch already calls `<AllSessions cards={cards} onOpenDevice={handleOpenDevice} />` — reuse that exact prop, don't invent a new callback.

- [ ] **Step 2: Add `desktopView` state and the tab strip**

In `App.tsx`, add state near the other `useState` calls at the top of `App()`:

```typescript
  const [desktopView, setDesktopView] = useState<'devices' | 'tasks' | 'sessions'>('devices');
```

Replace the existing desktop-overview branch:

```tsx
          ) : openCard ? (
            // Device detail — full main area
            <DeviceDetail
              device={openCard}
              cards={cards}
              tab={tab}
              setTab={setTab}
              onClose={() => handleOpen(null)}
              layout={layout}
            />
          ) : (
            // Overview grid — big cards
            <OverviewGrid
              cards={cards}
              layout={layout}
              onOpen={handleOpen}
            />
          )}
```

with:

```tsx
          ) : openCard ? (
            // Device detail — full main area
            <DeviceDetail
              device={openCard}
              cards={cards}
              tab={tab}
              setTab={setTab}
              onClose={() => handleOpen(null)}
              layout={layout}
            />
          ) : layout.mobile ? (
            // Overview grid — big cards
            <OverviewGrid
              cards={cards}
              layout={layout}
              onOpen={handleOpen}
            />
          ) : (
            // Desktop "All devices" overview: Devices / Tasks / Sessions.
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
              <div style={{
                flex: 'none', display: 'flex', gap: 4, padding: '10px 24px 0',
                borderBottom: `1px solid ${RT.border}`,
              }}>
                {(['devices', 'tasks', 'sessions'] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setDesktopView(v)}
                    style={{
                      background: 'transparent', border: 'none', cursor: 'pointer',
                      padding: '8px 12px', fontFamily: FONT_SANS, fontSize: 13,
                      color: desktopView === v ? RT.text : RT.textLow,
                      borderBottom: `2px solid ${desktopView === v ? RT.text : 'transparent'}`,
                      textTransform: 'capitalize',
                    }}
                  >{v === 'devices' ? 'Devices' : v === 'tasks' ? 'Tasks' : 'Sessions'}</button>
                ))}
              </div>
              <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minHeight: 0 }}>
                {desktopView === 'devices' && (
                  <OverviewGrid cards={cards} layout={layout} onOpen={handleOpen} />
                )}
                {desktopView === 'tasks' && <AllScheduled cards={cards} />}
                {desktopView === 'sessions' && (
                  <AllSessions cards={cards} onOpenDevice={handleOpenDevice} />
                )}
              </div>
            </div>
          )}
```

`AllScheduled` and `AllSessions` are already imported at the top of `App.tsx` (used by the mobile branch) — no new imports needed.

- [ ] **Step 3: Build and verify**

Run: `cd frontend && npm run build`
Expected: builds cleanly. Read the rendered tab strip mentally against `RT`/`FONT_SANS` tokens already imported in `App.tsx` (`grep -n "^import { RT" frontend/src/App.tsx` confirms both are in scope) — no new token imports needed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): desktop All-devices overview gets Tasks and Sessions tabs alongside Devices"
```

---

### Task 16: Frontend — per-device Claude Code version, highlighted when it differs from the hub

**Files:**
- Modify: `frontend/src/types.ts` (`DeviceCard`)
- Modify: `overview.py` (`card_from_parts`, `fetch_remote_card`)
- Modify: `frontend/src/components/DeviceHero.tsx` or wherever the device card renders its stat line (check first) and `frontend/src/components/DeviceDetail.tsx`'s header
- Test: `tests/test_overview.py` (extend)

**Interfaces:**
- Produces: `overview.card_from_parts(device, sessions, stats, online=None)` gains one more field read from `stats`: `"claude_version": stats.get("claude_version") if stats else None` (mirrors exactly how `os`/`user`/`home_dir` are already pulled from the same `stats` dict — Task 1 already put `claude_version` into every device's `/stats` response, so no new device-side endpoint is needed, only reading the field that's already there). `DeviceCard` (TypeScript) gains `claude_version?: string`. On the device card and in `DeviceDetail`'s header, `claude_version` is shown as `Claude Code <version>`; when it differs from the **hub's own** `claude_version` (the local device's `card.claude_version`, i.e. `cards.find(c => c.id === 'local')?.claude_version` — `overview.py`'s `build_overview` always makes the local device the first card with `id: "local"`, confirmed by reading `build_overview` above), the text is styled with `RT.amber` instead of the default `RT.textLow` to flag skew, matching the spec's config-parity intent without building the full Phase 6 parity matrix.
- Consumes: `compat.claude_version()` (Task 1, already flowing into `/stats`).

- [ ] **Step 1: Write the failing backend test**

Read `tests/test_overview.py`'s existing `card_from_parts` tests first (`grep -n "def test" tests/test_overview.py`) to match its fixture style exactly, then add:

```python
class CardFromPartsClaudeVersionTest(unittest.TestCase):
    def test_claude_version_read_from_stats(self):
        card = overview.card_from_parts(
            {"id": "local", "name": "local"}, [], {"claude_version": "2.1.263"})
        self.assertEqual(card["claude_version"], "2.1.263")

    def test_claude_version_none_when_stats_missing(self):
        card = overview.card_from_parts({"id": "local", "name": "local"}, [], None)
        self.assertIsNone(card["claude_version"])

    def test_claude_version_none_when_absent_from_stats(self):
        card = overview.card_from_parts({"id": "local", "name": "local"}, [], {})
        self.assertIsNone(card["claude_version"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_overview.CardFromPartsClaudeVersionTest -v`
Expected: FAIL — `KeyError: 'claude_version'`

- [ ] **Step 3: Implement the backend field**

In `overview.py`'s `card_from_parts`, add one line inside the `return { ... }` dict (alongside `"user": user, "home_dir": home_dir,`):

```python
        "user": user, "home_dir": home_dir,
        "claude_version": (stats or {}).get("claude_version"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover tests`
Expected: 3 new tests pass, full suite green.

- [ ] **Step 5: Commit the backend half**

```bash
git add overview.py tests/test_overview.py
git commit -m "feat: device cards carry claude_version (read from /stats, already populated by compat.py)"
```

- [ ] **Step 6: Add the TypeScript field**

In `frontend/src/types.ts`, add to `DeviceCard` (alongside the existing `home_dir?: string;`):

```typescript
  /** Claude Code version running on this device, from compat.claude_version(). May be absent on an older backend or when claude isn't installed. */
  claude_version?: string;
```

- [ ] **Step 7: Render it on the device card and in `DeviceDetail`'s header**

Run: `grep -rn "hostname\|c.os\b" frontend/src/components/DeviceHero.tsx frontend/src/components/BigCard.tsx frontend/src/components/DeviceDetail.tsx 2>/dev/null` to find exactly where the device's `os`/`hostname` line is rendered on the card (`BigCard.tsx` is the likely home for the grid-card stat line given its name; `DeviceHero.tsx`/`DeviceDetail.tsx` for the open-device header — read whichever files the grep surfaces).

Add, next to that existing os/hostname text, a conditionally-rendered version string using the hub-vs-device comparison:

```tsx
{c.claude_version && (() => {
  const hubVersion = cards.find((x) => x.id === 'local')?.claude_version;
  const skewed = hubVersion && c.claude_version !== hubVersion;
  return (
    <span style={{ fontFamily: FONT_MONO, fontSize: 10, color: skewed ? RT.amber : RT.textLow }}>
      Claude Code {c.claude_version}
    </span>
  );
})()}
```

(Adjust the variable name `c` to whatever the surrounding component actually calls its device-card prop — `BigCard.tsx` likely destructures a `card` prop, `DeviceDetail.tsx` a `device` prop; match the existing local name rather than introducing `c`. Both call sites need `cards` in scope to compute `hubVersion` — `BigCard` is rendered from `OverviewGrid`, which already receives the full `cards` array as a prop, so thread it through as a new `cards` prop on `BigCard` if it doesn't already take one; `DeviceDetail` already receives `cards` per its existing props list read earlier in this plan (see the App.tsx/DeviceDetail prop read in Task 15 Step 1).)

- [ ] **Step 8: Build and verify**

Run: `cd frontend && npm run build`
Expected: builds cleanly.

- [ ] **Step 9: Commit the frontend half**

```bash
git add frontend/src/types.ts frontend/src/components/BigCard.tsx frontend/src/components/DeviceHero.tsx frontend/src/components/DeviceDetail.tsx frontend/src/components/OverviewGrid.tsx
git commit -m "feat(frontend): show per-device Claude Code version, highlighted when it differs from the hub"
```

(Only `git add` the files Step 7 actually touched — the list above is a superset; `App.tsx`'s inline `OverviewGrid` function, not a separate file, may need the `cards` pass-through instead of a nonexistent `OverviewGrid.tsx`, per the repo survey showing `OverviewGrid` defined inline in `App.tsx` — drop that path if so and add `frontend/src/App.tsx` instead.)

---

### Task 17: Frontend — explicit "Check for updates" item in the header Menu

**Files:**
- Modify: `frontend/src/components/Header.tsx` (`GlobalMenu`)

**Interfaces:**
- Produces: a new menu item in `GlobalMenu` (`frontend/src/components/Header.tsx`), placed between the existing "Refresh" item and the divider before "Classic UI", labeled "Check for updates". Clicking it calls `api.updateCheck()` (existing, `frontend/src/api.ts:49`) directly (not through `VersionChip`'s own polling state, which stays untouched) and: if `update_available` is false, shows a transient result via `window.alert(`Up to date (v${data.current}).`)`; if `update_available` is true, reuses the exact same two-step confirm flow `VersionChip.handleUpdate` already implements (call `api.update()`, on `remote_sha` prompt `window.confirm` with pending commits, then `api.update({ confirm: result.remote_sha })`) — extracted into a shared helper so the logic is written once. This makes the update check discoverable even when `VersionChip` hasn't rendered a banner (e.g. its own poll hasn't completed yet, or a previous poll happened to run when GitHub was unreachable and cached a stale "no update" result for 10 minutes per `/update-check`'s server-side cache — the menu item always makes a fresh call, subject to that same 10-minute server cache, not a client-side one).
- Consumes: `api.updateCheck()`, `api.update()` (both existing, unchanged).

- [ ] **Step 1: Read `VersionChip.handleUpdate` in full**

Re-read `frontend/src/components/Header.tsx`'s `VersionChip` component (the `handleUpdate` function) — this task extracts its two-step confirm logic into a function `runUpdateFlow()` callable from two places (`VersionChip`'s existing button and the new menu item) instead of duplicating it.

- [ ] **Step 2: Extract `runUpdateFlow` as a module-level function in `Header.tsx`**

Above `VersionChip`, add:

```typescript
async function runUpdateFlow(onDone: () => void) {
  try {
    let result = await api.update();
    if (!result.ok && result.remote_sha) {
      const commits = (result.pending_commits || []).join('\n');
      const confirmed = window.confirm(
        `Update to ${result.remote_sha}?\n\nPending commits:\n${commits || '(none)'}`
      );
      if (!confirmed) {
        onDone();
        return;
      }
      result = await api.update({ confirm: result.remote_sha });
    }
    if (!result.ok) {
      window.alert(result.message || 'Update failed.');
      onDone();
      return;
    }
  } catch {/* likely network error as server restarts — expected */}
  setTimeout(() => { window.location.reload(); }, 3000);
}
```

Change `VersionChip.handleUpdate` to:

```typescript
  const handleUpdate = () => {
    setUpdating(true);
    runUpdateFlow(() => setUpdating(false));
  };
```

- [ ] **Step 3: Add the menu item to `GlobalMenu`**

In `GlobalMenu`, add a handler alongside `handleStopAll`:

```typescript
  const [checking, setChecking] = useState(false);

  const handleCheckUpdates = async () => {
    setChecking(true);
    setOpen(false);
    try {
      const data = await api.updateCheck() as { update_available: boolean; current?: string; latest?: string };
      if (data.update_available) {
        runUpdateFlow(() => {});
      } else {
        window.alert(`Up to date (v${data.current ?? data.latest ?? '?'}).`);
      }
    } catch {
      window.alert('Could not check for updates.');
    } finally {
      setChecking(false);
    }
  };
```

Insert the menu item between the existing "Refresh" button and the `<div style={{ height: 1, ... }} />` divider that precedes "Classic UI":

```tsx
          <button
            style={{ ...menuItemStyle, opacity: checking ? 0.6 : 1 }}
            onClick={checking ? undefined : handleCheckUpdates}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
          >
            <Icons.refresh size={11} stroke={RT.textDim} />
            {checking ? 'Checking…' : 'Check for updates'}
          </button>
```

(Reuse `Icons.refresh` — no new icon needed; the existing "Refresh" item uses the same icon for a different action, which is fine, matching this file's existing icon reuse elsewhere.)

- [ ] **Step 4: Build and verify**

Run: `cd frontend && npm run build`
Expected: builds cleanly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Header.tsx
git commit -m "feat(frontend): explicit 'Check for updates' item in the header Menu"
```

---

### Task 18: Frontend — state pill and external badge on session rows

**Files:**
- Modify: `frontend/src/types.ts` (add `state` and `external`/`kind` to the `Session` type)
- Modify: `frontend/src/components/SessionRow.tsx` (`V5StatusPill`, action buttons)
- Modify: `frontend/src/components/AllSessions.tsx` (pass through new fields if it does its own row rendering — check first)
- Test: none (no frontend test runner in this repo — verified by `npm run build` succeeding and a manual read-through; this mirrors how Phase 0's frontend changes were verified per its plan header)

**Interfaces:**
- Produces: `Session` type (in `frontend/src/types.ts`) gains `state?: 'starting' | 'busy' | 'idle' | 'needs_attention' | 'ended'` and `kind?: 'external'` (both optional — existing launcher rows may lag behind a backend that hasn't been redeployed, so the frontend must degrade gracefully when they're absent). `V5StatusPill` (in `SessionRow.tsx`) gains a `needs_attention` entry in its `map` (amber/pulsing, matching the existing `thinking` entry's styling) and a `starting` entry (use `RT.textLow`, non-pulsing, label `"starting"`) alongside the existing `running|thinking|idle|stopped` entries — `state`, when present on the row, is what gets passed to `V5StatusPill` instead of the row's raw `status` (read `SessionRow.tsx`'s current call site for `V5StatusPill` to find exactly what it's passed today, and change that call to prefer `s.state ?? <existing status-derivation expression>`). When `s.kind === 'external'`, `SessionRow` renders a small "external" badge next to the name (plain text pill, no icon, styled with `RT.textLow`/`RT.border` like the existing pills) and **omits** the preview and keys/terminal-access `V5IconButton`s (an external session has no rc-* tmux session for the launcher to attach a terminal to) while **keeping** the stop button.
- Consumes: `GET /sessions` response rows now carrying `state`/`kind`/`external` (Task 13/14, once deployed — the box this frontend build runs against is not touched by this plan, so this is forward-compatible code, not code exercised end-to-end here).

- [ ] **Step 1: Read the current `Session` type and `SessionRow`'s status-pill call site**

Run: `grep -n "interface Session" -A 15 frontend/src/types.ts` and `grep -n "V5StatusPill" frontend/src/components/SessionRow.tsx`.

- [ ] **Step 2: Extend the `Session` type**

In `frontend/src/types.ts`, add to the `Session` interface (exact position: alongside the existing `status` field):

```typescript
  state?: 'starting' | 'busy' | 'idle' | 'needs_attention' | 'ended';
  kind?: 'external';
  external?: boolean;
  session_id?: string;
  waiting_for?: string | null;
```

- [ ] **Step 3: Extend `V5StatusPill` and its call site in `SessionRow.tsx`**

In `SessionRow.tsx`, change the `map` inside `V5StatusPill` (currently `running|thinking|idle|stopped`) to:

```typescript
  const map: Record<string, { label: string; color: string; pulse: boolean }> = {
    running:  { label: 'running',  color: RT.green,   pulse: true  },
    thinking: { label: 'thinking', color: RT.amber,   pulse: true  },
    idle:     { label: 'idle',     color: RT.textLow, pulse: false },
    stopped:  { label: 'stopped',  color: RT.red,     pulse: false },
    busy:            { label: 'busy',            color: RT.amber,   pulse: true  },
    starting:        { label: 'starting',        color: RT.textLow, pulse: false },
    needs_attention: { label: 'needs attention', color: RT.amber,   pulse: true  },
    ended:           { label: 'ended',           color: RT.red,     pulse: false },
  };
```

Find the JSX call site rendering `<V5StatusPill status={...} />` and change its `status` prop to prefer the new field: `status={s.state ?? <whatever expression was there before>}`.

- [ ] **Step 4: Add the external badge and hide terminal-only actions**

Near where `SessionRow` renders the session name (find with `grep -n "s.name\|{truncate" frontend/src/components/SessionRow.tsx`), add, right after the name:

```tsx
          {s.kind === 'external' && (
            <span style={{
              fontSize: 9, letterSpacing: '.06em', textTransform: 'uppercase',
              fontFamily: FONT_MONO, padding: '1px 6px', borderRadius: 4,
              border: `1px solid ${RT.border}`, color: RT.textLow, flex: 'none',
            }}>external</span>
          )}
```

Find the block rendering the preview and keys/terminal `V5IconButton`s (`grep -n "V5IconButton" frontend/src/components/SessionRow.tsx`) and wrap that group's JSX in `{s.kind !== 'external' && (...)}`. Locate the stop-button `V5IconButton` specifically and confirm it stays **outside** that new wrapper (it must still render for external rows) — if stop and preview/keys are currently rendered in one contiguous `<>...</>` fragment, split the fragment so stop is a sibling, not nested inside the new conditional.

- [ ] **Step 5: Check `AllSessions.tsx` for a second rendering path**

Run: `grep -n "status\|V5StatusPill\|SessionRow" frontend/src/components/AllSessions.tsx`. If `AllSessions.tsx` renders `<SessionRow>` for every row (most likely, since it's the cross-device consolidated view), no further change is needed — it inherits everything from Steps 2-4 automatically. If it has its own inline status-pill rendering independent of `SessionRow`, apply the same `state`-preferred / `external`-badge treatment there, mirroring Steps 3-4 exactly.

- [ ] **Step 6: Build the frontend to verify no TypeScript errors**

Run: `cd frontend && npm run build`
Expected: builds cleanly (TypeScript strictness will flag any place `Session.status` was assumed non-optional in a way the new optional `state` field conflicts with — fix any such error by falling back to the pre-existing expression, exactly as Step 3 specifies).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/SessionRow.tsx frontend/src/components/AllSessions.tsx
git commit -m "feat(frontend): session rows show derived state pill and external badge"
```

(Do not rebuild `static/dist` yet — Task 19 does the final rebuild after `claude_version` is also wired into the header, so there's exactly one dist rebuild commit for this phase, matching Phase 0's convention of a single "rebuild static/dist" task at the end.)

---

### Task 19: Frontend — show `claude_version` in the device header, rebuild `static/dist`

**Files:**
- Modify: `frontend/src/components/DeviceHero.tsx` or `frontend/src/components/Header.tsx` (whichever renders the per-device version string today — check both)
- Modify: `frontend/src/api.ts` if its `Device`/`Stats` response type needs the new field
- Modify (generated): `static/dist/**` (rebuilt, not hand-edited)

**Interfaces:**
- Produces: the device header shows `Claude Code <claude_version>` (e.g. `"Claude Code 2.1.263"`) next to (or below) the existing launcher `VERSION` string, reading it from the `/version` or `/stats` response field added in Task 1. If `claude_version` is `null`/absent (older backend, or `claude` isn't installed), the extra text is simply omitted — no "unknown" placeholder, no layout shift beyond the missing text.
- Consumes: `GET /version` / `GET /stats` response now including `claude_version` (Task 1).

- [ ] **Step 1: Find where the launcher's own `VERSION` is currently displayed**

Run: `grep -rn "version" frontend/src/components/DeviceHero.tsx frontend/src/components/Header.tsx frontend/src/components/DeviceDetail.tsx 2>/dev/null | grep -iv "//\|import"`. Also check `frontend/src/api.ts` for the TypeScript response type of whichever endpoint (`/version` or `/stats`) the frontend already calls for this — check `frontend/src/usePanelData.ts` and `frontend/src/useCrossDevice.ts` for the fetch call.

- [ ] **Step 2: Add `claude_version` to the relevant TypeScript response type**

In `frontend/src/api.ts` (or wherever the `/version`/`/stats` response is typed), add `claude_version?: string | null;` and `caps?: Record<string, boolean | string | null>;` to that interface, matching the exact field names Task 1 added server-side.

- [ ] **Step 3: Render it**

In whichever component Step 1 identified, add a small text node next to the existing version string, conditionally rendered:

```tsx
{device.claude_version && (
  <span style={{ color: RT.textLow, fontSize: 11 }}>Claude Code {device.claude_version}</span>
)}
```

(Adjust the exact JSX to match the surrounding component's existing style tokens and layout — read 20 lines of context around the current version display before inserting, so the new span sits naturally rather than breaking the existing flex/grid layout.)

- [ ] **Step 4: Build and verify**

Run: `cd frontend && npm run build`
Expected: builds cleanly with no TypeScript errors.

- [ ] **Step 5: Rebuild `static/dist` and commit everything from this phase's frontend work in one commit**

Run: `cd frontend && npm run build` (if Step 4's build already wrote to `../static/dist`, this is the same command — confirm the build output path with `grep -n "outDir" frontend/vite.config.ts` first; if it writes elsewhere, copy/move to `static/dist` matching whatever mechanism Phase 0's equivalent final task used — check `docs/superpowers/plans/2026-09-06-v3-phase0-foundations.md` for its own "rebuild static/dist" task to mirror the exact command).

```bash
git add frontend/src/api.ts frontend/src/components/DeviceHero.tsx frontend/src/components/Header.tsx frontend/src/components/DeviceDetail.tsx static/dist
git commit -m "feat(frontend): show claude_version in device header; rebuild static/dist"
```

(Only `git add` the files actually touched in Steps 1-3 plus `static/dist` — the list above is a superset covering every file Step 1 might have found it in; drop whichever of `DeviceHero.tsx`/`Header.tsx`/`DeviceDetail.tsx` turned out not to be the right one.)

- [ ] **Step 6: Final full-suite verification**

Run: `python3 -m unittest discover tests`
Expected: `Ran 180 tests ... OK` (backend test count is unaffected by frontend work in Tasks 15-16).

---

### Task 20: `mcp_server.py` and `README.md` — surface the new fields (short)

**Files:**
- Modify: `mcp_server.py` (wherever it documents/returns session fields to an MCP client — check `grep -n "status\|def _handle_tool_call" mcp_server.py`)
- Modify: `README.md` (wherever it documents `GET /sessions`/`/version` response shape, if at all — check `grep -n "GET /sessions\|GET /version\|/stats" README.md`)

**Interfaces:**
- No new production interfaces — this task is documentation/pass-through only. If `mcp_server.py`'s session-listing tool already forwards whatever `GET /sessions` returns verbatim (likely, since it's a thin proxy per its `_api_call`/`_handle_tool_call` structure read in the repo survey), no code change is needed there beyond confirming it — add one line to its tool description string (wherever the MCP tool schema/description for the sessions-listing tool is defined) noting the new `state`/`kind`/`external` fields exist, so an MCP client (Claude itself, using this server) knows to look for them.

- [ ] **Step 1: Confirm `mcp_server.py` forwards `/sessions` fields verbatim**

Run: `grep -n "_api_call\|/sessions" mcp_server.py`. Read the function that lists sessions. If it JSON-passes-through the `GET /sessions` response body without filtering/reshaping keys, the new `state`/`kind`/`external`/`waiting_for` fields already flow through with zero code change — confirm this by reading the actual code, not assuming it.

- [ ] **Step 2: Update the tool description (if `mcp_server.py` defines one) and README**

If the sessions-listing MCP tool has a `description` string in its schema (search `grep -n "description" mcp_server.py`), append one clause: `" Rows include a derived 'state' (starting/busy/idle/needs_attention/ended) and, for sessions this launcher didn't start, kind: 'external'."`

In `README.md`, find any existing documentation of the `/sessions` response shape or the `/version`/`/stats` endpoints (`grep -n "GET /sessions\|GET /version\|GET /stats" README.md`). If such documentation exists, add the new fields (`state`, `kind`, `external`, `claude_version`, `caps`) to it, following the exact formatting already used there (table, bullet list, or code block — match what's already present). If no such documentation exists in `README.md` today, skip this file — do not introduce a new documentation section as a side effect of this task; this task closes out field-level documentation drift only where documentation already exists.

- [ ] **Step 3: Run the full suite one last time**

Run: `python3 -m unittest discover tests`
Expected: `Ran 180 tests ... OK`

- [ ] **Step 4: Commit**

```bash
git add mcp_server.py README.md
git commit -m "docs: mcp_server and README note the new session state/kind/claude_version fields"
```

(If Step 2 found nothing to change in either file, skip this commit entirely — do not create an empty commit.)

---

## Self-review notes

- Spec coverage: `compat.py` (Task 1), `build_tmux_command` native flags + tmux-env `RC_SESSION_ID`/`RC_TITLE` at creation (Tasks 3-4), `setup_session` slimmed with feature gating (Task 5), `get_transcript`/`restart_session`/`resume_session`/`list_resumable_sessions` UUID-first with title-scan fallback (Tasks 8-10), `get_url` OSC 8 fix (Task 6), `list_rc_sessions` external-session merge (Task 12), `RC_FLAGS` → `permission_mode` (Task 2), `agents.py` (Task 11), `state` field (Task 13), external `/stop` (Task 14), frontend pill/badge/version (Tasks 15-16), the three Phase-0-review parked minors (Task 0a-0c) are all covered. `rcservers.py` (spec's "Optional 1b") is explicitly out of scope per the task-count/ordering given in the assignment (17 tasks, none named for it) and is not claimed as done anywhere above.
- Placeholder scan: no `TBD`/`implement later`/bare "add error handling" left in any step; every code step has literal code or an exact grep/read instruction plus the resulting change described concretely.
- Type consistency: `build_tmux_command`'s new `session_id` kwarg name and `agents.list_claude_sessions`'s normalized row keys (`session_id`, `waiting_for`, etc.) are used identically across Tasks 3-4, 8-9, 11-13 and the frontend `Session` type in Task 18. `DeviceCard.claude_version` (Task 16) and `overview.card_from_parts`'s new `claude_version` key match exactly.
- QA-pass additions (items A/B/C from a live v2.1.3 review, appended after the initial draft): Task 15 (desktop Tasks/Sessions tabs, closing the original "consolidated tab" ask — ordered first among the frontend tasks per the request), Task 16 (per-device version + hub-skew highlight), Task 17 (explicit "Check for updates" menu item), all placed before the final `static/dist` rebuild in Task 19 so there is still exactly one rebuild commit.
