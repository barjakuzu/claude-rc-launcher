import time
import unittest
from unittest.mock import patch

import fleet


class BuildFleetTest(unittest.TestCase):
    def setUp(self):
        fleet._cache.clear()
        fleet._last_prune_at = None

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_full_role_carries_everything(self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "hub"
        get_caps.return_value = {"version": "2.1.263", "agents_json": True}
        list_sess.return_value = [
            {"name": "rc-foo", "session_id": "s1", "cwd": "/home/alice/proj", "state": "idle"},
        ]
        read_ev.return_value = ([{"ts": 1, "event": "Stop", "session_id": "s1", "extra": {}}], "f.jsonl:10")

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

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_metadata_role_strips_cwd_tmux_claude_and_hashes(self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "work-mac"
        get_caps.return_value = {"version": "2.1.263", "agents_json": True}
        list_sess.return_value = [{
            "name": "rc-secret-project", "session_id": "s1", "cwd": "/Users/alice/work",
            "state": "idle", "tmux": {"pane_id": "%1"}, "claude": {"pid": 123}, "tokens": 5000,
        }]
        read_ev.return_value = ([{"ts": 1, "event": "SessionStart", "session_id": "s1", "extra": {"source": "startup"}}], "f.jsonl:5")

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        row = result["sessions"][0]
        self.assertNotIn("cwd", row)
        self.assertNotIn("tmux", row)
        self.assertNotIn("claude", row)
        self.assertNotIn("tokens", row)
        self.assertEqual(set(row.keys()), {"session_id", "name", "state", "started_at", "kind"})
        self.assertNotEqual(row["session_id"], "s1")
        self.assertNotEqual(row["name"], "rc-secret-project")
        ev = result["events"][0]
        self.assertEqual(set(ev.keys()), {"ts", "event"})

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_cached_for_5_seconds_per_since_and_role(self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)
        now = {"t": 1000.0}
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        now["t"] = 1002.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(list_sess.call_count, 1)
        now["t"] = 1006.0
        fleet.build_fleet(role="full", since=None, now_fn=lambda: now["t"])
        self.assertEqual(list_sess.call_count, 2)

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_metadata_role_errors_carry_only_exception_type_name(
            self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.side_effect = OSError("/home/alice/secret/path: permission denied")
        read_ev.return_value = ([], None)

        result = fleet.build_fleet(role="metadata", now_fn=lambda: 5000.0)

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("OSError", result["errors"][0])
        self.assertNotIn("/home/alice", result["errors"][0])
        self.assertNotIn("permission denied", result["errors"][0])

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_full_role_errors_keep_exception_text(
            self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.side_effect = OSError("boom detail")
        read_ev.return_value = ([], None)

        result = fleet.build_fleet(role="full", now_fn=lambda: 5000.0)

        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("boom detail", result["errors"][0])

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_cache_keeps_only_newest_entry_per_role(self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)

        fleet.build_fleet(role="full", since=None, now_fn=lambda: 1000.0)
        fleet.build_fleet(role="full", since="cursor-a", now_fn=lambda: 1000.0)
        fleet.build_fleet(role="full", since="cursor-b", now_fn=lambda: 1000.0)

        full_keys = [k for k in fleet._cache if k[1] == "full"]
        self.assertEqual(len(full_keys), 1)
        self.assertEqual(full_keys[0], ("cursor-b", "full"))

    @patch("fleet.events.prune")
    @patch("fleet.events.read_events")
    @patch("fleet.sessions.list_rc_sessions")
    @patch("fleet.compat.get_caps")
    @patch("fleet.devices.get_local_name")
    def test_prune_invoked_at_most_once_per_hour(self, get_name, get_caps, list_sess, read_ev, prune):
        get_name.return_value = "hub"
        get_caps.return_value = {}
        list_sess.return_value = []
        read_ev.return_value = ([], None)

        now = {"t": 1000.0}
        fleet.build_fleet(role="full", since="a", now_fn=lambda: now["t"])
        self.assertEqual(prune.call_count, 1)

        now["t"] = 1000.0 + 60
        fleet.build_fleet(role="full", since="b", now_fn=lambda: now["t"])
        self.assertEqual(prune.call_count, 1)

        now["t"] = 1000.0 + fleet.PRUNE_INTERVAL_SECONDS + 1
        fleet.build_fleet(role="full", since="c", now_fn=lambda: now["t"])
        self.assertEqual(prune.call_count, 2)


if __name__ == "__main__":
    unittest.main()
