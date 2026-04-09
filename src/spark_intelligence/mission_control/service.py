from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB
from spark_intelligence.system_registry import build_system_registry

_WATCHTOWER_DIMENSION_LABELS = {
    "ingress_health": "ingress",
    "execution_health": "execution",
    "delivery_health": "delivery",
    "scheduler_freshness": "scheduler",
    "environment_parity": "environment",
}


@dataclass(frozen=True)
class MissionControlSnapshot:
    generated_at: str
    workspace_id: str
    summary: dict[str, Any]
    panels: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "summary": self.summary,
            "panels": self.panels,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class MissionControlPlan:
    generated_at: str
    workspace_id: str
    task: str
    summary: dict[str, Any]
    details: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "task": self.task,
            "summary": self.summary,
            "details": self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


def looks_like_mission_control_query(message: str) -> bool:
    lowered_message = str(message or "").strip().lower()
    if not lowered_message:
        return False
    direct_signals = (
        "mission control",
        "what are you doing right now",
        "what is active right now",
        "what is degraded",
        "what is broken",
        "what needs attention",
        "what should i focus on",
        "what should the operator focus on",
        "what should i look at next",
        "what loops are running",
        "what jobs are running",
        "what maintenance is running",
        "what is healthy right now",
        "what is the current state",
    )
    return any(signal in lowered_message for signal in direct_signals)


def build_mission_control_snapshot(config_manager: ConfigManager, state_db: StateDB) -> MissionControlSnapshot:
    from spark_intelligence.adapters.discord.runtime import build_discord_runtime_summary
    from spark_intelligence.adapters.telegram.runtime import (
        build_telegram_runtime_summary,
        read_telegram_runtime_health,
    )
    from spark_intelligence.adapters.whatsapp.runtime import build_whatsapp_runtime_summary
    from spark_intelligence.gateway.runtime import gateway_status
    from spark_intelligence.jobs.service import list_job_records
    from spark_intelligence.observability.store import build_watchtower_snapshot
    from spark_intelligence.researcher_bridge import researcher_bridge_status
    from spark_intelligence.swarm_bridge import swarm_status

    gateway = gateway_status(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    swarm = swarm_status(config_manager, state_db)
    watchtower = build_watchtower_snapshot(state_db)
    system_registry = build_system_registry(config_manager, state_db).to_payload()
    telegram_summary = build_telegram_runtime_summary(config_manager, state_db)
    telegram_health = read_telegram_runtime_health(state_db)
    discord_summary = build_discord_runtime_summary(config_manager, state_db)
    whatsapp_summary = build_whatsapp_runtime_summary(config_manager, state_db)
    job_records = list_job_records(state_db)
    workspace_id = str(config_manager.get_path("workspace.id", default="default"))

    active_systems = _derive_active_systems(system_registry)
    degraded_surfaces = _derive_degraded_surfaces(
        system_registry=system_registry,
        gateway=gateway,
        researcher=researcher,
        swarm=swarm,
        watchtower=watchtower,
        telegram_summary=telegram_summary,
        telegram_health=telegram_health,
        discord_summary=discord_summary,
        whatsapp_summary=whatsapp_summary,
        job_records=job_records,
    )
    active_channels = [
        channel
        for channel in (gateway.configured_channels or [])
        if channel in {"telegram", "discord", "whatsapp"}
    ]
    active_loops = _derive_active_loops(job_records=job_records)
    recommended_actions = _derive_recommended_actions(
        gateway=gateway,
        researcher=researcher,
        swarm=swarm,
        watchtower=watchtower,
        telegram_summary=telegram_summary,
        telegram_health=telegram_health,
        active_loops=active_loops,
    )
    top_level_state = _derive_top_level_state(
        gateway=gateway,
        researcher=researcher,
        swarm=swarm,
        watchtower=watchtower,
        degraded_surfaces=degraded_surfaces,
        telegram_health=telegram_health,
    )
    current_focus = _derive_current_focus(
        recommended_actions=recommended_actions,
        watchtower=watchtower,
        gateway=gateway,
        top_level_state=top_level_state,
    )

    watchtower_dimensions = watchtower.get("health_dimensions") or {}
    panels = {
        "gateway": {
            "ready": gateway.ready,
            "configured_channels": gateway.configured_channels,
            "configured_providers": gateway.configured_providers,
            "provider_runtime_ok": gateway.provider_runtime_ok,
            "provider_execution_ok": gateway.provider_execution_ok,
            "oauth_maintenance_ok": gateway.oauth_maintenance_ok,
            "repair_hints": gateway.repair_hints[:5],
        },
        "researcher": {
            "available": researcher.available,
            "mode": researcher.mode,
            "last_provider_transport": researcher.last_provider_execution_transport,
            "last_route": researcher.last_routing_decision,
            "last_active_chip_key": researcher.last_active_chip_key,
        },
        "swarm": {
            "enabled": swarm.enabled,
            "payload_ready": swarm.payload_ready,
            "api_ready": swarm.api_ready,
            "auth_state": swarm.auth_state,
            "workspace_id": swarm.workspace_id,
            "last_sync": swarm.last_sync,
            "last_decision": swarm.last_decision,
        },
        "watchtower": {
            "top_level_state": watchtower.get("top_level_state"),
            "health_dimensions": {
                key: {
                    "state": (watchtower_dimensions.get(key) or {}).get("state"),
                    "detail": (watchtower_dimensions.get(key) or {}).get("detail"),
                }
                for key in _WATCHTOWER_DIMENSION_LABELS
            },
        },
        "channels": {
            "telegram": {
                "configured": telegram_summary.configured,
                "status": telegram_summary.status,
                "pairing_mode": telegram_summary.pairing_mode,
                "bot_username": telegram_summary.bot_username,
                "auth_status": telegram_health.auth_status,
                "last_ok_at": telegram_health.last_ok_at,
                "consecutive_failures": telegram_health.consecutive_failures,
            },
            "discord": {
                "configured": discord_summary.configured,
                "status": discord_summary.status,
                "ingress_mode": discord_summary.ingress_mode(),
                "allowed_user_count": discord_summary.allowed_user_count,
            },
            "whatsapp": {
                "configured": whatsapp_summary.configured,
                "status": whatsapp_summary.status,
                "ingress_mode": whatsapp_summary.ingress_mode(),
                "allowed_user_count": whatsapp_summary.allowed_user_count,
            },
        },
        "jobs": {
            "scheduled": [
                {
                    "job_id": record.job_id,
                    "status": record.status,
                    "last_run_at": record.last_run_at,
                    "last_result": record.last_result,
                }
                for record in job_records
                if record.status == "scheduled"
            ],
            "all": [
                {
                    "job_id": record.job_id,
                    "status": record.status,
                    "last_run_at": record.last_run_at,
                    "last_result": record.last_result,
                }
                for record in job_records[:8]
            ],
        },
    }
    summary = {
        "top_level_state": top_level_state,
        "active_systems": active_systems,
        "degraded_surfaces": degraded_surfaces,
        "active_channels": active_channels,
        "active_loops": active_loops,
        "current_focus": current_focus,
        "recommended_actions": recommended_actions,
        "current_capabilities": list((system_registry.get("summary") or {}).get("current_capabilities") or []),
    }
    return MissionControlSnapshot(
        generated_at=_now_iso(),
        workspace_id=workspace_id,
        summary=summary,
        panels=panels,
    )


def build_mission_control_plan(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
    forced_harness_id: str | None = None,
    forced_recipe_id: str | None = None,
) -> MissionControlPlan:
    from spark_intelligence.capability_router import build_capability_route_decision
    from spark_intelligence.harness_registry import (
        build_harness_selection,
        select_auto_harness_recipe,
        select_harness_recipe,
    )

    normalized_task = str(task or "").strip()
    snapshot = build_mission_control_snapshot(config_manager, state_db).to_payload()
    summary = snapshot.get("summary") or {}
    route = build_capability_route_decision(
        config_manager=config_manager,
        state_db=state_db,
        task=normalized_task,
    ).to_payload()

    recipe_payload = None
    selection_mode = "route_only"
    effective_harness_id = str(forced_harness_id or "").strip() or None
    harness_route_mode_override = None
    harness_reason_override = None
    harness_next_action_override = None
    if forced_recipe_id:
        recipe_payload = select_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            recipe_id=forced_recipe_id,
        ).to_payload()
        effective_harness_id = str(recipe_payload.get("primary_harness_id") or "").strip() or effective_harness_id
        selection_mode = "explicit_recipe"
        harness_route_mode_override = "recipe_primary_harness"
        harness_reason_override = (
            f"Recipe {recipe_payload.get('recipe_id') or 'unknown'} starts with {effective_harness_id}."
        )
        harness_next_action_override = "Run the recipe's primary harness unless a degraded surface blocks execution."
    elif effective_harness_id:
        selection_mode = "explicit_harness"
        harness_route_mode_override = "forced_harness"
        harness_reason_override = f"Operator forced the harness to {effective_harness_id}."
        harness_next_action_override = "Run the forced harness unless a degraded surface blocks execution."
    else:
        auto_recipe = select_auto_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            task=normalized_task,
        )
        if auto_recipe is not None:
            recipe_payload = auto_recipe.to_payload()
            effective_harness_id = str(auto_recipe.recipe.primary_harness_id or "").strip() or None
            selection_mode = "auto_recipe"
            harness_route_mode_override = "auto_recipe_primary_harness"
            harness_reason_override = (
                f"Auto-selected recipe {auto_recipe.recipe.recipe_id} starts with {effective_harness_id}."
            )
            harness_next_action_override = "Run the recipe's primary harness unless a degraded surface blocks execution."

    harness = build_harness_selection(
        config_manager=config_manager,
        state_db=state_db,
        task=normalized_task,
    ).to_payload()
    if effective_harness_id and effective_harness_id != str(harness.get("harness_id") or ""):
        harness = build_harness_selection_override(
            config_manager=config_manager,
            state_db=state_db,
            task=normalized_task,
            harness_id=effective_harness_id,
            route_mode=harness_route_mode_override or "forced_harness",
            reason=harness_reason_override or f"Operator forced the harness to {effective_harness_id}.",
            next_action=harness_next_action_override or "Run the forced harness unless a degraded surface blocks execution.",
        )

    degraded_surfaces = [str(item) for item in (summary.get("degraded_surfaces") or []) if str(item)]
    blockers = _derive_plan_blockers(
        route=route,
        harness=harness,
        recipe=recipe_payload,
        degraded_surfaces=degraded_surfaces,
    )
    next_actions = _derive_plan_next_actions(
        mission_summary=summary,
        route=route,
        harness=harness,
        recipe=recipe_payload,
        blockers=blockers,
    )
    selected_system = str(harness.get("owner_system") or route.get("target_system") or "")
    plan_summary = {
        "top_level_state": summary.get("top_level_state") or "unknown",
        "current_focus": summary.get("current_focus") or "",
        "selected_system": selected_system,
        "route_target_system": route.get("target_system") or "",
        "selected_harness": harness.get("harness_id") or "",
        "selected_recipe": (recipe_payload or {}).get("recipe_id") or None,
        "selection_mode": selection_mode,
        "blockers": blockers,
        "degraded_surfaces": degraded_surfaces,
        "next_actions": next_actions,
    }
    plan_details = {
        "route": route,
        "harness": harness,
        "recipe": recipe_payload,
        "active_channels": list(summary.get("active_channels") or []),
        "active_loops": list(summary.get("active_loops") or []),
        "current_capabilities": list(summary.get("current_capabilities") or []),
    }
    return MissionControlPlan(
        generated_at=_now_iso(),
        workspace_id=str(snapshot.get("workspace_id") or config_manager.get_path("workspace.id", default="default")),
        task=normalized_task,
        summary=plan_summary,
        details=plan_details,
    )


def build_mission_control_prompt_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
) -> str:
    if not looks_like_mission_control_query(user_message):
        return ""
    payload = build_mission_control_snapshot(config_manager, state_db).to_payload()
    summary = payload.get("summary") or {}
    lines = ["[Spark mission control]"]
    lines.append(f"- state={summary.get('top_level_state') or 'unknown'}")
    if summary.get("current_focus"):
        lines.append(f"- focus={summary['current_focus']}")
    active_systems = [str(item) for item in (summary.get("active_systems") or []) if str(item)]
    if active_systems:
        lines.append(f"- active_systems={','.join(active_systems[:8])}")
    active_channels = [str(item) for item in (summary.get("active_channels") or []) if str(item)]
    if active_channels:
        lines.append(f"- active_channels={','.join(active_channels[:5])}")
    degraded_surfaces = [str(item) for item in (summary.get("degraded_surfaces") or []) if str(item)]
    if degraded_surfaces:
        lines.append(f"- degraded_surfaces={','.join(degraded_surfaces[:8])}")
    active_loops = [str(item) for item in (summary.get("active_loops") or []) if str(item)]
    if active_loops:
        lines.append(f"- active_loops={','.join(active_loops[:6])}")
    recommended_actions = [str(item) for item in (summary.get("recommended_actions") or []) if str(item)]
    if recommended_actions:
        lines.append("[Operator actions]")
        lines.extend(f"- {item}" for item in recommended_actions[:4])
    lines.extend(
        [
            "[Reply rule]",
            "When the user asks what is active right now, what is degraded, what loops or jobs are running, what needs attention, or what the operator should look at next, answer from this mission-control snapshot instead of guessing.",
        ]
    )
    return "\n".join(lines)


def build_harness_selection_override(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
    harness_id: str,
    route_mode: str,
    reason: str,
    next_action: str,
) -> dict[str, Any]:
    from spark_intelligence.harness_registry import build_harness_registry

    registry = build_harness_registry(config_manager, state_db).to_payload()
    contracts = {
        str(contract.get("harness_id") or ""): contract
        for contract in (registry.get("contracts") or [])
        if isinstance(contract, dict)
    }
    normalized_harness_id = str(harness_id or "").strip()
    contract = contracts.get(normalized_harness_id)
    if contract is None:
        available = ", ".join(sorted(contracts)) or "none"
        raise ValueError(f"Unknown harness '{normalized_harness_id}'. Available harnesses: {available}")
    return {
        "task": str(task or "").strip(),
        "harness_id": normalized_harness_id,
        "label": str(contract.get("label") or normalized_harness_id),
        "owner_system": str(contract.get("owner_system") or ""),
        "backend_kind": str(contract.get("backend_kind") or "unknown"),
        "session_scope": str(contract.get("session_scope") or "unknown"),
        "prompt_strategy": str(contract.get("prompt_strategy") or "unknown"),
        "toolsets": [str(item) for item in (contract.get("toolsets") or []) if str(item)],
        "required_capabilities": [str(item) for item in (contract.get("required_capabilities") or []) if str(item)],
        "artifacts": [str(item) for item in (contract.get("artifacts") or []) if str(item)],
        "route_mode": str(route_mode or "override_harness"),
        "reason": str(reason or f"Use harness {normalized_harness_id}."),
        "next_actions": [str(next_action or "Run the selected harness unless a degraded surface blocks execution.")],
        "limitations": [str(item) for item in (contract.get("limitations") or []) if str(item)],
    }


def _derive_active_systems(system_registry: dict[str, Any]) -> list[str]:
    records = system_registry.get("records") or []
    active_systems = [
        str(record.get("label") or record.get("key") or "")
        for record in records
        if isinstance(record, dict) and str(record.get("kind") or "") == "system" and bool(record.get("active"))
    ]
    return [item for item in active_systems if item]


def _derive_active_loops(*, job_records: list[Any]) -> list[str]:
    loops: list[str] = []
    for record in job_records:
        if str(record.status or "") != "scheduled":
            continue
        loops.append(f"job:{record.job_id}")
    return loops


def _derive_degraded_surfaces(
    *,
    system_registry: dict[str, Any],
    gateway: Any,
    researcher: Any,
    swarm: Any,
    watchtower: dict[str, Any],
    telegram_summary: Any,
    telegram_health: Any,
    discord_summary: Any,
    whatsapp_summary: Any,
    job_records: list[Any],
) -> list[str]:
    degraded: list[str] = []
    for record in system_registry.get("records") or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("kind") or "") != "system":
            continue
        status = str(record.get("status") or "")
        if status in {"degraded", "missing"}:
            degraded.append(str(record.get("label") or record.get("key") or "system"))
    if not gateway.ready and gateway.configured_channels:
        degraded.append("Gateway readiness")
    if researcher.enabled and researcher.configured and not researcher.available:
        degraded.append("Spark Researcher")
    if swarm.enabled and swarm.configured and not swarm.payload_ready:
        degraded.append("Spark Swarm payload")
    watchtower_state = str(watchtower.get("top_level_state") or "unknown")
    if watchtower_state not in {"healthy", "unknown"}:
        degraded.append(f"Watchtower:{watchtower_state}")
        for key, label in _WATCHTOWER_DIMENSION_LABELS.items():
            dimension = (watchtower.get("health_dimensions") or {}).get(key) or {}
            if str(dimension.get("state") or "unknown") not in {"healthy", "unknown"}:
                degraded.append(f"Watchtower {label}")
    if telegram_summary.configured and str(telegram_health.auth_status or "").strip() not in {"", "ok"}:
        degraded.append("Telegram auth")
    if int(telegram_health.consecutive_failures or 0) > 0:
        degraded.append("Telegram polling")
    if discord_summary.configured and not discord_summary.ingress_ready():
        degraded.append("Discord ingress")
    if whatsapp_summary.configured and not whatsapp_summary.ingress_ready():
        degraded.append("WhatsApp ingress")
    if any(str(record.status or "") == "scheduled" for record in job_records):
        degraded.append("Scheduled maintenance pending")
    return _dedupe_preserve_order(degraded)


def _derive_recommended_actions(
    *,
    gateway: Any,
    researcher: Any,
    swarm: Any,
    watchtower: dict[str, Any],
    telegram_summary: Any,
    telegram_health: Any,
    active_loops: list[str],
) -> list[str]:
    actions: list[str] = []
    actions.extend(str(item) for item in (gateway.repair_hints or [])[:2] if str(item))
    if researcher.enabled and researcher.configured and not researcher.available:
        actions.append("Repair the Spark Researcher runtime/config before relying on provider-backed advisory.")
    if swarm.enabled and swarm.configured and not swarm.payload_ready:
        actions.append("Repair Spark Swarm payload readiness before escalation or autoloops.")
    if str(watchtower.get("top_level_state") or "unknown") not in {"healthy", "unknown"}:
        for key, label in _WATCHTOWER_DIMENSION_LABELS.items():
            dimension = (watchtower.get("health_dimensions") or {}).get(key) or {}
            if str(dimension.get("state") or "unknown") in {"healthy", "unknown"}:
                continue
            actions.append(f"Inspect Watchtower {label} health and resolve the current runtime inconsistency.")
            break
    if telegram_summary.configured and str(telegram_health.auth_status or "").strip() not in {"", "ok"}:
        actions.append("Repair Telegram bot auth and restart the gateway polling loop.")
    if int(telegram_health.consecutive_failures or 0) > 0:
        actions.append("Inspect Telegram poll failures in gateway traces before trusting live delivery health.")
    if active_loops:
        actions.append("Run `spark-intelligence jobs tick` to execute due maintenance work.")
    deduped = _dedupe_preserve_order(actions)
    return deduped[:5]


def _derive_current_focus(
    *,
    recommended_actions: list[str],
    watchtower: dict[str, Any],
    gateway: Any,
    top_level_state: str,
) -> str:
    if recommended_actions:
        return recommended_actions[0]
    watchtower_state = str(watchtower.get("top_level_state") or "unknown")
    if watchtower_state == "healthy" and gateway.ready and top_level_state == "healthy":
        return "Operate through Builder; no urgent control-plane action is required."
    return "Review degraded runtime surfaces before expanding scope."


def _derive_top_level_state(
    *,
    gateway: Any,
    researcher: Any,
    swarm: Any,
    watchtower: dict[str, Any],
    degraded_surfaces: list[str],
    telegram_health: Any,
) -> str:
    watchtower_state = str(watchtower.get("top_level_state") or "unknown")
    if watchtower_state in {"degraded", "critical"}:
        return "degraded"
    if int(telegram_health.consecutive_failures or 0) >= 3:
        return "degraded"
    if gateway.configured_channels and not gateway.ready:
        return "attention"
    if researcher.enabled and researcher.configured and not researcher.available:
        return "attention"
    if swarm.enabled and swarm.configured and not swarm.payload_ready:
        return "attention"
    if degraded_surfaces:
        return "attention"
    return "healthy"


def _derive_plan_blockers(
    *,
    route: dict[str, Any],
    harness: dict[str, Any],
    recipe: dict[str, Any] | None,
    degraded_surfaces: list[str],
) -> list[str]:
    blockers: list[str] = []
    availability = route.get("availability") or {}
    route_mode = str(route.get("route_mode") or "")
    if route_mode == "browser_grounded" and not bool(availability.get("browser")):
        blockers.append("Spark Browser is not currently available.")
    if route_mode == "voice_io" and not bool(availability.get("voice")):
        blockers.append("Spark Voice is not currently available.")
    if route_mode == "researcher_advisory" and not bool(availability.get("researcher")):
        blockers.append("Spark Researcher is not currently available.")
    if route_mode == "swarm_escalation" and not bool(availability.get("swarm")):
        blockers.append("Spark Swarm is not currently available.")
    if "degraded" in str(route_mode):
        blockers.append(str(route.get("reason") or "The preferred route is degraded."))
    limitations = [str(item) for item in (harness.get("limitations") or []) if str(item)]
    if recipe is not None and not bool(recipe.get("available")):
        blockers.append(f"Recipe {recipe.get('recipe_id') or 'unknown'} is not currently available.")
    blockers.extend(item for item in degraded_surfaces if item)
    blockers.extend(limitations[:2] if route_mode == "swarm_escalation" else [])
    return _dedupe_preserve_order(blockers)[:8]


def _derive_plan_next_actions(
    *,
    mission_summary: dict[str, Any],
    route: dict[str, Any],
    harness: dict[str, Any],
    recipe: dict[str, Any] | None,
    blockers: list[str],
) -> list[str]:
    actions: list[str] = []
    if recipe is not None:
        if recipe.get("selection_mode") == "auto":
            actions.append(
                f"Proceed with auto-selected recipe {recipe.get('recipe_id') or 'unknown'} unless the operator overrides it."
            )
        else:
            actions.append(f"Proceed with recipe {recipe.get('recipe_id') or 'unknown'}.")
    actions.extend(str(item) for item in (route.get("next_actions") or []) if str(item))
    actions.extend(str(item) for item in (harness.get("next_actions") or []) if str(item))
    actions.extend(str(item) for item in (mission_summary.get("recommended_actions") or []) if str(item))
    if blockers:
        actions.append("Repair the blocking runtime surface before dispatching execution.")
    return _dedupe_preserve_order(actions)[:6]


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
