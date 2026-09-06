# RC Launcher v3 Phase 2a: External Session Adoption and Mobile Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the launcher UI drive a Claude Code session that was started outside the launcher (Terminal.app-adjacent workflows aside — specifically one running inside a tmux pane the launcher didn't create), by mapping its pid to the tmux pane it lives in, exposing that pane to Preview/terminal/keys the same way a launcher (`rc-*`) session is, and offering a one-click "enable Remote Control" action for it. Also fixes two small display bugs (hub overview card missing version lines; a matrix cell — verified already fixed, see Task 1) and hides a redundant session list on mobile.

**Architecture:** Additive, all inside the existing single-process stdlib Python backend plus the existing React/TypeScript frontend. A new `panes.py` module gives a pure, injectable-`run`/`read_ppid` way to list live tmux panes and map a pid to the pane it (or an ancestor of it) runs in, by walking `/proc/<pid>/status`'s `PPid` (falling back to `ps -o ppid=` for platforms without `/proc`, i.e. macOS) until a pid matches a pane's `pane_pid`. `sessions.list_rc_sessions()` uses this to attach a `tmux` mapping and, when available, an `rc_url` to each external row. `server.py` gains a per-request adoption allowlist so `/preview`, `/ws`, `/keys`, `/resize` can address an adopted external tmux session by name (still never an arbitrary non-`rc-*` name), plus a new `POST /sessions/<name>/enable-rc` that types `/remote-control` into an adopted pane and polls for the resulting URL. The frontend lets external rows with a `tmux` mapping open Preview/terminal exactly like a launcher row, and adds an "Enable RC" / "Open on claude.ai" control.

**Tech Stack:** Python 3.9+ stdlib (`subprocess`, `time`, `re`), tmux, React 18 + TypeScript + Vite (frontend, prebuilt to `static/dist` and committed).

**Spec:** External planning note from the 2026-09-06 planning session (not tracked in this repo — a private document, per Global Constraint 3 below). This plan is self-contained: every requirement it implements is captured in the Global Constraints and the 10 tasks below. Facts verified on this box, used throughout: `tmux list-panes -a -F '#{session_name} #{pane_id} #{pane_pid} #{pane_current_command}'` reports the pane's *shell* pid; the `claude` process is a descendant of that shell pid (observed: pane_pid 1483270 → claude pid 1483272), so mapping a claude pid to a pane means walking parent pids (`/proc/<pid>/status`'s `PPid` line on Linux, `ps -o ppid= -p <pid>` on macOS) until one equals a `pane_pid`. Transcript `bridge-session` records carry a `bridgeSessionId: cse_...` that is *not* the claude.ai URL id (`session_...`) — the only reliable URL source remains the OSC 8 link in the pane, via `sessions.get_url_with_source`. Non-tmux sessions (Terminal.app, VS Code integrated terminal, etc.) cannot be adopted or previewed — they keep today's read-only external row with no `tmux`/`rc_url`.

## Global Constraints

- Python 3.9+ stdlib only at runtime. No new pip dependencies. Use `Optional[X]` (`from typing import Optional`), never a bare runtime `X | None` union.
- All subprocess calls stay argv lists. Never `shell=True`. Every tmux/ps call gets a `timeout=`.
- No personal hostnames, tailnet names, IPs, or `/root/...` paths anywhere in tracked files. Use `~/.claude-rc`, `/home/alice`, `example.com`. CI enforces this: `.github/workflows/ci.yml:45` greps the diff for `barjazz|tail82c219|tbarjadze|hetzner|tba-lin|100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|/root/`.
- Existing tests keep passing: baseline is `python3 -m unittest discover tests` → `Ran 276 tests ... OK` (recorded 2026-09-06 on branch `v3-phase2a` at the tip of this worktree, `/var/www/rc-launcher-p2`, from `main` at `e2dc422`, v2.1.5 shipped).
- Each task ends with one commit on `v3-phase2a`.
- Adoption must never let a request address a tmux session outside the current adoption allowlist (an `rc-*` launcher session, or a non-`rc-*` session that `list_rc_sessions()` currently reports as `tmux`-mapped to a live external row). `POST .../enable-rc` must never type anything into a pane except the literal string `/remote-control` followed by Enter.
- Nothing in this plan restarts services, touches `~/.claude-rc` on this box, `/var/www/rc-launcher`, or pushes to a remote. All work happens inside this worktree (`/var/www/rc-launcher-p2`) only.
- Frontend build check before the final commit: `cd frontend && npm ci && npx tsc --noEmit && npm run build`, then commit `static/dist` alone in that task.

---

### Task 1: Residual fix — hub overview card missing version lines

**Files:**
- Modify: `server.py:1064-1069` (`elif path == "/overview":`)
- Test: `tests/test_server_helpers.py` (new `OverviewLocalCardTest`)

**Context:** `overview.card_from_parts()` (`overview.py:8-39`) reads `stats.get("claude_version")` and `stats.get("version")` to fill `DeviceCard.claude_version`/`.version` — the fields `BigCard.tsx` uses to render the "Launcher vX / Claude Code Y" lines. The `GET /stats` route (`server.py:1048-1059`) adds those two keys on top of `stats.system_stats()`. The `GET /overview` route (`server.py:1064-1069`) builds its **local** card's stats directly from `stats.system_stats()` (plus `token_history`) and never adds `version`/`claude_version`, so the hub's own card is always missing those two fields — remote cards don't have this bug because `overview.fetch_remote_card()` fetches the *full* `/rc/stats` response from each device. `git log` on `frontend/src/components/ConfigMatrix.tsx` and the current source (`ConfigMatrix.tsx:142`, `cellStyle(has('no hooks'))`, same call shape as every other skewed cell e.g. line 132 `cellStyle(has('dirty'))`) show the "no hooks" cell is **already** tinted red like the others — verified by reading the file, not reproduced. No change needed there; do not add a task for it.

**Interfaces:**
- Consumes: `stats.system_stats()`, `compat.get_caps()` (both already imported at top of `server.py`).
- Produces: no new interface — `local_stats` dict passed into `overview.build_overview()` now carries `version`/`claude_version` like a remote device's `/rc/stats` response does.

- [ ] **Step 1: Write the failing test**

```python
class OverviewLocalCardTest(unittest.TestCase):
    def test_local_card_carries_version_and_claude_version(self):
        """GET /overview's local card must include this device's own
        launcher version and claude_version, same as a remote card does
        (via fetch_remote_card -> full /rc/stats) — regression test for the
        bug where local_stats was built from bare stats.system_stats()
        without VERSION/claude_version added."""
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        overview_block = src.split('elif path == "/overview":', 1)[1].split('elif path ==', 1)[0]
        self.assertIn('local_stats["version"] = VERSION', overview_block)
        self.assertIn('local_stats["claude_version"]', overview_block)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_server_helpers.OverviewLocalCardTest -v`
Expected: FAIL — `AssertionError: 'local_stats["version"] = VERSION' not found in ...`

- [ ] **Step 3: Fix the route**

In `server.py`, replace the `/overview` block:

```python
        elif path == "/overview":
            local_sess = list_rc_sessions()
            local_stats = {**stats.system_stats(), "token_history": stats.token_history()}
            local_card = {"id": "local", "name": get_local_name(), "base_url": ""}
            cards = overview.build_overview(local_card, local_sess, local_stats, load_devices())
            self._json({"devices": cards})
```

with:

```python
        elif path == "/overview":
            local_sess = list_rc_sessions()
            local_stats = {**stats.system_stats(), "token_history": stats.token_history()}
            local_stats["version"] = VERSION
            local_stats["claude_version"] = compat.get_caps().get("version")
            local_card = {"id": "local", "name": get_local_name(), "base_url": ""}
            cards = overview.build_overview(local_card, local_sess, local_stats, load_devices())
            self._json({"devices": cards})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_server_helpers.OverviewLocalCardTest -v`
Expected: OK

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests` → expect `Ran 277 tests ... OK`

```bash
git add server.py tests/test_server_helpers.py
git commit -m "fix(overview): local card includes launcher/claude version like remote cards

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 2: `panes.py` — tmux pane inventory and claude-pid-to-pane mapping

**Files:**
- Create: `panes.py`
- Test: `tests/test_panes.py`

**Interfaces:**
- Produces:
  - `panes.list_panes(run=subprocess.run, now_fn=time.time) -> list[dict]`, each dict `{"session_name": str, "pane_id": str, "pane_pid": int, "window_index": str}`. Cached 15s (module-level, same shape as `agents.py`'s cache but single-threaded — no concurrency contract needed here, this is called from request-handling threads that already tolerate a 15s-stale view).
  - `panes.pane_for_pid(pid, panes=None, read_ppid=<default>, run=subprocess.run) -> Optional[dict]` — same dict shape as one `list_panes()` entry, or `None` if `pid` (or none of its ancestors, up to `panes.MAX_WALK` hops) matches any pane's `pane_pid`.
  - `panes.CACHE_TTL_SECONDS = 15`, `panes.MAX_WALK = 25` (module constants later tasks may reference).

- [ ] **Step 1: Write the failing tests**

```python
"""panes.py: tmux pane inventory + claude-pid-to-pane mapping via a
parent-pid walk, for adopting external (non-launcher) Claude Code
sessions into Preview/terminal/keys."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import panes


class FakeRun:
    def __init__(self, stdout="", returncode=0, raises=None):
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


LIST_PANES_OUT = (
    "rc-portugal\t%3\t1001\t0\n"
    "mysession\t%7\t2001\t0\n"
)


class ListPanesTest(unittest.TestCase):
    def setUp(self):
        panes._cache = {"panes": [], "at": 0.0}

    def test_parses_tab_separated_rows(self):
        fake = FakeRun(stdout=LIST_PANES_OUT)
        rows = panes.list_panes(run=fake, now_fn=lambda: 1000.0)
        self.assertEqual(rows, [
            {"session_name": "rc-portugal", "pane_id": "%3", "pane_pid": 1001, "window_index": "0"},
            {"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"},
        ])
        self.assertEqual(fake.calls, [
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{window_index}"],
        ])

    def test_skips_malformed_lines(self):
        fake = FakeRun(stdout="rc-portugal\t%3\tnotapid\t0\nonly\tthreefields\there\n")
        rows = panes.list_panes(run=fake, now_fn=lambda: 1000.0)
        self.assertEqual(rows, [])

    def test_nonzero_exit_returns_stale_cache(self):
        panes._cache = {"panes": [{"session_name": "old", "pane_id": "%1",
                                    "pane_pid": 1, "window_index": "0"}], "at": 990.0}
        fake = FakeRun(returncode=1)
        rows = panes.list_panes(run=fake, now_fn=lambda: 991.0)
        self.assertEqual(rows[0]["session_name"], "old")

    def test_missing_tmux_binary_returns_empty_or_stale(self):
        fake = FakeRun(raises=OSError("no tmux"))
        rows = panes.list_panes(run=fake, now_fn=lambda: 1000.0)
        self.assertEqual(rows, [])

    def test_caches_for_15_seconds(self):
        fake = FakeRun(stdout=LIST_PANES_OUT)
        clock = {"t": 1000.0}
        panes.list_panes(run=fake, now_fn=lambda: clock["t"])
        clock["t"] = 1010.0
        panes.list_panes(run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 1)
        clock["t"] = 1016.0
        panes.list_panes(run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 2)


class PaneForPidTest(unittest.TestCase):
    def setUp(self):
        panes._cache = {"panes": [], "at": 0.0}

    def test_direct_match_on_pane_pid(self):
        rows = [{"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}]
        result = panes.pane_for_pid(2001, panes=rows)
        self.assertEqual(result["session_name"], "mysession")

    def test_walks_parent_chain_to_find_pane_pid(self):
        rows = [{"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}]
        # claude pid 2003's parent is 2001 (the pane's shell) — one hop.
        ppids = {2003: 2001}
        result = panes.pane_for_pid(2003, panes=rows, read_ppid=lambda pid, run=None: ppids.get(pid))
        self.assertEqual(result["pane_id"], "%7")

    def test_no_match_returns_none(self):
        rows = [{"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}]
        result = panes.pane_for_pid(9999, panes=rows, read_ppid=lambda pid, run=None: None)
        self.assertIsNone(result)

    def test_stops_at_max_walk_instead_of_looping_forever(self):
        rows = []
        calls = {"n": 0}

        def read_ppid(pid, run=None):
            calls["n"] += 1
            return pid + 1  # never matches, never terminates on its own

        result = panes.pane_for_pid(1, panes=rows, read_ppid=read_ppid)
        self.assertIsNone(result)
        self.assertLessEqual(calls["n"], panes.MAX_WALK)

    def test_default_read_ppid_parses_proc_status(self):
        # Exercise the real default (Linux /proc path) against this test
        # process's own pid, which always has a readable PPid.
        ppid = panes._default_read_ppid(os.getpid())
        self.assertIsInstance(ppid, int)

    def test_default_read_ppid_falls_back_to_ps_when_no_proc(self):
        fake = FakeRun(stdout="4242\n")
        # Force the /proc path to fail by asking about a pid that can't
        # possibly exist, so only the `ps` fallback can produce a result.
        result = panes._default_read_ppid(2**30, run=fake)
        self.assertEqual(result, 4242)
        self.assertEqual(fake.calls, [["ps", "-o", "ppid=", "-p", str(2**30)]])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_panes -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'panes'`

- [ ] **Step 3: Write `panes.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_panes -v`
Expected: OK (12 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests` → expect `Ran 289 tests ... OK`

```bash
git add panes.py tests/test_panes.py
git commit -m "feat(panes): tmux pane inventory + claude-pid-to-pane mapping

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 3: `sessions.list_rc_sessions` — adopt external rows with a `tmux` mapping and `rc_url`

**Files:**
- Modify: `sessions.py:15` (imports), `sessions.py:207-222` (external-row loop)
- Test: `tests/test_sessions_adoption.py`

**Interfaces:**
- Consumes: `panes.pane_for_pid(pid) -> Optional[dict]` (Task 2), `sessions.get_url_with_source(session_name) -> (Optional[str], Optional[str])` (existing, `sessions.py:340`), `config.SESSION_PREFIX`.
- Produces: each external row from `list_rc_sessions()` gains two always-present keys:
  - `"tmux"`: `{"session_name": str, "pane_id": str} | None` — non-null only when the row's `pid` maps (via `panes.pane_for_pid`) to a pane whose `session_name` does **not** start with `SESSION_PREFIX` (an `rc-*` session is a launcher session, not an adoption target — it's already reachable by its own launcher row).
  - `"rc_url"`: `str | None` — set only when `get_url_with_source(tmux_session_name)` returns `source == "osc8"`. Never persisted anywhere beyond this response (no env var write beyond what `get_url_with_source` itself already does for its own bookkeeping).

- [ ] **Step 1: Write the failing tests**

```python
"""sessions.list_rc_sessions: external-row adoption via panes.pane_for_pid
and get_url_with_source — a claude pid that maps to a non-rc-* tmux pane
gets tmux+rc_url populated so the frontend can open Preview/terminal on it."""
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import sessions


EXTERNAL_ROW = {
    "session_id": "abc-123", "name": "portugal", "cwd": "/home/user/proj",
    "kind": "interactive", "status": "idle", "started_at": 1757100000000,
    "pid": 2003, "waiting_for": None, "state": None,
}


class ListRcSessionsAdoptionTest(unittest.TestCase):
    def _run_with(self, tmux_list_sessions_out, claude_rows, pane_for_pid_result,
                  url_with_source_result=(None, None)):
        run_result = mock.Mock(returncode=0, stdout=tmux_list_sessions_out)
        with mock.patch("sessions.subprocess.run", return_value=run_result), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=claude_rows), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane_for_pid_result) as pfp, \
             mock.patch("sessions.get_url_with_source", return_value=url_with_source_result) as guws:
            rows = sessions.list_rc_sessions()
        return rows, pfp, guws

    def test_adopts_row_whose_pid_maps_to_a_non_rc_pane(self):
        rows, pfp, guws = self._run_with(
            "", [EXTERNAL_ROW],
            pane_for_pid_result={"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"},
            url_with_source_result=("https://claude.ai/code/session_xyz", "osc8"),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tmux"], {"session_name": "mysession", "pane_id": "%7"})
        self.assertEqual(rows[0]["rc_url"], "https://claude.ai/code/session_xyz")
        pfp.assert_called_once_with(2003)
        guws.assert_called_once_with("mysession")

    def test_does_not_adopt_when_pane_belongs_to_an_rc_launcher_session(self):
        rows, _pfp, guws = self._run_with(
            "", [EXTERNAL_ROW],
            pane_for_pid_result={"session_name": "rc-portugal", "pane_id": "%3", "pane_pid": 1001, "window_index": "0"},
        )
        self.assertIsNone(rows[0]["tmux"])
        self.assertIsNone(rows[0]["rc_url"])
        guws.assert_not_called()

    def test_no_pane_match_leaves_tmux_and_rc_url_none(self):
        rows, _pfp, guws = self._run_with("", [EXTERNAL_ROW], pane_for_pid_result=None)
        self.assertIsNone(rows[0]["tmux"])
        self.assertIsNone(rows[0]["rc_url"])
        guws.assert_not_called()

    def test_adopted_but_no_osc8_url_yet_leaves_rc_url_none(self):
        rows, _pfp, guws = self._run_with(
            "", [EXTERNAL_ROW],
            pane_for_pid_result={"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"},
            url_with_source_result=(None, None),
        )
        self.assertEqual(rows[0]["tmux"], {"session_name": "mysession", "pane_id": "%7"})
        self.assertIsNone(rows[0]["rc_url"])

    def test_row_with_no_pid_skips_pane_lookup_entirely(self):
        row = dict(EXTERNAL_ROW, pid=None)
        rows, pfp, guws = self._run_with("", [row], pane_for_pid_result=None)
        self.assertIsNone(rows[0]["tmux"])
        pfp.assert_not_called()
        guws.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_sessions_adoption -v`
Expected: FAIL — `AttributeError: module 'sessions' has no attribute 'panes'` (or `KeyError: 'tmux'`)

- [ ] **Step 3: Wire up adoption in `sessions.py`**

Add the import (`sessions.py:15`, alongside the existing `agents` import — check the current import block first with `grep -n "^import agents" sessions.py`; add `import panes` next to it):

```python
import agents
import panes
```

Replace the external-row loop (`sessions.py:207-222`):

```python
    for row in claude_rows_by_id.values():
        if row["session_id"] in known_session_ids:
            continue
        sessions.append({
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
            "claude": {"state": row.get("state")},
        })
```

with:

```python
    for row in claude_rows_by_id.values():
        if row["session_id"] in known_session_ids:
            continue
        entry = {
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
            "claude": {"state": row.get("state")},
            "tmux": None,
            "rc_url": None,
        }
        pid = row.get("pid")
        if pid:
            pane = panes.pane_for_pid(pid)
            if pane and not pane["session_name"].startswith(SESSION_PREFIX):
                entry["tmux"] = {"session_name": pane["session_name"], "pane_id": pane["pane_id"]}
                url, source = get_url_with_source(pane["session_name"])
                if source == "osc8":
                    entry["rc_url"] = url
        sessions.append(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_sessions_adoption -v`
Expected: OK (5 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests` → expect `Ran 294 tests ... OK`

```bash
git add sessions.py tests/test_sessions_adoption.py
git commit -m "feat(sessions): adopt external rows into a tmux pane via panes.pane_for_pid

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 4: `server.py` — per-request adoption allowlist for `/preview`, `/ws`, `/keys`, `/resize`

**Files:**
- Modify: `server.py:438-442` (near `_valid_session_name`), plus the four guard sites at `server.py:906-912` (`/ws`), `server.py:938-940` (`/preview`), `server.py:1359-1363` (`/resize`), `server.py:1382-1385` (`/keys`)
- Test: `tests/test_server_helpers.py` (new `SessionNameAllowedTest`, plus route-source assertions)

**Interfaces:**
- Consumes: `sessions.list_rc_sessions()` (existing).
- Produces:
  - `server._adopted_tmux_names(rows=None) -> set[str]` — tmux session names of every row `list_rc_sessions()` currently reports as external+adopted (`row.get("external")` truthy and `row.get("tmux")` truthy); `rows` is injectable for tests, defaults to a fresh `list_rc_sessions()` call.
  - `server._session_name_allowed(name, adopted=None) -> bool` — `True` when `_valid_session_name(name)` is `True` (unchanged `rc-*` guard), OR when `name` is in `adopted` (computed via `_adopted_tmux_names()` if not passed). This is the **only** new way a non-`rc-*` name can pass — arbitrary strings are still rejected.
  - The four route guards switch from `if not _valid_session_name(name):` to `if not _session_name_allowed(name):`, unchanged response shape (`400 {"ok": False, "message": "Invalid session name"}`).

- [ ] **Step 1: Write the failing tests**

```python
class SessionNameAllowedTest(unittest.TestCase):
    def test_rc_prefixed_name_allowed_without_consulting_adoption(self):
        # Passing an empty adopted set proves the rc-* branch never needs it.
        self.assertTrue(server._session_name_allowed("rc-portugal", adopted=set()))

    def test_adopted_non_rc_name_allowed(self):
        self.assertTrue(server._session_name_allowed("mysession", adopted={"mysession"}))

    def test_non_adopted_arbitrary_name_rejected(self):
        self.assertFalse(server._session_name_allowed("mysession", adopted=set()))

    def test_path_traversal_rejected_even_if_somehow_in_adopted(self):
        self.assertFalse(server._session_name_allowed("../etc/passwd", adopted={"../etc/passwd"}))

    def test_lazily_computes_adoption_set_when_not_passed(self):
        fake_rows = [{"external": True, "tmux": {"session_name": "mysession", "pane_id": "%7"}},
                     {"external": True, "tmux": None},
                     {"external": False, "tmux": None}]
        orig = server.list_rc_sessions
        server.list_rc_sessions = lambda: fake_rows
        try:
            self.assertTrue(server._session_name_allowed("mysession"))
            self.assertFalse(server._session_name_allowed("not-adopted"))
        finally:
            server.list_rc_sessions = orig


class AdoptedTmuxNamesTest(unittest.TestCase):
    def test_collects_only_external_adopted_rows(self):
        rows = [
            {"external": True, "tmux": {"session_name": "a", "pane_id": "%1"}},
            {"external": True, "tmux": None},
            {"external": False, "tmux": {"session_name": "rc-x", "pane_id": "%2"}},
        ]
        self.assertEqual(server._adopted_tmux_names(rows), {"a"})


class RouteAdoptionGuardTest(unittest.TestCase):
    """Each of /preview, /ws, /keys, /resize must guard on
    _session_name_allowed, not the old rc-*-only _valid_session_name, so an
    adopted external session's tmux name can pass."""
    def test_ws_route_uses_session_name_allowed(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split("endswith(\"/ws\"):", 1)[1][:400]
        self.assertIn("_session_name_allowed(name)", block)

    def test_preview_route_uses_session_name_allowed(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split("endswith(\"/preview\"):", 1)[1][:400]
        self.assertIn("_session_name_allowed(name)", block)

    def test_resize_route_uses_session_name_allowed(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/resize"):', 1)[1][:400]
        self.assertIn("_session_name_allowed(name)", block)

    def test_keys_route_uses_session_name_allowed(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/keys"):', 1)[1][:400]
        self.assertIn("_session_name_allowed(name)", block)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.SessionNameAllowedTest tests.test_server_helpers.AdoptedTmuxNamesTest tests.test_server_helpers.RouteAdoptionGuardTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_session_name_allowed'`

- [ ] **Step 3: Add the helpers and update the four guards**

Add near `_valid_session_name` (`server.py:438-442`):

```python
def _valid_session_name(name):
    """True if `name` is safe to use as a tmux session identifier: non-empty,
    no path traversal or separators, and carries our 'rc-' prefix so a
    logged-in browser can only reach sessions the launcher itself created."""
    return bool(name) and ".." not in name and "/" not in name and name.startswith(SESSION_PREFIX)


def _adopted_tmux_names(rows=None):
    """Tmux session names of every currently-adopted external session —
    an external row from list_rc_sessions() whose "tmux" mapping is not
    None. This is the ONLY set of non-rc-* names /preview, /ws, /keys,
    and /resize may ever address; it is recomputed per request (never
    cached) so a session that stops being adopted (process exits, pane
    closes) can't be reached a moment later on a stale allowlist."""
    if rows is None:
        rows = list_rc_sessions()
    return {s["tmux"]["session_name"] for s in rows if s.get("external") and s.get("tmux")}


def _session_name_allowed(name, adopted=None):
    """True if `name` is safe to address for /preview, /ws, /keys, /resize:
    either an rc-* launcher session (_valid_session_name, unchanged), or a
    tmux session name currently in the adoption allowlist. `adopted` is
    injectable for tests; production callers leave it unset and it is
    computed lazily (only when the rc-* check fails) via
    _adopted_tmux_names()."""
    if _valid_session_name(name):
        return True
    if adopted is None:
        adopted = _adopted_tmux_names()
    return bool(name) and ".." not in name and "/" not in name and name in adopted
```

Update the four guard sites — each changes only its `if not _valid_session_name(name):` line to `if not _session_name_allowed(name):`:

`server.py` `/ws` (around line 910):
```python
            if not _session_name_allowed(name):
```

`/preview` (around line 939):
```python
            if not _session_name_allowed(name):
```

`/resize` (around line 1361):
```python
            if not _session_name_allowed(name):
```

`/keys` (around line 1383):
```python
            if not _session_name_allowed(name):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers -v`
Expected: OK

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests` → expect `Ran 301 tests ... OK`

```bash
git add server.py tests/test_server_helpers.py
git commit -m "feat(server): allow /preview,/ws,/keys,/resize to address adopted external sessions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 5: `POST /sessions/<name>/enable-rc`

**Files:**
- Modify: `server.py` (new helper near `_stop_external_pid`, `server.py:652`; new route in `do_POST` near the `/keys` block, `server.py:1381-1401`; import `get_url_with_source`)
- Test: `tests/test_server_helpers.py` (new `EnableRcTest`)

**Interfaces:**
- Consumes: `server._adopted_tmux_names()` (Task 4), `sessions.get_url_with_source` (now imported into `server.py`).
- Produces: `server._enable_rc_for_adopted(name, run=subprocess.run, sleep=time.sleep, now_fn=time.time) -> dict` — `{"ok": True, "url": str}` on success, `{"ok": False, "message": str}` on failure (send failed, or no `osc8` URL within `ENABLE_RC_POLL_SECONDS`). `server.ENABLE_RC_POLL_SECONDS = 20`, `server.ENABLE_RC_POLL_INTERVAL = 0.5`. Route: `POST /sessions/<name>/enable-rc` → `400` if `name` is not in the current adoption allowlist (never for an `rc-*` launcher session — those already have RC from launch), else `200`/`502` with the dict above as the JSON body.

- [ ] **Step 1: Write the failing tests**

```python
class EnableRcTest(unittest.TestCase):
    def test_sends_remote_control_then_enter(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("server.get_url_with_source", return_value=("https://claude.ai/code/session_x", "osc8")):
            result = server._enable_rc_for_adopted("mysession", run=fake_run, sleep=lambda s: None)
        self.assertEqual(result, {"ok": True, "url": "https://claude.ai/code/session_x"})
        self.assertEqual(calls[0], ["tmux", "send-keys", "-t", "mysession", "-l", "/remote-control"])
        self.assertEqual(calls[1], ["tmux", "send-keys", "-t", "mysession", "Enter"])

    def test_send_keys_failure_reports_message_without_polling(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr="no such session")
        with mock.patch("server.get_url_with_source") as guws:
            result = server._enable_rc_for_adopted("mysession", run=fake_run, sleep=lambda s: None)
        self.assertFalse(result["ok"])
        self.assertIn("message", result)
        guws.assert_not_called()

    def test_polls_until_osc8_url_appears(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")
        responses = [(None, None), (None, "text"), ("https://claude.ai/code/session_y", "osc8")]
        with mock.patch("server.get_url_with_source", side_effect=responses):
            result = server._enable_rc_for_adopted("mysession", run=fake_run, sleep=lambda s: None)
        self.assertEqual(result, {"ok": True, "url": "https://claude.ai/code/session_y"})

    def test_times_out_after_20_seconds_of_polling(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")
        clock = {"t": 0.0}

        def now_fn():
            return clock["t"]

        def sleep(s):
            clock["t"] += s

        with mock.patch("server.get_url_with_source", return_value=(None, None)):
            result = server._enable_rc_for_adopted("mysession", run=fake_run, sleep=sleep, now_fn=now_fn)
        self.assertFalse(result["ok"])
        self.assertIn("20", result["message"])

    def test_route_rejects_non_adopted_name(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        self.assertIn('endswith("/enable-rc")', src)
        block = src.split('endswith("/enable-rc")', 1)[1][:600]
        self.assertIn("_adopted_tmux_names()", block)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_server_helpers.EnableRcTest -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_enable_rc_for_adopted'`

- [ ] **Step 3: Import `get_url_with_source` and add the helper + route**

Extend the `sessions` import block at the top of `server.py` (currently `server.py:29-33`):

```python
from sessions import (
    list_rc_sessions, session_exists, setup_session, stop_session,
    restart_session, list_resumable_sessions, resume_session,
    get_all_session_errors, unstick_session, get_transcript,
    build_tmux_command, count_launcher_sessions, get_url_with_source,
)
```

Add near `_stop_external_pid` (`server.py:652`):

```python
ENABLE_RC_POLL_SECONDS = 20
ENABLE_RC_POLL_INTERVAL = 0.5


def _enable_rc_for_adopted(name, run=subprocess.run, sleep=time.sleep, now_fn=time.time):
    """POST /sessions/<name>/enable-rc backing logic for an already-adopted
    external tmux session (`name` is the tmux session name, validated by
    the caller against _adopted_tmux_names() before this is ever reached):
    type '/remote-control' into the pane, press Enter, then poll
    get_url_with_source(name) for up to ENABLE_RC_POLL_SECONDS for an
    'osc8' URL (the only trustworthy signal RC actually activated).
    Never sends anything but that literal command string."""
    r = run(["tmux", "send-keys", "-t", name, "-l", "/remote-control"],
            capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return {"ok": False, "message": "Could not send /remote-control to the pane"}
    r2 = run(["tmux", "send-keys", "-t", name, "Enter"],
             capture_output=True, text=True, timeout=5)
    if r2.returncode != 0:
        return {"ok": False, "message": "Could not send Enter to the pane"}
    deadline = now_fn() + ENABLE_RC_POLL_SECONDS
    while now_fn() < deadline:
        url, source = get_url_with_source(name)
        if source == "osc8" and url:
            return {"ok": True, "url": url}
        sleep(ENABLE_RC_POLL_INTERVAL)
    return {"ok": False, "message": "Remote Control did not activate within 20s"}
```

Add the route in `do_POST`, next to the `/keys` block (`server.py:1381-1401`) — insert right after that `elif` block ends:

```python
        elif path.startswith("/sessions/") and path.endswith("/enable-rc"):
            name = path[len("/sessions/"):-len("/enable-rc")]
            if name not in _adopted_tmux_names():
                self._json({"ok": False, "message": "Not an adopted external session"}, 400)
                return
            result = _enable_rc_for_adopted(name)
            self._json(result, 200 if result.get("ok") else 502)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_server_helpers.EnableRcTest -v`
Expected: OK (5 tests)

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m unittest discover tests` → expect `Ran 306 tests ... OK`

```bash
git add server.py tests/test_server_helpers.py
git commit -m "feat(server): POST /sessions/<name>/enable-rc for adopted external sessions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 6: Frontend types + API client — `tmux`, `rc_url`, `enableRc`

**Files:**
- Modify: `frontend/src/types.ts:13-22` (`Session` interface)
- Modify: `frontend/src/api.ts:59-100` (`api` object)

**Interfaces:**
- Produces: `Session.tmux?: { session_name: string; pane_id: string } | null`, `Session.rc_url?: string | null`, `Session.cwd?: string` (external rows already carry `cwd` server-side per `sessions.py:218` but the type never declared it — add it now since Task 7/8 need to show it). `api.enableRc(device: string, name: string): Promise<{ ok: boolean; url?: string; message?: string }>`.

This task has no Python/pytest cycle — it is a pure type/client addition consumed by Tasks 7-8. Verify it compiles as part of Task 7's `tsc --noEmit` (run once at the end of Task 9 for the whole frontend); there is no standalone frontend test runner configured in this repo (confirmed: no `test` script beyond typecheck+build in `frontend/package.json`).

- [ ] **Step 1: Edit `types.ts`**

Replace the `Session` interface (`frontend/src/types.ts:13-22`):

```typescript
export interface Session {
  name: string; mode: string; url?: string; status?: string;
  tokens?: number; workdir?: string; sessionId?: string; pct?: number;
  state?: 'starting' | 'busy' | 'idle' | 'needs_attention' | 'ended';
  kind?: 'external';
  external?: boolean;
  session_id?: string;
  pid?: number;
  waiting_for?: string | null;
  /** Working directory of an external session (sessions.py's "cwd"). */
  cwd?: string;
  /** Tmux pane this external session was adopted into, or null/absent if
   * it isn't running in a tmux pane the launcher can find (e.g.
   * Terminal.app, VS Code's integrated terminal) — no Preview/terminal
   * access is possible without this. */
  tmux?: { session_name: string; pane_id: string } | null;
  /** Remote Control URL for an adopted external session, from an OSC 8
   * hyperlink target in its pane (get_url_with_source's "osc8" source
   * only) — null until Remote Control has been enabled and shown a link. */
  rc_url?: string | null;
}
```

- [ ] **Step 2: Edit `api.ts`**

Add `enableRc` to the `api` object (`frontend/src/api.ts:59-100`), next to `sendKeys`/`resize`:

```typescript
  enableRc: (device: string, name: string): Promise<{ ok: boolean; url?: string; message?: string }> =>
    req('POST', `/sessions/${encodeURIComponent(name)}/enable-rc`, device),
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(frontend): Session.tmux/rc_url types and api.enableRc client

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 7: `SessionRow.tsx` — Preview/terminal + Enable RC for adopted external rows

**Files:**
- Modify: `frontend/src/components/SessionRow.tsx`

**Interfaces:**
- Consumes: `Session.tmux`, `Session.rc_url` (Task 6), `api.enableRc` (Task 6).
- Produces: no new exported interface — `SessionRow` behavior change only. `onPreview(name: string)` (existing prop) is now called with `s.tmux.session_name` for an adopted external row instead of `s.name`, since the backend addresses the tmux pane by its real tmux session name, which may differ from the display `name` `claude agents --json` reports.

This is a pure frontend component task — no Python test cycle. Verified by the frontend build (`tsc --noEmit` + `npm run build`) run once at the end of Task 9, plus manual reasoning below since this repo has no component test runner.

- [ ] **Step 1: Add an `isAdopted`/preview-target helper and wire the click targets**

In `frontend/src/components/SessionRow.tsx`, after the existing `const isExternal = s.kind === 'external';` (line 110), add:

```typescript
  // An external row can only open a terminal (Preview/keys/resize) when
  // sessions.list_rc_sessions() found a tmux pane for it — Terminal.app,
  // VS Code's integrated terminal, etc. have no such pane and stay
  // read-only. previewTarget is what api.preview/ws/keys/resize must
  // address: the launcher's own name for a launcher row, or the adopted
  // tmux session's real name for an external row (never s.name, which is
  // just claude agents --json's display name and may differ).
  const isAdopted = isExternal && !!s.tmux;
  const canOpenTerminal = !isExternal || isAdopted;
  const previewTarget = isAdopted ? s.tmux!.session_name : s.name;

  const [enablingRc, setEnablingRc] = useState(false);
  const [rcUrl, setRcUrl] = useState<string | null | undefined>(s.rc_url);

  const handleEnableRc = async () => {
    if (!isAdopted) return;
    setEnablingRc(true);
    try {
      const result = await api.enableRc(deviceId, s.tmux!.session_name);
      if (result.ok && result.url) setRcUrl(result.url);
    } catch { /* ignore */ }
    finally { setEnablingRc(false); }
  };

  const handleOpenRcUrl = () => {
    if (rcUrl) window.open(rcUrl, '_blank');
  };
```

Update the outer `div`'s click handler and `title` (was `onClick={isExternal ? undefined : () => onPreview(s.name)}` / `title={isExternal ? undefined : 'Open terminal'}`, around line 141-144):

```typescript
      onClick={canOpenTerminal ? () => onPreview(previewTarget) : undefined}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = RT.borderHi; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = RT.border; }}
      title={canOpenTerminal ? 'Open terminal' : undefined}
```

and its `cursor` (part of the same `style` object, line ~151):

```typescript
        cursor: canOpenTerminal ? 'pointer' : 'default', transition: 'border-color .12s',
```

- [ ] **Step 2: Show Preview access for an adopted external row**

The actions column currently wraps Restart + the ⋯ menu (which contains "Preview") in `{!isExternal && ( ... )}` (lines 204-279). Split that so an adopted external row still gets a minimal ⋯ menu with just "Preview" (no Restart/Unstick — those don't apply to a session the launcher didn't start), plus the new RC controls. Replace the block from `{!isExternal && (` (line 204) through its matching `)}` before the closing `</div>` (line 280) with:

```typescript
        {!isExternal && (
          <V5IconButton
            label="Restart session"
            accent={RT.green}
            mobile={mobile}
            pending={pending}
            onClick={handleRefresh}
          >
            <Icons.refresh size={14} />
          </V5IconButton>
        )}
        <V5IconButton
          label="Stop session"
          accent={RT.red}
          mobile={mobile}
          pending={pending}
          onClick={handleStop}
        >
          <Icons.stop size={12} />
        </V5IconButton>

        {isAdopted && (
          rcUrl ? (
            <V5IconButton
              label="Open on claude.ai"
              mobile={mobile}
              pending={false}
              onClick={handleOpenRcUrl}
            >
              <Icons.link size={13} />
            </V5IconButton>
          ) : (
            <V5IconButton
              label="Enable Remote Control"
              accent={RT.amber}
              mobile={mobile}
              pending={enablingRc}
              onClick={handleEnableRc}
            >
              <Icons.refresh size={13} />
            </V5IconButton>
          )
        )}

        {/* ⋯ more menu — secondary actions (preview/keys/terminal access).
            A launcher row gets the full menu; an adopted external row gets
            just Preview (no session ID/URL/unstick — those are launcher
            concepts). A non-adopted external row (no tmux pane found) gets
            no menu at all — nothing in it would work. */}
        {(!isExternal || isAdopted) && (
        <div ref={menuRef} style={{ position: 'relative' }}>
          <V5IconButton
            label="More options"
            mobile={mobile}
            pending={pending}
            onClick={() => {
              if (!menuOpen && menuRef.current) setMenuPos(fixedMenuPos(menuRef.current));
              setMenuOpen((o) => !o);
            }}
          >
            <Icons.more size={14} stroke={RT.textDim} />
          </V5IconButton>

          {menuOpen && (
            <div style={{
              ...(menuPos ?? {}),
              background: RT.panel, border: `1px solid ${RT.borderHi}`,
              borderRadius: 8, padding: 4, zIndex: Z.menu,
              boxShadow: '0 8px 24px rgba(0,0,0,.4)', minWidth: 160,
            }}>
              {!isExternal && (
                <>
                  <button
                    style={menuItemStyle} onClick={() => { setMenuOpen(false); handleCopy(); }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                  >
                    <Icons.copy size={11} stroke={RT.textDim} /> Copy session ID
                  </button>
                  <button
                    style={{ ...menuItemStyle, opacity: s.url ? 1 : 0.45, cursor: s.url ? 'pointer' : 'default' }}
                    onClick={() => { if (s.url) { setMenuOpen(false); handleLink(); } }}
                    onMouseEnter={(e) => { if (s.url) (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                  >
                    <Icons.link size={11} stroke={RT.textDim} /> Open URL
                  </button>
                </>
              )}
              <button
                style={menuItemStyle} onClick={handlePreviewClick}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
              >
                <Icons.search size={11} stroke={RT.textDim} /> Preview
              </button>
              {!isExternal && (
                <button
                  style={menuItemStyle} onClick={handleUnstick}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = RT.bgRaised; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                >
                  <Icons.refresh size={11} stroke={RT.amber} /> Unstick
                </button>
              )}
            </div>
          )}
        </div>
        )}
```

Also update `handlePreviewClick` (line 127-130) to use `previewTarget`:

```typescript
  const handlePreviewClick = () => {
    setMenuOpen(false);
    onPreview(previewTarget);
  };
```

And the "Stop" action's `isExternal` pid-based stop call is unaffected (it already branches on `isExternal` — an adopted row still stops via pid, since it has no `rc-*` tmux session of its own to `kill-session`).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/SessionRow.tsx
git commit -m "feat(frontend): SessionRow opens terminal + Enable RC for adopted external rows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 8: `AllSessions.tsx` — same treatment for the cross-device mobile list

**Files:**
- Modify: `frontend/src/components/AllSessions.tsx`

**Interfaces:**
- Consumes: same `Session.tmux`/`rc_url`/`api.enableRc` as Task 7.
- Produces: no new exported interface.

- [ ] **Step 1: Compute `isAdopted`/`previewTarget` and wire the row click + Preview button**

In `AllSessions.tsx`, inside the `items.map(({ device: d, session: s }) => { ... })` body, after `const isExternal = s.kind === 'external';` (line 55), add:

```typescript
          const isAdopted = isExternal && !!s.tmux;
          const canOpenTerminal = !isExternal || isAdopted;
          const previewTarget = isAdopted ? s.tmux!.session_name : s.name;
```

Update the row `onClick`/`title`/`cursor` (lines 60-66):

```typescript
              onClick={canOpenTerminal ? () => setPreview({ deviceId: d.id, name: previewTarget, mode: s.mode }) : undefined}
              title={canOpenTerminal ? 'Open terminal' : undefined}
              style={{
                background: RT.card, border: `1px solid ${RT.border}`,
                borderRadius: 10, padding: 12,
                display: 'flex', flexDirection: 'column', gap: 8,
                cursor: canOpenTerminal ? 'pointer' : 'default',
              }}>
```

- [ ] **Step 2: Show Preview + Enable RC / Open link for an adopted row, keep others read-only**

Replace `{!isExternal && ( ... )}` in the actions row (lines 99-125) — keep Restart+MoreMenu launcher-only, but add a Preview button and RC control for an adopted external row. Add local per-row state for the RC url override next to the existing `pending`/`preview` state (top of the component, after line 23):

```typescript
  const [rcUrls, setRcUrls] = useState<Record<string, string>>({});
```

And a handler alongside `guard`:

```typescript
  const handleEnableRc = async (key: string, deviceId: string, tmuxName: string) => {
    if (pending[`rc-${key}`]) return;
    setPending((p) => ({ ...p, [`rc-${key}`]: true }));
    try {
      const result = await api.enableRc(deviceId, tmuxName);
      if (result.ok && result.url) setRcUrls((u) => ({ ...u, [key]: result.url as string }));
    } finally {
      setPending((p) => ({ ...p, [`rc-${key}`]: false }));
    }
  };
```

Replace the actions block:

```typescript
              <div onClick={(e) => e.stopPropagation()} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                {!isExternal && (
                  <>
                    <button
                      style={mobileActionBtn()}
                      onClick={() => setPreview({ deviceId: d.id, name: s.name, mode: s.mode })}
                      title="Show terminal output"
                    >
                      <Icons.search size={13} stroke={RT.textDim} /> Preview
                    </button>
                    <button
                      style={mobileActionBtn()}
                      disabled={!!pending[`restart-${key}`]}
                      onClick={() => guard(`restart-${key}`, () => api.restart(d.id, s.name))}
                      title="Restart this session"
                    >
                      <Icons.refresh size={13} stroke={RT.green} /> Restart
                    </button>
                    <MoreMenu
                      deviceId={d.id}
                      sessionName={s.name}
                      sessionId={s.sessionId}
                      url={s.url}
                      pending={!!pending[`unstick-${key}`]}
                      onUnstick={() => guard(`unstick-${key}`, () => api.unstick(d.id, s.name))}
                    />
                  </>
                )}
                {isAdopted && (
                  <>
                    <button
                      style={mobileActionBtn()}
                      onClick={() => setPreview({ deviceId: d.id, name: previewTarget, mode: s.mode })}
                      title="Show terminal output"
                    >
                      <Icons.search size={13} stroke={RT.textDim} /> Preview
                    </button>
                    {rcUrls[key] || s.rc_url ? (
                      <button
                        style={mobileActionBtn()}
                        onClick={() => window.open(rcUrls[key] ?? s.rc_url ?? '', '_blank')}
                        title="Open on claude.ai"
                      >
                        <Icons.link size={13} stroke={RT.textDim} /> Open on claude.ai
                      </button>
                    ) : (
                      <button
                        style={mobileActionBtn()}
                        disabled={!!pending[`rc-${key}`]}
                        onClick={() => handleEnableRc(key, d.id, s.tmux!.session_name)}
                        title="Enable Remote Control"
                      >
                        <Icons.refresh size={13} stroke={RT.amber} /> Enable RC
                      </button>
                    )}
                  </>
                )}
                {isExternal && !isAdopted && (
                  <span style={{ fontFamily: FONT_MONO, fontSize: 10.5, color: RT.textLow, fontStyle: 'italic' }}>
                    not in tmux
                  </span>
                )}
                <button
                  style={{ background: RT.panel, border: `1px solid ${RT.border}`, borderRadius: 7, width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', marginLeft: 'auto' }}
                  disabled={!!pending[`stop-${key}`]}
                  onClick={() => guard(`stop-${key}`, () => api.stop(d.id, s.name, isExternal ? { external: true, pid: s.pid } : undefined))}
                  title="Stop this session"
                >
                  <Icons.stop size={12} stroke={RT.red} />
                </button>
              </div>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/AllSessions.tsx
git commit -m "feat(frontend): AllSessions mirrors SessionRow's adopted-external controls

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 9: Mobile polish — hide the redundant sessions list below the launcher

**Files:**
- Modify: `frontend/src/components/DeviceDetail.tsx:76-97`

**Interfaces:**
- Consumes: `Layout.mobile` (existing, `useLayout.ts:17`).
- Produces: no new interface.

**Context:** On mobile, the "Sessions" tab (`AllSessions.tsx`, reached via bottom nav) already lists every session across every device. `DeviceDetail`'s own `tab === 'running'` body (`DeviceDetail.tsx:76-97`, under the launcher) duplicates that same information for just the open device, which on a phone screen means scrolling past it twice. Desktop/tablet keep it — there is no separate cross-device list visible there.

- [ ] **Step 1: Gate the running-tab session list on `!mobile`**

Replace (`DeviceDetail.tsx:76-97`):

```typescript
        {tab === 'running' && (
          <>
            {/* "+ New schedule" equivalent for sessions: just the list */}
            {sessions.length === 0 ? (
              <V5Empty text={device.online ? `No active sessions on ${device.name}. Launch one above.` : 'Device offline.'} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sessions.map((s) => (
                  <SessionRow
                    key={s.kind === 'external' ? 'ext:' + (s.session_id ?? s.sessionId ?? s.name) : (s.session_id ?? s.sessionId ?? s.name)}
                    s={s}
                    hue={hue}
                    deviceId={device.id}
                    mobile={mobile}
                    onChanged={reloadSessions}
                    onPreview={(name) => setPreviewName(name)}
                  />
                ))}
              </div>
            )}
          </>
        )}
```

with:

```typescript
        {tab === 'running' && !mobile && (
          <>
            {/* "+ New schedule" equivalent for sessions: just the list.
                Mobile hides this — the Sessions tab (AllSessions.tsx)
                already lists every device's sessions, so repeating just
                this device's here would mean scrolling past the same
                rows twice on a phone screen. */}
            {sessions.length === 0 ? (
              <V5Empty text={device.online ? `No active sessions on ${device.name}. Launch one above.` : 'Device offline.'} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sessions.map((s) => (
                  <SessionRow
                    key={s.kind === 'external' ? 'ext:' + (s.session_id ?? s.sessionId ?? s.name) : (s.session_id ?? s.sessionId ?? s.name)}
                    s={s}
                    hue={hue}
                    deviceId={device.id}
                    mobile={mobile}
                    onChanged={reloadSessions}
                    onPreview={(name) => setPreviewName(name)}
                  />
                ))}
              </div>
            )}
          </>
        )}
```

`mobile` is already destructured at the top of the function (`const mobile = layout.mobile;`, `DeviceDetail.tsx:37`), so no new prop plumbing is needed. On mobile the "running" tab body is now empty — verify `PanelTabs`/tab switching still works by checking `PanelTabs.tsx`'s `sessionCount={sessions.length}` (line 64) is untouched, so the tab badge/count still reflects real session count even though the list itself doesn't render there on mobile.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/DeviceDetail.tsx
git commit -m "fix(frontend): hide the per-device running list on mobile, Sessions tab covers it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

### Task 10: Frontend build, bundle grep, final verification

**Files:**
- Modify: `static/dist/*` (rebuilt output only — no source changes in this task)

**Interfaces:**
- Consumes: everything from Tasks 6-9.
- Produces: nothing new — this task only verifies and ships the built bundle.

- [ ] **Step 1: Typecheck**

```bash
cd /var/www/rc-launcher-p2/frontend && npm ci && npx tsc --noEmit
```

Expected: no errors. If `Session.tmux`/`rc_url` or `api.enableRc` typing mismatches surface here, fix them in the relevant Task 6-8 file before proceeding — do not silence with `any`.

- [ ] **Step 2: Build**

```bash
cd /var/www/rc-launcher-p2/frontend && npm run build
```

Expected: build succeeds, `static/dist/` is refreshed.

- [ ] **Step 3: Bundle grep for personal identifiers**

```bash
grep -rniE 'barjazz|tail82c219|tbarjadze|hetzner|tba-lin|100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|/root/' /var/www/rc-launcher-p2/static/dist/
```

Expected: no output (empty). If anything hits, find its source in `frontend/src` (the bundle only contains what source produced — search there, e.g. `grep -rniE '<same pattern>' /var/www/rc-launcher-p2/frontend/src/`) and fix it before rebuilding; do not hand-edit `static/dist/` directly.

- [ ] **Step 4: Full backend test suite one more time**

```bash
cd /var/www/rc-launcher-p2 && python3 -m unittest discover tests
```

Expected: `Ran 306 tests ... OK` (unchanged from Task 5 — this task touches no Python).

- [ ] **Step 5: Commit the rebuilt bundle**

```bash
cd /var/www/rc-launcher-p2
git add static/dist
git commit -m "chore(frontend): rebuild static/dist for external session adoption + mobile polish

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PEEmyzw3539Hi8X79PwP67"
```

---

## Self-review notes

- **Spec coverage:** item 0 (both residuals) → Task 1 (overview fix; hooks-cell tint verified already correct, documented rather than faked as a task). Item 1 (`panes.py`) → Task 2. Item 2 (adoption in `list_rc_sessions`) → Task 3. Item 3 (route allowlist) → Task 4. Item 4 (`enable-rc`) → Task 5. Item 5 (frontend row controls) → Tasks 6-8. Item 6 (mobile hide) → Task 9. Item 7 (build/grep) → Task 10.
- **Placeholder scan:** none — every step has literal code, exact file paths/line ranges, and concrete assertions.
- **Type consistency:** `Session.tmux`/`rc_url` (Task 6) match the exact shape `sessions.list_rc_sessions()` produces in Task 3 (`{"session_name": str, "pane_id": str} | None`, `str | None`). `panes.pane_for_pid`'s return dict shape (Task 2) matches what Task 3's `entry["tmux"]` construction reads (`pane["session_name"]`, `pane["pane_id"]`). `server._session_name_allowed`/`_adopted_tmux_names` (Task 4) are consumed unchanged by Task 5's route guard. `api.enableRc`'s return shape (Task 6) matches `server._enable_rc_for_adopted`'s dict shape (Task 5) field-for-field (`ok`, `url`, `message`).
