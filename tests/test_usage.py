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
import io
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


def _padded_line(padding, **kwargs):
    """Like _line(_usage_row(**kwargs)), with an extra harmless field
    inflating the line's byte size by roughly `padding` bytes. Used to
    build a line reliably (and by a wide, non-fragile margin) larger or
    smaller than some budget, without depending on the exact fixed
    overhead of _usage_row's own JSON structure."""
    row = _usage_row(**kwargs)
    row["_pad"] = "x" * padding
    return _line(row)


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
        # bytes_read charges the full amount actually read off disk
        # (round 2, Important 2), including the dangling partial line's
        # bytes: they were genuinely read, just not yet consumed into a
        # complete record, and that real I/O cost must not look free.
        self.assertEqual(data["bytes_read"], len((complete + partial).encode("utf-8")))

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

    def test_grow_then_truncate_is_detected(self):
        # Critical 2 repro: ordinary growth (append) is picked up on the
        # next call, and a later truncation back down is still correctly
        # detected via the offset comparison (not the cached size, which
        # round 2's Important 1 fix no longer even sources from a stale
        # pre-scan: see StaleStatDuringUnlockedWorkTest for the
        # specific "file grew between the pre-scan and the per-file
        # read" race, now closed by re-stat'ing inside _update_entry's
        # own locked block instead of trusting the pre-scan value here).
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)))
        usage.rollup(root=self.root)

        self._write(path, _line(_usage_row(msg_id="m2", input_tokens=2, output=2)), mode="a")
        data = usage.rollup(root=self.root)
        sess = data["sessions"]["sess-1"]
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


class StaleStatDuringUnlockedWorkTest(UsageTestCase):
    """Round 2, Important 1: the pre-scan (_discover_files) stat can be
    badly stale by the time _update_entry actually gets to a file (every
    earlier file in the call is processed first, or a concurrent caller
    gets there sooner), and a file that only grew in the meantime must
    never look like a truncation. _update_entry now takes its own fresh
    os.stat inside its first locked block instead of trusting the
    pre-scan's (size, mtime, inode)."""

    def test_growth_between_prescan_and_fresh_stat_does_not_reset(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)))
        usage.rollup(root=self.root)  # baseline: synced, offset > 0

        extra_line = _line(_usage_row(msg_id="m2", input_tokens=2, output=2))
        real_stat = os.stat
        calls_for_path = [0]

        def _stat_wrapper(p, *a, **kw):
            result = real_stat(p, *a, **kw)
            if p == path:
                calls_for_path[0] += 1
                if calls_for_path[0] == 1:
                    # This is _discover_files's pre-scan stat. Grow the
                    # file right after it, before _update_entry's own
                    # (later, second) stat of the same path runs.
                    with open(p, "a") as f:
                        f.write(extra_line)
            return result

        with mock.patch("os.stat", side_effect=_stat_wrapper):
            data = usage.rollup(root=self.root)

        self.assertGreaterEqual(calls_for_path[0], 2)
        sess = data["sessions"]["sess-1"]
        # Not reset: the pre-existing m1 total survived AND the growth
        # that happened between the two stats (m2) was picked up in the
        # very same call, proving _update_entry decided this using its
        # own fresh stat, not the stale pre-scan value.
        self.assertEqual(sess["input"], 3)
        self.assertEqual(sess["messages"], 2)

        # Further proof of "not reset": a reset would have cleared
        # seen_keys, so a repeat of m1 would be (wrongly) recounted.
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=1, output=1)), mode="a")
        data2 = usage.rollup(root=self.root)
        self.assertEqual(data2["sessions"]["sess-1"]["messages"], 2)


class ClippedLineTest(UsageTestCase):
    """Round 2, Important 2: a single line longer than what a call
    allocates to it must still charge that call's budget (so the
    accounting doesn't lie about doing zero I/O) and must still be able
    to complete on a later call with enough budget, rather than being
    permanently wedged."""

    def test_line_longer_than_budget_charges_bytes_and_later_completes(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(msg_id="m1", input_tokens=5, output=5)))
        line_size = os.path.getsize(path)
        tiny_budget = 40
        self.assertLess(tiny_budget, line_size)

        first = usage.rollup(root=self.root, max_bytes_per_call=tiny_budget)
        # Charged the full clipped read even though zero complete lines
        # were parsed: this call's I/O was not free.
        self.assertEqual(first["bytes_read"], tiny_budget)
        self.assertTrue(first["partial"])
        self.assertNotIn("sess-1", first["sessions"])

        # A later call with enough budget makes real progress.
        second = usage.rollup(root=self.root, max_bytes_per_call=usage.DEFAULT_MAX_BYTES_PER_CALL)
        self.assertEqual(second["sessions"]["sess-1"]["messages"], 1)
        self.assertEqual(second["sessions"]["sess-1"]["input"], 5)


class ReadCapTest(UsageTestCase):
    """Round 2, Minor 3: the actual read must be capped at what the file
    really has pending, not the full per-call budget. f.read(n) can
    allocate close to n bytes even when far less is available, and with
    a large max_bytes_per_call that risks MemoryError straight out of
    rollup(), which must never raise."""

    def test_read_is_capped_at_remaining_file_bytes_not_full_budget(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(input_tokens=1, output=1)))
        file_size = os.path.getsize(path)

        real_read = usage._read_new_bytes
        captured = []

        def _spy(p, offset, max_bytes):
            captured.append(max_bytes)
            return real_read(p, offset, max_bytes)

        with mock.patch.object(usage, "_read_new_bytes", side_effect=_spy):
            usage.rollup(root=self.root, max_bytes_per_call=usage.DEFAULT_MAX_BYTES_PER_CALL)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], file_size)
        self.assertLess(captured[0], usage.DEFAULT_MAX_BYTES_PER_CALL)


class BytesIOParsingTest(UsageTestCase):
    """Round 2, Minor 4: parsing must iterate the read buffer (via
    io.BytesIO) instead of materializing a full list of every line via
    bytes.splitlines(keepends=True), which peaked at roughly 2x a full
    budget's worth of data. This checks the mechanism (io.BytesIO is
    actually used) rather than measuring RSS/tracemalloc peaks directly,
    since a memory-threshold assertion would be sensitive to allocator
    and Python-version noise; correctness of the parse through the new
    path is checked alongside it."""

    def test_parsing_uses_bytesio_not_a_materialized_line_list(self):
        path = self._session_path()
        rows = "".join(
            _line(_usage_row(msg_id="m%d" % i, request_id="r%d" % i, input_tokens=1, output=1))
            for i in range(20))
        self._write(path, rows)

        with mock.patch("usage.io.BytesIO", wraps=io.BytesIO) as spy:
            data = usage.rollup(root=self.root)
        spy.assert_called_once()
        self.assertEqual(data["sessions"]["sess-1"]["messages"], 20)


class PhantomEmptySessionTest(UsageTestCase):
    """Round 2, Minor 5: files with no usage-bearing record at all (no
    session id could ever be captured, no message was ever counted) must
    not fall back to a filename-stem session id, or several unrelated
    usage-free files sharing a name (several journal.jsonl files, say)
    merge into one meaningless phantom session."""

    def test_usage_free_files_sharing_a_filename_do_not_merge_into_a_phantom_session(self):
        non_usage_line = json.dumps(
            {"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n"
        for i in range(7):
            d = os.path.join(self.proj_dir, "sess-with-journal-%d" % i, "logs")
            os.makedirs(d)
            self._write(os.path.join(d, "journal.jsonl"), non_usage_line)

        data = usage.rollup(root=self.root)
        self.assertNotIn("journal", data["sessions"])
        self.assertEqual(data["sessions"], {})


class BudgetStallRecoveryTest(UsageTestCase):
    """Round 3/4/5: a single line larger than what one call's fair share
    of the budget allows must still eventually complete, once a call
    comes along that can offer it the full max_bytes_per_call. This is
    the HARDER case (round 4 had relaxed it to "sess-a" going idle for
    the retry; round 5's own review flagged that as no longer proving
    anything once the defer branch could starve a stalled file
    indefinitely): "sess-a" keeps having new content, and stays the
    newest-mtime file, on EVERY call, including the one where "sess-b"
    finally completes. Without round 5's stall-priority fix (processing
    a stalled file first, ahead of even the newest file, so it actually
    reaches a call offering the full allowance), "sess-b" would never
    naturally see a full budget as long as "sess-a" keeps competing
    (StarvationByNewerFileTest is the dedicated, more direct
    reproduction of that failure)."""

    def setUp(self):
        super().setUp()
        self.path_a = self._session_path(session_id="sess-a")
        self.path_b = self._session_path(session_id="sess-b")

    def _append_a(self, n):
        line = _line(_usage_row(session_id="sess-a", msg_id="a%d" % n, request_id="ra%d" % n,
                                 input_tokens=1, output=1))
        self._write(self.path_a, line, mode="a")
        os.utime(self.path_a, (2_000_000_100 + n, 2_000_000_100 + n))  # always newest
        return len(line.encode("utf-8"))

    def test_stalled_file_completes_even_while_a_newer_file_stays_continuously_active(self):
        row_b = _usage_row(session_id="sess-b", msg_id="big-b", input_tokens=3, output=3)
        line_b = _line(row_b)
        self._write(self.path_b, line_b)
        size_b = len(line_b.encode("utf-8"))
        os.utime(self.path_b, (2_000_000_000, 2_000_000_000))

        a_chunk = self._append_a(0)

        # Enough for "sess-b" alone, not enough once "sess-a" (newer
        # mtime, so processed first absent stall priority) has already
        # spent a_chunk of it.
        budget = size_b + max(a_chunk // 2, 1)
        self.assertLess(budget - a_chunk, size_b)

        first = usage.rollup(root=self.root, max_bytes_per_call=budget)
        self.assertNotIn("sess-b", first["sessions"])  # stalled: not enough room

        # "sess-a" keeps getting new content, and stays newest, on every
        # subsequent call too: "sess-b" must still land within a handful
        # of calls (a bounded loop, not an exact call count, since the
        # precise timing depends on rotation/ordering details this test
        # shouldn't need to hardcode).
        for n in range(1, 5):
            self._append_a(n)
            data = usage.rollup(root=self.root, max_bytes_per_call=budget)
            if "sess-b" in data["sessions"]:
                self.assertEqual(data["sessions"]["sess-b"]["messages"], 1)
                self.assertEqual(data["sessions"]["sess-b"]["input"], 3)
                return
        self.fail("sess-b never completed while sess-a stayed continuously active")


class StarvationByNewerFileTest(UsageTestCase):
    """Round 5, the Major: a stalled file must not be deferred forever
    just because some newer-mtime file keeps having a little new content
    every call. Before this fix, the defer branch in _update_entry
    triggered whenever ANY earlier (newer-mtime) file read even one
    byte, so a stalled file positioned behind a continuously active
    newer file was deferred on every single call, indefinitely:
    measured at 300 of 300 consecutive calls, stall_count stuck at 1
    forever (a defer never increments it), never counted in "skipped",
    only "partial" -- the same ambiguous signal ordinary clipping also
    sets, so a kill guard reading a frozen, wildly wrong number had no
    way to tell it apart from "the tree's just busy"."""

    def test_stalled_file_is_not_starved_by_a_continuously_active_newer_file(self):
        path_a = self._session_path(session_id="sess-a")
        path_b = self._session_path(session_id="sess-b")

        row_b = _usage_row(session_id="sess-b", msg_id="big-b", input_tokens=5, output=5)
        line_b = _line(row_b)
        self._write(path_b, line_b)
        size_b = len(line_b.encode("utf-8"))
        os.utime(path_b, (2_000_000_000, 2_000_000_000))

        a_chunk_line = _line(_usage_row(
            session_id="sess-a", msg_id="a0", request_id="ra0", input_tokens=1, output=1))
        a_chunk = len(a_chunk_line.encode("utf-8"))

        # Fits the full budget on its own; not what's left once "sess-a"
        # (always newer-mtime, always with new content) goes first.
        budget = size_b + max(a_chunk // 2, 1)
        self.assertLess(budget - a_chunk, size_b)

        landed = False
        for n in range(30):  # comfortably more than needed; nowhere near the measured 300
            line = _line(_usage_row(
                session_id="sess-a", msg_id="a%d" % n, request_id="ra%d" % n,
                input_tokens=1, output=1))
            self._write(path_a, line, mode="a")
            os.utime(path_a, (2_000_000_100 + n, 2_000_000_100 + n))
            data = usage.rollup(root=self.root, max_bytes_per_call=budget)
            if "sess-b" in data["sessions"]:
                landed = True
                self.assertEqual(data["sessions"]["sess-b"]["messages"], 1)
                self.assertEqual(data["sessions"]["sess-b"]["input"], 5)
                break

        self.assertTrue(
            landed, "sess-b was starved indefinitely behind a continuously active newer file")


class PickStallPriorityPathRotatesTest(UsageTestCase):
    """Round 6 (comments-and-tests): the mutation testing behind the
    round 5 review confirmed that pinning _stall_priority_cursor's index
    to a constant (either 0, or len(stalled) - 1) still passed every
    test in this file, meaning the rotation itself had never actually
    been exercised: outcome-based tests (StarvationByNewerFileTest,
    BudgetStallRecoveryTest) only ever have at most one stalled
    candidate competing for the priority slot at a time, so any fixed
    index happens to pick the only file that matters.

    This tests _pick_stall_priority_path directly, against a candidate
    set held deliberately stable (seeded straight into the cache, never
    actually read, so nothing resolves or changes the set between
    calls) specifically so the picks can be attributed to the rotation
    logic alone, not to byte-budget arithmetic deciding who succeeds."""

    def test_rotates_through_every_currently_stalled_candidate(self):
        n = 5
        paths = []
        for i in range(n):
            sid = "sess-%d" % i
            path = self._session_path(session_id=sid)
            self._write(path, _line(_usage_row(session_id=sid, input_tokens=1, output=1)))
            paths.append(path)
            # Seeded directly: a stalled entry as _pick_stall_priority_path
            # actually finds it, without needing a real clipped read to
            # produce one.
            usage._cache[path] = usage._new_entry(PROJECT_DIR, 0)
            usage._cache[path]["stall_count"] = 1

        file_entries = [(p, PROJECT_DIR, 0, 0.0, 0) for p in paths]

        # A true rotation visits every one of the n candidates exactly
        # once across n picks (in ascending sorted-path order, since the
        # cursor starts at 0 after setUp's reset_cache()); a cursor
        # pinned to a constant index -- 0, or len(stalled) - 1 -- would
        # return the very same path all n times instead.
        first_round = [usage._pick_stall_priority_path(file_entries) for _ in range(n)]
        self.assertEqual(set(first_round), set(paths))
        self.assertEqual(len(set(first_round)), n)
        self.assertEqual(first_round, sorted(paths))

        # And it wraps around: a second lap visits the same n candidates
        # again (the set is still stable, nothing here has resolved).
        second_round = [usage._pick_stall_priority_path(file_entries) for _ in range(n)]
        self.assertEqual(set(second_round), set(paths))


class GiveUpAfterMaxStallsTest(UsageTestCase):
    """Round 3: a file whose single line exceeds max_bytes_per_call
    itself (so even a full allowance never completes it) must stop
    being read at all after MAX_CONSECUTIVE_STALLS attempts, and every
    call from then on must report it as skipped and partial rather than
    silently doing nothing."""

    def test_file_that_never_progresses_is_skipped_after_limit_and_partial(self):
        path = self._session_path()
        line = _line(_usage_row(input_tokens=1, output=1))
        self._write(path, line)
        # Small enough that even a full max_bytes_per_call (== this
        # budget, single file) never reaches the line's end.
        max_bytes_per_call = max(len(line.encode("utf-8")) // 4, 20)

        real_read = usage._read_new_bytes
        read_calls = []

        def _spy(p, offset, max_bytes):
            read_calls.append(max_bytes)
            return real_read(p, offset, max_bytes)

        results = []
        with mock.patch.object(usage, "_read_new_bytes", side_effect=_spy):
            for _ in range(usage.MAX_CONSECUTIVE_STALLS + 2):
                results.append(usage.rollup(root=self.root, max_bytes_per_call=max_bytes_per_call))

        # Exactly MAX_CONSECUTIVE_STALLS real read attempts happened,
        # even though rollup() was called more times than that: once the
        # limit was reached, no further disk I/O was spent on this file.
        self.assertEqual(len(read_calls), usage.MAX_CONSECUTIVE_STALLS)

        for data in results[:usage.MAX_CONSECUTIVE_STALLS]:
            self.assertTrue(data["partial"])

        last = results[-1]
        self.assertEqual(last["sessions"], {})
        self.assertGreaterEqual(last["skipped"], 1)
        self.assertTrue(last["partial"])


class OtherFilesUnaffectedByStallTest(UsageTestCase):
    """Round 3/5: other files must not be permanently blocked by a
    misbehaving file, including past the point where it has given up.

    Round 5's stall-priority fix (see StarvationByNewerFileTest) means a
    still-actively-retrying stalled file now goes FIRST each call it's
    prioritized, and since its own retry allowance is the full
    max_bytes_per_call, a failed priority attempt can consume an entire
    call's budget, leaving nothing for "sess-a" THAT call. That's an
    accepted, bounded tradeoff of the round 5 fix: what must still hold
    is that "sess-a" is never stuck forever. Once "sess-b" gives up (its
    line exceeds max_bytes_per_call itself, so it always will, within
    MAX_CONSECUTIVE_STALLS calls) it stops competing for any budget at
    all, and "sess-a" catches back up."""

    def test_other_files_still_get_accounted_while_one_misbehaves(self):
        path_a = self._session_path(session_id="sess-a")
        path_b = self._session_path(session_id="sess-b")

        # Padded well past max_bytes_per_call, by a wide margin: this
        # line must never complete, however much budget it's given.
        line_b = _padded_line(2000, session_id="sess-b", msg_id="big", input_tokens=1, output=1)
        self._write(path_b, line_b)
        max_bytes_per_call = 1000
        self.assertLess(max_bytes_per_call, len(line_b.encode("utf-8")))

        total_iterations = usage.MAX_CONSECUTIVE_STALLS + 2
        data = None
        for i in range(total_iterations):
            self._write(path_a, _line(_usage_row(
                session_id="sess-a", msg_id="a%d" % i, request_id="ra%d" % i,
                input_tokens=1, output=1)), mode="a")
            os.utime(path_a, (2_000_001_000 + i, 2_000_001_000 + i))
            data = usage.rollup(root=self.root, max_bytes_per_call=max_bytes_per_call)
            self.assertNotIn("sess-b", data["sessions"])

        # By the last call, "sess-b" has long since given up (it stalls
        # out within MAX_CONSECUTIVE_STALLS calls every time, and stops
        # spending any further budget once it does), so "sess-a" has had
        # the chance to catch up on every line it ever appended, even
        # the ones skipped on calls where "sess-b" was still consuming
        # the whole budget trying (and failing) to complete.
        self.assertEqual(data["sessions"]["sess-a"]["messages"], total_iterations)


class StallCounterResetTest(UsageTestCase):
    """Round 3: a file that stalls and then makes real progress must have
    its stall counter reset, not merely happen to still work because
    nothing ever re-checks it."""

    def test_counter_resets_on_successful_progress(self):
        path_a = self._session_path(session_id="sess-a")
        path_b = self._session_path(session_id="sess-b")

        row_b = _usage_row(session_id="sess-b", msg_id="b1", request_id="rb1",
                            input_tokens=1, output=1)
        line_b = _line(row_b)
        self._write(path_b, line_b)
        size_b = len(line_b.encode("utf-8"))

        a_line = _line(_usage_row(session_id="sess-a", msg_id="a0", request_id="ra0",
                                   input_tokens=1, output=1))
        a_size = len(a_line.encode("utf-8"))
        self._write(path_a, a_line)
        os.utime(path_b, (2_000_000_000, 2_000_000_000))
        os.utime(path_a, (2_000_000_100, 2_000_000_100))

        budget = size_b + max(a_size // 2, 1)

        first = usage.rollup(root=self.root, max_bytes_per_call=budget)
        self.assertNotIn("sess-b", first["sessions"])
        self.assertEqual(usage._cache[path_b]["stall_count"], 1)

        # "sess-a" is now unchanged (nothing appended), so it costs
        # nothing this call: "sess-b" gets the full budget and completes,
        # which must reset the counter.
        second = usage.rollup(root=self.root, max_bytes_per_call=budget)
        self.assertIn("sess-b", second["sessions"])
        self.assertEqual(usage._cache[path_b]["stall_count"], 0)


class AbandonmentIsBudgetRelativeTest(UsageTestCase):
    """Round 4, the Major: giving up on a file must be relative to the
    budget that failed, not permanent. Round 3's design gave up until a
    rotation or truncation reset the entry, which the round 4 review
    proved was frequently unreachable (abandonment at offset 0, the
    common case, can never satisfy the size-based reset check, and
    delete-and-recreate is not guaranteed to change the inode either) --
    a live session's number could freeze forever with no way out. Giving
    up is now scoped to "no call since has offered more than what
    already failed", so a later call with a bigger budget always gets a
    real chance, however many times MAX_CONSECUTIVE_STALLS has been
    exceeded."""

    def _make_pathological(self):
        path = self._session_path()
        line = _line(_usage_row(input_tokens=7, output=7))
        self._write(path, line)
        line_size = len(line.encode("utf-8"))
        small_budget = max(line_size // 4, 20)
        return path, line_size, small_budget

    def _drive_to_abandonment(self, small_budget):
        for _ in range(usage.MAX_CONSECUTIVE_STALLS):
            usage.rollup(root=self.root, max_bytes_per_call=small_budget)
        confirm = usage.rollup(root=self.root, max_bytes_per_call=small_budget)
        self.assertEqual(confirm["sessions"], {})
        self.assertGreaterEqual(confirm["skipped"], 1)
        self.assertTrue(confirm["partial"])

    def test_abandoned_file_is_retried_under_a_larger_budget(self):
        path, line_size, small_budget = self._make_pathological()
        self._drive_to_abandonment(small_budget)

        big_budget = line_size + 200  # comfortably bigger than what failed
        self.assertGreater(big_budget, small_budget)
        data = usage.rollup(root=self.root, max_bytes_per_call=big_budget)
        self.assertIn("sess-1", data["sessions"])
        self.assertEqual(data["sessions"]["sess-1"]["input"], 7)
        self.assertEqual(data["sessions"]["sess-1"]["output"], 7)

    def test_abandoned_file_is_not_retried_under_the_same_or_a_smaller_budget(self):
        path, line_size, small_budget = self._make_pathological()
        self._drive_to_abandonment(small_budget)

        real_read = usage._read_new_bytes
        read_calls = []

        def _spy(p, offset, max_bytes):
            read_calls.append(max_bytes)
            return real_read(p, offset, max_bytes)

        with mock.patch.object(usage, "_read_new_bytes", side_effect=_spy):
            same = usage.rollup(root=self.root, max_bytes_per_call=small_budget)
            smaller = usage.rollup(root=self.root, max_bytes_per_call=max(small_budget // 2, 1))

        self.assertEqual(read_calls, [])  # no I/O spent on either call
        self.assertEqual(same["sessions"], {})
        self.assertTrue(same["partial"])
        self.assertEqual(smaller["sessions"], {})
        self.assertTrue(smaller["partial"])


class RetryNeverExceedsCallBudgetTest(UsageTestCase):
    """Round 4, Minor: a stalled file's retry must never push a call's
    total bytes_read past max_bytes_per_call, even when another file
    competes for the same call's budget on every single call (the
    scenario that measured a call reading 1.98x its documented cap
    before this fix: the allowance granted for a retry used to ignore
    budget already spent by other files in the same call)."""

    def test_bytes_read_never_exceeds_max_bytes_per_call(self):
        path_a = self._session_path(session_id="sess-a")
        path_b = self._session_path(session_id="sess-b")

        row_b = _usage_row(session_id="sess-b", msg_id="big-b", input_tokens=1, output=1)
        line_b = _line(row_b)
        self._write(path_b, line_b)
        size_b = len(line_b.encode("utf-8"))
        budget = size_b + 40

        for i in range(usage.MAX_CONSECUTIVE_STALLS + 2):
            a_line = _line(_usage_row(
                session_id="sess-a", msg_id="a%d" % i, request_id="ra%d" % i,
                input_tokens=1, output=1))
            self._write(path_a, a_line, mode="a")
            os.utime(path_a, (2_000_002_000 + i, 2_000_002_000 + i))  # always newer: goes first
            data = usage.rollup(root=self.root, max_bytes_per_call=budget)
            self.assertLessEqual(data["bytes_read"], budget)


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


class DailyByProjectTest(UsageTestCase):
    """daily_by_project: same per-day totals as daily, split out by
    project instead of summed across all of them."""

    def test_groups_by_day_and_project_independently(self):
        proj2 = os.path.join(self.root, "-tmp-otherproj")
        os.makedirs(proj2)
        path1 = self._session_path(session_id="s-in-proj1")
        self._write(path1, _line(_usage_row(
            session_id="s-in-proj1", msg_id="m1",
            ts="2026-09-06T08:00:00.000Z", input_tokens=1, output=1)))
        path2 = os.path.join(proj2, "s-in-proj2.jsonl")
        self._write(path2, _line(_usage_row(
            session_id="s-in-proj2", msg_id="m2",
            ts="2026-09-06T09:00:00.000Z", input_tokens=2, output=2)))

        data = usage.rollup(root=self.root)
        rows = {(r["day"], r["project"]): r for r in data["daily_by_project"]}

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[("2026-09-06", PROJECT_DIR)]["input"], 1)
        self.assertEqual(rows[("2026-09-06", PROJECT_DIR)]["output"], 1)
        self.assertEqual(rows[("2026-09-06", PROJECT_DIR)]["effective"],
                          usage.effective(input=1, output=1))
        self.assertEqual(rows[("2026-09-06", "-tmp-otherproj")]["input"], 2)
        self.assertEqual(rows[("2026-09-06", "-tmp-otherproj")]["output"], 2)

    def test_two_sessions_same_project_same_day_sum_together(self):
        path1 = self._session_path(session_id="s1")
        self._write(path1, _line(_usage_row(
            session_id="s1", msg_id="m1", ts="2026-09-06T08:00:00.000Z",
            input_tokens=1, output=1)))
        path2 = self._session_path(session_id="s2")
        self._write(path2, _line(_usage_row(
            session_id="s2", msg_id="m2", ts="2026-09-06T09:00:00.000Z",
            input_tokens=4, output=4)))

        data = usage.rollup(root=self.root)
        rows = [r for r in data["daily_by_project"]
                if r["day"] == "2026-09-06" and r["project"] == PROJECT_DIR]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input"], 5)
        self.assertEqual(rows[0]["output"], 5)

    def test_sparse_no_entry_for_a_day_never_seen(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(ts="2026-09-06T08:00:00.000Z", input_tokens=1, output=1)))
        data = usage.rollup(root=self.root)
        days_seen = {r["day"] for r in data["daily_by_project"]}
        self.assertEqual(days_seen, {"2026-09-06"})

    def test_honours_days_window_like_daily(self):
        path = self._session_path()
        recent_row = _usage_row(msg_id="m-recent", ts="2026-09-06T08:00:00.000Z",
                                 input_tokens=1, output=1)
        old_row = _usage_row(msg_id="m-old", ts="2026-06-01T00:00:00.000Z",
                              input_tokens=2, output=2)
        self._write(path, _line(recent_row) + _line(old_row))
        fixed_now = datetime.datetime(2026, 9, 7, tzinfo=datetime.timezone.utc).timestamp()

        data = usage.rollup(root=self.root, now_fn=lambda: fixed_now, days=30)
        days_seen = {r["day"] for r in data["daily_by_project"]}
        self.assertIn("2026-09-06", days_seen)
        self.assertNotIn("2026-06-01", days_seen)

    def test_days_30_yields_exactly_30_distinct_days(self):
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
        days_seen = {r["day"] for r in data["daily_by_project"]}
        self.assertEqual(len(days_seen), 30)

    def test_subagent_usage_rolls_into_parents_project_not_a_subagents_project(self):
        parent_id = "sess-parent"
        parent_path = self._session_path(session_id=parent_id)
        self._write(parent_path, _line(_usage_row(
            session_id=parent_id, msg_id="p1", ts="2026-09-06T08:00:00.000Z",
            input_tokens=1, output=1)))
        sub_path = self._subagent_path(parent_id, "agent-abc")
        self._write(sub_path, _line(_usage_row(
            session_id=parent_id, msg_id="a1", ts="2026-09-06T09:00:00.000Z",
            input_tokens=2, output=2)))

        data = usage.rollup(root=self.root)
        rows = [r for r in data["daily_by_project"] if r["day"] == "2026-09-06"]

        self.assertEqual(len(rows), 1)  # parent + subagent merge into ONE project row
        self.assertEqual(rows[0]["project"], PROJECT_DIR)
        self.assertEqual(rows[0]["input"], 3)
        self.assertEqual(rows[0]["output"], 3)

    def test_summed_across_projects_matches_daily(self):
        # daily must stay exactly as it is: summing daily_by_project's
        # rows for one day across every project must reproduce daily's
        # own total for that day.
        proj2 = os.path.join(self.root, "-tmp-otherproj")
        os.makedirs(proj2)
        path1 = self._session_path(session_id="s-in-proj1")
        self._write(path1, _line(_usage_row(
            session_id="s-in-proj1", msg_id="m1",
            ts="2026-09-06T08:00:00.000Z", input_tokens=1, cache_read=10,
            cache_write=100, output=1)))
        path2 = os.path.join(proj2, "s-in-proj2.jsonl")
        self._write(path2, _line(_usage_row(
            session_id="s-in-proj2", msg_id="m2",
            ts="2026-09-06T09:00:00.000Z", input_tokens=2, cache_read=20,
            cache_write=200, output=2)))

        data = usage.rollup(root=self.root)
        rows = [r for r in data["daily_by_project"] if r["day"] == "2026-09-06"]
        summed = {
            "input": sum(r["input"] for r in rows),
            "cache_read": sum(r["cache_read"] for r in rows),
            "cache_write": sum(r["cache_write"] for r in rows),
            "output": sum(r["output"] for r in rows),
            "effective": sum(r["effective"] for r in rows),
        }
        self.assertEqual(summed["input"], data["daily"]["2026-09-06"]["input"])
        self.assertEqual(summed["cache_read"], data["daily"]["2026-09-06"]["cache_read"])
        self.assertEqual(summed["cache_write"], data["daily"]["2026-09-06"]["cache_write"])
        self.assertEqual(summed["output"], data["daily"]["2026-09-06"]["output"])
        self.assertEqual(summed["effective"], data["daily"]["2026-09-06"]["effective"])

    def test_unparsable_timestamp_never_produces_a_row(self):
        path = self._session_path()
        self._write(path, _line(_usage_row(ts="not-a-timestamp", input_tokens=5, output=1)))
        data = usage.rollup(root=self.root)
        self.assertEqual(data["daily_by_project"], [])


if __name__ == "__main__":
    unittest.main()
