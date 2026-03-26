from __future__ import annotations

import json
from unittest.mock import patch

from spark_intelligence.llm.direct_provider import DirectProviderRequest, execute_direct_provider_prompt

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
