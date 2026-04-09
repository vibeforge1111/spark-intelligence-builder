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

    def test_harness_execute_supports_follow_up_harness_chain(self) -> None:
        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks; Builder delivers."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        def fake_voice_hook(*, hook, payload, **kwargs):
            if hook == "voice.status":
                return (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                )
            self.assertEqual(payload["text"], "Spark Researcher thinks; Builder delivers.")
            return (
                {
                    "result": {
                        "provider_id": "elevenlabs",
                        "voice_id": "voice-123",
                        "model_id": "eleven_turbo_v2_5",
                        "mime_type": "audio/ogg",
                        "filename": "voice-reply-test.ogg",
                        "voice_compatible": True,
                        "audio_base64": "aGVsbG8=",
                    }
                },
                "domain-chip-voice-comms",
            )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
                return_value=FakeResearcherResult(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_voice_hook",
                side_effect=fake_voice_hook,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder?",
                "--home",
                str(self.home),
                "--harness-id",
                "researcher.advisory",
                "--then-harness-id",
                "voice.io",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["chain_status"], "completed")
        self.assertEqual(len(payload["chained_results"]), 1)
        self.assertEqual(payload["chained_results"][0]["envelope"]["harness_id"], "voice.io")

    def test_harness_plan_supports_named_recipe(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "plan",
            "What is the difference between Spark Researcher and Builder?",
            "--home",
            str(self.home),
            "--recipe",
            "advisory_voice_reply",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["harness_id"], "researcher.advisory")
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")

    def test_harness_execute_supports_named_recipe(self) -> None:
        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks; Builder delivers."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        def fake_voice_hook(*, hook, payload, **kwargs):
            if hook == "voice.status":
                return (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                )
            self.assertIn("Spark Researcher thinks; Builder delivers.", payload["text"])
            return (
                {
                    "result": {
                        "provider_id": "elevenlabs",
                        "voice_id": "voice-123",
                        "model_id": "eleven_turbo_v2_5",
                        "mime_type": "audio/ogg",
                        "filename": "voice-reply-test.ogg",
                        "voice_compatible": True,
                        "audio_base64": "aGVsbG8=",
                    }
                },
                "domain-chip-voice-comms",
            )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
                return_value=FakeResearcherResult(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_voice_hook",
                side_effect=fake_voice_hook,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder?",
                "--home",
                str(self.home),
                "--recipe",
                "advisory_voice_reply",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["chain_status"], "completed")
        self.assertEqual(payload["chained_results"][0]["envelope"]["harness_id"], "voice.io")

    def test_harness_plan_auto_selects_recipe_for_voice_advisory_task(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "plan",
            "What is the difference between Spark Researcher and Builder? Answer in voice.",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["recipe"]["selection_mode"], "auto")

    def test_harness_execute_auto_selects_recipe_for_voice_advisory_task(self) -> None:
        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks; Builder delivers."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        def fake_voice_hook(*, hook, payload, **kwargs):
            if hook == "voice.status":
                return (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                )
            self.assertIn("Spark Researcher thinks; Builder delivers.", payload["text"])
            return (
                {
                    "result": {
                        "provider_id": "elevenlabs",
                        "voice_id": "voice-123",
                        "model_id": "eleven_turbo_v2_5",
                        "mime_type": "audio/ogg",
                        "filename": "voice-reply-test.ogg",
                        "voice_compatible": True,
                        "audio_base64": "aGVsbG8=",
                    }
                },
                "domain-chip-voice-comms",
            )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
                return_value=FakeResearcherResult(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_voice_hook",
                side_effect=fake_voice_hook,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder? Answer in voice.",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["recipe"]["selection_mode"], "auto")
        self.assertEqual(payload["chain_status"], "completed")
