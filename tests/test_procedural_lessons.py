from __future__ import annotations

from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    latest_events_by_type,
    recent_procedural_lesson_records,
)
from spark_intelligence.workflow_recovery import (
    build_procedural_lesson_context,
    get_procedural_lesson,
    latest_procedural_lessons,
    record_bad_self_review_lesson,
    record_target_resolution_lesson,
    record_timeout_recovery_lesson,
    record_wrong_build_target_lesson,
    retire_procedural_lesson,
    upsert_procedural_lesson,
)

from tests.test_support import SparkTestCase


class ProceduralLessonMemoryTests(SparkTestCase):
    def test_upsert_procedural_lesson_records_actionable_correction(self) -> None:
        lesson = upsert_procedural_lesson(
            self.state_db,
            lesson_key="target-resolution:spawner-ui",
            lesson_kind="target_resolution",
            trigger_pattern="User asks for a route inside spawner-ui but resolver selects a standalone workspace.",
            corrective_action="Confirm repo and route before building.",
            failure_summary="Memory dashboard was built outside the requested app.",
            target_repo="vibeforge1111/spawner-ui",
            applies_to_component="repo_resolution",
            source_task_key="task-memory-dashboard",
            confidence=0.84,
            evidence={"expected_route": "/memory-quality"},
        )

        self.assertEqual(lesson.status, "active")
        self.assertTrue(lesson.is_active)
        self.assertEqual(lesson.lesson_kind, "target_resolution")
        self.assertEqual(lesson.evidence["expected_route"], "/memory-quality")

        loaded = get_procedural_lesson(self.state_db, lesson_key="target-resolution:spawner-ui")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertIn("Confirm repo and route", loaded.to_context_text())

        events = latest_events_by_type(self.state_db, event_type="procedural_lesson_recorded", limit=10)
        self.assertTrue(events)
        self.assertEqual((events[0]["facts_json"] or {}).get("lesson_key"), "target-resolution:spawner-ui")

    def test_repeated_procedural_lesson_increments_occurrence_and_keeps_highest_confidence(self) -> None:
        upsert_procedural_lesson(
            self.state_db,
            lesson_key="timeout:graphiti-adapter",
            lesson_kind="timeout_recovery",
            trigger_pattern="Graphiti adapter work times out.",
            corrective_action="Resume from pending task ledger.",
            confidence=0.55,
        )

        lesson = upsert_procedural_lesson(
            self.state_db,
            lesson_key="timeout:graphiti-adapter",
            lesson_kind="timeout_recovery",
            trigger_pattern="Graphiti adapter work times out after tests start.",
            corrective_action="Load pending task and poll the test process before asking the user.",
            confidence=0.8,
            evidence={"retry": "poll process"},
        )

        self.assertEqual(lesson.occurrence_count, 2)
        self.assertEqual(lesson.confidence, 0.8)
        self.assertIn("poll the test process", lesson.corrective_action)
        self.assertEqual(lesson.evidence["retry"], "poll process")

    def test_named_helpers_cover_target_build_review_and_timeout_patterns(self) -> None:
        record_target_resolution_lesson(
            self.state_db,
            requested_target="spawner-ui",
            resolved_target="spark-memory-quality-dashboard",
            source_task_key="task-target",
        )
        record_wrong_build_target_lesson(
            self.state_db,
            expected_repo="spawner-ui",
            actual_repo="spark-memory-quality-dashboard",
            artifact="/memory-quality",
            source_task_key="task-build",
        )
        record_bad_self_review_lesson(
            self.state_db,
            reviewed_subject="Galaxy Garden build",
            missing_evidence=["diff", "route", "tests", "demo"],
            source_task_key="task-review",
        )
        record_timeout_recovery_lesson(
            self.state_db,
            task_key="memory:graphiti-adapter",
            timeout_point="after adapter inspection",
            next_retry_step="continue sidecar tests",
        )

        lessons = latest_procedural_lessons(self.state_db, limit=10)
        kinds = {lesson.lesson_kind for lesson in lessons}
        self.assertIn("target_resolution", kinds)
        self.assertIn("wrong_build_target", kinds)
        self.assertIn("bad_self_review", kinds)
        self.assertIn("timeout_recovery", kinds)

        context = build_procedural_lesson_context(self.state_db, applies_to_component="quality_review")
        self.assertIn("Inspect target repo, diff, route/demo state, and tests", context)

    def test_retired_lessons_are_removed_from_active_context(self) -> None:
        upsert_procedural_lesson(
            self.state_db,
            lesson_key="self-review:old",
            lesson_kind="bad_self_review",
            trigger_pattern="Old review problem.",
            corrective_action="Old correction.",
        )

        retired = retire_procedural_lesson(self.state_db, lesson_key="self-review:old", reason="Replaced by stronger rule.")

        self.assertEqual(retired.status, "retired")
        self.assertFalse(retired.is_active)
        self.assertEqual(latest_procedural_lessons(self.state_db), [])
        self.assertEqual(build_procedural_lesson_context(self.state_db), "No active procedural lessons are recorded.")

    def test_watchtower_procedural_memory_panel_counts_active_lessons(self) -> None:
        record_wrong_build_target_lesson(
            self.state_db,
            expected_repo="spawner-ui",
            actual_repo="spark-memory-quality-dashboard",
            artifact="/memory-quality",
        )
        record_timeout_recovery_lesson(
            self.state_db,
            task_key="memory:timeout-recovery",
            timeout_point="batch still running",
            next_retry_step="poll running command",
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["procedural_memory"]
        self.assertEqual(panel["counts"]["active_procedural_lessons"], 2)
        self.assertEqual(panel["counts"]["wrong_build_target_lessons"], 1)
        self.assertEqual(panel["counts"]["timeout_recovery_lessons"], 1)

        rows = recent_procedural_lesson_records(self.state_db, limit=10)
        self.assertEqual(len(rows), 2)
        self.assertIn(rows[0]["lesson_kind"], {"wrong_build_target", "timeout_recovery"})
