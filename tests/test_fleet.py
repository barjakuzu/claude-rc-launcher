import datetime
import json
import os
import time
import unittest
from unittest.mock import patch

import fleet

# A realistic "now" (matching the day strings used throughout these
# tests, all on or before 2026-09-07) rather than an arbitrary small
# epoch value: fleet.py's future-date guard (see _usage_daily_rows /
# _usage_daily_by_project_rows) compares day strings against today's
# UTC date derived from `now`, and a tiny epoch value (e.g. 5000.0,
# which is 1970-01-01) would make every 2026 day string look
# "future-dated" and get silently filtered before a test ever exercises
# the code path it's trying to cover.
_FIXED_NOW = datetime.datetime(2026, 9, 7, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()


def _empty_rollup(now=_FIXED_NOW):
    """A rollup() result with no transcript data at all: every test that
    doesn't care about usage specifically gets this so build_fleet's
    usage-shaped output stays deterministic instead of touching the real
    filesystem through an unmocked usage.rollup."""
    return {
        "sessions": {},
        "daily": {},
        "daily_by_project": [],
        "daily_by_project_capped": False,
        "generated_at": now,
        "files": 0,
        "bytes_read": 0,
        "skipped": 0,
        "partial": False,
    }


_EMPTY_LIMITS = {
    "available": False, "fetched_at": 0.0, "five_hour": None, "seven_day": None,
    "scoped": [], "spend": None, "extra_usage": None, "error": None,
}


class BuildFleetTest(unittest.TestCase):
    def setUp(self):
        fleet._cache.clear()
        fleet._last_prune_at = None
        # SECURITY (CONTRACT.md section 2): fleet.build_fleet() now calls
        # limits.get_limits(), which -- left unmocked -- would read this
        # machine's REAL ~/.claude/.credentials.json and attempt a REAL
        # authenticated call to api.anthropic.com. Patched here, once, for
        # every test in this class, rather than added to each test
        # method's own @patch stack individually, so no test written
        # before limits.py existed can silently regress into touching
        # real credentials just by calling build_fleet() the way it
        # always has. Tests that actually exercise the limits integration
        # override self._limits_mock's return_value/side_effect
        # explicitly -- see BuildFleetLimitsTest below.
        limits_patcher = patch("fleet.limits.get_limits")
        self._limits_mock = limits_patcher.start()
        self._limits_mock.return_value = _EMPTY_LIMITS
        self.addCleanup(limits_patcher.stop)

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_full_role_carries_everything(self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {"version": "2.1.263", "agents_json": True}
        list_sess.return_value = [
            {"name": "rc-foo", "session_id": "s1", "cwd": "/home/alice/proj", "state": "idle"},
        ]
        read_ev.return_value = ([{"ts": 1, "event": "Stop", "session_id": "s1", "extra": {}}], "f.jsonl:10")
        rollup.return_value = _empty_rollup(5000.0)

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["device_name"], "hub")
        self.assertEqual(result["role"], "full")
        self.assertEqual(result["version"], fleet.config.VERSION)
        self.assertEqual(result["claude_version"], "2.1.263")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["cwd"], "/home/alice/proj")
        self.assertEqual(result["cursor"], "f.jsonl:10")
        self.assertEqual(result["generated_at"], 5000.0)
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["sessions"][0]["usage"])
        self.assertEqual(result["usage_daily"], [])
        self.assertEqual(result["usage_daily_by_project"], [])
        self.assertEqual(
            result["usage_meta"],
            {"files": 0, "skipped": 0, "partial": False, "generated_at": 5000.0,
             "projects_capped": False})

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_metadata_role_strips_cwd_tmux_claude_and_hashes(self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "work-mac"
        get_caps.return_value = {"version": "2.1.263", "agents_json": True}
        list_sess.return_value = [{
            "name": "rc-secret-project", "session_id": "s1", "cwd": "/Users/alice/work",
            "state": "idle", "tmux": {"pane_id": "%1"}, "claude": {"pid": 123}, "tokens": 5000,
        }]
        read_ev.return_value = ([{"ts": 1, "event": "SessionStart", "session_id": "s1", "extra": {"source": "startup"}}], "f.jsonl:5")
        rollup.return_value = _empty_rollup(5000.0)

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        row = result["sessions"][0]
        self.assertNotIn("cwd", row)
        self.assertNotIn("tmux", row)
        self.assertNotIn("claude", row)
        self.assertNotIn("tokens", row)
        self.assertEqual(
            set(row.keys()),
            {"session_id", "name", "state", "started_at", "kind", "status", "usage"})
        self.assertIsNone(row["usage"])
        self.assertNotEqual(row["session_id"], "s1")
        self.assertNotEqual(row["name"], "rc-secret-project")
        self.assertNotIn("usage_daily_by_project", result)
        ev = result["events"][0]
        self.assertEqual(set(ev.keys()), {"ts", "event"})

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_cached_for_5_seconds_per_since_and_role(self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(1000.0)
        now = {"t": 1000.0}
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        now["t"] = 1002.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(list_sess.call_count, 1)
        now["t"] = 1006.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(list_sess.call_count, 2)

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_metadata_role_errors_carry_only_exception_type_name(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.side_effect = OSError("/home/alice/secret/path: permission denied")
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("OSError", result["errors"][0])
        self.assertNotIn("/home/alice", result["errors"][0])
        self.assertNotIn("permission denied", result["errors"][0])

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_full_role_errors_keep_exception_text(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.side_effect = OSError("boom detail")
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("boom detail", result["errors"][0])

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_cache_keeps_only_newest_entry_per_role(self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(1000.0)

        fleet.build_fleet(role="full", since=None, now_fn=lambda: 1000.0)
        fleet.build_fleet(role="full", since="cursor-a", now_fn=lambda: 1000.0)
        fleet.build_fleet(role="full", since="cursor-b", now_fn=lambda: 1000.0)

        full_keys = [k for k in fleet._cache if k[1] == "full"]
        self.assertEqual(len(full_keys), 1)
        self.assertEqual(full_keys[0], ("cursor-b", "full"))

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_prune_invoked_at_most_once_per_hour(self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(1000.0)

        now = {"t": 1000.0}
        fleet.build_fleet(role="full", since="a", now_fn=lambda: now["t"])
        self.assertEqual(prune.call_count, 1)

        now["t"] = 1000.0 + 60
        fleet.build_fleet(role="full", since="b", now_fn=lambda: now["t"])
        self.assertEqual(prune.call_count, 1)

        now["t"] = 1000.0 + fleet.PRUNE_INTERVAL_SECONDS + 1
        fleet.build_fleet(role="full", since="c", now_fn=lambda: now["t"])
        self.assertEqual(prune.call_count, 2)

    # -- usage wiring -----------------------------------------------------

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_session_with_transcript_data_gets_contract_shape(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "s1", "state": "idle"}]
        read_ev.return_value = ([], None)
        rollup.return_value = {
            "sessions": {
                "s1": {
                    "session_id": "s1", "project": "-home-alice-proj",
                    "input": 7214, "cache_read": 323420876, "cache_write": 4446439,
                    "output": 423997, "effective": 43361000,
                    "first_ts": 100.0, "last_ts": 1788786360.5,
                    "models": {"claude-x": 3}, "messages": 3,
                },
            },
            "daily": {},
            "daily_by_project": [],
            "daily_by_project_capped": False,
            "generated_at": 5000.0,
            "files": 1, "bytes_read": 10, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["sessions"][0]["usage"], {
            "input": 7214, "cache_read": 323420876, "cache_write": 4446439,
            "output": 423997, "effective": 43361000, "last_ts": 1788786360.5,
        })

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_session_with_no_transcript_data_gets_none_not_zeros(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "s1", "state": "idle"}]
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertIsNone(result["sessions"][0]["usage"])

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_tmux_derived_session_id_gets_none_without_raising(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "tmux:rc-foo", "state": "idle"}]
        read_ev.return_value = ([], None)
        # A real transcript session exists under its own UUID, unrelated
        # to the tmux-derived id the adopted row carries.
        rollup.return_value = {
            "sessions": {
                "11111111-1111-1111-1111-111111111111": {
                    "session_id": "11111111-1111-1111-1111-111111111111",
                    "project": "-home-alice-proj",
                    "input": 1, "cache_read": 1, "cache_write": 1, "output": 1,
                    "effective": 8, "first_ts": 1.0, "last_ts": 2.0,
                    "models": {}, "messages": 1,
                },
            },
            "daily": {}, "daily_by_project": [], "daily_by_project_capped": False,
            "generated_at": 5000.0,
            "files": 1, "bytes_read": 1, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertIsNone(result["sessions"][0]["usage"])

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_usage_daily_capped_at_30_and_newest_first(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)

        base = datetime.date(2026, 9, 7)
        daily = {}
        for i in range(35):
            day = (base - datetime.timedelta(days=i)).isoformat()
            daily[day] = {"input": i, "cache_read": 0, "cache_write": 0, "output": 0, "effective": i}
        rollup.return_value = {
            "sessions": {}, "daily": daily, "daily_by_project": [], "daily_by_project_capped": False,
            "generated_at": 5000.0,
            "files": 35, "bytes_read": 0, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: _FIXED_NOW)

        self.assertEqual(len(result["usage_daily"]), 30)
        self.assertEqual(result["usage_daily"][0]["day"], base.isoformat())
        self.assertEqual(
            result["usage_daily"][-1]["day"],
            (base - datetime.timedelta(days=29)).isoformat())
        days = [row["day"] for row in result["usage_daily"]]
        self.assertEqual(days, sorted(days, reverse=True))

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_future_dated_days_are_dropped_not_just_deprioritised(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        # Fix round 1, Minor: a device with clock skew reporting days
        # after "today" must not have those bogus days evict real ones
        # out of the capped 30-day usage_daily window.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)

        base = datetime.date(2026, 9, 7)  # "today" per _FIXED_NOW
        daily = {}
        for i in range(30):  # 30 real days, oldest at i=29
            day = (base - datetime.timedelta(days=i)).isoformat()
            daily[day] = {"input": 1, "cache_read": 0, "cache_write": 0, "output": 0, "effective": 1}
        for i in range(1, 6):  # 5 future-dated days (clock skew)
            day = (base + datetime.timedelta(days=i)).isoformat()
            daily[day] = {"input": 9, "cache_read": 0, "cache_write": 0, "output": 0, "effective": 9}
        rollup.return_value = {
            "sessions": {}, "daily": daily, "daily_by_project": [], "daily_by_project_capped": False,
            "generated_at": 5000.0,
            "files": 35, "bytes_read": 0, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: _FIXED_NOW)

        days = [row["day"] for row in result["usage_daily"]]
        self.assertEqual(len(days), 30)
        self.assertEqual(days[0], base.isoformat())  # newest real day, not a future one
        self.assertEqual(days[-1], (base - datetime.timedelta(days=29)).isoformat())  # oldest real day preserved
        self.assertTrue(all(d <= base.isoformat() for d in days))

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_metadata_role_redacts_usage_and_drops_project_name(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "s1", "state": "idle"}]
        read_ev.return_value = ([], None)
        rollup.return_value = {
            "sessions": {
                "s1": {
                    "session_id": "s1", "project": "-home-alice-super-secret-client",
                    "input": 1, "cache_read": 2, "cache_write": 3, "output": 4,
                    "effective": 50, "first_ts": 1.0, "last_ts": 2.0,
                    "models": {}, "messages": 1,
                },
            },
            "daily": {
                "2026-09-07": {"input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "effective": 50},
            },
            "daily_by_project": [
                {"day": "2026-09-07", "project": "-home-alice-super-secret-client",
                 "input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "effective": 50},
            ],
            "daily_by_project_capped": False,
            "generated_at": 5000.0,
            "files": 1, "bytes_read": 1, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="metadata", now_fn=lambda: _FIXED_NOW)

        self.assertEqual(result["sessions"][0]["usage"], {"effective": 50})
        self.assertEqual(result["usage_daily"], [{"day": "2026-09-07", "effective": 50}])
        # usage_meta is sent unchanged under metadata role (counts only).
        self.assertEqual(
            result["usage_meta"],
            {"files": 1, "skipped": 0, "partial": False, "generated_at": 5000.0,
             "projects_capped": False})
        # A project name is the cwd by another name: the whole key is
        # dropped under metadata role, not merely reduced.
        self.assertNotIn("usage_daily_by_project", result)
        payload = json.dumps(result)
        self.assertNotIn("super-secret-client", payload)

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_usage_daily_by_project_full_role_shape(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = {
            "sessions": {}, "daily": {},
            "daily_by_project": [
                {"day": "2026-09-06", "project": "-var-www",
                 "input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "effective": 43},
                {"day": "2026-09-07", "project": "-home-alice-proj",
                 "input": 5, "cache_read": 6, "cache_write": 7, "output": 8, "effective": 90},
            ],
            "daily_by_project_capped": False,
            "generated_at": 5000.0, "files": 2, "bytes_read": 1, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: _FIXED_NOW)

        self.assertEqual(result["usage_daily_by_project"], [
            {"day": "2026-09-07", "project": "-home-alice-proj",
             "input": 5, "cache_read": 6, "cache_write": 7, "output": 8, "effective": 90},
            {"day": "2026-09-06", "project": "-var-www",
             "input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "effective": 43},
        ])

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_projects_capped_flag_surfaces_in_usage_meta(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        # Fix round 2: usage.rollup() caps daily_by_project's total row
        # count and reports whether it actually truncated this call's
        # list. fleet.py must forward that as usage_meta.projects_capped
        # so a truncated projects list never quietly looks complete.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = {
            "sessions": {}, "daily": {}, "daily_by_project": [],
            "daily_by_project_capped": True,
            "generated_at": 5000.0, "files": 1, "bytes_read": 1, "skipped": 0, "partial": False,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(
            result["usage_meta"],
            {"files": 1, "skipped": 0, "partial": False, "generated_at": 5000.0,
             "projects_capped": True})

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_usage_rollup_raising_does_not_break_build_fleet(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "s1", "state": "idle"}]
        read_ev.return_value = ([], None)
        rollup.side_effect = RuntimeError("boom /home/alice detail")

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertIsNone(result["sessions"][0]["usage"])
        self.assertEqual(result["usage_daily"], [])
        self.assertEqual(result["usage_daily_by_project"], [])
        self.assertEqual(
            result["usage_meta"],
            {"files": 0, "skipped": 0, "partial": True, "generated_at": 5000.0,
             "projects_capped": False})
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("boom /home/alice detail", result["errors"][0])

        fleet._cache.clear()
        result_meta = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)
        self.assertEqual(len(result_meta["errors"]), 1)
        self.assertIn("RuntimeError", result_meta["errors"][0])
        self.assertNotIn("boom", result_meta["errors"][0])
        self.assertNotIn("/home/alice", result_meta["errors"][0])
        self.assertIsNone(result_meta["sessions"][0]["usage"])
        self.assertEqual(
            result_meta["usage_meta"],
            {"files": 0, "skipped": 0, "partial": True, "generated_at": 5000.0,
             "projects_capped": False})
        self.assertNotIn("usage_daily_by_project", result_meta)

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_every_malformed_rollup_return_leaves_build_fleet_working(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        # Fix round 1, Important 1: build_fleet's try/except must guard
        # CONSUMPTION of usage.rollup()'s result, not just the call.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "s1", "state": "idle"}]
        read_ev.return_value = ([], None)

        malformed = [
            None,
            [],
            "not a dict",
            {},
            {"sessions": None},
            {"sessions": []},
            {"sessions": {"s1": "not-a-dict"}},
            {"sessions": {"s1": {"input": 1}}},  # missing cache_read/cache_write/output/effective/last_ts
            {"sessions": {}, "daily": None},
            {"sessions": {}, "daily": []},
            {"sessions": {}, "daily": {"2026-09-07": "not-a-dict"}},
            {"sessions": {}, "daily": {"2026-09-07": {"input": 1}}},  # missing effective etc
            {"sessions": {}, "daily": {}, "daily_by_project": None},
            {"sessions": {}, "daily": {}, "daily_by_project": {}},
            {"sessions": {}, "daily": {}, "daily_by_project": ["not-a-dict"]},
            {"sessions": {}, "daily": {}, "daily_by_project": [{"day": "2026-09-07"}]},  # missing project
            {"sessions": {}, "daily": {}, "daily_by_project": [], "files": 1},  # missing skipped/partial/generated_at
            {"sessions": {}, "daily": {}, "daily_by_project": []},  # missing files/skipped/partial/generated_at
            {"sessions": {}, "daily": {}, "daily_by_project": [],  # missing daily_by_project_capped
             "files": 0, "skipped": 0, "partial": False, "generated_at": 1.0},
        ]

        for i, bad in enumerate(malformed):
            with self.subTest(i=i, bad=repr(bad)[:60]):
                fleet._cache.clear()
                rollup.return_value = bad
                result = fleet.build_fleet(role="full", now_fn=lambda: _FIXED_NOW)
                self.assertIsNone(result["sessions"][0]["usage"])
                self.assertEqual(result["usage_daily"], [])
                self.assertEqual(result["usage_daily_by_project"], [])
                self.assertEqual(
                    result["usage_meta"],
                    {"files": 0, "skipped": 0, "partial": True, "generated_at": _FIXED_NOW,
                     "projects_capped": False})
                self.assertTrue(
                    any(e.startswith("usage:") for e in result["errors"]),
                    "expected a usage error, got %r" % (result["errors"],))

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_cache_honoured_usage_rollup_called_once_per_window(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(1000.0)

        now = {"t": 1000.0}
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        now["t"] = 1002.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(rollup.call_count, 1)
        now["t"] = 1006.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(rollup.call_count, 2)

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_partial_rollup_surfaces_in_usage_meta(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = {
            "sessions": {}, "daily": {}, "daily_by_project": [], "daily_by_project_capped": False,
            "generated_at": 5000.0,
            "files": 10, "bytes_read": 999, "skipped": 2, "partial": True,
        }

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(
            result["usage_meta"],
            {"files": 10, "skipped": 2, "partial": True, "generated_at": 5000.0,
             "projects_capped": False})

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_rollup_called_once_with_explicit_budget_regardless_of_session_count(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [
            {"name": "rc-a", "session_id": "s1", "state": "idle"},
            {"name": "rc-b", "session_id": "s2", "state": "idle"},
            {"name": "rc-c", "session_id": "s3", "state": "idle"},
        ]
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)

        fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(rollup.call_count, 1)
        _, kwargs = rollup.call_args
        self.assertEqual(kwargs["max_bytes_per_call"], fleet.usage.DEFAULT_MAX_BYTES_PER_CALL)
        self.assertEqual(kwargs["now_fn"](), 5000.0)
        self.assertEqual(kwargs["days"], fleet.USAGE_DAILY_MAX_DAYS)

    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_pathological_now_does_not_break_build_fleet(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup):
        # Fix round 3, Minor: _today_str(now) used to run OUTSIDE the
        # usage try block. A NaN or a clock past year 9999 makes it raise
        # on its own, before usage.rollup() is even called, which is
        # exactly the class of bug Important 1 (fix round 1) exists to
        # prevent. Covers both raise shapes seen for a bad `now`
        # (ValueError for NaN, OverflowError for an out-of-range clock).
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = [{"name": "rc-foo", "session_id": "s1", "state": "idle"}]
        read_ev.return_value = ([], None)

        for bad_now in (float("nan"), 1e20):
            with self.subTest(bad_now=bad_now):
                fleet._cache.clear()
                rollup.return_value = _empty_rollup(bad_now)
                result = fleet.build_fleet(role="full", now_fn=lambda: bad_now)
                self.assertIsNone(result["sessions"][0]["usage"])
                self.assertEqual(result["usage_daily"], [])
                self.assertEqual(result["usage_daily_by_project"], [])
                self.assertTrue(result["usage_meta"]["partial"])
                self.assertFalse(result["usage_meta"]["projects_capped"])
                self.assertTrue(
                    any(e.startswith("usage:") for e in result["errors"]),
                    "expected a usage error, got %r" % (result["errors"],))


class BuildFleetLimitsTest(unittest.TestCase):
    """CONTRACT.md sections 2-3: build_fleet()'s top-level `limits` key.
    limits.get_limits() itself is mocked throughout -- see
    BuildFleetTest.setUp's docstring for why that is mandatory here, not
    just convenient: an unmocked call would read this machine's real
    credentials file."""

    def setUp(self):
        fleet._cache.clear()
        fleet._last_prune_at = None

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_limits_passed_through_unchanged_under_full_role(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        available_limits = {
            "available": True, "fetched_at": 5000.0,
            "five_hour": {"percent": 56.0, "resets_at": "2026-09-08T00:40:00+00:00"},
            "seven_day": {"percent": 80.0, "resets_at": "2026-09-08T07:00:00+00:00"},
            "scoped": [{"kind": "weekly_scoped", "group": "weekly", "percent": 42.0,
                        "severity": "normal", "resets_at": "r", "label": "Fable",
                        "is_active": False}],
            "spend": {"used_minor": 0, "currency": "USD", "exponent": 2,
                      "limit_minor": None, "percent": 0.0, "severity": "normal"},
            "extra_usage": {"enabled": False, "utilization": None,
                             "spend_limit_reached": False},
            "error": None,
        }
        get_limits.return_value = available_limits

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["limits"], available_limits)
        # now_fn passed through unchanged, same pattern as usage.rollup's
        # own now_fn kwarg elsewhere in this file.
        self.assertEqual(get_limits.call_args.kwargs["now_fn"](), 5000.0)

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_limits_sent_unchanged_under_metadata_role_too(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # CONTRACT.md section 3: "Under role == metadata the whole limits
        # key is sent unchanged... it describes the account, not the
        # machine" -- unlike sessions/events/usage_daily, no redaction.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
        get_limits.return_value = available_limits

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        self.assertEqual(result["limits"], available_limits)

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_limits_get_limits_raising_never_breaks_build_fleet(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        get_limits.side_effect = RuntimeError("boom")

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertFalse(result["limits"]["available"])
        self.assertEqual(result["limits"]["error"], "RuntimeError")

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_limits_error_in_errors_list_is_type_name_only_even_under_full_role(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # SECURITY (CONTRACT.md section 2): every OTHER error source in
        # build_fleet's `errors` list carries the real exception text
        # under full role (see test_full_role_errors_keep_exception_text
        # in BuildFleetTest above) -- "limits" is the one deliberate
        # exception to that. A urllib exception raised this close to the
        # OAuth token/HTTPS call can embed the request (headers included)
        # in its string form, so this must be forced to type(e).__name__
        # regardless of role, as defense in depth beyond limits.py's own
        # internal never-raise guarantee.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        get_limits.side_effect = RuntimeError(
            "GET https://api.anthropic.com/api/oauth/usage failed, "
            "Authorization: Bearer TEST-FIXTURE-NOT-A-REAL-TOKEN-xyz789")

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["errors"], ["limits: RuntimeError"])
        for err in result["errors"]:
            self.assertNotIn("TEST-FIXTURE-NOT-A-REAL-TOKEN-xyz789", err)
            self.assertNotIn("Bearer", err)
            self.assertNotIn("Authorization", err)

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_limits_get_limits_returning_non_dict_is_also_type_name_only(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        get_limits.return_value = "not a dict"

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertFalse(result["limits"]["available"])
        self.assertEqual(result["limits"]["error"], "TypeError")
        self.assertEqual(result["errors"], ["limits: TypeError"])


class IsLimitsHubTest(unittest.TestCase):
    """Task L5: is_limits_hub() is the explicit, operator-visible signal
    deciding whether THIS device calls the account limits endpoint at
    all. `env` is always passed explicitly here -- never the real
    os.environ -- so these tests can't be polluted by (or leak into) the
    actual process environment."""

    def test_unset_defaults_to_fetch(self):
        self.assertTrue(fleet.is_limits_hub(env={}))

    def test_explicit_off_values_disable_fetching(self):
        for value in ("0", "false", "False", "NO", "off", "Off"):
            self.assertFalse(
                fleet.is_limits_hub(env={"RC_FETCH_LIMITS": value}),
                f"{value!r} should disable fetching")

    def test_any_other_value_still_fetches(self):
        for value in ("1", "true", "yes", "on", "hub"):
            self.assertTrue(
                fleet.is_limits_hub(env={"RC_FETCH_LIMITS": value}),
                f"{value!r} should not disable fetching")

    def test_whitespace_around_off_value_is_tolerated(self):
        self.assertFalse(fleet.is_limits_hub(env={"RC_FETCH_LIMITS": "  0  "}))


class BuildFleetLimitsHubGatingTest(unittest.TestCase):
    """Task L5: 'A device only calls the API when it is acting as the
    hub' + 'A non-fetching device omits the limits key from its payload
    entirely, rather than sending available: false.'"""

    def setUp(self):
        fleet._cache.clear()
        fleet._last_prune_at = None

    @patch("fleet.is_limits_hub")
    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_non_hub_device_omits_limits_key_and_never_calls_get_limits(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup,
            get_limits, is_hub):
        get_name.return_value = "satellite"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        is_hub.return_value = False

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertNotIn("limits", result)
        get_limits.assert_not_called()

    @patch("fleet.is_limits_hub")
    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_non_hub_device_omits_limits_key_under_metadata_role_too(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup,
            get_limits, is_hub):
        get_name.return_value = "satellite"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        is_hub.return_value = False

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        self.assertNotIn("limits", result)
        get_limits.assert_not_called()

    @patch("fleet.is_limits_hub")
    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_hub_device_still_fetches_and_carries_limits_key(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup,
            get_limits, is_hub):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        is_hub.return_value = True
        available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
        get_limits.return_value = available_limits

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["limits"], available_limits)
        get_limits.assert_called_once()

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_default_env_still_fetches_zero_config(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # fleet.is_limits_hub is deliberately NOT patched here: with
        # RC_FETCH_LIMITS unset in the real process environment (the
        # normal case for every single-device install, and for CI),
        # build_fleet must keep fetching exactly as it always has -- zero
        # new configuration required. patch.dict restores whatever the
        # real environment had afterwards; the pop only affects this
        # block.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RC_FETCH_LIMITS", None)
            get_name.return_value = "hub"
            get_caps.return_value = {}
            list_sess.return_value = []
            read_ev.return_value = ([], None)
            rollup.return_value = _empty_rollup(5000.0)
            available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
            get_limits.return_value = available_limits

            result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

            self.assertEqual(result["limits"], available_limits)
            get_limits.assert_called_once()


if __name__ == "__main__":
    unittest.main()
