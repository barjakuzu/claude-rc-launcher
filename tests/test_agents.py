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
    def setUp(self):
        agents._cache = {"rows": [], "at": 0.0}

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


class ListRcSessionsMergesExternalTest(unittest.TestCase):
    def setUp(self):
        agents._cache = {"rows": [], "at": 0.0}
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


if __name__ == "__main__":
    unittest.main()
