from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class BlackBoxCliTests(SparkTestCase):
    def test_black_box_cli_reads_recorded_turn_trace(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(
            "self",
            "turn-trace",
            "--home",
            str(self.home),
            "--user-message",
            "any other thing you'd build on top of this",
            "--proposed-action",
            "start_mission",
            "--request-id",
            "req-black-box-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "black-box",
            "--home",
            str(self.home),
            "--request-id",
            "req-black-box-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["counts"]["entries"], 2)
        event_types = {entry["event_type"] for entry in payload["entries"]}
        self.assertIn("task_intent_detected", event_types)
        self.assertIn("blocker_detected", event_types)
        blocker = [entry for entry in payload["entries"] if entry["event_type"] == "blocker_detected"][0]
        self.assertTrue(any("start_mission" in item for item in blocker["blockers"]))
