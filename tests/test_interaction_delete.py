import contextlib
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from career_crm.cli import build_parser
from career_crm.core import (
    SCHEMAS,
    add_person,
    add_task,
    backup_operation,
    complete_task,
    delete_interaction,
    ensure_data_dir,
    log_event,
    operation_journal_path,
    read_rows,
    replace_bytes_atomic,
    rows_to_csv_bytes,
    validate,
    write_operation_journal,
    write_rows,
)


class CoffeeChatDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data"
        ensure_data_dir(self.data)
        self.person = add_person(self.data, {"name": "Avery Kim"})

    def tearDown(self):
        self.temp.cleanup()

    def _log_chat(self, days_from_today, created_at):
        chat_date = (date.today() + timedelta(days=days_from_today)).isoformat()
        with patch("career_crm.core.now_iso", return_value=created_at):
            interaction, _ = log_event(
                self.data,
                "coffee_chat",
                person_id=self.person["person_id"],
                event_date=chat_date,
                notes="2:00 PM ET · Google Meet",
            )
        generated = {
            row["type"]: row
            for row in read_rows(self.data, "tasks")
            if row["person_id"] == self.person["person_id"]
            and row["created_at"] == created_at
            and row["type"] in {"coffee_chat", "chat_reminder"}
        }
        self.assertEqual(set(generated), {"coffee_chat", "chat_reminder"})
        return interaction, generated

    def _tree_snapshot(self):
        snapshot = {".": ("dir", self.data.stat().st_mtime_ns)}
        for path in sorted(self.data.rglob("*")):
            relative = path.relative_to(self.data).as_posix()
            if path.is_dir():
                snapshot[relative] = ("dir", path.stat().st_mtime_ns)
            else:
                snapshot[relative] = ("file", path.stat().st_mtime_ns, path.read_bytes())
        return snapshot

    def _csv_snapshot(self):
        return {
            name: (self.data / f"{name}.csv").read_bytes()
            for name in SCHEMAS
        }

    def _operation_backup_dirs(self):
        backup_root = self.data / "backups"
        if not backup_root.exists():
            return set()
        return {path for path in backup_root.iterdir() if path.is_dir()}

    def test_dry_run_is_strictly_read_only(self):
        target, target_tasks = self._log_chat(10, "2026-08-10T09:00:00")
        self._log_chat(2, "2026-08-10T09:01:00")
        before = self._tree_snapshot()

        plan = delete_interaction(self.data, target["interaction_id"], confirm=False)

        self.assertFalse(plan["deleted"])
        self.assertEqual(
            {row["task_id"] for row in plan["tasks"]},
            {row["task_id"] for row in target_tasks.values()},
        )
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(self._tree_snapshot(), before)

    def test_delete_noncurrent_chat_removes_only_exact_generated_tasks_and_preserves_people_bytes(self):
        target, target_tasks = self._log_chat(10, "2026-08-10T09:00:00")
        keeper, keeper_tasks = self._log_chat(2, "2026-08-10T09:01:00")
        people_before = (self.data / "people.csv").read_bytes()
        backup_dirs_before = self._operation_backup_dirs()

        result = delete_interaction(self.data, target["interaction_id"], confirm=True)

        self.assertTrue(result["deleted"])
        self.assertEqual((self.data / "people.csv").read_bytes(), people_before)
        self.assertEqual(
            {row["interaction_id"] for row in read_rows(self.data, "interactions")},
            {keeper["interaction_id"]},
        )
        self.assertEqual(
            {row["task_id"] for row in read_rows(self.data, "tasks")},
            {row["task_id"] for row in keeper_tasks.values()},
        )
        self.assertTrue(
            {row["task_id"] for row in target_tasks.values()}.isdisjoint(
                {row["task_id"] for row in read_rows(self.data, "tasks")}
            )
        )
        new_backup_dirs = self._operation_backup_dirs() - backup_dirs_before
        self.assertEqual(len(new_backup_dirs), 1)
        self.assertEqual(
            {path.name for path in next(iter(new_backup_dirs)).iterdir()},
            {"interactions.csv", "tasks.csv"},
        )
        self.assertEqual(validate(self.data), [])

    def test_delete_current_chat_reconciles_person_to_nearest_remaining_future_chat(self):
        remaining, _ = self._log_chat(3, "2026-08-10T09:00:00")
        current, _ = self._log_chat(9, "2026-08-10T09:01:00")

        result = delete_interaction(self.data, current["interaction_id"], confirm=True)

        self.assertEqual(
            result["person_changes"],
            {
                "status": "chat_scheduled",
                "next_action": "Prepare for coffee chat",
                "next_action_date": remaining["date"],
            },
        )
        person = read_rows(self.data, "people")[0]
        self.assertEqual(person["status"], "chat_scheduled")
        self.assertEqual(person["next_action"], "Prepare for coffee chat")
        self.assertEqual(person["next_action_date"], remaining["date"])

    def test_generated_tasks_share_interaction_timestamp_across_clock_rollover(self):
        chat_date = (date.today() + timedelta(days=10)).isoformat()
        with patch(
            "career_crm.core.now_iso",
            side_effect=["2026-08-10T09:00:00", "2026-08-10T09:00:01"],
        ):
            target, _ = log_event(
                self.data,
                "coffee_chat",
                person_id=self.person["person_id"],
                event_date=chat_date,
            )
        self._log_chat(2, "2026-08-10T09:01:00")

        plan = delete_interaction(self.data, target["interaction_id"], confirm=False)

        self.assertEqual(plan["blockers"], [])
        self.assertEqual(len(plan["tasks"]), 2)
        matching_tasks = [
            row for row in read_rows(self.data, "tasks")
            if row["task_id"] in {item["task_id"] for item in plan["tasks"]}
        ]
        self.assertEqual(
            {row["created_at"] for row in matching_tasks},
            {target["created_at"]},
        )

    def test_restore_fields_are_rejected_when_another_upcoming_chat_remains(self):
        self._log_chat(3, "2026-08-10T09:00:00")
        current, _ = self._log_chat(9, "2026-08-10T09:01:00")
        before = self._tree_snapshot()

        plan = delete_interaction(
            self.data,
            current["interaction_id"],
            confirm=False,
            restore_status="closed",
        )

        self.assertTrue(plan["blockers"])
        self.assertIn("last upcoming chat", " ".join(plan["blockers"]))
        with self.assertRaisesRegex(ValueError, "Deletion blocked"):
            delete_interaction(
                self.data,
                current["interaction_id"],
                confirm=True,
                restore_status="closed",
            )
        self.assertEqual(self._tree_snapshot(), before)

    def test_last_owning_chat_without_explicit_restore_aborts_without_writes(self):
        target, _ = self._log_chat(5, "2026-08-10T09:00:00")
        before = self._tree_snapshot()

        plan = delete_interaction(self.data, target["interaction_id"], confirm=False)

        self.assertTrue(plan["blockers"])
        self.assertEqual(self._tree_snapshot(), before)
        with self.assertRaisesRegex(ValueError, "Deletion blocked"):
            delete_interaction(self.data, target["interaction_id"], confirm=True)
        self.assertEqual(self._tree_snapshot(), before)

    def test_missing_generated_task_blocks_deletion(self):
        target, target_tasks = self._log_chat(10, "2026-08-10T09:00:00")
        self._log_chat(2, "2026-08-10T09:01:00")
        reminder_id = target_tasks["chat_reminder"]["task_id"]
        write_rows(
            self.data,
            "tasks",
            [row for row in read_rows(self.data, "tasks") if row["task_id"] != reminder_id],
        )
        before = self._tree_snapshot()

        plan = delete_interaction(self.data, target["interaction_id"], confirm=False)

        blocker_text = " ".join(plan["blockers"]).lower()
        self.assertIn("chat_reminder", blocker_text)
        self.assertIn("task", blocker_text)
        with self.assertRaisesRegex(ValueError, "Deletion blocked"):
            delete_interaction(self.data, target["interaction_id"], confirm=True)
        self.assertEqual(self._tree_snapshot(), before)

    def test_ambiguous_generated_task_blocks_deletion(self):
        target, target_tasks = self._log_chat(10, "2026-08-10T09:00:00")
        self._log_chat(2, "2026-08-10T09:01:00")
        duplicate = dict(target_tasks["coffee_chat"])
        duplicate["task_id"] = "tsk_duplicate"
        tasks = read_rows(self.data, "tasks")
        tasks.append(duplicate)
        write_rows(self.data, "tasks", tasks)
        before = self._tree_snapshot()

        plan = delete_interaction(self.data, target["interaction_id"], confirm=False)

        blocker_text = " ".join(plan["blockers"]).lower()
        self.assertIn("coffee_chat", blocker_text)
        self.assertTrue("multiple" in blocker_text or "ambiguous" in blocker_text)
        with self.assertRaisesRegex(ValueError, "Deletion blocked"):
            delete_interaction(self.data, target["interaction_id"], confirm=True)
        self.assertEqual(self._tree_snapshot(), before)

    def test_unrelated_same_person_and_date_task_is_preserved(self):
        target, target_tasks = self._log_chat(10, "2026-08-10T09:00:00")
        keeper, keeper_tasks = self._log_chat(2, "2026-08-10T09:01:00")
        unrelated = add_task(self.data, {
            "type": "coffee_chat",
            "due_date": target["date"],
            "person_id": self.person["person_id"],
            "title": "Personal preparation notes for Avery",
            "priority": "low",
            "note": "User-created task; not generated by the interaction.",
        })

        delete_interaction(self.data, target["interaction_id"], confirm=True)

        remaining_ids = {row["task_id"] for row in read_rows(self.data, "tasks")}
        self.assertIn(unrelated["task_id"], remaining_ids)
        self.assertTrue(
            {row["task_id"] for row in target_tasks.values()}.isdisjoint(remaining_ids)
        )
        self.assertTrue(
            {row["task_id"] for row in keeper_tasks.values()}.issubset(remaining_ids)
        )
        self.assertIn(
            keeper["interaction_id"],
            {row["interaction_id"] for row in read_rows(self.data, "interactions")},
        )

    def test_done_and_dismissed_generated_tasks_are_still_removed(self):
        target, target_tasks = self._log_chat(10, "2026-08-10T09:00:00")
        self._log_chat(2, "2026-08-10T09:01:00")
        complete_task(self.data, target_tasks["coffee_chat"]["task_id"])
        complete_task(self.data, target_tasks["chat_reminder"]["task_id"], dismissed=True)

        plan = delete_interaction(self.data, target["interaction_id"], confirm=False)
        self.assertEqual(
            {row["status"] for row in plan["tasks"]},
            {"done", "dismissed"},
        )
        delete_interaction(self.data, target["interaction_id"], confirm=True)

        remaining_ids = {row["task_id"] for row in read_rows(self.data, "tasks")}
        self.assertTrue(
            {row["task_id"] for row in target_tasks.values()}.isdisjoint(remaining_ids)
        )

    def test_nonexistent_and_noncoffee_interactions_fail_without_writes(self):
        with patch("career_crm.core.now_iso", return_value="2026-08-10T09:00:00"):
            note, _ = log_event(
                self.data,
                "note",
                person_id=self.person["person_id"],
                event_date=date.today().isoformat(),
                notes="A regular note",
            )
        before = self._tree_snapshot()

        with self.assertRaisesRegex(ValueError, "Expected one interaction"):
            delete_interaction(self.data, "int_missing", confirm=False)
        with self.assertRaisesRegex(ValueError, "only coffee_chat"):
            delete_interaction(self.data, note["interaction_id"], confirm=False)
        self.assertEqual(self._tree_snapshot(), before)

    def test_validation_failure_rolls_back_every_changed_csv_byte_for_byte(self):
        target, _ = self._log_chat(10, "2026-08-10T09:00:00")
        self._log_chat(2, "2026-08-10T09:01:00")
        before = self._csv_snapshot()
        validation_calls = []

        def fail_post_write(data_dir):
            validation_calls.append(data_dir)
            if len(validation_calls) == 1:
                return validate(data_dir)
            return ["forced post-write validation failure"]

        with patch("career_crm.core.validate", side_effect=fail_post_write):
            with self.assertRaisesRegex(ValueError, "forced post-write validation failure"):
                delete_interaction(self.data, target["interaction_id"], confirm=True)

        self.assertGreaterEqual(len(validation_calls), 2)
        self.assertEqual(self._csv_snapshot(), before)
        self.assertIn(
            target["interaction_id"],
            {row["interaction_id"] for row in read_rows(self.data, "interactions")},
        )

    def test_next_mutating_command_recovers_a_crash_between_file_replacements(self):
        target, _ = self._log_chat(10, "2026-08-10T09:00:00")
        self._log_chat(2, "2026-08-10T09:01:00")
        before = self._csv_snapshot()
        backup_dir = backup_operation(
            self.data,
            ["interactions", "tasks"],
            "simulated_interrupted_delete",
        )
        write_operation_journal(
            self.data,
            backup_dir,
            ["interactions", "tasks"],
        )
        partial_interactions = [
            row for row in read_rows(self.data, "interactions")
            if row["interaction_id"] != target["interaction_id"]
        ]
        replace_bytes_atomic(
            self.data / "interactions.csv",
            rows_to_csv_bytes("interactions", partial_interactions),
        )
        self.assertTrue(operation_journal_path(self.data).exists())
        self.assertNotEqual((self.data / "interactions.csv").read_bytes(), before["interactions"])

        ensure_data_dir(self.data)

        self.assertEqual(self._csv_snapshot(), before)
        self.assertFalse(operation_journal_path(self.data).exists())


class InteractionDeleteCliParserTests(unittest.TestCase):
    def test_delete_requires_exactly_one_of_dry_run_or_confirm(self):
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["interaction", "delete", "int_example"])
            with self.assertRaises(SystemExit):
                parser.parse_args([
                    "interaction", "delete", "int_example", "--dry-run", "--confirm",
                ])

        dry_run = parser.parse_args([
            "interaction", "delete", "int_example", "--dry-run",
        ])
        confirmed = parser.parse_args([
            "interaction", "delete", "int_example", "--confirm",
        ])
        self.assertTrue(dry_run.dry_run)
        self.assertFalse(dry_run.confirm)
        self.assertFalse(confirmed.dry_run)
        self.assertTrue(confirmed.confirm)


if __name__ == "__main__":
    unittest.main()
