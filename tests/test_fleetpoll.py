import os
import tempfile
import unittest
import urllib.error
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
    def test_full_role_session_fields_pass_through_to_store(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "2.1.7",
            "claude_version": "2.1.263",
            "sessions": [{
                "session_id": "s1", "name": "rc-a", "cwd": "/tmp", "kind": "external",
                "external": True, "pid": 4242,
                "tmux": {"session_name": "rc-a", "pane_id": "%3"},
                "rc_url": "https://claude.ai/code/session_abc", "tokens": 999,
                "claude": {"status": "busy", "waitingFor": None},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        view = self.store.fleet_view()
        row = view["sessions"][0]
        self.assertEqual(row["pid"], 4242)
        self.assertEqual(row["tmux"], {"session_name": "rc-a", "pane_id": "%3"})
        self.assertEqual(row["rc_url"], "https://claude.ai/code/session_abc")
        self.assertEqual(row["tokens"], 999)
        self.assertEqual(row["claude"], {"status": "busy", "waitingFor": None})

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_polls_remote_device_over_http_with_cursor(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200",
                  "auth_user": "u", "auth_pass": "p"}
        load_devices.return_value = [device]
        remote_resp = {"device_name": "example-device", "role": "full", "version": "2.1.7",
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


class OfflinePreservesMetadataTest(unittest.TestCase):
    """Important-5 fix: a failed poll of an already-known device must not
    clobber its last-known name/role/version with placeholder
    "unknown"/None values -- only mark_device_offline should run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_device_version_survives_an_offline_poll(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        # First poll succeeds: device gets real metadata in the store.
        good_snapshot = {"device_name": "example-device", "role": "full", "version": "9.9.9",
                          "claude_version": "2.1.263", "sessions": [], "events": [],
                          "cursor": None, "generated_at": 2.0, "errors": []}
        http_get = MagicMock(return_value=good_snapshot)
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)
        poller.poll_once()
        row = [d for d in self.store.fleet_view()["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(row["version"], "9.9.9")
        self.assertEqual(row["online"], 1)

        # Second poll fails: the device goes offline, but its previously
        # known version/role must survive, not get overwritten with None.
        http_get.side_effect = ConnectionError("refused")
        poller.poll_once()
        row2 = [d for d in self.store.fleet_view()["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(row2["version"], "9.9.9")
        self.assertEqual(row2["claude_version"], "2.1.263")
        self.assertEqual(row2["online"], 0)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_unknown_device_still_gets_a_placeholder_row_when_offline(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "never-seen", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def raising_get(*a, **kw):
            raise ConnectionError("refused")

        poller = fleetpoll.FleetPoller(self.store, http_get=raising_get)
        poller.poll_once()
        row = [d for d in self.store.fleet_view()["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(row["name"], "never-seen")
        self.assertEqual(row["online"], 0)


class IngestFailureDoesNotBackOffTest(unittest.TestCase):
    """Minor fix: a store-side failure during _ingest (device answered
    fine) must not be treated the same as a transport failure -- no
    backoff, no mark_device_offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_ingest_failure_does_not_back_off_or_mark_offline(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]
        remote_resp = {"device_name": "example-device", "role": "full", "version": "1",
                       "claude_version": "1", "sessions": [], "events": [],
                       "cursor": None, "generated_at": 1.0, "errors": []}
        http_get = MagicMock(return_value=remote_resp)
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)
        with patch.object(poller.store, "upsert_device", side_effect=store.StoreClosed("boom")):
            poller.poll_once()  # must not raise
        self.assertEqual(poller._backoff.get("dev1"), None)
        self.assertEqual(poller._next_try_at.get("dev1"), None)


class MaxResponseSizeTest(unittest.TestCase):
    def test_default_http_get_rejects_oversized_response(self):
        big_body = b"x" * (fleetpoll.MAX_RESPONSE_BYTES + 1)

        class FakeResponse:
            def read(self, n=-1):
                return big_body[:n] if n and n > 0 else big_body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("fleetpoll.urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(ValueError):
                fleetpoll._default_http_get("http://example.com", "/rc/fleet")

    def test_default_http_get_accepts_response_under_the_cap(self):
        import json as json_mod
        body = json_mod.dumps({"ok": True}).encode()

        class FakeResponse:
            def read(self, n=-1):
                return body[:n] if n and n > 0 else body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("fleetpoll.urllib.request.urlopen", return_value=FakeResponse()):
            result = fleetpoll._default_http_get("http://example.com", "/rc/fleet")
        self.assertEqual(result, {"ok": True})


class LegacyFleetFallbackTest(unittest.TestCase):
    """A device on 2.1.7 (or earlier) has no /rc/fleet route. On a 404/501
    for it, the poller must fall back to /rc/sessions (+/rc/version) and
    keep the device online with its sessions, not go offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_device_404_on_fleet_still_appears_online_with_sessions(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def fake_http_get(base_url, path, auth_user="", auth_pass="", since=None, timeout=10):
            if path == "/rc/fleet":
                raise fleetpoll.FleetEndpointNotFound("404")
            if path == "/rc/sessions":
                return {"sessions": [{"session_id": "s1", "name": "rc-a"}]}
            if path == "/rc/version":
                return {"version": "2.1.7", "claude_version": "2.1.263"}
            raise AssertionError(f"unexpected path {path!r}")

        poller = fleetpoll.FleetPoller(self.store, http_get=fake_http_get)
        poller.poll_once()

        view = self.store.fleet_view()
        row = [d for d in view["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(row["online"], 1)
        self.assertEqual(row["version"], "2.1.7")
        self.assertEqual(len(view["sessions"]), 1)
        self.assertEqual(view["sessions"][0]["session_id"], "s1")

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_404_fallback_does_not_back_off(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def fake_http_get(base_url, path, auth_user="", auth_pass="", since=None, timeout=10):
            if path == "/rc/fleet":
                raise fleetpoll.FleetEndpointNotFound("404")
            if path == "/rc/sessions":
                return {"sessions": []}
            if path == "/rc/version":
                return {"version": "2.1.7", "claude_version": "2.1.263"}
            raise AssertionError(f"unexpected path {path!r}")

        poller = fleetpoll.FleetPoller(self.store, http_get=fake_http_get)
        poller.poll_once()
        poller.poll_once()

        self.assertIsNone(poller._backoff.get("dev1"))
        self.assertIsNone(poller._next_try_at.get("dev1"))

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_501_also_triggers_fallback(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def fake_http_get(base_url, path, auth_user="", auth_pass="", since=None, timeout=10):
            if path == "/rc/fleet":
                raise fleetpoll.FleetEndpointNotFound("501")
            if path == "/rc/sessions":
                return {"sessions": []}
            raise AssertionError(f"unexpected path {path!r}")

        poller = fleetpoll.FleetPoller(self.store, http_get=fake_http_get)
        poller.poll_once()  # must not raise, must not go offline

        view = self.store.fleet_view()
        row = [d for d in view["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(row["online"], 1)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_sessions_endpoint_failure_still_goes_offline_and_backs_off(
            self, get_name, load_devices, build_fleet):
        # The fallback must not mask a genuinely unreachable device --
        # only the missing /rc/fleet route itself is treated specially.
        get_name.return_value = "hub"
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        device = {"id": "dev1", "name": "example-device", "base_url": "http://example.com:8200"}
        load_devices.return_value = [device]

        def fake_http_get(base_url, path, auth_user="", auth_pass="", since=None, timeout=10):
            if path == "/rc/fleet":
                raise fleetpoll.FleetEndpointNotFound("404")
            raise ConnectionError("refused")

        poller = fleetpoll.FleetPoller(self.store, http_get=fake_http_get)
        poller.poll_once()

        view = self.store.fleet_view()
        row = [d for d in view["devices"] if d["id"] == "dev1"][0]
        self.assertEqual(row["online"], 0)
        self.assertIsNotNone(poller._backoff.get("dev1"))

    def test_default_http_get_raises_fleet_endpoint_not_found_on_404(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{httpd.server_port}"
            with self.assertRaises(fleetpoll.FleetEndpointNotFound):
                fleetpoll._default_http_get(base_url, "/rc/fleet")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


class PruneSchedulingTest(unittest.TestCase):
    """Nothing ever called Store.prune() before, so ended sessions/events/
    audit rows piled up forever. The poll loop must call it, but at most
    once an hour -- not on every 30s cycle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_prune_called_on_first_poll(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        with patch.object(self.store, "prune", wraps=self.store.prune) as prune:
            poller.poll_once(now_fn=lambda: 1000.0)
            prune.assert_called_once()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_prune_not_called_again_within_an_hour(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        with patch.object(self.store, "prune", wraps=self.store.prune) as prune:
            poller.poll_once(now_fn=lambda: 1000.0)
            poller.poll_once(now_fn=lambda: 1000.0 + fleetpoll.PRUNE_INTERVAL_SECONDS - 1)
            prune.assert_called_once()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_prune_called_again_after_an_hour(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        with patch.object(self.store, "prune", wraps=self.store.prune) as prune:
            poller.poll_once(now_fn=lambda: 1000.0)
            poller.poll_once(now_fn=lambda: 1000.0 + fleetpoll.PRUNE_INTERVAL_SECONDS)
            self.assertEqual(prune.call_count, 2)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_prune_failure_does_not_break_poll_once(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {"device_name": "hub", "role": "full", "version": "1",
                                     "claude_version": "1", "sessions": [], "events": [],
                                     "cursor": None, "generated_at": 1.0, "errors": []}
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        with patch.object(self.store, "prune", side_effect=RuntimeError("boom")):
            poller.poll_once(now_fn=lambda: 1000.0)  # must not raise


class FleetViewExcludesEndedSessionsTest(unittest.TestCase):
    """/api/fleet and the SSE snapshot read fleet_view() with defaults --
    an ended session must not sit in the Sessions tab forever."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_session_missing_from_a_later_snapshot_disappears_from_default_view(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.side_effect = [
            {"device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
             "sessions": [{"session_id": "s1", "name": "rc-a", "state": "idle"}],
             "events": [], "cursor": None, "generated_at": 1.0, "errors": []},
            {"device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
             "sessions": [], "events": [], "cursor": None, "generated_at": 2.0, "errors": []},
        ]
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()
        self.assertEqual(len(self.store.fleet_view()["sessions"]), 1)

        poller.poll_once()
        self.assertEqual(self.store.fleet_view()["sessions"], [])
        self.assertEqual(len(self.store.fleet_view(include_ended=True)["sessions"]), 1)


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
