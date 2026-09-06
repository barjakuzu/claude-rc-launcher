"""Pure-helper unit tests for server.py. The request handler itself needs a
live socket to construct, so logic worth covering gets extracted into small
functions and tested directly here instead."""
import contextlib
import io
import os
import sys
import time
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import server


class _FakeCompleted:
    def __init__(self, returncode):
        self.returncode = returncode


class EnrichNextRunTest(unittest.TestCase):
    def test_manual_schedule_has_no_next_run(self):
        s = server._enrich_next_run({"enabled": True, "cron": None})
        self.assertIsNone(s["next_run"])

    def test_disabled_schedule_has_no_next_run(self):
        s = server._enrich_next_run({"enabled": False, "cron": "0 9 * * *"})
        self.assertIsNone(s["next_run"])

    def test_enabled_cron_schedule_gets_a_next_run(self):
        s = server._enrich_next_run({"enabled": True, "cron": "0 9 * * *"})
        self.assertIsNotNone(s["next_run"])

    def test_does_not_mutate_the_input(self):
        original = {"enabled": True, "cron": None}
        server._enrich_next_run(original)
        self.assertNotIn("next_run", original)


class ValidSessionNameTest(unittest.TestCase):
    def test_accepts_a_normal_rc_session_name(self):
        self.assertTrue(server._valid_session_name("rc-portugal"))

    def test_rejects_missing_prefix(self):
        self.assertFalse(server._valid_session_name("portugal"))

    def test_rejects_empty(self):
        self.assertFalse(server._valid_session_name(""))

    def test_rejects_path_traversal(self):
        self.assertFalse(server._valid_session_name("rc-../../etc/passwd"))

    def test_rejects_embedded_slash(self):
        self.assertFalse(server._valid_session_name("rc-foo/bar"))

    def test_rejects_dotdot_even_with_prefix(self):
        self.assertFalse(server._valid_session_name("rc-..secret"))


class ResolveClientIpTest(unittest.TestCase):
    def test_untrusted_peer_is_used_as_is_even_with_headers(self):
        ip = server._resolve_client_ip("203.0.113.9", "1.2.3.4", "", {"127.0.0.1"})
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_peer_uses_x_real_ip(self):
        ip = server._resolve_client_ip("127.0.0.1", "203.0.113.9", "", {"127.0.0.1"})
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_peer_falls_back_to_x_forwarded_for(self):
        ip = server._resolve_client_ip("127.0.0.1", "", "203.0.113.9, 10.0.0.1", {"127.0.0.1"})
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_peer_with_no_headers_uses_peer(self):
        ip = server._resolve_client_ip("127.0.0.1", "", "", {"127.0.0.1"})
        self.assertEqual(ip, "127.0.0.1")

    def test_default_trusted_proxies_include_loopback(self):
        self.assertIn("127.0.0.1", config.RC_TRUSTED_PROXIES)
        self.assertIn("::1", config.RC_TRUSTED_PROXIES)

    def test_trusted_peer_with_garbage_x_real_ip_falls_back_to_peer(self):
        ip = server._resolve_client_ip("127.0.0.1", "not-an-ip\n", "", {"127.0.0.1"})
        self.assertEqual(ip, "127.0.0.1")


class CookieSecureFlagTest(unittest.TestCase):
    def test_true_when_behind_tls_env_set(self):
        self.assertTrue(server._cookie_secure_flag(True, None))

    def test_true_when_forwarded_proto_is_https(self):
        self.assertTrue(server._cookie_secure_flag(False, "https"))

    def test_false_over_plain_http_with_no_tls_env(self):
        self.assertFalse(server._cookie_secure_flag(False, None))
        self.assertFalse(server._cookie_secure_flag(False, "http"))


class LogSafeTest(unittest.TestCase):
    def test_strips_disallowed_characters(self):
        self.assertEqual(server._log_safe("admin bob!"), "admin_bob_")

    def test_truncates_to_max_len(self):
        self.assertEqual(server._log_safe("a" * 100, max_len=10), "a" * 10)

    def test_allows_common_safe_characters(self):
        self.assertEqual(server._log_safe("a.b_c@d:e-9"), "a.b_c@d:e-9")


class RecordFailedLoginLogTest(unittest.TestCase):
    def test_logs_single_sanitized_line_for_crlf_and_unicode_user(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            server._record_failed_login("203.0.113.9", "admin\r\nX-Injected: 1\nüser name")
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("AUTH FAIL ip=203.0.113.9 user="))
        self.assertNotIn("\n", lines[0])
        self.assertNotIn("\r", lines[0])

    def test_logs_truncated_user(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            server._record_failed_login("203.0.113.9", "u" * 200)
        line = buf.getvalue().strip()
        user_part = line.split("user=", 1)[1]
        self.assertEqual(len(user_part), 64)


class UpdateConfirmedTest(unittest.TestCase):
    def test_matching_sha_confirms(self):
        self.assertTrue(server._update_confirmed("abc123", "abc123"))

    def test_missing_confirm_does_not_confirm(self):
        self.assertFalse(server._update_confirmed("", "abc123"))

    def test_wrong_sha_does_not_confirm(self):
        self.assertFalse(server._update_confirmed("wrong", "abc123"))

    def test_empty_remote_sha_never_confirms(self):
        self.assertFalse(server._update_confirmed("", ""))


class GitUpdatePhaseTest(unittest.TestCase):
    """The git fetch/rev-parse/log/merge sequence for /update, extracted so
    it can be exercised without a live socket or a real git repo."""

    def test_hung_fetch_times_out_cleanly(self):
        import subprocess as sp

        def fake_run(cmd, **kw):
            raise sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

        status, result, merged_sha = server._do_git_update_phase(
            "/some/app-dir", "confirm-sha", run=fake_run)

        self.assertEqual(status, 500)
        self.assertEqual(result["ok"], False)
        self.assertIn("message", result)
        self.assertNotIn("error", result)
        self.assertTrue(result["message"].startswith("git failed:"))
        self.assertIsNone(merged_sha)

    def test_missing_git_binary_returns_error_not_exception(self):
        def fake_run(cmd, **kw):
            raise OSError("git not found")

        status, result, merged_sha = server._do_git_update_phase(
            "/some/app-dir", "confirm-sha", run=fake_run)

        self.assertEqual(status, 500)
        self.assertEqual(result["ok"], False)
        self.assertIn("message", result)
        self.assertIn("git not found", result["message"])

    def test_successful_merge_returns_ok(self):
        class R:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(cmd, **kw):
            if "fetch" in cmd:
                return R()
            if "rev-parse" in cmd:
                return R(stdout="deadbeef\n")
            if "merge" in cmd:
                return R()
            return R()

        status, result, merged_sha = server._do_git_update_phase(
            "/some/app-dir", "deadbeef", run=fake_run)

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(merged_sha, "deadbeef")


class PickRestartCommandTest(unittest.TestCase):
    def test_prefers_active_system_unit(self):
        cmd = server._pick_restart_command(True, True, True, 501)
        self.assertEqual(cmd, ["systemctl", "restart", "claude-rc-launcher"])

    def test_falls_back_to_user_unit(self):
        cmd = server._pick_restart_command(False, True, True, 501)
        self.assertEqual(cmd, ["systemctl", "--user", "restart", "claude-rc"])

    def test_falls_back_to_launchd_on_macos(self):
        cmd = server._pick_restart_command(False, False, True, 501)
        self.assertEqual(cmd, ["launchctl", "kickstart", "-k", "gui/501/com.claude-rc.launcher"])

    def test_none_when_nothing_detected(self):
        self.assertIsNone(server._pick_restart_command(False, False, False, 501))


class SessionCapMessageTest(unittest.TestCase):
    def test_none_when_under_cap(self):
        self.assertIsNone(server._session_cap_message(3, 10))

    def test_message_when_at_cap(self):
        msg = server._session_cap_message(10, 10)
        self.assertIsNotNone(msg)
        self.assertIn("10", msg)

    def test_message_when_over_cap(self):
        self.assertIsNotNone(server._session_cap_message(11, 10))

    def test_default_max_sessions_is_ten(self):
        self.assertEqual(config.RC_MAX_SESSIONS, 10)

    def test_zero_disables_the_cap(self):
        self.assertIsNone(server._session_cap_message(1000, 0))

    def test_negative_disables_the_cap(self):
        self.assertIsNone(server._session_cap_message(1000, -1))


class RcMaxSessionsParsingTest(unittest.TestCase):
    """config.RC_MAX_SESSIONS parsing happens at import time, so these drive
    the parsing logic directly (mirroring what config.py itself does)
    instead of re-importing the module under different env vars."""

    def _parse(self, raw):
        try:
            return int(raw) if raw is not None else 10
        except ValueError:
            return 0

    def test_unset_defaults_to_ten(self):
        self.assertEqual(self._parse(None), 10)

    def test_invalid_string_disables_without_raising(self):
        self.assertEqual(self._parse("abc"), 0)

    def test_explicit_zero_disables(self):
        self.assertEqual(self._parse("0"), 0)

    def test_negative_parses_through_unchanged(self):
        # Parsing itself doesn't clamp negatives to 0 - consumers treat any
        # value <= 0 as "cap disabled" (see _session_cap_message and the
        # scheduler's RC_MAX_SESSIONS > 0 guard).
        self.assertEqual(self._parse("-5"), -5)


class DetectAndRestartTest(unittest.TestCase):
    def setUp(self):
        self._orig_run = server.subprocess.run
        self._orig_platform = server.sys.platform

    def tearDown(self):
        server.subprocess.run = self._orig_run
        server.sys.platform = self._orig_platform

    def test_timeout_during_detection_falls_back_to_manual_restart(self):
        def _raise_timeout(*args, **kwargs):
            raise server.subprocess.TimeoutExpired(cmd=args[0], timeout=10)
        server.subprocess.run = _raise_timeout
        self.assertEqual(server._detect_and_restart(), "Restart manually to apply the update.")

    def test_missing_systemctl_falls_back_to_manual_restart(self):
        def _raise_missing(*args, **kwargs):
            raise FileNotFoundError("systemctl not found")
        server.subprocess.run = _raise_missing
        self.assertEqual(server._detect_and_restart(), "Restart manually to apply the update.")

    def test_falls_back_to_launchd_when_systemctl_missing(self):
        # macOS: no systemctl at all (FileNotFoundError), but a registered
        # launchd agent for com.claude-rc.launcher. Detection must not stop
        # at the first failing mechanism.
        server.sys.platform = "darwin"
        uid = os.getuid()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "systemctl":
                raise FileNotFoundError("systemctl not found")
            if cmd[:2] == ["launchctl", "print"]:
                self.assertEqual(cmd[2], f"gui/{uid}/com.claude-rc.launcher")
                return _FakeCompleted(0)
            raise AssertionError("unexpected command: %r" % (cmd,))

        msg = server._detect_and_restart(run=fake_run)
        self.assertIn("launchctl kickstart -k", msg)
        self.assertIn(f"gui/{uid}/com.claude-rc.launcher", msg)
        self.assertTrue(any(cmd[:2] == ["launchctl", "print"] for cmd in calls))

    def test_manual_restart_only_when_systemd_and_launchd_all_fail(self):
        server.sys.platform = "darwin"

        def fake_run(cmd, **kw):
            return _FakeCompleted(1)  # every detection command runs but reports inactive
        self.assertEqual(server._detect_and_restart(run=fake_run),
                          "Restart manually to apply the update.")

    def test_launchd_never_probed_on_linux(self):
        # On Linux there is no launchd agent to find; the launchctl probe
        # must not even be attempted (systemd is the only mechanism tried).
        server.sys.platform = "linux"
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "launchctl":
                raise AssertionError("launchctl must not be invoked on Linux")
            return _FakeCompleted(1)

        self.assertEqual(server._detect_and_restart(run=fake_run),
                          "Restart manually to apply the update.")
        self.assertFalse(any(cmd[0] == "launchctl" for cmd in calls))

    def test_launchd_detection_timeout_falls_back_to_other_mechanisms(self):
        # A hanging `launchctl print` must not prevent detecting an active
        # user systemd unit checked before it, nor abort the whole function.
        server.sys.platform = "darwin"

        def fake_run(cmd, **kw):
            if cmd[:2] == ["systemctl", "--user"]:
                return _FakeCompleted(0)
            if cmd[0] == "systemctl":
                return _FakeCompleted(1)
            if cmd[0] == "launchctl":
                raise server.subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            raise AssertionError("unexpected command: %r" % (cmd,))
        msg = server._detect_and_restart(run=fake_run)
        self.assertIn("systemctl --user restart claude-rc", msg)


class VersionResponseTest(unittest.TestCase):
    """Uses an explicit fake dict via compat.get_caps monkeypatching
    rather than reading the mutated global compat.CAPS, so this test
    doesn't depend on whatever detection has (or hasn't) run elsewhere."""

    def test_includes_claude_version_and_caps(self):
        fake_caps = {"session_id_flag": True, "name_flag": True,
                     "remote_control_flag": False, "permission_mode_flag": False,
                     "agents_json": True, "version": "9.9.9"}
        orig_get_caps = server.compat.get_caps
        server.compat.get_caps = lambda: fake_caps
        try:
            body = server._version_response()
        finally:
            server.compat.get_caps = orig_get_caps

        self.assertEqual(body["version"], server.VERSION)
        self.assertEqual(body["claude_version"], "9.9.9")
        self.assertEqual(body["caps"], fake_caps)


class NewSessionIdTest(unittest.TestCase):
    def test_returns_a_uuid_string(self):
        import uuid
        sid = server._new_session_id()
        self.assertIsInstance(sid, str)
        # Round-trips through uuid.UUID without raising -> it's a valid UUID.
        uuid.UUID(sid)


class DeriveSessionStateTest(unittest.TestCase):
    def test_waiting_for_wins_over_everything(self):
        row = {"status": "busy", "waiting_for": "permission_prompt"}
        self.assertEqual(server._derive_session_state(row), "needs_attention")

    def test_dead_launcher_session_is_ended(self):
        row = {"status": "dead"}
        self.assertEqual(server._derive_session_state(row), "ended")

    def test_busy_status_is_busy(self):
        row = {"status": "busy", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "busy")

    def test_unknown_status_non_external_with_created_at_is_starting(self):
        row = {"status": "unknown", "created_at": time.time()}
        self.assertEqual(server._derive_session_state(row), "starting")

    def test_none_status_non_external_with_created_at_is_starting(self):
        row = {"status": None, "created_at": time.time()}
        self.assertEqual(server._derive_session_state(row), "starting")

    def test_unknown_status_non_external_without_created_at_is_idle(self):
        row = {"status": "unknown"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_none_status_non_external_without_created_at_is_idle(self):
        row = {"status": None}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_unknown_status_external_is_not_starting(self):
        row = {"status": "unknown", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_running_launcher_session_is_idle(self):
        row = {"status": "running"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_idle_external_session_is_idle(self):
        row = {"status": "idle", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "idle")

    def test_ended_external_session(self):
        row = {"status": "ended", "kind": "external"}
        self.assertEqual(server._derive_session_state(row), "ended")


class StopExternalPidTest(unittest.TestCase):
    def test_refuses_non_claude_process(self):
        import unittest.mock as mock
        with mock.patch("builtins.open", mock.mock_open(read_data=b"/usr/bin/python3\x00script.py\x00")):
            ok, reason = server._stop_external_pid(99999)
        self.assertFalse(ok)
        self.assertEqual(reason, "Not a claude process")

    def test_refuses_when_cmdline_unreadable(self):
        import unittest.mock as mock
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            ok, reason = server._stop_external_pid(99999)
        self.assertFalse(ok)
        self.assertEqual(reason, "Process not found")

    def test_stops_a_verified_claude_process(self):
        import unittest.mock as mock
        with mock.patch("builtins.open", mock.mock_open(read_data=b"/usr/local/bin/claude\x00--resume\x00")), \
             mock.patch("os.kill") as fake_kill:
            ok, reason = server._stop_external_pid(12345)
        self.assertTrue(ok)
        self.assertEqual(reason, "Stopped")
        fake_kill.assert_called_once()
        import signal
        self.assertEqual(fake_kill.call_args[0], (12345, signal.SIGTERM))


class StoppableSessionNamesTest(unittest.TestCase):
    """/stop-all must never target an external row's name with
    tmux kill-session — including when that name collides with an
    unrelated, non-rc-* tmux session that happens to exist."""

    def test_skips_external_rows(self):
        rows = [
            {"name": "rc-portugal", "external": False},
            {"name": "external-abc12345", "external": True, "session_id": "abc12345-x"},
        ]
        self.assertEqual(server._stoppable_session_names(rows), ["rc-portugal"])

    def test_name_collision_with_a_non_rc_tmux_session_is_still_skipped(self):
        # An external row's synthesized/claude-reported name can collide
        # with some unrelated tmux session name already on the box (e.g.
        # a plain shell someone opened by hand called "portugal"). It must
        # still never be passed to stop_session.
        rows = [
            {"name": "portugal", "external": True, "session_id": "xyz"},
            {"name": "rc-real", "external": False},
        ]
        self.assertEqual(server._stoppable_session_names(rows), ["rc-real"])

    def test_no_external_key_at_all_is_treated_as_launcher_owned(self):
        rows = [{"name": "rc-portugal"}]
        self.assertEqual(server._stoppable_session_names(rows), ["rc-portugal"])


class ValidateStopPidTest(unittest.TestCase):
    def test_accepts_a_normal_pid(self):
        pid, err = server._validate_stop_pid(12345)
        self.assertEqual(pid, 12345)
        self.assertIsNone(err)

    def test_rejects_non_int(self):
        pid, err = server._validate_stop_pid("not-a-pid")
        self.assertIsNone(pid)
        self.assertEqual(err, "Invalid pid")

    def test_rejects_none(self):
        pid, err = server._validate_stop_pid(None)
        self.assertIsNone(pid)
        self.assertEqual(err, "Invalid pid")

    def test_rejects_zero_and_negative(self):
        for bad in (0, -1, -12345):
            pid, err = server._validate_stop_pid(bad)
            self.assertIsNone(pid, bad)
            self.assertEqual(err, "Invalid pid", bad)

    def test_rejects_pid_1(self):
        pid, err = server._validate_stop_pid(1)
        self.assertIsNone(pid)
        self.assertEqual(err, "Invalid pid")

    def test_rejects_our_own_pid(self):
        pid, err = server._validate_stop_pid(os.getpid())
        self.assertIsNone(pid)
        self.assertEqual(err, "Invalid pid")


class DeriveSessionStateStartingBoundaryTest(unittest.TestCase):
    def test_within_grace_window_is_starting(self):
        row = {"status": "unknown", "created_at": 1000}
        self.assertEqual(
            server._derive_session_state(row, now=1000 + server.STARTING_GRACE_SECONDS - 1),
            "starting",
        )

    def test_at_or_past_grace_window_falls_back_to_idle(self):
        row = {"status": "unknown", "created_at": 1000}
        self.assertEqual(
            server._derive_session_state(row, now=1000 + server.STARTING_GRACE_SECONDS),
            "idle",
        )

    def test_missing_created_at_falls_back_to_idle(self):
        row = {"status": "unknown", "created_at": None}
        self.assertEqual(server._derive_session_state(row, now=999999), "idle")

    def test_missing_created_at_still_needs_attention_if_claude_says_so(self):
        row = {"status": "unknown", "created_at": None, "waiting_for": "permission"}
        self.assertEqual(server._derive_session_state(row, now=999999), "needs_attention")

    def test_blocked_claude_state_is_needs_attention(self):
        row = {"status": "busy", "claude": {"state": "blocked"}}
        self.assertEqual(server._derive_session_state(row), "needs_attention")

    def test_working_claude_state_does_not_force_needs_attention(self):
        row = {"status": "busy", "claude": {"state": "working"}}
        self.assertEqual(server._derive_session_state(row), "busy")


class CountLauncherSessionsCapTest(unittest.TestCase):
    """RC_MAX_SESSIONS must only count launcher-owned rows — external
    rows are informational and this launcher can't restart or reap them,
    so they must never push a real /start or /resume/start into the cap."""

    def test_external_rows_excluded_from_count(self):
        import sessions
        rows = [
            {"name": "rc-a", "external": False},
            {"name": "rc-b", "external": False},
            {"name": "external-1", "external": True},
            {"name": "external-2", "external": True},
        ]
        self.assertEqual(sessions.count_launcher_sessions(rows), 2)

    def test_empty_rows(self):
        import sessions
        self.assertEqual(sessions.count_launcher_sessions([]), 0)


class StatsSessionsCountTest(unittest.TestCase):
    def test_stats_endpoint_uses_count_launcher_sessions_not_raw_len(self):
        """/stats must report the launcher-owned session count (excluding
        external rows), consistent with count_launcher_sessions and the
        RC_MAX_SESSIONS cap — not a raw len(sess) that also counts
        external/informational rows."""
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        stats_block = src.split('elif path == "/stats":', 1)[1].split('elif path ==', 1)[0]
        self.assertIn("count_launcher_sessions(sess)", stats_block)
        self.assertNotIn("len(sess)", stats_block)

    def test_stats_endpoint_includes_launcher_version(self):
        """/stats must report this device's own launcher VERSION so the hub
        can build overview.card_from_parts()'s "version" field and flag a
        per-device mismatch against itself."""
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        stats_block = src.split('elif path == "/stats":', 1)[1].split('elif path ==', 1)[0]
        self.assertIn('s["version"] = VERSION', stats_block)


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


class GetCachedConfigReportTest(unittest.TestCase):
    def test_caches_for_60_seconds(self):
        calls = {"n": 0}

        def fake_collect(**kw):
            calls["n"] += 1
            return {"generated_at": calls["n"]}

        orig = server.configreport.collect_config_report
        server.configreport.collect_config_report = fake_collect
        try:
            clock = {"t": 1000.0}
            r1 = server._get_cached_config_report(now_fn=lambda: clock["t"])
            clock["t"] += 10
            r2 = server._get_cached_config_report(now_fn=lambda: clock["t"])
            self.assertEqual(r1, r2)
            clock["t"] += 60
            r3 = server._get_cached_config_report(now_fn=lambda: clock["t"])
            self.assertNotEqual(r1, r3)
        finally:
            server.configreport.collect_config_report = orig


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


if __name__ == "__main__":
    unittest.main()
