from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit
from spark_intelligence.identity.service import resolve_inbound_dm
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply, record_researcher_bridge_result
from spark_intelligence.state.db import StateDB


@dataclass
class TelegramRuntimeSummary:
    channel_id: str
    configured: bool
    status: str | None
    pairing_mode: str | None
    auth_ref: str | None
    allowed_user_count: int

    def to_line(self) -> str:
        if not self.configured:
            return "- telegram: not configured"
        return (
            f"- telegram: status={self.status or 'unknown'} pairing_mode={self.pairing_mode} "
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
    failed_send_count: int
    ignored_count: int
    blocked_count: int
    held_count: int
    pending_pairing_count: int
    next_offset: int | None
    trace_refs: list[str]

    def to_text(self) -> str:
        return (
            "Telegram polling result\n"
            f"- fetched_updates: {self.fetched_update_count}\n"
            f"- processed: {self.processed_count}\n"
            f"- sent: {self.sent_count}\n"
            f"- failed_sends: {self.failed_send_count}\n"
            f"- ignored: {self.ignored_count}\n"
            f"- blocked: {self.blocked_count}\n"
            f"- held: {self.held_count}\n"
            f"- pending_pairing: {self.pending_pairing_count}\n"
            f"- next_offset: {self.next_offset}\n"
            f"- trace_refs: {', '.join(self.trace_refs) if self.trace_refs else 'none'}"
        )


def build_telegram_runtime_summary(config_manager: ConfigManager, state_db: StateDB) -> TelegramRuntimeSummary:
    config = config_manager.load()
    record = config.get("channels", {}).get("records", {}).get("telegram")
    if not record:
        return TelegramRuntimeSummary(
            channel_id="telegram",
            configured=False,
            status=None,
            pairing_mode=None,
            auth_ref=None,
            allowed_user_count=0,
        )

    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM allowlist_entries WHERE channel_id = 'telegram'"
        ).fetchone()["c"]
        installation = conn.execute(
            """
            SELECT status, pairing_mode, auth_ref
            FROM channel_installations
            WHERE channel_id = 'telegram'
            LIMIT 1
            """
        ).fetchone()

    return TelegramRuntimeSummary(
        channel_id="telegram",
        configured=True,
        status=(installation["status"] if installation else record.get("status")),
        pairing_mode=(installation["pairing_mode"] if installation else record.get("pairing_mode")),
        auth_ref=(installation["auth_ref"] if installation else record.get("auth_ref")),
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
        record_researcher_bridge_result(state_db=state_db, result=bridge_result)
        outbound_text = bridge_result.reply_text
        trace_ref = bridge_result.trace_ref
        bridge_mode = bridge_result.mode
        attachment_context = bridge_result.attachment_context
    else:
        trace_ref = None
        bridge_mode = None
        attachment_context = None
    detail = {
        "telegram_user_id": normalized.telegram_user_id,
        "chat_id": normalized.chat_id,
        "session_id": resolution.session_id,
        "human_id": resolution.human_id,
        "agent_id": resolution.agent_id,
        "message_text": normalized.text,
        "response_text": outbound_text,
        "trace_ref": trace_ref,
        "bridge_mode": bridge_mode,
        "attachment_context": attachment_context,
    }
    return TelegramSimulationResult(ok=resolution.allowed, decision=resolution.decision, detail=detail)


def poll_telegram_updates_once(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    client: TelegramBotApiClient,
    timeout_seconds: int,
) -> TelegramPollResult:
    policy = _telegram_security_policy(config_manager)
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = 'telegram:last_update_offset' LIMIT 1"
        ).fetchone()
        offset = int(row["value"]) if row and row["value"] is not None else None

    updates = client.get_updates(offset=offset, timeout_seconds=timeout_seconds)
    processed_count = 0
    sent_count = 0
    failed_send_count = 0
    ignored_count = 0
    blocked_count = 0
    held_count = 0
    pending_pairing_count = 0
    next_offset = offset
    trace_refs: list[str] = []

    for update in updates:
        normalized = normalize_telegram_update(update, channel_id="telegram")
        next_offset = normalized.update_id + 1
        if _is_duplicate_update(state_db=state_db, update_id=normalized.update_id, window_size=policy["duplicate_window_size"]):
            ignored_count += 1
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_update_duplicate",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_type": normalized.chat_type,
                },
            )
            continue
        if not normalized.is_dm:
            ignored_count += 1
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_update_ignored",
                    "reason": "non_dm_surface",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_type": normalized.chat_type,
                },
            )
            continue
        rate_limit = _apply_inbound_rate_limit(
            state_db=state_db,
            telegram_user_id=normalized.telegram_user_id,
            limit_per_minute=policy["max_messages_per_minute"],
            notice_cooldown_seconds=policy["rate_limit_notice_cooldown_seconds"],
        )
        if not rate_limit["allowed"]:
            blocked_count += 1
            delivery_ok = None
            delivery_error = None
            if rate_limit["notice_allowed"]:
                send_result = _send_telegram_reply(
                    config_manager=config_manager,
                    client=client,
                    chat_id=normalized.chat_id,
                    text=f"Rate limit reached. Try again in about {rate_limit['retry_after_seconds']} seconds.",
                    event="telegram_rate_limit_outbound",
                    update_id=normalized.update_id,
                    telegram_user_id=normalized.telegram_user_id,
                    session_id=None,
                    decision="rate_limited",
                    bridge_mode=None,
                    trace_ref=None,
                )
                delivery_ok = send_result["ok"]
                delivery_error = send_result["error"]
                if send_result["ok"]:
                    sent_count += 1
                else:
                    failed_send_count += 1
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_rate_limited",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "retry_after_seconds": rate_limit["retry_after_seconds"],
                    "notice_sent": rate_limit["notice_allowed"],
                    "delivery_ok": delivery_ok,
                    "delivery_error": delivery_error,
                },
            )
            continue

        resolution = resolve_inbound_dm(
            state_db=state_db,
            channel_id="telegram",
            external_user_id=normalized.telegram_user_id,
            display_name=normalized.telegram_username or f"telegram user {normalized.telegram_user_id}",
        )

        if resolution.decision == "pending_pairing":
            pending_pairing_count += 1
            send_result = _send_telegram_reply(
                config_manager=config_manager,
                client=client,
                chat_id=normalized.chat_id,
                text=resolution.response_text,
                event="telegram_pending_pairing_outbound",
                update_id=normalized.update_id,
                telegram_user_id=normalized.telegram_user_id,
                session_id=resolution.session_id,
                decision=resolution.decision,
                bridge_mode=None,
                trace_ref=None,
            )
            if send_result["ok"]:
                sent_count += 1
            else:
                failed_send_count += 1
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_pending_pairing",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "session_id": resolution.session_id,
                    "response_preview": _preview_text(resolution.response_text),
                    "delivery_ok": send_result["ok"],
                    "delivery_error": send_result["error"],
                    "guardrail_actions": send_result["guardrail_actions"],
                },
            )
            continue

        if not resolution.allowed or not resolution.agent_id or not resolution.human_id or not resolution.session_id:
            ignored_count += 1
            if resolution.decision == "held":
                held_count += 1
            elif resolution.decision not in {"ignored"}:
                blocked_count += 1
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_update_blocked",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "decision": resolution.decision,
                    "response_preview": _preview_text(resolution.response_text),
                },
            )
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
        record_researcher_bridge_result(state_db=state_db, result=bridge_result)
        send_result = _send_telegram_reply(
            config_manager=config_manager,
            client=client,
            chat_id=normalized.chat_id,
            text=bridge_result.reply_text,
            event="telegram_bridge_outbound",
            update_id=normalized.update_id,
            telegram_user_id=normalized.telegram_user_id,
            session_id=resolution.session_id,
            decision=resolution.decision,
            bridge_mode=bridge_result.mode,
            trace_ref=bridge_result.trace_ref,
        )
        processed_count += 1
        if send_result["ok"]:
            sent_count += 1
        else:
            failed_send_count += 1
        trace_refs.append(bridge_result.trace_ref)
        append_gateway_trace(
            config_manager,
            {
                "event": "telegram_update_processed",
                "update_id": normalized.update_id,
                "telegram_user_id": normalized.telegram_user_id,
                "chat_id": normalized.chat_id,
                "session_id": resolution.session_id,
                "trace_ref": bridge_result.trace_ref,
                "bridge_mode": bridge_result.mode,
                "runtime_root": bridge_result.runtime_root,
                "config_path": bridge_result.config_path,
                "evidence_summary": bridge_result.evidence_summary,
                "attachment_context": bridge_result.attachment_context,
                "response_preview": _preview_text(bridge_result.reply_text),
                "response_length": len(bridge_result.reply_text),
                "delivery_ok": send_result["ok"],
                "delivery_error": send_result["error"],
                "guardrail_actions": send_result["guardrail_actions"],
            },
        )

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
        failed_send_count=failed_send_count,
        ignored_count=ignored_count,
        blocked_count=blocked_count,
        held_count=held_count,
        pending_pairing_count=pending_pairing_count,
        next_offset=next_offset,
        trace_refs=trace_refs,
    )


def _telegram_security_policy(config_manager: ConfigManager) -> dict[str, Any]:
    configured = config_manager.get_path("security.telegram", default={}) or {}
    return {
        "duplicate_window_size": int(configured.get("duplicate_window_size", 128)),
        "max_messages_per_minute": int(configured.get("max_messages_per_minute", 6)),
        "rate_limit_notice_cooldown_seconds": int(configured.get("rate_limit_notice_cooldown_seconds", 30)),
        "max_reply_chars": int(configured.get("max_reply_chars", 3500)),
        "redact_secret_like_replies": bool(configured.get("redact_secret_like_replies", True)),
    }


def _is_duplicate_update(*, state_db: StateDB, update_id: int, window_size: int) -> bool:
    state_key = "telegram:recent_update_ids"
    recent_ids = _load_json_list(state_db=state_db, state_key=state_key)
    if update_id in recent_ids:
        return True
    trimmed = (recent_ids + [update_id])[-max(window_size, 1) :]
    _set_runtime_state_value(state_db=state_db, state_key=state_key, value=json.dumps(trimmed))
    return False


def _apply_inbound_rate_limit(
    *,
    state_db: StateDB,
    telegram_user_id: str,
    limit_per_minute: int,
    notice_cooldown_seconds: int,
) -> dict[str, Any]:
    state_key = f"telegram:rate_limit:{telegram_user_id}"
    raw = _load_json_object(state_db=state_db, state_key=state_key)
    now = int(time.time())
    timestamps = [int(item) for item in raw.get("timestamps", []) if isinstance(item, (int, float))]
    timestamps = [item for item in timestamps if item > now - 60]
    last_notice_at = int(raw.get("last_notice_at", 0) or 0)
    if len(timestamps) >= max(limit_per_minute, 1):
        retry_after_seconds = max(1, 60 - (now - timestamps[0]))
        notice_allowed = now - last_notice_at >= max(notice_cooldown_seconds, 1)
        if notice_allowed:
            last_notice_at = now
        _set_runtime_state_value(
            state_db=state_db,
            state_key=state_key,
            value=json.dumps({"timestamps": timestamps, "last_notice_at": last_notice_at}, sort_keys=True),
        )
        return {
            "allowed": False,
            "retry_after_seconds": retry_after_seconds,
            "notice_allowed": notice_allowed,
        }
    timestamps.append(now)
    _set_runtime_state_value(
        state_db=state_db,
        state_key=state_key,
        value=json.dumps({"timestamps": timestamps, "last_notice_at": last_notice_at}, sort_keys=True),
    )
    return {"allowed": True, "retry_after_seconds": 0, "notice_allowed": False}


def _send_telegram_reply(
    *,
    config_manager: ConfigManager,
    client: TelegramBotApiClient,
    chat_id: str,
    text: str,
    event: str,
    update_id: int,
    telegram_user_id: str,
    session_id: str | None,
    decision: str,
    bridge_mode: str | None,
    trace_ref: str | None,
) -> dict[str, Any]:
    policy = _telegram_security_policy(config_manager)
    guarded = _prepare_outbound_text(
        text=text,
        bridge_mode=bridge_mode,
        max_reply_chars=policy["max_reply_chars"],
        redact_secret_like_replies=policy["redact_secret_like_replies"],
    )
    error: str | None = None
    ok = True
    try:
        client.send_message(chat_id=chat_id, text=guarded["text"])
    except RuntimeError as exc:
        ok = False
        error = str(exc)
    except HTTPError as exc:
        ok = False
        error = f"HTTP {exc.code}"
    except URLError as exc:
        ok = False
        error = str(exc.reason)
    append_outbound_audit(
        config_manager,
        {
            "event": event,
            "channel_id": "telegram",
            "update_id": update_id,
            "telegram_user_id": telegram_user_id,
            "chat_id": chat_id,
            "session_id": session_id,
            "decision": decision,
            "bridge_mode": bridge_mode,
            "trace_ref": trace_ref,
            "delivery_ok": ok,
            "delivery_error": error,
            "guardrail_actions": guarded["actions"],
            "response_preview": _preview_text(guarded["text"]),
            "response_length": len(guarded["text"]),
        },
    )
    return {"ok": ok, "error": error, "guardrail_actions": guarded["actions"]}


def _prepare_outbound_text(
    *,
    text: str,
    bridge_mode: str | None,
    max_reply_chars: int,
    redact_secret_like_replies: bool,
) -> dict[str, Any]:
    actions: list[str] = []
    cleaned = "".join(character for character in text if character == "\n" or character == "\t" or ord(character) >= 32)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").strip()
    if cleaned != text:
        actions.append("sanitize_control_chars")
    if bridge_mode == "bridge_error":
        cleaned = "Spark Intelligence hit an internal bridge error. The operator can inspect local gateway traces."
        actions.append("replace_bridge_error")
    if redact_secret_like_replies and _looks_secret_like(cleaned):
        cleaned = "Spark Intelligence withheld this reply because it appeared to contain sensitive credential material. The operator can inspect local traces."
        actions.append("block_secret_like_reply")
    if len(cleaned) > max(max_reply_chars, 32):
        cleaned = f"{cleaned[: max(max_reply_chars, 32) - 28].rstrip()}\n\n[truncated for Telegram delivery]"
        actions.append("truncate_reply")
    if not cleaned:
        cleaned = "Spark Intelligence produced an empty reply."
        actions.append("replace_empty_reply")
    return {"text": cleaned, "actions": actions}


def _looks_secret_like(text: str) -> bool:
    patterns = [
        r"(?i)bearer\s+[A-Za-z0-9._-]{20,}",
        r"(?m)^[A-Z0-9_]{3,}=(?:ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|[0-9]{7,}:[A-Za-z0-9_-]{20,})$",
        r"ghp_[A-Za-z0-9]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"\b[0-9]{7,}:[A-Za-z0-9_-]{20,}\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _preview_text(text: str, *, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _load_json_list(*, state_db: StateDB, state_key: str) -> list[int]:
    with state_db.connect() as conn:
        row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return []
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [int(item) for item in payload if isinstance(item, (int, float))]


def _load_json_object(*, state_db: StateDB, state_key: str) -> dict[str, Any]:
    with state_db.connect() as conn:
        row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return {}
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _set_runtime_state_value(*, state_db: StateDB, state_key: str, value: str) -> None:
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_state(state_key, value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (state_key, value),
        )
        conn.commit()
