from __future__ import annotations

from spark_intelligence.harness_runtime import (
    build_harness_runtime_snapshot,
    build_harness_task_envelope,
    execute_harness_task,
)

from tests.test_support import SparkTestCase


class HarnessRuntimeSnapshotBoundsTests(SparkTestCase):
    def test_snapshot_summary_is_empty_when_no_runs_recorded(self) -> None:
        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        self.assertEqual(snapshot.summary["recent_run_count"], 0)
        self.assertEqual(snapshot.summary["open_run_count"], 0)
        self.assertEqual(snapshot.summary["failed_run_count"], 0)
        self.assertIsNone(snapshot.summary["last_harness_id"])
        self.assertEqual(snapshot.recent_runs, [])

    def test_snapshot_respects_limit_parameter(self) -> None:
        for index in range(3):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task=f"Question {index}",
            )
            execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        snapshot = build_harness_runtime_snapshot(
            self.config_manager,
            self.state_db,
            limit=2,
        )
        self.assertEqual(snapshot.summary["recent_run_count"], 2)
        self.assertEqual(len(snapshot.recent_runs), 2)

    def test_snapshot_default_limit_is_eight(self) -> None:
        # Run six tasks; default limit of 8 should still surface all six.
        for index in range(6):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task=f"Question {index}",
            )
            execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )
        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        self.assertEqual(snapshot.summary["recent_run_count"], 6)
        self.assertEqual(len(snapshot.recent_runs), 6)

    def test_snapshot_last_harness_id_reflects_most_recent_run(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="A first question.",
        )
        execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )
        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        self.assertEqual(snapshot.summary["last_harness_id"], envelope.harness_id)
        self.assertEqual(snapshot.recent_runs[0]["harness_id"], envelope.harness_id)

    def test_snapshot_payload_has_workspace_id_and_generated_at(self) -> None:
        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        payload = snapshot.to_payload()
        self.assertIn("workspace_id", payload)
        self.assertIn("generated_at", payload)
        self.assertIn("summary", payload)
        self.assertIn("recent_runs", payload)
