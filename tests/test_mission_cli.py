from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class MissionCliTests(SparkTestCase):
    def test_mission_status_returns_snapshot_payload(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "mission",
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("summary", payload)
        self.assertIn("panels", payload)

    def test_mission_plan_returns_auto_selected_recipe_for_voice_advisory_task(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "mission",
            "plan",
            "What is the difference between Spark Researcher and Builder? Answer in voice.",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["selected_system"], "Spark Researcher")
        self.assertEqual(payload["summary"]["selected_harness"], "researcher.advisory")
        self.assertEqual(payload["summary"]["selected_recipe"], "advisory_voice_reply")
        self.assertEqual(payload["summary"]["selection_mode"], "auto_recipe")

    def test_mission_plan_respects_forced_recipe(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "mission",
            "plan",
            "Research this and then escalate it to Swarm.",
            "--home",
            str(self.home),
            "--recipe",
            "research_then_swarm",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["selected_recipe"], "research_then_swarm")
        self.assertEqual(payload["summary"]["selection_mode"], "explicit_recipe")
        self.assertEqual(payload["details"]["recipe"]["primary_harness_id"], "researcher.advisory")
