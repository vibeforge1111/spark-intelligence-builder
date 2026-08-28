from __future__ import annotations

import unittest

from spark_intelligence.harness_registry.service import _build_harness_contract


def _build(*, status: str | None, system_records: dict | None = None):
    if system_records is None:
        records = {}
        if status is not None:
            records["target_owner"] = {"status": status}
    else:
        records = system_records
    return _build_harness_contract(
        harness_id="test.harness",
        label="Test Harness",
        owner_system="Test Owner System",
        owner_key="target_owner",
        route_modes=["test_mode"],
        backend_kind="test_backend",
        session_scope="session_scope",
        prompt_strategy="strategy",
        toolsets=["a", "b"],
        required_capabilities=["cap1"],
        artifacts=["artifact1"],
        retry_policy="retry",
        approval_mode="operator_governed",
        limitations=["limit1"],
        system_records=records,
    )


class BuildHarnessContractTests(unittest.TestCase):
    def test_contract_available_when_system_record_present_and_status_ready(self) -> None:
        contract = _build(status="ready")
        self.assertTrue(contract.available)
        self.assertFalse(contract.degraded)

    def test_contract_available_when_status_is_empty_string(self) -> None:
        # Empty status is NOT "missing", so contract is still available
        contract = _build(status="")
        self.assertTrue(contract.available)
        self.assertFalse(contract.degraded)

    def test_contract_unavailable_when_system_record_missing(self) -> None:
        contract = _build(status=None, system_records={})
        self.assertFalse(contract.available)
        self.assertFalse(contract.degraded)

    def test_contract_unavailable_when_status_missing_string(self) -> None:
        contract = _build(status="missing")
        self.assertFalse(contract.available)

    def test_contract_degraded_when_status_is_degraded(self) -> None:
        contract = _build(status="degraded")
        self.assertTrue(contract.available)
        self.assertTrue(contract.degraded)

    def test_contract_degraded_status_is_case_insensitive(self) -> None:
        contract = _build(status="DEGRADED")
        self.assertTrue(contract.degraded)

    def test_contract_to_dict_round_trip_exposes_all_fields(self) -> None:
        contract = _build(status="ready")
        payload = contract.to_dict()
        for field in (
            "harness_id",
            "label",
            "owner_system",
            "route_modes",
            "backend_kind",
            "session_scope",
            "prompt_strategy",
            "toolsets",
            "required_capabilities",
            "artifacts",
            "retry_policy",
            "approval_mode",
            "limitations",
            "available",
            "degraded",
        ):
            self.assertIn(field, payload, f"missing field: {field}")
        self.assertEqual(payload["harness_id"], "test.harness")
        self.assertEqual(payload["toolsets"], ["a", "b"])
        self.assertTrue(payload["available"])


if __name__ == "__main__":
    unittest.main()
