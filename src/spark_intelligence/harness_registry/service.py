from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class HarnessContract:
    harness_id: str
    label: str
    owner_system: str
    route_modes: list[str]
    backend_kind: str
    session_scope: str
    prompt_strategy: str
    toolsets: list[str]
    required_capabilities: list[str]
    artifacts: list[str]
    retry_policy: str
    approval_mode: str
    limitations: list[str]
    available: bool
    degraded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "label": self.label,
            "owner_system": self.owner_system,
            "route_modes": self.route_modes,
            "backend_kind": self.backend_kind,
            "session_scope": self.session_scope,
            "prompt_strategy": self.prompt_strategy,
            "toolsets": self.toolsets,
            "required_capabilities": self.required_capabilities,
            "artifacts": self.artifacts,
            "retry_policy": self.retry_policy,
            "approval_mode": self.approval_mode,
            "limitations": self.limitations,
            "available": self.available,
            "degraded": self.degraded,
        }


@dataclass(frozen=True)
class HarnessRegistrySnapshot:
    generated_at: str
    workspace_id: str
    contracts: list[HarnessContract]
    recipes: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "contracts": [contract.to_dict() for contract in self.contracts],
            "recipes": self.recipes,
            "summary": {
                "contract_count": len(self.contracts),
                "available_contract_count": len([item for item in self.contracts if item.available]),
                "degraded_contract_count": len([item for item in self.contracts if item.degraded]),
                "available_harnesses": [item.harness_id for item in self.contracts if item.available],
                "recipe_count": len(self.recipes),
                "available_recipes": [
                    str(item.get("recipe_id") or "")
                    for item in self.recipes
                    if bool(item.get("available"))
                ],
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class HarnessSelectionDecision:
    task: str
    harness_id: str
    label: str
    owner_system: str
    backend_kind: str
    session_scope: str
    prompt_strategy: str
    toolsets: list[str]
    required_capabilities: list[str]
    artifacts: list[str]
    route_mode: str
    reason: str
    next_actions: list[str]
    limitations: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "harness_id": self.harness_id,
            "label": self.label,
            "owner_system": self.owner_system,
            "backend_kind": self.backend_kind,
            "session_scope": self.session_scope,
            "prompt_strategy": self.prompt_strategy,
            "toolsets": self.toolsets,
            "required_capabilities": self.required_capabilities,
            "artifacts": self.artifacts,
            "route_mode": self.route_mode,
            "reason": self.reason,
            "next_actions": self.next_actions,
            "limitations": self.limitations,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class HarnessRecipeSelection:
    recipe_id: str
    label: str
    description: str
    primary_harness_id: str
    follow_up_harness_ids: list[str]
    available: bool
    limitations: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "label": self.label,
            "description": self.description,
            "primary_harness_id": self.primary_harness_id,
            "follow_up_harness_ids": self.follow_up_harness_ids,
            "available": self.available,
            "limitations": self.limitations,
        }


@dataclass(frozen=True)
class AutoHarnessRecipeSelection:
    recipe: HarnessRecipeSelection
    reason: str

    def to_payload(self) -> dict[str, Any]:
        payload = self.recipe.to_payload()
        payload["selection_reason"] = self.reason
        payload["selection_mode"] = "auto"
        return payload


def looks_like_harness_query(message: str) -> bool:
    lowered_message = str(message or "").strip().lower()
    if not lowered_message:
        return False
    direct_signals = (
        "what harness",
        "which harness",
        "how would you execute",
        "how would spark execute",
        "how would you run this",
        "what execution path",
        "what execution contract",
        "how do you actually do this",
        "how would this actually get done",
        "what backend would you use",
        "what toolset would you use",
        "what session would this use",
    )
    return any(signal in lowered_message for signal in direct_signals)


def build_harness_registry(
    config_manager: ConfigManager,
    state_db: StateDB,
) -> HarnessRegistrySnapshot:
    from spark_intelligence.system_registry import build_system_registry

    system_registry = build_system_registry(config_manager, state_db).to_payload()
    system_records = {
        str(record.get("key") or ""): record
        for record in (system_registry.get("records") or [])
        if isinstance(record, dict) and str(record.get("kind") or "") == "system"
    }
    workspace_id = str(config_manager.get_path("workspace.id", default="default"))
    contracts = [
        _build_harness_contract(
            harness_id="builder.direct",
            label="Builder Direct Harness",
            owner_system="Spark Intelligence Builder",
            owner_key="spark_intelligence_builder",
            route_modes=["builder_local", "self_knowledge"],
            backend_kind="builder_runtime",
            session_scope="current_conversation",
            prompt_strategy="registry_and_personality_context_injected_per_turn",
            toolsets=["routing", "attachments", "persona_state", "operator_controls"],
            required_capabilities=["delivery", "routing", "operator_controls"],
            artifacts=["visible_reply", "state_updates", "observability_events"],
            retry_policy="retry_locally_after_repair_or_operator_adjustment",
            approval_mode="operator_governed",
            limitations=[
                "Best for single-threaded work inside the current Builder runtime.",
                "Does not provide external web evidence unless another harness is selected.",
            ],
            system_records=system_records,
        ),
        _build_harness_contract(
            harness_id="researcher.advisory",
            label="Researcher Advisory Harness",
            owner_system="Spark Researcher",
            owner_key="spark_researcher",
            route_modes=["researcher_advisory", "swarm_unavailable_hold_local", "researcher_without_browser"],
            backend_kind="provider_bridge",
            session_scope="current_conversation",
            prompt_strategy="ephemeral_contextual_task_through_researcher_bridge",
            toolsets=["provider_advisory", "reasoning", "conversation_support"],
            required_capabilities=["provider_advisory", "reasoning", "conversation_support"],
            artifacts=["visible_reply", "trace_ref", "evidence_summary"],
            retry_policy="retry_on_provider_resolution_or_transport_failure",
            approval_mode="operator_governed",
            limitations=[
                "Depends on configured provider/runtime health.",
                "Not the right path for multi-agent parallel execution.",
            ],
            system_records=system_records,
        ),
        _build_harness_contract(
            harness_id="browser.grounded",
            label="Browser Grounding Harness",
            owner_system="Spark Browser",
            owner_key="spark_browser",
            route_modes=["browser_grounded"],
            backend_kind="browser_bridge",
            session_scope="tab_or_search_session",
            prompt_strategy="governed_browser_payload_plus_contextual_task",
            toolsets=["web_search", "page_inspection", "source_capture"],
            required_capabilities=["web_search", "page_inspection", "source_capture"],
            artifacts=["search_results", "page_text", "citations"],
            retry_policy="retry_after_browser_reconnect_or_surface_repair",
            approval_mode="operator_governed",
            limitations=[
                "Requires a healthy browser surface and compatible attachment/runtime wiring.",
                "Optimized for evidence capture, not parallel orchestration.",
            ],
            system_records=system_records,
        ),
        _build_harness_contract(
            harness_id="voice.io",
            label="Voice I/O Harness",
            owner_system="Spark Voice",
            owner_key="spark_voice",
            route_modes=["voice_io"],
            backend_kind="voice_bridge",
            session_scope="message_turn",
            prompt_strategy="transcript_then_spoken_reply_render",
            toolsets=["speech_to_text", "text_to_speech"],
            required_capabilities=["speech_to_text", "text_to_speech"],
            artifacts=["transcript", "spoken_reply", "audio_delivery_record"],
            retry_policy="retry_after_provider_or_encoding_repair",
            approval_mode="operator_governed",
            limitations=[
                "Quality depends on STT/TTS provider health and channel delivery constraints.",
            ],
            system_records=system_records,
        ),
        _build_harness_contract(
            harness_id="swarm.escalation",
            label="Swarm Escalation Harness",
            owner_system="Spark Swarm",
            owner_key="spark_swarm",
            route_modes=["swarm_escalation"],
            backend_kind="swarm_bridge",
            session_scope="workspace_job",
            prompt_strategy="escalation_contract_plus_shared_return_summary",
            toolsets=["collective_coordination", "autoloops", "parallel_work"],
            required_capabilities=["collective_coordination", "autoloops"],
            artifacts=["swarm_plan", "delegated_work", "synthesized_summary"],
            retry_policy="hold_local_until_swarm_ready_then_resume",
            approval_mode="operator_governed",
            limitations=[
                "Requires Swarm enablement, payload readiness, and auth/API health.",
                "Should only be used when the task genuinely benefits from multi-agent execution.",
            ],
            system_records=system_records,
        ),
    ]
    recipes = _build_harness_recipes(contracts)
    return HarnessRegistrySnapshot(
        generated_at=_now_iso(),
        workspace_id=workspace_id,
        contracts=contracts,
        recipes=recipes,
    )


def build_harness_selection(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
) -> HarnessSelectionDecision:
    from spark_intelligence.capability_router import build_capability_route_decision

    route_decision = build_capability_route_decision(
        config_manager=config_manager,
        state_db=state_db,
        task=task,
    )
    registry = build_harness_registry(config_manager, state_db).to_payload()
    contracts = {
        str(contract.get("harness_id") or ""): contract
        for contract in (registry.get("contracts") or [])
        if isinstance(contract, dict)
    }
    harness_id = _select_harness_id(route_decision=route_decision, contracts=contracts)
    contract = contracts.get(harness_id) or {}
    next_actions = list(route_decision.next_actions or [])
    limitations = list(route_decision.constraints or [])
    limitations.extend(str(item) for item in (contract.get("limitations") or []) if str(item))
    return HarnessSelectionDecision(
        task=str(task or "").strip(),
        harness_id=harness_id,
        label=str(contract.get("label") or harness_id),
        owner_system=str(contract.get("owner_system") or route_decision.target_system),
        backend_kind=str(contract.get("backend_kind") or "unknown"),
        session_scope=str(contract.get("session_scope") or "unknown"),
        prompt_strategy=str(contract.get("prompt_strategy") or "unknown"),
        toolsets=[str(item) for item in (contract.get("toolsets") or []) if str(item)],
        required_capabilities=[str(item) for item in (contract.get("required_capabilities") or []) if str(item)],
        artifacts=[str(item) for item in (contract.get("artifacts") or []) if str(item)],
        route_mode=route_decision.route_mode,
        reason=route_decision.reason,
        next_actions=_dedupe_preserve_order(next_actions)[:4],
        limitations=_dedupe_preserve_order(limitations)[:6],
    )


def build_harness_prompt_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
) -> str:
    if not looks_like_harness_query(user_message):
        return ""
    registry = build_harness_registry(config_manager, state_db).to_payload()
    selection = build_harness_selection(
        config_manager=config_manager,
        state_db=state_db,
        task=user_message,
    ).to_payload()
    lines = ["[Spark harness registry]"]
    lines.append(f"- selected_harness={selection['harness_id']}")
    lines.append(f"- owner_system={selection['owner_system']}")
    lines.append(f"- backend_kind={selection['backend_kind']}")
    lines.append(f"- session_scope={selection['session_scope']}")
    lines.append(f"- prompt_strategy={selection['prompt_strategy']}")
    lines.append(f"- route_mode={selection['route_mode']}")
    lines.append(f"- reason={selection['reason']}")
    toolsets = [str(item) for item in (selection.get("toolsets") or []) if str(item)]
    if toolsets:
        lines.append(f"- toolsets={','.join(toolsets)}")
    capabilities = [str(item) for item in (selection.get("required_capabilities") or []) if str(item)]
    if capabilities:
        lines.append(f"- required_capabilities={','.join(capabilities)}")
    artifacts = [str(item) for item in (selection.get("artifacts") or []) if str(item)]
    if artifacts:
        lines.append(f"- artifacts={','.join(artifacts)}")
    summary = registry.get("summary") or {}
    available_harnesses = [str(item) for item in (summary.get("available_harnesses") or []) if str(item)]
    if available_harnesses:
        lines.append(f"- available_harnesses={','.join(available_harnesses[:8])}")
    available_recipes = [str(item) for item in (summary.get("available_recipes") or []) if str(item)]
    if available_recipes:
        lines.append(f"- available_recipes={','.join(available_recipes[:8])}")
    limitations = [str(item) for item in (selection.get("limitations") or []) if str(item)]
    if limitations:
        lines.append("[Harness limitations]")
        lines.extend(f"- {item}" for item in limitations[:4])
    next_actions = [str(item) for item in (selection.get("next_actions") or []) if str(item)]
    if next_actions:
        lines.append("[Harness actions]")
        lines.extend(f"- {item}" for item in next_actions[:3])
    lines.extend(
        [
            "[Reply rule]",
            "When the user asks how Spark would execute work, which harness it would use, which backend or toolset it would rely on, or how a task actually gets done, answer from this harness contract instead of inventing an execution path.",
        ]
    )
    return "\n".join(lines)


def select_harness_recipe(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    recipe_id: str,
) -> HarnessRecipeSelection:
    registry = build_harness_registry(config_manager, state_db).to_payload()
    recipes = {
        str(recipe.get("recipe_id") or ""): recipe
        for recipe in (registry.get("recipes") or [])
        if isinstance(recipe, dict)
    }
    normalized_recipe_id = str(recipe_id or "").strip()
    recipe = recipes.get(normalized_recipe_id)
    if recipe is None:
        available = ", ".join(sorted(recipes)) or "none"
        raise ValueError(f"Unknown harness recipe '{normalized_recipe_id}'. Available recipes: {available}")
    return HarnessRecipeSelection(
        recipe_id=str(recipe.get("recipe_id") or ""),
        label=str(recipe.get("label") or normalized_recipe_id),
        description=str(recipe.get("description") or ""),
        primary_harness_id=str(recipe.get("primary_harness_id") or ""),
        follow_up_harness_ids=[str(item) for item in (recipe.get("follow_up_harness_ids") or []) if str(item)],
        available=bool(recipe.get("available")),
        limitations=[str(item) for item in (recipe.get("limitations") or []) if str(item)],
    )


def select_auto_harness_recipe(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
) -> AutoHarnessRecipeSelection | None:
    normalized_task = str(task or "").strip()
    lowered = normalized_task.lower()
    if not lowered:
        return None
    route_decision = build_harness_selection(
        config_manager=config_manager,
        state_db=state_db,
        task=normalized_task,
    )
    if _looks_like_advisory_voice_recipe_task(lowered):
        recipe = select_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            recipe_id="advisory_voice_reply",
        )
        if recipe.available:
            return AutoHarnessRecipeSelection(
                recipe=recipe,
                reason="The task asks for a normal advisory answer and spoken delivery, so chain Researcher into Voice.",
            )
    if route_decision.route_mode == "swarm_escalation" and _looks_like_research_then_swarm_task(lowered):
        recipe = select_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            recipe_id="research_then_swarm",
        )
        if recipe.available:
            return AutoHarnessRecipeSelection(
                recipe=recipe,
                reason="The task asks for reasoning plus Swarm escalation, so chain Researcher into a Swarm dry-run handoff.",
            )
    return None


def _build_harness_contract(
    *,
    harness_id: str,
    label: str,
    owner_system: str,
    owner_key: str,
    route_modes: list[str],
    backend_kind: str,
    session_scope: str,
    prompt_strategy: str,
    toolsets: list[str],
    required_capabilities: list[str],
    artifacts: list[str],
    retry_policy: str,
    approval_mode: str,
    limitations: list[str],
    system_records: dict[str, dict[str, Any]],
) -> HarnessContract:
    system_record = system_records.get(owner_key) or {}
    status = str(system_record.get("status") or "").strip().lower()
    available = bool(system_record) and status not in {"missing"}
    degraded = status == "degraded"
    return HarnessContract(
        harness_id=harness_id,
        label=label,
        owner_system=owner_system,
        route_modes=route_modes,
        backend_kind=backend_kind,
        session_scope=session_scope,
        prompt_strategy=prompt_strategy,
        toolsets=toolsets,
        required_capabilities=required_capabilities,
        artifacts=artifacts,
        retry_policy=retry_policy,
        approval_mode=approval_mode,
        limitations=limitations,
        available=available,
        degraded=degraded,
    )


def _select_harness_id(
    *,
    route_decision: Any,
    contracts: dict[str, dict[str, Any]],
) -> str:
    route_mode = str(getattr(route_decision, "route_mode", "") or "").strip()
    if route_mode == "voice_io":
        return "voice.io"
    if route_mode == "browser_grounded":
        return "browser.grounded"
    if route_mode == "swarm_escalation":
        return "swarm.escalation"
    if route_mode in {"researcher_advisory", "researcher_without_browser"}:
        return "researcher.advisory"
    if route_mode == "swarm_unavailable_hold_local":
        target_system = str(getattr(route_decision, "target_system", "") or "")
        if "Researcher" in target_system and contracts.get("researcher.advisory"):
            return "researcher.advisory"
        return "builder.direct"
    return "builder.direct"


def _build_harness_recipes(contracts: list[HarnessContract]) -> list[dict[str, Any]]:
    contract_map = {contract.harness_id: contract for contract in contracts}

    def recipe_available(primary: str, follow_ups: list[str]) -> bool:
        ids = [primary, *follow_ups]
        return all(bool(contract_map.get(item) and contract_map[item].available) for item in ids)

    def recipe_limitations(primary: str, follow_ups: list[str]) -> list[str]:
        gathered: list[str] = []
        for harness_id in [primary, *follow_ups]:
            contract = contract_map.get(harness_id)
            if contract:
                gathered.extend(contract.limitations)
        return _dedupe_preserve_order(gathered)[:6]

    recipe_specs = [
        {
            "recipe_id": "advisory_voice_reply",
            "label": "Advisory Voice Reply",
            "description": "Run a normal advisory reply through Spark Researcher, then synthesize the final answer through Spark Voice.",
            "primary_harness_id": "researcher.advisory",
            "follow_up_harness_ids": ["voice.io"],
        },
        {
            "recipe_id": "research_then_swarm",
            "label": "Research Then Swarm",
            "description": "Generate an advisory answer first, then prepare the task for Swarm escalation with a dry-run payload build.",
            "primary_harness_id": "researcher.advisory",
            "follow_up_harness_ids": ["swarm.escalation"],
        },
        {
            "recipe_id": "browser_then_advisory",
            "label": "Browser Then Advisory",
            "description": "Open a governed browser-grounding step first, then hand the grounded task back into Spark Researcher.",
            "primary_harness_id": "browser.grounded",
            "follow_up_harness_ids": ["researcher.advisory"],
        },
    ]
    recipes: list[dict[str, Any]] = []
    for spec in recipe_specs:
        primary_harness_id = str(spec["primary_harness_id"])
        follow_up_harness_ids = [str(item) for item in spec["follow_up_harness_ids"]]
        recipes.append(
            {
                **spec,
                "available": recipe_available(primary_harness_id, follow_up_harness_ids),
                "limitations": recipe_limitations(primary_harness_id, follow_up_harness_ids),
            }
        )
    return recipes


def _looks_like_advisory_voice_recipe_task(lowered: str) -> bool:
    voice_signals = (
        "reply in voice",
        "answer in voice",
        "say it back",
        "say this back",
        "say that back",
        "voice reply",
        "spoken reply",
        "read it out loud",
        "read this out loud",
        "speak the answer",
        "answer aloud",
    )
    advisory_signals = (
        "?",
        "what ",
        "why ",
        "how ",
        "explain",
        "difference between",
        "tell me",
        "give me",
    )
    return any(signal in lowered for signal in voice_signals) and any(signal in lowered for signal in advisory_signals)


def _looks_like_research_then_swarm_task(lowered: str) -> bool:
    research_signals = (
        "research",
        "investigate",
        "analyze",
        "think through",
        "evaluate",
        "dig into",
    )
    swarm_signals = (
        "swarm",
        "delegate",
        "multi-agent",
        "parallel",
        "escalate",
    )
    return any(signal in lowered for signal in research_signals) and any(signal in lowered for signal in swarm_signals)


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
    return datetime.now(timezone.utc).isoformat()
