from __future__ import annotations

import sqlite3
from unittest.mock import patch

from spark_intelligence.system_registry import (
    build_system_registry,
    build_system_registry_prompt_context,
    looks_like_system_registry_query,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip


class SystemRegistryTests(SparkTestCase):
    def test_build_system_registry_tracks_core_systems_and_chip_states(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        create_fake_hook_chip(self.home, chip_key="spark-swarm")
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc", "spark-browser"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])

        snapshot = build_system_registry(self.config_manager, self.state_db)
        payload = snapshot.to_payload()
        records = {str(record["key"]): record for record in payload["records"]}

        self.assertEqual(records["spark_intelligence_builder"]["kind"], "system")
        self.assertEqual(records["spark_researcher"]["kind"], "system")
        self.assertEqual(records["spark_swarm"]["kind"], "system")
        self.assertEqual(records["spark_browser"]["status"], "ready")
        self.assertEqual(records["spark_voice"]["status"], "available")
        self.assertEqual(records["startup-yc"]["kind"], "chip")
        self.assertTrue(records["startup-yc"]["pinned"])
        self.assertEqual(records["spark-browser"]["status"], "active")
        self.assertEqual(records["spark-browser"]["metadata"]["onboarding"]["harnesses"], ["browser.grounded"])
        self.assertIn("origin_access", records["spark-browser"]["metadata"]["onboarding"]["permissions"])
        self.assertIn(
            "governed browser search and page inspection",
            payload["summary"]["current_capabilities"],
        )
        self.assertGreaterEqual(int(payload["summary"]["onboarding_contract_count"]), 1)

    def test_build_system_registry_prompt_context_uses_registry_for_self_knowledge(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc", "spark-browser"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])

        prompt_context = build_system_registry_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What can you do right now and what are you connected to?",
        )

        self.assertIn("[Spark system registry]", prompt_context)
        self.assertIn("Spark Intelligence Builder: status=", prompt_context)
        self.assertIn("[Onboarded contracts]", prompt_context)
        self.assertIn("spark-browser:", prompt_context)
        self.assertIn("role=Governed browser and search chip for web inspection and source capture.", prompt_context)
        self.assertIn("harnesses=browser.grounded", prompt_context)
        self.assertIn("[Chip state]", prompt_context)
        self.assertIn("- pinned=startup-yc", prompt_context)
        self.assertIn("- active=spark-browser,startup-yc", prompt_context)
        self.assertIn("- only_attached=spark-swarm", prompt_context)
        self.assertIn("[Current capabilities]", prompt_context)
        self.assertIn("1:1 conversational work through Builder", prompt_context)
        self.assertIn("Use exact runtime names for core systems", prompt_context)
        self.assertIn("Do not collapse Spark Memory the system into an attached memory chip", prompt_context)

    def test_system_registry_query_detection_covers_capability_and_surroundings_questions(self) -> None:
        self.assertTrue(looks_like_system_registry_query("What can you do right now?"))
        self.assertTrue(looks_like_system_registry_query("What are you connected to?"))
        self.assertTrue(looks_like_system_registry_query("What tools and adapters do you have?"))
        self.assertTrue(looks_like_system_registry_query("What does spark-browser do for you?"))
        self.assertFalse(looks_like_system_registry_query("Help me write a landing page"))

    def test_build_system_registry_prompt_context_handles_chip_explanation_queries(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])

        prompt_context = build_system_registry_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What does spark-browser do for you?",
        )

        self.assertIn("[Spark system registry]", prompt_context)
        self.assertIn("spark-browser:", prompt_context)
        self.assertIn("role=Governed browser and search chip for web inspection and source capture.", prompt_context)
        self.assertIn("surfaces=researcher_bridge,cli,telegram", prompt_context)
        self.assertIn("permissions=browser_session,origin_access", prompt_context)

    def test_build_system_registry_prompt_context_degrades_closed_on_sqlite_fault(self) -> None:
        with patch(
            "spark_intelligence.system_registry.registry.build_system_registry",
            side_effect=sqlite3.DatabaseError("database disk image is malformed"),
        ):
            prompt_context = build_system_registry_prompt_context(
                config_manager=self.config_manager,
                state_db=self.state_db,
                user_message="What are you connected to right now?",
            )

        self.assertEqual(prompt_context, "")
