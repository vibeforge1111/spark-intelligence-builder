from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, replace
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
DOMAIN_CHIP_MEMORY_WRITE_TOOL_NAME = "domain-chip-memory.memory.write"
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
    governor_decision: dict[str, Any] | None = None


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _ledger_binding_reasons(
    *,
    envelope: dict[str, Any],
    proposed_action: dict[str, Any],
    authorization: dict[str, Any],
    ledger: dict[str, Any],
) -> tuple[str, ...]:
    reasons: list[str] = []
    ledger_authorization = ledger.get("authorization") if isinstance(ledger.get("authorization"), dict) else {}
    result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}

    turn_id = str(envelope.get("turn_id") or "")
    action_id = str(proposed_action.get("action_id") or "")
    capability_id = str(proposed_action.get("capability_id") or "")
    decision_id = str(authorization.get("decision_id") or "")
    proposed_actions = envelope.get("proposed_actions") if isinstance(envelope.get("proposed_actions"), list) else []
    action_in_envelope = any(
        isinstance(action, dict)
        and str(action.get("action_id") or "") == action_id
        and str(action.get("capability_id") or "") == capability_id
        for action in proposed_actions
    )

    if not turn_id:
        _append_reason(reasons, "missing_envelope_turn_id")
    if not action_id:
        _append_reason(reasons, "missing_proposed_action_id")
    if not capability_id:
        _append_reason(reasons, "missing_proposed_capability_id")
    if not decision_id:
        _append_reason(reasons, "missing_authorization_decision_id")
    if not action_in_envelope:
        _append_reason(reasons, "proposed_action_not_in_envelope")

    if str(authorization.get("verdict") or "") != "allow":
        _append_reason(reasons, "authorization_not_allowed")
    if str(authorization.get("turn_id") or "") != turn_id:
        _append_reason(reasons, "authorization_turn_mismatch")
    if str(authorization.get("action_id") or "") != action_id:
        _append_reason(reasons, "authorization_action_mismatch")
    if str(authorization.get("capability_id") or "") != capability_id:
        _append_reason(reasons, "authorization_capability_mismatch")

    if str(ledger.get("turn_id") or "") != turn_id:
        _append_reason(reasons, "tool_ledger_turn_mismatch")
    if str(ledger.get("action_id") or "") != action_id:
        _append_reason(reasons, "tool_ledger_action_mismatch")
    if str(ledger.get("capability_id") or "") != capability_id:
        _append_reason(reasons, "tool_ledger_capability_mismatch")
    if str(result.get("status") or "") != "not_started":
        _append_reason(reasons, "tool_ledger_not_pre_execution")

    if not ledger_authorization:
        _append_reason(reasons, "missing_tool_ledger_authorization")
    if str(ledger_authorization.get("verdict") or "") != "allow":
        _append_reason(reasons, "tool_ledger_authorization_not_allowed")
    if str(ledger_authorization.get("turn_id") or "") != turn_id:
        _append_reason(reasons, "tool_ledger_authorization_turn_mismatch")
    if str(ledger_authorization.get("action_id") or "") != action_id:
        _append_reason(reasons, "tool_ledger_authorization_action_mismatch")
    if str(ledger_authorization.get("capability_id") or "") != capability_id:
        _append_reason(reasons, "tool_ledger_authorization_capability_mismatch")
    if str(ledger_authorization.get("decision_id") or "") != decision_id:
        _append_reason(reasons, "tool_ledger_authorization_decision_mismatch")

    return tuple(reasons)


def _bridge_governor_packaging_evidence(
    *,
    envelope: dict[str, Any],
    authorization: dict[str, Any],
    ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    turn_id = str(envelope.get("turn_id") or "unknown")
    decision_id = str(authorization.get("decision_id") or "unknown")
    ledger_id = str(ledger.get("ledger_id") or "unknown")
    action_id = str(authorization.get("action_id") or ledger.get("action_id") or "unknown")
    return [
        {
            "id": f"evidence:bridge-governor-issuer:{turn_id}"[:128],
            "kind": "policy",
            "source": "issuer:spark-harness-core/governor",
            "summary": "Builder bridge packaged Harness Core authorization as GovernorDecision evidence.",
            "confidence": 1.0,
        },
        {
            "id": f"evidence:bridge-governor-provenance:{decision_id}"[:128],
            "kind": "authority_binding_ref",
            "source": "provenance:spark-harness-core/authorization",
            "summary": f"authorization_decision:{decision_id}",
            "confidence": 1.0,
        },
        {
            "id": f"evidence:bridge-governor-current:{ledger_id}"[:128],
            "kind": "runtime_state",
            "source": "current-binding:builder-bridge",
            "summary": f"turn:{turn_id};action:{action_id};ledger:{ledger_id}",
            "confidence": 1.0,
        },
    ]


def bridge_governor_decision_has_canonical_binding(governor_decision: dict[str, Any] | None) -> bool:
    if not isinstance(governor_decision, dict):
        return False
    evidence = governor_decision.get("evidence") if isinstance(governor_decision.get("evidence"), list) else []
    evidence_items = [item for item in evidence if isinstance(item, dict)]
    turn_id = str(governor_decision.get("turn_id") or "").strip()
    authorizations = governor_decision.get("authorizations") if isinstance(governor_decision.get("authorizations"), list) else []
    ledgers = governor_decision.get("tool_ledgers") if isinstance(governor_decision.get("tool_ledgers"), list) else []
    decision_ids = {
        str(item.get("decision_id") or "").strip()
        for item in authorizations
        if isinstance(item, dict) and str(item.get("decision_id") or "").strip()
    }
    ledger_bindings = {
        (
            str(item.get("action_id") or "").strip(),
            str(item.get("ledger_id") or "").strip(),
        )
        for item in ledgers
        if isinstance(item, dict)
        and str(item.get("action_id") or "").strip()
        and str(item.get("ledger_id") or "").strip()
    }
    if not turn_id or not decision_ids or not ledger_bindings:
        return False

    expected_issuer_id = f"evidence:bridge-governor-issuer:{turn_id}"[:128]
    expected_authorization_summaries = {f"authorization_decision:{decision_id}" for decision_id in decision_ids}
    issuer_ok = any(
        str(item.get("kind") or "") == "policy"
        and str(item.get("source") or "") == "issuer:spark-harness-core/governor"
        and str(item.get("id") or "") == expected_issuer_id
        for item in evidence_items
    )
    authorization_ok = any(
        str(item.get("kind") or "") == "authority_binding_ref"
        and str(item.get("source") or "") == "provenance:spark-harness-core/authorization"
        and str(item.get("summary") or "") in expected_authorization_summaries
        for item in evidence_items
    )
    runtime_ok = any(
        str(item.get("kind") or "") == "runtime_state"
        and str(item.get("source") or "") == "current-binding:builder-bridge"
        and any(
            str(item.get("summary") or "") == f"turn:{turn_id};action:{action_id};ledger:{ledger_id}"
            for action_id, ledger_id in ledger_bindings
        )
        for item in evidence_items
    )
    return issuer_ok and authorization_ok and runtime_ok


def build_governor_decision_from_bridge_authority(
    verdict: BridgeAuthorityVerdict,
    *,
    reply_instruction: str = "Execute the authorized Builder bridge action.",
) -> dict[str, Any] | None:
    envelope = verdict.harness_core_envelope
    proposed_action = verdict.proposed_action
    authorization = verdict.authorization_decision
    ledger = verdict.tool_call_ledger
    if not isinstance(envelope, dict) or envelope.get("schema_version") != "turn-intent-envelope-vnext":
        return None
    if not isinstance(proposed_action, dict):
        return None
    if not isinstance(authorization, dict) or authorization.get("schema_version") != "authorization-decision-v1":
        return None
    if not isinstance(ledger, dict) or ledger.get("schema_version") != "tool-call-ledger-v1":
        return None
    authorization_verdict = str(authorization.get("verdict") or "")
    if authorization_verdict not in {"allow", "deny", "interrupt", "degrade"}:
        return None
    outcome_by_verdict = {
        "allow": "execute",
        "deny": "deny",
        "interrupt": "interrupt",
        "degrade": "degrade",
    }
    outcome = outcome_by_verdict[authorization_verdict]
    ledger_binding_reasons = _ledger_binding_reasons(
        envelope=envelope,
        proposed_action=proposed_action,
        authorization=authorization,
        ledger=ledger,
    )
    if authorization_verdict == "allow" and (not verdict.allowed or ledger_binding_reasons):
        outcome = "degrade" if ledger_binding_reasons else "deny"
    action_authorized = verdict.allowed and authorization_verdict == "allow" and not ledger_binding_reasons
    authority_state = (envelope.get("action_authority") or {}).get("state")
    if outcome == "interrupt":
        authority_state = "confirmation_required"
    elif outcome in {"deny", "degrade"}:
        authority_state = "blocked"
    reasons = list(verdict.reason_codes) or list(authorization.get("reasons") or ["harness_core_authorized"])
    for reason in ledger_binding_reasons:
        _append_reason(reasons, reason)
    execution_boundary = {
        "action_authorized": action_authorized,
        "action_count": len(envelope.get("proposed_actions") if isinstance(envelope.get("proposed_actions"), list) else []),
        "authorized_action_count": 1 if action_authorized else 0,
        "requires_human_confirmation": bool((authorization.get("approval") or {}).get("required")),
        "legacy_authority_demoted": True,
        "reasons": reasons,
    }
    trace = authorization.get("trace") if isinstance(authorization.get("trace"), dict) else {}
    evidence = [
        *(envelope.get("evidence") if isinstance(envelope.get("evidence"), list) else []),
        *_bridge_governor_packaging_evidence(envelope=envelope, authorization=authorization, ledger=ledger),
    ]
    return {
        "schema_version": "governor-decision-v1",
        "wire_contract_version": int(
            authorization.get("wire_contract_version") or ledger.get("wire_contract_version") or 1
        ),
        "decision_id": f"governor-decision:{authorization.get('decision_id') or envelope.get('turn_id')}",
        "created_at": authorization.get("created_at") or ledger.get("created_at") or envelope.get("created_at"),
        "surface": envelope.get("surface") or "builder",
        "turn_id": envelope.get("turn_id"),
        "selected_move": envelope.get("selected_move"),
        "authority_state": authority_state,
        "risk_tier": authorization.get("risk_tier") or (envelope.get("action_authority") or {}).get("risk_tier"),
        "outcome": outcome,
        "envelope": envelope,
        "authorizations": [authorization],
        "tool_ledgers": [ledger],
        "execution_boundary": execution_boundary,
        "reply_contract": {
            "style": "human_conversational",
            "instruction": reply_instruction,
            "inspect_link_allowed": True,
            "should_interrupt": False,
        },
        "evidence": evidence or authorization.get("evidence") or [],
        "trace": {
            "id": trace.get("id") or f"{envelope.get('turn_id')}:governor",
            "summary": "Governor decision packaged from Builder bridge authority.",
            "redaction_class": trace.get("redaction_class") or "metadata_only",
        },
    }


def _with_governor_decision(verdict: BridgeAuthorityVerdict) -> BridgeAuthorityVerdict:
    governor_decision = build_governor_decision_from_bridge_authority(verdict)
    if not isinstance(governor_decision, dict):
        return verdict
    if verdict.allowed and str(governor_decision.get("outcome") or "") != "execute":
        boundary = governor_decision.get("execution_boundary")
        boundary_reasons = boundary.get("reasons") if isinstance(boundary, dict) else []
        reason_codes = list(verdict.reason_codes)
        iterable_reasons = boundary_reasons if isinstance(boundary_reasons, list) else []
        for reason in iterable_reasons:
            _append_reason(reason_codes, str(reason))
        if not reason_codes:
            reason_codes.append("governor_execution_boundary_not_executable")
        return replace(
            verdict,
            allowed=False,
            reason_codes=tuple(reason_codes),
            governor_decision=governor_decision,
        )
    return replace(verdict, governor_decision=governor_decision)


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
    storage_object = (
        r"(?:this|that|it|anything|something|memory|note|detail|context|phrase|"
        r"private\s+(?:phrase|detail|context|note))"
    )
    if re.search(rf"\bno[-\s]+store\b(?=\s*(?:[:.;!?]|$|\b(?:{storage_object}|the|my|our|please)\b))", lowered):
        return True
    if re.search(r"\b(?:answer|reply|respond)\s+without\s+(?:saving|storing|remembering|recording|capturing|persisting|learning)\b", lowered):
        return True
    if re.search(
        r"\b(?:do\s+not|don't|dont|please\s+don't|please\s+dont|no\s+need\s+to|without|never)\s+"
        r"(?:save|remember|store|write|record|capture|persist|learn)\s+"
        rf"{storage_object}\b",
        lowered,
    ):
        return True
    if re.search(
        r"\b(?:do\s+not|don't|dont|please\s+don't|please\s+dont|no\s+need\s+to|without|never)\s+"
        r"(?:save|remember|store|write|record|capture|persist|learn)\s*(?:[.!?;:]|$)",
        lowered,
    ):
        return True
    if re.search(r"\b(?:only|just)\s+for\s+this\s+(?:answer|reply|response|turn|message)\b", lowered) and re.search(
        r"\b(?:private|phrase|detail|context|secret|do\s+not\s+store|don't\s+store|dont\s+store|no[-\s]+store)\b",
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


def build_telegram_voice_delivery_turn_intent_payload_vnext(
    *,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    source_kind: str,
    user_message: str,
) -> dict[str, Any] | None:
    return _build_telegram_tool_vnext_payload(
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        source_kind=source_kind,
        tool_name="voice.speak",
        owner_system="spark-voice-comms",
        mutation_class="external_network",
        intent_summary="Fresh Telegram turn selected voice delivery for the assistant reply.",
        raw_turn_summary=(
            "Fresh Telegram user turn plus explicit voice request or saved voice delivery "
            f"preference selected voice output. User message: {user_message[:160]}"
        ),
    )


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
        tool_name=DOMAIN_CHIP_MEMORY_WRITE_TOOL_NAME,
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


def _bridge_bound_ledger_row(
    *,
    verdict: BridgeAuthorityVerdict,
    ledger: dict[str, Any],
    owner_system: str | None,
    mutation_class: str | None,
    component: str,
    request_id: str | None,
    trace_ref: str | None,
    channel_id: str | None,
) -> dict[str, Any]:
    authorization = ledger.get("authorization") if isinstance(ledger.get("authorization"), dict) else {}
    result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}
    trace = ledger.get("trace") if isinstance(ledger.get("trace"), dict) else {}
    authorization_decision = (
        verdict.authorization_decision if isinstance(verdict.authorization_decision, dict) else {}
    )
    surface = None
    if verdict.envelope is not None:
        surface = verdict.envelope.surface
    surface = surface or channel_id or component
    return {
        "turn_id": ledger.get("turn_id"),
        "action_id": ledger.get("action_id"),
        "capability_id": ledger.get("capability_id"),
        "authorization_decision_id": authorization_decision.get("decision_id") or authorization.get("decision_id"),
        "ledger_id": ledger.get("ledger_id"),
        "tool_name": ledger.get("tool_name"),
        "owner_system": owner_system,
        "mutation_class": mutation_class,
        "outcome": authorization.get("outcome") or ("execute" if verdict.allowed else "deny"),
        "status": result.get("status"),
        "surface": surface,
        "request_id": request_id,
        "trace_ref": trace_ref or trace.get("id"),
        "summary": result.get("summary") or trace.get("summary"),
        "ledger_json": ledger,
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
    agent_id: str | None = None,
    actor_id: str | None = None,
) -> str | None:
    ledger = verdict.tool_call_ledger
    if not isinstance(ledger, dict):
        return None

    from spark_intelligence.observability.store import persist_bound_ledger, record_event

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
    persist_bound_ledger(
        state_db,
        row=_bridge_bound_ledger_row(
            verdict=verdict,
            ledger=ledger,
            owner_system=None,
            mutation_class=None,
            component=component,
            request_id=request_id,
            trace_ref=str(trace.get("id") or ledger.get("turn_id") or ""),
            channel_id=channel_id,
        ),
        component=component,
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

    from spark_intelligence.observability.store import persist_bound_ledger, record_event

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
    event_id = record_event(
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
    persist_bound_ledger(
        state_db,
        row=_bridge_bound_ledger_row(
            verdict=verdict,
            ledger=final_ledger,
            owner_system=None,
            mutation_class=None,
            component=component,
            request_id=request_id,
            trace_ref=str(trace.get("id") or final_ledger.get("turn_id") or ""),
            channel_id=channel_id,
        ),
        component=component,
    )
    return event_id


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
    verdict = _with_governor_decision(verdict)
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
    return replace(verdict, ledger_event_id=ledger_event_id)


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
    verdict = _with_governor_decision(verdict)
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
    return replace(verdict, ledger_event_id=ledger_event_id)
