"""Tests for scheduler.py."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler


class ScheduleErrorLoggingTest(unittest.TestCase):
    def setUp(self):
        scheduler._last_logged_schedule_error = None

    def tearDown(self):
        scheduler._last_logged_schedule_error = None

    def test_first_occurrence_is_logged(self):
        msg = scheduler._schedule_error_to_log("Extra data: line 5 column 1")
        self.assertIn("Extra data", msg)

    def test_repeated_identical_error_is_not_logged_again(self):
        scheduler._schedule_error_to_log("boom")
        self.assertIsNone(scheduler._schedule_error_to_log("boom"))

    def test_a_different_error_is_logged_again(self):
        scheduler._schedule_error_to_log("boom")
        msg = scheduler._schedule_error_to_log("a different boom")
        self.assertIsNotNone(msg)

    def test_clearing_then_recurring_logs_again(self):
        scheduler._schedule_error_to_log("boom")
        scheduler._schedule_error_to_log(None)  # file loaded cleanly
        msg = scheduler._schedule_error_to_log("boom")
        self.assertIsNotNone(msg)

    def test_no_error_returns_none(self):
        self.assertIsNone(scheduler._schedule_error_to_log(None))


if __name__ == "__main__":
    unittest.main()
