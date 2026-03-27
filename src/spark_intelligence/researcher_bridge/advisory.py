from __future__ import annotations

import importlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import (
    build_attachment_context,
    record_chip_hook_execution,
    run_first_active_chip_hook,
    screen_chip_hook_text,
)
from spark_intelligence.auth.runtime import RuntimeProviderResolution, resolve_runtime_provider
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.llm.direct_provider import (
    DirectProviderGovernance,
    DirectProviderRequest,
    execute_direct_provider_prompt,
)
from spark_intelligence.observability.store import record_environment_snapshot, record_event, record_quarantine
from spark_intelligence.personality import (
    build_personality_context,
    build_preference_acknowledgment,
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
    maybe_evolve_traits,
    record_observation,
)
from spark_intelligence.personality.loader import build_personality_system_directive
from spark_intelligence.state.db import StateDB


@dataclass
class ResearcherBridgeResult:
    request_id: str
    reply_text: str
    evidence_summary: str
    escalation_hint: str | None
    trace_ref: str
    mode: str
    runtime_root: str | None
    config_path: str | None
    attachment_context: dict[str, object] | None
    provider_id: str | None = None
    provider_auth_profile_id: str | None = None
    provider_auth_method: str | None = None
    provider_model: str | None = None
    provider_model_family: str | None = None
    provider_execution_transport: str | None = None
    provider_base_url: str | None = None
    provider_source: str | None = None
    routing_decision: str | None = None
    active_chip_key: str | None = None
    active_chip_task_type: str | None = None
    active_chip_evaluate_used: bool = False


@dataclass
class ResearcherBridgeStatus:
    enabled: bool
    configured: bool
    available: bool
    mode: str
    runtime_root: str | None
    config_path: str | None
    attachment_context: dict[str, object]
    last_mode: str | None
    last_trace_ref: str | None
    last_request_id: str | None
    last_runtime_root: str | None
    last_config_path: str | None
    last_evidence_summary: str | None
    last_attachment_context: dict[str, Any] | None
    last_provider_id: str | None
    last_provider_model: str | None
    last_provider_model_family: str | None
    last_provider_auth_method: str | None
    last_provider_execution_transport: str | None
    last_routing_decision: str | None
    last_active_chip_key: str | None
    last_active_chip_task_type: str | None
    last_active_chip_evaluate_used: bool
    failure_count: int
    last_failure: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "configured": self.configured,
                "available": self.available,
                "enabled": self.enabled,
                "mode": self.mode,
                "runtime_root": self.runtime_root,
                "config_path": self.config_path,
                "attachment_context": self.attachment_context,
                "last_mode": self.last_mode,
                "last_trace_ref": self.last_trace_ref,
                "last_request_id": self.last_request_id,
                "last_runtime_root": self.last_runtime_root,
                "last_config_path": self.last_config_path,
                "last_evidence_summary": self.last_evidence_summary,
                "last_attachment_context": self.last_attachment_context,
                "last_provider_id": self.last_provider_id,
                "last_provider_model": self.last_provider_model,
                "last_provider_model_family": self.last_provider_model_family,
                "last_provider_auth_method": self.last_provider_auth_method,
                "last_provider_execution_transport": self.last_provider_execution_transport,
                "last_routing_decision": self.last_routing_decision,
                "last_active_chip_key": self.last_active_chip_key,
                "last_active_chip_task_type": self.last_active_chip_task_type,
                "last_active_chip_evaluate_used": self.last_active_chip_evaluate_used,
                "failure_count": self.failure_count,
                "last_failure": self.last_failure,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Researcher bridge enabled: {'yes' if self.enabled else 'no'}",
            f"Researcher bridge configured: {'yes' if self.configured else 'no'}",
            f"- available: {'yes' if self.available else 'no'}",
            f"- mode: {self.mode}",
            f"- runtime_root: {self.runtime_root or 'missing'}",
            f"- config_path: {self.config_path or 'missing'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- pinned_chip_keys: {', '.join(self.attachment_context.get('pinned_chip_keys', [])) if self.attachment_context.get('pinned_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
            f"- last_mode: {self.last_mode or 'none'}",
            f"- last_trace_ref: {self.last_trace_ref or 'none'}",
            f"- last_request_id: {self.last_request_id or 'none'}",
        ]
        if self.last_runtime_root:
            lines.append(f"- last_runtime_root: {self.last_runtime_root}")
        if self.last_config_path:
            lines.append(f"- last_config_path: {self.last_config_path}")
        if self.last_evidence_summary:
            lines.append(f"- last_evidence_summary: {self.last_evidence_summary}")
        if self.last_provider_id:
            lines.append(f"- last_provider_id: {self.last_provider_id}")
        if self.last_provider_model:
            lines.append(f"- last_provider_model: {self.last_provider_model}")
        if self.last_provider_model_family:
            lines.append(f"- last_provider_model_family: {self.last_provider_model_family}")
        if self.last_provider_auth_method:
            lines.append(f"- last_provider_auth_method: {self.last_provider_auth_method}")
        if self.last_provider_execution_transport:
            lines.append(f"- last_provider_execution_transport: {self.last_provider_execution_transport}")
        if self.last_routing_decision:
            lines.append(f"- last_routing_decision: {self.last_routing_decision}")
        if self.last_active_chip_key:
            lines.append(f"- last_active_chip_key: {self.last_active_chip_key}")
        if self.last_active_chip_task_type:
            lines.append(f"- last_active_chip_task_type: {self.last_active_chip_task_type}")
        lines.append(f"- last_active_chip_evaluate_used: {'yes' if self.last_active_chip_evaluate_used else 'no'}")
        lines.append(f"- failure_count: {self.failure_count}")
        if self.last_failure:
            lines.append(
                f"- last_failure: mode={self.last_failure.get('mode')} "
                f"at={self.last_failure.get('recorded_at')} "
                f"message={self.last_failure.get('message')}"
            )
        return "\n".join(lines)


def discover_researcher_runtime_root(config_manager: ConfigManager) -> tuple[Path | None, str]:
    config = config_manager.load()
    configured_root = config.get("spark", {}).get("researcher", {}).get("runtime_root")
    if configured_root:
        path = Path(str(configured_root)).expanduser()
        return (path if path.exists() else None, "configured")

    autodetect = Path.home() / "Desktop" / "spark-researcher"
    if autodetect.exists():
        return autodetect, "autodiscovered"
    return None, "missing"


def resolve_researcher_config_path(config_manager: ConfigManager, runtime_root: Path) -> Path:
    config = config_manager.load()
    configured_path = config.get("spark", {}).get("researcher", {}).get("config_path")
    if configured_path:
        return Path(str(configured_path)).expanduser()
    return runtime_root / "spark-researcher.project.json"


def _import_build_advisory(runtime_root: Path):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module("spark_researcher.advisory")
    return getattr(module, "build_advisory")


def _import_execute_with_research(runtime_root: Path):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module("spark_researcher.research")
    return getattr(module, "execute_with_research")


def _render_reply_from_advisory(advisory: dict) -> tuple[str, str, str]:
    guidance = advisory.get("guidance") or []
    epistemic = advisory.get("epistemic_status") or {}
    selected_packet_ids = advisory.get("selected_packet_ids") or []
    trace_ref = advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing"

    guidance_lines = guidance[:2] if isinstance(guidance, list) else []
    if guidance_lines:
        reply_text = " ".join(str(item).strip() for item in guidance_lines if str(item).strip())
    else:
        reply_text = "Spark Researcher returned no concrete guidance for this message."

    evidence_summary = (
        f"status={epistemic.get('status', 'unknown')} "
        f"packets={len(selected_packet_ids)} "
        f"stability={((epistemic.get('packet_stability') or {}).get('status', 'unknown'))}"
    )
    return reply_text, evidence_summary, str(trace_ref)


def _render_reply_from_execution(execution: dict[str, Any], advisory: dict[str, Any]) -> tuple[str, str, str]:
    response = execution.get("response")
    reply_text = ""
    if isinstance(response, dict):
        raw = response.get("raw_response")
        if isinstance(raw, str):
            reply_text = raw.strip()
    elif isinstance(response, str):
        reply_text = response.strip()

    if not reply_text:
        reply_text, _, _ = _render_reply_from_advisory(advisory)

    decision = str(execution.get("decision") or execution.get("status") or "unknown")
    trace_ref = (
        str(execution.get("research_trace_path") or "")
        or str(execution.get("trace_path") or "")
        or str(advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing")
    )
    evidence_summary = f"status={decision} provider_execution=yes"
    return reply_text, evidence_summary, trace_ref


def _researcher_routing_policy(config_manager: ConfigManager) -> dict[str, Any]:
    return {
        "conversational_fallback_enabled": bool(
            config_manager.get_path("spark.researcher.routing.conversational_fallback_enabled", default=True)
        ),
        "conversational_fallback_max_chars": int(
            config_manager.get_path("spark.researcher.routing.conversational_fallback_max_chars", default=240)
        ),
    }


def _is_conversational_fallback_candidate(
    *,
    user_message: str,
    advisory: dict[str, Any],
    fallback_max_chars: int,
) -> bool:
    epistemic = advisory.get("epistemic_status", {}) if isinstance(advisory, dict) else {}
    if str(epistemic.get("status") or "") != "under_supported":
        return False
    lowered = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not lowered:
        return False
    if len(lowered) > fallback_max_chars:
        return False
    if any(char.isdigit() for char in lowered):
        return False
    blocked_terms = (
        "http://",
        "https://",
        "latest",
        "today",
        "news",
        "price",
        "stock",
        "lawsuit",
        "legal",
        "medical",
        "diagnose",
        "treatment",
        "tax",
        "invest",
        "contract",
        "traceback",
        "exception",
        "stack trace",
        "error code",
    )
    if any(term in lowered for term in blocked_terms):
        return False
    if lowered in {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "what's up",
        "whats up",
        "how are you",
        "how are you doing",
        "good morning",
        "good afternoon",
        "good evening",
    }:
        return True
    tokens = lowered.split()
    return len(tokens) <= 10


def _render_direct_provider_chat_fallback(
    *,
    state_db: StateDB,
    provider: RuntimeProviderResolution,
    user_message: str,
    channel_kind: str,
    attachment_context: dict[str, object],
    active_chip_evaluate: dict[str, Any] | None = None,
    personality_profile: dict[str, Any] | None = None,
    personality_context_extra: str = "",
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
) -> str:
    base_system_prompt = (
        "You are Spark AGI in a 1:1 messaging conversation. "
        "Reply naturally, briefly, and helpfully. "
        "For casual greetings or small talk, respond like a normal assistant. "
        "When domain chip guidance is attached, treat it as hidden background context rather than an output template. "
        "Do not echo internal headings, confidence scores, packet ids, doctrine labels, or evidence-gap sections unless the user explicitly asks for them. "
        "For Telegram-style DMs, prefer a short paragraph or a short flat list over memo formatting. "
        "If the user asks for factual, legal, medical, financial, or time-sensitive guidance "
        "and you are not confident, say you need more context or verification before giving a hard answer. "
        "Do not mention internal advisory or verification systems."
    )
    if personality_profile:
        personality_directive = build_personality_system_directive(personality_profile)
        if personality_directive:
            base_system_prompt = f"{base_system_prompt} {personality_directive}"
    payload = execute_direct_provider_prompt(
        provider=DirectProviderRequest(
            provider_id=provider.provider_id,
            provider_kind=provider.provider_kind,
            auth_method=provider.auth_method,
            api_mode=provider.api_mode,
            base_url=provider.base_url,
            model=provider.default_model,
            secret_value=provider.secret_value,
        ),
        system_prompt=base_system_prompt,
        user_prompt=_build_contextual_task(
            user_message=(
                f"[channel_kind={channel_kind}]\n"
                f"[fallback_mode=conversational_under_supported]\n"
                f"{user_message}"
            ),
            attachment_context=attachment_context,
            active_chip_evaluate=active_chip_evaluate,
                personality_profile=personality_profile,
                personality_context_extra=personality_context_extra,
            ),
        governance=DirectProviderGovernance(
            state_db_path=str(state_db.path),
            source_kind="researcher_bridge_direct_prompt",
            source_ref=request_id or provider.provider_id,
            summary="Builder blocked direct-provider fallback context before execution.",
            reason_code="provider_fallback_prompt_secret_like",
            policy_domain="researcher_bridge",
            blocked_stage="pre_model",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            provenance={
                "source_kind": "researcher_bridge",
                "source_ref": provider.provider_id,
                "channel_kind": channel_kind,
                "active_chip_key": active_chip_evaluate.get("chip_key") if active_chip_evaluate else None,
                "active_path_key": attachment_context.get("active_path_key"),
                "personality_name": personality_profile.get("personality_name") if personality_profile else None,
            },
        ),
    )
    raw_response = str(payload.get("raw_response") or "").strip()
    if not raw_response:
        raise RuntimeError("Direct provider fallback returned no text content.")
    return raw_response


def _maybe_apply_swarm_recommendation(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
    channel_kind: str,
    reply_text: str,
    evidence_summary: str,
    routing_decision: str | None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[str, str, str | None, str | None]:
    try:
        from spark_intelligence.swarm_bridge import evaluate_swarm_escalation
    except Exception:  # pragma: no cover - defensive import guard
        return reply_text, evidence_summary, None, routing_decision

    decision = evaluate_swarm_escalation(
        config_manager=config_manager,
        state_db=state_db,
        task=user_message,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id="researcher_bridge",
    )
    if not decision.escalate or decision.mode != "manual_recommended":
        return reply_text, evidence_summary, None, routing_decision

    triggers = ", ".join(decision.triggers) if decision.triggers else "explicit request"
    escalation_hint = decision.mode
    next_line = (
        "Swarm: recommended for this task because it asks for delegation or multi-agent work "
        f"({triggers})."
    )
    if channel_kind == "telegram":
        reply_text = f"{reply_text}\n\n{next_line}"
    evidence_summary = f"{evidence_summary} swarm={decision.mode}"
    if routing_decision:
        routing_decision = f"{routing_decision}+{decision.mode}"
    else:
        routing_decision = decision.mode
    return reply_text, evidence_summary, escalation_hint, routing_decision


def _build_contextual_task(
    *,
    user_message: str,
    attachment_context: dict[str, object],
    active_chip_evaluate: dict[str, Any] | None = None,
    personality_profile: dict[str, Any] | None = None,
    personality_context_extra: str = "",
) -> str:
    active_chip_keys = attachment_context.get("active_chip_keys") or []
    pinned_chip_keys = attachment_context.get("pinned_chip_keys") or []
    active_path_key = attachment_context.get("active_path_key") or None
    lines = [
        "[Spark Intelligence context]",
        f"active_chip_keys={','.join(str(item) for item in active_chip_keys) if active_chip_keys else 'none'}",
        f"pinned_chip_keys={','.join(str(item) for item in pinned_chip_keys) if pinned_chip_keys else 'none'}",
        f"active_path_key={active_path_key or 'none'}",
        "",
    ]
    if personality_profile:
        personality_ctx = build_personality_context(personality_profile)
        if personality_ctx:
            lines.extend([personality_ctx, ""])
    if personality_context_extra:
        lines.extend([personality_context_extra, ""])
    if active_chip_evaluate:
        chip_guidance = _summarize_active_chip_guidance(str(active_chip_evaluate.get("analysis") or ""))
        lines.extend(
            [
                "[Active chip guidance]",
                f"chip_key={active_chip_evaluate.get('chip_key') or 'unknown'}",
                f"task_type={active_chip_evaluate.get('task_type') or 'unknown'}",
                f"stage={active_chip_evaluate.get('stage') or 'unknown'}",
                (
                    "Use this guidance as background context only. "
                    "Do not copy its headings, confidence labels, packet ids, or memo structure into the user-visible reply."
                ),
            ]
        )
        if active_chip_evaluate.get("stage_transition_suggested"):
            lines.append(
                f"possible_stage_transition={active_chip_evaluate['stage_transition_suggested']} "
                "(confirm with the user before treating it as true)"
            )
        if active_chip_evaluate.get("detected_state_updates"):
            lines.append(
                "possible_state_updates="
                + json.dumps(active_chip_evaluate["detected_state_updates"], sort_keys=True)
                + " (confirm with the user before treating them as true)"
            )
        if chip_guidance:
            lines.extend(["", chip_guidance, ""])
    lines.extend(
        [
        "[User message]",
        user_message,
        ]
    )
    return "\n".join(lines)


def _summarize_active_chip_guidance(analysis: str, *, max_lines: int = 4, max_chars: int = 700) -> str:
    cleaned_lines: list[str] = []
    for raw_line in analysis.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^\*\*(.*?)\*\*$", r"\1", line)
        line = re.sub(r"^[-*]\s*", "", line)
        lowered = line.lower().rstrip(":")
        if lowered in {"primary focus", "why this works", "what changes this", "next step"}:
            continue
        if lowered.startswith(("confidence:", "evidence gap:", "note:")):
            continue
        if _is_operational_residue_line(line):
            continue
        if lowered.startswith("recommendation:") or lowered.startswith("revised:"):
            _, _, remainder = line.partition(":")
            line = remainder.strip()
            if not line:
                continue
        cleaned_lines.append(line)
        if len(cleaned_lines) >= max_lines:
            break
    summary = "\n".join(cleaned_lines).strip()
    if len(summary) > max_chars:
        summary = f"{summary[: max_chars - 3].rstrip()}..."
    return summary


def _clean_messaging_reply(text: str, *, channel_kind: str) -> str:
    cleaned, _ = _clean_messaging_reply_with_metadata(text, channel_kind=channel_kind)
    return cleaned


def _clean_messaging_reply_with_metadata(text: str, *, channel_kind: str) -> tuple[str, list[str]]:
    if channel_kind != "telegram":
        return _strip_operational_residue_lines(text.strip())
    rewritten = _rewrite_structured_telegram_reply(text)
    if rewritten:
        text = rewritten
    cleaned_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line == "---":
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        normalized = re.sub(r"^#+\s*", "", line)
        normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
        normalized_for_meta = re.sub(r"^[-*]\s*", "", normalized)
        lowered = normalized_for_meta.lower().rstrip(":")
        if lowered in {"primary focus", "why this works", "what changes this", "next step"}:
            continue
        if lowered.startswith(("confidence:", "evidence gap:", "note: advisory")):
            continue
        if lowered.startswith("recommendation:") or lowered.startswith("revised:"):
            _, _, remainder = normalized_for_meta.partition(":")
            normalized = remainder.strip()
            if not normalized:
                continue
        cleaned_lines.append(normalized)
    reply = "\n".join(cleaned_lines)
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()
    reply = _strip_internal_reply_prefixes(reply)
    sanitized, removed_lines = _strip_operational_residue_lines(reply or text.strip())
    return sanitized or text.strip(), removed_lines


def _strip_operational_residue_lines(text: str) -> tuple[str, list[str]]:
    kept_lines: list[str] = []
    removed_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if line and _is_operational_residue_line(line):
            removed_lines.append(line)
            continue
        kept_lines.append(raw_line)
    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, removed_lines


def _is_operational_residue_line(line: str) -> bool:
    lowered = re.sub(r"^[-*]\s*", "", line.strip()).lower()
    explicit_prefixes = (
        "trace_ref:",
        "trace_path:",
        "trace_id:",
        "selected_packet_ids",
        "packet_refs:",
        "memory_refs:",
        "followup_actions:",
        "quarantine_id:",
        "runtime_root:",
        "config_path:",
        "recorded_at:",
        "epistemic_status:",
    )
    if lowered.startswith(explicit_prefixes):
        return True
    if "trace:" in lowered and any(token in lowered for token in ("trace", "research_trace_path", "trace_ref", "trace_path")):
        return True
    if any(token in lowered for token in ("packet_refs", "memory_refs", "selected_packet_ids", "followup_actions", "quarantine_id")):
        return True
    return False


def _rewrite_structured_telegram_reply(text: str) -> str | None:
    sections: dict[str, list[str]] = {
        "recommendation": [],
        "primary": [],
        "why": [],
        "changes": [],
        "next": [],
        "body": [],
    }
    current_section = "body"
    saw_structured_markers = False

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line == "---":
            continue
        normalized = re.sub(r"^#+\s*", "", line)
        normalized = re.sub(r"^\*\*(.*?)\*\*\s*:\s*", r"\1: ", normalized)
        normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
        normalized_for_meta = re.sub(r"^[-*]\s*", "", normalized).strip()
        lowered = normalized_for_meta.lower().rstrip(":")

        if lowered in {"primary focus", "why this works", "what changes this", "next step"}:
            saw_structured_markers = True
            current_section = {
                "primary focus": "primary",
                "why this works": "why",
                "what changes this": "changes",
                "next step": "next",
            }[lowered]
            continue
        if lowered.startswith(("confidence:", "evidence gap:", "note:")):
            saw_structured_markers = True
            continue
        if lowered.startswith("revised:"):
            saw_structured_markers = True
            continue
        if lowered.startswith("recommendation:"):
            saw_structured_markers = True
            _, _, remainder = normalized_for_meta.partition(":")
            candidate = remainder.strip()
            if candidate:
                sections["recommendation"].append(candidate)
            continue
        sections[current_section].append(normalized_for_meta)

    if not saw_structured_markers:
        return None

    lead = _first_distinct_line(
        sections["recommendation"] + sections["primary"] + sections["body"] + sections["why"] + sections["changes"]
    )
    if not lead:
        return None

    parts = [_ensure_terminal_punctuation(lead)]
    support_lines = _distinct_lines(
        sections["primary"] + sections["why"] + sections["changes"] + sections["body"],
        exclude={lead},
    )
    if support_lines:
        parts.append(" ".join(_ensure_terminal_punctuation(item) for item in support_lines[:2]))
    next_step = _first_distinct_line(sections["next"])
    if next_step:
        parts.append(f"Next: {_strip_terminal_punctuation(next_step)}.")
    return "\n\n".join(part for part in parts if part).strip() or None


def _strip_internal_reply_prefixes(text: str) -> str:
    patterns = (
        r"^based on (?:the )?(?:research|researcher) notes(?: provided)?[,:\-]\s*",
        r"^according to (?:the )?(?:research|researcher) notes[,:\-]\s*",
        r"^from (?:the )?(?:research|researcher) notes[,:\-]\s*",
        r"^the (?:research|researcher) notes (?:show|suggest|indicate) that\s*",
    )
    cleaned = text.strip()
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _first_distinct_line(lines: list[str]) -> str | None:
    for line in lines:
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def _distinct_lines(lines: list[str], *, exclude: set[str] | None = None) -> list[str]:
    excluded = {item.strip().lower() for item in (exclude or set()) if item.strip()}
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        candidate = line.strip()
        if not candidate:
            continue
        normalized = candidate.lower()
        if normalized in seen or normalized in excluded:
            continue
        seen.add(normalized)
        output.append(candidate)
    return output


def _ensure_terminal_punctuation(text: str) -> str:
    value = text.strip()
    if not value:
        return value
    if value[-1] in ".!?":
        return value
    return f"{value}."


def _strip_terminal_punctuation(text: str) -> str:
    return text.strip().rstrip(".!?")


def _run_active_chip_evaluate(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    request_id: str,
    channel_kind: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    user_message: str,
    attachment_context: dict[str, object],
    run_id: str | None = None,
) -> dict[str, Any] | None:
    payload = {
        "situation": user_message,
        "conversation_history": "",
        "channel_kind": channel_kind,
        "request_id": request_id,
        "agent_id": agent_id,
        "human_id": human_id,
        "session_id": session_id,
        "attachment_context": attachment_context,
    }
    try:
        execution = run_first_active_chip_hook(config_manager, hook="evaluate", payload=payload)
    except Exception:
        return None
    if not execution or not execution.ok:
        return None
    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="researcher_bridge",
        actor_id="researcher_bridge",
        summary="Researcher bridge executed an active chip hook before bridge execution.",
        reason_code="active_chip_evaluate",
        keepability="ephemeral_context",
        run_id=run_id,
        request_id=request_id,
        channel_id=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    result = execution.output.get("result")
    if not isinstance(result, dict):
        return None
    analysis = str(result.get("analysis") or "").strip()
    if not analysis:
        return None
    screened = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=analysis,
        summary="Chip guidance was quarantined before model-visible prompt assembly.",
        reason_code="chip_guidance_secret_like",
        policy_domain="researcher_bridge",
        blocked_stage="pre_model",
        run_id=run_id,
        request_id=request_id,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
    )
    if not screened["allowed"]:
        return {
            "chip_key": execution.chip_key,
            "analysis": "",
            "task_type": result.get("task_type"),
            "stage": result.get("stage"),
            "context_packet_ids": result.get("context_packet_ids") or [],
            "activations": result.get("activations") or [],
            "detected_state_updates": result.get("detected_state_updates") or [],
            "stage_transition_suggested": result.get("stage_transition_suggested"),
            "quarantined": True,
            "quarantine_id": screened["quarantine_id"],
        }
    return {
        "chip_key": execution.chip_key,
        "analysis": analysis,
        "task_type": result.get("task_type"),
        "stage": result.get("stage"),
        "context_packet_ids": result.get("context_packet_ids") or [],
        "activations": result.get("activations") or [],
        "detected_state_updates": result.get("detected_state_updates") or [],
        "stage_transition_suggested": result.get("stage_transition_suggested"),
    }


def researcher_bridge_status(*, config_manager: ConfigManager, state_db: StateDB) -> ResearcherBridgeStatus:
    attachment_context = build_attachment_context(config_manager)
    runtime_root, runtime_source = discover_researcher_runtime_root(config_manager)
    config_path = resolve_researcher_config_path(config_manager, runtime_root) if runtime_root else None
    enabled = bool(config_manager.get_path("spark.researcher.enabled", default=True))
    available = enabled and bool(runtime_root and config_path and config_path.exists())
    mode = "disabled" if not enabled else (f"external_{runtime_source}" if available else "stub")
    runtime_state = _read_runtime_state(state_db)
    return ResearcherBridgeStatus(
        enabled=enabled,
        configured=runtime_root is not None,
        available=available,
        mode=mode,
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(config_path) if config_path else None,
        attachment_context=attachment_context,
        last_mode=runtime_state.get("researcher:last_mode"),
        last_trace_ref=runtime_state.get("researcher:last_trace_ref"),
        last_request_id=runtime_state.get("researcher:last_request_id"),
        last_runtime_root=runtime_state.get("researcher:last_runtime_root"),
        last_config_path=runtime_state.get("researcher:last_config_path"),
        last_evidence_summary=runtime_state.get("researcher:last_evidence_summary"),
        last_attachment_context=_loads_json(runtime_state.get("researcher:last_attachment_context")),
        last_provider_id=runtime_state.get("researcher:last_provider_id"),
        last_provider_model=runtime_state.get("researcher:last_provider_model"),
        last_provider_model_family=runtime_state.get("researcher:last_provider_model_family"),
        last_provider_auth_method=runtime_state.get("researcher:last_provider_auth_method"),
        last_provider_execution_transport=runtime_state.get("researcher:last_provider_execution_transport"),
        last_routing_decision=runtime_state.get("researcher:last_routing_decision"),
        last_active_chip_key=runtime_state.get("researcher:last_active_chip_key"),
        last_active_chip_task_type=runtime_state.get("researcher:last_active_chip_task_type"),
        last_active_chip_evaluate_used=_parse_bool(runtime_state.get("researcher:last_active_chip_evaluate_used")),
        failure_count=_parse_int(runtime_state.get("researcher:failure_count")),
        last_failure=_loads_json(runtime_state.get("researcher:last_failure")),
    )


def record_researcher_bridge_result(*, state_db: StateDB, result: ResearcherBridgeResult) -> None:
    with state_db.connect() as conn:
        _set_runtime_state(conn, "researcher:last_mode", result.mode)
        _set_runtime_state(conn, "researcher:last_trace_ref", result.trace_ref)
        _set_runtime_state(conn, "researcher:last_request_id", result.request_id)
        _set_runtime_state(conn, "researcher:last_runtime_root", result.runtime_root or "")
        _set_runtime_state(conn, "researcher:last_config_path", result.config_path or "")
        _set_runtime_state(conn, "researcher:last_evidence_summary", result.evidence_summary)
        _set_runtime_state(conn, "researcher:last_provider_id", result.provider_id or "")
        _set_runtime_state(conn, "researcher:last_provider_model", result.provider_model or "")
        _set_runtime_state(conn, "researcher:last_provider_model_family", result.provider_model_family or "")
        _set_runtime_state(conn, "researcher:last_provider_auth_method", result.provider_auth_method or "")
        _set_runtime_state(
            conn,
            "researcher:last_provider_execution_transport",
            result.provider_execution_transport or "",
        )
        _set_runtime_state(conn, "researcher:last_routing_decision", result.routing_decision or "")
        _set_runtime_state(conn, "researcher:last_active_chip_key", result.active_chip_key or "")
        _set_runtime_state(conn, "researcher:last_active_chip_task_type", result.active_chip_task_type or "")
        _set_runtime_state(
            conn,
            "researcher:last_active_chip_evaluate_used",
            "1" if result.active_chip_evaluate_used else "0",
        )
        _set_runtime_state(
            conn,
            "researcher:last_attachment_context",
            json.dumps(result.attachment_context or {}, sort_keys=True),
        )
        if result.mode == "bridge_error":
            failure_count = _read_failure_count(conn, "researcher:failure_count")
            _set_runtime_state(conn, "researcher:failure_count", str(failure_count + 1))
            _set_runtime_state(
                conn,
                "researcher:last_failure",
                json.dumps(
                    {
                        "mode": result.mode,
                        "request_id": result.request_id,
                        "runtime_root": result.runtime_root,
                        "config_path": result.config_path,
                        "message": result.reply_text,
                        "recorded_at": _utc_now_iso(),
                    },
                    sort_keys=True,
                ),
            )
        conn.commit()


def build_researcher_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    request_id: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    channel_kind: str,
    user_message: str,
    run_id: str | None = None,
) -> ResearcherBridgeResult:
    attachment_context = build_attachment_context(config_manager)

    # ── Personality integration ──
    personality_profile = None
    personality_context_extra = ""  # extra context for acknowledgments/queries
    personality_query_kind = "none"
    evolved_deltas = None
    observation_record = None
    try:
        personality_profile = load_personality_profile(
            human_id=human_id,
            state_db=state_db,
            config_manager=config_manager,
        )
    except Exception:
        pass

    # Check for personality queries (status, reset) before NL detection
    try:
        query_result = detect_personality_query(
            user_message=user_message,
            human_id=human_id,
            state_db=state_db,
            profile=personality_profile,
        )
        if query_result.kind != "none":
            personality_query_kind = query_result.kind
            personality_context_extra = query_result.context_injection
            if query_result.kind == "reset":
                # Reload profile after reset
                personality_profile = load_personality_profile(
                    human_id=human_id,
                    state_db=state_db,
                    config_manager=config_manager,
                )
    except Exception:
        pass

    # Detect NL personality preferences and persist per-user deltas
    nl_pref_enabled = config_manager.get_path("spark.personality.nl_preference_detection", default=True)
    detected_deltas = None
    if nl_pref_enabled and not personality_context_extra:
        try:
            detected_deltas = detect_and_persist_nl_preferences(
                human_id=human_id,
                user_message=user_message,
                state_db=state_db,
            )
            if detected_deltas:
                # Build acknowledgment context for the LLM
                personality_context_extra = build_preference_acknowledgment(detected_deltas)
                # Reload profile with updated deltas applied
                personality_profile = load_personality_profile(
                    human_id=human_id,
                    state_db=state_db,
                    config_manager=config_manager,
                )
        except Exception:
            pass

    # Periodically trigger self-evolution based on accumulated observations
    try:
        evolved_deltas = maybe_evolve_traits(human_id=human_id, state_db=state_db)
    except Exception:
        pass

    # Record observation for self-evolution (runs on every message)
    try:
        if personality_profile and personality_profile.get("traits"):
            observation_record = record_observation(
                human_id=human_id,
                user_message=user_message,
                traits_active=personality_profile["traits"],
                state_db=state_db,
            )
    except Exception:
        pass

    if personality_profile or personality_context_extra or detected_deltas or evolved_deltas or observation_record:
        source_kind = "personality_profile"
        if detected_deltas:
            source_kind = "personality_preference_update"
        elif personality_query_kind != "none":
            source_kind = f"personality_query_{personality_query_kind}"
        elif evolved_deltas:
            source_kind = "personality_evolution"
        record_event(
            state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="Personality influence was recorded before bridge execution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="personality_context_applied",
            facts={
                "personality_name": personality_profile.get("personality_name") if personality_profile else None,
                "personality_id": personality_profile.get("personality_id") if personality_profile else None,
                "personality_source": personality_profile.get("source") if personality_profile else None,
                "user_deltas_applied": bool(personality_profile.get("user_deltas_applied")) if personality_profile else False,
                "query_kind": personality_query_kind,
                "detected_deltas": detected_deltas or {},
                "evolved_deltas": evolved_deltas or {},
                "observation_state": (
                    observation_record.get("user_state")
                    if isinstance(observation_record, dict)
                    else None
                ),
                "observation_confidence": (
                    observation_record.get("confidence")
                    if isinstance(observation_record, dict)
                    else None
                ),
                "keepability": "user_preference_ephemeral",
            },
            provenance={
                "source_kind": source_kind,
                "source_ref": personality_profile.get("personality_id") if personality_profile else human_id,
            },
        )

    active_chip_evaluate = _run_active_chip_evaluate(
        config_manager=config_manager,
        state_db=state_db,
        request_id=request_id,
        channel_kind=channel_kind,
        agent_id=agent_id,
        human_id=human_id,
        session_id=session_id,
        user_message=user_message,
        attachment_context=attachment_context,
        run_id=run_id,
    )
    if attachment_context.get("active_chip_keys") or attachment_context.get("active_path_key") or active_chip_evaluate:
        record_event(
            state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="Attachment or chip influence was recorded before bridge execution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="attachment_context_applied",
            facts={
                "active_chip_keys": attachment_context.get("active_chip_keys") or [],
                "pinned_chip_keys": attachment_context.get("pinned_chip_keys") or [],
                "active_path_key": attachment_context.get("active_path_key"),
                "active_chip_key": active_chip_evaluate.get("chip_key") if active_chip_evaluate else None,
                "active_chip_task_type": active_chip_evaluate.get("task_type") if active_chip_evaluate else None,
                "keepability": "ephemeral_context",
            },
            provenance={
                "source_kind": "chip_hook" if active_chip_evaluate else "attachment_snapshot",
                "source_ref": active_chip_evaluate.get("chip_key") if active_chip_evaluate else "attachments",
            },
        )
    contextual_task = _build_contextual_task(
        user_message=user_message,
        attachment_context=attachment_context,
        active_chip_evaluate=active_chip_evaluate,
        personality_profile=personality_profile,
        personality_context_extra=personality_context_extra,
    )
    active_chip_key = str(active_chip_evaluate.get("chip_key")) if active_chip_evaluate else None
    active_chip_task_type = str(active_chip_evaluate.get("task_type")) if active_chip_evaluate and active_chip_evaluate.get("task_type") else None
    active_chip_evaluate_used = active_chip_evaluate is not None
    screened_context = screen_model_visible_text(
        state_db=state_db,
        source_kind="contextual_task",
        source_ref=request_id,
        text=contextual_task,
        summary="Builder blocked model-visible context before bridge execution.",
        reason_code="contextual_task_secret_like",
        policy_domain="researcher_bridge",
        run_id=run_id,
        request_id=request_id,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        provenance={
            "channel_kind": channel_kind,
            "active_chip_key": active_chip_evaluate.get("chip_key") if active_chip_evaluate else None,
            "active_path_key": attachment_context.get("active_path_key"),
            "personality_name": personality_profile.get("personality_name") if personality_profile else None,
        },
    )
    if not screened_context["allowed"]:
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Researcher bridge dispatch was blocked by the pre-model secret boundary.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="secret_boundary_blocked",
            severity="high",
            facts={
                "quarantine_id": screened_context["quarantine_id"],
                "blocked_stage": "contextual_task",
            },
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=(
                "[Spark Researcher blocked] Sensitive material was detected in model-visible context. "
                "I did not send it to the bridge or provider."
            ),
            evidence_summary="Pre-model secret boundary blocked bridge execution.",
            escalation_hint="secret_boundary_violation",
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="blocked",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="secret_boundary_blocked",
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
        )
    provider_selection = _resolve_bridge_provider(config_manager=config_manager, state_db=state_db)
    routing_policy = _researcher_routing_policy(config_manager)
    runtime_root, runtime_source = discover_researcher_runtime_root(config_manager)
    config_path = resolve_researcher_config_path(config_manager, runtime_root) if runtime_root is not None else None
    record_environment_snapshot(
        state_db,
        surface="researcher_bridge",
        run_id=run_id,
        request_id=request_id,
        summary="Researcher bridge environment snapshot recorded.",
        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
        provider_execution_transport=(
            provider_selection.provider.execution_transport if provider_selection.provider else None
        ),
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(config_path) if config_path else None,
        env_refs={
            "provider_auth_profile_id": (
                provider_selection.provider.auth_profile_id if provider_selection.provider else None
            ),
            "provider_source": provider_selection.provider.source if provider_selection.provider else None,
        },
        facts={"model_family": provider_selection.model_family, "runtime_source": runtime_source},
    )
    if not bool(config_manager.get_path("spark.researcher.enabled", default=True)):
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Researcher bridge dispatch was blocked because the bridge is disabled.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="bridge_disabled",
            severity="high",
            facts={"mode": "disabled"},
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text="[Spark Researcher disabled] The operator has disabled the Spark Researcher bridge for this workspace.",
            evidence_summary="Spark Researcher bridge disabled by operator.",
            escalation_hint=None,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="disabled",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_execution_transport=(
                provider_selection.provider.execution_transport if provider_selection.provider else None
            ),
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
            routing_decision="bridge_disabled",
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
        )
    if provider_selection.error:
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Researcher bridge dispatch failed closed during provider resolution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="provider_resolution_failed",
            severity="high",
            facts={"error": provider_selection.error},
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=f"[Spark Researcher provider auth error] {provider_selection.error}",
            evidence_summary="Provider resolution failed closed before bridge execution.",
            escalation_hint="provider_auth_error",
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="bridge_error",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_execution_transport=(
                provider_selection.provider.execution_transport if provider_selection.provider else None
            ),
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
            routing_decision="provider_resolution_failed",
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
        )
    if runtime_root is not None:
        if config_path.exists():
            try:
                record_event(
                    state_db,
                    event_type="dispatch_started",
                    component="researcher_bridge",
                    summary="Researcher bridge dispatch started.",
                    run_id=run_id,
                    request_id=request_id,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code="build_advisory",
                    facts={"runtime_source": runtime_source, "model_family": provider_selection.model_family},
                )
                build_advisory = _import_build_advisory(runtime_root)
                execute_with_research = _import_execute_with_research(runtime_root)
                advisory = build_advisory(
                    config_path,
                    contextual_task,
                    model=provider_selection.model_family,
                    limit=3,
                    domain=None,
                )
                if (
                    provider_selection.provider
                    and provider_selection.provider.execution_transport == "direct_http"
                    and routing_policy["conversational_fallback_enabled"]
                    and _is_conversational_fallback_candidate(
                        user_message=user_message,
                        advisory=advisory,
                        fallback_max_chars=int(routing_policy["conversational_fallback_max_chars"]),
                    )
                ):
                    cleaned_reply, removed_residue = _clean_messaging_reply_with_metadata(
                        _render_direct_provider_chat_fallback(
                            state_db=state_db,
                            provider=provider_selection.provider,
                            user_message=user_message,
                            channel_kind=channel_kind,
                            attachment_context=attachment_context,
                            active_chip_evaluate=active_chip_evaluate,
                            personality_profile=personality_profile,
                            personality_context_extra=personality_context_extra,
                            run_id=run_id,
                            request_id=request_id,
                            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                        ),
                        channel_kind=channel_kind,
                    )
                    if removed_residue:
                        record_quarantine(
                            state_db,
                            run_id=run_id,
                            request_id=request_id,
                            source_kind="reply_residue",
                            source_ref=request_id,
                            policy_domain="researcher_bridge_residue",
                            reason_code="operational_residue_removed",
                            summary="Operational residue was stripped from a direct-provider fallback reply before delivery.",
                            payload_preview="\n".join(removed_residue)[:160],
                            provenance={"channel_kind": channel_kind, "trace_ref": f"trace:{agent_id}:{human_id}:{request_id}"},
                        )
                    reply_text = cleaned_reply
                    trace_ref = str(advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing")
                    evidence_summary = "status=under_supported provider_fallback=direct_http_chat"
                    reply_text, evidence_summary, escalation_hint, routing_decision = _maybe_apply_swarm_recommendation(
                        config_manager=config_manager,
                        state_db=state_db,
                        user_message=user_message,
                        channel_kind=channel_kind,
                        reply_text=reply_text,
                        evidence_summary=evidence_summary,
                        routing_decision="provider_fallback_chat",
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                    )
                    record_event(
                        state_db,
                        event_type="tool_result_received",
                        component="researcher_bridge",
                        summary="Researcher bridge produced a provider fallback result.",
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        channel_id=channel_kind,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                        actor_id="researcher_bridge",
                        reason_code="provider_fallback_chat",
                        facts={
                            "routing_decision": routing_decision,
                            "bridge_mode": f"external_{runtime_source}",
                            "evidence_summary": evidence_summary,
                        },
                    )
                    return ResearcherBridgeResult(
                        request_id=request_id,
                        reply_text=reply_text,
                        evidence_summary=evidence_summary,
                        escalation_hint=escalation_hint,
                        trace_ref=trace_ref,
                        mode=f"external_{runtime_source}",
                        runtime_root=str(runtime_root),
                        config_path=str(config_path),
                        attachment_context=attachment_context,
                        provider_id=provider_selection.provider.provider_id,
                        provider_auth_profile_id=provider_selection.provider.auth_profile_id,
                        provider_auth_method=provider_selection.provider.auth_method,
                        provider_model=provider_selection.provider.default_model,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=provider_selection.provider.execution_transport,
                        provider_base_url=provider_selection.provider.base_url,
                        provider_source=provider_selection.provider.source,
                        routing_decision=routing_decision,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
                    )
                if provider_selection.provider and _supports_direct_or_cli_execution(provider_selection):
                    with _temporary_provider_env(
                        provider_selection.provider,
                        state_db=state_db,
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                    ):
                        execution = execute_with_research(
                            runtime_root,
                            advisory=advisory,
                            model=provider_selection.model_family,
                            command_override=_command_override_for_provider(provider_selection),
                            dry_run=False,
                        )
                    reply_text, evidence_summary, trace_ref = _render_reply_from_execution(execution, advisory)
                else:
                    reply_text, evidence_summary, trace_ref = _render_reply_from_advisory(advisory)
                reply_text, removed_residue = _clean_messaging_reply_with_metadata(reply_text, channel_kind=channel_kind)
                if removed_residue:
                    record_quarantine(
                        state_db,
                        run_id=run_id,
                        request_id=request_id,
                        source_kind="reply_residue",
                        source_ref=request_id,
                        policy_domain="researcher_bridge_residue",
                        reason_code="operational_residue_removed",
                        summary="Operational residue was stripped from a researcher reply before delivery.",
                        payload_preview="\n".join(removed_residue)[:160],
                        provenance={"channel_kind": channel_kind, "trace_ref": trace_ref},
                    )
                base_routing_decision = (
                    "provider_execution"
                    if provider_selection.provider and _supports_direct_or_cli_execution(provider_selection)
                    else "researcher_advisory"
                )
                reply_text, evidence_summary, escalation_hint, routing_decision = _maybe_apply_swarm_recommendation(
                    config_manager=config_manager,
                    state_db=state_db,
                    user_message=user_message,
                    channel_kind=channel_kind,
                    reply_text=reply_text,
                    evidence_summary=evidence_summary,
                    routing_decision=base_routing_decision,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                )
                record_event(
                    state_db,
                    event_type="tool_result_received",
                    component="researcher_bridge",
                    summary="Researcher bridge produced a result.",
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code=base_routing_decision,
                    facts={
                        "routing_decision": routing_decision,
                        "bridge_mode": f"external_{runtime_source}",
                        "evidence_summary": evidence_summary,
                    },
                )
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=reply_text,
                    evidence_summary=evidence_summary,
                    escalation_hint=escalation_hint,
                    trace_ref=trace_ref,
                    mode=f"external_{runtime_source}",
                    runtime_root=str(runtime_root),
                    config_path=str(config_path),
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
                    provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                    provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                    provider_model_family=provider_selection.model_family,
                    provider_execution_transport=(
                        provider_selection.provider.execution_transport if provider_selection.provider else None
                    ),
                    provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                    provider_source=provider_selection.provider.source if provider_selection.provider else None,
                    routing_decision=routing_decision,
                    active_chip_key=active_chip_key,
                    active_chip_task_type=active_chip_task_type,
                    active_chip_evaluate_used=active_chip_evaluate_used,
                )
            except Exception as exc:  # pragma: no cover - external bridge safety
                record_event(
                    state_db,
                    event_type="dispatch_failed",
                    component="researcher_bridge",
                    summary="Researcher bridge dispatch failed with an exception.",
                    run_id=run_id,
                    request_id=request_id,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code="bridge_error",
                    severity="high",
                    facts={"error": str(exc)},
                )
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=f"[Spark Researcher bridge error] {exc}",
                    evidence_summary="External bridge failed closed.",
                    escalation_hint="bridge_error",
                    trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                    mode="bridge_error",
                    runtime_root=str(runtime_root),
                    config_path=str(config_path),
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
                    provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                    provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                    provider_model_family=provider_selection.model_family,
                    provider_execution_transport=(
                        provider_selection.provider.execution_transport if provider_selection.provider else None
                    ),
                    provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                    provider_source=provider_selection.provider.source if provider_selection.provider else None,
                    routing_decision="bridge_error",
                    active_chip_key=active_chip_key,
                    active_chip_task_type=active_chip_task_type,
                    active_chip_evaluate_used=active_chip_evaluate_used,
                )

    reply_text = (
        f"[Spark Researcher stub] I received your message in {channel_kind} "
        f"for {session_id}: {user_message}"
    )
    record_event(
        state_db,
        event_type="tool_result_received",
        component="researcher_bridge",
        summary="Researcher bridge stub result produced.",
        run_id=run_id,
        request_id=request_id,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        channel_id=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id="researcher_bridge",
        reason_code="stub",
        facts={"routing_decision": "stub"},
    )
    return ResearcherBridgeResult(
        request_id=request_id,
        reply_text=reply_text,
        evidence_summary=(
            "No external Spark Researcher runtime was configured or discovered. "
            f"active_chips={len(attachment_context.get('active_chip_keys') or [])} "
            f"active_path={attachment_context.get('active_path_key') or 'none'}"
        ),
        escalation_hint=None,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        mode="stub",
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(resolve_researcher_config_path(config_manager, runtime_root)) if runtime_root else None,
        attachment_context=attachment_context,
        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
        provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
        provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
        provider_model_family=provider_selection.model_family,
        provider_execution_transport=(
            provider_selection.provider.execution_transport if provider_selection.provider else None
        ),
        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
        provider_source=provider_selection.provider.source if provider_selection.provider else None,
        routing_decision="stub",
        active_chip_key=active_chip_key,
        active_chip_task_type=active_chip_task_type,
        active_chip_evaluate_used=active_chip_evaluate_used,
    )


@dataclass(frozen=True)
class ResearcherProviderSelection:
    provider: RuntimeProviderResolution | None
    model_family: str
    error: str | None = None


def _resolve_bridge_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> ResearcherProviderSelection:
    provider_records = config_manager.load().get("providers", {}).get("records", {}) or {}
    if not provider_records:
        return ResearcherProviderSelection(provider=None, model_family="generic")
    try:
        provider = resolve_runtime_provider(config_manager=config_manager, state_db=state_db)
    except RuntimeError as exc:
        return ResearcherProviderSelection(provider=None, model_family="generic", error=str(exc))
    return ResearcherProviderSelection(
        provider=provider,
        model_family=_model_family_for_provider(provider),
    )


def _model_family_for_provider(provider: RuntimeProviderResolution) -> str:
    provider_id = provider.provider_id.lower()
    model_name = (provider.default_model or "").lower()
    if provider_id == "openai-codex" or "codex" in model_name:
        return "codex"
    if provider.api_mode == "anthropic_messages" or provider_id == "anthropic" or "claude" in model_name:
        return "claude"
    if "openclaw" in model_name:
        return "openclaw"
    return "generic"


def _supports_direct_or_cli_execution(selection: ResearcherProviderSelection) -> bool:
    provider = selection.provider
    if provider is None:
        return False
    return provider.execution_transport in {"direct_http", "external_cli_wrapper"}


def _command_override_for_provider(selection: ResearcherProviderSelection) -> list[str] | None:
    provider = selection.provider
    if provider is None:
        return None
    if provider.execution_transport == "external_cli_wrapper":
        return None
    if provider.execution_transport != "direct_http":
        raise RuntimeError(f"Unsupported provider execution transport '{provider.execution_transport}'.")
    return [
        sys.executable,
        "-m",
        "spark_intelligence.llm.provider_wrapper",
        "{system_prompt_path}",
        "{user_prompt_path}",
        "{response_path}",
    ]


@contextmanager
def _temporary_provider_env(
    provider: RuntimeProviderResolution,
    *,
    state_db: StateDB | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
):
    values = {
        "SPARK_INTELLIGENCE_PROVIDER_ID": provider.provider_id,
        "SPARK_INTELLIGENCE_PROVIDER_KIND": provider.provider_kind,
        "SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD": provider.auth_method,
        "SPARK_INTELLIGENCE_PROVIDER_API_MODE": provider.api_mode,
        "SPARK_INTELLIGENCE_PROVIDER_EXECUTION_TRANSPORT": provider.execution_transport,
        "SPARK_INTELLIGENCE_PROVIDER_MODEL": provider.default_model or "",
        "SPARK_INTELLIGENCE_PROVIDER_BASE_URL": provider.base_url or "",
        "SPARK_INTELLIGENCE_PROVIDER_SECRET": provider.secret_value,
    }
    if state_db is not None:
        values["SPARK_INTELLIGENCE_STATE_DB_PATH"] = str(state_db.path)
    if run_id:
        values["SPARK_INTELLIGENCE_RUN_ID"] = run_id
    if request_id:
        values["SPARK_INTELLIGENCE_REQUEST_ID"] = request_id
    if trace_ref:
        values["SPARK_INTELLIGENCE_TRACE_REF"] = trace_ref
    original = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_runtime_state(state_db: StateDB) -> dict[str, str]:
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'researcher:%'"
        ).fetchall()
    return {str(row["state_key"]): str(row["value"] or "") for row in rows}


def _loads_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_int(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _read_failure_count(conn: Any, state_key: str) -> int:
    row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return 0
    try:
        return int(str(row["value"]))
    except ValueError:
        return 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_runtime_state(conn: Any, state_key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO runtime_state(state_key, value)
        VALUES (?, ?)
        ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (state_key, value),
    )
