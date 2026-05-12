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

    def test_self_panel_cli_accepts_stale_context_claims(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--live-claim-json",
            json.dumps(
                {
                    "claim_key": "spark_access_level",
                    "value": "Level 4",
                    "source": "current_diagnostics",
                    "freshness": "live_probed",
                }
            ),
            "--context-claim-json",
            json.dumps(
                {
                    "claim_key": "spark_access_level",
                    "value": "Level 1",
                    "source": "approved_memory",
                    "freshness": "stale",
                }
            ),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["stale_context_sweep"]["counts"]["stale"], 1)
        self.assertEqual(payload["source_ledger"]["counts"]["stale"], 1)
        contradictions = [
            section for section in payload["sections"]["sections"] if section["section_id"] == "contradictions"
        ][0]
        self.assertEqual(contradictions["status"], "needs_review")

    def test_self_panel_cli_accepts_execution_lane_json(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--spark-access-level",
            "4",
            "--execution-lane-json",
            json.dumps(
                {
                    "docker": {"available": True, "selected": True, "probed": False},
                    "workspace_sandbox": True,
                    "level5_whole_computer_claim": True,
                }
            ),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        lane = payload["aoc"]["execution_lane"]
        self.assertEqual(lane["docker"]["available"], True)
        self.assertEqual(lane["docker"]["selected"], True)
        self.assertEqual(lane["docker"]["probed"], False)
        self.assertTrue(lane["workspace_sandbox"])
        self.assertFalse(lane["level5_whole_computer_claim_allowed"])

    def test_self_panel_cli_accepts_live_state_json(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--runner-writable",
            "yes",
            "--live-state-json",
            json.dumps(
                {
                    "status": "healthy",
                    "spawner_ok": True,
                    "telegram_ok": True,
                    "providers_ok": True,
                    "memory_ok": True,
                    "checked_at": "2026-05-12T00:10:00Z",
                    "source_ref": "spark live status",
                }
            ),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        live_state = payload["aoc"]["live_state"]
        self.assertTrue(live_state["present"])
        self.assertEqual(live_state["status"], "healthy")
        self.assertTrue(live_state["spawner_ok"])
        source_items = {item["source"]: item for item in payload["source_ledger"]["items"]}
        self.assertTrue(source_items["live_spark_state"]["present"])
        self.assertEqual(source_items["live_spark_state"]["freshness"], "live_probed")
