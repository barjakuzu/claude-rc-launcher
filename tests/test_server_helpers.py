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


if __name__ == "__main__":
    unittest.main()
