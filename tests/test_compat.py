"""compat.py: feature-detect the installed `claude` binary from --help
output plus a live `agents --json` probe, lazily, cached in CAPS."""
import os, subprocess, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat

FAKES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakes")
NEW_CLAUDE = os.path.join(FAKES_DIR, "claude")
OLD_CLAUDE = os.path.join(FAKES_DIR, "claude_old")


class DetectCapsNewClaudeTest(unittest.TestCase):
    def test_all_flags_detected(self):
        caps = compat.detect_caps(NEW_CLAUDE)
        self.assertTrue(caps["session_id_flag"])
        self.assertTrue(caps["name_flag"])
        self.assertTrue(caps["remote_control_flag"])
        self.assertTrue(caps["permission_mode_flag"])
        self.assertTrue(caps["agents_json"])
        self.assertEqual(caps["version"], "2.1.263")


class DetectCapsOldClaudeTest(unittest.TestCase):
    def test_no_flags_detected(self):
        caps = compat.detect_caps(OLD_CLAUDE)
        self.assertFalse(caps["session_id_flag"])
        self.assertFalse(caps["name_flag"])
        self.assertFalse(caps["remote_control_flag"])
        self.assertFalse(caps["permission_mode_flag"])
        self.assertFalse(caps["agents_json"])
        self.assertEqual(caps["version"], "1.9.0")


class DetectCapsMissingBinaryTest(unittest.TestCase):
    def test_missing_binary_returns_all_false(self):
        caps = compat.detect_caps("/no/such/claude/binary")
        self.assertFalse(caps["session_id_flag"])
        self.assertFalse(caps["agents_json"])
        self.assertIsNone(caps["version"])


class DetectCapsHangingBinaryTest(unittest.TestCase):
    def test_timeout_degrades_to_all_false(self):
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

        caps = compat.detect_caps(NEW_CLAUDE, run=fake_run)

        self.assertFalse(caps["session_id_flag"])
        self.assertFalse(caps["name_flag"])
        self.assertFalse(caps["remote_control_flag"])
        self.assertFalse(caps["permission_mode_flag"])
        self.assertFalse(caps["agents_json"])
        self.assertIsNone(caps["version"])


class DetectCapsGarbageAgentsJsonTest(unittest.TestCase):
    def test_non_json_agents_output_degrades_to_false(self):
        class R:
            def __init__(self, cmd):
                self.returncode = 0 if "agents" not in cmd else 1
                self.stdout = "not json at all" if "agents" in cmd else ""
                self.stderr = ""

        def fake_run(cmd, **kw):
            return R(cmd)

        caps = compat.detect_caps(NEW_CLAUDE, run=fake_run)

        self.assertFalse(caps["agents_json"])


class GetCapsLazyTest(unittest.TestCase):
    """get_caps() must not run detection until first called, and must
    memoize after that - importing compat (or anything that imports it)
    must never shell out."""

    def setUp(self):
        self._snapshot = dict(compat.CAPS)
        self._detected_snapshot = compat._detected
        self.addCleanup(self._restore)

    def _restore(self):
        compat.CAPS.clear()
        compat.CAPS.update(self._snapshot)
        compat._detected = self._detected_snapshot

    def test_get_caps_memoizes_across_calls(self):
        calls = []
        real_run = subprocess.run

        def counting_run(cmd, **kw):
            calls.append(cmd)
            return real_run(cmd, **kw)

        compat.CAPS.clear()
        compat._detected = False
        compat.subprocess.run = counting_run
        try:
            import config
            old_bin = config.CLAUDE_BIN
            config.CLAUDE_BIN = NEW_CLAUDE
            try:
                first = compat.get_caps()
                count_after_first = len(calls)
                second = compat.get_caps()
            finally:
                config.CLAUDE_BIN = old_bin
        finally:
            compat.subprocess.run = real_run

        self.assertIs(first, second)
        self.assertEqual(len(calls), count_after_first)  # no new subprocess calls


class ClaudeVersionTest(unittest.TestCase):
    def setUp(self):
        self._snapshot = dict(compat.CAPS)
        self._detected_snapshot = compat._detected
        self.addCleanup(self._restore)

    def _restore(self):
        compat.CAPS.clear()
        compat.CAPS.update(self._snapshot)
        compat._detected = self._detected_snapshot

    def test_reads_from_caps(self):
        compat.refresh_caps(NEW_CLAUDE)
        self.assertEqual(compat.claude_version(), "2.1.263")
        compat.refresh_caps(OLD_CLAUDE)
        self.assertEqual(compat.claude_version(), "1.9.0")


if __name__ == "__main__":
    unittest.main()
