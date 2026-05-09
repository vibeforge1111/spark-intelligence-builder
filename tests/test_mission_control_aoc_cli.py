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
