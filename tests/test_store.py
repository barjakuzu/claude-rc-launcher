import os
import tempfile
import time
import unittest

import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_db_file_created_0600(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "2.1.7", "claude_version": "2.1.263"})
        self.assertTrue(os.path.exists(self.db_path))
        self.assertEqual(oct(os.stat(self.db_path).st_mode & 0o777), "0o600")

    def test_upsert_device_then_appears_in_fleet_view(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "2.1.7", "claude_version": "2.1.263"})
        view = self.store.fleet_view()
        self.assertEqual(len(view["devices"]), 1)
        self.assertEqual(view["devices"][0]["name"], "hub")

    def test_upsert_sessions_then_reupsert_without_a_row_marks_it_ended(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 1000},
        ], now_fn=lambda: 1010.0)
        view = self.store.fleet_view()
        self.assertEqual(len(view["sessions"]), 1)
        self.assertIsNone(view["sessions"][0]["ended_at"])

        self.store.upsert_sessions("local", [], now_fn=lambda: 1020.0)
        view2 = self.store.fleet_view()
        self.assertEqual(view2["sessions"][0]["ended_at"], 1020.0)

    def test_add_events_then_recent_events_by_session(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.add_events("local", [
            {"ts": 1000.0, "event": "SessionStart", "session_id": "s1", "extra": {"source": "startup"}},
            {"ts": 1001.0, "event": "Stop", "session_id": "s1", "extra": {}},
            {"ts": 1002.0, "event": "Stop", "session_id": "other", "extra": {}},
        ])
        rows = self.store.recent_events(session_id="s1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], "Stop")  # newest first
        self.assertEqual(rows[1]["extra"], {"source": "startup"})

    def test_add_audit_then_recent_audit(self):
        self.store.add_audit(actor="tok_abc", action="start", target="rc-foo",
                              device_id="local", detail="mode=c")
        rows = self.store.recent_audit(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "start")
        self.assertEqual(rows[0]["actor"], "tok_abc")

    def test_prune_removes_old_events_and_audit(self):
        self.store.add_events("local", [{"ts": 1.0, "event": "Stop", "session_id": "s1", "extra": {}}])
        self.store.add_audit(actor="a", action="b", target="c", device_id="local", detail="",
                              now_fn=lambda: 1.0)
        deleted = self.store.prune(days=1, now_fn=lambda: 1.0 + 2 * 86400)
        self.assertGreaterEqual(deleted["events"], 1)
        self.assertGreaterEqual(deleted["audit_log"], 1)
        self.assertEqual(self.store.recent_events(session_id="s1"), [])

    def test_concurrent_writes_do_not_raise(self):
        import threading
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        errors = []

        def writer(n):
            try:
                for i in range(20):
                    self.store.add_events("local", [
                        {"ts": float(i), "event": "Stop", "session_id": f"s{n}", "extra": {}}
                    ])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
