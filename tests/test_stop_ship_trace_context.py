from __future__ import annotations

from spark_intelligence.attachments.snapshot import sync_attachment_snapshot
from spark_intelligence.memory.doctor import run_memory_doctor
from spark_intelligence.memory.orchestrator import (
    MemoryReadResult,
    MemorySdkSmokeResult,
    MemoryWriteResult,
    _record_memory_smoke_event,
)
from spark_intelligence.observability.checks import StopShipIssue, _reconcile_stop_ship_contradictions
from spark_intelligence.observability.store import latest_events_by_type, record_environment_snapshot

from tests.test_support import SparkTestCase, create_fake_hook_chip


class StopShipTraceContextTests(SparkTestCase):
    def test_stop_ship_contradiction_events_emit_trace_ref(self) -> None:
        _reconcile_stop_ship_contradictions(
            state_db=self.state_db,
            issues=[
                StopShipIssue(
                    name="stop_ship_trace_context",
                    ok=False,
                    detail="trace context missing",
                    severity="high",
                )
            ],
        )

        open_events = latest_events_by_type(self.state_db, event_type="contradiction_recorded", limit=1)

        self.assertEqual(open_events[0]["request_id"], "stop_ship:stop_ship_trace_context")
        self.assertEqual(open_events[0]["trace_ref"], "trace:stop_ship:stop_ship_trace_context")

        _reconcile_stop_ship_contradictions(
            state_db=self.state_db,
            issues=[
                StopShipIssue(
                    name="stop_ship_trace_context",
                    ok=True,
                    detail="trace context restored",
                    severity="high",
                )
            ],
        )

        resolved_events = latest_events_by_type(self.state_db, event_type="contradiction_recorded", limit=5)
        resolved_event = next(event for event in resolved_events if event["status"] == "resolved")

        self.assertEqual(resolved_event["request_id"], "stop_ship:stop_ship_trace_context")
        self.assertEqual(resolved_event["trace_ref"], "trace:stop_ship:stop_ship_trace_context")

    def test_background_diagnostic_events_emit_trace_family(self) -> None:
        record_environment_snapshot(
            self.state_db,
            surface="doctor_cli",
            summary="Doctor CLI environment snapshot recorded.",
            facts={"surface": "doctor_cli"},
        )
        environment_events = latest_events_by_type(self.state_db, event_type="runtime_environment_snapshot", limit=1)
        self.assertTrue(environment_events[0]["request_id"].startswith("doctor_cli:environment_snapshot:"))
        self.assertEqual(environment_events[0]["trace_ref"], f"trace:{environment_events[0]['request_id']}")

        chip_root = create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-swarm"])
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)
        attachment_events = [
            event
            for event in latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=5)
            if event["component"] == "attachment_snapshot"
        ]
        self.assertTrue(attachment_events)
        self.assertTrue(attachment_events[0]["request_id"].startswith("attachment_snapshot:"))
        self.assertEqual(attachment_events[0]["trace_ref"], f"trace:{attachment_events[0]['request_id']}")

        _record_memory_smoke_event(
            state_db=self.state_db,
            actor_id="memory_cli",
            result=MemorySdkSmokeResult(
                sdk_module="domain_chip_memory",
                subject="human:smoke:test",
                predicate="system.memory.smoke",
                value="ok",
                write_result=MemoryWriteResult(
                    status="accepted",
                    operation="upsert",
                    method="write_observation",
                    memory_role="current_state",
                    accepted_count=1,
                    rejected_count=0,
                    skipped_count=0,
                    abstained=False,
                    retrieval_trace=None,
                    provenance=[],
                ),
                read_result=MemoryReadResult(
                    status="found",
                    method="get_current_state",
                    memory_role="current_state",
                    records=[{"predicate": "system.memory.smoke", "value": "ok"}],
                    provenance=[],
                    retrieval_trace=None,
                    answer_explanation=None,
                    abstained=False,
                ),
                cleanup_result=None,
            ),
        )
        smoke_events = latest_events_by_type(self.state_db, event_type="memory_smoke_succeeded", limit=1)
        self.assertTrue(smoke_events[0]["request_id"].startswith("memory_smoke:"))
        self.assertEqual(smoke_events[0]["trace_ref"], f"trace:{smoke_events[0]['request_id']}")

        run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Maya",
        )
        brain_events = latest_events_by_type(self.state_db, event_type="memory_doctor_brain_evaluated", limit=1)
        self.assertTrue(brain_events[0]["request_id"].startswith("memory_doctor:brain:"))
        self.assertEqual(brain_events[0]["trace_ref"], f"trace:{brain_events[0]['request_id']}")
