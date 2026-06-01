from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from spark_intelligence.harness_contract import (
    LegacyToolAuthorization,
    MutationClass,
    TurnIntentEnvelope,
    authorize_legacy_tool_call,
    authorize_vnext_tool_call,
    build_vnext_tool_intent_envelope,
    finalize_legacy_tool_call_ledger,
    parse_turn_intent_envelope,
)


_ENVELOPE_KEYS = (
    "spark_turn_intent",
    "sparkTurnIntent",
    "turnIntentEnvelope",
    "turn_intent_envelope",
)
_VNEXT_ENVELOPE_KEYS = (
    "turn_intent_envelope_vnext",
    "turnIntentEnvelopeVNext",
    "spark_turn_intent_vnext",
    "sparkTurnIntentVNext",
)
_BRIDGE_LEDGER_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("spark_bridge_ledger_context", default={})


@dataclass(frozen=True)
class BridgeAuthorityVerdict:
    allowed: bool
    reason_codes: tuple[str, ...]
    envelope: TurnIntentEnvelope | None
    harness_core_envelope: dict[str, Any] | None = None
    proposed_action: dict[str, Any] | None = None
    authorization_decision: dict[str, Any] | None = None
    tool_call_ledger: dict[str, Any] | None = None
    ledger_event_id: str | None = None


def set_bridge_authority_ledger_context(
    *,
    state_db: Any,
    component: str = "bridge_authority",
    request_id: str | None = None,
    run_id: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
) -> Any:
    return _BRIDGE_LEDGER_CONTEXT.set(
        {
            "state_db": state_db,
            "component": component,
            "request_id": request_id,
            "run_id": run_id,
            "channel_id": channel_id,
            "session_id": session_id,
            "human_id": human_id,
            "agent_id": agent_id,
            "actor_id": actor_id,
            "ledger_records": [],
        }
    )


def reset_bridge_authority_ledger_context(token: Any) -> None:
    _BRIDGE_LEDGER_CONTEXT.reset(token)


def _ledger_context(
    *,
    state_db: Any | None,
    component: str,
    request_id: str | None,
    run_id: str | None,
    channel_id: str | None,
    session_id: str | None,
    human_id: str | None,
    agent_id: str | None,
    actor_id: str | None,
) -> dict[str, Any]:
    context = _BRIDGE_LEDGER_CONTEXT.get() or {}
    return {
        "state_db": state_db if state_db is not None else context.get("state_db"),
        "component": component if component != "bridge_authority" else str(context.get("component") or component),
        "request_id": request_id if request_id is not None else context.get("request_id"),
        "run_id": run_id if run_id is not None else context.get("run_id"),
        "channel_id": channel_id if channel_id is not None else context.get("channel_id"),
        "session_id": session_id if session_id is not None else context.get("session_id"),
        "human_id": human_id if human_id is not None else context.get("human_id"),
        "agent_id": agent_id if agent_id is not None else context.get("agent_id"),
        "actor_id": actor_id if actor_id is not None else context.get("actor_id"),
    }


def _remember_context_ledger_event(
    *,
    state_db: Any,
    event_id: str,
    verdict: BridgeAuthorityVerdict,
    component: str,
    request_id: str | None,
    run_id: str | None,
    channel_id: str | None,
    session_id: str | None,
    human_id: str | None,
    agent_id: str | None,
    actor_id: str | None,
) -> None:
    context = _BRIDGE_LEDGER_CONTEXT.get() or {}
    if context.get("state_db") is not state_db:
        return
    records = context.get("ledger_records")
    if not isinstance(records, list):
        return
    records.append(
        {
            "event_id": event_id,
            "verdict": verdict,
            "component": component,
            "request_id": request_id,
            "run_id": run_id,
            "channel_id": channel_id,
            "session_id": session_id,
            "human_id": human_id,
            "agent_id": agent_id,
            "actor_id": actor_id,
            "result_event_id": None,
        }
    )


def extract_turn_intent_envelope(update_payload: dict[str, Any] | None) -> TurnIntentEnvelope | None:
    if not isinstance(update_payload, dict):
        return None

    candidates: list[Any] = []
    for key in _ENVELOPE_KEYS:
        candidates.append(update_payload.get(key))

    message = update_payload.get("message")
    if isinstance(message, dict):
        for key in _ENVELOPE_KEYS:
            candidates.append(message.get(key))

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            return parse_turn_intent_envelope(candidate)
        except ValueError:
            continue
    return None


def extract_turn_intent_envelope_vnext(update_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(update_payload, dict):
        return None

    candidates: list[Any] = []
    for key in _VNEXT_ENVELOPE_KEYS:
        candidates.append(update_payload.get(key))

    message = update_payload.get("message")
    if isinstance(message, dict):
        for key in _VNEXT_ENVELOPE_KEYS:
            candidates.append(message.get(key))

    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("schema_version") == "turn-intent-envelope-vnext":
            return candidate
    return None


def memory_write_boundary_blocks_adapter_authority(user_message: str) -> bool:
    text = " ".join(str(user_message or "").strip().split())
    if not text:
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:do\s+not|don't|dont|please\s+don't|no\s+need\s+to|without)\s+"
        r"(?:save|remember|store|write|record|capture|persist|learn)\b",
        lowered,
    ):
        return True
    if re.search(
        r"\b(?:not\s+a\s+(?:command|request|instruction)|just\s+(?:an?\s+)?example|"
        r"example\s+only|quoted\s+example|bug\s+report|hypothetical|for\s+example)\b",
        lowered,
    ) and re.search(r"\b(?:memory|remember|save|forget|favorite|current|decision|plan|focus)\b", lowered):
        return True
    if re.match(r"^\s*(?:example|quote|quoted\s+text)\s*:", lowered):
        return True
    return False


def _build_telegram_tool_vnext_payload(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    source_kind: str,
    tool_name: str,
    owner_system: str,
    mutation_class: MutationClass,
    intent_summary: str,
    raw_turn_summary: str,
) -> dict[str, Any] | None:
    if channel_kind != "telegram":
        return None
    try:
        return build_vnext_tool_intent_envelope(
            surface=channel_kind,
            actor_id_ref=human_id,
            request_id=request_id,
            source_kind=source_kind,
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            intent_summary=intent_summary,
            raw_turn_summary=raw_turn_summary,
            confidence=0.95,
        )
    except Exception:
        return None


def build_telegram_memory_turn_intent_payload(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    user_message: str,
    source_kind: str,
) -> dict[str, Any] | None:
    if channel_kind != "telegram":
        return None
    if memory_write_boundary_blocks_adapter_authority(user_message):
        return None
    return {
        "schema": "spark.turn_intent.v1",
        "turnId": f"turn:telegram-memory:{request_id}",
        "traceId": f"trace:telegram-memory:{request_id}",
        "surface": channel_kind,
        "directive": {
            "mode": "execute",
            "noExecution": False,
            "noPublish": True,
            "localOnly": True,
            "explanationOnly": False,
            "quotedOrMetaLanguage": False,
        },
        "selectedIntent": {
            "kind": "memory_action",
            "ownerSystem": "domain-chip-memory",
            "action": "memory.write",
            "confidence": "explicit",
            "requiresConfirmation": False,
            "source": source_kind,
        },
        "sessionScope": {
            "sessionKey": session_id,
            "surface": channel_kind,
            "conversationKind": "telegram_dm",
            "userRef": human_id,
            "chatRef": session_id,
            "memoryLoadPolicy": "bounded",
            "pendingStateScope": "fresh_turn",
        },
        "toolPolicy": {
            "allowedTools": ["answer.compose", "memory.write"],
            "deniedTools": [],
            "enabledToolsets": ["spark-harness-core", "domain-chip-memory"],
            "mutationClassesAllowed": ["none", "read_only", "writes_memory"],
            "requiresApprovalFor": [],
            "networkPolicy": "none",
            "elevatedAllowed": False,
        },
        "executionPolicy": {
            "canMutateFiles": False,
            "canLaunchMission": False,
            "canWriteMemory": True,
            "canDeleteSchedule": False,
            "canCreateChip": False,
            "canPublish": False,
            "canUseExternalNetwork": False,
        },
        "threatDefense": {
            "reasonCodes": [
                "fresh_user_turn_is_authority",
                "telegram_memory_adapter_explicit_intent",
            ]
        },
    }


def build_telegram_memory_turn_intent_payload_vnext(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    user_message: str,
    source_kind: str,
) -> dict[str, Any] | None:
    if memory_write_boundary_blocks_adapter_authority(user_message):
        return None
    return _build_telegram_tool_vnext_payload(
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        source_kind=source_kind,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
        intent_summary="User explicitly asked Spark to remember information from this Telegram turn.",
        raw_turn_summary=f"Telegram memory write intent matched {source_kind}; raw text remains offloaded.",
    )


def memory_read_boundary_blocks_adapter_authority(user_message: str) -> bool:
    text = " ".join(str(user_message or "").strip().split())
    if not text:
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:do\s+not|don't|dont|please\s+don't|no\s+need\s+to|without)\s+"
        r"(?:use|using|read|recall|retrieve|search|load|check|look\s+at)\s+(?:memory|memories|history|context)\b",
        lowered,
    ):
        return True
    if re.search(r"\bwithout\s+(?:using\s+)?(?:memory|memories|history|saved\s+context)\b", lowered):
        return True
    if re.search(
        r"\b(?:not\s+a\s+(?:command|request|instruction)|just\s+(?:an?\s+)?example|"
        r"example\s+only|quoted\s+example|bug\s+report|hypothetical|for\s+example|just\s+explain)\b",
        lowered,
    ) and re.search(
        r"\b(?:memory|remember|recall|saved|current\s+(?:plan|focus)|what\s+did\s+(?:i|we)|what\s+do\s+you\s+remember)\b",
        lowered,
    ):
        return True
    if re.match(r"^\s*(?:example|quote|quoted\s+text)\s*:", lowered):
        return True
    return False


def detect_telegram_memory_read_authority_source_kind(user_message: str) -> str | None:
    text = " ".join(str(user_message or "").strip().split())
    if not text or memory_read_boundary_blocks_adapter_authority(text):
        return None
    lowered = text.casefold()
    source_patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "telegram_runtime_current_plan_read",
            (
                r"\b(?:what(?:'s|\s+is)|show|tell|remind)\s+(?:me\s+)?(?:my\s+|the\s+)?current\s+plan\b",
                r"\buse\s+(?:my\s+|the\s+)?current\s+plan\b",
            ),
        ),
        (
            "telegram_runtime_current_focus_read",
            (
                r"\b(?:what(?:'s|\s+is)|show|tell|remind)\s+(?:me\s+)?(?:my\s+|the\s+)?current\s+focus\b",
                r"\buse\s+(?:my\s+|the\s+)?current\s+focus\b",
            ),
        ),
        (
            "telegram_runtime_explicit_memory_recall",
            (
                r"\b(?:what|which|who|when|where|why|how)\s+.*\b(?:remember|recall|saved|memory|memories)\b",
                r"\b(?:what\s+do\s+you|do\s+you|can\s+you)\s+(?:remember|recall)\b",
                r"\bwhat\s+(?:evidence|context|history)\s+do\s+you\s+have\s+about\b",
                r"\bwhat\s+do\s+you\s+know\s+about\b",
            ),
        ),
        (
            "telegram_runtime_prior_turn_recall",
            (
                r"\bwhat\s+did\s+(?:i|we)\s+(?:tell|say|mention|share|decide|agree|do|build|change)\b",
                r"\bwhat\s+(?:have\s+we|did\s+we)\s+(?:decided|decide|discuss|do|build|change|work\s+on)\b",
                r"\bwhat\s+(?:is|are)(?:\s+still)?\s+(?:next|remaining|left|open)\b",
                r"\bwhat\s+happened\s+(?:during|in|with)\b",
                r"\bwhat\s+changed\s+(?:in|during|from)\b",
            ),
        ),
        (
            "telegram_runtime_profile_fact_read",
            (
                r"\bwhat(?:'s|\s+is)\s+my\s+(?:favorite|preferred|name|timezone|location|role|company|email|phone|pronouns)\b",
                r"\bwhere\s+(?:is|are)\s+my\b",
                r"\bwho\s+(?:owns|is\s+responsible\s+for)\b",
            ),
        ),
    )
    for source_kind, patterns in source_patterns:
        if any(re.search(pattern, lowered) for pattern in patterns):
            return source_kind
    return None


def build_telegram_memory_read_turn_intent_payload(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    user_message: str,
    source_kind: str,
) -> dict[str, Any] | None:
    if channel_kind != "telegram":
        return None
    if memory_read_boundary_blocks_adapter_authority(user_message):
        return None
    return {
        "schema": "spark.turn_intent.v1",
        "turnId": f"turn:telegram-memory-read:{request_id}",
        "traceId": f"trace:telegram-memory-read:{request_id}",
        "surface": channel_kind,
        "directive": {
            "mode": "execute",
            "noExecution": False,
            "noPublish": True,
            "localOnly": True,
            "explanationOnly": False,
            "quotedOrMetaLanguage": False,
        },
        "selectedIntent": {
            "kind": "memory_read",
            "ownerSystem": "domain-chip-memory",
            "action": "memory.read",
            "confidence": "explicit",
            "requiresConfirmation": False,
            "source": source_kind,
        },
        "sessionScope": {
            "sessionKey": session_id,
            "surface": channel_kind,
            "conversationKind": "telegram_dm",
            "userRef": human_id,
            "chatRef": session_id,
            "memoryLoadPolicy": "bounded",
            "pendingStateScope": "fresh_turn",
        },
        "toolPolicy": {
            "allowedTools": ["answer.compose", "memory.read"],
            "deniedTools": [],
            "enabledToolsets": ["spark-harness-core", "domain-chip-memory"],
            "mutationClassesAllowed": ["none", "read_only"],
            "requiresApprovalFor": [],
            "networkPolicy": "none",
            "elevatedAllowed": False,
        },
        "executionPolicy": {
            "canMutateFiles": False,
            "canLaunchMission": False,
            "canWriteMemory": False,
            "canDeleteSchedule": False,
            "canCreateChip": False,
            "canPublish": False,
            "canUseExternalNetwork": False,
        },
        "threatDefense": {
            "reasonCodes": [
                "fresh_user_turn_is_authority",
                "telegram_memory_read_explicit_intent",
            ]
        },
    }


def build_telegram_memory_read_turn_intent_payload_vnext(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    user_message: str,
    source_kind: str,
) -> dict[str, Any] | None:
    if memory_read_boundary_blocks_adapter_authority(user_message):
        return None
    return _build_telegram_tool_vnext_payload(
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        source_kind=source_kind,
        tool_name="memory.read",
        owner_system="domain-chip-memory",
        mutation_class="read_only",
        intent_summary="User explicitly asked Spark to inspect bounded saved memory for this Telegram turn.",
        raw_turn_summary=f"Telegram memory read intent matched {source_kind}; raw text remains offloaded.",
    )


def build_telegram_memory_diagnostic_turn_intent_payload(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    source_kind: str,
) -> dict[str, Any] | None:
    if channel_kind != "telegram":
        return None
    return {
        "schema": "spark.turn_intent.v1",
        "turnId": f"turn:telegram-memory-diagnostic:{request_id}",
        "traceId": f"trace:telegram-memory-diagnostic:{request_id}",
        "surface": channel_kind,
        "directive": {
            "mode": "execute",
            "noExecution": False,
            "noPublish": True,
            "localOnly": True,
            "explanationOnly": False,
            "quotedOrMetaLanguage": False,
        },
        "selectedIntent": {
            "kind": "memory_diagnostic",
            "ownerSystem": "spark-intelligence-builder",
            "action": "memory.diagnose",
            "confidence": "explicit",
            "requiresConfirmation": False,
            "source": source_kind,
        },
        "sessionScope": {
            "sessionKey": session_id,
            "surface": channel_kind,
            "conversationKind": "telegram_dm",
            "userRef": human_id,
            "chatRef": session_id,
            "memoryLoadPolicy": "bounded",
            "pendingStateScope": "fresh_turn",
        },
        "toolPolicy": {
            "allowedTools": ["answer.compose", "memory.diagnose"],
            "deniedTools": [],
            "enabledToolsets": ["spark-harness-core", "spark-intelligence-builder"],
            "mutationClassesAllowed": ["none", "read_only"],
            "requiresApprovalFor": [],
            "networkPolicy": "none",
            "elevatedAllowed": False,
        },
        "executionPolicy": {
            "canMutateFiles": False,
            "canLaunchMission": False,
            "canWriteMemory": False,
            "canDeleteSchedule": False,
            "canCreateChip": False,
            "canPublish": False,
            "canUseExternalNetwork": False,
        },
        "threatDefense": {
            "reasonCodes": [
                "fresh_user_turn_is_authority",
                "telegram_memory_diagnostic_explicit_intent",
            ]
        },
    }


def build_telegram_memory_diagnostic_turn_intent_payload_vnext(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    source_kind: str,
) -> dict[str, Any] | None:
    return _build_telegram_tool_vnext_payload(
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        source_kind=source_kind,
        tool_name="memory.diagnose",
        owner_system="spark-intelligence-builder",
        mutation_class="read_only",
        intent_summary="User explicitly asked Spark to inspect memory health from this Telegram turn.",
        raw_turn_summary=f"Telegram memory diagnostic intent matched {source_kind}; raw text remains offloaded.",
    )


def record_bridge_tool_call_ledger(
    state_db: Any,
    verdict: BridgeAuthorityVerdict,
    *,
    component: str = "bridge_authority",
    request_id: str | None = None,
    run_id: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
) -> str | None:
    ledger = verdict.tool_call_ledger
    if not isinstance(ledger, dict):
        return None

    from spark_intelligence.observability.store import record_event

    envelope = verdict.envelope
    resolved_session_id = session_id
    resolved_human_id = human_id
    resolved_actor_id = actor_id
    if envelope is not None:
        resolved_session_id = resolved_session_id or envelope.session_scope.session_key
        resolved_human_id = resolved_human_id or envelope.session_scope.user_ref
        resolved_actor_id = resolved_actor_id or envelope.session_scope.user_ref

    authorization = verdict.authorization_decision or ledger.get("authorization") or {}
    trace = ledger.get("trace") if isinstance(ledger.get("trace"), dict) else {}
    ledger_id = str(ledger.get("ledger_id") or "")
    tool_name = str(ledger.get("tool_name") or "unknown_tool")
    result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}
    reason_code = (
        verdict.reason_codes[0]
        if verdict.reason_codes
        else str(authorization.get("verdict") or "harness_core_authorized")
    )

    event_id = record_event(
        state_db,
        event_type="tool_call_ledger_recorded",
        component=component,
        summary=f"Harness Core ToolCallLedger recorded for {tool_name}.",
        run_id=run_id,
        request_id=request_id,
        trace_ref=str(trace.get("id") or ledger.get("turn_id") or ""),
        channel_id=channel_id,
        session_id=resolved_session_id,
        human_id=resolved_human_id,
        agent_id=agent_id,
        actor_id=resolved_actor_id,
        reason_code=reason_code,
        severity="high" if not verdict.allowed else "medium",
        status="authorized" if verdict.allowed else "blocked",
        provenance={
            "source_kind": "spark_harness_core_tool_call_ledger",
            "source_ref": ledger_id,
            "component": component,
        },
        facts={
            "ledger_id": ledger_id,
            "turn_id": ledger.get("turn_id"),
            "action_id": ledger.get("action_id"),
            "capability_id": ledger.get("capability_id"),
            "tool_name": tool_name,
            "authorization_verdict": authorization.get("verdict"),
            "result_status": result.get("status"),
            "reason_codes": list(verdict.reason_codes),
            "tool_call_ledger": ledger,
        },
    )
    _remember_context_ledger_event(
        state_db=state_db,
        event_id=event_id,
        verdict=verdict,
        component=component,
        request_id=request_id,
        run_id=run_id,
        channel_id=channel_id,
        session_id=resolved_session_id,
        human_id=resolved_human_id,
        agent_id=agent_id,
        actor_id=resolved_actor_id,
    )
    return event_id


def record_bridge_tool_call_result_ledger(
    state_db: Any,
    verdict: BridgeAuthorityVerdict,
    *,
    status: str,
    summary: str,
    output_path: str | None = None,
    error_path: str | None = None,
    rollback_path: str | None = None,
    component: str = "bridge_authority",
    request_id: str | None = None,
    run_id: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
    initial_ledger_event_id: str | None = None,
) -> str | None:
    ledger = verdict.tool_call_ledger
    if not verdict.allowed or not isinstance(ledger, dict):
        return None

    from spark_intelligence.observability.store import record_event

    envelope = verdict.envelope
    resolved_session_id = session_id
    resolved_human_id = human_id
    resolved_actor_id = actor_id
    surface = "builder"
    if envelope is not None:
        resolved_session_id = resolved_session_id or envelope.session_scope.session_key
        resolved_human_id = resolved_human_id or envelope.session_scope.user_ref
        resolved_actor_id = resolved_actor_id or envelope.session_scope.user_ref
        surface = envelope.surface

    ledger_id = str(ledger.get("ledger_id") or "")
    tool_name = str(ledger.get("tool_name") or "unknown_tool")
    resolved_output_path = output_path or f"builder://requests/{request_id or 'unknown'}/tool-results/{ledger_id}"
    final_ledger = finalize_legacy_tool_call_ledger(
        ledger,
        status=status,
        output_path=resolved_output_path,
        summary=summary,
        surface=surface,
        error_path=error_path,
        rollback_path=rollback_path,
    )
    trace = final_ledger.get("trace") if isinstance(final_ledger.get("trace"), dict) else {}
    result = final_ledger.get("result") if isinstance(final_ledger.get("result"), dict) else {}
    return record_event(
        state_db,
        event_type="tool_call_ledger_result_recorded",
        component=component,
        summary=f"Harness Core ToolCallLedger result recorded for {tool_name}.",
        run_id=run_id,
        parent_event_id=initial_ledger_event_id,
        request_id=request_id,
        trace_ref=str(trace.get("id") or final_ledger.get("turn_id") or ""),
        channel_id=channel_id,
        session_id=resolved_session_id,
        human_id=resolved_human_id,
        agent_id=agent_id,
        actor_id=resolved_actor_id,
        reason_code=f"tool_result_{status}",
        severity="high" if status in {"failure", "rolled_back"} else "medium",
        status=status,
        provenance={
            "source_kind": "spark_harness_core_tool_call_ledger_result",
            "source_ref": ledger_id,
            "component": component,
            "initial_ledger_event_id": initial_ledger_event_id,
        },
        facts={
            "ledger_id": ledger_id,
            "turn_id": final_ledger.get("turn_id"),
            "action_id": final_ledger.get("action_id"),
            "capability_id": final_ledger.get("capability_id"),
            "tool_name": tool_name,
            "authorization_verdict": (final_ledger.get("authorization") or {}).get("verdict")
            if isinstance(final_ledger.get("authorization"), dict)
            else None,
            "result_status": result.get("status"),
            "initial_ledger_event_id": initial_ledger_event_id,
            "tool_call_ledger": final_ledger,
        },
    )


def record_scoped_bridge_tool_call_results(
    *,
    status: str,
    summary: str,
    output_path: str | None = None,
    error_path: str | None = None,
    rollback_path: str | None = None,
) -> tuple[str, ...]:
    context = _BRIDGE_LEDGER_CONTEXT.get() or {}
    state_db = context.get("state_db")
    records = context.get("ledger_records")
    if state_db is None or not isinstance(records, list):
        return ()

    event_ids: list[str] = []
    for record in records:
        if not isinstance(record, dict) or record.get("result_event_id"):
            continue
        verdict = record.get("verdict")
        if not isinstance(verdict, BridgeAuthorityVerdict) or not verdict.allowed:
            continue
        event_id = record_bridge_tool_call_result_ledger(
            state_db,
            verdict,
            status=status,
            summary=summary,
            output_path=output_path,
            error_path=error_path,
            rollback_path=rollback_path,
            component=str(record.get("component") or context.get("component") or "bridge_authority"),
            request_id=record.get("request_id") or context.get("request_id"),
            run_id=record.get("run_id") or context.get("run_id"),
            channel_id=record.get("channel_id") or context.get("channel_id"),
            session_id=record.get("session_id") or context.get("session_id"),
            human_id=record.get("human_id") or context.get("human_id"),
            agent_id=record.get("agent_id") or context.get("agent_id"),
            actor_id=record.get("actor_id") or context.get("actor_id"),
            initial_ledger_event_id=str(record.get("event_id") or ""),
        )
        if event_id:
            record["result_event_id"] = event_id
            event_ids.append(event_id)
    return tuple(event_ids)


def authorize_builder_bridge_action(
    update_payload: dict[str, Any] | None,
    *,
    tool_name: str,
    owner_system: str,
    mutation_class: MutationClass,
    publishes: bool = False,
    external_network: bool = False,
    state_db: Any | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
    component: str = "bridge_authority",
) -> BridgeAuthorityVerdict:
    vnext_envelope = extract_turn_intent_envelope_vnext(update_payload)
    envelope = None if vnext_envelope is not None else extract_turn_intent_envelope(update_payload)
    if vnext_envelope is not None:
        authorization: LegacyToolAuthorization = authorize_vnext_tool_call(
            vnext_envelope,
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            publishes=publishes,
            external_network=external_network,
        )
    else:
        authorization = authorize_legacy_tool_call(
            envelope,
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            publishes=publishes,
            external_network=external_network,
        )
    verdict = BridgeAuthorityVerdict(
        allowed=authorization.verdict == "allowed",
        reason_codes=authorization.reason_codes,
        envelope=envelope,
        harness_core_envelope=authorization.turn_intent_envelope_vnext,
        proposed_action=authorization.proposed_action,
        authorization_decision=authorization.authorization_decision,
        tool_call_ledger=authorization.tool_call_ledger,
    )
    ledger_context = _ledger_context(
        state_db=state_db,
        component=component,
        request_id=request_id,
        run_id=run_id,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    ledger_event_id = (
        record_bridge_tool_call_ledger(
            ledger_context["state_db"],
            verdict,
            component=str(ledger_context["component"] or component),
            request_id=ledger_context["request_id"],
            run_id=ledger_context["run_id"],
            channel_id=ledger_context["channel_id"],
            session_id=ledger_context["session_id"],
            human_id=ledger_context["human_id"],
            agent_id=ledger_context["agent_id"],
            actor_id=ledger_context["actor_id"],
        )
        if ledger_context["state_db"] is not None
        else None
    )
    if ledger_event_id is None:
        return verdict
    return BridgeAuthorityVerdict(
        verdict.allowed,
        verdict.reason_codes,
        verdict.envelope,
        verdict.harness_core_envelope,
        verdict.proposed_action,
        verdict.authorization_decision,
        verdict.tool_call_ledger,
        ledger_event_id,
    )


def authorize_pending_confirmation(
    update_payload: dict[str, Any] | None,
    *,
    tool_name: str,
    owner_system: str,
    mutation_class: MutationClass,
    publishes: bool = False,
    external_network: bool = False,
    state_db: Any | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
    component: str = "bridge_authority",
) -> BridgeAuthorityVerdict:
    vnext_envelope = extract_turn_intent_envelope_vnext(update_payload)
    envelope = None if vnext_envelope is not None else extract_turn_intent_envelope(update_payload)
    if envelope is None and vnext_envelope is None:
        return BridgeAuthorityVerdict(False, ("missing_or_invalid_envelope",), None)
    if vnext_envelope is not None:
        authorization: LegacyToolAuthorization = authorize_vnext_tool_call(
            vnext_envelope,
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            publishes=publishes,
            external_network=external_network,
        )
    else:
        authorization = authorize_legacy_tool_call(
            envelope,
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            publishes=publishes,
            external_network=external_network,
        )
    extra_reasons = list(authorization.reason_codes)
    if envelope is not None and envelope.directive.quoted_or_meta_language and "quoted_or_meta_language" not in extra_reasons:
        extra_reasons.append("quoted_or_meta_language")
    if envelope is not None and envelope.directive.explanation_only and "explanation_only_boundary" not in extra_reasons:
        extra_reasons.append("explanation_only_boundary")
    verdict = BridgeAuthorityVerdict(
        authorization.verdict == "allowed" and not extra_reasons,
        tuple(extra_reasons),
        envelope,
        authorization.turn_intent_envelope_vnext,
        authorization.proposed_action,
        authorization.authorization_decision,
        authorization.tool_call_ledger,
    )
    ledger_context = _ledger_context(
        state_db=state_db,
        component=component,
        request_id=request_id,
        run_id=run_id,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    ledger_event_id = (
        record_bridge_tool_call_ledger(
            ledger_context["state_db"],
            verdict,
            component=str(ledger_context["component"] or component),
            request_id=ledger_context["request_id"],
            run_id=ledger_context["run_id"],
            channel_id=ledger_context["channel_id"],
            session_id=ledger_context["session_id"],
            human_id=ledger_context["human_id"],
            agent_id=ledger_context["agent_id"],
            actor_id=ledger_context["actor_id"],
        )
        if ledger_context["state_db"] is not None
        else None
    )
    if ledger_event_id is None:
        return verdict
    return BridgeAuthorityVerdict(
        verdict.allowed,
        verdict.reason_codes,
        verdict.envelope,
        verdict.harness_core_envelope,
        verdict.proposed_action,
        verdict.authorization_decision,
        verdict.tool_call_ledger,
        ledger_event_id,
    )
