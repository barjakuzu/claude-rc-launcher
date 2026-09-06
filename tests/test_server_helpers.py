"""Pure-helper unit tests for server.py. The request handler itself needs a
live socket to construct, so logic worth covering gets extracted into small
functions and tested directly here instead."""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import server


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

    def tearDown(self):
        server.subprocess.run = self._orig_run

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

    def test_unknown_status_non_external_is_starting(self):
        row = {"status": "unknown"}
        self.assertEqual(server._derive_session_state(row), "starting")

    def test_none_status_non_external_is_starting(self):
        row = {"status": None}
        self.assertEqual(server._derive_session_state(row), "starting")

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


if __name__ == "__main__":
    unittest.main()
