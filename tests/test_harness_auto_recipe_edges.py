from __future__ import annotations

from spark_intelligence.harness_registry import select_auto_harness_recipe

from tests.test_support import SparkTestCase, create_fake_hook_chip, create_fake_researcher_runtime


class HarnessAutoRecipeEdgeTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_empty_task_returns_none(self) -> None:
        self.assertIsNone(
            select_auto_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="",
            )
        )

    def test_whitespace_only_task_returns_none(self) -> None:
        self.assertIsNone(
            select_auto_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="   \n   ",
            )
        )

    def test_none_task_returns_none(self) -> None:
        self.assertIsNone(
            select_auto_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task=None,  # type: ignore[arg-type]
            )
        )

    def test_plain_question_without_voice_signal_returns_none(self) -> None:
        # The task asks a "what" question but never mentions voice — the auto-recipe
        # should not select advisory_voice_reply.
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        self.assertIsNone(
            select_auto_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="What is Spark Builder?",
            )
        )

    def test_voice_signal_without_advisory_signal_returns_none(self) -> None:
        # The task has a voice signal but no advisory signal (?, what, why, how,
        # explain, etc.), so advisory_voice_reply should NOT match.
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        self.assertIsNone(
            select_auto_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="speak the answer aloud",
            )
        )

    def test_auto_recipe_payload_includes_selection_metadata(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        selection = select_auto_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What is the difference between Spark Researcher and Builder? Answer in voice.",
        )
        assert selection is not None
        payload = selection.to_payload()
        self.assertEqual(payload["selection_mode"], "auto")
        self.assertIn("selection_reason", payload)
        self.assertEqual(payload["recipe_id"], "advisory_voice_reply")
