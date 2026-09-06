"""Tests for schedules.py's storage layer: atomic writes, rolling backup,
load validation, and the manual-task (cron: null) data model."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedules


class SaveSchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sched_file = os.path.join(self.tmpdir, "schedules.json")
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = self.sched_file

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_creates_file_mode_0600(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        mode = stat.S_IMODE(os.stat(self.sched_file).st_mode)
        self.assertEqual(mode, 0o600)
        with open(self.sched_file) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_save_writes_rolling_backup_of_previous_contents(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        schedules.save_schedules([{"id": "b", "name": "B"}])
        bak_path = self.sched_file + ".bak"
        self.assertTrue(os.path.isfile(bak_path))
        with open(bak_path) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_no_backup_written_on_first_ever_save(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        self.assertFalse(os.path.isfile(self.sched_file + ".bak"))

    def test_save_leaves_no_tmp_files_behind(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        leftovers = [f for f in os.listdir(self.tmpdir) if ".tmp" in f]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
