from __future__ import annotations

from spark_intelligence.memory_contracts import (
    ALLOWED_MEMORY_ROLES,
    annotate_contract_trace,
    effective_memory_role,
    expected_memory_role,
    is_memory_contract_reason,
    memory_contract_reason,
    normalize_memory_role,
    persisted_memory_contract_reason,
)


def test_normalize_memory_role_maps_hybrid_alias_to_aggregate() -> None:
    assert normalize_memory_role("hybrid") == "aggregate"
    assert normalize_memory_role("HYBRID") == "aggregate"
    assert normalize_memory_role(" Hybrid ") == "aggregate"


def test_normalize_memory_role_passes_through_canonical_roles() -> None:
    for role in ALLOWED_MEMORY_ROLES:
        assert normalize_memory_role(role) == role


def test_normalize_memory_role_treats_unrecognized_as_unknown() -> None:
    assert normalize_memory_role("definitely-not-a-role") == "unknown"
    assert normalize_memory_role(None) == "unknown"
    assert normalize_memory_role("") == "unknown"


def test_normalize_memory_role_allow_unknown_preserves_blank_and_unknown_inputs() -> None:
    assert normalize_memory_role("", allow_unknown=True) == "unknown"
    assert normalize_memory_role("unknown", allow_unknown=True) == "unknown"
    # A populated-but-unrecognized role still falls back to "unknown".
    assert normalize_memory_role("garbage", allow_unknown=True) == "unknown"


def test_effective_memory_role_falls_back_to_metadata_role() -> None:
    role = effective_memory_role(
        "",
        allow_unknown=True,
        metadata={"memory_role": "current_state"},
    )
    assert role == "current_state"


def test_effective_memory_role_falls_back_through_provenance_chain() -> None:
    provenance = {
        "memory_role": "",
        "metadata": {"memory_role": "unknown"},
        "sdk_provenance": {"memory_role": "event"},
    }
    role = effective_memory_role("", allow_unknown=True, provenance=provenance)
    assert role == "event"


def test_effective_memory_role_handles_provenance_list() -> None:
    provenance = [
        {"memory_role": "unknown"},
        {"memory_role": "belief"},
    ]
    role = effective_memory_role(None, allow_unknown=True, provenance=provenance)
    assert role == "belief"


def test_expected_memory_role_resolves_operations_and_methods() -> None:
    assert expected_memory_role(operation="update") == frozenset({"current_state"})
    assert expected_memory_role(method="get_current_state") == frozenset(
        {"current_state", "state_deletion"}
    )
    assert expected_memory_role() is None
    assert expected_memory_role(operation="not-a-real-op") is None


def test_memory_contract_reason_flags_invalid_role() -> None:
    assert memory_contract_reason(memory_role="garbage", operation="update") == "invalid_memory_role"


def test_memory_contract_reason_flags_operation_mismatch() -> None:
    # belief is a known role but not allowed for "update".
    assert memory_contract_reason(memory_role="belief", operation="update") == (
        "memory_role_operation_mismatch"
    )


def test_memory_contract_reason_flags_method_mismatch() -> None:
    # event is a known role but not allowed for get_current_state reads.
    assert memory_contract_reason(memory_role="event", method="get_current_state") == (
        "memory_role_method_mismatch"
    )


def test_memory_contract_reason_returns_none_when_role_matches_expectation() -> None:
    assert memory_contract_reason(memory_role="current_state", operation="update") is None
    assert memory_contract_reason(memory_role="event", method="retrieve_events") is None


def test_memory_contract_reason_unknown_role_respects_allow_unknown_flag() -> None:
    assert memory_contract_reason(memory_role="", operation="update") == "invalid_memory_role"
    assert (
        memory_contract_reason(memory_role="", operation="update", allow_unknown=True) is None
    )


def test_annotate_contract_trace_attaches_reason_and_expected_roles() -> None:
    payload = annotate_contract_trace(
        None,
        scope="write",
        reason="memory_role_operation_mismatch",
        observed_role="belief",
        operation="update",
    )
    assert payload["contract_scope"] == "write"
    assert payload["contract_reason"] == "memory_role_operation_mismatch"
    assert payload["observed_memory_role"] == "belief"
    assert payload["operation"] == "update"
    assert payload["expected_memory_roles"] == ["current_state"]
    assert "method" not in payload


def test_annotate_contract_trace_preserves_existing_trace_keys() -> None:
    starting = {"trace_id": "trace-123"}
    payload = annotate_contract_trace(
        starting,
        scope="read",
        reason="memory_role_method_mismatch",
        observed_role="event",
        method="get_current_state",
    )
    assert payload["trace_id"] == "trace-123"
    assert payload["method"] == "get_current_state"
    assert payload["expected_memory_roles"] == ["current_state", "state_deletion"]
    # Original dict must not be mutated in place.
    assert "contract_scope" not in starting


def test_is_memory_contract_reason_only_matches_known_reasons() -> None:
    assert is_memory_contract_reason("invalid_memory_role")
    assert is_memory_contract_reason(" Memory_Role_Operation_Mismatch ")
    assert not is_memory_contract_reason("some_other_reason")
    assert not is_memory_contract_reason(None)


def test_persisted_memory_contract_reason_returns_persisted_when_observed_matches() -> None:
    result = persisted_memory_contract_reason(
        reason="memory_role_operation_mismatch",
        raw_memory_role="belief",
        effective_role="belief",
        operation="update",
    )
    assert result == "memory_role_operation_mismatch"


def test_persisted_memory_contract_reason_recomputes_when_roles_diverge() -> None:
    # Persisted reason claims a mismatch, but the effective role is actually
    # valid for the operation -- the helper must recompute and return None.
    result = persisted_memory_contract_reason(
        reason="memory_role_operation_mismatch",
        raw_memory_role="belief",
        effective_role="current_state",
        operation="update",
    )
    assert result is None


def test_persisted_memory_contract_reason_clears_invalid_when_method_has_no_expectation() -> None:
    # No expected role table for the method, allow_unknown lets blank pass.
    result = persisted_memory_contract_reason(
        reason="invalid_memory_role",
        raw_memory_role="",
        effective_role="",
        method="some_unmapped_method",
        allow_unknown=True,
    )
    assert result is None
