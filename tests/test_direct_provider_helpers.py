"""Coverage for the pure helpers inside llm.direct_provider.

The end-to-end execute paths are covered through provider_wrapper integration
tests. The small helpers that join URLs, normalise the Anthropic base URL,
strip provider prefixes from chat completion model names, merge prompts, and
extract assistant text from provider payloads are not asserted today. These
tests pin the contract so a future SDK refactor cannot quietly drop the
secret-redaction or the prefix-stripping behavior.
"""
from __future__ import annotations

import pytest

from spark_intelligence.llm.direct_provider import (
    DirectProviderRequest,
    _chat_messages,
    _extract_anthropic_text,
    _extract_chat_completion_text,
    _join_url,
    _merge_prompts,
    _normalize_anthropic_base_url,
    _normalize_chat_completions_model,
    _redact_provider_error,
    _render_model_visible_prompt,
)


def _provider(
    *,
    provider_id: str = "custom",
    base_url: str = "https://example.com",
    model: str = "model-1",
    secret_value: str = "secret",
) -> DirectProviderRequest:
    return DirectProviderRequest(
        provider_id=provider_id,
        provider_kind="custom",
        auth_method="api_key_env",
        api_mode="chat_completions",
        base_url=base_url,
        model=model,
        secret_value=secret_value,
    )


class TestJoinUrl:
    def test_no_double_slash(self) -> None:
        assert _join_url("https://api.example.com/", "/path") == "https://api.example.com/path"

    def test_appends_missing_slash(self) -> None:
        assert _join_url("https://api.example.com", "path") == "https://api.example.com/path"

    def test_trailing_slash_preserved_on_suffix(self) -> None:
        # The function only strips the trailing slash on base and leading slash on suffix.
        assert _join_url("https://api.example.com/", "path/") == "https://api.example.com/path/"


class TestNormalizeAnthropicBaseUrl:
    def test_adds_v1_when_missing(self) -> None:
        assert _normalize_anthropic_base_url("https://api.anthropic.com") == "https://api.anthropic.com/v1"

    def test_keeps_existing_v1(self) -> None:
        assert _normalize_anthropic_base_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1"

    def test_trailing_slash_is_normalised_then_v1_added(self) -> None:
        # The function rstrips '/' first, so trailing slash is removed and v1 is appended.
        assert _normalize_anthropic_base_url("https://api.anthropic.com/v1/") == "https://api.anthropic.com/v1"


class TestNormalizeChatCompletionsModel:
    def test_openai_prefix_stripped_for_custom_non_openrouter(self) -> None:
        assert _normalize_chat_completions_model(_provider(model="openai/gpt-4o")) == "gpt-4o"

    def test_openai_prefix_preserved_for_openrouter(self) -> None:
        provider = _provider(base_url="https://openrouter.ai/api/v1", model="openai/gpt-4o")
        assert _normalize_chat_completions_model(provider) == "openai/gpt-4o"

    def test_non_openai_prefix_unchanged(self) -> None:
        provider = _provider(model="anthropic/claude-3")
        assert _normalize_chat_completions_model(provider) == "anthropic/claude-3"

    def test_non_custom_provider_unchanged(self) -> None:
        provider = _provider(provider_id="openai", model="openai/gpt-4o")
        assert _normalize_chat_completions_model(provider) == "openai/gpt-4o"

    def test_blank_model_returns_empty(self) -> None:
        provider = _provider(model="")
        assert _normalize_chat_completions_model(provider) == ""


class TestMergePromptsAndChatMessages:
    def test_merge_with_system_prompt(self) -> None:
        assert _merge_prompts(system_prompt="sys", user_prompt="user") == "sys\n\nuser"

    def test_merge_without_system_prompt_returns_user_only(self) -> None:
        assert _merge_prompts(system_prompt="", user_prompt="user") == "user"

    def test_merge_strips_whitespace(self) -> None:
        assert _merge_prompts(system_prompt="  sys  ", user_prompt="  user  ") == "sys\n\nuser"

    def test_chat_messages_includes_system_when_present(self) -> None:
        msgs = _chat_messages(system_prompt="sys", user_prompt="user")
        assert msgs == [{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]

    def test_chat_messages_skips_system_when_blank(self) -> None:
        msgs = _chat_messages(system_prompt="   ", user_prompt="user")
        assert msgs == [{"role": "user", "content": "user"}]

    def test_render_model_visible_prompt_delegates_to_merge(self) -> None:
        # Public-API alias to _merge_prompts.
        assert _render_model_visible_prompt(system_prompt="sys", user_prompt="user") == "sys\n\nuser"


class TestExtractChatCompletionText:
    def test_string_content_returned(self) -> None:
        payload = {"choices": [{"message": {"content": "hello"}}]}
        assert _extract_chat_completion_text(payload) == "hello"

    def test_list_content_concatenated(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
            ]
        }
        assert _extract_chat_completion_text(payload) == "a b"

    def test_no_choices_raises(self) -> None:
        with pytest.raises(RuntimeError, match="no choices"):
            _extract_chat_completion_text({})

    def test_no_message_raises(self) -> None:
        with pytest.raises(RuntimeError, match="no assistant message"):
            _extract_chat_completion_text({"choices": [{}]})

    def test_no_text_content_raises(self) -> None:
        with pytest.raises(RuntimeError, match="no text content"):
            _extract_chat_completion_text({"choices": [{"message": {"content": ""}}]})


class TestExtractAnthropicText:
    def test_text_block_returned(self) -> None:
        payload = {"content": [{"type": "text", "text": "hello"}]}
        assert _extract_anthropic_text(payload) == "hello"

    def test_multiple_text_blocks_joined(self) -> None:
        payload = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        assert _extract_anthropic_text(payload) == "a b"

    def test_non_text_blocks_filtered(self) -> None:
        payload = {"content": [{"type": "tool_use", "name": "tool"}, {"type": "text", "text": "real"}]}
        assert _extract_anthropic_text(payload) == "real"

    def test_no_content_blocks_raises(self) -> None:
        with pytest.raises(RuntimeError, match="no content blocks"):
            _extract_anthropic_text({})

    def test_no_text_raises(self) -> None:
        # All blocks non-text yields the "no text content" error.
        with pytest.raises(RuntimeError, match="no text content"):
            _extract_anthropic_text({"content": [{"type": "tool_use", "name": "tool"}]})


class TestRedactProviderError:
    def test_secret_value_replaced_in_message(self) -> None:
        provider = _provider(secret_value="supersecret123")
        out = _redact_provider_error(RuntimeError("the secret is supersecret123"), provider)
        assert "supersecret123" not in out
        assert "[REDACTED]" in out

    def test_no_secret_value_passes_message_through(self) -> None:
        provider = _provider(secret_value="")
        out = _redact_provider_error(RuntimeError("plain error"), provider)
        assert out == "plain error"

    def test_message_truncated_at_240_chars(self) -> None:
        provider = _provider(secret_value="")
        long_msg = "x" * 500
        out = _redact_provider_error(RuntimeError(long_msg), provider)
        assert len(out) == 240
