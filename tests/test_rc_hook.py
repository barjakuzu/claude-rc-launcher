import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest

_hook_path = os.path.join(os.path.dirname(__file__), "..", "hooks", "rc-hook")
_loader = importlib.machinery.SourceFileLoader("rc_hook", _hook_path)
_spec = importlib.util.spec_from_loader("rc_hook", _loader)
rc_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc_hook)


class RcHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events_root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload, env=None):
        stdin = io.StringIO(json.dumps(payload))
        rc_hook.main([], stdin, env or {}, now_fn=lambda: 1000.0,
                     events_root=self.events_root)

    def _read_lines(self):
        files = os.listdir(self.events_root)
        self.assertEqual(len(files), 1)
        path = os.path.join(self.events_root, files[0])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_appends_one_jsonl_line_full_role(self):
        self._run({"hook_event_name": "SessionStart", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "source": "startup"})
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        row = lines[0]
        self.assertEqual(row["event"], "SessionStart")
        self.assertEqual(row["session_id"], "abc123")
        self.assertEqual(row["cwd"], "/home/alice/proj")
        self.assertEqual(row["extra"], {"source": "startup"})
        self.assertEqual(row["ts"], 1000.0)

    def test_userpromptsubmit_never_stores_prompt_text(self):
        self._run({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                    "cwd": "/tmp", "prompt": "the secret prompt text"})
        row = self._read_lines()[0]
        self.assertNotIn("prompt", row["extra"])
        self.assertEqual(row["extra"]["prompt_len"], len("the secret prompt text"))

    def test_unknown_extra_fields_are_dropped(self):
        self._run({"hook_event_name": "Stop", "session_id": "s1", "cwd": "/tmp",
                    "stop_hook_active": True, "transcript_path": "/tmp/x.jsonl",
                    "some_future_field": "junk"})
        row = self._read_lines()[0]
        self.assertEqual(row["extra"], {"stop_hook_active": True})

    def test_metadata_role_hashes_session_id_and_drops_cwd(self):
        self._run({"hook_event_name": "SessionEnd", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "reason": "clear"},
                   env={"RC_ROLE": "metadata", "RC_HASH_SALT": "pepper"})
        row = self._read_lines()[0]
        self.assertNotIn("cwd", row)
        self.assertNotEqual(row["session_id"], "abc123")
        self.assertEqual(len(row["session_id"]), 64)  # hex sha256

    def test_malformed_stdin_never_raises_and_exits_zero(self):
        stdin = io.StringIO("not json{{{")
        code = rc_hook.main([], stdin, {}, now_fn=lambda: 1.0,
                             events_root=self.events_root)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.events_root), [])

    def test_missing_session_id_is_dropped_silently(self):
        stdin = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        code = rc_hook.main([], stdin, {}, now_fn=lambda: 1.0,
                             events_root=self.events_root)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.events_root), [])


if __name__ == "__main__":
    unittest.main()
