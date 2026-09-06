"""compat.py: feature-detect the installed `claude` binary from --help
output plus a live `agents --json` probe, once, cached in CAPS."""
import os, sys, unittest
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


class ClaudeVersionTest(unittest.TestCase):
    def test_reads_from_caps(self):
        compat.refresh_caps(NEW_CLAUDE)
        self.assertEqual(compat.claude_version(), "2.1.263")
        compat.refresh_caps(OLD_CLAUDE)
        self.assertEqual(compat.claude_version(), "1.9.0")
