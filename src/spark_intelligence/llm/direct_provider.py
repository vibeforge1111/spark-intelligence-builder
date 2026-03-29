from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.state.db import StateDB

_REQUEST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class DirectProviderRequest:
    provider_id: str
    provider_kind: str
    auth_method: str
    api_mode: str
    base_url: str | None
    model: str | None
    secret_value: str


@dataclass(frozen=True)
class DirectProviderGovernance:
    state_db_path: str
    source_kind: str
    source_ref: str
    summary: str
    reason_code: str
    policy_domain: str
    blocked_stage: str
    run_id: str | None = None
    request_id: str | None = None
    trace_ref: str | None = None
    provenance: dict[str, object] | None = None


def execute_direct_provider_prompt(
    *,
    provider: DirectProviderRequest,
    system_prompt: str,
    user_prompt: str,
    governance: DirectProviderGovernance | None = None,
) -> dict[str, object]:
    if governance and governance.state_db_path:
        state_db = StateDB(Path(governance.state_db_path))
        screening = screen_model_visible_text(
            state_db=state_db,
            source_kind=governance.source_kind,
            source_ref=governance.source_ref,
            text=_render_model_visible_prompt(system_prompt=system_prompt, user_prompt=user_prompt),
            summary=governance.summary,
            reason_code=governance.reason_code,
            policy_domain=governance.policy_domain,
            run_id=governance.run_id,
            request_id=governance.request_id,
            trace_ref=governance.trace_ref,
            blocked_stage=governance.blocked_stage,
            provenance=governance.provenance,
        )
        if not screening["allowed"]:
            raise RuntimeError("Direct provider execution blocked by the pre-model secret boundary.")
    if not provider.model:
        raise RuntimeError(f"Provider '{provider.provider_id}' has no default model configured.")
    if not provider.base_url:
        raise RuntimeError(f"Provider '{provider.provider_id}' has no base URL configured.")

    if provider.api_mode == "chat_completions":
        return _execute_chat_completions(provider=provider, system_prompt=system_prompt, user_prompt=user_prompt)
    if provider.api_mode == "anthropic_messages":
        return _execute_anthropic_messages(provider=provider, system_prompt=system_prompt, user_prompt=user_prompt)
    raise RuntimeError(
        f"Provider '{provider.provider_id}' uses unsupported direct execution mode '{provider.api_mode}'."
    )


def _execute_chat_completions(
    *,
    provider: DirectProviderRequest,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, object]:
    model_name = _normalize_chat_completions_model(provider)
    payload = {
        "model": model_name,
        "messages": _chat_messages(system_prompt=system_prompt, user_prompt=user_prompt),
        "temperature": 0.2,
    }
    response = _post_json(
        _join_url(provider.base_url, "chat/completions"),
        headers={
            "Authorization": f"Bearer {provider.secret_value}",
            "Content-Type": "application/json",
        },
        payload=payload,
    )
    content = _extract_chat_completion_text(response)
    return {
        "raw_response": content,
        "provider_id": provider.provider_id,
        "model": model_name,
        "api_mode": provider.api_mode,
        "response": response,
    }


def _execute_anthropic_messages(
    *,
    provider: DirectProviderRequest,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, object]:
    payload = {
        "model": provider.model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": _merge_prompts(system_prompt=system_prompt, user_prompt=user_prompt),
            }
        ],
    }
    response = _post_json(
        _join_url(_normalize_anthropic_base_url(provider.base_url), "messages"),
        headers={
            "x-api-key": provider.secret_value,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        payload=payload,
    )
    content = _extract_anthropic_text(response)
    return {
        "raw_response": content,
        "provider_id": provider.provider_id,
        "model": provider.model,
        "api_mode": provider.api_mode,
        "response": response,
    }


def _post_json(url: str, *, headers: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Provider HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Provider network error: {exc.reason}") from exc


def _chat_messages(*, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_prompt.strip()})
    return messages


def _merge_prompts(*, system_prompt: str, user_prompt: str) -> str:
    if system_prompt.strip():
        return f"{system_prompt.strip()}\n\n{user_prompt.strip()}".strip()
    return user_prompt.strip()


def _render_model_visible_prompt(*, system_prompt: str, user_prompt: str) -> str:
    return _merge_prompts(system_prompt=system_prompt, user_prompt=user_prompt)


def _extract_chat_completion_text(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Chat completion response contained no choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise RuntimeError("Chat completion response contained no assistant message.")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and str(item.get("type") or "") == "text"
        ]
        joined = " ".join(part for part in text_parts if part)
        if joined:
            return joined
    raise RuntimeError("Chat completion response contained no text content.")


def _extract_anthropic_text(payload: dict[str, object]) -> str:
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        raise RuntimeError("Anthropic response contained no content blocks.")
    text_parts = [
        str(item.get("text") or "").strip()
        for item in content
        if isinstance(item, dict) and str(item.get("type") or "") == "text"
    ]
    joined = " ".join(part for part in text_parts if part)
    if not joined:
        raise RuntimeError("Anthropic response contained no text content.")
    return joined


def _join_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def _normalize_anthropic_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _normalize_chat_completions_model(provider: DirectProviderRequest) -> str:
    model_name = str(provider.model or "").strip()
    if (
        provider.provider_id == "custom"
        and model_name.lower().startswith("openai/")
        and "openrouter.ai" not in str(provider.base_url or "").lower()
    ):
        _, _, stripped = model_name.partition("/")
        return stripped or model_name
    return model_name
