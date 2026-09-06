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
        # The claude invocation itself is wrapped in a single `bash -c
        # "<script>"` argv element (same shape the pre-existing baseline
        # tests assert on via cmd[-1]/cmd[-3:-1]), so native flags land as
        # substrings of that joined script rather than as standalone argv
        # items — check the joined command line instead of raw membership.
        joined = " ".join(cmd)
        self.assertIn("--session-id 0d3b8b1a-1111-4a2b-9c3d-abcdef012345", joined)
        self.assertIn("-n portugal", joined)
        self.assertIn("--remote-control portugal", joined)
        self.assertIn("--permission-mode bypassPermissions", joined)
        # env vars set at creation (these ARE standalone argv items)
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
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertNotIn("--remote-control", joined)
        self.assertNotIn("RC_SESSION_ID=0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        # The RC_FLAGS string still ends up in the joined claude_cmd (bash -c argv)
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
        self.assertIn("--permission-mode bypassPermissions", joined)
        self.assertIn("--teammate-mode in-process", joined)

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
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertNotIn("--remote-control", joined)
        self.assertNotIn("-n portugal", joined)

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


if __name__ == "__main__":
    unittest.main()
