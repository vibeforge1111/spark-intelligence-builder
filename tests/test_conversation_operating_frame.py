from __future__ import annotations

import unittest

from spark_intelligence.self_awareness import build_agent_operating_context
from spark_intelligence.self_awareness.conversation_frame import (
    build_conversation_operating_frame,
    check_final_answer_drift,
    evaluate_action_gate,
)

from tests.test_support import SparkTestCase


class ConversationOperatingFrameUnitTests(unittest.TestCase):
    def test_concept_chat_blocks_route_changing_actions_without_explicit_request(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="Any other thing you'd build on top of this?",
        )

        self.assertEqual(frame.current_mode, "concept_chat")
        self.assertEqual(frame.user_intent, "answer")
        self.assertIn("answer_in_chat", frame.allowed_next_actions)
        self.assertIn("start_mission", frame.disallowed_next_actions)

        gate = evaluate_action_gate(frame, proposed_action="start_mission")

        self.assertEqual(gate.decision, "blocked")
        self.assertEqual(gate.safe_next_action, "answer_in_chat")

    def test_mentioning_mission_control_does_not_authorize_opening_it(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="How would the AOC panel mention Mission Control without confusing agents?",
        )

        gate = evaluate_action_gate(frame, proposed_action="open_mission_control")

        self.assertEqual(frame.current_mode, "concept_chat")
        self.assertEqual(gate.decision, "blocked")
        self.assertIn("latest user turn did not authorize", gate.reason)

    def test_patch_work_allows_commits_when_user_says_lets_do_it(self) -> None:
        frame = build_conversation_operating_frame(user_message="lets do it commit often")

        self.assertEqual(frame.current_mode, "patch_work")
        self.assertEqual(frame.user_intent, "edit")
        self.assertIn("commit_changes", frame.allowed_next_actions)
        self.assertEqual(evaluate_action_gate(frame, proposed_action="commit_changes").decision, "allowed")

    def test_final_answer_check_rejects_unrequested_mission_status(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="Any other thing you'd build on top of this?",
        )

        check = check_final_answer_drift(
            frame,
            draft_answer="Got it. Spark picked up the build. Mission: mission-1778325245851.",
        )

        self.assertFalse(check.match)
        self.assertTrue(check.rewrite_required)
        self.assertEqual(check.drift_type, "unrequested_mission_status")


class ConversationOperatingFrameAocTests(SparkTestCase):
    def test_agent_operating_context_includes_conversation_frame(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="How should we connect AOC with existing systems?",
        )

        payload = context.to_payload()
        frame = payload["conversation_frame"]
        self.assertEqual(frame["current_mode"], "concept_chat")
        self.assertIn("answer_in_chat", frame["allowed_next_actions"])
        self.assertIn("start_mission", frame["disallowed_next_actions"])
        self.assertIn("Current mode: concept_chat", context.to_text())

        ledger_sources = {item["source"] for item in payload["source_ledger"]}
        self.assertIn("conversation_operating_frame", ledger_sources)
