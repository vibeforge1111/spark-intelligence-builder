from __future__ import annotations

from spark_intelligence.self_awareness.agent_events import build_agent_black_box_report
from spark_intelligence.self_awareness.event_producers import (
    record_mission_state_agent_event,
    record_route_selection_agent_event,
    record_user_override_agent_event,
)
from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel
from spark_intelligence.self_awareness.route_probe import record_route_probe_evidence

from tests.test_support import SparkTestCase


class AgentEventProducerTests(SparkTestCase):
    def test_route_probe_emits_capability_probed_agent_event_end_to_end(self) -> None:
        result = record_route_probe_evidence(
            self.state_db,
            capability_key="spark_memory",
            status="success",
            route_latency_ms=42,
            eval_ref="pytest:memory-smoke",
            source_ref="test:memory-smoke",
            request_id="req-probe",
            actor_id="operator:test",
            probe_summary="memory smoke write=succeeded read_records=1 cleanup=ok",
        )

        black_box = build_agent_black_box_report(self.state_db, request_id="req-probe").to_payload()
        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-probe",
            user_message="check memory route",
        ).to_payload()

        self.assertTrue(result.agent_event_id)
        self.assertEqual(black_box["counts"]["entries"], 1)
        self.assertEqual(black_box["entries"][0]["event_type"], "capability_probed")
        self.assertEqual(black_box["entries"][0]["route_chosen"], "spark_memory")
        self.assertEqual(panel["black_box"]["counts"]["entries"], 1)
        self.assertEqual(panel["sections"]["sections"][5]["section_id"], "black_box_recorder")

    def test_route_selection_mission_state_and_override_emit_black_box_events(self) -> None:
        record_route_selection_agent_event(
            self.state_db,
            selected_route="writable_spawner_codex_mission",
            user_intent="edit",
            confidence="high",
            reason="Needs file edits and current runner is read-only.",
            request_id="req-flow",
            actor_id="operator:test",
        )
        record_mission_state_agent_event(
            self.state_db,
            mission_id="mission-123",
            from_state="queued",
            to_state="running",
            summary="Writable mission started.",
            request_id="req-flow",
            actor_id="operator:test",
        )
        record_user_override_agent_event(
            self.state_db,
            override_summary="Stay in chat; do not open Mission Control.",
            corrected_route="answer_in_chat",
            request_id="req-flow",
            actor_id="operator:test",
        )

        report = build_agent_black_box_report(self.state_db, request_id="req-flow").to_payload()
        event_types = {entry["event_type"] for entry in report["entries"]}

        self.assertIn("route_selected", event_types)
        self.assertIn("mission_changed_state", event_types)
        self.assertIn("user_override_received", event_types)
