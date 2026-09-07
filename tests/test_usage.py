"""usage.py: transcript-derived token accounting. Verifies dedup by
(message.id, requestId), the effective-token weights, the incremental
byte-offset cache (append/partial-line/truncation/rotation/grow-during-read
handling), subagent-to-parent-session merging, per-session-id summing
across multiple files, daily bucketing, LRU cache eviction, the
byte-budget clip, lock-narrowing race safety, and the various
never-raise-on-corrupt-input guarantees.

All fixtures live under a tempfile.TemporaryDirectory passed explicitly as
`root=`, never the real HOME, so these tests are safe to run anywhere
(including as root, where filesystem permission bits don't apply, which is
why the "unreadable file" and "unlistable root" tests avoid chmod: the
former patches usage._read_new_bytes, the latter points root at a plain
file instead of a directory, which os.walk's onerror hook rejects
regardless of privilege)."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import usage

PROJECT_DIR = "-tmp-fakeproj"


def _usage_row(msg_id="msg-1", request_id="req-1", model="claude-test-model",
                input_tokens=0, cache_read=0, cache_write=0, output=0,
                ts="2026-09-06T08:03:19.462Z", row_uuid="uuid-1",
                session_id="sess-1", iterations=None):
    message = {
        "id": msg_id,
        "model": model,
        "usage": {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output,
            "output_tokens_details": {"thinking_tokens": 1},
            "cache_creation": {"ephemeral_1h_input_tokens": cache_write, "ephemeral_5m_input_tokens": 0},
        },
    }
    if msg_id is None:
        del message["id"]
    if iterations is not None:
        message["usage"]["iterations"] = iterations
    row = {
        "type": "assistant",
        "sessionId": session_id,
        "requestId": request_id,
        "uuid": row_uuid,
        "timestamp": ts,
        "cwd": "/tmp/fakeproj",
        "isSidechain": False,
        "message": message,
    }
    return row


def _line(row):
    return json.dumps(row) + "\n"


class UsageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.proj_dir = os.path.join(self.root, PROJECT_DIR)
        os.makedirs(self.proj_dir)
        usage.reset_cache()

    def tearDown(self):
        usage.reset_cache()
        self.tmp.cleanup()

    def _session_path(self, session_id="sess-1"):
        return os.path.join(self.proj_dir, session_id + ".jsonl")

    def _subagent_path(self, parent_session_id, agent_id="agent-1"):
        d = os.path.join(self.proj_dir, parent_session_id, "subagents")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, agent_id + ".jsonl")

    def _write(self, path, text, mode="w"):
        with open(path, mode) as f:
            f.write(text)


class DedupTest(UsageTestCase):
    def test_duplicate_message_id_and_request_id_counted_once(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=10, output=5)))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["input"], 10)
        self.assertEqual(sess["output"], 5)
        self.assertEqual(sess["messages"], 1)

        # Same (message.id, requestId) arrives again in a LATER incremental
        # read (appended after the first rollup already ran).
        self._write(path, _line(_usage_row(input_tokens=10, output=5)), mode="a")
        data2 = usage.rollup(root=self.root)
        sess2 = data2["sessions"]["sess-1"]
        self.assertEqual(sess2["input"], 10)
        self.assertEqual(sess2["output"], 5)
        self.assertEqual(sess2["messages"], 1)

    def test_missing_message_id_falls_back_to_uuid(self):
        path = self._session_path()
        # Two lines with no message.id but the same requestId; distinct
        # uuids so they should NOT be treated as duplicates of each other.
        self._write(path,
                     _line(_usage_row(msg_id=None, row_uuid="u1", input_tokens=1, output=1))
                     + _line(_usage_row(msg_id=None, row_uuid="u2", input_tokens=1, output=1)))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["messages"], 2)
        self.assertEqual(sess["input"], 2)

        # But a repeat of the SAME uuid+requestId pair is a duplicate.
        self._write(path,
                     _line(_usage_row(msg_id=None, row_uuid="u1", input_tokens=1, output=1)),
                     mode="a")
        data2 = usage.rollup(root=self.root)
        self.assertEqual(data2["sessions"]["sess-1"]["messages"], 2)

    def test_both_id_and_uuid_missing_counts_unconditionally(self):
        path = self._session_path()
        row1 = _usage_row(msg_id=None, row_uuid=None, input_tokens=1, output=1)
        row1["message"].pop("id", None)
        row1.pop("uuid", None)
        row2 = dict(row1)  # structurally identical
        self._write(path, _line(row1) + _line(row2))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        # Both counted: no key was possible, so neither could be a
        # detected duplicate of the other. Data is never silently dropped.
        self.assertEqual(sess["messages"], 2)
        self.assertEqual(sess["input"], 2)


class SyntheticModelTest(UsageTestCase):
    def test_synthetic_model_excluded(self):
        path = self._session_path()
        self._write(
            path,
            _line(_usage_row(msg_id="m1", model="<synthetic>", input_tokens=999, output=999))
            + _line(_usage_row(msg_id="m2", model="claude-real", input_tokens=1, output=1)))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["input"], 1)
        self.assertEqual(sess["output"], 1)
        self.assertEqual(sess["messages"], 1)
        self.assertEqual(sess["models"], {"claude-real": 1})


class IterationsTest(UsageTestCase):
    def test_iterations_never_double_counted(self):
        path = self._session_path()
        row = _usage_row(input_tokens=10, cache_read=20, cache_write=5, output=3,
                          iterations=[
                              {"input_tokens": 10, "cache_read_input_tokens": 20,
                               "cache_creation_input_tokens": 5, "output_tokens": 3},
                              {"input_tokens": 10, "cache_read_input_tokens": 20,
                               "cache_creation_input_tokens": 5, "output_tokens": 3},
                          ])
        self._write(path, _line(row))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["input"], 10)
        self.assertEqual(sess["cache_read"], 20)
        self.assertEqual(sess["cache_write"], 5)
        self.assertEqual(sess["output"], 3)
        self.assertEqual(sess["messages"], 1)


class EffectiveWeightingTest(unittest.TestCase):
    def test_hand_computed_weighting(self):
        # 100*1 + 200*0.1 + 50*2 + 10*5 = 100 + 20 + 100 + 50 = 270
        self.assertEqual(usage.effective(input=100, cache_read=200, cache_write=50, output=10), 270)

    def test_defaults_to_zero(self):
        self.assertEqual(usage.effective(), 0)

    def test_weights_constant_matches_spec(self):
        self.assertEqual(
            usage.WEIGHTS,
            {"input": 1.0, "cache_read": 0.1, "cache_write": 2.0, "output": 5.0})

    def test_non_int_inputs_count_as_zero(self):
        self.assertEqual(usage.effective(input="abc", cache_read=None, cache_write=True, output=10), 50)


class NegativeValuesTest(UsageTestCase):
    """Minor: _as_int (and therefore effective() and every parsed usage
    field) must clamp negative values to 0, not subtract from totals."""

    def test_as_int_rejects_negative_values(self):
        self.assertEqual(usage._as_int(-5), 0)
        self.assertEqual(usage._as_int(5), 5)

    def test_effective_treats_negative_inputs_as_zero(self):
        self.assertEqual(usage.effective(input=-100, output=10), 50)

    def test_negative_usage_field_in_record_counts_as_zero(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=-999, output=1)))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["input"], 0)
        self.assertEqual(sess["output"], 1)


class IncrementalReadTest(UsageTestCase):
    def test_second_call_reads_only_appended_bytes(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)))
        first = usage.rollup(root=self.root)
        self.assertEqual(first["bytes_read"], os.path.getsize(path))

        self._write(path, _line(_usage_row(msg_id="m2", input_tokens=2, output=2)), mode="a")
        second = usage.rollup(root=self.root)
        self.assertLess(second["bytes_read"], os.path.getsize(path))
        self.assertEqual(second["bytes_read"], len(_line(_usage_row(msg_id="m2", input_tokens=2, output=2)).encode("utf-8")))
        self.assertEqual(second["sessions"]["sess-1"]["input"], 3)

    def test_unchanged_file_is_not_reopened(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))
        usage.rollup(root=self.root)
        second = usage.rollup(root=self.root)
        self.assertEqual(second["bytes_read"], 0)


class PartialLineTest(UsageTestCase):
    def test_partial_trailing_line_not_consumed_until_completed(self):
        path = self._session_path()
        complete = _line(_usage_row(msg_id="m1", input_tokens=1, output=1))
        partial = json.dumps(_usage_row(msg_id="m2", input_tokens=2, output=2))  # no trailing \n
        self._write(path, complete + partial)

        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["messages"], 1)
        self.assertEqual(sess["input"], 1)
        self.assertEqual(data["bytes_read"], len(complete.encode("utf-8")))

        # Complete the line.
        self._write(path, "\n", mode="a")
        data2 = usage.rollup(root=self.root)
        sess2 = data2["sessions"]["sess-1"]
        self.assertEqual(sess2["messages"], 2)
        self.assertEqual(sess2["input"], 3)
        self.assertEqual(data2["bytes_read"], len(partial.encode("utf-8")) + 1)


class TruncationAndRotationTest(UsageTestCase):
    def test_truncation_forces_full_reread(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=100, output=100)))
        usage.rollup(root=self.root)

        # Truncate and write a smaller, different record at the same path
        # (same inode, smaller size).
        self._write(path, _line(_usage_row(msg_id="m2", input_tokens=1, output=1)))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["input"], 1)
        self.assertEqual(sess["output"], 1)
        self.assertEqual(sess["messages"], 1)

    def test_inode_change_forces_full_reread(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=100, output=100)))
        usage.rollup(root=self.root)

        os.remove(path)
        self._write(path, _line(_usage_row(msg_id="m2", input_tokens=1, output=1)))
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        self.assertEqual(sess["input"], 1)
        self.assertEqual(sess["messages"], 1)

    def test_grow_between_stat_and_read_then_truncate_is_detected(self):
        # Critical 2 repro: the file grows AFTER _discover_files() stats it
        # but BEFORE _read_new_bytes() actually reads it, so the read
        # observes more bytes than the stale pre-read stat reported. If
        # the cached "size" were left at that stale, smaller value, a
        # later truncation back down to it would go undetected (new size
        # >= stale cached size) and the offset would sit past the new EOF
        # forever.
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)))

        real_read = usage._read_new_bytes
        extra_line = _line(_usage_row(msg_id="m2", input_tokens=2, output=2))

        def _grow_then_read(p, offset, max_bytes):
            with open(p, "a") as f:
                f.write(extra_line)
            return real_read(p, offset, max_bytes)

        with mock.patch.object(usage, "_read_new_bytes", side_effect=_grow_then_read):
            data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
        # The read picked up the grown content too (m1 and m2): the
        # cached size reflects what was actually observed, not the stale
        # pre-growth stat value.
        self.assertEqual(sess["input"], 3)
        self.assertEqual(sess["messages"], 2)

        # Truncate back down to just one (different, smaller) record.
        self._write(path, _line(_usage_row(msg_id="m3", input_tokens=9, output=9)))
        data2 = usage.rollup(root=self.root)
        sess2 = data2["sessions"]["sess-1"]
        self.assertEqual(sess2["input"], 9)
        self.assertEqual(sess2["messages"], 1)


class ClippedReadTest(UsageTestCase):
    def test_read_larger_than_remaining_budget_is_clipped_and_partial(self):
        path = self._session_path()
        rows = "".join(
            _line(_usage_row(msg_id="m%d" % i, request_id="r%d" % i, input_tokens=1, output=1))
            for i in range(50))
        self._write(path, rows)
        total_size = os.path.getsize(path)
        budget = max(total_size // 3, 1)  # deliberately smaller than the file

        data = usage.rollup(root=self.root, max_bytes_per_call=budget)
        self.assertTrue(data["partial"])
        self.assertLessEqual(data["bytes_read"], budget)
        self.assertGreater(data["bytes_read"], 0)
        self.assertLess(data["sessions"]["sess-1"]["messages"], 50)

        # A follow-up call with a generous budget picks up the rest.
        data2 = usage.rollup(root=self.root, max_bytes_per_call=usage.DEFAULT_MAX_BYTES_PER_CALL)
        self.assertEqual(data2["sessions"]["sess-1"]["messages"], 50)


class CorruptInputTest(UsageTestCase):
    def test_corrupt_missing_and_noninteger_lines_never_raise(self):
        path = self._session_path()
        lines = [
            "not valid json at all\n",
            json.dumps({"type": "assistant", "message": {"model": "x"}}) + "\n",  # no usage key
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m-bad",
                    "model": "claude-test-model",
                    "usage": {"input_tokens": "abc", "output_tokens": None,
                              "cache_read_input_tokens": [1, 2], "cache_creation_input_tokens": 3.5},
                },
            }) + "\n",
            _line(_usage_row(msg_id="m-good", input_tokens=7, output=1)),
        ]
        self._write(path, "".join(lines))
        try:
            data = usage.rollup(root=self.root)
        except Exception as e:  # pragma: no cover - failure path
            self.fail(f"rollup() raised on corrupt input: {e!r}")
        sess = data["sessions"]["sess-1"]
        # m-bad contributes 0 for every non-int field but is still counted
        # as a message (it had a valid usage dict).
        self.assertEqual(sess["messages"], 2)
        self.assertEqual(sess["input"], 7)
        self.assertEqual(sess["output"], 1)
        self.assertEqual(sess["cache_read"], 0)
        self.assertEqual(sess["cache_write"], 0)


class UnreadableFileTest(UsageTestCase):
    def test_unreadable_file_counted_as_skipped_not_raised(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))

        def _boom(_path, _offset, _max_bytes):
            raise OSError("simulated permission error")

        with mock.patch.object(usage, "_read_new_bytes", side_effect=_boom):
            data = usage.rollup(root=self.root)
        self.assertEqual(data["skipped"], 1)
        self.assertEqual(data["sessions"], {})

    def test_unreadable_file_recovers_once_readable_again(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))

        def _boom(_path, _offset, _max_bytes):
            raise OSError("simulated permission error")

        with mock.patch.object(usage, "_read_new_bytes", side_effect=_boom):
            usage.rollup(root=self.root)
        data = usage.rollup(root=self.root)
        self.assertEqual(data["skipped"], 0)
        self.assertEqual(data["sessions"]["sess-1"]["input"], 1)


class ConcurrentUpdateDiscardTest(UsageTestCase):
    def test_stale_read_is_discarded_not_double_applied(self):
        """Important 6's lock-narrowing: the disk read and JSON parse run
        with no lock held. If another caller has already committed a
        change to the same file's cache entry by the time we finish, our
        (now stale) result must be discarded, not merged on top (which
        would double count) and not allowed to clobber the newer state."""
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)))

        real_read = usage._read_new_bytes

        def _read_then_race_ahead(p, offset, max_bytes):
            data = real_read(p, offset, max_bytes)
            # Simulate a concurrent caller having already committed an
            # update to this same entry while we were "reading".
            entry = usage._cache.get(p)
            if entry is not None:
                entry["offset"] += 1
            return data

        with mock.patch.object(usage, "_read_new_bytes", side_effect=_read_then_race_ahead):
            data = usage.rollup(root=self.root)
        self.assertNotIn("sess-1", data["sessions"])
        self.assertEqual(data["bytes_read"], 0)


class DailyBucketTest(UsageTestCase):
    def test_daily_buckets_by_utc_date_and_honours_days(self):
        path = self._session_path()
        recent_row = _usage_row(msg_id="m-recent", ts="2026-09-06T08:03:19.462Z",
                                 input_tokens=1, output=1)
        old_row = _usage_row(msg_id="m-old", ts="2026-06-01T00:00:00.000Z",
                              input_tokens=2, output=2)
        self._write(path, _line(recent_row) + _line(old_row))

        fixed_now = datetime.datetime(2026, 9, 7, tzinfo=datetime.timezone.utc).timestamp()

        data = usage.rollup(root=self.root, now_fn=lambda: fixed_now, days=30)
        self.assertIn("2026-09-06", data["daily"])
        self.assertNotIn("2026-06-01", data["daily"])
        self.assertEqual(data["daily"]["2026-09-06"]["input"], 1)
        self.assertEqual(data["daily"]["2026-09-06"]["effective"], usage.effective(input=1, output=1))

        usage.reset_cache()
        self._write(path, _line(recent_row) + _line(old_row))
        data2 = usage.rollup(root=self.root, now_fn=lambda: fixed_now, days=120)
        self.assertIn("2026-06-01", data2["daily"])
        self.assertEqual(data2["daily"]["2026-06-01"]["input"], 2)

    def test_unparsable_timestamp_still_counts_tokens_but_skips_daily(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(ts="not-a-timestamp", input_tokens=5, output=1)))
        data = usage.rollup(root=self.root)
        self.assertEqual(data["sessions"]["sess-1"]["input"], 5)
        self.assertEqual(data["daily"], {})

    def test_days_30_yields_exactly_30_buckets_not_31(self):
        # Minor: an inclusive cutoff comparison used to yield days+1
        # buckets. 35 consecutive daily records, days=30, must yield
        # exactly 30 daily buckets.
        path = self._session_path()
        fixed_now_dt = datetime.datetime(2026, 9, 7, tzinfo=datetime.timezone.utc)
        fixed_now = fixed_now_dt.timestamp()
        lines = []
        for i in range(35):
            day = fixed_now_dt - datetime.timedelta(days=i)
            ts = day.strftime("%Y-%m-%dT00:00:00.000Z")
            lines.append(_line(_usage_row(
                msg_id="m%d" % i, request_id="r%d" % i, ts=ts, input_tokens=1, output=1)))
        self._write(path, "".join(lines))
        data = usage.rollup(root=self.root, now_fn=lambda: fixed_now, days=30)
        self.assertEqual(len(data["daily"]), 30)


class MaxBytesPerCallTest(UsageTestCase):
    def test_budget_exhaustion_sets_partial_and_keeps_cached_data(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=42, output=1)))
        first = usage.rollup(root=self.root)
        self.assertFalse(first["partial"])
        self.assertEqual(first["sessions"]["sess-1"]["input"], 42)

        second = usage.rollup(root=self.root, max_bytes_per_call=0)
        self.assertTrue(second["partial"])
        self.assertEqual(second["bytes_read"], 0)
        # Cached totals from the first call are still returned.
        self.assertEqual(second["sessions"]["sess-1"]["input"], 42)


class ResetCacheTest(UsageTestCase):
    def test_reset_cache_forces_full_reread(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)))
        usage.rollup(root=self.root)
        self._write(path, _line(_usage_row(msg_id="m2", input_tokens=1, output=1)), mode="a")
        incremental = usage.rollup(root=self.root)
        self.assertLess(incremental["bytes_read"], os.path.getsize(path))

        usage.reset_cache()
        full = usage.rollup(root=self.root)
        self.assertEqual(full["bytes_read"], os.path.getsize(path))
        self.assertEqual(full["sessions"]["sess-1"]["input"], 2)


class SubagentMergeTest(UsageTestCase):
    def test_subagent_usage_rolls_into_parent_session_not_a_phantom(self):
        parent_id = "parent-uuid-1"
        parent_path = self._session_path(session_id=parent_id)
        self._write(parent_path, _line(_usage_row(
            session_id=parent_id, msg_id="p1", request_id="rp1",
            input_tokens=1, output=1)))

        sub_path = self._subagent_path(parent_id, "agent-abc")
        self._write(sub_path, _line(_usage_row(
            session_id=parent_id, msg_id="s1", request_id="rs1",
            input_tokens=2, output=2)))

        data = usage.rollup(root=self.root)
        self.assertIn(parent_id, data["sessions"])
        self.assertEqual(data["sessions"][parent_id]["input"], 3)
        self.assertEqual(data["sessions"][parent_id]["output"], 3)
        self.assertEqual(data["sessions"][parent_id]["messages"], 2)
        # No phantom session named after the subagent's own filename.
        self.assertNotIn("agent-abc", data["sessions"])
        for sid in data["sessions"]:
            self.assertFalse(sid.startswith("agent-"), "phantom subagent session: %r" % sid)

    def test_subagent_project_matches_parent_not_subagents_dirname(self):
        parent_id = "parent-uuid-2"
        parent_path = self._session_path(session_id=parent_id)
        self._write(parent_path, _line(_usage_row(
            session_id=parent_id, msg_id="p1", request_id="rp1", input_tokens=1, output=1)))
        sub_path = self._subagent_path(parent_id, "agent-xyz")
        self._write(sub_path, _line(_usage_row(
            session_id=parent_id, msg_id="s1", request_id="rs1", input_tokens=1, output=1)))

        data = usage.rollup(root=self.root)
        self.assertEqual(data["sessions"][parent_id]["project"], PROJECT_DIR)

    def test_subagent_only_session_falls_back_to_own_id_when_no_parent_file(self):
        # A subagent file's own records still carry the parent sessionId
        # even if the parent's own top-level file isn't present (e.g. not
        # yet created, or outside the read window): the subagent's spend
        # should land on that parent id, not its own filename stem.
        parent_id = "parent-uuid-3"
        sub_path = self._subagent_path(parent_id, "agent-only")
        self._write(sub_path, _line(_usage_row(
            session_id=parent_id, msg_id="s1", request_id="rs1", input_tokens=5, output=5)))

        data = usage.rollup(root=self.root)
        self.assertIn(parent_id, data["sessions"])
        self.assertNotIn("agent-only", data["sessions"])


class MultiFileSessionMergeTest(UsageTestCase):
    def test_two_files_mapping_to_one_session_sum_not_overwrite(self):
        # Reproduces the "7 different journal.jsonl files collapsed into
        # one session" bug: two files under the same session directory,
        # both carrying the same sessionId, must SUM rather than the
        # second overwriting the first.
        session_id = "shared-session"
        dir_a = os.path.join(self.proj_dir, session_id, "part-a")
        dir_b = os.path.join(self.proj_dir, session_id, "part-b")
        os.makedirs(dir_a)
        os.makedirs(dir_b)
        path_a = os.path.join(dir_a, "journal.jsonl")
        path_b = os.path.join(dir_b, "journal.jsonl")
        self._write(path_a, _line(_usage_row(
            session_id=session_id, msg_id="a1", request_id="ra", input_tokens=10, output=1)))
        self._write(path_b, _line(_usage_row(
            session_id=session_id, msg_id="b1", request_id="rb", input_tokens=20, output=2)))

        data = usage.rollup(root=self.root)
        matching = [sid for sid in data["sessions"] if sid == session_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(data["sessions"][session_id]["input"], 30)
        self.assertEqual(data["sessions"][session_id]["output"], 3)
        self.assertEqual(data["sessions"][session_id]["messages"], 2)

    def test_each_files_dedup_set_stays_separate(self):
        # The SAME (message.id, requestId) appearing in two DIFFERENT
        # files mapping to the same session is not a cross-file duplicate
        # (each file keeps its own dedup set): both count.
        session_id = "shared-session-2"
        dir_a = os.path.join(self.proj_dir, session_id, "part-a")
        dir_b = os.path.join(self.proj_dir, session_id, "part-b")
        os.makedirs(dir_a)
        os.makedirs(dir_b)
        path_a = os.path.join(dir_a, "journal.jsonl")
        path_b = os.path.join(dir_b, "journal.jsonl")
        row = _usage_row(session_id=session_id, msg_id="same-id", request_id="same-req",
                          input_tokens=7, output=1)
        self._write(path_a, _line(row))
        self._write(path_b, _line(row))

        data = usage.rollup(root=self.root)
        self.assertEqual(data["sessions"][session_id]["input"], 14)
        self.assertEqual(data["sessions"][session_id]["messages"], 2)


class MultipleProjectDirsTest(UsageTestCase):
    def test_multiple_project_dirs_produce_independent_sessions(self):
        proj2 = os.path.join(self.root, "-tmp-otherproj")
        os.makedirs(proj2)
        path1 = self._session_path(session_id="s-in-proj1")
        self._write(path1, _line(_usage_row(
            session_id="s-in-proj1", msg_id="m1", input_tokens=1, output=1)))
        path2 = os.path.join(proj2, "s-in-proj2.jsonl")
        self._write(path2, _line(_usage_row(
            session_id="s-in-proj2", msg_id="m2", input_tokens=2, output=2)))

        data = usage.rollup(root=self.root)
        self.assertEqual(data["sessions"]["s-in-proj1"]["project"], PROJECT_DIR)
        self.assertEqual(data["sessions"]["s-in-proj2"]["project"], "-tmp-otherproj")


class LRUEvictionTest(UsageTestCase):
    def test_evicts_least_recently_active_not_most_active(self):
        paths = []
        for sid in ("old", "mid", "new"):
            p = self._session_path(session_id=sid)
            self._write(p, _line(_usage_row(session_id=sid, msg_id="m-" + sid, input_tokens=1, output=1)))
            paths.append(p)
        base = 1_700_000_000
        os.utime(paths[0], (base, base))
        os.utime(paths[1], (base + 100, base + 100))
        os.utime(paths[2], (base + 200, base + 200))

        with mock.patch.object(usage, "MAX_CACHE_ENTRIES", 2):
            data = usage.rollup(root=self.root)

        self.assertEqual(len(usage._cache), 2)
        self.assertNotIn(paths[0], usage._cache)  # oldest-mtime: evicted
        self.assertIn(paths[1], usage._cache)
        self.assertIn(paths[2], usage._cache)  # newest-mtime: survives
        self.assertNotIn("old", data["sessions"])
        self.assertIn("mid", data["sessions"])
        self.assertIn("new", data["sessions"])


class EvictMissingPerfTest(UsageTestCase):
    def test_evict_missing_skips_exists_check_for_discovered_files(self):
        # Minor: _evict_missing() used to call os.path.exists() for every
        # cached path on every call. It should now skip any path that
        # this call's directory walk already confirmed exists.
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))
        usage.rollup(root=self.root)  # populates the cache

        calls = []
        real_exists = os.path.exists

        def _spy(p):
            calls.append(p)
            return real_exists(p)

        with mock.patch("os.path.exists", side_effect=_spy):
            usage.rollup(root=self.root)  # steady state: file unchanged, still discovered
        self.assertNotIn(path, calls)


class UnlistableRootTest(UsageTestCase):
    def test_existing_but_empty_root_is_a_clean_zero(self):
        empty_root = os.path.join(self.root, "genuinely-empty")
        os.makedirs(empty_root)
        data = usage.rollup(root=empty_root)
        self.assertEqual(data["sessions"], {})
        self.assertEqual(data["files"], 0)
        self.assertEqual(data["skipped"], 0)
        self.assertFalse(data["partial"])

    def test_missing_root_counts_as_skipped_not_clean_zero(self):
        missing_root = os.path.join(self.root, "does-not-exist")
        data = usage.rollup(root=missing_root)
        self.assertEqual(data["sessions"], {})
        self.assertGreaterEqual(data["skipped"], 1)
        self.assertFalse(data["files"] == 0 and data["skipped"] == 0)

    def test_root_that_is_a_file_not_a_directory_counts_as_skipped(self):
        fake_root = os.path.join(self.root, "not-a-directory.jsonl")
        with open(fake_root, "w"):
            pass
        data = usage.rollup(root=fake_root)
        self.assertEqual(data["sessions"], {})
        self.assertGreaterEqual(data["skipped"], 1)


class MiscTest(UsageTestCase):
    def test_project_field_is_raw_encoded_dir_name(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))
        data = usage.rollup(root=self.root)
        self.assertEqual(data["sessions"]["sess-1"]["project"], PROJECT_DIR)

    def test_session_usage_returns_none_for_unknown_session(self):
        self.assertIsNone(usage.session_usage("no-such-session", root=self.root))

    def test_session_usage_matches_rollup(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))
        self.assertEqual(
            usage.session_usage("sess-1", root=self.root),
            usage.rollup(root=self.root)["sessions"]["sess-1"])

    def test_root_honours_claude_config_dir_override(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/alt-config"}):
            self.assertEqual(usage._default_root(), "/tmp/alt-config/projects")

    def test_default_root_without_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(usage._default_root(), os.path.expanduser("~/.claude/projects"))


if __name__ == "__main__":
    unittest.main()
