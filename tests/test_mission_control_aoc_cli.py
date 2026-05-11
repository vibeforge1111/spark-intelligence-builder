from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class MissionControlAocCliTests(SparkTestCase):
    def test_mission_status_cli_can_include_aoc_panel_drilldown(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "mission",
            "status",
            "--home",
            str(self.home),
            "--include-aoc-panel",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        panel = payload["panels"]["agent_operating_panel"]
        self.assertEqual(panel["schema_version"], "spark.agent_operating_panel.v1")
        self.assertEqual(payload["summary"]["aoc_best_route"], panel["strip"]["best_route"])

    def test_mission_plan_cli_can_record_route_selection_in_aoc_black_box(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "mission",
            "plan",
            "Patch the mission memory loop",
            "--home",
            str(self.home),
            "--record-aoc-events",
            "--request-id",
            "req-mission-plan-aoc",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        plan_payload = json.loads(stdout)
        self.assertTrue(plan_payload["agent_event_id"])

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "black-box",
            "--home",
            str(self.home),
            "--request-id",
            "req-mission-plan-aoc",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        black_box = json.loads(stdout)
        self.assertEqual(black_box["counts"]["entries"], 1)
        entry = black_box["entries"][0]
        self.assertEqual(entry["event_type"], "route_selected")
        self.assertEqual(entry["perceived_intent"], "plan")
        self.assertEqual(entry["sources_used"][0]["source"], "mission_control_plan")
