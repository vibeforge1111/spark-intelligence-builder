from __future__ import annotations

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
