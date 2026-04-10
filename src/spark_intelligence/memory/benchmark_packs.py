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
            pack_id="boundary_abstention",
            title="Boundary Abstention",
            description=(
                "Checks whether the system abstains cleanly when a fact has not been written, "
                "instead of hallucinating a profile answer."
            ),
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
                    isolate_memory=True,
                ),
                _variant(
                    "country_query",
                    case_id="country_query_missing_cleanroom",
                    category="abstention",
                    expected_response_contains=("don't currently have that saved",),
                    isolate_memory=True,
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
    )
