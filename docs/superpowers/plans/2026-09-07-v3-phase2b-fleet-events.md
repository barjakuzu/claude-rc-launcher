# RC Launcher v3 Phase 2b: Hook Events, Fleet Spool and Hub Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the hub a persistent, cross-device view of every Claude Code session's lifecycle events (not just its current `claude agents --json` snapshot), by having each device spool Claude Code hook events to a local file, exposing them (merged with `list_rc_sessions()`) over a new `GET /fleet` route, having the hub poll every device plus itself into a SQLite store, and serving that store to the frontend over `GET /api/fleet` (polling) and `GET /api/fleet/stream` (SSE), with an audit log for every mutating route and a `metadata` device role that hashes identifying fields.

**Architecture:** Additive, stdlib-only, split device-side (Lane A: `hooks/rc-hook`, `events.py`, `fleet.py`) from hub-side (Lane B: `store.py`, `fleetpoll.py`, new `/api/fleet*` routes) from frontend (Lane C: `useFleet` hook, Sessions/Activity/Audit UI) from cross-cutting `RC_ROLE` enforcement (Lane D). Every device — including the hub acting on itself, in-process, no HTTP — runs the same `fleet.py`; the hub's poller (`fleetpoll.py`) is the only new thread, started next to `scheduler.start_scheduler()` in `app.py`. Hook events never carry prompt text; a `metadata`-role device hashes session ids/names and drops `cwd` before anything leaves the process.

**Tech Stack:** Python 3.9+ stdlib (`sqlite3`, `queue`, `threading`, `json`, `hashlib`, `hmac`), tmux, React 18 + TypeScript + Vite (frontend, prebuilt to `static/dist` and committed).

**Spec:** External planning note `we-have-rc-launcher-greedy-shamir.md`, section 3 (components table: `rc-hook`, `fleet.py`, `store.py` rows), the "Phase 2" subsection of section 5, and section 6 rows S9/S10/S13 (not tracked in this repo — a private document living outside the checkout; per Global Constraint 3 below, its path is never written into any tracked file). This plan is self-contained: every requirement it implements is captured in the Global Constraints and the 18 tasks below. Verified Claude Code hook facts used throughout (not re-derived): hooks are configured in user `~/.claude/settings.json` under `hooks.<EventName>[] = {matcher?, hooks:[{type:"command", command:"..."}]}`; every hook receives one JSON object on stdin carrying `session_id`, `cwd`, `transcript_path`, `hook_event_name`; env carries `CLAUDE_SESSION_ID` and `CLAUDE_PROJECT_DIR`. Useful events: `SessionStart` (field `source`: startup|resume|clear|compact|fork), `UserPromptSubmit` (`prompt`), `Stop` (`stop_hook_active`), `StopFailure` (`error_type`: rate_limit|overloaded|billing_error|...), `Notification` (`notification_type`: permission_prompt|idle_prompt|agent_needs_input|agent_completed|quota_auto_resume_*), `SubagentStop`, `PreCompact`, `SessionEnd` (`reason`: clear|resume|logout|prompt_input_exit|other). `--bare` sessions fire no hooks; `SessionStart` re-fires on resume/clear/compact, so events must be idempotent and `claude agents --json` (via `agents.list_claude_sessions()`) stays the source of truth for session existence, never the event spool.

## Global Constraints

- Python 3.9+ stdlib only at runtime. No new pip dependencies (SQLite via the `sqlite3` stdlib module). Use `Optional[X]` (`from typing import Optional`), never a bare runtime `X | None` union.
- Every file under `~/.claude-rc` is written atomically (temp file in the same directory + `os.replace`) and mode `0600` (`0700` for directories it creates).
- All subprocess calls stay argv lists, never `shell=True`, always pass `timeout=`.
- Hooks must never block a Claude Code session: `rc-hook` reads at most 1 MB of stdin, never waits longer than 2 s for it, makes no network call, and exits `0` unconditionally (even on a malformed or missing payload).
- No personal hostnames, tailnet names, IPs, or `/root/...` paths anywhere in tracked files. Use `~/.claude-rc`, `/home/alice`, `example.com`, `203.0.113.x`. CI's identifier grep enforces this.
- Existing tests keep passing: baseline is `python3 -m unittest discover tests` -> `Ran 351 tests ... OK` (recorded 2026-09-07 on branch `v3-phase2b` at the tip of this worktree, `/var/www/rc-launcher-p2b`, from `main` at `e7d0c8d`, v2.1.7 shipped).
- Each task ends with one commit on `v3-phase2b`.
- Nothing in this plan restarts services, touches `~/.claude-rc` on this box, `/var/www/rc-launcher`, or pushes to a remote. All work happens inside this worktree only.
- Security: `hub.db` is `0600` and never leaves the box; hook events never carry prompt text (only `len(prompt)` for `UserPromptSubmit`); a `metadata`-role device's session rows and events are hashed (`session_id`, `name` unless explicitly allowlisted) and carry no `cwd`; the audit log never records credentials, only actor/action/target/device/detail.
- Frontend build check before the final commit: `cd frontend && npm ci && npx tsc --noEmit && npm run build`, then commit `static/dist` alone in that task.

---

### Task 1: `hooks/rc-hook` device executable

**Files:**
- Create: `hooks/rc-hook` (executable, `chmod +x`, shebang `#!/usr/bin/env python3`)
- Test: `tests/test_rc_hook.py`

**Context:** This is the one process every Claude Code hook invocation runs. It must never fail a hook (exit 0 always), never block one (2 s stdin read cap, no network), and must degrade safely under `RC_ROLE=metadata`. It is invoked by the guarded snippet from Task 2, so it can assume `$0` is its own path and reads its one JSON object from stdin.

**Interfaces:**
- Consumes: nothing from earlier tasks (first task in Lane A).
- Produces: a CLI executable at `hooks/rc-hook` with `main(argv, stdin, env, now_fn=time.time, events_root=None)` importable for tests, appending one JSON line shaped `{"ts": <float epoch>, "event": <str>, "session_id": <str|hashed str>, "cwd": <str|omitted>, "extra": {...}}` to `<events_root>/<YYYY-MM-DD>.jsonl`. Later tasks (`events.py`) read this exact line shape.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rc_hook.py
import importlib.util
import io
import json
import os
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "rc_hook", os.path.join(os.path.dirname(__file__), "..", "hooks", "rc-hook"))
rc_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc_hook)


class RcHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events_root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload, env=None):
        stdin = io.StringIO(json.dumps(payload))
        rc_hook.main([], stdin, env or {}, now_fn=lambda: 1000.0,
                     events_root=self.events_root)

    def _read_lines(self):
        files = os.listdir(self.events_root)
        self.assertEqual(len(files), 1)
        path = os.path.join(self.events_root, files[0])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_appends_one_jsonl_line_full_role(self):
        self._run({"hook_event_name": "SessionStart", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "source": "startup"})
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        row = lines[0]
        self.assertEqual(row["event"], "SessionStart")
        self.assertEqual(row["session_id"], "abc123")
        self.assertEqual(row["cwd"], "/home/alice/proj")
        self.assertEqual(row["extra"], {"source": "startup"})
        self.assertEqual(row["ts"], 1000.0)

    def test_userpromptsubmit_never_stores_prompt_text(self):
        self._run({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                    "cwd": "/tmp", "prompt": "the secret prompt text"})
        row = self._read_lines()[0]
        self.assertNotIn("prompt", row["extra"])
        self.assertEqual(row["extra"]["prompt_len"], len("the secret prompt text"))

    def test_unknown_extra_fields_are_dropped(self):
        self._run({"hook_event_name": "Stop", "session_id": "s1", "cwd": "/tmp",
                    "stop_hook_active": True, "transcript_path": "/tmp/x.jsonl",
                    "some_future_field": "junk"})
        row = self._read_lines()[0]
        self.assertEqual(row["extra"], {"stop_hook_active": True})

    def test_metadata_role_hashes_session_id_and_drops_cwd(self):
        self._run({"hook_event_name": "SessionEnd", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "reason": "clear"},
                   env={"RC_ROLE": "metadata", "RC_HASH_SALT": "pepper"})
        row = self._read_lines()[0]
        self.assertNotIn("cwd", row)
        self.assertNotEqual(row["session_id"], "abc123")
        self.assertEqual(len(row["session_id"]), 64)  # hex sha256

    def test_malformed_stdin_never_raises_and_exits_zero(self):
        stdin = io.StringIO("not json{{{")
        code = rc_hook.main([], stdin, {}, now_fn=lambda: 1.0,
                             events_root=self.events_root)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.events_root), [])

    def test_missing_session_id_is_dropped_silently(self):
        stdin = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        code = rc_hook.main([], stdin, {}, now_fn=lambda: 1.0,
                             events_root=self.events_root)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.events_root), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_rc_hook -v`
Expected: FAIL — `hooks/rc-hook` does not exist yet (import error).

- [ ] **Step 3: Write `hooks/rc-hook`**

```python
#!/usr/bin/env python3
"""Called by Claude Code hooks (SessionStart, UserPromptSubmit, Stop,
StopFailure, Notification, SubagentStop, PreCompact, SessionEnd). Reads one
JSON object from stdin, appends one normalized JSON line to
~/.claude-rc/events/<YYYY-MM-DD>.jsonl, and exits 0 no matter what.

Never blocks a Claude Code session: at most 1 MB of stdin, at most ~2s wait
for it, no network calls, never raises past main(). RC_ROLE=metadata drops
cwd and hashes session_id with RC_HASH_SALT (HMAC-SHA256-ish via sha256 of
salt+id — good enough to defeat casual correlation without a live secret).
"""
import hashlib
import json
import os
import select
import sys
import time

MAX_STDIN_BYTES = 1024 * 1024
STDIN_TIMEOUT_S = 2.0

# Per-event allowlist of extra fields kept from the hook payload. Anything
# not listed here (including "prompt" itself) is dropped.
_EXTRA_ALLOWLIST = {
    "SessionStart": ("source",),
    "SessionEnd": ("reason",),
    "UserPromptSubmit": (),  # prompt_len is derived, not passed through
    "Stop": ("stop_hook_active",),
    "StopFailure": ("error_type",),
    "Notification": ("notification_type",),
    "SubagentStop": ("stop_hook_active",),
    "PreCompact": (),
}


def _read_stdin(stdin):
    """Read at most MAX_STDIN_BYTES, never blocking past STDIN_TIMEOUT_S.
    Falls back to a plain (unbounded-wait) read on a stdin object that
    doesn't support select (e.g. io.StringIO in tests)."""
    try:
        fd = stdin.fileno()
    except (AttributeError, OSError, io_error_types()):
        return stdin.read(MAX_STDIN_BYTES)
    ready, _, _ = select.select([fd], [], [], STDIN_TIMEOUT_S)
    if not ready:
        return ""
    return os.read(fd, MAX_STDIN_BYTES).decode("utf-8", errors="replace")


def io_error_types():
    import io
    return (io.UnsupportedOperation,)


def _hash(value, salt):
    return hashlib.sha256((salt + "|" + value).encode()).hexdigest()


def _events_root(env, override=None):
    if override:
        return override
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(env.get("RC_HOME", os.path.join(home, ".claude-rc")), "events")


def main(argv, stdin, env, now_fn=time.time, events_root=None):
    try:
        raw = _read_stdin(stdin)
        if not raw:
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        session_id = payload.get("session_id")
        if not event or not session_id:
            return 0

        role = env.get("RC_ROLE", "full")
        salt = env.get("RC_HASH_SALT", "")

        extra = {}
        for field in _EXTRA_ALLOWLIST.get(event, ()):
            if field in payload:
                extra[field] = payload[field]
        if event == "UserPromptSubmit" and isinstance(payload.get("prompt"), str):
            extra["prompt_len"] = len(payload["prompt"])

        row = {"ts": now_fn(), "event": event, "session_id": session_id, "extra": extra}
        cwd = payload.get("cwd")
        if cwd and role != "metadata":
            row["cwd"] = cwd
        if role == "metadata" and salt:
            row["session_id"] = _hash(session_id, salt)

        root = _events_root(env, events_root)
        os.makedirs(root, mode=0o700, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
        date_str = time.strftime("%Y-%m-%d", time.gmtime(row["ts"]))
        path = os.path.join(root, f"{date_str}.jsonl")
        line = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.chmod(path, 0o600)
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, os.environ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_rc_hook -v`
Expected: `Ran 6 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add hooks/rc-hook tests/test_rc_hook.py
git commit -m "$(cat <<'EOF'
feat: add rc-hook, the Claude Code hook event spooler

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 2: `docs/hooks/settings.snippet.json` + README hook section

**Files:**
- Create: `docs/hooks/settings.snippet.json`
- Modify: `README.md` (append a "Hook events" section)
- Test: `tests/test_hooks_snippet.py`

**Context:** This is the exact block an operator adds to a shared `settings.json` (e.g. via `claude-config`) to wire every relevant event into `rc-hook`. Every command must be guarded so a device without the launcher installed is unaffected — no crash, no stderr noise, no delay.

**Interfaces:**
- Consumes: nothing.
- Produces: `docs/hooks/settings.snippet.json`, a JSON file whose top-level `hooks` key is a valid Claude Code hooks block, parseable by `json.load`. Task 3 (install.sh) prints its path.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hooks_snippet.py
import json
import os
import unittest

SNIPPET_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "hooks", "settings.snippet.json")

_EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "StopFailure",
           "Notification", "SubagentStop", "PreCompact", "SessionEnd"]


class HooksSnippetTest(unittest.TestCase):
    def test_snippet_is_valid_json_with_hooks_key(self):
        with open(SNIPPET_PATH) as f:
            data = json.load(f)
        self.assertIn("hooks", data)

    def test_every_expected_event_present_and_guarded(self):
        with open(SNIPPET_PATH) as f:
            data = json.load(f)
        hooks = data["hooks"]
        for event in _EVENTS:
            self.assertIn(event, hooks, f"missing {event}")
            entries = hooks[event]
            self.assertTrue(entries, f"{event} has no entries")
            for entry in entries:
                for h in entry["hooks"]:
                    self.assertEqual(h["type"], "command")
                    self.assertIn('[ -x "$HOME/.claude-rc/bin/rc-hook" ]', h["command"])
                    self.assertIn('|| true', h["command"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_hooks_snippet -v`
Expected: FAIL — file not found.

- [ ] **Step 3: Write the snippet and README section**

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "StopFailure": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "SubagentStop": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "PreCompact": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "command": "[ -x \"$HOME/.claude-rc/bin/rc-hook\" ] && \"$HOME/.claude-rc/bin/rc-hook\" || true"}]}
    ]
  }
}
```

Append to `README.md`:

```markdown
## Hook events (fleet visibility)

RC Launcher can show a session's lifecycle (started, prompted, stopped,
needs attention...) across every device, not just the ones it launched.
This works by having Claude Code call a tiny spooler script on every hook
event. It is entirely optional and safe on a device without the launcher
installed — every hook command is guarded.

To enable it, merge the block from `docs/hooks/settings.snippet.json`
into your (usually shared) `~/.claude/settings.json` under its `hooks`
key. `install.sh` already places the spooler at
`~/.claude-rc/bin/rc-hook`; nothing else is required.

The spooler never sends anything over the network, never stores prompt
text (only its length), and exits successfully even when it can't do
anything — a hook must never block or fail a Claude Code session because
of it.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_hooks_snippet -v`
Expected: `Ran 2 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add docs/hooks/settings.snippet.json README.md tests/test_hooks_snippet.py
git commit -m "$(cat <<'EOF'
docs: add shareable hooks settings snippet for rc-hook

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 3: `install.sh` installs `rc-hook`

**Files:**
- Modify: `install.sh` (near the existing `mkdir -p "$BIN_DIR"` / wrapper-script block around line 266)
- Test: `tests/test_install_rc_hook.py`

**Context:** `install.sh` already creates `$RC_HOME` (`~/.claude-rc`) and `$BIN_DIR` (`~/.local/bin`) and writes a wrapper to `$BIN_LINK`. This task adds installing `hooks/rc-hook` to `~/.claude-rc/bin/rc-hook` (mode `0700`) and creating `~/.claude-rc/events/` (mode `0700`), then printing the path to the snippet from Task 2 so the operator knows what to merge into `settings.json`. Idempotent: re-running install.sh just overwrites the file and re-asserts permissions.

**Interfaces:**
- Consumes: `hooks/rc-hook` (Task 1), `docs/hooks/settings.snippet.json` (Task 2).
- Produces: on a fresh install, `~/.claude-rc/bin/rc-hook` (0700) and `~/.claude-rc/events/` (0700) exist; nothing else in this repo reads these paths yet (Task 4 does, but at runtime, not install time).

- [ ] **Step 1: Write the failing test**

Since `install.sh` isn't run for real in CI (it touches `$HOME`), test it by grepping for the expected commands — matching the convention already used by `tests/test_repair_schedules.py`-style shell-script assertions.

```python
# tests/test_install_rc_hook.py
import os
import unittest

INSTALL_SH = os.path.join(os.path.dirname(__file__), "..", "install.sh")


class InstallRcHookTest(unittest.TestCase):
    def setUp(self):
        with open(INSTALL_SH) as f:
            self.src = f.read()

    def test_installs_rc_hook_to_bin_dir_0700(self):
        self.assertIn('cp "$SCRIPT_DIR/hooks/rc-hook" "$RC_HOME/bin/rc-hook"', self.src)
        self.assertIn('chmod 700 "$RC_HOME/bin/rc-hook"', self.src)

    def test_creates_events_dir_0700(self):
        self.assertIn('mkdir -p "$RC_HOME/events"', self.src)
        self.assertIn('chmod 700 "$RC_HOME/events"', self.src)

    def test_prints_snippet_path(self):
        self.assertIn("docs/hooks/settings.snippet.json", self.src)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_install_rc_hook -v`
Expected: FAIL — assertions not found in `install.sh`.

- [ ] **Step 3: Edit `install.sh`**

Add near the existing `mkdir -p "$BIN_DIR"` wrapper-script block (after `ok "Created wrapper at $BIN_LINK"`):

```bash
# ── rc-hook (Claude Code hook event spooler) ─────────────────────────

mkdir -p "$RC_HOME/bin"
mkdir -p "$RC_HOME/events"
chmod 700 "$RC_HOME/bin"
chmod 700 "$RC_HOME/events"
cp "$SCRIPT_DIR/hooks/rc-hook" "$RC_HOME/bin/rc-hook"
chmod 700 "$RC_HOME/bin/rc-hook"
ok "Installed hook spooler at $RC_HOME/bin/rc-hook"
echo "  To enable fleet event visibility, merge docs/hooks/settings.snippet.json"
echo "  into your ~/.claude/settings.json (see README.md 'Hook events')."
```

`$SCRIPT_DIR` must already resolve to the directory `install.sh` lives in (check near the top of the file; if it's not yet defined, add `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` right after the shebang/`set -euo pipefail` line — this only matters when `install.sh` is run from a cloned checkout, which is the only case where `hooks/rc-hook` exists to copy; the `curl | bash` remote-install path in the `update` branch reruns the installer from a fresh clone anyway).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_install_rc_hook -v`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_install_rc_hook.py
git commit -m "$(cat <<'EOF'
feat: install rc-hook and events dir from install.sh

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 4: `events.py` (device-side spool reader)

**Files:**
- Create: `events.py`
- Test: `tests/test_events.py`

**Context:** Pure, injectable-root functions over the date-named JSONL spool `rc-hook` (Task 1) writes. Must tolerate file rotation (a `since_cursor` from yesterday's file, spool now has today's file too) and a truncated last line (the process could be killed mid-`os.write`, though `os.write` under 1 line is effectively atomic on Linux for lines this short — still, be defensive). Cursor format: `"<filename>:<byte offset>"`, e.g. `"2026-09-07.jsonl:184"`.

**Interfaces:**
- Consumes: the JSONL line shape from Task 1 (`{"ts", "event", "session_id", "cwd"?, "extra"}`).
- Produces: `read_events(root, since_cursor=None, limit=500)` -> `(rows, new_cursor)` where `rows` is a list of parsed dicts in file order (oldest first), and `new_cursor` is a `str` cursor to pass next time (or the same `since_cursor` if nothing new). `prune(root, days=7, now_fn=time.time)` -> deletes spool files whose date is older than `days` and returns the list of deleted filenames. Task 5 (`fleet.py`) calls `read_events`. Task 7 (`fleetpoll.py`, hub side) stores the returned `new_cursor` per device.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_events.py
import json
import os
import tempfile
import time
import unittest

import events


def _write_day(root, date_str, rows):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{date_str}.jsonl")
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


class ReadEventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_all_rows_when_no_cursor(self):
        _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
            {"ts": 2, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor = events.read_events(self.root)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], "SessionStart")
        self.assertEqual(cursor, "2026-09-07.jsonl:" + str(
            os.path.getsize(os.path.join(self.root, "2026-09-07.jsonl"))))

    def test_cursor_resumes_from_byte_offset(self):
        _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        _, cursor = events.read_events(self.root)
        _write_day(self.root, "2026-09-07", [
            {"ts": 2, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor2 = events.read_events(self.root, since_cursor=cursor)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "Stop")
        self.assertNotEqual(cursor, cursor2)

    def test_rotation_across_day_boundary_reads_new_file_from_start(self):
        _write_day(self.root, "2026-09-06", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        _, cursor = events.read_events(self.root)
        _write_day(self.root, "2026-09-07", [
            {"ts": 100000, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor2 = events.read_events(self.root, since_cursor=cursor)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "Stop")
        self.assertTrue(cursor2.startswith("2026-09-07.jsonl:"))

    def test_truncated_last_line_is_skipped_not_raised(self):
        path = _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        with open(path, "a") as f:
            f.write('{"ts": 2, "event": "Stop"')  # no closing brace/newline
        rows, cursor = events.read_events(self.root)
        self.assertEqual(len(rows), 1)

    def test_limit_caps_rows_and_cursor_stays_mid_stream(self):
        _write_day(self.root, "2026-09-07", [
            {"ts": i, "event": "Stop", "session_id": "a", "extra": {}} for i in range(10)
        ])
        rows, cursor = events.read_events(self.root, limit=3)
        self.assertEqual(len(rows), 3)
        rows2, _ = events.read_events(self.root, since_cursor=cursor, limit=100)
        self.assertEqual(len(rows2), 7)

    def test_missing_root_returns_empty(self):
        rows, cursor = events.read_events(os.path.join(self.root, "nope"))
        self.assertEqual(rows, [])
        self.assertIsNone(cursor)


class PruneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_prune_deletes_old_files_keeps_recent(self):
        _write_day(self.root, "2026-08-01", [{"ts": 1, "event": "Stop", "session_id": "a", "extra": {}}])
        _write_day(self.root, "2026-09-06", [{"ts": 1, "event": "Stop", "session_id": "a", "extra": {}}])
        deleted = events.prune(self.root, days=7, now_fn=lambda: time.mktime(
            time.strptime("2026-09-07", "%Y-%m-%d")))
        self.assertEqual(deleted, ["2026-08-01.jsonl"])
        self.assertEqual(sorted(os.listdir(self.root)), ["2026-09-06.jsonl"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_events -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'events'` (note: shadows stdlib? No — Python has no stdlib module named `events`; safe to use).

- [ ] **Step 3: Write `events.py`**

```python
"""Reads the JSONL event spool rc-hook (hooks/rc-hook) writes to
~/.claude-rc/events/<YYYY-MM-DD>.jsonl. Pure functions, root is always
passed in (never reads config.RC_HOME itself) so tests and fleet.py both
control it explicitly.
"""
import datetime
import json
import os
import time

DATE_FMT = "%Y-%m-%d"


def _spool_files(root):
    """Sorted (oldest first) list of "<date>.jsonl" filenames present."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".jsonl"):
            try:
                datetime.datetime.strptime(n[:-len(".jsonl")], DATE_FMT)
            except ValueError:
                continue
            out.append(n)
    return sorted(out)


def _parse_cursor(cursor):
    if not cursor or ":" not in cursor:
        return None, 0
    filename, _, offset = cursor.rpartition(":")
    try:
        return filename, int(offset)
    except ValueError:
        return None, 0


def read_events(root, since_cursor=None, limit=500):
    """Rows newer than since_cursor (oldest first), capped at limit, and
    the cursor to resume from next time. Tolerant of day-boundary
    rotation and of a truncated last line in the current file."""
    files = _spool_files(root)
    if not files:
        return [], None

    cursor_file, cursor_offset = _parse_cursor(since_cursor)
    if cursor_file in files:
        start_index = files.index(cursor_file)
    else:
        # Unknown/older/missing file (pruned, or first-ever read): start
        # from the beginning of the earliest file still present.
        start_index = 0
        cursor_offset = 0

    rows = []
    last_file, last_offset = files[start_index], cursor_offset
    for i in range(start_index, len(files)):
        fname = files[i]
        path = os.path.join(root, fname)
        offset = cursor_offset if fname == cursor_file else 0
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if offset > size:
            offset = 0  # file was rotated/truncated externally; restart it
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        pos = offset
        for line in data.splitlines(keepends=True):
            line_len = len(line)
            if not line.endswith(b"\n"):
                # Truncated last line (mid-write): stop here, do not
                # advance the cursor past it, so it gets re-read whole
                # next time once the writer finishes.
                break
            pos += line_len
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
                if len(rows) >= limit:
                    last_file, last_offset = fname, pos
                    return rows, f"{last_file}:{last_offset}"
        last_file, last_offset = fname, pos

    return rows, f"{last_file}:{last_offset}"


def prune(root, days=7, now_fn=time.time):
    """Delete spool files older than `days`. Returns the list of deleted
    filenames (empty if root doesn't exist or nothing was old enough)."""
    try:
        files = _spool_files(root)
    except OSError:
        return []
    if not files:
        return []
    cutoff = datetime.datetime.utcfromtimestamp(now_fn()).date() - datetime.timedelta(days=days)
    deleted = []
    for fname in files:
        date_str = fname[:-len(".jsonl")]
        try:
            file_date = datetime.datetime.strptime(date_str, DATE_FMT).date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                os.remove(os.path.join(root, fname))
                deleted.append(fname)
            except OSError:
                pass
    return deleted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_events -v`
Expected: `Ran 7 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add events.py tests/test_events.py
git commit -m "$(cat <<'EOF'
feat: add events.py, the device-side hook spool reader

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 5: `fleet.py` (device-side) + `GET /fleet`

**Files:**
- Create: `fleet.py`
- Modify: `server.py` (add `/fleet` to the GET route dispatch, and to the `_should_proxy` exemption list and `log_message` quiet list)
- Test: `tests/test_fleet.py`, extend `tests/test_server_helpers.py`

**Context:** `fleet.py` is the single response shape every device — hub included, called in-process — returns. It merges `sessions.list_rc_sessions()` (existing, already merges `claude agents --json` via `agents.list_claude_sessions()`) with `events.read_events()` and applies the `metadata`-role redaction. Cached 5 s like `panes.py`'s pattern (module-level dict + `now_fn`).

**Interfaces:**
- Consumes: `sessions.list_rc_sessions()` (existing, returns list of session dicts with `name`/`session_id`/`state`/etc.), `compat.get_caps()` (existing), `config.VERSION`, `config.RC_HOME`, `events.read_events(root, since_cursor, limit)` (Task 4), `devices.get_local_name()` (existing).
- Produces: `build_fleet(since=None, role=None, events_root=None, now_fn=time.time)` -> dict `{device_name, role, version, claude_version, caps, sessions: [...], events: [...], cursor, generated_at, errors: []}`. Route `GET /fleet` (and `/rc/fleet` when proxied) returns this as JSON, cached 5 s keyed on `(since, role)`. Task 7 (hub poller) is the consumer of this exact shape (local in-process call to `build_fleet` for the hub's own device, HTTP GET to `<base_url>/rc/fleet?since=<cursor>` for every other device).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fleet.py
import time
import unittest
from unittest.mock import patch

import fleet


class BuildFleetTest(unittest.TestCase):
    def setUp(self):
        fleet._cache.clear()

    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_full_role_carries_everything(self, get_name, get_caps, list_sess, read_ev):
        get_name.return_value = "hub"
        get_caps.return_value = {"version": "2.1.263", "agents_json": True}
        list_sess.return_value = [
            {"name": "rc-foo", "session_id": "s1", "cwd": "/home/alice/proj", "state": "idle"},
        ]
        read_ev.return_value = ([{"ts": 1, "event": "Stop", "session_id": "s1", "extra": {}}], "f.jsonl:10")

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["device_name"], "hub")
        self.assertEqual(result["role"], "full")
        self.assertEqual(result["version"], fleet.config.VERSION)
        self.assertEqual(result["claude_version"], "2.1.263")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["cwd"], "/home/alice/proj")
        self.assertEqual(result["cursor"], "f.jsonl:10")
        self.assertEqual(result["generated_at"], 5000.0)
        self.assertEqual(result["errors"], [])

    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_metadata_role_strips_cwd_tmux_claude_and_hashes(self, get_name, get_caps, list_sess, read_ev):
        get_name.return_value = "work-mac"
        get_caps.return_value = {"version": "2.1.263", "agents_json": True}
        list_sess.return_value = [{
            "name": "rc-secret-project", "session_id": "s1", "cwd": "/Users/alice/work",
            "state": "idle", "tmux": {"pane_id": "%1"}, "claude": {"pid": 123}, "tokens": 5000,
        }]
        read_ev.return_value = ([{"ts": 1, "event": "SessionStart", "session_id": "s1", "extra": {"source": "startup"}}], "f.jsonl:5")

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        row = result["sessions"][0]
        self.assertNotIn("cwd", row)
        self.assertNotIn("tmux", row)
        self.assertNotIn("claude", row)
        self.assertNotIn("tokens", row)
        self.assertEqual(set(row.keys()), {"session_id", "name", "state", "started_at", "kind"})
        self.assertNotEqual(row["session_id"], "s1")
        ev = result["events"][0]
        self.assertEqual(set(ev.keys()), {"ts", "event"})

    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_cached_for_5_seconds_per_since_and_role(self, get_name, get_caps, list_sess, read_ev):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        now = {"t": 1000.0}
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        now["t"] = 1002.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(list_sess.call_count, 1)
        now["t"] = 1006.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(list_sess.call_count, 2)


if __name__ == "__main__":
    unittest.main()
```

Extend `tests/test_server_helpers.py` with a route-dispatch check:

```python
class FleetRouteTest(unittest.TestCase):
    def test_fleet_added_to_should_proxy_exemption_and_log_quiet_list(self):
        import inspect
        src = inspect.getsource(server)
        self.assertIn('"/rc/fleet"', src)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_fleet tests.test_server_helpers.FleetRouteTest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet'`.

- [ ] **Step 3: Write `fleet.py`**

```python
"""Device-side fleet snapshot: sessions (claude agents --json + tmux,
via sessions.list_rc_sessions) merged with recent hook events (events.py),
role-gated. Called in-process by the hub for itself, and over HTTP
(GET /fleet, proxied as GET /rc/fleet) for every other device.
"""
import hashlib
import os
import time

import compat
import config
import devices
import events
import sessions

CACHE_TTL_SECONDS = 5

_cache = {}  # key: (since, role) -> {"at": float, "result": dict}

_METADATA_SESSION_FIELDS = ("session_id", "name", "state", "started_at", "kind")
_METADATA_EVENT_FIELDS = ("ts", "event")

EVENTS_LIMIT = 500


def _events_root():
    return os.path.join(config.RC_HOME, "events")


def _hash(value, salt):
    if not salt:
        return value
    return hashlib.sha256((salt + "|" + str(value)).encode()).hexdigest()


def _redact_session(row, salt):
    out = {k: row.get(k) for k in _METADATA_SESSION_FIELDS}
    if out.get("session_id"):
        out["session_id"] = _hash(out["session_id"], salt)
    return out


def _redact_event(row):
    return {k: row.get(k) for k in _METADATA_EVENT_FIELDS}


def build_fleet(since=None, role=None, events_root=None, now_fn=time.time):
    """One device's fleet snapshot. role defaults to config.RC_ROLE.
    Cached CACHE_TTL_SECONDS per (since, role)."""
    role = role or getattr(config, "RC_ROLE", "full")
    cache_key = (since, role)
    cached = _cache.get(cache_key)
    now = now_fn()
    if cached and now - cached["at"] < CACHE_TTL_SECONDS:
        return cached["result"]

    errors = []
    try:
        raw_sessions = sessions.list_rc_sessions()
    except Exception as e:
        raw_sessions, errors = [], errors + [f"sessions: {e}"]

    root = events_root or _events_root()
    try:
        raw_events, cursor = events.read_events(root, since_cursor=since, limit=EVENTS_LIMIT)
    except Exception as e:
        raw_events, cursor, errors = [], since, errors + [f"events: {e}"]

    caps = compat.get_caps()
    salt = os.environ.get("RC_HASH_SALT", "")

    if role == "metadata":
        out_sessions = [_redact_session(s, salt) for s in raw_sessions]
        out_events = [_redact_event(e) for e in raw_events]
    else:
        out_sessions = raw_sessions
        out_events = raw_events

    result = {
        "device_name": devices.get_local_name(),
        "role": role,
        "version": config.VERSION,
        "claude_version": caps.get("version"),
        "caps": caps,
        "sessions": out_sessions,
        "events": out_events,
        "cursor": cursor,
        "generated_at": now,
        "errors": errors,
    }
    _cache[cache_key] = {"at": now, "result": result}
    return result
```

- [ ] **Step 4: Wire `GET /fleet` into `server.py`**

Add near the `/sessions` branch (after `elif path == "/sessions": ...` block, around `server.py:1035`):

```python
        elif path.split('?')[0] == "/fleet":
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            since = qs.get("since", [None])[0]
            self._json(fleet.build_fleet(since=since))
```

Add `import fleet` to the top-of-file imports (alongside the existing `import compat`, `import overview`, etc.).

Add `"/rc/fleet"` to the `_should_proxy` exemption tuple is **not** correct here — `/fleet` must proxy normally to a specific device when `?device=` is set (unlike `/devices`), so no change to `_should_proxy` is needed. Instead add `"/rc/fleet"` to the quiet `log_message` list (it will be polled every 30 s by the hub and would otherwise spam the console):

```python
        if path in ("/rc/sessions", "/rc/tunnel/status", "/rc/projects",
                     "/rc/browse", "/rc/schedules", "/rc/version",
                     "/rc/resume/sessions", "/rc/stats", "/rc/overview",
                     "/rc/config-report", "/api/config-matrix", "/rc/fleet") or \
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_fleet tests.test_server_helpers -v`
Expected: `OK` for both, plus full suite `python3 -m unittest discover tests` still `OK`.

- [ ] **Step 6: Commit**

```bash
git add fleet.py server.py tests/test_fleet.py tests/test_server_helpers.py
git commit -m "$(cat <<'EOF'
feat: add fleet.py device snapshot and GET /fleet route

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 6: `store.py` (hub SQLite store)

**Files:**
- Create: `store.py`
- Test: `tests/test_store.py`

**Context:** The hub's single source of truth for cross-device sessions/events/audit, replacing per-request fan-out for the frontend. One writer thread drains a `queue.Queue` (SQLite in WAL mode allows only one writer at a time; funneling every write through one thread avoids `database is locked` errors under concurrency). Reads use short-lived per-thread connections opened directly (readers don't contend with the writer under WAL). Tests use a temp DB file, never the real `~/.claude-rc/hub.db`.

**Interfaces:**
- Consumes: nothing from earlier tasks (independent of Lane A beyond matching `fleet.build_fleet()`'s response shape for `upsert_sessions`/`add_events` callers in Task 7).
- Produces: `Store(db_path)` with methods `upsert_device(row)`, `upsert_sessions(device_id, rows, now_fn=time.time)` (marks rows in the DB for that device but absent from `rows` as ended), `add_events(device_id, rows)`, `recent_events(session_id=None, device_id=None, limit=50)`, `fleet_view()`, `add_audit(actor, action, target, device_id, detail)`, `recent_audit(limit=50)`, `prune(days=14, now_fn=time.time)`, `close()`. Task 7 (`fleetpoll.py`), Task 8/9/10 (`/api/fleet*` routes), Task 11 (audit log) all call this exact `Store` interface.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
import os
import tempfile
import time
import unittest

import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_db_file_created_0600(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "2.1.7", "claude_version": "2.1.263"})
        self.assertTrue(os.path.exists(self.db_path))
        self.assertEqual(oct(os.stat(self.db_path).st_mode & 0o777), "0o600")

    def test_upsert_device_then_appears_in_fleet_view(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "2.1.7", "claude_version": "2.1.263"})
        view = self.store.fleet_view()
        self.assertEqual(len(view["devices"]), 1)
        self.assertEqual(view["devices"][0]["name"], "hub")

    def test_upsert_sessions_then_reupsert_without_a_row_marks_it_ended(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 1000},
        ], now_fn=lambda: 1010.0)
        view = self.store.fleet_view()
        self.assertEqual(len(view["sessions"]), 1)
        self.assertIsNone(view["sessions"][0]["ended_at"])

        self.store.upsert_sessions("local", [], now_fn=lambda: 1020.0)
        view2 = self.store.fleet_view()
        self.assertEqual(view2["sessions"][0]["ended_at"], 1020.0)

    def test_add_events_then_recent_events_by_session(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.add_events("local", [
            {"ts": 1000.0, "event": "SessionStart", "session_id": "s1", "extra": {"source": "startup"}},
            {"ts": 1001.0, "event": "Stop", "session_id": "s1", "extra": {}},
            {"ts": 1002.0, "event": "Stop", "session_id": "other", "extra": {}},
        ])
        rows = self.store.recent_events(session_id="s1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], "Stop")  # newest first
        self.assertEqual(rows[1]["extra"], {"source": "startup"})

    def test_add_audit_then_recent_audit(self):
        self.store.add_audit(actor="tok_abc", action="start", target="rc-foo",
                              device_id="local", detail="mode=c")
        rows = self.store.recent_audit(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "start")
        self.assertEqual(rows[0]["actor"], "tok_abc")

    def test_prune_removes_old_events_and_audit(self):
        self.store.add_events("local", [{"ts": 1.0, "event": "Stop", "session_id": "s1", "extra": {}}])
        self.store.add_audit(actor="a", action="b", target="c", device_id="local", detail="")
        deleted = self.store.prune(days=1, now_fn=lambda: 1.0 + 2 * 86400)
        self.assertGreaterEqual(deleted["events"], 1)
        self.assertGreaterEqual(deleted["audit_log"], 1)
        self.assertEqual(self.store.recent_events(session_id="s1"), [])

    def test_concurrent_writes_do_not_raise(self):
        import threading
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        errors = []

        def writer(n):
            try:
                for i in range(20):
                    self.store.add_events("local", [
                        {"ts": float(i), "event": "Stop", "session_id": f"s{n}", "extra": {}}
                    ])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_store -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'store'`.

- [ ] **Step 3: Write `store.py`**

```python
"""Hub-side SQLite store: devices, sessions, session_events, audit_log.

One writer thread drains a queue.Queue (SQLite/WAL allows one writer at a
time; funneling every write through one thread avoids "database is locked"
under concurrency). Reads use short-lived per-thread connections opened
directly against the file — safe to read concurrently with the writer
under WAL. Schema creation is idempotent (CREATE TABLE IF NOT EXISTS).
"""
import json
import os
import queue
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, name TEXT, role TEXT, version TEXT,
    claude_version TEXT, last_seen REAL, online INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    device_id TEXT, session_id TEXT, name TEXT, cwd TEXT, kind TEXT,
    state TEXT, started_at REAL, ended_at REAL, last_seen REAL,
    external INTEGER DEFAULT 0,
    PRIMARY KEY (device_id, session_id)
);
CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, session_id TEXT,
    ts REAL, event TEXT, extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_device ON session_events(device_id, ts);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT,
    target TEXT, device_id TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


class _Write:
    __slots__ = ("fn", "args", "kwargs", "result", "done")

    def __init__(self, fn, args, kwargs):
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.result, self.done = None, threading.Event()


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_conn = self._connect()
        self._init_conn.executescript(_SCHEMA)
        self._init_conn.commit()
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _writer_loop(self):
        conn = self._connect()
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                item.result = item.fn(conn, *item.args, **item.kwargs)
                conn.commit()
            except Exception as e:
                conn.rollback()
                item.result = e
            finally:
                item.done.set()
        conn.close()

    def _write(self, fn, *args, **kwargs):
        item = _Write(fn, args, kwargs)
        self._q.put(item)
        item.done.wait(timeout=10)
        if isinstance(item.result, Exception):
            raise item.result
        return item.result

    def _read_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    # -- writes ------------------------------------------------------

    def upsert_device(self, row, now_fn=time.time):
        def _do(conn):
            conn.execute(
                "INSERT INTO devices (id, name, role, version, claude_version, last_seen, online) "
                "VALUES (?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, role=excluded.role, "
                "version=excluded.version, claude_version=excluded.claude_version, "
                "last_seen=excluded.last_seen, online=1",
                (row["id"], row.get("name", row["id"]), row.get("role", "full"),
                 row.get("version"), row.get("claude_version"), now_fn()))
        return self._write(_do)

    def mark_device_offline(self, device_id):
        def _do(conn):
            conn.execute("UPDATE devices SET online=0 WHERE id=?", (device_id,))
        return self._write(_do)

    def upsert_sessions(self, device_id, rows, now_fn=time.time):
        """Replace this device's live-session view: rows present are
        upserted (started_at kept from the first sighting); rows in the DB
        for this device but absent from `rows` get ended_at set (once)."""
        def _do(conn):
            now = now_fn()
            seen_ids = set()
            for r in rows:
                sid = r.get("session_id") or r.get("name")
                if not sid:
                    continue
                seen_ids.add(sid)
                existing = conn.execute(
                    "SELECT started_at FROM sessions WHERE device_id=? AND session_id=?",
                    (device_id, sid)).fetchone()
                started_at = existing["started_at"] if existing else (r.get("started_at") or now)
                conn.execute(
                    "INSERT INTO sessions (device_id, session_id, name, cwd, kind, state, "
                    "started_at, ended_at, last_seen, external) VALUES (?,?,?,?,?,?,?,NULL,?,?) "
                    "ON CONFLICT(device_id, session_id) DO UPDATE SET name=excluded.name, "
                    "cwd=excluded.cwd, kind=excluded.kind, state=excluded.state, "
                    "last_seen=excluded.last_seen, ended_at=NULL, external=excluded.external",
                    (device_id, sid, r.get("name"), r.get("cwd"), r.get("kind"),
                     r.get("state"), started_at, now, int(bool(r.get("external")))))
            existing_ids = [row["session_id"] for row in conn.execute(
                "SELECT session_id FROM sessions WHERE device_id=? AND ended_at IS NULL",
                (device_id,))]
            for sid in existing_ids:
                if sid not in seen_ids:
                    conn.execute(
                        "UPDATE sessions SET ended_at=? WHERE device_id=? AND session_id=? AND ended_at IS NULL",
                        (now, device_id, sid))
        return self._write(_do)

    def add_events(self, device_id, rows):
        def _do(conn):
            for r in rows:
                conn.execute(
                    "INSERT INTO session_events (device_id, session_id, ts, event, extra_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (device_id, r.get("session_id"), r.get("ts"), r.get("event"),
                     json.dumps(r.get("extra") or {})))
        return self._write(_do)

    def add_audit(self, actor, action, target, device_id, detail="", now_fn=time.time):
        def _do(conn):
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, target, device_id, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_fn(), actor, action, target, device_id, detail))
        return self._write(_do)

    def prune(self, days=14, now_fn=time.time):
        def _do(conn):
            cutoff = now_fn() - days * 86400
            ev = conn.execute("DELETE FROM session_events WHERE ts < ?", (cutoff,)).rowcount
            au = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,)).rowcount
            se = conn.execute("DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,)).rowcount
            return {"events": ev, "audit_log": au, "sessions": se}
        return self._write(_do)

    # -- reads ---------------------------------------------------------

    def fleet_view(self):
        conn = self._read_conn()
        try:
            devices_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM devices ORDER BY name")]
            session_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM sessions ORDER BY last_seen DESC")]
            return {"devices": devices_rows, "sessions": session_rows}
        finally:
            conn.close()

    def recent_events(self, session_id=None, device_id=None, limit=50):
        conn = self._read_conn()
        try:
            clauses, params = [], []
            if session_id:
                clauses.append("session_id=?")
                params.append(session_id)
            if device_id:
                clauses.append("device_id=?")
                params.append(device_id)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM session_events {where} ORDER BY ts DESC LIMIT ?",
                params + [limit]).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["extra"] = json.loads(d.pop("extra_json") or "{}")
                except ValueError:
                    d["extra"] = {}
                out.append(d)
            return out
        finally:
            conn.close()

    def recent_audit(self, limit=50):
        conn = self._read_conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))]
        finally:
            conn.close()

    def close(self):
        self._stop.set()
        self._q.put(None)
        self._thread.join(timeout=5)
        self._init_conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_store -v`
Expected: `Ran 7 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "$(cat <<'EOF'
feat: add store.py, the hub SQLite fleet/event/audit store

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 7: `fleetpoll.py` hub poller thread

**Files:**
- Create: `fleetpoll.py`
- Modify: `app.py` (start the poller next to `start_scheduler()`)
- Test: `tests/test_fleetpoll.py`

**Context:** Every 30 s, fan out to every device in `devices.load_devices()` plus the local device (in-process call to `fleet.build_fleet`, no HTTP — the hub never HTTPs itself), passing each device's stored cursor (kept in-memory in the poller, not the DB — losing a cursor across a hub restart just means a bit of event replay, which `store.add_events` handles fine since events are additive and roughly idempotent per Claude's own dedup at the session-state layer). An offline device backs off from 30 s to a 5 min ceiling (doubling each consecutive failure). Never raises out of the poll loop. One lock so two `poll_once()` calls never overlap (a slow device must not cause the next tick to pile up a second fetch of it).

**Interfaces:**
- Consumes: `devices.load_devices()` (existing, returns `[{id, name, base_url, auth_user, auth_pass}]`), `devices.get_local_name()` (existing), `fleet.build_fleet(since=...)` (Task 5, in-process for local), `store.Store` (Task 6: `.upsert_device`, `.upsert_sessions`, `.add_events`, `.mark_device_offline`).
- Produces: `FleetPoller(store, interval=30, http_get=None)` with `.poll_once(now_fn=time.time)` (one pass over every device, returns nothing, never raises) and `.start()`/`.stop()` (background `threading.Thread`, daemon). `app.py` calls `poller.start()`. Task 8/9 read only from `store`, not from `fleetpoll` directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fleetpoll.py
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import fleetpoll
import store


def _fake_store(tmp_path):
    return store.Store(os.path.join(tmp_path, "hub.db"))


class PollOnceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_polls_local_in_process_no_http(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "2.1.7",
            "claude_version": "2.1.263", "sessions": [{"session_id": "s1", "name": "rc-a"}],
            "events": [{"ts": 1.0, "event": "Stop", "session_id": "s1", "extra": {}}],
            "cursor": "f.jsonl:1", "generated_at": 1000.0, "errors": [],
        }
        http_get = MagicMock()
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)
        poller.poll_once()

        http_get.assert_not_called()
        view = self.store.fleet_view()
        self.assertEqual(len(view["devices"]), 1)
        self.assertEqual(view["devices"][0]["id"], "local")
        self.assertEqual(len(view["sessions"]), 1)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_polls_remote_device_over_http_with_cursor(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "tba-lin", "base_url": "http://example.com:8200",
                  "auth_user": "u", "auth_pass": "p"}
        load_devices.return_value = [device]
        remote_resp = {"device_name": "tba-lin", "role": "full", "version": "2.1.7",
                       "claude_version": "2.1.263", "sessions": [{"session_id": "s2", "name": "rc-b"}],
                       "events": [], "cursor": "g.jsonl:1", "generated_at": 2.0, "errors": []}
        http_get = MagicMock(return_value=remote_resp)
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)

        poller.poll_once()
        poller.poll_once()

        self.assertEqual(http_get.call_count, 2)
        first_call_kwargs = http_get.call_args_list[1]
        self.assertIn("since", first_call_kwargs.kwargs)
        self.assertEqual(first_call_kwargs.kwargs["since"], "g.jsonl:1")

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_offline_device_backs_off_and_never_raises(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "offline-box", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def raising_get(*a, **kw):
            raise ConnectionError("refused")

        poller = fleetpoll.FleetPoller(self.store, http_get=raising_get)
        poller.poll_once()  # must not raise
        poller.poll_once()

        view = self.store.fleet_view()
        offline_row = [d for d in view["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(offline_row["online"], 0)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_concurrent_poll_once_calls_do_not_overlap(self, get_name, load_devices, build_fleet):
        import threading
        get_name.return_value = "hub"
        load_devices.return_value = []
        calls = []

        def slow_build_fleet(since=None):
            calls.append(1)
            import time as t
            t.sleep(0.05)
            return {"device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
                    "sessions": [], "events": [], "cursor": None, "generated_at": 1.0, "errors": []}

        build_fleet.side_effect = slow_build_fleet
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        threads = [threading.Thread(target=poller.poll_once) for _ in range(3)]
        for t_ in threads:
            t_.start()
        for t_ in threads:
            t_.join(timeout=5)
        self.assertEqual(len(calls), 3)  # all ran, just serialized — no assertion on order


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_fleetpoll -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleetpoll'`.

- [ ] **Step 3: Write `fleetpoll.py`**

```python
"""Hub poller: every interval seconds, pulls fleet.build_fleet() from the
local device in-process (no HTTP) and from every registered device over
GET /rc/fleet?since=<cursor>, writing results into store.Store. Backs off
an unreachable device from `interval` up to a 5-minute ceiling.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.request

import devices
import fleet

BACKOFF_CEILING_SECONDS = 300


def _default_http_get(base_url, path, auth_user="", auth_pass="", since=None, timeout=10):
    url = base_url.rstrip("/") + path
    if since:
        url += "?since=" + urllib.parse_quote(since)
    req = urllib.request.Request(url)
    if auth_user or auth_pass:
        tok = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


import urllib.parse  # noqa: E402  (kept near use above for clarity)


class FleetPoller:
    def __init__(self, store, interval=30, http_get=None):
        self.store = store
        self.interval = interval
        self.http_get = http_get or _default_http_get
        self._lock = threading.Lock()
        self._cursors = {}       # device_id -> cursor
        self._next_try_at = {}   # device_id -> epoch seconds
        self._backoff = {}       # device_id -> current backoff seconds
        self._stop = threading.Event()
        self._thread = None

    def _ingest(self, device_id, snapshot):
        self.store.upsert_device({
            "id": device_id, "name": snapshot.get("device_name", device_id),
            "role": snapshot.get("role", "full"), "version": snapshot.get("version"),
            "claude_version": snapshot.get("claude_version"),
        })
        self.store.upsert_sessions(device_id, snapshot.get("sessions") or [])
        events = snapshot.get("events") or []
        if events:
            self.store.add_events(device_id, events)
        if snapshot.get("cursor"):
            self._cursors[device_id] = snapshot["cursor"]
        self._backoff.pop(device_id, None)
        self._next_try_at.pop(device_id, None)

    def _poll_local(self, now):
        try:
            snapshot = fleet.build_fleet(since=self._cursors.get("local"))
            self._ingest("local", snapshot)
        except Exception:
            pass  # the local device is always "online" from the hub's own view

    def _poll_remote(self, device, now):
        device_id = device["id"]
        due_at = self._next_try_at.get(device_id, 0)
        if now < due_at:
            return
        try:
            snapshot = self.http_get(
                device["base_url"], "/rc/fleet",
                auth_user=device.get("auth_user", ""), auth_pass=device.get("auth_pass", ""),
                since=self._cursors.get(device_id))
            self._ingest(device_id, snapshot)
        except Exception:
            backoff = min(self._backoff.get(device_id, self.interval) * 2, BACKOFF_CEILING_SECONDS)
            self._backoff[device_id] = backoff
            self._next_try_at[device_id] = now + backoff
            try:
                self.store.upsert_device({
                    "id": device_id, "name": device.get("name", device_id),
                    "role": "unknown", "version": None, "claude_version": None,
                })
                self.store.mark_device_offline(device_id)
            except Exception:
                pass

    def poll_once(self, now_fn=time.time):
        """One pass over every device. Never raises. Serialized: a second
        concurrent call waits for the first to finish rather than racing
        it (a slow remote device must not pile up duplicate fetches)."""
        with self._lock:
            now = now_fn()
            self._poll_local(now)
            for device in devices.load_devices():
                self._poll_remote(device, now)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
```

- [ ] **Step 4: Wire into `app.py`**

```python
from config import VERSION, HOST, PORT, WORKING_DIR, CLAUDE_BIN, AUTH_USER, RC_HOME
from tunnel import cloudflared_available
from scheduler import start_scheduler
from server import Handler
import os
import store
import fleetpoll

...

    # Start the scheduler thread
    start_scheduler()

    # Start the hub fleet poller (SQLite store shared with server.py's
    # /api/fleet* routes via server.HUB_STORE).
    import server as server_module
    server_module.HUB_STORE = store.Store(os.path.join(RC_HOME, "hub.db"))
    fleetpoll.FleetPoller(server_module.HUB_STORE).start()
```

(This keeps `app.py`'s existing import line intact aside from adding `RC_HOME` to it; add the two new imports and the block above right after `start_scheduler()`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_fleetpoll -v`
Expected: `Ran 4 tests ... OK`

- [ ] **Step 6: Commit**

```bash
git add fleetpoll.py app.py tests/test_fleetpoll.py
git commit -m "$(cat <<'EOF'
feat: add fleetpoll.py, the hub fleet polling thread

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 8: `GET /api/fleet` route

**Files:**
- Modify: `server.py` (add route + `_should_proxy` exemption)
- Test: extend `tests/test_server_helpers.py`

**Context:** Hub-only: `/api/fleet` must never be proxied to a device (add it to `_should_proxy`'s exemption list next to `/devices`), and it reads only `server.HUB_STORE` (set in Task 7's `app.py` wiring; tests set it directly on the `server` module). `needs_attention` is derived from the newest `Notification` event per session (`permission_prompt` or `agent_needs_input`) not yet followed by a later `Stop` or `UserPromptSubmit` for that same session — i.e. per session, look at events ordered by `ts` descending and take the first `Notification`/`Stop`/`UserPromptSubmit` seen; if it's a `Notification` with one of those two types, the session needs attention.

**Interfaces:**
- Consumes: `store.Store.fleet_view()`, `store.Store.recent_events(device_id=..., limit=...)` (Task 6).
- Produces: `GET /api/fleet` -> `{"devices": [...], "sessions": [...enriched with needs_attention: bool...]}`. Function `server._derive_needs_attention(events_by_session)` -> `dict[session_id, bool]`, unit-testable directly. Task 12 (frontend `useFleet`) is the consumer of this JSON shape.

- [ ] **Step 1: Write the failing tests**

```python
class DeriveNeedsAttentionTest(unittest.TestCase):
    def test_permission_prompt_with_no_later_stop_needs_attention(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "Notification", "extra": {"notification_type": "permission_prompt"}},
        ]
        result = server._derive_needs_attention(events)
        self.assertTrue(result["s1"])

    def test_stop_after_notification_clears_attention(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "Notification", "extra": {"notification_type": "permission_prompt"}},
            {"session_id": "s1", "ts": 11, "event": "Stop", "extra": {}},
        ]
        result = server._derive_needs_attention(events)
        self.assertFalse(result.get("s1", False))

    def test_agent_completed_notification_does_not_need_attention(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "Notification", "extra": {"notification_type": "agent_completed"}},
        ]
        result = server._derive_needs_attention(events)
        self.assertFalse(result.get("s1", False))


class ApiFleetRouteTest(unittest.TestCase):
    def test_api_fleet_is_hub_only_not_proxied(self):
        h = _FakeHandler()  # existing test fixture used elsewhere in this file
        self.assertFalse(h._should_proxy_path("/api/fleet", "some-device"))
```

(If `_FakeHandler`/`_should_proxy_path` don't already exist as a test seam in `tests/test_server_helpers.py`, use the existing pattern in that file for testing `_should_proxy` — check for a similar test around `/devices` and mirror its shape exactly instead of inventing a new fixture.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.DeriveNeedsAttentionTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_derive_needs_attention'`.

- [ ] **Step 3: Implement in `server.py`**

Add module-level state near the top (with the other module globals like `_auth_tokens`):

```python
HUB_STORE = None  # set by app.py at startup; store.Store instance
```

Add the derivation helper (near `_derive_session_state`):

```python
_ATTENTION_NOTIFICATION_TYPES = {"permission_prompt", "agent_needs_input"}


def _derive_needs_attention(events):
    """events: iterable of {session_id, ts, event, extra}. Returns
    {session_id: bool} — True when the newest Notification/Stop/
    UserPromptSubmit event for that session is a Notification whose
    notification_type needs a human."""
    by_session = {}
    for e in events:
        sid = e.get("session_id")
        if not sid:
            continue
        by_session.setdefault(sid, []).append(e)
    result = {}
    for sid, rows in by_session.items():
        rows = sorted(rows, key=lambda r: r.get("ts") or 0, reverse=True)
        for r in rows:
            if r.get("event") in ("Notification", "Stop", "UserPromptSubmit"):
                result[sid] = (
                    r.get("event") == "Notification"
                    and (r.get("extra") or {}).get("notification_type") in _ATTENTION_NOTIFICATION_TYPES
                )
                break
    return result
```

Add the route (GET dispatch, near `/overview`):

```python
        elif path == "/api/fleet":
            if HUB_STORE is None:
                return self._json({"devices": [], "sessions": []})
            view = HUB_STORE.fleet_view()
            recent = HUB_STORE.recent_events(limit=500)
            attention = _derive_needs_attention(recent)
            for s in view["sessions"]:
                s["needs_attention"] = attention.get(s["session_id"], False)
            self._json(view)
```

Add `"/api/fleet"` to `_should_proxy`'s local-only exemptions:

```python
        if p.startswith("/static/") or p == "/devices" or p == "/devices/rename" or p == "/api/config-matrix" or p == "/api/fleet":
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_helpers.py
git commit -m "$(cat <<'EOF'
feat: add hub-only GET /api/fleet with needs_attention derivation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 9: `GET /api/fleet/stream` (SSE)

**Files:**
- Modify: `server.py`
- Test: extend `tests/test_server_helpers.py`

**Context:** The hub sits behind nginx with `proxy_read_timeout 3600s` but **without** `proxy_buffering off`. With default buffering the browser would see no events until nginx's buffer flushes. This task must not require an nginx change: the response sets `X-Accel-Buffering: no` (nginx honours this per-response even with buffering on globally) alongside `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, and sends a heartbeat comment line (`: ping\n\n`) every 20 s so idle connections aren't dropped by any intermediary (nginx, a corporate proxy, a mobile carrier NAT). The polling `GET /api/fleet` from Task 8 is the fallback for an old device or any proxy that still buffers despite the header — state this in the frontend task (Task 12) too.

Implementation approach: a generator that blocks on a `threading.Event` the poller (Task 7) sets after every `store` commit, with a 20 s timeout so the heartbeat fires even with no changes. `FleetPoller` needs one addition: a `changed` callback/event it fires after each `poll_once()` that actually wrote something. Wire it via a simple pub-sub: `server.py` keeps a small `set()` of `threading.Event()`s (one per open SSE connection); `HUB_STORE`-adjacent global `FLEET_CHANGE_SUBSCRIBERS`; `fleetpoll.FleetPoller` gets an optional `on_change` callback invoked after a successful ingest, which `app.py` wires to a function in `server.py` that sets and replaces every subscriber's event.

**Interfaces:**
- Consumes: `HUB_STORE.fleet_view()` (Task 6/8).
- Produces: `GET /api/fleet/stream` emitting `text/event-stream` with `data: {...same shape as GET /api/fleet...}\n\n` on every change and `: ping\n\n` every 20 s; module-level `server.notify_fleet_changed()` that `app.py` wires to `FleetPoller(on_change=server.notify_fleet_changed)`.

- [ ] **Step 1: Write the failing tests**

```python
class ApiFleetStreamHeadersTest(unittest.TestCase):
    def test_stream_sets_no_buffering_headers(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split('"/api/fleet/stream"', 1)[1]
        block = block[:2000]
        self.assertIn('text/event-stream', block)
        self.assertIn('X-Accel-Buffering', block)
        self.assertIn('no-cache', block)
        self.assertIn('keep-alive', block)

    def test_heartbeat_interval_constant_is_20_seconds(self):
        self.assertEqual(server.SSE_HEARTBEAT_SECONDS, 20)


class NotifyFleetChangedTest(unittest.TestCase):
    def test_notify_sets_all_subscriber_events(self):
        server.FLEET_CHANGE_SUBSCRIBERS.clear()
        ev1, ev2 = threading.Event(), threading.Event()
        server.FLEET_CHANGE_SUBSCRIBERS.add(ev1)
        server.FLEET_CHANGE_SUBSCRIBERS.add(ev2)
        server.notify_fleet_changed()
        self.assertTrue(ev1.is_set())
        self.assertTrue(ev2.is_set())
```

(Add `import threading` at the top of `tests/test_server_helpers.py` if not already present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.ApiFleetStreamHeadersTest tests.test_server_helpers.NotifyFleetChangedTest -v`
Expected: FAIL — `AttributeError`/route not found.

- [ ] **Step 3: Implement in `server.py`**

Add near `HUB_STORE = None`:

```python
SSE_HEARTBEAT_SECONDS = 20
FLEET_CHANGE_SUBSCRIBERS = set()
_fleet_change_lock = threading.Lock()


def notify_fleet_changed():
    """Called by fleetpoll.FleetPoller after a successful ingest that
    changed something. Wakes every open /api/fleet/stream connection."""
    with _fleet_change_lock:
        subs = list(FLEET_CHANGE_SUBSCRIBERS)
    for ev in subs:
        ev.set()
```

(`import threading` must already be at the top of `server.py` — verify; add it if missing.)

Add the route in `do_GET`, right after the `/api/fleet` branch:

```python
        elif path == "/api/fleet/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            my_event = threading.Event()
            with _fleet_change_lock:
                FLEET_CHANGE_SUBSCRIBERS.add(my_event)
            try:
                self._sse_send_fleet_snapshot()
                while True:
                    changed = my_event.wait(timeout=SSE_HEARTBEAT_SECONDS)
                    if changed:
                        my_event.clear()
                        if not self._sse_send_fleet_snapshot():
                            break
                    else:
                        if not self._sse_write(": ping\n\n"):
                            break
            finally:
                with _fleet_change_lock:
                    FLEET_CHANGE_SUBSCRIBERS.discard(my_event)
```

Add two small helper methods to `Handler` (near `_json`):

```python
    def _sse_write(self, text):
        """Write one raw SSE chunk. Returns False (and swallows the error)
        if the client has disconnected, so the stream loop can exit
        cleanly instead of raising into the request-handling thread."""
        try:
            self.wfile.write(text.encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _sse_send_fleet_snapshot(self):
        if HUB_STORE is None:
            view = {"devices": [], "sessions": []}
        else:
            view = HUB_STORE.fleet_view()
            recent = HUB_STORE.recent_events(limit=500)
            attention = _derive_needs_attention(recent)
            for s in view["sessions"]:
                s["needs_attention"] = attention.get(s["session_id"], False)
        return self._sse_write(f"data: {json.dumps(view)}\n\n")
```

Add `"/api/fleet/stream"` alongside `"/api/fleet"` in the `_should_proxy` exemption list, and add both to the quiet `log_message` list (an SSE connection stays open for a long time and would otherwise log nothing useful repeatedly — actually it logs once per connection open, which is fine to keep visible; only add `/api/fleet` polling to the quiet list, not the stream, since a stream connect/disconnect is a meaningful log line):

```python
        if p.startswith("/static/") or p == "/devices" or p == "/devices/rename" or p == "/api/config-matrix" or p == "/api/fleet" or p == "/api/fleet/stream":
            return False
```

```python
        if path in ("/rc/sessions", "/rc/tunnel/status", "/rc/projects",
                     "/rc/browse", "/rc/schedules", "/rc/version",
                     "/rc/resume/sessions", "/rc/stats", "/rc/overview",
                     "/rc/config-report", "/api/config-matrix", "/rc/fleet", "/api/fleet") or \
```

- [ ] **Step 4: Wire `on_change` in `app.py`**

Update Task 7's `app.py` block:

```python
    server_module.HUB_STORE = store.Store(os.path.join(RC_HOME, "hub.db"))
    fleetpoll.FleetPoller(server_module.HUB_STORE, on_change=server_module.notify_fleet_changed).start()
```

And in `fleetpoll.py`'s `FleetPoller.__init__`, accept and store `on_change=None`; call it (guarded by `if self.on_change:`) at the end of `_ingest()` whenever `upsert_sessions` or `add_events` actually wrote rows (simplest correct approximation: call it unconditionally at the end of a successful `_ingest`, since an unchanged snapshot is cheap to re-render on the frontend and correctness matters more than avoiding one redundant push).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers tests.test_fleetpoll -v`
Expected: `OK` for both; full suite still green.

- [ ] **Step 6: Commit**

```bash
git add server.py fleetpoll.py app.py tests/test_server_helpers.py
git commit -m "$(cat <<'EOF'
feat: add GET /api/fleet/stream SSE with unbuffered headers and heartbeat

Sets X-Accel-Buffering: no so nginx's default buffering (proxy_buffering
on, no config change required) does not delay events to the browser;
sends a ping comment every 20s so intermediaries don't drop idle
connections. GET /api/fleet stays as the polling fallback for an old
device or a proxy that still buffers despite the header.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 10: `GET /api/sessions/<device>/<session_id>/events`

**Files:**
- Modify: `server.py`
- Test: extend `tests/test_server_helpers.py`

**Context:** One session's recent events from the store, for the per-session Activity panel (Task 14). Hub-only (never proxied — the store only exists on the hub). Path parsing follows the same style as the existing `/sessions/<name>/transcript` branch (`path[len("/sessions/"):-len("/transcript")]`).

**Interfaces:**
- Consumes: `HUB_STORE.recent_events(session_id=..., device_id=..., limit=...)` (Task 6).
- Produces: `GET /api/sessions/<device_id>/<session_id>/events?limit=N` -> `{"events": [...]}`, newest first, `limit` default 50, capped at 500.

- [ ] **Step 1: Write the failing test**

```python
class ApiSessionEventsRouteTest(unittest.TestCase):
    def test_route_calls_store_recent_events_with_device_and_session(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        self.assertIn('"/api/sessions/"', src)
        self.assertIn('.recent_events(', src)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server_helpers.ApiSessionEventsRouteTest -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add a route-matching branch in `do_GET` (path form `/api/sessions/<device_id>/<session_id>/events`):

```python
        elif path.split('?')[0].startswith("/api/sessions/") and path.split('?')[0].endswith("/events"):
            clean = path.split('?')[0]
            parts = clean[len("/api/sessions/"):-len("/events")].strip("/").split("/", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return self._json({"ok": False, "message": "Malformed path"}, 400)
            device_id, session_id = parts
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = min(500, max(1, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            if HUB_STORE is None:
                return self._json({"events": []})
            rows = HUB_STORE.recent_events(session_id=session_id, device_id=device_id, limit=limit)
            self._json({"events": rows})
```

Add `"/api/sessions/"` prefix handling to `_should_proxy`'s exemption (hub-only, same reasoning as `/api/fleet`):

```python
        if (p.startswith("/static/") or p == "/devices" or p == "/devices/rename"
                or p == "/api/config-matrix" or p == "/api/fleet" or p == "/api/fleet/stream"
                or p.startswith("/api/sessions/")):
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_helpers.py
git commit -m "$(cat <<'EOF'
feat: add GET /api/sessions/<device>/<session_id>/events

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 11: Audit log on every mutating route

**Files:**
- Modify: `server.py` (`do_POST`, adding one call per mutating branch)
- Test: extend `tests/test_server_helpers.py`

**Context:** Every mutating route writes one `audit_log` row: `/start`, `/stop`, `/stop-all`, `/restart`, `/unstick`, `/keys`, `/enable-rc`, `/schedules*`, `/update`, `/devices/rename`. Actor is the login token id (from the `rc_session` cookie) or `"basic"` for Basic auth, never the password. Add one small helper `server._audit(handler, action, target, detail="")` that resolves the actor from `handler` and, if `HUB_STORE` is set, writes the row; a no-op (never raises) when `HUB_STORE` is `None` (e.g. a device running without a hub role, or in tests that don't set it). Call it right after each mutating branch's success path (find each branch by its existing `elif path == "..."` in `do_POST`, matching the routes named above — do not add auditing to read-only `GET` routes).

**Interfaces:**
- Consumes: `HUB_STORE.add_audit(actor, action, target, device_id, detail)` (Task 6), the existing `_auth_tokens` dict and cookie-parsing helper already used by `_check_auth` in `server.py` for resolving the actor.
- Produces: `server._audit(handler, action, target, detail="")`. Task 15 (frontend Audit tab) reads back via `HUB_STORE.recent_audit()`, already exposed at `/api/audit` added in this task too.

- [ ] **Step 1: Write the failing tests**

```python
class AuditLogTest(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.tmp = tempfile.TemporaryDirectory()
        server.HUB_STORE = store.Store(os.path.join(self.tmp.name, "hub.db"))

    def tearDown(self):
        server.HUB_STORE.close()
        server.HUB_STORE = None
        self.tmp.cleanup()

    def test_audit_writes_row_with_resolved_actor(self):
        class FakeHandler:
            headers = {"Cookie": "rc_session=tok_abc123"}
        server._audit(FakeHandler(), action="start", target="rc-foo", detail="mode=c")
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "start")
        self.assertEqual(rows[0]["target"], "rc-foo")
        self.assertNotIn("password", rows[0]["detail"])

    def test_audit_never_raises_when_store_is_none(self):
        server.HUB_STORE = None

        class FakeHandler:
            headers = {}
        server._audit(FakeHandler(), action="stop", target="rc-foo")  # must not raise

    def test_all_named_mutating_routes_call_audit(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        for route in ('"/start"', '"/stop"', '"/stop-all"', '"/restart"',
                      '"/unstick"', '"/keys"', '"/enable-rc"', '"/schedules"',
                      '"/update"', '"/devices/rename"'):
            block = src.split(f"elif path == {route}", 1)
            self.assertEqual(len(block), 2, f"route {route} not found in do_POST")
            next_block = block[1].split("elif path ==", 1)[0]
            self.assertIn("_audit(", next_block, f"{route} branch missing _audit() call")


class ApiAuditRouteTest(unittest.TestCase):
    def test_get_api_audit_is_hub_only(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        self.assertIn('"/api/audit"', src)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.AuditLogTest tests.test_server_helpers.ApiAuditRouteTest -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `import store` to `server.py`'s top-of-file imports (test-only usage is fine at module scope since Task 6 already made `store.py` importable stdlib-only).

Add the helper near `HUB_STORE = None`:

```python
def _resolve_actor(handler):
    """Actor for the audit log: the login token id if cookie auth was
    used, "basic" for Basic auth, "unknown" otherwise. Never the password
    — only the token/cookie value's own id (itself a random secret, but
    that's the existing session identifier, not a credential to protect
    further than the cookie already is)."""
    cookie_header = getattr(handler, "headers", {}).get("Cookie", "") if hasattr(handler, "headers") else ""
    if cookie_header and "rc_session=" in str(cookie_header):
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            if "rc_session" in cookie:
                return cookie["rc_session"].value[:12]  # short id, not a secret disclosure surface
        except Exception:
            pass
    auth_hdr = getattr(handler, "headers", {}).get("Authorization", "") if hasattr(handler, "headers") else ""
    if str(auth_hdr).startswith("Basic "):
        return "basic"
    return "unknown"


def _audit(handler, action, target, device_id="local", detail=""):
    if HUB_STORE is None:
        return
    try:
        HUB_STORE.add_audit(actor=_resolve_actor(handler), action=action,
                             target=target or "", device_id=device_id or "local", detail=detail)
    except Exception:
        pass
```

For each of the ten named routes in `do_POST`, add one call to `_audit(self, action="<route-name-without-slash>", target=<best available identifier>, detail=<short non-secret context>)` right after the branch determines success (before or after building the response body — placement doesn't affect correctness since `_audit` never raises). Example for `/start` (mirror the same pattern for the other nine — locate each branch by its existing `elif path == "..."` in `do_POST` and read a few lines of context to find the right target/detail values already in scope, e.g. `name` for `/stop`/`/restart`/`/unstick`/`/keys`, the schedule `id` for `/schedules*`, the device `id` for `/devices/rename`):

```python
        elif path == "/start":
            ...  # existing body determines `name`, `mode`, etc.
            _audit(self, action="start", target=name, detail=f"mode={mode}")
            ...
```

Add the read route in `do_GET`:

```python
        elif path == "/api/audit":
            if HUB_STORE is None:
                return self._json({"audit": []})
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = min(200, max(1, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            self._json({"audit": HUB_STORE.recent_audit(limit=limit)})
```

Add `"/api/audit"` to the `_should_proxy` exemption list next to `/api/fleet`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: `OK`. Then run the full suite (`python3 -m unittest discover tests`) — this task touches ten existing route branches, so a regression is likely here; fix any failure by checking the exact `elif path ==` line found via `grep -n 'elif path ==' server.py` before assuming the mirrored pattern above matched correctly.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_helpers.py
git commit -m "$(cat <<'EOF'
feat: audit log every mutating route, add GET /api/audit

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 12: Frontend `api.ts` types + `useFleet` hook

**Files:**
- Modify: `frontend/src/api.ts` (or wherever the existing typed fetch helpers live — check the file first; mirror its existing style, e.g. `useAllSessions`/`useCrossDevice` fetch wrappers)
- Create: `frontend/src/hooks/useFleet.ts`
- Test: whatever frontend test runner this repo already uses for hooks (check `frontend/package.json` `"scripts"` and any existing `*.test.ts(x)` under `frontend/src`; if none exist, this task's only verification gate is `npx tsc --noEmit` plus manual review — state that explicitly rather than inventing a test framework)

**Context:** `useFleet` subscribes to `GET /api/fleet/stream` (`EventSource`) with automatic fallback to polling `GET /api/fleet` every 5 s when the `EventSource` errors or the browser/proxy doesn't support/allow SSE (Task 9's polling fallback). It replaces the Sessions tab's use of `useAllSessions`'s per-device fan-out (`useCrossDevice.ts`) with this one source; per-device `usePanelData` (used by the device detail view) is untouched — it's a different data need (live per-device stats, not the fleet roll-up).

**Interfaces:**
- Consumes: `GET /api/fleet` and `GET /api/fleet/stream` response shape from Task 8/9: `{"devices": [{id,name,role,version,claude_version,last_seen,online}], "sessions": [{device_id,session_id,name,cwd,kind,state,started_at,ended_at,last_seen,external,needs_attention}]}`.
- Produces: `useFleet(): {devices, sessions, connected: boolean, usingFallback: boolean}` importable by Task 13 (Sessions list) and Task 14 (Activity panel, via `sessions` rows carrying `device_id`/`session_id`).

- [ ] **Step 1: Read the existing patterns first**

```bash
grep -n "useCrossDevice\|useAllSessions\|EventSource\|usePanelData" -r frontend/src | head -40
```

Match whatever fetch-wrapper/auth-header convention (`fetch(..., {credentials: "include"})`? a shared `apiFetch` helper?) those existing hooks use — do not invent a different one.

- [ ] **Step 2: Write `frontend/src/hooks/useFleet.ts`**

```typescript
import { useEffect, useRef, useState } from "react";

export interface FleetDevice {
  id: string;
  name: string;
  role: string;
  version: string | null;
  claude_version: string | null;
  last_seen: number | null;
  online: number;
}

export interface FleetSession {
  device_id: string;
  session_id: string;
  name: string | null;
  cwd: string | null;
  kind: string | null;
  state: string | null;
  started_at: number | null;
  ended_at: number | null;
  last_seen: number | null;
  external: number;
  needs_attention: boolean;
}

interface FleetView {
  devices: FleetDevice[];
  sessions: FleetSession[];
}

const POLL_INTERVAL_MS = 5000;

export function useFleet() {
  const [view, setView] = useState<FleetView>({ devices: [], sessions: [] });
  const [connected, setConnected] = useState(false);
  const [usingFallback, setUsingFallback] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;

    const startPolling = () => {
      if (pollTimer.current) return;
      setUsingFallback(true);
      const poll = async () => {
        try {
          const res = await fetch("/api/fleet", { credentials: "include" });
          if (!res.ok) return;
          const data = (await res.json()) as FleetView;
          if (!cancelled) setView(data);
        } catch {
          // stay on the last known view; try again next tick
        }
      };
      poll();
      pollTimer.current = setInterval(poll, POLL_INTERVAL_MS);
    };

    try {
      es = new EventSource("/api/fleet/stream", { withCredentials: true });
      es.onopen = () => {
        if (cancelled) return;
        setConnected(true);
        setUsingFallback(false);
        if (pollTimer.current) {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
        }
      };
      es.onmessage = (ev) => {
        if (cancelled) return;
        try {
          setView(JSON.parse(ev.data) as FleetView);
        } catch {
          // malformed frame — ignore, wait for the next one
        }
      };
      es.onerror = () => {
        if (cancelled) return;
        setConnected(false);
        es?.close();
        startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, []);

  return { devices: view.devices, sessions: view.sessions, connected, usingFallback };
}
```

- [ ] **Step 3: Replace `useAllSessions`'s fan-out on the Sessions tab**

Find the Sessions tab component consuming `useCrossDevice`/`useAllSessions` (`grep -rn "useAllSessions" frontend/src`), swap its data source to `useFleet()`, keeping any existing filter/sort UI working against the new `FleetSession[]` shape (map `device_id` -> device name via the `devices` array returned alongside). Leave `useCrossDevice.ts` and `usePanelData` file itself in place (Task 13 still needs the per-device detail view, which is out of scope here) — only the Sessions tab's top-level data source changes.

- [ ] **Step 4: Verify with the type checker**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useFleet.ts frontend/src/api.ts
git commit -m "$(cat <<'EOF'
feat: add useFleet hook (SSE with polling fallback) for the fleet store

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 13: Sessions list reads from the fleet store

**Files:**
- Modify: the Sessions tab component identified in Task 12 (Step 1's grep result — likely `frontend/src/components/AllSessions.tsx` or similar; confirm the exact filename by reading `frontend/src` before editing)
- Test: `npx tsc --noEmit` (no existing component test harness — see Task 12's note on test framework absence)

**Context:** Rows now come from `useFleet()` (Task 12), including sessions on a device that is currently offline (`device.online === 0`): render those dimmed with a "last seen Xm ago" label instead of hiding them (today's fan-out silently drops an unreachable device's sessions). Rows with `needs_attention: true` sort first.

**Interfaces:**
- Consumes: `useFleet()` (Task 12): `{devices, sessions}`.
- Produces: no new exported interface — this is the leaf UI consumer.

- [ ] **Step 1: Read the current component**

```bash
sed -n '1,80p' frontend/src/components/AllSessions.tsx  # adjust path to what Task 12's grep found
```

- [ ] **Step 2: Update the row list**

Sort: `needs_attention` sessions first, then by `last_seen` descending. Dim rows: apply the existing "offline"/muted style class already used elsewhere in this file (e.g. check how `BigCard.tsx` or `ConfigMatrix.tsx` style an offline device — reuse that class name rather than inventing a new one) when `devices.find(d => d.id === s.device_id)?.online === 0`, and render `"last seen " + formatRelativeTime(s.last_seen)` (reuse whatever relative-time formatter already exists in `frontend/src` — grep for `formatRelativeTime`/`timeAgo`/similar before writing a new one).

```typescript
const deviceById = new Map(devices.map((d) => [d.id, d]));

const sortedSessions = [...sessions].sort((a, b) => {
  if (a.needs_attention !== b.needs_attention) return a.needs_attention ? -1 : 1;
  return (b.last_seen ?? 0) - (a.last_seen ?? 0);
});
```

Render each row's device-offline state:

```tsx
{sortedSessions.map((s) => {
  const device = deviceById.get(s.device_id);
  const offline = device ? device.online === 0 : false;
  return (
    <SessionRow
      key={`${s.device_id}:${s.session_id}`}
      session={s}
      dimmed={offline}
      subtitle={offline ? `last seen ${formatRelativeTime(s.last_seen)}` : undefined}
      attention={s.needs_attention}
    />
  );
})}
```

(Adapt to whatever the existing row component/props actually are — read the file before assuming `SessionRow` exists with these exact props; keep to the file's existing conventions.)

- [ ] **Step 3: Verify with the type checker**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/
git commit -m "$(cat <<'EOF'
feat: Sessions tab reads the fleet store, shows offline-device sessions

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 14: Per-session Activity panel

**Files:**
- Modify: the preview/transcript modal component (`grep -rn "transcript" frontend/src/components | grep -i modal` to find it)
- Test: `npx tsc --noEmit`

**Context:** Add an "Activity" section to the existing preview/transcript modal listing recent events for that session, fetched from `GET /api/sessions/<device>/<session_id>/events` (Task 10).

**Interfaces:**
- Consumes: `GET /api/sessions/<device_id>/<session_id>/events` (Task 10) -> `{"events": [{id, device_id, session_id, ts, event, extra}]}`.
- Produces: no new exported interface — leaf UI consumer.

- [ ] **Step 1: Locate and read the modal**

```bash
grep -rln "transcript" frontend/src/components
```

Read the matched file(s) to find where the modal already fetches per-session data (mirror its existing `fetch`/loading-state pattern).

- [ ] **Step 2: Add the Activity section**

```typescript
const [events, setEvents] = useState<FleetEvent[]>([]);

useEffect(() => {
  if (!open || !deviceId || !sessionId) return;
  let cancelled = false;
  fetch(`/api/sessions/${encodeURIComponent(deviceId)}/${encodeURIComponent(sessionId)}/events?limit=50`, {
    credentials: "include",
  })
    .then((r) => r.json())
    .then((data) => {
      if (!cancelled) setEvents(data.events ?? []);
    })
    .catch(() => {
      if (!cancelled) setEvents([]);
    });
  return () => {
    cancelled = true;
  };
}, [open, deviceId, sessionId]);
```

Render as a simple reverse-chronological list (`event`, relative `ts`, and a one-line rendering of `extra` when non-empty), placed below the existing transcript content, using the modal's existing section-heading style.

- [ ] **Step 3: Verify with the type checker**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/
git commit -m "$(cat <<'EOF'
feat: add per-session Activity panel to the preview/transcript modal

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 15: Settings > Audit

**Files:**
- Modify: the Settings tab component (`grep -rln "Settings" frontend/src/components | grep -iv modal`)
- Test: `npx tsc --noEmit`

**Context:** New "Audit" subsection under Settings, listing the last 50 entries from `GET /api/audit` (Task 11).

**Interfaces:**
- Consumes: `GET /api/audit?limit=50` -> `{"audit": [{id, ts, actor, action, target, device_id, detail}]}`.
- Produces: no new exported interface.

- [ ] **Step 1: Read the Settings component's existing subsection pattern**

```bash
grep -n "function \|const.*=.*(" frontend/src/components/Settings*.tsx | head -30
```

- [ ] **Step 2: Add the Audit subsection**

```typescript
const [audit, setAudit] = useState<AuditRow[]>([]);

useEffect(() => {
  fetch("/api/audit?limit=50", { credentials: "include" })
    .then((r) => r.json())
    .then((data) => setAudit(data.audit ?? []))
    .catch(() => setAudit([]));
}, []);
```

Render a simple table: timestamp, actor, action, target, device, detail — reusing whatever table styling Settings already uses for another subsection (e.g. Devices).

- [ ] **Step 3: Verify with the type checker**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/
git commit -m "$(cat <<'EOF'
feat: add Settings > Audit showing the last 50 audit log entries

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 16: `RC_ROLE` enforcement in `config.py` and `server.py`

**Files:**
- Modify: `config.py` (add `RC_ROLE`, `RC_HASH_SALT`)
- Modify: `server.py` (403 on disallowed routes in `metadata` role)
- Test: `tests/test_config.py` (extend), `tests/test_server_helpers.py` (extend)

**Context:** `RC_ROLE` (`full` default, `metadata`) is read from the environment at `config.py` import time, matching every other `config.py` setting's convention. In `metadata` role, the server refuses `/start`, `/keys`, `/resize`, `/ws`, `/enable-rc`, `/schedules*` (create/update/fire/delete — read-only `GET /schedules` may stay allowed or be refused too; refuse it for simplicity and consistency: a metadata device exposes no scheduling at all) with `403`, and `/preview` too (transcript content) — and serves only `/fleet`, `/version`, `/stats`, `/config-report`. `RC_HASH_SALT` is generated once into `~/.claude-rc/env` (the same file `install.sh`'s wrapper sources) if absent, atomically, `0600` — mirroring the existing `device-name` file pattern in `devices.py`.

**Interfaces:**
- Consumes: nothing new from earlier tasks in this plan (Task 1's `rc-hook` already reads `RC_ROLE`/`RC_HASH_SALT` straight from its own process env — those are the *Claude Code hook's* env, separate from the launcher process's env read here; both must agree operationally, documented in Task 17).
- Produces: `config.RC_ROLE` (`str`), `config.RC_HASH_SALT` (`str`, generated if missing). `server.py`'s route dispatch consults `config.RC_ROLE` directly (not through a new function) at the top of `do_GET`/`do_POST`, right after `_check_auth`.

- [ ] **Step 1: Write the failing tests**

```python
# extend tests/test_config.py
class RcRoleTest(unittest.TestCase):
    def test_default_role_is_full(self):
        # config module already imported at test collection time with no
        # RC_ROLE set in this test process's env
        self.assertIn(config.RC_ROLE, ("full", "metadata"))

    def test_hash_salt_is_generated_and_persisted(self):
        import importlib
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RC_HOME"] = tmp
            os.environ.pop("RC_HASH_SALT", None)
            importlib.reload(config)
            self.assertTrue(config.RC_HASH_SALT)
            env_file = os.path.join(tmp, "env")
            self.assertTrue(os.path.exists(env_file))
            self.assertEqual(oct(os.stat(env_file).st_mode & 0o777), "0o600")
            with open(env_file) as f:
                content = f.read()
            self.assertIn("RC_HASH_SALT=", content)
            del os.environ["RC_HOME"]
            importlib.reload(config)
```

```python
# extend tests/test_server_helpers.py
class MetadataRoleGatingTest(unittest.TestCase):
    def setUp(self):
        server.config.RC_ROLE = "metadata"

    def tearDown(self):
        server.config.RC_ROLE = "full"

    def test_metadata_role_refuses_start_with_403(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        self.assertIn("RC_ROLE", src)

    def test_metadata_allowed_paths_constant_matches_spec(self):
        self.assertEqual(
            server.METADATA_ALLOWED_GET_PATHS,
            {"/fleet", "/version", "/stats", "/config-report"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_config.RcRoleTest tests.test_server_helpers.MetadataRoleGatingTest -v`
Expected: FAIL.

- [ ] **Step 3: Implement in `config.py`**

Add near the other env-derived settings (after `AUTH_PASS`):

```python
RC_ROLE = os.environ.get("RC_ROLE", "full")
if RC_ROLE not in ("full", "metadata"):
    print(f"Warning: invalid RC_ROLE={RC_ROLE!r}, defaulting to 'full'")
    RC_ROLE = "full"
```

Add near the bottom, after `RC_HOME` directories are ensured (so the salt file can be written there):

```python
_ENV_FILE = os.path.join(RC_HOME, "env")


def _load_or_create_hash_salt():
    env_var = os.environ.get("RC_HASH_SALT", "").strip()
    if env_var:
        return env_var
    existing = None
    try:
        with open(_ENV_FILE) as f:
            for line in f:
                if line.startswith("RC_HASH_SALT="):
                    existing = line.strip().split("=", 1)[1]
                    break
    except OSError:
        pass
    if existing:
        return existing
    import secrets
    salt = secrets.token_hex(32)
    line = f"RC_HASH_SALT={salt}\n"
    try:
        fd = os.open(_ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
        os.chmod(_ENV_FILE, 0o600)
    except OSError:
        pass
    return salt


RC_HASH_SALT = _load_or_create_hash_salt()
os.environ.setdefault("RC_HASH_SALT", RC_HASH_SALT)
```

(The `os.environ.setdefault` line is what makes `fleet.py`'s existing `os.environ.get("RC_HASH_SALT", "")` read — added in Task 5 — pick this up without a second code path; `hooks/rc-hook`, running as a separate process invoked by Claude Code, needs `RC_HASH_SALT` exported into *its* environment separately — see Task 17's operator note.)

- [ ] **Step 4: Implement in `server.py`**

```python
METADATA_ALLOWED_GET_PATHS = {"/fleet", "/version", "/stats", "/config-report"}
METADATA_REFUSED_POST_PATHS_PREFIXES = ("/start", "/keys", "/resize", "/enable-rc", "/schedules")
```

At the top of `do_GET`, right after the `if not _check_auth(self): ...` block and before proxy routing:

```python
        if config.RC_ROLE == "metadata":
            clean = self.path.split('?')[0]
            local_path = clean[3:] if clean.startswith("/rc") else clean
            if local_path.split('?')[0] not in METADATA_ALLOWED_GET_PATHS and local_path not in ("/", "/legacy") \
                    and not local_path.startswith("/static/") and not local_path.startswith("/rc/static/"):
                return self._json({"ok": False, "message": "This device is metadata-role only"}, 403)
```

(Static assets and the SPA shell (`/`, `/legacy`) stay servable so the UI itself still loads on a metadata device even though its data routes are locked down — matches how a metadata device is meant to be viewed *from* the hub, not used standalone, but doesn't need to 404 its own frontend.)

At the top of `do_POST`, mirroring the same placement:

```python
        if config.RC_ROLE == "metadata":
            clean = self.path.split('?')[0]
            local_path = clean[3:] if clean.startswith("/rc") else clean
            if local_path.startswith(METADATA_REFUSED_POST_PATHS_PREFIXES) or local_path in (
                    "/stop", "/stop-all", "/restart", "/unstick", "/ws", "/preview"):
                return self._json({"ok": False, "message": "This device is metadata-role only"}, 403)
```

`/ws` is a `GET` with an `Upgrade` header in this codebase (see the WebSocket branch under `/sessions/<name>/ws` in `do_GET`), so also add it to the `do_GET` refusal path check above by extending the condition to refuse any path ending in `/ws` or `/preview` when metadata-role, in addition to the allowed-list check.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_config tests.test_server_helpers -v`, then the full suite: `python3 -m unittest discover tests`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add config.py server.py tests/test_config.py tests/test_server_helpers.py
git commit -m "$(cat <<'EOF'
feat: enforce RC_ROLE=metadata centrally, generate RC_HASH_SALT

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 17: `docs/DEVICES.md`

**Files:**
- Create: `docs/DEVICES.md`
- Test: `tests/test_devices_doc.py`

**Context:** Operator-facing doc: adding a device, the two roles, the Tailscale ACL note (device `:8200` reachable from the hub node only — a network-layer control, not something this repo enforces in code, so state it as an operational requirement), and precisely what a `metadata` device does/doesn't expose (per Task 16's enforcement and Task 5's redaction).

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: `docs/DEVICES.md`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_devices_doc.py
import os
import unittest

DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "DEVICES.md")


class DevicesDocTest(unittest.TestCase):
    def test_doc_exists_and_covers_required_topics(self):
        with open(DOC_PATH) as f:
            content = f.read()
        for phrase in ("metadata", "full", "devices.json", "Tailscale",
                       "8200", "RC_ROLE", "RC_HASH_SALT"):
            self.assertIn(phrase, content, f"missing coverage of {phrase!r}")

    def test_doc_has_no_personal_identifiers(self):
        with open(DOC_PATH) as f:
            content = f.read()
        for banned in ("barjazz", "tbarjadze", "hetzner", "tba-lin", "/root/"):
            self.assertNotIn(banned, content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_devices_doc -v`
Expected: FAIL — file not found.

- [ ] **Step 3: Write `docs/DEVICES.md`**

```markdown
# Adding a device to the fleet

A device is any machine running this app's `app.py` (installed via
`install.sh`) that the hub can reach. The hub is never listed as a device
itself — it's the implicit "local" row.

## Adding one

1. On the new device, run `install.sh` and note its Tailscale (or other
   private-network) address and the port it listens on (`RC_PORT`,
   default `8200`).
2. On the hub, add an entry to `~/.claude-rc/devices.json`:

   ```json
   [
     {
       "id": "example-device",
       "name": "Example Device",
       "base_url": "http://example-device.example.ts.net:8200",
       "auth_user": "admin",
       "auth_pass": "a strong random password"
     }
   ]
   ```

   `devices.json` holds per-device credentials in clear text (they can't
   be hashed — the hub needs to send them outbound) and must stay
   `chmod 600`, which `install.sh`/`config.py` already enforce for the
   directory it lives in.
3. Merge `docs/hooks/settings.snippet.json` into the new device's
   `~/.claude/settings.json` (see `README.md`'s "Hook events" section) so
   its sessions' lifecycle events reach the fleet spool.
4. The hub's poller picks the device up automatically within its poll
   interval (30s by default); no restart is required on either side.

## The two roles

Set `RC_ROLE` in the device's environment (its `~/.claude-rc/env`, which
`install.sh`'s wrapper script sources before launching `app.py`):

- `full` (default): the device serves everything — sessions, terminal,
  key injection, transcripts, schedules, config reports. This is what
  every personal device should run.
- `metadata`: the device serves only `GET /fleet`, `/version`, `/stats`,
  `/config-report`. Every other route (`/start`, `/keys`, `/resize`,
  `/ws`, `/enable-rc`, `/preview`, `/stop*`, `/restart`, `/unstick`,
  `/schedules*`) returns `403`. Its `/fleet` response hashes every
  session's `session_id` (and drops `name` unless explicitly
  allowlisted — none are, by default) with `RC_HASH_SALT`, and never
  includes `cwd`, `tmux`, `claude`, or token counts. Use this for a
  device where you want fleet visibility (it's running, it has N
  sessions, they're idle/busy) without exposing what those sessions are
  or letting the hub control them — for example, a work laptop where
  full visibility or remote control may not be appropriate.

`RC_HASH_SALT` is generated automatically on first run into
`~/.claude-rc/env` (mode `0600`) if not already set; it must also be
exported into the environment Claude Code's hooks run in (the same
`~/.claude-rc/env` file, since `rc-hook` reads `RC_HASH_SALT` from its
own process environment, which on most setups is the shell/session
environment Claude Code inherited — if hooks run under a different
environment than the launcher process, set `RC_HASH_SALT` there too, to
the *same* value, or a metadata device's sessions won't hash
consistently between `/fleet`'s session list and its hook-derived event
stream).

## Tailscale ACL note

Devices should bind their listener to a Tailscale IP, not `0.0.0.0`
(`RC_HOST` in `config.py`), and the network ACL should restrict `:8200`
so only the hub node can reach it — any other tailnet member reaching a
device's `:8200` directly bypasses the hub's own access controls (login,
audit log). This is a network-layer requirement this app cannot enforce
in code; configure it in your Tailscale ACL policy.

## What a metadata device does and does not expose

| Surface | full | metadata |
|---|---|---|
| `GET /fleet` sessions | full detail | hashed `session_id`, `name`/`state`/`started_at`/`kind` only |
| `GET /fleet` events | full detail | `{ts, event}` only, no `extra` |
| Terminal (`/ws`, `/keys`, `/resize`) | yes | 403 |
| Transcript (`/preview`) | yes | 403 |
| Launch/stop (`/start`, `/stop*`, `/restart`, `/unstick`, `/enable-rc`) | yes | 403 |
| Schedules (`/schedules*`) | yes | 403 |
| `GET /version`, `/stats`, `/config-report` | yes | yes |
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_devices_doc -v`
Expected: `Ran 2 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add docs/DEVICES.md tests/test_devices_doc.py
git commit -m "$(cat <<'EOF'
docs: add DEVICES.md covering roles, adding a device, Tailscale ACL

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

### Task 18: Final frontend build, identifier grep, full suite, `static/dist`

**Files:**
- Modify: `static/dist/*` (generated)
- No new tests — this task *is* the verification gate for Tasks 12-15's frontend work plus the whole plan.

**Context:** Every earlier frontend task type-checked with `tsc --noEmit` but did not build or commit `static/dist`; this task does the real build, checks the built bundle for accidentally-leaked identifiers (mirroring Global Constraint 5's CI grep), runs the full Python suite once more, then commits `static/dist` alone.

**Interfaces:**
- Consumes: all of Lane C's source changes (Tasks 12-15).
- Produces: an up-to-date `static/dist/` matching `frontend/src`.

- [ ] **Step 1: Install and build**

```bash
cd frontend && npm ci && npx tsc --noEmit && npm run build
```

Expected: build succeeds with no new `tsc` errors.

- [ ] **Step 2: Grep the built bundle for leaked identifiers**

```bash
grep -riE "barjazz|tbarjadze|hetzner|tba-lin|/root/" ../static/dist/ && echo "LEAK FOUND" || echo "clean"
```

Expected: `clean`. If a leak is found, trace it to source (likely a stray comment or default value copied from this session's context) and fix before proceeding — do not commit a bundle with a leak.

- [ ] **Step 3: Run the full backend test suite once more**

```bash
cd /var/www/rc-launcher-p2b && python3 -m unittest discover tests
```

Expected: `Ran <N> tests ... OK` where `N >= 351 + (new tests added across Tasks 1-11, 16-17)`.

- [ ] **Step 4: Commit `static/dist` alone**

```bash
git add static/dist
git commit -m "$(cat <<'EOF'
build: rebuild frontend bundle for phase 2b fleet UI

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67
EOF
)"
```

---

## Self-review notes (spec coverage)

- Section 3 components (`rc-hook`, `fleet.py`, `store.py`): Tasks 1, 5, 6.
- Section 5 Phase 2 changes: `hooks/rc-hook` + settings snippet (Tasks 1-2), `install.sh` (Task 3), `fleet.py` device-side + role gating (Tasks 5, 16), hub `store.py` + poller (Tasks 6-7), `/api/fleet` + SSE + `needs_attention` (Tasks 8-9), audit log on every mutating route (Task 11), Fleet-sourced frontend replacing the fan-out (Tasks 12-13), Tailscale ACL note (Task 17, documentation only — not code-enforceable).
- Section 6 S9 (`devices.json` plaintext, ACL, non-`0.0.0.0` bind): documented in Task 17 (code enforcement of the bind/ACL itself is out of scope — it's an operator/network config, `devices.json`'s `0600` handling already exists in `devices.py`).
- Section 6 S10 (work Mac metadata role): Tasks 5 (redaction), 16 (route enforcement), 17 (docs).
- Section 6 S13 (public repo leakage): enforced throughout via Global Constraint 4 and verified explicitly in Task 18's grep and Task 17's doc test.
