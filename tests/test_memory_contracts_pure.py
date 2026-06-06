"""Coverage for the pure helpers in :mod:`spark_intelligence.memory_contracts`.

These functions gate memory-role tagging across the orchestrator and the
observability store, so locking the lookup tables and the normalize/fallback
logic in tests prevents silent contract drift.
"""
from __future__ import annotations

import pytest

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


class TestNormalizeMemoryRole:
    def test_known_role_returned_lowercase(self) -> None:
        assert normalize_memory_role("CURRENT_STATE") == "current_state"

    def test_hybrid_alias_maps_to_aggregate(self) -> None:
        assert normalize_memory_role("hybrid") == "aggregate"

    def test_unknown_role_falls_back_to_unknown(self) -> None:
        assert normalize_memory_role("not_a_real_role") == "unknown"

    def test_empty_value_returns_unknown_when_allowed(self) -> None:
        assert normalize_memory_role("", allow_unknown=True) == "unknown"

    def test_empty_value_returns_unknown_by_default(self) -> None:
        assert normalize_memory_role(None) == "unknown"


class TestEffectiveMemoryRole:
    def test_known_role_returned_as_is(self) -> None:
        assert effective_memory_role("episodic") == "episodic"

    def test_metadata_fallback_used_when_role_missing(self) -> None:
        assert effective_memory_role("", metadata={"memory_role": "belief"}) == "belief"

    def test_provenance_chain_fallback(self) -> None:
        provenance = {"metadata": {"memory_role": "event"}}
        assert effective_memory_role(None, provenance=provenance) == "event"

    def test_provenance_list_fallback(self) -> None:
        provenance = [{"memory_role": "aggregate"}]
        assert effective_memory_role("", provenance=provenance) == "aggregate"

    def test_no_fallback_yields_unknown(self) -> None:
        assert effective_memory_role("", metadata={"other": "x"}) == "unknown"


class TestExpectedMemoryRole:
    def test_operation_lookup(self) -> None:
        assert expected_memory_role(operation="update") == frozenset({"current_state"})

    def test_method_lookup(self) -> None:
        assert expected_memory_role(method="retrieve_events") == frozenset({"event"})

    def test_unknown_operation_returns_none(self) -> None:
        assert expected_memory_role(operation="not_a_real_op") is None

    def test_no_arguments_returns_none(self) -> None:
        assert expected_memory_role() is None


class TestMemoryContractReason:
    def test_invalid_role_flagged(self) -> None:
        assert memory_contract_reason(memory_role="bogus") == "invalid_memory_role"

    def test_operation_mismatch_flagged(self) -> None:
        assert (
            memory_contract_reason(memory_role="episodic", operation="update")
            == "memory_role_operation_mismatch"
        )

    def test_method_mismatch_flagged(self) -> None:
        assert (
            memory_contract_reason(memory_role="belief", method="retrieve_events")
            == "memory_role_method_mismatch"
        )

    def test_matching_role_returns_none(self) -> None:
        assert (
            memory_contract_reason(memory_role="current_state", operation="update")
            is None
        )

    def test_unknown_role_with_allow_unknown_returns_none(self) -> None:
        assert memory_contract_reason(memory_role="", allow_unknown=True) is None


class TestAnnotateContractTrace:
    def test_payload_includes_scope_and_reason(self) -> None:
        payload = annotate_contract_trace(
            None,
            scope="write",
            reason="invalid_memory_role",
            observed_role="unknown",
            operation="update",
        )
        assert payload["contract_scope"] == "write"
        assert payload["contract_reason"] == "invalid_memory_role"
        assert payload["observed_memory_role"] == "unknown"
        assert payload["operation"] == "update"
        assert payload["expected_memory_roles"] == ["current_state"]

    def test_method_branch_records_method_and_expected(self) -> None:
        payload = annotate_contract_trace(
            {"existing": "field"},
            scope="read",
            reason="memory_role_method_mismatch",
            observed_role="belief",
            method="retrieve_events",
        )
        assert payload["existing"] == "field"
        assert payload["method"] == "retrieve_events"
        assert payload["expected_memory_roles"] == ["event"]

    def test_no_operation_or_method_skips_expected_field(self) -> None:
        payload = annotate_contract_trace(
            None,
            scope="read",
            reason="invalid_memory_role",
            observed_role="unknown",
        )
        assert "expected_memory_roles" not in payload
        assert "operation" not in payload
        assert "method" not in payload


class TestIsMemoryContractReason:
    @pytest.mark.parametrize(
        "value",
        ["invalid_memory_role", "memory_role_operation_mismatch", "memory_role_method_mismatch"],
    )
    def test_recognized_reasons(self, value: str) -> None:
        assert is_memory_contract_reason(value) is True

    def test_unknown_string_rejected(self) -> None:
        assert is_memory_contract_reason("other_reason") is False

    def test_none_rejected(self) -> None:
        assert is_memory_contract_reason(None) is False


class TestPersistedMemoryContractReason:
    def test_persisted_reason_passes_through_when_consistent(self) -> None:
        out = persisted_memory_contract_reason(
            reason="invalid_memory_role",
            raw_memory_role="totally_bogus",
            effective_role="totally_bogus",
        )
        assert out == "invalid_memory_role"

    def test_persisted_reason_cleared_when_operation_matches(self) -> None:
        out = persisted_memory_contract_reason(
            reason="unrelated",
            raw_memory_role="current_state",
            effective_role="current_state",
            operation="update",
        )
        assert out is None

    def test_unknown_role_allowed_when_allow_unknown(self) -> None:
        out = persisted_memory_contract_reason(
            reason="invalid_memory_role",
            raw_memory_role="",
            effective_role="",
            method="unknown_method_x",
            allow_unknown=True,
        )
        assert out is None


class TestAllowedRolesContract:
    def test_known_roles_present(self) -> None:
        for role in ("current_state", "episodic", "event", "aggregate", "belief"):
            assert role in ALLOWED_MEMORY_ROLES

    def test_hybrid_is_not_directly_allowed(self) -> None:
        # 'hybrid' is an alias, not a directly-allowed role; only aggregate is.
        assert "hybrid" not in ALLOWED_MEMORY_ROLES
        assert "aggregate" in ALLOWED_MEMORY_ROLES
