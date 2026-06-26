from __future__ import annotations

import json
import unittest

from spark_intelligence.harness_registry.service import (
    HarnessContract,
    HarnessRegistrySnapshot,
    HarnessSelectionDecision,
)


def _make_contract(
    *,
    harness_id: str = "builder.direct",
    available: bool = True,
    degraded: bool = False,
) -> HarnessContract:
    return HarnessContract(
        harness_id=harness_id,
        label=f"{harness_id} label",
        owner_system="Spark",
        route_modes=["builder_local"],
        backend_kind="builder_runtime",
        session_scope="current_conversation",
        prompt_strategy="ps",
        toolsets=["routing"],
        required_capabilities=["delivery"],
        artifacts=["visible_reply"],
        retry_policy="retry_locally",
        approval_mode="operator_governed",
        limitations=["limit"],
        available=available,
        degraded=degraded,
    )


class HarnessRegistryPayloadShapeTests(unittest.TestCase):
    def test_contract_to_dict_round_trip_preserves_fields(self) -> None:
        contract = _make_contract()
        payload = contract.to_dict()
        self.assertEqual(payload["harness_id"], "builder.direct")
        self.assertEqual(payload["available"], True)
        self.assertEqual(payload["degraded"], False)
        self.assertEqual(payload["toolsets"], ["routing"])

    def test_snapshot_summary_counts_contracts(self) -> None:
        snap = HarnessRegistrySnapshot(
            generated_at="2024-01-01T00:00:00+00:00",
            workspace_id="ws-1",
            contracts=[
                _make_contract(harness_id="builder.direct", available=True),
                _make_contract(harness_id="researcher.advisory", available=True, degraded=True),
                _make_contract(harness_id="browser.grounded", available=False),
            ],
            recipes=[],
        )
        payload = snap.to_payload()
        summary = payload["summary"]
        self.assertEqual(summary["contract_count"], 3)
        self.assertEqual(summary["available_contract_count"], 2)
        self.assertEqual(summary["degraded_contract_count"], 1)
        self.assertEqual(
            summary["available_harnesses"],
            ["builder.direct", "researcher.advisory"],
        )

    def test_snapshot_summary_filters_recipes_by_available_flag(self) -> None:
        snap = HarnessRegistrySnapshot(
            generated_at="2024-01-01T00:00:00+00:00",
            workspace_id="ws-1",
            contracts=[],
            recipes=[
                {"recipe_id": "r1", "available": True},
                {"recipe_id": "r2", "available": False},
                {"recipe_id": "r3", "available": True},
            ],
        )
        payload = snap.to_payload()
        self.assertEqual(payload["summary"]["recipe_count"], 3)
        self.assertEqual(payload["summary"]["available_recipes"], ["r1", "r3"])

    def test_snapshot_to_json_round_trips_via_json_loads(self) -> None:
        snap = HarnessRegistrySnapshot(
            generated_at="2024-01-01T00:00:00+00:00",
            workspace_id="ws-1",
            contracts=[_make_contract()],
            recipes=[{"recipe_id": "r1", "available": True}],
        )
        rehydrated = json.loads(snap.to_json())
        self.assertEqual(rehydrated["workspace_id"], "ws-1")
        self.assertEqual(len(rehydrated["contracts"]), 1)
        self.assertEqual(rehydrated["summary"]["contract_count"], 1)

    def test_selection_decision_to_payload_preserves_all_fields(self) -> None:
        decision = HarnessSelectionDecision(
            task="What harness?",
            harness_id="researcher.advisory",
            label="Researcher Advisory Harness",
            owner_system="Spark Researcher",
            backend_kind="provider_bridge",
            session_scope="current_conversation",
            prompt_strategy="ephemeral",
            toolsets=["provider_advisory"],
            required_capabilities=["reasoning"],
            artifacts=["reply_text"],
            route_mode="researcher_advisory",
            reason="Default advisory.",
            next_actions=["Reply directly."],
            limitations=["limit"],
        )
        payload = decision.to_payload()
        # Spot-check each field round-trips.
        self.assertEqual(payload["task"], "What harness?")
        self.assertEqual(payload["harness_id"], "researcher.advisory")
        self.assertEqual(payload["route_mode"], "researcher_advisory")
        self.assertEqual(payload["next_actions"], ["Reply directly."])

    def test_selection_decision_to_json_round_trips(self) -> None:
        decision = HarnessSelectionDecision(
            task="What harness?",
            harness_id="builder.direct",
            label="Builder Direct Harness",
            owner_system="Spark Intelligence Builder",
            backend_kind="builder_runtime",
            session_scope="current_conversation",
            prompt_strategy="ps",
            toolsets=[],
            required_capabilities=[],
            artifacts=[],
            route_mode="builder_local",
            reason="Default.",
            next_actions=[],
            limitations=[],
        )
        rehydrated = json.loads(decision.to_json())
        self.assertEqual(rehydrated["harness_id"], "builder.direct")
        self.assertEqual(rehydrated["route_mode"], "builder_local")


if __name__ == "__main__":
    unittest.main()
