from __future__ import annotations

from spark_intelligence.researcher_bridge.advisory import (
    _is_conversational_fallback_candidate,
    _profile_fact_record_value,
    _record_is_suppressed_by_state_deletion,
)


def _under_supported() -> dict[str, object]:
    return {"epistemic_status": {"status": "under_supported"}}


def test_state_deletion_compares_iso_timestamps_by_instant() -> None:
    scope = [("human:1", "entity.city", "", "2026-01-01T01:30:00+01:00")]

    newer_record = {
        "subject": "human:1",
        "predicate": "entity.city",
        "lifecycle": {"created_at": "2026-01-01T00:45:00Z"},
    }
    older_record = {
        "subject": "human:1",
        "predicate": "entity.city",
        "lifecycle": {"created_at": "2026-01-01T00:15:00Z"},
    }

    assert not _record_is_suppressed_by_state_deletion(record=newer_record, deleted_scopes=scope)
    assert _record_is_suppressed_by_state_deletion(record=older_record, deleted_scopes=scope)


def test_state_deletion_does_not_suppress_when_ordering_is_unprovable() -> None:
    record = {
        "subject": "human:1",
        "predicate": "entity.city",
        "lifecycle": {"created_at": "2026-01-01T00:15:00Z"},
    }
    scope = [("human:1", "entity.city", "", "not-a-timestamp")]

    assert not _record_is_suppressed_by_state_deletion(record=record, deleted_scopes=scope)


def test_profile_fact_value_preserves_authoritative_falsy_values() -> None:
    assert _profile_fact_record_value({"value": 0, "normalized_value": "stale"}) == "0"
    assert _profile_fact_record_value({"value": False, "normalized_value": "stale"}) == "False"
    assert _profile_fact_record_value({"value": "", "normalized_value": "stale"}) == ""
    assert _profile_fact_record_value({"value": None, "normalized_value": "current"}) == "current"


def test_conversational_fallback_allows_mixed_text_and_digits_but_not_numeric_only() -> None:
    assert _is_conversational_fallback_candidate(
        user_message="I have 2 kids",
        advisory=_under_supported(),
        fallback_max_chars=120,
    )
    assert not _is_conversational_fallback_candidate(
        user_message="12345",
        advisory=_under_supported(),
        fallback_max_chars=120,
    )
    assert not _is_conversational_fallback_candidate(
        user_message="error code 500",
        advisory=_under_supported(),
        fallback_max_chars=120,
    )
