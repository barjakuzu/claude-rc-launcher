"""noredirect.py: the shared no-redirect urllib opener limits.py,
fleetpoll.py, overview.py and server.py all use instead of bare
urlopen. Real per-call-site redirect-refusal proofs (with a fake
credential and a real attacker server) live in
tests/test_limits.py::RedirectRefusalTest, tests/test_fleetpoll.py::
RedirectNeverForwardsBasicAuthTest, tests/test_overview.py::
RedirectNeverForwardsBasicAuthTest and tests/test_server_helpers.py::
ProxyRedirectNeverForwardsBasicAuthTest -- this file only covers the
opener/handler itself in isolation."""
import http.server
import threading
import unittest
import urllib.error
import urllib.request

import noredirect


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    redirect_target = None
    status_code = 302

    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        self.send_response(self.status_code)
        self.send_header("Location", self.redirect_target)
        self.end_headers()


class _PlainHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class NoRedirectOpenerTest(unittest.TestCase):
    def setUp(self):
        self.origin = None
        self.target = None

    def tearDown(self):
        for srv, thread in ((self.origin, self._origin_thread),
                             (self.target, self._target_thread)):
            if srv is not None:
                srv.shutdown()
                thread.join(timeout=5)
                srv.server_close()

    def _start(self, handler_cls):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        return srv, thread

    def test_normal_response_still_works(self):
        self.target, self._target_thread = self._start(_PlainHandler)
        self.origin, self._origin_thread = None, None
        url = f"http://127.0.0.1:{self.target.server_address[1]}/"
        with noredirect.NO_REDIRECT_OPENER.open(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b'{"ok": true}')

    def test_redirect_raises_httperror_with_the_original_status(self):
        self.target, self._target_thread = self._start(_PlainHandler)
        target_url = f"http://127.0.0.1:{self.target.server_address[1]}/elsewhere"
        _RedirectHandler.redirect_target = target_url
        _RedirectHandler.status_code = 302
        self.origin, self._origin_thread = self._start(_RedirectHandler)
        url = f"http://127.0.0.1:{self.origin.server_address[1]}/"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            noredirect.NO_REDIRECT_OPENER.open(url, timeout=5)
        self.assertEqual(ctx.exception.code, 302)

    def test_301_303_307_308_all_refused(self):
        self.target, self._target_thread = self._start(_PlainHandler)
        target_url = f"http://127.0.0.1:{self.target.server_address[1]}/elsewhere"
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                _RedirectHandler.redirect_target = target_url
                _RedirectHandler.status_code = code
                self.origin, self._origin_thread = self._start(_RedirectHandler)
                url = f"http://127.0.0.1:{self.origin.server_address[1]}/"
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    noredirect.NO_REDIRECT_OPENER.open(url, timeout=5)
                self.assertEqual(ctx.exception.code, code)
                self.origin.shutdown()
                self._origin_thread.join(timeout=5)
                self.origin.server_close()
                self.origin, self._origin_thread = None, None

    def test_default_urlopen_would_have_followed_it_control(self):
        # Control proving the probe above is real: bare urlopen (no
        # opener override) DOES follow the same redirect.
        self.target, self._target_thread = self._start(_PlainHandler)
        target_url = f"http://127.0.0.1:{self.target.server_address[1]}/elsewhere"
        _RedirectHandler.redirect_target = target_url
        _RedirectHandler.status_code = 302
        self.origin, self._origin_thread = self._start(_RedirectHandler)
        url = f"http://127.0.0.1:{self.origin.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b'{"ok": true}')


if __name__ == "__main__":
    unittest.main()
