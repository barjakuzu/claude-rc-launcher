"""Pure-helper unit tests for server.py. The request handler itself needs a
live socket to construct, so logic worth covering gets extracted into small
functions and tested directly here instead."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


if __name__ == "__main__":
    unittest.main()
