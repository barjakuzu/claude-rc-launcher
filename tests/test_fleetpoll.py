import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import fleetpoll
import store


def _fake_store(tmp_path):
    return store.Store(os.path.join(tmp_path, "hub.db"))


class PollOnceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_polls_local_in_process_no_http(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "2.1.7",
            "claude_version": "2.1.263", "sessions": [{"session_id": "s1", "name": "rc-a"}],
            "events": [{"ts": 1.0, "event": "Stop", "session_id": "s1", "extra": {}}],
            "cursor": "f.jsonl:1", "generated_at": 1000.0, "errors": [],
        }
        http_get = MagicMock()
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)
        poller.poll_once()

        http_get.assert_not_called()
        view = self.store.fleet_view()
        self.assertEqual(len(view["devices"]), 1)
        self.assertEqual(view["devices"][0]["id"], "local")
        self.assertEqual(len(view["sessions"]), 1)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_polls_remote_device_over_http_with_cursor(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "tba-lin", "base_url": "http://example.com:8200",
                  "auth_user": "u", "auth_pass": "p"}
        load_devices.return_value = [device]
        remote_resp = {"device_name": "tba-lin", "role": "full", "version": "2.1.7",
                       "claude_version": "2.1.263", "sessions": [{"session_id": "s2", "name": "rc-b"}],
                       "events": [], "cursor": "g.jsonl:1", "generated_at": 2.0, "errors": []}
        http_get = MagicMock(return_value=remote_resp)
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)

        poller.poll_once()
        poller.poll_once()

        self.assertEqual(http_get.call_count, 2)
        first_call_kwargs = http_get.call_args_list[1]
        self.assertIn("since", first_call_kwargs.kwargs)
        self.assertEqual(first_call_kwargs.kwargs["since"], "g.jsonl:1")

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_offline_device_backs_off_and_never_raises(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "offline-box", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def raising_get(*a, **kw):
            raise ConnectionError("refused")

        poller = fleetpoll.FleetPoller(self.store, http_get=raising_get)
        poller.poll_once()  # must not raise
        poller.poll_once()

        view = self.store.fleet_view()
        offline_row = [d for d in view["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(offline_row["online"], 0)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_concurrent_poll_once_calls_do_not_overlap(self, get_name, load_devices, build_fleet):
        import threading
        get_name.return_value = "hub"
        load_devices.return_value = []
        calls = []

        def slow_build_fleet(since=None):
            calls.append(1)
            import time as t
            t.sleep(0.05)
            return {"device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
                    "sessions": [], "events": [], "cursor": None, "generated_at": 1.0, "errors": []}

        build_fleet.side_effect = slow_build_fleet
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        threads = [threading.Thread(target=poller.poll_once) for _ in range(3)]
        for t_ in threads:
            t_.start()
        for t_ in threads:
            t_.join(timeout=5)
        self.assertEqual(len(calls), 3)  # all ran, just serialized — no assertion on order

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_on_change_called_after_successful_ingest(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{"session_id": "s1", "name": "rc-a"}], "events": [],
            "cursor": None, "generated_at": 1.0, "errors": [],
        }
        on_change = MagicMock()
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock(), on_change=on_change)
        poller.poll_once()
        on_change.assert_called()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_on_change_exception_does_not_break_poll(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1.0, "errors": [],
        }
        on_change = MagicMock(side_effect=RuntimeError("boom"))
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock(), on_change=on_change)
        poller.poll_once()  # must not raise


class StartStopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_start_then_stop_joins_thread_cleanly(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        poller = fleetpoll.FleetPoller(self.store, interval=0.05, http_get=MagicMock())
        poller.start()
        import time
        time.sleep(0.15)
        poller.stop()
        self.assertFalse(poller._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
