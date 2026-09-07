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
import store


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
    def test_sends_remote_control_then_enter_targeting_pane_id(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("server.get_url_with_source", return_value=("https://claude.ai/code/session_x", "osc8")):
            result = server._enable_rc_for_adopted("mysession", "%7", run=fake_run, sleep=lambda s: None)
        self.assertEqual(result, {"ok": True, "url": "https://claude.ai/code/session_x"})
        # Both send-keys calls target the pane_id, never the session name.
        self.assertEqual(calls[0], ["tmux", "send-keys", "-t", "%7", "-l", "/remote-control"])
        self.assertEqual(calls[1], ["tmux", "send-keys", "-t", "%7", "Enter"])

    def test_send_keys_failure_reports_message_without_polling(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr="no such session")
        with mock.patch("server.get_url_with_source") as guws:
            result = server._enable_rc_for_adopted("mysession", "%7", run=fake_run, sleep=lambda s: None)
        self.assertFalse(result["ok"])
        self.assertIn("message", result)
        guws.assert_not_called()

    def test_polls_until_osc8_url_appears(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")
        responses = [(None, None), (None, "text"), ("https://claude.ai/code/session_y", "osc8")]
        with mock.patch("server.get_url_with_source", side_effect=responses):
            result = server._enable_rc_for_adopted("mysession", "%7", run=fake_run, sleep=lambda s: None)
        self.assertEqual(result, {"ok": True, "url": "https://claude.ai/code/session_y"})

    def test_times_out_after_20_seconds_of_polling(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")
        clock = {"t": 0.0}

        def now_fn():
            return clock["t"]

        def sleep(s):
            clock["t"] += s

        with mock.patch("server.get_url_with_source", return_value=(None, None)):
            result = server._enable_rc_for_adopted("mysession", "%7", run=fake_run, sleep=sleep, now_fn=now_fn)
        self.assertFalse(result["ok"])
        self.assertIn(str(server.ENABLE_RC_POLL_SECONDS), result["message"])

    def test_success_invalidates_the_adoption_url_cache(self):
        fake_run = lambda cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("server.get_url_with_source", return_value=("https://claude.ai/code/session_x", "osc8")), \
             mock.patch("server.invalidate_adopted_url_cache") as inv:
            server._enable_rc_for_adopted("mysession", "%7", run=fake_run, sleep=lambda s: None)
        # Once right after typing /remote-control, once again on success —
        # so a poller reading list_rc_sessions right after this returns
        # never serves a stale cached miss.
        self.assertEqual(inv.call_count, 2)
        inv.assert_called_with("mysession")

    def test_route_rejects_non_adopted_name(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        self.assertIn('endswith("/enable-rc")', src)
        block = src.split('endswith("/enable-rc")', 1)[1][:600]
        self.assertIn("_adopted_tmux_names(rows)", block)

    def test_route_resolves_pane_id_from_adoption_record_not_body(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/enable-rc")', 1)[1][:800]
        self.assertIn("_adopted_pane_id(name, rows)", block)
        self.assertNotIn("body", block)


class ValidPaneIdTest(unittest.TestCase):
    def test_accepts_well_formed_pane_id(self):
        self.assertTrue(server._valid_pane_id("%7"))
        self.assertTrue(server._valid_pane_id("%123"))

    def test_rejects_none(self):
        self.assertFalse(server._valid_pane_id(None))

    def test_rejects_malformed_value(self):
        self.assertFalse(server._valid_pane_id("7"))
        self.assertFalse(server._valid_pane_id("%"))
        self.assertFalse(server._valid_pane_id("%7; rm -rf /"))
        self.assertFalse(server._valid_pane_id(""))
        self.assertFalse(server._valid_pane_id("mysession"))

    def test_route_validates_pane_id_before_use(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/enable-rc")', 1)[1][:900]
        self.assertIn("_valid_pane_id(pane_id)", block)
        # The validation must happen before pane_id is handed to the
        # enable-rc backing logic.
        self.assertLess(block.index("_valid_pane_id(pane_id)"),
                         block.index("_enable_rc_for_adopted(name, pane_id)"))


class AdoptedPaneIdTest(unittest.TestCase):
    def test_returns_pane_id_for_adopted_row(self):
        rows = [{"external": True, "tmux": {"session_name": "mysession", "pane_id": "%7"}}]
        self.assertEqual(server._adopted_pane_id("mysession", rows), "%7")

    def test_returns_none_when_not_adopted(self):
        rows = [{"external": True, "tmux": {"session_name": "other", "pane_id": "%3"}}]
        self.assertIsNone(server._adopted_pane_id("mysession", rows))


class EnableRcInFlightGuardTest(unittest.TestCase):
    def setUp(self):
        server._enable_rc_in_flight.clear()

    def tearDown(self):
        server._enable_rc_in_flight.clear()

    def test_second_concurrent_call_is_rejected_with_409(self):
        import threading as th

        release = th.Event()
        started = th.Event()
        results = []

        def slow_run(cmd, **kw):
            if cmd[:2] == ["tmux", "send-keys"] and "-l" in cmd:
                started.set()
                release.wait(timeout=5)
            return mock.Mock(returncode=0, stdout="", stderr="")

        def worker():
            with server._enable_rc_in_flight_lock:
                if "mysession" in server._enable_rc_in_flight:
                    results.append(("rejected", 409))
                    return
                server._enable_rc_in_flight["mysession"] = time.time()
            try:
                with mock.patch("server.get_url_with_source", return_value=("https://claude.ai/code/session_x", "osc8")):
                    r = server._enable_rc_for_adopted("mysession", "%7", run=slow_run, sleep=lambda s: None)
                results.append(("done", r))
            finally:
                with server._enable_rc_in_flight_lock:
                    server._enable_rc_in_flight.pop("mysession", None)

        t1 = th.Thread(target=worker)
        t1.start()
        started.wait(timeout=5)
        # A second call while the first is mid-flight must see the
        # in-flight marker and be rejected before doing any tmux work.
        with server._enable_rc_in_flight_lock:
            in_flight = "mysession" in server._enable_rc_in_flight
        self.assertTrue(in_flight)
        release.set()
        t1.join(timeout=5)
        self.assertNotIn("mysession", server._enable_rc_in_flight)


class PreviewByeGuardTest(unittest.TestCase):
    def test_preview_bye_route_uses_session_name_allowed(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/preview-bye")', 1)[1][:400]
        self.assertIn("_session_name_allowed(name)", block)


class SessionNameAllowedBehavioralTest(unittest.TestCase):
    """A behavioural (not getsource-based) check that the wiring actually
    rejects a non-adopted, non-rc-* name end to end, not just that the
    right identifier appears in the route source."""

    def test_non_adopted_non_rc_name_is_actually_rejected(self):
        orig = server.list_rc_sessions
        server.list_rc_sessions = lambda: []
        try:
            self.assertFalse(server._session_name_allowed("some-random-name"))
        finally:
            server.list_rc_sessions = orig


class AdoptedWindowSizeTest(unittest.TestCase):
    def setUp(self):
        import sessions
        sessions._adopted_window_size_cache.clear()

    def test_capture_reads_current_window_size_once(self):
        import sessions
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="132x44\n", stderr="")

        size1 = sessions.capture_adopted_window_size("mysession", run=fake_run)
        size2 = sessions.capture_adopted_window_size("mysession", run=fake_run)
        self.assertEqual(size1, (132, 44))
        self.assertEqual(size2, (132, 44))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["tmux", "display", "-p", "-t", "mysession",
                                     "#{window_width}x#{window_height}"])

    def test_restore_returns_launcher_default_for_rc_session(self):
        import sessions
        self.assertEqual(sessions.restore_window_size("rc-portugal"), (200, 50))

    def test_restore_returns_captured_size_for_adopted_session(self):
        import sessions
        sessions.capture_adopted_window_size(
            "mysession", run=lambda cmd, **kw: mock.Mock(returncode=0, stdout="90x30", stderr=""))
        self.assertEqual(sessions.restore_window_size("mysession"), (90, 30))

    def test_restore_returns_none_when_capture_failed(self):
        import sessions
        sessions.capture_adopted_window_size(
            "mysession", run=lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr="error"))
        self.assertIsNone(sessions.restore_window_size("mysession"))

    def test_restore_returns_none_when_never_captured(self):
        import sessions
        self.assertIsNone(sessions.restore_window_size("mysession"))

    def test_apply_preview_size_captures_before_first_resize_for_adopted_session(self):
        with mock.patch("server.capture_adopted_window_size") as cap, \
             mock.patch("server.restore_window_size", return_value=(90, 30)), \
             mock.patch("server.subprocess.run", return_value=mock.Mock(returncode=0)):
            server._preview_viewers.pop("mysession", None)
            server._preview_applied.pop("mysession", None)
            server._apply_preview_size("mysession")
        cap.assert_called_once_with("mysession")

    def test_apply_preview_size_skips_resize_when_no_captured_size(self):
        with mock.patch("server.capture_adopted_window_size"), \
             mock.patch("server.restore_window_size", return_value=None), \
             mock.patch("server.subprocess.run") as run:
            server._preview_viewers.pop("mysession", None)
            server._preview_applied.pop("mysession", None)
            server._apply_preview_size("mysession")
        run.assert_not_called()

    def test_apply_preview_size_still_resizes_rc_session_to_default(self):
        with mock.patch("server.subprocess.run", return_value=mock.Mock(returncode=0)) as run:
            server._preview_viewers.pop("rc-portugal", None)
            server._preview_applied.pop("rc-portugal", None)
            server._apply_preview_size("rc-portugal")
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0],
                          ["tmux", "resize-window", "-t", "rc-portugal", "-x", "200", "-y", "50"])

    def test_apply_preview_size_never_captures_for_rc_session(self):
        with mock.patch("server.capture_adopted_window_size") as cap, \
             mock.patch("server.subprocess.run", return_value=mock.Mock(returncode=0)):
            server._preview_viewers.pop("rc-portugal", None)
            server._preview_applied.pop("rc-portugal", None)
            server._apply_preview_size("rc-portugal")
        cap.assert_not_called()


class KeysTargetTest(unittest.TestCase):
    """server._keys_target: adopted external rows must be addressed by
    their validated pane_id, never the tmux session name, while rc-*
    launcher rows keep the session-name target."""

    def test_rc_session_targets_its_own_name_without_consulting_rows(self):
        # No rows passed at all — proves the rc-* branch never looks up
        # adoption state.
        self.assertEqual(server._keys_target("rc-portugal"), "rc-portugal")

    def test_adopted_session_targets_its_validated_pane_id(self):
        rows = [{"external": True, "tmux": {"session_name": "mysession", "pane_id": "%7"}}]
        self.assertEqual(server._keys_target("mysession", rows), "%7")

    def test_adopted_session_with_malformed_pane_id_falls_back_to_name(self):
        rows = [{"external": True, "tmux": {"session_name": "mysession", "pane_id": "not-a-pane"}}]
        self.assertEqual(server._keys_target("mysession", rows), "mysession")

    def test_not_currently_adopted_falls_back_to_name(self):
        rows = [{"external": True, "tmux": {"session_name": "other", "pane_id": "%3"}}]
        self.assertEqual(server._keys_target("mysession", rows), "mysession")

    def test_lazily_computes_rows_when_not_passed(self):
        fake_rows = [{"external": True, "tmux": {"session_name": "mysession", "pane_id": "%9"}}]
        orig = server.list_rc_sessions
        server.list_rc_sessions = lambda: fake_rows
        try:
            self.assertEqual(server._keys_target("mysession"), "%9")
        finally:
            server.list_rc_sessions = orig


class KeysRouteTargetingTest(unittest.TestCase):
    """POST /sessions/<name>/keys must send-keys against _keys_target(name),
    not the raw path-derived name, so an adopted row's keystrokes land on
    its pane_id."""

    def test_keys_route_sends_to_keys_target_not_raw_name(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/keys"):', 1)[1]
        block = block[:block.index("elif path.startswith(\"/sessions/\") and path.endswith(\"/enable-rc\")")]
        self.assertIn("target = _keys_target(name)", block)
        self.assertIn('["tmux", "send-keys", "-t", target, *special]', block)
        self.assertIn('["tmux", "send-keys", "-t", target, "-l", keys]', block)
        self.assertNotIn('"-t", name, *special', block)
        self.assertNotIn('"-t", name, "-l", keys', block)

    def test_ws_route_passes_keys_target_to_serve_terminal(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split("endswith(\"/ws\"):", 1)[1][:1200]
        self.assertIn("ws_terminal.serve_terminal(self, name, keys_target=_keys_target(name))", block)


class WsServeTerminalKeysTargetTest(unittest.TestCase):
    """ws.serve_terminal's own send-keys calls must address keys_target
    (defaulting to `name` when the caller passes none), not `name` itself
    — the control-mode attach line is the sole exception and stays on
    `name`."""

    def test_send_keys_blocks_use_keys_target_variable(self):
        import inspect
        import ws
        src = inspect.getsource(ws.serve_terminal)
        self.assertIn('def serve_terminal(handler, name, keys_target=None):', src)
        self.assertIn('["tmux", "send-keys", "-t", keys_target, "-l", str(msg["keys"])]', src)
        self.assertIn('["tmux", "send-keys", "-t", keys_target, *keys]', src)
        # The control-mode attach itself stays targeted by session name.
        self.assertIn('["tmux", "-C", "attach-session", "-t", name]', src)

    def test_keys_target_defaults_to_name_when_falsy(self):
        import inspect
        import ws
        self.assertIn("keys_target = keys_target if keys_target else name",
                       inspect.getsource(ws.serve_terminal))


class ResizeCapturesAdoptedWindowSizeTest(unittest.TestCase):
    """POST /sessions/<name>/resize must capture an adopted session's
    pre-adoption window size before ever resizing it, even when /resize is
    the very first request for that session (no prior /preview or /ws)."""

    def test_resize_route_captures_before_resizing(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        block = src.split('endswith("/resize"):', 1)[1]
        block = block[:block.index('elif path.startswith("/sessions/") and path.endswith("/keys")')]
        capture_idx = block.index("capture_adopted_window_size(name)")
        resize_idx = block.index('"tmux", "resize-window"')
        self.assertLess(capture_idx, resize_idx)
        self.assertIn("if not name.startswith(SESSION_PREFIX):", block)

    def test_first_resize_on_adopted_name_captures_size_before_resize_window(self):
        import sessions
        sessions._adopted_window_size_cache.clear()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[:3] == ["tmux", "display", "-p"]:
                return mock.Mock(returncode=0, stdout="132x44\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        # Simulate exactly what the /resize handler now does: capture
        # first (guarded by the SESSION_PREFIX check), then resize.
        name = "mysession"
        if not name.startswith(server.SESSION_PREFIX):
            sessions.capture_adopted_window_size(name, run=fake_run)
        fake_run(["tmux", "resize-window", "-t", name, "-x", "100", "-y", "40"])

        self.assertEqual(calls[0], ["tmux", "display", "-p", "-t", "mysession",
                                     "#{window_width}x#{window_height}"])
        self.assertEqual(sessions._adopted_window_size_cache.get("mysession"), (132, 44))
        # A later restore (e.g. when the last viewer disconnects) now
        # returns the size captured before /resize ever touched the window.
        self.assertEqual(sessions.restore_window_size("mysession"), (132, 44))


class FleetRouteTest(unittest.TestCase):
    def test_fleet_added_to_should_proxy_exemption_and_log_quiet_list(self):
        import inspect
        src = inspect.getsource(server)
        self.assertIn('"/rc/fleet"', src)


class DeriveNeedsAttentionTest(unittest.TestCase):
    # CONTROLLER RULING supersedes the original task-8 brief here: the
    # hook set was cut to StopFailure/Notification/SubagentStop/
    # PreCompact/SessionEnd, so Stop/UserPromptSubmit never appear and
    # can't be the clearing signal. _derive_needs_attention now only
    # returns a per-session override when the newest relevant event is
    # Notification (True) or SessionEnd (False); other event types are
    # not decisive. The base value (no override) comes from the store's
    # polled session state, applied by _apply_needs_attention.
    def test_notification_is_a_decisive_override(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "Notification", "extra": {}},
        ]
        result = server._derive_needs_attention(events)
        self.assertTrue(result["s1"])

    def test_session_end_after_notification_clears_override(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "Notification", "extra": {}},
            {"session_id": "s1", "ts": 11, "event": "SessionEnd", "extra": {}},
        ]
        result = server._derive_needs_attention(events)
        self.assertFalse(result["s1"])

    def test_non_decisive_event_types_are_skipped_when_scanning_back(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "Notification", "extra": {}},
            {"session_id": "s1", "ts": 11, "event": "StopFailure", "extra": {}},
            {"session_id": "s1", "ts": 12, "event": "SubagentStop", "extra": {}},
            {"session_id": "s1", "ts": 13, "event": "PreCompact", "extra": {}},
        ]
        result = server._derive_needs_attention(events)
        self.assertTrue(result["s1"])

    def test_no_decisive_event_leaves_session_absent(self):
        events = [
            {"session_id": "s1", "ts": 10, "event": "PreCompact", "extra": {}},
        ]
        result = server._derive_needs_attention(events)
        self.assertNotIn("s1", result)


class ApplyNeedsAttentionTest(unittest.TestCase):
    def test_polled_needs_attention_state_is_the_base(self):
        sessions_rows = [{"session_id": "s1", "state": "needs_attention"}]
        server._apply_needs_attention(sessions_rows, [])
        self.assertTrue(sessions_rows[0]["needs_attention"])

    def test_notification_event_overrides_idle_polled_state(self):
        sessions_rows = [{"session_id": "s1", "state": "idle"}]
        events = [{"session_id": "s1", "ts": 10, "event": "Notification", "extra": {}}]
        server._apply_needs_attention(sessions_rows, events)
        self.assertTrue(sessions_rows[0]["needs_attention"])

    def test_session_end_event_overrides_stale_needs_attention_state(self):
        sessions_rows = [{"session_id": "s1", "state": "needs_attention"}]
        events = [{"session_id": "s1", "ts": 10, "event": "SessionEnd", "extra": {}}]
        server._apply_needs_attention(sessions_rows, events)
        self.assertFalse(sessions_rows[0]["needs_attention"])


class ApiFleetRouteTest(unittest.TestCase):
    def test_api_fleet_is_hub_only_not_proxied(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/api/fleet"
        self.assertFalse(h._should_proxy("some-device"))

    def test_api_fleet_stream_is_hub_only_not_proxied(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/api/fleet/stream"
        self.assertFalse(h._should_proxy("some-device"))

    def test_api_audit_is_hub_only_not_proxied(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/api/audit"
        self.assertFalse(h._should_proxy("some-device"))

    def test_api_sessions_events_is_hub_only_not_proxied(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/api/sessions/local/s1/events"
        self.assertFalse(h._should_proxy("some-device"))


class ApiFleetStreamHeadersTest(unittest.TestCase):
    def test_stream_sets_no_buffering_headers(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split('"/api/fleet/stream"', 1)[1]
        block = block[:2000]
        self.assertIn('text/event-stream', block)
        self.assertIn('X-Accel-Buffering', block)
        self.assertIn('no-cache', block)
        self.assertIn('keep-alive', block)

    def test_heartbeat_interval_constant_is_20_seconds(self):
        self.assertEqual(server.SSE_HEARTBEAT_SECONDS, 20)

    def test_heartbeat_is_a_data_frame_not_a_comment(self):
        # A ": ping" SSE comment line never reaches the browser's
        # EventSource.onmessage, so the client's staleness watchdog can't
        # tell "no changes" from "connection silently died" and settles
        # into permanent polling on an idle-but-healthy stream. The
        # heartbeat must be a real "data:" frame instead.
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split('"/api/fleet/stream"', 1)[1]
        block = block[:3000]
        self.assertNotIn(': ping', block)
        self.assertIn('"type": "heartbeat"', block)
        self.assertIn('data: {heartbeat}', block)


class MetadataRoleGatingTest(unittest.TestCase):
    def setUp(self):
        server.config.RC_ROLE = "metadata"

    def tearDown(self):
        server.config.RC_ROLE = "full"

    def test_metadata_role_refuses_start_with_403(self):
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        self.assertIn("RC_ROLE", src)

    def test_metadata_allowed_paths_constant_matches_spec(self):
        self.assertEqual(
            server.METADATA_ALLOWED_GET_PATHS,
            {"/fleet", "/version", "/stats", "/config-report"})

    def test_get_enforcement_present_in_do_get(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        self.assertIn("RC_ROLE", src)
        self.assertIn("METADATA_ALLOWED_GET_PATHS", src)

    def test_post_refused_prefixes_constant_matches_spec(self):
        self.assertEqual(
            server.METADATA_REFUSED_POST_PATHS_PREFIXES,
            ("/start", "/keys", "/resize", "/enable-rc", "/schedules"))


class MetadataPostRefusedHelperTest(unittest.TestCase):
    """Unit tests for the pure route-classification helper."""

    REFUSED_PATHS = (
        "/sessions/abc/keys",
        "/sessions/abc/resize",
        "/sessions/abc/enable-rc",
        "/sessions/abc/preview-bye",
        "/resume/start",
        "/tunnel/start",
        "/tunnel/stop",
        "/devices/rename",
        "/update",
    )

    def test_refuses_every_spec_path(self):
        for path in self.REFUSED_PATHS:
            self.assertTrue(server._metadata_post_refused(path), path)

    def test_allows_read_only_paths(self):
        for path in ("/fleet", "/version", "/stats", "/config-report", "/status"):
            self.assertFalse(server._metadata_post_refused(path), path)


class MetadataPostGateEndToEndTest(unittest.TestCase):
    """Full do_POST round trip through the RC_ROLE=metadata gate."""

    PATHS = MetadataPostRefusedHelperTest.REFUSED_PATHS

    def setUp(self):
        self._role = server.config.RC_ROLE

    def tearDown(self):
        server.config.RC_ROLE = self._role

    def _make_handler(self, path):
        h = server.Handler.__new__(server.Handler)
        h.path = path
        h.headers = {}
        h.client_address = ("127.0.0.1", 12345)
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        return h

    def test_every_path_returns_403_under_metadata_role(self):
        server.config.RC_ROLE = "metadata"
        for path in self.PATHS:
            with mock.patch.object(server, "_check_auth", return_value=True):
                h = self._make_handler(path)
                captured = {}

                def fake_json(data, code=200, _captured=captured):
                    _captured["code"] = code
                    return None

                h._json = fake_json
                h.do_POST()
                self.assertEqual(captured.get("code"), 403, path)

    def test_every_path_bypasses_gate_under_full_role(self):
        server.config.RC_ROLE = "full"

        class _ReachedRouting(Exception):
            pass

        for path in self.PATHS:
            with mock.patch.object(server, "_check_auth", return_value=True):
                h = self._make_handler(path)

                def fake_target_device():
                    raise _ReachedRouting()

                h._target_device = fake_target_device
                with self.assertRaises(_ReachedRouting, msg=path):
                    h.do_POST()


class SseCapacityTest(unittest.TestCase):
    def setUp(self):
        server.FLEET_CHANGE_SUBSCRIBERS.clear()

    def tearDown(self):
        server.FLEET_CHANGE_SUBSCRIBERS.clear()

    def test_not_exceeded_below_the_cap(self):
        import threading
        for _ in range(server.SSE_MAX_SUBSCRIBERS - 1):
            server.FLEET_CHANGE_SUBSCRIBERS.add(threading.Event())
        self.assertFalse(server._sse_capacity_exceeded())

    def test_exceeded_at_the_cap(self):
        import threading
        for _ in range(server.SSE_MAX_SUBSCRIBERS):
            server.FLEET_CHANGE_SUBSCRIBERS.add(threading.Event())
        self.assertTrue(server._sse_capacity_exceeded())

    def test_route_responds_503_over_the_cap(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split('"/api/fleet/stream"', 1)[1][:1500]
        self.assertIn("503", block)
        self.assertIn("SSE_MAX_SUBSCRIBERS", block)


class SseSnapshotStoreFailureTest(unittest.TestCase):
    """Minor fix: _sse_send_fleet_snapshot must not raise into the
    request-handling thread when the store read itself fails (as
    opposed to the client having disconnected)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        server.HUB_STORE = store.Store(os.path.join(self.tmp.name, "hub.db"))

    def tearDown(self):
        server.HUB_STORE.close()
        server.HUB_STORE = None
        self.tmp.cleanup()

    def test_store_failure_returns_none_without_raising(self):
        # Tri-state: None (not False) on a store-side error -- False is
        # reserved for an actual client disconnect. A caller checking
        # `is False` correctly keeps the subscriber on a store hiccup.
        class FakeWfile:
            def write(self, data):
                pass

            def flush(self):
                pass

        h = server.Handler.__new__(server.Handler)
        h.wfile = FakeWfile()
        with mock.patch.object(server.HUB_STORE, "fleet_view", side_effect=RuntimeError("db hiccup")):
            result = h._sse_send_fleet_snapshot()  # must not raise
        self.assertIsNone(result)
        self.assertIsNot(result, False)

    def test_client_disconnect_returns_false(self):
        class DisconnectingWfile:
            def write(self, data):
                raise BrokenPipeError()

            def flush(self):
                pass

        h = server.Handler.__new__(server.Handler)
        h.wfile = DisconnectingWfile()
        self.assertIs(h._sse_send_fleet_snapshot(), False)

    def test_sse_stream_loop_does_not_stop_on_store_error(self):
        # Regression for the bug itself: the do_GET stream loop must use
        # `is False`, not a truthiness check, or a None (store-error)
        # return is indistinguishable from False and the subscriber gets
        # dropped on a transient store hiccup.
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        block = src.split('"/api/fleet/stream"', 1)[1][:2500]
        self.assertIn("_sse_send_fleet_snapshot() is False", block)
        self.assertNotIn("if not self._sse_send_fleet_snapshot()", block)


class NotifyFleetChangedTest(unittest.TestCase):
    def test_notify_sets_all_subscriber_events(self):
        import threading
        server.FLEET_CHANGE_SUBSCRIBERS.clear()
        ev1, ev2 = threading.Event(), threading.Event()
        server.FLEET_CHANGE_SUBSCRIBERS.add(ev1)
        server.FLEET_CHANGE_SUBSCRIBERS.add(ev2)
        server.notify_fleet_changed()
        self.assertTrue(ev1.is_set())
        self.assertTrue(ev2.is_set())


class ApiSessionEventsRouteTest(unittest.TestCase):
    def test_route_calls_store_recent_events_with_device_and_session(self):
        import inspect
        src = inspect.getsource(server.Handler.do_GET)
        self.assertIn('"/api/sessions/"', src)
        self.assertIn('.recent_events(', src)


class AuditLogTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        server.HUB_STORE = store.Store(os.path.join(self.tmp.name, "hub.db"))

    def tearDown(self):
        if server.HUB_STORE is not None:
            server.HUB_STORE.close()
        server.HUB_STORE = None
        self.tmp.cleanup()

    def test_audit_writes_row_with_resolved_actor(self):
        class FakeHandler:
            headers = {"Cookie": "rc_session=tok_abc123"}
        server._audit(FakeHandler(), action="start", target="rc-foo", detail="mode=c")
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "start")
        self.assertEqual(rows[0]["target"], "rc-foo")
        self.assertNotIn("password", rows[0]["detail"])

    def test_audit_never_raises_when_store_is_none(self):
        server.HUB_STORE.close()
        server.HUB_STORE = None

        class FakeHandler:
            headers = {}
        server._audit(FakeHandler(), action="stop", target="rc-foo")  # must not raise

    def test_audit_actor_is_basic_for_basic_auth(self):
        class FakeHandler:
            headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        server._audit(FakeHandler(), action="stop", target="rc-foo")
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(rows[0]["actor"], "basic")

    def test_all_named_mutating_routes_call_audit(self):
        # Each marker is the literal text that opens the named route's
        # elif branch in do_POST -- some routes are exact-path matches
        # ("/start"), others are startswith/endswith matches for a path
        # under /sessions/<name>/... ("/keys", "/enable-rc").
        import inspect
        src = inspect.getsource(server.Handler.do_POST)
        markers = {
            "/start": 'if path == "/start"',
            "/stop": 'elif path == "/stop"',
            "/stop-all": 'elif path == "/stop-all"',
            "/restart": 'elif path == "/restart"',
            "/unstick": 'elif path == "/unstick"',
            "/keys": 'endswith("/keys")',
            "/enable-rc": 'endswith("/enable-rc")',
            "/schedules (create)": 'elif path == "/schedules"',
            "/schedules/update": 'elif path == "/schedules/update"',
            "/schedules/delete": 'elif path == "/schedules/delete"',
            "/schedules/fire": 'elif path == "/schedules/fire"',
            "/update": 'elif path == "/update"',
            "/devices/rename": 'elif path == "/devices/rename"',
            # Important-4 fix: the phase mandate is every mutating route,
            # not just the brief's (incomplete) list.
            "/resume/start": 'elif path == "/resume/start"',
            "/tunnel/start": 'elif path == "/tunnel/start"',
            "/tunnel/stop": 'elif path == "/tunnel/stop"',
        }
        for name, marker in markers.items():
            block = src.split(marker, 1)
            self.assertEqual(len(block), 2, f"route {name} not found in do_POST")
            next_block = block[1].split("\n        elif ", 1)[0]
            self.assertIn("_audit(", next_block, f"{name} branch missing _audit() call")


class _ApiRouteFixture(unittest.TestCase):
    """Shared fixture for every GET /api/* route test below: a real Store
    on a temp DB, auth mocked out, and a helper that drives do_GET()
    through a bare Handler and captures whatever it hands to _json().
    Defines no test_* methods of its own, so it contributes nothing when
    collected directly -- only its subclasses run."""

    def setUp(self):
        self._role = server.config.RC_ROLE
        server.config.RC_ROLE = "full"
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        server.HUB_STORE = store.Store(os.path.join(self.tmp.name, "hub.db"))
        self._orig_get_cached = server._get_cached_config_report

    def tearDown(self):
        server.config.RC_ROLE = self._role
        server.HUB_STORE.close()
        server.HUB_STORE = None
        self.tmp.cleanup()
        server._get_cached_config_report = self._orig_get_cached

    def _make_handler(self, path):
        h = server.Handler.__new__(server.Handler)
        h.path = path
        h.headers = {}
        h.client_address = ("127.0.0.1", 12345)
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        return h

    def _get_json(self, path):
        h = self._make_handler(path)
        captured = {}

        def fake_json(data, code=200, _captured=captured):
            _captured["data"] = data
            _captured["code"] = code
            return None

        h._json = fake_json
        with mock.patch.object(server, "_check_auth", return_value=True):
            h.do_GET()
        self.assertIn("data", captured, f"{path} did not reach a _json() response")
        return captured["data"], captured.get("code", 200)


class ApiRouteQueryStringToleranceTest(_ApiRouteFixture):
    """Regression test: the hub's exact-match GET routes must match on the
    query-stripped path, not on self.path verbatim. A request carrying a
    query string (as the SPA's /api/audit?limit=N call always does) used to
    404 because these routes compared against a path that still had the
    query string attached."""

    def test_api_fleet_ok_with_and_without_query_string(self):
        for path in ("/api/fleet", "/api/fleet?x=1"):
            data, code = self._get_json(path)
            self.assertEqual(code, 200, path)
            self.assertIn("devices", data, path)
            self.assertIn("sessions", data, path)

    def test_api_audit_ok_with_and_without_query_string(self):
        for path in ("/api/audit", "/api/audit?limit=5"):
            data, code = self._get_json(path)
            self.assertEqual(code, 200, path)
            self.assertIn("audit", data, path)

    def test_api_audit_honours_limit_query_param(self):
        for i in range(5):
            server.HUB_STORE.add_audit(actor="tok", action=f"action{i}",
                                        target="rc-foo", device_id="local")
        data, code = self._get_json("/api/audit?limit=2")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["audit"]), 2)

    def test_api_config_matrix_ok_with_and_without_query_string(self):
        server._get_cached_config_report = lambda: {}
        with mock.patch.object(server.overview, "build_config_matrix",
                                return_value={"ok": True}):
            for path in ("/api/config-matrix", "/api/config-matrix?x=1"):
                data, code = self._get_json(path)
                self.assertEqual(code, 200, path)
                self.assertEqual(data, {"ok": True}, path)

    def test_api_cost_ok_with_and_without_query_string(self):
        for path in ("/api/cost", "/api/cost?days=7"):
            data, code = self._get_json(path)
            self.assertEqual(code, 200, path)
            self.assertIn("devices", data, path)
            self.assertIn("projects", data, path)
            self.assertIn("sessions", data, path)
            self.assertIn("totals", data, path)

    def test_api_alerts_ok_with_and_without_query_string(self):
        for path in ("/api/alerts", "/api/alerts?x=1"):
            data, code = self._get_json(path)
            self.assertEqual(code, 200, path)
            self.assertIn("alerts", data, path)
            self.assertIn("summary", data, path)
            self.assertIn("config_error", data, path)


class ApiFleetUsageFieldsTest(_ApiRouteFixture):
    """CONTRACT.md section 3: GET /api/fleet gains usage_age_seconds per
    session and usage_partial per device."""

    def test_usage_age_seconds_present_and_null_when_no_usage(self):
        server.HUB_STORE.upsert_device({"id": "local", "name": "hub", "role": "full",
                                         "version": "1", "claude_version": "1"})
        server.HUB_STORE.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "launcher",
             "state": "idle", "started_at": 1000.0},
        ])
        server.HUB_STORE.upsert_session_usage("local", [
            {"session_id": "s1", "effective": 100, "last_ts": time.time() - 30},
        ])
        data, code = self._get_json("/api/fleet")
        self.assertEqual(code, 200)
        by_id = {s["session_id"]: s for s in data["sessions"]}
        self.assertIn("usage_age_seconds", by_id["s1"])
        self.assertGreaterEqual(by_id["s1"]["usage_age_seconds"], 30)

    def test_usage_age_seconds_null_when_usage_absent(self):
        server.HUB_STORE.upsert_device({"id": "local", "name": "hub", "role": "full",
                                         "version": "1", "claude_version": "1"})
        server.HUB_STORE.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "launcher",
             "state": "idle", "started_at": 1000.0},
        ])
        data, code = self._get_json("/api/fleet")
        self.assertEqual(code, 200)
        self.assertIsNone(data["sessions"][0]["usage_age_seconds"])

    def test_usage_partial_is_a_real_bool(self):
        server.HUB_STORE.upsert_device({"id": "local", "name": "hub", "role": "full",
                                         "version": "1", "claude_version": "1",
                                         "usage_partial": True})
        data, code = self._get_json("/api/fleet")
        self.assertEqual(code, 200)
        dev = next(d for d in data["devices"] if d["id"] == "local")
        self.assertIs(dev["usage_partial"], True)


class ApiCostRouteTest(_ApiRouteFixture):
    """Exercises /api/cost's behavior beyond bare query-string tolerance
    (covered by ApiRouteQueryStringToleranceTest above) -- days
    validation, the happy path with real rows, and the empty-store case."""

    def test_days_absent_defaults_to_30(self):
        data, _ = self._get_json("/api/cost")
        self.assertEqual(data["days"], 30)

    def test_days_non_numeric_defaults_rather_than_500(self):
        data, code = self._get_json("/api/cost?days=banana")
        self.assertEqual(code, 200)
        self.assertEqual(data["days"], 30)

    def test_days_negative_clamps_to_minimum(self):
        data, code = self._get_json("/api/cost?days=-5")
        self.assertEqual(code, 200)
        self.assertEqual(data["days"], 1)

    def test_days_zero_clamps_to_minimum(self):
        data, code = self._get_json("/api/cost?days=0")
        self.assertEqual(code, 200)
        self.assertEqual(data["days"], 1)

    def test_days_absurdly_large_clamps_to_maximum(self):
        data, code = self._get_json("/api/cost?days=99999999999999999999")
        self.assertEqual(code, 200)
        self.assertEqual(data["days"], 365)

    def test_empty_store_returns_empty_shape_not_an_error(self):
        data, code = self._get_json("/api/cost")
        self.assertEqual(code, 200)
        self.assertEqual(data["devices"], [])
        self.assertEqual(data["projects"], [])
        self.assertEqual(data["sessions"], [])
        self.assertEqual(data["totals"],
                          {"effective": 0, "input": 0, "cache_read": 0,
                           "cache_write": 0, "output": 0})

    def test_happy_path_totals_summed_from_daily_not_projects(self):
        server.HUB_STORE.upsert_device({"id": "local", "name": "hub", "role": "full",
                                         "version": "1", "claude_version": "1"})
        server.HUB_STORE.upsert_cost_daily("local", [
            {"day": time.strftime("%Y-%m-%d", time.gmtime()), "project": "-var-www",
             "input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "effective": 1000},
        ])
        data, code = self._get_json("/api/cost?days=30")
        self.assertEqual(code, 200)
        self.assertEqual(data["totals"]["effective"], 1000)
        dev = next(d for d in data["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 1000)
        projects = {p["project"]: p["effective"] for p in data["projects"]}
        self.assertEqual(projects.get("-var-www"), 1000)


class ApiAlertsRouteTest(_ApiRouteFixture):
    """Exercises /api/alerts's behavior beyond bare query-string tolerance
    (covered by ApiRouteQueryStringToleranceTest above)."""

    def test_empty_store_returns_empty_alerts_not_an_error(self):
        data, code = self._get_json("/api/alerts")
        self.assertEqual(code, 200)
        self.assertEqual(data["alerts"], [])
        self.assertEqual(data["summary"], {"alert": 0, "warn": 0, "rules": {}})

    def test_happy_path_returns_live_alerts_and_summary(self):
        server.HUB_STORE.replace_alerts([
            {"rule": "token_rate", "severity": "alert", "target_type": "session",
             "device_id": "local", "session_id": "s1", "name": "rc-foo",
             "message": "m", "value": 1.0, "threshold": 2.0, "since": 100.0},
        ], now_fn=lambda: 1000.0)
        data, code = self._get_json("/api/alerts")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["alerts"]), 1)
        self.assertEqual(data["alerts"][0]["rule"], "token_rate")
        self.assertEqual(data["summary"], {"alert": 1, "warn": 0, "rules": {"token_rate": 1}})

    def test_config_error_surfaces_guard_last_load_error(self):
        with mock.patch.object(server.guard, "LAST_LOAD_ERROR", "boom: bad guard.json"):
            data, code = self._get_json("/api/alerts")
        self.assertEqual(code, 200)
        self.assertEqual(data["config_error"], "boom: bad guard.json")


class ShouldProxyQueryStringTest(unittest.TestCase):
    def test_should_proxy_classifies_correctly_with_query_string(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/some/other/path?x=1"
        self.assertTrue(h._should_proxy("some-device"))

    def test_should_proxy_hub_only_route_not_proxied_with_query_string(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/api/audit?limit=5"
        self.assertFalse(h._should_proxy("some-device"))


class ProxiedRequestAuditTest(unittest.TestCase):
    """A mutating POST forwarded to a remote device is audited at the hub,
    same as a local mutating POST -- the hub is the only place that ever
    sees these actions happen, so it's the only place that can log them."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_store = server.HUB_STORE
        server.HUB_STORE = store.Store(os.path.join(self.tmp.name, "hub.db"))
        self.device = {"id": "dev1", "base_url": "http://10.0.0.5:9999",
                        "auth_user": "", "auth_pass": ""}
        self._get_device_patch = mock.patch.object(
            server, "get_device", return_value=self.device)
        self._get_device_patch.start()
        self._check_auth_patch = mock.patch.object(
            server, "_check_auth", return_value=True)
        self._check_auth_patch.start()

    def tearDown(self):
        self._get_device_patch.stop()
        self._check_auth_patch.stop()
        if server.HUB_STORE is not None:
            server.HUB_STORE.close()
        server.HUB_STORE = self._orig_store
        self.tmp.cleanup()

    def _make_handler(self, method_path, body_bytes, headers=None):
        h = server.Handler.__new__(server.Handler)
        h.path = method_path
        h.headers = dict(headers or {})
        h.headers["Content-Length"] = str(len(body_bytes))
        h.headers["X-RC-Device"] = "dev1"
        h.client_address = ("127.0.0.1", 12345)
        h.rfile = io.BytesIO(body_bytes)
        h.wfile = io.BytesIO()
        h.command = "POST"
        h.send_response = lambda *a, **kw: None
        h.send_header = lambda *a, **kw: None
        h.end_headers = lambda *a, **kw: None
        return h

    def _fake_response(self, payload=b'{"ok": true}', status=200):
        resp = mock.MagicMock()
        resp.read.return_value = payload
        resp.status = status
        resp.headers = {"Content-Type": "application/json"}
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        return resp

    def test_proxied_mutating_post_writes_one_audit_row_with_device_and_proxied_marker(self):
        body = b'{"name": "rc-foo"}'
        h = self._make_handler("/rc/stop", body)
        with mock.patch.object(server.urllib.request, "urlopen",
                                return_value=self._fake_response()):
            h.do_POST()
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["device_id"], "dev1")
        self.assertEqual(rows[0]["target"], "rc-foo")
        self.assertIn("proxied", rows[0]["detail"])

    def test_proxied_resume_start_records_session_id_as_target(self):
        body = b'{"session_id": "abc-123", "title": "My Session"}'
        h = self._make_handler("/rc/resume/start", body)
        with mock.patch.object(server.urllib.request, "urlopen",
                                return_value=self._fake_response()):
            h.do_POST()
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "abc-123")

    def test_proxied_schedules_update_prefers_id_over_name(self):
        body = b'{"id": "42", "name": "should-not-win"}'
        h = self._make_handler("/rc/schedules/update", body)
        with mock.patch.object(server.urllib.request, "urlopen",
                                return_value=self._fake_response()):
            h.do_POST()
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "42")

    def test_proxied_get_writes_no_audit_row(self):
        h = self._make_handler("/rc/stop", b"")
        h.command = "GET"
        with mock.patch.object(server.urllib.request, "urlopen",
                                return_value=self._fake_response()):
            h.do_GET()
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 0)

    def test_proxied_mutating_post_succeeds_when_audit_write_raises(self):
        body = b'{"name": "rc-foo"}'
        h = self._make_handler("/rc/stop", body)
        captured = {}

        def fake_json(data, code=200, _captured=captured):
            _captured["data"] = data
            _captured["code"] = code

        h._json = fake_json
        with mock.patch.object(server.HUB_STORE, "add_audit",
                                side_effect=RuntimeError("boom")):
            with mock.patch.object(server.urllib.request, "urlopen",
                                    return_value=self._fake_response()):
                h.do_POST()
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 0)
        # The proxied request itself still went through -- it wasn't
        # blocked or failed by the audit write raising.
        self.assertEqual(len(h.wfile.getvalue()), len(b'{"ok": true}'))

    def test_local_post_still_writes_exactly_one_row_no_double_audit(self):
        h = server.Handler.__new__(server.Handler)
        h.path = "/rc/stop"
        body = b'{"name": "rc-foo"}'
        h.headers = {"Content-Length": str(len(body))}
        h.client_address = ("127.0.0.1", 12345)
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        h.command = "POST"

        def fake_json(data, code=200):
            return None

        h._json = fake_json
        with mock.patch.object(server, "session_exists", return_value=False):
            h.do_POST()
        rows = server.HUB_STORE.recent_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["device_id"], "local")
        self.assertNotIn("proxied", rows[0]["detail"])


if __name__ == "__main__":
    unittest.main()
