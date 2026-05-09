from __future__ import annotations

from spark_intelligence.self_awareness import (
    build_capability_proposal_packet,
    capability_is_active,
    load_capability_ledger,
    record_capability_proposal,
)

from tests.test_support import SparkTestCase


class CapabilityUpgradeCompatibilityTests(SparkTestCase):
    def test_legacy_v1_packet_without_connector_harness_still_records_as_proposal(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Build this for you, Spark: read my emails and summarize my inbox.",
            user_message="Build this for you, Spark: read my emails and summarize my inbox.",
        ).to_payload()
        packet.pop("connector_harness", None)

        result = record_capability_proposal(
            config_manager=self.config_manager,
            proposal_packet=packet,
            actor_id="upgrade:test",
            source_ref="compat:legacy-v1-without-harness",
        )

        self.assertEqual(result.payload["status"], "proposed")
        self.assertNotIn("connector_harness", result.payload["proposal_packet"])
        self.assertFalse(capability_is_active(result.payload))

    def test_future_packet_and_harness_fields_are_preserved_but_not_trusted_as_activation(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Create a capability for Spark to read my calendar.",
            user_message="Create a capability for Spark to read my calendar.",
        ).to_payload()
        packet["future_packet_field"] = {"upgrade": "kept"}
        packet["connector_harness"]["future_harness_field"] = {
            "upgrade": "kept",
            "claim": "do not treat this as activation",
        }

        result = record_capability_proposal(
            config_manager=self.config_manager,
            proposal_packet=packet,
            actor_id="upgrade:test",
            source_ref="compat:future-fields",
        )

        entry = result.payload
        self.assertEqual(entry["status"], "proposed")
        self.assertEqual(entry["proposal_packet"]["future_packet_field"]["upgrade"], "kept")
        self.assertEqual(entry["proposal_packet"]["connector_harness"]["future_harness_field"]["upgrade"], "kept")
        self.assertEqual(entry["activation_evidence"], [])
        self.assertFalse(capability_is_active(entry))

        saved = load_capability_ledger(self.config_manager).payload["entries"][packet["capability_ledger_key"]]
        self.assertEqual(saved["proposal_packet"]["connector_harness"]["authority_stage"], "proposal_only")
        self.assertEqual(saved["truth_boundary"], "proposal_packet_is_not_activation_proof")

