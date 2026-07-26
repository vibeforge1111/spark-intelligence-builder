from __future__ import annotations

from spark_intelligence.harness_registry import build_harness_prompt_context

from tests.test_support import SparkTestCase, create_fake_hook_chip, create_fake_researcher_runtime


class HarnessPromptContextGatingTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_non_harness_question_returns_empty_string(self) -> None:
        self._enable_fake_researcher()
        result = build_harness_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Write me a short reply about the weather.",
        )
        self.assertEqual(result, "")

    def test_empty_user_message_returns_empty_string(self) -> None:
        self._enable_fake_researcher()
        result = build_harness_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="",
        )
        self.assertEqual(result, "")

    def test_whitespace_only_message_returns_empty_string(self) -> None:
        self._enable_fake_researcher()
        result = build_harness_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="   \n   ",
        )
        self.assertEqual(result, "")

    def test_harness_query_returns_block_with_reply_rule(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        result = build_harness_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What harness would you use for a spoken reply?",
        )
        # Sanity-only: the gated branch returns non-empty content and the reply rule
        # the existing harness_registry test already covers fuller assertions on shape.
        self.assertNotEqual(result, "")
        self.assertIn("[Reply rule]", result)
