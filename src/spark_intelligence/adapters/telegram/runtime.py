from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from uuid import uuid4

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.attachments import (
    build_attachment_context,
    list_chip_records,
    record_chip_hook_execution,
    run_chip_hook,
    run_first_chip_hook_supporting,
    screen_chip_hook_text,
)
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
from spark_intelligence.llm_wiki import (
    build_llm_wiki_candidate_inbox,
    build_llm_wiki_candidate_scan,
    build_llm_wiki_inventory,
    build_llm_wiki_status,
)
from spark_intelligence.memory import run_memory_doctor
from spark_intelligence.personality import (
    agent_has_reonboard_candidate,
    apply_telegram_surface_persona,
    build_telegram_surface_identity_preamble,
    create_agent_persona_savepoint,
    detect_and_persist_agent_persona_preferences,
    load_agent_persona_savepoint,
    list_agent_persona_savepoints,
    load_personality_profile,
    maybe_handle_agent_persona_onboarding_turn,
    pop_agent_persona_undo_snapshot,
    resolve_builder_persona_agent_id,
    restore_agent_persona_savepoint,
)
from spark_intelligence.researcher_bridge.advisory import (
    build_researcher_reply,
    record_researcher_bridge_result,
    try_spark_character_fallback,
)
from spark_intelligence.self_awareness import (
    build_agent_operating_context,
    build_self_awareness_capsule,
    load_capability_ledger,
    run_route_probe_and_record,
)
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
    swarm_doctor,
    swarm_status,
    sync_swarm_collective,
)


TELEGRAM_PARROT_EFFECT_VERSION = "parrot-balanced-v1"


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
    allowed_user_source: str = "config.allowed_users"
    runtime_allowlist_entry_count: int = 0

    def to_line(self) -> str:
        if not self.configured:
            return "- telegram: not configured"
        bot_ref = f" bot=@{self.bot_username}" if self.bot_username else ""
        auth_ref = f" auth={self.auth_status or 'unknown'}"
        allowlist_parts = self.allowlist_detail_parts()
        return (
            f"- telegram: status={self.status or 'unknown'} pairing_mode={self.pairing_mode} "
            f"auth_ref={self.auth_ref or 'missing'}{bot_ref}{auth_ref} {' '.join(allowlist_parts)}"
        )

    def allowlist_detail_parts(self) -> list[str]:
        parts = [
            f"allowed_users={self.allowed_user_count}",
            f"allowlist_source={self.allowed_user_source}",
        ]
        if self.runtime_allowlist_entry_count != self.allowed_user_count:
            parts.append(f"raw_runtime_allowlist_entries={self.runtime_allowlist_entry_count}")
        return parts


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


_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "operator": {
        "label": "Operator",
        "description": "Direct, grounded, and concrete. Prefer next steps over filler.",
        "instruction": "be direct, grounded, and concrete; keep replies focused on the user's actual thread; avoid filler and canned opener questions",
    },
    "claude-like": {
        "label": "Claude-like",
        "description": "Stronger continuity, less canned phrasing, and more grounded follow-up questions.",
        "instruction": "be more Claude-like in conversation continuity and less canned with more grounded follow-up questions",
    },
    "concise": {
        "label": "Concise",
        "description": "Shorter answers, brisk pacing, and low filler.",
        "instruction": "be more direct and keep replies short",
    },
    "warm": {
        "label": "Warm",
        "description": "More human and calm without losing structure.",
        "instruction": "be less formal and more human while staying calm and friendly",
    },
}


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


_SCORE_INTENT_PATTERN = re.compile(
    r"\b(score|rate|evaluate|grade|assess|critique|review|judge|rank)\b",
    flags=re.IGNORECASE,
)

_PROMPT_INJECTION_INTENT_PATTERN = re.compile(
    r"("
    r"\b(ignore|disregard|override|bypass|forget)\b.{0,80}\b(previous|prior|earlier|above|system|developer|safety|policy|instruction|instructions|rules)\b"
    r"|"
    r"\b(system|developer|admin|root)\s*(prompt|message|instruction|instructions|rules)\b"
    r"|"
    r"\b(reveal|show|print|dump|leak|exfiltrate)\b.{0,80}\b(system|developer|hidden|secret|prompt|instructions|token|key)\b"
    r"|"
    r"<\s*/?\s*(system|developer|instructions?)\s*>"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)


def _looks_like_score_request(message: str) -> bool:
    if not message:
        return False
    return bool(_SCORE_INTENT_PATTERN.search(message))


def _looks_like_prompt_injection_instruction(message: str) -> bool:
    if not message:
        return False
    compact = " ".join(str(message).split())
    return bool(_PROMPT_INJECTION_INTENT_PATTERN.search(compact))


def _format_chip_metric_value(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        if 0.0 <= value <= 1.0:
            return f"{round(value * 100)}%"
        return f"{value:.2f}"
    if value is None:
        return "n/a"
    return str(value)


def _build_verbatim_chip_block(raw_chip_metrics: list[dict]) -> str:
    lines = ["", "---", "Chip output (verbatim, not paraphrased):"]
    for entry in raw_chip_metrics:
        if not isinstance(entry, dict):
            continue
        chip_key = str(entry.get("chip_key") or "unknown")
        verdict = entry.get("verdict")
        confidence = entry.get("verdict_confidence")
        metrics = entry.get("metrics") or {}
        parts: list[str] = []
        if verdict is not None:
            parts.append(f"verdict={verdict}")
        if confidence is not None:
            parts.append(f"confidence={_format_chip_metric_value(confidence)}")
        if isinstance(metrics, dict):
            for label, value in metrics.items():
                if label in ("verdict_confidence",):
                    continue
                clean_label = str(label).replace("_score", "").replace("_", " ")
                parts.append(f"{clean_label}={_format_chip_metric_value(value)}")
        recommended = entry.get("recommended_next_step")
        if recommended:
            parts.append(f"next={recommended}")
        if parts:
            lines.append(f"- {chip_key}: " + ", ".join(parts))
    return "\n".join(lines) if len(lines) > 3 else ""


def _maybe_save_reply_as_draft(
    *,
    state_db,
    external_user_id: str,
    session_id: str | None,
    chip_used: str | None,
    reply_text: str,
    user_message: str = "",
) -> str:
    if not reply_text or not external_user_id:
        return reply_text
    try:
        from spark_intelligence.bot_drafts import (
            detect_generative_intent,
            detect_iteration_intent,
            find_draft_for_iteration,
            reply_resembles_draft,
            save_draft,
            update_draft_content,
        )
    except Exception:
        return reply_text

    user = str(external_user_id or "").strip()
    reply = str(reply_text or "")
    if not user or not reply.strip():
        return reply_text

    is_iteration = bool(user_message) and detect_iteration_intent(user_message) is not None
    is_generative = bool(user_message) and detect_generative_intent(user_message)

    try:
        from pathlib import Path as _P
        _dbg = _P(r"C:/Users/USER/Desktop/spark-intelligence-builder/.tmp-home-live-telegram-real/logs/draft_capture_probe.log")
        _dbg.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with _dbg.open("a", encoding="utf-8") as _fh:
            _fh.write(
                f"{timestamp}Z user={user} iter={is_iteration} "
                f"gen={is_generative} msg={user_message[:120]!r} reply_len={len(reply)}\n"
            )
    except Exception:
        pass

    if is_iteration:
        source_draft = None
        try:
            source_draft = find_draft_for_iteration(
                state_db,
                external_user_id=user,
                channel_kind="telegram",
                user_message=user_message,
            )
        except Exception:
            source_draft = None
        if source_draft is not None:
            on_topic = True
            try:
                on_topic = reply_resembles_draft(source_draft.content, reply)
            except Exception:
                on_topic = True
            if on_topic:
                try:
                    update_draft_content(
                        state_db,
                        draft_id=source_draft.draft_id,
                        content=reply,
                        chip_used=chip_used,
                    )
                except Exception:
                    pass
                return reply_text
            # iteration intent fired but reply drifted off-topic —
            # preserve the original draft and capture the divergent
            # reply as a fresh draft instead of silently clobbering.
            try:
                save_draft(
                    state_db,
                    external_user_id=user,
                    channel_kind="telegram",
                    content=reply,
                    session_id=session_id,
                    chip_used=chip_used,
                )
            except Exception:
                pass
            return reply_text
        try:
            save_draft(
                state_db,
                external_user_id=user,
                channel_kind="telegram",
                content=reply,
                session_id=session_id,
                chip_used=chip_used,
            )
        except Exception:
            pass
        return reply_text

    if is_generative:
        try:
            save_draft(
                state_db,
                external_user_id=user,
                channel_kind="telegram",
                content=reply,
                session_id=session_id,
                chip_used=chip_used,
            )
        except Exception:
            pass
        return reply_text

    return reply_text


def _maybe_capture_user_instruction(
    *,
    state_db,
    user_message: str,
    external_user_id: str,
    reply_text: str,
    bridge_mode: str | None = None,
    routing_decision: str | None = None,
) -> str:
    try:
        from spark_intelligence.user_instructions import (
            add_instruction,
            archive_instruction,
            detect_instruction_intent,
            matching_instructions_to_archive,
        )
    except Exception:
        return reply_text
    if not user_message or not external_user_id:
        return reply_text
    if bridge_mode == "memory_generic_observation_delete" or routing_decision == "memory_generic_observation_delete":
        return reply_text
    if _looks_like_memory_forget_request(user_message):
        return reply_text
    if _looks_like_prompt_injection_instruction(user_message):
        return reply_text
    intent = detect_instruction_intent(user_message)
    if not intent:
        return reply_text
    base = (reply_text or "").rstrip()
    text_value = str(intent.get("instruction_text") or "").strip()
    if not text_value:
        return reply_text
    if intent.get("action") == "forget":
        try:
            matches = matching_instructions_to_archive(
                state_db,
                external_user_id=str(external_user_id),
                channel_kind="telegram",
                needle=text_value,
                limit=3,
            )
        except Exception:
            return reply_text
        if not matches:
            return f"{base}\n\n_(no matching saved instruction to forget for: \"{text_value[:120]}\")_\n"
        archived_texts: list[str] = []
        for inst in matches:
            try:
                if archive_instruction(state_db, instruction_id=inst.instruction_id):
                    archived_texts.append(inst.instruction_text)
            except Exception:
                continue
        if not archived_texts:
            return reply_text
        joined = "; ".join(t[:120] for t in archived_texts)
        return f"{base}\n\n_(forgot {len(archived_texts)} saved instruction(s): {joined})_\n"
    try:
        saved = add_instruction(
            state_db,
            external_user_id=str(external_user_id),
            channel_kind="telegram",
            instruction_text=text_value,
            source="explicit",
        )
    except Exception:
        return reply_text
    return f"{base}\n\n_(saved instruction: \"{saved.instruction_text[:160]}\" - will apply to future replies)_\n"


def _looks_like_memory_forget_request(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    if not re.search(r"\b(?:forget|delete|remove|erase|purge|stop remembering)\b", text):
        return False
    return bool(
        re.search(r"\b(?:saved\s+)?memor(?:y|ies)|\bprofile\s+(?:fact|memory)|\bactive\s+current\s+profile\b", text)
        or re.search(r"\b(?:my\s+name|preferred\s+name|written\s+name|pronounced|pronunciation)\b", text)
    )


def _maybe_append_verbatim_chip_block(
    *,
    user_message: str,
    reply_text: str,
    raw_chip_metrics: list[dict],
) -> str:
    if not raw_chip_metrics:
        return reply_text
    if not _looks_like_score_request(user_message):
        return reply_text
    block = _build_verbatim_chip_block(raw_chip_metrics)
    if not block:
        return reply_text
    base = (reply_text or "").rstrip()
    return f"{base}\n{block}\n"


_SPARK_CHARACTER_FALLBACK_ROUTES = frozenset(
    {"bridge_error", "bridge_disabled", "stub", "provider_resolution_failed"}
)
_SPARK_CHARACTER_FALLBACK_MODES = frozenset({"bridge_error", "disabled", "stub"})


def _maybe_spark_character_reply(
    *,
    config_manager: ConfigManager,
    user_message: str,
    bridge_mode: str | None,
    routing_decision: str | None,
    surface: str | None = None,
) -> str | None:
    """Try to serve a real LLM reply via spark-character when the
    Researcher bridge cannot. Returns None on any failure so the caller
    falls through to the canned error copy.

    surface lets the caller hint what surface the reply is for so the
    correct surface overlay (voice, browser_extension, telegram, ...)
    gets appended to the persona during generation.
    """
    mode = str(bridge_mode or "").strip()
    route = str(routing_decision or "").strip()
    if route not in _SPARK_CHARACTER_FALLBACK_ROUTES and mode not in _SPARK_CHARACTER_FALLBACK_MODES:
        return None
    return try_spark_character_fallback(
        user_message=user_message,
        config_manager=config_manager,
        surface=surface,
    )


def _spark_character_delivery_route(
    *,
    bridge_mode: str | None,
    routing_decision: str | None,
    spark_character_reply: str | None,
) -> tuple[str | None, str | None, dict[str, str]]:
    if not spark_character_reply:
        if bridge_mode == "bridge_error" and routing_decision == "provider_resolution_failed":
            return "provider_resolution_failed", routing_decision, {}
        return bridge_mode, routing_decision, {}
    primary: dict[str, str] = {}
    if bridge_mode:
        primary["primary_bridge_mode"] = str(bridge_mode)
    if routing_decision:
        primary["primary_routing_decision"] = str(routing_decision)
    return "spark_character_fallback", "spark_character_fallback", primary


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
                "I can't pull live research for you right now.",
                "Live research is turned off in this workspace.",
                "Try again once it's been turned back on.",
            ]
        )
    if route == "secret_boundary_blocked":
        return "\n".join(
            [
                "I held off on that one.",
                "There was sensitive material in the request that I'd rather not send out.",
                "Pull the secret bits and ask me again.",
            ]
        )
    if route == "provider_resolution_failed":
        lines = [
            "I can't pull live research for you right now.",
            "My model credentials aren't set up correctly on this side.",
        ]
        if detail and detail != text:
            lines.append(f"Detail: {detail}")
        lines.append("Get the model auth fixed, then ask me again.")
        return "\n".join(lines)
    if route == "bridge_error":
        lines = [
            "I can't pull live research for you right now.",
            "Something on my end failed mid-call.",
        ]
        if detail and detail != text:
            lines.append(f"Detail: {detail}")
        lines.append("Give it another shot in a minute, or check the runtime if it keeps happening.")
        return "\n".join(lines)
    if route == "stub" or mode == "stub":
        return "\n".join(
            [
                "I can't pull live research for you right now.",
                "Nothing's wired up to handle that yet in this workspace.",
                "Once it's set up, ask me again.",
            ]
        )
    if mode != "blocked":
        return text
    if route == "browser_permission_required":
        origin_match = re.search(r"host access for (\S+)", text, flags=re.IGNORECASE)
        origin = str(origin_match.group(1)).rstrip(".,") if origin_match else "the requested site"
        return "\n".join(
            [
                "I can't open that page yet.",
                f"I don't have access to {origin}.",
                f"Grant site access for {origin} in the extension popup and I'll try again.",
            ]
        )
    if route == "browser_unavailable":
        return "\n".join(
            [
                "I can't search the web right now.",
                "My live browser session dropped.",
                "Reconnect it and ask me again.",
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
            allowed_user_source="none",
            runtime_allowlist_entry_count=0,
        )

    configured_allowed_users: list[str] = []
    for item in record.get("allowed_users") or []:
        user_id = str(item).strip()
        if user_id and user_id not in configured_allowed_users:
            configured_allowed_users.append(user_id)

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
        allowed_user_count=len(configured_allowed_users),
        allowed_user_source="config.allowed_users",
        runtime_allowlist_entry_count=int(count or 0),
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
        "advisor_context": {
            "runtime": {
                "os": platform.system() or "unknown",
                "machine": platform.machine() or "unknown",
            },
            "preferences": [],
            "source_ledger": [
                {
                    "source": "builder_runtime",
                    "role": "system_context",
                    "claim_boundary": "Runtime facts can inform setup recommendations but cannot prove provider voice readiness.",
                }
            ],
        },
    }
    try:
        provider = resolve_runtime_provider(config_manager=config_manager, state_db=state_db)
        secret_ref = getattr(provider, "secret_ref", None)
        provider_payload = {
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
        payload["provider"] = provider_payload
        payload["advisor_context"]["active_provider"] = {
            key: value
            for key, value in provider_payload.items()
            if key in {"provider_id", "provider_kind", "api_mode", "execution_transport", "base_url", "default_model"}
        }
        payload["advisor_context"]["source_ledger"].append(
            {
                "source": "builder_runtime_provider",
                "role": "provider_context",
                "claim_boundary": "Provider identity can guide recommendations; dedicated STT/TTS probes decide readiness.",
            }
        )
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


def _decode_embedded_telegram_audio(normalized: Any) -> tuple[bytes, str] | None:
    encoded = str(getattr(normalized, "media_audio_base64", "") or "").strip()
    if not encoded:
        return None
    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RuntimeError("Telegram runner provided invalid embedded audio bytes.") from exc
    if not audio_bytes:
        raise RuntimeError("Telegram runner provided empty embedded audio bytes.")
    filename = str(getattr(normalized, "media_filename", "") or "").strip()
    return audio_bytes, filename


def _transcribe_telegram_audio_bytes(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    normalized: Any,
    audio_bytes: bytes,
    file_path: str,
) -> dict[str, Any]:
    transcribe_started = perf_counter()
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
    transcribe_ms = int((perf_counter() - transcribe_started) * 1000)
    if execution is None:
        raise RuntimeError(
            "No attached chip supports `voice.transcribe`. Attach and activate `spark-voice-comms` first."
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
        "voice_timing": {
            "transcribe_hook_ms": transcribe_ms,
            "audio_bytes": len(audio_bytes),
            "audio_source": str(getattr(normalized, "media_source", "") or "telegram_client"),
        },
    }


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
    try:
        embedded_audio = _decode_embedded_telegram_audio(normalized)
        if embedded_audio is not None:
            audio_bytes, file_path = embedded_audio
            return _transcribe_telegram_audio_bytes(
                config_manager=config_manager,
                state_db=state_db,
                normalized=normalized,
                audio_bytes=audio_bytes,
                file_path=file_path,
            )
    except Exception as exc:
        return {
            "effective_text": None,
            "transcript_text": None,
            "routing_decision": "voice_transcription_unavailable",
            "reply_text": _render_telegram_voice_transcription_unavailable_reply(reason=str(exc)),
            "error": str(exc),
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
        return _transcribe_telegram_audio_bytes(
            config_manager=config_manager,
            state_db=state_db,
            normalized=normalized,
            audio_bytes=audio_bytes,
            file_path=file_path,
        )
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
    simulation: bool = True,
) -> TelegramSimulationResult:
    normalized = normalize_telegram_update(update_payload, channel_id="telegram")
    request_prefix = "sim" if simulation else "telegram"
    request_id = f"{request_prefix}:{normalized.update_id}"
    origin_surface = "simulation_cli" if simulation else "telegram_runtime"
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
    delivery_primary_route: dict[str, str] = {}
    effective_text = normalized.text
    transcript_text = None
    bridge_voice_media: dict[str, Any] | None = None
    bridge_voice_error: str | None = None
    respect_voice_reply_state_for_bridge = True
    voice_answer_requested_for_bridge = False
    media_input: dict[str, Any] = {}
    runtime_command_name: str | None = None
    runtime_command_metadata: dict[str, object] = {}
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
            respect_voice_reply_state_for_bridge = False
        else:
            effective_text = str(media_input.get("effective_text") or normalized.text)
            transcript_text = media_input.get("transcript_text")
        voice_answer_request = _extract_voice_answer_request(effective_text)
        if voice_answer_request:
            effective_text = voice_answer_request
            voice_answer_requested_for_bridge = True
        command_result = _handle_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            inbound_text=effective_text,
            run_id=None,
            request_id=request_id,
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
        )
        if media_input.get("reply_text"):
            pass
        elif command_result is not None:
            runtime_command_name = str(command_result.get("command") or "").strip() or None
            runtime_command_metadata = _runtime_command_trace_metadata(command_result)
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
            respect_voice_reply_state_for_bridge = bool(command_result.get("respect_voice_reply_state", True))
            if not simulation and bool(command_result.get("force_voice", False)):
                spoken_source = str(command_result.get("voice_text") or outbound_text)
                spoken_text = _prepare_voice_reply_text(spoken_source)
                try:
                    voice_payload = _synthesize_telegram_voice_reply(
                        config_manager=config_manager,
                        state_db=state_db,
                        human_id=resolution.human_id,
                        agent_id=resolution.agent_id,
                        text=spoken_text,
                        tts=command_result.get("voice_tts") if isinstance(command_result.get("voice_tts"), dict) else None,
                    )
                    bridge_voice_media = _bridge_voice_media_from_payload(voice_payload)
                except Exception as exc:  # pragma: no cover - exercised by live adapter failures
                    bridge_voice_error = str(exc)
                    outbound_text = (
                        "I tried to make that voice reply, but the audio step failed.\n\n"
                        "Run `/voice onboard local`, then try `/voice speak ...` again."
                    )
        else:
            # P2-13: enter the v2 onboarding state machine whenever the
            # pairing welcome is still pending (fresh pair, existing
            # behavior) OR whenever the user already has a saved persona
            # profile but no in-progress onboarding state blob. The
            # second branch powers the P2-12 one-tap skip offer for
            # existing users. Q-H of
            # docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11.
            onboarding_eligible = pairing_welcome_pending(
                state_db=state_db,
                channel_id="telegram",
                external_user_id=normalized.telegram_user_id,
            ) or agent_has_reonboard_candidate(
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                state_db=state_db,
            )
            onboarding_result = maybe_handle_agent_persona_onboarding_turn(
                human_id=resolution.human_id,
                agent_id=resolution.agent_id,
                user_message=effective_text,
                state_db=state_db,
                source_surface="telegram",
                source_ref=request_id,
                start_if_eligible=onboarding_eligible,
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
                _instruction_intent = None
                _schedule_intent = None
                _delete_intent = None
                _pending_delete = None
                _generic_memory_observation = None
                _mission_control_query = False
                _confirmation_yes = False
                _confirmation_no = False
                if effective_text and not _looks_like_prompt_injection_instruction(effective_text):
                    try:
                        from spark_intelligence.user_instructions import (
                            detect_instruction_intent as _detect_instruction_intent,
                        )
                        _instruction_intent = _detect_instruction_intent(effective_text)
                    except Exception:
                        _instruction_intent = None
                    if _instruction_intent is not None:
                        try:
                            memory_enabled = bool(config_manager.get_path("spark.memory.enabled"))
                            shadow_mode = bool(config_manager.get_path("spark.memory.shadow_mode"))
                            if memory_enabled and not shadow_mode:
                                from spark_intelligence.memory.generic_observations import (
                                    detect_telegram_generic_deletion as _detect_generic_memory_deletion,
                                )

                                if _detect_generic_memory_deletion(effective_text) is not None:
                                    _instruction_intent = None
                        except Exception:
                            pass
                    try:
                        memory_enabled = bool(config_manager.get_path("spark.memory.enabled"))
                        shadow_mode = bool(config_manager.get_path("spark.memory.shadow_mode"))
                        if memory_enabled and not shadow_mode:
                            from spark_intelligence.memory.generic_observations import (
                                detect_telegram_generic_observation as _detect_generic_memory_observation,
                            )

                            _generic_memory_observation = _detect_generic_memory_observation(effective_text)
                    except Exception:
                        _generic_memory_observation = None
                    try:
                        from spark_intelligence.mission_control import (
                            looks_like_mission_control_query as _looks_like_mission_control_query,
                        )

                        _mission_control_query = _looks_like_mission_control_query(effective_text)
                    except Exception:
                        _mission_control_query = False
                    try:
                        from spark_intelligence.schedule_bridge import (
                            detect_schedule_intent as _detect_schedule_intent,
                            detect_delete_intent as _detect_delete_intent,
                            peek_pending_delete as _peek_pending_delete,
                            is_confirmation_yes as _is_confirm_yes,
                            is_confirmation_no as _is_confirm_no,
                            format_schedule_list_from_spawner as _fmt_schedules,
                            fetch_schedules as _fetch_schedules,
                            match_schedules as _match_schedules,
                            arm_pending_delete as _arm_pending_delete,
                            clear_pending_delete as _clear_pending_delete,
                            delete_schedule_via_spawner as _delete_schedule,
                            format_delete_prompt as _fmt_delete_prompt,
                            format_delete_ambiguous as _fmt_delete_ambiguous,
                            format_delete_not_found as _fmt_delete_notfound,
                            format_delete_success as _fmt_delete_success,
                            format_delete_cancelled as _fmt_delete_cancelled,
                        )
                        _schedule_intent = _detect_schedule_intent(effective_text)
                        _delete_intent = _detect_delete_intent(effective_text)
                        _pending_delete = _peek_pending_delete(str(normalized.telegram_user_id))
                        _confirmation_yes = _is_confirm_yes(effective_text)
                        _confirmation_no = _is_confirm_no(effective_text)
                    except Exception:
                        _schedule_intent = None
                        _delete_intent = None
                        _pending_delete = None
                _shortcircuited = False
                # Confirmation of a pending delete takes priority over anything
                if _pending_delete is not None and (_confirmation_yes or _confirmation_no):
                    _shortcircuited = True
                    if _confirmation_yes:
                        sid = str(_pending_delete.get("schedule_id") or "")
                        try:
                            ok = _delete_schedule(sid) if sid else False
                        except Exception:
                            ok = False
                        if ok:
                            outbound_text = _fmt_delete_success(_pending_delete)
                        else:
                            outbound_text = f"Tried to kill {sid}, but the scheduler rejected it. Run 'show my schedules' to check."
                    else:
                        outbound_text = _fmt_delete_cancelled()
                    _clear_pending_delete(str(normalized.telegram_user_id))
                    trace_ref = None
                    bridge_mode = "schedule_confirm_shortcircuit"
                    attachment_context = None
                    routing_decision = "schedule_confirm_shortcircuit"
                    active_chip_key = None
                    active_chip_task_type = None
                    active_chip_evaluate_used = False
                    evidence_summary = None
                    bridge_result = None
                elif _delete_intent is not None and _instruction_intent is None:
                    _shortcircuited = True
                    try:
                        existing = _fetch_schedules()
                    except Exception:
                        existing = []
                    matches = _match_schedules(existing, _delete_intent.get("hints", {}))
                    if len(matches) == 0:
                        outbound_text = _fmt_delete_notfound(_delete_intent.get("hints", {}))
                    elif len(matches) == 1:
                        _arm_pending_delete(str(normalized.telegram_user_id), matches[0])
                        outbound_text = _fmt_delete_prompt(matches[0])
                    else:
                        outbound_text = _fmt_delete_ambiguous(matches)
                    trace_ref = None
                    bridge_mode = "schedule_delete_shortcircuit"
                    attachment_context = None
                    routing_decision = "schedule_delete_shortcircuit"
                    active_chip_key = None
                    active_chip_task_type = None
                    active_chip_evaluate_used = False
                    evidence_summary = None
                    bridge_result = None
                elif _instruction_intent is None:
                    _board_intent = None
                    _loop_intent = None
                    try:
                        from spark_intelligence.mission_bridge import (
                            detect_board_intent as _detect_board_intent,
                            has_live_missions as _has_live_missions,
                            format_board_from_spawner as _fmt_board,
                        )
                        _board_intent = _detect_board_intent(effective_text)
                    except Exception:
                        _board_intent = None
                    try:
                        from spark_intelligence.loop_bridge import (
                            detect_loop_invoke_intent as _detect_loop_intent,
                        )
                        _loop_intent = _detect_loop_intent(effective_text)
                    except Exception:
                        _loop_intent = None
                    _chip_create_intent = None
                    _schedule_create_intent = None
                    try:
                        from spark_intelligence.chip_create_bridge import (
                            detect_chip_create_intent as _detect_chip_create_intent,
                            format_chip_create_suggestion as _fmt_chip_create,
                        )
                        _chip_create_intent = _detect_chip_create_intent(effective_text)
                    except Exception:
                        _chip_create_intent = None
                    try:
                        from spark_intelligence.schedule_create_bridge import (
                            detect_schedule_create_intent as _detect_sch_create_intent,
                            format_schedule_create_suggestion as _fmt_sch_create,
                        )
                        _schedule_create_intent = _detect_sch_create_intent(effective_text)
                    except Exception:
                        _schedule_create_intent = None
                    if _generic_memory_observation is not None or _mission_control_query:
                        pass
                    elif _schedule_create_intent is not None:
                        _shortcircuited = True
                        outbound_text = _fmt_sch_create(_schedule_create_intent)
                        trace_ref = None
                        bridge_mode = "schedule_create_suggest_shortcircuit"
                        attachment_context = None
                        routing_decision = "schedule_create_suggest_shortcircuit"
                        active_chip_key = None
                        active_chip_task_type = None
                        active_chip_evaluate_used = False
                        evidence_summary = None
                        bridge_result = None
                    elif _chip_create_intent is not None:
                        _shortcircuited = True
                        outbound_text = _fmt_chip_create(_chip_create_intent.get("brief", ""))
                        trace_ref = None
                        bridge_mode = "chip_create_suggest_shortcircuit"
                        attachment_context = None
                        routing_decision = "chip_create_suggest_shortcircuit"
                        active_chip_key = None
                        active_chip_task_type = None
                        active_chip_evaluate_used = False
                        evidence_summary = None
                        bridge_result = None
                    elif _loop_intent is not None:
                        _shortcircuited = True
                        chip = _loop_intent.get("chip_key")
                        rounds = _loop_intent.get("rounds", 1)
                        outbound_text = (
                            f"Got it - you want to loop {chip} for {rounds} round"
                            f"{'' if rounds == 1 else 's'}. "
                            f"Tap /loop {chip} {rounds} to fire it (takes a few minutes).\n"
                            f"I don't run loops from plain chat because they're long-running "
                            f"and I'd rather hand back control to you than block."
                        )
                        trace_ref = None
                        bridge_mode = "loop_suggest_shortcircuit"
                        attachment_context = None
                        routing_decision = "loop_suggest_shortcircuit"
                        active_chip_key = None
                        active_chip_task_type = None
                        active_chip_evaluate_used = False
                        evidence_summary = None
                        bridge_result = None
                    elif _board_intent is not None:
                        _shortcircuited = True
                        outbound_text = _fmt_board()
                        trace_ref = None
                        bridge_mode = "board_shortcircuit"
                        attachment_context = None
                        routing_decision = "board_shortcircuit"
                        active_chip_key = None
                        active_chip_task_type = None
                        active_chip_evaluate_used = False
                        evidence_summary = None
                        bridge_result = None
                    elif _schedule_intent is not None:
                        _shortcircuited = True
                        # "what's running" is ambiguous: if there are live
                        # missions, show the board; else fall through to the
                        # schedule list. Per the conversational-intent-design
                        # skill, prioritize missions > schedules for ambiguous
                        # "what's running" queries.
                        try:
                            show_board = re.search(r"\brunning\b", effective_text, re.IGNORECASE) and _has_live_missions()
                        except Exception:
                            show_board = False
                        if show_board:
                            outbound_text = _fmt_board()
                            bridge_mode = "board_shortcircuit"
                            routing_decision = "board_shortcircuit"
                        else:
                            try:
                                outbound_text = _fmt_schedules()
                            except Exception as exc:
                                outbound_text = f"Could not reach scheduler: {exc}"
                            bridge_mode = "schedule_list_shortcircuit"
                            routing_decision = "schedule_list_shortcircuit"
                        trace_ref = None
                        attachment_context = None
                        active_chip_key = None
                        active_chip_task_type = None
                        active_chip_evaluate_used = False
                        evidence_summary = None
                        bridge_result = None
                # Disambiguation: if no specific intent matched but the
                # message clearly references agent surfaces, ask a single
                # clarifying question rather than falling through to web
                # search / chip routing / bridge.
                if (
                    not _shortcircuited
                    and _instruction_intent is None
                    and _generic_memory_observation is None
                    and not _mission_control_query
                    and not voice_answer_requested_for_bridge
                    and effective_text
                ):
                    try:
                        from spark_intelligence.disambiguation_bridge import (
                            detect_ambiguous_intent as _detect_ambiguous,
                            format_clarifying_question as _fmt_clarify,
                        )
                        _ambiguous = _detect_ambiguous(effective_text)
                    except Exception:
                        _ambiguous = None
                    if _ambiguous is not None:
                        _shortcircuited = True
                        outbound_text = _fmt_clarify(_ambiguous)
                        trace_ref = None
                        bridge_mode = "disambiguation_shortcircuit"
                        attachment_context = None
                        routing_decision = "disambiguation_shortcircuit"
                        active_chip_key = None
                        active_chip_task_type = None
                        active_chip_evaluate_used = False
                        evidence_summary = None
                        bridge_result = None
                if not _shortcircuited and _instruction_intent is not None:
                    _shortcircuited = True
                    outbound_text = _maybe_capture_user_instruction(
                        state_db=state_db,
                        user_message=effective_text,
                        external_user_id=normalized.telegram_user_id,
                        reply_text="",
                        bridge_mode="user_instruction_shortcircuit",
                        routing_decision="user_instruction_shortcircuit",
                    )
                    trace_ref = None
                    bridge_mode = "user_instruction_shortcircuit"
                    attachment_context = None
                    routing_decision = "user_instruction_shortcircuit"
                    active_chip_key = None
                    active_chip_task_type = None
                    active_chip_evaluate_used = False
                    evidence_summary = None
                    bridge_result = None
                if not _shortcircuited:
                    bridge_result = build_researcher_reply(
                        config_manager=config_manager,
                        state_db=state_db,
                        request_id=request_id,
                        agent_id=resolution.agent_id,
                        human_id=resolution.human_id,
                        session_id=resolution.session_id,
                        channel_kind="telegram",
                        user_message=effective_text,
                    )
                    record_researcher_bridge_result(state_db=state_db, result=bridge_result)
                    spark_character_reply = _maybe_spark_character_reply(
                        config_manager=config_manager,
                        user_message=effective_text,
                        bridge_mode=bridge_result.mode,
                        routing_decision=bridge_result.routing_decision,
                        surface=(
                            "voice"
                            if _voice_reply_enabled_for_user(
                                state_db=state_db,
                                external_user_id=normalized.telegram_user_id,
                            )
                            else "telegram"
                        ),
                    )
                    delivery_bridge_mode, delivery_routing_decision, delivery_primary_route = _spark_character_delivery_route(
                        bridge_mode=bridge_result.mode,
                        routing_decision=bridge_result.routing_decision,
                        spark_character_reply=spark_character_reply,
                    )
                    shaped_bridge_reply = spark_character_reply or _shape_telegram_bridge_reply(
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
                    bridge_mode = delivery_bridge_mode
                    attachment_context = bridge_result.attachment_context
                    routing_decision = delivery_routing_decision
                    active_chip_key = bridge_result.active_chip_key
                    active_chip_task_type = bridge_result.active_chip_task_type
                    active_chip_evaluate_used = bridge_result.active_chip_evaluate_used
                    evidence_summary = bridge_result.evidence_summary
                    outbound_text = _maybe_append_verbatim_chip_block(
                        user_message=effective_text,
                        reply_text=outbound_text,
                        raw_chip_metrics=getattr(bridge_result, "raw_chip_metrics", []) or [],
                    )
                    outbound_text = _maybe_capture_user_instruction(
                        state_db=state_db,
                        user_message=effective_text,
                        external_user_id=normalized.telegram_user_id,
                        reply_text=outbound_text,
                        bridge_mode=bridge_result.mode,
                        routing_decision=bridge_result.routing_decision,
                    )
                    outbound_text = _maybe_save_reply_as_draft(
                        state_db=state_db,
                        external_user_id=normalized.telegram_user_id,
                        session_id=resolution.session_id,
                        chip_used=bridge_result.active_chip_key,
                        reply_text=outbound_text,
                        user_message=effective_text,
                    )
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
        voice_origin_reply = normalized.message_kind in {"voice", "audio"} and bool(transcript_text)
        voice_reply_state_enabled = _voice_reply_enabled_for_user(
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
        )
        voice_bridge_requested = (
            voice_origin_reply
            or voice_answer_requested_for_bridge
            or (respect_voice_reply_state_for_bridge and voice_reply_state_enabled)
        )
        if not simulation and voice_bridge_requested:
            outbound_text = _repair_voice_delivery_denial(outbound_text, voice_available=True)
        if not simulation and voice_bridge_requested and bridge_voice_media is None:
            spoken_text = _prepare_voice_reply_text(outbound_text)
            if spoken_text:
                try:
                    voice_payload = _synthesize_telegram_voice_reply(
                        config_manager=config_manager,
                        state_db=state_db,
                        human_id=resolution.human_id,
                        agent_id=resolution.agent_id,
                        text=spoken_text,
                    )
                    bridge_voice_media = _bridge_voice_media_from_payload(voice_payload)
                except Exception as exc:  # pragma: no cover - exercised by live adapter failures
                    bridge_voice_error = str(exc)
    else:
        trace_ref = None
        bridge_mode = None
        attachment_context = None
        routing_decision = None
        active_chip_key = None
        active_chip_task_type = None
        active_chip_evaluate_used = False
        evidence_summary = None
        delivery_primary_route = {}
    sanitized_outbound_text, _outbound_actions = _prepare_simulate_outbound(
        config_manager=config_manager,
        state_db=state_db,
        text=outbound_text,
        bridge_mode=bridge_mode,
        request_id=request_id,
        trace_ref=trace_ref,
        session_id=resolution.session_id,
    )
    if bridge_voice_media is not None:
        voice_caption_text = _strip_delivery_chunk_markers(sanitized_outbound_text)
        if voice_caption_text != sanitized_outbound_text:
            sanitized_outbound_text = voice_caption_text
            _outbound_actions = ["strip_voice_caption_chunk_markers", *_outbound_actions]
    detail = {
        "request_id": request_id,
        "simulation": simulation,
        "origin_surface": origin_surface,
        "telegram_user_id": normalized.telegram_user_id,
        "chat_id": normalized.chat_id,
        "session_id": resolution.session_id,
        "human_id": resolution.human_id,
        "agent_id": resolution.agent_id,
        "message_text": effective_text,
        "message_kind": normalized.message_kind,
        "transcript_text": transcript_text,
        "response_text": sanitized_outbound_text,
        "trace_ref": trace_ref,
        "bridge_mode": bridge_mode,
        "routing_decision": routing_decision,
        **delivery_primary_route,
        "active_chip_key": active_chip_key,
        "active_chip_task_type": active_chip_task_type,
        "active_chip_evaluate_used": active_chip_evaluate_used,
        "attachment_context": attachment_context,
        "guardrail_actions": _outbound_actions,
    }
    if runtime_command_name:
        detail["runtime_command"] = runtime_command_name
    if runtime_command_metadata:
        detail["runtime_command_metadata"] = runtime_command_metadata
    voice_timing = (
        dict(media_input.get("voice_timing"))
        if isinstance(media_input, dict) and isinstance(media_input.get("voice_timing"), dict)
        else {}
    )
    if bridge_voice_media is not None:
        detail["voice_media"] = bridge_voice_media
        synthesis_ms = bridge_voice_media.get("synthesis_ms")
        if synthesis_ms is not None:
            voice_timing["synthesis_ms"] = synthesis_ms
    if bridge_voice_error:
        detail["voice_error"] = bridge_voice_error
    if voice_timing:
        detail["voice_timing"] = voice_timing
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
                **delivery_primary_route,
                "evidence_summary": evidence_summary,
                "attachment_context": attachment_context,
                "active_chip_key": active_chip_key,
                "active_chip_task_type": active_chip_task_type,
                "active_chip_evaluate_used": active_chip_evaluate_used,
                "response_preview": _preview_text(sanitized_outbound_text),
                "response_length": len(sanitized_outbound_text),
                "user_message_preview": _preview_text(effective_text) if effective_text else None,
                "user_message_length": len(effective_text) if effective_text else None,
                "delivery_ok": True,
                "delivery_error": None,
                "guardrail_actions": _outbound_actions,
                "simulation": simulation,
                "origin_surface": origin_surface,
                "request_id": request_id,
                "runtime_command": runtime_command_name,
                "runtime_command_metadata": runtime_command_metadata or None,
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
                user_message=normalized.text,
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
                user_message=normalized.text,
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
                user_message=effective_text,
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
        voice_answer_requested_for_bridge = False
        voice_answer_request = _extract_voice_answer_request(effective_text)
        if voice_answer_request:
            effective_text = voice_answer_request
            voice_answer_requested_for_bridge = True

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
            runtime_command_metadata = _runtime_command_trace_metadata(command_result)
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
                    "runtime_command_metadata": runtime_command_metadata,
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
                force_voice=bool(command_result.get("force_voice", False)) or voice_origin_reply or voice_answer_requested_for_bridge,
                voice_text=(
                    str(command_result.get("voice_text")).strip()
                    if command_result.get("voice_text") is not None
                    else (outbound_text if voice_origin_reply or voice_answer_requested_for_bridge else None)
                ),
                respect_voice_reply_state=bool(command_result.get("respect_voice_reply_state", True)),
                user_message=effective_text,
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
                    "runtime_command_metadata": runtime_command_metadata,
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
                    "runtime_command_metadata": runtime_command_metadata or None,
                    **_build_voice_trace_fields(media_input=media_input, transcript_text=transcript_text),
                },
            )
            continue

        # P2-13: enter the v2 onboarding state machine whenever the
        # pairing welcome is still pending (fresh pair, existing
        # behavior) OR whenever the user already has a saved persona
        # profile but no in-progress onboarding state blob. The second
        # branch powers the P2-12 one-tap skip offer for existing
        # users. Q-H of
        # docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11.
        onboarding_eligible = pairing_welcome_pending(
            state_db=state_db,
            channel_id="telegram",
            external_user_id=normalized.telegram_user_id,
        ) or agent_has_reonboard_candidate(
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            state_db=state_db,
        )
        onboarding_result = maybe_handle_agent_persona_onboarding_turn(
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            user_message=effective_text,
            state_db=state_db,
            source_surface="telegram",
            source_ref=run.request_id,
            start_if_eligible=onboarding_eligible,
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
                user_message=effective_text,
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
        spark_character_reply = _maybe_spark_character_reply(
            config_manager=config_manager,
            user_message=effective_text,
            bridge_mode=bridge_result.mode,
            routing_decision=bridge_result.routing_decision,
            surface=(
                "voice"
                if _voice_reply_enabled_for_user(
                    state_db=state_db,
                    external_user_id=normalized.telegram_user_id,
                )
                else "telegram"
            ),
        )
        delivery_bridge_mode, delivery_routing_decision, delivery_primary_route = _spark_character_delivery_route(
            bridge_mode=bridge_result.mode,
            routing_decision=bridge_result.routing_decision,
            spark_character_reply=spark_character_reply,
        )
        shaped_bridge_reply = spark_character_reply or _shape_telegram_bridge_reply(
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
        outbound_text = _maybe_append_verbatim_chip_block(
            user_message=effective_text,
            reply_text=outbound_text,
            raw_chip_metrics=getattr(bridge_result, "raw_chip_metrics", []) or [],
        )
        outbound_text = _maybe_capture_user_instruction(
            state_db=state_db,
            user_message=effective_text,
            external_user_id=normalized.telegram_user_id,
            reply_text=outbound_text,
            bridge_mode=bridge_result.mode,
            routing_decision=bridge_result.routing_decision,
        )
        outbound_text = _maybe_save_reply_as_draft(
            state_db=state_db,
            external_user_id=normalized.telegram_user_id,
            session_id=resolution.session_id,
            chip_used=bridge_result.active_chip_key,
            reply_text=outbound_text,
            user_message=effective_text,
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
            bridge_mode=delivery_bridge_mode,
            routing_decision=delivery_routing_decision,
            active_chip_key=bridge_result.active_chip_key,
            active_chip_task_type=bridge_result.active_chip_task_type,
            run_id=run.run_id,
            request_id=run.request_id,
            trace_ref=bridge_result.trace_ref,
            output_keepability=bridge_result.output_keepability,
            promotion_disposition=bridge_result.promotion_disposition,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            force_voice=voice_origin_reply or voice_answer_requested_for_bridge,
            voice_text=outbound_text if voice_origin_reply or voice_answer_requested_for_bridge else None,
            user_message=effective_text,
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
                "bridge_mode": delivery_bridge_mode,
                "routing_decision": delivery_routing_decision,
                **delivery_primary_route,
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
                "bridge_mode": delivery_bridge_mode,
                "routing_decision": delivery_routing_decision,
                **delivery_primary_route,
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
    tts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        **_build_voice_chip_payload(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        ),
        "text": text,
    }
    resolved_tts = _telegram_voice_tts_override_from_env()
    if tts:
        env_provider_id = str((resolved_tts or {}).get("provider_id") or "").strip().lower()
        explicit_provider_id = str(tts.get("provider_id") or "").strip().lower()
        if env_provider_id and explicit_provider_id and env_provider_id != explicit_provider_id:
            resolved_tts = tts
        else:
            resolved_tts = {**(resolved_tts or {}), **tts}
    if resolved_tts:
        payload["tts"] = resolved_tts
    voice_profile_status: dict[str, Any] | None = None
    voice_status_profile = _telegram_voice_status_profile()
    if voice_status_profile:
        voice_profile_status = _telegram_voice_profile_fingerprint_status(voice_status_profile)
        payload["voice_profile_status"] = voice_profile_status
    synthesis_started = perf_counter()
    execution = run_first_chip_hook_supporting(
        config_manager,
        hook="voice.speak",
        payload=payload,
    )
    synthesis_ms = int((perf_counter() - synthesis_started) * 1000)
    if execution is None:
        raise RuntimeError("No attached chip supports `voice.speak`. Attach and activate `spark-voice-comms` first.")
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
    payload = {
        "audio_bytes": base64.b64decode(audio_base64),
        "mime_type": mime_type,
        "filename": str((result or {}).get("filename") or "").strip()
        or (
            f"telegram-reply-{uuid4().hex[:8]}.mp3"
            if "mpeg" in mime_type or "mp3" in mime_type
            else f"telegram-reply-{uuid4().hex[:8]}.ogg"
            if "ogg" in mime_type or "opus" in mime_type
            else f"telegram-reply-{uuid4().hex[:8]}.audio"
        ),
        "provider_id": str((result or {}).get("provider_id") or "").strip() or None,
        "voice_id": str((result or {}).get("voice_id") or "").strip() or None,
        "voice_compatible": bool((result or {}).get("voice_compatible")),
        "spoken_text": text,
        "synthesis_ms": synthesis_ms,
    }
    profile_status = voice_profile_status
    if isinstance(profile_status, dict):
        payload["voice_profile_fingerprint"] = profile_status.get("fingerprint")
        payload["voice_profile_fingerprint_status"] = profile_status.get("status")
        payload["voice_profile_fingerprint_fields"] = profile_status.get("fields")
    payload = _apply_telegram_voice_effect_from_env(payload)
    return _convert_voice_payload_for_telegram_if_available(payload)


def _first_nonempty_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _telegram_voice_profile_registry_path() -> Path:
    configured = str(os.environ.get("SPARK_TELEGRAM_VOICE_PROFILE_REGISTRY") or "").strip()
    if configured:
        return Path(configured).expanduser()
    home = Path(str(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~")).expanduser()
    return home / ".spark" / "config" / "telegram-voice-profiles.json"


def _telegram_voice_registry_profile() -> dict[str, Any]:
    registry_path = _telegram_voice_profile_registry_path()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, dict):
        return {}
    profile_key = str(os.environ.get("SPARK_TELEGRAM_PROFILE") or "default").strip() or "default"
    profile = profiles.get(profile_key)
    if not isinstance(profile, dict) and profile_key != "default":
        profile = profiles.get("default")
    if not isinstance(profile, dict):
        return {}
    return {str(key): value for key, value in profile.items()}


def _telegram_voice_tts_override_from_env() -> dict[str, Any] | None:
    registry_profile = _telegram_voice_registry_profile()
    provider_id = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_PROVIDER", "SPARK_TELEGRAM_TTS_PROVIDER")
    voice_id = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_VOICE_ID", "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_VOICE_ID")
    voice_name = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_VOICE_NAME", "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_VOICE_NAME")
    model_id = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_MODEL_ID", "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_MODEL_ID")
    base_url = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_BASE_URL", "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_BASE_URL")
    output_format = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_OUTPUT_FORMAT")
    secret_env_ref = _first_nonempty_env("SPARK_TELEGRAM_VOICE_TTS_SECRET_ENV_REF")
    tts: dict[str, Any] = {}
    for output_key, registry_key, env_value in (
        ("provider_id", "provider_id", provider_id),
        ("voice_id", "voice_id", voice_id),
        ("voice_name", "voice_name", voice_name),
        ("model_id", "model_id", model_id),
        ("base_url", "base_url", base_url),
        ("output_format", "output_format", output_format),
        ("secret_env_ref", "secret_env_ref", secret_env_ref),
    ):
        value = env_value or str(registry_profile.get(registry_key) or "").strip()
        if value:
            tts[output_key] = value
    voice_settings = _telegram_voice_settings_override_from_env()
    if not voice_settings:
        registry_settings = registry_profile.get("voice_settings")
        if isinstance(registry_settings, dict):
            voice_settings = dict(registry_settings)
    if voice_settings:
        tts["voice_settings"] = voice_settings
    return tts or None


def _telegram_voice_settings_override_from_env() -> dict[str, Any] | None:
    settings: dict[str, Any] = {}
    for key, env_name in {
        "stability": "SPARK_TELEGRAM_VOICE_TTS_STABILITY",
        "similarity_boost": "SPARK_TELEGRAM_VOICE_TTS_SIMILARITY_BOOST",
        "style": "SPARK_TELEGRAM_VOICE_TTS_STYLE",
        "speed": "SPARK_TELEGRAM_VOICE_TTS_SPEED",
    }.items():
        value = _optional_float_env(env_name)
        if value is not None:
            settings[key] = value
    speaker_boost = _optional_bool_env("SPARK_TELEGRAM_VOICE_TTS_USE_SPEAKER_BOOST")
    if speaker_boost is not None:
        settings["use_speaker_boost"] = speaker_boost
    return settings or None


def _telegram_voice_effect_from_env() -> str:
    effect = str(os.environ.get("SPARK_TELEGRAM_VOICE_AUDIO_EFFECT") or "").strip().lower()
    if not effect:
        effect = str(_telegram_voice_registry_profile().get("audio_effect") or "").strip().lower()
    return effect if effect in {"parrot"} else ""


def _apply_telegram_voice_effect_from_env(payload: dict[str, Any]) -> dict[str, Any]:
    effect = _telegram_voice_effect_from_env()
    if effect != "parrot":
        return payload
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return payload
    audio_bytes = bytes(payload.get("audio_bytes") or b"")
    if not audio_bytes:
        return payload
    filename = str(payload.get("filename") or "telegram-reply.audio").strip() or "telegram-reply.audio"
    input_suffix = Path(filename).suffix or ".ogg"
    try:
        with tempfile.TemporaryDirectory(prefix="spark-voice-effect-") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / f"input{input_suffix}"
            output_path = temp_path / "parrot.ogg"
            input_path.write_bytes(audio_bytes)
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(input_path),
                    "-af",
                    (
                        "asetrate=48000*1.10,aresample=48000,atempo=0.99,"
                        "highpass=f=320,lowpass=f=7000,"
                        "equalizer=f=1800:t=q:w=1:g=1.2,"
                        "equalizer=f=3600:t=q:w=1:g=2.0,"
                        "equalizer=f=5600:t=q:w=1:g=0.6,"
                        "tremolo=f=6:d=0.035,"
                        "acompressor=threshold=-18dB:ratio=1.45:attack=6:release=95"
                    ),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "64k",
                    "-vbr",
                    "on",
                    "-application",
                    "voip",
                    str(output_path),
                ],
                check=True,
            )
            processed = output_path.read_bytes()
            if not processed:
                return payload
            stem = Path(filename).stem or "telegram-reply"
            return {
                **payload,
                "audio_bytes": processed,
                "mime_type": "audio/ogg",
                "filename": f"{stem}-parrot.ogg",
                "voice_compatible": True,
                "audio_effect": "parrot",
                "audio_effect_version": TELEGRAM_PARROT_EFFECT_VERSION,
            }
    except Exception:
        return payload


def _optional_float_env(name: str) -> float | None:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _optional_bool_env(name: str) -> bool | None:
    value = str(os.environ.get(name) or "").strip().lower()
    if not value:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _voice_tts_override_from_text(text: str) -> dict[str, str] | None:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return None
    if re.search(r"\b(?:kokoro|kokoro[-\s]*onnx|local[-\s]*kokoro)\b", normalized):
        return {"provider_id": "kokoro"}
    if re.search(r"\b(?:gpt[-\s]*realtime[-\s]*2|openai[-\s]*realtime|realtime[-\s]*2)\b", normalized):
        return {"provider_id": "openai-realtime"}
    if re.search(r"\beleven\s*labs\b|\belevenlabs\b", normalized):
        return {"provider_id": "elevenlabs"}
    return None


def _telegram_voice_payload_is_voice_compatible(payload: dict[str, Any]) -> bool:
    mime_type = str(payload.get("mime_type") or "").strip().lower()
    filename = str(payload.get("filename") or "").strip().lower()
    if bool(payload.get("voice_compatible")):
        return True
    if mime_type in {"audio/ogg", "audio/opus"}:
        return True
    return filename.endswith(".ogg") or filename.endswith(".opus")


def _convert_voice_payload_for_telegram_if_available(payload: dict[str, Any]) -> dict[str, Any]:
    if _telegram_voice_payload_is_voice_compatible(payload):
        return payload
    mime_type = str(payload.get("mime_type") or "").strip().lower()
    filename = str(payload.get("filename") or "").strip()
    if mime_type not in {"audio/wav", "audio/x-wav"} and not filename.lower().endswith(".wav"):
        return payload
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return payload
    try:
        with tempfile.TemporaryDirectory(prefix="spark-voice-") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.wav"
            output_path = temp_path / "output.ogg"
            input_path.write_bytes(bytes(payload["audio_bytes"]))
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(input_path),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "64k",
                    "-vbr",
                    "on",
                    "-application",
                    "voip",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            converted = dict(payload)
            converted["audio_bytes"] = output_path.read_bytes()
            converted["mime_type"] = "audio/ogg"
            converted["filename"] = f"{Path(filename or 'telegram-reply').stem}.ogg"
            converted["voice_compatible"] = True
            return converted
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError):
        return payload


def _bridge_voice_media_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "audio_base64": base64.b64encode(payload["audio_bytes"]).decode("ascii"),
        "mime_type": str(payload.get("mime_type") or "audio/mpeg"),
        "filename": str(payload.get("filename") or "telegram-reply.audio"),
        "voice_compatible": _telegram_voice_payload_is_voice_compatible(payload),
        "provider_id": payload.get("provider_id"),
        "voice_id": payload.get("voice_id"),
        "spoken_text": str(payload.get("spoken_text") or ""),
        "synthesis_ms": payload.get("synthesis_ms"),
        "voice_profile_fingerprint": payload.get("voice_profile_fingerprint"),
        "voice_profile_fingerprint_status": payload.get("voice_profile_fingerprint_status"),
    }


def _prepare_voice_reply_text(text: str, *, max_chars: int = 900) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""
    lines: list[str] = []
    for raw_line in value.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("next:"):
            continue
        line = re.sub(r"^[\-\*\u2022]+\s*", "", line)
        lines.append(line)
    if not lines:
        return ""
    spoken = " ".join(lines)
    spoken = spoken.replace(" -- ", ", ")
    spoken = re.sub(r"\s*[:;]\s*", ". ", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    if len(spoken) > max_chars:
        window = spoken[:max_chars]
        last_break = max(window.rfind(". "), window.rfind("? "), window.rfind("! "), window.rfind(", "))
        if last_break >= max_chars // 2:
            spoken = window[: last_break + 1].strip()
        else:
            spoken = window.rstrip(" ,;:-")
    if spoken and spoken[-1] not in ".!?":
        spoken = f"{spoken}."
    return spoken


def _repair_voice_delivery_denial(text: str, *, voice_available: bool) -> str:
    if not voice_available:
        return text
    raw = str(text or "").strip()
    lowered = raw.lower()
    denial_markers = (
        "i can't send voice",
        "i cannot send voice",
        "i can't send audio",
        "i cannot send audio",
        "i don't have the ability to generate and send audio",
        "i do not have the ability to generate and send audio",
        "i can only generate text",
        "don't have a path to push audio",
        "do not have a path to push audio",
        "can't trigger it directly",
        "cannot trigger it directly",
        "sending a voice reply isn't something i can trigger",
        "sending a voice reply is not something i can trigger",
        "wire up the voice.speak hook",
        "voice-speak hook exists",
        "supported surface or session",
        "different surface or setup step",
        "route it through the telegram bot api",
        "sendvoice endpoint",
    )
    denial_pattern = re.search(
        r"\b(?:can(?:not|'t)|don't|do not|unable|not able|only generate text|isn't something i can|is not something i can)\b"
        r".{0,180}\b(?:send|generate|trigger|push|deliver|outbound|voice|audio|speech|spoken|surface)\b",
        lowered,
    )
    if not any(marker in lowered for marker in denial_markers) and not denial_pattern:
        return text
    if not any(marker in lowered for marker in ("voice", "audio", "tts", "sendvoice")):
        return text
    return "Voice is working here. I can send spoken replies through this Telegram chat now."


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
    user_message: str | None = None,
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
    voice_requested = force_voice or (
        respect_voice_reply_state
        and _voice_reply_enabled_for_user(state_db=state_db, external_user_id=telegram_user_id)
    )
    if voice_requested:
        filtered_text = _repair_voice_delivery_denial(filtered_text, voice_available=True)
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
    delivery_medium = "text"
    voice_error: str | None = None
    voice_payload: dict[str, Any] | None = None
    if voice_requested:
        spoken_source = str(voice_text or guarded["text"])
        if voice_text is not None:
            spoken_source = _apply_think_visibility(
                state_db=state_db,
                external_user_id=telegram_user_id,
                text=spoken_source,
            )
            spoken_source = _strip_internal_swarm_recommendation(spoken_source)
        spoken_text = _prepare_voice_reply_text(spoken_source)
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
            voice_caption_text = _strip_delivery_chunk_markers(str(guarded["text"]))
            if _telegram_voice_payload_is_voice_compatible(voice_payload):
                client.send_voice(
                    chat_id=chat_id,
                    voice_bytes=voice_payload["audio_bytes"],
                    filename=str(voice_payload["filename"]),
                    mime_type=str(voice_payload["mime_type"]),
                    caption=voice_caption_text if len(voice_caption_text) <= 1024 else None,
                )
            else:
                client.send_document(
                    chat_id=chat_id,
                    document_bytes=voice_payload["audio_bytes"],
                    filename=str(voice_payload["filename"]),
                    mime_type=str(voice_payload["mime_type"]),
                    caption=voice_caption_text if len(voice_caption_text) <= 1024 else None,
                )
        else:
            for _chunk in (guarded.get("chunks") or [guarded["text"]]):
                client.send_message(chat_id=chat_id, text=_chunk)
    except (RuntimeError, HTTPError, URLError) as exc:
        if voice_payload is not None:
            voice_error = _describe_telegram_delivery_exception(exc)
            guarded["actions"] = ["voice_delivery_fallback_to_text", *list(guarded["actions"])]
            delivery_medium = "text"
            try:
                for _chunk in (guarded.get("chunks") or [guarded["text"]]):
                    client.send_message(chat_id=chat_id, text=_chunk)
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
            "user_message_preview": _preview_text(user_message) if user_message else None,
            "user_message_length": len(user_message) if user_message else None,
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


def _strip_delivery_chunk_markers(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    return re.sub(r"(?m)^\((?:\d+)\/(?:\d+)\)\s*", "", value).strip()


def _prepare_simulate_outbound(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    text: str,
    bridge_mode: str | None,
    request_id: str | None,
    trace_ref: str | None,
    session_id: str | None,
) -> tuple[str, list[str]]:
    """Apply the same outbound text boundary used by real Telegram sends."""
    policy = _telegram_security_policy(config_manager)
    guarded = prepare_outbound_text(
        config_manager=config_manager,
        state_db=state_db,
        text=text,
        bridge_mode=bridge_mode,
        max_reply_chars=policy["max_reply_chars"],
        redact_secret_like_replies=policy["redact_secret_like_replies"],
        run_id=None,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id="telegram",
        session_id=session_id,
        actor_id="telegram_runtime",
    )
    return str(guarded["text"]), list(guarded["actions"])


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


def _is_aoc_runtime_command(lowered: str) -> bool:
    return lowered in {"/aoc", "/context", "/operating-context", "/agent-context"} or any(
        lowered.startswith(f"{prefix} ")
        for prefix in ("/aoc", "/context", "/operating-context", "/agent-context")
    )


def _telegram_route_probe_key(route_name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", str(route_name or "").strip().casefold()).strip("_-")
    return {
        "core": "spark_intelligence_builder",
        "builder": "spark_intelligence_builder",
        "spark": "spark_intelligence_builder",
        "spark_intelligence": "spark_intelligence_builder",
        "spark_intelligence_builder": "spark_intelligence_builder",
        "memory": "spark_memory",
        "spark_memory": "spark_memory",
        "researcher": "spark_researcher",
        "spark_researcher": "spark_researcher",
        "browser": "spark_browser",
        "spark_browser": "spark_browser",
        "swarm": "spark_swarm",
        "spark_swarm": "spark_swarm",
        "spawner": "spark_spawner",
        "spark_spawner": "spark_spawner",
        "local": "spark_local_work",
        "local_work": "spark_local_work",
        "spark_local_work": "spark_local_work",
    }.get(normalized)


def _render_telegram_route_probe_help(*, route_name: str) -> str:
    if route_name:
        prefix = f"Unknown route `{route_name}`.\n"
    else:
        prefix = "Route probe needs a route name.\n"
    return (
        f"{prefix}"
        "Use `/probe core`, `/probe builder`, `/probe memory`, `/probe researcher`, "
        "`/probe browser`, `/probe swarm`, or `/probe spawner`."
    )


def _render_telegram_route_probe_reply(probe: Any) -> str:
    status = str(getattr(probe, "status", "") or "unknown")
    capability_key = str(getattr(probe, "capability_key", "") or "unknown")
    lines = [
        f"Route probe: {capability_key}",
        f"Status: {status}",
    ]
    latency = getattr(probe, "route_latency_ms", None)
    if latency is not None:
        lines.append(f"Latency: {latency}ms")
    failure = str(getattr(probe, "failure_reason", "") or "").strip()
    if failure:
        lines.append(f"Reason: {_with_terminal_period(failure)}")
    summary = str(getattr(probe, "probe_summary", "") or "").strip()
    if summary:
        lines.append(f"Evidence: {_with_terminal_period(summary)}")
    lines.append("Boundary: this is route evidence for the ledger, not proof that a user task completed.")
    return "\n".join(lines)


_TELEGRAM_LEDGER_REVIEW_STATES = {"proposed", "scaffolded", "probed"}


def _is_ledger_runtime_command(lowered: str) -> bool:
    return lowered in {"/ledger", "/capabilities"} or any(
        lowered.startswith(f"{prefix} ") for prefix in ("/ledger", "/capabilities")
    )


def _telegram_short_text(value: Any, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _render_telegram_capability_ledger_review(*, config_manager: ConfigManager, requested_command: str) -> str:
    ledger = load_capability_ledger(config_manager).payload
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), dict) else {}
    review_entries = [
        entry
        for entry in entries.values()
        if isinstance(entry, dict) and str(entry.get("status") or "") in _TELEGRAM_LEDGER_REVIEW_STATES
    ]
    review_entries.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    command_args = str(requested_command or "").strip().split(maxsplit=1)
    read_only_note = ""
    if len(command_args) > 1:
        read_only_note = "Read-only: Telegram ledger review cannot activate, approve, connect credentials, or change capability state.\n"
    if not review_entries:
        return (
            f"{read_only_note}"
            "Capability ledger review (read-only)\n"
            "No proposed, scaffolded, or probed capabilities are waiting for review.\n"
            "Boundary: activation requires separate CLI ledger evidence with approval plus probe/eval refs."
        )

    lines = [f"{read_only_note}Capability ledger review (read-only)"]
    for entry in review_entries[:5]:
        packet = entry.get("proposal_packet") if isinstance(entry.get("proposal_packet"), dict) else {}
        harness = packet.get("connector_harness") if isinstance(packet.get("connector_harness"), dict) else {}
        key = str(entry.get("capability_ledger_key") or packet.get("capability_ledger_key") or "unknown")
        status = str(entry.get("status") or "unknown")
        permissions = packet.get("permissions_required") if isinstance(packet.get("permissions_required"), list) else []
        permission_text = ", ".join(str(item) for item in permissions[:3] if str(item or "").strip())
        if len(permissions) > 3:
            permission_text = f"{permission_text}, +{len(permissions) - 3} more"
        connector = str(harness.get("connector_key") or "").strip()
        authority_stage = str(harness.get("authority_stage") or "").strip()
        connector_text = f"{connector} ({authority_stage})" if connector else "none"
        evidence_count = len(entry.get("activation_evidence") or [])
        lines.extend(
            [
                f"- {key} [{status}]",
                f"  route: {_telegram_short_text(packet.get('implementation_route') or entry.get('implementation_route') or 'unknown', limit=80)}",
                f"  connector: {connector_text}",
                f"  permissions: {_telegram_short_text(permission_text or 'none', limit=140)}",
                f"  safe_probe: {_telegram_short_text(packet.get('safe_probe'), limit=140)}",
                f"  approval_boundary: {_telegram_short_text(packet.get('human_approval_boundary'), limit=140)}",
                f"  eval_or_smoke_test: {_telegram_short_text(packet.get('eval_or_smoke_test'), limit=140)}",
                f"  rollback_path: {_telegram_short_text(packet.get('rollback_path'), limit=140)}",
                f"  activation_evidence: {evidence_count}",
            ]
        )
    if len(review_entries) > 5:
        lines.append(f"...and {len(review_entries) - 5} more review entries.")
    lines.append("Boundary: review only. Telegram cannot activate capabilities; activation requires separate approval plus probe/eval evidence.")
    return "\n".join(lines)


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
    natural_memory_doctor_command = _match_natural_memory_doctor_command(normalized)
    if natural_memory_doctor_command is None:
        natural_memory_doctor_command = _match_contextual_memory_doctor_command(
            inbound_text=normalized,
            config_manager=config_manager,
            external_user_id=external_user_id,
            session_id=session_id or "",
            current_request_id=request_id or "",
        )
    style_command = _parse_style_command(normalized) or _match_natural_style_command(normalized)
    natural_voice_command = _match_natural_voice_command(normalized)
    natural_think_command = _match_natural_think_command(normalized)
    if style_command is not None:
        return _handle_style_command(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
            command=style_command["command"],
            payload=style_command.get("payload"),
        )
    if lowered in {"/self", "/introspect"}:
        capsule = build_self_awareness_capsule(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id or f"human:telegram:{external_user_id}",
            session_id=session_id or f"session:telegram:{external_user_id}",
            channel_kind="telegram",
            request_id=request_id,
            user_message=normalized,
        )
        return {
            "command": "/self",
            "reply_text": capsule.to_text(),
            "respect_voice_reply_state": True,
        }
    if _is_aoc_runtime_command(lowered):
        context = build_agent_operating_context(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id or f"human:telegram:{external_user_id}",
            session_id=session_id or f"session:telegram:{external_user_id}",
            channel_kind="telegram",
            request_id=request_id,
            user_message=normalized,
            runner_writable=None,
            runner_label="telegram runtime unknown",
        )
        return {
            "command": "/aoc" if lowered.startswith("/aoc") else "/context",
            "reply_text": context.to_text(),
            "respect_voice_reply_state": True,
        }
    if lowered == "/probe" or lowered.startswith("/probe "):
        route_name = normalized[len("/probe") :].strip()
        capability_key = _telegram_route_probe_key(route_name)
        if capability_key is None:
            return {
                "command": "/probe",
                "reply_text": _render_telegram_route_probe_help(route_name=route_name),
                "respect_voice_reply_state": True,
            }
        probe = run_route_probe_and_record(
            config_manager,
            state_db,
            capability_key=capability_key,
            actor_id="telegram_runtime",
            request_id=request_id or "",
            session_id=session_id or "",
            human_id=human_id or f"human:telegram:{external_user_id}",
        )
        return {
            "command": "/probe",
            "reply_text": _render_telegram_route_probe_reply(probe),
            "respect_voice_reply_state": True,
        }
    if _is_ledger_runtime_command(lowered):
        return {
            "command": "/ledger" if lowered.startswith("/ledger") else "/capabilities",
            "reply_text": _render_telegram_capability_ledger_review(
                config_manager=config_manager,
                requested_command=normalized,
            ),
            "respect_voice_reply_state": True,
        }
    if lowered in {"/wiki", "/wiki status"}:
        result = build_llm_wiki_status(config_manager=config_manager, state_db=state_db)
        return {
            "command": "/wiki",
            "reply_text": result.to_text(),
            "respect_voice_reply_state": True,
        }
    if lowered in {"/wiki pages", "/wiki inventory"}:
        result = build_llm_wiki_inventory(config_manager=config_manager, state_db=state_db, limit=12)
        return {
            "command": "/wiki pages",
            "reply_text": result.to_text(),
            "respect_voice_reply_state": True,
        }
    if lowered in {"/wiki candidates", "/wiki candidate inbox"}:
        result = build_llm_wiki_candidate_inbox(config_manager=config_manager, status="candidate", limit=8)
        return {
            "command": "/wiki candidates",
            "reply_text": result.to_text(),
            "respect_voice_reply_state": True,
        }
    if lowered in {"/wiki scan-candidates", "/wiki scan candidates"}:
        result = build_llm_wiki_candidate_scan(config_manager=config_manager, status="all", limit=8)
        return {
            "command": "/wiki scan-candidates",
            "reply_text": result.to_text(),
            "respect_voice_reply_state": True,
        }
    if lowered in {"/voice", "/voice status"} or natural_voice_command == ("/voice", None):
        return _run_voice_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            command="/voice",
            hook="voice.status",
            fallback_reply=_render_telegram_voice_status_reply(),
            human_id=human_id,
            agent_id=agent_id,
        )
    if lowered in {"/voice reply", "/voice reply status"} or natural_voice_command == ("/voice reply", None):
        enabled = _voice_reply_enabled_for_user(state_db=state_db, external_user_id=external_user_id)
        state_text = "on" if enabled else "off"
        return {
            "command": "/voice reply",
            "reply_text": (
                f"Voice replies are currently {state_text} for this Telegram DM. "
                "Use `/voice reply on` to send future replies as audio, `/voice reply off` to stay text-only, "
                "`/voice ask <question>` for a generated voice answer, or `/voice speak <text>` to read exact text."
            ),
            "respect_voice_reply_state": False,
        }
    if lowered in {"/voice reply on", "/voice reply off"} or natural_voice_command in {
        ("/voice reply on", None),
        ("/voice reply off", None),
    }:
        enabled = lowered == "/voice reply on" or natural_voice_command == ("/voice reply on", None)
        _set_voice_reply_enabled_for_user(
            state_db=state_db,
            external_user_id=external_user_id,
            enabled=enabled,
        )
        return {
            "command": lowered,
            "reply_text": (
                "Voice replies enabled for this Telegram DM. "
                "Next: ask a normal question, use `/voice ask <question>` for one generated voice answer, "
                "or `/voice speak <text>` to read exact text."
                if enabled
                else "Voice replies disabled for this Telegram DM. Future replies will stay text-only unless you use `/voice ask <question>` or `/voice speak <text>`."
            ),
            "respect_voice_reply_state": False,
        }
    if lowered == "/voice plan" or natural_voice_command == ("/voice plan", None):
        return _run_voice_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            command="/voice plan",
            hook="voice.plan",
            fallback_reply=_render_telegram_voice_plan_reply(),
            human_id=human_id,
            agent_id=agent_id,
        )
    if (
        lowered == "/voice install"
        or lowered.startswith("/voice install ")
        or (natural_voice_command and natural_voice_command[0] == "/voice install")
    ):
        if lowered.startswith("/voice install "):
            target = normalized[len("/voice install") :].strip().lower()
        elif natural_voice_command and natural_voice_command[0] == "/voice install":
            target = str(natural_voice_command[1] or "").strip().lower()
        else:
            target = ""
        if not target:
            return {
                "command": "/voice install",
                "reply_text": "Voice install needs a target. Use `/voice install kokoro` for local neural TTS.",
                "respect_voice_reply_state": False,
            }
        if target in {"local", "local voice", "local tts", "kokoro voice", "kokoro tts", "kokoro locally"}:
            target = "kokoro"
        return _run_voice_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            command="/voice install",
            hook="voice.install",
            fallback_reply=_render_telegram_voice_install_reply(target),
            human_id=human_id,
            agent_id=agent_id,
            payload_extra={"target": target},
        )
    if (
        lowered in {"/voice onboard", "/voice onboarding", "/voice setup"}
        or lowered.startswith("/voice onboard ")
        or lowered.startswith("/voice setup ")
        or (natural_voice_command and natural_voice_command[0] == "/voice onboard")
    ):
        if lowered.startswith("/voice onboard "):
            route = normalized[len("/voice onboard") :].strip().lower()
        elif lowered.startswith("/voice setup "):
            route = normalized[len("/voice setup") :].strip().lower()
        elif natural_voice_command and natural_voice_command[0] == "/voice onboard":
            route = str(natural_voice_command[1] or "").strip().lower()
        else:
            route = ""
        payload_extra = {"route": route} if route else None
        return _run_voice_runtime_command(
            config_manager=config_manager,
            state_db=state_db,
            command="/voice onboard",
            hook="voice.onboard",
            fallback_reply=_render_telegram_voice_onboarding_reply(route or None),
            human_id=human_id,
            agent_id=agent_id,
            payload_extra=payload_extra,
        )
    if lowered in {"/voice ask", "/voice answer"}:
        return {
            "command": "/voice ask",
            "reply_text": "Voice ask needs a question. Use `/voice ask what should I focus on today?`.",
            "respect_voice_reply_state": False,
        }
    if lowered.startswith("/voice speak") or (natural_voice_command and natural_voice_command[0] == "/voice speak"):
        speak_text = (
            normalized[len("/voice speak") :].strip()
            if lowered.startswith("/voice speak")
            else str(natural_voice_command[1] or "").strip()
        )
        if not speak_text:
            return {
                "command": "/voice speak",
                "reply_text": (
                    "Voice speak reads exact text. Use `/voice speak <words to read>`.\n\n"
                    "For a generated spoken answer, use `/voice ask <question>`."
                ),
                "respect_voice_reply_state": False,
            }
        return {
            "command": "/voice speak",
            "reply_text": "Reading that exact text as a voice reply now.\n\nFor generated wording, use `/voice ask <question>`.",
            "force_voice": True,
            "voice_text": speak_text,
            "voice_tts": _voice_tts_override_from_text(speak_text),
            "respect_voice_reply_state": False,
        }
    if lowered in {"/think", "/think on", "/think off"} or natural_think_command in {
        "/think",
        "/think on",
        "/think off",
    }:
        if lowered == "/think" or natural_think_command == "/think":
            enabled = _think_enabled_for_user(state_db=state_db, external_user_id=external_user_id)
            state_text = "on" if enabled else "off"
            return {
                "command": "/think",
                "reply_text": (
                    f"Thinking visibility is currently {state_text} for this Telegram DM. "
                    "Use `/think on` to show `<think>` blocks or `/think off` to hide them."
                ),
            }
        enabled = lowered == "/think on" or natural_think_command == "/think on"
        _set_think_enabled_for_user(
            state_db=state_db,
            external_user_id=external_user_id,
            enabled=enabled,
        )
        state_text = "enabled" if enabled else "disabled"
        return {
            "command": natural_think_command or lowered,
            "reply_text": (
                f"Thinking visibility {state_text} for this Telegram DM. "
                "This only affects `<think>` blocks in future replies."
            ),
        }
    if lowered == "/memory doctor" or lowered.startswith("/memory doctor ") or natural_memory_doctor_command:
        doctor_target = (
            _memory_doctor_target_from_slash_command(normalized)
            if lowered.startswith("/memory doctor")
            else dict(natural_memory_doctor_command or {})
        )
        doctor_topic = str(doctor_target.get("topic") or "").strip()
        doctor_request_id = str(doctor_target.get("request_id") or "").strip()
        if not doctor_request_id and doctor_target.get("request_selector") == "previous_gateway_turn":
            doctor_request_id = _memory_doctor_previous_gateway_request_id(
                config_manager=config_manager,
                external_user_id=external_user_id,
                session_id=session_id,
                current_request_id=request_id,
            )
            if not doctor_request_id:
                return {
                    "command": "/memory doctor",
                    "reply_text": "Memory Doctor could not find a previous Telegram request id to replay.",
                }
        report = run_memory_doctor(
            state_db,
            config_manager=config_manager,
            human_id=human_id or f"human:telegram:{external_user_id}",
            topic=doctor_topic or None,
            request_id=doctor_request_id or None,
            repair_requested=bool(
                natural_memory_doctor_command and natural_memory_doctor_command.get("repair_requested")
            ),
        )
        return {
            "command": "/memory doctor",
            "reply_text": report.to_telegram_text(),
            "trace_metadata": {
                "diagnosed_request_id": doctor_request_id or None,
                "diagnosed_topic": doctor_topic or None,
                "request_selector": doctor_target.get("request_selector") or None,
                "contextual_trigger_score": doctor_target.get("contextual_trigger_score"),
                "memory_doctor_ok": report.ok,
            },
        }
    chip_command = _parse_chip_command(normalized)
    if chip_command is not None:
        return _handle_telegram_chip_command(
            config_manager=config_manager,
            state_db=state_db,
            chip_command=chip_command,
            run_id=run_id,
            request_id=request_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )

    if lowered == "/swarm" or natural_swarm_command == ("/swarm", None):
        return {
            "command": "/swarm",
            "reply_text": (
                "Swarm commands: `/swarm status`, `/swarm overview`, `/swarm live`, `/swarm runtime`, "
                "`/swarm doctor`, `/swarm specializations`, `/swarm insights`, `/swarm masteries`, `/swarm upgrades`, `/swarm issues`, `/swarm inbox`, `/swarm collective`, `/swarm sync`, "
                "`/swarm paths`, `/swarm run <path_key>`, `/swarm autoloop <path_key> [rounds <n>]`, "
                "`/swarm continue <path_key> [session <id>] [rounds <n>]`, `/swarm sessions <path_key>`, "
                "`/swarm session <path_key> [latest|<session_id>]`, `/swarm rerun [path_key]`, "
                "`/swarm evaluate <task>`, `/swarm absorb <insight_id>`, `/swarm review <mastery_id> <approve|defer|reject> because <reason>`, "
                "`/swarm mode <specialization_id> <observe_only|review_required|checked_auto_merge|trusted_auto_apply>`, "
                "`/swarm deliver <upgrade_id>`, and `/swarm sync-delivery <upgrade_id>`."
            ),
        }
    if lowered == "/swarm doctor" or natural_swarm_command == ("/swarm doctor", None):
        report = swarm_doctor(config_manager, state_db)
        return {
            "command": "/swarm doctor",
            "reply_text": _render_swarm_doctor_reply(report),
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
    if lowered in {"/style history", "style history"}:
        return {"command": "/style history", "payload": None}
    if lowered in {"/style savepoints", "style savepoints"}:
        return {"command": "/style savepoints", "payload": None}
    diff_prefixes = ("/style diff ", "style diff ")
    for prefix in diff_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style diff", "payload": payload}
    if lowered in {"/style presets", "style presets"}:
        return {"command": "/style presets", "payload": None}
    if lowered in {"/style undo", "style undo", "/style rollback", "style rollback"}:
        return {"command": "/style undo", "payload": None}
    if lowered in {"/style score", "style score"}:
        return {"command": "/style score", "payload": None}
    if lowered in {"/style examples", "style examples"}:
        return {"command": "/style examples", "payload": None}
    if lowered in {"/style compare", "style compare"}:
        return {"command": "/style compare", "payload": None}
    preset_prefixes = ("/style preset ", "style preset ")
    for prefix in preset_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style preset", "payload": payload}
    savepoint_prefixes = ("/style savepoint ", "style savepoint ")
    for prefix in savepoint_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style savepoint", "payload": payload}
    restore_prefixes = ("/style restore ", "style restore ")
    for prefix in restore_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style restore", "payload": payload}
    before_after_prefixes = (
        "/style before-after ",
        "style before-after ",
        "/style before after ",
        "style before after ",
    )
    for prefix in before_after_prefixes:
        if lowered.startswith(prefix):
            payload = normalized[len(prefix) :].strip()
            return {"command": "/style before-after", "payload": payload}
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


def _style_feedback_payload_from_natural_message(inbound_text: str) -> str | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    for pattern in (
        r"^(?:that|this|the reply|your reply)\s+(?:was|felt|is)\s+(?P<payload>too\s+[a-z0-9\- ]+)$",
        r"^(?:you(?:'re| are)|it(?:'s| is))\s+(?P<payload>too\s+[a-z0-9\- ]+)$",
    ):
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if match:
            payload = " ".join(str(match.group("payload") or "").strip().split())
            if any(
                token in payload
                for token in (
                    "verbose",
                    "formal",
                    "soft",
                    "harsh",
                    "blunt",
                    "cold",
                    "playful",
                    "dry",
                    "robotic",
                    "slow",
                    "rushed",
                    "canned",
                    "generic",
                    "scripted",
                    "chatbot",
                    "polished",
                    "performative",
                    "commentary",
                    "previous",
                )
            ):
                return payload
    for pattern in (
        r"^(?P<payload>skip meta commentary)$",
        r"^(?P<payload>give the answer first)$",
        r"^(?P<payload>answer the immediately previous turn)$",
        r"^(?P<payload>be less polished)$",
        r"^(?P<payload>be less performative)$",
    ):
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if match:
            return " ".join(str(match.group("payload") or "").strip().split())
    return None


def _match_natural_style_command(inbound_text: str) -> dict[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    simplified = " ".join(re.sub(r"[^a-z0-9\s/]", " ", lowered).split())

    if simplified in {
        "show my style",
        "show me my style",
        "what is my style",
        "what s my style",
        "what is my current style",
        "what s my current style",
        "show my personality",
        "show me my personality",
        "what is my personality",
        "what s my personality",
    }:
        return {"command": "/style status", "payload": None}
    if re.match(
        r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|tell me)\s+my\s+(?:current\s+)?(?:style|personality)$",
        simplified,
        flags=re.IGNORECASE,
    ):
        return {"command": "/style status", "payload": None}
    if simplified in {
        "show my style history",
        "show me my style history",
        "show style history",
        "what style changes have you saved",
        "what feedback have i given you about style",
        "what style feedback have you saved",
    }:
        return {"command": "/style history", "payload": None}
    if simplified in {
        "show style savepoints",
        "show me style savepoints",
        "what style savepoints do i have",
        "list style savepoints",
    }:
        return {"command": "/style savepoints", "payload": None}
    for pattern in (
        r"^(?:compare|diff)\s+(?:my\s+)?style\s+(?:to|against|vs)\s+(?:savepoint\s+)?(?P<payload>.+)$",
        r"^(?:show(?:\s+me)?|preview)\s+style\s+diff\s+(?:for|against|vs)\s+(?P<payload>.+)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            payload = " ".join(str(match.group("payload") or "").strip().split())
            if payload:
                return {"command": "/style diff", "payload": payload}
    if simplified in {
        "show style presets",
        "show me style presets",
        "what style presets are available",
        "what style presets do you have",
        "list style presets",
    }:
        return {"command": "/style presets", "payload": None}
    if simplified in {
        "undo the last style change",
        "undo my last style change",
        "revert the last style change",
        "rollback the last style change",
        "undo the last preset",
    }:
        return {"command": "/style undo", "payload": None}
    for pattern in (
        r"^(?:save|create)\s+(?:a\s+)?style\s+savepoint(?:\s+(?:called|named))?\s+(?P<payload>.+)$",
        r"^(?:restore|load)\s+(?:the\s+)?style\s+savepoint(?:\s+(?:called|named))?\s+(?P<payload>.+)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            payload = " ".join(str(match.group("payload") or "").strip().split())
            if payload:
                return {"command": "/style savepoint" if pattern.startswith("^(?:save|create)") else "/style restore", "payload": payload}
    if re.match(
        r"^(?:please\s+|can you\s+)?(?:show(?:\s+me)?|tell me)\s+(?:my\s+)?style\s+history$",
        simplified,
        flags=re.IGNORECASE,
    ):
        return {"command": "/style history", "payload": None}
    if simplified in {
        "score my style",
        "show my style score",
        "show me my style score",
        "how is my style scoring",
        "grade my style",
        "grade my current style",
    }:
        return {"command": "/style score", "payload": None}
    if simplified in {
        "show my style examples",
        "show me my style examples",
        "show style examples",
        "show me examples of my style",
        "show me how you would reply right now",
    }:
        return {"command": "/style examples", "payload": None}
    for pattern in (
        r"^(?:use|apply|set)\s+(?:the\s+)?style\s+preset(?:\s+to)?\s+(?P<payload>[a-z0-9\-_ ]+)$",
        r"^(?:use|apply|switch to)\s+(?:the\s+)?(?P<payload>[a-z0-9\-_ ]+)\s+preset$",
        r"^(?:show(?:\s+me)?|preview|compare)\s+(?:my\s+)?style\s+before(?:\s+and\s+|\-)?after\s+(?:for|with)\s+(?P<payload>.+)$",
        r"^(?:show(?:\s+me)?|preview)\s+before(?:\s+and\s+|\-)?after\s+(?:style\s+)?for\s+(?P<payload>.+)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            payload = " ".join(str(match.group("payload") or "").strip().split())
            if payload:
                if pattern.startswith("^(?:use|apply|set)") or pattern.startswith("^(?:use|apply|switch to)"):
                    return {"command": "/style preset", "payload": payload}
                return {"command": "/style before-after", "payload": payload}
    if simplified in {
        "compare my style",
        "show me a style comparison",
        "show my style comparison",
        "compare my current style",
        "show me how you would sound right now",
    }:
        return {"command": "/style compare", "payload": None}
    for pattern in (
        r"^(?:please\s+|can you\s+)?(?:train|update|adjust|change)\s+(?:your|the)\s+(?:style|voice|personality)\s+(?:to|so (?:it|you)(?:'s| is))\s+(?P<payload>.+)$",
        r"^(?:for future replies[,:\s]+)?(?:be|sound)\s+(?P<payload>more .+|less .+|.+)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            payload = " ".join(str(match.group("payload") or "").strip().split())
            if payload:
                return {"command": "/style train", "payload": payload}
    feedback_payload = _style_feedback_payload_from_natural_message(normalized)
    if feedback_payload:
        return {"command": "/style feedback", "payload": feedback_payload}
    return None


def _extract_voice_answer_request(inbound_text: str) -> str | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.lower()
    for prefix in ("/voice ask", "/voice answer"):
        if lowered.startswith(f"{prefix} "):
            prompt = normalized[len(prefix) :].strip()
            return prompt or None

    for pattern in (
        r"^(?P<prompt>.+?)\s+(?:as|in|with)\s+(?:a\s+)?(?:voice|audio|spoken)\s+(?:message|reply|note)$",
        r"^(?:send|reply)\s+(?:me\s+)?(?:a\s+)?(?:voice|audio|spoken)\s+(?:message|reply|note)\s+(?:about|on|for)\s+(?P<prompt>.+)$",
        r"^(?:answer|respond\s+to)\s+(?P<prompt>.+?)\s+(?:by|with|in)\s+(?:voice|audio|speech)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        prompt = " ".join(str(match.group("prompt") or "").strip().split())
        if prompt:
            return prompt
    return None


def _match_natural_voice_command(inbound_text: str) -> tuple[str, str | None] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    simplified = " ".join(re.sub(r"[^a-z0-9\s/]", " ", lowered).split())

    if simplified in {
        "voice status",
        "show voice status",
        "show me voice status",
        "what is the voice status",
        "what s the voice status",
        "is voice ready",
    }:
        return ("/voice", None)
    if simplified in {
        "voice reply status",
        "show voice reply status",
        "show me voice reply status",
        "are voice replies on",
        "is voice reply on",
    }:
        return ("/voice reply", None)
    if simplified in {
        "install kokoro",
        "install kokoro voice",
        "install kokoro tts",
        "install kokoro voice locally",
        "install local voice",
        "install local tts",
        "add kokoro",
        "add kokoro voice",
        "add local tts",
        "can you install kokoro",
        "can you install kokoro voice",
        "can you install kokoro voice locally",
        "can you install local voice",
        "can you install local tts",
    }:
        return ("/voice install", "kokoro")
    for pattern in (
        r"^(?:please\s+|can you\s+)?(?:install|add|set up|setup)\s+(?P<payload>kokoro|kokoro\s+voice|kokoro\s+tts|local\s+voice|local\s+tts)(?:\s+.+)?$",
        r"^(?:please\s+|can you\s+)?(?:install|add)\s+(?P<payload>kokoro)(?:\s+voice)?\s+(?:locally|local)(?:\s+.+)?$",
    ):
        match = re.match(pattern, simplified, flags=re.IGNORECASE)
        if match:
            return ("/voice install", "kokoro")
    if simplified in {
        "turn voice replies on",
        "enable voice replies",
        "reply with voice",
        "reply in voice",
        "install voice",
        "install a voice",
        "install voice to yourself",
        "install a voice to yourself",
        "install voice to youself",
        "install a voice to youself",
        "can you install voice to yourself right now",
        "can you install a voice to yourself right now",
        "can you install voice to youself right now",
        "can you install a voice to youself right now",
        "give yourself a voice",
        "give youself a voice",
        "give you a voice",
        "add voice to yourself",
        "add a voice to yourself",
        "add voice to youself",
        "add a voice to youself",
    }:
        return ("/voice reply on", None)
    if simplified in {
        "turn voice replies off",
        "disable voice replies",
        "keep replies text only",
        "reply in text only",
    }:
        return ("/voice reply off", None)
    if simplified in {
        "voice plan",
        "show voice plan",
        "show me the voice plan",
        "how does voice work",
        "how will voice work",
    }:
        return ("/voice plan", None)
    if simplified in {
        "voice onboard",
        "voice onboarding",
        "voice setup",
        "setup voice",
        "set up voice",
        "help me set up voice",
        "can you help me set up voice",
        "how do i set up voice",
        "can i use free local tts",
    }:
        route = "local" if simplified == "can i use free local tts" else None
        return ("/voice onboard", route)
    for pattern in (
        r"^(?:voice\s+)?(?:onboard|onboarding|setup)\s+(?P<payload>local|free|offline|paid|provider|providers|production|hosted)$",
        r"^(?:can you\s+)?(?:help me\s+)?set\s+up\s+voice\s+(?:with|using|for)\s+(?P<payload>local|free|offline|paid|provider|providers|production|hosted)$",
        r"^(?:can you\s+)?(?:help me\s+)?set\s+up\s+voice\s+(?P<payload>locally|local|free|offline|paid|provider|providers|production|hosted)(?:\s+for\s+.+)?$",
        r"^(?:i\s+want|i\s+would\s+like|i\s+d\s+like)\s+(?:paid|premium|high\s+quality)(?:\s+\w+){0,8}\s+voice(?:\s+.+)?$",
    ):
        match = re.match(pattern, simplified, flags=re.IGNORECASE)
        if match:
            payload = str(match.groupdict().get("payload") or "paid").strip()
            if payload in {"locally", "free", "offline"}:
                payload = "local"
            if payload in {"provider", "providers", "production", "hosted"}:
                payload = "paid"
            return ("/voice onboard", payload)
    for pattern in (
        r"^(?:please\s+|can you\s+)?(?:speak|say|read)\s+(?:this(?: out loud)?[:\s-]+)(?P<payload>.+)$",
        r"^(?:send|reply with)\s+(?:this\s+)?(?:as\s+)?voice[:\s-]+(?P<payload>.+)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            payload = " ".join(str(match.group("payload") or "").strip().split())
            if payload:
                return ("/voice speak", payload)
    if simplified in {
        "send a short telegram voice note",
        "send a short voice note",
        "send me a short telegram voice note",
        "send me a short voice note",
        "can you send a short telegram voice note",
        "can you send a short voice note",
        "send a telegram voice note",
        "send a voice note",
    }:
        return ("/voice speak", "Voice is working here. I can send spoken replies through this Telegram chat now.")
    return None


def _match_natural_think_command(inbound_text: str) -> str | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    simplified = " ".join(re.sub(r"[^a-z0-9\s/]", " ", lowered).split())

    if simplified in {
        "think status",
        "show think status",
        "show me think status",
        "thinking status",
        "show thinking status",
        "show me thinking status",
        "what is the thinking status",
        "what s the thinking status",
        "is thinking on",
        "is think on",
    }:
        return "/think"
    if simplified in {
        "turn thinking on",
        "enable thinking",
        "show thinking",
        "show think blocks",
        "show hidden reasoning",
    }:
        return "/think on"
    if simplified in {
        "turn thinking off",
        "disable thinking",
        "hide thinking",
        "hide think blocks",
        "hide hidden reasoning",
    }:
        return "/think off"
    if re.match(
        r"^(?:please\s+|can you\s+)?(?:turn|set)\s+thinking\s+(on|off)$",
        simplified,
        flags=re.IGNORECASE,
    ):
        return "/think on" if simplified.endswith("on") else "/think off"
    return None


def _match_natural_memory_doctor_command(inbound_text: str) -> dict[str, object] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    lowered = normalized.lower()
    simplified = " ".join(re.sub(r"[^a-z0-9\s/]", " ", lowered).split())
    if simplified in {
        "memory doctor",
        "show memory doctor",
        "show me memory doctor",
        "run memory doctor",
        "check memory doctor",
        "diagnose memory",
        "diagnose spark memory",
        "audit memory",
        "audit spark memory",
        "check memory deletes",
        "check memory deletion",
        "check memory deletions",
        "did memory forget work",
        "did the memory forget work",
        "did forget memory work",
        "check active profile memory",
        "show active profile memory",
        "recommend memory fixes",
        "recommend memory improvements",
    }:
        return {
            "command": "/memory doctor",
            "topic": None,
            "request_id": None,
            "request_selector": None,
            "repair_requested": False,
        }
    if simplified in {
        "memory doctor last request",
        "memory doctor previous request",
        "memory doctor last turn",
        "memory doctor previous turn",
        "diagnose last request",
        "diagnose previous request",
        "diagnose last turn",
        "diagnose previous turn",
        "check last request memory",
        "check previous request memory",
        "check last turn memory",
        "check previous turn memory",
    }:
        return {
            "command": "/memory doctor",
            "topic": None,
            "request_id": None,
            "request_selector": "previous_gateway_turn",
            "repair_requested": False,
        }
    topic_patterns = (
        r"^memory\s+doctor\s+(.+)$",
        r"^(?:run|check|show)\s+memory\s+doctor\s+for\s+(.+)$",
        r"^(?:trace|check|diagnose|audit)\s+memory\s+for\s+(.+)$",
        r"^why\s+did\s+(?:memory|you|spark)\s+recall\s+(.+)$",
        r"^why\s+is\s+memory\s+recalling\s+(.+)$",
        r"^repair\s+memory\s+for\s+(.+)$",
    )
    for pattern in topic_patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            target = _memory_doctor_target_from_value(match.group(1))
            return {
                "command": "/memory doctor",
                "topic": target["topic"],
                "request_id": target["request_id"],
                "request_selector": target.get("request_selector"),
                "repair_requested": simplified.startswith("repair memory"),
            }
    for pattern in topic_patterns:
        match = re.match(pattern, simplified, flags=re.IGNORECASE)
        if match:
            target = _memory_doctor_target_from_value(match.group(1))
            return {
                "command": "/memory doctor",
                "topic": target["topic"],
                "request_id": target["request_id"],
                "request_selector": target.get("request_selector"),
                "repair_requested": simplified.startswith("repair memory"),
            }
    return None


def _runtime_command_trace_metadata(command_result: dict[str, Any]) -> dict[str, object]:
    metadata = command_result.get("trace_metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _match_contextual_memory_doctor_command(
    *,
    inbound_text: str,
    config_manager: ConfigManager,
    external_user_id: str,
    session_id: str,
    current_request_id: str,
) -> dict[str, object] | None:
    simplified = " ".join(re.sub(r"[^a-z0-9\s/]", " ", str(inbound_text or "").lower()).split())
    previous_record = _memory_doctor_previous_gateway_record(
        config_manager=config_manager,
        external_user_id=external_user_id,
        session_id=session_id,
        current_request_id=current_request_id,
    )
    if previous_record is None:
        return None
    score = _memory_doctor_distress_score(simplified)
    if _previous_gateway_turn_looks_like_memory_failure(previous_record):
        score += 2
    if score < 3:
        return None
    return {
        "command": "/memory doctor",
        "topic": None,
        "request_id": None,
        "request_selector": "previous_gateway_turn",
        "repair_requested": False,
        "contextual_trigger_score": score,
    }


def _memory_doctor_distress_score(simplified_text: str) -> int:
    text = str(simplified_text or "").strip()
    if not text:
        return 0
    score = 0
    if re.search(r"\b(?:memory|context|thread|previous|last|what i just said|what we were talking about)\b", text):
        score += 2
    if re.search(r"\b(?:blank|forgot|forget|remember|lost|dropped|missed|confused)\b", text):
        score += 2
    if re.search(r"\b(?:not responding|stopped responding|are you there|hello|wrong|again|seriously|come on)\b", text):
        score += 1
    if re.search(r"\b(?:why|what|where|how come|did you|do you|can you)\b", text):
        score += 1
    return score


def _previous_gateway_turn_looks_like_memory_failure(record: dict[str, object]) -> bool:
    user_preview = str(record.get("user_message_preview") or "").lower()
    response_preview = str(record.get("response_preview") or "").lower()
    route_text = f"{record.get('bridge_mode') or ''} {record.get('routing_decision') or ''}".lower()
    close_turn_markers = (
        "what did i just",
        "what phrase did i",
        "what did you just",
        "i just told you",
        "message right before",
        "last thing i said",
        "previous message",
    )
    blank_response_markers = (
        "not the message before",
        "not the prior message",
        "do not have the previous",
        "don't have the previous",
        "not in context",
        "lost context",
        "context capsule",
        "what did you just tell me",
    )
    if any(marker in user_preview for marker in close_turn_markers):
        return True
    if any(marker in response_preview for marker in blank_response_markers):
        return True
    return "researcher_advisory" in route_text and "previous" in user_preview


def _memory_doctor_target_from_slash_command(inbound_text: str) -> dict[str, str | None]:
    suffix = str(inbound_text or "")[len("/memory doctor") :].strip()
    if not suffix:
        return {"topic": None, "request_id": None, "request_selector": None}
    if suffix.lower().startswith("for "):
        suffix = suffix[4:].strip()
    return _memory_doctor_target_from_value(suffix)


def _memory_doctor_target_from_value(value: str) -> dict[str, str | None]:
    cleaned = " ".join(str(value or "").strip().strip(".?!`'\"").split())
    if cleaned.lower() in {
        "last request",
        "latest request",
        "previous request",
        "last turn",
        "latest turn",
        "previous turn",
        "last message",
        "previous message",
        "last telegram turn",
        "previous telegram turn",
    }:
        return {"topic": None, "request_id": None, "request_selector": "previous_gateway_turn"}
    request_match = re.match(
        r"^(?:request(?:\s+id)?|request-id|request_id|req(?:uest)?[_-]?id|req)\s*[:=#-]?\s*(.+)$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if request_match:
        request_id = _clean_memory_doctor_request_id(request_match.group(1))
        return {"topic": None, "request_id": request_id or None, "request_selector": None}
    return {"topic": _clean_memory_doctor_topic(cleaned), "request_id": None, "request_selector": None}


def _memory_doctor_previous_gateway_request_id(
    *,
    config_manager: ConfigManager,
    external_user_id: str,
    session_id: str,
    current_request_id: str,
) -> str | None:
    record = _memory_doctor_previous_gateway_record(
        config_manager=config_manager,
        external_user_id=external_user_id,
        session_id=session_id,
        current_request_id=current_request_id,
    )
    return str(record.get("request_id") or "").strip() if record else None


def _memory_doctor_previous_gateway_record(
    *,
    config_manager: ConfigManager,
    external_user_id: str,
    session_id: str,
    current_request_id: str,
) -> dict[str, object] | None:
    try:
        from spark_intelligence.gateway.tracing import read_gateway_traces

        traces = read_gateway_traces(config_manager, limit=80)
    except Exception:
        return None
    normalized_user_id = str(external_user_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    normalized_current_request_id = str(current_request_id or "").strip()
    for record in reversed(traces):
        if str(record.get("event") or "") != "telegram_update_processed":
            continue
        request_id = str(record.get("request_id") or "").strip()
        if not request_id or request_id == normalized_current_request_id:
            continue
        if normalized_user_id and str(record.get("telegram_user_id") or "").strip() != normalized_user_id:
            continue
        if normalized_session_id and str(record.get("session_id") or "").strip() != normalized_session_id:
            continue
        route_text = f"{record.get('bridge_mode') or ''} {record.get('routing_decision') or ''}".lower()
        if "runtime_command" in route_text:
            continue
        preview = str(record.get("user_message_preview") or "").strip()
        preview_lower = preview.lower()
        if preview_lower.startswith("/memory doctor") or _match_natural_memory_doctor_command(preview):
            continue
        return record
    return None


def _clean_memory_doctor_request_id(value: str) -> str:
    return " ".join(str(value or "").strip().strip(".?!`'\"").split())


def _clean_memory_doctor_topic(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().strip(".?!`'\"").split())
    if cleaned.lower() in {"it", "this", "that", "memory", "profile", "active profile"}:
        return ""
    return cleaned


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
                "`/style history` to inspect recent saved mutations, "
                "`/style savepoints` to list named checkpoints, "
                "`/style diff <name>` to compare the current voice against a savepoint, "
                "`/style presets` to list named conversation presets, "
                "`/style undo` to revert the last saved style change, "
                "`/style score` to grade the current voice against the training rubric, "
                "`/style examples` to see fixed sample replies in the current voice, "
                "`/style compare` to preview how the current voice differs from the default baseline, "
                "`/style savepoint <name>` to save the current voice state, "
                "`/style restore <name>` to restore a named savepoint, "
                "`/style preset <name>` to apply a named preset, "
                "`/style before-after <instruction>` to preview a training change before saving it, "
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
    if command == "/style history":
        return {
            "command": "/style history",
            "reply_text": _render_style_history_reply(
                rows=_list_recent_style_mutations(
                    state_db=state_db,
                    human_id=human_id,
                    agent_id=agent_id,
                    limit=5,
                ),
                profile=profile,
                agent_name=agent_name,
            ),
        }
    if command == "/style savepoints":
        return {
            "command": "/style savepoints",
            "reply_text": _render_style_savepoints_reply(
                rows=_list_style_savepoints(
                    state_db=state_db,
                    human_id=human_id,
                    agent_id=agent_id,
                    limit=10,
                ),
            ),
        }
    if command == "/style diff":
        savepoint_name = " ".join(str(payload or "").strip().split())
        if not savepoint_name:
            return {
                "command": "/style diff",
                "reply_text": (
                    "Style diff needs a savepoint name.\n"
                    "Next: send `/style diff claude baseline`."
                ),
            }
        if not human_id or not agent_id:
            return {
                "command": "/style diff",
                "reply_text": (
                    "Style diff is unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        savepoint = load_agent_persona_savepoint(
            agent_id=agent_id,
            human_id=human_id,
            savepoint_name=savepoint_name,
            state_db=state_db,
        )
        if savepoint is None:
            return {
                "command": "/style diff",
                "reply_text": (
                    "Style savepoint not found.\n"
                    "Next: use `/style savepoints` to list the available names."
                ),
            }
        return {
            "command": "/style diff",
            "reply_text": _render_style_diff_reply(
                profile=profile,
                agent_name=agent_name,
                savepoint=savepoint,
            ),
        }
    if command == "/style presets":
        return {
            "command": "/style presets",
            "reply_text": _render_style_presets_reply(),
        }
    if command == "/style undo":
        if not human_id or not agent_id:
            return {
                "command": "/style undo",
                "reply_text": (
                    "Style undo is unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        restored_profile = pop_agent_persona_undo_snapshot(
            agent_id=agent_id,
            human_id=human_id,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-undo",
        )
        if restored_profile is None:
            return {
                "command": "/style undo",
                "reply_text": (
                    "No style undo is available.\n"
                    "Reason: there is no saved pre-change snapshot yet.\n"
                    "Next: use `/style train`, `/style feedback`, or `/style preset` first."
                ),
            }
        refreshed_profile, refreshed_agent_name = _load_telegram_persona_surface_state(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        )
        return {
            "command": "/style undo",
            "reply_text": _render_style_undo_reply(profile=refreshed_profile, agent_name=refreshed_agent_name),
        }
    if command == "/style score":
        return {
            "command": "/style score",
            "reply_text": _render_style_score_reply(profile=profile, agent_name=agent_name),
        }
    if command == "/style examples":
        return {
            "command": "/style examples",
            "reply_text": _render_style_examples_reply(profile=profile, agent_name=agent_name),
        }
    if command == "/style before-after":
        instruction = str(payload or "").strip()
        if not instruction:
            return {
                "command": "/style before-after",
                "reply_text": (
                    "Style before/after needs an instruction.\n"
                    "Next: send `/style before-after be more direct and keep replies short`."
                ),
            }
        return {
            "command": "/style before-after",
            "reply_text": _render_style_before_after_reply(
                profile=profile,
                agent_name=agent_name,
                instruction=instruction,
            ),
        }
    if command == "/style savepoint":
        savepoint_name = " ".join(str(payload or "").strip().split())
        if not savepoint_name:
            return {
                "command": "/style savepoint",
                "reply_text": (
                    "Style savepoint needs a name.\n"
                    "Next: send `/style savepoint claude baseline`."
                ),
            }
        if not human_id or not agent_id:
            return {
                "command": "/style savepoint",
                "reply_text": (
                    "Style savepoints are unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        create_agent_persona_savepoint(
            agent_id=agent_id,
            human_id=human_id,
            savepoint_name=savepoint_name,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-savepoint",
        )
        return {
            "command": "/style savepoint",
            "reply_text": _render_style_savepoint_created_reply(name=savepoint_name),
        }
    if command == "/style restore":
        savepoint_name = " ".join(str(payload or "").strip().split())
        if not savepoint_name:
            return {
                "command": "/style restore",
                "reply_text": (
                    "Style restore needs a savepoint name.\n"
                    "Next: send `/style restore claude baseline`."
                ),
            }
        if not human_id or not agent_id:
            return {
                "command": "/style restore",
                "reply_text": (
                    "Style restore is unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        restored_profile = restore_agent_persona_savepoint(
            agent_id=agent_id,
            human_id=human_id,
            savepoint_name=savepoint_name,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-restore",
        )
        if restored_profile is None:
            return {
                "command": "/style restore",
                "reply_text": (
                    "Style savepoint not found.\n"
                    "Next: use `/style savepoints` to list the available names."
                ),
            }
        refreshed_profile, refreshed_agent_name = _load_telegram_persona_surface_state(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        )
        return {
            "command": "/style restore",
            "reply_text": _render_style_restore_reply(
                profile=refreshed_profile,
                agent_name=refreshed_agent_name,
                savepoint_name=savepoint_name,
            ),
        }
    if command == "/style preset":
        preset_name = _resolve_style_preset_name(payload)
        if not preset_name:
            return {
                "command": "/style preset",
                "reply_text": (
                    "Style preset not found.\n"
                    "Next: use `/style presets` to list the available preset names."
                ),
            }
        if not human_id or not agent_id:
            return {
                "command": "/style preset",
                "reply_text": (
                    "Style presets are unavailable right now.\n"
                    "Reason: this Telegram DM does not have a resolved Builder identity yet."
                ),
            }
        preset = _STYLE_PRESETS[preset_name]
        training_message = _build_style_training_message(str(preset.get("instruction") or ""))
        mutation = detect_and_persist_agent_persona_preferences(
            agent_id=agent_id,
            human_id=human_id,
            user_message=training_message,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-preset",
            push_undo_snapshot=True,
        )
        if mutation is None:
            return {
                "command": "/style preset",
                "reply_text": (
                    "I couldn't apply that style preset cleanly.\n"
                    "Next: use `/style before-after <instruction>` or `/style train <instruction>` directly."
                ),
            }
        refreshed_profile, refreshed_agent_name = _load_telegram_persona_surface_state(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            agent_id=agent_id,
        )
        return {
            "command": "/style preset",
            "reply_text": _render_style_training_reply(
                profile=refreshed_profile,
                agent_name=refreshed_agent_name,
                mutation=mutation,
                mode="preset",
                applied_instruction=f"Preset {preset['label']}: {_display_style_training_message(training_message)}",
            ),
        }
    if command == "/style compare":
        return {
            "command": "/style compare",
            "reply_text": _render_style_compare_reply(profile=profile, agent_name=agent_name),
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
        training_message = _build_style_training_message(instruction)
        mutation = detect_and_persist_agent_persona_preferences(
            agent_id=agent_id,
            human_id=human_id,
            user_message=training_message,
            state_db=state_db,
            source_surface="telegram",
            source_ref="telegram-style-train",
            push_undo_snapshot=True,
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
                applied_instruction=_display_style_training_message(training_message),
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
            push_undo_snapshot=True,
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
                applied_instruction=_display_style_training_message(training_message),
            ),
        }
    return {"command": command, "reply_text": "Style command is unavailable right now."}


def _build_style_training_message(instruction: str) -> str:
    normalized = " ".join(str(instruction or "").strip().split())
    lowered = normalized.lower()
    if not normalized:
        return ""
    if any(token in lowered for token in ("canned", "generic", "scripted", "chatbot")) and (
        ("follow-up" in lowered or "follow up" in lowered)
        and any(token in lowered for token in ("grounded", "specific", "better", "question", "questions"))
    ):
        return (
            "Your style.\n"
            "Avoid canned enthusiasm and generic assistant phrasing.\n"
            "Stay on the user's actual thread instead of restarting the conversation.\n"
            "Avoid generic opener questions and canned assistant greetings.\n"
            "Ask at most one follow-up question when needed.\n"
            "Ask follow-up questions that are specific to the user's last message.\n"
            "Do not ask broad check-in questions when a concrete follow-up is available."
        )
    if (
        ("previous message" in lowered or "previous turn" in lowered or "immediately previous" in lowered)
        and any(token in lowered for token in ("answer", "use", "refer", "respond"))
    ):
        return (
            "Your style.\n"
            "When the user asks about the previous message or previous turn, answer the immediately previous visible turn.\n"
            "Do not broaden the answer into a summary of the whole conversation."
        )
    if "meta commentary" in lowered or "meta-commentary" in lowered:
        return (
            "Your style.\n"
            "Give the answer first.\n"
            "Skip meta commentary about your process, tone, or performance.\n"
            "Do not add scene-setting phrases before the answer."
        )
    if any(token in lowered for token in ("polished", "performative")):
        return (
            "Your style.\n"
            "Be plain and natural.\n"
            "Avoid polished, performative, or theatrical phrasing.\n"
            "Prefer direct wording over presentation."
        )
    if "claude" in lowered or ("continuity" in lowered and "conversation" in lowered):
        return (
            "Your style.\n"
            "Stay anchored to the user's last message instead of resetting the conversation.\n"
            "Start by acknowledging the user's core point before moving the conversation forward.\n"
            "Avoid generic opener questions and canned assistant greetings.\n"
            "Ask at most one specific follow-up question when needed."
        )
    if any(token in lowered for token in ("canned", "generic", "scripted", "chatbot")):
        return (
            "Your style.\n"
            "Avoid canned enthusiasm and generic assistant phrasing.\n"
            "Stay on the user's actual thread instead of restarting the conversation.\n"
            "Prefer a concrete answer or next step over filler."
        )
    if ("follow-up" in lowered or "follow up" in lowered) and any(
        token in lowered for token in ("grounded", "specific", "better", "question", "questions")
    ):
        return (
            "Your style.\n"
            "Ask at most one follow-up question when needed.\n"
            "Ask follow-up questions that are specific to the user's last message.\n"
            "Do not ask broad check-in questions when a concrete follow-up is available."
        )
    if not any(
        marker in lowered
        for marker in ("your personality", "your style", "agent persona", "your name is", "call you", "rename yourself")
    ):
        return f"Your style.\n{normalized}"
    return normalized


def _build_style_feedback_training_message(*, feedback: str, sentiment: str) -> str:
    normalized = " ".join(str(feedback or "").strip().split())
    lowered = normalized.lower()
    if not normalized:
        return ""
    if any(token in lowered for token in ("canned", "generic", "scripted", "chatbot")) and (
        ("follow-up" in lowered or "follow up" in lowered)
        and any(token in lowered for token in ("grounded", "specific", "better", "question", "questions"))
    ):
        return (
            "Your style.\n"
            "Avoid canned enthusiasm and generic assistant phrasing.\n"
            "Stay on the user's actual thread instead of restarting the conversation.\n"
            "Avoid generic opener questions and canned assistant greetings.\n"
            "Ask at most one follow-up question when needed.\n"
            "Ask follow-up questions that are specific to the user's last message.\n"
            "Do not ask broad check-in questions when a concrete follow-up is available."
        )
    if (
        ("previous message" in lowered or "previous turn" in lowered or "immediately previous" in lowered)
        and any(token in lowered for token in ("answer", "use", "refer", "respond"))
    ):
        return (
            "Your style.\n"
            "When the user asks about the previous message or previous turn, answer the immediately previous visible turn.\n"
            "Do not broaden the answer into a summary of the whole conversation."
        )
    if "meta commentary" in lowered or "meta-commentary" in lowered:
        return (
            "Your style.\n"
            "Give the answer first.\n"
            "Skip meta commentary about your process, tone, or performance.\n"
            "Do not add scene-setting phrases before the answer."
        )
    if any(token in lowered for token in ("polished", "performative")):
        return (
            "Your style.\n"
            "Be plain and natural.\n"
            "Avoid polished, performative, or theatrical phrasing.\n"
            "Prefer direct wording over presentation."
        )
    if "answer first" in lowered or "lead with answers" in lowered or "give the answer first" in lowered:
        return (
            "Your style.\n"
            "Give the answer first.\n"
            "Prefer direct answers over scene-setting or commentary."
        )
    if "canned follow-up" in lowered or "generic follow-up" in lowered or "generic follow up" in lowered:
        return (
            "Your style.\n"
            "Ask at most one follow-up question when needed.\n"
            "Do not ask canned or generic follow-up questions.\n"
            "Use a follow-up only when it is specific to the user's last message."
        )
    if "claude" in lowered or ("continuity" in lowered and "conversation" in lowered):
        return (
            "Your style.\n"
            "Stay anchored to the user's last message instead of resetting the conversation.\n"
            "Start by acknowledging the user's core point before moving the conversation forward.\n"
            "Avoid generic opener questions and canned assistant greetings.\n"
            "Ask at most one specific follow-up question when needed."
        )
    if any(token in lowered for token in ("canned", "generic", "scripted", "chatbot")):
        return (
            "Your style.\n"
            "Avoid canned enthusiasm and generic assistant phrasing.\n"
            "Stay on the user's actual thread instead of restarting the conversation.\n"
            "Prefer a concrete answer or next step over filler."
        )
    if ("follow-up" in lowered or "follow up" in lowered) and any(
        token in lowered for token in ("grounded", "specific", "better", "question", "questions")
    ):
        return (
            "Your style.\n"
            "Ask at most one follow-up question when needed.\n"
            "Ask follow-up questions that are specific to the user's last message.\n"
            "Do not ask broad check-in questions when a concrete follow-up is available."
        )
    if sentiment == "positive":
        if "direct" in lowered or "concise" in lowered or "short" in lowered:
            return "Your style.\nStay direct and keep replies short."
        if "warm" in lowered or "friendly" in lowered or "human" in lowered:
            return "Your style.\nStay warm and friendly."
        if "calm" in lowered:
            return "Your style.\nStay calm and steady."
    if "too verbose" in lowered or "too long" in lowered or "rambling" in lowered:
        return "Your style.\nBe less verbose and keep replies short."
    if "too formal" in lowered or "stiff" in lowered:
        return "Your style.\nBe less formal and more human."
    if "too soft" in lowered or "hedging" in lowered or "wishy" in lowered:
        return "Your style.\nBe more assertive and stop hedging."
    if "too harsh" in lowered or "too blunt" in lowered or "too cold" in lowered:
        return "Your style.\nBe gentler and warmer."
    if "too playful" in lowered:
        return "Your style.\nBe more serious."
    if "too dry" in lowered or "robotic" in lowered:
        return "Your style.\nBe warmer and more human."
    if "too slow" in lowered:
        return "Your style.\nSpeed it up and get to the point."
    if "too rushed" in lowered:
        return "Your style.\nSlow down and explain more."
    if sentiment == "negative":
        return f"Your style.\nAdjust based on this feedback: {normalized}"
    return f"Your style.\nBe {normalized}"


def _normalize_style_preset_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")


def _resolve_style_preset_name(name: str | None) -> str | None:
    normalized = _normalize_style_preset_key(name)
    if not normalized:
        return None
    alias_map = {
        "claude": "claude-like",
        "claude-like": "claude-like",
        "continuity": "claude-like",
        "operator": "operator",
        "concise": "concise",
        "short": "concise",
        "warm": "warm",
        "friendly": "warm",
    }
    candidate = alias_map.get(normalized, normalized)
    return candidate if candidate in _STYLE_PRESETS else None


def _display_style_training_message(training_message: str) -> str:
    normalized = str(training_message or "").strip()
    if normalized.lower().startswith("your style."):
        normalized = normalized[len("your style.") :].strip()
    return " ".join(normalized.split())


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
    lines.append("Next: `/style score` to grade the current voice, `/style examples` to sample it, then `/style compare` to preview drift from baseline.")
    return "\n".join(lines)


def _render_style_savepoints_reply(*, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "Style savepoints are empty.\n"
            "Next: use `/style savepoint <name>` to store the current voice state."
        )
    lines = ["Style savepoints."]
    for index, row in enumerate(rows[:10], start=1):
        name = str(row.get("savepoint_name") or "").strip() or "(unnamed)"
        summary = str(row.get("persona_summary") or "").strip()
        created_at = str(row.get("created_at") or "").strip()
        detail = name
        if created_at:
            detail += f" at {created_at}"
        if summary:
            detail += f": {summary}"
        lines.append(f"{index}. {detail}")
    lines.append("Next: use `/style restore <name>` to restore one, or `/style savepoint <name>` to add another.")
    return "\n".join(lines)


def _render_style_diff_reply(*, profile: dict[str, Any] | None, agent_name: str | None, savepoint: dict[str, Any]) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    savepoint_name = str(savepoint.get("savepoint_name") or "").strip() or "savepoint"
    current_score_rows = _style_score_rows(resolved_profile)
    savepoint_profile = {
        "agent_persona_summary": savepoint.get("persona_summary"),
        "agent_behavioral_rules": list(savepoint.get("behavioral_rules") or []),
        "traits": dict(savepoint.get("base_traits") or {}),
    }
    savepoint_score_rows = _style_score_rows(savepoint_profile)
    current_overall = round(sum(score for _, score, _ in current_score_rows) / max(len(current_score_rows), 1), 1)
    savepoint_overall = round(sum(score for _, score, _ in savepoint_score_rows) / max(len(savepoint_score_rows), 1), 1)
    current_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    savepoint_summary = str(savepoint.get("persona_summary") or "").strip()
    current_rules = [str(rule).strip() for rule in list(resolved_profile.get("agent_behavioral_rules") or []) if str(rule).strip()]
    savepoint_rules = [str(rule).strip() for rule in list(savepoint.get("behavioral_rules") or []) if str(rule).strip()]
    current_example = dict(_style_example_rows(resolved_profile)).get("Ask me one clarifying question, then stop.", "")
    savepoint_example = dict(_style_example_rows(savepoint_profile)).get("Ask me one clarifying question, then stop.", "")
    deltas: list[str] = []
    for (label, current_score, _), (_, old_score, _) in zip(current_score_rows, savepoint_score_rows):
        diff = round(current_score - old_score, 1)
        if diff:
            sign = "+" if diff > 0 else ""
            deltas.append(f"{label} {old_score}/10 -> {current_score}/10 ({sign}{diff})")
    lines = [f"Style diff for {name} vs savepoint `{savepoint_name}`."]
    lines.append(f"Overall: {savepoint_overall}/10 -> {current_overall}/10.")
    if deltas:
        lines.append("Score delta: " + " | ".join(deltas[:4]) + ".")
    if savepoint_summary or current_summary:
        lines.append(f"Summary then: {savepoint_summary or '(none)'}.")
        lines.append(f"Summary now: {current_summary or '(none)'}.")
    if savepoint_rules or current_rules:
        lines.append("Rules then: " + (" | ".join(savepoint_rules[:3]) if savepoint_rules else "(none)") + ".")
        lines.append("Rules now: " + (" | ".join(current_rules[:3]) if current_rules else "(none)") + ".")
    if savepoint_example or current_example:
        lines.append("Prompt: Ask me one clarifying question, then stop.")
        lines.append(f"Then: {savepoint_example}")
        lines.append(f"Now: {current_example}")
    lines.append("Next: use `/style restore <name>` to go back, or `/style undo` to roll back the last change instead.")
    return "\n".join(lines)


def _render_style_savepoint_created_reply(*, name: str) -> str:
    normalized_name = " ".join(str(name or "").strip().split())
    return (
        f"Saved style savepoint `{normalized_name}`.\n"
        "Next: use `/style restore <name>` to restore it later, or `/style savepoints` to list all checkpoints."
    )


def _render_style_restore_reply(*, profile: dict[str, Any] | None, agent_name: str | None, savepoint_name: str) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    persona_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    lines = [f"Restored style savepoint `{savepoint_name}` for {name}."]
    if persona_summary:
        lines.append(f"Current summary: {persona_summary}.")
    lines.append("Next: ask a normal question in this DM to verify the restored voice, or use `/style undo` to roll back the restore.")
    return "\n".join(lines)


def _render_style_presets_reply() -> str:
    lines = ["Style presets available."]
    for key, preset in _STYLE_PRESETS.items():
        lines.append(f"- `{key}`: {preset['description']}")
    lines.append("Next: use `/style preset <name>` to apply one, `/style undo` to roll back the last saved change, or `/style before-after <instruction>` to preview a custom change.")
    return "\n".join(lines)


def _list_recent_style_mutations(
    *,
    state_db: StateDB,
    human_id: str | None,
    agent_id: str | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    resolved_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or str(agent_id or "").strip()
    if not resolved_agent_id:
        return []
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT mutation_id, mutation_kind, persona_name, persona_summary, source_surface, source_ref, created_at
            FROM agent_persona_mutations
            WHERE agent_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (resolved_agent_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _list_style_savepoints(
    *,
    state_db: StateDB,
    human_id: str | None,
    agent_id: str | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    resolved_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or str(agent_id or "").strip()
    if not resolved_agent_id or not human_id:
        return []
    return list_agent_persona_savepoints(
        agent_id=resolved_agent_id,
        human_id=human_id,
        state_db=state_db,
        limit=limit,
    )


def _style_history_event_label(row: dict[str, Any]) -> str:
    source_ref = str(row.get("source_ref") or "").strip().lower()
    mutation_kind = str(row.get("mutation_kind") or "").strip().lower()
    if source_ref == "telegram-style-feedback":
        return "feedback"
    if source_ref == "telegram-style-preset":
        return "preset"
    if source_ref == "telegram-style-undo" or mutation_kind == "rollback":
        return "undo"
    if source_ref == "telegram-style-train":
        return "training"
    if mutation_kind == "onboarding_authoring":
        return "onboarding"
    if mutation_kind == "external_import":
        return "import"
    return mutation_kind.replace("_", " ") or "update"


def _render_style_history_reply(
    *,
    rows: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    agent_name: str | None,
) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    if not rows:
        return (
            f"Style history for {name} is empty.\n"
            "Next: use `/style train <instruction>` or `/style feedback <note>` to save the first durable change."
        )

    lines = [f"Recent style history for {name}."]
    for index, row in enumerate(rows[:5], start=1):
        label = _style_history_event_label(row)
        summary = str(row.get("persona_summary") or "").strip()
        created_at = str(row.get("created_at") or "").strip()
        detail = summary or "Saved persona state updated."
        if created_at:
            lines.append(f"{index}. {label} at {created_at}: {detail}")
        else:
            lines.append(f"{index}. {label}: {detail}")
    lines.append("Next: use `/style feedback <note>` after a reply to keep shaping the live voice.")
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
    lines.append("Next: after each reply, use `/style feedback <note>`, `/style good <note>`, or `/style bad <note>`. Use `/style score` for the rubric, `/style examples` for fixed samples, and `/style compare` for a side-by-side preview.")
    return "\n".join(lines)


def _render_style_score_reply(*, profile: dict[str, Any] | None, agent_name: str | None) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    score_rows = _style_score_rows(resolved_profile)
    overall = round(sum(score for _, score, _ in score_rows) / max(len(score_rows), 1), 1)
    lines = [f"Style score for {name}: {overall}/10."]
    for label, score, rationale in score_rows:
        lines.append(f"- {label}: {score}/10 - {rationale}")
    weakest = min(score_rows, key=lambda row: row[1])[0]
    strongest = max(score_rows, key=lambda row: row[1])[0]
    lines.append(f"Strongest: {strongest}.")
    lines.append(f"Next focus: {weakest}. Use `/style feedback <note>` or `/style train <instruction>` to move it.")
    return "\n".join(lines)


def _render_style_examples_reply(*, profile: dict[str, Any] | None, agent_name: str | None) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    lines = [f"Style examples for {name}."]
    for index, (prompt, sample) in enumerate(_style_example_rows(resolved_profile), start=1):
        lines.append(f"{index}. Prompt: {prompt}")
        lines.append(f"Sample: {sample}")
    lines.append("Next: if one sample feels off, use `/style feedback <note>`. Use `/style compare` to see baseline drift.")
    return "\n".join(lines)


def _render_style_undo_reply(*, profile: dict[str, Any] | None, agent_name: str | None) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    persona_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    behavioral_rules = [
        str(rule).strip() for rule in list(resolved_profile.get("agent_behavioral_rules") or []) if str(rule).strip()
    ]
    lines = [f"Reverted last style change for {name}."]
    if persona_summary:
        lines.append(f"Current summary: {persona_summary}.")
    if behavioral_rules:
        lines.append("Current rules: " + " | ".join(behavioral_rules[:4]) + ".")
    lines.append("Next: ask a normal question in this DM to verify the rollback, or use `/style history` to inspect the mutation trail.")
    return "\n".join(lines)


def _render_style_before_after_reply(*, profile: dict[str, Any] | None, agent_name: str | None, instruction: str) -> str:
    resolved_profile = profile or {}
    simulated_profile = _simulate_style_profile(resolved_profile, instruction=instruction)
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    current_rows = _style_score_rows(resolved_profile)
    simulated_rows = _style_score_rows(simulated_profile)
    current_overall = round(sum(score for _, score, _ in current_rows) / max(len(current_rows), 1), 1)
    simulated_overall = round(sum(score for _, score, _ in simulated_rows) / max(len(simulated_rows), 1), 1)
    deltas: list[str] = []
    for (label, current_score, _), (_, next_score, _) in zip(current_rows, simulated_rows):
        if round(next_score - current_score, 1) != 0:
            delta = round(next_score - current_score, 1)
            sign = "+" if delta > 0 else ""
            deltas.append(f"{label} {current_score}/10 -> {next_score}/10 ({sign}{delta})")
    current_examples = dict(_style_example_rows(resolved_profile))
    simulated_examples = dict(_style_example_rows(simulated_profile))
    applied = _display_style_training_message(_build_style_training_message(instruction))
    lines = [f"Style before/after for {name}.", f"Instruction: {applied or instruction}."]
    lines.append(f"Overall: {current_overall}/10 -> {simulated_overall}/10.")
    if deltas:
        lines.append("Delta: " + " | ".join(deltas[:4]) + ".")
    for prompt in (
        "Give me the answer in two lines and skip filler.",
        "Ask me one clarifying question, then stop.",
    ):
        lines.append(f"Prompt: {prompt}")
        lines.append(f"Before: {current_examples.get(prompt, '')}")
        lines.append(f"After: {simulated_examples.get(prompt, '')}")
    lines.append("Next: if this looks right, use `/style train <instruction>` to save it.")
    return "\n".join(lines)


def _simulate_style_profile(profile: dict[str, Any], *, instruction: str) -> dict[str, Any]:
    training_message = _build_style_training_message(instruction)
    simulated: dict[str, Any] = dict(profile or {})
    next_traits = dict((profile or {}).get("traits") or {})
    for trait in ("warmth", "directness", "playfulness", "pacing", "assertiveness"):
        next_traits.setdefault(trait, 0.5)
    next_rules = [str(rule).strip() for rule in list((profile or {}).get("agent_behavioral_rules") or []) if str(rule).strip()]
    normalized_lines = [
        str(line).strip()
        for line in str(training_message or "").splitlines()
        if str(line).strip() and str(line).strip().lower() != "your style."
    ]
    for line in normalized_lines:
        if line not in next_rules:
            next_rules.append(line)
    lowered = str(training_message or "").lower()
    if "less verbose" in lowered or "keep replies short" in lowered or "get to the point" in lowered:
        next_traits["pacing"] = min(1.0, float(next_traits.get("pacing", 0.5)) + 0.2)
        next_traits["directness"] = min(1.0, float(next_traits.get("directness", 0.5)) + 0.1)
    if "more direct" in lowered or "stop hedging" in lowered:
        next_traits["directness"] = min(1.0, float(next_traits.get("directness", 0.5)) + 0.2)
        next_traits["assertiveness"] = min(1.0, float(next_traits.get("assertiveness", 0.5)) + 0.1)
    if "more assertive" in lowered:
        next_traits["assertiveness"] = min(1.0, float(next_traits.get("assertiveness", 0.5)) + 0.2)
    if "less formal" in lowered or "more human" in lowered or "warmer" in lowered or "friendly" in lowered:
        next_traits["warmth"] = min(1.0, float(next_traits.get("warmth", 0.5)) + 0.15)
    if "gentler" in lowered:
        next_traits["warmth"] = min(1.0, float(next_traits.get("warmth", 0.5)) + 0.15)
        next_traits["assertiveness"] = max(0.0, float(next_traits.get("assertiveness", 0.5)) - 0.05)
    if "more serious" in lowered:
        next_traits["playfulness"] = max(0.0, float(next_traits.get("playfulness", 0.5)) - 0.15)
    if "slow down" in lowered or "explain more" in lowered:
        next_traits["pacing"] = max(0.0, float(next_traits.get("pacing", 0.5)) - 0.1)
    simulated["traits"] = next_traits
    simulated["agent_behavioral_rules"] = next_rules
    if normalized_lines and not str(simulated.get("agent_persona_summary") or "").strip():
        simulated["agent_persona_summary"] = normalized_lines[0]
    return simulated


def _render_style_compare_reply(*, profile: dict[str, Any] | None, agent_name: str | None) -> str:
    resolved_profile = profile or {}
    name = str(agent_name or resolved_profile.get("agent_persona_name") or "the agent").strip()
    persona_summary = str(resolved_profile.get("agent_persona_summary") or "").strip()
    behavioral_rules = [
        str(rule).strip() for rule in list(resolved_profile.get("agent_behavioral_rules") or []) if str(rule).strip()
    ]
    lines = [f"Style compare for {name}."]
    if persona_summary:
        lines.append(f"Current voice: {persona_summary}.")
    if behavioral_rules:
        lines.append("Saved rules shaping this: " + " | ".join(behavioral_rules[:3]) + ".")
    for index, (prompt, baseline, current) in enumerate(_style_compare_examples(resolved_profile), start=1):
        lines.append(f"{index}. Prompt: {prompt}")
        lines.append(f"Baseline: {baseline}")
        lines.append(f"Current: {current}")
    lines.append("Next: if one of the current examples is off, use `/style feedback <note>` or `/style train <instruction>`.")
    return "\n".join(lines)


def _style_example_rows(profile: dict[str, Any]) -> list[tuple[str, str]]:
    traits = profile.get("traits") or {}
    rules = [str(rule).strip().lower() for rule in list(profile.get("agent_behavioral_rules") or []) if str(rule).strip()]
    direct = float(traits.get("directness", 0.5)) >= 0.65
    brisk = float(traits.get("pacing", 0.5)) >= 0.65
    warm = float(traits.get("warmth", 0.5)) >= 0.6
    assertive = float(traits.get("assertiveness", 0.5)) >= 0.65
    continuity = any("anchored to the user's last message" in rule or "stay on the user's actual thread" in rule for rule in rules)
    avoid_canned = any("avoid canned enthusiasm" in rule or "avoid generic opener questions" in rule for rule in rules)

    short_answer = "Start with one wedge, validate it fast, then expand."
    if not direct and not brisk:
        short_answer = "Start with one wedge, validate it, then expand once it clearly works."
    elif warm and not assertive:
        short_answer = "Start with one wedge, prove it works, then expand from there."

    plan_critique = "The plan is too broad right now. Cut to one user, one workflow, and one proof point."
    if warm and not assertive:
        plan_critique = "The plan is still too broad right now. Cut to one user, one workflow, and one proof point."
    elif not direct:
        plan_critique = "The plan is still broad. Narrow it to one user, one workflow, and one proof point."

    follow_up = "What exact user or workflow is blocked right now?"
    if warm and continuity:
        follow_up = "What exact user or workflow feels blocked right now?"
    elif not direct:
        follow_up = "What specific user or workflow should we narrow first?"

    browser_answer = "BTC is trading live, but I’d verify the current price on a stronger source page before I state a number."
    if direct and brisk:
        browser_answer = "BTC is moving live. I’d verify the number on a stronger source page before I quote it."
    elif warm and avoid_canned:
        browser_answer = "BTC is moving live, but I’d verify the current number on a stronger source page before I quote it."

    return [
        ("Give me the answer in two lines and skip filler.", short_answer),
        ("Critique this plan bluntly and tell me the next step.", plan_critique),
        ("Ask me one clarifying question, then stop.", follow_up),
        ("Search the web for BTC and give me the answer carefully.", browser_answer),
    ]


def _style_score_rows(profile: dict[str, Any]) -> list[tuple[str, float, str]]:
    traits = profile.get("traits") or {}
    rules = [str(rule).strip().lower() for rule in list(profile.get("agent_behavioral_rules") or []) if str(rule).strip()]
    persona_summary = str(profile.get("agent_persona_summary") or "").strip()
    directness = float(traits.get("directness", 0.5))
    pacing = float(traits.get("pacing", 0.5))
    warmth = float(traits.get("warmth", 0.5))
    assertiveness = float(traits.get("assertiveness", 0.5))
    continuity = any("anchored to the user's last message" in rule or "stay on the user's actual thread" in rule for rule in rules)
    avoid_canned = any("avoid canned enthusiasm" in rule or "avoid generic opener questions" in rule for rule in rules)
    follow_up_specific = any(
        "specific to the user's last message" in rule or "do not ask broad check-in questions" in rule for rule in rules
    )

    continuity_score = min(10.0, 5.0 + (3.0 if continuity else 0.0) + (2.0 if avoid_canned else 0.0))
    filler_score = min(10.0, 4.0 + (2.0 if pacing >= 0.65 else 0.0) + (2.0 if directness >= 0.65 else 0.0) + (2.0 if avoid_canned else 0.0))
    follow_up_score = min(10.0, 4.0 + (4.0 if follow_up_specific else 0.0) + (1.0 if continuity else 0.0) + (1.0 if directness >= 0.6 else 0.0))
    directness_score = min(10.0, 4.0 + round(directness * 3.0, 1) + round(assertiveness * 2.0, 1) + (1.0 if pacing >= 0.6 else 0.0))
    consistency_score = min(10.0, 4.0 + (2.0 if persona_summary else 0.0) + min(3.0, float(len(rules))) + (1.0 if 0.35 <= warmth <= 0.8 else 0.0))

    return [
        (
            "Continuity",
            round(continuity_score, 1),
            "Stays anchored to the user's actual thread instead of restarting the conversation." if continuity else "Still needs stronger thread continuity rules.",
        ),
        (
            "Anti-filler",
            round(filler_score, 1),
            "Pushes toward concise answers and avoids canned filler." if avoid_canned or pacing >= 0.65 else "Still at risk of generic or padded replies.",
        ),
        (
            "Follow-up specificity",
            round(follow_up_score, 1),
            "Favors one specific follow-up question when clarification is needed." if follow_up_specific else "Needs stronger follow-up-question constraints.",
        ),
        (
            "Directness",
            round(directness_score, 1),
            "Keeps answers clear and decisive." if directness >= 0.65 or assertiveness >= 0.65 else "Could still be more direct and decisive.",
        ),
        (
            "Voice consistency",
            round(consistency_score, 1),
            "The saved persona summary and rules are strong enough to stabilize the voice." if persona_summary or rules else "Needs more saved persona detail to keep the voice stable.",
        ),
    ]


def _style_compare_examples(profile: dict[str, Any]) -> list[tuple[str, str, str]]:
    traits = profile.get("traits") or {}
    rules = [str(rule).strip().lower() for rule in list(profile.get("agent_behavioral_rules") or []) if str(rule).strip()]
    direct = float(traits.get("directness", 0.5)) >= 0.65
    brisk = float(traits.get("pacing", 0.5)) >= 0.65
    warm = float(traits.get("warmth", 0.5)) >= 0.6
    assertive = float(traits.get("assertiveness", 0.5)) >= 0.65
    continuity = any("anchored to the user's last message" in rule or "stay on the user's actual thread" in rule for rule in rules)
    avoid_canned = any("avoid canned enthusiasm" in rule or "avoid generic opener questions" in rule for rule in rules)

    current_short = "Start with one narrow wedge. Prove it works before you expand."
    if not direct and not brisk:
        current_short = "Start with one narrow wedge, validate it, then expand once it proves out."
    elif warm and not assertive:
        current_short = "Start with one narrow wedge. Prove it works, then expand from there."

    current_critique = "The scope is too broad. Next: cut to one user, one workflow, one proof point."
    if warm and not assertive:
        current_critique = "The scope is still too broad. Next: cut to one user, one workflow, one proof point."
    elif not direct:
        current_critique = "The scope is still broad. Next: pick one user, one workflow, and one proof point."

    current_follow_up = "What exact user or workflow is blocked right now?"
    if continuity and avoid_canned:
        current_follow_up = "What exact user or workflow is blocked right now?"
    elif warm:
        current_follow_up = "What specific user or workflow feels blocked right now?"
    elif not direct:
        current_follow_up = "What specific user or workflow should we narrow first?"

    return [
        (
            "Give me the answer in two lines and skip filler.",
            "Here’s the short version: start with one narrow wedge and validate it. Then expand only after it works.",
            current_short,
        ),
        (
            "Critique this plan bluntly and tell me the next step.",
            "The plan is promising, but the scope is still broad. Next: choose one user, one workflow, and one proof point.",
            current_critique,
        ),
        (
            "Ask me one clarifying question, then stop.",
            "What specific user or workflow are you solving for?",
            current_follow_up,
        ),
    ]


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
    if mode == "feedback":
        lead = "Saved style feedback"
    elif mode == "preset":
        lead = "Saved style preset"
    else:
        lead = "Saved style update"
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
    payload_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _build_voice_chip_payload(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
    )
    if payload_extra:
        payload.update(payload_extra)
    execution = run_first_chip_hook_supporting(
        config_manager,
        hook=hook,
        payload=payload,
    )
    if execution is None:
        return {"command": command, "reply_text": fallback_reply}
    result = execution.output.get("result") if isinstance(execution.output, dict) else None
    reply_text = str((result or {}).get("reply_text") or "").strip()
    if command in {"/voice", "/voice status"}:
        if reply_text:
            reply_text = _append_telegram_voice_profile_status(reply_text, config_manager=config_manager)
        else:
            fallback_reply = _append_telegram_voice_profile_status(fallback_reply, config_manager=config_manager)
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
        "I can see Telegram voice messages, but the voice chip is not attached to this Spark yet.\n"
        "Attach `spark-voice-comms` first; then I can transcribe voice notes and help you choose a local or hosted voice setup.\n"
        "After that, rerun `/voice`."
    )


def _append_telegram_voice_profile_status(reply_text: str, *, config_manager: ConfigManager | None = None) -> str:
    profile = _telegram_voice_status_profile()
    if not profile:
        return reply_text
    lines = [
        "",
        "Profile voice:",
        f"- Telegram profile: {profile['telegram_profile']}",
        f"- Provider: {profile['provider_id']}",
        f"- Voice: {profile['voice_name']}",
    ]
    voice_id = str(profile.get("voice_id") or "").strip()
    if voice_id:
        lines.append(f"- Voice ID: {_mask_voice_id(voice_id)}")
    model_id = str(profile.get("model_id") or "").strip()
    if model_id:
        lines.append(f"- Model: {model_id}")
    settings = profile.get("voice_settings") if isinstance(profile.get("voice_settings"), dict) else {}
    if settings:
        rendered = ", ".join(f"{key}={settings[key]}" for key in sorted(settings))
        lines.append(f"- Settings: {rendered}")
    effect = str(profile.get("audio_effect") or "").strip()
    if effect:
        lines.append(f"- Effect: {effect}")
    source = str(profile.get("source") or "").strip()
    if source:
        lines.append(f"- Source: {source}")
    fingerprint_status = _telegram_voice_profile_fingerprint_status(profile)
    if fingerprint_status:
        lines.append(f"- Fingerprint: {fingerprint_status['fingerprint']} ({fingerprint_status['status']})")
        drift_fields = fingerprint_status.get("drift_fields")
        if drift_fields:
            lines.append(f"- Drift fields: {', '.join(drift_fields)}")
    preflight = _telegram_voice_profile_preflight(profile=profile, config_manager=config_manager)
    if preflight:
        lines.append("")
        lines.append("Preflight:")
        lines.extend(f"- {item}" for item in preflight)
    return f"{reply_text.rstrip()}\n" + "\n".join(lines)


def _telegram_voice_status_profile() -> dict[str, Any]:
    registry_profile = _telegram_voice_registry_profile()
    tts = _telegram_voice_tts_override_from_env() or {}
    provider_id = str(tts.get("provider_id") or "").strip()
    voice_id = str(tts.get("voice_id") or "").strip()
    voice_name = str(tts.get("voice_name") or "").strip()
    model_id = str(tts.get("model_id") or "").strip()
    secret_env_ref = str(tts.get("secret_env_ref") or "").strip()
    effect = _telegram_voice_effect_from_env()
    settings = tts.get("voice_settings") if isinstance(tts.get("voice_settings"), dict) else {}
    if not any([provider_id, voice_id, voice_name, model_id, effect, settings]):
        return {}
    source_parts = []
    if registry_profile:
        source_parts.append("registry")
    if any(
        str(os.environ.get(name) or "").strip()
        for name in (
            "SPARK_TELEGRAM_VOICE_TTS_PROVIDER",
            "SPARK_TELEGRAM_TTS_PROVIDER",
            "SPARK_TELEGRAM_VOICE_TTS_VOICE_ID",
            "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_VOICE_ID",
            "SPARK_TELEGRAM_VOICE_TTS_VOICE_NAME",
            "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_VOICE_NAME",
            "SPARK_TELEGRAM_VOICE_TTS_MODEL_ID",
            "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_MODEL_ID",
            "SPARK_TELEGRAM_VOICE_TTS_BASE_URL",
            "SPARK_TELEGRAM_VOICE_TTS_ELEVENLABS_BASE_URL",
            "SPARK_TELEGRAM_VOICE_TTS_OUTPUT_FORMAT",
            "SPARK_TELEGRAM_VOICE_TTS_SECRET_ENV_REF",
            "SPARK_TELEGRAM_VOICE_TTS_STABILITY",
            "SPARK_TELEGRAM_VOICE_TTS_SIMILARITY_BOOST",
            "SPARK_TELEGRAM_VOICE_TTS_STYLE",
            "SPARK_TELEGRAM_VOICE_TTS_SPEED",
            "SPARK_TELEGRAM_VOICE_TTS_USE_SPEAKER_BOOST",
            "SPARK_TELEGRAM_VOICE_AUDIO_EFFECT",
        )
    ):
        source_parts.append("env")
    return {
        "telegram_profile": str(os.environ.get("SPARK_TELEGRAM_PROFILE") or "default").strip() or "default",
        "provider_id": provider_id or "default",
        "voice_id": voice_id,
        "voice_name": voice_name or "default",
        "model_id": model_id,
        "secret_env_ref": secret_env_ref,
        "voice_settings": settings,
        "audio_effect": effect,
        "source": "+".join(source_parts) if source_parts else "default",
    }


def _telegram_voice_profile_fingerprint_status(profile: dict[str, Any]) -> dict[str, Any]:
    fields = _telegram_voice_profile_fingerprint_fields(profile)
    fingerprint = _telegram_voice_profile_fingerprint(fields)
    registry_profile = _telegram_voice_registry_profile()
    if not registry_profile:
        return {
            "fingerprint": fingerprint,
            "status": "no saved registry baseline",
            "fields": fields,
            "baseline_fingerprint": None,
            "drift_fields": [],
        }
    baseline_fields = _telegram_voice_profile_fingerprint_fields(registry_profile)
    baseline_fingerprint = _telegram_voice_profile_fingerprint(baseline_fields)
    expected_fingerprint = str(registry_profile.get("voice_fingerprint") or baseline_fingerprint).strip()
    drift_fields = [
        key
        for key in ("provider_id", "voice_id", "model_id", "voice_settings", "audio_effect", "audio_effect_version")
        if fields.get(key) != baseline_fields.get(key)
    ]
    if fingerprint != expected_fingerprint and not drift_fields:
        drift_fields = ["voice_fingerprint"]
    return {
        "fingerprint": fingerprint,
        "status": "matches saved profile" if fingerprint == expected_fingerprint else f"drift from saved profile {expected_fingerprint}",
        "fields": fields,
        "baseline_fingerprint": expected_fingerprint,
        "drift_fields": drift_fields,
    }


def _telegram_voice_profile_fingerprint_fields(profile: dict[str, Any]) -> dict[str, Any]:
    effect = str(profile.get("audio_effect") or "").strip().lower()
    settings = profile.get("voice_settings") if isinstance(profile.get("voice_settings"), dict) else {}
    normalized_settings = {str(key): settings[key] for key in sorted(settings)}
    return {
        "provider_id": str(profile.get("provider_id") or "").strip().lower(),
        "voice_id": str(profile.get("voice_id") or "").strip(),
        "model_id": str(profile.get("model_id") or "").strip(),
        "voice_settings": normalized_settings,
        "audio_effect": effect,
        "audio_effect_version": TELEGRAM_PARROT_EFFECT_VERSION if effect == "parrot" else "",
    }


def _telegram_voice_profile_fingerprint(fields: dict[str, Any]) -> str:
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _telegram_voice_profile_preflight(
    *,
    profile: dict[str, Any],
    config_manager: ConfigManager | None = None,
) -> list[str]:
    checks: list[str] = []
    provider_id = str(profile.get("provider_id") or "").strip().lower()
    voice_id = str(profile.get("voice_id") or "").strip()
    if provider_id == "elevenlabs":
        secret_env_ref = str(profile.get("secret_env_ref") or "ELEVENLABS_API_KEY").strip() or "ELEVENLABS_API_KEY"
        secret_status = "present" if _telegram_voice_secret_is_available(secret_env_ref, config_manager=config_manager) else "missing"
        checks.append(f"ElevenLabs secret: {secret_status} ({secret_env_ref})")
        checks.append("ElevenLabs voice id: configured" if voice_id else "ElevenLabs voice id: missing")
    effect = str(profile.get("audio_effect") or "").strip().lower()
    if effect == "parrot":
        checks.append(
            "Parrot effect: ready (ffmpeg found)"
            if shutil.which("ffmpeg")
            else "Parrot effect: ffmpeg missing; raw TTS audio will be sent"
        )
    return checks


def _telegram_voice_secret_is_available(
    secret_env_ref: str,
    *,
    config_manager: ConfigManager | None = None,
) -> bool:
    env_name = str(secret_env_ref or "").strip()
    if not env_name:
        return False
    if str(os.environ.get(env_name) or "").strip():
        return True
    if config_manager is None:
        return False
    try:
        return bool(str(config_manager.read_env_map().get(env_name) or "").strip())
    except Exception:
        return False


def _mask_voice_id(voice_id: str) -> str:
    value = str(voice_id or "").strip()
    if len(value) <= 8:
        return value
    return f"{value[:6]}-{value[-4:]}"


def _render_telegram_voice_plan_reply() -> str:
    return (
        "Telegram voice plan:\n"
        "1. let Builder fetch Telegram voice/audio payloads and hand them to `spark-voice-comms`.\n"
        "2. transcribe them back into the same Builder Telegram runtime used for text and persona styling.\n"
        "3. optionally add voice reply synthesis as a later chip hook without growing Builder.\n"
        "Next: attach the chip, validate `/voice`, then dogfood real Telegram voice notes."
    )


def _render_telegram_voice_install_reply(target: str | None) -> str:
    normalized_target = str(target or "").strip() or "kokoro"
    return (
        f"I can help install `{normalized_target}`, but this Spark needs the voice chip attached first.\n"
        "Attach `spark-voice-comms`, then rerun `/voice install kokoro` and I will handle the local setup from there."
    )


def _render_telegram_voice_onboarding_reply(route: str | None = None) -> str:
    suffix = f" for `{route}`" if route else ""
    return (
        f"I can guide voice setup{suffix}, but the voice chip is not attached to this Spark yet.\n"
        "Once `spark-voice-comms` is active, I can recommend the right path from inside Telegram: local/private with Kokoro, or hosted/high-quality with a paid provider.\n"
        "Attach the chip, then run `/voice onboard local` or `/voice onboard paid`."
    )


def _render_telegram_voice_transcription_unavailable_reply(*, reason: str) -> str:
    cleaned_reason = " ".join(str(reason or "").strip().split())
    lines = [
        "Voice transcription is unavailable right now.",
        f"Reason: {cleaned_reason or 'the runtime could not transcribe this Telegram audio message.'}",
        "Next: send the instruction as text for now, or finish the `spark-voice-comms` setup.",
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


def _handle_telegram_chip_command(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    chip_command: dict[str, Any],
    run_id: str | None,
    request_id: str,
    session_id: str,
    human_id: str | None,
    agent_id: str | None,
) -> dict[str, str]:
    command = str(chip_command.get("command") or "/chip")
    if command == "/chip":
        return {
            "command": "/chip",
            "reply_text": _render_chip_help_reply(),
        }
    if command == "/chip status":
        return {
            "command": "/chip status",
            "reply_text": _render_chip_status_reply(
                config_manager=config_manager,
                chip_key=str(chip_command.get("chip_key") or "").strip() or None,
            ),
        }
    if command == "/chip autoloop":
        return {
            "command": "/chip autoloop",
            "reply_text": _render_chip_autoloop_reply(
                chip_key=str(chip_command.get("chip_key") or "").strip() or "chip",
            ),
        }

    hook = str(chip_command.get("hook") or "").strip()
    chip_key = str(chip_command.get("chip_key") or "").strip()
    if not hook or not chip_key:
        return {
            "command": command,
            "reply_text": _render_chip_help_reply(),
        }
    try:
        payload, payload_mode = _build_direct_chip_command_payload(
            config_manager=config_manager,
            raw_payload=str(chip_command.get("payload") or ""),
            request_id=request_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
    except ValueError as exc:
        return {
            "command": command,
            "reply_text": f"Chip command payload is invalid.\nReason: {_with_terminal_period(str(exc))}",
        }

    try:
        execution = run_chip_hook(
            config_manager,
            chip_key=chip_key,
            hook=hook,
            payload=payload,
        )
    except ValueError as exc:
        return {
            "command": command,
            "reply_text": f"Chip command is unavailable.\nReason: {_with_terminal_period(str(exc))}",
        }
    except RuntimeError as exc:
        return {
            "command": command,
            "reply_text": f"Chip command failed before execution.\nReason: {_with_terminal_period(str(exc))}",
        }

    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="telegram_runtime",
        actor_id="telegram_runtime",
        summary="Telegram runtime executed a direct chip hook command.",
        reason_code="telegram_chip_runtime_command",
        keepability="operator_debug_only",
        run_id=run_id,
        request_id=request_id,
        channel_id="telegram",
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    reply_text = _render_direct_chip_execution_reply(
        execution=execution,
        hook=hook,
        payload_mode=payload_mode,
    )
    screened = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=reply_text,
        summary="Telegram runtime blocked secret-like chip hook output before delivery.",
        reason_code="telegram_chip_runtime_secret_like",
        policy_domain="telegram_runtime",
        blocked_stage="telegram_reply",
        run_id=run_id,
        request_id=request_id,
        trace_ref=f"trace:telegram:{request_id}",
    )
    if not screened["allowed"]:
        return {
            "command": command,
            "reply_text": (
                "Chip output was blocked before Telegram delivery.\n"
                f"Reason: it contained secret-like material and was quarantined as {screened['quarantine_id']}."
            ),
        }
    return {
        "command": command,
        "reply_text": str(screened["text"] or "").strip() or "Chip command completed with no displayable output.",
    }


def _render_chip_help_reply() -> str:
    return (
        "Chip commands: `/chip status [chip_key]`, `/chip evaluate <chip_key> [text|key=value ...|json]`, "
        "`/chip suggest <chip_key> [text|key=value ...|json]`, and `/chip autoloop <chip_key>`.\n"
        "Example: `/chip evaluate domain-chip-trading-crypto doctrine_id=trend_regime_following "
        "strategy_id=ema_pullback_long market_regime=trend timeframe=1h venue=binance asset_universe=BTC paper_gate=strict`.\n"
        "Rule: direct chip commands run the chip hook locally in Telegram, but they do not create a Swarm insight, "
        "autoloop session, or GitHub delivery by themselves."
    )


def _render_chip_status_reply(
    *,
    config_manager: ConfigManager,
    chip_key: str | None,
) -> str:
    records = list_chip_records(config_manager)
    if not records:
        return (
            "No chips are attached in this workspace yet.\n"
            "Next: add a chip root with `spark-intelligence attachments add-root chips <repo>` and activate the chip you want."
        )
    attachment_context = build_attachment_context(config_manager)
    active_keys = {
        str(item)
        for item in attachment_context.get("active_chip_keys", [])
        if str(item).strip()
    }
    pinned_keys = {
        str(item)
        for item in attachment_context.get("pinned_chip_keys", [])
        if str(item).strip()
    }
    selected = None
    if chip_key:
        for record in records:
            if str(record.key) == chip_key:
                selected = record
                break
        if selected is None:
            known = ", ".join(sorted(str(record.key) for record in records))
            return f"Unknown chip key `{chip_key}`.\nKnown chips: {known}."
    if selected is not None:
        hooks = ", ".join(sorted(selected.commands)) if selected.commands else "none"
        mutation_fields = []
        frontier = selected.frontier if isinstance(selected.frontier, dict) else {}
        allowed_mutations = frontier.get("allowed_mutations") if isinstance(frontier.get("allowed_mutations"), dict) else {}
        if isinstance(allowed_mutations, dict):
            mutation_fields = sorted(str(field) for field in allowed_mutations.keys() if str(field).strip())
        status_bits = [
            "active" if selected.key in active_keys else "inactive",
            "pinned" if selected.key in pinned_keys else "unpinned",
        ]
        lines = [
            f"Chip `{selected.key}` is {' and '.join(status_bits)}.",
            f"Hooks: {hooks}.",
            f"Repo: {selected.repo_root}.",
        ]
        if selected.description:
            lines.append(f"Description: {_with_terminal_period(selected.description)}")
        if mutation_fields:
            lines.append(f"Frontier mutation fields: {', '.join(mutation_fields[:8])}.")
        lines.append(
            "Autoloop note: raw chip repos do not run through `/swarm autoloop`; that requires an attached specialization path."
        )
        return "\n".join(lines)
    ranked = sorted(records, key=lambda record: (str(record.key) not in active_keys, str(record.key)))
    lines = [f"Attached chips: {len(records)}."]
    for record in ranked[:5]:
        state = "active" if record.key in active_keys else "available"
        if record.key in pinned_keys:
            state = f"{state}, pinned"
        hooks = ", ".join(sorted(record.commands)) if record.commands else "none"
        lines.append(f"- {record.key}: {state} [{hooks}]")
    lines.append("Next: `/chip status <chip_key>` for details or `/chip evaluate <chip_key> ...` to run one directly.")
    return "\n".join(lines)


def _render_chip_autoloop_reply(*, chip_key: str) -> str:
    return (
        f"Chip autoloop is not available for `{chip_key}` through Builder Telegram yet.\n"
        "Reason: Builder autoloop runs attached specialization paths through the Swarm bridge, not raw chip repos.\n"
        "Next: attach a specialization path and use `/swarm autoloop <path_key>` if you want a tracked autoloop session, "
        "Swarm sync, and downstream upgrade-delivery flow."
    )


def _build_direct_chip_command_payload(
    *,
    config_manager: ConfigManager,
    raw_payload: str,
    request_id: str,
    session_id: str,
    human_id: str | None,
    agent_id: str | None,
) -> tuple[dict[str, Any], str]:
    attachment_context = build_attachment_context(config_manager)
    payload_text = str(raw_payload or "").strip()
    user_payload: dict[str, Any]
    payload_mode: str
    if not payload_text:
        user_payload = {}
        payload_mode = "empty"
    elif payload_text.startswith("{"):
        try:
            decoded = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON payload ({exc})") from exc
        if not isinstance(decoded, dict):
            raise ValueError("JSON payload must decode to an object.")
        user_payload = decoded
        payload_mode = "json"
    else:
        mutation_payload = _parse_direct_chip_mutation_pairs(payload_text)
        if mutation_payload is not None:
            user_payload = mutation_payload
            payload_mode = "mutation_pairs"
        else:
            user_payload = {
                "situation": payload_text,
                "text": payload_text,
                "prompt": payload_text,
            }
            payload_mode = "text"
    base_payload = {
        "channel_kind": "telegram",
        "request_id": request_id,
        "session_id": session_id,
        "human_id": human_id,
        "agent_id": agent_id,
        "attachment_context": attachment_context,
        "invocation_surface": "telegram_direct_chip_command",
    }
    payload = dict(user_payload)
    for key, value in base_payload.items():
        payload.setdefault(key, value)
    return payload, payload_mode


def _parse_direct_chip_mutation_pairs(payload_text: str) -> dict[str, Any] | None:
    tokens = [token for token in str(payload_text or "").split() if token]
    if not tokens or not all("=" in token and not token.startswith("=") and not token.endswith("=") for token in tokens):
        return None
    mutations: dict[str, str] = {}
    for token in tokens:
        key, value = token.split("=", 1)
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if not cleaned_key or not cleaned_value:
            return None
        mutations[cleaned_key] = cleaned_value
    return {
        "candidate": {
            "mutations": mutations,
        }
    }


def _render_direct_chip_execution_reply(
    *,
    execution: Any,
    hook: str,
    payload_mode: str,
) -> str:
    if not getattr(execution, "ok", False):
        return (
            f"Chip `{execution.chip_key}` `{hook}` failed.\n"
            f"Reason: {_with_terminal_period(_extract_chip_execution_error(execution))}"
        )
    output = execution.output if isinstance(getattr(execution, "output", None), dict) else {}
    result = output.get("result") if isinstance(output.get("result"), dict) else {}
    reply_text = str(result.get("reply_text") or output.get("reply_text") or "").strip()
    if reply_text:
        return reply_text
    if hook == "evaluate":
        return _render_direct_chip_evaluate_reply(
            chip_key=str(execution.chip_key),
            output=output,
            payload_mode=payload_mode,
        )
    if hook == "suggest":
        return _render_direct_chip_suggest_reply(
            chip_key=str(execution.chip_key),
            output=output,
            payload_mode=payload_mode,
        )
    visible_output = json.dumps(output, indent=2, sort_keys=True)[:1500] if output else "No result payload."
    return (
        f"Chip `{execution.chip_key}` `{hook}` completed.\n"
        f"Input mode: {payload_mode}.\n"
        f"Output:\n{visible_output}"
    )


def _render_direct_chip_evaluate_reply(
    *,
    chip_key: str,
    output: dict[str, Any],
    payload_mode: str,
) -> str:
    result = output.get("result") if isinstance(output.get("result"), dict) else {}
    metrics = output.get("metrics") if isinstance(output.get("metrics"), dict) else {}
    lines = [f"Chip `{chip_key}` evaluate completed.", f"Input mode: {payload_mode}."]
    if result:
        if result.get("claim"):
            lines.append(f"Claim: {_with_terminal_period(str(result.get('claim')))}")
        if result.get("verdict"):
            lines.append(f"Verdict: {str(result.get('verdict'))}.")
        if result.get("mechanism"):
            lines.append(f"Mechanism: {_with_terminal_period(str(result.get('mechanism')))}")
        if result.get("boundary"):
            lines.append(f"Boundary: {_with_terminal_period(str(result.get('boundary')))}")
        if result.get("recommended_next_step"):
            lines.append(f"Recommended next step: {str(result.get('recommended_next_step'))}.")
    if metrics:
        metric_bits = []
        for key in (
            "profitability_score",
            "sharpe_ratio",
            "max_drawdown",
            "win_rate",
            "paper_trade_readiness",
            "verdict_confidence",
        ):
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                metric_bits.append(f"{key}={float(value):.4f}")
        if metric_bits:
            lines.append("Metrics: " + ", ".join(metric_bits) + ".")
    lines.append(
        "Scope: this was a direct chip hook run only, so no Swarm insight, autoloop session, or GitHub delivery was created."
    )
    return "\n".join(lines)


def _render_direct_chip_suggest_reply(
    *,
    chip_key: str,
    output: dict[str, Any],
    payload_mode: str,
) -> str:
    suggestions = output.get("suggestions") if isinstance(output.get("suggestions"), list) else []
    reasons = output.get("reasons") if isinstance(output.get("reasons"), list) else []
    lines = [f"Chip `{chip_key}` suggest completed.", f"Input mode: {payload_mode}."]
    if reasons:
        lines.append(f"Guidance: {_with_terminal_period(str(reasons[0]))}")
    if not suggestions:
        lines.append("No suggestions were returned.")
    else:
        lines.append(f"Suggestions: {len(suggestions)} candidate(s).")
        for item in suggestions[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {str(item.get('candidate_id') or 'candidate')}: "
                f"{str(item.get('candidate_summary') or item.get('hypothesis') or 'no summary')}"
            )
    lines.append(
        "Scope: this was a direct chip hook run only, so no Swarm insight, autoloop session, or GitHub delivery was created."
    )
    return "\n".join(lines)


def _extract_chip_execution_error(execution: Any) -> str:
    output = execution.output if isinstance(getattr(execution, "output", None), dict) else {}
    result = output.get("result") if isinstance(output.get("result"), dict) else {}
    for candidate in (
        result.get("error") if isinstance(result, dict) else None,
        output.get("error"),
        getattr(execution, "stderr", ""),
        getattr(execution, "stdout", ""),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return "chip command failed without a detailed error message"


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


def _render_swarm_doctor_reply(report: Any) -> str:
    blockers = list(getattr(report, "blockers", []) or [])
    recommendations = list(getattr(report, "recommendations", []) or [])
    next_step = recommendations[0] if recommendations else "No next action suggested."
    lines = [
        f"Swarm doctor: {'blocked' if blockers else 'ready'}.",
        f"Auth source: {getattr(report, 'auth_source', 'unknown')}. Payload source: {getattr(report, 'payload_source', 'missing')}.",
        f"Active path: {getattr(report, 'active_path_key', None) or 'none'}. Repo: {getattr(report, 'active_path_repo_root', None) or 'missing'}.",
        f"Scenario: {getattr(report, 'scenario_path', None) or 'missing'}. Mutation target: {getattr(report, 'mutation_target_path', None) or 'missing'}.",
    ]
    if blockers:
        lines.append(f"Blocker: {blockers[0]}")
    else:
        lines.append("No blockers detected.")
    lines.append(f"Next: {next_step}")
    return "\n".join(lines)


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


def _parse_chip_command(inbound_text: str) -> dict[str, Any] | None:
    normalized = " ".join(str(inbound_text or "").strip().split())
    if not normalized:
        return None
    if normalized.lower() == "/chip":
        return {"command": "/chip"}
    match = re.match(r"^/chip status(?: (?P<chip_key>[A-Za-z0-9:_-]+))?$", normalized, flags=re.IGNORECASE)
    if match:
        return {
            "command": "/chip status",
            "chip_key": str(match.group("chip_key") or "").strip() or None,
        }
    match = re.match(r"^/chip autoloop (?P<chip_key>[A-Za-z0-9:_-]+)$", normalized, flags=re.IGNORECASE)
    if match:
        return {
            "command": "/chip autoloop",
            "chip_key": str(match.group("chip_key")),
        }
    match = re.match(
        r"^/chip (?P<hook>evaluate|suggest) (?P<chip_key>[A-Za-z0-9:_-]+)(?: (?P<payload>.+))?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        hook = str(match.group("hook") or "").lower()
        return {
            "command": f"/chip {hook}",
            "hook": hook,
            "chip_key": str(match.group("chip_key") or "").strip(),
            "payload": str(match.group("payload") or "").strip(),
        }
    return None


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
        "swarm doctor",
        "show swarm doctor",
        "show me swarm doctor",
        "run swarm doctor",
        "check swarm doctor",
        "diagnose swarm",
        "diagnose spark swarm",
    }:
        return ("/swarm doctor", None)

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
    # Pass agent_name as-is (possibly empty). build_telegram_surface_identity_preamble
    # handles the empty-name case with a name-free welcome under the Phase 1
    # empty-string sentinel regime.
    welcome_text = build_telegram_surface_identity_preamble(
        profile=profile,
        agent_name=agent_name,
        surface="approval_welcome",
    )
    return f"{welcome_text}\n\n{styled_reply}".strip()


def _resolution_reply_text(*, decision: str, default_text: str, inbound_text: str) -> str:
    if decision == "channel_paused":
        return "Spark Intelligence is temporarily paused for this Telegram channel. Try again later."
    if decision == "channel_disabled":
        return "Spark Intelligence is currently disabled for this Telegram channel."
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
