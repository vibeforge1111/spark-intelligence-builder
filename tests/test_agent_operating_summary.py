from __future__ import annotations

from spark_intelligence.self_awareness import build_agent_operating_context

from tests.test_support import SparkTestCase


class AgentOperatingSummaryTests(SparkTestCase):
    def test_aoc_agent_summary_blocks_patch_claims_in_read_only_runner(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        payload = context.to_payload()

        self.assertIn("cannot patch files in this runner", payload["agent_facing_summary"])
        self.assertIn("For code changes, route to writable Spawner/Codex.", payload["agent_facing_summary"])
        need_names = {item["need"] for item in payload["agent_needs"]}
        self.assertIn("writable_runner", need_names)
        self.assertIn("memory_save_confirmation", need_names)
        rendered = context.to_text()
        self.assertIn("What Rec Needs", rendered)
        self.assertIn("Agent-facing Summary", rendered)

    def test_aoc_agent_summary_preserves_concept_chat_disallowed_actions(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Any other thing you'd build on top of this?",
        )

        summary = context.to_payload()["agent_facing_summary"]

        self.assertIn("You can answer in chat", summary)
        self.assertIn("Disallowed now:", summary)
        self.assertIn("start_mission", summary)
