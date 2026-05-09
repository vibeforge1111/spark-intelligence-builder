from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class AgentOperatingPanelCliTests(SparkTestCase):
    def test_self_panel_cli_emits_shared_read_model(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--user-message",
            "patch the mission memory loop",
            "--spark-access-level",
            "4",
            "--runner-writable",
            "no",
            "--runner-label",
            "read-only chat runner",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema_version"], "spark.agent_operating_panel.v1")
        self.assertEqual(payload["strip"]["best_route"], "writable Spawner/Codex mission")
        self.assertEqual(payload["aoc"]["conversation_frame"]["current_mode"], "patch_work")
        self.assertEqual(payload["aoc"]["task_fit"]["recommended_route"], "writable_spawner_codex_mission")
        self.assertIn("source_ledger", payload)
        self.assertIn("sections", payload)
        self.assertTrue(
            any(section["section_id"] == "what_rec_needs" for section in payload["sections"]["sections"])
        )
        self.assertIn("black_box", payload)
        self.assertIn("memory_approval_inbox", payload)
        self.assertIn("stale_context_sweep", payload)
