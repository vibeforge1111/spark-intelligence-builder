from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class AgentEventProducerCliTests(SparkTestCase):
    def test_route_probe_cli_feeds_aoc_black_box(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "route-probe",
            "spark_memory",
            "--home",
            str(self.home),
            "--status",
            "success",
            "--latency-ms",
            "33",
            "--eval-ref",
            "pytest:memory-smoke",
            "--source-ref",
            "test:memory-smoke",
            "--request-id",
            "req-cli-probe",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        probe_payload = json.loads(stdout)
        self.assertEqual(probe_payload["status"], "success")
        self.assertTrue(probe_payload["agent_event_id"])

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--request-id",
            "req-cli-probe",
            "--user-message",
            "check memory route",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        panel_payload = json.loads(stdout)
        self.assertEqual(panel_payload["black_box"]["counts"]["entries"], 1)
        self.assertEqual(panel_payload["black_box"]["entries"][0]["event_type"], "capability_probed")
        self.assertEqual(panel_payload["black_box"]["entries"][0]["route_chosen"], "spark_memory")
        self.assertTrue(
            any(section["section_id"] == "black_box_recorder" for section in panel_payload["sections"]["sections"])
        )
