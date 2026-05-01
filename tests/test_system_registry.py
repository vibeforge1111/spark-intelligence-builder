from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.system_registry import (
    build_system_registry,
    build_system_registry_direct_reply,
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
        records = {(str(record["kind"]), str(record["key"])): record for record in payload["records"]}

        self.assertEqual(records[("system", "spark_intelligence_builder")]["kind"], "system")
        self.assertEqual(records[("system", "spark_researcher")]["kind"], "system")
        self.assertEqual(records[("system", "spark_swarm")]["kind"], "system")
        self.assertEqual(records[("system", "spark_browser")]["status"], "ready")
        self.assertEqual(records[("system", "spark_voice")]["status"], "available")
        self.assertEqual(records[("system", "spark_spawner")]["status"], "configured")
        self.assertEqual(records[("system", "spark_local_work")]["status"], "available")
        self.assertIn("repo_inspection", records[("system", "spark_local_work")]["capabilities"])
        self.assertEqual(records[("chip", "startup-yc")]["kind"], "chip")
        self.assertTrue(records[("chip", "startup-yc")]["pinned"])
        self.assertEqual(records[("chip", "spark-browser")]["status"], "active")
        self.assertEqual(
            records[("chip", "spark-browser")]["metadata"]["onboarding"]["harnesses"],
            ["browser.grounded"],
        )
        self.assertIn(
            "origin_access",
            records[("chip", "spark-browser")]["metadata"]["onboarding"]["permissions"],
        )
        self.assertIn(
            "governed browser search and page inspection",
            payload["summary"]["current_capabilities"],
        )
        self.assertIn(
            "operator-governed local repo/file inspection through Codex or Spawner workflows",
            payload["summary"]["current_capabilities"],
        )
        self.assertGreaterEqual(int(payload["summary"]["onboarding_contract_count"]), 1)

    def test_build_system_registry_prompt_context_uses_registry_for_self_knowledge(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
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
        self.assertIn("harnesses=browser.grounded", prompt_context)
        self.assertIn("[Current capabilities]", prompt_context)
        self.assertIn("1:1 conversational work through Builder", prompt_context)

    def test_build_system_registry_marks_browser_standby_when_session_is_stale(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        with patch(
            "spark_intelligence.system_registry.registry._collect_browser_registry_payload",
            return_value={
                "status": "failed",
                "chip_key": "spark-browser",
                "error_code": "BROWSER_SESSION_STALE",
                "error_message": "Live browser session is not currently connected.",
            },
        ):
            payload = build_system_registry(self.config_manager, self.state_db).to_payload()

        records = {str(record["key"]): record for record in payload["records"]}
        self.assertEqual(records["spark_browser"]["status"], "standby")
        self.assertFalse(records["spark_browser"]["active"])
        self.assertNotIn(
            "governed browser search and page inspection",
            payload["summary"]["current_capabilities"],
        )

    def test_build_system_registry_direct_reply_uses_verified_registry_state(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc", "spark-browser"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])

        with patch(
            "spark_intelligence.system_registry.registry._collect_browser_registry_payload",
            return_value={
                "status": "failed",
                "chip_key": "spark-browser",
                "error_code": "BROWSER_SESSION_STALE",
                "error_message": "Live browser session is not currently connected.",
            },
        ):
            reply = build_system_registry_direct_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                user_message="What are you connected to right now?",
            )

        self.assertIn("Here's what is connected right now:", reply)
        self.assertIn("Spark Intelligence Builder:", reply)
        self.assertIn("Spark Researcher:", reply)
        self.assertIn("Spark Browser: standby", reply)
        self.assertIn("Live browser session is not currently connected.", reply)
        self.assertIn("Active chips:", reply)
        self.assertIn("startup-yc", reply)
        self.assertIn("spark-browser", reply)
        self.assertIn("Spark Spawner:", reply)
        self.assertIn("Spark Local Work:", reply)
        self.assertIn("Local repo/file inspection is available", reply)

    def test_self_awareness_report_separates_observed_unverified_inferred_and_unknown(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc", "spark-browser"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])

        reply = build_system_registry_direct_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Do you have powers to self-introspect about your surroundings and where you lack?",
        )

        self.assertIn("Self-awareness report", reply)
        self.assertIn("Observed now", reply)
        self.assertIn("Source: live Builder system registry snapshot", reply)
        self.assertIn("Active chips:", reply)
        self.assertIn("startup-yc", reply)
        self.assertIn("spark-browser", reply)
        self.assertIn("Available but unverified", reply)
        self.assertIn("Registry presence is not the same as a successful invocation", reply)
        self.assertIn("Inferred", reply)
        self.assertIn("Unknown", reply)
        self.assertIn("I cannot see secrets", reply)
        self.assertIn("Best next checks", reply)
        self.assertIn("Top improvements for this goal", reply)

    def test_build_system_registry_surfaces_local_repo_index(self) -> None:
        repo_root = self.home / "spawner-ui"
        repo_root.mkdir(parents=True)
        (repo_root / "package.json").write_text("{}", encoding="utf-8")
        self.config_manager.set_path("spark.local_projects.roots", [str(repo_root)])

        payload = build_system_registry(self.config_manager, self.state_db, probe_browser=False, probe_git=False).to_payload()
        records = {(str(record["kind"]), str(record["key"])): record for record in payload["records"]}

        self.assertGreaterEqual(payload["summary"]["repo_count"], 1)
        self.assertEqual(records[("repo", "spawner-ui")]["kind"], "repo")
        self.assertEqual(records[("repo", "spawner-ui")]["status"], "ready")
        self.assertIn("frontend_or_node_runtime", records[("repo", "spawner-ui")]["capabilities"])
        self.assertTrue(
            any(str(item).startswith("local repo index:") for item in payload["summary"]["current_capabilities"])
        )

    def test_system_registry_query_detection_covers_capability_and_surroundings_questions(self) -> None:
        self.assertTrue(looks_like_system_registry_query("What can you do right now?"))
        self.assertTrue(looks_like_system_registry_query("What are you connected to?"))
        self.assertTrue(looks_like_system_registry_query("What tools and adapters do you have?"))
        self.assertTrue(looks_like_system_registry_query("Can you self-introspect?"))
        self.assertTrue(looks_like_system_registry_query("Where do you lack knowledge?"))
        self.assertFalse(looks_like_system_registry_query("Help me write a landing page"))
