from __future__ import annotations

import ipaddress
import json
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from spark_intelligence.auth.providers import get_provider_spec
from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.observability.store import record_event
from spark_intelligence.security.https_endpoint import (
    PinnedHTTPSConnection,
    ResolvedHTTPSEndpoint,
    canonical_https_origin,
    post_https_bytes,
    resolve_public_https_endpoint,
)
from spark_intelligence.security.redaction import redact_text
from spark_intelligence.state.db import StateDB

_REQUEST_TIMEOUT_SECONDS = 60
_MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "0.0.0.0",
    "metadata.google.internal",
    "169.254.169.254",
})


def _validate_base_url(url: str) -> None:
    """Reject provider URLs that resolve outside the public HTTPS internet."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(
            f"SSRF policy: provider base URL must use HTTPS, got '{parsed.scheme}': {url}"
        )
    hostname = parsed.hostname or ""
    if not hostname:
        raise RuntimeError(f"SSRF policy: provider base URL has no hostname: {url}")
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise RuntimeError(
            f"SSRF policy: provider base URL targets blocked hostname '{hostname}': {url}"
        )
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise RuntimeError(
            f"SSRF policy: provider base URL hostname could not be resolved: {hostname}"
        ) from None
    for _, _, _, _, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise RuntimeError(
                f"SSRF policy: provider base URL resolved to invalid IP '{ip_str}': {url}"
            ) from None
        if _is_private_or_reserved(ip):
            raise RuntimeError(
                "SSRF policy: provider base URL resolves to private/reserved IP "
                f"'{ip_str}' (hostname: {hostname}): {url}"
            )


def _is_private_or_reserved(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
    )


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


_ResolvedProviderEndpoint = ResolvedHTTPSEndpoint
_PinnedHTTPSConnection = PinnedHTTPSConnection


def execute_direct_provider_prompt(
    *,
    provider: DirectProviderRequest,
    system_prompt: str,
    user_prompt: str,
    governance: DirectProviderGovernance | None = None,
    tools: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    state_db: StateDB | None = None
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

    started = perf_counter()
    try:
        if not provider.model:
            raise RuntimeError(f"Provider '{provider.provider_id}' has no default model configured.")
        if not provider.base_url:
            raise RuntimeError(f"Provider '{provider.provider_id}' has no base URL configured.")

        if provider.api_mode == "chat_completions":
            payload = _execute_chat_completions(
                provider=provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=tools,
            )
        elif provider.api_mode == "anthropic_messages":
            payload = _execute_anthropic_messages(
                provider=provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools=tools,
            )
        else:
            raise RuntimeError(
                "Provider uses an unsupported direct execution mode on this path. "
                "Use the configured Researcher bridge for non-direct provider execution."
            )
    except Exception as exc:
        failure_reason = _redact_provider_error(exc, provider)
        _record_provider_execution_event(
            state_db=state_db,
            provider=provider,
            governance=governance,
            event_type="dispatch_failed",
            summary=f"Direct provider {provider.provider_id} failed.",
            route_latency_ms=_elapsed_ms(started),
            failure_reason=failure_reason,
        )
        raise RuntimeError(failure_reason) from None
    _record_provider_execution_event(
        state_db=state_db,
        provider=provider,
        governance=governance,
        event_type="tool_result_received",
        summary=f"Direct provider {provider.provider_id} produced a response.",
        route_latency_ms=_elapsed_ms(started),
        failure_reason=None,
    )
    return payload


def _record_provider_execution_event(
    *,
    state_db: StateDB | None,
    provider: DirectProviderRequest,
    governance: DirectProviderGovernance | None,
    event_type: str,
    summary: str,
    route_latency_ms: int,
    failure_reason: str | None,
) -> None:
    if state_db is None or governance is None:
        return
    facts: dict[str, object] = {
        "capability_key": provider.provider_id,
        "provider_id": provider.provider_id,
        "provider_kind": provider.provider_kind,
        "api_mode": provider.api_mode,
        "auth_method": provider.auth_method,
        "route_latency_ms": route_latency_ms,
        "eval_coverage_status": "observed",
        "eval_ref": "direct_provider_execution",
    }
    if provider.model:
        facts["model"] = provider.model
    if failure_reason:
        facts["failure_reason"] = failure_reason
    record_event(
        state_db,
        event_type=event_type,
        component="direct_provider",
        summary=summary,
        run_id=governance.run_id,
        request_id=governance.request_id,
        trace_ref=governance.trace_ref,
        reason_code="direct_provider_execution",
        provenance={
            "source_kind": governance.source_kind,
            "source_ref": governance.source_ref,
            "provider_id": provider.provider_id,
            "provider_kind": provider.provider_kind,
            "api_mode": provider.api_mode,
            **(governance.provenance or {}),
        },
        facts=facts,
        status="failed" if event_type == "dispatch_failed" else "recorded",
        severity="high" if event_type == "dispatch_failed" else "medium",
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _redact_provider_error(exc: Exception, provider: DirectProviderRequest) -> str:
    message = str(exc)
    if provider.secret_value:
        message = message.replace(provider.secret_value, "[REDACTED]")
    message = redact_text(message).strip()
    return (message or "Direct provider execution failed.")[:240]


def _execute_chat_completions(
    *,
    provider: DirectProviderRequest,
    system_prompt: str,
    user_prompt: str,
    tools: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    model_name = _normalize_chat_completions_model(provider)
    payload: dict[str, object] = {
        "model": model_name,
        "messages": _chat_messages(system_prompt=system_prompt, user_prompt=user_prompt),
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    if tools:
        payload["tools"] = tools
    response = _post_json(
        _join_url(provider.base_url, "chat/completions"),
        headers={
            "Authorization": f"Bearer {provider.secret_value}",
            "Content-Type": "application/json",
        },
        payload=payload,
        provider=provider,
    )
    content = _extract_chat_completion_text(response)
    return {
        "raw_response": content,
        "provider_id": provider.provider_id,
        "model": model_name,
        "api_mode": provider.api_mode,
        "response": response,
    }


# Anthropic prompt-caching: when the system prompt is long enough to be
# cache-eligible (~1024 tokens, ~4KB), send it as a structured `system`
# block with cache_control so repeat calls within the 5-minute ephemeral
# window reuse the cached prefix instead of re-billing it as fresh input
# tokens. Short prompts retain the existing plain system-field shape.
_ANTHROPIC_SYSTEM_CACHE_MIN_CHARS = 4096


def _execute_anthropic_messages(
    *,
    provider: DirectProviderRequest,
    system_prompt: str,
    user_prompt: str,
    tools: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": provider.model,
        "max_tokens": 1024,
    }
    stripped_system = system_prompt.strip() if isinstance(system_prompt, str) else ""
    if stripped_system and len(stripped_system) >= _ANTHROPIC_SYSTEM_CACHE_MIN_CHARS:
        payload["system"] = [
            {
                "type": "text",
                "text": stripped_system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        payload["messages"] = [
            {"role": "user", "content": user_prompt.strip()},
        ]
    else:
        payload["messages"] = [
            {
                "role": "user",
                "content": user_prompt.strip(),
            }
        ]
        if stripped_system:
            payload["system"] = stripped_system
    if tools:
        payload["tools"] = tools
    response = _post_json(
        _join_url(_normalize_anthropic_base_url(provider.base_url), "messages"),
        headers={
            "x-api-key": provider.secret_value,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        payload=payload,
        provider=provider,
    )
    content = _extract_anthropic_text(response)
    return {
        "raw_response": content,
        "provider_id": provider.provider_id,
        "model": provider.model,
        "api_mode": provider.api_mode,
        "response": response,
    }


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, object],
    provider: DirectProviderRequest,
) -> dict[str, object]:
    endpoint = _resolve_provider_endpoint(url, provider=provider)
    body = json.dumps(payload).encode("utf-8")
    response_body = post_https_bytes(
        endpoint,
        body=body,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        max_response_bytes=_MAX_PROVIDER_RESPONSE_BYTES,
    )
    try:
        decoded = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Provider returned an invalid JSON response.") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Provider returned an invalid JSON response.")
    return decoded


def _resolve_provider_endpoint(
    url: str,
    *,
    provider: DirectProviderRequest,
) -> _ResolvedProviderEndpoint:
    expected_origin = _registered_provider_origin(provider=provider)
    return resolve_public_https_endpoint(
        url,
        expected_origin=expected_origin,
    )


def _registered_provider_origin(
    *,
    provider: DirectProviderRequest,
) -> tuple[str, int] | None:
    if provider.provider_id == "custom":
        return None
    try:
        spec = get_provider_spec(provider.provider_id)
    except ValueError as exc:
        raise RuntimeError(
            "HTTPS endpoint policy rejected the request: the provider identity is not registered."
        ) from exc
    if not spec.default_base_url:
        raise RuntimeError(
            "HTTPS endpoint policy rejected the request: the provider has no registered direct origin."
        )
    return canonical_https_origin(spec.default_base_url)


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
