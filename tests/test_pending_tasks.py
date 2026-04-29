from __future__ import annotations

from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    latest_events_by_type,
    recent_pending_task_records,
)
from spark_intelligence.workflow_recovery import (
    build_pending_task_resume_context,
    close_pending_task,
    get_pending_task,
    latest_pending_tasks,
    record_pending_task_timeout,
    upsert_pending_task,
)

from tests.test_support import SparkTestCase


class PendingTaskLedgerTests(SparkTestCase):
    def test_upsert_pending_task_records_resumable_work_context(self) -> None:
        record = upsert_pending_task(
            self.state_db,
            task_key="memory:daily-summary",
            original_request="Build daily memory summaries.",
            human_id="human:test",
            agent_id="agent:test",
            session_id="session:task",
            target_repo="vibeforge1111/spark-intelligence-builder",
            target_component="memory.session_summaries",
            command="python -m pytest tests/test_session_summaries.py",
            mission_id="mission-123",
            last_evidence="Session summaries are already green.",
            next_retry_step="Add daily/project rollups.",
            evidence={"track": "Track C"},
        )

        self.assertEqual(record.status, "open")
        self.assertTrue(record.is_open)
        self.assertEqual(record.target_repo, "vibeforge1111/spark-intelligence-builder")
        self.assertEqual(record.evidence["track"], "Track C")

        loaded = get_pending_task(self.state_db, task_key="memory:daily-summary")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.original_request, "Build daily memory summaries.")
        self.assertIn("Build daily memory summaries.", loaded.to_resume_text())
        self.assertIn("Add daily/project rollups.", loaded.to_resume_text())

        events = latest_events_by_type(self.state_db, event_type="pending_task_recorded", limit=10)
        self.assertTrue(events)
        self.assertEqual((events[0]["facts_json"] or {}).get("task_key"), "memory:daily-summary")

    def test_record_pending_task_timeout_updates_existing_task_for_resume(self) -> None:
        upsert_pending_task(
            self.state_db,
            task_key="memory:graphiti-adapter",
            original_request="Wire Graphiti adapter behind a disabled feature flag.",
            human_id="human:test",
            target_repo="vibeforge1111/domain-chip-memory",
            next_retry_step="Inspect adapter contract.",
        )

        record = record_pending_task_timeout(
            self.state_db,
            task_key="memory:graphiti-adapter",
            original_request="Wire Graphiti adapter behind a disabled feature flag.",
            human_id="human:test",
            target_repo="vibeforge1111/domain-chip-memory",
            target_component="memory_sidecars",
            command="python -m pytest tests/test_memory_sidecars.py",
            mission_id="mission-graphiti",
            timeout_point="after sidecar contract inspection",
            last_evidence="No live adapter was enabled yet.",
            next_retry_step="Add disabled Graphiti adapter and unit tests.",
            evidence={"timeout_seconds": 120},
        )

        self.assertEqual(record.status, "timed_out")
        self.assertEqual(record.timeout_point, "after sidecar contract inspection")
        self.assertEqual(record.last_evidence, "No live adapter was enabled yet.")
        self.assertEqual(record.next_retry_step, "Add disabled Graphiti adapter and unit tests.")
        self.assertEqual(record.evidence["timeout_seconds"], 120)

        context = build_pending_task_resume_context(self.state_db, human_id="human:test")
        self.assertIn("Wire Graphiti adapter", context)
        self.assertIn("Interrupted at: after sidecar contract inspection", context)
        self.assertIn("Next retry step: Add disabled Graphiti adapter and unit tests.", context)

    def test_close_pending_task_removes_it_from_open_resume_context(self) -> None:
        upsert_pending_task(
            self.state_db,
            task_key="memory:pending-ledger",
            original_request="Add pending task ledger.",
            human_id="human:test",
            next_retry_step="Write tests.",
        )

        closed = close_pending_task(
            self.state_db,
            task_key="memory:pending-ledger",
            completion_summary="Pending task ledger tests passed.",
        )

        self.assertEqual(closed.status, "completed")
        self.assertFalse(closed.is_open)
        self.assertEqual(closed.last_evidence, "Pending task ledger tests passed.")
        self.assertEqual(latest_pending_tasks(self.state_db, human_id="human:test"), [])
        self.assertEqual(build_pending_task_resume_context(self.state_db, human_id="human:test"), "No open pending tasks are recorded.")

        close_events = latest_events_by_type(self.state_db, event_type="pending_task_closed", limit=10)
        self.assertTrue(close_events)
        self.assertEqual((close_events[0]["facts_json"] or {}).get("task_key"), "memory:pending-ledger")

    def test_watchtower_session_integrity_panel_surfaces_open_pending_tasks(self) -> None:
        record_pending_task_timeout(
            self.state_db,
            task_key="memory:timeout-recovery",
            original_request="Resume interrupted memory work.",
            human_id="human:test",
            timeout_point="fast-contract batch timeout",
            last_evidence="Batch was still running at 79 percent.",
            next_retry_step="Poll the running process and continue.",
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["session_integrity"]
        self.assertEqual(panel["counts"]["open_pending_tasks"], 1)
        self.assertEqual(panel["recent_pending_tasks"][0]["task_key"], "memory:timeout-recovery")

        rows = recent_pending_task_records(self.state_db, limit=10)
        self.assertEqual(rows[0]["task_key"], "memory:timeout-recovery")
        self.assertEqual(rows[0]["evidence_json"], {})
