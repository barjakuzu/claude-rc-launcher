"""transcript_path: pure encoding of (workdir, session_id) -> the JSONL
path Claude Code writes to. Encoding verified against this box's actual
~/.claude/projects listing (2026-09-06): '/' and '.' both become '-'."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sessions


class TranscriptPathTest(unittest.TestCase):
    def test_simple_path_no_dots(self):
        p = sessions.transcript_path("/home/user/project", "abc123")
        self.assertEqual(
            p, os.path.expanduser("~/.claude/projects/-home-user-project/abc123.jsonl"))

    def test_path_with_a_dot_segment(self):
        # Matches the real encoding of ~/.claude-rc on this box (leading
        # dot of .claude-rc becomes an extra hyphen, i.e. '.' -> '-' just
        # like '/' -> '-').
        p = sessions.transcript_path("/home/user/.claude-rc", "abc123")
        self.assertEqual(
            p, os.path.expanduser("~/.claude/projects/-home-user--claude-rc/abc123.jsonl"))

    def test_nested_path(self):
        p = sessions.transcript_path("/var/www/rc-launcher-p1", "abc123")
        self.assertEqual(
            p, os.path.expanduser("~/.claude/projects/-var-www-rc-launcher-p1/abc123.jsonl"))


if __name__ == "__main__":
    unittest.main()
