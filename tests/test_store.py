import json
import os
import sqlite3
import tempfile
import time
import unittest

import store


def _day(offset_days=0):
    """A real "YYYY-MM-DD" date string, `offset_days` days before now (UTC).
    cost_view() has no injectable clock (CONTRACT.md: no now_fn parameter,
    since cost_daily.day is a calendar-date string written once per real
    day, not an epoch) -- so tests that need to land inside or outside its
    rolling window use real day arithmetic instead of a fake now_fn."""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - offset_days * 86400))


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
    """Fix round 2, Minor 2 (narrowed by fix round 3, Item 1): a row can
    drop out of a single poll's `rows` without the underlying session
    having stopped (e.g. `claude agents --json` timing out, which empties
    every EXTERNAL row for that poll -- see agents._fetch_rows). The
    end-of-sweep in upsert_sessions marks it ended_at=now regardless. If
    an EXTERNAL row reappears within the grace window and still has no
    usable reported started_at, its own (recently-ended) started_at must
    be kept -- not reset to `now` -- or a flaky claude build would restart
    a real session's clock on every hiccup.

    The grace window applies to external rows only: a launcher
    (tmux-derived) row does not go missing from `rows` the way an
    external row does, so if one is reported with no usable started_at
    right after ending, that is presumed to be a genuinely new session,
    not a flicker -- see the launcher-row tests below."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_flickered_external_row_within_grace_window_keeps_its_started_at(self):
        old_now = 1757000000.0
        old_started = old_now - 12 * 86400  # a 12-day-old session
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "hand-started", "cwd": "/tmp",
             "kind": "external", "external": True, "state": "idle",
             "started_at": old_started},
        ], now_fn=lambda: old_now)

        # A flicker: the row drops out of one poll (marks it ended)...
        ended_at = old_now + 30.0
        self.store.upsert_sessions("local", [], now_fn=lambda: ended_at)
        # ...then reappears well within the grace window, still without a
        # usable reported started_at.
        self.assertLess(store.ENDED_ROW_GRACE_SECONDS, 3600)  # sanity: grace is short
        flicker_now = ended_at + 15.0
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "hand-started", "cwd": "/tmp",
             "kind": "external", "external": True, "state": "idle"},  # no started_at reported
        ], now_fn=lambda: flicker_now)

        result = self.store.fleet_view()
        self.assertEqual(result["sessions"][0]["started_at"], old_started)
        self.assertIsNone(result["sessions"][0]["ended_at"])

    def test_20_flicker_cycles_do_not_reset_a_12_day_age(self):
        start = 1757000000.0
        old_started = start - 12 * 86400
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "hand-started", "cwd": "/tmp",
             "kind": "external", "external": True, "state": "idle",
             "started_at": old_started},
        ], now_fn=lambda: start)

        t = start
        for _ in range(20):
            t += 15.0
            self.store.upsert_sessions("local", [], now_fn=lambda: t)  # flicker: row vanishes
            t += 15.0
            self.store.upsert_sessions("local", [
                {"session_id": "s1", "name": "hand-started", "cwd": "/tmp",
                 "kind": "external", "external": True,
                 "state": "idle"},  # reappears, still no usable started_at
            ], now_fn=lambda: t)

        result = self.store.fleet_view()
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["started_at"], old_started)
        self.assertIsNone(result["sessions"][0]["ended_at"])

    def test_launcher_row_within_grace_window_does_not_inherit_stale_started_at(self):
        """Fix round 3, Item 1's exact scenario: a tmux-derived (launcher,
        non-external) id recreated moments after ending, reporting no
        started_at, must NOT inherit the dead row's age even though the
        gap is well within the grace window -- that would be a false
        runaway/false-kill risk, the one direction of error this feature
        exists to avoid. It gets its own clock (now) instead."""
        old_now = 1757000000.0
        old_started = old_now - 20 * 86400  # a 20-day-old session
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle", "started_at": old_started},
        ], now_fn=lambda: old_now)
        ended_at = old_now + 10.0
        self.store.upsert_sessions("local", [], now_fn=lambda: ended_at)

        # Recreated 30 seconds later -- well within the grace window --
        # but with no usable reported started_at.
        new_now = ended_at + 30.0
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle"},  # no started_at reported
        ], now_fn=lambda: new_now)

        result = self.store.fleet_view()
        self.assertEqual(result["sessions"][0]["started_at"], new_now)

    def test_external_flag_is_read_from_the_stored_row_not_the_current_poll(self):
        """The trustworthiness check reads the EXISTING (already stored)
        row's external flag, not whatever the current (reported-invalid)
        poll's row happens to claim -- a poll with no usable started_at
        cannot retroactively make a launcher row's dead value trustworthy
        by mislabeling it external on the way back in."""
        old_now = 1757000000.0
        old_started = old_now - 20 * 86400
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "interactive", "state": "idle", "started_at": old_started},
        ], now_fn=lambda: old_now)  # stored as external=False
        ended_at = old_now + 10.0
        self.store.upsert_sessions("local", [], now_fn=lambda: ended_at)

        new_now = ended_at + 30.0
        self.store.upsert_sessions("local", [
            {"session_id": "tmux:rc-alpha", "name": "rc-alpha", "cwd": "/tmp",
             "kind": "external", "external": True,
             "state": "idle"},  # claims external now, still no started_at
        ], now_fn=lambda: new_now)

        result = self.store.fleet_view()
        self.assertEqual(result["sessions"][0]["started_at"], new_now)


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


class UsageCostAlertsTest(unittest.TestCase):
    """Phase 3 wiring, CONTRACT.md section 2: session_usage, cost_daily,
    alerts and the methods built on them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "hub.db")
        self.store = store.Store(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_new_tables_exist_on_a_fresh_database(self):
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("session_usage", tables)
            self.assertIn("cost_daily", tables)
            self.assertIn("alerts", tables)
            # Fix round 1: target_type/name are on `alerts` from the start
            # on a fresh database too, applied via the same additive-column
            # path that handles a pre-existing alerts table (both run
            # unconditionally in Store.__init__).
            alert_cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)")}
            self.assertIn("target_type", alert_cols)
            self.assertIn("name", alert_cols)
        finally:
            conn.close()

    # -- upsert_session_usage --------------------------------------------

    def test_upsert_session_usage_insert_then_update_in_place_via_fleet_view(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "busy", "started_at": 1000},
        ], now_fn=lambda: 1000.0)
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "input": 100, "cache_read": 200, "cache_write": 30,
             "output": 40, "effective": 5000, "last_ts": 1000.0},
        ], now_fn=lambda: 1010.0)
        view = self.store.fleet_view()
        self.assertEqual(view["sessions"][0]["usage"], {
            "input": 100, "cache_read": 200, "cache_write": 30,
            "output": 40, "effective": 5000, "last_ts": 1000.0})

        # Same (device_id, session_id): updates the one row in place,
        # never a second row.
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "input": 150, "cache_read": 250, "cache_write": 35,
             "output": 45, "effective": 6000, "last_ts": 1020.0},
        ], now_fn=lambda: 1030.0)
        view2 = self.store.fleet_view()
        self.assertEqual(len(view2["sessions"]), 1)
        self.assertEqual(view2["sessions"][0]["usage"]["effective"], 6000)
        self.assertEqual(view2["sessions"][0]["usage"]["last_ts"], 1020.0)

    def test_upsert_session_usage_skips_rows_without_session_id(self):
        result = self.store.upsert_session_usage("local", [
            {"input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "effective": 5,
             "last_ts": 1.0},
        ])
        self.assertEqual(result["skipped"], 1)

    def test_upsert_session_usage_skips_row_with_non_coercible_numeric_field(self):
        # Fix round 2, review Important 2: a device payload is untrusted --
        # NaN, +/-inf and a non-numeric type, PRESENT in a numeric field,
        # must be rejected and the whole row dropped rather than written
        # with a partial value. None is deliberately NOT in this list --
        # see test_upsert_session_usage_none_field_is_absent_not_invalid
        # below (fix round 3, review Important 2: a present field of None
        # is "absent", not "bad", and must not cause a skip).
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ])
        bad_values = (float("nan"), float("inf"), float("-inf"), "n/a",
                      [1], {"a": 1}, True)
        for bad in bad_values:
            with self.subTest(bad=bad):
                result = self.store.upsert_session_usage("local", [
                    {"session_id": "s1", "input": 1, "cache_read": 1, "cache_write": 1,
                     "output": 1, "effective": bad, "last_ts": 1.0},
                ])
                self.assertEqual(result["skipped"], 1)
        # None of those attempts ever wrote a row: usage stays None, not a
        # partially-written or garbage value.
        view = self.store.fleet_view()
        self.assertIsNone(view["sessions"][0]["usage"])

    def test_upsert_session_usage_none_field_is_absent_not_invalid(self):
        # Fix round 3, review Important 2 (a regression fix round 2's own
        # change introduced): a numeric field present as None is treated
        # the same as an absent field -- stored as NULL, never a reason to
        # drop the whole row.
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ])
        result = self.store.upsert_session_usage("local", [
            {"session_id": "s1", "input": None, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 100, "last_ts": None},
        ])
        self.assertEqual(result["skipped"], 0)
        view = self.store.fleet_view()
        self.assertEqual(view["sessions"][0]["usage"]["effective"], 100)
        self.assertIsNone(view["sessions"][0]["usage"]["input"])
        self.assertIsNone(view["sessions"][0]["usage"]["last_ts"])

    def test_upsert_session_usage_accepts_the_literal_metadata_role_shape(self):
        # Fix round 3, review Important 2: CONTRACT.md section 1's
        # metadata role sends exactly {"session_id", "effective"} for
        # per-session usage -- nothing else. This device must still
        # contribute its effective total, per "contributes to per-device
        # totals only, never to the projects table" (metadata never
        # reports cost_daily/project data at all, only this).
        self.store.upsert_sessions("local", [
            {"session_id": "hashed-abc", "name": None, "cwd": None, "kind": "interactive",
             "state": "idle"},
        ])
        result = self.store.upsert_session_usage("local", [
            {"session_id": "hashed-abc", "effective": 43361000},
        ])
        self.assertEqual(result["skipped"], 0)
        view = self.store.fleet_view()
        usage = view["sessions"][0]["usage"]
        self.assertEqual(usage["effective"], 43361000)
        self.assertIsNone(usage["input"])
        self.assertIsNone(usage["cache_read"])
        self.assertIsNone(usage["cache_write"])
        self.assertIsNone(usage["output"])
        self.assertIsNone(usage["last_ts"])

    def test_upsert_session_usage_rejects_out_of_range_ints_without_losing_other_rows_in_the_batch(self):
        # Fix round 3, review Important 1: an out-of-range int must not
        # raise OverflowError out of the whole batch.
        self.store.upsert_sessions("local", [
            {"session_id": "good", "name": "rc-good", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
            {"session_id": "bad", "name": "rc-bad", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ])
        result = self.store.upsert_session_usage("local", [
            {"session_id": "good", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 100, "last_ts": 1.0},
            {"session_id": "bad", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 2 ** 63, "last_ts": 1.0},
        ])
        self.assertEqual(result["skipped"], 1)
        view = self.store.fleet_view()
        by_id = {s["session_id"]: s["usage"] for s in view["sessions"]}
        self.assertEqual(by_id["good"]["effective"], 100)
        self.assertIsNone(by_id["bad"])

    def test_upsert_session_usage_coerces_numeric_strings(self):
        result = self.store.upsert_session_usage("local", [
            {"session_id": "s1", "input": "10", "cache_read": "20", "cache_write": "3",
             "output": "4", "effective": "5000", "last_ts": "999.5"},
        ])
        self.assertEqual(result["skipped"], 0)
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle"},
        ])
        view = self.store.fleet_view()
        self.assertEqual(view["sessions"][0]["usage"]["effective"], 5000)
        self.assertEqual(view["sessions"][0]["usage"]["last_ts"], 999.5)

    # -- upsert_cost_daily -------------------------------------------------

    def test_upsert_cost_daily_replaces_not_accumulates(self):
        row = {"day": _day(0), "project": "-var-www", "input": 100, "cache_read": 200,
               "cache_write": 30, "output": 40, "effective": 5000}
        for _ in range(3):
            self.store.upsert_cost_daily("local", [row])
        view = self.store.cost_view(days=30)
        dev = next(d for d in view["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 5000)  # not 15000
        self.assertEqual(len(dev["daily"]), 1)
        self.assertEqual(dev["daily"][0]["effective"], 5000)

    def test_upsert_cost_daily_skips_rows_without_day(self):
        result = self.store.upsert_cost_daily("local", [
            {"project": "p1", "input": 1, "cache_read": 2, "cache_write": 3, "output": 4,
             "effective": 5},
        ])
        self.assertEqual(result["skipped"], 1)

    def test_upsert_cost_daily_empty_project_is_valid_not_skipped(self):
        # CONTRACT.md: project is the empty string when the device did not
        # report one (metadata role), never skipped for that reason alone
        # -- but it must still count toward the device's own total (the
        # amendment: "a metadata device contributes to per-device totals
        # only, never to the projects table" -- see the next test for the
        # "never to the projects table" half).
        self.store.upsert_device({"id": "local", "name": "hub", "role": "metadata",
                                   "version": "1", "claude_version": "1"})
        result = self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 42},
        ])
        self.assertEqual(result["skipped"], 0)
        view = self.store.cost_view(days=30)
        dev = next(d for d in view["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 42)

    def test_cost_view_never_surfaces_an_empty_project_in_the_projects_table(self):
        # CONTRACT.md amendment (per-project daily totals): "A metadata
        # device contributes to per-device totals only, never to the
        # projects table." project="" is exactly what a metadata (or
        # legacy pre-fleet) device's rows carry -- it must never appear
        # in view["projects"], including alongside a real project from
        # another device in the same window.
        self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 42},
            {"day": _day(0), "project": "-var-www", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 7},
        ])
        view = self.store.cost_view(days=30)
        projects = {p["project"] for p in view["projects"]}
        self.assertNotIn("", projects)
        self.assertIn("-var-www", projects)

    def test_upsert_cost_daily_skips_row_with_non_coercible_numeric_field(self):
        # Fix round 2, review Important 2: this is the exact probe that
        # broke /api/cost fleet-wide in the review -- effective: "n/a" must
        # never reach the table, not raise later out of cost_view. None is
        # deliberately NOT in this list -- see
        # test_upsert_cost_daily_none_field_is_absent_not_invalid below
        # (fix round 3: a present field of None is "absent", not "bad").
        bad_values = (float("nan"), float("inf"), float("-inf"), "n/a",
                      [1], {"a": 1}, True)
        for bad in bad_values:
            with self.subTest(bad=bad):
                result = self.store.upsert_cost_daily("local", [
                    {"day": _day(0), "project": "p1", "input": 1, "cache_read": 1,
                     "cache_write": 1, "output": 1, "effective": bad},
                ])
                self.assertEqual(result["skipped"], 1)
        # cost_view must still work (no TypeError out of summing a garbage
        # value) and must show nothing for this device -- the bad row was
        # never written.
        view = self.store.cost_view(days=30)
        self.assertEqual(view["devices"], [])
        self.assertEqual(view["projects"], [])

    def test_upsert_cost_daily_coerces_numeric_strings(self):
        result = self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "p1", "input": "10", "cache_read": "20",
             "cache_write": "3", "output": "4", "effective": "5000"},
        ])
        self.assertEqual(result["skipped"], 0)
        view = self.store.cost_view(days=30)
        dev = next(d for d in view["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 5000)

    def test_upsert_cost_daily_none_field_is_absent_not_invalid(self):
        # Fix round 3, review Important 2 (a regression fix round 2's own
        # change introduced): a numeric field present as None is treated
        # the same as an absent field -- stored as NULL (cost_view's `or
        # 0` already handles that), never a reason to drop the whole row.
        result = self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "p1", "input": None, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 100},
        ])
        self.assertEqual(result["skipped"], 0)
        view = self.store.cost_view(days=30)
        dev = next(d for d in view["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 100)

    def test_upsert_cost_daily_accepts_the_literal_metadata_role_shape(self):
        # Fix round 3, review Important 2: CONTRACT.md section 1's
        # metadata role reports cost_daily rows as exactly {"day",
        # "effective"} -- no project (stored as ""), no
        # input/cache_read/cache_write/output at all. The row must still
        # contribute to the device's own total ("contributes to per-device
        # totals only, never to the projects table" -- a "" project is
        # still technically a projects-table row, but a metadata device
        # never contributes a REAL project name, matching the contract's
        # intent that its data is device-level, not per-project).
        result = self.store.upsert_cost_daily("local", [
            {"day": _day(0), "effective": 43361000},
        ])
        self.assertEqual(result["skipped"], 0)
        view = self.store.cost_view(days=30)
        dev = next(d for d in view["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 43361000)
        self.assertEqual(dev["daily"][0]["input"], 0)  # NULL summed via `or 0`

    def test_upsert_cost_daily_rejects_out_of_range_ints_without_losing_other_rows_in_the_batch(self):
        # Fix round 3, review Important 1: an out-of-range int must not
        # raise OverflowError out of the whole batch (rolling back rows
        # that coerced fine).
        result = self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "good", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 100},
            {"day": _day(0), "project": "bad-2-63", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 2 ** 63},
            {"day": _day(0), "project": "bad-10-30", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 10 ** 30},
            {"day": _day(0), "project": "bad-20-digit-string", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": "99999999999999999999"},
        ])
        self.assertEqual(result["skipped"], 3)
        view = self.store.cost_view(days=30)
        projects = {p["project"] for p in view["projects"]}
        self.assertEqual(projects, {"good"})

    def test_upsert_cost_daily_accepts_the_exact_sqlite_int64_boundary(self):
        result = self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "p-max", "input": 0, "cache_read": 0,
             "cache_write": 0, "output": 0, "effective": 2 ** 63 - 1},
            {"day": _day(0), "project": "p-min", "input": -(2 ** 63), "cache_read": 0,
             "cache_write": 0, "output": 0, "effective": 1},
        ])
        self.assertEqual(result["skipped"], 0)

    def test_upsert_cost_daily_bad_row_from_one_device_does_not_break_others(self):
        # Fix round 2, review Important 2: a bad value from one device must
        # not take down /api/cost for the rest of the fleet.
        self.store.upsert_cost_daily("device-a", [
            {"day": _day(0), "project": "p1", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": "n/a"},
        ])
        self.store.upsert_cost_daily("device-b", [
            {"day": _day(0), "project": "p2", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 100},
        ])
        view = self.store.cost_view(days=30)
        device_ids = {d["device_id"] for d in view["devices"]}
        self.assertEqual(device_ids, {"device-b"})
        projects = {p["project"] for p in view["projects"]}
        self.assertEqual(projects, {"p2"})

    # -- cost_view -----------------------------------------------------

    def test_cost_view_aggregates_devices_and_projects_and_honours_days(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "proj-a", "input": 10, "cache_read": 20,
             "cache_write": 3, "output": 4, "effective": 1000},
            {"day": _day(0), "project": "proj-b", "input": 5, "cache_read": 6,
             "cache_write": 1, "output": 2, "effective": 500},
        ])
        self.store.upsert_cost_daily("local", [
            {"day": _day(40), "project": "proj-old", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 999999},
        ])
        view = self.store.cost_view(days=30)
        dev = next(d for d in view["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["name"], "hub")
        # The 40-day-old row is outside the 30-day window and must not
        # count toward the device total.
        self.assertEqual(dev["total_effective"], 1500)
        self.assertEqual(len(dev["daily"]), 1)
        projects = {p["project"]: p["effective"] for p in view["projects"]
                    if p["device_id"] == "local"}
        self.assertEqual(projects, {"proj-a": 1000, "proj-b": 500})
        self.assertNotIn("proj-old", projects)

    def test_cost_view_projects_sorted_descending_and_capped_at_50(self):
        rows = [
            {"day": _day(0), "project": f"proj-{i}", "input": 0, "cache_read": 0,
             "cache_write": 0, "output": 0, "effective": i}
            for i in range(60)
        ]
        self.store.upsert_cost_daily("local", rows)
        view = self.store.cost_view(days=30)
        self.assertEqual(len(view["projects"]), 50)
        effectives = [p["effective"] for p in view["projects"]]
        self.assertEqual(effectives, sorted(effectives, reverse=True))
        self.assertEqual(effectives[0], 59)  # highest-effective project first

    def test_cost_view_days_window_is_exact_not_off_by_one(self):
        # Fix round 2, review Minor 5: days=30 must return exactly 30
        # distinct calendar days, not 31 -- a day exactly `days` days old
        # is the boundary itself and must be excluded.
        self.store.upsert_cost_daily("local", [
            {"day": _day(29), "project": "p-in", "input": 0, "cache_read": 0,
             "cache_write": 0, "output": 0, "effective": 10},
            {"day": _day(30), "project": "p-out", "input": 0, "cache_read": 0,
             "cache_write": 0, "output": 0, "effective": 20},
        ])
        view = self.store.cost_view(days=30)
        projects = {p["project"] for p in view["projects"]}
        self.assertIn("p-in", projects)
        self.assertNotIn("p-out", projects)

    # -- cost_view.sessions (W4/integration, CONTRACT.md amendment) -------

    def test_cost_view_sessions_includes_an_ended_session(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-live", "cwd": "/var/www/live",
             "kind": "launcher", "state": "busy", "started_at": 1000},
        ], now_fn=lambda: 1000.0)
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "effective": 500, "last_ts": 1000.0},
        ])
        # A big session that has since ENDED -- upsert_sessions with an
        # empty rows list for this device ends every previously-live row,
        # same mechanism a real poll uses when a session stops appearing.
        self.store.upsert_sessions("local", [], now_fn=lambda: 2000.0)
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "effective": 999999, "last_ts": 1999.0},
        ])
        view = self.store.cost_view(days=30)
        row = next(r for r in view["sessions"] if r["session_id"] == "s1")
        self.assertTrue(row["ended"])
        self.assertEqual(row["effective"], 999999)
        self.assertEqual(row["name"], "rc-live")
        self.assertEqual(row["project"], "-var-www-live")

    def test_cost_view_sessions_sorted_descending_and_capped_at_50(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": f"s{i}", "name": f"rc-{i}", "cwd": "/tmp",
             "kind": "launcher", "state": "idle", "started_at": 1000}
            for i in range(60)
        ], now_fn=lambda: 1000.0)
        self.store.upsert_session_usage("local", [
            {"session_id": f"s{i}", "effective": i, "last_ts": 1000.0}
            for i in range(60)
        ])
        view = self.store.cost_view(days=30)
        self.assertEqual(len(view["sessions"]), 50)
        effectives = [r["effective"] for r in view["sessions"]]
        self.assertEqual(effectives, sorted(effectives, reverse=True))
        self.assertEqual(effectives[0], 59)

    def test_cost_view_sessions_name_null_for_an_orphaned_usage_row(self):
        # session_usage row with no matching sessions row at all -- e.g. a
        # session row pruned out from under it. A LEFT JOIN must still
        # surface the usage row, with name/project coming back as the
        # "nothing known" values rather than the row disappearing.
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_session_usage("local", [
            {"session_id": "orphan", "effective": 42, "last_ts": 1000.0},
        ])
        view = self.store.cost_view(days=30)
        row = next(r for r in view["sessions"] if r["session_id"] == "orphan")
        self.assertIsNone(row["name"])
        self.assertEqual(row["project"], "")
        # Fix round 1 (Minor): an orphan (no `sessions` row matched at
        # all) reports ended=True, not False -- a session_usage row with
        # no live counterpart is far more likely to be something that
        # ended and was later pruned than something still live, and
        # "unknown" should never read as "confirmed still running".
        self.assertTrue(row["ended"])
        self.assertEqual(row["effective"], 42)

    def test_cost_view_sessions_ended_false_for_a_genuinely_live_session(self):
        # A real sessions row with ended_at IS NULL (still live) must
        # report ended=False, not get swept into the orphan-defaults-to-
        # True path above -- only a session with NO sessions row at all
        # defaults to True.
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-live", "cwd": "/tmp",
             "kind": "launcher", "state": "busy", "started_at": 1000},
        ], now_fn=lambda: 1000.0)
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "effective": 10, "last_ts": 1000.0},
        ])
        view = self.store.cost_view(days=30)
        row = next(r for r in view["sessions"] if r["session_id"] == "s1")
        self.assertFalse(row["ended"])

    def test_cost_view_sessions_not_limited_to_the_days_window(self):
        # Unlike devices/projects, `sessions` has no `day` column to
        # filter by (session_usage holds lifetime totals, not a daily
        # series) -- a days=1 request must still return every session.
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "effective": 10, "last_ts": 1000.0},
        ])
        view = self.store.cost_view(days=1)
        self.assertEqual(len(view["sessions"]), 1)

    # -- last_event_ts_map -------------------------------------------------

    def test_last_event_ts_map_returns_max_ts_per_session(self):
        self.store.add_events("local", [
            {"session_id": "s1", "ts": 100.0, "event": "Notification"},
            {"session_id": "s1", "ts": 300.0, "event": "SubagentStop"},
            {"session_id": "s1", "ts": 200.0, "event": "Notification"},
            {"session_id": "s2", "ts": 50.0, "event": "Notification"},
        ])
        m = self.store.last_event_ts_map()
        self.assertEqual(m[("local", "s1")], 300.0)
        self.assertEqual(m[("local", "s2")], 50.0)
        self.assertNotIn(("local", "s3"), m)

    # -- devices.usage_partial ----------------------------------------------

    def test_upsert_device_usage_partial_round_trips(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1",
                                   "usage_partial": True})
        view = self.store.fleet_view()
        dev = next(d for d in view["devices"] if d["id"] == "local")
        self.assertEqual(dev["usage_partial"], 1)

    def test_upsert_device_usage_partial_defaults_false_on_a_new_row(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        view = self.store.fleet_view()
        dev = next(d for d in view["devices"] if d["id"] == "local")
        self.assertEqual(dev["usage_partial"], 0)

    def test_upsert_device_usage_partial_not_provided_keeps_prior_value(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1",
                                   "usage_partial": True})
        # A later update that doesn't mention usage_partial at all (e.g. a
        # caller that doesn't know the fact) must not clobber it back to
        # false.
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "2", "claude_version": "1"})
        view = self.store.fleet_view()
        dev = next(d for d in view["devices"] if d["id"] == "local")
        self.assertEqual(dev["usage_partial"], 1)
        self.assertEqual(dev["version"], "2")

    def test_upsert_device_usage_partial_can_be_cleared_explicitly(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1",
                                   "usage_partial": True})
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1",
                                   "usage_partial": False})
        view = self.store.fleet_view()
        dev = next(d for d in view["devices"] if d["id"] == "local")
        self.assertEqual(dev["usage_partial"], 0)

    # -- replace_alerts --------------------------------------------------

    def test_replace_alerts_preserves_first_seen_moves_last_seen_and_deletes_stale(self):
        finding = {"rule": "token_rate", "severity": "alert", "target_type": "session",
                   "device_id": "local", "session_id": "s1", "name": "rc-foo",
                   "message": "m1", "value": 1.0, "threshold": 2.0, "since": 100.0}
        self.store.replace_alerts([finding], now_fn=lambda: 1000.0)
        alerts = self.store.live_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["first_seen"], 1000.0)
        self.assertEqual(alerts[0]["last_seen"], 1000.0)
        # target_type/name (fix round 1) round-trip on the row itself, no
        # join needed.
        self.assertEqual(alerts[0]["target_type"], "session")
        self.assertEqual(alerts[0]["name"], "rc-foo")

        finding_updated = dict(finding, message="m1 updated", value=2.0, name="rc-foo-renamed")
        self.store.replace_alerts([finding_updated], now_fn=lambda: 2000.0)
        alerts2 = self.store.live_alerts()
        self.assertEqual(len(alerts2), 1)
        self.assertEqual(alerts2[0]["first_seen"], 1000.0)  # unchanged
        self.assertEqual(alerts2[0]["last_seen"], 2000.0)   # moved
        self.assertEqual(alerts2[0]["message"], "m1 updated")
        # name is NOT frozen like first_seen: it updates on every
        # re-observation, same as severity/message/value.
        self.assertEqual(alerts2[0]["name"], "rc-foo-renamed")

        # Stops firing (absent from the batch) -> deleted, not left stale.
        self.store.replace_alerts([], now_fn=lambda: 3000.0)
        self.assertEqual(self.store.live_alerts(), [])

    def test_replace_alerts_device_finding_upserted_twice_yields_one_row(self):
        device_finding = {"rule": "device_offline", "severity": "warn",
                           "target_type": "device", "device_id": "dev1",
                           "session_id": None, "name": "laptop", "message": "offline",
                           "value": 10.0, "threshold": 5.0, "since": 50.0}
        self.store.replace_alerts([device_finding], now_fn=lambda: 100.0)
        self.store.replace_alerts([device_finding], now_fn=lambda: 200.0)
        alerts = self.store.live_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["session_id"], "")  # empty string, not NULL
        self.assertEqual(alerts[0]["first_seen"], 100.0)
        self.assertEqual(alerts[0]["last_seen"], 200.0)
        self.assertEqual(alerts[0]["target_type"], "device")
        self.assertEqual(alerts[0]["name"], "laptop")

    def test_replace_alerts_is_atomic_on_failure(self):
        good = {"rule": "token_rate", "severity": "alert", "device_id": "local",
                "session_id": "s1", "name": "rc-foo", "message": "m1", "value": 1.0,
                "threshold": 2.0, "since": 100.0}
        self.store.replace_alerts([good], now_fn=lambda: 1000.0)
        before = self.store.live_alerts()

        # A finding with a value sqlite3 cannot bind at all: the write must
        # fail and roll back, not partially apply (e.g. deleting `good`
        # because it's absent from this batch, then dying on the insert).
        # `message` (not `value`/`threshold` -- fix round 4 routes those
        # through _coerce_number, which turns an unsupported type into
        # None rather than a bind error) is still passed through raw.
        bad = {"rule": "token_rate", "severity": "alert", "device_id": "local",
               "session_id": "s2", "name": "rc-bar", "message": object(),
               "value": 2.0, "threshold": 2.0, "since": 100.0}
        with self.assertRaises(Exception):
            self.store.replace_alerts([bad], now_fn=lambda: 2000.0)

        after = self.store.live_alerts()
        self.assertEqual(after, before)

    def test_replace_alerts_coerces_value_and_threshold_without_losing_the_batch(self):
        # Fix round 4, review residual 1: guard.py's own _finite_or_none
        # rejects NaN/inf but never checks SQLite's 64-bit range, so an
        # operator typo in guard.json (an over-int64 threshold) reaches
        # here as a plain finite Python int. Reproduced directly: a
        # finding with threshold=2**63 alongside a normal finding must
        # not raise (dropping the whole alerts batch, including the
        # normal finding) the way an uncoerced bind used to.
        good = {"rule": "session_age", "severity": "warn", "device_id": "local",
                "session_id": "s1", "name": "rc-good", "message": "m", "value": 1.0,
                "threshold": 2.0, "since": 100.0}
        bad_threshold = {"rule": "token_rate", "severity": "alert", "device_id": "local",
                          "session_id": "s2", "name": "rc-bad", "message": "m",
                          "value": 1.0, "threshold": 2 ** 63, "since": 100.0}
        result = self.store.replace_alerts([good, bad_threshold])
        self.assertEqual(result["count"], 2)
        alerts = {a["rule"]: a for a in self.store.live_alerts()}
        self.assertEqual(alerts["session_age"]["threshold"], 2.0)
        # The out-of-range threshold is coerced to None (SQL NULL), not
        # dropped from the batch and not left to raise.
        self.assertIsNone(alerts["token_rate"]["threshold"])

    def test_replace_alerts_coerces_a_numeric_string_value(self):
        self.store.replace_alerts([
            {"rule": "token_rate", "severity": "alert", "device_id": "local",
             "session_id": "s1", "name": "rc-foo", "message": "m", "value": "5400000.0",
             "threshold": "5000000", "since": 100.0},
        ])
        alerts = self.store.live_alerts()
        self.assertEqual(alerts[0]["value"], 5400000.0)
        self.assertEqual(alerts[0]["threshold"], 5000000)

    def test_replace_alerts_returns_skipped_count(self):
        # Fix round 2, review Minor 6: matches every sibling upsert
        # (upsert_sessions, upsert_session_usage, upsert_cost_daily all
        # report `skipped`).
        good = {"rule": "token_rate", "severity": "alert", "device_id": "local",
                "session_id": "s1", "name": "rc-foo", "message": "m", "value": 1.0,
                "threshold": 2.0, "since": 100.0}
        malformed = {"rule": "token_rate", "severity": "alert", "message": "no device_id"}
        result = self.store.replace_alerts([good, malformed])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["skipped"], 1)

    def test_replace_alerts_sweep_deletes_a_legacy_null_session_id_row(self):
        # Fix round 2, review Minor 6: `session_id=?` with a NULL parameter
        # never matches in SQL (NULL is never "=" to anything), so a
        # legacy row written with a real NULL (rather than this method's
        # own "" convention) must be matched with IS NULL instead, or it
        # can never be swept even after it stops firing.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO alerts (device_id, session_id, rule, severity, message, value, "
            "threshold, since, first_seen, last_seen) VALUES "
            "('dev1', NULL, 'device_offline', 'warn', 'legacy row', 1.0, 2.0, "
            "50.0, 50.0, 50.0)")
        conn.commit()
        conn.close()
        self.assertEqual(len(self.store.live_alerts()), 1)

        # An empty batch: the legacy row is absent from it and must be
        # swept, same as any other stale finding.
        self.store.replace_alerts([])
        self.assertEqual(self.store.live_alerts(), [])

    def test_live_alerts_ordering_alert_before_warn_then_oldest_first_seen(self):
        warn_old = {"rule": "session_age", "severity": "warn", "device_id": "local",
                    "session_id": "s1", "name": "a", "message": "m", "value": 1.0,
                    "threshold": 2.0, "since": 1.0}
        alert_old = {"rule": "stalled", "severity": "alert", "device_id": "local",
                     "session_id": "s4", "name": "d", "message": "m", "value": 1.0,
                     "threshold": 2.0, "since": 1.0}
        warn_new = {"rule": "token_total", "severity": "warn", "device_id": "local",
                    "session_id": "s3", "name": "c", "message": "m", "value": 1.0,
                    "threshold": 2.0, "since": 1.0}
        alert_new = {"rule": "token_rate", "severity": "alert", "device_id": "local",
                     "session_id": "s2", "name": "b", "message": "m", "value": 1.0,
                     "threshold": 2.0, "since": 1.0}
        # first_seen order across calls: warn_old(100) < alert_old(200) <
        # warn_new(300) < alert_new(400).
        self.store.replace_alerts([warn_old], now_fn=lambda: 100.0)
        self.store.replace_alerts([warn_old, alert_old], now_fn=lambda: 200.0)
        self.store.replace_alerts([warn_old, alert_old, warn_new], now_fn=lambda: 300.0)
        self.store.replace_alerts(
            [warn_old, alert_old, warn_new, alert_new], now_fn=lambda: 400.0)

        rules = [a["rule"] for a in self.store.live_alerts()]
        self.assertEqual(rules, ["stalled", "token_rate", "session_age", "token_total"])

    # -- fleet_view usage join --------------------------------------------

    def test_fleet_view_sessions_carry_usage_none_when_absent(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 1000},
        ], now_fn=lambda: 1000.0)
        view = self.store.fleet_view()
        self.assertIsNone(view["sessions"][0]["usage"])

    def test_fleet_view_returns_none_for_an_all_null_session_usage_row(self):
        # Fix round 4, review residual 2: a session_usage row can itself
        # have every numeric column NULL (upsert_session_usage's
        # sparse-row handling accepts {"session_id": "s1"} alone, with no
        # numeric fields at all). "No data" must have exactly one
        # representation -- None -- not two ({"input": None, ...} being
        # the other).
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": 1000},
        ], now_fn=lambda: 1000.0)
        result = self.store.upsert_session_usage("local", [{"session_id": "s1"}])
        self.assertEqual(result["skipped"], 0)
        view = self.store.fleet_view()
        self.assertIsNone(view["sessions"][0]["usage"])

    # -- prune -------------------------------------------------------------

    def test_prune_removes_orphan_session_usage_and_old_cost_daily_keeps_live(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": time.time()},
        ])
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 1, "last_ts": time.time()},
            {"session_id": "orphan", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 1, "last_ts": time.time()},
        ])
        self.store.upsert_cost_daily("local", [
            {"day": _day(0), "project": "p1", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 1},
            {"day": _day(40), "project": "p2", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 1},
        ])

        deleted = self.store.prune(days=14)
        self.assertEqual(deleted["session_usage"], 1)  # only the orphan
        self.assertEqual(deleted["cost_daily"], 1)      # only the 40-day-old row

        # The live session's own usage row survives (its sessions row still
        # exists -- it never got an ended_at, so `se` doesn't touch it
        # either).
        view = self.store.fleet_view()
        self.assertIsNotNone(view["sessions"][0]["usage"])
        cost = self.store.cost_view(days=30)
        dev = next(d for d in cost["devices"] if d["device_id"] == "local")
        self.assertEqual(len(dev["daily"]), 1)
        self.assertEqual(dev["daily"][0]["day"], _day(0))

    def test_prune_does_not_remove_a_still_referenced_session_usage_row(self):
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        self.store.upsert_sessions("local", [
            {"session_id": "s1", "name": "rc-foo", "cwd": "/tmp", "kind": "interactive",
             "state": "idle", "started_at": time.time()},
        ])
        self.store.upsert_session_usage("local", [
            {"session_id": "s1", "input": 1, "cache_read": 1, "cache_write": 1,
             "output": 1, "effective": 1, "last_ts": time.time()},
        ])
        deleted = self.store.prune(days=14)
        self.assertEqual(deleted["session_usage"], 0)

    def test_prune_cost_daily_uses_its_own_cutoff_not_the_generic_days(self):
        # Fix round 2, review Important 1: fleetpoll.py calls prune() bare,
        # hourly. A cost_daily row 20 days old must survive that bare call
        # even though it is well past the generic 14-day cutoff every
        # other table uses -- cost_days defaults to 35, with headroom past
        # /api/cost's own 30-day default window.
        self.store.upsert_cost_daily("local", [
            {"day": _day(20), "project": "p-recent", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 1},
            {"day": _day(50), "project": "p-ancient", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 1},
        ])
        deleted = self.store.prune()  # bare call, matching fleetpoll.py's own usage
        self.assertEqual(deleted["cost_daily"], 1)  # only the 50-day-old row
        view = self.store.cost_view(days=9999)
        days_present = {d["day"] for dev in view["devices"] for d in dev["daily"]}
        self.assertIn(_day(20), days_present)
        self.assertNotIn(_day(50), days_present)

    def test_prune_cost_days_param_is_independently_overridable(self):
        self.store.upsert_cost_daily("local", [
            {"day": _day(20), "project": "p1", "input": 1, "cache_read": 1,
             "cache_write": 1, "output": 1, "effective": 1},
        ])
        # An explicit, tighter cost_days deletes a row the 35-day default
        # would have kept, while `days` (still 14 here) continues to
        # govern everything else unchanged.
        deleted = self.store.prune(days=14, cost_days=10)
        self.assertEqual(deleted["cost_daily"], 1)

    # -- concurrency -------------------------------------------------------

    def test_concurrent_writes_to_new_tables_do_not_raise(self):
        import threading
        self.store.upsert_device({"id": "local", "name": "hub", "role": "full",
                                   "version": "1", "claude_version": "1"})
        # upsert_sessions treats its `rows` as the device's WHOLE current
        # session list (anything absent gets ended_at) -- seed all 5
        # sessions in one call, upfront, so the concurrent section below
        # only races the per-key upserts against each other, not against
        # upsert_sessions's own end-of-sweep semantics.
        self.store.upsert_sessions("local", [
            {"session_id": f"s{n}", "name": f"s{n}", "cwd": "/tmp",
             "kind": "interactive", "state": "busy", "started_at": 1000.0}
            for n in range(5)
        ])
        errors = []

        def writer(n):
            try:
                sid = f"s{n}"
                for i in range(15):
                    self.store.upsert_session_usage("local", [
                        {"session_id": sid, "input": i, "cache_read": i,
                         "cache_write": i, "output": i, "effective": i, "last_ts": float(i)},
                    ])
                    self.store.upsert_cost_daily("local", [
                        {"day": _day(0), "project": f"proj-{n}", "input": i,
                         "cache_read": i, "cache_write": i, "output": i, "effective": i},
                    ])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(errors, [])

        view = self.store.fleet_view()
        self.assertEqual(len(view["sessions"]), 5)
        for s in view["sessions"]:
            self.assertIsNotNone(s["usage"])
            self.assertEqual(s["usage"]["effective"], 14)  # last iteration's value, i=14

        cost = self.store.cost_view(days=30)
        dev = next(d for d in cost["devices"] if d["device_id"] == "local")
        self.assertEqual(len(dev["daily"]), 1)
        self.assertEqual(len(cost["projects"]), 5)
        for p in cost["projects"]:
            self.assertEqual(p["effective"], 14)


class UsageCostAlertsMigrationTest(unittest.TestCase):
    """The new tables against a copy of a hub.db that predates them (the
    actual shape of the live production hub.db this task must never touch
    directly -- see task-w2-brief.md)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_tables_created_on_a_db_that_predates_them(self):
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
                pid INTEGER, tmux TEXT, rc_url TEXT, tokens INTEGER, claude TEXT,
                status TEXT,
                PRIMARY KEY (device_id, session_id)
            );
            CREATE TABLE session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, session_id TEXT,
                ts REAL, event TEXT, extra_json TEXT
            );
            CREATE UNIQUE INDEX idx_events_unique
                ON session_events(device_id, session_id, ts, event);
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
            "ended_at, last_seen, external, status) VALUES "
            "('local', 's1', 'rc-old', '/tmp', 'interactive', 'idle', 100.0, NULL, 100.0, 0, "
            "'busy')")
        conn.commit()
        conn.close()
        os.chmod(old_db, 0o600)

        migrated = store.Store(old_db)
        try:
            view = migrated.fleet_view()
            self.assertEqual(len(view["sessions"]), 1)
            self.assertEqual(view["sessions"][0]["name"], "rc-old")
            self.assertIsNone(view["sessions"][0]["usage"])  # table is new, empty

            migrated.upsert_session_usage("local", [
                {"session_id": "s1", "input": 1, "cache_read": 2, "cache_write": 3,
                 "output": 4, "effective": 5, "last_ts": 200.0},
            ])
            migrated.upsert_cost_daily("local", [
                {"day": _day(0), "project": "p1", "input": 1, "cache_read": 2,
                 "cache_write": 3, "output": 4, "effective": 5},
            ])
            migrated.replace_alerts([
                {"rule": "token_rate", "severity": "warn", "device_id": "local",
                 "session_id": "s1", "name": "rc-old", "message": "m", "value": 1.0,
                 "threshold": 2.0, "since": 100.0},
            ])

            view2 = migrated.fleet_view()
            self.assertEqual(view2["sessions"][0]["usage"]["effective"], 5)
            cost = migrated.cost_view(days=30)
            dev = next(d for d in cost["devices"] if d["device_id"] == "local")
            self.assertEqual(dev["total_effective"], 5)
            self.assertEqual(len(migrated.live_alerts()), 1)
        finally:
            migrated.close()


class DeviceUsagePartialColumnTest(unittest.TestCase):
    """W4/integration: usage_partial added to a `devices` table that
    already exists, same additive ALTER TABLE path as every other new
    column in this file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_usage_partial_added_to_a_pre_existing_devices_table(self):
        old_db = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(old_db)
        conn.executescript("""
            CREATE TABLE devices (
                id TEXT PRIMARY KEY, name TEXT, role TEXT, version TEXT,
                claude_version TEXT, last_seen REAL, online INTEGER DEFAULT 0
            );
        """)
        conn.execute(
            "INSERT INTO devices (id, name, role, version, claude_version, "
            "last_seen, online) VALUES ('local', 'hub', 'full', '1', '1', 100.0, 1)")
        conn.commit()
        conn.close()
        os.chmod(old_db, 0o600)

        migrated = store.Store(old_db)
        try:
            view = migrated.fleet_view()
            dev = next(d for d in view["devices"] if d["id"] == "local")
            # The pre-existing row survives untouched; the new column
            # comes back NULL for it (no data to backfill from).
            self.assertIsNone(dev["usage_partial"])

            migrated.upsert_device({"id": "local", "name": "hub", "role": "full",
                                     "version": "2", "claude_version": "1",
                                     "usage_partial": True})
            view2 = migrated.fleet_view()
            dev2 = next(d for d in view2["devices"] if d["id"] == "local")
            self.assertEqual(dev2["usage_partial"], 1)
        finally:
            migrated.close()


class AlertsTargetTypeNameColumnsTest(unittest.TestCase):
    """Fix round 1 (CONTRACT.md amendment): target_type/name added to an
    `alerts` table that already exists. Once any hub.db has run a store.py
    from between the original alerts table (session_usage/cost_daily/alerts
    all landing in the same release) and this fix, `alerts` is no longer
    guaranteed brand-new -- CREATE TABLE IF NOT EXISTS is a no-op against
    it, so this must be the same additive ALTER TABLE path as
    _NEW_SESSION_COLUMNS/_migrate_session_columns, not a schema change."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_target_type_and_name_added_to_a_pre_existing_alerts_table(self):
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
                pid INTEGER, tmux TEXT, rc_url TEXT, tokens INTEGER, claude TEXT,
                status TEXT,
                PRIMARY KEY (device_id, session_id)
            );
            CREATE TABLE session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, session_id TEXT,
                ts REAL, event TEXT, extra_json TEXT
            );
            CREATE UNIQUE INDEX idx_events_unique
                ON session_events(device_id, session_id, ts, event);
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT,
                target TEXT, device_id TEXT, detail TEXT
            );
            CREATE TABLE session_usage (
                device_id TEXT, session_id TEXT,
                input INTEGER, cache_read INTEGER, cache_write INTEGER,
                output INTEGER, effective INTEGER,
                last_ts REAL, updated_at REAL,
                PRIMARY KEY (device_id, session_id)
            );
            CREATE TABLE cost_daily (
                device_id TEXT, day TEXT, project TEXT,
                input INTEGER, cache_read INTEGER, cache_write INTEGER,
                output INTEGER, effective INTEGER, updated_at REAL,
                PRIMARY KEY (device_id, day, project)
            );
            -- Pre-fix-round alerts: exactly the original 10 columns, no
            -- target_type/name -- the shape this task's own first commit
            -- produced, before this fix round.
            CREATE TABLE alerts (
                device_id TEXT, session_id TEXT, rule TEXT,
                severity TEXT, message TEXT, value REAL, threshold REAL,
                since REAL, first_seen REAL, last_seen REAL,
                PRIMARY KEY (device_id, session_id, rule)
            );
        """)
        conn.execute(
            "INSERT INTO alerts (device_id, session_id, rule, severity, message, value, "
            "threshold, since, first_seen, last_seen) VALUES "
            "('local', 's1', 'token_rate', 'alert', 'old row', 5.0, 2.0, 100.0, 200.0, 300.0)")
        conn.commit()
        conn.close()
        os.chmod(old_db, 0o600)

        migrated = store.Store(old_db)
        try:
            alerts = migrated.live_alerts()
            self.assertEqual(len(alerts), 1)
            # The pre-existing row survives untouched; the two new columns
            # come back as NULL for it (no data to backfill from).
            self.assertEqual(alerts[0]["message"], "old row")
            self.assertEqual(alerts[0]["first_seen"], 200.0)
            self.assertIsNone(alerts[0]["target_type"])
            self.assertIsNone(alerts[0]["name"])

            # The columns are usable going forward: replace_alerts on the
            # SAME key (device_id, session_id, rule) sets them and still
            # preserves the pre-existing row's first_seen.
            migrated.replace_alerts([
                {"rule": "token_rate", "severity": "alert", "target_type": "session",
                 "device_id": "local", "session_id": "s1", "name": "rc-old",
                 "message": "updated", "value": 6.0, "threshold": 2.0, "since": 150.0},
            ], now_fn=lambda: 400.0)
            alerts2 = migrated.live_alerts()
            self.assertEqual(len(alerts2), 1)
            self.assertEqual(alerts2[0]["target_type"], "session")
            self.assertEqual(alerts2[0]["name"], "rc-old")
            self.assertEqual(alerts2[0]["first_seen"], 200.0)  # preserved
            self.assertEqual(alerts2[0]["last_seen"], 400.0)
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
