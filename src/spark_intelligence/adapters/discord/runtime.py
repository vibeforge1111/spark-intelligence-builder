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

    def to_line(self) -> str:
        if not self.configured:
            return "- discord: not configured"
        return (
            f"- discord: status={self.status or 'unknown'} pairing_mode={self.pairing_mode} "
            f"auth_ref={self.auth_ref or 'missing'} allowed_users={self.allowed_user_count}"
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
        )
    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM allowlist_entries WHERE channel_id = 'discord'"
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
    )


def simulate_discord_message(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    payload: dict[str, Any],
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
