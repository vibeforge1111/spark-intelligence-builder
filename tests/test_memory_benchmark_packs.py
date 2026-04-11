from spark_intelligence.memory.benchmark_packs import default_telegram_memory_benchmark_packs


def test_default_benchmark_packs_include_live_pressure_expansions() -> None:
    packs = {pack.pack_id: pack for pack in default_telegram_memory_benchmark_packs()}

    assert "loaded_context_abstention" in packs
    assert "temporal_conflict_gauntlet" in packs
    assert "explanation_pressure_suite" in packs
    assert "identity_under_recency_pressure" in packs

    assert "abstention" in packs["loaded_context_abstention"].focus_areas
    assert "temporal_conflict" in packs["temporal_conflict_gauntlet"].focus_areas
    assert "provenance" in packs["explanation_pressure_suite"].focus_areas
    assert "identity_synthesis" in packs["identity_under_recency_pressure"].focus_areas

    loaded_context_case_ids = {case.case_id for case in packs["loaded_context_abstention"].cases}
    assert "favorite_color_missing_after_loaded_context" in loaded_context_case_ids
    assert "dog_name_missing_after_loaded_context" in loaded_context_case_ids
    assert "favorite_food_missing_after_loaded_context" in loaded_context_case_ids

    temporal_case_ids = {case.case_id for case in packs["temporal_conflict_gauntlet"].cases}
    assert "occupation_query_after_temporal_conflict_noise" in temporal_case_ids
    assert "timezone_query_after_temporal_conflict_noise" in temporal_case_ids

    identity_case_ids = {case.case_id for case in packs["identity_under_recency_pressure"].cases}
    assert "name_query_after_recency_pressure" in identity_case_ids
    assert "occupation_query_after_recency_pressure" in identity_case_ids
    assert "timezone_query_after_recency_pressure" in identity_case_ids
    assert "founder_query_after_recency_pressure" in identity_case_ids
    assert "mission_query_after_recency_pressure" in identity_case_ids
    assert "identity_summary_after_recency_pressure_rich" in identity_case_ids
    assert "identity_summary_after_recency_pressure_with_latest_state" in identity_case_ids
