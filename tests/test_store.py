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
        # Default fleet_view() excludes ended sessions -- the live
        # Sessions tab must not show them forever.
        view2 = self.store.fleet_view()
        self.assertEqual(view2["sessions"], [])
        # They stay queryable with include_ended=True (activity/history).
        view2_all = self.store.fleet_view(include_ended=True)
        self.assertEqual(view2_all["sessions"][0]["ended_at"], 1020.0)

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


class UpsertSessionsStartedAtTest(unittest.TestCase):
    """upsert_sessions: a valid reported started_at WINS OUTRIGHT over
    whatever is already stored, regardless of which is earlier -- min()
    was tried and rejected here too (see store.upsert_sessions' docstring):
    restart-in-place reuses the session id, so the row never goes through
    "ended" between the restart and the next poll, and a stale existing
    value would otherwise permanently outrank the new session's own,
    correct, more recent started_at. Existing is used only when THIS
    poll's reported value is not usable. 0, negative, NaN and inf all
    count as "not set", the same as None."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _started_at(self):
        return self.store.fleet_view()["sessions"][0]["started_at"]

    def test_existing_null_and_reported_null_falls_back_to_now(self):
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ], now_fn=lambda: 500.0)
        self.assertEqual(self._started_at(), 500.0)

    def test_existing_null_reported_set_takes_reported(self):
        # First sighting has no started_at at all (lands on now), the
        # backfill path below covers moving it back to a real value --
        # this test is the plain "reported arrives already set" case.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 300.0},
        ], now_fn=lambda: 500.0)
        self.assertEqual(self._started_at(), 300.0)

    def test_existing_set_reported_null_keeps_existing(self):
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 300.0},
        ], now_fn=lambda: 500.0)
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ], now_fn=lambda: 600.0)
        self.assertEqual(self._started_at(), 300.0)

    def test_reported_wins_over_an_older_existing_value(self):
        # The reported value (300.0) is EARLIER than what's already stored
        # (900.0) -- still wins outright, same as any other reported value.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 900.0},
        ], now_fn=lambda: 1000.0)
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 300.0},
        ], now_fn=lambda: 1100.0)
        self.assertEqual(self._started_at(), 300.0)

    def test_reported_wins_over_a_newer_existing_value_restart_in_place(self):
        # The exact restart-in-place bug from fix round 2: the row keeps
        # its session id across a restart (never goes "ended"), so a stale
        # existing value (300.0, from long before the restart) would
        # otherwise permanently outrank the new session's own, later,
        # started_at (9999.0) if it lost a min() comparison. It must not:
        # the later reported value wins outright and moves the stored
        # value FORWARD, replacing the stale one.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 300.0},
        ], now_fn=lambda: 1000.0)
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 9999.0},
        ], now_fn=lambda: 1100.0)
        self.assertEqual(self._started_at(), 9999.0)

    def test_reported_zero_is_treated_as_not_set(self):
        # upsert_sessions itself must never let a bare 0 win -- it falls
        # back to now, the same as reported being absent entirely.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 0},
        ], now_fn=lambda: 1000.0)
        self.assertEqual(self._started_at(), 1000.0)

    def test_stored_zero_loses_to_a_real_reported_timestamp(self):
        # Simulate a legacy row that already has a bare 0 stored directly
        # (bypassing upsert_sessions, which would never write one itself)
        # -- a real reported timestamp must still win outright, per the
        # brief's explicit "a stored 0 must not win" requirement.
        def _insert_raw(conn):
            conn.execute(
                "INSERT INTO sessions (device_id, session_id, name, cwd, kind, state, "
                "started_at, ended_at, last_seen, external) VALUES "
                "('local','s1','rc-a','/tmp','interactive','idle',0,NULL,1000.0,0)")
        self.store._write(_insert_raw)
        self.assertEqual(self._started_at(), 0)

        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 300.0},
        ], now_fn=lambda: 1100.0)
        self.assertEqual(self._started_at(), 300.0)

    def test_reported_negative_nan_inf_all_treated_as_not_set(self):
        for bad_value in (-5.0, float("nan"), float("inf")):
            with self.subTest(bad_value=bad_value):
                self.store.upsert_sessions("local", [
                    {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
                     "state": "idle", "started_at": 300.0},
                ], now_fn=lambda: 1000.0)
                self.store.upsert_sessions("local", [
                    {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
                     "state": "idle", "started_at": bad_value},
                ], now_fn=lambda: 1100.0)
                # existing (300.0) stays -- the bad reported value never wins.
                self.assertEqual(self._started_at(), 300.0)

    def test_backfill_path_moves_stored_value_back_to_a_real_earlier_timestamp(self):
        """A row inserted with no started_at lands on `now` (a
        first-sighting guess). A later upsert reporting the real (earlier)
        claude/tmux timestamp must move the stored value BACK to it -- the
        reported value wins outright, so this falls out of the same rule
        that fixes restart-in-place, and it is the exact repair the live
        hub.db needs for every row already in it. This is also why the
        reported-wins rule cannot simply ignore existing altogether: this
        path still needs `now` as a placeholder until a real value shows
        up, which is existing's other job (see the invalid/invalid case)."""
        now = 1757000000.0  # a realistic epoch "now", not a small test offset
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ], now_fn=lambda: now)
        self.assertEqual(self._started_at(), now)

        real_earlier = now - (12.8 * 86400)  # the 12.8-day-old case from the brief
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": real_earlier},
        ], now_fn=lambda: now + 100.0)
        self.assertEqual(self._started_at(), real_earlier)

    def test_reported_huge_int_does_not_raise_overflow_error(self):
        # An int with ~308+ digits can't convert to a C double at all --
        # math.isfinite() itself raises OverflowError on it. _valid_started_at
        # must swallow that and treat the value as not-set (falls back to
        # now), not let it escape upsert_sessions.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 10 ** 400},
        ], now_fn=lambda: 1000.0)
        self.assertEqual(self._started_at(), 1000.0)


class RecreatedSessionStartedAtTest(unittest.TestCase):
    """A (device_id, session_id) pair can be reused -- most commonly a
    synthetic tmux:<name> id, reassigned the moment a tmux session of that
    name is recreated. A LONG-dead row's started_at must not be inherited
    by the new session (neither kept outright nor min-ed against it), or a
    session recreated well after the old one ended would permanently read
    as however old the previous occupant of that id happened to be. A
    row ended only moments ago (within store.ENDED_ROW_GRACE_SECONDS) is
    treated differently -- see FlickeredRowStartedAtTest below -- since
    that's more likely the same session flickering than a real
    end-then-recreate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_recreated_session_does_not_inherit_dead_rows_started_at(self):
        old_now = 1757000000.0
        old_started = old_now - 20 * 86400  # a 20-day-old session
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle", "started_at": old_started},
        ], now_fn=lambda: old_now)
        self.assertEqual(
            self.store.fleet_view()["sessions"][0]["started_at"], old_started)

        # The session ends (absent from the next poll).
        self.store.upsert_sessions("local", [], now_fn=lambda: old_now + 10.0)
        ended_view = self.store.fleet_view(include_ended=True)
        self.assertIsNotNone(ended_view["sessions"][0]["ended_at"])

        # tmux reuses the same name/id five seconds later, with its own
        # real, recent started_at -- this is the reviewer's exact probe.
        new_now = old_now + 15.0
        new_started = new_now - 5.0
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle", "started_at": new_started},
        ], now_fn=lambda: new_now)

        result = self.store.fleet_view()
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["started_at"], new_started)
        self.assertIsNone(result["sessions"][0]["ended_at"])

    def test_recreated_session_with_no_reported_started_at_falls_back_to_now_not_dead_value(self):
        # The gap between the old row ending and the new sighting is well
        # past store.ENDED_ROW_GRACE_SECONDS -- a genuinely new session
        # reusing the id, not the same session flickering (contrast with
        # FlickeredRowStartedAtTest, where a short gap keeps the old value).
        old_now = 1757000000.0
        old_started = old_now - 20 * 86400
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle", "started_at": old_started},
        ], now_fn=lambda: old_now)
        ended_at = old_now + 10.0
        self.store.upsert_sessions("local", [], now_fn=lambda: ended_at)

        new_now = ended_at + store.ENDED_ROW_GRACE_SECONDS + 60.0
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle"},  # no started_at reported
        ], now_fn=lambda: new_now)

        result = self.store.fleet_view()
        self.assertEqual(result["sessions"][0]["started_at"], new_now)


class FlickeredRowStartedAtTest(unittest.TestCase):
    """Fix round 2, Minor 2: a row can drop out of a single poll's `rows`
    without the underlying session having stopped (e.g. `claude agents
    --json` timing out, which empties every external row for that poll --
    see agents._fetch_rows). The end-of-sweep in upsert_sessions marks it
    ended_at=now regardless. If the row reappears within
    store.ENDED_ROW_GRACE_SECONDS and still has no usable reported
    started_at, its own (recently-ended) started_at must be kept -- not
    reset to `now` -- or a flaky claude build would restart a real
    session's clock on every hiccup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_flickered_row_within_grace_window_keeps_its_started_at(self):
        old_now = 1757000000.0
        old_started = old_now - 12 * 86400  # a 12-day-old session
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": old_started},
        ], now_fn=lambda: old_now)

        # A flicker: the row drops out of one poll (marks it ended)...
        ended_at = old_now + 30.0
        self.store.upsert_sessions("local", [], now_fn=lambda: ended_at)
        # ...then reappears well within the grace window, still without a
        # usable reported started_at.
        self.assertLess(store.ENDED_ROW_GRACE_SECONDS, 3600)  # sanity: grace is short
        flicker_now = ended_at + 15.0
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},  # no started_at reported
        ], now_fn=lambda: flicker_now)

        result = self.store.fleet_view()
        self.assertEqual(result["sessions"][0]["started_at"], old_started)
        self.assertIsNone(result["sessions"][0]["ended_at"])

    def test_20_flicker_cycles_do_not_reset_a_12_day_age(self):
        start = 1757000000.0
        old_started = start - 12 * 86400
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": old_started},
        ], now_fn=lambda: start)

        t = start
        for _ in range(20):
            t += 15.0
            self.store.upsert_sessions("local", [], now_fn=lambda: t)  # flicker: row vanishes
            t += 15.0
            self.store.upsert_sessions("local", [
                {"session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "interactive",
                 "state": "idle"},  # reappears, still no usable started_at
            ], now_fn=lambda: t)

        result = self.store.fleet_view()
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["started_at"], old_started)
        self.assertIsNone(result["sessions"][0]["ended_at"])


class SessionsStatusColumnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_column_added_to_a_pre_existing_db_and_round_trips(self):
        # Simulate a hub.db from before the status column existed (also
        # missing the other additive columns, same as
        # test_existing_db_created_before_new_columns_gains_them_without_data_loss).
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
            self.assertIsNone(view["sessions"][0]["status"])  # pre-existing row: column added as NULL

            migrated.upsert_sessions("local", [
                {"session_id": "s1", "name": "rc-old", "cwd": "/tmp", "kind": "interactive",
                 "state": "idle", "status": "busy"},
            ])
            view2 = migrated.fleet_view()
            self.assertEqual(view2["sessions"][0]["status"], "busy")
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
