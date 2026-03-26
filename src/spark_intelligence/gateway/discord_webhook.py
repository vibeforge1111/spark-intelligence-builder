from __future__ import annotations

import json
from dataclasses import dataclass

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

    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_error_response(400, "Discord webhook body must be valid JSON.")
    if not isinstance(payload, dict):
        return _json_error_response(400, "Discord webhook body must be a JSON object.")

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
