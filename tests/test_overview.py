import http.server
import os, sys, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overview

class OverviewTest(unittest.TestCase):
    def test_card_from_parts_online(self):
        card = overview.card_from_parts(
            device={"id": "home", "name": "Home", "base_url": "http://home-box.example.net:8200"},
            sessions=[{"tokens": 1000}, {"tokens": 500}],
            stats={"loadavg": [2.0, 1.0, 1.0], "cores": 4, "os": "Ubuntu 24.04", "token_history": [1, 2, 3]},
        )
        self.assertEqual(card["id"], "home")
        self.assertEqual(card["hostname"], "home-box.example.net")
        self.assertTrue(card["online"])
        self.assertEqual(card["sessions"], 2)
        self.assertEqual(card["tokens"], 1500)
        self.assertEqual(card["loadPct"], 50)
        self.assertEqual(card["os"], "Ubuntu 24.04")
        self.assertEqual(card["spark"], [1, 2, 3])

    def test_card_offline_when_no_stats(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=None, stats=None,
        )
        self.assertFalse(card["online"])
        # An unreachable device cannot vouch for a session count or a CPU
        # reading: null, not a fabricated 0 that would read as "confirmed
        # none"/"confirmed idle". tokens is untouched by this (a separate,
        # already-flagged stale field).
        self.assertIsNone(card["sessions"])
        self.assertIsNone(card["loadPct"])
        self.assertEqual(card["tokens"], 0)
        self.assertEqual(card["spark"], [])

    def test_unreachable_device_reports_null_not_zero(self):
        # fetch_remote_card's except branch (the real caller for a device
        # that couldn't be reached at all) calls exactly this shape:
        # sessions and stats both None, online explicitly False.
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=None, stats=None, online=False,
        )
        self.assertIsNone(card["sessions"])
        self.assertIsNone(card["loadPct"])

    def test_online_with_sessions_but_no_stats(self):
        card = overview.card_from_parts(
            device={"id": "old", "name": "Old", "base_url": "http://old:8200"},
            sessions=[{"tokens": 100}], stats=None, online=True,
        )
        self.assertTrue(card["online"])
        self.assertEqual(card["sessions"], 1)
        self.assertEqual(card["tokens"], 100)
        self.assertEqual(card["loadPct"], 0)
        self.assertEqual(card["os"], "")
        self.assertEqual(card["spark"], [])

    def test_loadpct_caps_at_100(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=[], stats={"loadavg": [9.0], "cores": 2, "os": "o", "token_history": []},
        )
        self.assertEqual(card["loadPct"], 100)

    def test_sessions_count_excludes_external_rows(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=[{"tokens": 100}, {"tokens": 50, "external": True}],
            stats=None, online=True,
        )
        self.assertEqual(card["sessions"], 1)
        self.assertEqual(card["tokens"], 150)


class CardFromPartsClaudeVersionTest(unittest.TestCase):
    def test_claude_version_read_from_stats(self):
        card = overview.card_from_parts(
            {"id": "local", "name": "local"}, [], {"claude_version": "2.1.263"})
        self.assertEqual(card["claude_version"], "2.1.263")

    def test_claude_version_none_when_stats_missing(self):
        card = overview.card_from_parts({"id": "local", "name": "local"}, [], None)
        self.assertIsNone(card["claude_version"])

    def test_claude_version_none_when_absent_from_stats(self):
        card = overview.card_from_parts({"id": "local", "name": "local"}, [], {})
        self.assertIsNone(card["claude_version"])


class CardFromPartsLauncherVersionTest(unittest.TestCase):
    """The device's own launcher version (from /rc/stats' "version" field),
    surfaced on the card so the UI can flag a mismatch against the hub."""

    def test_version_read_from_stats(self):
        card = overview.card_from_parts(
            {"id": "local", "name": "local"}, [], {"version": "2.1.4"})
        self.assertEqual(card["version"], "2.1.4")

    def test_version_none_when_stats_missing(self):
        card = overview.card_from_parts({"id": "local", "name": "local"}, [], None)
        self.assertIsNone(card["version"])

    def test_version_none_when_absent_from_stats(self):
        card = overview.card_from_parts({"id": "local", "name": "local"}, [], {})
        self.assertIsNone(card["version"])


class BuildConfigMatrixTest(unittest.TestCase):
    def _report(self, head="abc123", dirty=False, dirty_files=None, deps_missing=None,
                missing=None, hooks=True, version="2.1.263", launcher_version="2.1.10",
                base_sync=None, declared=None, disabled=None):
        return {
            "claude_version": version,
            "launcher_version": launcher_version,
            "claude_config": {"head": head, "dirty": dirty, "dirty_files": dirty_files or []},
            "skills": {"deps_missing": deps_missing or [], "dangling": [], "device_only": []},
            "plugins": {"missing": missing or [], "extra": [],
                        "declared": declared or [], "disabled": disabled or []},
            "settings": {"hooks_present": hooks, "base_sync": base_sync},
            "rules": {"shared": [], "local": []},
        }

    def test_local_device_has_no_skew_against_itself(self):
        hub = self._report()
        matrix = overview.build_config_matrix(hub, [], fetch=lambda d: None)
        self.assertEqual(matrix["skew"]["local"], [])
        self.assertEqual(matrix["hub_head"], "abc123")

    def test_head_mismatch_flagged(self):
        hub = self._report(head="abc123")
        other = self._report(head="def456")
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertIn("head differs from hub", matrix["skew"]["dev2"])

    def test_dirty_deps_missing_missing_plugins_no_hooks_and_version(self):
        hub = self._report()
        other = self._report(head="abc123", dirty=True, dirty_files=["agents/claude.md"],
                              deps_missing=["watch"], missing=["ghost@official"],
                              hooks=False, version="2.0.0")
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        reasons = matrix["skew"]["dev2"]
        for expected in ("dirty", "external skills not installed (run bootstrap)",
                          "missing plugins", "no hooks", "claude version differs"):
            self.assertIn(expected, reasons)
        self.assertNotIn("head differs from hub", reasons)
        self.assertNotIn("settings out of date (run bootstrap)", reasons)

    def test_base_sync_stale_flagged_as_out_of_date(self):
        hub = self._report()
        other = self._report(head="abc123",
                              base_sync={"kind": "stale", "missing": ["hooks"], "differing": []})
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        reasons = matrix["skew"]["dev2"]
        self.assertIn("settings out of date (run bootstrap)", reasons)
        self.assertNotIn("dirty", reasons)

    def test_base_sync_in_sync_not_flagged(self):
        hub = self._report()
        other = self._report(head="abc123",
                              base_sync={"kind": "in-sync", "missing": [], "differing": []})
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertEqual(matrix["skew"]["dev2"], [])

    def test_base_sync_unknown_not_flagged_but_report_stays_visible(self):
        hub = self._report()
        other = self._report(head="abc123",
                              base_sync={"kind": "unknown", "missing": [], "differing": []})
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertEqual(matrix["skew"]["dev2"], [])
        self.assertEqual(matrix["devices"]["dev2"]["settings"]["base_sync"]["kind"], "unknown")

    def test_plugins_installed_but_disabled_flagged(self):
        hub = self._report()
        other = self._report(head="abc123", declared=["watch@official"], disabled=["watch@official"])
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertIn("plugins installed but disabled", matrix["skew"]["dev2"])

    def test_disabled_plugin_not_declared_is_not_flagged(self):
        hub = self._report()
        other = self._report(head="abc123", declared=["watch@official"], disabled=["extra@official"])
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertNotIn("plugins installed but disabled", matrix["skew"]["dev2"])

    def test_unreachable_device_marked(self):
        hub = self._report()
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: None)
        self.assertEqual(matrix["skew"]["dev2"], ["unreachable"])
        self.assertEqual(matrix["devices"]["dev2"], {"error": "unreachable"})

    def test_launcher_version_mismatch_flagged(self):
        hub = self._report(launcher_version="2.1.10")
        other = self._report(head="abc123", launcher_version="2.1.9")
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertIn("launcher version differs", matrix["skew"]["dev2"])

    def test_launcher_version_match_not_flagged(self):
        hub = self._report(launcher_version="2.1.10")
        other = self._report(head="abc123", launcher_version="2.1.10")
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertNotIn("launcher version differs", matrix["skew"]["dev2"])

    def test_matching_device_has_no_skew(self):
        hub = self._report()
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: self._report())
        self.assertEqual(matrix["skew"]["dev2"], [])

    def test_empty_rules_and_device_only_skills_are_never_skew(self):
        hub = self._report()
        other = self._report(head="abc123")
        other["skills"]["device_only"] = ["google-workspace"]
        matrix = overview.build_config_matrix(
            hub, [{"id": "dev2", "base_url": "http://x"}], fetch=lambda d: other)
        self.assertEqual(matrix["skew"]["dev2"], [])


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


class RedirectNeverForwardsBasicAuthTest(unittest.TestCase):
    """Fix round 2, Important: overview._fetch sends this hub's own Basic
    auth password for a device (from devices.json) on every call. Bare
    urlopen would re-send that header to wherever a device's 302 points
    -- proven here with two real loopback HTTP servers, same as
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

    def test_302_to_another_origin_never_forwards_the_device_password(self):
        with self.assertRaises(Exception):
            overview._fetch(
                self.origin_base_url, "/rc/sessions",
                "hub", "super-secret-device-password")
        self.assertEqual(_AttackerHandler.hits, [],
                          "the redirect target must never receive a request, "
                          "and the device password must never reach it")

    def test_fetch_remote_card_reports_offline_not_a_raise(self):
        # fetch_remote_card is the real caller; it must degrade to
        # online=False rather than letting the HTTPError escape.
        device = {"id": "dev1", "name": "dev1", "base_url": self.origin_base_url,
                  "auth_user": "hub", "auth_pass": "super-secret-device-password"}
        card = overview.fetch_remote_card(device)
        self.assertFalse(card["online"])
        self.assertIsNone(card["sessions"])
        self.assertIsNone(card["loadPct"])
        self.assertEqual(_AttackerHandler.hits, [])


if __name__ == "__main__":
    unittest.main()
