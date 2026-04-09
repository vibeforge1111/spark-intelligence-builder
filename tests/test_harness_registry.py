from __future__ import annotations

from spark_intelligence.harness_registry import (
    build_harness_prompt_context,
    build_harness_registry,
    build_harness_selection,
    looks_like_harness_query,
    select_harness_recipe,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip


class HarnessRegistryTests(SparkTestCase):
    def test_harness_registry_exposes_expected_contracts(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path(
            "spark.chips.active_keys",
            ["spark-browser", "domain-chip-voice-comms", "spark-swarm"],
        )

        payload = build_harness_registry(self.config_manager, self.state_db).to_payload()

        harness_ids = [contract["harness_id"] for contract in payload["contracts"]]
        self.assertIn("builder.direct", harness_ids)
        self.assertIn("researcher.advisory", harness_ids)
        self.assertIn("browser.grounded", harness_ids)
        self.assertIn("voice.io", harness_ids)
        self.assertIn("swarm.escalation", harness_ids)
        recipe_ids = [recipe["recipe_id"] for recipe in payload["recipes"]]
        self.assertIn("advisory_voice_reply", recipe_ids)
        self.assertIn("research_then_swarm", recipe_ids)
        self.assertIn("browser_then_advisory", recipe_ids)

    def test_browser_route_selects_browser_harness(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        selection = build_harness_selection(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="How would you execute a task where you need to search the web and inspect a page?",
        )

        self.assertEqual(selection.harness_id, "browser.grounded")
        self.assertEqual(selection.backend_kind, "browser_bridge")
        self.assertIn("web_search", selection.required_capabilities)

    def test_swarm_disabled_request_holds_local_on_researcher_harness(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-swarm"])
        self.config_manager.set_path("spark.swarm.enabled", False)

        selection = build_harness_selection(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="How would Spark execute this if I ask you to send it to Swarm for multi-agent work?",
        )

        self.assertEqual(selection.harness_id, "researcher.advisory")
        self.assertEqual(selection.route_mode, "swarm_unavailable_hold_local")
        self.assertIn("Keep the task on the primary runtime", selection.reason)

    def test_harness_prompt_context_describes_execution_contract(self) -> None:
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        prompt_context = build_harness_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What harness would you use to send a voice reply?",
        )

        self.assertIn("[Spark harness registry]", prompt_context)
        self.assertIn("selected_harness=voice.io", prompt_context)
        self.assertIn("backend_kind=voice_bridge", prompt_context)
        self.assertIn("[Reply rule]", prompt_context)

    def test_harness_query_detection_catches_execution_questions(self) -> None:
        self.assertTrue(looks_like_harness_query("What harness would you use for this?"))
        self.assertTrue(looks_like_harness_query("How would Spark execute this task?"))
        self.assertTrue(looks_like_harness_query("What backend would you use?"))
        self.assertFalse(looks_like_harness_query("Write a shorter reply."))

    def test_select_harness_recipe_returns_expected_chain(self) -> None:
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        recipe = select_harness_recipe(
            config_manager=self.config_manager,
            state_db=self.state_db,
            recipe_id="advisory_voice_reply",
        )

        self.assertEqual(recipe.primary_harness_id, "researcher.advisory")
        self.assertEqual(recipe.follow_up_harness_ids, ["voice.io"])
