from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class RouteAndMissionEventCliTests(SparkTestCase):
    def test_route_selection_and_mission_state_cli_feed_aoc_black_box(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "route-selection",
            "writable_spawner_codex_mission",
            "--home",
            str(self.home),
            "--user-intent",
            "edit",
            "--confidence",
            "high",
            "--reason",
            "Needs file edits and current runner is read-only.",
            "--request-id",
            "req-route-mission-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        route_payload = json.loads(stdout)
        self.assertEqual(route_payload["event_type"], "route_selected")
        self.assertEqual(route_payload["selected_route"], "writable_spawner_codex_mission")

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "mission-state",
            "mission-123",
            "--home",
            str(self.home),
            "--from-state",
            "queued",
            "--to-state",
            "running",
            "--summary",
            "Writable mission started.",
            "--request-id",
            "req-route-mission-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        mission_payload = json.loads(stdout)
        self.assertEqual(mission_payload["event_type"], "mission_changed_state")
        self.assertEqual(mission_payload["mission_id"], "mission-123")

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--request-id",
            "req-route-mission-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        panel_payload = json.loads(stdout)
        event_types = {entry["event_type"] for entry in panel_payload["black_box"]["entries"]}
        self.assertIn("route_selected", event_types)
        self.assertIn("mission_changed_state", event_types)
        self.assertEqual(panel_payload["black_box"]["counts"]["entries"], 2)
