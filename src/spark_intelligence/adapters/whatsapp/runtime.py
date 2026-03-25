from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.adapters.whatsapp.normalize import normalize_whatsapp_message
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway import resolve_simulated_dm
from spark_intelligence.state.db import StateDB


@dataclass
class WhatsAppRuntimeSummary:
    channel_id: str
    configured: bool
    status: str | None
    pairing_mode: str | None
    auth_ref: str | None
    allowed_user_count: int

    def to_line(self) -> str:
        if not self.configured:
            return "- whatsapp: not configured"
        return (
            f"- whatsapp: status={self.status or 'unknown'} pairing_mode={self.pairing_mode} "
            f"auth_ref={self.auth_ref or 'missing'} allowed_users={self.allowed_user_count}"
        )


@dataclass
class WhatsAppSimulationResult:
    ok: bool
    decision: str
    detail: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"ok": self.ok, "decision": self.decision, "detail": self.detail}, indent=2)

    def to_text(self) -> str:
        lines = [f"WhatsApp simulation: {self.decision}"]
        for key, value in self.detail.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)


def build_whatsapp_runtime_summary(config_manager: ConfigManager, state_db: StateDB) -> WhatsAppRuntimeSummary:
    config = config_manager.load()
    record = config.get("channels", {}).get("records", {}).get("whatsapp")
    if not record:
        return WhatsAppRuntimeSummary(
            channel_id="whatsapp",
            configured=False,
            status=None,
            pairing_mode=None,
            auth_ref=None,
            allowed_user_count=0,
        )
    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM allowlist_entries WHERE channel_id = 'whatsapp'"
        ).fetchone()["c"]
        installation = conn.execute(
            """
            SELECT status, pairing_mode, auth_ref
            FROM channel_installations
            WHERE channel_id = 'whatsapp'
            LIMIT 1
            """
        ).fetchone()
    return WhatsAppRuntimeSummary(
        channel_id="whatsapp",
        configured=True,
        status=(installation["status"] if installation else record.get("status")),
        pairing_mode=(installation["pairing_mode"] if installation else record.get("pairing_mode")),
        auth_ref=(installation["auth_ref"] if installation else record.get("auth_ref")),
        allowed_user_count=count,
    )


def simulate_whatsapp_message(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    payload: dict[str, Any],
) -> WhatsAppSimulationResult:
    normalized = normalize_whatsapp_message(payload)
    if not normalized.is_dm:
        return WhatsAppSimulationResult(
            ok=False,
            decision="ignored",
            detail={
                "reason": "non_dm_surface",
                "whatsapp_user_id": normalized.whatsapp_user_id,
                "group_id": normalized.group_id,
                "chat_id": normalized.chat_id,
            },
        )
    bridge = resolve_simulated_dm(
        config_manager=config_manager,
        state_db=state_db,
        channel_id="whatsapp",
        request_id=f"whatsapp:{normalized.message_id}",
        external_user_id=normalized.whatsapp_user_id,
        display_name=normalized.whatsapp_profile_name or f"whatsapp user {normalized.whatsapp_user_id}",
        user_message=normalized.text,
    )
    return WhatsAppSimulationResult(
        ok=bridge.ok,
        decision=bridge.decision,
        detail={
            "whatsapp_user_id": normalized.whatsapp_user_id,
            "chat_id": normalized.chat_id,
            "group_id": normalized.group_id,
            **bridge.detail,
        },
    )
