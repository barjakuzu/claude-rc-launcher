"""setup_session must rename the session even when remote control never
activates — the two are independent, and a session that fails to activate
still needs the name the user gave it."""
import os, re, sys, unittest
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


NATIVE_CAPS = {
    "session_id_flag": True, "name_flag": True,
    "remote_control_flag": True, "permission_mode_flag": True,
    "agents_json": True, "version": "2.1.263",
}
LEGACY_CAPS = {
    "session_id_flag": False, "name_flag": False,
    "remote_control_flag": False, "permission_mode_flag": False,
    "agents_json": False, "version": "1.9.0",
}


class SetupSessionNativeIdentitySkipsRcDanceTest(unittest.TestCase):
    """When build_tmux_command already used --session-id/--name/
    --remote-control (signalled by RC_SESSION_ID being set on the session
    AND compat.native_launch(compat.get_caps()) being true), setup_session
    must return immediately after the prompt/init wait: no /remote-control
    keystrokes, no /rename keystrokes, no polling loop."""

    def setUp(self):
        import compat
        self.fake = FakeRun()
        self._patched = {
            "subprocess": sessions.subprocess.run,
            "sleep": sessions.time.sleep,
            "exists": sessions.session_exists,
            "status": sessions.get_session_status,
            "env": sessions.get_session_env,
            "get_caps": compat.get_caps,
        }
        sessions.subprocess.run = self.fake
        sessions.time.sleep = lambda *_: None
        sessions.session_exists = lambda name: True
        sessions.get_session_status = lambda name: "running"
        sessions.get_session_env = lambda name, var: (
            "0d3b8b1a-1111-4a2b-9c3d-abcdef012345" if var == "RC_SESSION_ID" else None
        )
        compat.get_caps = lambda: dict(NATIVE_CAPS)

    def tearDown(self):
        import compat
        sessions.subprocess.run = self._patched["subprocess"]
        sessions.time.sleep = self._patched["sleep"]
        sessions.session_exists = self._patched["exists"]
        sessions.get_session_status = self._patched["status"]
        sessions.get_session_env = self._patched["env"]
        compat.get_caps = self._patched["get_caps"]

    def test_no_remote_control_or_rename_keystrokes_sent(self):
        sessions.setup_session("rc-portugal", "portugal", "c")
        sent = self.fake.sent_text()
        self.assertNotIn("/remote-control", sent)
        self.assertFalse(any(s.startswith("/rename") for s in sent))

    def test_mixed_caps_missing_remote_control_falls_back_to_keystrokes(self):
        # session_id + name but NOT remote_control -> not native_launch,
        # so RC_SESSION_ID being set is not enough on its own; setup_session
        # must still take the legacy keystroke path.
        import compat
        compat.get_caps = lambda: {**NATIVE_CAPS, "remote_control_flag": False}
        sessions.setup_session("rc-portugal", "portugal", "c")
        sent = self.fake.sent_text()
        self.assertIn("/remote-control", sent)
        self.assertTrue(any(s.startswith("/rename") for s in sent))


class BuildTmuxCommandNativeFlagsTest(unittest.TestCase):
    """build_tmux_command adds --session-id/--name/--remote-control/
    --permission-mode only when compat.get_caps() says the installed
    claude supports the full native set, and always falls back to
    config.RC_FLAGS otherwise."""

    def setUp(self):
        import compat
        self._orig_get_caps = compat.get_caps

    def tearDown(self):
        import compat
        compat.get_caps = self._orig_get_caps

    def _set_caps(self, caps):
        import compat
        compat.get_caps = lambda: dict(caps)

    def test_new_claude_gets_native_flags(self):
        self._set_caps(NATIVE_CAPS)
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
        self.assertIn("--name portugal", joined)
        self.assertIn("--remote-control portugal", joined)
        self.assertIn("--permission-mode bypassPermissions", joined)
        self.assertIn("--verbose", joined)
        # env vars set at creation (these ARE standalone argv items)
        self.assertIn("-e", cmd)
        self.assertIn("RC_SESSION_ID=0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        self.assertIn("RC_TITLE=portugal", cmd)

    def test_old_claude_falls_back_to_rc_flags_and_no_env(self):
        self._set_caps(LEGACY_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertNotIn("--remote-control", joined)
        self.assertNotIn("RC_SESSION_ID=0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        # The RC_FLAGS string still ends up in the joined claude_cmd (bash -c argv)
        self.assertIn("--dangerously-skip-permissions", joined)

    def test_mixed_caps_missing_session_id_flag_disables_all_native_flags(self):
        # remote_control + name present but session_id_flag missing ->
        # native_launch is false, so NONE of --session-id/--name/
        # --remote-control should appear, even though session_id was given.
        self._set_caps({**NATIVE_CAPS, "session_id_flag": False})
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertNotIn("--name portugal", joined)
        self.assertNotIn("--remote-control", joined)
        self.assertNotIn("RC_SESSION_ID=", " ".join(cmd))

    def test_ci_mode_native_flags_carry_teammate_mode(self):
        self._set_caps(NATIVE_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-ci-job", "/home/user/project", "ci",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertIn("--permission-mode bypassPermissions", joined)
        self.assertIn("--teammate-mode in-process", joined)

    def test_shell_mode_never_gets_native_flags_even_with_full_caps(self):
        self._set_caps(NATIVE_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "sh",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertNotIn("--remote-control", joined)
        self.assertNotIn("--name portugal", joined)

    def test_no_session_id_means_no_rc_env_vars(self):
        self._set_caps(NATIVE_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c")  # no session_id passed
        self.assertFalse(any(str(x).startswith("RC_SESSION_ID=") for x in cmd))

    def test_resume_never_combines_session_id_with_resume_flag(self):
        # RULING: --session-id and --resume are mutually exclusive; on a
        # resume launch only --resume <uuid> is passed, but RC_SESSION_ID/
        # RC_TITLE env and --name/--remote-control (when native) still are.
        self._set_caps(NATIVE_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c",
            resume=True, resume_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertIn("--resume 0d3b8b1a-1111-4a2b-9c3d-abcdef012345", joined)
        self.assertIn("--name portugal", joined)
        self.assertIn("--remote-control portugal", joined)
        self.assertIn("RC_SESSION_ID=0d3b8b1a-1111-4a2b-9c3d-abcdef012345", cmd)
        self.assertIn("RC_TITLE=portugal", cmd)

    def test_mixed_caps_session_id_flag_only_disables_native_session_id_too(self):
        # session_id_flag + remote_control_flag present but name_flag
        # missing -> native_launch is false (needs the full set), so
        # --session-id must NOT be emitted either (it used to be gated on
        # caps["session_id_flag"] alone, which let a mixed-caps claude get
        # --session-id without --name/--remote-control).
        self._set_caps({**NATIVE_CAPS, "name_flag": False})
        cmd = sessions.build_tmux_command(
            "rc-portugal", "/home/user/project", "c",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345")
        joined = " ".join(cmd)
        self.assertNotIn("--session-id", joined)
        self.assertNotIn("RC_SESSION_ID=", " ".join(cmd))
        self.assertNotIn("--remote-control", joined)

    def test_title_overrides_display_name(self):
        self._set_caps(NATIVE_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-run-abc123", "/home/user/project", "c",
            session_id="0d3b8b1a-1111-4a2b-9c3d-abcdef012345",
            title="nightly-report")
        joined = " ".join(cmd)
        self.assertIn("--name nightly-report", joined)
        self.assertIn("--remote-control nightly-report", joined)
        self.assertIn("RC_TITLE=nightly-report", cmd)

    def test_extra_env_is_appended(self):
        self._set_caps(LEGACY_CAPS)
        cmd = sessions.build_tmux_command(
            "rc-run-abc123", "/home/user/project", "c",
            extra_env=["-e", "RC_SCHEDULE_ID=sched-1"])
        self.assertIn("RC_SCHEDULE_ID=sched-1", cmd)


class RestartSessionPassesSessionIdTest(unittest.TestCase):
    """restart_session must pass the resolved conversation UUID through to
    build_tmux_command as session_id, so a restart keeps the same identity
    (RC_SESSION_ID/RC_TITLE, and --name/--remote-control when native)."""

    def setUp(self):
        self._patched = {
            "build_tmux_command": sessions.build_tmux_command,
            "find_uuid": sessions._find_session_uuid,
            "exists": sessions.session_exists,
            "subprocess": sessions.subprocess.run,
            "sleep": sessions.time.sleep,
            "thread": sessions.threading.Thread,
        }
        self.captured_kwargs = {}

        def fake_build_tmux_command(name, session_dir, mode, **kwargs):
            self.captured_kwargs.update(kwargs)
            return ["tmux", "new-session", "-d", "-s", name]

        sessions.build_tmux_command = fake_build_tmux_command
        sessions._find_session_uuid = lambda tmux_name, workdir: "resolved-uuid-1234"
        sessions.session_exists = lambda n: False  # nothing to kill first
        sessions.subprocess.run = lambda *a, **kw: type(
            "R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        sessions.time.sleep = lambda *_: None

        class ImmediateThread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                self._target, self._args = target, args
            def start(self):
                self._target(*self._args)

        sessions.threading.Thread = ImmediateThread

    def tearDown(self):
        sessions.build_tmux_command = self._patched["build_tmux_command"]
        sessions._find_session_uuid = self._patched["find_uuid"]
        sessions.session_exists = self._patched["exists"]
        sessions.subprocess.run = self._patched["subprocess"]
        sessions.time.sleep = self._patched["sleep"]
        sessions.threading.Thread = self._patched["thread"]

    def test_restart_passes_resolved_uuid_as_session_id(self):
        sessions.restart_session("rc-portugal", mode="c", workdir="/tmp", resume=True)
        self.assertEqual(self.captured_kwargs.get("session_id"), "resolved-uuid-1234")
        self.assertEqual(self.captured_kwargs.get("resume_id"), "resolved-uuid-1234")


class RestartSessionPrefersRcSessionIdTest(unittest.TestCase):
    """restart_session must reuse RC_SESSION_ID as the resume target
    (passed through to build_tmux_command as both resume_id and
    session_id, so the new session's env still carries RC_SESSION_ID),
    without ever calling _find_session_uuid, when the env var is present."""

    def setUp(self):
        self.fake = FakeRun()
        self._patched = {
            "run": sessions.subprocess.run,
            "sleep": sessions.time.sleep,
            "exists": sessions.session_exists,
            "env": sessions.get_session_env,
            "find_uuid": sessions._find_session_uuid,
            "setup": sessions.setup_session,
        }
        self.find_uuid_calls = []
        sessions.subprocess.run = self.fake
        sessions.time.sleep = lambda *_: None
        sessions.session_exists = lambda name: True
        sessions.get_session_env = lambda name, var: {
            "RC_MODE": "c", "RC_WORKDIR": "/home/user/project",
            "RC_SESSION_ID": "0d3b8b1a-1111-4a2b-9c3d-abcdef012345",
        }.get(var)
        sessions._find_session_uuid = lambda *a, **kw: (
            self.find_uuid_calls.append((a, kw)) or None
        )
        sessions.setup_session = lambda *a, **kw: None

    def tearDown(self):
        sessions.subprocess.run = self._patched["run"]
        sessions.time.sleep = self._patched["sleep"]
        sessions.session_exists = self._patched["exists"]
        sessions.get_session_env = self._patched["env"]
        sessions._find_session_uuid = self._patched["find_uuid"]
        sessions.setup_session = self._patched["setup"]

    def test_reuses_rc_session_id_without_title_scan(self):
        ok, msg = sessions.restart_session("rc-portugal")
        self.assertTrue(ok)
        self.assertEqual(self.find_uuid_calls, [])
        # The new-session tmux command must carry the reused UUID both as
        # --resume target and as --session-id.
        new_session_calls = [c for c in self.fake.calls
                              if isinstance(c, list) and "new-session" in c]
        self.assertEqual(len(new_session_calls), 1)
        cmd = new_session_calls[0]
        # resume defaults to True here, so --session-id is never combined
        # with --resume (they're mutually exclusive - see build_tmux_command);
        # the reused UUID instead lands as the standalone -e
        # RC_SESSION_ID=<uuid> argv item and embedded in the bash -c
        # claude invocation's --resume <uuid>, so check the joined command
        # line rather than list membership.
        self.assertIn("0d3b8b1a-1111-4a2b-9c3d-abcdef012345", " ".join(cmd))


class ListResumableSessionsIsTmuxIndependentTest(unittest.TestCase):
    """list_resumable_sessions must not read tmux env at all — it only
    scans ~/.claude/projects/*/*.jsonl. Pinned here so a future change
    doesn't accidentally couple it to RC_SESSION_ID."""

    def test_does_not_call_get_session_env(self):
        import unittest.mock as mock
        with mock.patch.object(sessions, "get_session_env") as m:
            sessions.list_resumable_sessions()
            m.assert_not_called()


class StripOsc8Test(unittest.TestCase):
    def test_strips_close_sequence(self):
        raw = "hello\x1b]8;;\x1b\\world"
        self.assertEqual(sessions._strip_osc8(raw), "helloworld")

    def test_open_sequence_becomes_its_url_target(self):
        raw = "status: \x1b]8;id=1dcslmk;https://claude.ai/code/session_01HuRGXzwUppFqzPmUXZQZ6J?from=cli\x1b\\/rc\x1b]8;;\x1b\\"
        cleaned = sessions._strip_osc8(raw)
        self.assertIn("https://claude.ai/code/session_01HuRGXzwUppFqzPmUXZQZ6J?from=cli", cleaned)
        # The close sequence must not leave escape-code litter behind.
        self.assertNotIn("\x1b", cleaned)

    def test_never_captures_escape_junk_as_part_of_the_url(self):
        # Regression test with the real status-bar bytes from a v2.1.263
        # session: OSC_LINK is the visible-label-only form ("/rc" is the
        # label; the URL lives in the hyperlink target, not the text).
        cleaned = sessions._strip_osc8(OSC_LINK)
        matches = re.findall(r'https://claude\.ai/code/session_[^\s\x1b]+', cleaned)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], "https://claude.ai/code/session_01FxTHitxTgkXbpbQEDJaR4R?from=cli")


class GetUrlOsc8Test(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun(pane=(
            "  ~/career-ops | Opus 5 | Tokens: 0/1.0M (0%)          " + OSC_LINK + "\n"
        ))
        self._patched_run = sessions.subprocess.run
        self._patched_env = sessions.get_session_env
        self._patched_shell = sessions.is_shell_session
        sessions.subprocess.run = self.fake
        sessions.get_session_env = lambda name, var: None
        sessions.is_shell_session = lambda name: False

    def tearDown(self):
        sessions.subprocess.run = self._patched_run
        sessions.get_session_env = self._patched_env
        sessions.is_shell_session = self._patched_shell

    def test_extracts_url_from_hyperlink_target_only(self):
        url = sessions.get_url("rc-portugal")
        self.assertEqual(url, "https://claude.ai/code/session_01FxTHitxTgkXbpbQEDJaR4R?from=cli")


class GetUrlPrefersHyperlinkTargetOverPlainTextTest(unittest.TestCase):
    """A claude.ai session URL appearing only as plain/pasted text (e.g. a
    git log attribution line quoted into the pane) must never be picked
    over — or mistaken for — the real status-bar OSC 8 hyperlink target."""

    PASTED_URL = "https://claude.ai/code/session_01AAAAAAAAAAAAAAAAAAAAAAAA?from=cli"
    STATUS_BAR_URL = "https://claude.ai/code/session_01FxTHitxTgkXbpbQEDJaR4R?from=cli"

    def setUp(self):
        self._patched_run = sessions.subprocess.run
        self._patched_env = sessions.get_session_env
        self._patched_shell = sessions.is_shell_session
        sessions.get_session_env = lambda name, var: None
        sessions.is_shell_session = lambda name: False

    def tearDown(self):
        sessions.subprocess.run = self._patched_run
        sessions.get_session_env = self._patched_env
        sessions.is_shell_session = self._patched_shell

    def test_status_bar_target_wins_over_pasted_plain_text(self):
        pane = (
            f"Co-Authored-By: someone <noreply> {self.PASTED_URL}\n"
            "  ~/career-ops | Opus 5 | Tokens: 0/1.0M (0%)          " + OSC_LINK + "\n"
        )
        self.fake = FakeRun(pane=pane)
        sessions.subprocess.run = self.fake
        url = sessions.get_url("rc-portugal")
        self.assertEqual(url, self.STATUS_BAR_URL)

    def test_pasted_only_url_is_returned_but_not_persisted(self):
        pane = f"Co-Authored-By: someone <noreply> {self.PASTED_URL}\n"
        self.fake = FakeRun(pane=pane)
        sessions.subprocess.run = self.fake
        url = sessions.get_url("rc-portugal")
        self.assertEqual(url, self.PASTED_URL)
        set_env_calls = [
            c for c in self.fake.calls
            if isinstance(c, list) and "set-environment" in c and "RC_URL" in c
        ]
        self.assertEqual(set_env_calls, [])


class GetUrlCapturePaneUsesDashETest(unittest.TestCase):
    """tmux capture-pane must run with -e, or tmux strips escape sequences
    and the OSC 8 hyperlink target branch can never fire in production."""

    def setUp(self):
        self.fake = FakeRun(pane=(
            "  ~/career-ops | Opus 5 | Tokens: 0/1.0M (0%)          " + OSC_LINK + "\n"
        ))
        self._patched_run = sessions.subprocess.run
        self._patched_env = sessions.get_session_env
        self._patched_shell = sessions.is_shell_session
        sessions.subprocess.run = self.fake
        sessions.get_session_env = lambda name, var: None
        sessions.is_shell_session = lambda name: False

    def tearDown(self):
        sessions.subprocess.run = self._patched_run
        sessions.get_session_env = self._patched_env
        sessions.is_shell_session = self._patched_shell

    def test_capture_pane_argv_includes_dash_e(self):
        sessions.get_url("rc-portugal")
        capture_calls = [c for c in self.fake.calls
                          if isinstance(c, list) and "capture-pane" in c]
        self.assertTrue(capture_calls, "capture-pane was never called")
        for c in capture_calls:
            self.assertIn("-e", c)


class SetupSessionPersistsUrlOnlyFromOsc8Test(unittest.TestCase):
    """setup_session must only write RC_URL into the tmux environment when
    the URL came from an OSC 8 hyperlink target — never a plain-text
    match, even when the status bar independently reports RC as active."""

    PASTED_URL = "https://claude.ai/code/session_01AAAAAAAAAAAAAAAAAAAAAAAA?from=cli"

    def setUp(self):
        # Status bar shows the "already active" marker but the only URL in
        # the pane is plain/pasted text (no OSC 8 hyperlink at all).
        pane = (
            "  Claude Code v2.1.259\n"
            "❯ \n"
            "Remote Control active\n"
            f"Co-Authored-By: someone <noreply> {self.PASTED_URL}\n"
        )
        self.fake = FakeRun(pane=pane)
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

    def test_text_sourced_url_is_never_persisted(self):
        sessions.setup_session("rc-portugal", "portugal", "c")
        set_env_calls = [
            c for c in self.fake.calls
            if isinstance(c, list) and "set-environment" in c and "RC_URL" in c
        ]
        self.assertEqual(set_env_calls, [])

    def test_osc8_sourced_url_is_persisted(self):
        self.fake.pane = (
            "  Claude Code v2.1.259\n"
            "❯ \n"
            "Remote Control active\n"
            "  ~/career-ops | Opus 5 | Tokens: 0/1.0M (0%)          " + OSC_LINK + "\n"
        )
        sessions.setup_session("rc-portugal", "portugal", "c")
        set_env_calls = [
            c for c in self.fake.calls
            if isinstance(c, list) and "set-environment" in c and "RC_URL" in c
        ]
        self.assertTrue(set_env_calls, "expected RC_URL to be persisted for an OSC 8 URL")


if __name__ == "__main__":
    unittest.main()
