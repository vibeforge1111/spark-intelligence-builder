from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import resolve_inbound_dm
from spark_intelligence.state.db import StateDB


@dataclass
class TelegramRuntimeSummary:
    channel_id: str
    configured: bool
    pairing_mode: str | None
    auth_ref: str | None
    allowed_user_count: int

    def to_line(self) -> str:
        if not self.configured:
            return "- telegram: not configured"
        return (
            f"- telegram: configured pairing_mode={self.pairing_mode} "
            f"auth_ref={self.auth_ref or 'missing'} allowed_users={self.allowed_user_count}"
        )


@dataclass
class TelegramSimulationResult:
    ok: bool
    decision: str
    detail: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {"ok": self.ok, "decision": self.decision, "detail": self.detail},
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Telegram simulation: {self.decision}"]
        for key, value in self.detail.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)


def build_telegram_runtime_summary(config_manager: ConfigManager, state_db: StateDB) -> TelegramRuntimeSummary:
    config = config_manager.load()
    record = config.get("channels", {}).get("records", {}).get("telegram")
    if not record:
        return TelegramRuntimeSummary(
            channel_id="telegram",
            configured=False,
            pairing_mode=None,
            auth_ref=None,
            allowed_user_count=0,
        )

    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM allowlist_entries WHERE channel_id = 'telegram'"
        ).fetchone()["c"]

    return TelegramRuntimeSummary(
        channel_id="telegram",
        configured=True,
        pairing_mode=record.get("pairing_mode"),
        auth_ref=record.get("auth_ref"),
        allowed_user_count=count,
    )


def simulate_telegram_update(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    update_payload: dict[str, Any],
) -> TelegramSimulationResult:
    normalized = normalize_telegram_update(update_payload, channel_id="telegram")
    if not normalized.is_dm:
        return TelegramSimulationResult(
            ok=False,
            decision="ignored",
            detail={
                "reason": "non_dm_surface",
                "chat_type": normalized.chat_type,
                "telegram_user_id": normalized.telegram_user_id,
            },
        )

    resolution = resolve_inbound_dm(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=normalized.telegram_user_id,
        display_name=normalized.telegram_username or f"telegram user {normalized.telegram_user_id}",
    )
    detail = {
        "telegram_user_id": normalized.telegram_user_id,
        "chat_id": normalized.chat_id,
        "session_id": resolution.session_id,
        "human_id": resolution.human_id,
        "agent_id": resolution.agent_id,
        "message_text": normalized.text,
        "response_text": resolution.response_text,
    }
    return TelegramSimulationResult(ok=resolution.allowed, decision=resolution.decision, detail=detail)
