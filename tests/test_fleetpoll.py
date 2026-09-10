import http.server
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

import fleet
import fleetpoll
import store


def _fake_store(tmp_path):
    return store.Store(os.path.join(tmp_path, "hub.db"))


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Answers every request with a 302 to `redirect_target` (set by the
    test before starting the server)."""
    redirect_target = None

    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.redirect_target)
        self.end_headers()


class _AttackerHandler(http.server.BaseHTTPRequestHandler):
    """Records every request it receives (headers included)."""
    hits = []

    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        _AttackerHandler.hits.append(dict(self.headers))
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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


class HubPollHeaderTest(unittest.TestCase):
    """Task L5 fix round 2: every request _default_http_get makes IS a
    hub polling a device, so it must always carry fleet.HUB_POLL_HEADER
    -- the receiving device's own fleet.is_limits_hub() depends on it to
    stop fetching without any operator configuration."""

    class _FakeResponse:
        def __init__(self, body):
            self._body = body

        def read(self, n=-1):
            return self._body[:n] if n and n > 0 else self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _header_value(self, req):
        # urllib.request.Request stores whatever add_header() was given
        # under name.capitalize() (a quirk of the stdlib, not of this
        # code) -- looked up case-insensitively here since HTTP header
        # names are case-insensitive on the wire regardless.
        for key, value in req.headers.items():
            if key.lower() == fleet.HUB_POLL_HEADER.lower():
                return value
        return None

    def test_marks_the_request_as_a_hub_poll(self):
        import json as json_mod
        body = json_mod.dumps({"ok": True}).encode()
        captured = {}

        def fake_open(req, timeout=None):
            captured["req"] = req
            return self._FakeResponse(body)

        with patch("fleetpoll.noredirect.NO_REDIRECT_OPENER.open", side_effect=fake_open):
            fleetpoll._default_http_get("http://example.com", "/rc/fleet")

        self.assertEqual(self._header_value(captured["req"]), "1")

    def test_marks_the_request_even_with_no_auth_credentials(self):
        # The header is unconditional -- unlike Authorization, it is not
        # gated on auth_user/auth_pass being set (a device with auth
        # disabled must still learn it is being polled).
        import json as json_mod
        body = json_mod.dumps({"ok": True}).encode()
        captured = {}

        def fake_open(req, timeout=None):
            captured["req"] = req
            return self._FakeResponse(body)

        with patch("fleetpoll.noredirect.NO_REDIRECT_OPENER.open", side_effect=fake_open):
            fleetpoll._default_http_get("http://example.com", "/rc/sessions",
                                         auth_user="", auth_pass="")

        self.assertEqual(self._header_value(captured["req"]), "1")


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

        with patch("fleetpoll.noredirect.NO_REDIRECT_OPENER.open", return_value=FakeResponse()):
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

        with patch("fleetpoll.noredirect.NO_REDIRECT_OPENER.open", return_value=FakeResponse()):
            result = fleetpoll._default_http_get("http://example.com", "/rc/fleet")
        self.assertEqual(result, {"ok": True})


class RedirectNeverForwardsBasicAuthTest(unittest.TestCase):
    """Fix round 2, Important: _default_http_get sends this hub's own
    Basic auth password for a device (from devices.json) on every call.
    Bare urlopen would re-send that header to wherever a device's 302
    points -- proven here with two real loopback HTTP servers, the same
    way tests/test_limits.py proves it for the OAuth token."""

    def setUp(self):
        _AttackerHandler.hits = []
        self.attacker = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AttackerHandler)
        self.attacker_thread = threading.Thread(target=self.attacker.serve_forever, daemon=True)
        self.attacker_thread.start()
        attacker_port = self.attacker.server_address[1]
        _RedirectHandler.redirect_target = f"http://127.0.0.1:{attacker_port}/stolen"

        self.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        self.origin_thread = threading.Thread(target=self.origin.serve_forever, daemon=True)
        self.origin_thread.start()
        self.origin_base_url = f"http://127.0.0.1:{self.origin.server_address[1]}"

    def tearDown(self):
        self.origin.shutdown()
        self.origin_thread.join(timeout=5)
        self.origin.server_close()
        self.attacker.shutdown()
        self.attacker_thread.join(timeout=5)
        self.attacker.server_close()

    def test_302_to_another_origin_never_forwards_the_device_password(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            fleetpoll._default_http_get(
                self.origin_base_url, "/rc/fleet",
                auth_user="hub", auth_pass="super-secret-device-password")
        self.assertEqual(ctx.exception.code, 302)
        self.assertEqual(_AttackerHandler.hits, [],
                          "the redirect target must never receive a request, "
                          "and the device password must never reach it")


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


class GraceSecondsCoupledToPollIntervalTest(unittest.TestCase):
    """Fix round 3, Item 2: store.ENDED_ROW_GRACE_SECONDS is a floor, not
    the effective grace window -- _ingest must pass
    max(store.ENDED_ROW_GRACE_SECONDS, 3 * self.interval) explicitly, so a
    poller configured with a longer-than-default interval can't have a
    single missed poll silently disable the flicker-vs-new-session grace
    window in upsert_sessions."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_default_interval_uses_the_grace_floor(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{"session_id": "s1", "name": "rc-a", "state": "idle"}],
            "events": [], "cursor": None, "generated_at": 1.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())  # default interval=30
        self.assertEqual(poller.interval, 30)
        poller.store.upsert_sessions = MagicMock()
        poller.poll_once()

        poller.store.upsert_sessions.assert_called_once()
        self.assertEqual(
            poller.store.upsert_sessions.call_args.kwargs["grace_seconds"],
            store.ENDED_ROW_GRACE_SECONDS)  # 3*30=90 ties the floor, floor wins

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_longer_interval_widens_the_grace_window(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{"session_id": "s1", "name": "rc-a", "state": "idle"}],
            "events": [], "cursor": None, "generated_at": 1.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, interval=120, http_get=MagicMock())
        poller.store.upsert_sessions = MagicMock()
        poller.poll_once()

        poller.store.upsert_sessions.assert_called_once()
        self.assertEqual(
            poller.store.upsert_sessions.call_args.kwargs["grace_seconds"], 360)  # 3*120


class UsageCostIngestTest(unittest.TestCase):
    """W4/integration: CONTRACT.md sections 1/2 -- per-session usage and
    per-project daily cost totals, ingested from the same fleet snapshot
    fleet.build_fleet() already returns, persisted via
    store.upsert_session_usage/upsert_cost_daily."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_full_role_payload_persists_session_usage_and_cost_daily(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s1", "name": "rc-a", "state": "busy",
                "usage": {"input": 100, "cache_read": 200, "cache_write": 10,
                          "output": 50, "effective": 900, "last_ts": 500.0},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": [{"day": "2026-09-07", "input": 100, "cache_read": 200,
                              "cache_write": 10, "output": 50, "effective": 900}],
            "usage_daily_by_project": [{"day": "2026-09-07", "project": "-var-www",
                                         "input": 100, "cache_read": 200, "cache_write": 10,
                                         "output": 50, "effective": 900}],
            "usage_meta": {"files": 1, "skipped": 0, "partial": False, "generated_at": 1000.0},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        view = self.store.fleet_view()
        self.assertEqual(view["sessions"][0]["usage"]["effective"], 900)

        cost = self.store.cost_view(days=30)
        dev = next(d for d in cost["devices"] if d["device_id"] == "local")
        self.assertEqual(dev["total_effective"], 900)
        projects = {p["project"]: p["effective"] for p in cost["projects"]}
        self.assertEqual(projects.get("-var-www"), 900)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_full_role_payload_persists_cost_hourly(
            self, get_name, load_devices, build_fleet):
        # Task-tk: usage_hourly ingests into cost_hourly the same way
        # usage_daily_by_project ingests into cost_daily. A recent (real)
        # hour, not a fixed historical one: poll_once() also runs
        # store.prune() on its first call, which sweeps cost_hourly rows
        # older than its own real-clock cutoff, and a stale fixture hour
        # would be pruned again inside the same poll_once() call.
        get_name.return_value = "hub"
        load_devices.return_value = []
        current_hour = time.strftime("%Y-%m-%dT%H", time.gmtime())
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": [], "usage_daily_by_project": [],
            "usage_hourly": [
                {"hour": current_hour, "input": 1, "cache_read": 2, "cache_write": 3,
                 "output": 4, "effective": 900},
            ],
            "usage_meta": {"files": 1, "skipped": 0, "partial": False, "generated_at": 1000.0},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        conn = self.store._read_conn()
        try:
            row = conn.execute(
                "SELECT effective FROM cost_hourly WHERE device_id=? AND hour=?",
                ("local", current_hour)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["effective"], 900)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_missing_usage_hourly_key_never_raises(self, get_name, load_devices, build_fleet):
        # A legacy pre-fleet device's synthesized snapshot has no
        # "usage_hourly" key at all -- must be a no-op, not an error.
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": [], "usage_daily_by_project": [],
            "usage_meta": {"files": 0, "skipped": 0, "partial": False, "generated_at": 1000.0},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        try:
            poller.poll_once()
        except Exception as e:  # pragma: no cover - failure path
            self.fail(f"poll_once raised {e!r}")

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_malformed_usage_hourly_from_one_device_does_not_stop_others(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": [], "usage_daily_by_project": [],
            "usage_hourly": "not-a-list",
            "usage_meta": {"files": 0, "skipped": 0, "partial": False, "generated_at": 1000.0},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        try:
            poller.poll_once()
        except Exception as e:  # pragma: no cover - failure path
            self.fail(f"poll_once raised {e!r}")
        # The rest of ingest must still have gone through.
        view = self.store.fleet_view()
        self.assertIsNotNone(view)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_metadata_payload_falls_back_to_project_empty_string(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "metadata", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "hashabc", "name": "hashname", "state": "busy",
                "usage": {"effective": 555},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": [{"day": "2026-09-07", "effective": 555}],
            # usage_daily_by_project key is OMITTED entirely under
            # role == metadata, per CONTRACT.md.
            "usage_meta": {"files": 1, "skipped": 0, "partial": False, "generated_at": 1000.0},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        view = self.store.fleet_view()
        self.assertEqual(view["sessions"][0]["usage"]["effective"], 555)

        cost = self.store.cost_view(days=30)
        dev = next(d for d in cost["devices"] if d["device_id"] == "local")
        # Contributes to the device's own total ...
        self.assertEqual(dev["total_effective"], 555)
        # ... but never to the projects breakdown (CONTRACT.md amendment).
        self.assertEqual(cost["projects"], [])

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_malformed_usage_payload_from_one_device_does_not_stop_others(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        good_device = {"id": "dev-good", "name": "good-box", "base_url": "http://good:8200"}
        load_devices.return_value = [good_device]
        build_fleet.return_value = {
            # The LOCAL device's own snapshot is deliberately malformed:
            # `usage` on the session is a string instead of a dict, and
            # `usage_daily_by_project` is a string instead of a list.
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{"session_id": "s1", "name": "rc-a", "state": "idle",
                          "usage": "not-a-dict"}],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": "also-not-a-list",
            "usage_daily_by_project": "still-not-a-list",
            "usage_meta": "not-even-a-dict",
        }
        remote_resp = {
            "device_name": "good-box", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s2", "name": "rc-b", "state": "idle",
                "usage": {"input": 1, "cache_read": 1, "cache_write": 1,
                          "output": 1, "effective": 42, "last_ts": 10.0},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_daily": [{"day": "2026-09-07", "effective": 42}],
            "usage_daily_by_project": [{"day": "2026-09-07", "project": "-tmp",
                                         "input": 1, "cache_read": 1, "cache_write": 1,
                                         "output": 1, "effective": 42}],
            "usage_meta": {"files": 1, "skipped": 0, "partial": False, "generated_at": 1000.0},
        }
        http_get = MagicMock(return_value=remote_resp)
        poller = fleetpoll.FleetPoller(self.store, http_get=http_get)
        poller.poll_once()  # must not raise

        view = self.store.fleet_view()
        by_id = {(s["device_id"], s["session_id"]): s for s in view["sessions"]}
        # The malformed local session still got upserted (name/state) --
        # only its usage was skipped -- and the good remote device's
        # session usage made it in untouched.
        self.assertIn(("local", "s1"), by_id)
        self.assertIn(("dev-good", "s2"), by_id)
        self.assertEqual(by_id[("dev-good", "s2")]["usage"]["effective"], 42)

        cost = self.store.cost_view(days=30)
        dev_ids = {d["device_id"] for d in cost["devices"]}
        self.assertIn("dev-good", dev_ids)
        self.assertNotIn("local", dev_ids)  # nothing coercible was ever written for it

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_launcher_session_reaches_cost_view_sessions_with_a_real_project(
            self, get_name, load_devices, build_fleet):
        # Fix round 1 (Important): sessions.list_rc_sessions() emits
        # `workdir` (+ a basename-only `project`, e.g. "viewlogic" --
        # NOT the encoded form cost_view uses) for a launcher row, never
        # `cwd`. Before _ingest's setdefault fix, every launcher session
        # landed in the store with cwd=NULL, so store._encode_cwd_as_project
        # always produced "" for it regardless of its real project.
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s1", "name": "rc-a", "kind": "launcher", "state": "idle",
                "workdir": "/var/www/viewlogic", "project": "viewlogic",
                "usage": {"input": 0, "cache_read": 0, "cache_write": 0,
                          "output": 0, "effective": 100, "last_ts": 10.0},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        cost = self.store.cost_view(days=30)
        row = next(r for r in cost["sessions"] if r["session_id"] == "s1")
        self.assertEqual(row["project"], "-var-www-viewlogic")

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_external_session_cwd_is_never_overwritten_by_workdir(
            self, get_name, load_devices, build_fleet):
        # setdefault, not assignment: an external row's real `cwd` (it
        # never has `workdir` at all) must survive untouched.
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s1", "name": "rc-ext", "kind": "external", "state": "idle",
                "cwd": "/var/www/other-project",
                "usage": {"input": 0, "cache_read": 0, "cache_write": 0,
                          "output": 0, "effective": 100, "last_ts": 10.0},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        cost = self.store.cost_view(days=30)
        row = next(r for r in cost["sessions"] if r["session_id"] == "s1")
        self.assertEqual(row["project"], "-var-www-other-project")


class GuardInvocationTest(unittest.TestCase):
    """W4/integration: CONTRACT.md section 4 -- guard.evaluate() runs once
    per completed poll cycle over an enriched store.fleet_view(), and the
    result is persisted via store.replace_alerts()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_stalled_session_wedged_needs_attention_still_trips_guard(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        # A session that's still `status: busy` (its raw, device-reported
        # status) but presents as needs_attention (waiting_for is set) --
        # this is the exact "wedged inside a Stop hook" case CONTRACT.md
        # section 4 calls out. It last emitted an event over an hour ago,
        # so the stalled rule (default stalled_minutes=30) should fire if
        # guard sees it as busy.
        #
        # Fix round 1 (Critical, reviewer-mandated): this runs through
        # poll_once() on a SINGLE clock -- no now_fn override at all,
        # exactly as fleetpoll._loop calls it in production -- not two
        # different injected clocks for ingestion vs. evaluation. The
        # original version of this test injected now_fn=lambda: t0+3600
        # into poll_once while store.upsert_sessions wrote `last_seen`
        # via its own uninjectable real time.time(): that desync is not
        # something production can ever produce, and it was only needed
        # because the stalled rule was (before this fix) reading
        # `last_seen`, a value that upsert_sessions refreshes to "now" on
        # every single poll for every session, so on one real clock
        # elapsed time against it is always ~0 and the rule can never
        # fire. Withholding `last_seen` from guard's enrichment copy (see
        # fleetpoll.py's `s["last_seen"] = None`) is what makes a
        # same-clock test able to pass at all -- this test would FAIL
        # without that fix, using nothing but a real, old event
        # timestamp as the only activity signal.
        now = time.time()
        long_ago = now - 3700  # well past the default 30-minute threshold
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s1", "name": "rc-wedged", "kind": "launcher",
                "status": "busy", "waiting_for": "tool", "started_at": long_ago,
            }],
            "events": [{"session_id": "s1", "ts": long_ago, "event": "Notification"}],
            "cursor": None, "generated_at": now, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()  # no now_fn override -- the real clock, once

        # The UI-facing derived state is still needs_attention...
        view = self.store.fleet_view()
        self.assertEqual(view["sessions"][0]["state"], "needs_attention")

        # ...but guard saw it as busy and the stalled rule fired.
        alerts = self.store.live_alerts()
        rules = {a["rule"] for a in alerts}
        self.assertIn("stalled", rules)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_partial_device_usage_withheld_from_guard_cost_rules(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        # A session already well over token_total's default threshold
        # (50,000,000 effective) -- but its device reports usage_partial,
        # so guard must not see `usage` at all for it and token_total must
        # not fire. session_age/stalled/device_concurrency are unaffected
        # by usage and must still be free to evaluate normally.
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s1", "name": "rc-big", "kind": "launcher",
                "status": "idle", "started_at": 1000.0,
                "usage": {"input": 0, "cache_read": 0, "cache_write": 0,
                          "output": 0, "effective": 999_000_000, "last_ts": 1000.0},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_meta": {"files": 1, "skipped": 0, "partial": True, "generated_at": 1000.0},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once(now_fn=lambda: 1000.0)

        alerts = self.store.live_alerts()
        rules = {a["rule"] for a in alerts}
        self.assertNotIn("token_total", rules)

        view = self.store.fleet_view()
        dev = next(d for d in view["devices"] if d["id"] == "local")
        self.assertEqual(dev["usage_partial"], 1)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_projects_capped_alone_does_not_withhold_usage(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{
                "session_id": "s1", "name": "rc-big", "kind": "launcher",
                "status": "idle", "started_at": 1000.0,
                "usage": {"input": 0, "cache_read": 0, "cache_write": 0,
                          "output": 0, "effective": 999_000_000, "last_ts": 1000.0},
            }],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            "usage_meta": {"files": 1, "skipped": 0, "partial": False,
                           "generated_at": 1000.0, "projects_capped": True},
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once(now_fn=lambda: 1000.0)

        alerts = self.store.live_alerts()
        rules = {a["rule"] for a in alerts}
        self.assertIn("token_total", rules)  # usage was NOT withheld

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_guard_raising_does_not_break_the_poll_loop(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{"session_id": "s1", "name": "rc-a", "state": "idle"}],
            "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        with patch("fleetpoll.guard.evaluate", side_effect=RuntimeError("boom")):
            poller.poll_once()  # must not raise

        # The session was still ingested even though guard blew up on it.
        view = self.store.fleet_view()
        self.assertEqual(len(view["sessions"]), 1)

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_alerts_that_stop_firing_are_removed_on_the_next_cycle(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.side_effect = [
            {  # cycle 1: a session already way over the age limit
                # started_at=1.0, not 0.0 -- store._valid_started_at treats
                # 0 as "not set" (indistinguishable from a numeric-default
                # column), which would make upsert_sessions fall back to
                # real wall-clock time for started_at instead of honouring
                # this deliberately-ancient value.
                "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
                "sessions": [{"session_id": "s1", "name": "rc-old", "kind": "launcher",
                              "status": "idle", "started_at": 1.0}],
                "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
            },
            {  # cycle 2: session s1 has ended (absent from the payload)
                "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
                "sessions": [], "events": [], "cursor": None,
                "generated_at": 2000.0, "errors": [],
            },
        ]
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        very_late = 30 * 3600  # 30h after started_at=0, over the 24h default
        poller.poll_once(now_fn=lambda: very_late)
        self.assertIn("session_age", {a["rule"] for a in self.store.live_alerts()})

        poller.poll_once(now_fn=lambda: very_late + 10)
        self.assertEqual(self.store.live_alerts(), [])


class LimitsIngestTest(unittest.TestCase):
    """CONTRACT.md sections 3-4: fleet.build_fleet()'s `limits` key is
    persisted per device via store.upsert_account_limits(), same ingest
    pass as sessions/usage/cost above."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _fake_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _limits_payload(self, available=True, fetched_at=1000.0):
        return {
            "available": available, "fetched_at": fetched_at,
            "five_hour": {"percent": 56.0, "resets_at": "r"} if available else None,
            "seven_day": {"percent": 80.0, "resets_at": "r"} if available else None,
            "scoped": [], "spend": None, "extra_usage": None,
            "error": None if available else "CredentialsUnavailable",
        }

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_limits_key_persisted_to_account_limits(self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1000.0,
            "errors": [], "limits": self._limits_payload(),
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        view = self.store.limits_view()
        self.assertEqual(len(view["rows"]), 1)
        self.assertEqual(view["rows"][0]["device_id"], "local")
        self.assertTrue(view["rows"][0]["available"])
        self.assertEqual(view["primary_device_id"], "local")

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_missing_limits_key_never_writes_a_row(self, get_name, load_devices, build_fleet):
        # A legacy/pre-fleet snapshot (see _poll_remote_legacy) has no
        # "limits" key at all -- this must be a silent no-op, not a
        # fabricated unavailable row.
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1000.0, "errors": [],
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        self.assertEqual(self.store.limits_view()["rows"], [])

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_malformed_limits_never_breaks_the_rest_of_ingest(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [{"session_id": "s1", "name": "rc-a", "state": "idle"}],
            "events": [], "cursor": "cur-1", "generated_at": 1000.0, "errors": [],
            "limits": "not a dict",
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        # Sessions/cursor still made it through despite the bad `limits`.
        view = self.store.fleet_view()
        self.assertEqual(len(view["sessions"]), 1)
        self.assertEqual(poller._cursors["local"], "cur-1")
        self.assertEqual(self.store.limits_view()["rows"], [])

    @patch("fleetpoll.fleet.build_fleet")
    @patch("fleetpoll.devices.load_devices")
    @patch("fleetpoll.devices.get_local_name")
    def test_unavailable_limits_persisted_and_never_becomes_primary(
            self, get_name, load_devices, build_fleet):
        get_name.return_value = "hub"
        load_devices.return_value = []
        build_fleet.return_value = {
            "device_name": "hub", "role": "full", "version": "1", "claude_version": "1",
            "sessions": [], "events": [], "cursor": None, "generated_at": 1000.0,
            "errors": [], "limits": self._limits_payload(available=False),
        }
        poller = fleetpoll.FleetPoller(self.store, http_get=MagicMock())
        poller.poll_once()

        view = self.store.limits_view()
        self.assertEqual(len(view["rows"]), 1)
        self.assertFalse(view["rows"][0]["available"])
        self.assertIsNone(view["primary"])


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
