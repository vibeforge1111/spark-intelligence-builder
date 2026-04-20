from __future__ import annotations

from typing import Any


ALLOWED_MEMORY_ROLES = frozenset({"current_state", "event"})
READ_METHOD_EXPECTED_MEMORY_ROLE = {
    "get_current_state": "current_state",
    "get_historical_state": "current_state",
    "retrieve_events": "event",
}
WRITE_OPERATION_EXPECTED_MEMORY_ROLE = {
    "update": "current_state",
    "delete": "current_state",
    "event": "event",
}
MEMORY_CONTRACT_REASONS = frozenset(
    {
        "invalid_memory_role",
        "memory_role_operation_mismatch",
        "memory_role_method_mismatch",
    }
)


def normalize_memory_role(value: Any, *, allow_unknown: bool = False) -> str:
    role = str(value or "").strip().lower()
    if role in ALLOWED_MEMORY_ROLES:
        return role
    if allow_unknown and role in {"", "unknown"}:
        return "unknown"
    return "unknown"


def effective_memory_role(
    value: Any,
    *,
    allow_unknown: bool = False,
    metadata: Any = None,
    provenance: Any = None,
) -> str:
    role = normalize_memory_role(value, allow_unknown=allow_unknown)
    raw_role = str(value or "").strip().lower()
    if role != "unknown" or raw_role not in {"", "unknown"}:
        return role
    for candidate in _fallback_memory_role_candidates(metadata=metadata, provenance=provenance):
        candidate_role = normalize_memory_role(candidate, allow_unknown=False)
        if candidate_role != "unknown":
            return candidate_role
    return role


def expected_memory_role(*, operation: str | None = None, method: str | None = None) -> str | None:
    if operation:
        return WRITE_OPERATION_EXPECTED_MEMORY_ROLE.get(str(operation).strip().lower())
    if method:
        return READ_METHOD_EXPECTED_MEMORY_ROLE.get(str(method).strip().lower())
    return None


def memory_contract_reason(
    *,
    memory_role: Any,
    operation: str | None = None,
    method: str | None = None,
    allow_unknown: bool = False,
) -> str | None:
    raw_role = str(memory_role or "").strip().lower()
    if raw_role and raw_role not in ALLOWED_MEMORY_ROLES and raw_role != "unknown":
        return "invalid_memory_role"
    observed = normalize_memory_role(memory_role, allow_unknown=allow_unknown)
    if observed == "unknown":
        return None if allow_unknown else "invalid_memory_role"
    expected = expected_memory_role(operation=operation, method=method)
    if expected and observed != expected:
        return "memory_role_operation_mismatch" if operation else "memory_role_method_mismatch"
    return None


def annotate_contract_trace(
    trace: dict[str, Any] | None,
    *,
    scope: str,
    reason: str,
    observed_role: str,
    operation: str | None = None,
    method: str | None = None,
) -> dict[str, Any]:
    payload = dict(trace or {})
    payload["contract_scope"] = scope
    payload["contract_reason"] = reason
    payload["observed_memory_role"] = observed_role
    expected = expected_memory_role(operation=operation, method=method)
    if expected:
        payload["expected_memory_role"] = expected
    if operation:
        payload["operation"] = operation
    if method:
        payload["method"] = method
    return payload


def is_memory_contract_reason(value: Any) -> bool:
    return str(value or "").strip().lower() in MEMORY_CONTRACT_REASONS


def _fallback_memory_role_candidates(*, metadata: Any, provenance: Any) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(metadata, dict):
        candidates.append(metadata.get("memory_role"))
    candidates.extend(_provenance_memory_role_candidates(provenance))
    return candidates


def _provenance_memory_role_candidates(provenance: Any) -> list[Any]:
    if isinstance(provenance, dict):
        candidates: list[Any] = [provenance.get("memory_role")]
        candidates.extend(_provenance_memory_role_candidates(provenance.get("metadata")))
        candidates.extend(_provenance_memory_role_candidates(provenance.get("sdk_provenance")))
        return candidates
    if isinstance(provenance, list):
        candidates: list[Any] = []
        for item in provenance:
            candidates.extend(_provenance_memory_role_candidates(item))
        return candidates
    return []
