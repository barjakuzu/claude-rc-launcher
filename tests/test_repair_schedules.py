"""Tests for tools/repair_schedules.py. Never run this tool against a real
schedules.json from a test - only against temp-directory fixtures."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import repair_schedules


class RepairSchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def test_recovers_array_with_trailing_stray_bracket(self):
        self._write('[{"id": "a", "name": "A"}]]')
        ok = repair_schedules.repair(self.path)
        self.assertTrue(ok)
        with open(self.path) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_backs_up_original_before_rewriting(self):
        self._write('[{"id": "a"}]]')
        repair_schedules.repair(self.path)
        backups = [f for f in os.listdir(self.tmpdir) if f.startswith("schedules.json.corrupt-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmpdir, backups[0])) as f:
            self.assertEqual(f.read(), '[{"id": "a"}]]')

    def test_clean_file_is_left_untouched(self):
        self._write('[{"id": "a"}]')
        before = os.stat(self.path).st_mtime_ns
        ok = repair_schedules.repair(self.path)
        self.assertTrue(ok)
        after = os.stat(self.path).st_mtime_ns
        self.assertEqual(before, after)
        backups = [f for f in os.listdir(self.tmpdir) if "corrupt" in f]
        self.assertEqual(backups, [])

    def test_unrecoverable_garbage_is_left_untouched(self):
        self._write('not json at all {{{')
        ok = repair_schedules.repair(self.path)
        self.assertFalse(ok)
        with open(self.path) as f:
            self.assertEqual(f.read(), 'not json at all {{{')

    def test_recovered_value_that_is_not_a_list_is_left_untouched(self):
        self._write('{"a": 1}extra')
        ok = repair_schedules.repair(self.path)
        self.assertFalse(ok)

    def test_recovered_file_is_mode_0600(self):
        self._write('[{"id": "a"}]]')
        repair_schedules.repair(self.path)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_backup_file_is_mode_0600(self):
        self._write('[{"id": "a"}]]')
        repair_schedules.repair(self.path)
        backups = [f for f in os.listdir(self.tmpdir) if f.startswith("schedules.json.corrupt-")]
        self.assertEqual(len(backups), 1)
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmpdir, backups[0])).st_mode)
        self.assertEqual(mode, 0o600)

    def test_recovers_array_when_file_starts_with_leading_whitespace(self):
        self._write('\n  [{"id": "a", "name": "A"}]]')
        ok = repair_schedules.repair(self.path)
        self.assertTrue(ok)
        with open(self.path) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])


if __name__ == "__main__":
    unittest.main()
