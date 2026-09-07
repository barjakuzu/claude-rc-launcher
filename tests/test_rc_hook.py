import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
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
        # Isolated fake $RC_HOME so tests never touch the real
        # ~/.claude-rc/env this host may actually have installed.
        self.rc_home_tmp = tempfile.TemporaryDirectory()
        self.rc_home = self.rc_home_tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        self.rc_home_tmp.cleanup()

    def _env(self, extra=None):
        env = {"RC_HOME": self.rc_home}
        if extra:
            env.update(extra)
        return env

    def _run(self, payload, env=None, now_fn=None):
        stdin = io.StringIO(json.dumps(payload))
        rc_hook.main([], stdin, self._env(env), now_fn=now_fn or (lambda: 1000.0),
                     events_root=self.events_root)

    def _read_lines(self):
        files = [f for f in os.listdir(self.events_root) if f.endswith(".jsonl")]
        self.assertEqual(len(files), 1)
        path = os.path.join(self.events_root, files[0])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_appends_one_jsonl_line_full_role(self):
        self._run({"hook_event_name": "SessionEnd", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "reason": "clear"})
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        row = lines[0]
        self.assertEqual(row["event"], "SessionEnd")
        self.assertEqual(row["session_id"], "abc123")
        self.assertEqual(row["cwd"], "/home/alice/proj")
        self.assertEqual(row["extra"], {"reason": "clear"})
        self.assertEqual(row["ts"], 1000.0)

    def test_unknown_extra_fields_are_dropped(self):
        self._run({"hook_event_name": "SubagentStop", "session_id": "s1", "cwd": "/tmp",
                    "stop_hook_active": True, "transcript_path": "/tmp/x.jsonl",
                    "some_future_field": "junk"})
        row = self._read_lines()[0]
        self.assertEqual(row["extra"], {"stop_hook_active": True})

    def test_unexpected_event_name_recorded_with_empty_extra(self):
        # SessionStart/UserPromptSubmit/Stop are deliberately not in the
        # allowlist (tmux/claude-agents polling already covers them), but
        # any event name Claude Code sends is still recorded so a future
        # event needs no rc-hook code change - just an allowlist entry.
        self._run({"hook_event_name": "SessionStart", "session_id": "s1",
                    "cwd": "/tmp", "source": "startup"})
        row = self._read_lines()[0]
        self.assertEqual(row["event"], "SessionStart")
        self.assertEqual(row["extra"], {})

    def test_metadata_role_hashes_session_id_and_drops_cwd(self):
        self._run({"hook_event_name": "SessionEnd", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "reason": "clear"},
                   env={"RC_ROLE": "metadata", "RC_HASH_SALT": "pepper"})
        row = self._read_lines()[0]
        self.assertNotIn("cwd", row)
        self.assertNotEqual(row["session_id"], "abc123")
        self.assertEqual(len(row["session_id"]), 64)  # hex sha256

    def test_metadata_role_still_hashes_with_no_salt_available(self):
        # Never falls back to the raw id, even with no salt configured
        # anywhere (no process env, no $RC_HOME/env).
        self._run({"hook_event_name": "SessionEnd", "session_id": "abc123",
                    "cwd": "/home/alice/proj", "reason": "clear"},
                   env={"RC_ROLE": "metadata"})
        row = self._read_lines()[0]
        self.assertNotEqual(row["session_id"], "abc123")
        self.assertEqual(len(row["session_id"]), 64)

    def test_malformed_stdin_never_raises_and_exits_zero(self):
        stdin = io.StringIO("not json{{{")
        code = rc_hook.main([], stdin, self._env(), now_fn=lambda: 1.0,
                             events_root=self.events_root)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.events_root), [])

    def test_missing_session_id_is_dropped_silently(self):
        stdin = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        code = rc_hook.main([], stdin, self._env(), now_fn=lambda: 1.0,
                             events_root=self.events_root)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.events_root), [])


class EnvFileFallbackTest(unittest.TestCase):
    """RC_ROLE/RC_HASH_SALT: process env wins; $RC_HOME/env is the
    fallback, since a hook Claude Code spawns doesn't inherit the
    launcher's systemd/launchd environment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events_root = self.tmp.name
        self.rc_home_tmp = tempfile.TemporaryDirectory()
        self.rc_home = self.rc_home_tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        self.rc_home_tmp.cleanup()

    def _write_env_file(self, text):
        with open(os.path.join(self.rc_home, "env"), "w") as f:
            f.write(text)

    def _run(self, env):
        payload = {"hook_event_name": "SessionEnd", "session_id": "abc123",
                   "cwd": "/home/alice/proj"}
        stdin = io.StringIO(json.dumps(payload))
        rc_hook.main([], stdin, env, now_fn=lambda: 1000.0, events_root=self.events_root)
        files = [f for f in os.listdir(self.events_root) if f.endswith(".jsonl")]
        with open(os.path.join(self.events_root, files[0])) as f:
            return json.loads(f.readline())

    def test_reads_role_and_salt_from_rc_home_env_file(self):
        self._write_env_file("# comment\nexport RC_ROLE=metadata\nRC_HASH_SALT=filesalt\n")
        row = self._run({"RC_HOME": self.rc_home})
        self.assertNotIn("cwd", row)
        self.assertNotEqual(row["session_id"], "abc123")

    def test_missing_env_file_tolerated_defaults_to_full(self):
        row = self._run({"RC_HOME": self.rc_home})
        self.assertEqual(row["cwd"], "/home/alice/proj")
        self.assertEqual(row["session_id"], "abc123")

    def test_process_env_takes_precedence_over_file(self):
        self._write_env_file("RC_ROLE=metadata\nRC_HASH_SALT=filesalt\n")
        row = self._run({"RC_HOME": self.rc_home, "RC_ROLE": "full"})
        self.assertEqual(row["cwd"], "/home/alice/proj")
        self.assertEqual(row["session_id"], "abc123")


class LargeStdinPayloadTest(unittest.TestCase):
    """A single os.read() on a pipe can return far fewer bytes than a
    large payload (typical pipe buffer is 64 KB; a StopFailure or
    Notification payload can carry a sizable transcript/error field);
    rc-hook must loop reads until EOF or the 1 MB cap instead of
    dropping the event."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events_root = self.tmp.name
        self.rc_home_tmp = tempfile.TemporaryDirectory()
        self.rc_home = self.rc_home_tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        self.rc_home_tmp.cleanup()

    def test_large_payload_via_real_pipe_is_not_dropped(self):
        big_field = "x" * 200_000  # well over a 64 KB pipe buffer
        payload = json.dumps({"hook_event_name": "SessionEnd",
                               "session_id": "s1", "cwd": "/tmp", "reason": big_field})
        r_fd, w_fd = os.pipe()

        def _feed():
            data = payload.encode()
            written = 0
            while written < len(data):
                written += os.write(w_fd, data[written:])
            os.close(w_fd)

        import threading
        t = threading.Thread(target=_feed)
        t.start()
        try:
            with os.fdopen(r_fd, "rb", buffering=0) as stdin:
                code = rc_hook.main([], stdin, {"RC_HOME": self.rc_home},
                                     now_fn=time.time, events_root=self.events_root)
        finally:
            t.join()

        self.assertEqual(code, 0)
        files = [f for f in os.listdir(self.events_root) if f.endswith(".jsonl")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.events_root, files[0])) as f:
            row = json.loads(f.readline())
        self.assertEqual(row["extra"]["reason"], big_field)


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events_root = self.tmp.name
        self.rc_home_tmp = tempfile.TemporaryDirectory()
        self.rc_home = self.rc_home_tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        self.rc_home_tmp.cleanup()

    def _run(self, now):
        payload = {"hook_event_name": "Stop", "session_id": "s1", "cwd": "/tmp"}
        stdin = io.StringIO(json.dumps(payload))
        return rc_hook.main([], stdin, {"RC_HOME": self.rc_home}, now_fn=lambda: now,
                             events_root=self.events_root)

    def test_rotates_aside_when_spool_file_exceeds_size_cap(self):
        today = "2026-09-07"
        now = time.mktime(time.strptime(today, "%Y-%m-%d"))
        path = os.path.join(self.events_root, f"{today}.jsonl")
        with open(path, "wb") as f:
            f.write(b"x" * (rc_hook.ROTATE_SIZE_BYTES + 1))
        code = self._run(now)
        self.assertEqual(code, 0)
        rotated = path + ".1"
        self.assertTrue(os.path.exists(rotated))
        # The next append (not this one) starts a fresh current file.
        self.assertGreater(os.path.getsize(rotated), rc_hook.ROTATE_SIZE_BYTES)

    def test_deletes_spool_files_older_than_retention(self):
        old_date = "2026-08-01"
        old_path = os.path.join(self.events_root, f"{old_date}.jsonl")
        with open(old_path, "w") as f:
            f.write('{"ts": 1, "event": "Stop", "session_id": "a", "extra": {}}\n')
        now = time.mktime(time.strptime("2026-09-07", "%Y-%m-%d"))
        code = self._run(now)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(old_path))

    def test_never_fails_when_events_dir_unwritable(self):
        os.chmod(self.events_root, 0o500)
        try:
            code = self._run(time.time())
        finally:
            os.chmod(self.events_root, 0o700)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
