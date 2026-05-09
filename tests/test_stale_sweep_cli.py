from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class StaleSweepCliTests(SparkTestCase):
    def test_stale_sweep_cli_records_contradiction_black_box_event(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "stale-sweep",
            "--home",
            str(self.home),
            "--live-claim-json",
            json.dumps(
                {
                    "claim_key": "spark_access_level",
                    "value": "Level 4",
                    "source": "current_diagnostics",
                    "freshness": "live_probed",
                    "source_ref": "diag:current",
                }
            ),
            "--context-claim-json",
            json.dumps(
                {
                    "claim_key": "spark_access_level",
                    "value": "Level 1",
                    "source": "approved_memory",
                    "freshness": "stale",
                    "source_ref": "memory:old",
                }
            ),
            "--record-contradictions",
            "--request-id",
            "req-stale-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        sweep_payload = json.loads(stdout)
        self.assertEqual(sweep_payload["status"], "needs_review")
        self.assertEqual(sweep_payload["counts"]["stale"], 1)
        self.assertEqual(sweep_payload["counts"]["recorded_contradictions"], 1)
        self.assertEqual(sweep_payload["counts"]["recorded_agent_events"], 1)
        self.assertEqual(sweep_payload["stale_items"][0]["action_type"], "mark_memory_stale")

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--request-id",
            "req-stale-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        panel_payload = json.loads(stdout)
        event_types = {entry["event_type"] for entry in panel_payload["black_box"]["entries"]}
        self.assertIn("contradiction_found", event_types)
