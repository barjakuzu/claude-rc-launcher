"""Tests for scheduler.py."""
import os
import sys
import threading
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler


class ScheduleErrorLoggingTest(unittest.TestCase):
    def setUp(self):
        scheduler._last_logged_schedule_error = None

    def tearDown(self):
        scheduler._last_logged_schedule_error = None

    def test_first_occurrence_is_logged(self):
        msg = scheduler._schedule_error_to_log("Extra data: line 5 column 1")
        self.assertIn("Extra data", msg)

    def test_repeated_identical_error_is_not_logged_again(self):
        scheduler._schedule_error_to_log("boom")
        self.assertIsNone(scheduler._schedule_error_to_log("boom"))

    def test_a_different_error_is_logged_again(self):
        scheduler._schedule_error_to_log("boom")
        msg = scheduler._schedule_error_to_log("a different boom")
        self.assertIsNotNone(msg)

    def test_clearing_then_recurring_logs_again(self):
        scheduler._schedule_error_to_log("boom")
        scheduler._schedule_error_to_log(None)  # file loaded cleanly
        msg = scheduler._schedule_error_to_log("boom")
        self.assertIsNotNone(msg)

    def test_no_error_returns_none(self):
        self.assertIsNone(scheduler._schedule_error_to_log(None))


class NullSafeCronTest(unittest.TestCase):
    def test_validate_cron_accepts_null(self):
        self.assertIsNone(scheduler.validate_cron(None))

    def test_next_cron_run_returns_none_for_null(self):
        self.assertIsNone(scheduler.next_cron_run(None))

    def test_validate_cron_still_rejects_bad_strings(self):
        self.assertIsNotNone(scheduler.validate_cron("not a cron"))

    def test_validate_cron_still_accepts_good_strings(self):
        self.assertIsNone(scheduler.validate_cron("0 9 * * *"))


class FakeRun:
    """Records every subprocess.run call scheduler.py makes and answers
    'tmux has-session' / 'tmux list-sessions' according to a settable set
    of "alive" session names. Same approach as tests/test_setup_session.py's
    FakeRun, extended for the session-lifecycle calls scheduler.py makes."""

    def __init__(self):
        self.calls = []
        self.alive = set()
        self.env = {}  # session_name -> {var: value}

    def __call__(self, cmd, *a, **kw):
        self.calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if isinstance(cmd, list) and cmd[:2] == ["tmux", "has-session"]:
            name = cmd[cmd.index("-t") + 1]
            R.returncode = 0 if name in self.alive else 1
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "new-session"]:
            name = cmd[cmd.index("-s") + 1]
            self.alive.add(name)
            # Capture any -e VAR=value pairs onto this session's env.
            env = self.env.setdefault(name, {})
            for i, part in enumerate(cmd):
                if part == "-e" and i + 1 < len(cmd) and "=" in cmd[i + 1]:
                    k, v = cmd[i + 1].split("=", 1)
                    env[k] = v
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "kill-session"]:
            name = cmd[cmd.index("-t") + 1]
            self.alive.discard(name)
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "list-sessions"]:
            R.stdout = "\n".join(self.alive)
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "show-environment"]:
            name = cmd[cmd.index("-t") + 1]
            var = cmd[-1]
            val = self.env.get(name, {}).get(var)
            R.stdout = f"{var}={val}" if val is not None else ""
        return R

    def new_session_names(self):
        return [c[c.index("-s") + 1] for c in self.calls
                if isinstance(c, list) and c[:2] == ["tmux", "new-session"]]


class ImmediateThread:
    """Runs its target synchronously instead of in a background thread, so
    scheduler tests don't race _fire_schedule's async setup-and-send step."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _patch_scheduler(fake):
    """Patch scheduler.py's IO surface with `fake`, returning the original
    values so a test's tearDown can restore them via _restore_scheduler."""
    saved = {
        "run": scheduler.subprocess.run,
        "sleep": scheduler.time.sleep,
        "exists": scheduler.session_exists,
        "setup": scheduler.setup_session,
        "thread": scheduler.threading.Thread,
        "list_sessions": scheduler.list_rc_sessions,
        "add_history": scheduler.add_history_entry,
    }
    scheduler.subprocess.run = fake
    scheduler.time.sleep = lambda *_: None
    scheduler.session_exists = lambda n: n in fake.alive
    scheduler.setup_session = lambda *a, **kw: None
    scheduler.threading.Thread = ImmediateThread
    scheduler.list_rc_sessions = lambda: [{"name": n} for n in fake.alive]
    scheduler._active_scheduled_sessions.clear()
    return saved


def _restore_scheduler(saved):
    scheduler.subprocess.run = saved["run"]
    scheduler.time.sleep = saved["sleep"]
    scheduler.session_exists = saved["exists"]
    scheduler.setup_session = saved["setup"]
    scheduler.threading.Thread = saved["thread"]
    scheduler.list_rc_sessions = saved["list_sessions"]
    scheduler.add_history_entry = saved["add_history"]
    scheduler._active_scheduled_sessions.clear()


class DueToFireTest(unittest.TestCase):
    def test_manual_task_is_never_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertFalse(scheduler._due_to_fire({"cron": None, "enabled": True}, now))

    def test_empty_string_cron_is_never_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertFalse(scheduler._due_to_fire({"cron": "", "enabled": True}, now))

    def test_matching_cron_with_no_last_run_is_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertTrue(scheduler._due_to_fire({"cron": "0 9 * * *", "enabled": True}, now))

    def test_matching_cron_already_run_this_minute_is_not_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        schedule = {"cron": "0 9 * * *", "enabled": True, "last_run": now.isoformat()}
        self.assertFalse(scheduler._due_to_fire(schedule, now))

    def test_non_matching_cron_is_not_due(self):
        now = datetime(2026, 9, 6, 9, 1)
        self.assertFalse(scheduler._due_to_fire({"cron": "0 9 * * *", "enabled": True}, now))

    def test_invalid_cron_string_is_not_due(self):
        now = datetime(2026, 9, 6, 9, 0)
        self.assertFalse(scheduler._due_to_fire({"cron": "garbage", "enabled": True}, now))


class ManualTaskFiresTest(unittest.TestCase):
    """POST /schedules/fire (which calls _fire_schedule directly) must work
    for a manual task even though _due_to_fire would never call it."""

    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def test_fire_schedule_works_with_no_cron(self):
        scheduler._fire_schedule({"id": "manual-1", "name": "ad hoc", "cron": None,
                                   "workdir": "/tmp", "prompt": "do the thing"})
        self.assertEqual(len(self.fake.new_session_names()), 1)
        self.assertIn(("manual-1", "ok"), [(h[0], h[1]) for h in self.history])


class FireScheduleNamingTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def test_session_name_matches_rc_run_hex_pattern(self):
        scheduler._fire_schedule({"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        names = self.fake.new_session_names()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r'^rc-run-[0-9a-f]{12}$')


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def _schedule(self, **overrides):
        s = {"id": "s1", "name": "task", "cron": "0 9 * * *", "workdir": "/tmp", "prompt": "hi"}
        s.update(overrides)
        return s

    def test_default_skip_does_not_fire_a_second_time_while_running(self):
        schedule = self._schedule()
        scheduler._fire_schedule(schedule)
        first_names = self.fake.new_session_names()
        self.assertEqual(len(first_names), 1)

        scheduler._fire_schedule(schedule)
        self.assertEqual(self.fake.new_session_names(), first_names)  # no new session
        self.assertIn(("s1", "skipped"), [(h[0], h[1]) for h in self.history])

    def test_kill_stops_the_old_session_and_starts_a_new_one(self):
        schedule = self._schedule(concurrency="kill")
        scheduler._fire_schedule(schedule)
        first_names = self.fake.new_session_names()
        self.assertEqual(len(first_names), 1)
        self.assertIn(first_names[0], self.fake.alive)

        scheduler._fire_schedule(schedule)
        second_names = self.fake.new_session_names()
        self.assertEqual(len(second_names), 2)
        self.assertNotIn(first_names[0], self.fake.alive)  # old one killed
        self.assertIn(second_names[1], self.fake.alive)

    def test_fires_normally_when_nothing_is_tracked_yet(self):
        scheduler._fire_schedule(self._schedule())
        self.assertEqual(len(self.fake.new_session_names()), 1)
        self.assertIn(("s1", "ok"), [(h[0], h[1]) for h in self.history])


class FireScheduleClaimRaceTest(unittest.TestCase):
    """Task 9-10 review finding: the concurrency check-and-register must be
    atomic and synchronous, not racing a background setup thread."""

    def _schedule(self, **overrides):
        s = {"id": "s1", "name": "task", "cron": "0 9 * * *", "workdir": "/tmp", "prompt": "hi"}
        s.update(overrides)
        return s

    def test_concurrent_fire_calls_are_serialized_by_the_claim(self):
        fake = FakeRun()
        saved = _patch_scheduler(fake)
        history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: history.append((sid, status, msg))
        # Use a real background thread (not ImmediateThread) so the second
        # _fire_schedule call genuinely happens while the first is still
        # inside its background setup step.
        scheduler.threading.Thread = threading.Thread
        entered_setup = threading.Event()
        release = threading.Event()

        def blocking_setup(session_name, name, mode):
            entered_setup.set()
            release.wait(timeout=2)

        scheduler.setup_session = blocking_setup
        try:
            schedule = self._schedule()
            scheduler._fire_schedule(schedule)
            self.assertTrue(entered_setup.wait(timeout=2), "background setup never started")

            # Second fire arrives while the first session's setup is still
            # in flight (i.e. before it has finished and would otherwise
            # register itself). The synchronous claim made at the top of
            # _fire_schedule must already be in place.
            scheduler._fire_schedule(schedule)

            release.set()
            time.sleep(0.2)  # let the first background thread finish

            self.assertEqual(len(fake.new_session_names()), 1)
            self.assertIn(("s1", "skipped"), [(h[0], h[1]) for h in history])
        finally:
            _restore_scheduler(saved)

    def test_failed_tmux_launch_releases_the_claim(self):
        fake = FakeRun()
        saved = _patch_scheduler(fake)
        history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: history.append((sid, status, msg))

        def failing_run(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd[:2] == ["tmux", "new-session"]:
                class R:
                    returncode = 1
                    stdout = ""
                    stderr = "duplicate session"
                return R
            return fake(cmd, *a, **kw)

        scheduler.subprocess.run = failing_run
        try:
            schedule = self._schedule()
            scheduler._fire_schedule(schedule)
            self.assertNotIn("s1", scheduler._active_scheduled_sessions)
            self.assertIn(("s1", "error"), [(h[0], h[1]) for h in history])

            # A failed launch must not permanently block later fires: a
            # second attempt should try again (not be skipped as "already
            # running").
            scheduler._fire_schedule(schedule)
            self.assertEqual(
                [(h[0], h[1]) for h in history if h[0] == "s1" and h[1] == "error"],
                [("s1", "error"), ("s1", "error")],
            )
            self.assertNotIn(("s1", "skipped"), [(h[0], h[1]) for h in history])
        finally:
            _restore_scheduler(saved)


class SessionCapTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))
        self._orig_max = scheduler.RC_MAX_SESSIONS

    def tearDown(self):
        scheduler.RC_MAX_SESSIONS = self._orig_max
        _restore_scheduler(self._saved)

    def test_skips_firing_when_at_session_cap(self):
        scheduler.RC_MAX_SESSIONS = 2
        scheduler.list_rc_sessions = lambda: [{"name": "rc-a"}, {"name": "rc-b"}]
        scheduler._fire_schedule({"id": "s1", "name": "task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        self.assertEqual(self.fake.new_session_names(), [])
        self.assertEqual(len(self.history), 1)
        self.assertEqual(self.history[0][1], "skipped")

    def test_zero_disables_the_cap(self):
        scheduler.RC_MAX_SESSIONS = 0
        scheduler.list_rc_sessions = lambda: [{"name": "rc-a"}, {"name": "rc-b"}]
        scheduler._fire_schedule({"id": "s1", "name": "task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        self.assertEqual(len(self.fake.new_session_names()), 1)
        self.assertFalse(any(h[1] == "skipped" for h in self.history))

    def test_negative_disables_the_cap(self):
        scheduler.RC_MAX_SESSIONS = -1
        scheduler.list_rc_sessions = lambda: [{"name": "rc-a"}, {"name": "rc-b"}]
        scheduler._fire_schedule({"id": "s1", "name": "task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        self.assertEqual(len(self.fake.new_session_names()), 1)
        self.assertFalse(any(h[1] == "skipped" for h in self.history))


class AdoptLiveSessionsTest(unittest.TestCase):
    """Task: after a launcher restart, a still-running rc-run-* or legacy
    rc-sched-<name>-* tmux session must be adopted back into
    _active_scheduled_sessions so concurrency=skip/kill and history
    tracking still work, instead of the scheduler thinking it's gone and
    starting a duplicate."""

    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def test_adopts_rc_run_session_via_env_var(self):
        self.fake.alive.add("rc-run-abcdef012345")
        self.fake.env["rc-run-abcdef012345"] = {"RC_SCHEDULE_ID": "s1"}
        schedules = [{"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                      "workdir": "/tmp", "prompt": "hi"}]

        scheduler._adopt_live_sessions(schedules)

        self.assertIn("s1", scheduler._active_scheduled_sessions)
        self.assertEqual(
            scheduler._active_scheduled_sessions["s1"]["session_name"],
            "rc-run-abcdef012345",
        )

    def test_adopts_legacy_rc_sched_session_by_name(self):
        self.fake.alive.add("rc-sched-My-Task-20260101")
        schedules = [{"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                      "workdir": "/tmp", "prompt": "hi"}]

        scheduler._adopt_live_sessions(schedules)

        self.assertIn("s1", scheduler._active_scheduled_sessions)
        self.assertEqual(
            scheduler._active_scheduled_sessions["s1"]["session_name"],
            "rc-sched-My-Task-20260101",
        )

    def test_fire_schedule_skips_after_adoption(self):
        self.fake.alive.add("rc-run-abcdef012345")
        self.fake.env["rc-run-abcdef012345"] = {"RC_SCHEDULE_ID": "s1"}
        schedule = {"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                    "workdir": "/tmp", "prompt": "hi"}

        scheduler._adopt_live_sessions([schedule])
        scheduler._fire_schedule(schedule)

        self.assertEqual(self.fake.new_session_names(), [])
        self.assertIn(("s1", "skipped"), [(h[0], h[1]) for h in self.history])


if __name__ == "__main__":
    unittest.main()
