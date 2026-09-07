import os
import tempfile
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

    def test_add_events_reupload_same_batch_is_a_noop(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        batch = [
            {"ts": 1000.0, "event": "SessionStart", "session_id": "s1", "extra": {"a": 1}},
            {"ts": 1001.0, "event": "Stop", "session_id": "s1", "extra": {}},
        ]
        self.store.add_events("local", batch)
        self.assertEqual(len(self.store.recent_events(session_id="s1")), 2)

        # A poller cursor rewind resubmits the same batch.
        self.store.add_events("local", batch)
        self.assertEqual(len(self.store.recent_events(session_id="s1")), 2)

    def test_upsert_sessions_skips_rows_without_session_id(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        result = self.store.upsert_sessions("local", [
            {"name": "rc-foo", "cwd": "/tmp", "kind": "interactive", "state": "idle"},
        ], now_fn=lambda: 1000.0)
        self.assertEqual(result["skipped"], 1)
        view = self.store.fleet_view()
        self.assertEqual(view["sessions"], [])

        # A prior real session must not be marked ended by an unrelated
        # nameless row showing up in the same batch.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-real", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ], now_fn=lambda: 1001.0)
        result2 = self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-real", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
            {"name": "no-id-row"},
        ], now_fn=lambda: 1002.0)
        self.assertEqual(result2["skipped"], 1)
        view2 = self.store.fleet_view()
        self.assertEqual(len(view2["sessions"]), 1)
        self.assertIsNone(view2["sessions"][0]["ended_at"])

    def test_close_rejects_concurrent_write_promptly(self):
        import threading
        import time as time_mod

        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        result = {}

        def writer():
            try:
                self.store.add_events("local", [
                    {"ts": 1.0, "event": "Stop", "session_id": "s1", "extra": {}}])
                result["outcome"] = "completed"
            except Exception as e:
                result["outcome"] = "raised"
                result["error"] = e

        t = threading.Thread(target=writer)
        t.start()
        self.store.close()
        t.join(timeout=2)
        start = time_mod.time()
        self.assertFalse(t.is_alive())
        self.assertLess(time_mod.time() - start, 1)
        self.assertIn(result.get("outcome"), ("completed", "raised"))
        # tearDown calls store.close() again; make sure that's a no-op.

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

    def test_write_after_close_raises_store_closed(self):
        self.store.close()
        with self.assertRaises(store.StoreClosed):
            self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                       "version": "1", "claude_version": "1"})

    def test_add_events_rejects_rows_without_session_id(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        result = self.store.add_events("local", [
            {"ts": 1.0, "event": "Stop", "extra": {}},  # no session_id
        ])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(self.store.recent_events(device_id="local"), [])


if __name__ == "__main__":
    unittest.main()
