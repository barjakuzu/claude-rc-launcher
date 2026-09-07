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
import json
import os
import tempfile
import unittest

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
        self.assertEqual(out["five_hour"],
                          {"percent": 56.0, "resets_at": "2026-09-08T00:40:00.511284+00:00"})
        self.assertEqual(out["seven_day"],
                          {"percent": 80.0, "resets_at": "2026-09-08T07:00:00.511301+00:00"})
        self.assertNotIn("tangelo", json.dumps(out))
        self.assertNotIn("nimbus_quill", json.dumps(out))

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

    def test_garbage_sub_shapes_never_raise(self):
        raw = {
            "five_hour": "not a dict",
            "seven_day": 12345,
            "limits": "not a list",
            "spend": ["not", "a", "dict"],
            "extra_usage": None,
        }
        out = limits._parse_usage_response(raw, now=1000.0)
        self.assertTrue(out["available"])
        self.assertIsNone(out["five_hour"])
        self.assertIsNone(out["seven_day"])
        self.assertEqual(out["scoped"], [])
        self.assertIsNone(out["spend"])
        self.assertIsNone(out["extra_usage"])

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
        def read_token_fn():
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


if __name__ == "__main__":
    unittest.main()
