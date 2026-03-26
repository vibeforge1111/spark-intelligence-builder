from __future__ import annotations

import hmac
import json
from dataclasses import dataclass

from spark_intelligence.adapters.whatsapp.runtime import simulate_whatsapp_message
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.routes import GatewayRouteRegistration, GatewayRouteRegistry
from spark_intelligence.state.db import StateDB


WHATSAPP_WEBHOOK_PATH = "/webhooks/whatsapp"


@dataclass(frozen=True)
class WhatsAppWebhookResponse:
    status_code: int
    body: str
    content_type: str = "application/json"


def whatsapp_webhook_route() -> GatewayRouteRegistration:
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
    registry: GatewayRouteRegistry | None = None,
) -> WhatsAppWebhookResponse:
    route_registry = registry or GatewayRouteRegistry()
    if route_registry.resolve(path=WHATSAPP_WEBHOOK_PATH, method="POST") is None:
        route_registry.register(whatsapp_webhook_route())

    try:
        route_registry.validate_request(
            path=path,
            method=method,
            content_type=content_type,
        )
    except ValueError as exc:
        return _request_error_response(str(exc))

    auth_error = _validate_whatsapp_webhook_auth(
        config_manager=config_manager,
        provided_secret=_header_value(headers, "X-Spark-Webhook-Secret"),
    )
    if auth_error:
        return _json_error_response(auth_error[0], auth_error[1])

    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_error_response(400, "WhatsApp webhook body must be valid JSON.")
    if not isinstance(payload, dict):
        return _json_error_response(400, "WhatsApp webhook body must be a JSON object.")

    try:
        result = simulate_whatsapp_message(
            config_manager=config_manager,
            state_db=state_db,
            payload=payload,
        )
    except ValueError as exc:
        return _json_error_response(400, str(exc))

    return WhatsAppWebhookResponse(
        status_code=200,
        body=result.to_json(),
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


def _validate_whatsapp_webhook_auth(
    *,
    config_manager: ConfigManager,
    provided_secret: str | None,
) -> tuple[int, str] | None:
    record = config_manager.get_path("channels.records.whatsapp", default={}) or {}
    if not isinstance(record, dict):
        return (503, "WhatsApp webhook channel is not configured.")
    secret_ref = record.get("webhook_auth_ref")
    if not secret_ref:
        return (503, "WhatsApp webhook auth secret is not configured.")
    expected_secret = config_manager.read_env_map().get(str(secret_ref), "")
    if not expected_secret:
        return (503, f"WhatsApp webhook auth secret ref '{secret_ref}' is unresolved.")
    if not provided_secret:
        return (401, "WhatsApp webhook secret header is missing.")
    if not hmac.compare_digest(expected_secret, provided_secret):
        return (401, "WhatsApp webhook secret is invalid.")
    return None


def _header_value(headers: dict[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None
