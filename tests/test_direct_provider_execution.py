from __future__ import annotations

import json
import os
from unittest.mock import patch

from spark_intelligence.llm.direct_provider import (
    DirectProviderGovernance,
    DirectProviderRequest,
    execute_direct_provider_prompt,
)
from spark_intelligence.llm.provider_wrapper import main as provider_wrapper_main
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class DirectProviderExecutionTests(SparkTestCase):
    def test_chat_completions_execution_uses_bearer_secret(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout: int = 30):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "OpenAI-style reply",
                            }
                        }
                    ]
                }
            )

        provider = DirectProviderRequest(
            provider_id="openai",
            provider_kind="openai",
            auth_method="api_key_env",
            api_mode="chat_completions",
            base_url="https://api.openai.com/v1",
            model="gpt-5.4",
            secret_value="openai-secret",
        )

        with patch("spark_intelligence.llm.direct_provider.urllib.request.urlopen", side_effect=fake_urlopen):
            payload = execute_direct_provider_prompt(
                provider=provider,
                system_prompt="System instructions",
                user_prompt="User task",
            )

        headers = {str(key).lower(): value for key, value in captured["headers"].items()}
        self.assertEqual(payload["raw_response"], "OpenAI-style reply")
        self.assertEqual(captured["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer openai-secret")
        self.assertEqual(captured["body"]["model"], "gpt-5.4")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")
        self.assertEqual(captured["body"]["messages"][1]["content"], "User task")

    def test_custom_chat_completions_strips_openai_prefix_for_model(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout: int = 30):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Normalized custom reply",
                            }
                        }
                    ]
                }
            )

        provider = DirectProviderRequest(
            provider_id="custom",
            provider_kind="custom",
            auth_method="api_key_env",
            api_mode="chat_completions",
            base_url="https://api.minimax.io/v1",
            model="openai/MiniMax-M2.7",
            secret_value="custom-secret",
        )

        with patch("spark_intelligence.llm.direct_provider.urllib.request.urlopen", side_effect=fake_urlopen):
            payload = execute_direct_provider_prompt(
                provider=provider,
                system_prompt="System instructions",
                user_prompt="User task",
            )

        self.assertEqual(payload["raw_response"], "Normalized custom reply")
        self.assertEqual(payload["model"], "MiniMax-M2.7")
        self.assertEqual(captured["body"]["model"], "MiniMax-M2.7")

    def test_governed_direct_execution_records_redacted_capability_success(self) -> None:
        def fake_urlopen(request, timeout: int = 30):
            return _FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "OK",
                            }
                        }
                    ]
                }
            )

        provider = DirectProviderRequest(
            provider_id="custom",
            provider_kind="custom",
            auth_method="api_key_env",
            api_mode="chat_completions",
            base_url="https://api.example.com/v1",
            model="glm-5.1",
            secret_value="provider-secret",
        )
        governance = DirectProviderGovernance(
            state_db_path=str(self.state_db.path),
            source_kind="provider_health_probe",
            source_ref="test.direct_provider",
            summary="test prompt screening",
            reason_code="test_direct_provider_prompt",
            policy_domain="provider_health",
            blocked_stage="pre_model",
            run_id="run-direct-provider-test",
            request_id="req-direct-provider-test",
            trace_ref="trace:direct-provider-test",
        )

        with patch("spark_intelligence.llm.direct_provider.urllib.request.urlopen", side_effect=fake_urlopen):
            payload = execute_direct_provider_prompt(
                provider=provider,
                system_prompt="Reply OK.",
                user_prompt="Reply OK.",
                governance=governance,
            )

        self.assertEqual(payload["raw_response"], "OK")
        events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=5)
        provider_events = [
            event
            for event in events
            if event.get("component") == "direct_provider"
            and event.get("facts_json", {}).get("provider_id") == "custom"
        ]
        self.assertTrue(provider_events)
        facts = provider_events[0]["facts_json"]
        self.assertEqual(facts["capability_key"], "custom")
        self.assertEqual(facts["model"], "glm-5.1")
        self.assertEqual(facts["eval_ref"], "direct_provider_execution")
        self.assertIsInstance(facts["route_latency_ms"], int)
        self.assertNotIn("provider-secret", json.dumps(provider_events[0], sort_keys=True))

    def test_anthropic_execution_uses_messages_api_headers(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout: int = 30):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeHttpResponse(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "Anthropic reply",
                        }
                    ]
                }
            )

        provider = DirectProviderRequest(
            provider_id="anthropic",
            provider_kind="anthropic",
            auth_method="api_key_env",
            api_mode="anthropic_messages",
            base_url="https://api.anthropic.com",
            model="claude-opus-4-6",
            secret_value="anthropic-secret",
        )

        with patch("spark_intelligence.llm.direct_provider.urllib.request.urlopen", side_effect=fake_urlopen):
            payload = execute_direct_provider_prompt(
                provider=provider,
                system_prompt="System instructions",
                user_prompt="User task",
            )

        headers = {str(key).lower(): value for key, value in captured["headers"].items()}
        self.assertEqual(payload["raw_response"], "Anthropic reply")
        self.assertEqual(captured["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "anthropic-secret")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["body"]["model"], "claude-opus-4-6")
        self.assertIn("System instructions", captured["body"]["messages"][0]["content"])
        self.assertIn("User task", captured["body"]["messages"][0]["content"])

    def test_unsupported_direct_execution_mode_fails_closed(self) -> None:
        provider = DirectProviderRequest(
            provider_id="openai-codex",
            provider_kind="openai-codex",
            auth_method="oauth",
            api_mode="codex_responses",
            base_url="https://chatgpt.com/backend-api/codex",
            model="gpt-5.4",
            secret_value="oauth-secret",
        )

        with self.assertRaisesRegex(RuntimeError, "unsupported direct execution mode"):
            execute_direct_provider_prompt(
                provider=provider,
                system_prompt="System instructions",
                user_prompt="User task",
            )

    def test_provider_wrapper_blocks_secret_like_prompt_with_state_db_context(self) -> None:
        system_prompt_path = self.home / "system.txt"
        user_prompt_path = self.home / "user.txt"
        response_path = self.home / "response.json"
        system_prompt_path.write_text("System instructions", encoding="utf-8")
        user_prompt_path.write_text("User token " + "sk-" + "abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")

        env = {
            "SPARK_INTELLIGENCE_PROVIDER_ID": "custom",
            "SPARK_INTELLIGENCE_PROVIDER_KIND": "custom",
            "SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD": "api_key_env",
            "SPARK_INTELLIGENCE_PROVIDER_API_MODE": "chat_completions",
            "SPARK_INTELLIGENCE_PROVIDER_EXECUTION_TRANSPORT": "direct_http",
            "SPARK_INTELLIGENCE_PROVIDER_BASE_URL": "https://api.example.com/v1",
            "SPARK_INTELLIGENCE_PROVIDER_MODEL": "MiniMax-M2.7",
            "SPARK_INTELLIGENCE_PROVIDER_SECRET": "provider-secret",
            "SPARK_INTELLIGENCE_STATE_DB_PATH": str(self.state_db.path),
            "SPARK_INTELLIGENCE_RUN_ID": "run-provider-wrapper",
            "SPARK_INTELLIGENCE_REQUEST_ID": "req-provider-wrapper",
            "SPARK_INTELLIGENCE_TRACE_REF": "trace:provider-wrapper",
        }

        with patch.dict(os.environ, env, clear=False), patch(
            "spark_intelligence.llm.direct_provider.urllib.request.urlopen",
            side_effect=AssertionError("network should not run when prompt is blocked"),
        ):
            with self.assertRaisesRegex(RuntimeError, "pre-model secret boundary"):
                provider_wrapper_main(
                    [
                        str(system_prompt_path),
                        str(user_prompt_path),
                        str(response_path),
                    ]
                )

        self.assertFalse(response_path.exists())
        self.assertTrue(latest_events_by_type(self.state_db, event_type="secret_boundary_violation", limit=10))
        with self.state_db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM quarantine_records").fetchone()
        self.assertGreaterEqual(int(row["count"]), 1)
