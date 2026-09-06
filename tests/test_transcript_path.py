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


class GetTranscriptUsesSessionIdFirstTest(unittest.TestCase):
    """get_transcript must use RC_SESSION_ID (via transcript_path) instead
    of scanning JSONL titles when the env var is present and the file
    exists — the title scan is the pre-v3 fallback only."""

    def setUp(self):
        self._patched_env = sessions.get_session_env
        self._patched_shell = sessions.is_shell_session
        self._patched_find_uuid = sessions._find_session_uuid
        self.find_uuid_calls = []
        sessions._find_session_uuid = lambda *a, **kw: (
            self.find_uuid_calls.append((a, kw)) or None
        )

    def tearDown(self):
        sessions.get_session_env = self._patched_env
        sessions.is_shell_session = self._patched_shell
        sessions._find_session_uuid = self._patched_find_uuid

    def test_skips_title_scan_when_rc_session_id_present_and_file_exists(self):
        import tempfile, json as jsonlib

        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "proj")
            os.makedirs(workdir)
            session_id = "abc123"
            proj_dir_name = workdir.replace("/", "-").replace(".", "-")
            claude_projects = os.path.join(tmp, ".claude", "projects", proj_dir_name)
            os.makedirs(claude_projects)
            transcript_file = os.path.join(claude_projects, session_id + ".jsonl")
            with open(transcript_file, "w") as f:
                f.write(jsonlib.dumps({
                    "type": "user", "message": {"role": "user", "content": "hi"},
                    "timestamp": "2026-09-06T00:00:00Z",
                }) + "\n")

            sessions.is_shell_session = lambda name: False
            sessions.get_session_env = lambda name, var: {
                "RC_SESSION_ID": session_id, "RC_WORKDIR": workdir,
            }.get(var)

            real_expanduser = os.path.expanduser
            fake_home = tmp

            def fake_expanduser(p):
                if p.startswith("~"):
                    return fake_home + p[1:]
                return real_expanduser(p)
            os.path.expanduser = fake_expanduser
            try:
                result = sessions.get_transcript("rc-portugal")
            finally:
                os.path.expanduser = real_expanduser

            self.assertIsNotNone(result)
            self.assertEqual(result["sessionId"], session_id)
            self.assertEqual(len(result["messages"]), 1)
            self.assertEqual(self.find_uuid_calls, [])  # title scan never invoked


if __name__ == "__main__":
    unittest.main()
