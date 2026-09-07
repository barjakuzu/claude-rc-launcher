import json
import os
import tempfile
import time
import unittest
from unittest import mock

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
        self.assertTrue(cursor.startswith("2026-09-07.jsonl:" + str(
            os.path.getsize(os.path.join(self.root, "2026-09-07.jsonl"))) + ":"))

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


    def test_rotation_mid_stream_reads_rotated_and_fresh_without_loss(self):
        # Simulate rc-hook's rotation: read events partway through the
        # day's file, then the file gets rotated (renamed) to ".1" and
        # a fresh file with more events is written. A cursor taken
        # before rotation must still see everything: the tail of the
        # rotated ".1" file plus the fresh file's events.
        path = _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        _, cursor = events.read_events(self.root)
        _write_day(self.root, "2026-09-07", [
            {"ts": 2, "event": "Notification", "session_id": "a", "extra": {}},
        ])
        # rc-hook rotates by a pure rename, preserving bytes/offsets.
        os.replace(path, path + ".1")
        _write_day(self.root, "2026-09-07", [
            {"ts": 3, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, cursor2 = events.read_events(self.root, since_cursor=cursor)
        self.assertEqual([r["event"] for r in rows], ["Notification", "Stop"])
        self.assertTrue(cursor2.startswith("2026-09-07.jsonl:"))
        self.assertFalse(cursor2.startswith("2026-09-07.jsonl.1"))
        # And nothing further to read from the new cursor.
        rows3, _ = events.read_events(self.root, since_cursor=cursor2)
        self.assertEqual(rows3, [])

    def test_rotation_detected_by_inode_even_after_fresh_file_grows_past_old_offset(self):
        # Important-1 fix: rotation used to be detected only by
        # "cursor_offset > size(new file)". If the fresh file grows past
        # the old cursor offset before the next read, that heuristic
        # missed the rotation entirely and reading resumed mid-line into
        # unrelated fresh content instead of draining the rotated ".1"
        # tail first. Inode tracking catches this regardless of size.
        path = _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        _, cursor = events.read_events(self.root)
        self.assertEqual(cursor.count(":"), 2)  # filename:offset:inode
        rotated_tail_event = {"ts": 2, "event": "Notification", "session_id": "a", "extra": {}}
        _write_day(self.root, "2026-09-07", [rotated_tail_event])
        os.replace(path, path + ".1")  # pure rename, preserves bytes/offsets
        # Fresh file grows well past the old cursor offset before the
        # next read -- the old size-only heuristic would treat this as
        # "no rotation" and resume mid-line into this fresh content.
        _write_day(self.root, "2026-09-07", [
            {"ts": i, "event": "Stop", "session_id": "a", "extra": {}} for i in range(3, 50)
        ])
        rows, _ = events.read_events(self.root, since_cursor=cursor, limit=1000)
        self.assertEqual(rows[0], rotated_tail_event)
        self.assertEqual(len(rows), 1 + 47)  # rotated tail + the 47 fresh rows
        # No row was dropped or corrupted by a mid-line resume.
        self.assertTrue(all(isinstance(r, dict) and "event" in r for r in rows))

    def test_stat_failure_writes_empty_inode_not_zero(self):
        # `_inode(path) or 0` used to fold a failed stat() into inode 0,
        # a *valid-looking* inode value. A later read would then compare
        # that fabricated 0 against the real current inode, see them
        # differ, and report a spurious rotation -- re-reading the ".1"
        # sibling and duplicating rows even though nothing rotated.
        # inode must come back as "" (unknown), not "0".
        _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        with mock.patch.object(events, "_inode", return_value=None):
            _, cursor = events.read_events(self.root)
        self.assertTrue(cursor.endswith(":"), cursor)
        filename, offset, inode = events._parse_cursor(cursor)
        self.assertIsNone(inode)

    def test_unknown_cursor_inode_does_not_spuriously_report_rotation(self):
        # With a cursor whose inode is unknown (as produced above), a
        # same-file read must fall back to the size heuristic instead of
        # comparing against a fabricated inode 0 -- so no duplicate rows
        # even when an unrelated ".1" sibling happens to exist.
        path = _write_day(self.root, "2026-09-07", [
            {"ts": 1, "event": "SessionStart", "session_id": "a", "extra": {}},
        ])
        with mock.patch.object(events, "_inode", return_value=None):
            _, cursor = events.read_events(self.root)
        self.assertTrue(cursor.endswith(":"), cursor)
        # An unrelated rotated sibling from some earlier day's rollover.
        with open(path + ".1", "w") as f:
            f.write(json.dumps({"ts": 0, "event": "Stop", "session_id": "old", "extra": {}}) + "\n")
        _write_day(self.root, "2026-09-07", [
            {"ts": 2, "event": "Stop", "session_id": "a", "extra": {}},
        ])
        rows, _ = events.read_events(self.root, since_cursor=cursor)
        self.assertEqual([r["event"] for r in rows], ["Stop"])
        self.assertEqual(rows[0]["session_id"], "a")


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

    def test_prune_deletes_rotated_dot1_file_past_cutoff(self):
        # Critical fix: _spool_files widened to include "<date>.jsonl.1"
        # but prune() used to strip only ".jsonl", so strptime("2026-08-
        # 01.jsonl", ...) failed and rotated files were skipped forever.
        path = _write_day(self.root, "2026-08-01", [
            {"ts": 1, "event": "Stop", "session_id": "a", "extra": {}}])
        os.replace(path, path + ".1")
        deleted = events.prune(self.root, days=7, now_fn=lambda: time.mktime(
            time.strptime("2026-09-07", "%Y-%m-%d")))
        self.assertEqual(deleted, ["2026-08-01.jsonl.1"])
        self.assertEqual(os.listdir(self.root), [])


if __name__ == "__main__":
    unittest.main()
