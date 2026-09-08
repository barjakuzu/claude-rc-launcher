"""mcp_server.py: the stdio MCP server's HTTP client (_api_call).

Fix round 3: _api_call sent RC_AUTH_PASS via bare urllib.request.urlopen,
the same redirect-follows-and-resends-headers bug already closed at every
other credentialed call site in this codebase (limits.py, fleetpoll.py,
overview.py, server.py) -- see noredirect.py. This call is loopback-only
(BASE_URL is always http://localhost), but the fix is the same one import
away, so it is closed here too rather than left as a known instance of
the same bug class."""
import http.server
import threading
import unittest
from unittest import mock

import mcp_server

_FAKE_PASSWORD = "TEST-FIXTURE-NOT-A-REAL-PASSWORD-abc123"


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


class ApiCallRedirectNeverForwardsAuthTest(unittest.TestCase):
    """_api_call sends RC_AUTH_USER/RC_AUTH_PASS as Basic auth on every
    call. Bare urlopen would re-send that header to wherever a 302
    points -- proven here with two real loopback HTTP servers, same as
    tests/test_limits.py proves it for the OAuth token."""

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

    def test_302_to_another_origin_never_forwards_rc_auth_pass(self):
        with mock.patch.object(mcp_server, "BASE_URL", self.origin_base_url), \
             mock.patch.object(mcp_server, "RC_AUTH_USER", "hub"), \
             mock.patch.object(mcp_server, "RC_AUTH_PASS", _FAKE_PASSWORD):
            result = mcp_server._api_call("GET", "/fleet")
        self.assertFalse(result.get("ok", True))
        self.assertEqual(_AttackerHandler.hits, [],
                          "the redirect target must never receive a request, "
                          "and RC_AUTH_PASS must never reach it")


if __name__ == "__main__":
    unittest.main()
