from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class ResearcherBridgeProviderResolutionTests(SparkTestCase):
    def test_build_researcher_reply_uses_direct_provider_chat_fallback_for_under_supported_conversation(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["advisory_model"] = model
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str):
            captured["provider_id"] = provider.provider_id
            captured["provider_model"] = provider.model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Hey there. How can I help?"}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(captured["advisory_model"], "generic")
        self.assertEqual(captured["provider_id"], "custom")
        self.assertEqual(captured["provider_model"], "MiniMax-M2.7")
        self.assertIn("1:1 messaging conversation", str(captured["system_prompt"]))
        self.assertIn("[fallback_mode=conversational_under_supported]", str(captured["user_prompt"]))
        self.assertEqual(result.reply_text, "Hey there. How can I help?")
        self.assertEqual(result.trace_ref, "trace:under-supported")
        self.assertEqual(result.provider_id, "custom")
        self.assertEqual(result.provider_execution_transport, "direct_http")
        self.assertEqual(result.evidence_summary, "status=under_supported provider_fallback=direct_http_chat")

    def test_build_researcher_reply_respects_disabled_conversational_fallback_policy(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.routing.conversational_fallback_enabled", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Researcher-side provider execution reply"},
                "trace_path": "trace:execution",
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
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("direct provider fallback should be disabled"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-fallback-disabled",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(result.routing_decision, "provider_execution")
        self.assertEqual(result.reply_text, "Researcher-side provider execution reply")

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
        captured_execution: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            captured_execution["model"] = model
            captured_execution["command_override"] = list(command_override or [])
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Provider-backed reply"},
                "trace_path": "trace:execution",
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
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
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
        self.assertEqual(captured_execution["model"], "claude")
        self.assertEqual(
            captured_execution["command_override"],
            [
                sys.executable,
                "-m",
                "spark_intelligence.llm.provider_wrapper",
                "{system_prompt_path}",
                "{user_prompt_path}",
                "{response_path}",
            ],
        )
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.reply_text, "Provider-backed reply")
        self.assertEqual(result.provider_id, "anthropic")
        self.assertEqual(result.provider_auth_method, "api_key_env")
        self.assertEqual(result.provider_model, "claude-opus-4-6")
        self.assertEqual(result.provider_model_family, "claude")
        self.assertEqual(result.provider_execution_transport, "direct_http")

    def test_build_researcher_reply_keeps_codex_on_external_wrapper_transport(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)

        import json

        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["advisory_model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            captured["execution_model"] = model
            captured["command_override"] = command_override
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Codex-backed reply"},
                "trace_path": "trace:execution",
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
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-3",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Should codex run through the shared bridge?",
            )

        self.assertEqual(captured["advisory_model"], "codex")
        self.assertEqual(captured["execution_model"], "codex")
        self.assertEqual(captured["command_override"], None)
        self.assertEqual(result.reply_text, "Codex-backed reply")
        self.assertEqual(result.provider_id, "openai-codex")
        self.assertEqual(result.provider_auth_method, "oauth")
        self.assertEqual(result.provider_model_family, "codex")
        self.assertEqual(result.provider_execution_transport, "external_cli_wrapper")

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
