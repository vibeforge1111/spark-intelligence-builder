from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.attachments import run_first_chip_hook_supporting
from spark_intelligence.auth.runtime import resolve_runtime_provider
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.guardrails import (
    apply_inbound_rate_limit,
    is_duplicate_event,
    load_channel_security_policy,
    prepare_outbound_text,
    set_runtime_state_value,
)
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit
from spark_intelligence.identity.service import (
    consume_pairing_welcome,
    pairing_welcome_pending,
    read_canonical_agent_state,
    record_pairing_context,
    resolve_inbound_dm,
)
from spark_intelligence.observability.store import build_text_mutation_facts, close_run, open_run, record_event
from spark_intelligence.personality import (
    apply_telegram_surface_persona,
    build_telegram_surface_identity_preamble,
    detect_and_persist_agent_persona_preferences,
    load_personality_profile,
    maybe_handle_agent_persona_onboarding_turn,
)
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply, record_researcher_bridge_result
from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import JSON_RICHNESS_MERGE_GUARD
from spark_intelligence.swarm_bridge import (
    evaluate_swarm_escalation,
    swarm_bridge_autoloop,
    swarm_bridge_execute_rerun_request,
    swarm_bridge_list_autoloop_sessions,
    swarm_bridge_list_paths,
    swarm_bridge_read_autoloop_session,
    swarm_bridge_run_specialization_path,
    swarm_absorb_insight,
    swarm_deliver_upgrade,
    swarm_read_collective_snapshot,
    swarm_read_evolution_inbox,
    swarm_read_insights,
    swarm_read_live_session,
    swarm_read_masteries,
    swarm_read_operator_issues,
    swarm_read_overview,
    swarm_read_runtime_pulse,
    swarm_read_specializations,
    swarm_read_upgrades,
    swarm_review_mastery,
    swarm_set_evolution_mode,
    swarm_sync_upgrade_delivery_status,
    swarm_status,
    sync_swarm_collective,
)


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


def _shape_telegram_bridge_reply(reply_text: str, *, bridge_mode: str | None, routing_decision: str | None) -> str:
    text = str(reply_text or "").strip()
    mode = str(bridge_mode or "").strip()
    route = str(routing_decision or "").strip()
    detail_match = re.match(r"^\[[^\]]+\]\s*(.*)$", text, flags=re.DOTALL)
    detail = str(detail_match.group(1)).strip() if detail_match else text
    citation_warning = "Source capture failed on the result page, so retry the search if you need an authoritative citation."
    if mode == "browser_evidence" and citation_warning in text:
        lines: list[str] = []
        next_inserted = False
        for raw_line in text.split("\n"):
            stripped = raw_line.strip()
            if stripped != citation_warning:
                lines.append(raw_line)
                continue
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("Citation status: source capture failed on the result page.")
            lines.append("Next: retry the search if you need an authoritative citation.")
            next_inserted = True
        shaped = "\n".join(lines).strip()
        if next_inserted:
            return shaped
    if mode == "disabled" or route == "bridge_disabled":
        return "\n".join(
            [
                "Researcher is unavailable right now.",
                "Reason: the Spark Researcher bridge is disabled for this workspace.",
                "Next: enable Spark Researcher for this workspace, then retry.",
            ]
        )
    if route == "secret_boundary_blocked":
        return "\n".join(
            [
                "Research request was blocked.",
                "Reason: sensitive material was detected in model-visible context.",
                "Next: remove the sensitive material from the request, then retry.",
            ]
        )
    if route == "provider_resolution_failed":
        lines = [
            "Researcher is unavailable right now.",
            "Reason: provider authentication is not configured correctly.",
        ]
        if detail and detail != text:
            lines.append(f"Detail: {detail}")
        lines.append("Next: fix the researcher provider auth configuration, then retry.")
        return "\n".join(lines)
    if route == "bridge_error":
        lines = [
            "Researcher is unavailable right now.",
            "Reason: the external researcher bridge failed during execution.",
        ]
        if detail and detail != text:
            lines.append(f"Detail: {detail}")
        lines.append("Next: inspect the researcher runtime, then retry.")
        return "\n".join(lines)
    if route == "stub" or mode == "stub":
        return "\n".join(
            [
                "Researcher is unavailable right now.",
                "Reason: no external Spark Researcher runtime is configured for this workspace.",
                "Next: configure or attach Spark Researcher, then retry.",
            ]
        )
    if mode != "blocked":
        return text
    if route == "browser_permission_required":
        origin_match = re.search(r"host access for (\S+)", text, flags=re.IGNORECASE)
        origin = str(origin_match.group(1)).rstrip(".,") if origin_match else "the requested site"
        return "\n".join(
            [
                "Web search is blocked right now.",
                f"Reason: the browser extension does not have site access for {origin}.",
                f"Next: open the extension popup, grant site access for {origin}, then retry.",
            ]
        )
    if route == "browser_unavailable":
        return "\n".join(
            [
                "Web search is unavailable right now.",
                "Reason: the live browser session is disconnected.",
                "Next: reconnect the Spark Browser session, then retry.",
            ]
        )
    return text


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


def _resolve_telegram_bot_token(config_manager: ConfigManager) -> str | None:
    auth_ref = config_manager.get_path("channels.records.telegram.auth_ref")
    if not auth_ref:
        return None
    env_map = config_manager.read_env_map()
    token = env_map.get(str(auth_ref))
    return str(token).strip() if token else None


def _resolve_telegram_client(config_manager: ConfigManager) -> TelegramBotApiClient | None:
    token = _resolve_telegram_bot_token(config_manager)
    if not token:
        return None
    return TelegramBotApiClient(token=token)


def _build_voice_chip_payload(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str | None = None,
    agent_id: str | None = None,
    normalized: Any | None = None,
    audio_bytes: bytes | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "surface": "telegram",
        "human_id": human_id,
        "agent_id": agent_id,
        "builder_env_file_path": str(config_manager.paths.env_file.resolve()),
    }
    try:
        provider = resolve_runtime_provider(config_manager=config_manager, state_db=state_db)
        secret_ref = getattr(provider, "secret_ref", None)
        payload["provider"] = {
            "provider_id": provider.provider_id,
            "provider_kind": provider.provider_kind,
            "auth_method": provider.auth_method,
            "api_mode": provider.api_mode,
            "execution_transport": provider.execution_transport,
            "base_url": provider.base_url,
            "default_model": provider.default_model,
            "secret_env_ref": (
                getattr(secret_ref, "ref_id", None) if secret_ref and getattr(secret_ref, "source", "") == "env" else None
            ),
        }
    except Exception as exc:
        payload["provider_error"] = str(exc)
    if normalized is not None:
        payload.update(
            {
                "message_kind": normalized.message_kind,
                "mime_type": normalized.media_mime_type,
                "duration_seconds": normalized.media_duration_seconds,
                "caption_text": normalized.caption_text,
                "filename": _telegram_media_filename(normalized=normalized, file_path=file_path or ""),
            }
        )
    if audio_bytes is not None:
        payload["audio_base64"] = base64.b64encode(audio_bytes).decode("ascii")
    return payload


def _prepare_telegram_media_input(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    normalized: Any,
    client: TelegramBotApiClient | None,
) -> dict[str, Any]:
    if normalized.message_kind not in {"voice", "audio"}:
        return {
            "effective_text": normalized.text,
            "transcript_text": None,
            "routing_decision": None,
        }
    media_client = client or _resolve_telegram_client(config_manager)
    if media_client is None:
        return {
            "effective_text": None,
            "transcript_text": None,
            "routing_decision": "voice_transcription_no_client",
            "reply_text": _render_telegram_voice_transcription_unavailable_reply(
                reason="the Telegram bot token is not available in this workspace.",
            ),
        }
    if not normalized.media_file_id:
        return {
            "effective_text": None,
            "transcript_text": None,
            "routing_decision": "voice_transcription_missing_file",
            "reply_text": _render_telegram_voice_transcription_unavailable_reply(
                reason="Telegram did not provide a downloadable media file id for that message.",
            ),
        }
    try:
        file_meta = media_client.get_file(file_id=normalized.media_file_id)
        file_path = str(file_meta.get("file_path") or "").strip()
        if not file_path:
            raise RuntimeError("Telegram did not return a file path for the media payload.")
        audio_bytes = media_client.download_file(file_path=file_path)
        execution = run_first_chip_hook_supporting(
            config_manager,
            hook="voice.transcribe",
            payload=_build_voice_chip_payload(
                config_manager=config_manager,
                state_db=state_db,
                normalized=normalized,
                audio_bytes=audio_bytes,
                file_path=file_path,
            ),
        )
        if execution is None:
            raise RuntimeError(
                "No attached chip supports `voice.transcribe`. Attach and activate `domain-chip-voice-comms` first."
            )
        result = execution.output.get("result") if isinstance(execution.output, dict) else None
        if not execution.ok:
            error_text = ""
            if isinstance(result, dict):
                error_text = str(result.get("error") or result.get("reply_text") or "").strip()
            if not error_text and isinstance(execution.output, dict):
                error_text = str(execution.output.get("error") or "").strip()
            if not error_text:
                error_text = str(execution.stderr or execution.stdout or "").strip()
            raise RuntimeError(error_text or f"Voice chip '{execution.chip_key}' could not transcribe the message.")
        transcript_text = str((result or {}).get("transcript_text") or "").strip()
        if not transcript_text:
            raise RuntimeError("The voice chip returned no transcript text.")
        effective_text = transcript_text
        if normalized.caption_text:
            effective_text = f"{normalized.caption_text}\n\nVoice transcript: {transcript_text}"
        return {
            "effective_text": effective_text,
            "transcript_text": transcript_text,
            "routing_decision": "voice_transcribed",
            "reply_text": None,
            "provider_id": str((result or {}).get("provider_id") or "").strip() or None,
            "provider_model": str((result or {}).get("model") or "").strip() or None,
            "provider_mode": str((result or {}).get("mode") or "").strip() or None,
        }
    except Exception as exc:
        return {
            "effective_text": None,
            "transcript_text": None,
            "routing_decision": "voice_transcription_unavailable",
            "reply_text": _render_telegram_voice_transcription_unavailable_reply(reason=str(exc)),
            "error": str(exc),
        }


def _telegram_media_filename(*, normalized: Any, file_path: str) -> str:
    suffix = Path(str(file_path)).suffix or (".ogg" if normalized.message_kind == "voice" else ".mp3")
    stem = "telegram-voice" if normalized.message_kind == "voice" else "telegram-audio"
    return f"{stem}{suffix}"


def _build_voice_trace_fields(
    *,
    media_input: dict[str, Any] | None,
    transcript_text: str | None,
) -> dict[str, Any]:
    transcript = str(transcript_text or "").strip()
    return {
        "voice_transcript_preview": _preview_text(transcript, limit=120) if transcript else None,
        "voice_transcript_length": len(transcript) if transcript else 0,
        "voice_transcribe_provider_id": str((media_input or {}).get("provider_id") or "").strip() or None,
        "voice_transcribe_model": str((media_input or {}).get("provider_model") or "").strip() or None,
        "voice_transcribe_mode": str((media_input or {}).get("provider_mode") or "").strip() or None,
        "voice_transcription_error": str((media_input or {}).get("error") or "").strip() or None,
    }


def simulate_telegram_update(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    update_payload: dict[str, Any],
    client: TelegramBotApiClient | None = None,
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
    effective_text = normalized.text
    transcript_text = None
    if resolution.allowed and resolution.agent_id and resolution.human_id and resolution.session_id:
        media_input = _prepare_telegram_media_input(
            config_manager=config_manager,
            state_db=state_db,
            normalized=normalized,
            client=client,
        )
        if media_input.get("reply_text"):
            outbound_text = _apply_saved_telegram_surface_style(
                config_manager=config_manager,
                state_db=state_db,
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                reply_text=str(media_input["reply_text"]),
                surface="runtime_command",
            )
            trace_ref = None
            bridge_mode = "runtime_command"
            attachment_context = None
            routing_decision = str(media_input.get("routing_decision") or "runtime_command")
            active_chip_key = None
            active_chip_task_type = None
            active_chip_evaluate_used = False
            evidence_summary = None
        else:
            effective_text = str(media_input.get("effective_text") or normalized.text)
            transcript_text = media_input.get("transcript_text")
        command_result = _handle_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            inbound_text=effective_text,
            run_id=None,
            request_id=f"sim:{normalized.update_id}",
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
        )
        if media_input.get("reply_text"):
            pass
        elif command_result is not None:
            outbound_text = _apply_saved_telegram_surface_style(
                config_manager=config_manager,
                state_db=state_db,
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                reply_text=command_result["reply_text"],
                surface="runtime_command",
            )
            trace_ref = None
            bridge_mode = "runtime_command"
            attachment_context = None
            routing_decision = "runtime_command"
            active_chip_key = None
            active_chip_task_type = None
            active_chip_evaluate_used = False
            evidence_summary = None
        else:
            onboarding_result = maybe_handle_agent_persona_onboarding_turn(
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                user_message=effective_text,
                state_db=state_db,
                source_surface="telegram",
                source_ref=f"sim:{normalized.update_id}",
                start_if_eligible=pairing_welcome_pending(
                    state_db=state_db,
                    channel_id="telegram",
                    external_user_id=normalized.telegram_user_id,
                ),
            )
            if onboarding_result is not None:
                outbound_text = _apply_post_approval_welcome(
                    config_manager=config_manager,
                    state_db=state_db,
                    external_user_id=normalized.telegram_user_id,
                    human_id=resolution.human_id,
                    agent_id=resolution.agent_id,
                    reply_text=onboarding_result.reply_text,
                )
                trace_ref = None
                bridge_mode = "agent_onboarding"
                attachment_context = None
                routing_decision = "agent_onboarding"
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
                    user_message=effective_text,
                )
                record_researcher_bridge_result(state_db=state_db, result=bridge_result)
                shaped_bridge_reply = _shape_telegram_bridge_reply(
                    bridge_result.reply_text,
                    bridge_mode=bridge_result.mode,
                    routing_decision=bridge_result.routing_decision,
                )
                outbound_text = _apply_post_approval_welcome(
                    config_manager=config_manager,
                    state_db=state_db,
                    external_user_id=normalized.telegram_user_id,
                    human_id=resolution.human_id,
                    agent_id=resolution.agent_id,
                    reply_text=shaped_bridge_reply,
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
        outbound_text = _strip_internal_swarm_recommendation(outbound_text)
        outbound_text = _apply_saved_telegram_surface_style(
            config_manager=config_manager,
            state_db=state_db,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            reply_text=outbound_text,
            surface="telegram_chat",
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
        "message_text": effective_text,
        "message_kind": normalized.message_kind,
        "transcript_text": transcript_text,
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

        effective_text = normalized.text
        transcript_text = None
        media_input = _prepare_telegram_media_input(
            config_manager=config_manager,
            state_db=state_db,
            normalized=normalized,
            client=client,
        )
        if media_input.get("reply_text"):
            outbound_text = _apply_think_visibility(
                state_db=state_db,
                external_user_id=normalized.telegram_user_id,
                text=_apply_saved_telegram_surface_style(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=resolution.human_id,
                    agent_id=resolution.agent_id,
                    reply_text=str(media_input["reply_text"]),
                    surface="runtime_command",
                ),
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
                routing_decision=str(media_input.get("routing_decision") or "voice_transcription_unavailable"),
                run_id=run.run_id,
                request_id=run.request_id,
                trace_ref=None,
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                respect_voice_reply_state=False,
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
                close_reason=str(media_input.get("routing_decision") or "voice_transcription_unavailable"),
                summary=f"Telegram {normalized.message_kind} message closed after transcription handling.",
                facts={
                    "message_kind": normalized.message_kind,
                    "delivery_ok": send_result["ok"],
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=None),
                },
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
                    "command": f"/{normalized.message_kind}",
                    "delivery_ok": send_result["ok"],
                    "delivery_error": send_result["error"],
                    "guardrail_actions": send_result["guardrail_actions"],
                    "response_preview": _preview_text(outbound_text),
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=None),
                },
            )
            continue
        effective_text = str(media_input.get("effective_text") or normalized.text)
        transcript_text = media_input.get("transcript_text")
        voice_origin_reply = normalized.message_kind in {"voice", "audio"} and bool(transcript_text)

        command_result = _handle_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            inbound_text=effective_text,
            run_id=run.run_id,
            request_id=run.request_id,
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
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
                text=_apply_saved_telegram_surface_style(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=resolution.human_id,
                    agent_id=resolution.agent_id,
                    reply_text=command_result["reply_text"],
                    surface="runtime_command",
                ),
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
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                force_voice=bool(command_result.get("force_voice", False)) or voice_origin_reply,
                voice_text=(
                    str(command_result.get("voice_text")).strip()
                    if command_result.get("voice_text") is not None
                    else (outbound_text if voice_origin_reply else None)
                ),
                respect_voice_reply_state=bool(command_result.get("respect_voice_reply_state", True)),
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
                facts={
                    "command": command_result["command"],
                    "delivery_ok": send_result["ok"],
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
                },
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
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
                },
            )
            continue

        onboarding_result = maybe_handle_agent_persona_onboarding_turn(
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            user_message=effective_text,
            state_db=state_db,
            source_surface="telegram",
            source_ref=run.request_id,
            start_if_eligible=pairing_welcome_pending(
                state_db=state_db,
                channel_id="telegram",
                external_user_id=normalized.telegram_user_id,
            ),
        )
        if onboarding_result is not None:
            record_event(
                state_db,
                event_type="intent_committed",
                component="telegram_runtime",
                summary="Telegram agent onboarding turn committed for execution.",
                run_id=run.run_id,
                request_id=run.request_id,
                channel_id="telegram",
                session_id=resolution.session_id,
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                actor_id="telegram_runtime",
                reason_code="agent_onboarding",
                facts={
                    "step": onboarding_result.step,
                    "completed": onboarding_result.completed,
                    "update_id": normalized.update_id,
                    "message_text": effective_text,
                    "message_kind": normalized.message_kind,
                    "transcript_text": transcript_text,
                },
            )
            outbound_text = _apply_think_visibility(
                state_db=state_db,
                external_user_id=normalized.telegram_user_id,
                text=_apply_post_approval_welcome(
                    config_manager=config_manager,
                    state_db=state_db,
                    external_user_id=normalized.telegram_user_id,
                    human_id=resolution.human_id,
                    agent_id=resolution.agent_id,
                    reply_text=onboarding_result.reply_text,
                ),
            )
            send_result = _send_telegram_reply(
                config_manager=config_manager,
                state_db=state_db,
                client=client,
                chat_id=normalized.chat_id,
                text=outbound_text,
                event="telegram_agent_onboarding_outbound",
                update_id=normalized.update_id,
                telegram_user_id=normalized.telegram_user_id,
                session_id=resolution.session_id,
                decision=resolution.decision,
                bridge_mode="agent_onboarding",
                routing_decision="agent_onboarding",
                run_id=run.run_id,
                request_id=run.request_id,
                trace_ref=None,
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                force_voice=voice_origin_reply,
                voice_text=outbound_text if voice_origin_reply else None,
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
                close_reason="agent_onboarding",
                summary=f"Telegram agent onboarding step {onboarding_result.step} closed.",
                facts={
                    "step": onboarding_result.step,
                    "completed": onboarding_result.completed,
                    "delivery_ok": send_result["ok"],
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
                },
            )
            append_gateway_trace(
                config_manager,
                {
                    "event": "telegram_agent_onboarding_processed",
                    "channel_id": "telegram",
                    "update_id": normalized.update_id,
                    "telegram_user_id": normalized.telegram_user_id,
                    "chat_id": normalized.chat_id,
                    "session_id": resolution.session_id,
                    "step": onboarding_result.step,
                    "completed": onboarding_result.completed,
                    "delivery_ok": send_result["ok"],
                    "delivery_error": send_result["error"],
                    "guardrail_actions": send_result["guardrail_actions"],
                    "response_preview": _preview_text(outbound_text),
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
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
                "message_text": effective_text,
                "message_kind": normalized.message_kind,
                "transcript_text": transcript_text,
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
            user_message=effective_text,
            run_id=run.run_id,
        )
        record_researcher_bridge_result(state_db=state_db, result=bridge_result)
        shaped_bridge_reply = _shape_telegram_bridge_reply(
            bridge_result.reply_text,
            bridge_mode=bridge_result.mode,
            routing_decision=bridge_result.routing_decision,
        )
        outbound_text = _apply_post_approval_welcome(
            config_manager=config_manager,
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            reply_text=shaped_bridge_reply,
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
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            force_voice=voice_origin_reply,
            voice_text=outbound_text if voice_origin_reply else None,
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
                **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
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
                **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
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


def _describe_telegram_delivery_exception(exc: Exception) -> str:
    if isinstance(exc, RuntimeError):
        return str(exc)
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, URLError):
        return str(exc.reason)
    return str(exc)


def _synthesize_telegram_voice_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    text: str,
    human_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    execution = run_first_chip_hook_supporting(
        config_manager,
        hook="voice.speak",
        payload={
            **_build_voice_chip_payload(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                agent_id=agent_id,
            ),
            "text": text,
        },
    )
    if execution is None:
        raise RuntimeError("No attached chip supports `voice.speak`. Attach and activate `domain-chip-voice-comms` first.")
    result = execution.output.get("result") if isinstance(execution.output, dict) else None
    if not execution.ok:
        reason = ""
        if isinstance(result, dict):
            reason = str(result.get("error") or result.get("reply_text") or "").strip()
        if not reason and isinstance(execution.output, dict):
            reason = str(execution.output.get("error") or "").strip()
        if not reason:
            reason = str(execution.stderr or execution.stdout or "").strip()
        raise RuntimeError(reason or f"Voice chip '{execution.chip_key}' could not synthesize the reply.")
    audio_base64 = str((result or {}).get("audio_base64") or "").strip()
    if not audio_base64:
        raise RuntimeError("The voice chip returned no audio payload.")
    mime_type = str((result or {}).get("mime_type") or "audio/mpeg").strip() or "audio/mpeg"
    return {
        "audio_bytes": base64.b64decode(audio_base64),
        "mime_type": mime_type,
        "filename": "telegram-reply.mp3" if "mpeg" in mime_type or "mp3" in mime_type else "telegram-reply.audio",
        "provider_id": str((result or {}).get("provider_id") or "").strip() or None,
        "voice_id": str((result or {}).get("voice_id") or "").strip() or None,
    }


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
    human_id: str | None = None,
    agent_id: str | None = None,
    force_voice: bool = False,
    voice_text: str | None = None,
    respect_voice_reply_state: bool = True,
) -> dict[str, Any]:
    if output_keepability is None and event != "telegram_bridge_outbound":
        output_keepability = "operator_debug_only"
    if promotion_disposition is None and output_keepability in {
        "ephemeral_context",
        "user_preference_ephemeral",
        "operator_debug_only",
    }:
        promotion_disposition = "not_promotable"
    policy = _telegram_security_policy(config_manager)
    visible_text = _apply_think_visibility(
        state_db=state_db,
        external_user_id=telegram_user_id,
        text=text,
    )
    filtered_text = _strip_internal_swarm_recommendation(visible_text)
    filtered_text = _apply_saved_telegram_surface_style(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
        reply_text=filtered_text,
        surface="telegram_chat",
    )
    guarded = prepare_outbound_text(
        config_manager=config_manager,
        state_db=state_db,
        text=filtered_text,
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
    if filtered_text != visible_text:
        guarded["actions"] = ["strip_swarm_routing_note", *list(guarded["actions"])]
    voice_requested = force_voice or (
        respect_voice_reply_state
        and _voice_reply_enabled_for_user(state_db=state_db, external_user_id=telegram_user_id)
    )
    delivery_medium = "text"
    voice_error: str | None = None
    voice_payload: dict[str, Any] | None = None
    if voice_requested:
        spoken_text = str(voice_text or guarded["text"]).strip()
        if spoken_text:
            try:
                voice_payload = _synthesize_telegram_voice_reply(
                    config_manager=config_manager,
                    state_db=state_db,
                    text=spoken_text,
                    human_id=human_id,
                    agent_id=agent_id,
                )
                delivery_medium = "audio"
            except Exception as exc:
                voice_error = str(exc)
                guarded["actions"] = ["voice_reply_fallback_to_text", *list(guarded["actions"])]
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
            "delivery_medium": delivery_medium,
            "voice_requested": voice_requested,
            "voice_error": voice_error,
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
        if voice_payload is not None:
            client.send_audio(
                chat_id=chat_id,
                audio_bytes=voice_payload["audio_bytes"],
                filename=str(voice_payload["filename"]),
                mime_type=str(voice_payload["mime_type"]),
                caption=guarded["text"] if len(guarded["text"]) <= 1024 else None,
            )
        else:
            client.send_message(chat_id=chat_id, text=guarded["text"])
    except (RuntimeError, HTTPError, URLError) as exc:
        if voice_payload is not None:
            voice_error = _describe_telegram_delivery_exception(exc)
            guarded["actions"] = ["voice_delivery_fallback_to_text", *list(guarded["actions"])]
            delivery_medium = "text"
            try:
                client.send_message(chat_id=chat_id, text=guarded["text"])
            except (RuntimeError, HTTPError, URLError) as fallback_exc:
                ok = False
                error = _describe_telegram_delivery_exception(fallback_exc)
        else:
            ok = False
            error = _describe_telegram_delivery_exception(exc)
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
            "delivery_medium": delivery_medium,
            "voice_requested": voice_requested,
            "voice_error": voice_error,
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
            "delivery_medium": delivery_medium,
            "voice_requested": voice_requested,
            "voice_error": voice_error,
            "response_preview": _preview_text(guarded["text"]),
            "response_length": len(guarded["text"]),
        },
    )
    return {
        "ok": ok,
        "error": error,
        "guardrail_actions": guarded["actions"],
        "delivery_medium": delivery_medium,
        "voice_error": voice_error,
    }


def _preview_text(text: str, *, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _load_telegram_persona_surface_state(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str | None,
    agent_id: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not human_id:
        return None, None
    profile = load_personality_profile(
        human_id=human_id,
        agent_id=agent_id,
        state_db=state_db,
        config_manager=config_manager,
    )
    try:
        agent_name = read_canonical_agent_state(state_db=state_db, human_id=human_id).agent_name
    except Exception:
        agent_name = None
    return profile, agent_name


def _apply_saved_telegram_surface_style(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str | None,
    agent_id: str | None,
    reply_text: str,
    surface: str,
) -> str:
    profile, agent_name = _load_telegram_persona_surface_state(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
    )
    return apply_telegram_surface_persona(
        reply_text=reply_text,
        profile=profile,
        agent_name=agent_name,
        surface=surface,
    )


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
) -> dict[str, Any] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    natural_swarm_command = _match_natural_swarm_command(normalized)
    style_command = _parse_style_command(normalized)
    if style_command is not None:
        return _handle_style_command(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
            command=style_command["command"],
            payload=style_command.get("payload"),
        )
    if lowered in {"/voice", "/voice status"}:
        return _run_voice_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            command="/voice",
            hook="voice.status",
            fallback_reply=_render_telegram_voice_status_reply(),
            human_id=human_id,
            agent_id=agent_id,
        )
    if lowered in {"/voice reply", "/voice reply status"}:
        enabled = _voice_reply_enabled_for_user(state_db=state_db, external_user_id=external_user_id)
        state_text = "on" if enabled else "off"
        return {
            "command": "/voice reply",
            "reply_text": (
                f"Voice replies are currently {state_text} for this Telegram DM. "
                "Use `/voice reply on` to send future replies as audio, `/voice reply off` to stay text-only, "
                "or `/voice speak <text>` for a one-shot spoken reply."
            ),
            "respect_voice_reply_state": False,
        }
    if lowered in {"/voice reply on", "/voice reply off"}:
        enabled = lowered == "/voice reply on"
        _set_voice_reply_enabled_for_user(
            state_db=state_db,
            external_user_id=external_user_id,
            enabled=enabled,
        )
        return {
            "command": lowered,
            "reply_text": (
                "Voice replies enabled for this Telegram DM. "
                "Next: ask a normal question or use `/voice speak <text>` to test the voice path."
                if enabled
                else "Voice replies disabled for this Telegram DM. Future replies will stay text-only unless you use `/voice speak <text>`."
            ),
            "respect_voice_reply_state": False,
        }
    if lowered == "/voice plan":
        return _run_voice_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            command="/voice plan",
            hook="voice.plan",
            fallback_reply=_render_telegram_voice_plan_reply(),
            human_id=human_id,
            agent_id=agent_id,
        )
    if lowered.startswith("/voice speak"):
        speak_text = normalized[len("/voice speak") :].strip()
        if not speak_text:
            return {
                "command": "/voice speak",
                "reply_text": "Voice speak needs text. Use `/voice speak <what you want me to say>`.",
                "respect_voice_reply_state": False,
            }
        return {
            "command": "/voice speak",
            "reply_text": (
                "Voice reply queued.\n"
                f"Text: {speak_text}\n"
                "Next: use `/voice reply on` if you want future replies in this DM to synthesize automatically."
            ),
            "force_voice": True,
            "voice_text": speak_text,
            "respect_voice_reply_state": False,
        }
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

    if lowered == "/swarm" or natural_swarm_command == ("/swarm", None):
        return {
            "command": "/swarm",
            "reply_text": (
                "Swarm commands: `/swarm status`, `/swarm overview`, `/swarm live`, `/swarm runtime`, "
                "`/swarm specializations`, `/swarm insights`, `/swarm masteries`, `/swarm upgrades`, `/swarm issues`, `/swarm inbox`, `/swarm collective`, `/swarm sync`, "
                "`/swarm paths`, `/swarm run <path_key>`, `/swarm autoloop <path_key> [rounds <n>]`, "
                "`/swarm continue <path_key> [session <id>] [rounds <n>]`, `/swarm sessions <path_key>`, "
                "`/swarm session <path_key> [latest|<session_id>]`, `/swarm rerun [path_key]`, "
                "`/swarm evaluate <task>`, `/swarm absorb <insight_id>`, `/swarm review <mastery_id> <approve|defer|reject> because <reason>`, "
                "`/swarm mode <specialization_id> <observe_only|review_required|checked_auto_merge|trusted_auto_apply>`, "
                "`/swarm deliver <upgrade_id>`, and `/swarm sync-delivery <upgrade_id>`."
            ),
        }
    if lowered == "/swarm status" or natural_swarm_command == ("/swarm status", None):
        status = swarm_status(config_manager, state_db)
        return {
            "command": "/swarm status",
            "reply_text": (
                f"Swarm is {'ready' if status.api_ready else 'not ready'}.\n"
                f"Auth is {status.auth_state}, last sync was {(status.last_sync or {}).get('mode', 'none')}, "
                f"and the last decision was {(status.last_decision or {}).get('mode', 'none')}.\n"
                "Next: `/swarm sync` or `/swarm collective`."
            ),
        }
    if lowered == "/swarm overview" or natural_swarm_command == ("/swarm overview", None):
        return _run_swarm_read_command(
            command="/swarm overview",
            loader=lambda: swarm_read_overview(config_manager, state_db),
            renderer=_render_swarm_overview_reply,
        )
    if lowered == "/swarm live" or natural_swarm_command == ("/swarm live", None):
        return _run_swarm_read_command(
            command="/swarm live",
            loader=lambda: swarm_read_live_session(config_manager, state_db),
            renderer=_render_swarm_live_reply,
        )
    if lowered == "/swarm runtime" or natural_swarm_command == ("/swarm runtime", None):
        return _run_swarm_read_command(
            command="/swarm runtime",
            loader=lambda: swarm_read_runtime_pulse(config_manager, state_db),
            renderer=_render_swarm_runtime_reply,
        )
    if lowered == "/swarm specializations" or natural_swarm_command == ("/swarm specializations", None):
        return _run_swarm_read_command(
            command="/swarm specializations",
            loader=lambda: swarm_read_specializations(config_manager, state_db),
            renderer=_render_swarm_specializations_reply,
        )
    if lowered == "/swarm insights" or natural_swarm_command == ("/swarm insights", None):
        return _run_swarm_read_command(
            command="/swarm insights",
            loader=lambda: swarm_read_insights(config_manager, state_db),
            renderer=_render_swarm_insights_reply,
        )
    scoped_insights_args = _parse_swarm_scoped_read_command(normalized, noun="insights")
    if scoped_insights_args:
        return _run_swarm_read_command(
            command="/swarm insights",
            loader=lambda: _load_swarm_scoped_insights(
                config_manager=config_manager,
                state_db=state_db,
                label=scoped_insights_args["label"],
            ),
            renderer=_render_swarm_scoped_insights_reply,
        )
    scoped_insights_resolution = _resolve_natural_swarm_scoped_read_target(
        inbound_text=normalized,
        noun="insights",
        config_manager=config_manager,
    )
    if scoped_insights_resolution is not None:
        if scoped_insights_resolution.get("error"):
            return {"command": "/swarm insights", "reply_text": str(scoped_insights_resolution["error"])}
        return _run_swarm_read_command(
            command="/swarm insights",
            loader=lambda: _load_swarm_scoped_insights(
                config_manager=config_manager,
                state_db=state_db,
                label=str(scoped_insights_resolution["label"]),
            ),
            renderer=_render_swarm_scoped_insights_reply,
        )
    if lowered == "/swarm masteries" or natural_swarm_command == ("/swarm masteries", None):
        return _run_swarm_read_command(
            command="/swarm masteries",
            loader=lambda: swarm_read_masteries(config_manager, state_db),
            renderer=_render_swarm_masteries_reply,
        )
    scoped_masteries_args = _parse_swarm_scoped_read_command(normalized, noun="masteries")
    if scoped_masteries_args:
        return _run_swarm_read_command(
            command="/swarm masteries",
            loader=lambda: _load_swarm_scoped_masteries(
                config_manager=config_manager,
                state_db=state_db,
                label=scoped_masteries_args["label"],
            ),
            renderer=_render_swarm_scoped_masteries_reply,
        )
    scoped_masteries_resolution = _resolve_natural_swarm_scoped_read_target(
        inbound_text=normalized,
        noun="masteries",
        config_manager=config_manager,
    )
    if scoped_masteries_resolution is not None:
        if scoped_masteries_resolution.get("error"):
            return {"command": "/swarm masteries", "reply_text": str(scoped_masteries_resolution["error"])}
        return _run_swarm_read_command(
            command="/swarm masteries",
            loader=lambda: _load_swarm_scoped_masteries(
                config_manager=config_manager,
                state_db=state_db,
                label=str(scoped_masteries_resolution["label"]),
            ),
            renderer=_render_swarm_scoped_masteries_reply,
        )
    if lowered == "/swarm upgrades" or natural_swarm_command == ("/swarm upgrades", None):
        return _run_swarm_read_command(
            command="/swarm upgrades",
            loader=lambda: swarm_read_upgrades(config_manager, state_db),
            renderer=_render_swarm_upgrades_reply,
        )
    scoped_upgrades_args = _parse_swarm_scoped_read_command(normalized, noun="upgrades")
    if scoped_upgrades_args:
        return _run_swarm_read_command(
            command="/swarm upgrades",
            loader=lambda: _load_swarm_scoped_upgrades(
                config_manager=config_manager,
                state_db=state_db,
                label=scoped_upgrades_args["label"],
            ),
            renderer=_render_swarm_scoped_upgrades_reply,
        )
    scoped_upgrades_resolution = _resolve_natural_swarm_scoped_read_target(
        inbound_text=normalized,
        noun="upgrades",
        config_manager=config_manager,
    )
    if scoped_upgrades_resolution is not None:
        if scoped_upgrades_resolution.get("error"):
            return {"command": "/swarm upgrades", "reply_text": str(scoped_upgrades_resolution["error"])}
        return _run_swarm_read_command(
            command="/swarm upgrades",
            loader=lambda: _load_swarm_scoped_upgrades(
                config_manager=config_manager,
                state_db=state_db,
                label=str(scoped_upgrades_resolution["label"]),
            ),
            renderer=_render_swarm_scoped_upgrades_reply,
        )
    if lowered == "/swarm issues" or natural_swarm_command == ("/swarm issues", None):
        return _run_swarm_read_command(
            command="/swarm issues",
            loader=lambda: swarm_read_operator_issues(config_manager, state_db),
            renderer=_render_swarm_operator_issues_reply,
            unavailable_message=(
                "Swarm operator issues are unavailable right now.\n"
                "The current Spark Swarm host does not expose the operator issues route yet."
            ),
        )
    if lowered == "/swarm inbox" or natural_swarm_command == ("/swarm inbox", None):
        return _run_swarm_read_command(
            command="/swarm inbox",
            loader=lambda: swarm_read_evolution_inbox(config_manager, state_db),
            renderer=_render_swarm_inbox_reply,
        )
    if lowered == "/swarm collective" or natural_swarm_command == ("/swarm collective", None):
        return _run_swarm_read_command(
            command="/swarm collective",
            loader=lambda: swarm_read_collective_snapshot(config_manager, state_db),
            renderer=_render_swarm_collective_reply,
        )
    if lowered == "/swarm paths" or natural_swarm_command == ("/swarm paths", None):
        return _run_swarm_read_command(
            command="/swarm paths",
            loader=lambda: swarm_bridge_list_paths(config_manager),
            renderer=_render_swarm_paths_reply,
        )
    if lowered == "/swarm sync" or natural_swarm_command == ("/swarm sync", None):
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
                f"Swarm sync {'completed' if result.ok else 'failed'}.\n"
                f"Mode: {result.mode}. Accepted: {'yes' if result.accepted else 'no'}.\n"
                f"{result.message}\n"
                "Next: `/swarm status` to confirm the current bridge state."
            ),
        }
    run_args = _parse_swarm_path_run_command(normalized)
    if run_args:
        return _run_swarm_bridge_command(
            command="/swarm run",
            runner=lambda: swarm_bridge_run_specialization_path(
                config_manager,
                path_key=run_args["path_key"],
            ),
            renderer=_render_swarm_bridge_run_reply,
        )
    run_resolution = _resolve_natural_swarm_run_target(
        inbound_text=normalized,
        config_manager=config_manager,
    )
    if run_resolution is not None:
        if run_resolution.get("error"):
            return {"command": "/swarm run", "reply_text": str(run_resolution["error"])}
        return _run_swarm_bridge_command(
            command="/swarm run",
            runner=lambda: swarm_bridge_run_specialization_path(
                config_manager,
                path_key=str(run_resolution["path_key"]),
            ),
            renderer=_render_swarm_bridge_run_reply,
        )
    autoloop_args = _parse_swarm_autoloop_command(normalized)
    if autoloop_args:
        prepared_autoloop = _prepare_swarm_autoloop_request(
            config_manager=config_manager,
            payload=autoloop_args,
        )
        if prepared_autoloop.get("error"):
            return {"command": "/swarm autoloop", "reply_text": str(prepared_autoloop["error"])}
        return _run_swarm_bridge_command(
            command="/swarm autoloop",
            runner=lambda: swarm_bridge_autoloop(
                config_manager,
                path_key=str(prepared_autoloop["path_key"]),
                rounds=int(prepared_autoloop.get("rounds") or 1),
                session_id=str(prepared_autoloop.get("session_id") or "") or None,
                allow_fallback_planner=bool(prepared_autoloop.get("allow_fallback_planner")),
                force=bool(prepared_autoloop.get("force")),
            ),
            renderer=_render_swarm_bridge_autoloop_reply,
        )
    autoloop_resolution = _resolve_natural_swarm_autoloop_target(
        inbound_text=normalized,
        config_manager=config_manager,
    )
    if autoloop_resolution is not None:
        if autoloop_resolution.get("error"):
            return {"command": "/swarm autoloop", "reply_text": str(autoloop_resolution["error"])}
        prepared_autoloop = _prepare_swarm_autoloop_request(
            config_manager=config_manager,
            payload=autoloop_resolution,
        )
        if prepared_autoloop.get("error"):
            return {"command": "/swarm autoloop", "reply_text": str(prepared_autoloop["error"])}
        return _run_swarm_bridge_command(
            command="/swarm autoloop",
            runner=lambda: swarm_bridge_autoloop(
                config_manager,
                path_key=str(prepared_autoloop["path_key"]),
                rounds=int(prepared_autoloop.get("rounds") or 1),
                session_id=str(prepared_autoloop.get("session_id") or "") or None,
                allow_fallback_planner=bool(prepared_autoloop.get("allow_fallback_planner")),
                force=bool(prepared_autoloop.get("force")),
            ),
            renderer=_render_swarm_bridge_autoloop_reply,
        )
    sessions_args = _parse_swarm_sessions_command(normalized)
    if sessions_args:
        return _run_swarm_read_command(
            command="/swarm sessions",
            loader=lambda: swarm_bridge_list_autoloop_sessions(
                config_manager,
                path_key=sessions_args["path_key"],
            ),
            renderer=_render_swarm_sessions_reply,
        )
    sessions_resolution = _resolve_natural_swarm_sessions_target(
        inbound_text=normalized,
        config_manager=config_manager,
    )
    if sessions_resolution is not None:
        if sessions_resolution.get("error"):
            return {"command": "/swarm sessions", "reply_text": str(sessions_resolution["error"])}
        return _run_swarm_read_command(
            command="/swarm sessions",
            loader=lambda: swarm_bridge_list_autoloop_sessions(
                config_manager,
                path_key=str(sessions_resolution["path_key"]),
            ),
            renderer=_render_swarm_sessions_reply,
        )
    session_args = _parse_swarm_session_command(normalized)
    if session_args:
        return _run_swarm_read_command(
            command="/swarm session",
            loader=lambda: swarm_bridge_read_autoloop_session(
                config_manager,
                path_key=session_args["path_key"],
                session_id=session_args.get("session_id"),
            ),
            renderer=_render_swarm_session_reply,
        )
    session_resolution = _resolve_natural_swarm_session_target(
        inbound_text=normalized,
        config_manager=config_manager,
    )
    if session_resolution is not None:
        if session_resolution.get("error"):
            return {"command": "/swarm session", "reply_text": str(session_resolution["error"])}
        return _run_swarm_read_command(
            command="/swarm session",
            loader=lambda: swarm_bridge_read_autoloop_session(
                config_manager,
                path_key=str(session_resolution["path_key"]),
                session_id=str(session_resolution.get("session_id") or "") or None,
            ),
            renderer=_render_swarm_session_reply,
        )
    rerun_args = _parse_swarm_rerun_command(normalized)
    if rerun_args is not None:
        return _run_swarm_bridge_command(
            command="/swarm rerun",
            runner=lambda: swarm_bridge_execute_rerun_request(
                config_manager,
                path_key=rerun_args.get("path_key"),
            ),
            renderer=_render_swarm_bridge_rerun_reply,
        )
    rerun_resolution = _resolve_natural_swarm_rerun_target(
        inbound_text=normalized,
        config_manager=config_manager,
    )
    if rerun_resolution is not None:
        if rerun_resolution.get("error"):
            return {"command": "/swarm rerun", "reply_text": str(rerun_resolution["error"])}
        return _run_swarm_bridge_command(
            command="/swarm rerun",
            runner=lambda: swarm_bridge_execute_rerun_request(
                config_manager,
                path_key=str(rerun_resolution.get("path_key") or "") or None,
            ),
            renderer=_render_swarm_bridge_rerun_reply,
        )
    absorb_args = _parse_swarm_absorb_command(normalized)
    if absorb_args:
        return _run_swarm_action_command(
            command="/swarm absorb",
            runner=lambda: swarm_absorb_insight(
                config_manager,
                state_db,
                insight_id=absorb_args["insight_id"],
                reason=absorb_args.get("reason"),
            ),
            renderer=_render_swarm_absorb_reply,
        )
    absorb_resolution = _resolve_natural_swarm_absorb_target(
        inbound_text=normalized,
        config_manager=config_manager,
        state_db=state_db,
    )
    if absorb_resolution is not None:
        if absorb_resolution.get("error"):
            return {
                "command": "/swarm absorb",
                "reply_text": str(absorb_resolution["error"]),
            }
        return _run_swarm_action_command(
            command="/swarm absorb",
            runner=lambda: swarm_absorb_insight(
                config_manager,
                state_db,
                insight_id=str(absorb_resolution["insight_id"]),
                reason=str(absorb_resolution["reason"]) if absorb_resolution.get("reason") else None,
            ),
            renderer=_render_swarm_absorb_reply,
        )
    review_args = _parse_swarm_review_command(normalized)
    if review_args:
        if not review_args["reason"]:
            return {
                "command": "/swarm review",
                "reply_text": "Usage: `/swarm review <mastery_id> <approve|defer|reject> because <reason>`.",
            }
        return _run_swarm_action_command(
            command="/swarm review",
            runner=lambda: swarm_review_mastery(
                config_manager,
                state_db,
                mastery_id=review_args["mastery_id"],
                decision=review_args["decision"],
                reason=review_args["reason"],
            ),
            renderer=_render_swarm_review_reply,
        )
    review_resolution = _resolve_natural_swarm_review_target(
        inbound_text=normalized,
        config_manager=config_manager,
        state_db=state_db,
    )
    if review_resolution is not None:
        if review_resolution.get("error"):
            return {
                "command": "/swarm review",
                "reply_text": str(review_resolution["error"]),
            }
        return _run_swarm_action_command(
            command="/swarm review",
            runner=lambda: swarm_review_mastery(
                config_manager,
                state_db,
                mastery_id=str(review_resolution["mastery_id"]),
                decision=str(review_resolution["decision"]),
                reason=str(review_resolution["reason"]),
            ),
            renderer=_render_swarm_review_reply,
        )
    mode_args = _parse_swarm_mode_command(normalized)
    if mode_args:
        return _run_swarm_action_command(
            command="/swarm mode",
            runner=lambda: swarm_set_evolution_mode(
                config_manager,
                state_db,
                specialization_id=mode_args["specialization_id"],
                evolution_mode=mode_args["evolution_mode"],
            ),
            renderer=_render_swarm_mode_reply,
        )
    mode_resolution = _resolve_natural_swarm_mode_target(
        inbound_text=normalized,
        config_manager=config_manager,
        state_db=state_db,
    )
    if mode_resolution is not None:
        if mode_resolution.get("error"):
            return {
                "command": "/swarm mode",
                "reply_text": str(mode_resolution["error"]),
            }
        return _run_swarm_action_command(
            command="/swarm mode",
            runner=lambda: swarm_set_evolution_mode(
                config_manager,
                state_db,
                specialization_id=str(mode_resolution["specialization_id"]),
                evolution_mode=str(mode_resolution["evolution_mode"]),
            ),
            renderer=_render_swarm_mode_reply,
        )
    deliver_args = _parse_swarm_deliver_command(normalized)
    if deliver_args:
        return _run_swarm_action_command(
            command="/swarm deliver",
            runner=lambda: swarm_deliver_upgrade(
                config_manager,
                state_db,
                upgrade_id=deliver_args["upgrade_id"],
                evolution_mode=deliver_args.get("evolution_mode"),
                pr_url=deliver_args.get("pr_url"),
            ),
            renderer=_render_swarm_delivery_reply,
        )
    deliver_resolution = _resolve_natural_swarm_deliver_target(
        inbound_text=normalized,
        config_manager=config_manager,
        state_db=state_db,
    )
    if deliver_resolution is not None:
        if deliver_resolution.get("error"):
            return {
                "command": "/swarm deliver",
                "reply_text": str(deliver_resolution["error"]),
            }
        return _run_swarm_action_command(
            command="/swarm deliver",
            runner=lambda: swarm_deliver_upgrade(
                config_manager,
                state_db,
                upgrade_id=str(deliver_resolution["upgrade_id"]),
            ),
            renderer=_render_swarm_delivery_reply,
        )
    sync_delivery_args = _parse_swarm_sync_delivery_command(normalized)
    if sync_delivery_args:
        return _run_swarm_action_command(
            command="/swarm sync-delivery",
            runner=lambda: swarm_sync_upgrade_delivery_status(
                config_manager,
                state_db,
                upgrade_id=sync_delivery_args["upgrade_id"],
                pr_url=sync_delivery_args.get("pr_url"),
            ),
            renderer=_render_swarm_delivery_sync_reply,
        )
    sync_delivery_resolution = _resolve_natural_swarm_sync_delivery_target(
        inbound_text=normalized,
        config_manager=config_manager,
        state_db=state_db,
    )
    if sync_delivery_resolution is not None:
        if sync_delivery_resolution.get("error"):
            return {
                "command": "/swarm sync-delivery",
                "reply_text": str(sync_delivery_resolution["error"]),
            }
        return _run_swarm_action_command(
            command="/swarm sync-delivery",
            runner=lambda: swarm_sync_upgrade_delivery_status(
                config_manager,
                state_db,
                upgrade_id=str(sync_delivery_resolution["upgrade_id"]),
            ),
            renderer=_render_swarm_delivery_sync_reply,
        )
    if lowered.startswith("/swarm evaluate") or (natural_swarm_command and natural_swarm_command[0] == "/swarm evaluate"):
        task = normalized[len("/swarm evaluate") :].strip() if lowered.startswith("/swarm evaluate") else str(natural_swarm_command[1] or "").strip()
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


def _parse_style_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    if lowered in {"/style", "style"}:
        return {"command": "/style", "payload": None}
    if lowered in {"/style status", "style status"}:
        return {"command": "/style status", "payload": None}
    if lowered in {"/style test", "style test"}:
        return {"command": "/style test", "payload": None}
    feedback_prefixes = ("/style feedback ", "style feedback ")
    for prefix in feedback_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style feedback", "payload": payload}
    for command in ("/style good", "/style bad"):
        prefix = f"{command} "
        if lowered == command:
            return {"command": command, "payload": None}
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": command, "payload": payload}
    train_prefixes = ("/style train ", "style train ")
    for prefix in train_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style train", "payload": payload}
    return None


def _handle_style_command(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str | None,
    agent_id: str | None,
    command: str,
    payload: str | None,
) -> dict[str, str]:
    if command == "/style":
        return {
            "command": "/style",
            "reply_text": (
                "Style controls: `/style status` to inspect the current Telegram persona, "
                "`/style train <instruction>` to save a new style rule, and `/style feedback <note>` after a live test reply.\n"
                "Next: after training, ask a normal question in this DM to test the visible voice."
            ),
        }
    profile, agent_name = _load_telegram_persona_surface_state(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
    )
    if command == "/style status":
        return {
            "command": "/style status",
            "reply_text": _render_style_status_reply(profile=profile, agent_name=agent_name),
        }
    if command == "/style test":
        return {
            "command": "/style test",
            "reply_text": _render_style_test_reply(profile=profile, agent_name=agent_name),
        }
    if command == "/style train":
        instruction = str(payload or "").strip()
        if not instruction:
            return {
                "command": "/style train",
                "reply_text": (
                    "Style training needs an instruction.\n"
                    "Next: send `/style train be more direct and keep replies short`."
                ),
            }
        if not human_id or not agent_id:
            return {
                "command": "/style train",
                "reply_text": (
                    "Style training is unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        training_message = instruction
        lowered_instruction = instruction.lower()
        if not any(
            marker in lowered_instruction
            for marker in ("your personality", "your style", "agent persona", "your name is", "call you", "rename yourself")
        ):
            training_message = f"Your style should be {instruction}"
        mutation = detect_and_persist_agent_persona_preferences(
            agent_id=agent_id,
            human_id=human_id,
            user_message=training_message,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-train",
        )
        if mutation is None:
            return {
                "command": "/style train",
                "reply_text": (
                    "I didn't extract a durable style change from that instruction.\n"
                    "Next: be explicit, for example `/style train be more direct, keep replies short, and avoid filler`."
                ),
            }
        refreshed_profile, refreshed_agent_name = _load_telegram_persona_surface_state(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        )
        return {
            "command": "/style train",
            "reply_text": _render_style_training_reply(
                profile=refreshed_profile,
                agent_name=refreshed_agent_name,
                mutation=mutation,
                mode="training",
                applied_instruction=training_message,
            ),
        }
    if command in {"/style feedback", "/style good", "/style bad"}:
        feedback = str(payload or "").strip()
        if command in {"/style good", "/style bad"} and not feedback:
            tone = "good" if command.endswith("good") else "bad"
            return {
                "command": command,
                "reply_text": (
                    f"Style {tone} feedback needs a short note.\n"
                    f"Next: send `{command} too verbose` or `{command} too formal`."
                ),
            }
        if command == "/style feedback" and not feedback:
            return {
                "command": "/style feedback",
                "reply_text": (
                    "Style feedback needs a short note.\n"
                    "Next: send `/style feedback too verbose`, `/style feedback too formal`, or `/style feedback more direct`."
                ),
            }
        if not human_id or not agent_id:
            return {
                "command": command,
                "reply_text": (
                    "Style feedback is unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        training_message = _build_style_feedback_training_message(
            feedback=feedback,
            sentiment="positive" if command == "/style good" else ("negative" if command == "/style bad" else "neutral"),
        )
        mutation = detect_and_persist_agent_persona_preferences(
            agent_id=agent_id,
            human_id=human_id,
            user_message=training_message,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-feedback",
        )
        if mutation is None:
            return {
                "command": command,
                "reply_text": (
                    "I didn't extract a durable style change from that feedback.\n"
                    "Next: be explicit, for example `/style feedback too verbose` or `/style feedback more direct`."
                ),
            }
        refreshed_profile, refreshed_agent_name = _load_telegram_persona_surface_state(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        )
        return {
            "command": command,
            "reply_text": _render_style_training_reply(
                profile=refreshed_profile,
                agent_name=refreshed_agent_name,
                mutation=mutation,
                mode="feedback",
                applied_instruction=training_message,
            ),
        }
    return {"command": command, "reply_text": "Style command is unavailable right now."}


def _build_style_feedback_training_message(*, feedback: str, sentiment: str) -> str:
    normalized = " ".join(str(feedback or "").strip().split())
    lowered = normalized.lower()
    if not normalized:
        return ""
    if sentiment == "positive":
        if "direct" in lowered or "concise" in lowered or "short" in lowered:
            return "Your style should stay direct and keep replies short."
        if "warm" in lowered or "friendly" in lowered or "human" in lowered:
            return "Your style should stay warm and friendly."
        if "calm" in lowered:
            return "Your style should stay calm and steady."
    if "too verbose" in lowered or "too long" in lowered or "rambling" in lowered:
        return "Your style should be less verbose and keep replies short."
    if "too formal" in lowered or "stiff" in lowered:
        return "Your style should be less formal and more human."
    if "too soft" in lowered or "hedging" in lowered or "wishy" in lowered:
        return "Your style should be more assertive and stop hedging."
    if "too harsh" in lowered or "too blunt" in lowered or "too cold" in lowered:
        return "Your style should be gentler and warmer."
    if "too playful" in lowered:
        return "Your style should be more serious."
    if "too dry" in lowered or "robotic" in lowered:
        return "Your style should be warmer and more human."
    if "too slow" in lowered:
        return "Your style should speed it up and get to the point."
    if "too rushed" in lowered:
        return "Your style should slow down and explain more."
    if sentiment == "negative":
        return f"Your style should be adjusted based on this feedback: {normalized}"
    return f"Your style should be {normalized}"


def _render_style_status_reply(*, profile: dict[str, Any] | None, agent_name: str | None) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    persona_name = str(resolved_profile.get("agent_persona_name") or "").strip()
    persona_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    behavioral_rules = [
        str(rule).strip() for rule in list(resolved_profile.get("agent_behavioral_rules") or []) if str(rule).strip()
    ]
    style_labels = resolved_profile.get("style_labels") or {}
    style_summary = ", ".join(
        f"{trait}={label}" for trait, label in style_labels.items() if isinstance(label, str) and label.strip()
    )
    lines = [f"Style status for {name}."]
    if persona_name:
        lines.append(f"Persona: {persona_name}.")
    if persona_summary:
        lines.append(f"Summary: {persona_summary}.")
    if style_summary:
        lines.append(f"Traits: {style_summary}.")
    if behavioral_rules:
        lines.append("Rules: " + " | ".join(behavioral_rules[:4]) + ".")
    lines.append("Next: `/style train <instruction>` and then ask a normal question here to test the live voice.")
    return "\n".join(lines)


def _render_style_test_reply(*, profile: dict[str, Any] | None, agent_name: str | None) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    persona_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    lines = [f"Style test kit for {name}."]
    if persona_summary:
        lines.append(f"Target voice: {persona_summary}.")
    lines.append("Try these prompts:")
    lines.append('1. `Give me the answer in two lines and skip filler.`')
    lines.append('2. `Critique this plan bluntly and tell me the next step.`')
    lines.append('3. `Search the web for BTC and cite the source.`')
    lines.append('4. `Ask me one clarifying question, then stop.`')
    lines.append("Next: after each reply, use `/style feedback <note>`, `/style good <note>`, or `/style bad <note>`.")
    return "\n".join(lines)


def _render_style_training_reply(
    *,
    profile: dict[str, Any] | None,
    agent_name: str | None,
    mutation: Any,
    mode: str,
    applied_instruction: str,
) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or getattr(mutation, "agent_name", None) or resolved_profile.get("agent_persona_name") or "the agent").strip()
    persona_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    applied_text = str(applied_instruction or "").strip().rstrip(".! ")
    behavioral_rules = [
        str(rule).strip() for rule in list(resolved_profile.get("agent_behavioral_rules") or []) if str(rule).strip()
    ]
    lead = "Saved style feedback" if mode == "feedback" else "Saved style update"
    lines = [f"{lead} for {name}."]
    if getattr(mutation, "agent_name", None):
        lines.append(f"Visible name: {getattr(mutation, 'agent_name')}.")
    if applied_text:
        lines.append(f"Applied: {applied_text}.")
    if persona_summary:
        lines.append(f"Current summary: {persona_summary}.")
    if behavioral_rules:
        lines.append("Current rules: " + " | ".join(behavioral_rules[:4]) + ".")
    lines.append("Next: ask a normal question in this DM to test the updated voice live.")
    return "\n".join(lines)


def _run_voice_runtime_command(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    command: str,
    hook: str,
    fallback_reply: str,
    human_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    execution = run_first_chip_hook_supporting(
        config_manager,
        hook=hook,
        payload=_build_voice_chip_payload(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        ),
    )
    if execution is None:
        return {"command": command, "reply_text": fallback_reply}
    result = execution.output.get("result") if isinstance(execution.output, dict) else None
    reply_text = str((result or {}).get("reply_text") or "").strip()
    if reply_text:
        return {"command": command, "reply_text": reply_text}
    if execution.ok:
        return {"command": command, "reply_text": fallback_reply}
    reason = ""
    if isinstance(execution.output, dict):
        reason = str(execution.output.get("error") or "").strip()
    if not reason:
        reason = str(execution.stderr or execution.stdout or "").strip()
    return {
        "command": command,
        "reply_text": f"{fallback_reply}\nReason: {reason}" if reason else fallback_reply,
    }


def _render_telegram_voice_status_reply() -> str:
    return (
        "Voice chip is not attached yet.\n"
        "Current state: Builder can detect Telegram voice/audio, but live transcription now belongs in `domain-chip-voice-comms` instead of the main Builder repo.\n"
        "Next: attach and activate `domain-chip-voice-comms`, then rerun `/voice`."
    )


def _render_telegram_voice_plan_reply() -> str:
    return (
        "Telegram voice plan:\n"
        "1. let Builder fetch Telegram voice/audio payloads and hand them to `domain-chip-voice-comms`.\n"
        "2. transcribe them back into the same Builder Telegram runtime used for text and persona styling.\n"
        "3. optionally add voice reply synthesis as a later chip hook without growing Builder.\n"
        "Next: attach the chip, validate `/voice`, then dogfood real Telegram voice notes."
    )


def _render_telegram_voice_transcription_unavailable_reply(*, reason: str) -> str:
    cleaned_reason = " ".join(str(reason or "").strip().split())
    lines = [
        "Voice transcription is unavailable right now.",
        f"Reason: {cleaned_reason or 'the runtime could not transcribe this Telegram audio message.'}",
        "Next: send the instruction as text for now, or finish the `domain-chip-voice-comms` setup.",
    ]
    return "\n".join(lines)


def _render_telegram_voice_not_ready_reply(*, message_kind: str) -> str:
    noun = "voice note" if message_kind == "voice" else "audio message"
    return (
        f"I received the {noun}, but Telegram voice is not wired yet.\n"
        "Reason: this runtime does not transcribe Telegram audio into the Builder chat loop yet.\n"
        "Next: send the instruction as text for now, or finish the Telegram voice pipeline."
    )


def _run_swarm_read_command(
    *,
    command: str,
    loader: Any,
    renderer: Any,
    unavailable_message: str | None = None,
) -> dict[str, str]:
    try:
        payload = loader()
        reply_text = renderer(payload)
    except RuntimeError as exc:
        if unavailable_message and "HTTP 404" in str(exc):
            reply_text = unavailable_message
        else:
            reply_text = _render_swarm_unavailable_reply("Swarm read is unavailable.", exc)
    return {
        "command": command,
        "reply_text": reply_text,
    }


def _run_swarm_action_command(
    *,
    command: str,
    runner: Any,
    renderer: Any,
) -> dict[str, str]:
    try:
        payload = runner()
        reply_text = renderer(payload)
    except RuntimeError as exc:
        reply_text = _render_swarm_unavailable_reply("Swarm action is unavailable.", exc)
    return {
        "command": command,
        "reply_text": reply_text,
    }


def _run_swarm_bridge_command(
    *,
    command: str,
    runner: Any,
    renderer: Any,
) -> dict[str, str]:
    try:
        result = runner()
        reply_text = renderer(result)
    except RuntimeError as exc:
        reply_text = _render_swarm_unavailable_reply("Swarm bridge action is unavailable.", exc)
    return {
        "command": command,
        "reply_text": reply_text,
    }


def _render_swarm_unavailable_reply(verdict: str, exc: RuntimeError) -> str:
    detail = str(exc).strip()
    lines = [verdict]
    if detail:
        lines.append(f"Reason: {_with_terminal_period(detail)}")
    next_step = _suggest_swarm_unavailable_next_step(detail)
    if next_step:
        lines.append(f"Next: {next_step}")
    return "\n".join(lines)


def _suggest_swarm_unavailable_next_step(detail: str) -> str | None:
    normalized = str(detail or "").strip().lower()
    if not normalized:
        return None
    if "api url is missing" in normalized:
        return "configure the Swarm API URL, then retry."
    if "http 404" in normalized and "not found" in normalized:
        return "verify the target id or route on the current Swarm host."
    if "http 404" in normalized:
        return "check whether the current Swarm host exposes that route."
    return None


def _render_swarm_overview_reply(payload: dict[str, Any]) -> str:
    session = payload.get("session") if isinstance(payload, dict) else {}
    agent = payload.get("agent") if isinstance(payload, dict) else {}
    attached_repos = payload.get("attachedRepos") if isinstance(payload, dict) else []
    repos = attached_repos if isinstance(attached_repos, list) else []
    verified_count = sum(1 for repo in repos if isinstance(repo, dict) and str(repo.get("verificationState") or "") == "verified")
    pending_count = sum(1 for repo in repos if isinstance(repo, dict) and str(repo.get("verificationState") or "") != "verified")
    workspace = str((session or {}).get("workspaceName") or (session or {}).get("workspaceSlug") or (session or {}).get("workspaceId") or "unknown")
    agent_name = str((agent or {}).get("name") or (agent or {}).get("id") or "unknown")
    return (
        f"Swarm is attached to {workspace}.\n"
        f"Agent: {agent_name}. Repos: {len(repos)} attached, {verified_count} verified, {pending_count} pending.\n"
        "Next: `/swarm status` or `/swarm collective`."
    )


def _render_swarm_runtime_reply(payload: dict[str, Any]) -> str:
    intelligence = payload.get("intelligencePulse") if isinstance(payload, dict) else {}
    pending_upgrades = (intelligence or {}).get("pendingUpgradeCount") if isinstance(intelligence, dict) else None
    pending_contradictions = (intelligence or {}).get("pendingContradictionCount") if isinstance(intelligence, dict) else None
    state = str(payload.get("runtimeState") or "unknown")
    stage = str(payload.get("stageLabel") or payload.get("stageKey") or "unknown")
    recommendation = str(payload.get("recommendation") or "none")
    lines = [
        f"Swarm runtime is {state}.",
        f"Stage: {stage}. Recommendation: {recommendation}.",
    ]
    if payload.get("blocker"):
        lines.append(f"Blocker: {str(payload.get('blocker'))}.")
    if pending_upgrades is not None or pending_contradictions is not None:
        lines.append(
            f"Pressure: {int(pending_upgrades or 0)} pending upgrades, {int(pending_contradictions or 0)} open contradictions."
        )
    lines.append("Next: `/swarm issues` if blocked, otherwise `/swarm collective`.")
    return "\n".join(lines)


def _render_swarm_specializations_reply(payload: list[dict[str, Any]]) -> str:
    specializations = payload if isinstance(payload, list) else []
    if not specializations:
        return "Swarm specializations:\nNo specialization records are available right now."
    ranked = sorted(
        [item for item in specializations if isinstance(item, dict)],
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )[:5]
    lines = [f"Swarm specializations:\n{len(specializations)} specialization(s)."]
    for item in ranked:
        lines.append(
            f"- {str(item.get('id') or 'unknown')}: {str(item.get('label') or item.get('key') or 'specialization')} "
            f"[mode={str(item.get('evolutionMode') or 'unknown')}]"
        )
    first_id = str(ranked[0].get("id") or "specialization_id")
    lines.append(f"Next: `/swarm mode {first_id} review_required`")
    return "\n".join(lines)


def _render_swarm_insights_reply(payload: list[dict[str, Any]]) -> str:
    insights = payload if isinstance(payload, list) else []
    actionable_statuses = {"captured", "distilled", "queued_for_test", "benchmark_supported", "live_supported"}
    actionable = [
        item for item in insights if isinstance(item, dict) and str(item.get("status") or "") in actionable_statuses
    ]
    if not actionable:
        return "Swarm insights:\nNo absorbable insights are waiting right now."
    ranked = sorted(actionable, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)[:5]
    lines = [f"Swarm insights:\n{len(actionable)} absorbable insight(s)."]
    for item in ranked:
        lines.append(
            f"- {str(item.get('id') or 'unknown')}: {str(item.get('summary') or item.get('title') or 'insight')} "
            f"[status={str(item.get('status') or 'unknown')}]"
        )
    first_id = str(ranked[0].get("id") or "insight_id")
    lines.append(f"Next: `/swarm absorb {first_id} because <reason>`")
    return "\n".join(lines)


def _render_swarm_scoped_insights_reply(payload: dict[str, Any]) -> str:
    label = str(payload.get("specialization_label") or payload.get("specialization_key") or "lane")
    insights = payload.get("insights") if isinstance(payload, dict) else []
    actionable = insights if isinstance(insights, list) else []
    if not actionable:
        return f"Swarm insights for {label}:\nNo absorbable insights are waiting right now."
    ranked = sorted(
        [item for item in actionable if isinstance(item, dict)],
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )[:5]
    lines = [f"Swarm insights for {label}:\n{len(actionable)} absorbable insight(s)."]
    for item in ranked:
        lines.append(
            f"- {str(item.get('id') or 'unknown')}: {str(item.get('summary') or item.get('title') or 'insight')} "
            f"[status={str(item.get('status') or 'unknown')}]"
        )
    first_id = str(ranked[0].get("id") or "insight_id")
    lines.append(f"Next: `/swarm absorb {first_id} because <reason>` or `absorb the latest {label} insight`")
    return "\n".join(lines)


def _render_swarm_masteries_reply(payload: list[dict[str, Any]]) -> str:
    masteries = payload if isinstance(payload, list) else []
    if not masteries:
        return "Swarm masteries:\nNo mastery records are available right now."
    ranked = sorted(
        [item for item in masteries if isinstance(item, dict)],
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )[:5]
    lines = [f"Swarm masteries:\n{len(masteries)} mastery record(s)."]
    for item in ranked:
        lines.append(
            f"- {str(item.get('id') or 'unknown')}: {str(item.get('summary') or item.get('title') or 'mastery')} "
            f"[status={str(item.get('status') or 'unknown')}]"
        )
    first_id = str(ranked[0].get("id") or "mastery_id")
    lines.append(f"Next: `/swarm review {first_id} approve because <reason>`")
    return "\n".join(lines)


def _render_swarm_scoped_masteries_reply(payload: dict[str, Any]) -> str:
    label = str(payload.get("specialization_label") or payload.get("specialization_key") or "lane")
    masteries = payload.get("masteries") if isinstance(payload, dict) else []
    records = masteries if isinstance(masteries, list) else []
    if not records:
        return f"Swarm masteries for {label}:\nNo mastery records are available right now."
    ranked = sorted(
        [item for item in records if isinstance(item, dict)],
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )[:5]
    lines = [f"Swarm masteries for {label}:\n{len(records)} mastery record(s)."]
    for item in ranked:
        lines.append(
            f"- {str(item.get('id') or 'unknown')}: {str(item.get('summary') or item.get('title') or 'mastery')} "
            f"[status={str(item.get('status') or 'unknown')}]"
        )
    first_id = str(ranked[0].get("id") or "mastery_id")
    lines.append(f"Next: `/swarm review {first_id} approve because <reason>` or `approve the latest {label} mastery`")
    return "\n".join(lines)


def _render_swarm_live_reply(payload: dict[str, Any]) -> str:
    lines = [
        "Swarm live state:",
        f"Runtime: {str(payload.get('runtimeState') or 'unknown')}.",
        f"Current stage: {str(payload.get('currentStageLabel') or payload.get('currentStageKey') or 'unknown')}.",
        f"Activity: {int(payload.get('activeAgentCount') or 0)} active agents, {int(payload.get('activePathCount') or 0)} active paths, {int(payload.get('queuedEventCount') or 0)} queued events.",
    ]
    if payload.get("recommendation"):
        lines.append(f"Recommendation: {str(payload.get('recommendation'))}.")
    latest_delivery = payload.get("latestUpgradeDelivery")
    if isinstance(latest_delivery, dict) and latest_delivery.get("status"):
        lines.append(
            f"Latest delivery: {str(latest_delivery.get('status'))} for {str(latest_delivery.get('changeSummary') or latest_delivery.get('upgradeId') or 'latest upgrade')}."
        )
    return "\n".join(lines)


def _render_swarm_upgrades_reply(payload: list[dict[str, Any]]) -> str:
    upgrades = payload if isinstance(payload, list) else []
    pending_statuses = {"draft", "queued", "upgrade_opened", "awaiting_review"}
    pending = [item for item in upgrades if isinstance(item, dict) and str(item.get("status") or "") in pending_statuses]
    if not pending:
        return "Swarm upgrades:\nNo pending upgrades are waiting right now."
    recent = sorted(pending, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)[:3]
    lines = [f"Swarm upgrades:\n{len(pending)} pending upgrade(s)."]
    for item in recent:
        lines.append(
            f"- {str(item.get('status') or 'unknown')}: {str(item.get('changeSummary') or item.get('id') or 'upgrade')} ({str(item.get('riskLevel') or 'unknown')} risk)"
        )
    first_id = str(recent[0].get("id") or "upgrade_id")
    lines.append(f"Next: `/swarm deliver {first_id}` or `/swarm sync-delivery {first_id}`")
    return "\n".join(lines)


def _render_swarm_scoped_upgrades_reply(payload: dict[str, Any]) -> str:
    label = str(payload.get("specialization_label") or payload.get("specialization_key") or "lane")
    upgrades = payload.get("upgrades") if isinstance(payload, dict) else []
    pending = upgrades if isinstance(upgrades, list) else []
    if not pending:
        return f"Swarm upgrades for {label}:\nNo pending upgrades are waiting right now."
    recent = sorted(
        [item for item in pending if isinstance(item, dict)],
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )[:5]
    lines = [f"Swarm upgrades for {label}:\n{len(pending)} pending upgrade(s)."]
    for item in recent:
        lines.append(
            f"- {str(item.get('status') or 'unknown')}: {str(item.get('changeSummary') or item.get('id') or 'upgrade')} "
            f"({str(item.get('riskLevel') or 'unknown')} risk)"
        )
    first_id = str(recent[0].get("id") or "upgrade_id")
    lines.append(
        f"Next: `/swarm deliver {first_id}` or `/swarm sync-delivery {first_id}` or `deliver the latest {label} upgrade`"
    )
    return "\n".join(lines)


def _render_swarm_operator_issues_reply(payload: list[dict[str, Any]]) -> str:
    issues = payload if isinstance(payload, list) else []
    open_issues = [item for item in issues if isinstance(item, dict) and str(item.get("status") or "") != "resolved"]
    if not open_issues:
        return "Swarm operator issues:\nNo open operator issues right now."
    ranked = sorted(
        open_issues,
        key=lambda item: (str(item.get("severity") or "") != "critical", str(item.get("updatedAt") or item.get("createdAt") or "")),
        reverse=False,
    )[:3]
    lines = [f"Swarm operator issues:\n{len(open_issues)} open issue(s)."]
    for item in ranked:
        lines.append(f"- {str(item.get('severity') or 'warn')}: {str(item.get('summary') or item.get('kind') or 'issue')}")
    return "\n".join(lines)


def _render_swarm_inbox_reply(payload: dict[str, Any]) -> str:
    items = payload.get("items") if isinstance(payload, dict) else []
    inbox_items = items if isinstance(items, list) else []
    if not inbox_items:
        return "Swarm inbox:\nNo evolution inbox items are waiting right now."
    ranked = sorted(inbox_items, key=lambda item: str((item if isinstance(item, dict) else {}).get("createdAt") or ""), reverse=True)[:3]
    lines = [f"Swarm inbox:\n{len(inbox_items)} item(s) waiting."]
    for item in ranked:
        if isinstance(item, dict):
            lines.append(
                f"- {str(item.get('priority') or 'medium')}: {str(item.get('title') or item.get('summary') or item.get('id') or 'inbox item')}"
            )
    return "\n".join(lines)


def _render_swarm_collective_reply(payload: dict[str, Any]) -> str:
    specializations = payload.get("specializations") if isinstance(payload, dict) else []
    paths = payload.get("evolutionPaths") if isinstance(payload, dict) else []
    insights = payload.get("insights") if isinstance(payload, dict) else []
    masteries = payload.get("masteries") if isinstance(payload, dict) else []
    contradictions = payload.get("contradictions") if isinstance(payload, dict) else []
    upgrades = payload.get("upgrades") if isinstance(payload, dict) else []
    inbox = payload.get("inbox") if isinstance(payload, dict) else {}
    inbox_items = (inbox or {}).get("items") if isinstance(inbox, dict) else []
    open_contradictions = [
        item for item in (contradictions if isinstance(contradictions, list) else [])
        if isinstance(item, dict) and str(item.get("status") or "open") != "resolved"
    ]
    pending_statuses = {"draft", "queued", "upgrade_opened", "awaiting_review"}
    pending_upgrades = [
        item for item in (upgrades if isinstance(upgrades, list) else [])
        if isinstance(item, dict) and str(item.get("status") or "") in pending_statuses
    ]
    return (
        "Swarm collective has "
        f"{len(open_contradictions)} open contradiction(s), {len(pending_upgrades)} pending upgrade(s), and "
        f"{len(inbox_items) if isinstance(inbox_items, list) else 0} inbox item(s).\n"
        f"Specializations: {len(specializations) if isinstance(specializations, list) else 0}. "
        f"Paths: {len(paths) if isinstance(paths, list) else 0}. "
        f"Insights: {len(insights) if isinstance(insights, list) else 0}. "
        f"Masteries: {len(masteries) if isinstance(masteries, list) else 0}.\n"
        "Next: `/swarm upgrades`, `/swarm issues`, or `/swarm inbox`."
    )


def _render_swarm_paths_reply(payload: dict[str, Any]) -> str:
    paths = payload.get("paths") if isinstance(payload, dict) else []
    entries = paths if isinstance(paths, list) else []
    if not entries:
        return "Swarm paths:\nNo attached specialization paths are available right now."
    lines = [f"Swarm paths:\n{len(entries)} attached path(s)."]
    active_path_key = str(payload.get("active_path_key") or "").strip()
    for item in entries[:5]:
        if not isinstance(item, dict):
            continue
        prefix = "* " if str(item.get("key") or "") == active_path_key else "- "
        lines.append(f"{prefix}{str(item.get('key') or 'unknown')}: {str(item.get('label') or 'path')}")
    first_key = str((entries[0] if entries and isinstance(entries[0], dict) else {}).get("key") or "startup-operator")
    lines.append(f"Next: `/swarm autoloop {first_key}` or `/swarm session {first_key}`")
    return "\n".join(lines)


def _render_swarm_bridge_run_reply(result: Any) -> str:
    if not getattr(result, "ok", False):
        return _render_swarm_bridge_failure("run", result)
    path_key = str(getattr(result, "path_key", "") or "unknown")
    path_label = _humanize_swarm_path_key(path_key)
    artifacts_path = str(getattr(result, "artifacts_path", "") or "").strip() or "unknown"
    payload_path = str(getattr(result, "payload_path", "") or "").strip() or "not written"
    return (
        f"{path_label} run completed.\n"
        f"Artifacts: {artifacts_path}. Collective payload: {payload_path}.\n"
        f"Next: `/swarm autoloop {path_key}` or `/swarm session {path_key}`."
    )


def _render_swarm_bridge_autoloop_reply(result: Any) -> str:
    if not getattr(result, "ok", False):
        paused_reply = _render_swarm_bridge_autoloop_pause_reply(result)
        if paused_reply is not None:
            return paused_reply
        return _render_swarm_bridge_failure("autoloop", result)
    session_summary = getattr(result, "session_summary", None) if isinstance(getattr(result, "session_summary", None), dict) else None
    latest_round_summary = getattr(result, "latest_round_summary", None) if isinstance(getattr(result, "latest_round_summary", None), dict) else None
    latest_round_summary_path = str(getattr(result, "latest_round_summary_path", "") or "").strip() or None
    round_history = getattr(result, "round_history", None) if isinstance(getattr(result, "round_history", None), dict) else None
    path_key = str(getattr(result, "path_key", "") or "unknown")
    path_label = _humanize_swarm_path_key(path_key)
    session_id = str(getattr(result, "session_id", "") or (session_summary or {}).get("sessionId") or "unknown")
    completed_rounds = int((session_summary or {}).get("completedRounds") or 0)
    requested_rounds = int((session_summary or {}).get("requestedRoundsTotal") or 0)
    kept_rounds = int((session_summary or {}).get("keptRounds") or 0)
    reverted_rounds = int((session_summary or {}).get("revertedRounds") or 0)
    lines = [f"{path_label} autoloop finished."]
    if session_summary:
        stop_reason = _describe_swarm_autoloop_stop_reason(str(session_summary.get("stopReason") or "unknown"))
        lines.append(
            f"It completed {completed_rounds} of {requested_rounds} requested rounds and kept {kept_rounds} candidate(s) while reverting {reverted_rounds}."
        )
        lines.append(f"Session: {session_id}. Stop: {stop_reason}.")
        lines.append(
            f"Path: {path_key}. Decisions: {kept_rounds} kept, {reverted_rounds} reverted."
        )
        if session_summary.get("plannerStatus"):
            lines.append(f"Planner: {str(session_summary.get('plannerStatus'))}.")
        if session_summary.get("latestPlannerKind"):
            lines.append(f"Latest planner kind: {str(session_summary.get('latestPlannerKind'))}.")
    else:
        lines.append(f"Session: {session_id}.")
        lines.append(f"Path: {path_key}.")
    if round_history:
        current_score = round_history.get("currentScore")
        best_score = round_history.get("bestScore")
        no_gain_streak = int(round_history.get("noGainStreak") or 0)
        score_text = f"{float(current_score):.4f}" if isinstance(current_score, (int, float)) else "unknown"
        best_text = f"{float(best_score):.4f}" if isinstance(best_score, (int, float)) else "unknown"
        lines.append(f"Scores: current {score_text}, best {best_text}, no-gain streak {no_gain_streak}.")
    lines.extend(_render_swarm_latest_round_detail_lines(latest_round_summary, latest_round_summary_path))
    lines.append(f"Next: inspect this session with `/swarm session {path_key} {session_id}`.")
    return "\n".join(lines)


def _render_swarm_bridge_autoloop_pause_reply(result: Any) -> str | None:
    stdout = str(getattr(result, "stdout", "") or "")
    if "paused_no_gain_streak" not in stdout and "Autoloop paused:" not in stdout:
        return None
    session_summary = getattr(result, "session_summary", None) if isinstance(getattr(result, "session_summary", None), dict) else None
    round_history = getattr(result, "round_history", None) if isinstance(getattr(result, "round_history", None), dict) else None
    path_key = str(getattr(result, "path_key", "") or "unknown")
    session_id = str(getattr(result, "session_id", "") or (session_summary or {}).get("sessionId") or "unknown")
    path_label = _humanize_swarm_path_key(path_key)
    completed_rounds = int((session_summary or {}).get("completedRounds") or 0)
    requested_rounds = int((session_summary or {}).get("requestedRoundsTotal") or 0)
    kept_rounds = int((session_summary or {}).get("keptRounds") or 0)
    reverted_rounds = int((session_summary or {}).get("revertedRounds") or 0)
    planner_readiness = str((session_summary or {}).get("plannerReadinessStatus") or "unknown")
    current_score = round_history.get("currentScore") if round_history else None
    best_score = round_history.get("bestScore") if round_history else None
    no_gain_streak = int(round_history.get("noGainStreak") or (session_summary or {}).get("noGainStreak") or 0)
    score_text = f"{float(current_score):.4f}" if isinstance(current_score, (int, float)) else "unknown"
    best_text = f"{float(best_score):.4f}" if isinstance(best_score, (int, float)) else "unknown"
    return "\n".join(
        [
            f"{path_label} autoloop is paused.",
            "It hit the no-gain guard before another round, so nothing changed yet.",
            f"Session: {session_id}. Path: {path_key}.",
            f"Rounds: {completed_rounds} of {requested_rounds}. Decisions: {kept_rounds} kept, {reverted_rounds} reverted.",
            f"Planner readiness: {planner_readiness}.",
            f"Scores: current {score_text}, best {best_text}, no-gain streak {no_gain_streak}.",
            f"Next: continue with `/swarm continue {path_key} session {session_id} rounds 1 force`.",
        ]
    )


def _render_swarm_sessions_reply(payload: dict[str, Any]) -> str:
    sessions = payload.get("sessions") if isinstance(payload, dict) else []
    entries = sessions if isinstance(sessions, list) else []
    path_key = str(payload.get("path_key") or "path")
    path_label = _humanize_swarm_path_key(path_key)
    if not entries:
        return (
            f"{path_label} has no saved autoloop sessions yet.\n"
            f"Next: start one with `/swarm autoloop {path_key}`."
        )
    lines = [f"{path_label} has {len(entries)} recent autoloop session(s)."]
    for item in entries[:3]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {str(item.get('session_id') or 'unknown')}: {int(item.get('completed_rounds') or 0)}/{int(item.get('requested_rounds_total') or 0)} rounds, "
            f"stop={_describe_swarm_autoloop_stop_reason(str(item.get('stop_reason') or 'active'))}, "
            f"planner={str(item.get('planner_status') or 'pending')}"
        )
    first_id = str((entries[0] if entries and isinstance(entries[0], dict) else {}).get("session_id") or "session_id")
    lines.append(f"Next: inspect the latest one with `/swarm session {path_key} {first_id}`.")
    return "\n".join(lines)


def _render_swarm_session_reply(payload: dict[str, Any]) -> str:
    session_summary = payload.get("session_summary") if isinstance(payload, dict) else None
    latest_round_summary = payload.get("latest_round_summary") if isinstance(payload, dict) else None
    latest_round_summary_path = str(payload.get("latest_round_summary_path") or "").strip() or None
    round_history = payload.get("round_history") if isinstance(payload, dict) else None
    path_key = str(payload.get("path_key") or "path")
    path_label = _humanize_swarm_path_key(path_key)
    if not isinstance(session_summary, dict):
        return (
            f"No saved {path_label} session summary exists yet.\n"
            f"Next: start one with `/swarm autoloop {path_key}`."
        )
    latest_round = None
    rounds = session_summary.get("rounds")
    if isinstance(rounds, list) and rounds:
        latest_round = rounds[-1] if isinstance(rounds[-1], dict) else None
    session_id = str(session_summary.get("sessionId") or "unknown")
    completed_rounds = int(session_summary.get("completedRounds") or 0)
    requested_rounds = int(session_summary.get("requestedRoundsTotal") or 0)
    kept_rounds = int(session_summary.get("keptRounds") or 0)
    reverted_rounds = int(session_summary.get("revertedRounds") or 0)
    stop_reason = _describe_swarm_autoloop_stop_reason(str(session_summary.get("stopReason") or "active"))
    lines = [
        f"Latest {path_label} autoloop session is {stop_reason}.",
        f"It has completed {completed_rounds} of {requested_rounds} requested rounds, with {kept_rounds} kept and {reverted_rounds} reverted.",
        f"Session: {session_id}. Path: {path_key}.",
    ]
    if session_summary.get("plannerStatus"):
        lines.append(f"Planner: {str(session_summary.get('plannerStatus'))}.")
    if session_summary.get("plannerReadinessStatus"):
        lines.append(f"Planner readiness: {str(session_summary.get('plannerReadinessStatus'))}.")
    if round_history and isinstance(round_history, dict):
        current_score = round_history.get("currentScore")
        best_score = round_history.get("bestScore")
        no_gain_streak = int(round_history.get("noGainStreak") or session_summary.get("noGainStreak") or 0)
        score_text = f"{float(current_score):.4f}" if isinstance(current_score, (int, float)) else "unknown"
        best_text = f"{float(best_score):.4f}" if isinstance(best_score, (int, float)) else "unknown"
        lines.append(f"Scores: current {score_text}, best {best_text}, no-gain streak {no_gain_streak}.")
    elif session_summary.get("noGainStreak") is not None:
        lines.append(f"Scores: current unknown, best unknown, no-gain streak {int(session_summary.get('noGainStreak') or 0)}.")
    if latest_round:
        lines.append(
            f"Latest round: #{int(latest_round.get('ordinal') or 0)} {str(latest_round.get('decision') or 'unknown')} "
            f"target={str(latest_round.get('targetPath') or 'unknown')}."
        )
        if latest_round.get("plannerKind"):
            lines.append(f"Latest planner kind: {str(latest_round.get('plannerKind'))}.")
        if latest_round.get("candidateSummary"):
            lines.append(f"Latest candidate: {_with_terminal_period(str(latest_round.get('candidateSummary')))}")
    lines.extend(_render_swarm_latest_round_detail_lines(latest_round_summary, latest_round_summary_path))
    next_command = f"/swarm continue {path_key} session {str(session_summary.get('sessionId') or 'unknown')} rounds 1"
    if str(session_summary.get("stopReason") or "") == "paused_no_gain_streak":
        lines.append("The autoloop is paused on the no-gain guard.")
        next_command = f"{next_command} force"
    lines.append(f"Next: `{next_command}`")
    return "\n".join(lines)


def _humanize_swarm_path_key(path_key: str) -> str:
    normalized = str(path_key or "").strip()
    if not normalized:
        return "Swarm"
    return normalized.replace("-", " ").replace("_", " ").title()


def _render_swarm_latest_round_detail_lines(
    latest_round_summary: dict[str, Any] | None,
    latest_round_summary_path: str | None,
) -> list[str]:
    if not isinstance(latest_round_summary, dict):
        return []
    planner = latest_round_summary.get("planner") if isinstance(latest_round_summary.get("planner"), dict) else {}
    mutation_target = (
        latest_round_summary.get("mutationTarget")
        if isinstance(latest_round_summary.get("mutationTarget"), dict)
        else {}
    )
    benchmark_runner_type = str(latest_round_summary.get("benchmarkRunnerType") or "").strip()
    benchmark_runner_label = str(latest_round_summary.get("benchmarkRunnerLabel") or "").strip()
    target_path = str(mutation_target.get("path") or "").strip()
    candidate_summary = str(planner.get("candidateSummary") or "").strip()
    hypothesis = str(planner.get("hypothesis") or "").strip()
    target_rationale = str(mutation_target.get("rationale") or "").strip()
    baseline_score = latest_round_summary.get("baselineScore")
    candidate_score = latest_round_summary.get("candidateScore")
    decision = str(latest_round_summary.get("decision") or "").strip() or "unknown"
    lines: list[str] = []
    if benchmark_runner_type or benchmark_runner_label:
        lines.append(
            f"Benchmark runner: {_describe_swarm_benchmark_runner(benchmark_runner_type, benchmark_runner_label)}."
        )
    if target_path:
        lines.append(f"Mutation target: {target_path}.")
    if candidate_summary:
        lines.append(f"Round candidate: {_with_terminal_period(candidate_summary)}")
    if hypothesis:
        lines.append(f"Hypothesis: {_with_terminal_period(hypothesis)}")
    if target_rationale:
        lines.append(f"Target rationale: {_with_terminal_period(target_rationale)}")
    if isinstance(baseline_score, (int, float)) and isinstance(candidate_score, (int, float)):
        delta = float(candidate_score) - float(baseline_score)
        lines.append(
            f"Round delta: {delta:+.4f} ({float(baseline_score):.4f} -> {float(candidate_score):.4f})."
        )
        if decision == "reverted":
            lines.append("Interpretation: this mutation did not beat the current benchmarked baseline, so the repo stayed unchanged.")
        elif decision == "kept":
            lines.append("Interpretation: this mutation beat the baseline and was kept in the repo.")
    if latest_round_summary_path:
        lines.append(f"Round artifact: {latest_round_summary_path}.")
    return lines


def _describe_swarm_autoloop_stop_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    if not normalized:
        return "unknown"
    mapping = {
        "completed_requested_rounds": "requested rounds completed",
        "paused_no_gain_streak": "paused on the no-gain guard",
        "active": "active",
    }
    return mapping.get(normalized, normalized.replace("_", " "))


def _describe_swarm_benchmark_runner(runner_type: str, runner_label: str) -> str:
    normalized_type = str(runner_type or "").strip()
    normalized_label = str(runner_label or "").strip()
    if normalized_type and normalized_label:
        return f"{normalized_type} via {normalized_label}"
    if normalized_label:
        return normalized_label
    return normalized_type or "unknown"


def _with_terminal_period(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    return value if value.endswith((".", "!", "?")) else f"{value}."


def _render_swarm_bridge_rerun_reply(result: Any) -> str:
    if not getattr(result, "ok", False):
        return _render_swarm_bridge_failure("rerun request", result)
    path_key = str(getattr(result, "path_key", "") or "latest open path")
    artifacts_path = str(getattr(result, "artifacts_path", "") or "").strip() or "unknown"
    payload_path = str(getattr(result, "payload_path", "") or "").strip() or "not written"
    return (
        "Swarm rerun request executed.\n"
        f"Path: {path_key}.\n"
        f"Artifacts: {artifacts_path}.\n"
        f"Collective payload: {payload_path}."
    )


def _render_swarm_bridge_failure(action: str, result: Any) -> str:
    stdout = str(getattr(result, "stdout", "") or "").strip()
    stderr = str(getattr(result, "stderr", "") or "").strip()
    detail = stderr or stdout or "Command failed without stdout or stderr."
    lines = [
        f"Swarm {action} failed.",
        f"Exit code: {int(getattr(result, 'exit_code', 1) or 1)}.",
        detail[:800],
    ]
    return "\n".join(lines)


def _render_swarm_absorb_reply(payload: dict[str, Any]) -> str:
    insight = payload.get("insight") if isinstance(payload, dict) else {}
    mastery = payload.get("mastery") if isinstance(payload, dict) else {}
    review = payload.get("review") if isinstance(payload, dict) else {}
    return (
        "Swarm insight absorbed.\n"
        f"Insight: {str((insight or {}).get('summary') or (insight or {}).get('title') or (insight or {}).get('id') or 'unknown')}.\n"
        f"Mastery: {str((mastery or {}).get('id') or 'unknown')} ({str((mastery or {}).get('status') or 'unknown')}).\n"
        f"Review: {str((review or {}).get('decision') or 'unknown')}."
    )


def _render_swarm_review_reply(payload: dict[str, Any]) -> str:
    mastery = payload.get("mastery") if isinstance(payload, dict) else {}
    review = payload.get("review") if isinstance(payload, dict) else {}
    return (
        "Swarm mastery review recorded.\n"
        f"Mastery: {str((mastery or {}).get('id') or 'unknown')}.\n"
        f"Decision: {str((review or {}).get('decision') or 'unknown')}.\n"
        f"Status: {str((mastery or {}).get('status') or 'unknown')}."
    )


def _render_swarm_mode_reply(payload: dict[str, Any]) -> str:
    return (
        "Swarm evolution mode updated.\n"
        f"Specialization: {str(payload.get('label') or payload.get('key') or payload.get('id') or 'unknown')}.\n"
        f"Evolution mode: {str(payload.get('evolutionMode') or 'unknown')}."
    )


def _render_swarm_delivery_reply(payload: dict[str, Any]) -> str:
    upgrade = payload.get("upgrade") if isinstance(payload, dict) else {}
    delivery = payload.get("delivery") if isinstance(payload, dict) else {}
    return (
        "Swarm upgrade delivery recorded.\n"
        f"Upgrade: {str((upgrade or {}).get('changeSummary') or (upgrade or {}).get('id') or 'unknown')}.\n"
        f"Upgrade status: {str((upgrade or {}).get('status') or 'unknown')}.\n"
        f"Delivery status: {str((delivery or {}).get('status') or 'unknown')}."
    )


def _render_swarm_delivery_sync_reply(payload: dict[str, Any]) -> str:
    upgrade = payload.get("upgrade") if isinstance(payload, dict) else {}
    delivery = payload.get("delivery") if isinstance(payload, dict) else {}
    return (
        "Swarm delivery status synced.\n"
        f"Upgrade: {str((upgrade or {}).get('changeSummary') or (upgrade or {}).get('id') or 'unknown')}.\n"
        f"Upgrade status: {str((upgrade or {}).get('status') or 'unknown')}.\n"
        f"Delivery status: {str((delivery or {}).get('status') or 'unknown')}."
    )


def _match_natural_swarm_command(inbound_text: str) -> tuple[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    simplified = " ".join(re.sub(r"[^a-z0-9\s/]", " ", lowered).split())

    if simplified in {
        "swarm",
        "swarm help",
        "help with swarm",
        "show swarm commands",
        "show me swarm commands",
        "what are the swarm commands",
        "what can swarm do",
    }:
        return ("/swarm", None)

    if simplified in {
        "swarm status",
        "show swarm status",
        "show me swarm status",
        "check swarm status",
        "what is swarm status",
        "what s swarm status",
        "what is the swarm status",
        "what s the swarm status",
        "is swarm ready",
        "is spark swarm ready",
        "is swarm connected",
        "is spark swarm connected",
        "check if swarm is ready",
        "check if spark swarm is ready",
    }:
        return ("/swarm status", None)
    if re.match(
        r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|check|tell me)\s+(?:the\s+)?(?:spark\s+)?swarm\s+status$",
        simplified,
        flags=re.IGNORECASE,
    ):
        return ("/swarm status", None)
    if simplified in {
        "swarm overview",
        "show swarm overview",
        "show me swarm overview",
        "show swarm summary",
        "show me swarm summary",
        "summarize swarm",
        "summarize the swarm",
    }:
        return ("/swarm overview", None)
    if simplified in {
        "swarm live",
        "show swarm live",
        "show me swarm live",
        "show swarm live state",
        "show me swarm live state",
        "what is the live state in swarm",
        "what is the swarm live state",
    }:
        return ("/swarm live", None)
    if simplified in {
        "swarm runtime",
        "show swarm runtime",
        "show me swarm runtime",
        "show swarm runtime pulse",
        "show me the swarm runtime pulse",
        "what is the swarm runtime state",
        "what is the swarm runtime pulse",
    }:
        return ("/swarm runtime", None)
    if simplified in {
        "swarm specializations",
        "show swarm specializations",
        "show me swarm specializations",
        "list swarm specializations",
        "what specializations are in swarm",
    }:
        return ("/swarm specializations", None)
    if simplified in {
        "swarm insights",
        "show swarm insights",
        "show me swarm insights",
        "list swarm insights",
        "show me absorbable insights in swarm",
        "what insights can i absorb in swarm",
    }:
        return ("/swarm insights", None)
    if simplified in {
        "swarm masteries",
        "show swarm masteries",
        "show me swarm masteries",
        "list swarm masteries",
        "what masteries are in swarm",
    }:
        return ("/swarm masteries", None)
    if simplified in {
        "swarm upgrades",
        "show swarm upgrades",
        "show me swarm upgrades",
        "show pending upgrades in swarm",
        "what upgrades are pending in swarm",
        "what pending upgrades are in swarm",
    }:
        return ("/swarm upgrades", None)
    if simplified in {
        "swarm issues",
        "show swarm issues",
        "show me swarm issues",
        "show operator issues in swarm",
        "show me operator issues in swarm",
        "what operator issues are open in swarm",
    }:
        return ("/swarm issues", None)
    if simplified in {
        "swarm inbox",
        "show swarm inbox",
        "show me swarm inbox",
        "show evolution inbox in swarm",
        "what is in the swarm inbox",
    }:
        return ("/swarm inbox", None)
    if simplified in {
        "swarm collective",
        "show swarm collective",
        "show me swarm collective",
        "summarize the collective in swarm",
        "show me the collective summary in swarm",
        "summarize the swarm collective",
    }:
        return ("/swarm collective", None)
    if simplified in {
        "swarm paths",
        "show swarm paths",
        "show me swarm paths",
        "list swarm paths",
        "what swarm paths are attached",
        "what specialization paths are attached in swarm",
    }:
        return ("/swarm paths", None)

    if simplified in {
        "swarm sync",
        "sync swarm",
        "sync with swarm",
        "sync the swarm",
        "please sync with swarm",
        "upload to swarm",
        "upload this to swarm",
        "push this to swarm",
        "sync this with swarm",
    }:
        return ("/swarm sync", None)
    if re.match(
        r"^(?:please\s+|can you\s+)?(?:sync|upload|push)\s+(?:(?:this|the latest payload)\s+)?(?:with|to)?\s*swarm$",
        simplified,
        flags=re.IGNORECASE,
    ):
        return ("/swarm sync", None)

    evaluate_patterns = (
        r"^(?:please\s+|can you\s+)?evaluate(?:\s+this)?\s+for\s+swarm[:\s-]*(?P<task>.+)$",
        r"^(?:please\s+|can you\s+)?check(?:\s+this)?\s+for\s+swarm[:\s-]*(?P<task>.+)$",
        r"^(?:please\s+|can you\s+)?should\s+(?:this|we)\s+(?:go\s+to|use|delegate\s+to|escalate\s+to)\s+swarm[:\s-]*(?P<task>.+)$",
        r"^(?:please\s+|can you\s+)?should\s+this\s+be\s+(?:delegated|escalated)\s+to\s+swarm[:\s-]*(?P<task>.+)$",
    )
    for pattern in evaluate_patterns:
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if match:
            task = re.sub(r"^[\s:\-]+", "", str(match.group("task") or "").strip())
            if task:
                return ("/swarm evaluate", task)
    return None


def _parse_swarm_absorb_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    for pattern in (
        r"^/swarm absorb (?P<insight_id>[A-Za-z0-9:_-]+)(?: because (?P<reason>.+))?$",
        r"^(?:please\s+|can you\s+)?absorb(?:\s+insight)?\s+(?P<insight_id>[A-Za-z0-9:_-]+)(?:\s+(?:in|into)\s+swarm)?(?: because (?P<reason>.+))?$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return {
                "insight_id": str(match.group("insight_id")),
                "reason": str(match.group("reason")).strip() if match.groupdict().get("reason") else None,
            }
    return None


def _parse_swarm_review_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    for pattern in (
        r"^/swarm review (?P<mastery_id>[A-Za-z0-9:_-]+) (?P<decision>approve|defer|reject)(?: because (?P<reason>.+))?$",
        r"^(?:please\s+|can you\s+)?review mastery (?P<mastery_id>[A-Za-z0-9:_-]+) (?:as )?(?P<decision>approve|defer|reject)(?:\s+in\s+swarm)?(?: because (?P<reason>.+))?$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return {
                "mastery_id": str(match.group("mastery_id")),
                "decision": str(match.group("decision")).lower(),
                "reason": str(match.group("reason")).strip() if match.groupdict().get("reason") else None,
            }
    return None


def _parse_swarm_mode_command(inbound_text: str) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    for pattern in (
        r"^/swarm mode (?P<specialization_id>[A-Za-z0-9:_-]+) (?P<mode>[A-Za-z0-9_\- ]+)$",
        r"^(?:please\s+|can you\s+)?set specialization (?P<specialization_id>[A-Za-z0-9:_-]+) to (?P<mode>[A-Za-z0-9_\- ]+?)(?:\s+in\s+swarm)?$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            parsed_mode = _parse_swarm_evolution_mode(str(match.group("mode")))
            if parsed_mode:
                return {
                    "specialization_id": str(match.group("specialization_id")),
                    "evolution_mode": parsed_mode,
                }
    return None


def _parse_swarm_deliver_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    for pattern in (
        r"^/swarm deliver (?P<upgrade_id>[A-Za-z0-9:_-]+)(?: mode (?P<mode>[A-Za-z0-9_\- ]+))?(?: pr (?P<pr_url>https?://\S+))?$",
        r"^(?:please\s+|can you\s+)?deliver upgrade (?P<upgrade_id>[A-Za-z0-9:_-]+)(?:\s+in\s+swarm)?(?: using (?P<pr_url>https?://\S+))?$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            parsed_mode = _parse_swarm_evolution_mode(str(match.group("mode"))) if match.groupdict().get("mode") else None
            return {
                "upgrade_id": str(match.group("upgrade_id")),
                "evolution_mode": parsed_mode,
                "pr_url": str(match.group("pr_url")).strip() if match.groupdict().get("pr_url") else None,
            }
    return None


def _parse_swarm_sync_delivery_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    for pattern in (
        r"^/swarm sync-delivery (?P<upgrade_id>[A-Za-z0-9:_-]+)(?: pr (?P<pr_url>https?://\S+))?$",
        r"^(?:please\s+|can you\s+)?sync delivery status for upgrade (?P<upgrade_id>[A-Za-z0-9:_-]+)(?:\s+in\s+swarm)?(?: using (?P<pr_url>https?://\S+))?$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return {
                "upgrade_id": str(match.group("upgrade_id")),
                "pr_url": str(match.group("pr_url")).strip() if match.groupdict().get("pr_url") else None,
            }
    return None


def _parse_swarm_path_run_command(inbound_text: str) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(r"^/swarm run (?P<path_key>[A-Za-z0-9:_-]+)$", normalized, flags=re.IGNORECASE)
    if not match:
        return None
    return {"path_key": str(match.group("path_key"))}


def _parse_swarm_autoloop_command(inbound_text: str) -> dict[str, Any] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    autoloop_match = re.match(
        r"^/swarm autoloop (?P<path_key>[A-Za-z0-9:_-]+)(?: rounds (?P<rounds>\d+))?(?: session (?P<session_id>[A-Za-z0-9:_-]+))?(?: allow-fallback)?(?: force)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if autoloop_match:
        return {
            "path_key": str(autoloop_match.group("path_key")),
            "rounds": int(autoloop_match.group("rounds") or 1),
            "session_id": str(autoloop_match.group("session_id") or "").strip() or None,
            "allow_fallback_planner": "allow-fallback" in normalized.lower(),
            "force": normalized.lower().endswith(" force"),
            "continue_latest": False,
        }
    continue_match = re.match(
        r"^/swarm continue (?P<path_key>[A-Za-z0-9:_-]+)(?: session (?P<session_id>[A-Za-z0-9:_-]+))?(?: rounds (?P<rounds>\d+))?(?: force)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if continue_match:
        session_id = str(continue_match.group("session_id") or "").strip() or None
        return {
            "path_key": str(continue_match.group("path_key")),
            "rounds": int(continue_match.group("rounds") or 1),
            "session_id": session_id,
            "allow_fallback_planner": False,
            "force": normalized.lower().endswith(" force"),
            "continue_latest": session_id is None,
        }
    return None


def _parse_swarm_sessions_command(inbound_text: str) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(r"^/swarm sessions (?P<path_key>[A-Za-z0-9:_-]+)$", normalized, flags=re.IGNORECASE)
    if not match:
        return None
    return {"path_key": str(match.group("path_key"))}


def _parse_swarm_session_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^/swarm session (?P<path_key>[A-Za-z0-9:_-]+)(?: (?P<session_id>latest|[A-Za-z0-9:_-]+))?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    session_id = str(match.group("session_id") or "").strip() or None
    if session_id and session_id.lower() == "latest":
        session_id = None
    return {
        "path_key": str(match.group("path_key")),
        "session_id": session_id,
    }


def _parse_swarm_rerun_command(inbound_text: str) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(r"^/swarm rerun(?: (?P<path_key>[A-Za-z0-9:_-]+))?$", normalized, flags=re.IGNORECASE)
    if not match:
        return None
    path_key = str(match.group("path_key") or "").strip() or None
    return {"path_key": path_key} if path_key else {}


def _parse_swarm_scoped_read_command(inbound_text: str, *, noun: str) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        rf"^/swarm {re.escape(noun)} (?P<label>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    label = str(match.group("label") or "").strip()
    if not label:
        return None
    return {"label": label}


def _parse_swarm_evolution_mode(value: str) -> str | None:
    normalized = "_".join(str(value or "").strip().lower().replace("-", " ").split())
    aliases = {
        "observe_only": "observe_only",
        "review_required": "review_required",
        "checked_auto_merge": "checked_auto_merge",
        "trusted_auto_apply": "trusted_auto_apply",
        "observe": "observe_only",
        "review": "review_required",
        "auto_merge": "checked_auto_merge",
        "checked_auto": "checked_auto_merge",
        "trusted_auto": "trusted_auto_apply",
        "auto_apply": "trusted_auto_apply",
    }
    return aliases.get(normalized)


def _resolve_natural_swarm_run_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?run\s+(?:the\s+)?(?P<label>.+?)\s+path(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _resolve_swarm_path_by_label(config_manager=config_manager, label=str(match.group("label") or ""))


def _resolve_natural_swarm_autoloop_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
) -> dict[str, Any] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    force_suffix = r"(?:\s+(?:force|anyway|still|ignore(?:\s+the)?\s+pause|override(?:\s+the)?\s+pause))?"
    start_match = re.match(
        rf"^(?:please\s+|can you\s+)?start\s+(?:an?\s+)?autoloop\s+for\s+(?:the\s+)?(?P<label>.+?)(?:\s+path)?(?:\s+in\s+swarm)?(?:\s+for\s+(?P<rounds>\d+)\s+(?:more\s+)?rounds?)?{force_suffix}$",
        normalized,
        flags=re.IGNORECASE,
    )
    if start_match:
        path_target = _resolve_swarm_path_by_label(config_manager=config_manager, label=str(start_match.group("label") or ""))
        if path_target.get("error"):
            return path_target
        return {
            "path_key": str(path_target["path_key"]),
            "rounds": int(start_match.group("rounds") or 1),
            "session_id": None,
            "allow_fallback_planner": False,
            "force": _natural_swarm_autoloop_force_requested(normalized),
            "continue_latest": False,
        }
    continue_match = re.match(
        rf"^(?:please\s+|can you\s+)?continue\s+(?:the\s+)?(?P<label>.+?)\s+autoloop(?:\s+session\s+(?P<session_id>[A-Za-z0-9:_-]+))?(?:\s+in\s+swarm)?(?:\s+for\s+(?P<rounds>\d+)\s+(?:more\s+)?rounds?)?{force_suffix}$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not continue_match:
        return None
    path_target = _resolve_swarm_path_by_label(config_manager=config_manager, label=str(continue_match.group("label") or ""))
    if path_target.get("error"):
        return path_target
    session_id = str(continue_match.group("session_id") or "").strip() or None
    return {
        "path_key": str(path_target["path_key"]),
        "rounds": int(continue_match.group("rounds") or 1),
        "session_id": session_id,
        "allow_fallback_planner": False,
        "force": _natural_swarm_autoloop_force_requested(normalized),
        "continue_latest": session_id is None,
    }


def _natural_swarm_autoloop_force_requested(inbound_text: str) -> bool:
    lowered = " ".join(str(inbound_text or "").strip().lower().split())
    force_markers = (
        " force ",
        " force",
        " anyway",
        " still",
        " ignore the pause",
        " ignore pause",
        " override the pause",
        " override pause",
    )
    return any(marker in lowered for marker in force_markers)


def _resolve_natural_swarm_sessions_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|list)\s+(?:recent\s+)?(?P<label>.+?)\s+autoloop\s+sessions(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _resolve_swarm_path_by_label(config_manager=config_manager, label=str(match.group("label") or ""))


def _resolve_natural_swarm_session_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|inspect)\s+(?:the\s+)?(?:(?P<latest>latest)\s+)?(?P<label>.+?)\s+autoloop\s+session(?:\s+(?P<session_id>[A-Za-z0-9:_-]+))?(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    path_target = _resolve_swarm_path_by_label(config_manager=config_manager, label=str(match.group("label") or ""))
    if path_target.get("error"):
        return path_target
    session_id = str(match.group("session_id") or "").strip() or None
    if match.group("latest"):
        session_id = None
    return {
        "path_key": str(path_target["path_key"]),
        "session_id": session_id,
    }


def _resolve_natural_swarm_rerun_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?execute\s+(?:the\s+)?latest\s+(?P<label>.+?)\s+rerun\s+request(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _resolve_swarm_path_by_label(config_manager=config_manager, label=str(match.group("label") or ""))


def _resolve_natural_swarm_scoped_read_target(
    *,
    inbound_text: str,
    noun: str,
    config_manager: ConfigManager,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    patterns = {
        "insights": (
            r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|list)\s+(?P<label>.+?)\s+insights(?:\s+in\s+swarm)?$",
            r"^(?:please\s+|can you\s+)?what\s+insights\s+are\s+in\s+(?P<label>.+?)(?:\s+in\s+swarm)?$",
        ),
        "masteries": (
            r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|list)\s+(?P<label>.+?)\s+masteries(?:\s+in\s+swarm)?$",
            r"^(?:please\s+|can you\s+)?what\s+masteries\s+are\s+in\s+(?P<label>.+?)(?:\s+in\s+swarm)?$",
        ),
        "upgrades": (
            r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|list)\s+(?:pending\s+)?(?P<label>.+?)\s+upgrades(?:\s+in\s+swarm)?$",
            r"^(?:please\s+|can you\s+)?what\s+(?:pending\s+)?upgrades\s+are\s+in\s+(?P<label>.+?)(?:\s+in\s+swarm)?$",
        ),
    }
    for pattern in patterns.get(noun, ()):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            label = str(match.group("label") or "").strip()
            if label:
                return {"label": label}
    return None


def _prepare_swarm_autoloop_request(
    *,
    config_manager: ConfigManager,
    payload: dict[str, Any],
) -> dict[str, Any]:
    prepared = dict(payload)
    if not prepared.get("continue_latest"):
        return prepared
    latest = swarm_bridge_read_autoloop_session(
        config_manager,
        path_key=str(prepared.get("path_key") or ""),
        session_id=None,
    )
    session_id = str(latest.get("session_id") or "").strip()
    if not session_id:
        return {
            "error": (
                "Swarm autoloop continuation needs an existing session.\n"
                f"No saved session summary exists yet for {str(prepared.get('path_key') or 'that path')}. "
                "Start one first with `/swarm autoloop <path_key>`."
            )
        }
    prepared["session_id"] = session_id
    prepared["continue_latest"] = False
    return prepared


def _load_swarm_scoped_insights(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    label: str,
) -> dict[str, Any]:
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=label,
    )
    if specialization.get("error"):
        raise RuntimeError(str(specialization["error"]).replace("specialization target", "insight lane"))
    specialization_id = str(specialization["specialization_id"])
    specialization_key = str(specialization["specialization_key"])
    actionable_statuses = {"captured", "distilled", "queued_for_test", "benchmark_supported", "live_supported"}
    insights = [
        item
        for item in swarm_read_insights(config_manager, state_db)
        if isinstance(item, dict)
        and str(item.get("status") or "") in actionable_statuses
        and (
            str(item.get("specializationId") or "") == specialization_id
            or _normalize_swarm_label(str(item.get("specializationId") or "")) == _normalize_swarm_label(specialization_key)
        )
    ]
    return {
        "specialization_key": specialization_key,
        "specialization_label": str(specialization["specialization_label"]),
        "insights": insights,
    }


def _load_swarm_scoped_masteries(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    label: str,
) -> dict[str, Any]:
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=label,
    )
    if specialization.get("error"):
        raise RuntimeError(str(specialization["error"]).replace("specialization target", "mastery lane"))
    specialization_key = str(specialization["specialization_key"])
    masteries = [
        item
        for item in swarm_read_masteries(config_manager, state_db)
        if isinstance(item, dict)
        and _normalize_swarm_label(str(item.get("specializationScope") or "")) == _normalize_swarm_label(specialization_key)
    ]
    return {
        "specialization_key": specialization_key,
        "specialization_label": str(specialization["specialization_label"]),
        "masteries": masteries,
    }


def _load_swarm_scoped_upgrades(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    label: str,
) -> dict[str, Any]:
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=label,
    )
    if specialization.get("error"):
        raise RuntimeError(str(specialization["error"]).replace("specialization target", "upgrade lane"))
    specialization_key = str(specialization["specialization_key"])
    mastery_ids = {
        str(item.get("id") or "")
        for item in swarm_read_masteries(config_manager, state_db)
        if isinstance(item, dict)
        and _normalize_swarm_label(str(item.get("specializationScope") or "")) == _normalize_swarm_label(specialization_key)
    }
    pending_statuses = {"draft", "queued", "upgrade_opened", "awaiting_review"}
    upgrades = [
        item
        for item in swarm_read_upgrades(config_manager, state_db)
        if isinstance(item, dict)
        and str(item.get("status") or "") in pending_statuses
        and str(item.get("derivedFromMasteryId") or "") in mastery_ids
    ]
    return {
        "specialization_key": specialization_key,
        "specialization_label": str(specialization["specialization_label"]),
        "upgrades": upgrades,
    }


def _resolve_natural_swarm_mode_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?set (?P<label>.+?) to (?P<mode>[A-Za-z0-9_\- ]+?)(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    raw_label = str(match.group("label") or "").strip()
    if raw_label.lower().startswith("specialization "):
        return None
    evolution_mode = _parse_swarm_evolution_mode(str(match.group("mode") or ""))
    if not evolution_mode:
        return None
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=raw_label,
    )
    if specialization.get("error"):
        return {
            "error": str(specialization["error"]),
        }
    return {
        "specialization_id": str(specialization["specialization_id"]),
        "evolution_mode": evolution_mode,
    }


def _resolve_natural_swarm_absorb_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?absorb\s+(?:the\s+)?latest\s+(?P<label>.+?)\s+insight(?:\s+in\s+swarm)?(?:\s+because\s+(?P<reason>.+))?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=str(match.group("label") or ""),
    )
    if specialization.get("error"):
        return {
            "error": str(specialization["error"]).replace("specialization target", "insight target"),
        }
    specialization_id = str(specialization["specialization_id"])
    insights = swarm_read_insights(config_manager, state_db)
    actionable_statuses = {"captured", "distilled", "queued_for_test", "benchmark_supported", "live_supported"}
    candidates = [
        item
        for item in insights
        if isinstance(item, dict)
        and str(item.get("status") or "") in actionable_statuses
        and (
            str(item.get("specializationId") or "") == specialization_id
            or _normalize_swarm_label(str(item.get("specializationId") or "")) == _normalize_swarm_label(str(specialization.get("specialization_key") or ""))
        )
    ]
    if not candidates:
        label = str(specialization.get("specialization_label") or specialization.get("specialization_key") or "")
        return {
            "error": (
                "Swarm action needs a clearer insight target.\n"
                f"No absorbable insights matched {label}. Use `/swarm insights` to pick an exact ID."
            ),
        }
    ranked = sorted(candidates, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    reason = str(match.group("reason") or "").strip()
    if not reason:
        reason = f"Recorded from Telegram natural-language request: {normalized}"
    return {
        "insight_id": str(ranked[0].get("id") or ""),
        "reason": reason,
    }


def _resolve_natural_swarm_review_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?(?P<decision>approve|defer|reject)\s+(?:the\s+)?latest\s+(?P<label>.+?)\s+mastery(?:\s+in\s+swarm)?(?:\s+because\s+(?P<reason>.+))?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=str(match.group("label") or ""),
    )
    if specialization.get("error"):
        return {
            "error": str(specialization["error"]).replace("specialization target", "mastery target"),
        }
    specialization_key = str(specialization["specialization_key"])
    masteries = swarm_read_masteries(config_manager, state_db)
    candidates = [
        item
        for item in masteries
        if isinstance(item, dict) and _normalize_swarm_label(str(item.get("specializationScope") or "")) == _normalize_swarm_label(specialization_key)
    ]
    if not candidates:
        label = str(specialization.get("specialization_label") or specialization_key)
        return {
            "error": (
                "Swarm action needs a clearer mastery target.\n"
                f"No mastery records matched {label}. Use `/swarm masteries` to pick an exact ID."
            ),
        }
    ranked = sorted(candidates, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    reason = str(match.group("reason") or "").strip()
    if not reason:
        reason = f"Recorded from Telegram natural-language request: {normalized}"
    return {
        "mastery_id": str(ranked[0].get("id") or ""),
        "decision": str(match.group("decision") or "").lower(),
        "reason": reason,
    }


def _resolve_natural_swarm_deliver_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?deliver\s+(?:the\s+)?latest\s+(?P<label>.+?)\s+upgrade(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _resolve_latest_swarm_upgrade_target(
        config_manager=config_manager,
        state_db=state_db,
        label=str(match.group("label") or ""),
    )


def _resolve_natural_swarm_sync_delivery_target(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> dict[str, str] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    match = re.match(
        r"^(?:please\s+|can you\s+)?sync delivery status for (?:the\s+)?latest\s+(?P<label>.+?)\s+upgrade(?:\s+in\s+swarm)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _resolve_latest_swarm_upgrade_target(
        config_manager=config_manager,
        state_db=state_db,
        label=str(match.group("label") or ""),
    )


def _resolve_latest_swarm_upgrade_target(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    label: str,
) -> dict[str, str]:
    specialization = _resolve_swarm_specialization_by_label(
        config_manager=config_manager,
        state_db=state_db,
        label=label,
    )
    if specialization.get("error"):
        return {
            "error": str(specialization["error"]).replace("specialization target", "upgrade target"),
        }
    specialization_key = str(specialization["specialization_key"])
    masteries = swarm_read_masteries(config_manager, state_db)
    specialization_mastery_ids = {
        str(item.get("id") or "")
        for item in masteries
        if isinstance(item, dict) and _normalize_swarm_label(str(item.get("specializationScope") or "")) == _normalize_swarm_label(specialization_key)
    }
    pending_statuses = {"draft", "queued", "upgrade_opened", "awaiting_review"}
    upgrades = swarm_read_upgrades(config_manager, state_db)
    candidates = [
        item
        for item in upgrades
        if isinstance(item, dict)
        and str(item.get("status") or "") in pending_statuses
        and str(item.get("derivedFromMasteryId") or "") in specialization_mastery_ids
    ]
    if not candidates:
        label = str(specialization.get("specialization_label") or specialization_key)
        return {
            "error": (
                "Swarm action needs a clearer upgrade target.\n"
                f"No pending upgrades matched {label}. Use `/swarm upgrades` to pick an exact ID."
            ),
        }
    ranked = sorted(candidates, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    return {
        "upgrade_id": str(ranked[0].get("id") or ""),
    }


def _resolve_swarm_path_by_label(
    *,
    config_manager: ConfigManager,
    label: str,
) -> dict[str, str]:
    payload = swarm_bridge_list_paths(config_manager)
    paths = payload.get("paths") if isinstance(payload, dict) else []
    entries = [item for item in paths if isinstance(item, dict)]
    target = _normalize_swarm_label(label)
    if not target:
        return {
            "error": "Swarm path action needs a clearer path target.\nUse `/swarm paths` to inspect available path keys.",
        }
    exact_matches = [
        item
        for item in entries
        if target
        in {
            _normalize_swarm_label(str(item.get("key") or "")),
            _normalize_swarm_label(str(item.get("label") or "")),
        }
    ]
    matches = exact_matches
    if not matches:
        matches = [
            item
            for item in entries
            if (
                target in _normalize_swarm_label(str(item.get("key") or ""))
                or target in _normalize_swarm_label(str(item.get("label") or ""))
                or _normalize_swarm_label(str(item.get("key") or "")).startswith(target)
                or _normalize_swarm_label(str(item.get("label") or "")).startswith(target)
            )
        ]
    if len(matches) != 1:
        return {
            "error": "Swarm path action needs a clearer path target.\nUse `/swarm paths` to inspect available path keys.",
        }
    match = matches[0]
    return {
        "path_key": str(match.get("key") or ""),
        "path_label": str(match.get("label") or ""),
    }


def _resolve_swarm_specialization_by_label(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    label: str,
) -> dict[str, str]:
    specializations = swarm_read_specializations(config_manager, state_db)
    target = _normalize_swarm_label(label)
    if not target:
        return {
            "error": "Swarm action needs a clearer specialization target.\nUse `/swarm specializations` to pick an exact ID.",
        }
    exact_matches = [
        item
        for item in specializations
        if isinstance(item, dict)
        and target
        in {
            _normalize_swarm_label(str(item.get("label") or "")),
            _normalize_swarm_label(str(item.get("key") or "")),
            _normalize_swarm_label(str(item.get("id") or "")),
        }
    ]
    matches = exact_matches
    if not matches:
        matches = [
            item
            for item in specializations
            if isinstance(item, dict)
            and (
                target in _normalize_swarm_label(str(item.get("label") or ""))
                or target in _normalize_swarm_label(str(item.get("key") or ""))
                or _normalize_swarm_label(str(item.get("label") or "")).startswith(target)
                or _normalize_swarm_label(str(item.get("key") or "")).startswith(target)
            )
        ]
    if len(matches) != 1:
        return {
            "error": (
                "Swarm action needs a clearer specialization target.\n"
                "Use `/swarm specializations` to pick an exact ID."
            ),
        }
    match = matches[0]
    return {
        "specialization_id": str(match.get("id") or ""),
        "specialization_key": str(match.get("key") or ""),
        "specialization_label": str(match.get("label") or ""),
    }


def _normalize_swarm_label(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def _think_state_key(*, external_user_id: str) -> str:
    return f"telegram:think_visibility:{external_user_id}"


def _voice_reply_state_key(*, external_user_id: str) -> str:
    return f"telegram:voice_reply:{external_user_id}"


def _voice_reply_enabled_for_user(*, state_db: StateDB, external_user_id: str) -> bool:
    payload = _load_runtime_json_object(state_db, _voice_reply_state_key(external_user_id=external_user_id))
    return bool(payload.get("enabled", False))


def _set_voice_reply_enabled_for_user(
    *,
    state_db: StateDB,
    external_user_id: str,
    enabled: bool,
) -> None:
    set_runtime_state_value(
        state_db=state_db,
        state_key=_voice_reply_state_key(external_user_id=external_user_id),
        value=json.dumps({"enabled": enabled}, sort_keys=True),
    )


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


def _strip_internal_swarm_recommendation(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    filtered_lines = [
        line
        for line in lines
        if not re.match(r"^\s*Swarm:\s+recommended for this task because\b", line, flags=re.IGNORECASE)
    ]
    collapsed = "\n".join(filtered_lines).strip()
    return collapsed or str(text or "").strip()


def _apply_post_approval_welcome(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    external_user_id: str,
    human_id: str | None,
    agent_id: str | None,
    reply_text: str,
) -> str:
    styled_reply = _apply_saved_telegram_surface_style(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
        reply_text=reply_text,
        surface="telegram_chat",
    )
    welcome_pending = consume_pairing_welcome(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
    )
    if not welcome_pending:
        return styled_reply
    profile, agent_name = _load_telegram_persona_surface_state(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
    )
    welcome_text = build_telegram_surface_identity_preamble(
        profile=profile,
        agent_name=agent_name or "Spark Intelligence",
        surface="approval_welcome",
    )
    return f"{welcome_text}\n\n{styled_reply}".strip()


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
