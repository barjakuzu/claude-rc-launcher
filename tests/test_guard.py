"""Tests for guard.py: the runaway/stalled-session rule engine.

Fixtures use neutral device names (dev-a, dev-b) and synthetic epoch
timestamps - CI greps tracked files for personal identifiers, so no real
hostnames, usernames or IP-like strings appear anywhere here.
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guard

NOW = 1_700_000_000.0


def now_fn():
    return NOW


def session(**kw):
    base = {
        "device_id": "dev-a",
        "session_id": "sess-1",
        "name": "rc-foo",
        "cwd": "/some/path",
        "kind": "launcher",
        "state": "busy",
        "started_at": NOW - 3600,
        "ended_at": None,
        "last_seen": NOW,
        "external": 0,
        "usage": None,
        "last_event_ts": NOW,
    }
    base.update(kw)
    return base


def device(**kw):
    base = {
        "id": "dev-a",
        "name": "dev-a",
        "role": "full",
        "version": "2.1.13",
        "claude_version": "2.1.263",
        "last_seen": NOW,
        "online": 1,
    }
    base.update(kw)
    return base


def snap(sessions=None, devices=None):
    return {"sessions": sessions or [], "devices": devices or []}


class FormatHelpersTest(unittest.TestCase):
    def test_fmt_count_millions(self):
        self.assertEqual(guard._fmt_count(5_400_000), "5.4 M")
        self.assertEqual(guard._fmt_count(43_400_000), "43.4 M")

    def test_fmt_count_below_thousand_is_plain_int(self):
        self.assertEqual(guard._fmt_count(999), "999")
        self.assertEqual(guard._fmt_count(0), "0")

    def test_fmt_count_thousands(self):
        self.assertEqual(guard._fmt_count(1500), "1.5 k")

    def test_fmt_count_billions(self):
        self.assertEqual(guard._fmt_count(2_500_000_000), "2.5 B")

    def test_fmt_count_non_numeric_does_not_raise(self):
        self.assertEqual(guard._fmt_count("nope"), "nope")

    def test_fmt_hours(self):
        self.assertEqual(guard._fmt_hours(6.1), "6.1 h")
        self.assertEqual(guard._fmt_hours(24), "24.0 h")

    def test_fmt_hours_non_numeric_does_not_raise(self):
        self.assertEqual(guard._fmt_hours(None), "None")


class SessionAgeRuleTest(unittest.TestCase):
    def _rules(self, max_age_hours=2):
        r = guard.default_rules()
        r["rules"]["session_age"]["max_age_hours"] = max_age_hours
        return r

    def test_exactly_at_threshold_does_not_fire(self):
        s = session(started_at=NOW - 2 * 3600)
        findings = guard.evaluate(snap([s]), rules=self._rules(2), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_one_second_over_fires(self):
        s = session(started_at=NOW - (2 * 3600 + 1))
        findings = guard.evaluate(snap([s]), rules=self._rules(2), now_fn=now_fn)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["rule"], "session_age")
        self.assertEqual(f["severity"], "warn")
        self.assertEqual(f["target_type"], "session")
        self.assertEqual(f["threshold"], 2)
        self.assertGreater(f["value"], 2)
        self.assertEqual(f["since"], NOW - (2 * 3600 + 1))
        self.assertNotIn("/some/path", f["message"])

    def test_missing_started_at_disables_only_session_age(self):
        s = session(started_at=None, usage={"effective": 60_000_000, "last_ts": NOW})
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        rules_fired = {f["rule"] for f in findings}
        self.assertNotIn("session_age", rules_fired)
        self.assertIn("token_total", rules_fired)

    def test_ended_session_never_fires(self):
        s = session(started_at=NOW - 1000 * 3600, ended_at=NOW - 10)
        findings = guard.evaluate(snap([s]), rules=self._rules(2), now_fn=now_fn)
        self.assertEqual(findings, [])


class TokenRateRuleTest(unittest.TestCase):
    def _rules(self, max_rate=1000, min_age=1):
        r = guard.default_rules()
        r["rules"]["token_rate"]["max_effective_per_hour"] = max_rate
        r["rules"]["token_rate"]["min_age_hours_for_rate"] = min_age
        return r

    def test_exactly_at_threshold_does_not_fire(self):
        s = session(started_at=NOW - 2 * 3600, usage={"effective": 2000, "last_ts": NOW})
        findings = guard.evaluate(snap([s]), rules=self._rules(1000, 1), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_one_over_threshold_fires(self):
        s = session(started_at=NOW - 2 * 3600, usage={"effective": 2001, "last_ts": NOW})
        findings = guard.evaluate(snap([s]), rules=self._rules(1000, 1), now_fn=now_fn)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["rule"], "token_rate")
        self.assertEqual(f["severity"], "alert")
        self.assertIn("effective tokens/hour", f["message"])

    def test_missing_usage_does_not_fire(self):
        s = session(started_at=NOW - 2 * 3600, usage=None)
        findings = guard.evaluate(snap([s]), rules=self._rules(1000, 1), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_min_age_floor_prevents_fake_extrapolation(self):
        # A session two minutes old that used 200k tokens must not
        # extrapolate to a fake 6 M/h against the default 5 M/h threshold.
        s = session(started_at=NOW - 120, usage={"effective": 200_000, "last_ts": NOW})
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(findings, [])


class TokenTotalRuleTest(unittest.TestCase):
    def _rules(self):
        # Isolate token_total: at these effective-token levels the
        # session's default 1h age would also cross the token_rate
        # threshold, which these tests aren't about.
        r = guard.default_rules()
        r["rules"]["token_rate"]["enabled"] = False
        return r

    def test_exactly_at_threshold_does_not_fire(self):
        s = session(usage={"effective": 50_000_000, "last_ts": NOW})
        findings = guard.evaluate(snap([s]), rules=self._rules(), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_one_over_threshold_fires(self):
        s = session(usage={"effective": 50_000_001, "last_ts": NOW})
        findings = guard.evaluate(snap([s]), rules=self._rules(), now_fn=now_fn)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["rule"], "token_total")
        self.assertEqual(f["severity"], "alert")

    def test_missing_usage_disables_only_cost_rules(self):
        # session_age and stalled must still evaluate normally.
        s = session(
            started_at=NOW - 1000 * 3600,
            state="busy",
            last_event_ts=NOW - 3000,
            last_seen=NOW - 3000,
            usage=None,
        )
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        rules_fired = {f["rule"] for f in findings}
        self.assertNotIn("token_rate", rules_fired)
        self.assertNotIn("token_total", rules_fired)
        self.assertIn("session_age", rules_fired)
        self.assertIn("stalled", rules_fired)


class StalledRuleTest(unittest.TestCase):
    def test_exactly_at_threshold_does_not_fire(self):
        s = session(state="busy", last_event_ts=NOW - 30 * 60, last_seen=NOW - 30 * 60, usage=None)
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_one_minute_over_fires(self):
        stale = NOW - 31 * 60
        s = session(state="busy", last_event_ts=stale, last_seen=stale, usage=None)
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["rule"], "stalled")
        self.assertEqual(f["severity"], "warn")
        self.assertEqual(f["since"], stale)

    def test_non_busy_state_never_stalls(self):
        stale = NOW - 10 * 3600
        for state in ("idle", "starting", "needs_attention", "ended", None):
            s = session(state=state, last_event_ts=stale, last_seen=stale, usage=None)
            findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
            self.assertEqual(findings, [], f"state={state!r} must not stall")

    def test_missing_all_activity_timestamps_does_not_fire(self):
        s = session(state="busy", last_event_ts=None, last_seen=None, usage=None)
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_uses_most_recent_of_the_three_timestamps(self):
        # last_event_ts is stale but usage.last_ts is fresh -> not stalled.
        s = session(
            state="busy",
            last_event_ts=NOW - 3600,
            last_seen=NOW - 3600,
            usage={"effective": 1, "last_ts": NOW - 5},
        )
        findings = guard.evaluate(snap([s]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(findings, [])


class DeviceConcurrencyRuleTest(unittest.TestCase):
    def _rules(self, max_n=3):
        r = guard.default_rules()
        r["rules"]["device_concurrency"]["max_sessions_per_device"] = max_n
        return r

    def _sessions(self, n, ended=0):
        out = []
        for i in range(n):
            out.append(session(session_id=f"s-{i}", started_at=NOW - (i + 1) * 3600))
        for i in range(ended):
            out.append(session(session_id=f"e-{i}", ended_at=NOW - 10))
        return out

    def test_exactly_at_threshold_does_not_fire(self):
        findings = guard.evaluate(snap(self._sessions(3), [device()]), rules=self._rules(3), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_one_over_threshold_fires_once_per_device(self):
        findings = guard.evaluate(snap(self._sessions(4), [device()]), rules=self._rules(3), now_fn=now_fn)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["rule"], "device_concurrency")
        self.assertEqual(f["target_type"], "device")
        self.assertEqual(f["device_id"], "dev-a")
        self.assertIsNone(f["session_id"])
        self.assertEqual(f["value"], 4)
        self.assertEqual(f["threshold"], 3)

    def test_ended_sessions_do_not_count(self):
        findings = guard.evaluate(
            snap(self._sessions(3, ended=5), [device()]), rules=self._rules(3), now_fn=now_fn)
        self.assertEqual(findings, [])


class DeviceOfflineRuleTest(unittest.TestCase):
    def _rules(self, offline_minutes=10):
        r = guard.default_rules()
        r["rules"]["device_offline"]["enabled"] = True
        r["rules"]["device_offline"]["offline_minutes"] = offline_minutes
        return r

    def test_disabled_by_default(self):
        d = device(online=0, last_seen=NOW - 100 * 3600)
        findings = guard.evaluate(snap([], [d]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_exactly_at_threshold_does_not_fire(self):
        d = device(online=1, last_seen=NOW - 10 * 60)
        findings = guard.evaluate(snap([], [d]), rules=self._rules(10), now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_one_minute_over_fires(self):
        d = device(online=1, last_seen=NOW - 11 * 60)
        findings = guard.evaluate(snap([], [d]), rules=self._rules(10), now_fn=now_fn)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["rule"], "device_offline")
        self.assertEqual(f["target_type"], "device")

    def test_online_flag_falsy_fires_even_if_recently_seen(self):
        d = device(online=0, last_seen=NOW - 5)
        findings = guard.evaluate(snap([], [d]), rules=self._rules(10), now_fn=now_fn)
        self.assertEqual(len(findings), 1)

    def test_missing_online_and_last_seen_does_not_fire(self):
        d = device(online=None, last_seen=None)
        findings = guard.evaluate(snap([], [d]), rules=self._rules(10), now_fn=now_fn)
        self.assertEqual(findings, [])


class IgnoreListsTest(unittest.TestCase):
    def setUp(self):
        self.rules = guard.default_rules()
        self.rules["rules"]["session_age"]["max_age_hours"] = 1

    def _session(self):
        return session(
            device_id="dev-a", session_id="sess-ignore-me", name="rc-longrunner",
            started_at=NOW - 2 * 3600)

    def test_no_ignore_fires_normally(self):
        findings = guard.evaluate(snap([self._session()]), rules=self.rules, now_fn=now_fn)
        self.assertEqual(len(findings), 1)

    def test_ignore_by_device(self):
        self.rules["ignore"]["devices"] = ["dev-a"]
        findings = guard.evaluate(snap([self._session()]), rules=self.rules, now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_ignore_by_session_id(self):
        self.rules["ignore"]["sessions"] = ["sess-ignore-me"]
        findings = guard.evaluate(snap([self._session()]), rules=self.rules, now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_ignore_by_name(self):
        self.rules["ignore"]["names"] = ["rc-longrunner"]
        findings = guard.evaluate(snap([self._session()]), rules=self.rules, now_fn=now_fn)
        self.assertEqual(findings, [])

    def test_ignore_matching_is_exact_and_case_sensitive(self):
        self.rules["ignore"]["names"] = ["RC-LONGRUNNER"]
        findings = guard.evaluate(snap([self._session()]), rules=self.rules, now_fn=now_fn)
        self.assertEqual(len(findings), 1)

    def test_ignore_by_device_suppresses_device_target_findings(self):
        rules = guard.default_rules()
        rules["rules"]["device_concurrency"]["max_sessions_per_device"] = 0
        rules["ignore"]["devices"] = ["dev-a"]
        findings = guard.evaluate(snap([self._session()], [device()]), rules=rules, now_fn=now_fn)
        self.assertEqual(findings, [])


class RuleErrorContainmentTest(unittest.TestCase):
    def test_raising_rule_is_contained_and_recorded(self):
        rules = guard.default_rules()
        # A caller-constructed rules dict can carry a bad type that
        # load_rules()/_merge_rules would have rejected; evaluate() must
        # not trust it blindly, and must not let it take down the run.
        rules["rules"]["session_age"]["max_age_hours"] = "not-a-number-/secret/path"
        s1 = session(session_id="s1", started_at=NOW - 3600)
        s2 = session(session_id="s2", usage={"effective": 90_000_000, "last_ts": NOW})
        findings = guard.evaluate(snap([s1, s2]), rules=rules, now_fn=now_fn)

        fired_rules = {f["rule"] for f in findings}
        self.assertNotIn("session_age", fired_rules)
        self.assertIn("token_total", fired_rules)

        self.assertEqual(len(guard.LAST_RULE_ERRORS), 2)
        for rule_name, exc_type in guard.LAST_RULE_ERRORS:
            self.assertEqual(rule_name, "session_age")
            self.assertEqual(exc_type, "TypeError")
        # The exception message (which could embed a path) must never
        # leak into the recorded errors.
        self.assertNotIn("secret", str(guard.LAST_RULE_ERRORS))

    def test_clean_run_leaves_last_rule_errors_empty(self):
        guard.evaluate(snap([session()]), rules=guard.default_rules(), now_fn=now_fn)
        self.assertEqual(guard.LAST_RULE_ERRORS, [])


class OrderingTest(unittest.TestCase):
    def test_alerts_before_warns(self):
        rules = guard.default_rules()
        rules["rules"]["session_age"]["max_age_hours"] = 1
        rules["rules"]["token_rate"]["enabled"] = False  # isolate token_total
        warn_s = session(session_id="warn-1", started_at=NOW - 2 * 3600, usage=None)
        alert_s = session(
            session_id="alert-1", state="idle", started_at=NOW - 1800,
            usage={"effective": 60_000_000, "last_ts": NOW})
        findings = guard.evaluate(snap([warn_s, alert_s]), rules=rules, now_fn=now_fn)
        severities = [f["severity"] for f in findings]
        self.assertEqual(severities, ["alert", "warn"])

    def test_oldest_first_within_severity(self):
        rules = guard.default_rules()
        rules["rules"]["session_age"]["max_age_hours"] = 1
        older = session(session_id="older", started_at=NOW - 10 * 3600)
        newer = session(session_id="newer", started_at=NOW - 5 * 3600)
        # Insertion order deliberately reversed vs. expected output order.
        findings = guard.evaluate(snap([newer, older]), rules=rules, now_fn=now_fn)
        session_ids = [f["session_id"] for f in findings]
        self.assertEqual(session_ids, ["older", "newer"])

    def test_stable_within_same_severity_and_since(self):
        rules = guard.default_rules()
        rules["rules"]["session_age"]["max_age_hours"] = 1
        same_start = NOW - 10 * 3600
        first = session(session_id="first", started_at=same_start)
        second = session(session_id="second", started_at=same_start)
        findings = guard.evaluate(snap([first, second]), rules=rules, now_fn=now_fn)
        session_ids = [f["session_id"] for f in findings]
        self.assertEqual(session_ids, ["first", "second"])


class SummarizeTest(unittest.TestCase):
    def test_counts(self):
        findings = [
            {"rule": "token_rate", "severity": "alert"},
            {"rule": "token_total", "severity": "alert"},
            {"rule": "session_age", "severity": "warn"},
            {"rule": "session_age", "severity": "warn"},
            {"rule": "stalled", "severity": "warn"},
        ]
        summary = guard.summarize(findings)
        self.assertEqual(summary, {
            "alert": 2, "warn": 3,
            "rules": {"token_rate": 1, "token_total": 1, "session_age": 2, "stalled": 1},
        })

    def test_empty(self):
        self.assertEqual(guard.summarize([]), {"alert": 0, "warn": 0, "rules": {}})


class LoadRulesConfigMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "guard.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, text, mtime=None):
        with open(self.path, "w") as f:
            f.write(text)
        if mtime is not None:
            os.utime(self.path, (mtime, mtime))

    def test_missing_file_returns_defaults_no_error(self):
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules, guard.default_rules())
        self.assertIsNone(guard.LAST_LOAD_ERROR)

    def test_partial_file_keeps_other_defaults(self):
        self._write('{"rules": {"session_age": {"max_age_hours": 5}}}', mtime=1000)
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules["rules"]["session_age"]["max_age_hours"], 5)
        self.assertTrue(rules["rules"]["session_age"]["enabled"])
        self.assertEqual(rules["rules"]["token_rate"], guard.default_rules()["rules"]["token_rate"])
        self.assertEqual(rules["ignore"], {"devices": [], "sessions": [], "names": []})
        self.assertIsNone(guard.LAST_LOAD_ERROR)

    def test_bad_type_falls_back_per_key_not_per_rule(self):
        self._write(
            '{"rules": {"session_age": {"max_age_hours": 5, "enabled": "yes"}}}', mtime=1000)
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules["rules"]["session_age"]["max_age_hours"], 5)  # good key kept
        self.assertTrue(rules["rules"]["session_age"]["enabled"])  # bad key fell back
        self.assertIsNotNone(guard.LAST_LOAD_ERROR)

    def test_unknown_rule_name_ignored_silently(self):
        self._write('{"rules": {"not_a_real_rule": {"enabled": true}}}', mtime=1000)
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules, guard.default_rules())
        self.assertIsNone(guard.LAST_LOAD_ERROR)

    def test_ignore_bad_type_falls_back(self):
        self._write('{"ignore": {"devices": "dev-a"}}', mtime=1000)
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules["ignore"]["devices"], [])
        self.assertIsNotNone(guard.LAST_LOAD_ERROR)

    def test_top_level_not_an_object(self):
        self._write('[1, 2, 3]', mtime=1000)
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules, guard.default_rules())
        self.assertIsNotNone(guard.LAST_LOAD_ERROR)

    def test_malformed_json_returns_defaults_and_sets_error(self):
        self._write('{"rules": {"session_age": {}}', mtime=1000)  # missing closing brace
        rules = guard.load_rules(path=self.path)
        self.assertEqual(rules, guard.default_rules())
        self.assertIsNotNone(guard.LAST_LOAD_ERROR)

    def test_mtime_change_triggers_reread(self):
        self._write('{"rules": {"session_age": {"max_age_hours": 5}}}', mtime=1000)
        first = guard.load_rules(path=self.path)
        self.assertEqual(first["rules"]["session_age"]["max_age_hours"], 5)

        self._write('{"rules": {"session_age": {"max_age_hours": 9}}}', mtime=2000)
        second = guard.load_rules(path=self.path)
        self.assertEqual(second["rules"]["session_age"]["max_age_hours"], 9)

    def test_unchanged_mtime_is_served_from_cache(self):
        self._write('{"rules": {"session_age": {"max_age_hours": 5}}}', mtime=1000)
        first = guard.load_rules(path=self.path)
        # Rewrite with different content but the SAME mtime: a real cache
        # should still serve the memoized value rather than reparsing.
        self._write('{"rules": {"session_age": {"max_age_hours": 42}}}', mtime=1000)
        second = guard.load_rules(path=self.path)
        self.assertEqual(first, second)
        self.assertEqual(second["rules"]["session_age"]["max_age_hours"], 5)

    def test_failed_parse_does_not_poison_the_memo(self):
        self._write('{"rules": {"session_age": {"max_age_hours": 99}}}', mtime=1000)
        good = guard.load_rules(path=self.path)
        self.assertEqual(good["rules"]["session_age"]["max_age_hours"], 99)

        self._write('{not valid json', mtime=2000)
        broken = guard.load_rules(path=self.path)
        self.assertEqual(broken, guard.default_rules())
        self.assertIsNotNone(guard.LAST_LOAD_ERROR)

        # Retrying the exact same broken file/mtime must not get stuck
        # returning something other than defaults.
        broken_again = guard.load_rules(path=self.path)
        self.assertEqual(broken_again, guard.default_rules())

        # And once the file is fixed, loading must reflect the new
        # content rather than anything remembered from the failure.
        self._write('{"rules": {"session_age": {"max_age_hours": 55}}}', mtime=3000)
        fixed = guard.load_rules(path=self.path)
        self.assertEqual(fixed["rules"]["session_age"]["max_age_hours"], 55)

    def test_returned_rules_are_independent_copies(self):
        self._write('{"rules": {"session_age": {"max_age_hours": 5}}}', mtime=1000)
        first = guard.load_rules(path=self.path)
        first["rules"]["session_age"]["max_age_hours"] = 999
        second = guard.load_rules(path=self.path)
        self.assertEqual(second["rules"]["session_age"]["max_age_hours"], 5)

    def test_concurrent_loads_do_not_raise_and_agree(self):
        self._write('{"rules": {"session_age": {"max_age_hours": 7}}}', mtime=1000)
        results = []
        errors = []

        def worker():
            try:
                for _ in range(20):
                    results.append(guard.load_rules(path=self.path))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(all(r["rules"]["session_age"]["max_age_hours"] == 7 for r in results))


class EndToEndFourResumeLoopsTest(unittest.TestCase):
    """Reconstructs the 2026-09-05 incident: 120.6 M effective tokens in
    one day from four forgotten --resume loops, one alive for weeks. This
    is the scenario guard.py was built to catch."""

    def test_incident_scenario(self):
        leaker = session(
            device_id="dev-a", session_id="s-leaker", name="rc-resume-1",
            state="busy", started_at=NOW - 504 * 3600,  # 21 days old
            last_seen=NOW - 60, last_event_ts=NOW - 60,
            usage={"effective": 45_000_000, "output": 100_000, "last_ts": NOW - 60},
        )
        sprinter = session(
            device_id="dev-a", session_id="s-sprinter", name="rc-resume-2",
            state="busy", started_at=NOW - 2 * 3600,
            last_seen=NOW - 30, last_event_ts=NOW - 30,
            usage={"effective": 12_000_000, "output": 50_000, "last_ts": NOW - 30},
        )
        wedged = session(
            device_id="dev-a", session_id="s-wedged", name="rc-resume-3",
            state="busy", started_at=NOW - 5 * 3600,
            last_seen=NOW - 3000, last_event_ts=NOW - 3000,
            usage={"effective": 50_600_000, "output": 200_000, "last_ts": NOW - 3000},
        )
        quiet = session(
            device_id="dev-a", session_id="s-quiet", name="rc-normal",
            state="idle", started_at=NOW - 3 * 3600,
            last_seen=NOW - 100, last_event_ts=NOW - 100,
            usage={"effective": 13_000_000, "output": 80_000, "last_ts": NOW - 100},
        )
        sessions = [leaker, sprinter, wedged, quiet]

        total_effective = sum(s["usage"]["effective"] for s in sessions)
        self.assertEqual(total_effective, 120_600_000)

        findings = guard.evaluate(
            snap(sessions, [device()]), rules=guard.default_rules(), now_fn=now_fn)

        fired = {(f["rule"], f["session_id"]) for f in findings}
        self.assertEqual(fired, {
            ("session_age", "s-leaker"),
            ("token_rate", "s-sprinter"),
            ("token_rate", "s-wedged"),
            ("token_total", "s-wedged"),
            ("stalled", "s-wedged"),
        })

        for f in findings:
            self.assertNotEqual(f["session_id"], "s-quiet")

        summary = guard.summarize(findings)
        self.assertEqual(summary["alert"], 3)
        self.assertEqual(summary["warn"], 2)
        self.assertEqual(summary["rules"], {
            "session_age": 1, "token_rate": 2, "token_total": 1, "stalled": 1,
        })

        severities = [f["severity"] for f in findings]
        self.assertEqual(severities[:3], ["alert", "alert", "alert"])
        self.assertEqual(severities[3:], ["warn", "warn"])

        self.assertEqual(guard.LAST_RULE_ERRORS, [])


if __name__ == "__main__":
    unittest.main()
