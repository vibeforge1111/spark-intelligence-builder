from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class AgentEventProducerCliTests(SparkTestCase):
    def test_source_used_cli_feeds_aoc_black_box(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "source-used",
            "current_diagnostics",
            "--home",
            str(self.home),
            "--role",
            "health_truth",
            "--freshness",
            "live_probed",
            "--source-ref",
            "diagnostics:scan-1",
            "--summary",
            "Builder healthy, Browser unavailable.",
            "--user-intent",
            "diagnose",
            "--selected-route",
            "answer_in_chat",
            "--confidence",
            "high",
            "--request-id",
            "req-cli-source-used",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        source_payload = json.loads(stdout)
        self.assertEqual(source_payload["event_type"], "source_used")
        self.assertTrue(source_payload["event_id"])

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "black-box",
            "--home",
            str(self.home),
            "--request-id",
            "req-cli-source-used",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        black_box = json.loads(stdout)
        entry = black_box["entries"][0]
        self.assertEqual(entry["event_type"], "source_used")
        self.assertEqual(entry["sources_used"][0]["source"], "current_diagnostics")
        self.assertEqual(entry["sources_used"][0]["freshness"], "live_probed")

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
