from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.guardrails import (
    apply_inbound_rate_limit,
    is_duplicate_event,
    load_channel_security_policy,
    prepare_outbound_text,
    set_runtime_state_value,
)
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit
from spark_intelligence.identity.service import consume_pairing_welcome, record_pairing_context, resolve_inbound_dm
from spark_intelligence.observability.store import build_text_mutation_facts, close_run, open_run, record_event
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply, record_researcher_bridge_result
from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import JSON_RICHNESS_MERGE_GUARD
from spark_intelligence.swarm_bridge import evaluate_swarm_escalation, swarm_status, sync_swarm_collective


@dataclass
class TelegramRuntimeSummary:
    channel_id: str
    configured: bool
    status: str | None
    pairing_mode: str | None
    auth_ref: str | None
    bot_username: str | None
    auth_status: str | None
    allowed_user_count: int

    def to_line(self) -> str:
        if not self.configured:
            return "- telegram: not configured"
        bot_ref = f" bot=@{self.bot_username}" if self.bot_username else ""
        auth_ref = f" auth={self.auth_status or 'unknown'}"
        return (
            f"- telegram: status={self.status or 'unknown'} pairing_mode={self.pairing_mode} "
            f"auth_ref={self.auth_ref or 'missing'}{bot_ref}{auth_ref} allowed_users={self.allowed_user_count}"
        )


@dataclass
class TelegramRuntimeHealth:
    auth_status: str | None
    auth_checked_at: str | None
    auth_error: str | None
    bot_username: str | None
    last_ok_at: str | None
    last_failure_at: str | None
    last_failure_type: str | None
    last_failure_message: str | None
    consecutive_failures: int
    last_backoff_seconds: int


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
            bot_username=None,
            auth_status=None,
            allowed_user_count=0,
        )

    health = read_telegram_runtime_health(state_db)
    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(DISTINCT external_user_id) AS c FROM allowlist_entries WHERE channel_id = 'telegram'"
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
        bot_username=(
            ((record.get("bot_profile") or {}).get("username") if isinstance(record.get("bot_profile"), dict) else None)
            or health.bot_username
        ),
        auth_status=health.auth_status,
        allowed_user_count=count,
    )


def read_telegram_runtime_health(state_db: StateDB) -> TelegramRuntimeHealth:
    auth_payload = _load_runtime_json_object(state_db, "telegram:auth_state")
    poll_payload = _load_runtime_json_object(state_db, "telegram:poll_state")
    return TelegramRuntimeHealth(
        auth_status=_read_optional_text(auth_payload.get("status")),
        auth_checked_at=_read_optional_text(auth_payload.get("checked_at")),
        auth_error=_read_optional_text(auth_payload.get("error")),
        bot_username=_read_optional_text(auth_payload.get("bot_username")),
        last_ok_at=_read_optional_text(poll_payload.get("last_ok_at")),
        last_failure_at=_read_optional_text(poll_payload.get("last_failure_at")),
        last_failure_type=_read_optional_text(poll_payload.get("last_failure_type")),
        last_failure_message=_read_optional_text(poll_payload.get("last_failure_message")),
        consecutive_failures=_read_optional_int(poll_payload.get("consecutive_failures")),
        last_backoff_seconds=_read_optional_int(poll_payload.get("last_backoff_seconds")),
    )


def record_telegram_auth_result(
    *,
    state_db: StateDB,
    status: str,
    bot_username: str | None = None,
    error: str | None = None,
) -> None:
    set_runtime_state_value(
        state_db=state_db,
        state_key="telegram:auth_state",
        value=json.dumps(
            {
                "status": status,
                "checked_at": _utc_now_iso(),
                "bot_username": bot_username,
                "error": error,
            },
            sort_keys=True,
        ),
        component="telegram_runtime",
        guard_strategy=JSON_RICHNESS_MERGE_GUARD,
    )


def record_telegram_poll_success(*, state_db: StateDB) -> None:
    payload = _load_runtime_json_object(state_db, "telegram:poll_state")
    payload["last_ok_at"] = _utc_now_iso()
    payload["consecutive_failures"] = 0
    payload["last_backoff_seconds"] = 0
    set_runtime_state_value(
        state_db=state_db,
        state_key="telegram:poll_state",
        value=json.dumps(payload, sort_keys=True),
        component="telegram_runtime",
        guard_strategy=JSON_RICHNESS_MERGE_GUARD,
    )


def record_telegram_poll_failure(
    *,
    state_db: StateDB,
    failure_type: str,
    message: str,
    backoff_seconds: int,
) -> None:
    payload = _load_runtime_json_object(state_db, "telegram:poll_state")
    payload["last_failure_at"] = _utc_now_iso()
    payload["last_failure_type"] = failure_type
    payload["last_failure_message"] = message
    payload["consecutive_failures"] = _read_optional_int(payload.get("consecutive_failures")) + 1
    payload["last_backoff_seconds"] = max(backoff_seconds, 0)
    set_runtime_state_value(
        state_db=state_db,
        state_key="telegram:poll_state",
        value=json.dumps(payload, sort_keys=True),
        component="telegram_runtime",
        guard_strategy=JSON_RICHNESS_MERGE_GUARD,
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
    if resolution.decision in {"pending_pairing", "held"}:
        record_pairing_context(
            state_db=state_db,
            channel_id="telegram",
            external_user_id=normalized.telegram_user_id,
            context={
                "display_name": normalized.telegram_username or f"telegram user {normalized.telegram_user_id}",
                "telegram_username": normalized.telegram_username,
                "chat_id": normalized.chat_id,
                "last_message_text": _preview_text(normalized.text, limit=80),
                "last_update_id": normalized.update_id,
                "last_seen_at": _utc_now_iso(),
            },
        )
    outbound_text = _resolution_reply_text(
        decision=resolution.decision,
        default_text=resolution.response_text,
        inbound_text=normalized.text,
    )
    if resolution.allowed and resolution.agent_id and resolution.human_id and resolution.session_id:
        command_result = _handle_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            inbound_text=normalized.text,
            run_id=None,
            request_id=f"sim:{normalized.update_id}",
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
        )
        if command_result is not None:
            outbound_text = command_result["reply_text"]
            trace_ref = None
            bridge_mode = "runtime_command"
            attachment_context = None
            routing_decision = "runtime_command"
            active_chip_key = None
            active_chip_task_type = None
            active_chip_evaluate_used = False
            evidence_summary = None
        else:
            bridge_result = build_researcher_reply(
                config_manager=config_manager,
                state_db=state_db,
                request_id=f"sim:{normalized.update_id}",
                agent_id=resolution.agent_id,
                human_id=resolution.human_id,
                session_id=resolution.session_id,
                channel_kind="telegram",
                user_message=normalized.text,
            )
            record_researcher_bridge_result(state_db=state_db, result=bridge_result)
            outbound_text = _apply_post_approval_welcome(
                state_db=state_db,
                external_user_id=normalized.telegram_user_id,
                reply_text=bridge_result.reply_text,
            )
            trace_ref = bridge_result.trace_ref
            bridge_mode = bridge_result.mode
            attachment_context = bridge_result.attachment_context
            routing_decision = bridge_result.routing_decision
            active_chip_key = bridge_result.active_chip_key
            active_chip_task_type = bridge_result.active_chip_task_type
            active_chip_evaluate_used = bridge_result.active_chip_evaluate_used
            evidence_summary = bridge_result.evidence_summary
        outbound_text = _apply_think_visibility(
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            text=outbound_text,
        )
    else:
        trace_ref = None
        bridge_mode = None
        attachment_context = None
        routing_decision = None
        active_chip_key = None
        active_chip_task_type = None
        active_chip_evaluate_used = False
        evidence_summary = None
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
        "routing_decision": routing_decision,
        "active_chip_key": active_chip_key,
        "active_chip_task_type": active_chip_task_type,
        "active_chip_evaluate_used": active_chip_evaluate_used,
        "attachment_context": attachment_context,
    }
    if resolution.allowed:
        append_gateway_trace(
            config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "update_id": normalized.update_id,
                "telegram_user_id": normalized.telegram_user_id,
                "chat_id": normalized.chat_id,
                "session_id": resolution.session_id,
                "trace_ref": trace_ref,
                "bridge_mode": bridge_mode,
                "routing_decision": routing_decision,
                "evidence_summary": evidence_summary,
                "attachment_context": attachment_context,
                "active_chip_key": active_chip_key,
                "active_chip_task_type": active_chip_task_type,
                "active_chip_evaluate_used": active_chip_evaluate_used,
                "response_preview": _preview_text(outbound_text),
                "response_length": len(outbound_text),
                "delivery_ok": True,
                "delivery_error": None,
                "guardrail_actions": [],
                "simulation": True,
            },
        )
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
        if is_duplicate_event(
            state_db=state_db,
            channel_id="telegram",
            event_id=normalized.update_id,
            window_size=policy["duplicate_window_size"],
        ):
            ignored_count += 1
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_update_duplicate",
                    "channel_id": "telegram",
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
                    "channel_id": "telegram",
                    "reason": "non_dm_surface",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_type": normalized.chat_type,
                },
            )
            continue
        rate_limit = apply_inbound_rate_limit(
            state_db=state_db,
            channel_id="telegram",
            external_user_id=normalized.telegram_user_id,
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
                    state_db=state_db,
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
                    "channel_id": "telegram",
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
        run = open_run(
            state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary=f"Telegram update {normalized.update_id} opened for user {normalized.telegram_user_id}.",
            request_id=f"telegram:{normalized.update_id}",
            channel_id="telegram",
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            actor_id="telegram_runtime",
            reason_code="inbound_update_received",
            facts={
                "update_id": normalized.update_id,
                "telegram_user_id": normalized.telegram_user_id,
                "chat_id": normalized.chat_id,
            },
        )
        if resolution.decision in {"pending_pairing", "held"}:
            record_pairing_context(
                state_db=state_db,
                channel_id="telegram",
                external_user_id=normalized.telegram_user_id,
                context={
                    "display_name": normalized.telegram_username or f"telegram user {normalized.telegram_user_id}",
                    "telegram_username": normalized.telegram_username,
                    "chat_id": normalized.chat_id,
                    "last_message_text": _preview_text(normalized.text, limit=80),
                    "last_update_id": normalized.update_id,
                    "last_seen_at": _utc_now_iso(),
                },
            )

        if resolution.decision == "pending_pairing":
            pending_pairing_count += 1
            send_result = _send_telegram_reply(
                config_manager=config_manager,
                state_db=state_db,
                client=client,
                chat_id=normalized.chat_id,
                text=_resolution_reply_text(
                    decision=resolution.decision,
                    default_text=resolution.response_text,
                    inbound_text=normalized.text,
                ),
                event="telegram_pending_pairing_outbound",
                update_id=normalized.update_id,
                telegram_user_id=normalized.telegram_user_id,
                session_id=resolution.session_id,
                decision=resolution.decision,
                bridge_mode=None,
                run_id=run.run_id,
                request_id=run.request_id,
                trace_ref=None,
            )
            if send_result["ok"]:
                sent_count += 1
            else:
                failed_send_count += 1
            close_run(
                state_db,
                run_id=run.run_id,
                status="closed",
                close_reason="pending_pairing",
                summary=f"Telegram update {normalized.update_id} closed with pending pairing.",
                facts={"decision": resolution.decision, "delivery_ok": send_result["ok"]},
            )
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_pending_pairing",
                    "channel_id": "telegram",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "session_id": resolution.session_id,
                    "response_preview": _preview_text(
                        _resolution_reply_text(
                            decision=resolution.decision,
                            default_text=resolution.response_text,
                            inbound_text=normalized.text,
                        )
                    ),
                    "delivery_ok": send_result["ok"],
                    "delivery_error": send_result["error"],
                    "guardrail_actions": send_result["guardrail_actions"],
                },
            )
            continue

        if not resolution.allowed or not resolution.agent_id or not resolution.human_id or not resolution.session_id:
            denied_text = _resolution_reply_text(
                decision=resolution.decision,
                default_text=resolution.response_text,
                inbound_text=normalized.text,
            )
            send_result = _send_telegram_reply(
                config_manager=config_manager,
                state_db=state_db,
                client=client,
                chat_id=normalized.chat_id,
                text=denied_text,
                event="telegram_denied_outbound",
                update_id=normalized.update_id,
                telegram_user_id=normalized.telegram_user_id,
                session_id=resolution.session_id,
                decision=resolution.decision,
                bridge_mode=None,
                run_id=run.run_id,
                request_id=run.request_id,
                trace_ref=None,
            )
            if resolution.decision == "held":
                held_count += 1
            elif resolution.decision not in {"ignored"}:
                blocked_count += 1
            if send_result["ok"]:
                sent_count += 1
            else:
                failed_send_count += 1
            close_run(
                state_db,
                run_id=run.run_id,
                status="closed",
                close_reason=resolution.decision,
                summary=f"Telegram update {normalized.update_id} closed with decision {resolution.decision}.",
                facts={"decision": resolution.decision, "delivery_ok": send_result["ok"]},
            )
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_update_denied",
                    "channel_id": "telegram",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "decision": resolution.decision,
                    "response_preview": _preview_text(denied_text),
                    "delivery_ok": send_result["ok"],
                    "delivery_error": send_result["error"],
                    "guardrail_actions": send_result["guardrail_actions"],
                },
            )
            continue

        command_result = _handle_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            inbound_text=normalized.text,
        )
        if command_result is not None:
            record_event(
                state_db,
                event_type="intent_committed",
                component="telegram_runtime",
                summary="Telegram runtime command committed for execution.",
                run_id=run.run_id,
                request_id=run.request_id,
                channel_id="telegram",
                session_id=resolution.session_id,
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                actor_id="telegram_runtime",
                reason_code="runtime_command",
                facts={
                    "command": command_result["command"],
                    "update_id": normalized.update_id,
                    "message_text": normalized.text,
                },
            )
            outbound_text = _apply_think_visibility(
                state_db=state_db,
                external_user_id=normalized.telegram_user_id,
                text=command_result["reply_text"],
            )
            send_result = _send_telegram_reply(
                config_manager=config_manager,
                state_db=state_db,
                client=client,
                chat_id=normalized.chat_id,
                text=outbound_text,
                event="telegram_runtime_command_outbound",
                update_id=normalized.update_id,
                telegram_user_id=normalized.telegram_user_id,
                session_id=resolution.session_id,
                decision=resolution.decision,
                bridge_mode="runtime_command",
                run_id=run.run_id,
                request_id=run.request_id,
                trace_ref=None,
            )
            processed_count += 1
            if send_result["ok"]:
                sent_count += 1
            else:
                failed_send_count += 1
            close_run(
                state_db,
                run_id=run.run_id,
                status="closed",
                close_reason="runtime_command",
                summary=f"Telegram runtime command {command_result['command']} closed.",
                facts={"command": command_result["command"], "delivery_ok": send_result["ok"]},
            )
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_runtime_command_processed",
                    "channel_id": "telegram",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "session_id": resolution.session_id,
                    "command": command_result["command"],
                    "delivery_ok": send_result["ok"],
                    "delivery_error": send_result["error"],
                    "guardrail_actions": send_result["guardrail_actions"],
                    "response_preview": _preview_text(outbound_text),
                },
            )
            continue

        record_event(
            state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="Telegram message committed to researcher bridge execution.",
            run_id=run.run_id,
            request_id=run.request_id,
            channel_id="telegram",
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            actor_id="telegram_runtime",
            reason_code="user_message_allowed",
            facts={
                "update_id": normalized.update_id,
                "message_length": len(normalized.text),
                "message_text": normalized.text,
            },
        )
        bridge_result = build_researcher_reply(
            config_manager=config_manager,
            state_db=state_db,
            request_id=f"telegram:{normalized.update_id}",
            agent_id=resolution.agent_id,
            human_id=resolution.human_id,
            session_id=resolution.session_id,
            channel_kind="telegram",
            user_message=normalized.text,
            run_id=run.run_id,
        )
        record_researcher_bridge_result(state_db=state_db, result=bridge_result)
        outbound_text = _apply_post_approval_welcome(
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            reply_text=bridge_result.reply_text,
        )
        send_result = _send_telegram_reply(
            config_manager=config_manager,
            state_db=state_db,
            client=client,
            chat_id=normalized.chat_id,
            text=outbound_text,
            event="telegram_bridge_outbound",
            update_id=normalized.update_id,
            telegram_user_id=normalized.telegram_user_id,
            session_id=resolution.session_id,
            decision=resolution.decision,
            bridge_mode=bridge_result.mode,
            routing_decision=bridge_result.routing_decision,
            active_chip_key=bridge_result.active_chip_key,
            active_chip_task_type=bridge_result.active_chip_task_type,
            run_id=run.run_id,
            request_id=run.request_id,
            trace_ref=bridge_result.trace_ref,
            output_keepability=bridge_result.output_keepability,
            promotion_disposition=bridge_result.promotion_disposition,
        )
        processed_count += 1
        if send_result["ok"]:
            sent_count += 1
        else:
            failed_send_count += 1
        close_run(
            state_db,
            run_id=run.run_id,
            status="closed",
            close_reason="telegram_update_processed",
            summary=f"Telegram update {normalized.update_id} closed after researcher bridge processing.",
            facts={
                "bridge_mode": bridge_result.mode,
                "routing_decision": bridge_result.routing_decision,
                "delivery_ok": send_result["ok"],
            },
        )
        trace_refs.append(bridge_result.trace_ref)
        append_gateway_trace(
            config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "update_id": normalized.update_id,
                "telegram_user_id": normalized.telegram_user_id,
                "chat_id": normalized.chat_id,
                "session_id": resolution.session_id,
                "trace_ref": bridge_result.trace_ref,
                "bridge_mode": bridge_result.mode,
                "routing_decision": bridge_result.routing_decision,
                "runtime_root": bridge_result.runtime_root,
                "config_path": bridge_result.config_path,
                "evidence_summary": bridge_result.evidence_summary,
                "attachment_context": bridge_result.attachment_context,
                "active_chip_key": bridge_result.active_chip_key,
                "active_chip_task_type": bridge_result.active_chip_task_type,
                "active_chip_evaluate_used": bridge_result.active_chip_evaluate_used,
                "response_preview": _preview_text(outbound_text),
                "response_length": len(outbound_text),
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
    return load_channel_security_policy(
        config_manager,
        channel_id="telegram",
        defaults={
            "duplicate_window_size": 128,
            "max_messages_per_minute": 6,
            "rate_limit_notice_cooldown_seconds": 30,
            "max_reply_chars": 3500,
            "redact_secret_like_replies": True,
        },
    )


def _send_telegram_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    client: TelegramBotApiClient,
    chat_id: str,
    text: str,
    event: str,
    update_id: int,
    telegram_user_id: str,
    session_id: str | None,
    decision: str,
    bridge_mode: str | None,
    routing_decision: str | None = None,
    active_chip_key: str | None = None,
    active_chip_task_type: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None,
    output_keepability: str | None = None,
    promotion_disposition: str | None = None,
) -> dict[str, Any]:
    policy = _telegram_security_policy(config_manager)
    visible_text = _apply_think_visibility(
        state_db=state_db,
        external_user_id=telegram_user_id,
        text=text,
    )
    guarded = prepare_outbound_text(
        config_manager=config_manager,
        state_db=state_db,
        text=visible_text,
        bridge_mode=bridge_mode,
        max_reply_chars=policy["max_reply_chars"],
        redact_secret_like_replies=policy["redact_secret_like_replies"],
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id="telegram",
        session_id=session_id,
        actor_id="telegram_runtime",
    )
    if visible_text != text:
        guarded["actions"] = ["strip_think_blocks", *list(guarded["actions"])]
    error: str | None = None
    ok = True
    record_event(
        state_db,
        event_type="delivery_attempted",
        component="telegram_runtime",
        summary=f"Telegram delivery attempted for event {event}.",
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id="telegram",
        session_id=session_id,
        actor_id="telegram_runtime",
        reason_code=event,
        truth_kind="delivery",
        facts={
            "event": event,
            "update_id": update_id,
            "telegram_user_id": telegram_user_id,
            "delivery_target": chat_id,
            "message_ref": f"telegram:{update_id}",
            "guardrail_actions": guarded["actions"],
            "response_length": len(guarded["text"]),
            "delivered_text": guarded["text"],
            "keepability": output_keepability,
            "promotion_disposition": promotion_disposition,
            **build_text_mutation_facts(
                raw_text=text,
                mutated_text=guarded["text"],
                mutation_actions=guarded["actions"],
            ),
        },
    )
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
    record_event(
        state_db,
        event_type="delivery_succeeded" if ok else "delivery_failed",
        component="telegram_runtime",
        summary=(
            f"Telegram delivery succeeded for event {event}."
            if ok
            else f"Telegram delivery failed for event {event}."
        ),
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id="telegram",
        session_id=session_id,
        actor_id="telegram_runtime",
        reason_code=event,
        truth_kind="delivery",
        severity="high" if not ok else "medium",
        status="ok" if ok else "failed",
        facts={
            "event": event,
            "update_id": update_id,
            "telegram_user_id": telegram_user_id,
            "delivery_target": chat_id,
            "message_ref": f"telegram:{update_id}",
            "ack_ref": f"telegram:{update_id}" if ok else None,
            "delivery_error": error,
            "failure_family": (
                "http_error"
                if error and str(error).startswith("HTTP ")
                else ("network_error" if error and "connection" in str(error).lower() else None)
            ),
            "retryable": bool(error),
            "guardrail_actions": guarded["actions"],
            "delivered_text": guarded["text"] if ok else None,
            "keepability": output_keepability,
            "promotion_disposition": promotion_disposition,
            **build_text_mutation_facts(
                raw_text=text,
                mutated_text=guarded["text"] if ok else guarded["text"],
                mutation_actions=guarded["actions"],
            ),
        },
    )
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
            "routing_decision": routing_decision,
            "active_chip_key": active_chip_key,
            "active_chip_task_type": active_chip_task_type,
            "trace_ref": trace_ref,
            "output_keepability": output_keepability,
            "promotion_disposition": promotion_disposition,
            "delivery_ok": ok,
            "delivery_error": error,
            "guardrail_actions": guarded["actions"],
            "response_preview": _preview_text(guarded["text"]),
            "response_length": len(guarded["text"]),
        },
    )
    return {"ok": ok, "error": error, "guardrail_actions": guarded["actions"]}


def _preview_text(text: str, *, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _handle_runtime_command(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    external_user_id: str,
    inbound_text: str,
    run_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    if lowered in {"/think", "/think on", "/think off"}:
        if lowered == "/think":
            enabled = _think_enabled_for_user(state_db=state_db, external_user_id=external_user_id)
            state_text = "on" if enabled else "off"
            return {
                "command": "/think",
                "reply_text": (
                    f"Thinking visibility is currently {state_text} for this Telegram DM. "
                    "Use `/think on` to show `<think>` blocks or `/think off` to hide them."
                ),
            }
        enabled = lowered == "/think on"
        _set_think_enabled_for_user(
            state_db=state_db,
            external_user_id=external_user_id,
            enabled=enabled,
        )
        state_text = "enabled" if enabled else "disabled"
        return {
            "command": lowered,
            "reply_text": (
                f"Thinking visibility {state_text} for this Telegram DM. "
                "This only affects `<think>` blocks in future replies."
            ),
        }

    if lowered == "/swarm":
        return {
            "command": "/swarm",
            "reply_text": "Swarm commands: `/swarm status`, `/swarm sync`, and `/swarm evaluate <task>`.",
        }
    if lowered == "/swarm status":
        status = swarm_status(config_manager, state_db)
        return {
            "command": "/swarm status",
            "reply_text": (
                f"Swarm is {'ready' if status.api_ready else 'not ready'}.\n"
                f"Auth: {status.auth_state}.\n"
                f"Last sync: {(status.last_sync or {}).get('mode', 'none')}.\n"
                f"Last decision: {(status.last_decision or {}).get('mode', 'none')}."
            ),
        }
    if lowered == "/swarm sync":
        result = sync_swarm_collective(
            config_manager=config_manager,
            state_db=state_db,
            dry_run=False,
            run_id=run_id,
            request_id=request_id,
            channel_id="telegram",
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="telegram_runtime",
        )
        return {
            "command": "/swarm sync",
            "reply_text": (
                f"Swarm sync {'ok' if result.ok else 'failed'}.\n"
                f"Mode: {result.mode}.\n"
                f"Accepted: {'yes' if result.accepted else 'no'}.\n"
                f"{result.message}"
            ),
        }
    if lowered.startswith("/swarm evaluate"):
        task = normalized[len("/swarm evaluate") :].strip()
        if not task:
            return {
                "command": "/swarm evaluate",
                "reply_text": "Usage: `/swarm evaluate <task>`.",
            }
        result = evaluate_swarm_escalation(
            config_manager=config_manager,
            state_db=state_db,
            task=task,
            run_id=run_id,
            request_id=request_id,
            channel_id="telegram",
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="telegram_runtime",
        )
        triggers = ", ".join(result.triggers) if result.triggers else "none"
        return {
            "command": "/swarm evaluate",
            "reply_text": (
                f"Swarm decision: {result.mode}.\n"
                f"Escalate: {'yes' if result.escalate else 'no'}.\n"
                f"Triggers: {triggers}.\n"
                f"{result.reason}"
            ),
        }
    return None


def _think_state_key(*, external_user_id: str) -> str:
    return f"telegram:think_visibility:{external_user_id}"


def _think_enabled_for_user(*, state_db: StateDB, external_user_id: str) -> bool:
    payload = _load_runtime_json_object(state_db, _think_state_key(external_user_id=external_user_id))
    return bool(payload.get("enabled", False))


def _set_think_enabled_for_user(
    *,
    state_db: StateDB,
    external_user_id: str,
    enabled: bool,
) -> None:
    set_runtime_state_value(
        state_db=state_db,
        state_key=_think_state_key(external_user_id=external_user_id),
        value=json.dumps({"enabled": enabled}, sort_keys=True),
    )


def _apply_think_visibility(
    *,
    state_db: StateDB,
    external_user_id: str,
    text: str,
) -> str:
    if _think_enabled_for_user(state_db=state_db, external_user_id=external_user_id):
        return text
    without_think = re.sub(r"(?is)<think>.*?</think>", "", text)
    collapsed = re.sub(r"\n{3,}", "\n\n", without_think).strip()
    return collapsed or text.strip()


def _apply_post_approval_welcome(
    *,
    state_db: StateDB,
    external_user_id: str,
    reply_text: str,
) -> str:
    welcome_pending = consume_pairing_welcome(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
    )
    if not welcome_pending:
        return reply_text
    welcome_text = "Pairing approved. Spark Intelligence is now active in this Telegram DM."
    return f"{welcome_text}\n\n{reply_text}".strip()


def _pairing_reply_text(default_text: str, *, inbound_text: str) -> str:
    normalized = inbound_text.strip().lower()
    if normalized in {"/start", "start"}:
        return (
            "Spark Intelligence received your start request. "
            "This Telegram account is waiting for operator approval before the agent will respond here."
        )
    return default_text


def _resolution_reply_text(*, decision: str, default_text: str, inbound_text: str) -> str:
    normalized = inbound_text.strip().lower()
    if decision == "pending_pairing":
        return _pairing_reply_text(default_text, inbound_text=inbound_text)
    if decision == "held":
        if normalized in {"/start", "start"}:
            return "Spark Intelligence received your start request, but this Telegram account is currently on hold pending operator review."
        return "This Telegram account is currently on hold pending operator review before the agent can reply here."
    if decision == "revoked":
        return "This Telegram account is no longer paired with Spark Intelligence. Contact the operator if this is unexpected."
    if decision == "channel_paused":
        return "Spark Intelligence is temporarily paused for this Telegram channel. Try again later."
    if decision == "channel_disabled":
        return "Spark Intelligence is currently disabled for this Telegram channel."
    if decision == "blocked" and normalized in {"/start", "start"}:
        return "This Telegram account is not currently paired with Spark Intelligence. Ask the operator to allowlist this account or enable pairing mode."
    return default_text


def _load_runtime_json_object(state_db: StateDB, state_key: str) -> dict[str, Any]:
    with state_db.connect() as conn:
        row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return {}
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_optional_text(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _read_optional_int(value: object) -> int:
    if value in {None, ""}:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
