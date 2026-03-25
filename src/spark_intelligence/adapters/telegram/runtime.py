from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import resolve_inbound_dm
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply
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


@dataclass
class TelegramPollResult:
    fetched_update_count: int
    processed_count: int
    sent_count: int
    ignored_count: int
    pending_pairing_count: int
    next_offset: int | None

    def to_text(self) -> str:
        return (
            "Telegram polling result\n"
            f"- fetched_updates: {self.fetched_update_count}\n"
            f"- processed: {self.processed_count}\n"
            f"- sent: {self.sent_count}\n"
            f"- ignored: {self.ignored_count}\n"
            f"- pending_pairing: {self.pending_pairing_count}\n"
            f"- next_offset: {self.next_offset}"
        )


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
    outbound_text = resolution.response_text
    if resolution.allowed and resolution.agent_id and resolution.human_id and resolution.session_id:
        bridge_result = build_researcher_reply(
            config_manager=config_manager,
            request_id=f"sim:{normalized.update_id}",
            agent_id=resolution.agent_id,
            human_id=resolution.human_id,
            session_id=resolution.session_id,
            channel_kind="telegram",
            user_message=normalized.text,
        )
        outbound_text = bridge_result.reply_text
    detail = {
        "telegram_user_id": normalized.telegram_user_id,
        "chat_id": normalized.chat_id,
        "session_id": resolution.session_id,
        "human_id": resolution.human_id,
        "agent_id": resolution.agent_id,
        "message_text": normalized.text,
        "response_text": outbound_text,
    }
    return TelegramSimulationResult(ok=resolution.allowed, decision=resolution.decision, detail=detail)


def poll_telegram_updates_once(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    client: TelegramBotApiClient,
    timeout_seconds: int,
) -> TelegramPollResult:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = 'telegram:last_update_offset' LIMIT 1"
        ).fetchone()
        offset = int(row["value"]) if row and row["value"] is not None else None

    updates = client.get_updates(offset=offset, timeout_seconds=timeout_seconds)
    processed_count = 0
    sent_count = 0
    ignored_count = 0
    pending_pairing_count = 0
    next_offset = offset

    for update in updates:
        normalized = normalize_telegram_update(update, channel_id="telegram")
        next_offset = normalized.update_id + 1
        if not normalized.is_dm:
            ignored_count += 1
            continue

        resolution = resolve_inbound_dm(
            state_db=state_db,
            channel_id="telegram",
            external_user_id=normalized.telegram_user_id,
            display_name=normalized.telegram_username or f"telegram user {normalized.telegram_user_id}",
        )

        if resolution.decision == "pending_pairing":
            pending_pairing_count += 1
            client.send_message(chat_id=normalized.chat_id, text=resolution.response_text)
            sent_count += 1
            continue

        if not resolution.allowed or not resolution.agent_id or not resolution.human_id or not resolution.session_id:
            ignored_count += 1
            continue

        bridge_result = build_researcher_reply(
            config_manager=config_manager,
            request_id=f"telegram:{normalized.update_id}",
            agent_id=resolution.agent_id,
            human_id=resolution.human_id,
            session_id=resolution.session_id,
            channel_kind="telegram",
            user_message=normalized.text,
        )
        client.send_message(chat_id=normalized.chat_id, text=bridge_result.reply_text)
        processed_count += 1
        sent_count += 1

    if next_offset is not None:
        with state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_state(state_key, value)
                VALUES ('telegram:last_update_offset', ?)
                ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
                """,
                (str(next_offset),),
            )
            conn.commit()

    return TelegramPollResult(
        fetched_update_count=len(updates),
        processed_count=processed_count,
        sent_count=sent_count,
        ignored_count=ignored_count,
        pending_pairing_count=pending_pairing_count,
        next_offset=next_offset,
    )
