from __future__ import annotations

from spark_intelligence.memory.approval_inbox import build_memory_approval_inbox
from spark_intelligence.self_awareness.agent_events import build_agent_black_box_report
from spark_intelligence.self_awareness.turn_recorder import record_agent_turn_trace

from tests.test_support import SparkTestCase


class AgentTurnRecorderTests(SparkTestCase):
    def test_turn_recorder_writes_frame_gate_drift_and_memory_candidate_events(self) -> None:
        trace = record_agent_turn_trace(
            self.state_db,
            request_id="req-turn",
            user_message="Any other thing you'd build on top of this?",
            proposed_action="start_mission",
            draft_answer="Spark picked up the build. Mission: mission-1778325245851.",
            source_refs=[
                {
                    "source": "current_diagnostics",
                    "role": "health_truth",
                    "freshness": "live_probed",
                    "source_ref": "diagnostics:scan-1",
                    "summary": "Builder healthy, Browser unavailable.",
                }
            ],
            memory_candidate={
                "text": "AOC should be a shared read-model, not a new brain.",
                "memory_role": "belief",
                "target_scope": "spark_doctrine",
            },
        )

        black_box = build_agent_black_box_report(self.state_db, request_id="req-turn")
        inbox = build_memory_approval_inbox(self.state_db)

        self.assertEqual(trace.frame.current_mode, "concept_chat")
        self.assertEqual(trace.action_gate.decision, "blocked")
        self.assertTrue(trace.final_answer_check.rewrite_required)
        self.assertEqual(black_box.to_payload()["counts"]["entries"], 5)
        self.assertEqual(black_box.to_payload()["counts"]["blocker_events"], 2)
        self.assertTrue(
            any(entry["event_type"] == "source_used" for entry in black_box.to_payload()["entries"])
        )
        self.assertEqual(inbox.counts["pending"], 1)

    def test_turn_recorder_resolves_option_reference(self) -> None:
        trace = record_agent_turn_trace(
            self.state_db,
            request_id="req-option",
            user_message="option 2",
            active_reference_items=["AOC UI polish", "black box recorder"],
            proposed_action="answer_in_chat",
        )

        self.assertEqual(trace.frame.active_reference_list["selected_item"], "black box recorder")
        self.assertEqual(trace.action_gate.decision, "allowed")
