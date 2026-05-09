from __future__ import annotations

from spark_intelligence.self_awareness.agent_scratchpad import (
    AGENT_SCRATCHPAD_SCHEMA_VERSION,
    build_agent_scratchpad,
)
from spark_intelligence.self_awareness.operating_context import build_agent_operating_context

from tests.test_support import SparkTestCase


class AgentScratchpadTests(SparkTestCase):
    def test_scratchpad_names_boundaries_for_read_only_patch_work(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        scratchpad = build_agent_scratchpad(context.to_payload()).to_payload()

        self.assertEqual(scratchpad["schema_version"], AGENT_SCRATCHPAD_SCHEMA_VERSION)
        self.assertEqual(scratchpad["current_mode"], "patch_work")
        self.assertEqual(scratchpad["next_safe_action"], "start_or_route_to_writable_spawner_codex_mission")
        self.assertIn("latest_user_message_wins", scratchpad["active_constraints"])
        self.assertIn("current_runner_read_only", scratchpad["open_blockers"])
        self.assertIn("claim_patch_files_here", scratchpad["do_not_do"])
        self.assertIn("save_memory_without_approval", scratchpad["do_not_do"])

    def test_scratchpad_keeps_concept_chat_inside_chat_boundary(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Any other thing you'd build on top of this?",
        )

        scratchpad = build_agent_scratchpad(context.to_payload()).to_payload()

        self.assertEqual(scratchpad["current_goal"], "Answer the latest user turn in chat.")
        self.assertEqual(scratchpad["next_safe_action"], "answer_in_chat")
        self.assertIn("start_mission", scratchpad["do_not_do"])
