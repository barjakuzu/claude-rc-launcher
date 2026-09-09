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
        # Task L5 fix round 1: is_limits_hub()'s auto-detect default reads
        # devices.load_devices() (see fleet.py), which -- left unmocked --
        # reads this MACHINE's real ~/.claude-rc/devices.json. None of
        # this class's tests are about limits/hub-gating at all, so this
        # is patched to a fixed, empty stand-in purely for determinism:
        # without it, every test in this class would silently behave
        # differently depending on whether the box running the suite
        # happens to have a real devices.json (this repo's own dev box
        # does), even though no assertion here reads "limits" or cares
        # which way is_limits_hub() resolves.
        devices_patcher = patch("fleet.devices.load_devices")
        self._load_devices_mock = devices_patcher.start()
        self._load_devices_mock.return_value = []
        self.addCleanup(devices_patcher.stop)
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
        # Task L5 fix round 1: every test in this class is about what
        # build_fleet() produces WHEN it fetches, not about the
        # auto-detect default that decides WHETHER it fetches (that's
        # IsLimitsHubTest / BuildFleetLimitsHubGatingTest below) --
        # devices.load_devices() is patched to a non-empty stand-in
        # ("this device has other devices configured, so it fetches")
        # for every test here, deterministic regardless of whatever
        # devices.json the machine running the suite happens to have.
        devices_patcher = patch("fleet.devices.load_devices")
        self._load_devices_mock = devices_patcher.start()
        self._load_devices_mock.return_value = [{"id": "peer", "base_url": "http://peer:8200"}]
        self.addCleanup(devices_patcher.stop)

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
    """Task L5 fix round 2: is_limits_hub() trusts a hub's own
    HUB_POLL_HEADER marker (relayed here via note_hub_poll(), or the
    `polled_by_hub_recently` seam directly) over any local inference --
    fetch UNLESS a hub has said "you are my satellite" recently. The
    default direction is now the opposite of round 1: absent any signal,
    this device fetches, which is what keeps a standalone install
    (nobody ever polls it) working with zero configuration.

    `env` and `polled_by_hub_recently` are always passed explicitly here
    -- never the real os.environ / the real module-level poll-marker
    state -- so these tests can't be polluted by (or leak into) each
    other or the actual process."""

    def tearDown(self):
        fleet._last_hub_poll_at = None

    # --- the marker (RC_FETCH_LIMITS unset) ---

    def test_no_marker_at_all_fetches(self):
        # Covers a standalone install with no fleet, AND a satellite
        # whose hub hasn't reached it yet (startup, or the hub itself
        # not yet upgraded) -- both fetch, by design: absent a signal,
        # assume nobody else is doing it.
        self.assertTrue(fleet.is_limits_hub(env={}, polled_by_hub_recently=False))

    def test_recent_marker_does_not_fetch(self):
        self.assertFalse(fleet.is_limits_hub(env={}, polled_by_hub_recently=True))

    def test_default_arg_reads_real_poll_marker_state(self):
        # polled_by_hub_recently=None (the default) falls through to a
        # real _polled_by_hub_recently() call against the real module
        # state -- proven here via note_hub_poll() using a real
        # timestamp (so is_limits_hub()'s own default now_fn=time.time
        # sees it as recent too), reset by tearDown, never leaking into
        # any other test.
        self.assertTrue(fleet.is_limits_hub(env={}))  # no poll ever recorded
        fleet.note_hub_poll()  # real time.time(), right now
        self.assertFalse(fleet.is_limits_hub(env={}))

    # --- explicit override, either direction, wins regardless of the marker ---

    def test_explicit_on_values_force_fetch_even_with_a_recent_marker(self):
        for value in ("1", "true", "True", "YES", "on", "On"):
            self.assertTrue(
                fleet.is_limits_hub(env={"RC_FETCH_LIMITS": value}, polled_by_hub_recently=True),
                f"{value!r} should force fetching on")

    def test_explicit_off_values_force_no_fetch_even_with_no_marker(self):
        for value in ("0", "false", "False", "NO", "off", "Off"):
            self.assertFalse(
                fleet.is_limits_hub(env={"RC_FETCH_LIMITS": value}, polled_by_hub_recently=False),
                f"{value!r} should force fetching off")

    def test_whitespace_around_override_value_is_tolerated(self):
        self.assertFalse(
            fleet.is_limits_hub(env={"RC_FETCH_LIMITS": "  0  "}, polled_by_hub_recently=False))
        self.assertTrue(
            fleet.is_limits_hub(env={"RC_FETCH_LIMITS": "  1  "}, polled_by_hub_recently=True))

    def test_unrecognised_value_falls_through_to_the_marker(self):
        self.assertTrue(
            fleet.is_limits_hub(env={"RC_FETCH_LIMITS": "banana"}, polled_by_hub_recently=False))
        self.assertFalse(
            fleet.is_limits_hub(env={"RC_FETCH_LIMITS": "banana"}, polled_by_hub_recently=True))


class HubPollMarkerTest(unittest.TestCase):
    """Task L5 fix round 2: note_hub_poll()/_polled_by_hub_recently()
    are the module-level state is_limits_hub() reads by default. Tested
    directly here (rather than only indirectly through is_limits_hub's
    seam) so the staleness window itself, and note_hub_poll's own
    now/now_fn handling, are covered on their own."""

    def tearDown(self):
        fleet._last_hub_poll_at = None

    def test_never_polled_is_not_recent(self):
        self.assertIsNone(fleet._last_hub_poll_at)
        self.assertFalse(fleet._polled_by_hub_recently(now_fn=lambda: 1000.0))

    def test_just_polled_is_recent(self):
        fleet.note_hub_poll(now=1000.0)
        self.assertTrue(fleet._polled_by_hub_recently(now_fn=lambda: 1000.0))
        self.assertTrue(fleet._polled_by_hub_recently(
            now_fn=lambda: 1000.0 + fleet.HUB_POLL_STALE_SECONDS - 1))

    def test_marker_goes_stale_after_the_window(self):
        fleet.note_hub_poll(now=1000.0)
        # stale_seconds pinned explicitly (fix round 3 added per-device
        # jitter on top of the base window -- see HubPollJitterTest for
        # that) so this boundary stays exact regardless of whatever
        # config.RC_HASH_SALT the machine running the suite happens to
        # have.
        self.assertFalse(fleet._polled_by_hub_recently(
            now_fn=lambda: 1000.0 + fleet.HUB_POLL_STALE_SECONDS,
            stale_seconds=fleet.HUB_POLL_STALE_SECONDS))
        self.assertFalse(fleet._polled_by_hub_recently(
            now_fn=lambda: 1000.0 + fleet.HUB_POLL_STALE_SECONDS + 60,
            stale_seconds=fleet.HUB_POLL_STALE_SECONDS))

    def test_note_hub_poll_defaults_to_now_fn(self):
        fleet.note_hub_poll(now_fn=lambda: 4242.0)
        self.assertEqual(fleet._last_hub_poll_at, 4242.0)

    def test_a_later_poll_extends_the_window(self):
        fleet.note_hub_poll(now=1000.0)
        fleet.note_hub_poll(now=1000.0 + fleet.HUB_POLL_STALE_SECONDS - 1)
        # Still recent relative to the SECOND poll, even though the
        # first one alone would have just gone stale by this clock.
        self.assertTrue(fleet._polled_by_hub_recently(
            now_fn=lambda: 1000.0 + fleet.HUB_POLL_STALE_SECONDS))


class HubPollJitterTest(unittest.TestCase):
    """Task L5 fix round 3: _hub_poll_jitter() spreads simultaneous hub
    markings across devices so they do not all lapse (and resume
    fetching independently) at the same instant after a hub outage --
    the original many-callers bug, rebuilt in degraded mode, that a
    fixed HUB_POLL_STALE_SECONDS alone would allow."""

    def tearDown(self):
        fleet._last_hub_poll_at = None

    def test_deterministic_for_the_same_seed(self):
        self.assertEqual(fleet._hub_poll_jitter(seed="device-a"),
                          fleet._hub_poll_jitter(seed="device-a"))

    def test_stable_across_repeated_calls_with_no_seed_too(self):
        # No seed given -> falls through to config.RC_HASH_SALT, which
        # does not change between calls in the same process (or across a
        # real restart -- it is read once into ~/.claude-rc/env).
        self.assertEqual(fleet._hub_poll_jitter(), fleet._hub_poll_jitter())

    def test_differs_across_seeds_in_the_common_case(self):
        # Not a mathematical guarantee (a hash collision is always
        # possible in principle) but overwhelmingly likely across 20
        # arbitrary seeds, and this IS the point of jitter: two devices
        # must not, in practice, land on the same offset.
        values = {fleet._hub_poll_jitter(seed=f"device-{i}") for i in range(20)}
        self.assertGreater(len(values), 1)

    def test_bounded_in_range(self):
        for i in range(50):
            value = fleet._hub_poll_jitter(seed=f"seed-{i}")
            self.assertGreaterEqual(value, 0.0)
            self.assertLess(value, fleet.HUB_POLL_JITTER_SECONDS)

    def test_empty_or_missing_seed_never_raises(self):
        fleet._hub_poll_jitter(seed="")
        fleet._hub_poll_jitter(seed=None)

    def test_polled_by_hub_recently_default_adds_jitter_on_top_of_the_base_window(self):
        with patch("fleet._hub_poll_jitter", return_value=12.5):
            fleet.note_hub_poll(now=1000.0)
            # Still within base + jitter.
            self.assertTrue(fleet._polled_by_hub_recently(
                now_fn=lambda: 1000.0 + fleet.HUB_POLL_STALE_SECONDS + 10))
            # Past base + jitter.
            self.assertFalse(fleet._polled_by_hub_recently(
                now_fn=lambda: 1000.0 + fleet.HUB_POLL_STALE_SECONDS + 13))

    def test_two_devices_marked_at_the_same_instant_lapse_at_different_times(self):
        # The actual property this round asks for: given the SAME poll
        # timestamp (as a hub polling two satellites moments apart in
        # the same cycle would produce), two devices with different
        # jitter must not both flip from "recent" to "stale" at the same
        # `now` -- there must be a `now` where one has lapsed and the
        # other has not.
        poll_at = 1000.0
        with patch("fleet._hub_poll_jitter", return_value=5.0):
            fleet.note_hub_poll(now=poll_at)
            device_a_stale_at = poll_at + fleet.HUB_POLL_STALE_SECONDS + 5.0
        with patch("fleet._hub_poll_jitter", return_value=45.0):
            fleet.note_hub_poll(now=poll_at)  # same marker timestamp
            device_b_stale_at = poll_at + fleet.HUB_POLL_STALE_SECONDS + 45.0
        self.assertNotEqual(device_a_stale_at, device_b_stale_at)

        with patch("fleet._hub_poll_jitter", return_value=45.0):
            # At device A's stale instant, device B (more jitter) is
            # still within its own, longer window -- it has not lapsed
            # yet, so it does not resume fetching at the same moment A does.
            fleet.note_hub_poll(now=poll_at)
            self.assertTrue(fleet._polled_by_hub_recently(now_fn=lambda: device_a_stale_at))


class BuildFleetLimitsHubGatingTest(unittest.TestCase):
    """Task L5: 'A device only calls the API when it is acting as the
    hub' + 'A non-fetching device omits the limits key from its payload
    entirely, rather than sending available: false.' Fix round 2: the
    signal is the hub-poll marker (note_hub_poll()), not an inference --
    these tests exercise build_fleet() end-to-end through the real
    is_limits_hub(), varying only whether a poll was ever recorded."""

    def setUp(self):
        fleet._cache.clear()
        fleet._last_prune_at = None
        fleet._last_hub_poll_at = None

    def tearDown(self):
        fleet._last_hub_poll_at = None

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_satellite_recently_polled_by_hub_omits_limits_key(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # Task L5 fix round 2, "test both": a satellite (its hub polled
        # it moments ago, RC_FETCH_LIMITS unset) does not fetch -- the
        # fix for the reported bug, requiring zero configuration on the
        # satellite once its hub is upgraded and has polled it once.
        get_name.return_value = "satellite"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        fleet.note_hub_poll(now=4990.0)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RC_FETCH_LIMITS", None)
            result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertNotIn("limits", result)
        get_limits.assert_not_called()

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_satellite_omits_limits_key_under_metadata_role_too(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        get_name.return_value = "satellite"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        fleet.note_hub_poll(now=4990.0)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RC_FETCH_LIMITS", None)
            result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        self.assertNotIn("limits", result)
        get_limits.assert_not_called()

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_hub_never_polled_by_anyone_fetches_and_carries_limits_key(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # Task L5 fix round 2: the hub (nobody ever polls IT -- its own
        # fleetpoll self-polls in-process, no HTTP, no header) keeps
        # fetching -- zero configuration.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
        get_limits.return_value = available_limits

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RC_FETCH_LIMITS", None)
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
    def test_standalone_install_fetches_zero_config(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # Task L5 fix round 2, "test both": a standalone install (nobody
        # has ever polled it, RC_FETCH_LIMITS unset) fetches -- exactly
        # the case round 1's devices.json inference broke, fixed here
        # with no explicit override needed at all.
        get_name.return_value = "standalone"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
        get_limits.return_value = available_limits

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RC_FETCH_LIMITS", None)
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
    def test_satellite_whose_marker_has_gone_stale_resumes_fetching(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # "recovers automatically" / avalanche protection: a hub that
        # stopped polling (down, or a satellite the operator removed
        # from devices.json) leaves this device fetching again once the
        # marker is older than HUB_POLL_STALE_SECONDS, not forever.
        get_name.return_value = "satellite"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
        get_limits.return_value = available_limits
        # Well past stale regardless of this device's own real jitter
        # (fix round 3, up to HUB_POLL_JITTER_SECONDS on top of the base
        # window) -- this test is about the end-to-end "resumes
        # fetching" behaviour, not the exact boundary (see
        # HubPollJitterTest for that).
        fleet.note_hub_poll(
            now=5000.0 - fleet.HUB_POLL_STALE_SECONDS - fleet.HUB_POLL_JITTER_SECONDS - 1)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RC_FETCH_LIMITS", None)
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
    def test_hub_can_be_forced_off_despite_never_being_polled(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        # "an operator may want to force it either way" -- even a device
        # nobody polls (the hub itself) can be told not to fetch.
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)

        with patch.dict(os.environ, {"RC_FETCH_LIMITS": "0"}):
            result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertNotIn("limits", result)
        get_limits.assert_not_called()

    @patch("fleet.limits.get_limits")
    @patch("fleet.usage.rollup")
    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_satellite_can_be_forced_on_despite_a_recent_marker(
            self, get_name, get_caps, list_sess, read_ev, prune, rollup, get_limits):
        get_name.return_value = "satellite"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        rollup.return_value = _empty_rollup(5000.0)
        available_limits = dict(_EMPTY_LIMITS, available=True, fetched_at=5000.0)
        get_limits.return_value = available_limits
        fleet.note_hub_poll(now=4990.0)

        with patch.dict(os.environ, {"RC_FETCH_LIMITS": "1"}):
            result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(result["limits"], available_limits)
        get_limits.assert_called_once()


if __name__ == "__main__":
    unittest.main()
