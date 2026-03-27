from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.adapters.whatsapp.runtime import simulate_whatsapp_message
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import close_run, open_run, record_event
from spark_intelligence.gateway.routes import GatewayRouteRegistration, GatewayRouteRegistry
from spark_intelligence.gateway.tracing import append_gateway_trace
from spark_intelligence.state.db import StateDB


WHATSAPP_WEBHOOK_PATH = "/webhooks/whatsapp"


@dataclass(frozen=True)
class WhatsAppWebhookResponse:
    status_code: int
    body: str
    content_type: str = "application/json"


def whatsapp_webhook_get_route() -> GatewayRouteRegistration:
    return GatewayRouteRegistration(
        path=WHATSAPP_WEBHOOK_PATH,
        methods=("GET",),
        auth_mode="provider_internal",
        owner="whatsapp-adapter",
    )


def whatsapp_webhook_post_route() -> GatewayRouteRegistration:
    return GatewayRouteRegistration(
        path=WHATSAPP_WEBHOOK_PATH,
        methods=("POST",),
        auth_mode="adapter_webhook",
        owner="whatsapp-adapter",
        content_types=("application/json",),
    )


def handle_whatsapp_webhook(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    path: str,
    method: str,
    content_type: str | None,
    headers: dict[str, str] | None,
    body: bytes,
    query_params: dict[str, str | list[str]] | None = None,
    registry: GatewayRouteRegistry | None = None,
) -> WhatsAppWebhookResponse:
    route_registry = registry or GatewayRouteRegistry()
    if route_registry.resolve(path=WHATSAPP_WEBHOOK_PATH, method="GET") is None:
        route_registry.register(whatsapp_webhook_get_route())
    if route_registry.resolve(path=WHATSAPP_WEBHOOK_PATH, method="POST") is None:
        route_registry.register(whatsapp_webhook_post_route())

    try:
        route_registry.validate_request(
            path=path,
            method=method,
            content_type=content_type if method.upper() == "POST" else None,
        )
    except ValueError as exc:
        return _request_error_response(str(exc))

    if method.upper() == "GET":
        return _handle_whatsapp_verification(
            config_manager=config_manager,
            query_params=query_params or {},
        )
    return _handle_whatsapp_event_post(
        config_manager=config_manager,
        state_db=state_db,
        headers=headers,
        body=body,
    )


def _handle_whatsapp_verification(
    *,
    config_manager: ConfigManager,
    query_params: dict[str, str | list[str]],
) -> WhatsAppWebhookResponse:
    record = _whatsapp_record(config_manager)
    verify_token_ref = record.get("webhook_verify_token_ref")
    if not verify_token_ref:
        return _log_whatsapp_verification_failure(
            config_manager=config_manager,
            status_code=503,
            message="WhatsApp webhook verify token is not configured.",
        )
    expected_verify_token = config_manager.read_env_map().get(str(verify_token_ref), "")
    if not expected_verify_token:
        return _log_whatsapp_verification_failure(
            config_manager=config_manager,
            status_code=503,
            message=f"WhatsApp webhook verify token ref '{verify_token_ref}' is unresolved.",
        )
    mode = _query_value(query_params, "hub.mode")
    verify_token = _query_value(query_params, "hub.verify_token")
    challenge = _query_value(query_params, "hub.challenge")
    if mode != "subscribe":
        return _log_whatsapp_verification_failure(
            config_manager=config_manager,
            status_code=400,
            message="WhatsApp webhook verification requires hub.mode=subscribe.",
        )
    if not challenge:
        return _log_whatsapp_verification_failure(
            config_manager=config_manager,
            status_code=400,
            message="WhatsApp webhook verification challenge is missing.",
        )
    if not verify_token:
        return _log_whatsapp_verification_failure(
            config_manager=config_manager,
            status_code=401,
            message="WhatsApp webhook verify token is missing.",
        )
    if not hmac.compare_digest(expected_verify_token, verify_token):
        return _log_whatsapp_verification_failure(
            config_manager=config_manager,
            status_code=401,
            message="WhatsApp webhook verify token is invalid.",
        )
    return WhatsAppWebhookResponse(
        status_code=200,
        body=challenge,
        content_type="text/plain",
    )


def _handle_whatsapp_event_post(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    headers: dict[str, str] | None,
    body: bytes,
) -> WhatsAppWebhookResponse:
    auth_error = _validate_whatsapp_webhook_signature(
        config_manager=config_manager,
        signature=_header_value(headers, "X-Hub-Signature-256"),
        body=body,
    )
    if auth_error:
        append_gateway_trace(
            config_manager,
            {
                "event": "whatsapp_webhook_auth_failed",
                "channel_id": "whatsapp",
                "decision": "rejected",
                "reason": auth_error[1],
                "status_code": auth_error[0],
            },
        )
        return _json_error_response(auth_error[0], auth_error[1])

    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_error_response(400, "WhatsApp webhook body must be valid JSON.")
    if not isinstance(payload, dict):
        return _json_error_response(400, "WhatsApp webhook body must be a JSON object.")

    normalized_payload, ignored_reason = _extract_supported_whatsapp_payload(payload)
    if normalized_payload is None:
        append_gateway_trace(
            config_manager,
            {
                "event": "whatsapp_webhook_ignored",
                "channel_id": "whatsapp",
                "decision": "ignored",
                "reason": ignored_reason or "unsupported_event",
            },
        )
        return WhatsAppWebhookResponse(
            status_code=200,
            body=json.dumps(
                {"ok": True, "decision": "ignored", "detail": {"reason": ignored_reason or "unsupported_event"}},
                indent=2,
            ),
        )

    request_id = f"whatsapp:{normalized_payload.get('id') or normalized_payload.get('from') or 'missing'}"
    run = open_run(
        state_db,
        run_kind="webhook:whatsapp_message",
        origin_surface="whatsapp_webhook",
        summary="WhatsApp webhook run opened.",
        request_id=request_id,
        channel_id="whatsapp",
        actor_id="whatsapp_webhook",
        facts={
            "message_id": normalized_payload.get("id"),
            "external_user_id": normalized_payload.get("from"),
        },
    )
    try:
        result = simulate_whatsapp_message(
            config_manager=config_manager,
            state_db=state_db,
            payload=normalized_payload,
            run_id=run.run_id,
        )
    except ValueError as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="closed",
            close_reason="invalid_payload",
            summary="WhatsApp webhook run closed with invalid payload.",
            facts={"error": str(exc)},
        )
        return _json_error_response(400, str(exc))

    append_gateway_trace(
        config_manager,
        {
            "event": "whatsapp_webhook_processed",
            "channel_id": "whatsapp",
            "update_id": normalized_payload.get("id"),
            "external_user_id": normalized_payload.get("from"),
            "chat_id": normalized_payload.get("chat_id"),
            "decision": result.decision,
            "bridge_mode": result.detail.get("bridge_mode"),
            "trace_ref": result.detail.get("trace_ref"),
            "output_keepability": result.detail.get("output_keepability"),
            "promotion_disposition": result.detail.get("promotion_disposition"),
        },
    )
    _record_whatsapp_delivery(
        state_db=state_db,
        run_id=run.run_id,
        request_id=request_id,
        trace_ref=str(result.detail.get("trace_ref") or "") or None,
        reason_code="whatsapp_webhook_response",
        whatsapp_user_id=str(result.detail.get("whatsapp_user_id") or ""),
        decision=result.decision,
        bridge_mode=str(result.detail.get("bridge_mode") or "") or None,
        keepability=str(result.detail.get("output_keepability") or "") or None,
        promotion_disposition=str(result.detail.get("promotion_disposition") or "") or None,
        delivered_text=str(result.detail.get("response_text") or ""),
    )
    close_run(
        state_db,
        run_id=run.run_id,
        status="closed",
        close_reason="whatsapp_webhook_processed",
        summary="WhatsApp webhook run closed after delivery.",
        facts={
            "decision": result.decision,
            "bridge_mode": result.detail.get("bridge_mode"),
            "delivery_ok": True,
        },
    )
    return WhatsAppWebhookResponse(
        status_code=200,
        body=result.to_json(),
    )


def _extract_supported_whatsapp_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if payload.get("object") != "whatsapp_business_account":
        return None, "unsupported_event"
    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        return None, "missing_entries"
    if len(entries) != 1:
        return None, "batched_entries_unsupported"
    entry = entries[0]
    if not isinstance(entry, dict):
        return None, "invalid_entry"

    changes = entry.get("changes")
    if not isinstance(changes, list) or not changes:
        return None, "missing_changes"
    if len(changes) != 1:
        return None, "batched_changes_unsupported"
    change = changes[0]
    if not isinstance(change, dict):
        return None, "invalid_change"
    if change.get("field") != "messages":
        return None, "unsupported_change_field"

    value = change.get("value")
    if not isinstance(value, dict):
        return None, "invalid_change_value"
    messages = value.get("messages")
    if value.get("statuses") and not messages:
        return None, "status_event"
    if not isinstance(messages, list) or not messages:
        return None, "missing_messages"
    if len(messages) != 1:
        return None, "batched_messages_unsupported"
    message = messages[0]
    if not isinstance(message, dict):
        return None, "invalid_message"
    if message.get("type") != "text":
        return None, "unsupported_message_type"

    text = ((message.get("text") or {}).get("body") if isinstance(message.get("text"), dict) else None)
    if not isinstance(text, str) or not text.strip():
        return None, "missing_text_body"

    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    contacts = value.get("contacts") if isinstance(value.get("contacts"), list) else []
    contact = contacts[0] if contacts and isinstance(contacts[0], dict) else {}
    profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
    whatsapp_user_id = str(message.get("from") or contact.get("wa_id") or "")
    if not whatsapp_user_id:
        return None, "missing_sender"
    return (
        {
            "id": str(message.get("id") or ""),
            "chat_id": str(metadata.get("phone_number_id") or whatsapp_user_id),
            "from": whatsapp_user_id,
            "profile_name": profile.get("name"),
            "text": text.strip(),
        },
        None,
    )


def _request_error_response(message: str) -> WhatsAppWebhookResponse:
    if "not found" in message:
        return _json_error_response(404, message)
    if "rejects method" in message:
        return _json_error_response(405, message)
    if "Content-Type" in message:
        return _json_error_response(415, message)
    return _json_error_response(400, message)


def _json_error_response(status_code: int, message: str) -> WhatsAppWebhookResponse:
    return WhatsAppWebhookResponse(
        status_code=status_code,
        body=json.dumps({"ok": False, "error": message}, indent=2),
    )


def _log_whatsapp_verification_failure(
    *,
    config_manager: ConfigManager,
    status_code: int,
    message: str,
) -> WhatsAppWebhookResponse:
    append_gateway_trace(
        config_manager,
        {
            "event": "whatsapp_webhook_verification_failed",
            "channel_id": "whatsapp",
            "decision": "rejected",
            "reason": message,
            "status_code": status_code,
        },
    )
    return _json_error_response(status_code, message)


def _validate_whatsapp_webhook_signature(
    *,
    config_manager: ConfigManager,
    signature: str | None,
    body: bytes,
) -> tuple[int, str] | None:
    record = _whatsapp_record(config_manager)
    secret_ref = record.get("webhook_auth_ref")
    if not secret_ref:
        return (503, "WhatsApp webhook auth secret is not configured.")
    expected_secret = config_manager.read_env_map().get(str(secret_ref), "")
    if not expected_secret:
        return (503, f"WhatsApp webhook auth secret ref '{secret_ref}' is unresolved.")
    if not signature:
        return (401, "WhatsApp webhook signature header is missing.")
    expected_signature = "sha256=" + hmac.new(
        expected_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return (401, "WhatsApp webhook signature is invalid.")
    return None


def _whatsapp_record(config_manager: ConfigManager) -> dict[str, Any]:
    record = config_manager.get_path("channels.records.whatsapp", default={}) or {}
    return record if isinstance(record, dict) else {}


def _header_value(headers: dict[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _record_whatsapp_delivery(
    *,
    state_db: StateDB,
    run_id: str | None,
    request_id: str,
    trace_ref: str | None,
    reason_code: str,
    whatsapp_user_id: str,
    decision: str,
    bridge_mode: str | None,
    keepability: str | None,
    promotion_disposition: str | None,
    delivered_text: str,
) -> None:
    facts = {
        "whatsapp_user_id": whatsapp_user_id,
        "decision": decision,
        "bridge_mode": bridge_mode,
        "delivery_target": whatsapp_user_id,
        "message_ref": request_id,
        "ack_ref": request_id,
        "keepability": keepability,
        "promotion_disposition": promotion_disposition,
        "response_length": len(delivered_text),
        "delivered_text": delivered_text,
    }
    record_event(
        state_db,
        event_type="delivery_attempted",
        component="whatsapp_webhook",
        summary="WhatsApp webhook delivery attempted.",
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id="whatsapp",
        actor_id="whatsapp_webhook",
        reason_code=reason_code,
        truth_kind="delivery",
        facts=facts,
    )
    record_event(
        state_db,
        event_type="delivery_succeeded",
        component="whatsapp_webhook",
        summary="WhatsApp webhook delivery succeeded.",
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id="whatsapp",
        actor_id="whatsapp_webhook",
        reason_code=reason_code,
        truth_kind="delivery",
        status="ok",
        facts=facts,
    )


def _query_value(query_params: dict[str, str | list[str]], name: str) -> str | None:
    value = query_params.get(name)
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value is None:
        return None
    return str(value)
