from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class ResearcherBridgeProviderResolutionTests(SparkTestCase):
    def test_build_researcher_reply_uses_resolved_provider_model_family(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "anthropic",
            "--home",
            str(self.home),
            "--api-key",
            "anthropic-secret",
            "--model",
            "claude-opus-4-6",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, str] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-1",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="How should I answer this?",
            )

        self.assertEqual(captured["model"], "claude")
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.provider_id, "anthropic")
        self.assertEqual(result.provider_auth_method, "api_key_env")
        self.assertEqual(result.provider_model, "claude-opus-4-6")
        self.assertEqual(result.provider_model_family, "claude")

    def test_build_researcher_reply_fails_closed_when_provider_auth_is_unresolved(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openrouter",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENROUTER_KEY",
            "--model",
            "anthropic/claude-3.7-sonnet",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-1",
            channel_kind="telegram",
            user_message="Should I reply?",
        )

        self.assertEqual(result.mode, "bridge_error")
        self.assertEqual(result.provider_id, None)
        self.assertEqual(result.provider_model_family, "generic")
        self.assertIn("missing secret value", result.reply_text)
