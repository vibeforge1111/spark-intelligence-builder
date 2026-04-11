from __future__ import annotations

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
                "architecture has to preserve the latest correct state instead of stale facts."
            ),
            focus_areas=("overwrite", "recency", "temporal_conflict"),
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
                "separate current truth from earlier evidence-backed state."
            ),
            focus_areas=("temporal_conflict", "overwrite", "staleness", "lineage_proxy"),
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
                    "mission_write",
                    "mission_query",
                    "hack_actor_write",
                    "hack_actor_query",
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
                ),
                _variant(
                    "identity_summary",
                    case_id="identity_summary_after_recency_pressure",
                    category="identity_synthesis",
                    expected_response_contains=("entrepreneur", "Spark Swarm"),
                    benchmark_tags=("identity_audit", "overwrite"),
                ),
            ),
        ),
    )
