import os
import re
import unittest

CONF_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "fail2ban",
    "claude-rc.conf",
)

# Permissive stand-in for fail2ban's built-in <HOST> tag (IPv4/IPv6/hostname).
HOST_PATTERN = r"(?P<host>\S+)"


def load_failregex(path):
    """Extract the `failregex` value from a fail2ban filter .conf file."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("failregex"):
                _, _, value = line.partition("=")
                return value.strip()
    raise AssertionError(f"no failregex line found in {path}")


class TestFail2banFilter(unittest.TestCase):
    def setUp(self):
        raw = load_failregex(CONF_PATH)
        self.assertIn("<HOST>", raw, "failregex should use the <HOST> tag")
        pattern = raw.replace("<HOST>", HOST_PATTERN)
        self.regex = re.compile(pattern)

    def test_matches_sample_line(self):
        sample = "AUTH FAIL ip=203.0.113.9 user=admin"
        m = self.regex.match(sample)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("host"), "203.0.113.9")

    def test_matches_sanitized_user_and_ip(self):
        # _log_safe() in server.py only ever leaves [A-Za-z0-9._@:-], truncated
        # to 64 chars, so exercise that shape too.
        sample = "AUTH FAIL ip=198.51.100.23 user=some_user@example.com"
        self.assertIsNotNone(self.regex.match(sample))

    def test_matches_ipv6_host(self):
        sample = "AUTH FAIL ip=2001:db8::1 user=root"
        m = self.regex.match(sample)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("host"), "2001:db8::1")

    def test_rejects_missing_prefix(self):
        sample = "AUTH FAILURE ip=203.0.113.9 user=admin"
        self.assertIsNone(self.regex.match(sample))

    def test_rejects_wrong_field_name(self):
        sample = "AUTH FAIL host=203.0.113.9 user=admin"
        self.assertIsNone(self.regex.match(sample))

    def test_rejects_missing_user_field(self):
        sample = "AUTH FAIL ip=203.0.113.9"
        self.assertIsNone(self.regex.match(sample))

    def test_rejects_unrelated_log_line(self):
        sample = "INFO server started on port 8080"
        self.assertIsNone(self.regex.match(sample))

    def test_rejects_empty_user(self):
        sample = "AUTH FAIL ip=203.0.113.9 user="
        self.assertIsNone(self.regex.match(sample))

    def test_rejects_trailing_junk_after_user(self):
        sample = "AUTH FAIL ip=203.0.113.9 user=admin; rm -rf /"
        self.assertIsNone(self.regex.match(sample))


if __name__ == "__main__":
    unittest.main()
