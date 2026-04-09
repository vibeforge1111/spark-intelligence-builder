from __future__ import annotations

import json
from unittest.mock import patch

from tests.test_support import SparkTestCase, create_fake_hook_chip


class HarnessCliTests(SparkTestCase):
    def test_harness_plan_reports_browser_harness_for_url_task(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "plan",
            "Open https://example.com and inspect it.",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["harness_id"], "browser.grounded")
        self.assertEqual(payload["backend_kind"], "browser_bridge")

    def test_harness_execute_runs_researcher_advisory_runner(self) -> None:
        class FakeResult:
            reply_text = "Here is the answer."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            return_value=FakeResult(),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "Draft a direct answer for this operator question.",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["envelope"]["harness_id"], "researcher.advisory")
        self.assertEqual(payload["artifacts"]["reply_text"], "Here is the answer.")

    def test_harness_execute_respects_forced_harness_id(self) -> None:
        class FakeResult:
            reply_text = "Forced harness reply."
            evidence_summary = "status=ok"
            trace_ref = "trace:forced"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "forced_harness"
            active_chip_key = None

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            return_value=FakeResult(),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder?",
                "--home",
                str(self.home),
                "--harness-id",
                "researcher.advisory",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["envelope"]["harness_id"], "researcher.advisory")
        self.assertEqual(payload["envelope"]["route_mode"], "forced_harness")
        self.assertEqual(payload["artifacts"]["reply_text"], "Forced harness reply.")

    def test_harness_status_returns_registry_and_runtime_payload(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("harness_registry", payload)
        self.assertIn("harness_runtime", payload)
