"""Tests for schedules.py's storage layer: atomic writes, rolling backup,
load validation, and the manual-task (cron: null) data model."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedules


class SaveSchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sched_file = os.path.join(self.tmpdir, "schedules.json")
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = self.sched_file

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_creates_file_mode_0600(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        mode = stat.S_IMODE(os.stat(self.sched_file).st_mode)
        self.assertEqual(mode, 0o600)
        with open(self.sched_file) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_save_writes_rolling_backup_of_previous_contents(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        schedules.save_schedules([{"id": "b", "name": "B"}])
        bak_path = self.sched_file + ".bak"
        self.assertTrue(os.path.isfile(bak_path))
        with open(bak_path) as f:
            self.assertEqual(json.load(f), [{"id": "a", "name": "A"}])

    def test_backup_is_chmoded_0600_even_if_original_was_looser(self):
        # Simulate upgrading from an older schedules.json that predates the
        # 0600 write mode.
        with open(self.sched_file, "w") as f:
            json.dump([{"id": "a", "name": "A"}], f)
        os.chmod(self.sched_file, 0o644)

        schedules.save_schedules([{"id": "b", "name": "B"}])

        bak_path = self.sched_file + ".bak"
        mode = stat.S_IMODE(os.stat(bak_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_no_backup_written_on_first_ever_save(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        self.assertFalse(os.path.isfile(self.sched_file + ".bak"))

    def test_save_leaves_no_tmp_files_behind(self):
        schedules.save_schedules([{"id": "a", "name": "A"}])
        leftovers = [f for f in os.listdir(self.tmpdir) if ".tmp" in f]
        self.assertEqual(leftovers, [])


class LoadSchedulesValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sched_file = os.path.join(self.tmpdir, "schedules.json")
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = self.sched_file

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_raw(self, text):
        with open(self.sched_file, "w") as f:
            f.write(text)

    def test_missing_file_is_not_an_error(self):
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNone(schedules.LAST_LOAD_ERROR)

    def test_valid_file_clears_last_load_error(self):
        self._write_raw('[{"id": "a", "name": "A", "cron": null}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [{"id": "a", "name": "A", "cron": None}])
        self.assertIsNone(schedules.LAST_LOAD_ERROR)

    def test_corrupt_json_sets_last_load_error_and_returns_empty(self):
        self._write_raw('[{"id": "a"}]]')  # the real-world failure mode
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_non_list_top_level_sets_last_load_error(self):
        self._write_raw('{"not": "a list"}')
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_entry_missing_id_is_dropped_but_others_survive(self):
        self._write_raw('[{"name": "no id"}, {"id": "b", "name": "B"}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [{"id": "b", "name": "B"}])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_entry_with_non_string_cron_is_dropped(self):
        self._write_raw('[{"id": "a", "name": "A", "cron": 5}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)


class ManualTaskCronTest(unittest.TestCase):
    """schedules.py stores whatever it is given, with no cron-specific
    logic, so these already pass with zero production code changes - this
    locks the behavior in against future refactors."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_schedule_preserves_null_cron(self):
        s = schedules.create_schedule({"name": "ad hoc", "cron": None, "prompt": "hi"})
        self.assertIsNone(s["cron"])

    def test_update_schedule_can_set_cron_to_null(self):
        s = schedules.create_schedule({"name": "was cron'd", "cron": "0 9 * * *"})
        updated = schedules.update_schedule(s["id"], {"cron": None})
        self.assertIsNone(updated["cron"])

    def test_update_schedule_can_set_cron_back_to_a_string(self):
        s = schedules.create_schedule({"name": "manual", "cron": None})
        updated = schedules.update_schedule(s["id"], {"cron": "0 9 * * *"})
        self.assertEqual(updated["cron"], "0 9 * * *")


class ConcurrencyFieldTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_schedule_defaults_concurrency_to_skip(self):
        s = schedules.create_schedule({"name": "task"})
        self.assertEqual(s["concurrency"], "skip")

    def test_create_schedule_preserves_explicit_kill(self):
        s = schedules.create_schedule({"name": "task", "concurrency": "kill"})
        self.assertEqual(s["concurrency"], "kill")

    def test_update_schedule_can_change_concurrency(self):
        s = schedules.create_schedule({"name": "task"})
        updated = schedules.update_schedule(s["id"], {"concurrency": "kill"})
        self.assertEqual(updated["concurrency"], "kill")


class ValidateTriggerTest(unittest.TestCase):
    def test_none_is_valid(self):
        self.assertIsNone(schedules.validate_trigger(None))

    def test_valid_minimal_trigger(self):
        self.assertIsNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "five_hour"}))

    def test_valid_full_trigger(self):
        self.assertIsNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "seven_day",
             "delay_minutes": 30, "catch_up": "none"}))

    def test_non_dict_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger("limit_reset"))

    def test_unknown_kind_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger(
            {"kind": "something_else", "window": "five_hour"}))

    def test_unknown_window_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "three_hour"}))

    def test_delay_minutes_over_cap_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "five_hour", "delay_minutes": 241}))

    def test_delay_minutes_at_cap_is_accepted(self):
        self.assertIsNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "five_hour", "delay_minutes": 240}))

    def test_negative_delay_minutes_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "five_hour", "delay_minutes": -1}))

    def test_non_numeric_delay_minutes_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "five_hour", "delay_minutes": "soon"}))

    def test_unknown_catch_up_is_rejected(self):
        self.assertIsNotNone(schedules.validate_trigger(
            {"kind": "limit_reset", "window": "five_hour", "catch_up": "everything"}))

    def test_client_supplied_marker_is_rejected(self):
        # Fix round 1 (Important 4, task-l3-findings-r1.md): the marker
        # is server state (the firing rule's own bookkeeping, written
        # only by scheduler.py's internal update_schedule() calls) and
        # must never be settable through the API - this hub sits on the
        # internet behind a password, so the API is the trust boundary.
        # Before this fix a client could include last_seen_resets_at in
        # a POST /schedules/update body and force an immediate fire.
        err = schedules.validate_trigger({
            "kind": "limit_reset", "window": "five_hour",
            "last_seen_resets_at": "2020-01-01T00:00:00Z",
        })
        self.assertIsNotNone(err)
        self.assertIn("last_seen_resets_at", err)

    def test_unknown_field_is_rejected(self):
        err = schedules.validate_trigger({
            "kind": "limit_reset", "window": "five_hour", "extra_field": 1,
        })
        self.assertIsNotNone(err)
        self.assertIn("extra_field", err)


class IsoToEpochTest(unittest.TestCase):
    def test_z_suffix_parses(self):
        self.assertIsNotNone(schedules.iso_to_epoch("2026-01-01T00:00:00Z"))

    def test_naive_treated_as_utc(self):
        a = schedules.iso_to_epoch("2026-01-01T00:00:00Z")
        b = schedules.iso_to_epoch("2026-01-01T00:00:00")
        self.assertEqual(a, b)

    def test_none_returns_none(self):
        self.assertIsNone(schedules.iso_to_epoch(None))

    def test_garbage_returns_none(self):
        self.assertIsNone(schedules.iso_to_epoch("not a timestamp"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(schedules.iso_to_epoch(""))


class TriggerCrudTest(unittest.TestCase):
    """create_schedule / update_schedule's handling of `trigger`: storage
    shape, cron/trigger mutual exclusivity, and how the last_seen_resets_at
    marker is kept or reset across an update."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = os.path.join(self.tmpdir, "schedules.json")

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_without_trigger_is_unaffected(self):
        s = schedules.create_schedule({"name": "cron task", "cron": "0 9 * * *"})
        self.assertIsNone(s["trigger"])
        self.assertEqual(s["cron"], "0 9 * * *")

    def test_create_with_trigger_forces_cron_null(self):
        s = schedules.create_schedule({
            "name": "reset task", "cron": "0 9 * * *",
            "trigger": {"kind": "limit_reset", "window": "five_hour"},
        })
        self.assertIsNone(s["cron"])
        self.assertEqual(s["trigger"]["kind"], "limit_reset")
        self.assertEqual(s["trigger"]["window"], "five_hour")

    def test_create_defaults_delay_and_catch_up(self):
        s = schedules.create_schedule({
            "name": "reset task",
            "trigger": {"kind": "limit_reset", "window": "seven_day"},
        })
        self.assertEqual(s["trigger"]["delay_minutes"], 0)
        self.assertEqual(s["trigger"]["catch_up"], "latest")

    def test_create_never_trusts_a_client_supplied_marker(self):
        s = schedules.create_schedule({
            "name": "reset task",
            "trigger": {"kind": "limit_reset", "window": "five_hour",
                        "last_seen_resets_at": "2020-01-01T00:00:00Z"},
        })
        self.assertIsNone(s["trigger"]["last_seen_resets_at"])

    def test_update_editing_delay_keeps_marker(self):
        s = schedules.create_schedule({
            "name": "reset task",
            "trigger": {"kind": "limit_reset", "window": "five_hour"},
        })
        # Simulate the scheduler having already seeded a marker.
        schedules.update_schedule(s["id"], {
            "trigger": {"kind": "limit_reset", "window": "five_hour",
                        "last_seen_resets_at": "2026-01-01T00:00:00Z"},
        })
        # A user-facing edit (no last_seen_resets_at key in the payload,
        # matching what the API layer actually sends) changes delay only.
        updated = schedules.update_schedule(s["id"], {
            "trigger": {"kind": "limit_reset", "window": "five_hour", "delay_minutes": 15},
        })
        self.assertEqual(updated["trigger"]["delay_minutes"], 15)
        self.assertEqual(updated["trigger"]["last_seen_resets_at"], "2026-01-01T00:00:00Z")

    def test_update_changing_window_resets_marker(self):
        s = schedules.create_schedule({
            "name": "reset task",
            "trigger": {"kind": "limit_reset", "window": "five_hour"},
        })
        schedules.update_schedule(s["id"], {
            "trigger": {"kind": "limit_reset", "window": "five_hour",
                        "last_seen_resets_at": "2026-01-01T00:00:00Z"},
        })
        updated = schedules.update_schedule(s["id"], {
            "trigger": {"kind": "limit_reset", "window": "seven_day"},
        })
        self.assertIsNone(updated["trigger"]["last_seen_resets_at"])

    def test_update_scheduler_marker_write_is_trusted_verbatim(self):
        s = schedules.create_schedule({
            "name": "reset task",
            "trigger": {"kind": "limit_reset", "window": "five_hour"},
        })
        updated = schedules.update_schedule(s["id"], {
            "trigger": {"kind": "limit_reset", "window": "five_hour",
                        "last_seen_resets_at": "2026-03-01T00:00:00Z"},
        })
        self.assertEqual(updated["trigger"]["last_seen_resets_at"], "2026-03-01T00:00:00Z")

    def test_update_setting_trigger_forces_cron_null_even_if_cron_also_sent(self):
        s = schedules.create_schedule({"name": "cron task", "cron": "0 9 * * *"})
        updated = schedules.update_schedule(s["id"], {
            "cron": "0 12 * * *",
            "trigger": {"kind": "limit_reset", "window": "five_hour"},
        })
        self.assertIsNone(updated["cron"])

    def test_update_clearing_trigger_restores_cron_control(self):
        s = schedules.create_schedule({
            "name": "reset task",
            "trigger": {"kind": "limit_reset", "window": "five_hour"},
        })
        updated = schedules.update_schedule(s["id"], {"trigger": None, "cron": "0 9 * * *"})
        self.assertIsNone(updated["trigger"])
        self.assertEqual(updated["cron"], "0 9 * * *")


class TriggerLoadValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sched_file = os.path.join(self.tmpdir, "schedules.json")
        self._orig = schedules.SCHEDULES_FILE
        schedules.SCHEDULES_FILE = self.sched_file

    def tearDown(self):
        schedules.SCHEDULES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_raw(self, text):
        with open(self.sched_file, "w") as f:
            f.write(text)

    def test_null_trigger_round_trips(self):
        self._write_raw('[{"id": "a", "name": "A", "cron": null, "trigger": null}]')
        result = schedules.load_schedules()
        self.assertEqual(result[0]["trigger"], None)

    def test_valid_trigger_round_trips(self):
        self._write_raw(
            '[{"id": "a", "name": "A", "cron": null, "trigger": '
            '{"kind": "limit_reset", "window": "five_hour", "delay_minutes": 10, '
            '"catch_up": "latest", "last_seen_resets_at": null}}]'
        )
        result = schedules.load_schedules()
        self.assertEqual(result[0]["trigger"]["window"], "five_hour")

    def test_entry_missing_trigger_key_survives_unaffected(self):
        # A pre-existing task from before this feature never had a
        # `trigger` key at all - loading it must not require or invent one.
        self._write_raw('[{"id": "a", "name": "A", "cron": "0 9 * * *"}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [{"id": "a", "name": "A", "cron": "0 9 * * *"}])
        self.assertNotIn("trigger", result[0])

    def test_non_object_trigger_sanitizes_field_but_keeps_the_entry(self):
        # Fix round 1 (Minor, task-l3-findings-r1.md): this used to drop
        # the WHOLE entry, which meant the next unrelated write (any
        # other schedule's create/update/delete) would silently and
        # permanently erase it from disk via save_schedules() persisting
        # exactly the shrunk `valid` list. Given this file's own history
        # (silently corrupt for three months), only the bad field is
        # dropped now - the entry survives with trigger: None (falls
        # back to whatever `cron` says), and the problem is still
        # surfaced via LAST_LOAD_ERROR.
        self._write_raw('[{"id": "a", "name": "A", "cron": "0 9 * * *", '
                         '"trigger": "not an object"}]')
        result = schedules.load_schedules()
        self.assertEqual(result, [{"id": "a", "name": "A", "cron": "0 9 * * *", "trigger": None}])
        self.assertIsNotNone(schedules.LAST_LOAD_ERROR)

    def test_sanitized_trigger_survives_an_unrelated_write(self):
        # The actual harm the previous behavior caused: a routine write
        # for a DIFFERENT schedule used to permanently erase this one.
        self._write_raw(
            '[{"id": "a", "name": "A", "trigger": "not an object"}, '
            '{"id": "b", "name": "B", "cron": null}]'
        )
        schedules.update_schedule("b", {"name": "B renamed"})
        result = schedules.load_schedules()
        ids = {e["id"] for e in result}
        self.assertIn("a", ids)

    def test_semantically_bad_trigger_still_loads(self):
        # Deep validation (unknown kind/window) is deliberately NOT done
        # at load time - the task must stay visible so the scheduler's
        # runtime check can disable it with an explanatory history entry
        # rather than it silently vanishing from the list.
        self._write_raw(
            '[{"id": "a", "name": "A", "trigger": {"kind": "limit_reset", "window": "bogus"}}]'
        )
        result = schedules.load_schedules()
        self.assertEqual(len(result), 1)
        self.assertIsNone(schedules.LAST_LOAD_ERROR)


if __name__ == "__main__":
    unittest.main()
