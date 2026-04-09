from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class CapabilityRouteDecision:
    task: str
    target_system: str
    route_mode: str
    reason: str
    supporting_systems: list[str]
    required_capabilities: list[str]
    availability: dict[str, bool]
    constraints: list[str]
    next_actions: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "target_system": self.target_system,
            "route_mode": self.route_mode,
            "reason": self.reason,
            "supporting_systems": self.supporting_systems,
            "required_capabilities": self.required_capabilities,
            "availability": self.availability,
            "constraints": self.constraints,
            "next_actions": self.next_actions,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


def looks_like_capability_router_query(message: str) -> bool:
    lowered_message = str(message or "").strip().lower()
    if not lowered_message:
        return False
    direct_signals = (
        "should this stay in builder",
        "should this go to swarm",
        "should this go to researcher",
        "what should handle this",
        "which system should handle",
        "which tool should handle",
        "what should you use for this",
        "what should spark use for this",
        "should you browse this",
        "should you search the web",
        "should you use browser",
        "should you use voice",
        "route this task",
    )
    return any(signal in lowered_message for signal in direct_signals)


def build_capability_route_decision(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
) -> CapabilityRouteDecision:
    from spark_intelligence.mission_control import build_mission_control_snapshot
    from spark_intelligence.swarm_bridge import evaluate_swarm_escalation
    from spark_intelligence.system_registry import build_system_registry

    normalized_task = str(task or "").strip()
    lowered = normalized_task.lower()
    system_registry = build_system_registry(config_manager, state_db).to_payload()
    mission_control = build_mission_control_snapshot(config_manager, state_db).to_payload()
    system_records = {
        str(record.get("key") or ""): record
        for record in (system_registry.get("records") or [])
        if isinstance(record, dict) and str(record.get("kind") or "") == "system"
    }

    availability = {
        "builder": _system_available(system_records, "spark_intelligence_builder"),
        "researcher": _system_available(system_records, "spark_researcher"),
        "swarm": _system_available(system_records, "spark_swarm"),
        "browser": _system_available(system_records, "spark_browser"),
        "voice": _system_available(system_records, "spark_voice"),
        "memory": _system_available(system_records, "spark_memory"),
    }
    constraints = list(mission_control.get("summary", {}).get("degraded_surfaces") or [])
    next_actions = list(mission_control.get("summary", {}).get("recommended_actions") or [])

    if _looks_like_voice_task(lowered):
        target_system = "Spark Voice"
        route_mode = "voice_io"
        reason = "The task is about speech input/output, so route through Builder plus the voice surface."
        supporting_systems = ["Spark Intelligence Builder"]
        required_capabilities = ["speech_to_text", "text_to_speech"]
    elif _looks_like_browser_task(lowered):
        target_system = "Spark Browser" if availability["browser"] else "Spark Researcher"
        route_mode = "browser_grounded" if availability["browser"] else "researcher_without_browser"
        reason = (
            "The task needs live web or page evidence, so use the browser/search surface when it is available."
            if availability["browser"]
            else "The task needs live web or page evidence, but the browser surface is unavailable, so fall back to Researcher."
        )
        supporting_systems = ["Spark Intelligence Builder"]
        required_capabilities = ["web_search", "page_inspection"]
    elif _looks_like_self_knowledge_task(lowered):
        target_system = "Spark Intelligence Builder"
        route_mode = "self_knowledge"
        reason = "The task is about Spark's own systems, chips, or runtime state, so Builder should answer from registry and mission-control context."
        supporting_systems = ["Spark Researcher"]
        required_capabilities = ["routing", "attachments", "operator_controls"]
    else:
        swarm_decision = evaluate_swarm_escalation(
            config_manager=config_manager,
            state_db=state_db,
            task=normalized_task,
        )
        if swarm_decision.escalate:
            target_system = "Spark Swarm"
            route_mode = "swarm_escalation"
            reason = swarm_decision.reason
            supporting_systems = ["Spark Intelligence Builder", "Spark Researcher"]
            required_capabilities = ["collective_coordination", "autoloops"]
            constraints.extend(str(item) for item in (swarm_decision.triggers or []) if str(item))
        elif (
            swarm_decision.mode in {"unavailable", "disabled"}
            and ((swarm_decision.triggers or []) or "swarm" in lowered or "delegate" in lowered)
        ):
            target_system = "Spark Researcher" if availability["researcher"] else "Spark Intelligence Builder"
            route_mode = "swarm_unavailable_hold_local"
            reason = f"{swarm_decision.reason} Keep the task on the primary runtime until Swarm is ready."
            supporting_systems = ["Spark Intelligence Builder"] if target_system == "Spark Researcher" else []
            required_capabilities = ["provider_advisory", "reasoning"] if target_system == "Spark Researcher" else ["routing", "delivery"]
            constraints.extend(str(item) for item in (swarm_decision.triggers or []) if str(item))
        elif availability["researcher"]:
            target_system = "Spark Researcher"
            route_mode = "researcher_advisory"
            reason = "This looks like normal single-threaded advisory or constructive work, so keep it in Builder backed by Researcher."
            supporting_systems = ["Spark Intelligence Builder"]
            required_capabilities = ["provider_advisory", "reasoning", "conversation_support"]
        else:
            target_system = "Spark Intelligence Builder"
            route_mode = "builder_local"
            reason = "Researcher is not currently available, so Builder should handle the task locally."
            supporting_systems = []
            required_capabilities = ["delivery", "operator_controls", "routing"]

    if not next_actions:
        next_actions = ["Proceed with the routed system unless a degraded surface blocks execution."]

    return CapabilityRouteDecision(
        task=normalized_task,
        target_system=target_system,
        route_mode=route_mode,
        reason=reason,
        supporting_systems=supporting_systems,
        required_capabilities=required_capabilities,
        availability=availability,
        constraints=_dedupe_preserve_order(constraints)[:8],
        next_actions=_dedupe_preserve_order(next_actions)[:4],
    )


def build_capability_router_prompt_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
) -> str:
    if not looks_like_capability_router_query(user_message):
        return ""
    decision = build_capability_route_decision(
        config_manager=config_manager,
        state_db=state_db,
        task=user_message,
    )
    payload = decision.to_payload()
    lines = ["[Spark capability router]"]
    lines.append(f"- target_system={payload['target_system']}")
    lines.append(f"- route_mode={payload['route_mode']}")
    lines.append(f"- reason={payload['reason']}")
    supporting_systems = [str(item) for item in (payload.get("supporting_systems") or []) if str(item)]
    if supporting_systems:
        lines.append(f"- supporting_systems={','.join(supporting_systems)}")
    required_capabilities = [str(item) for item in (payload.get("required_capabilities") or []) if str(item)]
    if required_capabilities:
        lines.append(f"- required_capabilities={','.join(required_capabilities)}")
    availability = payload.get("availability") or {}
    if availability:
        availability_line = ",".join(
            f"{key}={'yes' if bool(value) else 'no'}" for key, value in availability.items()
        )
        lines.append(f"- availability={availability_line}")
    constraints = [str(item) for item in (payload.get("constraints") or []) if str(item)]
    if constraints:
        lines.append("[Route constraints]")
        lines.extend(f"- {item}" for item in constraints[:4])
    next_actions = [str(item) for item in (payload.get("next_actions") or []) if str(item)]
    if next_actions:
        lines.append("[Route actions]")
        lines.extend(f"- {item}" for item in next_actions[:3])
    lines.extend(
        [
            "[Reply rule]",
            "When the user asks which system should handle a task, whether to stay in Builder, whether to escalate to Swarm, or whether browser or voice should be used, answer from this capability-router decision instead of improvising from names alone.",
        ]
    )
    return "\n".join(lines)


def _looks_like_browser_task(lowered: str) -> bool:
    signals = (
        "search the web",
        "browse",
        "open the site",
        "look it up",
        "latest",
        "today",
        "current price",
        "what does this page say",
        "check the website",
        "use browser",
    )
    return any(signal in lowered for signal in signals)


def _looks_like_voice_task(lowered: str) -> bool:
    signals = (
        "voice note",
        "voice reply",
        "audio reply",
        "transcribe",
        "speak this",
        "say this out loud",
        "use voice",
    )
    return any(signal in lowered for signal in signals)


def _looks_like_self_knowledge_task(lowered: str) -> bool:
    signals = (
        "what chips",
        "what systems",
        "what are you connected to",
        "what can you do",
        "what tools",
        "what is active",
        "what is degraded",
    )
    return any(signal in lowered for signal in signals)


def _system_available(system_records: dict[str, dict[str, Any]], key: str) -> bool:
    record = system_records.get(key) or {}
    status = str(record.get("status") or "").strip().lower()
    return bool(record) and status not in {"missing", "degraded"}


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered
