"""agents.py: wraps `claude agents --json`, normalizes rows, caches
briefly (agents.CACHE_TTL_SECONDS), single-flight refreshes, gated on
compat's agents_json capability."""
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


class NormalizeStartedAtTest(unittest.TestCase):
    """agents.normalize_started_at: raw `startedAt` (as `claude agents
    --json` reports it, or whatever else might show up) -> epoch SECONDS
    as a float, or None. Never raises."""

    def test_milliseconds_converts_to_seconds(self):
        self.assertEqual(agents.normalize_started_at(1757100000000), 1757100000.0)

    def test_real_observed_value_converts_exactly(self):
        # The actual value seen in `claude agents --json` output on a
        # production box.
        self.assertEqual(agents.normalize_started_at(1787677948238), 1787677948.238)

    def test_seconds_already_kept_as_is(self):
        self.assertEqual(agents.normalize_started_at(1757100000), 1757100000.0)

    def test_numeric_string_milliseconds_converts(self):
        self.assertEqual(agents.normalize_started_at("1787677948238"), 1787677948.238)

    def test_numeric_string_seconds_converts(self):
        self.assertEqual(agents.normalize_started_at("1757100000"), 1757100000.0)

    def test_zero_is_none(self):
        self.assertIsNone(agents.normalize_started_at(0))

    def test_negative_is_none(self):
        self.assertIsNone(agents.normalize_started_at(-1757100000))

    def test_none_is_none(self):
        self.assertIsNone(agents.normalize_started_at(None))

    def test_bool_true_is_not_accepted_as_a_number(self):
        self.assertIsNone(agents.normalize_started_at(True))

    def test_bool_false_is_not_accepted_as_a_number(self):
        self.assertIsNone(agents.normalize_started_at(False))

    def test_nan_is_none(self):
        self.assertIsNone(agents.normalize_started_at(float("nan")))

    def test_positive_infinity_is_none(self):
        self.assertIsNone(agents.normalize_started_at(float("inf")))

    def test_negative_infinity_is_none(self):
        self.assertIsNone(agents.normalize_started_at(float("-inf")))

    def test_non_numeric_string_is_none(self):
        self.assertIsNone(agents.normalize_started_at("not-a-number"))

    def test_too_small_to_be_a_real_timestamp_is_none(self):
        # Below MIN_STARTED_AT_SECONDS (1e9) but positive -- e.g. a
        # relative/offset value, not a plausible epoch time.
        self.assertIsNone(agents.normalize_started_at(12345))

    def test_huge_int_does_not_raise_overflow_error(self):
        # An int with ~308+ digits can't convert to a float at all
        # ("int too large to convert to float") -- must come back None,
        # not escape as an OverflowError, per the "never raises" contract.
        self.assertIsNone(agents.normalize_started_at(10 ** 400))

    def test_huge_negative_int_does_not_raise_overflow_error(self):
        self.assertIsNone(agents.normalize_started_at(-(10 ** 400)))


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
        # startedAt in the fixture is epoch MILLISECONDS (the real shape
        # `claude agents --json` reports) -- normalize_started_at converts
        # it to epoch seconds; started_at_raw keeps the untouched original
        # for debugging.
        self.assertEqual(rows[0], {
            "session_id": "abc-123", "name": "portugal", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "idle", "started_at": 1757100000.0,
            "started_at_raw": 1757100000000, "pid": 111, "waiting_for": None,
            "state": None,
        })
        self.assertEqual(rows[2]["waiting_for"], "permission_prompt")
        self.assertIsNone(rows[2]["name"])
        self.assertEqual(rows[1]["state"], "running")
        self.assertEqual(rows[1]["started_at"], 1757100005.0)
        self.assertEqual(rows[2]["started_at"], 1757100010.0)

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

    def test_caches_for_ttl_then_refreshes(self):
        # Parametrized on the real module constant, not a hardcoded
        # number, so this keeps testing the mechanism (cache within the
        # window, background refresh once stale) regardless of exactly
        # how CACHE_TTL_SECONDS is tuned.
        ttl = agents.CACHE_TTL_SECONDS
        fake = FakeRun(stdout=RAW_JSON)
        clock = {"t": 1000.0}
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        clock["t"] += ttl / 2
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 1)  # second call served from cache
        clock["t"] += ttl + 1
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


class InvalidateCacheTest(AgentsJsonCapsGateMixin, unittest.TestCase):
    """The targeted alternative to a shorter CACHE_TTL_SECONDS: a caller
    that already knows the truth just changed (server.py's POST /stop
    confirming a pid is gone) forces the next read fresh on demand,
    instead of every device polling faster all the time on the chance
    something changed."""

    def test_forces_a_fresh_fetch_well_within_the_ttl_window(self):
        fake = FakeRun(stdout=RAW_JSON)
        clock = {"t": 1000.0}
        agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 1)
        clock["t"] += 1  # nowhere near CACHE_TTL_SECONDS
        agents.invalidate_cache(claude_bin="claude")
        rows = agents.list_claude_sessions(claude_bin="claude", run=fake, now_fn=lambda: clock["t"])
        # Invalidated, not merely marked stale: this call blocks for its
        # own synchronous fetch (the "nothing cached yet" path) and
        # returns fresh data immediately, rather than serving the old
        # rows once more while a background refresh catches up behind it.
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len(rows), 3)

    def test_defaults_to_the_configured_claude_bin(self):
        fake = FakeRun(stdout=RAW_JSON)
        clock = {"t": 1000.0}
        agents.list_claude_sessions(claude_bin=agents.CLAUDE_BIN, run=fake, now_fn=lambda: clock["t"])
        agents.invalidate_cache()  # no claude_bin given
        agents.list_claude_sessions(claude_bin=agents.CLAUDE_BIN, run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 2)

    def test_does_not_affect_a_different_bins_cache(self):
        fake_a = FakeRun(stdout=RAW_JSON)
        fake_b = FakeRun(stdout="[]")
        clock = {"t": 1000.0}
        agents.list_claude_sessions(claude_bin="claude-a", run=fake_a, now_fn=lambda: clock["t"])
        agents.invalidate_cache(claude_bin="claude-b")  # a different, never-fetched bin
        agents.list_claude_sessions(claude_bin="claude-a", run=fake_a, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake_a.calls), 1)  # claude-a's cache untouched, still fresh

    def test_noop_when_nothing_cached_yet(self):
        agents.invalidate_cache(claude_bin="claude")  # must not raise
        self.assertEqual(agents._cache["at"], 0.0)


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

    def test_launcher_row_uses_claude_session_id_when_attached(self):
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
        ]

        result = self.sessions.list_rc_sessions()

        launcher_row = next(s for s in result if s["name"] == "rc-portugal")
        self.assertEqual(launcher_row["session_id"], "known-uuid-1")

    def test_launcher_row_falls_back_to_rc_session_id_env_when_no_claude_row(self):
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
            "RC_SESSION_ID": "env-uuid-9",
        }.get(var)
        agents.list_claude_sessions = lambda: []

        result = self.sessions.list_rc_sessions()

        launcher_row = next(s for s in result if s["name"] == "rc-portugal")
        self.assertEqual(launcher_row["session_id"], "env-uuid-9")

    def test_launcher_row_with_no_rc_session_id_gets_stable_synthetic_id(self):
        """Pre-v3 case: a launcher (rc-*) tmux session with no
        RC_SESSION_ID in its env and no matching claude row (e.g. claude
        agents --json unavailable or the process hasn't registered yet)
        must still get a stable top-level session_id, or
        store.upsert_sessions would silently skip it and the fleet store
        would end up holding only external sessions."""
        class FakeRun:
            def __call__(self, cmd, **kw):
                class R:
                    returncode = 0
                    stderr = ""
                    stdout = "rc-jobs-lin\n" if "list-sessions" in cmd else ""
                return R()
        self.sessions.subprocess.run = FakeRun()
        self.sessions.get_session_env = lambda name, var: {
            "RC_MODE": "c", "RC_WORKDIR": "/home/user/proj",
        }.get(var)
        agents.list_claude_sessions = lambda: []

        result1 = self.sessions.list_rc_sessions()
        result2 = self.sessions.list_rc_sessions()

        row1 = next(s for s in result1 if s["name"] == "rc-jobs-lin")
        row2 = next(s for s in result2 if s["name"] == "rc-jobs-lin")
        self.assertTrue(row1["session_id"])
        self.assertNotEqual(row1["session_id"], "")
        # Stable across consecutive builds -- not a random id -- or the
        # hub store would end and recreate the session on every poll.
        self.assertEqual(row1["session_id"], row2["session_id"])
        self.assertEqual(row1["session_id"], "tmux:rc-jobs-lin")

    def test_pre_v3_pane_matched_row_gets_real_claude_session_id_not_synthetic(self):
        """The pane-resolved match path (pre-v3 sessions with no
        RC_SESSION_ID) must end up with the claude row's real session_id,
        not the synthetic tmux:<name> fallback, once the claude row is
        attached and RC_SESSION_ID is backfilled -- otherwise the next
        poll (which matches by RC_SESSION_ID and gets the real id) would
        look like a brand new session to the hub store."""
        import panes as panes_mod
        legacy_row = {
            "session_id": "new-uuid-1", "name": "jobs-lin", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "idle", "started_at": 1,
            "pid": 2567453, "waiting_for": None, "state": None,
        }
        pane = {"session_name": "rc-jobs-lin", "pane_id": "%5", "pane_pid": 2567448,
                "window_index": "0"}

        class FakeRun:
            def __call__(self, cmd, **kw):
                class R:
                    returncode = 0
                    stderr = ""
                    stdout = "rc-jobs-lin\t1700000000\n" if "list-sessions" in cmd else ""
                return R()
        self.sessions.subprocess.run = FakeRun()
        self.sessions.get_session_env = lambda name, var: {"RC_MODE": "c"}.get(var)
        agents.list_claude_sessions = lambda: [legacy_row]
        orig_pane_for_pid = panes_mod.pane_for_pid
        panes_mod.pane_for_pid = lambda pid: pane
        try:
            result = self.sessions.list_rc_sessions()
        finally:
            panes_mod.pane_for_pid = orig_pane_for_pid

        launcher_row = next(s for s in result if s["name"] == "rc-jobs-lin")
        self.assertEqual(launcher_row["session_id"], "new-uuid-1")


if __name__ == "__main__":
    unittest.main()
