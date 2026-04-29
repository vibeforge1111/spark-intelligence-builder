from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from spark_intelligence.build_quality_review import (
    build_build_quality_review_reply,
    looks_like_build_quality_review_query,
)
from spark_intelligence.gateway.guardrails import set_runtime_state_value
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class BuildQualityReviewTests(SparkTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo_root = self.home / "spawner-ui"
        route_dir = self.repo_root / "src" / "routes" / "memory-quality"
        route_dir.mkdir(parents=True)
        (self.repo_root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
        (route_dir / "+page.svelte").write_text("<h1>Memory Quality</h1>\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=spark-tests@example.com",
                "-c",
                "user.name=Spark Tests",
                "commit",
                "-m",
                "Initial spawner fixture",
            ],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        (route_dir / "+page.svelte").write_text("<h1>Memory Quality Dashboard</h1>\n", encoding="utf-8")
        self.memory_chip_root = self.home / "domain-chip-memory"
        self.memory_chip_root.mkdir(parents=True)
        (self.memory_chip_root / "pyproject.toml").write_text(
            "[project]\nname='domain-chip-memory'\n",
            encoding="utf-8",
        )
        self.config_manager.set_path("spark.local_projects.include_known_spark_repos", False)
        self.config_manager.set_path("spark.local_projects.include_attachment_repos", False)
        self.config_manager.set_path("spark.local_projects.roots", [str(self.repo_root), str(self.memory_chip_root)])
        self.config_manager.set_path("spark.swarm.enabled", False)

    def test_build_quality_review_requires_tests_and_demo_before_rating(self) -> None:
        result = build_build_quality_review_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="How would you rate the quality of the /memory-quality build in spawner-ui?",
        )

        self.assertFalse(result.facts["evidence_complete"])
        self.assertEqual(result.facts["target_repo"], "spawner-ui")
        self.assertEqual(result.facts["git"]["status"], "dirty")
        self.assertEqual(result.facts["route"]["status"], "ok")
        self.assertIn("passing test evidence", result.facts["missing_evidence"])
        self.assertIn("route/demo evidence", result.facts["missing_evidence"])
        self.assertIn("I should not rate the build yet", result.reply_text)

    def test_build_quality_review_accepts_recorded_test_and_demo_evidence(self) -> None:
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="build_quality:last_tests:spawner-ui",
            value=json.dumps(
                {
                    "status": "passed",
                    "command": "npm test",
                    "summary": "12 passed",
                },
                sort_keys=True,
            ),
            component="test",
        )
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="build_quality:last_demo:spawner-ui",
            value=json.dumps(
                {
                    "status": "passed",
                    "url": "http://localhost:5173/memory-quality",
                    "summary": "route rendered",
                },
                sort_keys=True,
            ),
            component="test",
        )

        result = build_build_quality_review_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Review the /memory-quality build quality in spawner-ui.",
        )

        self.assertTrue(result.facts["evidence_complete"])
        self.assertEqual(result.facts["tests"]["status"], "passed")
        self.assertEqual(result.facts["demo"]["status"], "passed")
        self.assertIn("complete enough to rate", result.reply_text)

    def test_build_quality_review_targets_standalone_memory_quality_dashboard(self) -> None:
        dashboard_root = self.home / "spark-memory-quality-dashboard"
        dashboard_root.mkdir(parents=True)
        (dashboard_root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
        (dashboard_root / "src").mkdir()
        (dashboard_root / "tests").mkdir()
        subprocess.run(["git", "init"], cwd=dashboard_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=dashboard_root, check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=spark-tests@example.com",
                "-c",
                "user.name=Spark Tests",
                "commit",
                "-m",
                "Initial dashboard fixture",
            ],
            cwd=dashboard_root,
            check=True,
            capture_output=True,
            text=True,
        )
        self.config_manager.set_path(
            "spark.local_projects.roots",
            [str(self.repo_root), str(self.memory_chip_root), str(dashboard_root)],
        )
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="build_quality:last_tests:spark-memory-quality-dashboard",
            value=json.dumps(
                {
                    "status": "passed",
                    "command": "npm test",
                    "summary": "14 passed",
                },
                sort_keys=True,
            ),
            component="test",
        )

        result = build_build_quality_review_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="Review the quality of the memory quality dashboard build in spark-memory-quality-dashboard.",
        )

        self.assertEqual(result.facts["target_repo"], "spark-memory-quality-dashboard")
        self.assertTrue(result.facts["evidence_complete"])
        self.assertEqual(result.facts["tests"]["status"], "passed")
        self.assertIn("complete enough to rate", result.reply_text)

    def test_researcher_reply_routes_build_quality_review_without_provider(self) -> None:
        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider should not run"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-build-quality",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="How would you rate the quality of the /memory-quality build in spawner-ui?",
            )

        self.assertEqual(result.mode, "build_quality_review_direct")
        self.assertEqual(result.routing_decision, "build_quality_review_direct")
        self.assertIn("Target repo: spawner-ui", result.reply_text)
        self.assertIn("Tests: missing", result.reply_text)

    def test_source_debug_explains_build_quality_review_route(self) -> None:
        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider should not run"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-build-quality",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Review the quality of the /memory-quality build in spawner-ui.",
            )
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-source-debug",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Why did you answer that?",
            )

        self.assertEqual(result.mode, "context_source_debug")
        self.assertIn("build-quality review route", result.reply_text)
        self.assertIn("- routing_decision: build_quality_review_direct", result.reply_text)
        self.assertIn("- source: grounded local repo evidence", result.reply_text)
        self.assertIn("- target_repo: spawner-ui", result.reply_text)
        self.assertNotIn("latest Spark context capsule", result.reply_text)

    def test_query_detection_avoids_memory_quality_plan(self) -> None:
        self.assertTrue(looks_like_build_quality_review_query("How good is this build?"))
        self.assertTrue(
            looks_like_build_quality_review_query("Review the quality of the /memory-quality build in spawner-ui.")
        )
        self.assertFalse(
            looks_like_build_quality_review_query("Give me a concrete evaluation plan for persistent memory quality.")
        )
