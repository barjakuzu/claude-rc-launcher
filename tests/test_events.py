import json
import os
import tempfile
import time
import unittest

import events


def _write_day(root, date_str, rows):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{date_str}.jsonl")
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


class ReadEventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_all_rows_when_no_cursor(self):
        _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
            {"ts": 2, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor = events.read_events(self.root)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], "SessionStart")
        self.assertEqual(cursor, "2026-09-07.jsonl:" + str(
            os.path.getsize(os.path.join(self.root, "2026-09-07.jsonl"))))

    def test_cursor_resumes_from_byte_offset(self):
        _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        _, cursor = events.read_events(self.root)
        _write_day(self.root, "2026-09-07", [
            {"ts": 2, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor2 = events.read_events(self.root, since_cursor=cursor)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "Stop")
        self.assertNotEqual(cursor, cursor2)

    def test_rotation_across_day_boundary_reads_new_file_from_start(self):
        _write_day(self.root, "2026-09-06", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        _, cursor = events.read_events(self.root)
        _write_day(self.root, "2026-09-07", [
            {"ts": 100000, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor2 = events.read_events(self.root, since_cursor=cursor)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "Stop")
        self.assertTrue(cursor2.startswith("2026-09-07.jsonl:"))

    def test_truncated_last_line_is_skipped_not_raised(self):
        path = _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        with open(path, "a") as f:
            f.write('{"ts": 2, "event": "Stop"')  # no closing brace/newline
        rows, cursor = events.read_events(self.root)
        self.assertEqual(len(rows), 1)

    def test_limit_caps_rows_and_cursor_stays_mid_stream(self):
        _write_day(self.root, "2026-09-07", [
            {"ts": i, "event": "Stop", "session_id": "a", "extra": {}} for i in range(10)
        ])
        rows, cursor = events.read_events(self.root, limit=3)
        self.assertEqual(len(rows), 3)
        rows2, _ = events.read_events(self.root, since_cursor=cursor, limit=100)
        self.assertEqual(len(rows2), 7)

    def test_missing_root_returns_empty(self):
        rows, cursor = events.read_events(os.path.join(self.root, "nope"))
        self.assertEqual(rows, [])
        self.assertIsNone(cursor)

    def test_truncated_last_line_does_not_skip_ahead_at_day_rollover(self):
        # A truncated line in the current file must stop the whole scan
        # (not just that file), so the cursor can't skip past it even
        # when a newer day's file already exists.
        path = _write_day(self.root, "2026-09-06", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        with open(path, "a") as f:
            f.write('{"ts": 2, "event": "Stop"')  # truncated, no newline
        _write_day(self.root, "2026-09-07", [
            {"ts": 3, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor = events.read_events(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "SessionStart")
        self.assertTrue(cursor.startswith("2026-09-06.jsonl:"))


class PruneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_prune_deletes_old_files_keeps_recent(self):
        _write_day(self.root, "2026-08-01", [{"ts": 1, "event": "Stop", "session_id": "a", "extra": {}}])
        _write_day(self.root, "2026-09-06", [{"ts": 1, "event": "Stop", "session_id": "a", "extra": {}}])
        deleted = events.prune(self.root, days=7, now_fn=lambda: time.mktime(
            time.strptime("2026-09-07", "%Y-%m-%d")))
        self.assertEqual(deleted, ["2026-08-01.jsonl"])
        self.assertEqual(sorted(os.listdir(self.root)), ["2026-09-06.jsonl"])


if __name__ == "__main__":
    unittest.main()
