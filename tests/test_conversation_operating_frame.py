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

    def test_do_not_open_mission_control_stays_in_concept_chat(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="Do not open Mission Control unless Cem asks.",
        )

        self.assertEqual(frame.current_mode, "concept_chat")
        self.assertIn("open_mission_control", frame.disallowed_next_actions)

    def test_conceptual_mission_board_aoc_talk_stays_in_chat(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="How should the mission board fit into AOC without becoming an instant menu?",
        )

        self.assertEqual(frame.current_mode, "concept_chat")
        self.assertEqual(frame.user_intent, "answer")
        self.assertEqual(evaluate_action_gate(frame, proposed_action="open_mission_control").decision, "blocked")

    def test_conceptual_route_probe_aoc_talk_stays_in_chat(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="How should route probe results be shown in the AOC design?",
        )

        self.assertEqual(frame.current_mode, "concept_chat")
        self.assertEqual(frame.user_intent, "answer")
        self.assertEqual(evaluate_action_gate(frame, proposed_action="run_route_probe").decision, "blocked")

    def test_conceptual_fix_before_coding_stays_in_chat(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="Can we fix deterministic route hijacks conceptually before coding?",
        )

        self.assertEqual(frame.current_mode, "concept_chat")
        self.assertEqual(frame.user_intent, "answer")
        self.assertEqual(evaluate_action_gate(frame, proposed_action="edit_files").decision, "blocked")

    def test_option_reference_resolves_against_active_list(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="option 2",
            active_reference_items=["AOC UI polish", "black box recorder", "theme rewrite"],
            source_turn_id="turn-list",
        )

        self.assertEqual(frame.active_reference_list["resolution_status"], "resolved")
        self.assertEqual(frame.active_reference_list["selected_index"], 2)
        self.assertEqual(frame.active_reference_list["selected_item"], "black box recorder")

    def test_option_reference_reports_out_of_range(self) -> None:
        frame = build_conversation_operating_frame(
            user_message="option 4",
            active_reference_items=["AOC UI polish", "black box recorder"],
        )

        self.assertEqual(frame.active_reference_list["resolution_status"], "out_of_range")
        self.assertIsNone(frame.active_reference_list["selected_item"])


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
