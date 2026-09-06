"""Tests for SHELL session mode (mode 'sh') — plain terminal sessions."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat
import config
import sessions

# This file's BuildTmuxCommandTest predates native-flag support and
# exercises the legacy RC_FLAGS-driven argv shape specifically - it must
# not depend on whatever claude binary happens to be installed on the
# machine running the tests, so caps are pinned to all-False here.
_LEGACY_CAPS = {
    "session_id_flag": False, "name_flag": False,
    "remote_control_flag": False, "permission_mode_flag": False,
    "agents_json": False, "version": None,
}


class BuildTmuxCommandTest(unittest.TestCase):
    def setUp(self):
        self._orig_get_caps = compat.get_caps
        compat.get_caps = lambda: dict(_LEGACY_CAPS)

    def tearDown(self):
        compat.get_caps = self._orig_get_caps

    def _env_value(self, cmd, key):
        """Return the value of an -e KEY=VALUE pair in a tmux argv."""
        for i, arg in enumerate(cmd):
            if arg == "-e" and cmd[i + 1].startswith(key + "="):
                return cmd[i + 1][len(key) + 1:]
        return None

    def test_claude_mode_runs_claude_in_a_bash_wrapper(self):
        cmd = sessions.build_tmux_command("rc-x", "/tmp", "c")
        self.assertEqual(cmd[:4], ["tmux", "new-session", "-d", "-s"])
        self.assertEqual(cmd[-3:-1], ["bash", "-c"])
        self.assertIn(config.CLAUDE_BIN, cmd[-1])
        self.assertIn("--dangerously-skip-permissions", cmd[-1])
        self.assertEqual(self._env_value(cmd, "RC_MODE"), "c")

    def test_claude_mode_appends_model_flag(self):
        cmd = sessions.build_tmux_command("rc-x", "/tmp", "c", model="2")
        self.assertIn("--model sonnet", cmd[-1])

    def test_claude_mode_resume_uses_uuid_when_known(self):
        cmd = sessions.build_tmux_command("rc-x", "/tmp", "c", resume=True,
                                          resume_id="abc-123")
        self.assertIn("--resume abc-123", cmd[-1])

    def test_claude_mode_resume_without_uuid_is_bare_flag(self):
        cmd = sessions.build_tmux_command("rc-x", "/tmp", "c", resume=True)
        self.assertIn("--resume", cmd[-1])
        self.assertNotIn("--resume ", cmd[-1].split("--resume")[1][:1])

    def test_shell_mode_runs_login_shell_not_claude(self):
        cmd = sessions.build_tmux_command("rc-x", "/tmp", config.SHELL_MODE)
        self.assertEqual(cmd[-2:], [config.SHELL_BIN, "-l"])
        self.assertNotIn(config.CLAUDE_BIN, " ".join(cmd))
        self.assertNotIn("bash -c", " ".join(cmd))

    def test_shell_mode_sets_session_env(self):
        cmd = sessions.build_tmux_command("rc-x", "/srv/app", config.SHELL_MODE)
        self.assertEqual(self._env_value(cmd, "RC_MODE"), config.SHELL_MODE)
        self.assertEqual(self._env_value(cmd, "RC_WORKDIR"), "/srv/app")
        self.assertEqual(self._env_value(cmd, "TERM"), "xterm-256color")

    def test_shell_mode_ignores_model_and_resume(self):
        cmd = sessions.build_tmux_command("rc-x", "/tmp", config.SHELL_MODE,
                                          model="2", resume=True, resume_id="abc")
        joined = " ".join(cmd)
        self.assertNotIn("sonnet", joined)
        self.assertNotIn("--resume", joined)
        self.assertNotIn("abc", joined)

    def test_working_dir_and_size_are_set(self):
        cmd = sessions.build_tmux_command("rc-x", "/srv/app", config.SHELL_MODE)
        self.assertEqual(cmd[cmd.index("-c") + 1], "/srv/app")
        self.assertEqual(cmd[cmd.index("-x") + 1], "200")
        self.assertEqual(cmd[cmd.index("-y") + 1], "50")


class ShellSessionsSkipClaudeScrapingTest(unittest.TestCase):
    """A shell session has no claude.ai URL, no token counter and no JSONL
    transcript — the Claude-specific probes must not run against it."""

    def setUp(self):
        self._real_env = sessions.get_session_env
        self._real_run = sessions.subprocess.run
        sessions.get_session_env = lambda name, var: (
            config.SHELL_MODE if var == "RC_MODE" else None
        )

        def _fail(*a, **kw):
            raise AssertionError(f"subprocess.run called for a shell session: {a}")

        self._fail = _fail

    def tearDown(self):
        sessions.get_session_env = self._real_env
        sessions.subprocess.run = self._real_run

    def test_get_url_returns_none_without_scraping(self):
        sessions.subprocess.run = self._fail
        self.assertIsNone(sessions.get_url("rc-shell"))

    def test_get_tokens_returns_none_without_scraping(self):
        sessions.subprocess.run = self._fail
        self.assertIsNone(sessions.get_tokens("rc-shell"))

    def test_transcript_is_none_without_lookup(self):
        sessions.subprocess.run = self._fail
        self.assertIsNone(sessions.get_transcript("rc-shell"))

    def test_shell_session_is_never_the_active_rc_session(self):
        self.assertFalse(sessions._is_rc_active("rc-shell"))


class ResolveClaudeModeTest(unittest.TestCase):
    """The scheduler always needs a Claude session; it must never end up
    running a bare shell."""

    def test_shell_mode_falls_back_to_standard(self):
        self.assertEqual(config.resolve_claude_mode(config.SHELL_MODE), "c")

    def test_unknown_mode_falls_back_to_standard(self):
        self.assertEqual(config.resolve_claude_mode("nonsense"), "c")

    def test_claude_modes_pass_through(self):
        for mode in ("c", "ci", "safe"):
            self.assertEqual(config.resolve_claude_mode(mode), mode)


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


if __name__ == "__main__":
    unittest.main()
