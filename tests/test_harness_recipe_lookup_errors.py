from __future__ import annotations

from spark_intelligence.harness_registry import select_harness_recipe

from tests.test_support import SparkTestCase, create_fake_hook_chip, create_fake_researcher_runtime


class HarnessRecipeLookupErrorTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_unknown_recipe_id_raises_value_error_with_available_listing(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        with self.assertRaises(ValueError) as ctx:
            select_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                recipe_id="totally_made_up_recipe",
            )
        message = str(ctx.exception)
        self.assertIn("totally_made_up_recipe", message)
        self.assertIn("Available recipes", message)

    def test_empty_recipe_id_raises_value_error(self) -> None:
        self._enable_fake_researcher()
        with self.assertRaises(ValueError):
            select_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                recipe_id="",
            )

    def test_whitespace_recipe_id_is_normalized_then_raises(self) -> None:
        self._enable_fake_researcher()
        with self.assertRaises(ValueError) as ctx:
            select_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                recipe_id="   ",
            )
        # The empty-after-strip recipe_id should appear in the message, not the raw whitespace.
        self.assertIn("''", str(ctx.exception))

    def test_known_recipe_id_with_surrounding_whitespace_resolves(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        recipe = select_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            recipe_id="  advisory_voice_reply  ",
        )
        self.assertEqual(recipe.recipe_id, "advisory_voice_reply")
