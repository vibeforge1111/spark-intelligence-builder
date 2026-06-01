from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.harness_contract import (
    LegacyToolAuthorization,
    MutationClass,
    TurnIntentEnvelope,
    authorize_legacy_tool_call,
    parse_turn_intent_envelope,
)


_ENVELOPE_KEYS = (
    "spark_turn_intent",
    "sparkTurnIntent",
    "turnIntentEnvelope",
    "turn_intent_envelope",
)


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

    return record_event(
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
    actor_id: str | None = None,
    component: str = "bridge_authority",
) -> BridgeAuthorityVerdict:
    envelope = extract_turn_intent_envelope(update_payload)
    authorization: LegacyToolAuthorization = authorize_legacy_tool_call(
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
    ledger_event_id = (
        record_bridge_tool_call_ledger(
            state_db,
            verdict,
            component=component,
            request_id=request_id,
            run_id=run_id,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            actor_id=actor_id,
        )
        if state_db is not None
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
    actor_id: str | None = None,
    component: str = "bridge_authority",
) -> BridgeAuthorityVerdict:
    envelope = extract_turn_intent_envelope(update_payload)
    if envelope is None:
        return BridgeAuthorityVerdict(False, ("missing_or_invalid_envelope",), None)
    authorization: LegacyToolAuthorization = authorize_legacy_tool_call(
        envelope,
        tool_name=tool_name,
        owner_system=owner_system,
        mutation_class=mutation_class,
        publishes=publishes,
        external_network=external_network,
    )
    extra_reasons = list(authorization.reason_codes)
    if envelope.directive.quoted_or_meta_language and "quoted_or_meta_language" not in extra_reasons:
        extra_reasons.append("quoted_or_meta_language")
    if envelope.directive.explanation_only and "explanation_only_boundary" not in extra_reasons:
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
    ledger_event_id = (
        record_bridge_tool_call_ledger(
            state_db,
            verdict,
            component=component,
            request_id=request_id,
            run_id=run_id,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            actor_id=actor_id,
        )
        if state_db is not None
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
