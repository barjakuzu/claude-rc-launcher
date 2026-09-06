"""agents.py: wraps `claude agents --json`, normalizes rows, caches 30s,
single-flight refreshes, gated on compat's agents_json capability."""
import os, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents
import compat
import server


class FakeRun:
    def __init__(self, stdout="[]", returncode=0, raises=None, delay=0):
        self.stdout = stdout
        self.returncode = returncode
        self.raises = raises
        self.delay = delay
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, cmd, **kw):
        with self.lock:
            self.calls.append(cmd)
        if self.delay:
            time.sleep(self.delay)
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


def _reset_agents_state():
    agents._cache = {"rows": [], "at": 0.0, "bin": None}
    agents._refreshing = False
    agents._last_refresh_thread = None


class AgentsJsonCapsGateMixin:
    """Force compat.get_caps() to report agents_json: True for tests that
    exercise the spawn path, restoring the real get_caps on tearDown."""

    def setUp(self):
        _reset_agents_state()
        self._patched_get_caps = compat.get_caps
        compat.get_caps = lambda: {"agents_json": True}
        super().setUp()

    def tearDown(self):
        compat.get_caps = self._patched_get_caps
        super().tearDown()


class ListClaudeSessionsTest(AgentsJsonCapsGateMixin, unittest.TestCase):
    def test_normalizes_rows(self):
        fake = FakeRun(stdout=RAW_JSON)
        rows = agents.list_claude_sessions(claude_bin="claude", run=fake)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {
            "session_id": "abc-123", "name": "portugal", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "idle", "started_at": 1757100000000,
            "pid": 111, "waiting_for": None, "state": None,
        })
        self.assertEqual(rows[2]["waiting_for"], "permission_prompt")
        self.assertIsNone(rows[2]["name"])
        self.assertEqual(rows[1]["state"], "running")

    def test_returned_list_is_a_copy(self):
        """Mutating a previously returned list must not corrupt the cache
        (or the next call's result) — list_claude_sessions must hand back
        a copy, not the cached list itself."""
        fake = FakeRun(stdout=RAW_JSON)
        rows = agents.list_claude_sessions(claude_bin="claude", run=fake)
        rows.append({"session_id": "injected"})
        rows.clear()

        rows_again = agents.list_claude_sessions(claude_bin="claude", run=fake)
        self.assertEqual(len(rows_again), 3)

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
        if agents._last_refresh_thread:
            agents._last_refresh_thread.join(timeout=5)
        self.assertEqual(len(fake.calls), 2)  # cache expired, re-ran (in background)

    def test_cache_is_keyed_on_claude_bin(self):
        fake_a = FakeRun(stdout=RAW_JSON)
        fake_b = FakeRun(stdout="[]")
        clock = {"t": 1000.0}
        rows_a = agents.list_claude_sessions(claude_bin="claude-a", run=fake_a, now_fn=lambda: clock["t"])
        self.assertEqual(len(rows_a), 3)
        # A different bin must not be served the first bin's cached rows —
        # nothing cached for it yet, so it blocks and fetches its own.
        rows_b = agents.list_claude_sessions(claude_bin="claude-b", run=fake_b, now_fn=lambda: clock["t"])
        self.assertEqual(rows_b, [])
        self.assertEqual(len(fake_a.calls), 1)
        self.assertEqual(len(fake_b.calls), 1)

    def test_gated_on_agents_json_cap(self):
        compat.get_caps = lambda: {"agents_json": False}
        fake = FakeRun(stdout=RAW_JSON)
        rows = agents.list_claude_sessions(claude_bin="claude", run=fake)
        self.assertEqual(rows, [])
        self.assertEqual(fake.calls, [])  # never even spawned


class SingleFlightRefreshTest(AgentsJsonCapsGateMixin, unittest.TestCase):
    """Two concurrent callers with nothing cached yet must trigger exactly
    one `claude agents --json` spawn, not one each."""

    def test_two_threads_share_one_spawn(self):
        fake = FakeRun(stdout=RAW_JSON, delay=0.2)
        results = []

        def worker():
            results.append(agents.list_claude_sessions(claude_bin="claude", run=fake))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(fake.calls), 1)
        for r in results:
            self.assertEqual(len(r), 3)

    def test_stale_cache_triggers_background_refresh_not_a_block(self):
        # First call populates the cache (blocking, nothing cached yet).
        fake = FakeRun(stdout=RAW_JSON)
        clock = {"t": 1000.0}
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 1)

        # Now make the underlying spawn slow, expire the TTL, and confirm
        # the stale rows come back immediately (not after the delay).
        slow_fake = FakeRun(stdout=RAW_JSON, delay=0.3)
        clock["t"] += 31
        started = time.time()
        rows = agents.list_claude_sessions(claude_bin="claude", run=slow_fake, now_fn=lambda: clock["t"])
        elapsed = time.time() - started
        self.assertEqual(len(rows), 3)  # stale rows, served immediately
        self.assertLess(elapsed, 0.2, "stale read must not block on the slow refresh")
        if agents._last_refresh_thread:
            agents._last_refresh_thread.join(timeout=5)
        self.assertEqual(len(slow_fake.calls), 1)


class DoRefreshClearsFlagOnExceptionTest(unittest.TestCase):
    """An unexpected exception inside _do_refresh (not one of the handled
    _fetch_rows failure modes) must still clear _refreshing, or every
    subsequent caller would see the cache as permanently mid-refresh and
    never spawn again."""

    def tearDown(self):
        with agents._refresh_cond:
            agents._refreshing = False

    def test_refreshing_cleared_after_unexpected_exception(self):
        with agents._refresh_cond:
            agents._refreshing = True

        def boom(*a, **kw):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            agents._do_refresh("claude", boom, time.time)

        with agents._refresh_cond:
            self.assertFalse(agents._refreshing)


class ListRcSessionsMergesExternalTest(unittest.TestCase):
    def setUp(self):
        _reset_agents_state()
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
             "kind": "interactive", "status": "idle", "started_at": 1, "pid": 1,
             "waiting_for": None, "state": None},
            {"session_id": "external-uuid-2", "name": "hand-started", "cwd": "/home/user/other",
             "kind": "interactive", "status": "busy", "started_at": 2, "pid": 2,
             "waiting_for": None, "state": None},
        ]

        result = self.sessions.list_rc_sessions()

        names = [s["name"] for s in result]
        self.assertIn("rc-portugal", names)
        external_rows = [s for s in result if s.get("kind") == "external"]
        self.assertEqual(len(external_rows), 1)
        self.assertEqual(external_rows[0]["session_id"], "external-uuid-2")
        self.assertTrue(external_rows[0]["external"])
        self.assertEqual(external_rows[0]["name"], "hand-started")

    def test_external_row_carries_claude_state_for_needs_attention(self):
        class FakeRun:
            def __call__(self, cmd, **kw):
                class R:
                    returncode = 0
                    stderr = ""
                    stdout = ""
                return R()
        self.sessions.subprocess.run = FakeRun()
        self.sessions.get_session_env = lambda name, var: None
        agents.list_claude_sessions = lambda: [
            {"session_id": "external-uuid-3", "name": "bg-task", "cwd": "/home/user/other",
             "kind": "background", "status": "busy", "started_at": 2, "pid": 3,
             "waiting_for": None, "state": "blocked"},
        ]

        result = self.sessions.list_rc_sessions()

        external_rows = [s for s in result if s.get("kind") == "external"]
        self.assertEqual(len(external_rows), 1)
        self.assertEqual(external_rows[0]["claude"]["state"], "blocked")

    def test_launcher_row_with_busy_claude_row_derives_busy(self):
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
             "kind": "interactive", "status": "busy", "started_at": 1, "pid": 1,
             "waiting_for": None, "state": "running"},
        ]

        result = self.sessions.list_rc_sessions()

        launcher_row = next(s for s in result if s["name"] == "rc-portugal")
        self.assertEqual(launcher_row["claude"]["status"], "busy")
        self.assertEqual(server._derive_session_state(launcher_row), "busy")

    def test_launcher_row_with_waiting_for_derives_needs_attention(self):
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
             "kind": "interactive", "status": "idle", "started_at": 1, "pid": 1,
             "waiting_for": "permission_prompt", "state": "waiting"},
        ]

        result = self.sessions.list_rc_sessions()

        launcher_row = next(s for s in result if s["name"] == "rc-portugal")
        self.assertEqual(launcher_row["claude"]["waitingFor"], "permission_prompt")
        self.assertEqual(server._derive_session_state(launcher_row), "needs_attention")


if __name__ == "__main__":
    unittest.main()
