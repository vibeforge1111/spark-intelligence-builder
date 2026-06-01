from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.harness_contract import (
    MutationClass,
    TurnIntentEnvelope,
    authorize_tool_call,
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


def authorize_builder_bridge_action(
    update_payload: dict[str, Any] | None,
    *,
    tool_name: str,
    owner_system: str,
    mutation_class: MutationClass,
    publishes: bool = False,
    external_network: bool = False,
) -> BridgeAuthorityVerdict:
    envelope = extract_turn_intent_envelope(update_payload)
    verdict, reasons = authorize_tool_call(
        envelope,
        tool_name=tool_name,
        owner_system=owner_system,
        mutation_class=mutation_class,
        publishes=publishes,
        external_network=external_network,
    )
    return BridgeAuthorityVerdict(
        allowed=verdict == "allowed",
        reason_codes=reasons,
        envelope=envelope,
    )


def authorize_pending_confirmation(
    update_payload: dict[str, Any] | None,
    *,
    tool_name: str,
    owner_system: str,
    mutation_class: MutationClass,
    publishes: bool = False,
    external_network: bool = False,
) -> BridgeAuthorityVerdict:
    envelope = extract_turn_intent_envelope(update_payload)
    if envelope is None:
        return BridgeAuthorityVerdict(False, ("missing_or_invalid_envelope",), None)
    verdict, reasons = authorize_tool_call(
        envelope,
        tool_name=tool_name,
        owner_system=owner_system,
        mutation_class=mutation_class,
        publishes=publishes,
        external_network=external_network,
    )
    extra_reasons = list(reasons)
    if envelope.directive.quoted_or_meta_language and "quoted_or_meta_language" not in extra_reasons:
        extra_reasons.append("quoted_or_meta_language")
    if envelope.directive.explanation_only and "explanation_only_boundary" not in extra_reasons:
        extra_reasons.append("explanation_only_boundary")
    return BridgeAuthorityVerdict(verdict == "allowed" and not extra_reasons, tuple(extra_reasons), envelope)
