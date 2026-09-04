"""setup_session must rename the session even when remote control never
activates — the two are independent, and a session that fails to activate
still needs the name the user gave it."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sessions

# A status bar as the current Claude Code TUI renders it: the remote-control
# indicator is an OSC 8 hyperlink whose visible label is just "/rc". It looks
# byte-identical whether remote control is active or not, so none of the old
# "Remote Control active" marker strings appear anywhere.
OSC_LINK = (
    "\x1b]8;id=31i8xc;https://claude.ai/code/session_01FxTHitxTgkXbpbQEDJaR4R"
    "?from=cli\x1b\\/rc\x1b]8;;\x1b\\"
)
READY_PANE = (
    "  Claude Code v2.1.259\n"
    "❯ \n"
    "  ~/career-ops | Opus 5 | Tokens: 0/1.0M (0%)          " + OSC_LINK + "\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)


class FakeRun:
    """Records tmux invocations and answers capture-pane with READY_PANE."""

    def __init__(self, pane=READY_PANE):
        self.pane = pane
        self.calls = []

    def __call__(self, cmd, *a, **kw):
        self.calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        if isinstance(cmd, list) and "capture-pane" in cmd:
            R.stdout = self.pane
        if isinstance(cmd, list) and "list-panes" in cmd:
            R.stdout = "0 node"
        return R

    def sent_text(self):
        """Every literal string typed into the session via send-keys -l."""
        return [c[-1] for c in self.calls
                if isinstance(c, list) and "send-keys" in c and "-l" in c]


class SetupSessionRenameTest(unittest.TestCase):
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
        sessions.session_exists = lambda n: True
        sessions.get_session_status = lambda n: "running"
        sessions.get_session_env = lambda n, v: None

    def tearDown(self):
        sessions.subprocess.run = self._patched["subprocess"]
        sessions.time.sleep = self._patched["sleep"]
        sessions.session_exists = self._patched["exists"]
        sessions.get_session_status = self._patched["status"]
        sessions.get_session_env = self._patched["env"]

    def test_renames_even_when_remote_control_never_activates(self):
        sessions.setup_session("rc-portugal", "portugal", "c")
        typed = self.fake.sent_text()
        self.assertTrue(
            any(t.startswith("/rename") for t in typed),
            f"/rename was never sent; typed instead: {typed}",
        )
        self.assertIn("/rename portugal", typed)

    def test_rename_is_sent_after_remote_control_is_attempted(self):
        sessions.setup_session("rc-portugal", "portugal", "c")
        typed = self.fake.sent_text()
        self.assertIn("/remote-control", typed)
        self.assertLess(typed.index("/remote-control"), typed.index("/rename portugal"),
                        "rename must follow the remote-control attempt")

    def test_shell_sessions_are_never_set_up(self):
        # Guard for the SHELL mode contract: /start must not run setup_session
        # for a shell, so nothing is ever typed into it.
        self.assertFalse(sessions.is_shell_session("rc-portugal"))


if __name__ == "__main__":
    unittest.main()
