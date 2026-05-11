from __future__ import annotations

from spark_intelligence.capability_router import (
    build_capability_route_decision,
    build_capability_router_prompt_context,
    looks_like_capability_router_query,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip


class CapabilityRouterTests(SparkTestCase):
    def test_browser_task_routes_to_browser_when_available(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        decision = build_capability_route_decision(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Search the web for Spark and check the website.",
        )

        self.assertEqual(decision.target_system, "Spark CLI browser-use")
        self.assertEqual(decision.route_mode, "browser_grounded")
        self.assertIn("web_search", decision.required_capabilities)

    def test_voice_task_routes_to_voice_when_available(self) -> None:
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        decision = build_capability_route_decision(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Send a voice reply and transcribe the next voice note.",
        )

        self.assertEqual(decision.target_system, "Spark Voice")
        self.assertEqual(decision.route_mode, "voice_io")
        self.assertIn("speech_to_text", decision.required_capabilities)

    def test_install_voice_request_routes_to_voice(self) -> None:
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])

        decision = build_capability_route_decision(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Can you install a voice to yourself right now?",
        )

        self.assertEqual(decision.target_system, "Spark Voice")
        self.assertEqual(decision.route_mode, "voice_io")
        self.assertIn("text_to_speech", decision.required_capabilities)
        self.assertTrue(looks_like_capability_router_query("Can you install a voice to yourself right now?"))

    def test_capability_improvement_request_routes_to_builder_plan(self) -> None:
        decision = build_capability_route_decision(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Can you add a capability for Spark to read my emails?",
        )

        self.assertEqual(decision.target_system, "Spark Intelligence Builder")
        self.assertEqual(decision.route_mode, "capability_improvement")
        self.assertIn("Spark Spawner", decision.supporting_systems)
        self.assertIn("capability_proposal_packet", decision.required_capabilities)
        self.assertIn("capability_probes", decision.required_capabilities)
        self.assertIn("operator_approval", decision.required_capabilities)
        self.assertIn("spawner_mission_control", decision.required_capabilities)
        self.assertTrue(any("domain_chip" in action for action in decision.next_actions))
        self.assertTrue(looks_like_capability_router_query("Set up daily reports of my memories."))
        self.assertTrue(looks_like_capability_router_query("Okay let's build this for you, Spark: read my emails."))
        self.assertTrue(looks_like_capability_router_query("Build a skill that lets you browse my project files."))

    def test_explicit_swarm_request_holds_local_when_swarm_is_disabled(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-swarm"])
        self.config_manager.set_path("spark.swarm.enabled", False)

        decision = build_capability_route_decision(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Should this go to swarm for parallel multi-agent execution?",
        )

        self.assertEqual(decision.route_mode, "swarm_unavailable_hold_local")
        self.assertIn(decision.target_system, {"Spark Researcher", "Spark Intelligence Builder"})
        self.assertIn("Spark Swarm is disabled by operator", decision.reason)

    def test_capability_router_prompt_context_describes_route(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        prompt_context = build_capability_router_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Which system should handle this if I need you to search the web?",
        )

        self.assertIn("[Spark capability router]", prompt_context)
        self.assertIn("target_system=Spark CLI browser-use", prompt_context)
        self.assertIn("[Reply rule]", prompt_context)

    def test_capability_router_query_detection_catches_routing_questions(self) -> None:
        self.assertTrue(looks_like_capability_router_query("Which system should handle this task?"))
        self.assertTrue(looks_like_capability_router_query("Should this stay in Builder or go to Swarm?"))
        self.assertTrue(looks_like_capability_router_query("Should you browse this?"))
        self.assertTrue(looks_like_capability_router_query("Can you add a capability for Spark to read my emails?"))
        self.assertFalse(looks_like_capability_router_query("Write a tighter landing page headline."))
