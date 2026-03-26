from __future__ import annotations

import hmac
import json
from dataclasses import dataclass

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from spark_intelligence.adapters.discord.runtime import simulate_discord_message
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.routes import GatewayRouteRegistration, GatewayRouteRegistry
from spark_intelligence.state.db import StateDB


DISCORD_WEBHOOK_PATH = "/webhooks/discord"


@dataclass(frozen=True)
class GatewayWebhookResponse:
    status_code: int
    body: str
    content_type: str = "application/json"


def discord_webhook_route() -> GatewayRouteRegistration:
    return GatewayRouteRegistration(
        path=DISCORD_WEBHOOK_PATH,
        methods=("POST",),
        auth_mode="adapter_webhook",
        owner="discord-adapter",
        content_types=("application/json",),
    )


def handle_discord_webhook(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    path: str,
    method: str,
    content_type: str | None,
    headers: dict[str, str] | None,
    body: bytes,
    registry: GatewayRouteRegistry | None = None,
) -> GatewayWebhookResponse:
    route_registry = registry or GatewayRouteRegistry()
    if route_registry.resolve(path=DISCORD_WEBHOOK_PATH, method="POST") is None:
        route_registry.register(discord_webhook_route())

    try:
        route_registry.validate_request(
            path=path,
            method=method,
            content_type=content_type,
        )
    except ValueError as exc:
        return _request_error_response(str(exc))

    auth_error = _validate_discord_webhook_auth(
        config_manager=config_manager,
        provided_secret=_header_value(headers, "X-Spark-Webhook-Secret"),
        headers=headers,
        body=body,
    )
    if auth_error:
        return _json_error_response(auth_error[0], auth_error[1])

    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_error_response(400, "Discord webhook body must be valid JSON.")
    if not isinstance(payload, dict):
        return _json_error_response(400, "Discord webhook body must be a JSON object.")
    if payload.get("type") == 1:
        return GatewayWebhookResponse(
            status_code=200,
            body=json.dumps({"type": 1}, indent=2),
        )
    if "type" in payload and "content" not in payload:
        return _json_error_response(501, "Discord interaction payload handling is not implemented yet.")

    try:
        result = simulate_discord_message(
            config_manager=config_manager,
            state_db=state_db,
            payload=payload,
        )
    except ValueError as exc:
        return _json_error_response(400, str(exc))

    return GatewayWebhookResponse(
        status_code=200,
        body=result.to_json(),
    )


def _request_error_response(message: str) -> GatewayWebhookResponse:
    if "not found" in message:
        return _json_error_response(404, message)
    if "rejects method" in message:
        return _json_error_response(405, message)
    if "Content-Type" in message:
        return _json_error_response(415, message)
    return _json_error_response(400, message)


def _json_error_response(status_code: int, message: str) -> GatewayWebhookResponse:
    return GatewayWebhookResponse(
        status_code=status_code,
        body=json.dumps({"ok": False, "error": message}, indent=2),
    )


def _validate_discord_webhook_auth(
    *,
    config_manager: ConfigManager,
    provided_secret: str | None,
    headers: dict[str, str] | None,
    body: bytes,
) -> tuple[int, str] | None:
    record = config_manager.get_path("channels.records.discord", default={}) or {}
    if not isinstance(record, dict):
        return (503, "Discord webhook channel is not configured.")
    interaction_public_key = str(record.get("interaction_public_key") or "").strip()
    if interaction_public_key:
        return _validate_discord_interaction_signature(
            interaction_public_key=interaction_public_key,
            signature=_header_value(headers, "X-Signature-Ed25519"),
            timestamp=_header_value(headers, "X-Signature-Timestamp"),
            body=body,
        )
    secret_ref = record.get("webhook_auth_ref")
    if not secret_ref:
        return (503, "Discord webhook auth secret is not configured.")
    expected_secret = config_manager.read_env_map().get(str(secret_ref), "")
    if not expected_secret:
        return (503, f"Discord webhook auth secret ref '{secret_ref}' is unresolved.")
    if not provided_secret:
        return (401, "Discord webhook secret header is missing.")
    if not hmac.compare_digest(expected_secret, provided_secret):
        return (401, "Discord webhook secret is invalid.")
    return None


def _validate_discord_interaction_signature(
    *,
    interaction_public_key: str,
    signature: str | None,
    timestamp: str | None,
    body: bytes,
) -> tuple[int, str] | None:
    if not signature:
        return (401, "Discord signature header is missing.")
    if not timestamp:
        return (401, "Discord signature timestamp header is missing.")
    try:
        verify_key = VerifyKey(bytes.fromhex(interaction_public_key))
    except ValueError:
        return (503, "Discord interaction public key is invalid.")
    try:
        verify_key.verify(timestamp.encode("utf-8") + body, bytes.fromhex(signature))
    except ValueError:
        return (401, "Discord signature header is invalid.")
    except BadSignatureError:
        return (401, "Discord request signature is invalid.")
    return None


def _header_value(headers: dict[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None
