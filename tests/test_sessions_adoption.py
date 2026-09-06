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
