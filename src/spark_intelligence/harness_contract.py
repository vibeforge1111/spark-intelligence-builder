from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MutationClass = Literal[
    "none",
    "read_only",
    "writes_memory",
    "writes_files",
    "launches_mission",
    "creates_schedule",
    "deletes_schedule",
    "creates_chip",
    "publishes",
    "external_network",
]


@dataclass(frozen=True)
class HarnessDirective:
    mode: str
    no_execution: bool
    no_publish: bool
    local_only: bool
    explanation_only: bool
    quoted_or_meta_language: bool


@dataclass(frozen=True)
class HarnessSelectedIntent:
    kind: str
    owner_system: str
    action: str | None
    confidence: str
    requires_confirmation: bool
    source: str


@dataclass(frozen=True)
class HarnessSessionScope:
    session_key: str
    surface: str
    conversation_kind: str
    user_ref: str
    chat_ref: str | None
    memory_load_policy: str
    pending_state_scope: str


@dataclass(frozen=True)
class HarnessToolPolicy:
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    enabled_toolsets: tuple[str, ...]
    mutation_classes_allowed: tuple[MutationClass, ...]
    requires_approval_for: tuple[MutationClass, ...]
    network_policy: str
    elevated_allowed: bool


@dataclass(frozen=True)
class HarnessExecutionPolicy:
    can_mutate_files: bool
    can_launch_mission: bool
    can_write_memory: bool
    can_delete_schedule: bool
    can_create_chip: bool
    can_publish: bool
    can_use_external_network: bool


@dataclass(frozen=True)
class TurnIntentEnvelope:
    schema: Literal["spark.turn_intent.v1"]
    turn_id: str
    trace_id: str
    surface: str
    directive: HarnessDirective
    selected_intent: HarnessSelectedIntent
    session_scope: HarnessSessionScope
    tool_policy: HarnessToolPolicy
    execution_policy: HarnessExecutionPolicy
    threat_reason_codes: tuple[str, ...]


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    nested = value.get(field) if isinstance(value, dict) else None
    if not isinstance(nested, dict):
        raise ValueError(f"Turn intent envelope missing object field: {field}")
    return nested


def _require_str(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"Turn intent envelope missing string field: {field}")
    return item


def _bool(value: dict[str, Any], field: str) -> bool:
    return bool(value.get(field))


def _tuple_str(value: dict[str, Any], field: str) -> tuple[str, ...]:
    items = value.get(field)
    if not isinstance(items, list):
        raise ValueError(f"Turn intent envelope missing list field: {field}")
    return tuple(str(item) for item in items if isinstance(item, str))


def parse_turn_intent_envelope(payload: dict[str, Any]) -> TurnIntentEnvelope:
    if not isinstance(payload, dict):
        raise ValueError("Turn intent envelope must be an object")
    if payload.get("schema") != "spark.turn_intent.v1":
        raise ValueError("Unsupported turn intent envelope schema")

    directive = _require_dict(payload, "directive")
    selected_intent = _require_dict(payload, "selectedIntent")
    session_scope = _require_dict(payload, "sessionScope")
    tool_policy = _require_dict(payload, "toolPolicy")
    execution_policy = _require_dict(payload, "executionPolicy")
    threat_defense = _require_dict(payload, "threatDefense")

    return TurnIntentEnvelope(
        schema="spark.turn_intent.v1",
        turn_id=_require_str(payload, "turnId"),
        trace_id=_require_str(payload, "traceId"),
        surface=_require_str(payload, "surface"),
        directive=HarnessDirective(
            mode=_require_str(directive, "mode"),
            no_execution=_bool(directive, "noExecution"),
            no_publish=_bool(directive, "noPublish"),
            local_only=_bool(directive, "localOnly"),
            explanation_only=_bool(directive, "explanationOnly"),
            quoted_or_meta_language=_bool(directive, "quotedOrMetaLanguage"),
        ),
        selected_intent=HarnessSelectedIntent(
            kind=_require_str(selected_intent, "kind"),
            owner_system=_require_str(selected_intent, "ownerSystem"),
            action=selected_intent.get("action") if isinstance(selected_intent.get("action"), str) else None,
            confidence=_require_str(selected_intent, "confidence"),
            requires_confirmation=_bool(selected_intent, "requiresConfirmation"),
            source=_require_str(selected_intent, "source"),
        ),
        session_scope=HarnessSessionScope(
            session_key=_require_str(session_scope, "sessionKey"),
            surface=_require_str(session_scope, "surface"),
            conversation_kind=_require_str(session_scope, "conversationKind"),
            user_ref=_require_str(session_scope, "userRef"),
            chat_ref=session_scope.get("chatRef") if isinstance(session_scope.get("chatRef"), str) else None,
            memory_load_policy=_require_str(session_scope, "memoryLoadPolicy"),
            pending_state_scope=_require_str(session_scope, "pendingStateScope"),
        ),
        tool_policy=HarnessToolPolicy(
            allowed_tools=_tuple_str(tool_policy, "allowedTools"),
            denied_tools=_tuple_str(tool_policy, "deniedTools"),
            enabled_toolsets=_tuple_str(tool_policy, "enabledToolsets"),
            mutation_classes_allowed=tuple(
                item for item in _tuple_str(tool_policy, "mutationClassesAllowed")
                if item in {
                    "none",
                    "read_only",
                    "writes_memory",
                    "writes_files",
                    "launches_mission",
                    "creates_schedule",
                    "deletes_schedule",
                    "creates_chip",
                    "publishes",
                    "external_network",
                }
            ),
            requires_approval_for=tuple(
                item for item in _tuple_str(tool_policy, "requiresApprovalFor")
                if item in {
                    "none",
                    "read_only",
                    "writes_memory",
                    "writes_files",
                    "launches_mission",
                    "creates_schedule",
                    "deletes_schedule",
                    "creates_chip",
                    "publishes",
                    "external_network",
                }
            ),
            network_policy=_require_str(tool_policy, "networkPolicy"),
            elevated_allowed=_bool(tool_policy, "elevatedAllowed"),
        ),
        execution_policy=HarnessExecutionPolicy(
            can_mutate_files=_bool(execution_policy, "canMutateFiles"),
            can_launch_mission=_bool(execution_policy, "canLaunchMission"),
            can_write_memory=_bool(execution_policy, "canWriteMemory"),
            can_delete_schedule=_bool(execution_policy, "canDeleteSchedule"),
            can_create_chip=_bool(execution_policy, "canCreateChip"),
            can_publish=_bool(execution_policy, "canPublish"),
            can_use_external_network=_bool(execution_policy, "canUseExternalNetwork"),
        ),
        threat_reason_codes=_tuple_str(threat_defense, "reasonCodes"),
    )


def authorize_tool_call(
    envelope: TurnIntentEnvelope | None,
    *,
    tool_name: str,
    owner_system: str,
    mutation_class: MutationClass,
    publishes: bool = False,
    external_network: bool = False,
) -> tuple[Literal["allowed", "blocked"], tuple[str, ...]]:
    if envelope is None:
        return "blocked", ("missing_or_invalid_envelope",)

    reasons: list[str] = []
    if envelope.directive.no_execution and mutation_class not in ("none", "read_only"):
        reasons.append("no_execution_boundary")
    if envelope.directive.no_publish and publishes:
        reasons.append("no_publish_boundary")
    if external_network and not envelope.execution_policy.can_use_external_network:
        reasons.append("external_network_not_authorized")
    if tool_name in envelope.tool_policy.denied_tools:
        reasons.append("tool_denied_by_policy")
    if tool_name not in envelope.tool_policy.allowed_tools:
        reasons.append("tool_not_allowed_by_policy")
    if mutation_class not in envelope.tool_policy.mutation_classes_allowed:
        reasons.append("mutation_class_not_authorized")
    if owner_system != envelope.selected_intent.owner_system and owner_system != "spark-telegram-bot":
        reasons.append("owner_mismatch")

    if reasons:
        return "blocked", tuple(reasons)
    return "allowed", ()
