"""Tests for notify.py: turning newly-fired guard findings into an
outbound notification via RC_NOTIFY_CMD.

Never wires up or exercises the user's real notification transport (none
lives in this repo) -- every test here uses a throwaway recorder script
under a tempdir that just writes whatever it received on stdin to a file,
so this suite can assert on exactly what would have been sent without
depending on, or even naming, any real chat/paging integration.

Fixtures use neutral device/session identifiers (dev-a, sess-1) and
synthetic epoch timestamps -- CI greps tracked files for personal
identifiers, so no real hostnames, usernames, chat ids or tokens appear
anywhere here.
"""
import json
import os
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify


def _finding(rule="token_rate", severity="alert", device_id="dev-a",
             session_id="sess-1", name="rc-foo", message="m", value=1.0,
             threshold=2.0, target_type="session"):
    return {
        "rule": rule, "severity": severity, "target_type": target_type,
        "device_id": device_id, "session_id": session_id, "name": name,
        "message": message, "value": value, "threshold": threshold,
        "since": 100.0,
    }


class _FakeStore:
    """A minimal stand-in for store.Store's claim_notifications: an
    in-memory dict keyed the same way, so notify.py's poll-loop entry
    point can be unit-tested without a real sqlite file."""

    def __init__(self):
        self._last = {}

    def claim_notifications(self, keys, cooldown_seconds, now_fn=time.time):
        now = now_fn()
        claimed = []
        for key in keys:
            last = self._last.get(key)
            if last is not None and (now - last) < cooldown_seconds:
                continue
            self._last[key] = now
            claimed.append(key)
        return claimed


def _write_recorder(tmpdir, name="recorder.py"):
    """A script that appends whatever it receives on stdin, followed by a
    newline separator, to `out_path` -- lets a test assert on exactly one
    call producing exactly one JSON object, or several calls producing
    several. Returns (cmd, out_path)."""
    out_path = os.path.join(tmpdir, "out.log")
    script_path = os.path.join(tmpdir, name)
    with open(script_path, "w") as f:
        f.write(
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            f"with open({out_path!r}, 'ab') as out:\n"
            "    out.write(data + b'\\n---\\n')\n"
        )
    os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IEXEC)
    cmd = f"{sys.executable} {script_path}"
    return cmd, out_path


def _write_hanging_script(tmpdir, sleep_seconds=5):
    script_path = os.path.join(tmpdir, "hang.py")
    with open(script_path, "w") as f:
        f.write(f"import time\ntime.sleep({sleep_seconds})\n")
    return f"{sys.executable} {script_path}"


def _read_records(out_path):
    if not os.path.exists(out_path):
        return []
    with open(out_path, "rb") as f:
        raw = f.read()
    chunks = [c for c in raw.split(b"\n---\n") if c.strip()]
    return [json.loads(c) for c in chunks]


class EnvConfigTest(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for k in ("RC_NOTIFY_CMD", "RC_NOTIFY_MIN_SEVERITY",
                  "RC_NOTIFY_TIMEOUT_SECONDS", "RC_NOTIFY_COOLDOWN_SECONDS"):
            self._saved[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_notify_cmd_unset_is_empty_string(self):
        self.assertEqual(notify.notify_cmd(), "")

    def test_notify_cmd_whitespace_only_counts_as_unset(self):
        os.environ["RC_NOTIFY_CMD"] = "   "
        self.assertEqual(notify.notify_cmd(), "")

    def test_min_severity_defaults_to_alert(self):
        self.assertEqual(notify.min_severity(), "alert")

    def test_min_severity_accepts_warn(self):
        os.environ["RC_NOTIFY_MIN_SEVERITY"] = "warn"
        self.assertEqual(notify.min_severity(), "warn")

    def test_min_severity_bad_value_falls_back_to_default(self):
        os.environ["RC_NOTIFY_MIN_SEVERITY"] = "critical"
        self.assertEqual(notify.min_severity(), "alert")

    def test_timeout_defaults_and_rejects_non_positive(self):
        self.assertEqual(notify.timeout_seconds(), notify.DEFAULT_TIMEOUT_SECONDS)
        os.environ["RC_NOTIFY_TIMEOUT_SECONDS"] = "-5"
        self.assertEqual(notify.timeout_seconds(), notify.DEFAULT_TIMEOUT_SECONDS)
        os.environ["RC_NOTIFY_TIMEOUT_SECONDS"] = "3.5"
        self.assertEqual(notify.timeout_seconds(), 3.5)

    def test_cooldown_defaults_and_rejects_garbage(self):
        self.assertEqual(notify.cooldown_seconds(), notify.DEFAULT_COOLDOWN_SECONDS)
        os.environ["RC_NOTIFY_COOLDOWN_SECONDS"] = "not-a-number"
        self.assertEqual(notify.cooldown_seconds(), notify.DEFAULT_COOLDOWN_SECONDS)


class BuildEventTest(unittest.TestCase):
    def test_compact_finding_allowlist_drops_unknown_fields(self):
        f = _finding()
        f["cwd"] = "/secret/path"       # must never appear in a notification
        f["prompt"] = "do the thing"    # must never appear in a notification
        event = notify.build_event([f])
        self.assertEqual(event["count"], 1)
        sent = event["findings"][0]
        self.assertNotIn("cwd", sent)
        self.assertNotIn("prompt", sent)
        self.assertEqual(sent["rule"], "token_rate")
        self.assertEqual(sent["device_id"], "dev-a")
        self.assertEqual(sent["session_id"], "sess-1")
        self.assertEqual(sent["threshold"], 2.0)

    def test_build_event_json_serializable(self):
        event = notify.build_event([_finding(), _finding(rule="session_age")])
        json.dumps(event)  # must not raise


class SendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_send_delivers_one_json_event_on_stdin(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        ok = notify.send([_finding()], cmd=cmd, timeout=5)
        self.assertTrue(ok)
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["count"], 1)
        self.assertEqual(records[0]["findings"][0]["rule"], "token_rate")

    def test_send_with_no_findings_is_a_noop(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        ok = notify.send([], cmd=cmd, timeout=5)
        self.assertFalse(ok)
        self.assertEqual(_read_records(out_path), [])

    def test_send_with_no_cmd_is_a_noop(self):
        ok = notify.send([_finding()], cmd="", timeout=5)
        self.assertFalse(ok)

    def test_send_grouped_burst_is_exactly_one_call(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        findings = [_finding(session_id=f"sess-{i}") for i in range(5)]
        ok = notify.send(findings, cmd=cmd, timeout=5)
        self.assertTrue(ok)
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)          # one message, not five
        self.assertEqual(records[0]["count"], 5)

    def test_send_hanging_command_times_out_and_returns_false(self):
        cmd = _write_hanging_script(self.tmp.name, sleep_seconds=5)
        start = time.time()
        ok = notify.send([_finding()], cmd=cmd, timeout=0.5)
        elapsed = time.time() - start
        self.assertFalse(ok)
        self.assertLess(elapsed, 4.0)  # did not wait out the 5s sleep

    def test_send_invalid_shell_syntax_does_not_raise(self):
        ok = notify.send([_finding()], cmd='unterminated "quote', timeout=5)
        self.assertFalse(ok)

    def test_send_nonexistent_command_does_not_raise(self):
        ok = notify.send([_finding()], cmd="/no/such/binary-xyz", timeout=5)
        self.assertFalse(ok)


class NotifyNewFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _FakeStore()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_fire_notifies_via_env(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        try:
            notify.notify_new_findings([_finding()], self.store, now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["findings"][0]["rule"], "token_rate")

    def test_repeat_cycles_with_no_new_findings_do_not_notify(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        try:
            notify.notify_new_findings([_finding()], self.store, now_fn=lambda: 1000.0)
            # The poll loop only ever passes replace_alerts's "new" list --
            # an already-firing finding is simply never in that list on a
            # later cycle, so passing [] here models every subsequent
            # 30-second cycle of a still-open finding.
            for t in (1030.0, 1060.0, 1090.0):
                notify.notify_new_findings([], self.store, now_fn=lambda: t)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)  # not four

    def test_fifteen_findings_at_once_is_one_grouped_message(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        try:
            findings = [_finding(session_id=f"sess-{i}") for i in range(15)]
            notify.notify_new_findings(findings, self.store, now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["count"], 15)

    def test_warn_below_default_threshold_is_filtered(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        try:
            notify.notify_new_findings(
                [_finding(severity="warn", rule="session_age")], self.store,
                now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
        self.assertEqual(_read_records(out_path), [])

    def test_warn_notifies_when_threshold_lowered(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        os.environ["RC_NOTIFY_MIN_SEVERITY"] = "warn"
        try:
            notify.notify_new_findings(
                [_finding(severity="warn", rule="session_age")], self.store,
                now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
            del os.environ["RC_NOTIFY_MIN_SEVERITY"]
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)

    def test_mixed_batch_only_alert_passes_default_threshold(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        try:
            notify.notify_new_findings(
                [_finding(severity="warn", rule="session_age"),
                 _finding(severity="alert", rule="token_total")],
                self.store, now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["count"], 1)
        self.assertEqual(records[0]["findings"][0]["rule"], "token_total")

    def test_cooldown_floor_suppresses_a_flapping_finding_within_the_window(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        os.environ["RC_NOTIFY_COOLDOWN_SECONDS"] = "3600"
        try:
            notify.notify_new_findings([_finding()], self.store, now_fn=lambda: 1000.0)
            # The finding cleared and re-fired (a fresh "new" observation
            # from replace_alerts's point of view) 5 minutes later, well
            # inside the 1-hour cooldown floor.
            notify.notify_new_findings([_finding()], self.store, now_fn=lambda: 1300.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
            del os.environ["RC_NOTIFY_COOLDOWN_SECONDS"]
        records = _read_records(out_path)
        self.assertEqual(len(records), 1)

    def test_unset_cmd_is_silent_and_never_touches_the_store(self):
        os.environ.pop("RC_NOTIFY_CMD", None)

        class _ExplodingStore:
            def claim_notifications(self, *a, **k):
                raise AssertionError("must not be called when RC_NOTIFY_CMD is unset")

        # Must not raise, and must not call claim_notifications at all.
        notify.notify_new_findings([_finding()], _ExplodingStore(), now_fn=lambda: 1000.0)

    def test_empty_new_findings_is_a_noop_even_with_cmd_set(self):
        cmd, out_path = _write_recorder(self.tmp.name)
        os.environ["RC_NOTIFY_CMD"] = cmd
        try:
            notify.notify_new_findings([], self.store, now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]
        self.assertEqual(_read_records(out_path), [])

    def test_store_error_does_not_raise(self):
        os.environ["RC_NOTIFY_CMD"] = "true"

        class _BrokenStore:
            def claim_notifications(self, *a, **k):
                raise RuntimeError("boom")

        try:
            notify.notify_new_findings([_finding()], _BrokenStore(), now_fn=lambda: 1000.0)
        finally:
            del os.environ["RC_NOTIFY_CMD"]


if __name__ == "__main__":
    unittest.main()
