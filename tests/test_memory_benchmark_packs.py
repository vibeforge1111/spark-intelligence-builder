import pytest

from spark_intelligence.memory.benchmark_packs import (
    default_telegram_memory_benchmark_packs,
    flatten_benchmark_pack_cases,
    select_telegram_memory_benchmark_packs,
)


def test_default_benchmark_packs_include_live_pressure_expansions() -> None:
    packs = {pack.pack_id: pack for pack in default_telegram_memory_benchmark_packs()}

    assert "loaded_context_abstention" in packs
    assert "temporal_conflict_gauntlet" in packs
    assert "event_calendar_lineage_proxy" in packs
    assert "telegram_event_overwrite_lineage" in packs
    assert "telegram_generic_profile_lifecycle" in packs
    assert "explanation_pressure_suite" in packs
    assert "identity_under_recency_pressure" in packs

    assert "abstention" in packs["loaded_context_abstention"].focus_areas
    assert "temporal_conflict" in packs["temporal_conflict_gauntlet"].focus_areas
    assert "native_history" in packs["temporal_conflict_gauntlet"].focus_areas
    assert "native_history" in packs["contradiction_and_recency"].focus_areas
    assert "event_ordering_proxy" in packs["event_calendar_lineage_proxy"].focus_areas
    assert "native_history" in packs["event_calendar_lineage_proxy"].focus_areas
    assert "telegram_events" in packs["telegram_event_overwrite_lineage"].focus_areas
    assert "current_state" in packs["telegram_event_overwrite_lineage"].focus_areas
    assert "delete" in packs["telegram_generic_profile_lifecycle"].focus_areas
    assert "native_history" in packs["telegram_generic_profile_lifecycle"].focus_areas
    assert "provenance" in packs["explanation_pressure_suite"].focus_areas
    assert packs["long_horizon_recall"].selection_role == "health_gate"
    assert packs["boundary_abstention"].selection_role == "health_gate"
    assert packs["anti_personalization_guardrails"].selection_role == "health_gate"
    assert packs["identity_synthesis"].selection_role == "health_gate"
    assert packs["loaded_context_abstention"].selection_role == "health_gate"
    assert "identity_synthesis" in packs["identity_under_recency_pressure"].focus_areas
    assert packs["identity_under_recency_pressure"].selection_role == "health_gate"

    loaded_context_case_ids = {case.case_id for case in packs["loaded_context_abstention"].cases}
    assert "favorite_color_missing_after_loaded_context" in loaded_context_case_ids
    assert "dog_name_missing_after_loaded_context" in loaded_context_case_ids
    assert "favorite_food_missing_after_loaded_context" in loaded_context_case_ids

    temporal_case_ids = {case.case_id for case in packs["temporal_conflict_gauntlet"].cases}
    assert "occupation_query_after_temporal_conflict_noise" in temporal_case_ids
    assert "timezone_query_after_temporal_conflict_noise" in temporal_case_ids
    assert "city_history_query_after_overwrite" in temporal_case_ids
    assert "country_history_query_after_overwrite" in temporal_case_ids
    assert "city_event_history_query_after_overwrite" in temporal_case_ids

    contradiction_case_ids = {case.case_id for case in packs["contradiction_and_recency"].cases}
    assert "city_history_query_after_overwrite" in contradiction_case_ids
    assert "country_history_query_after_overwrite" in contradiction_case_ids
    assert "city_event_history_query_after_overwrite" in contradiction_case_ids

    event_proxy_case_ids = {case.case_id for case in packs["event_calendar_lineage_proxy"].cases}
    assert "mission_query_after_event_lineage_noise" in event_proxy_case_ids
    assert "timezone_query_after_event_lineage_noise" in event_proxy_case_ids
    assert "occupation_query_after_event_lineage_noise" in event_proxy_case_ids
    assert "identity_summary_after_event_lineage_proxy" in event_proxy_case_ids
    assert "city_history_query_after_overwrite" in event_proxy_case_ids
    assert "country_history_query_after_overwrite" in event_proxy_case_ids
    assert "city_event_history_query_after_overwrite" in event_proxy_case_ids

    telegram_event_case_ids = {case.case_id for case in packs["telegram_event_overwrite_lineage"].cases}
    assert "meeting_write" in telegram_event_case_ids
    assert "event_query_after_meeting_write" in telegram_event_case_ids
    assert "flight_write" in telegram_event_case_ids
    assert "flight_overwrite" in telegram_event_case_ids
    assert "latest_flight_query_after_overwrite" in telegram_event_case_ids
    assert "flight_history_query_after_overwrite" in telegram_event_case_ids

    generic_profile_case_ids = {case.case_id for case in packs["telegram_generic_profile_lifecycle"].cases}
    assert "generic_cofounder_write" in generic_profile_case_ids
    assert "generic_cofounder_history_query_after_overwrite" in generic_profile_case_ids
    assert "generic_cofounder_delete" in generic_profile_case_ids
    assert "generic_cofounder_event_history_query_after_delete" in generic_profile_case_ids
    assert "generic_decision_write" in generic_profile_case_ids
    assert "generic_decision_history_query_after_overwrite" in generic_profile_case_ids
    assert "generic_decision_delete" in generic_profile_case_ids
    assert "generic_decision_event_history_query_after_delete" in generic_profile_case_ids
    assert "generic_blocker_write" in generic_profile_case_ids
    assert "generic_blocker_history_query_after_overwrite" in generic_profile_case_ids
    assert "generic_blocker_delete" in generic_profile_case_ids
    assert "generic_blocker_event_history_query_after_delete" in generic_profile_case_ids
    assert "generic_status_write" in generic_profile_case_ids
    assert "generic_status_history_query_after_overwrite" in generic_profile_case_ids
    assert "generic_status_delete" in generic_profile_case_ids
    assert "generic_status_event_history_query_after_delete" in generic_profile_case_ids

    identity_case_ids = {case.case_id for case in packs["identity_under_recency_pressure"].cases}
    assert "name_query_after_recency_pressure" in identity_case_ids
    assert "occupation_query_after_recency_pressure" in identity_case_ids
    assert "timezone_query_after_recency_pressure" in identity_case_ids
    assert "founder_query_after_recency_pressure" in identity_case_ids
    assert "mission_query_after_recency_pressure" in identity_case_ids
    assert "identity_summary_after_recency_pressure_rich" in identity_case_ids
    assert "identity_summary_after_recency_pressure_with_latest_state" in identity_case_ids


def test_select_benchmark_packs_and_flatten_cases_preserves_custom_variants() -> None:
    packs = select_telegram_memory_benchmark_packs(["identity_under_recency_pressure"])

    assert [pack.pack_id for pack in packs] == ["identity_under_recency_pressure"]

    case_ids = {case.case_id for case in flatten_benchmark_pack_cases(packs)}
    assert "identity_summary_after_recency_pressure_rich" in case_ids
    assert "identity_summary_after_recency_pressure_with_latest_state" in case_ids


def test_select_event_calendar_proxy_pack_exposes_lineage_cases() -> None:
    packs = select_telegram_memory_benchmark_packs(["event_calendar_lineage_proxy"])

    assert [pack.pack_id for pack in packs] == ["event_calendar_lineage_proxy"]

    case_ids = {case.case_id for case in flatten_benchmark_pack_cases(packs)}
    assert "startup_query_after_founder" in case_ids
    assert "mission_query_after_event_lineage_noise" in case_ids
    assert "identity_summary_after_event_lineage_proxy" in case_ids
    assert "city_history_query_after_overwrite" in case_ids
    assert "city_event_history_query_after_overwrite" in case_ids


def test_select_telegram_event_overwrite_pack_exposes_latest_and_history_cases() -> None:
    packs = select_telegram_memory_benchmark_packs(["telegram_event_overwrite_lineage"])

    assert [pack.pack_id for pack in packs] == ["telegram_event_overwrite_lineage"]

    case_ids = {case.case_id for case in flatten_benchmark_pack_cases(packs)}
    assert "meeting_write" in case_ids
    assert "event_query_after_meeting_write" in case_ids
    assert "latest_flight_query_after_overwrite" in case_ids
    assert "flight_history_query_after_overwrite" in case_ids


def test_select_generic_profile_lifecycle_pack_exposes_overwrite_and_delete_cases() -> None:
    packs = select_telegram_memory_benchmark_packs(["telegram_generic_profile_lifecycle"])

    assert [pack.pack_id for pack in packs] == ["telegram_generic_profile_lifecycle"]

    case_ids = {case.case_id for case in flatten_benchmark_pack_cases(packs)}
    assert "generic_cofounder_overwrite" in case_ids
    assert "generic_cofounder_history_query_after_overwrite" in case_ids
    assert "generic_cofounder_delete" in case_ids
    assert "generic_cofounder_current_query_after_delete" in case_ids
    assert "generic_decision_overwrite" in case_ids
    assert "generic_decision_history_query_after_overwrite" in case_ids
    assert "generic_decision_delete" in case_ids
    assert "generic_decision_current_query_after_delete" in case_ids
    assert "generic_blocker_overwrite" in case_ids
    assert "generic_blocker_history_query_after_overwrite" in case_ids
    assert "generic_blocker_delete" in case_ids
    assert "generic_blocker_current_query_after_delete" in case_ids
    assert "generic_status_overwrite" in case_ids
    assert "generic_status_history_query_after_overwrite" in case_ids
    assert "generic_status_delete" in case_ids
    assert "generic_status_current_query_after_delete" in case_ids


def test_select_benchmark_packs_rejects_unknown_pack_ids() -> None:
    with pytest.raises(ValueError, match="unknown_benchmark_packs"):
        select_telegram_memory_benchmark_packs(["does_not_exist"])
