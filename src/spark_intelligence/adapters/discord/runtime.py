from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.adapters.discord.normalize import normalize_discord_message
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway import resolve_simulated_dm
from spark_intelligence.state.db import StateDB


@dataclass
class DiscordRuntimeSummary:
    channel_id: str
    configured: bool
    status: str | None
    pairing_mode: str | None
    auth_ref: str | None
    allowed_user_count: int
    interaction_public_key_configured: bool
    webhook_auth_ref: str | None
    legacy_message_webhook_enabled: bool

    def ingress_mode(self) -> str:
        if self.interaction_public_key_configured:
            return "signed_interactions"
        if self.legacy_message_webhook_enabled and self.webhook_auth_ref:
            return "legacy_message_webhook"
        if self.legacy_message_webhook_enabled:
            return "legacy_message_webhook_missing_secret"
        return "missing"

    def ingress_ready(self) -> bool:
        return self.ingress_mode() in {"signed_interactions", "legacy_message_webhook"}

    def to_line(self) -> str:
        if not self.configured:
            return "- discord: not configured"
        return (
            f"- discord: status={self.status or 'unknown'} pairing_mode={self.pairing_mode} "
            f"auth_ref={self.auth_ref or 'missing'} allowed_users={self.allowed_user_count} "
            f"ingress={self.ingress_mode()}"
        )


@dataclass
class DiscordSimulationResult:
    ok: bool
    decision: str
    detail: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"ok": self.ok, "decision": self.decision, "detail": self.detail}, indent=2)

    def to_text(self) -> str:
        lines = [f"Discord simulation: {self.decision}"]
        for key, value in self.detail.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)


def build_discord_runtime_summary(config_manager: ConfigManager, state_db: StateDB) -> DiscordRuntimeSummary:
    config = config_manager.load()
    record = config.get("channels", {}).get("records", {}).get("discord")
    if not record:
        return DiscordRuntimeSummary(
            channel_id="discord",
            configured=False,
            status=None,
            pairing_mode=None,
            auth_ref=None,
            allowed_user_count=0,
            interaction_public_key_configured=False,
            webhook_auth_ref=None,
            legacy_message_webhook_enabled=False,
        )
    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(DISTINCT external_user_id) AS c FROM allowlist_entries WHERE channel_id = 'discord'"
        ).fetchone()["c"]
        installation = conn.execute(
            """
            SELECT status, pairing_mode, auth_ref
            FROM channel_installations
            WHERE channel_id = 'discord'
            LIMIT 1
            """
        ).fetchone()
    return DiscordRuntimeSummary(
        channel_id="discord",
        configured=True,
        status=(installation["status"] if installation else record.get("status")),
        pairing_mode=(installation["pairing_mode"] if installation else record.get("pairing_mode")),
        auth_ref=(installation["auth_ref"] if installation else record.get("auth_ref")),
        allowed_user_count=count,
        interaction_public_key_configured=bool(str(record.get("interaction_public_key") or "").strip()),
        webhook_auth_ref=(str(record.get("webhook_auth_ref")) if record.get("webhook_auth_ref") else None),
        legacy_message_webhook_enabled=bool(record.get("allow_legacy_message_webhook")),
    )


def simulate_discord_message(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    payload: dict[str, Any],
    run_id: str | None = None,
) -> DiscordSimulationResult:
    normalized = normalize_discord_message(payload)
    if not normalized.is_dm:
        return DiscordSimulationResult(
            ok=False,
            decision="ignored",
            detail={
                "reason": "non_dm_surface",
                "discord_user_id": normalized.discord_user_id,
                "guild_id": normalized.guild_id,
                "channel_id": normalized.channel_id,
            },
        )
    bridge = resolve_simulated_dm(
        config_manager=config_manager,
        state_db=state_db,
        channel_id="discord",
        request_id=f"discord:{normalized.message_id}",
        external_user_id=normalized.discord_user_id,
        display_name=normalized.discord_username or f"discord user {normalized.discord_user_id}",
        user_message=normalized.content,
        run_id=run_id,
        origin_surface="discord_webhook",
    )
    return DiscordSimulationResult(
        ok=bridge.ok,
        decision=bridge.decision,
        detail={
            "discord_user_id": normalized.discord_user_id,
            "channel_id": normalized.channel_id,
            "guild_id": normalized.guild_id,
            **bridge.detail,
        },
    )
