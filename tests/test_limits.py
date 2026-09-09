"""limits.py: account-level rate limits/spend fetch (CONTRACT.md sections
2-3).

SECURITY (public repo): CONTRACT.md section 2 is explicit that no test may
read a real credentials file. Every test below that exercises token
reading passes an explicit `path=`/`runner=` fixture of its own (a temp
file this test itself creates, or a fake runner callable) -- none ever
touches os.path.expanduser("~/.claude/.credentials.json") or a real
Keychain. Every test that exercises get_limits() injects `fetch_fn`
instead of letting it fall through to a real token read or network call.

A running theme across the "never leaks" tests below: they plant a fake
token/secret inside a raised exception's message on purpose, then assert
that string is nowhere in limits.py's output -- proving type(e).__name__
is really all that crosses the boundary, not just that it happens not to
today."""
import http.server
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import limits

_FAKE_TOKEN = "TEST-FIXTURE-NOT-A-REAL-TOKEN-abc123"


def _creds_file(tmp_dir, token=_FAKE_TOKEN, name="creds.json"):
    """A throwaway JSON file shaped like ~/.claude/.credentials.json,
    written under a tempfile.TemporaryDirectory -- never the real path."""
    path = os.path.join(tmp_dir, name)
    with open(path, "w") as f:
        json.dump({"claudeAiOauth": {"accessToken": token}}, f)
    return path


class _FakeCompletedProcess:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class ReadTokenLinuxTest(unittest.TestCase):
    def test_reads_token_from_fixture_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = _creds_file(d)
            self.assertEqual(limits._read_token_linux(path=path), _FAKE_TOKEN)

    def test_missing_file_raises_credentials_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "does-not-exist.json")
            with self.assertRaises(limits.CredentialsUnavailable):
                limits._read_token_linux(path=path)

    def test_malformed_json_raises_credentials_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            with open(path, "w") as f:
                f.write("{not json")
            with self.assertRaises(limits.CredentialsUnavailable):
                limits._read_token_linux(path=path)

    def test_missing_oauth_key_raises_credentials_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            with open(path, "w") as f:
                json.dump({"somethingElse": True}, f)
            with self.assertRaises(limits.CredentialsUnavailable):
                limits._read_token_linux(path=path)

    def test_empty_access_token_raises_credentials_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = _creds_file(d, token="")
            with self.assertRaises(limits.CredentialsUnavailable):
                limits._read_token_linux(path=path)

    def test_non_dict_json_raises_credentials_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "creds.json")
            with open(path, "w") as f:
                json.dump([1, 2, 3], f)
            with self.assertRaises(limits.CredentialsUnavailable):
                limits._read_token_linux(path=path)


class ReadTokenMacosTest(unittest.TestCase):
    """`runner` fully replaces subprocess.run -- no real `security` binary
    or Keychain entry is ever touched."""

    def test_reads_token_from_fake_keychain_output(self):
        blob = json.dumps({"claudeAiOauth": {"accessToken": _FAKE_TOKEN}}).encode()
        runner = lambda *a, **k: _FakeCompletedProcess(blob)
        self.assertEqual(limits._read_token_macos(runner=runner), _FAKE_TOKEN)

    def test_runner_raising_raises_credentials_unavailable(self):
        import subprocess

        def runner(*a, **k):
            raise subprocess.CalledProcessError(44, "security")

        with self.assertRaises(limits.CredentialsUnavailable):
            limits._read_token_macos(runner=runner)

    def test_garbage_stdout_raises_credentials_unavailable(self):
        runner = lambda *a, **k: _FakeCompletedProcess(b"not json at all")
        with self.assertRaises(limits.CredentialsUnavailable):
            limits._read_token_macos(runner=runner)

    def test_str_stdout_is_also_accepted(self):
        # subprocess.run with text=True would hand back a str, not bytes;
        # this module always calls it without text=True, but the parse
        # path tolerates either just in case a future refactor changes
        # that default.
        blob = json.dumps({"claudeAiOauth": {"accessToken": _FAKE_TOKEN}})
        runner = lambda *a, **k: _FakeCompletedProcess(blob)
        self.assertEqual(limits._read_token_macos(runner=runner), _FAKE_TOKEN)


class ParseUsageResponseTest(unittest.TestCase):
    """The CONTRACT.md section 1 example response, mapped to section 3's
    output shape."""

    def _raw(self):
        return {
            "five_hour": {"utilization": 56.0,
                           "resets_at": "2026-09-08T00:40:00.511284+00:00"},
            "seven_day": {"utilization": 80.0,
                           "resets_at": "2026-09-08T07:00:00.511301+00:00"},
            "limits": [
                {"kind": "session", "group": "session", "percent": 56,
                 "severity": "normal", "resets_at": "r1", "scope": None,
                 "is_active": False},
                {"kind": "weekly_all", "group": "weekly", "percent": 80,
                 "severity": "warning", "resets_at": "r2", "scope": None,
                 "is_active": True},
                {"kind": "weekly_scoped", "group": "weekly", "percent": 42,
                 "severity": "normal", "resets_at": "r3",
                 "scope": {"model": {"display_name": "Fable"}}},
            ],
            "spend": {"used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
                      "limit": None, "percent": 0, "severity": "normal"},
            "extra_usage": {"is_enabled": False, "monthly_limit": None,
                             "used_credits": None, "utilization": None,
                             "user_disabled": True, "spend_limit_reached": False},
            # Unknown/codename keys must never break the parse or leak
            # through -- CONTRACT.md section 1.
            "tangelo": {"anything": "here"},
            "nimbus_quill": 12345,
        }

    def test_happy_path_matches_contract_shape(self):
        out = limits._parse_usage_response(self._raw(), now=1000.0)
        self.assertTrue(out["available"])
        self.assertIsNone(out["error"])
        self.assertEqual(out["fetched_at"], 1000.0)
        # Contract amendment 1 (fix round 1): five_hour/seven_day carry
        # `severity`, sourced from the matching `limits[]` row by kind
        # ("session" for five_hour, "weekly_all" for seven_day).
        self.assertEqual(out["five_hour"], {
            "percent": 56.0, "resets_at": "2026-09-08T00:40:00.511284+00:00",
            "severity": "normal",
        })
        self.assertEqual(out["seven_day"], {
            "percent": 80.0, "resets_at": "2026-09-08T07:00:00.511301+00:00",
            "severity": "warning",
        })
        self.assertNotIn("tangelo", json.dumps(out))
        self.assertNotIn("nimbus_quill", json.dumps(out))

    def test_severity_null_when_no_matching_limits_row(self):
        raw = self._raw()
        raw["limits"] = [row for row in raw["limits"] if row["kind"] == "weekly_scoped"]
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertIsNone(out["five_hour"]["severity"])
        self.assertIsNone(out["seven_day"]["severity"])

    def test_severity_null_when_limits_array_missing(self):
        raw = self._raw()
        del raw["limits"]
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertIsNone(out["five_hour"]["severity"])
        self.assertIsNone(out["seven_day"]["severity"])

    def test_percentages_are_floats(self):
        out = limits._parse_usage_response(self._raw(), now=1000.0)
        self.assertIsInstance(out["five_hour"]["percent"], float)
        self.assertIsInstance(out["seven_day"]["percent"], float)
        self.assertIsInstance(out["spend"]["percent"], float)
        for row in out["scoped"]:
            self.assertIsInstance(row["percent"], float)

    def test_scoped_excludes_unscoped_session_and_weekly_all(self):
        out = limits._parse_usage_response(self._raw(), now=1000.0)
        kinds = [row["kind"] for row in out["scoped"]]
        self.assertEqual(kinds, ["weekly_scoped"])

    def test_scoped_label_from_scope_model_display_name(self):
        out = limits._parse_usage_response(self._raw(), now=1000.0)
        self.assertEqual(out["scoped"][0]["label"], "Fable")
        self.assertIs(out["scoped"][0]["is_active"], False)

    def test_scoped_row_missing_scope_model_gets_null_label(self):
        raw = self._raw()
        raw["limits"].append({"kind": "weekly_scoped_other", "group": "weekly",
                               "percent": 10, "severity": "normal",
                               "resets_at": None, "scope": {}})
        out = limits._parse_usage_response(raw, now=1000.0)
        row = next(r for r in out["scoped"] if r["kind"] == "weekly_scoped_other")
        self.assertIsNone(row["label"])

    def test_spend_mapped_and_renamed(self):
        raw = self._raw()
        raw["spend"] = {"used": {"amount_minor": 1234, "currency": "USD", "exponent": 2},
                         "limit": 5000, "percent": 24.68, "severity": "warning"}
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertEqual(out["spend"], {
            "used_minor": 1234, "currency": "USD", "exponent": 2,
            "limit_minor": 5000, "percent": 24.68, "severity": "warning",
        })

    def test_extra_usage_keeps_only_the_three_documented_fields(self):
        raw = self._raw()
        raw["extra_usage"] = {"is_enabled": True, "monthly_limit": 999,
                               "used_credits": 12, "utilization": 33.5,
                               "user_disabled": False, "spend_limit_reached": True}
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertEqual(out["extra_usage"],
                          {"enabled": True, "utilization": 33.5, "spend_limit_reached": True})
        self.assertNotIn("monthly_limit", out["extra_usage"])
        self.assertNotIn("used_credits", out["extra_usage"])
        self.assertNotIn("user_disabled", out["extra_usage"])

    def test_empty_spend_dict_returns_none_not_an_invented_zero(self):
        # Low, fix round 2: {"spend": {}} used to produce {"used_minor":
        # None, ..., "percent": 0.0, ...} -- a dict that looks like a
        # real, if zero, reading even though nothing was present.
        self.assertIsNone(limits._spend({}))

    def test_spend_with_only_limit_present_still_returns_a_real_dict(self):
        # One recognised field present is enough to keep this a real
        # reading -- percent stays None rather than being invented as
        # 0.0, but the dict itself is not thrown away.
        out = limits._spend({"limit": 5000})
        self.assertIsNotNone(out)
        self.assertEqual(out["limit_minor"], 5000)
        self.assertIsNone(out["percent"])

    def test_empty_extra_usage_dict_returns_none_not_invented_false(self):
        # Low, fix round 2: {"extra_usage": {}} used to produce
        # {"enabled": False, "utilization": None, "spend_limit_reached":
        # False} -- bool(None) collapsing to False defeated an all-None
        # check run against the coerced output.
        self.assertIsNone(limits._extra_usage({}))

    def test_extra_usage_explicit_false_is_not_treated_as_absent(self):
        # An explicit is_enabled: false is real data, not "nothing was
        # present" -- must still return a dict, not None.
        out = limits._extra_usage({"is_enabled": False})
        self.assertIsNotNone(out)
        self.assertIs(out["enabled"], False)

    def test_response_with_only_empty_spend_and_extra_usage_raises(self):
        # End-to-end proof: a body carrying nothing but {"spend": {},
        # "extra_usage": {}} must trip the SAME Important 3 (fix round 1)
        # gate as a response with no recognised keys at all -- it must
        # never reach get_limits() as available=true and become primary.
        raw = {"spend": {}, "extra_usage": {}}
        with self.assertRaises(ValueError):
            limits._parse_usage_response(raw, now=1000.0)

    def test_missing_five_hour_becomes_none_not_a_raise(self):
        raw = self._raw()
        del raw["five_hour"]
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertIsNone(out["five_hour"])
        self.assertTrue(out["available"])

    def test_missing_limits_array_gives_empty_scoped(self):
        raw = self._raw()
        del raw["limits"]
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertEqual(out["scoped"], [])

    def test_missing_spend_and_extra_usage_become_none(self):
        raw = self._raw()
        del raw["spend"]
        del raw["extra_usage"]
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertIsNone(out["spend"])
        self.assertIsNone(out["extra_usage"])

    def test_garbage_sub_shapes_with_one_valid_field_still_available(self):
        # Every field except spend is unusable garbage; spend alone is
        # enough to keep this available=True (Important 3, fix round 1,
        # is about a response with NOTHING recognisable, not one where
        # most fields happen to be garbage).
        raw = {
            "five_hour": "not a dict",
            "seven_day": 12345,
            "limits": "not a list",
            "spend": {"used": {"amount_minor": 1, "currency": "USD", "exponent": 2},
                      "limit": None, "percent": 1.0, "severity": "normal"},
            "extra_usage": None,
        }
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertTrue(out["available"])
        self.assertIsNone(out["five_hour"])
        self.assertIsNone(out["seven_day"])
        self.assertEqual(out["scoped"], [])
        self.assertIsNotNone(out["spend"])
        self.assertIsNone(out["extra_usage"])

    def test_response_with_no_recognised_keys_raises(self):
        # Important 3 (fix round 1): a 200 whose body is a JSON object but
        # carries none of the keys this module reads must not be reported
        # as available -- it would become `primary` (being freshest) and
        # silently displace a genuinely good reading from another device.
        raw = {
            "five_hour": "not a dict",
            "seven_day": 12345,
            "limits": "not a list",
            "spend": ["not", "a", "dict"],
            "extra_usage": None,
            "some_unrelated_codename_key": 42,
        }
        with self.assertRaises(ValueError):
            limits._parse_usage_response(raw, now=1000.0)

    def test_empty_json_object_raises(self):
        with self.assertRaises(ValueError):
            limits._parse_usage_response({}, now=1000.0)

    def test_non_dict_response_raises(self):
        with self.assertRaises(ValueError):
            limits._parse_usage_response(["not", "a", "dict"], now=1000.0)


class GetLimitsCachingTest(unittest.TestCase):
    def setUp(self):
        limits._cache.clear()

    def test_success_is_cached_for_60_seconds(self):
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            return {"five_hour": {"utilization": 10.0, "resets_at": "r"},
                    "seven_day": {"utilization": 20.0, "resets_at": "r"},
                    "limits": [], "spend": None, "extra_usage": None}

        clock = {"t": 1000.0}
        result1 = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        clock["t"] += 30  # inside the 60s window
        result2 = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(result1, result2)
        self.assertEqual(result1["fetched_at"], 1000.0)

    def test_refetches_after_ttl_expires(self):
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            return {"five_hour": {"utilization": float(calls["n"]), "resets_at": "r"},
                    "seven_day": None, "limits": [], "spend": None, "extra_usage": None}

        clock = {"t": 1000.0}
        limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        clock["t"] += 61
        result2 = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result2["fetched_at"], 1061.0)

    def test_failure_after_a_success_serves_last_good_with_original_fetched_at(self):
        def good_fetch(timeout):
            return {"five_hour": {"utilization": 10.0, "resets_at": "r"},
                    "seven_day": None, "limits": [], "spend": None, "extra_usage": None}

        def bad_fetch(timeout):
            raise TimeoutError("plant: token=" + _FAKE_TOKEN)

        clock = {"t": 1000.0}
        good = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=good_fetch)
        self.assertTrue(good["available"])

        clock["t"] += 61
        served = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=bad_fetch)
        self.assertTrue(served["available"])
        self.assertEqual(served["fetched_at"], 1000.0)  # ORIGINAL fetched_at, not 1061
        self.assertEqual(served, good)

    def test_failure_with_no_prior_success_reports_unavailable(self):
        def bad_fetch(timeout):
            raise ValueError("boom")

        result = limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=bad_fetch)
        self.assertFalse(result["available"])
        self.assertEqual(result["fetched_at"], 1000.0)
        self.assertEqual(result["error"], "ValueError")
        self.assertIsNone(result["five_hour"])
        self.assertIsNone(result["seven_day"])
        self.assertEqual(result["scoped"], [])
        self.assertIsNone(result["spend"])
        self.assertIsNone(result["extra_usage"])

    def test_credentials_unavailable_reports_that_type_name_not_a_fault_message(self):
        def bad_fetch(timeout):
            raise limits.CredentialsUnavailable()

        result = limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=bad_fetch)
        self.assertFalse(result["available"])
        self.assertEqual(result["error"], "CredentialsUnavailable")

    def test_failure_is_also_throttled_for_60_seconds(self):
        calls = {"n": 0}

        def bad_fetch(timeout):
            calls["n"] += 1
            raise ValueError("boom")

        clock = {"t": 1000.0}
        limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=bad_fetch)
        clock["t"] += 30
        limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=bad_fetch)
        self.assertEqual(calls["n"], 1)


class RetryAfterParsingTest(unittest.TestCase):
    """_parse_retry_after_seconds: delta-seconds only (Task L5) -- an
    HTTP-date or garbage value falls back to None, never raises."""

    def test_plain_digit_string_parses_to_float_seconds(self):
        self.assertEqual(limits._parse_retry_after_seconds("120"), 120.0)

    def test_zero_is_a_valid_value(self):
        self.assertEqual(limits._parse_retry_after_seconds("0"), 0.0)

    def test_missing_header_returns_none(self):
        self.assertIsNone(limits._parse_retry_after_seconds(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(limits._parse_retry_after_seconds(""))

    def test_http_date_form_is_not_supported_returns_none(self):
        # RFC 7231 allows this form too; this module's stated residual
        # (see _parse_retry_after_seconds docstring) treats it the same
        # as a missing header rather than parsing it.
        self.assertIsNone(
            limits._parse_retry_after_seconds("Fri, 31 Dec 2027 23:59:59 GMT"))

    def test_negative_number_is_rejected(self):
        self.assertIsNone(limits._parse_retry_after_seconds("-5"))

    def test_non_numeric_garbage_returns_none(self):
        self.assertIsNone(limits._parse_retry_after_seconds("not-a-number"))

    def test_non_string_value_returns_none(self):
        self.assertIsNone(limits._parse_retry_after_seconds(120))


class RateLimitBackoffTest(unittest.TestCase):
    """Task L5: a 429 is not the same as a failure. get_limits() must
    respect Retry-After when present, otherwise back off past the normal
    60s cadence up to RATE_LIMIT_BACKOFF_CEILING_SECONDS, keep serving
    the last good reading with its true (growing) age throughout, and
    recover automatically once a non-429 outcome occurs."""

    def setUp(self):
        limits._cache.clear()
        limits.LAST_RATE_LIMITED_AT = None

    def test_error_is_rate_limited_not_a_generic_http_error(self):
        def fetch_fn(timeout):
            raise limits.RateLimited(retry_after=30.0)

        result = limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=fetch_fn)
        self.assertFalse(result["available"])
        self.assertEqual(result["error"], "RateLimited")

    def test_retry_after_is_honoured_over_the_normal_60s_cache(self):
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            raise limits.RateLimited(retry_after=300.0)  # 5 minutes

        clock = {"t": 1000.0}
        limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 1)

        clock["t"] += 61  # past the normal 60s cache, still inside 300s
        limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 1, "a 429 must not simply retry at the normal cadence")

        clock["t"] = 1000.0 + 300.0  # Retry-After has elapsed
        limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 2)

    def test_last_good_served_with_true_growing_age_during_backoff(self):
        def good_fetch(timeout):
            return {"five_hour": {"utilization": 10.0, "resets_at": "r"},
                    "seven_day": None, "limits": [], "spend": None, "extra_usage": None}

        def rate_limited_fetch(timeout):
            raise limits.RateLimited(retry_after=600.0)

        clock = {"t": 1000.0}
        good = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=good_fetch)
        self.assertTrue(good["available"])

        clock["t"] += 61
        served1 = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=rate_limited_fetch)
        clock["t"] += 100
        served2 = limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=rate_limited_fetch)

        # Never a fresh-looking failure: the SAME last-good reading, same
        # original fetched_at, every time -- staleness is computed by the
        # caller (now - fetched_at), which keeps growing as `clock`
        # advances, never reset by these throttled calls.
        self.assertEqual(served1, good)
        self.assertEqual(served2, good)
        self.assertEqual(served1["fetched_at"], 1000.0)
        self.assertEqual(served2["fetched_at"], 1000.0)

    def test_no_retry_after_backs_off_past_normal_cadence_and_escalates(self):
        calls = {"n": 0}
        attempt_times = []

        def fetch_fn(timeout):
            calls["n"] += 1
            raise limits.RateLimited(retry_after=None)

        clock = {"t": 0.0}

        def now_fn():
            return clock["t"]

        # First attempt: 429, no Retry-After.
        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        attempt_times.append(clock["t"])
        self.assertEqual(calls["n"], 1)

        # A plain 60s later must NOT be enough to retry (backoff exceeds
        # the normal cache interval on the very first 429).
        clock["t"] += 60
        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 1)

        # Advance far enough (well past any single-step backoff) and
        # confirm it DOES eventually retry, and that a second consecutive
        # 429 pushes the next attempt out even further than the first
        # gap did (escalation), while never exceeding the >=15 minute
        # ceiling by more than the ceiling itself.
        clock["t"] += limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS
        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 2)

    def test_backoff_caps_at_ceiling_after_repeated_429s(self):
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            raise limits.RateLimited(retry_after=None)

        clock = {"t": 0.0}

        def now_fn():
            return clock["t"]

        # Drive enough consecutive 429s that the escalating backoff would
        # have blown past any reasonable ceiling if it were unbounded,
        # advancing the clock by more than the ceiling between each.
        for _ in range(8):
            limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
            clock["t"] += limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS + 1

        self.assertEqual(calls["n"], 8)
        self.assertGreaterEqual(limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS, 900)

    def test_recovers_automatically_after_a_non_429_outcome(self):
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise limits.RateLimited(retry_after=None)
            return {"five_hour": {"utilization": 5.0, "resets_at": "r"},
                    "seven_day": None, "limits": [], "spend": None, "extra_usage": None}

        clock = {"t": 0.0}

        def now_fn():
            return clock["t"]

        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)  # 429, escalated backoff
        self.assertEqual(calls["n"], 1)

        clock["t"] += limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS + 1
        result = limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)  # success this time
        self.assertEqual(calls["n"], 2)
        self.assertTrue(result["available"])

        # Back to the NORMAL 60s cadence, not still backed off.
        clock["t"] += 61
        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 3)

    def test_explicit_retry_after_never_leaks_into_the_result(self):
        # The retry_after value is read off a RESPONSE header (never the
        # token), but this still asserts no incidental field carries it
        # into the wire shape -- the result dict must be exactly
        # CONTRACT.md's documented keys.
        def fetch_fn(timeout):
            raise limits.RateLimited(retry_after=42.0)

        result = limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=fetch_fn)
        self.assertEqual(
            set(result.keys()),
            {"available", "fetched_at", "five_hour", "seven_day", "scoped",
             "spend", "extra_usage", "error"})

    def test_retry_after_zero_still_throttles_does_not_hammer(self):
        # Fix round 3, MUST FIX: Retry-After: 0 used to set
        # next_attempt_at = now + 0, which the throttle gate (now <
        # next_attempt_at) treats as already expired -- every subsequent
        # call refetched immediately. Floored at CACHE_TTL_SECONDS now.
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            raise limits.RateLimited(retry_after=0.0)

        clock = {"t": 1000.0}

        def now_fn():
            return clock["t"]

        # Simulate what a naive caller polling once a second for 5
        # seconds would do -- the exact shape of the measured regression
        # (20 outbound calls in a 5s window against 1 correctly).
        for _ in range(5):
            limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
            clock["t"] += 1.0
        self.assertEqual(calls["n"], 1, "a Retry-After: 0 must not remove the throttle")

        # The floor is CACHE_TTL_SECONDS, not "forever" -- it still
        # recovers on its own once that's elapsed.
        clock["t"] = 1000.0 + limits.CACHE_TTL_SECONDS
        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 2)

    def test_absurdly_large_retry_after_is_clamped_not_honoured_verbatim(self):
        # Fix round 3, MUST FIX: an unbounded Retry-After used to wedge
        # this device for millennia (99999999999999999999 measured),
        # recoverable only by restarting the process.
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            raise limits.RateLimited(retry_after=99999999999999999999.0)

        clock = {"t": 1000.0}

        def now_fn():
            return clock["t"]

        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 1)

        clock["t"] = 1000.0 + limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS
        limits.get_limits(now_fn=now_fn, fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 2, "must recover within the ceiling, not stay wedged")

    def test_last_rate_limited_at_set_on_a_429_and_cleared_on_recovery(self):
        limits.LAST_RATE_LIMITED_AT = None

        def rate_limited_fetch(timeout):
            raise limits.RateLimited(retry_after=60.0)

        def good_fetch(timeout):
            return {"five_hour": {"utilization": 5.0, "resets_at": "r"},
                    "seven_day": None, "limits": [], "spend": None, "extra_usage": None}

        limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=rate_limited_fetch)
        self.assertEqual(limits.LAST_RATE_LIMITED_AT, 1000.0)

        limits.get_limits(now_fn=lambda: 1061.0, fetch_fn=good_fetch)
        self.assertIsNone(limits.LAST_RATE_LIMITED_AT)

    def test_last_rate_limited_at_cleared_on_a_different_kind_of_failure_too(self):
        limits.LAST_RATE_LIMITED_AT = None

        def rate_limited_fetch(timeout):
            raise limits.RateLimited(retry_after=60.0)

        def other_failure_fetch(timeout):
            raise ValueError("boom")

        limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=rate_limited_fetch)
        self.assertIsNotNone(limits.LAST_RATE_LIMITED_AT)

        limits.get_limits(now_fn=lambda: 1061.0, fetch_fn=other_failure_fetch)
        self.assertIsNone(limits.LAST_RATE_LIMITED_AT)

    def test_a_429_logs_a_warning_visible_without_a_manual_probe(self):
        def fetch_fn(timeout):
            raise limits.RateLimited(retry_after=60.0)

        with self.assertLogs("limits", level="WARNING") as ctx:
            limits.get_limits(now_fn=lambda: 1000.0, fetch_fn=fetch_fn)
        self.assertTrue(any("rate limited" in line for line in ctx.output))
        # Never the token, never an exception message -- only the two
        # plain numbers this module already exposes elsewhere.
        for line in ctx.output:
            self.assertNotIn(_FAKE_TOKEN, line)

    def test_throttled_return_from_cache_does_not_log_again(self):
        calls = {"n": 0}

        def fetch_fn(timeout):
            calls["n"] += 1
            raise limits.RateLimited(retry_after=300.0)

        clock = {"t": 1000.0}
        with self.assertLogs("limits", level="WARNING") as ctx:
            limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(len(ctx.output), 1)

        clock["t"] += 61  # still inside the 300s backoff, no real fetch
        with self.assertRaises(AssertionError):
            # assertLogs raises AssertionError itself if nothing was
            # logged in the block -- exactly what we want to confirm.
            with self.assertLogs("limits", level="WARNING"):
                limits.get_limits(now_fn=lambda: clock["t"], fetch_fn=fetch_fn)
        self.assertEqual(calls["n"], 1)


class ClampRetryAfterTest(unittest.TestCase):
    """_clamp_retry_after: [CACHE_TTL_SECONDS, RATE_LIMIT_BACKOFF_CEILING_SECONDS]."""

    def test_zero_clamps_up_to_the_floor(self):
        self.assertEqual(limits._clamp_retry_after(0.0), limits.CACHE_TTL_SECONDS)

    def test_below_the_floor_clamps_up(self):
        self.assertEqual(limits._clamp_retry_after(1.0), limits.CACHE_TTL_SECONDS)

    def test_huge_value_clamps_down_to_the_ceiling(self):
        self.assertEqual(
            limits._clamp_retry_after(99999999999999999999.0),
            limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS)

    def test_value_within_range_passes_through_unchanged(self):
        self.assertEqual(limits._clamp_retry_after(300.0), 300.0)

    def test_exact_floor_and_ceiling_pass_through_unchanged(self):
        self.assertEqual(limits._clamp_retry_after(limits.CACHE_TTL_SECONDS),
                          limits.CACHE_TTL_SECONDS)
        self.assertEqual(limits._clamp_retry_after(limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS),
                          limits.RATE_LIMIT_BACKOFF_CEILING_SECONDS)


class FetchFromApiRateLimitTest(unittest.TestCase):
    """_fetch_from_api translates a real HTTP 429 (with or without a
    Retry-After header) into RateLimited, never a plain HTTPError."""

    class _RateLimitHandler(http.server.BaseHTTPRequestHandler):
        retry_after_header = None  # set by each test before starting the server

        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            self.send_response(429)
            if type(self).retry_after_header is not None:
                self.send_header("Retry-After", type(self).retry_after_header)
            self.end_headers()

    def _start_server(self, retry_after_header=None):
        handler_cls = type(
            "_RateLimitHandlerInstance", (FetchFromApiRateLimitTest._RateLimitHandler,), {
                "retry_after_header": retry_after_header,
            })
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    def test_429_with_retry_after_header_raises_rate_limited_with_parsed_value(self):
        url = self._start_server(retry_after_header="45")

        def read_token_fn(timeout):
            return _FAKE_TOKEN

        with mock.patch.object(limits, "USAGE_URL", url):
            with self.assertRaises(limits.RateLimited) as ctx:
                limits._fetch_from_api(5, read_token_fn=read_token_fn)
        self.assertEqual(ctx.exception.retry_after, 45.0)

    def test_429_without_retry_after_header_raises_rate_limited_with_none(self):
        url = self._start_server(retry_after_header=None)

        def read_token_fn(timeout):
            return _FAKE_TOKEN

        with mock.patch.object(limits, "USAGE_URL", url):
            with self.assertRaises(limits.RateLimited) as ctx:
                limits._fetch_from_api(5, read_token_fn=read_token_fn)
        self.assertIsNone(ctx.exception.retry_after)

    def test_other_status_codes_still_raise_plain_http_error(self):
        url = self._start_server(retry_after_header=None)
        # Reuse the same server but force a different status via a
        # one-off handler subclass so this test stays self-contained.

        def read_token_fn(timeout):
            return _FAKE_TOKEN

        class _ForbiddenHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def do_GET(self):
                self.send_response(403)
                self.end_headers()

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ForbiddenHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        forbidden_url = f"http://127.0.0.1:{server.server_address[1]}/"

        with mock.patch.object(limits, "USAGE_URL", forbidden_url):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                limits._fetch_from_api(5, read_token_fn=read_token_fn)
        self.assertNotIsInstance(ctx.exception, limits.RateLimited)
        self.assertEqual(ctx.exception.code, 403)


class SecurityNeverLeaksTest(unittest.TestCase):
    """CONTRACT.md section 2: an exception raised anywhere near the token
    must never surface as str(e) -- only its type name. These tests plant
    a fake secret inside exception messages at every seam this module
    catches broadly, then assert it never appears anywhere in what
    get_limits() returns."""

    def setUp(self):
        limits._cache.clear()

    def _assert_no_leak(self, result):
        dumped = json.dumps(result)
        self.assertNotIn(_FAKE_TOKEN, dumped)
        self.assertNotIn("Authorization", dumped)
        self.assertNotIn("Bearer", dumped)

    def test_fetch_fn_exception_message_never_leaks(self):
        def fetch_fn(timeout):
            raise RuntimeError(
                f"GET failed, Authorization: Bearer {_FAKE_TOKEN}")

        result = limits.get_limits(now_fn=lambda: 1.0, fetch_fn=fetch_fn)
        self._assert_no_leak(result)
        self.assertEqual(result["error"], "RuntimeError")

    def test_parse_failure_exception_message_never_leaks(self):
        class _Boom(dict):
            def get(self, *a, **k):
                raise KeyError(f"leaked token {_FAKE_TOKEN}")

        def fetch_fn(timeout):
            return _Boom()

        result = limits.get_limits(now_fn=lambda: 1.0, fetch_fn=fetch_fn)
        self._assert_no_leak(result)
        self.assertEqual(result["error"], "KeyError")

    def test_read_token_exception_message_never_leaks_via_fetch_from_api(self):
        def read_token_fn(timeout):
            raise RuntimeError(f"could not read token {_FAKE_TOKEN}")

        def fetch_fn(timeout):
            return limits._fetch_from_api(timeout, read_token_fn=read_token_fn)

        result = limits.get_limits(now_fn=lambda: 1.0, fetch_fn=fetch_fn)
        self._assert_no_leak(result)
        self.assertEqual(result["error"], "RuntimeError")

    def test_unavailable_result_never_carries_a_token_field(self):
        result = limits.unavailable_result(1.0, error="SomeError")
        self.assertNotIn("token", result)
        self.assertNotIn("accessToken", result)
        self.assertNotIn("Authorization", result)


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Answers every GET with a 302 to `redirect_target` (set by the
    test before starting the server)."""
    redirect_target = None

    def log_message(self, *a, **k):
        pass  # keep test output quiet

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.redirect_target)
        self.end_headers()


class _AttackerHandler(http.server.BaseHTTPRequestHandler):
    """Records every request it receives (headers included) -- the
    Critical fix round 1 test asserts this list stays empty."""
    hits = []

    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        _AttackerHandler.hits.append(dict(self.headers))
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RedirectRefusalTest(unittest.TestCase):
    """Critical, fix round 1 security review: urlopen's default opener
    follows a redirect and RE-SENDS Authorization to the new host -- a
    302 from the usage endpoint to a foreign origin delivered a canary
    bearer token to an attacker server over plaintext http. Both real
    servers here are bound to 127.0.0.1 (loopback only, same as every
    other test in this suite -- no non-loopback connection is ever made)."""

    def setUp(self):
        limits._cache.clear()
        _AttackerHandler.hits = []
        self.attacker = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AttackerHandler)
        self.attacker_thread = threading.Thread(target=self.attacker.serve_forever, daemon=True)
        self.attacker_thread.start()
        attacker_port = self.attacker.server_address[1]
        _RedirectHandler.redirect_target = f"http://127.0.0.1:{attacker_port}/stolen"

        self.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        self.origin_thread = threading.Thread(target=self.origin.serve_forever, daemon=True)
        self.origin_thread.start()
        self.origin_url = f"http://127.0.0.1:{self.origin.server_address[1]}/"

    def tearDown(self):
        self.origin.shutdown()
        self.origin_thread.join(timeout=5)
        self.origin.server_close()
        self.attacker.shutdown()
        self.attacker_thread.join(timeout=5)
        self.attacker.server_close()

    def test_opener_refuses_redirect_to_another_origin(self):
        req = urllib.request.Request(
            self.origin_url, headers={"Authorization": "Bearer " + _FAKE_TOKEN})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            limits._NO_REDIRECT_OPENER.open(req, timeout=5)
        self.assertEqual(ctx.exception.code, 302)
        self.assertEqual(_AttackerHandler.hits, [],
                          "the redirect target must never receive a request")

    def test_fetch_from_api_never_follows_redirect_to_foreign_origin(self):
        def read_token_fn(timeout):
            return _FAKE_TOKEN

        with mock.patch.object(limits, "USAGE_URL", self.origin_url):
            with self.assertRaises(Exception):
                limits._fetch_from_api(5, read_token_fn=read_token_fn)

        self.assertEqual(_AttackerHandler.hits, [],
                          "the redirect target must never receive a request, "
                          "and the token must never reach it")

    def test_get_limits_reports_a_clean_failure_not_a_hang(self):
        def read_token_fn(timeout):
            return _FAKE_TOKEN

        def fetch_fn(timeout):
            with mock.patch.object(limits, "USAGE_URL", self.origin_url):
                return limits._fetch_from_api(timeout, read_token_fn=read_token_fn)

        result = limits.get_limits(now_fn=lambda: 1.0, fetch_fn=fetch_fn)
        self.assertFalse(result["available"])
        self.assertEqual(result["error"], "HTTPError")
        self.assertEqual(_AttackerHandler.hits, [])


class SingleFlightConcurrencyTest(unittest.TestCase):
    """Important 1, fix round 1: eight concurrent get_limits() calls used
    to make eight real fetches. The hub is a ThreadingHTTPServer, so
    concurrent callers are real -- get_limits() must guard the throttle
    with a lock so only one of them actually fetches."""

    def setUp(self):
        limits._cache.clear()

    def test_concurrent_calls_share_one_real_fetch(self):
        calls = {"n": 0}
        calls_lock = threading.Lock()

        def fetch_fn(timeout):
            with calls_lock:
                calls["n"] += 1
            time.sleep(0.05)  # widen the window concurrent callers can land in
            return {"five_hour": {"utilization": 10.0, "resets_at": "r"},
                    "seven_day": None, "limits": [], "spend": None, "extra_usage": None}

        barrier = threading.Barrier(8)
        results = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait(timeout=5)
            r = limits.get_limits(now_fn=time.time, fetch_fn=fetch_fn)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(calls["n"], 1, "only one thread should have performed a real fetch")
        self.assertEqual(len(results), 8)
        for r in results:
            self.assertTrue(r["available"])


class FetchBudgetTest(unittest.TestCase):
    """Important 2, fix round 1: the token read and the HTTPS call share
    ONE overall timeout budget, not one full budget each -- worst case
    used to be 2x timeout (measured 5.83s against a hung endpoint before
    this fix), against a contract that promises a hard 5s cap and a hub
    poll timeout of ~10s."""

    def test_slow_token_read_leaves_a_reduced_budget_for_the_http_call(self):
        def read_token_fn(timeout):
            time.sleep(0.2)
            return _FAKE_TOKEN

        captured = {}

        def fake_open(req, timeout):
            captured["timeout"] = timeout
            raise urllib.error.URLError("stop here, this test only checks the budget")

        with mock.patch.object(limits._NO_REDIRECT_OPENER, "open", side_effect=fake_open):
            with self.assertRaises(urllib.error.URLError):
                limits._fetch_from_api(1.0, read_token_fn=read_token_fn)

        self.assertLess(captured["timeout"], 1.0)
        self.assertGreater(captured["timeout"], 0.0)

    def test_token_read_exhausting_the_budget_raises_without_attempting_http(self):
        def read_token_fn(timeout):
            time.sleep(0.05)
            return _FAKE_TOKEN

        called = {"n": 0}

        def fake_open(req, timeout):
            called["n"] += 1
            raise AssertionError("the HTTPS call must never be attempted here")

        with mock.patch.object(limits._NO_REDIRECT_OPENER, "open", side_effect=fake_open):
            with self.assertRaises(TimeoutError):
                limits._fetch_from_api(0.01, read_token_fn=read_token_fn)

        self.assertEqual(called["n"], 0)

    def test_read_token_macos_forwards_the_remaining_budget_not_the_full_default(self):
        captured = {}

        def runner(cmd, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _FakeCompletedProcess(
                json.dumps({"claudeAiOauth": {"accessToken": _FAKE_TOKEN}}).encode())

        limits._read_token_macos(runner=runner, timeout=1.5)
        self.assertEqual(captured["timeout"], 1.5)
        self.assertNotEqual(captured["timeout"], limits.FETCH_TIMEOUT_SECONDS)


class TimeoutErrorNormalizationTest(unittest.TestCase):
    """Minor, fix round 1: a socket-level timeout's type name is
    "timeout" on 3.9 and "TimeoutError" on 3.10+ (socket.timeout became a
    plain alias of the builtin there). Normalised to one value here so
    the UI never has to know which Python produced it."""

    def test_socket_timeout_normalizes_to_timeouterror_string(self):
        def fetch_fn(timeout):
            raise socket.timeout("timed out")

        result, retry_after = limits._do_fetch(1000.0, 5, fetch_fn)
        self.assertEqual(result["error"], "TimeoutError")
        self.assertIsNone(retry_after)

    def test_plain_timeouterror_also_normalizes(self):
        def fetch_fn(timeout):
            raise TimeoutError("timed out")

        result, retry_after = limits._do_fetch(1000.0, 5, fetch_fn)
        self.assertEqual(result["error"], "TimeoutError")
        self.assertIsNone(retry_after)


class EstimateWindowTokensTest(unittest.TestCase):
    """Task-m3 (2026-09-09-usability): estimate_window_tokens() derives a
    token budget from Anthropic's percent + this hub's own measured
    `consumed`, since the usage endpoint never reports a budget itself.
    Every case below either checks the arithmetic or checks one of the
    "never guess" refusals the task-m3 brief asks for."""

    def test_derives_budget_and_remaining_from_percent_and_consumed(self):
        # 58% used, 100 measured -> budget ~172.4, remaining ~72.4.
        result = limits.estimate_window_tokens(58.0, 100.0)
        self.assertEqual(result["consumed"], 100)
        self.assertEqual(result["budget"], 172)
        self.assertEqual(result["remaining"], 72)
        self.assertIs(result["approximate"], True)

    def test_full_utilization_leaves_zero_remaining_not_negative(self):
        result = limits.estimate_window_tokens(100.0, 500.0)
        self.assertEqual(result["budget"], 500)
        self.assertEqual(result["remaining"], 0)

    def test_percent_below_floor_returns_none(self):
        self.assertIsNone(limits.estimate_window_tokens(
            limits.TOKEN_ESTIMATE_PERCENT_FLOOR - 0.1, 1000.0))

    def test_percent_at_floor_is_allowed(self):
        self.assertIsNotNone(limits.estimate_window_tokens(
            limits.TOKEN_ESTIMATE_PERCENT_FLOOR, 1000.0))

    def test_none_percent_returns_none(self):
        self.assertIsNone(limits.estimate_window_tokens(None, 1000.0))

    def test_none_consumed_returns_none(self):
        self.assertIsNone(limits.estimate_window_tokens(58.0, None))

    def test_zero_or_negative_consumed_returns_none(self):
        self.assertIsNone(limits.estimate_window_tokens(58.0, 0))
        self.assertIsNone(limits.estimate_window_tokens(58.0, -5.0))

    def test_bool_is_never_treated_as_a_number(self):
        # bool is an int subclass; both inputs must reject it explicitly.
        self.assertIsNone(limits.estimate_window_tokens(True, 1000.0))
        self.assertIsNone(limits.estimate_window_tokens(58.0, True))

    def test_never_raises_on_garbage_or_non_finite_input(self):
        for percent, consumed in (("58", 100), ([], 100), (58.0, "100"),
                                   (float("nan"), 100), (58.0, float("inf")),
                                   (float("inf"), 100), (58.0, float("nan"))):
            with self.subTest(percent=percent, consumed=consumed):
                try:
                    result = limits.estimate_window_tokens(percent, consumed)
                except Exception as e:  # pragma: no cover - failure path
                    self.fail(f"estimate_window_tokens raised {e!r}")
                else:
                    self.assertIsNone(result)


class EstimatesAreCoherentTest(unittest.TestCase):
    """Fix round 1 (coordinator review, 2026-09-09): the live bug this
    catches was two windows reporting the SAME `consumed`, and a
    five_hour `budget` LARGER than seven_day's -- both impossible, since
    a 5-hour window's activity is a strict subset of a 7-day window's."""

    def _est(self, consumed, budget):
        return {"consumed": consumed, "budget": budget, "remaining": budget - consumed,
                "approximate": True}

    def test_either_side_none_is_trivially_coherent(self):
        self.assertTrue(limits.estimates_are_coherent(None, self._est(100, 200)))
        self.assertTrue(limits.estimates_are_coherent(self._est(100, 200), None))
        self.assertTrue(limits.estimates_are_coherent(None, None))

    def test_shorter_within_longer_is_coherent(self):
        shorter = self._est(500, 1000)
        longer = self._est(1000, 2000)
        self.assertTrue(limits.estimates_are_coherent(shorter, longer))

    def test_equal_is_coherent(self):
        # Not the live bug's exact shape (that also broke on budget),
        # but consumed alone tying is not by itself a contradiction.
        same = self._est(500, 1000)
        self.assertTrue(limits.estimates_are_coherent(same, same))

    def test_shorter_consumed_exceeding_longer_is_incoherent(self):
        shorter = self._est(5000, 1000)  # consumed 5000 > longer's 1000
        longer = self._est(1000, 2000)
        self.assertFalse(limits.estimates_are_coherent(shorter, longer))

    def test_shorter_budget_exceeding_longer_is_incoherent(self):
        # This is the exact live-bug shape: equal consumed, but a larger
        # implied budget for the shorter window because its percent
        # happened to be lower.
        shorter = self._est(122_000_000, 290_000_000)
        longer = self._est(122_000_000, 201_000_000)
        self.assertFalse(limits.estimates_are_coherent(shorter, longer))


if __name__ == "__main__":
    unittest.main()
