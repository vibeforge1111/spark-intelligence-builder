from __future__ import annotations

import json
from pathlib import Path

from spark_intelligence.capability_router import build_capability_route_decision, looks_like_capability_router_query
from spark_intelligence.self_awareness import build_capability_proposal_packet

from tests.test_support import SparkTestCase


MATRIX_PATH = Path(__file__).resolve().parents[1] / "ops" / "capability-natural-language-matrix.json"


class CapabilityNaturalLanguageMatrixTests(SparkTestCase):
    def test_capability_matrix_matches_builder_router_and_proposal_packet(self) -> None:
        matrix = _load_matrix()
        self.assertEqual(matrix["matrix_id"], "spark_capability_natural_language_routes_v1")
        self.assertIn("proposal packets remain proposal_plan_only", matrix["guardrails"])
        self.assertIn("connector and workflow capabilities start behind dry-run harnesses", matrix["guardrails"])

        for case in matrix["cases"]:
            with self.subTest(case=case["id"]):
                prompt = str(case["prompt"])
                self.assertEqual(
                    looks_like_capability_router_query(prompt),
                    bool(case["expected_builder_capability_query"]),
                )

                expected_route_mode = case.get("expected_builder_route_mode")
                if expected_route_mode:
                    decision = build_capability_route_decision(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        task=prompt,
                    )
                    self.assertEqual(decision.route_mode, expected_route_mode)
                    self.assertEqual(decision.target_system, case["expected_builder_target_system"])

                expected_proposal_route = case.get("expected_proposal_route")
                if expected_proposal_route:
                    packet = build_capability_proposal_packet(goal=prompt, user_message=prompt).to_payload()
                    self.assertEqual(packet["schema_version"], "spark.capability_proposal.v1")
                    self.assertEqual(packet["status"], "proposal_plan_only")
                    self.assertEqual(packet["implementation_route"], expected_proposal_route)
                    connector_key = case.get("expected_connector_key")
                    if connector_key:
                        self.assertEqual(packet["connector_harness"]["schema_version"], "spark.connector_harness.v1")
                        self.assertEqual(packet["connector_harness"]["connector_key"], connector_key)
                        self.assertEqual(packet["connector_harness"]["authority_stage"], "proposal_only")
                    else:
                        self.assertNotIn("connector_harness", packet)


def _load_matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
