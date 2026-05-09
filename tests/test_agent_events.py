from __future__ import annotations

from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.self_awareness.agent_events import (
    AgentEvent,
    AgentSourceRef,
    build_agent_black_box_entries,
    build_agent_black_box_report,
    record_action_gate_event,
    record_agent_event,
    record_conversation_frame_event,
    record_final_answer_check_event,
)
from spark_intelligence.self_awareness.conversation_frame import (
    build_conversation_operating_frame,
    check_final_answer_drift,
    evaluate_action_gate,
)

from tests.test_support import SparkTestCase


class AgentEventModelTests(SparkTestCase):
    def test_record_agent_event_preserves_source_refs_and_payload(self) -> None:
        event_id = record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="source_used",
                summary="AOC used current diagnostics as source evidence.",
                user_intent="diagnose",
                selected_route="answer_in_chat",
                route_confidence="high",
                sources=[
                    AgentSourceRef(
                        source="diagnostics",
                        role="live_state",
                        freshness="live_probed",
                        source_ref="diag-1",
                        summary="Builder degraded.",
                    )
                ],
                assumptions=["diagnostics payload is current"],
                changed=["source ledger updated"],
                facts={"diagnostic_status": "degraded"},
            ),
            request_id="req-source",
        )

        rows = latest_events_by_type(self.state_db, event_type="source_used", limit=1)

        self.assertEqual(rows[0]["event_id"], event_id)
        self.assertEqual(rows[0]["component"], "agent_event_model")
        self.assertEqual(rows[0]["facts_json"]["schema_version"], "spark.agent_event.v1")
        self.assertEqual(rows[0]["facts_json"]["sources"][0]["freshness"], "live_probed")
        self.assertEqual(rows[0]["provenance_json"]["source_refs"][0]["source_ref"], "diag-1")

    def test_conversation_frame_event_records_intent_as_black_box_entry(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="How would AOC connect to existing Spark systems?",
            source_turn_id="turn-1",
        )

        record_conversation_frame_event(self.state_db, frame, request_id="req-frame")
        entries = build_agent_black_box_entries(self.state_db, request_id="req-frame", limit=5)

        self.assertEqual(entries[0].event_type, "task_intent_detected")
        self.assertEqual(entries[0].perceived_intent, "answer")
        self.assertEqual(entries[0].route_chosen, "answer_in_chat")
        self.assertEqual(entries[0].sources_used[0]["source"], "current_user_message")

    def test_action_gate_event_records_blocker_when_route_is_disallowed(self) -> None:
        frame = build_conversation_operating_frame(user_message="What else would you build on top of AOC?")
        gate = evaluate_action_gate(frame, proposed_action="start_mission")

        record_action_gate_event(self.state_db, frame, gate, request_id="req-blocked")
        entries = build_agent_black_box_entries(self.state_db, request_id="req-blocked", limit=5)

        self.assertEqual(entries[0].event_type, "blocker_detected")
        self.assertEqual(entries[0].route_chosen, "answer_in_chat")
        self.assertTrue(entries[0].blockers)
        self.assertIn("latest user turn did not authorize", entries[0].blockers[0])

    def test_final_answer_check_event_records_drift_when_answer_mismatches_turn(self) -> None:
        frame = build_conversation_operating_frame(user_message="Any other thing you'd build on top of this?")
        drift_check = check_final_answer_drift(
            frame,
            draft_answer="Spark picked up the build. Mission: mission-1778325245851.",
        )

        record_final_answer_check_event(self.state_db, frame, drift_check, request_id="req-drift")
        rows = latest_events_by_type(self.state_db, event_type="agent_drift_detected", limit=1)

        self.assertEqual(rows[0]["facts_json"]["drift_check"]["drift_type"], "unrequested_mission_status")
        entries = build_agent_black_box_entries(self.state_db, request_id="req-drift", limit=5)
        self.assertEqual(entries[0].route_chosen, "rewrite_answer")
        self.assertEqual(entries[0].changed, ["rewrite_required"])

    def test_black_box_report_summarizes_entries_and_counts(self) -> None:
        frame = build_conversation_operating_frame(user_message="What else would you build on AOC?")
        gate = evaluate_action_gate(frame, proposed_action="start_mission")
        record_conversation_frame_event(self.state_db, frame, request_id="req-report")
        record_action_gate_event(self.state_db, frame, gate, request_id="req-report")

        report = build_agent_black_box_report(self.state_db, request_id="req-report")
        payload = report.to_payload()

        self.assertEqual(payload["counts"]["entries"], 2)
        self.assertEqual(payload["counts"]["blocker_events"], 1)
        self.assertIn("Agent black box: 2 event(s).", report.to_text())
        self.assertIn("blockers=1", report.to_text())
