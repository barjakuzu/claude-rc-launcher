"""Tests for scheduler.py."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compat
import scheduler
import schedules as schedules_module

# scheduler.py logs progress via print() (session adoption, firing,
# skip/kill decisions) — real signal for an operator watching the
# launcher's stdout, but noise in a test run. Silence it for just this
# module's tests so the suite's final tail stays legible.
_stdout_guard = contextlib.redirect_stdout(io.StringIO())


def setUpModule():
    _stdout_guard.__enter__()


def tearDownModule():
    _stdout_guard.__exit__(None, None, None)

# _fire_schedule now routes through sessions.build_tmux_command, which calls
# compat.get_caps() - pin it deterministically so scheduler tests don't
# depend on whatever claude binary happens to be installed on the machine
# running the suite. Default to legacy (matches these tests' pre-native
# expectations); FireScheduleNativeFlagsTest overrides per-case.
_LEGACY_CAPS = {
    "session_id_flag": False, "name_flag": False,
    "remote_control_flag": False, "permission_mode_flag": False,
    "agents_json": False, "version": None,
}
_NATIVE_CAPS = {
    "session_id_flag": True, "name_flag": True,
    "remote_control_flag": True, "permission_mode_flag": True,
    "agents_json": True, "version": "2.1.263",
}


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

    def test_validate_cron_empty_string_gets_pick_a_preset_message(self):
        # An empty string is what the UI sends when the preset is left at
        # its blank default (as opposed to explicit None for Manual).
        self.assertEqual(scheduler.validate_cron(""),
                          "Pick a schedule preset, enter a 5-field cron, or choose Manual")
        self.assertEqual(scheduler.validate_cron("   "),
                          "Pick a schedule preset, enter a 5-field cron, or choose Manual")

    def test_validate_cron_wrong_nonzero_field_count_keeps_generic_message(self):
        self.assertEqual(scheduler.validate_cron("0 9 * *"), "Expected 5 fields, got 4")


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
        "get_caps": compat.get_caps,
    }
    scheduler.subprocess.run = fake
    scheduler.time.sleep = lambda *_: None
    scheduler.session_exists = lambda n: n in fake.alive
    scheduler.setup_session = lambda *a, **kw: None
    scheduler.threading.Thread = ImmediateThread
    scheduler.list_rc_sessions = lambda: [{"name": n} for n in fake.alive]
    compat.get_caps = lambda: dict(_LEGACY_CAPS)
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
    compat.get_caps = saved["get_caps"]
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


class FireScheduleNativeFlagsTest(unittest.TestCase):
    """_fire_schedule is routed through sessions.build_tmux_command
    (shared with /start, restart_session, resume_session), so a scheduled
    run gets a pre-assigned UUID and native identity flags when the
    installed claude supports them, and keeps RC_SCHEDULE_ID either way."""

    def setUp(self):
        self.fake = FakeRun()
        self._saved = _patch_scheduler(self.fake)
        self.history = []
        scheduler.add_history_entry = lambda sid, status, msg, **kw: self.history.append((sid, status, msg))

    def tearDown(self):
        _restore_scheduler(self._saved)

    def test_rc_schedule_id_env_is_set_regardless_of_caps(self):
        scheduler._fire_schedule({"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        name = self.fake.new_session_names()[0]
        self.assertEqual(self.fake.env[name].get("RC_SCHEDULE_ID"), "s1")

    def test_native_caps_use_title_as_name_and_remote_control(self):
        compat.get_caps = lambda: dict(_NATIVE_CAPS)
        scheduler._fire_schedule({"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        name = self.fake.new_session_names()[0]
        new_session_call = next(
            c for c in self.fake.calls
            if isinstance(c, list) and c[:2] == ["tmux", "new-session"] and c[c.index("-s") + 1] == name
        )
        joined = " ".join(new_session_call)
        self.assertIn("--name My-Task", joined)
        self.assertIn("--remote-control My-Task", joined)
        self.assertIn("--session-id", joined)
        self.assertEqual(self.fake.env[name].get("RC_TITLE"), "My-Task")
        self.assertIn("RC_SESSION_ID", self.fake.env[name])

    def test_legacy_caps_fall_back_to_rc_flags(self):
        compat.get_caps = lambda: dict(_LEGACY_CAPS)
        scheduler._fire_schedule({"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                                   "workdir": "/tmp", "prompt": "hi"})
        name = self.fake.new_session_names()[0]
        new_session_call = next(
            c for c in self.fake.calls
            if isinstance(c, list) and c[:2] == ["tmux", "new-session"] and c[c.index("-s") + 1] == name
        )
        joined = " ".join(new_session_call)
        self.assertNotIn("--name", joined)
        self.assertNotIn("--remote-control", joined)
        self.assertNotIn("RC_SESSION_ID", self.fake.env[name])
        self.assertIn("--dangerously-skip-permissions", joined)


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

    def test_prefers_longest_matching_safe_name(self):
        # Two schedules whose sanitized names are prefixes of one another.
        # A legacy tmux session "rc-sched-deploy-prod-0906-1200" must adopt
        # into "deploy-prod", not "deploy" (the shorter, earlier-seen match).
        self.fake.alive.add("rc-sched-deploy-prod-0906-1200")
        schedules = [
            {"id": "short-id", "name": "deploy", "cron": "0 9 * * *",
             "workdir": "/tmp", "prompt": "hi"},
            {"id": "long-id", "name": "deploy-prod", "cron": "0 9 * * *",
             "workdir": "/tmp", "prompt": "hi"},
        ]

        adopted = scheduler._adopt_live_sessions(schedules)

        self.assertEqual(adopted, 1)
        self.assertIn("long-id", scheduler._active_scheduled_sessions)
        self.assertNotIn("short-id", scheduler._active_scheduled_sessions)

    def test_fire_schedule_skips_after_adoption(self):
        self.fake.alive.add("rc-run-abcdef012345")
        self.fake.env["rc-run-abcdef012345"] = {"RC_SCHEDULE_ID": "s1"}
        schedule = {"id": "s1", "name": "My Task", "cron": "0 9 * * *",
                    "workdir": "/tmp", "prompt": "hi"}

        scheduler._adopt_live_sessions([schedule])
        scheduler._fire_schedule(schedule)

        self.assertEqual(self.fake.new_session_names(), [])
        self.assertIn(("s1", "skipped"), [(h[0], h[1]) for h in self.history])


class LimitResetDecisionTest(unittest.TestCase):
    """_limit_reset_decision is the pure core of the task-l3 firing rule:
    given a trigger's stored marker, the window's current resets_at, and
    the wall clock, decide skip / seed / fire. One test per case the
    brief calls out explicitly, plus the mechanics (delay window, unknown
    timestamps) those cases rest on."""

    T1 = "2026-01-01T00:00:00Z"   # an earlier reset boundary
    T2 = "2026-01-02T00:00:00Z"   # a later one
    T3 = "2026-01-03T00:00:00Z"   # later still

    def _epoch(self, iso):
        return schedules_module.iso_to_epoch(iso)

    def test_hub_down_across_one_reset_fires_once(self):
        # Marker still points at T1 (last thing the hub ever recorded).
        # The API now reports T2, and T1 is safely in the past - exactly
        # what "the hub was down across a reset" looks like on restart.
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0}
        now = self._epoch(self.T2) + 3600  # well after the API's own update
        action, marker = scheduler._limit_reset_decision(trigger, self.T2, now)
        self.assertEqual(action, "fire")
        self.assertEqual(marker, self.T2)

    def test_hub_down_across_two_resets_fires_once_for_latest(self):
        # The hub never observed T2 at all - only T1 (stored) and T3
        # (the API's current answer) exist as far as this function knows.
        # It must still fire exactly once, for T3.
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0}
        now = self._epoch(self.T3) + 3600
        action, marker = scheduler._limit_reset_decision(trigger, self.T3, now)
        self.assertEqual(action, "fire")
        self.assertEqual(marker, self.T3)

    def test_limits_unavailable_fires_nothing_and_preserves_marker(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0}
        action, marker = scheduler._limit_reset_decision(trigger, None, self._epoch(self.T2))
        self.assertEqual(action, "skip")
        self.assertIsNone(marker)

    def test_same_resets_at_for_hours_fires_nothing(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0}
        action, marker = scheduler._limit_reset_decision(
            trigger, self.T1, self._epoch(self.T1) + 3600 * 4)
        self.assertEqual(action, "skip")

    def test_brand_new_task_seeds_without_firing(self):
        trigger = {"last_seen_resets_at": None, "delay_minutes": 0}
        action, marker = scheduler._limit_reset_decision(
            trigger, self.T1, self._epoch(self.T1))
        self.assertEqual(action, "seed")
        self.assertEqual(marker, self.T1)

    def test_clock_skew_backwards_is_ignored(self):
        # Stored marker is T2 (later); the API now reports the earlier T1.
        trigger = {"last_seen_resets_at": self.T2, "delay_minutes": 0}
        action, marker = scheduler._limit_reset_decision(
            trigger, self.T1, self._epoch(self.T2) + 3600)
        self.assertEqual(action, "skip")
        self.assertIsNone(marker)

    def test_stored_marker_not_yet_in_the_past_does_not_fire(self):
        # current differs from stored, but wall clock hasn't even reached
        # the stored boundary yet - brief requires "the stored one is in
        # the past" as part of the firing condition.
        trigger = {"last_seen_resets_at": self.T2, "delay_minutes": 0}
        action, marker = scheduler._limit_reset_decision(
            trigger, self.T3, self._epoch(self.T2) - 3600)
        self.assertEqual(action, "skip")

    def test_delay_minutes_not_yet_elapsed_does_not_fire(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 30}
        # Only 10 minutes past the reset boundary; delay wants 30.
        now = self._epoch(self.T1) + 600
        action, marker = scheduler._limit_reset_decision(trigger, self.T2, now)
        self.assertEqual(action, "skip")
        self.assertIsNone(marker)

    def test_delay_minutes_elapsed_fires(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 30}
        now = self._epoch(self.T1) + 30 * 60 + 1
        action, marker = scheduler._limit_reset_decision(trigger, self.T2, now)
        self.assertEqual(action, "fire")
        self.assertEqual(marker, self.T2)

    def test_unparseable_current_resets_at_does_not_fire(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0}
        action, marker = scheduler._limit_reset_decision(
            trigger, "not-a-timestamp", self._epoch(self.T1) + 3600)
        self.assertEqual(action, "skip")


class ApplyLimitResetTriggerCatchUpTest(unittest.TestCase):
    """_apply_limit_reset_trigger layers trigger.catch_up on top of
    _limit_reset_decision - "none" suppresses exactly the first GENUINE
    fire decision each process ever reaches for a schedule, per the
    brief's "not at all if the task's catch_up is none" case.

    Fix round 1 (Critical 1, task-l3-findings-r1.md): the signature
    changed from a caller-supplied `first_check_this_process` bool to a
    `schedule_id` this function tracks itself, consumed ONLY at the
    moment a real "fire" decision is reached - see
    test_catch_up_none_not_consumed_by_a_skip_decision and
    test_catch_up_none_not_consumed_by_unavailable_limits below for the
    regression this closes."""

    T1 = "2026-01-01T00:00:00Z"
    T2 = "2026-01-02T00:00:00Z"

    def setUp(self):
        scheduler._limit_reset_seen_this_process.clear()

    def tearDown(self):
        scheduler._limit_reset_seen_this_process.clear()

    def _epoch(self, iso):
        return schedules_module.iso_to_epoch(iso)

    def test_catch_up_none_suppresses_the_first_genuine_fire(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0, "catch_up": "none"}
        now = self._epoch(self.T2) + 3600
        action, marker = scheduler._apply_limit_reset_trigger("s1", trigger, self.T2, now)
        self.assertEqual(action, "seed")
        self.assertEqual(marker, self.T2)

    def test_catch_up_none_fires_normally_once_already_fired(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0, "catch_up": "none"}
        now = self._epoch(self.T2) + 3600
        scheduler._apply_limit_reset_trigger("s1", trigger, self.T2, now)  # consumes the allowance
        action, marker = scheduler._apply_limit_reset_trigger("s1", trigger, self.T2, now)
        self.assertEqual(action, "fire")

    def test_catch_up_latest_is_the_default_and_fires_immediately(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0}
        now = self._epoch(self.T2) + 3600
        action, marker = scheduler._apply_limit_reset_trigger("s1", trigger, self.T2, now)
        self.assertEqual(action, "fire")

    def test_catch_up_none_does_not_affect_a_non_firing_decision(self):
        # catch_up only ever downgrades a "fire" - it must never turn a
        # "skip" (e.g. limits unavailable) into anything else.
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0, "catch_up": "none"}
        action, marker = scheduler._apply_limit_reset_trigger(
            "s1", trigger, None, self._epoch(self.T1) + 3600)
        self.assertEqual(action, "skip")

    def test_catch_up_none_not_consumed_by_a_skip_decision(self):
        # Critical 1 regression: a tick that resolves to "skip" because
        # current_resets_at happens to equal the stored marker (the
        # SQLite-cache staleness right after a restart, or simply "no
        # reset happened") must NOT spend the one-time catch_up: "none"
        # allowance - only a genuine "fire" decision may.
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0, "catch_up": "none"}
        action, _ = scheduler._apply_limit_reset_trigger(
            "s1", trigger, self.T1, self._epoch(self.T1) + 3600)  # current == stored
        self.assertEqual(action, "skip")
        self.assertNotIn("s1", scheduler._limit_reset_seen_this_process)
        # The REAL missed-reset value shows up next - still must be
        # suppressed, since this is the process's first genuine fire.
        action2, marker2 = scheduler._apply_limit_reset_trigger(
            "s1", trigger, self.T2, self._epoch(self.T2) + 3600)
        self.assertEqual(action2, "seed")
        self.assertEqual(marker2, self.T2)

    def test_catch_up_none_not_consumed_by_unavailable_limits(self):
        trigger = {"last_seen_resets_at": self.T1, "delay_minutes": 0, "catch_up": "none"}
        action, _ = scheduler._apply_limit_reset_trigger(
            "s1", trigger, None, self._epoch(self.T1) + 3600)
        self.assertEqual(action, "skip")
        self.assertNotIn("s1", scheduler._limit_reset_seen_this_process)
        action2, marker2 = scheduler._apply_limit_reset_trigger(
            "s1", trigger, self.T2, self._epoch(self.T2) + 3600)
        self.assertEqual(action2, "seed")


class CheckLimitResetSchedulesTest(unittest.TestCase):
    """_check_limit_reset_schedules: the per-tick wiring around the pure
    decision functions above - fetches limits once per tick, decides and
    persists per schedule under a claim lock, fires via _fire_schedule,
    and never lets a bad trigger escape as an exception.

    Fix round 1 (Critical 3, task-l3-findings-r1.md): this function now
    re-reads each schedule fresh from disk inside its claim, rather than
    trusting the `schedules_list` snapshot it was called with - so these
    tests drive it against a REAL schedules.json (via schedules_module,
    same as tests/test_schedules.py's own CRUD tests) instead of
    synthetic in-memory dicts and a mocked update_schedule. Only
    _fire_schedule and add_history_entry are mocked, since those are the
    two real-world side effects (launching a session, writing history)
    this module owns and the tests need to observe without either
    happening for real."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_sched_file = schedules_module.SCHEDULES_FILE
        schedules_module.SCHEDULES_FILE = os.path.join(self.tmpdir, "schedules.json")
        self.fired = []
        self.history = []
        self._saved_fire = scheduler._fire_schedule
        self._saved_history = scheduler.add_history_entry
        scheduler._fire_schedule = lambda sched: self.fired.append(sched["id"])
        scheduler.add_history_entry = (
            lambda sid, status, msg, **kw: self.history.append((sid, status, msg)))
        scheduler._limit_reset_seen_this_process.clear()

    def tearDown(self):
        scheduler._fire_schedule = self._saved_fire
        scheduler.add_history_entry = self._saved_history
        schedules_module.SCHEDULES_FILE = self._orig_sched_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        scheduler._limit_reset_seen_this_process.clear()

    def _create(self, **overrides):
        trigger = overrides.pop("trigger", {
            "kind": "limit_reset", "window": "five_hour", "delay_minutes": 0,
            "catch_up": "latest",
        })
        data = {"name": "task", "enabled": True, "trigger": trigger}
        data.update(overrides)
        return schedules_module.create_schedule(data)

    def _fresh(self, schedule_id):
        return schedules_module.get_schedule_by_id(schedule_id)[1]

    def _seed_marker(self, schedule_id, resets_at):
        # The scheduler's own internal marker write - includes
        # last_seen_resets_at explicitly, exactly like
        # _check_limit_reset_schedules itself does. This bypasses
        # validate_trigger on purpose: that gate belongs to the API
        # layer (server.py), not to update_schedule, which is what lets
        # the scheduler's internal bookkeeping calls work at all - see
        # tests/test_schedules.py's TriggerCrudTest for that boundary.
        trigger = dict(self._fresh(schedule_id)["trigger"])
        trigger["last_seen_resets_at"] = resets_at
        schedules_module.update_schedule(schedule_id, {"trigger": trigger})

    def _view(self, resets_at, window="five_hour"):
        return lambda: {"primary": {window: {"resets_at": resets_at}}}

    def test_fires_and_persists_marker(self):
        s = self._create()
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")
        now = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        scheduler._check_limit_reset_schedules(
            [s], now, self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [s["id"]])
        self.assertEqual(self._fresh(s["id"])["trigger"]["last_seen_resets_at"],
                          "2026-01-02T00:00:00Z")

    def test_brand_new_task_seeds_and_does_not_fire(self):
        s = self._create()
        now = schedules_module.iso_to_epoch("2026-01-01T00:00:00Z")
        scheduler._check_limit_reset_schedules(
            [s], now, self._view("2026-01-01T00:00:00Z"))
        self.assertEqual(self.fired, [])
        self.assertEqual(self._fresh(s["id"])["trigger"]["last_seen_resets_at"],
                          "2026-01-01T00:00:00Z")

    def test_limits_view_raising_skips_without_disabling(self):
        s = self._create()
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")

        def boom():
            raise RuntimeError("store unavailable")

        scheduler._check_limit_reset_schedules([s], time.time(), boom)
        self.assertEqual(self.fired, [])
        self.assertEqual(self.history, [])
        fresh = self._fresh(s["id"])
        self.assertTrue(fresh["enabled"])
        self.assertEqual(fresh["trigger"]["last_seen_resets_at"], "2026-01-01T00:00:00Z")

    def test_unknown_kind_disables_task_with_history_entry(self):
        s = self._create(trigger={"kind": "something_else", "window": "five_hour"})
        scheduler._check_limit_reset_schedules(
            [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])
        self.assertIn((s["id"], "error"), [(h[0], h[1]) for h in self.history])
        self.assertFalse(self._fresh(s["id"])["enabled"])

    def test_unknown_window_disables_task_with_history_entry(self):
        s = self._create(trigger={"kind": "limit_reset", "window": "bogus"})
        scheduler._check_limit_reset_schedules(
            [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])
        self.assertIn((s["id"], "error"), [(h[0], h[1]) for h in self.history])
        self.assertFalse(self._fresh(s["id"])["enabled"])

    def test_delay_minutes_out_of_range_disables_task(self):
        s = self._create(trigger={
            "kind": "limit_reset", "window": "five_hour", "delay_minutes": 9999,
        })
        scheduler._check_limit_reset_schedules(
            [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])
        self.assertFalse(self._fresh(s["id"])["enabled"])

    def test_non_dict_trigger_disables_task(self):
        # schedules._validate_schedules now sanitizes a non-dict trigger
        # to None at LOAD time (the Minor fix, task-l3-findings-r1.md),
        # so the real load_schedules()/get_schedule_by_id() path can no
        # longer hand this function a non-dict trigger at all - only
        # None (handled separately, see test_trigger_cleared_concurrently_
        # is_skipped_silently below) or a real dict ever reaches here
        # through real file I/O. This defensive branch is exercised
        # directly instead, by patching get_schedule_by_id to hand back
        # a schedule whose trigger is neither: it still must disable and
        # log rather than raise, for whatever future caller CAN produce
        # that shape (a different storage backend, a bug elsewhere).
        s = self._create(trigger={"kind": "limit_reset", "window": "five_hour"})
        saved = scheduler.get_schedule_by_id
        broken = dict(s)
        broken["trigger"] = "limit_reset"
        scheduler.get_schedule_by_id = lambda sid: (0, broken) if sid == s["id"] else (None, None)
        try:
            scheduler._check_limit_reset_schedules(
                [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        finally:
            scheduler.get_schedule_by_id = saved
        self.assertEqual(self.fired, [])
        self.assertFalse(self._fresh(s["id"])["enabled"])

    def test_trigger_cleared_concurrently_is_skipped_silently(self):
        # The candidate list is built from a possibly-stale snapshot; if
        # another edit cleared the trigger (or converted the task back
        # to cron/manual) between that snapshot and this function's own
        # fresh re-read, the fresh value is legitimately None - nothing
        # to do, and NOT the same as a malformed trigger (must not log
        # an error or disable the task for someone else's unrelated,
        # perfectly valid edit).
        s = self._create()
        schedules_module.update_schedule(s["id"], {"trigger": None, "cron": "0 9 * * *"})
        scheduler._check_limit_reset_schedules(
            [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])
        self.assertEqual(self.history, [])
        fresh = self._fresh(s["id"])
        self.assertTrue(fresh["enabled"])
        self.assertEqual(fresh["cron"], "0 9 * * *")

    def test_cron_only_schedule_is_ignored(self):
        s = self._create(trigger=None, cron="0 9 * * *")
        scheduler._check_limit_reset_schedules(
            [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])

    def test_no_limit_reset_schedules_never_calls_get_limits_view(self):
        calls = []
        s = self._create(trigger=None, cron="0 9 * * *")
        scheduler._check_limit_reset_schedules(
            [s], time.time(), lambda: calls.append(1))
        self.assertEqual(calls, [])

    def test_deleted_schedule_is_skipped_without_error(self):
        s = self._create()
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")
        schedules_module.delete_schedule(s["id"])
        now = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        # Should not raise even though the snapshot in `[s]` is stale.
        scheduler._check_limit_reset_schedules(
            [s], now, self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])

    # --- Critical 2 regressions: an administrative action (disabling,
    # re-enabling) must never itself cause a fire. ---

    def test_disabled_schedule_marker_still_advances_but_never_fires(self):
        s = self._create(enabled=False)
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")
        now = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        scheduler._check_limit_reset_schedules(
            [s], now, self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])
        self.assertEqual(self._fresh(s["id"])["trigger"]["last_seen_resets_at"],
                          "2026-01-02T00:00:00Z")

    def test_reenabling_after_marker_kept_current_does_not_fire(self):
        s = self._create(enabled=False)
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")
        now1 = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        # While disabled, a reset happens - the marker must advance
        # silently (previous test), never fire.
        scheduler._check_limit_reset_schedules(
            [s], now1, self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])
        # Re-enabling is a purely administrative action. schedules.py's
        # own reseed-on-enable (fix round 2) clears the marker on this
        # transition regardless of whether a tick already kept it
        # current - either way, the very next tick must not fire just
        # because the task was switched back on.
        schedules_module.update_schedule(s["id"], {"enabled": True})
        fresh = self._fresh(s["id"])
        now2 = now1 + 60
        scheduler._check_limit_reset_schedules(
            [fresh], now2, self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])

    def test_disabled_malformed_trigger_is_not_repeatedly_relogged(self):
        s = self._create(trigger={"kind": "limit_reset", "window": "bogus"})
        # First tick: enabled, malformed - disables and logs once.
        scheduler._check_limit_reset_schedules(
            [s], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(len(self.history), 1)
        self.assertFalse(self._fresh(s["id"])["enabled"])
        # Second tick against the now-disabled, still-malformed
        # schedule must not add a second history entry or write.
        fresh = self._fresh(s["id"])
        scheduler._check_limit_reset_schedules(
            [fresh], time.time(), self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(len(self.history), 1)

    # --- Critical 2, fix round 2 (task-l3-findings-r2.md): the marker is
    # only ever kept current by ticks, and enabling never itself reseeded
    # it - reachable when NO tick had a chance to run between the reset
    # and the re-enable. schedules.update_schedule's reseed-on-enable
    # closes this by clearing the marker synchronously on the
    # disabled -> enabled transition, no tick required. ---

    def test_reenable_before_first_tick_after_a_restart_spanning_reset(self):
        # A hub restart across a reset, re-enabled inside the sub-60s
        # window before the scheduler's first tick even runs. No tick
        # exists in this test at all before the re-enable - proving the
        # fix does not depend on one having run.
        s = self._create(enabled=False)
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")  # stale: pre-restart value
        schedules_module.update_schedule(s["id"], {"enabled": True})  # re-enable, no tick yet
        fresh = self._fresh(s["id"])
        self.assertIsNone(fresh["trigger"]["last_seen_resets_at"])
        now = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        scheduler._check_limit_reset_schedules(
            [fresh], now, self._view("2026-01-02T00:00:00Z"))
        self.assertEqual(self.fired, [])

    def test_reenable_while_limits_were_unavailable_across_a_reset(self):
        # Disabled while limits are unavailable across a reset (ticks
        # DO run here, unlike the restart case, but each one sees no
        # usable resets_at, so the marker never moves), re-enabled
        # before limits recover.
        s = self._create(enabled=False)
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")
        now1 = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        scheduler._check_limit_reset_schedules([s], now1, self._view(None))
        self.assertEqual(self._fresh(s["id"])["trigger"]["last_seen_resets_at"],
                          "2026-01-01T00:00:00Z")  # untouched - limits were unavailable
        schedules_module.update_schedule(s["id"], {"enabled": True})  # re-enable before recovery
        fresh = self._fresh(s["id"])
        self.assertIsNone(fresh["trigger"]["last_seen_resets_at"])
        now2 = now1 + 60
        scheduler._check_limit_reset_schedules(
            [fresh], now2, self._view("2026-01-02T00:00:00Z"))  # limits recover here
        self.assertEqual(self.fired, [])

    # --- Critical 3 regression: concurrent evaluation of the SAME
    # schedule must claim the decision exactly once. ---

    def test_concurrent_ticks_fire_exactly_once(self):
        s = self._create()
        self._seed_marker(s["id"], "2026-01-01T00:00:00Z")
        now = schedules_module.iso_to_epoch("2026-01-02T00:00:00Z") + 3600
        view = self._view("2026-01-02T00:00:00Z")
        threads = [threading.Thread(target=scheduler._check_limit_reset_schedules,
                                     args=([s], now, view))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.fired), 1)
        self.assertEqual(self._fresh(s["id"])["trigger"]["last_seen_resets_at"],
                          "2026-01-02T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
