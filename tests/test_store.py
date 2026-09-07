import json
import os
import sqlite3
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

    def test_upsert_sessions_round_trips_full_role_fields(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "external",
             "state": "idle", "started_at": 1000, "external": True, "pid": 4242,
             "tmux": {"session_name": "rc-foo", "pane_id": "%3"},
             "rc_url": "https://claude.ai/code/session_abc", "tokens": 12345,
             "claude": {"status": "busy", "waitingFor": "permission", "state": "blocked",
                        "sessionId": "s1", "pid": 4242}},
        ], now_fn=lambda: 1010.0)
        view = self.store.fleet_view()
        self.assertEqual(len(view["sessions"]), 1)
        row = view["sessions"][0]
        self.assertEqual(row["pid"], 4242)
        self.assertEqual(row["tmux"], {"session_name": "rc-foo", "pane_id": "%3"})
        self.assertEqual(row["rc_url"], "https://claude.ai/code/session_abc")
        self.assertEqual(row["tokens"], 12345)
        self.assertEqual(row["kind"], "external")
        self.assertEqual(row["cwd"], "/tmp")
        self.assertEqual(row["external"], 1)
        self.assertEqual(row["claude"], {"status": "busy", "waitingFor": "permission",
                                          "state": "blocked", "sessionId": "s1", "pid": 4242})

    def test_upsert_sessions_metadata_role_row_keeps_new_fields_absent(self):
        self.store.upsert_device({"id": "dev1", "name": "meta-box", "role": "metadata",
                                   "version": "1", "claude_version": "1"})
        # A metadata-role device's /rc/fleet rows carry only session_id,
        # name, state, started_at, kind -- pid/tmux/rc_url/tokens/claude
        # are simply absent, not empty strings.
        self.store.upsert_sessions("dev1", [
            {"session_id": "hashed123", "name": "hashedname", "state": "idle",
             "started_at": 1000, "kind": "interactive"},
        ], now_fn=lambda: 1010.0)
        view = self.store.fleet_view()
        row = [s for s in view["sessions"] if s["session_id"] == "hashed123"][0]
        self.assertIsNone(row["pid"])
        self.assertIsNone(row["tmux"])
        self.assertIsNone(row["rc_url"])
        self.assertIsNone(row["tokens"])
        self.assertIsNone(row["claude"])

    def test_existing_db_created_before_new_columns_gains_them_without_data_loss(self):
        # Simulate a hub.db created by a version of store.py before this
        # migration: schema has no pid/tmux/rc_url/tokens/claude columns.
        old_db = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(old_db)
        conn.executescript("""
            CREATE TABLE devices (
                id TEXT PRIMARY KEY, name TEXT, role TEXT, version TEXT,
                claude_version TEXT, last_seen REAL, online INTEGER DEFAULT 0
            );
            CREATE TABLE sessions (
                device_id TEXT, session_id TEXT, name TEXT, cwd TEXT, kind TEXT,
                state TEXT, started_at REAL, ended_at REAL, last_seen REAL,
                external INTEGER DEFAULT 0,
                PRIMARY KEY (device_id, session_id)
            );
            CREATE TABLE session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, session_id TEXT,
                ts REAL, event TEXT, extra_json TEXT
            );
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT,
                target TEXT, device_id TEXT, detail TEXT
            );
        """)
        conn.execute(
            "INSERT INTO devices (id, name, role, version, claude_version, last_seen, online) "
            "VALUES ('local', 'hub', 'full', '1', '1', 100.0, 1)")
        conn.execute(
            "INSERT INTO sessions (device_id, session_id, name, cwd, kind, state, started_at, "
            "ended_at, last_seen, external) VALUES "
            "('local', 's1', 'rc-old', '/tmp', 'interactive', 'idle', 100.0, NULL, 100.0, 0)")
        conn.commit()
        conn.close()
        os.chmod(old_db, 0o600)

        migrated = store.Store(old_db)
        try:
            view = migrated.fleet_view()
            self.assertEqual(len(view["devices"]), 1)
            self.assertEqual(view["devices"][0]["name"], "hub")
            self.assertEqual(len(view["sessions"]), 1)
            self.assertEqual(view["sessions"][0]["name"], "rc-old")
            self.assertIsNone(view["sessions"][0]["pid"])
            self.assertIsNone(view["sessions"][0]["tmux"])
            # New columns are usable going forward.
            migrated.upsert_sessions("local", [
                {"session_id": "s1", "name": "rc-old", "cwd": "/tmp", "kind": "interactive",
                 "state": "idle", "pid": 555},
            ])
            view2 = migrated.fleet_view()
            self.assertEqual(view2["sessions"][0]["pid"], 555)
        finally:
            migrated.close()

    def test_realistic_pre_v3_launcher_row_round_trips_not_skipped(self):
        """A launcher row as produced by sessions.list_rc_sessions() for a
        session with no RC_SESSION_ID (the pre-v3 case, no claude row
        attached either) now carries the synthetic tmux:<name>
        session_id -- it must round-trip into fleet_view(), not be
        silently skipped the way a session_id-less row is."""
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        launcher_row = {
            "session_id": "tmux:rc-jobs-lin", "name": "rc-jobs-lin", "mode": "c",
            "url": None, "status": "unknown", "created_at": 1700000000,
            "workdir": "/home/user/proj", "project": "proj", "cwd": "/home/user/proj",
            "kind": "interactive",
        }
        result = self.store.upsert_sessions("local", [launcher_row], now_fn=lambda: 1010.0)
        self.assertEqual(result["skipped"], 0)
        view = self.store.fleet_view()
        row = [s for s in view["sessions"] if s["session_id"] == "tmux:rc-jobs-lin"]
        self.assertEqual(len(row), 1)
        self.assertIsNone(row[0]["ended_at"])
        self.assertEqual(row[0]["name"], "rc-jobs-lin")

    def test_synthetic_launcher_session_id_stable_across_consecutive_upserts(self):
        """The synthetic tmux:<name> id must stay identical across polls,
        or the hub store would end and recreate the session every cycle
        instead of tracking one continuous session."""
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        row = {"session_id": "tmux:rc-jobs-lin", "name": "rc-jobs-lin", "cwd": "/tmp",
               "kind": "interactive", "state": "idle"}
        self.store.upsert_sessions("local", [row], now_fn=lambda: 1000.0)
        view1 = self.store.fleet_view()
        self.store.upsert_sessions("local", [row], now_fn=lambda: 1030.0)
        view2 = self.store.fleet_view()
        self.assertEqual(len(view1["sessions"]), 1)
        self.assertEqual(len(view2["sessions"]), 1)
        self.assertEqual(view1["sessions"][0]["session_id"], view2["sessions"][0]["session_id"])
        self.assertEqual(view1["sessions"][0]["started_at"], view2["sessions"][0]["started_at"])
        self.assertIsNone(view2["sessions"][0]["ended_at"])

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
