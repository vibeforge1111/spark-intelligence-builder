from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from spark_intelligence.memory.regression import (
    DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES,
    TelegramMemoryRegressionCase,
)


@dataclass(frozen=True)
class TelegramMemoryBenchmarkPack:
    pack_id: str
    title: str
    description: str
    focus_areas: tuple[str, ...]
    cases: tuple[TelegramMemoryRegressionCase, ...]
    selection_role: str = "separator"


_DEFAULT_CASES_BY_ID = {
    case.case_id: case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES
}


def _existing(*case_ids: str) -> tuple[TelegramMemoryRegressionCase, ...]:
    return tuple(_DEFAULT_CASES_BY_ID[case_id] for case_id in case_ids)


def _variant(
    source_case_id: str,
    *,
    case_id: str,
    category: str | None = None,
    message: str | None = None,
    expected_response_contains: tuple[str, ...] | None = None,
    expected_response_excludes: tuple[str, ...] | None = None,
    benchmark_tags: tuple[str, ...] | None = None,
    isolate_memory: bool | None = None,
) -> TelegramMemoryRegressionCase:
    base_case = _DEFAULT_CASES_BY_ID[source_case_id]
    return replace(
        base_case,
        case_id=case_id,
        category=category if category is not None else base_case.category,
        message=message if message is not None else base_case.message,
        expected_response_contains=(
            expected_response_contains
            if expected_response_contains is not None
            else base_case.expected_response_contains
        ),
        expected_response_excludes=(
            expected_response_excludes
            if expected_response_excludes is not None
            else base_case.expected_response_excludes
        ),
        benchmark_tags=(
            benchmark_tags if benchmark_tags is not None else base_case.benchmark_tags
        ),
        isolate_memory=isolate_memory if isolate_memory is not None else base_case.isolate_memory,
    )


def default_telegram_memory_benchmark_packs() -> tuple[TelegramMemoryBenchmarkPack, ...]:
    return (
        TelegramMemoryBenchmarkPack(
            pack_id="core_profile_baseline",
            title="Core Profile Baseline",
            description=(
                "Baseline end-to-end profile memory pack covering writes, direct recall, and "
                "evidence-style explanations through the Telegram runtime."
            ),
            focus_areas=("profile_recall", "direct_query", "explanation"),
            cases=_existing(
                "name_write",
                "name_query",
                "occupation_write",
                "occupation_query",
                "city_write",
                "city_explanation",
                "timezone_write",
                "timezone_query",
                "country_write",
                "country_query",
                "country_explanation",
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="long_horizon_recall",
            title="Long-Horizon Recall",
            description=(
                "Writes multiple facts, injects unrelated memory updates, then checks whether "
                "older facts are still recalled after a longer interaction horizon."
            ),
            selection_role="health_gate",
            focus_areas=("long_horizon_recall", "interleaved_noise", "profile_retention"),
            cases=(
                *_existing(
                    "timezone_write",
                    "city_write",
                    "occupation_write",
                    "startup_write",
                    "mission_write",
                    "hack_actor_write",
                    "country_write",
                    "spark_role_write",
                ),
                _variant(
                    "timezone_query",
                    case_id="timezone_query_long_horizon",
                    category="long_term_memory",
                ),
                _variant(
                    "occupation_query",
                    case_id="occupation_query_long_horizon",
                    category="long_term_memory",
                ),
                _variant(
                    "mission_query",
                    case_id="mission_query_long_horizon",
                    category="long_term_memory",
                ),
                _variant(
                    "hack_actor_query",
                    case_id="hack_actor_query_long_horizon",
                    category="long_term_memory",
                ),
                _variant(
                    "country_query",
                    case_id="country_query_long_horizon",
                    category="long_term_memory",
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="contradiction_and_recency",
            title="Contradiction And Recency",
            description=(
                "Exercises overwrite and contradiction pressure so the winning memory "
                "architecture has to preserve the latest correct state instead of stale facts. "
                "It now also checks whether overwritten state can still be retrieved through native "
                "Telegram history and event-history questions."
            ),
            focus_areas=("overwrite", "recency", "temporal_conflict", "native_history"),
            cases=(
                *_existing(
                    "startup_write",
                    "founder_write",
                    "founder_query",
                    "startup_query_after_founder",
                    "startup_explanation_after_founder",
                    "city_write",
                    "city_overwrite",
                    "city_query_after_overwrite",
                    "country_write",
                    "country_overwrite",
                    "country_query_after_overwrite",
                    "city_history_query_after_overwrite",
                    "country_history_query_after_overwrite",
                    "city_event_history_query_after_overwrite",
                ),
                _variant(
                    "city_query_after_overwrite",
                    case_id="city_query_after_more_noise",
                    category="short_term_memory",
                ),
                _variant(
                    "country_query_after_overwrite",
                    case_id="country_query_after_more_noise",
                    category="short_term_memory",
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="provenance_audit",
            title="Provenance Audit",
            description=(
                "Focuses on explanation quality and whether the answer explicitly signals that it is "
                "using stored memory rather than free-associating from unrelated facts."
            ),
            focus_areas=("explanation", "grounding", "provenance"),
            cases=(
                *_existing(
                    "occupation_write",
                    "city_write",
                    "startup_write",
                    "mission_write",
                    "country_write",
                    "city_explanation",
                    "startup_explanation",
                    "mission_explanation",
                    "country_explanation",
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_provenance_audit",
                    category="identity",
                    expected_response_contains=("entrepreneur", "Seedify"),
                    benchmark_tags=("identity_audit",),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="boundary_abstention",
            title="Boundary Abstention",
            description=(
                "Checks whether the system abstains cleanly when a fact has not been written, "
                "instead of hallucinating a profile answer."
            ),
            selection_role="health_gate",
            focus_areas=("abstention", "cleanroom", "anti_hallucination"),
            cases=(
                _variant(
                    "spark_role_abstention",
                    case_id="spark_role_abstention_cleanroom",
                    category="abstention",
                    isolate_memory=True,
                ),
                _variant(
                    "hack_actor_query_missing",
                    case_id="hack_actor_query_missing_cleanroom",
                    category="abstention",
                    isolate_memory=True,
                ),
                _variant(
                    "timezone_query",
                    case_id="timezone_query_missing_cleanroom",
                    category="abstention",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization"),
                    isolate_memory=True,
                ),
                _variant(
                    "country_query",
                    case_id="country_query_missing_cleanroom",
                    category="abstention",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization"),
                    isolate_memory=True,
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="anti_personalization_guardrails",
            title="Anti-Personalization Guardrails",
            description=(
                "Measures whether the memory system avoids using unrelated stored profile facts to "
                "answer a question that should trigger abstention."
            ),
            selection_role="health_gate",
            focus_areas=("abstention", "loaded_context", "anti_overpersonalization"),
            cases=(
                *_existing(
                    "name_write",
                    "occupation_write",
                    "city_write",
                    "startup_write",
                    "country_write",
                    "timezone_write",
                ),
                _variant(
                    "timezone_query",
                    case_id="favorite_color_missing_after_profile_writes",
                    category="inappropriate_memory_use",
                    message="What is my favorite color?",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization"),
                ),
                _variant(
                    "country_query",
                    case_id="dog_name_missing_after_profile_writes",
                    category="inappropriate_memory_use",
                    message="What is my dog's name?",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization"),
                ),
                _variant(
                    "country_query",
                    case_id="favorite_food_missing_after_profile_writes",
                    category="inappropriate_memory_use",
                    message="What food do I love the most?",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization"),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="identity_synthesis",
            title="Identity Synthesis",
            description=(
                "Measures whether many separate writes are fused into a coherent identity-level "
                "summary instead of being recalled only as isolated facts."
            ),
            selection_role="health_gate",
            focus_areas=("identity_synthesis", "multi_fact_fusion", "profile_summary"),
            cases=(
                *_existing(
                    "name_write",
                    "occupation_write",
                    "startup_write",
                    "mission_write",
                    "timezone_write",
                    "country_write",
                    "identity_summary",
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_rich",
                    category="identity_synthesis",
                    expected_response_contains=("entrepreneur", "Seedify", "Asia/Dubai", "UAE"),
                    benchmark_tags=("identity_audit",),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="interleaved_noise_resilience",
            title="Interleaved Noise Resilience",
            description=(
                "Interleaves unrelated writes and reads so the benchmark captures whether memory "
                "still surfaces the right fact under short-term distraction."
            ),
            focus_areas=("short_term_resilience", "interleaved_noise", "query_stability"),
            cases=(
                *_existing(
                    "name_write",
                    "occupation_write",
                    "country_write",
                    "mission_write",
                    "hack_actor_write",
                    "city_write",
                    "country_query",
                    "mission_query",
                    "hack_actor_query",
                ),
                _variant(
                    "name_query",
                    case_id="name_query_after_noise",
                    category="short_term_memory",
                ),
                _variant(
                    "occupation_query",
                    case_id="occupation_query_after_noise",
                    category="short_term_memory",
                ),
                _variant(
                    "city_explanation",
                    case_id="city_explanation_after_noise",
                    category="explanation",
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="quality_lane_gauntlet",
            title="Quality Lane Gauntlet",
            description=(
                "Combines abstention, overwrite, staleness, explanation, and identity pressure in "
                "one pack to expose architectural tradeoffs under mixed real-world conditions."
            ),
            focus_areas=("mixed_quality_lanes", "abstention", "overwrite", "identity"),
            cases=(
                *_existing(
                    "startup_write",
                    "startup_explanation",
                    "founder_write",
                    "startup_query_after_founder",
                    "startup_explanation_after_founder",
                    "country_write",
                    "country_overwrite",
                    "country_query_after_overwrite",
                    "spark_role_abstention",
                    "hack_actor_query_missing",
                    "occupation_write",
                    "identity_summary",
                ),
                _variant(
                    "timezone_query",
                    case_id="timezone_query_missing_gauntlet",
                    category="abstention",
                    expected_response_contains=("don't currently have that saved",),
                    isolate_memory=True,
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="loaded_context_abstention",
            title="Loaded Context Abstention",
            description=(
                "Loads the runtime with many saved profile facts, then asks several tempting but "
                "unsupported questions so abstention quality is measured under maximal personalization pressure."
            ),
            selection_role="health_gate",
            focus_areas=("abstention", "loaded_context", "anti_hallucination", "anti_overpersonalization"),
            cases=(
                *_existing(
                    "name_write",
                    "occupation_write",
                    "city_write",
                    "startup_write",
                    "mission_write",
                    "hack_actor_write",
                    "timezone_write",
                    "country_write",
                ),
                _variant(
                    "timezone_query",
                    case_id="favorite_color_missing_after_loaded_context",
                    category="inappropriate_memory_use",
                    message="What is my favorite color?",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "North Korea", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization", "loaded_context"),
                ),
                _variant(
                    "country_query",
                    case_id="dog_name_missing_after_loaded_context",
                    category="inappropriate_memory_use",
                    message="What is my dog's name?",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "North Korea", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization", "loaded_context"),
                ),
                _variant(
                    "country_query",
                    case_id="favorite_food_missing_after_loaded_context",
                    category="inappropriate_memory_use",
                    message="What food do I love the most?",
                    expected_response_contains=("don't currently have that saved",),
                    expected_response_excludes=("Sarah", "entrepreneur", "Dubai", "Seedify", "North Korea", "UAE", "Asia/Dubai"),
                    benchmark_tags=("anti_hallucination", "anti_overpersonalization", "loaded_context"),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="temporal_conflict_gauntlet",
            title="Temporal Conflict Gauntlet",
            description=(
                "Pushes repeated recency conflicts and stale-explanation pressure so the live runtime has to "
                "separate current truth from earlier evidence-backed state. It now also includes native "
                "Telegram history questions so chronology is tested directly instead of only through current-state prompts."
            ),
            focus_areas=("temporal_conflict", "overwrite", "staleness", "lineage_proxy", "native_history"),
            cases=(
                *_existing(
                    "startup_write",
                    "founder_write",
                    "startup_query_after_founder",
                    "startup_explanation_after_founder",
                    "city_write",
                    "city_overwrite",
                    "city_query_after_overwrite",
                    "country_write",
                    "country_overwrite",
                    "country_query_after_overwrite",
                    "city_history_query_after_overwrite",
                    "country_history_query_after_overwrite",
                    "city_event_history_query_after_overwrite",
                    "mission_write",
                    "mission_query",
                    "hack_actor_write",
                    "hack_actor_query",
                    "occupation_write",
                    "timezone_write",
                ),
                _variant(
                    "city_query_after_overwrite",
                    case_id="city_query_after_temporal_conflict_noise",
                    category="short_term_memory",
                    benchmark_tags=("temporal_conflict", "overwrite"),
                ),
                _variant(
                    "country_query_after_overwrite",
                    case_id="country_query_after_temporal_conflict_noise",
                    category="short_term_memory",
                    benchmark_tags=("temporal_conflict", "overwrite"),
                ),
                _variant(
                    "occupation_query",
                    case_id="occupation_query_after_temporal_conflict_noise",
                    category="short_term_memory",
                    benchmark_tags=("temporal_conflict", "lineage_proxy"),
                ),
                _variant(
                    "timezone_query",
                    case_id="timezone_query_after_temporal_conflict_noise",
                    category="short_term_memory",
                    benchmark_tags=("temporal_conflict", "lineage_proxy"),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="event_calendar_lineage_proxy",
            title="Event Calendar Lineage Proxy",
            description=(
                "A chronology-sensitive lane for event-ordering and calendar-style recall that now mixes native "
                "Telegram historical-state/event queries with the older overwrite and summary proxy prompts. "
                "This pack is the live checkpoint for whether chronology survives both direct history questions "
                "and regular profile-memory pressure."
            ),
            focus_areas=("event_ordering_proxy", "calendar_proxy", "temporal_lineage", "lineage_proxy", "native_history"),
            cases=(
                *_existing(
                    "startup_write",
                    "founder_write",
                    "startup_query_after_founder",
                    "startup_explanation_after_founder",
                    "city_write",
                    "city_overwrite",
                    "city_query_after_overwrite",
                    "country_write",
                    "country_overwrite",
                    "country_query_after_overwrite",
                    "city_history_query_after_overwrite",
                    "country_history_query_after_overwrite",
                    "city_event_history_query_after_overwrite",
                    "timezone_write",
                    "occupation_write",
                    "mission_write",
                ),
                _variant(
                    "mission_query",
                    case_id="mission_query_after_event_lineage_noise",
                    category="short_term_memory",
                    benchmark_tags=("event_calendar_proxy", "lineage_proxy"),
                ),
                _variant(
                    "timezone_query",
                    case_id="timezone_query_after_event_lineage_noise",
                    category="short_term_memory",
                    benchmark_tags=("event_calendar_proxy", "lineage_proxy"),
                ),
                _variant(
                    "occupation_query",
                    case_id="occupation_query_after_event_lineage_noise",
                    category="short_term_memory",
                    benchmark_tags=("event_calendar_proxy", "lineage_proxy"),
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_after_event_lineage_proxy",
                    category="identity_synthesis",
                    expected_response_contains=("entrepreneur", "Spark Swarm", "Canada", "Abu Dhabi"),
                    benchmark_tags=("event_calendar_proxy", "lineage_proxy", "profile_summary"),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="telegram_event_overwrite_lineage",
            title="Telegram Event Overwrite Lineage",
            description=(
                "Exercises the dedicated Telegram event lane directly: event writes, latest-event current-state "
                "answers, and event-history recall after an overwrite so chronology stays intact while latest-state "
                "answers stay current."
            ),
            focus_areas=("telegram_events", "event_overwrite", "current_state", "native_history"),
            cases=(
                *_existing(
                    "meeting_write",
                    "event_query_after_meeting_write",
                    "flight_write",
                    "flight_overwrite",
                    "latest_flight_query_after_overwrite",
                    "flight_history_query_after_overwrite",
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="telegram_generic_profile_lifecycle",
            title="Telegram Generic Profile Lifecycle",
            description=(
                "Exercises the generic Telegram profile-memory lane through update, overwrite, current recall, "
                "previous-state recall, explicit deletion, and post-delete history so mutable profile memory "
                "behaves like a governed lifecycle instead of a one-shot fact store."
            ),
            focus_areas=("generic_profile_memory", "overwrite", "delete", "native_history", "current_state"),
            cases=(
                *_existing(
                    "generic_cofounder_write",
                    "generic_cofounder_overwrite",
                    "generic_cofounder_current_query_after_overwrite",
                    "generic_cofounder_history_query_after_overwrite",
                    "generic_cofounder_delete",
                    "generic_cofounder_current_query_after_delete",
                    "generic_cofounder_history_query_after_delete",
                    "generic_cofounder_event_history_query_after_delete",
                    "generic_decision_write",
                    "generic_decision_overwrite",
                    "generic_decision_current_query_after_overwrite",
                    "generic_decision_history_query_after_overwrite",
                    "generic_decision_delete",
                    "generic_decision_current_query_after_delete",
                    "generic_decision_history_query_after_delete",
                    "generic_decision_event_history_query_after_delete",
                    "generic_blocker_write",
                    "generic_blocker_overwrite",
                    "generic_blocker_current_query_after_overwrite",
                    "generic_blocker_history_query_after_overwrite",
                    "generic_blocker_delete",
                    "generic_blocker_current_query_after_delete",
                    "generic_blocker_history_query_after_delete",
                    "generic_blocker_event_history_query_after_delete",
                    "generic_status_write",
                    "generic_status_overwrite",
                    "generic_status_current_query_after_overwrite",
                    "generic_status_history_query_after_overwrite",
                    "generic_status_delete",
                    "generic_status_current_query_after_delete",
                    "generic_status_history_query_after_delete",
                    "generic_status_event_history_query_after_delete",
                    "generic_commitment_write",
                    "generic_commitment_overwrite",
                    "generic_commitment_current_query_after_overwrite",
                    "generic_commitment_history_query_after_overwrite",
                    "generic_commitment_delete",
                    "generic_commitment_current_query_after_delete",
                    "generic_commitment_history_query_after_delete",
                    "generic_commitment_event_history_query_after_delete",
                    "generic_milestone_write",
                    "generic_milestone_overwrite",
                    "generic_milestone_current_query_after_overwrite",
                    "generic_milestone_history_query_after_overwrite",
                    "generic_milestone_delete",
                    "generic_milestone_current_query_after_delete",
                    "generic_milestone_history_query_after_delete",
                    "generic_milestone_event_history_query_after_delete",
                    "generic_risk_write",
                    "generic_risk_overwrite",
                    "generic_risk_current_query_after_overwrite",
                    "generic_risk_history_query_after_overwrite",
                    "generic_risk_delete",
                    "generic_risk_current_query_after_delete",
                    "generic_risk_history_query_after_delete",
                    "generic_risk_event_history_query_after_delete",
                    "generic_dependency_write",
                    "generic_dependency_overwrite",
                    "generic_dependency_current_query_after_overwrite",
                    "generic_dependency_history_query_after_overwrite",
                    "generic_dependency_delete",
                    "generic_dependency_current_query_after_delete",
                    "generic_dependency_history_query_after_delete",
                    "generic_dependency_event_history_query_after_delete",
                    "generic_constraint_write",
                    "generic_constraint_overwrite",
                    "generic_constraint_current_query_after_overwrite",
                    "generic_constraint_history_query_after_overwrite",
                    "generic_constraint_delete",
                    "generic_constraint_current_query_after_delete",
                    "generic_constraint_history_query_after_delete",
                    "generic_constraint_event_history_query_after_delete",
                    "generic_assumption_write",
                    "generic_assumption_overwrite",
                    "generic_assumption_current_query_after_overwrite",
                    "generic_assumption_history_query_after_overwrite",
                    "generic_assumption_delete",
                    "generic_assumption_current_query_after_delete",
                    "generic_assumption_history_query_after_delete",
                    "generic_assumption_event_history_query_after_delete",
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="explanation_pressure_suite",
            title="Explanation Pressure Suite",
            description=(
                "Stacks many writes before running explanation prompts so provenance quality is tested under "
                "heavy contextual load rather than one clean fact at a time."
            ),
            focus_areas=("explanation", "grounding", "loaded_context", "provenance"),
            cases=(
                *_existing(
                    "name_write",
                    "occupation_write",
                    "city_write",
                    "startup_write",
                    "founder_write",
                    "mission_write",
                    "hack_actor_write",
                    "country_write",
                    "city_explanation",
                    "startup_explanation",
                    "mission_explanation",
                    "country_explanation",
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_after_explanation_pressure",
                    category="identity_synthesis",
                    expected_response_contains=("entrepreneur", "Seedify"),
                    benchmark_tags=("identity_audit", "loaded_context"),
                ),
            ),
        ),
        TelegramMemoryBenchmarkPack(
            pack_id="identity_under_recency_pressure",
            title="Identity Under Recency Pressure",
            description=(
                "Combines identity synthesis with overwrite pressure so the profile summary remains coherent "
                "after newer state updates displace older facts."
            ),
            focus_areas=("identity_synthesis", "overwrite", "recency", "profile_summary"),
            selection_role="health_gate",
            cases=(
                *_existing(
                    "name_write",
                    "occupation_write",
                    "startup_write",
                    "founder_write",
                    "timezone_write",
                    "city_write",
                    "city_overwrite",
                    "country_write",
                    "country_overwrite",
                    "city_query_after_overwrite",
                    "country_query_after_overwrite",
                    "identity_summary",
                    "mission_write",
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_after_recency_pressure",
                    category="identity_synthesis",
                    expected_response_contains=("entrepreneur", "Spark Swarm"),
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
                _variant(
                    "name_query",
                    case_id="name_query_after_recency_pressure",
                    category="identity_synthesis",
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
                _variant(
                    "occupation_query",
                    case_id="occupation_query_after_recency_pressure",
                    category="identity_synthesis",
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
                _variant(
                    "timezone_query",
                    case_id="timezone_query_after_recency_pressure",
                    category="identity_synthesis",
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
                _variant(
                    "founder_query",
                    case_id="founder_query_after_recency_pressure",
                    category="identity_synthesis",
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
                _variant(
                    "mission_query",
                    case_id="mission_query_after_recency_pressure",
                    category="identity_synthesis",
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_after_recency_pressure_rich",
                    category="identity_synthesis",
                    message="Summarize my profile in one sentence.",
                    expected_response_contains=("entrepreneur", "Spark Swarm", "Asia/Dubai"),
                    benchmark_tags=("identity_audit", "overwrite", "profile_summary"),
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_after_recency_pressure_with_latest_state",
                    category="identity_synthesis",
                    message="Give me a full profile summary with my latest location too.",
                    expected_response_contains=("entrepreneur", "Spark Swarm", "Canada"),
                    benchmark_tags=("identity_audit", "overwrite", "profile_summary"),
                ),
            ),
        ),
    )


def select_telegram_memory_benchmark_packs(
    pack_ids: Sequence[str] | None = None,
) -> tuple[TelegramMemoryBenchmarkPack, ...]:
    packs = default_telegram_memory_benchmark_packs()
    if not pack_ids:
        return packs
    packs_by_id = {pack.pack_id: pack for pack in packs}
    selected: list[TelegramMemoryBenchmarkPack] = []
    missing: list[str] = []
    for raw_pack_id in pack_ids:
        pack_id = str(raw_pack_id).strip()
        if not pack_id:
            continue
        pack = packs_by_id.get(pack_id)
        if pack is None:
            missing.append(pack_id)
            continue
        selected.append(pack)
    if missing:
        available = ", ".join(sorted(packs_by_id))
        raise ValueError(
            f"unknown_benchmark_packs:{', '.join(missing)}; available={available}"
        )
    return tuple(selected)


def flatten_benchmark_pack_cases(
    packs: Sequence[TelegramMemoryBenchmarkPack],
) -> tuple[TelegramMemoryRegressionCase, ...]:
    deduped: dict[str, TelegramMemoryRegressionCase] = {}
    for pack in packs:
        for case in pack.cases:
            deduped.setdefault(case.case_id, case)
    return tuple(deduped.values())
