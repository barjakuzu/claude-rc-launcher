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
    def setUp(self):
        # The adoption path caches get_url_with_source lookups per tmux
        # session name (sessions._adoption_url_cache); several tests here
        # reuse the same "mysession" name, so a stale cache entry left by
        # one test would leak into the next and hide a real regression.
        sessions._adoption_url_cache.clear()

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
        guws.assert_called_once_with("mysession", persist=False)

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

    def test_second_call_within_ttl_reuses_cached_url_no_extra_capture_pane(self):
        """Two consecutive list_rc_sessions calls for the same adopted
        session within the cache TTL must spawn get_url_with_source (and
        therefore capture-pane) at most once."""
        run_result = mock.Mock(returncode=0, stdout="")
        pane = {"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}
        with mock.patch("sessions.subprocess.run", return_value=run_result), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[EXTERNAL_ROW]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane), \
             mock.patch("sessions.get_url_with_source",
                         return_value=("https://claude.ai/code/session_xyz", "osc8")) as guws:
            rows1 = sessions.list_rc_sessions()
            rows2 = sessions.list_rc_sessions()
        self.assertEqual(guws.call_count, 1)
        self.assertEqual(rows1[0]["rc_url"], "https://claude.ai/code/session_xyz")
        self.assertEqual(rows2[0]["rc_url"], "https://claude.ai/code/session_xyz")

    def test_cache_entry_expires_after_ttl(self):
        pane = {"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}
        run_result = mock.Mock(returncode=0, stdout="")
        clock = {"t": 1000.0}
        with mock.patch("sessions.subprocess.run", return_value=run_result), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[EXTERNAL_ROW]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane), \
             mock.patch("sessions.get_url_with_source",
                         return_value=("https://claude.ai/code/session_xyz", "osc8")) as guws, \
             mock.patch("sessions.time.time", side_effect=lambda: clock["t"]):
            sessions.list_rc_sessions()
            clock["t"] += sessions.ADOPTION_URL_CACHE_TTL + 1
            sessions.list_rc_sessions()
        self.assertEqual(guws.call_count, 2)

    def test_miss_is_retried_sooner_than_a_hit(self):
        pane = {"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}
        run_result = mock.Mock(returncode=0, stdout="")
        clock = {"t": 1000.0}
        with mock.patch("sessions.subprocess.run", return_value=run_result), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[EXTERNAL_ROW]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane), \
             mock.patch("sessions.get_url_with_source", return_value=(None, None)) as guws, \
             mock.patch("sessions.time.time", side_effect=lambda: clock["t"]):
            sessions.list_rc_sessions()
            clock["t"] += sessions.ADOPTION_URL_CACHE_MISS_TTL + 1
            sessions.list_rc_sessions()
        self.assertEqual(guws.call_count, 2)


class GetUrlWithSourcePersistFalseTest(unittest.TestCase):
    """persist=False (the adoption path's mode) must never write to a tmux
    session it does not own: no `tmux set-environment` call, ever."""

    def test_no_set_environment_call_when_osc8_url_found(self):
        osc8_pane = "\x1b]8;;https://claude.ai/code/session_abc\x1b\\/rc\x1b]8;;\x1b\\"
        run_result = mock.Mock(returncode=0, stdout=osc8_pane)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return run_result

        with mock.patch("sessions.subprocess.run", side_effect=fake_run):
            url, source = sessions.get_url_with_source("mysession", persist=False)
        self.assertEqual((url, source), ("https://claude.ai/code/session_abc", "osc8"))
        self.assertTrue(all("set-environment" not in c for c in calls))
        self.assertTrue(all("show-environment" not in c for c in calls))

    def test_no_env_calls_at_all_on_a_miss(self):
        run_result = mock.Mock(returncode=0, stdout="nothing here")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return run_result

        with mock.patch("sessions.subprocess.run", side_effect=fake_run):
            url, source = sessions.get_url_with_source("mysession", persist=False)
        self.assertEqual((url, source), (None, None))
        self.assertTrue(all("show-environment" not in c for c in calls))
        self.assertTrue(all("set-environment" not in c for c in calls))


class InvalidateAdoptedUrlCacheTest(unittest.TestCase):
    def test_removes_cached_entry(self):
        sessions._adoption_url_cache["mysession"] = ("https://claude.ai/code/session_x", "osc8", 99999999999.0)
        sessions.invalidate_adopted_url_cache("mysession")
        self.assertNotIn("mysession", sessions._adoption_url_cache)

    def test_missing_entry_is_a_no_op(self):
        sessions._adoption_url_cache.pop("nope", None)
        sessions.invalidate_adopted_url_cache("nope")  # must not raise


class PaneMatchedRcRowMergeTest(unittest.TestCase):
    """Pre-v3 launcher sessions have no RC_SESSION_ID in their tmux env, so
    their `claude agents --json` row never matches the id-based lookup and
    used to be listed a second time as an external row. When the pid's
    resolved pane belongs to one of our own rc-* sessions that has a
    matching launcher row, that row must merge in (not duplicate), and the
    launcher session's tmux env must be back-filled with RC_SESSION_ID so
    later polls match by id."""

    def setUp(self):
        sessions._adoption_url_cache.clear()

    def _fake_run(self, calls, list_sessions_stdout):
        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "list-sessions" in cmd:
                return mock.Mock(returncode=0, stdout=list_sessions_stdout)
            return mock.Mock(returncode=0, stdout="")
        return fake_run

    def test_pane_matched_row_merges_into_launcher_row_no_duplicate(self):
        legacy_row = {
            "session_id": "new-uuid-1", "name": "jobs-lin", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "idle", "started_at": 1,
            "pid": 2567453, "waiting_for": None, "state": None,
        }
        pane = {"session_name": "rc-jobs-lin", "pane_id": "%5", "pane_pid": 2567448, "window_index": "0"}
        calls = []
        with mock.patch("sessions.subprocess.run",
                         side_effect=self._fake_run(calls, "rc-jobs-lin\t1700000000\n")), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[legacy_row]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane) as pfp:
            rows = sessions.list_rc_sessions()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "rc-jobs-lin")
        self.assertNotEqual(row.get("kind"), "external")
        self.assertEqual(row["claude"], {
            "status": "idle", "waiting_for": None, "session_id": "new-uuid-1",
        })
        pfp.assert_called_once_with(2567453)
        set_env_calls = [c for c in calls if "set-environment" in c]
        self.assertEqual(len(set_env_calls), 1)
        self.assertEqual(
            set_env_calls[0],
            ["tmux", "set-environment", "-t", "rc-jobs-lin", "RC_SESSION_ID", "new-uuid-1"],
        )

    def test_pane_matched_row_mirrors_waiting_for_and_busy_status(self):
        legacy_row = {
            "session_id": "new-uuid-2", "name": "jobs-lin", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "busy", "started_at": 1,
            "pid": 999, "waiting_for": "permission_prompt", "state": "waiting",
        }
        pane = {"session_name": "rc-jobs-lin", "pane_id": "%5", "pane_pid": 111, "window_index": "0"}
        calls = []
        with mock.patch("sessions.subprocess.run",
                         side_effect=self._fake_run(calls, "rc-jobs-lin\t1700000000\n")), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[legacy_row]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane):
            rows = sessions.list_rc_sessions()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["waiting_for"], "permission_prompt")
        self.assertEqual(row["status"], "busy")

    def test_no_set_environment_when_pane_matches_a_foreign_session_name(self):
        # Same as the existing "does not adopt" case, but here we also
        # assert no set-environment call is ever attempted for a
        # non-rc-* pane name.
        row = dict(EXTERNAL_ROW)
        pane = {"session_name": "some-other-session", "pane_id": "%3", "pane_pid": 1001, "window_index": "0"}
        calls = []
        with mock.patch("sessions.subprocess.run", side_effect=self._fake_run(calls, "")), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[row]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane):
            rows = sessions.list_rc_sessions()

        self.assertEqual(rows[0].get("kind"), "external")
        self.assertFalse(any("set-environment" in c for c in calls))

    def test_no_launcher_row_for_matched_name_stays_external(self):
        # Pane resolves to an rc-* name, but no launcher row with that
        # exact name exists (edge case / race) — must not crash, must not
        # persist RC_SESSION_ID, and the row stays external.
        legacy_row = {
            "session_id": "new-uuid-3", "name": "ghost", "cwd": "/home/user/proj",
            "kind": "interactive", "status": "idle", "started_at": 1,
            "pid": 2222, "waiting_for": None, "state": None,
        }
        pane = {"session_name": "rc-ghost", "pane_id": "%9", "pane_pid": 3333, "window_index": "0"}
        calls = []
        with mock.patch("sessions.subprocess.run", side_effect=self._fake_run(calls, "")), \
             mock.patch("sessions.agents.list_claude_sessions", return_value=[legacy_row]), \
             mock.patch("sessions.panes.pane_for_pid", return_value=pane):
            rows = sessions.list_rc_sessions()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("kind"), "external")
        self.assertFalse(any("set-environment" in c for c in calls))
