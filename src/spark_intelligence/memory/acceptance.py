from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import approve_pairing, consume_pairing_welcome, rename_agent_identity
from spark_intelligence.memory.orchestrator import export_memory_dashboard_movement_in_memory, hybrid_memory_retrieve
from spark_intelligence.memory.regression import _allocate_regression_identity
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class TelegramMemoryAcceptanceCase:
    case_id: str
    category: str
    message: str
    expected_bridge_mode: str | None = None
    expected_routing_decision: str | None = None
    expected_response_contains: tuple[str, ...] = ()
    expected_response_excludes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelegramMemoryGauntletCase:
    case_id: str
    category: str
    message: str
    expected_bridge_mode: str | None = None
    expected_routing_decision: str | None = None
    expected_response_contains: tuple[str, ...] = ()
    expected_response_excludes: tuple[str, ...] = ()
    expected_movement_states: tuple[str, ...] = ()


DEFAULT_TELEGRAM_MEMORY_GAUNTLET_CASES: tuple[TelegramMemoryGauntletCase, ...] = (
    TelegramMemoryGauntletCase(
        case_id="seed_current_focus",
        category="current_state_write",
        message="Set my current focus to persistent memory quality evaluation.",
        expected_response_contains=("persistent memory quality evaluation",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="seed_supporting_episode",
        category="episodic_write",
        message="For later, in our memory work today, we used the tiny desk plant named Sol as a low-stakes episodic recall probe.",
        expected_response_contains=("Sol",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="current_vs_supporting_recall",
        category="source_aware_recall",
        message="What do you remember about our memory work today, and what is current versus supporting context?",
        expected_response_contains=("current", "supporting"),
        expected_response_excludes=("status checklist",),
        expected_movement_states=("retrieved",),
    ),
    TelegramMemoryGauntletCase(
        case_id="memory_lack_self_awareness",
        category="self_awareness_memory_limits",
        message="Where does your memory still lack right now, and how would we improve it?",
        expected_response_contains=("memory", "improve"),
        expected_response_excludes=("Spark Browser", "Spark Voice"),
        expected_movement_states=("retrieved",),
    ),
)


HARD_TELEGRAM_MEMORY_GAUNTLET_CASES: tuple[TelegramMemoryGauntletCase, ...] = (
    *DEFAULT_TELEGRAM_MEMORY_GAUNTLET_CASES,
    TelegramMemoryGauntletCase(
        case_id="seed_stale_plan",
        category="stale_current_conflict",
        message="Set my current plan to verify scheduled memory cleanup.",
        expected_response_contains=("verify scheduled memory cleanup",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="replace_current_plan",
        category="stale_current_conflict",
        message="Set my current plan to evaluate open-ended persistent memory recall.",
        expected_response_contains=("evaluate open-ended persistent memory recall",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="current_plan_staleness_recall",
        category="stale_current_conflict",
        message="What is my current plan, and what older plan should not override it?",
        expected_response_contains=("evaluate open-ended persistent memory recall",),
        expected_response_excludes=("verify scheduled memory cleanup",),
        expected_movement_states=("retrieved",),
    ),
    TelegramMemoryGauntletCase(
        case_id="mutable_fact_authority_rule",
        category="authority_priority",
        message="What outranks wiki or old conversation when you answer mutable facts about me?",
        expected_response_contains=("current", "newest"),
        expected_response_excludes=("supporting_not_authoritative wins",),
    ),
)


LIMIT_TELEGRAM_MEMORY_GAUNTLET_CASES: tuple[TelegramMemoryGauntletCase, ...] = (
    *HARD_TELEGRAM_MEMORY_GAUNTLET_CASES,
    TelegramMemoryGauntletCase(
        case_id="seed_stale_timezone",
        category="mutable_fact_correction",
        message="Set my timezone to America/Los_Angeles.",
        expected_response_contains=("America/Los_Angeles",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="replace_timezone_current",
        category="mutable_fact_correction",
        message="Actually, set my timezone to Asia/Dubai.",
        expected_response_contains=("Asia/Dubai",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="timezone_wiki_conflict_recall",
        category="wiki_conflict",
        message=(
            "If an old wiki note or older conversation says my timezone is America/Los_Angeles, "
            "what timezone should you use right now?"
        ),
        expected_response_contains=("Asia/Dubai", "current"),
        expected_movement_states=("retrieved",),
    ),
    TelegramMemoryGauntletCase(
        case_id="seed_stale_preferred_name",
        category="mutable_fact_correction",
        message="Set my preferred name to Limit Probe Alpha.",
        expected_response_contains=("Limit Probe Alpha",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="replace_preferred_name_current",
        category="mutable_fact_correction",
        message="Actually, my preferred name is Cem.",
        expected_response_contains=("Cem",),
        expected_movement_states=("captured", "saved"),
    ),
    TelegramMemoryGauntletCase(
        case_id="preferred_name_stale_recall",
        category="stale_current_conflict",
        message="What preferred name should you use now, even if older recall says Limit Probe Alpha?",
        expected_response_contains=("Cem",),
        expected_movement_states=("retrieved",),
    ),
    TelegramMemoryGauntletCase(
        case_id="dashboard_movement_trace_probe",
        category="dashboard_traceability",
        message="When you answer from memory, what movement evidence should the dashboard show?",
        expected_response_contains=("retrieved", "captured", "saved"),
        expected_movement_states=("retrieved",),
    ),
    TelegramMemoryGauntletCase(
        case_id="task_recovery_current_authority_probe",
        category="task_recovery_authority",
        message=(
            "If task recovery or an older conversation points somewhere else, but current state says "
            "my plan is evaluate open-ended persistent memory recall, what should guide your next answer?"
        ),
        expected_response_contains=("current", "evaluate open-ended persistent memory recall"),
        expected_movement_states=("retrieved",),
    ),
)


DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES: tuple[TelegramMemoryAcceptanceCase, ...] = (
    TelegramMemoryAcceptanceCase(
        case_id="seed_focus",
        category="current_state",
        message="Set my current focus to persistent memory quality evaluation.",
        expected_response_contains=("persistent memory quality evaluation",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="seed_old_plan",
        category="stale_current_conflict",
        message="Set my current plan to verify scheduled memory cleanup.",
        expected_response_contains=("verify scheduled memory cleanup",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="replace_plan",
        category="stale_current_conflict",
        message="Set my current plan to evaluate open-ended persistent memory recall.",
        expected_response_contains=("evaluate open-ended persistent memory recall",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_seed",
        category="natural_recall",
        message="For later, the tiny desk plant is named Mira.",
        expected_response_contains=("Mira",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="current_focus_plan_query",
        category="current_state",
        message="What is my current focus and plan?",
        expected_bridge_mode="memory_current_focus_plan",
        expected_routing_decision="memory_current_focus_plan_query",
        expected_response_contains=(
            "persistent memory quality evaluation",
            "evaluate open-ended persistent memory recall",
        ),
        expected_response_excludes=("verify scheduled memory cleanup",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="open_ended_next_step",
        category="open_ended_recall",
        message="What should we focus on next?",
        expected_bridge_mode="memory_kernel_next_step",
        expected_routing_decision="memory_kernel_next_step",
        expected_response_contains=(
            "persistent memory quality evaluation",
            "evaluate open-ended persistent memory recall",
        ),
        expected_response_excludes=("verify scheduled memory cleanup",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="source_explanation",
        category="source_explanation",
        message="Why did you answer that?",
        expected_bridge_mode="context_source_debug",
        expected_routing_decision="context_source_debug",
        expected_response_contains=(
            "memory kernel next-step route",
            "promotion gates",
            "current_state",
        ),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_recall",
        category="natural_recall",
        message="What did I name the plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("Mira",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_replace",
        category="stale_current_conflict",
        message="Actually, the tiny desk plant is named Sol.",
        expected_response_contains=("Sol",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_updated_recall",
        category="natural_recall",
        message="What did I name the plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("Sol",),
        expected_response_excludes=("Mira",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_history_recall",
        category="stale_current_conflict",
        message="What was the plant called before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Mira", "Sol"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_seed_desk",
        category="entity_attribute_recall",
        message="For later, the tiny desk plant is on the kitchen shelf.",
        expected_response_contains=("kitchen shelf",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_seed_office",
        category="entity_attribute_recall",
        message="For later, the office plant is on the balcony.",
        expected_response_contains=("balcony",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_recall_desk",
        category="entity_attribute_recall",
        message="Where is the desk plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("kitchen shelf",),
        expected_response_excludes=("Sol",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_recall_office",
        category="entity_attribute_recall",
        message="Where is the office plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("balcony",),
        expected_response_excludes=("Sol", "kitchen shelf"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_replace_desk",
        category="stale_current_conflict",
        message="Actually, the tiny desk plant is on the windowsill.",
        expected_response_contains=("windowsill",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_updated_recall_desk",
        category="entity_attribute_recall",
        message="Where is the desk plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("windowsill",),
        expected_response_excludes=("Sol", "kitchen shelf"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_recall_source_explanation",
        category="source_explanation",
        message="Why did you answer that?",
        expected_bridge_mode="context_source_debug",
        expected_routing_decision="context_source_debug",
        expected_response_contains=(
            "entity-state current recall route",
            "query_kind: location_recall",
            "read_method: get_current_state",
            "entity-scoped location records",
        ),
        expected_response_excludes=("diagnostics: authority", "location history"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_history_recall_desk",
        category="stale_current_conflict",
        message="Where was the desk plant before?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("windowsill", "kitchen shelf"),
        expected_response_excludes=("Sol",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_location_history_source_explanation",
        category="source_explanation",
        message="Why did you answer that?",
        expected_bridge_mode="context_source_debug",
        expected_routing_decision="context_source_debug",
        expected_response_contains=(
            "entity-state history route",
            "predicate=entity.location",
            "attribute=location",
            "read_method=get_historical_state",
            "entity-scoped location history",
        ),
        expected_response_excludes=("diagnostics: authority",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_seed_launch",
        category="entity_attribute_recall",
        message="For later, Omar owns the launch checklist.",
        expected_response_contains=("launch checklist", "Omar"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_seed_investor",
        category="entity_attribute_recall",
        message="For later, Lina owns the investor update.",
        expected_response_contains=("investor update", "Lina"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_recall_launch",
        category="entity_attribute_recall",
        message="Who owns the launch checklist?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "Omar"),
        expected_response_excludes=("Lina",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_recall_investor",
        category="entity_attribute_recall",
        message="Who owns the investor update?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("investor update", "Lina"),
        expected_response_excludes=("Omar",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_replace_launch",
        category="stale_current_conflict",
        message="Actually, Maya owns the launch checklist.",
        expected_response_contains=("launch checklist", "Maya"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_updated_recall_launch",
        category="entity_attribute_recall",
        message="Who owns the launch checklist?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "Maya"),
        expected_response_excludes=("Omar", "Lina"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_history_recall_launch",
        category="stale_current_conflict",
        message="Who owned the launch checklist before?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("Maya", "Omar"),
        expected_response_excludes=("Lina",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_owner_history_source_explanation",
        category="source_explanation",
        message="Why did you answer that?",
        expected_bridge_mode="context_source_debug",
        expected_routing_decision="context_source_debug",
        expected_response_contains=(
            "entity-state history route",
            "predicate=entity.owner",
            "attribute=owner",
            "read_method=get_historical_state",
            "entity-scoped owner history",
        ),
        expected_response_excludes=("location history",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_status_seed_launch",
        category="entity_attribute_recall",
        message="For later, the launch checklist status is blocked.",
        expected_response_contains=("launch checklist", "blocked"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_status_replace_launch",
        category="stale_current_conflict",
        message="Actually, the launch checklist status is ready.",
        expected_response_contains=("launch checklist", "ready"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_status_recall_launch",
        category="entity_attribute_recall",
        message="What is the status of the launch checklist?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "ready"),
        expected_response_excludes=("blocked",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_status_history_launch",
        category="stale_current_conflict",
        message="What was the previous status of the launch checklist before?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("ready", "blocked"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_deadline_seed_launch",
        category="entity_attribute_recall",
        message="For later, the launch checklist deadline is Friday.",
        expected_response_contains=("launch checklist", "Friday"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_deadline_replace_launch",
        category="stale_current_conflict",
        message="Actually, the launch checklist is due Monday.",
        expected_response_contains=("launch checklist", "Monday"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_deadline_recall_launch",
        category="entity_attribute_recall",
        message="When is the launch checklist due?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "Monday"),
        expected_response_excludes=("Friday",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_deadline_history_launch",
        category="stale_current_conflict",
        message="When was the launch checklist due before?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("Monday", "Friday"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_relation_seed_launch",
        category="entity_attribute_recall",
        message="For later, the launch checklist relates to investor update.",
        expected_response_contains=("launch checklist", "investor update"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_relation_replace_launch",
        category="stale_current_conflict",
        message="Actually, the launch checklist relates to board prep.",
        expected_response_contains=("launch checklist", "board prep"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_relation_recall_launch",
        category="entity_attribute_recall",
        message="What is the launch checklist related to?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "board prep"),
        expected_response_excludes=("investor update",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_relation_history_launch",
        category="stale_current_conflict",
        message="What was the launch checklist related to before?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("board prep", "investor update"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_preference_seed_launch",
        category="entity_attribute_recall",
        message="For later, the launch checklist preference is concise bullets.",
        expected_response_contains=("launch checklist", "concise bullets"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_preference_replace_launch",
        category="stale_current_conflict",
        message="Actually, the launch checklist prefers short sections.",
        expected_response_contains=("launch checklist", "short sections"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_preference_recall_launch",
        category="entity_attribute_recall",
        message="What does the launch checklist prefer?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "short sections"),
        expected_response_excludes=("concise bullets",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_preference_recall_source_explanation",
        category="source_explanation",
        message="Why did you answer that?",
        expected_bridge_mode="context_source_debug",
        expected_routing_decision="context_source_debug",
        expected_response_contains=(
            "entity-state current recall route",
            "query_kind: preference_recall",
            "read_method: get_current_state",
            "entity-scoped preference records",
        ),
        expected_response_excludes=("diagnostics: authority", "preference history"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_preference_history_launch",
        category="stale_current_conflict",
        message="What did the launch checklist prefer before?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("short sections", "concise bullets"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_project_seed_launch",
        category="entity_attribute_recall",
        message="For later, the launch checklist active project is Neon Harbor.",
        expected_response_contains=("launch checklist", "Neon Harbor"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_project_replace_launch",
        category="stale_current_conflict",
        message="Actually, the launch checklist project is Seed Round.",
        expected_response_contains=("launch checklist", "Seed Round"),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_project_recall_launch",
        category="entity_attribute_recall",
        message="What project is the launch checklist for?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("launch checklist", "Seed Round"),
        expected_response_excludes=("Neon Harbor",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="entity_project_history_launch",
        category="stale_current_conflict",
        message="What was the previous project for the launch checklist?",
        expected_bridge_mode="memory_entity_state_history",
        expected_routing_decision="memory_entity_state_history_query",
        expected_response_contains=("Seed Round", "Neon Harbor"),
    ),
)


@dataclass(frozen=True)
class TelegramMemoryAcceptanceResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark Telegram memory acceptance"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- status: {summary.get('status') or 'unknown'}")
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- matched: {summary.get('matched_case_count', 0)}")
            lines.append(f"- mismatched: {summary.get('mismatched_case_count', 0)}")
            lines.append(f"- gate_status: {summary.get('promotion_gate_status') or 'unknown'}")
            lines.append(f"- selected_user_id: {summary.get('selected_user_id') or 'unknown'}")
        mismatches = self.payload.get("mismatches") if isinstance(self.payload, dict) else []
        if mismatches:
            lines.append("- mismatches:")
            for mismatch in mismatches[:8]:
                if not isinstance(mismatch, dict):
                    continue
                lines.append(
                    f"  - {mismatch.get('case_id') or 'unknown'}: "
                    + ", ".join(str(item) for item in mismatch.get("mismatches", []))
                )
        gate_assertions = self.payload.get("gate_assertions") if isinstance(self.payload, dict) else {}
        gate_mismatches = gate_assertions.get("mismatches") if isinstance(gate_assertions, dict) else []
        if gate_mismatches:
            lines.append("- gate mismatches:")
            lines.extend(f"  - {item}" for item in gate_mismatches)
        promotion_gates = gate_assertions.get("promotion_gates") if isinstance(gate_assertions, dict) else {}
        gates = promotion_gates.get("gates") if isinstance(promotion_gates, dict) else {}
        if isinstance(gates, dict) and gates:
            lines.append("- promotion gates:")
            for name, gate in gates.items():
                if not isinstance(gate, dict):
                    continue
                lines.append(
                    f"  - {name}: {gate.get('status') or 'unknown'} "
                    f"(evidence: {gate.get('evidence', '?')})"
                )
        source_mix = gate_assertions.get("source_mix") if isinstance(gate_assertions, dict) else {}
        if isinstance(source_mix, dict) and source_mix:
            lines.append("- source_mix:")
            for key, value in source_mix.items():
                lines.append(f"  - {key}: {value}")
        return "\n".join(lines)


@dataclass(frozen=True)
class TelegramMemoryGauntletResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark Telegram memory gauntlet"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- status: {summary.get('status') or 'unknown'}")
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- matched: {summary.get('matched_case_count', 0)}")
            lines.append(f"- mismatched: {summary.get('mismatched_case_count', 0)}")
            lines.append(f"- selected_user_id: {summary.get('selected_user_id') or 'unknown'}")
            movement_delta = summary.get("movement_delta_counts")
            if isinstance(movement_delta, dict) and movement_delta:
                rendered = ", ".join(f"{key}:{value}" for key, value in sorted(movement_delta.items()))
                lines.append(f"- movement_delta: {rendered}")
        mismatches = self.payload.get("mismatches") if isinstance(self.payload, dict) else []
        if mismatches:
            lines.append("- mismatches:")
            for mismatch in mismatches[:8]:
                if not isinstance(mismatch, dict):
                    continue
                lines.append(
                    f"  - {mismatch.get('case_id') or 'unknown'}: "
                    + ", ".join(str(item) for item in mismatch.get("mismatches", []))
                )
        artifact_paths = self.payload.get("artifact_paths") if isinstance(self.payload, dict) else {}
        if isinstance(artifact_paths, dict) and artifact_paths.get("summary_json"):
            lines.append(f"- summary_json: {artifact_paths['summary_json']}")
        return "\n".join(lines)


@dataclass(frozen=True)
class TelegramMemoryAcceptancePackExportResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        artifact_paths = self.payload.get("artifact_paths") if isinstance(self.payload, dict) else {}
        lines = ["Spark Telegram memory acceptance pack export"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- pack_kind: {summary.get('pack_kind') or 'unknown'}")
        if isinstance(artifact_paths, dict):
            if artifact_paths.get("pack_json"):
                lines.append(f"- pack_json: {artifact_paths['pack_json']}")
            if artifact_paths.get("operator_markdown"):
                lines.append(f"- operator_markdown: {artifact_paths['operator_markdown']}")
        return "\n".join(lines)


def export_telegram_memory_acceptance_pack(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    write_path: str | Path | None = None,
    markdown_path: str | Path | None = None,
    cases: tuple[TelegramMemoryAcceptanceCase, ...] = DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES,
) -> TelegramMemoryAcceptancePackExportResult:
    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-acceptance-supervised"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-acceptance-pack.json"
    resolved_markdown_path = Path(markdown_path) if markdown_path else resolved_output_dir / "telegram-memory-acceptance-pack.md"

    case_payloads = [_case_to_operator_payload(index=index, case=case) for index, case in enumerate(cases, start=1)]
    payload = {
        "summary": {
            "pack_kind": "telegram_memory_acceptance_supervised",
            "case_count": len(case_payloads),
            "operator_flow": "send_each_prompt_to_spark_agi_or_tester_and_capture_the_exact_reply",
            "acceptance_gate": "compare_live_replies_against_expected_fragments_then_run_local_blocking_acceptance",
        },
        "cases": case_payloads,
        "shadow_telegram_pack": [
            {
                "message": case.message,
                "case_id": case.case_id,
                "category": case.category,
            }
            for case in cases
        ],
        "capture_template": [
            {
                "case_id": case.case_id,
                "prompt": case.message,
                "live_response": "",
                "operator_notes": "",
            }
            for case in cases
        ],
        "artifact_paths": {
            "pack_json": str(resolved_write_path),
            "operator_markdown": str(resolved_markdown_path),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    resolved_markdown_path.write_text(_render_operator_acceptance_markdown(payload), encoding="utf-8")
    return TelegramMemoryAcceptancePackExportResult(output_dir=resolved_output_dir, payload=payload)


def run_telegram_memory_acceptance(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    write_path: str | Path | None = None,
    cases: tuple[TelegramMemoryAcceptanceCase, ...] = DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES,
) -> TelegramMemoryAcceptanceResult:
    from spark_intelligence.gateway.runtime import gateway_ask_telegram

    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-acceptance"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-acceptance.json"

    selected_user_id = str(user_id or "").strip() or None
    selected_chat_id = str(chat_id or "").strip() or None
    if selected_user_id is None:
        selected_user_id, selected_chat_id = _allocate_regression_identity(chat_id=selected_chat_id)
    elif selected_chat_id is None:
        selected_chat_id = selected_user_id
    _prepare_acceptance_identity(
        state_db=state_db,
        external_user_id=selected_user_id,
        username=username or "memory-acceptance",
    )

    case_payloads: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for case in cases:
        print(f"[memory-acceptance] case:{case.case_id}", file=sys.stderr, flush=True)
        raw = gateway_ask_telegram(
            config_manager=config_manager,
            state_db=state_db,
            message=case.message,
            user_id=selected_user_id,
            username=username,
            chat_id=selected_chat_id,
            as_json=True,
        )
        case_result = _build_acceptance_case_result(case=case, payload=_parse_json_object(raw))
        case_payloads.append(case_result)
        if not case_result.get("matched_expectations", True):
            mismatches.append(case_result)

    memory_subject = f"human:telegram:{selected_user_id}"
    gate_assertions = _build_acceptance_gate_assertions(
        config_manager=config_manager,
        state_db=state_db,
        memory_subject=memory_subject,
    )
    gate_mismatches = gate_assertions.get("mismatches") if isinstance(gate_assertions, dict) else []
    gate_enforcement = gate_assertions.get("enforcement") if isinstance(gate_assertions, dict) else {}
    status = "passed" if not mismatches and not gate_mismatches else "failed"
    summary = {
        "status": status,
        "case_count": len(case_payloads),
        "matched_case_count": len(case_payloads) - len(mismatches),
        "mismatched_case_count": len(mismatches),
        "promotion_gate_status": gate_assertions.get("status"),
        "promotion_gate_mismatch_count": len(gate_mismatches or []),
        "promotion_gate_enforcement": (gate_enforcement or {}).get("mode") or "blocking_acceptance",
        "promotion_gate_blocking": bool((gate_enforcement or {}).get("blocking")),
        "selected_user_id": selected_user_id,
        "selected_chat_id": selected_chat_id,
        "human_id": memory_subject,
        "quality_lanes": _acceptance_quality_lanes(case_payloads),
    }
    payload = {
        "summary": summary,
        "cases": case_payloads,
        "mismatches": mismatches,
        "gate_assertions": gate_assertions,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramMemoryAcceptanceResult(output_dir=resolved_output_dir, payload=payload)


def run_telegram_memory_gauntlet(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    write_path: str | Path | None = None,
    origin: str = "simulation",
    cases: tuple[TelegramMemoryGauntletCase, ...] | None = DEFAULT_TELEGRAM_MEMORY_GAUNTLET_CASES,
) -> TelegramMemoryGauntletResult:
    from spark_intelligence.gateway.runtime import gateway_simulate_telegram_update

    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-gauntlet"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    update_dir = resolved_output_dir / "telegram-updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-gauntlet.json"

    selected_user_id = str(user_id or "").strip() or None
    selected_chat_id = str(chat_id or "").strip() or None
    if selected_user_id is None:
        selected_user_id, selected_chat_id = _allocate_regression_identity(chat_id=selected_chat_id)
    elif selected_chat_id is None:
        selected_chat_id = selected_user_id
    selected_username = str(username or "memory-gauntlet").strip() or "memory-gauntlet"
    _prepare_acceptance_identity(
        state_db=state_db,
        external_user_id=selected_user_id,
        username=selected_username,
    )
    selected_cases = cases or DEFAULT_TELEGRAM_MEMORY_GAUNTLET_CASES

    run_movement_before = _movement_snapshot(config_manager=config_manager)
    case_payloads: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for index, case in enumerate(selected_cases, start=1):
        print(f"[memory-gauntlet] case:{case.case_id}", file=sys.stderr, flush=True)
        update_payload = _build_gauntlet_update_payload(
            index=index,
            case=case,
            user_id=selected_user_id,
            username=selected_username,
            chat_id=selected_chat_id,
        )
        update_path = update_dir / f"{index:02d}-{case.case_id}.json"
        update_path.write_text(json.dumps(update_payload, indent=2), encoding="utf-8")
        movement_before = _movement_snapshot(config_manager=config_manager)
        raw = gateway_simulate_telegram_update(
            config_manager=config_manager,
            state_db=state_db,
            update_path=update_path,
            as_json=True,
            simulation=origin != "telegram-runtime",
        )
        movement_after = _movement_snapshot(config_manager=config_manager)
        case_result = _build_gauntlet_case_result(
            case=case,
            payload=_parse_json_object(raw),
            update_payload=update_payload,
            update_path=update_path,
            movement_before=movement_before,
            movement_after=movement_after,
        )
        case_payloads.append(case_result)
        if not case_result.get("matched_expectations", True):
            mismatches.append(case_result)

    run_movement_after = _movement_snapshot(config_manager=config_manager)
    movement_delta_counts = _movement_counts_delta(
        run_movement_before.get("movement_counts"),
        run_movement_after.get("movement_counts"),
    )
    status = "passed" if not mismatches else "failed"
    payload = {
        "summary": {
            "status": status,
            "case_count": len(case_payloads),
            "matched_case_count": len(case_payloads) - len(mismatches),
            "mismatched_case_count": len(mismatches),
            "selected_user_id": selected_user_id,
            "selected_chat_id": selected_chat_id,
            "human_id": f"human:telegram:{selected_user_id}",
            "origin": origin,
            "movement_delta_counts": movement_delta_counts,
            "dashboard_movement_authority": run_movement_after.get("authority"),
        },
        "cases": case_payloads,
        "mismatches": mismatches,
        "dashboard_movement_before": run_movement_before,
        "dashboard_movement_after": run_movement_after,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
            "telegram_updates_dir": str(update_dir),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramMemoryGauntletResult(output_dir=resolved_output_dir, payload=payload)


def _case_to_operator_payload(*, index: int, case: TelegramMemoryAcceptanceCase) -> dict[str, Any]:
    return {
        "index": index,
        "case_id": case.case_id,
        "category": case.category,
        "prompt": case.message,
        "expected_bridge_mode": case.expected_bridge_mode,
        "expected_routing_decision": case.expected_routing_decision,
        "expected_response_contains": list(case.expected_response_contains),
        "expected_response_excludes": list(case.expected_response_excludes),
    }


def _render_operator_acceptance_markdown(payload: dict[str, Any]) -> str:
    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    lines = [
        "# Telegram Memory Acceptance Pack",
        "",
        "Send each prompt to Spark AGI or Tester in order. Capture the exact reply under each case.",
        "",
        "Pass rule: every expected fragment must appear, every excluded fragment must be absent, and source/gate explanations must stay aligned with the local blocking acceptance run.",
        "",
    ]
    for item in cases:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"## {item.get('index')}. {item.get('case_id')}",
                "",
                f"Category: `{item.get('category')}`",
                "",
                "Prompt:",
                "",
                "```text",
                str(item.get("prompt") or ""),
                "```",
                "",
            ]
        )
        expected_contains = [str(value) for value in item.get("expected_response_contains") or []]
        expected_excludes = [str(value) for value in item.get("expected_response_excludes") or []]
        if item.get("expected_bridge_mode") or item.get("expected_routing_decision"):
            lines.append("Expected route:")
            if item.get("expected_bridge_mode"):
                lines.append(f"- bridge_mode: `{item.get('expected_bridge_mode')}`")
            if item.get("expected_routing_decision"):
                lines.append(f"- routing_decision: `{item.get('expected_routing_decision')}`")
            lines.append("")
        if expected_contains:
            lines.append("Expected response fragments:")
            lines.extend(f"- `{fragment}`" for fragment in expected_contains)
            lines.append("")
        if expected_excludes:
            lines.append("Forbidden response fragments:")
            lines.extend(f"- `{fragment}`" for fragment in expected_excludes)
            lines.append("")
        lines.extend(["Live response:", "", "```text", "", "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def _prepare_acceptance_identity(*, state_db: StateDB, external_user_id: str, username: str) -> None:
    approve_pairing(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
        display_name=username,
    )
    rename_agent_identity(
        state_db=state_db,
        human_id=f"human:telegram:{external_user_id}",
        new_name="Atlas",
        source_surface="memory_acceptance",
        source_ref="memory-acceptance-setup",
    )
    consume_pairing_welcome(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
    )


def _build_acceptance_case_result(*, case: TelegramMemoryAcceptanceCase, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    bridge_mode = str(detail.get("bridge_mode") or payload.get("bridge_mode") or "").strip()
    routing_decision = str(detail.get("routing_decision") or payload.get("routing_decision") or "").strip()
    response_text = str(detail.get("response_text") or payload.get("response_text") or "").strip()
    mismatches: list[str] = []
    if str(result.get("decision") or payload.get("decision") or "").strip() != "allowed":
        mismatches.append(f"decision:{str(result.get('decision') or payload.get('decision') or 'missing').strip()}")
    if case.expected_bridge_mode and bridge_mode != case.expected_bridge_mode:
        mismatches.append(f"bridge_mode:{bridge_mode or 'missing'}")
    if case.expected_routing_decision and routing_decision != case.expected_routing_decision:
        mismatches.append(f"routing_decision:{routing_decision or 'missing'}")
    lowered_response = response_text.lower()
    for expected_fragment in case.expected_response_contains:
        if expected_fragment.lower() not in lowered_response:
            mismatches.append(f"response_missing:{expected_fragment}")
    for forbidden_fragment in case.expected_response_excludes:
        if forbidden_fragment.lower() in lowered_response:
            mismatches.append(f"response_forbidden:{forbidden_fragment}")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "message": case.message,
        "expected_response_contains": list(case.expected_response_contains),
        "expected_response_excludes": list(case.expected_response_excludes),
        "decision": str(result.get("decision") or payload.get("decision") or "").strip(),
        "bridge_mode": bridge_mode,
        "routing_decision": routing_decision,
        "response_text": response_text,
        "trace_ref": str(detail.get("trace_ref") or payload.get("trace_ref") or "").strip(),
        "matched_expectations": not mismatches,
        "mismatches": mismatches,
        "gateway_payload": payload,
    }


def _build_gauntlet_update_payload(
    *,
    index: int,
    case: TelegramMemoryGauntletCase,
    user_id: str,
    username: str,
    chat_id: str,
) -> dict[str, Any]:
    return {
        "update_id": 910000 + index,
        "message": {
            "message_id": 920000 + index,
            "chat": {
                "id": chat_id,
                "type": "private",
            },
            "from": {
                "id": user_id,
                "username": username,
            },
            "text": case.message,
        },
    }


def _build_gauntlet_case_result(
    *,
    case: TelegramMemoryGauntletCase,
    payload: dict[str, Any],
    update_payload: dict[str, Any],
    update_path: Path,
    movement_before: dict[str, Any],
    movement_after: dict[str, Any],
) -> dict[str, Any]:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    bridge_mode = str(detail.get("bridge_mode") or payload.get("bridge_mode") or "").strip()
    routing_decision = str(detail.get("routing_decision") or payload.get("routing_decision") or "").strip()
    response_text = str(detail.get("response_text") or payload.get("response_text") or "").strip()
    movement_delta = _movement_counts_delta(movement_before.get("movement_counts"), movement_after.get("movement_counts"))
    mismatches: list[str] = []
    if str(payload.get("decision") or "").strip() != "allowed":
        mismatches.append(f"decision:{str(payload.get('decision') or 'missing').strip()}")
    if case.expected_bridge_mode and bridge_mode != case.expected_bridge_mode:
        mismatches.append(f"bridge_mode:{bridge_mode or 'missing'}")
    if case.expected_routing_decision and routing_decision != case.expected_routing_decision:
        mismatches.append(f"routing_decision:{routing_decision or 'missing'}")
    lowered_response = response_text.lower()
    for expected_fragment in case.expected_response_contains:
        if expected_fragment.lower() not in lowered_response:
            mismatches.append(f"response_missing:{expected_fragment}")
    for forbidden_fragment in case.expected_response_excludes:
        if forbidden_fragment.lower() in lowered_response:
            mismatches.append(f"response_forbidden:{forbidden_fragment}")
    for movement_state in case.expected_movement_states:
        if int(movement_delta.get(movement_state, 0) or 0) < 1:
            mismatches.append(f"movement_missing:{movement_state}")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "message": case.message,
        "telegram_update_path": str(update_path),
        "telegram_update_payload": update_payload,
        "expected_response_contains": list(case.expected_response_contains),
        "expected_response_excludes": list(case.expected_response_excludes),
        "expected_movement_states": list(case.expected_movement_states),
        "decision": str(payload.get("decision") or "").strip(),
        "bridge_mode": bridge_mode,
        "routing_decision": routing_decision,
        "response_text": response_text,
        "trace_ref": str(detail.get("trace_ref") or payload.get("trace_ref") or "").strip(),
        "movement_delta_counts": movement_delta,
        "matched_expectations": not mismatches,
        "mismatches": mismatches,
        "gateway_payload": _compact_gauntlet_gateway_payload(payload),
    }


def _compact_gauntlet_gateway_payload(payload: dict[str, Any]) -> dict[str, Any]:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    compact_detail_keys = (
        "request_id",
        "simulation",
        "origin_surface",
        "telegram_user_id",
        "chat_id",
        "session_id",
        "human_id",
        "agent_id",
        "message_text",
        "response_text",
        "trace_ref",
        "bridge_mode",
        "routing_decision",
        "active_chip_key",
        "active_chip_task_type",
        "active_chip_evaluate_used",
    )
    return {
        "ok": bool(payload.get("ok")),
        "decision": str(payload.get("decision") or "").strip(),
        "detail": {key: detail.get(key) for key in compact_detail_keys if key in detail},
    }


def _movement_snapshot(*, config_manager: ConfigManager) -> dict[str, Any]:
    snapshot = export_memory_dashboard_movement_in_memory(
        config_manager=config_manager,
        sdk_module="domain_chip_memory",
    )
    return snapshot if isinstance(snapshot, dict) else {"status": "unavailable", "movement_counts": {}}


def _movement_counts_delta(before: Any, after: Any) -> dict[str, int]:
    before_counts = before if isinstance(before, dict) else {}
    after_counts = after if isinstance(after, dict) else {}
    movement_states = sorted({str(key) for key in before_counts} | {str(key) for key in after_counts})
    delta: dict[str, int] = {}
    for movement_state in movement_states:
        try:
            before_value = int(before_counts.get(movement_state) or 0)
        except (TypeError, ValueError):
            before_value = 0
        try:
            after_value = int(after_counts.get(movement_state) or 0)
        except (TypeError, ValueError):
            after_value = 0
        if after_value != before_value:
            delta[movement_state] = after_value - before_value
    return delta


def _build_acceptance_gate_assertions(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    memory_subject: str,
) -> dict[str, Any]:
    result = hybrid_memory_retrieve(
        config_manager=config_manager,
        state_db=state_db,
        query="What should we focus on next?",
        subject=memory_subject,
        predicate="profile.current_focus",
        limit=5,
        actor_id="memory_acceptance",
        source_surface="telegram_memory_acceptance_gate_probe",
        record_activity=False,
    )
    packet = result.context_packet.to_payload() if result.context_packet is not None else {}
    trace = packet.get("trace") if isinstance(packet, dict) else {}
    promotion_gates = trace.get("promotion_gates") if isinstance(trace, dict) else {}
    gates = promotion_gates.get("gates") if isinstance(promotion_gates.get("gates"), dict) else {}
    mismatches: list[str] = []
    if promotion_gates.get("status") != "pass":
        mismatches.append(f"promotion_gate_status:{promotion_gates.get('status') or 'missing'}")
    for gate_name in (
        "source_swamp_resistance",
        "stale_current_conflict",
        "recent_conversation_noise",
        "source_mix_stability",
    ):
        gate = gates.get(gate_name) if isinstance(gates, dict) else None
        status = gate.get("status") if isinstance(gate, dict) else None
        if status != "pass":
            mismatches.append(f"{gate_name}:{status or 'missing'}")
    source_mix = packet.get("source_mix") if isinstance(packet, dict) else {}
    try:
        current_state_count = int((source_mix or {}).get("current_state") or 0)
    except (TypeError, ValueError):
        current_state_count = 0
    if current_state_count < 1:
        mismatches.append("source_mix:current_state_missing")
    enforcement = {
        "mode": "blocking_acceptance",
        "accepted_statuses": ["pass"],
        "blocking": bool(mismatches),
        "blockers": list(mismatches),
    }
    return {
        "status": promotion_gates.get("status") or "missing",
        "mismatches": mismatches,
        "enforcement": enforcement,
        "promotion_gates": promotion_gates,
        "source_mix": source_mix if isinstance(source_mix, dict) else {},
        "context_packet_sections": [
            section.get("section")
            for section in (packet.get("sections") if isinstance(packet, dict) else []) or []
            if isinstance(section, dict)
        ],
    }


def _acceptance_quality_lanes(case_payloads: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in case_payloads:
        category = str(case.get("category") or "unknown").strip() or "unknown"
        counts[category] = counts.get(category, 0) + 1
    return counts


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_output": raw}
    return parsed if isinstance(parsed, dict) else {"raw_output": raw}
