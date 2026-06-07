from __future__ import annotations

from spark_intelligence.harness_registry import (
    build_harness_registry,
    build_harness_selection,
    select_auto_harness_recipe,
    select_harness_recipe,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip, create_fake_researcher_runtime


class HarnessRegistryPayloadTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_select_harness_recipe_raises_on_unknown_recipe_id(self) -> None:
        self._enable_fake_researcher()
        with self.assertRaises(ValueError) as ctx:
            select_harness_recipe(
                config_manager=self.config_manager,
                state_db=self.state_db,
                recipe_id="not.a.real.recipe",
            )
        self.assertIn("not.a.real.recipe", str(ctx.exception))
        # The error message must list the available recipes so the operator can self-recover.
        message = str(ctx.exception).lower()
        self.assertIn("available recipes", message)

    def test_select_harness_recipe_strips_whitespace_in_recipe_id(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        recipe = select_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            recipe_id="   advisory_voice_reply   ",
        )
        self.assertEqual(recipe.recipe_id, "advisory_voice_reply")
        self.assertEqual(recipe.primary_harness_id, "researcher.advisory")

    def test_select_harness_recipe_returns_payload_with_available_flag(self) -> None:
        self._enable_fake_researcher()
        # Note: no voice chip attached, so advisory_voice_reply is NOT available
        recipe = select_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            recipe_id="advisory_voice_reply",
        )
        payload = recipe.to_payload()
        self.assertEqual(payload["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["primary_harness_id"], "researcher.advisory")
        self.assertEqual(payload["follow_up_harness_ids"], ["voice.io"])
        self.assertFalse(payload["available"])
        self.assertIn("limitations", payload)

    def test_harness_selection_payload_round_trip_preserves_fields(self) -> None:
        self._enable_fake_researcher()
        selection = build_harness_selection(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Explain this directly.",
        )
        payload = selection.to_payload()
        # Payload mirrors all dataclass fields
        for field in (
            "task",
            "harness_id",
            "label",
            "owner_system",
            "backend_kind",
            "session_scope",
            "prompt_strategy",
            "toolsets",
            "required_capabilities",
            "artifacts",
            "route_mode",
            "reason",
            "next_actions",
            "limitations",
        ):
            self.assertIn(field, payload, f"missing field: {field}")
        self.assertEqual(payload["task"], "Explain this directly.")
        # to_json must be valid JSON that decodes back to the same payload
        import json as _json
        decoded = _json.loads(selection.to_json())
        self.assertEqual(decoded["harness_id"], payload["harness_id"])

    def test_auto_harness_recipe_payload_includes_selection_marker_fields(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        auto = select_auto_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What is the difference between Spark Researcher and Builder? Answer in voice.",
        )
        self.assertIsNotNone(auto)
        assert auto is not None
        payload = auto.to_payload()
        self.assertEqual(payload["selection_mode"], "auto")
        self.assertEqual(payload["recipe_id"], "advisory_voice_reply")
        self.assertIn("selection_reason", payload)
        self.assertTrue(payload["selection_reason"])

    def test_auto_harness_recipe_returns_none_for_empty_task(self) -> None:
        self._enable_fake_researcher()
        result = select_auto_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="   ",
        )
        self.assertIsNone(result)

    def test_registry_payload_summary_filters_unavailable_recipes(self) -> None:
        self._enable_fake_researcher()
        # No voice chip / swarm chip — recipes built on those will be unavailable
        snapshot = build_harness_registry(self.config_manager, self.state_db)
        summary = snapshot.to_payload()["summary"]
        self.assertEqual(summary["recipe_count"], 3)
        # available_recipes is a strict subset of all recipe ids
        all_recipe_ids = {r["recipe_id"] for r in snapshot.to_payload()["recipes"]}
        for available_id in summary["available_recipes"]:
            self.assertIn(available_id, all_recipe_ids)
        # available_recipes count <= recipe_count
        self.assertLessEqual(len(summary["available_recipes"]), summary["recipe_count"])
