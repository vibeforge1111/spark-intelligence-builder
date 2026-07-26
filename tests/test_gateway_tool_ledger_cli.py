from __future__ import annotations

import io
import json
from unittest.mock import patch

from spark_intelligence.bridge_authority import authorize_builder_bridge_action
from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

from tests.test_support import SparkTestCase


class GatewayToolLedgerCliTests(SparkTestCase):
    def _bound_row(self) -> dict[str, object]:
        envelope = build_vnext_tool_intent_envelope(
            surface="spawner",
            actor_id_ref="human:r30-test",
            request_id="request-r30-spawner-ledger-cli",
            source_kind="spawner_canonical_ledger_ingest_test",
            tool_name="spawner.dispatch",
            owner_system="spawner-ui",
            mutation_class="launches_mission",
            intent_summary="User explicitly requested a governed Spawner dispatch.",
            raw_turn_summary="Raw test turn remains offloaded.",
            confidence=1.0,
        )
        self.assertIsNotNone(envelope)
        verdict = authorize_builder_bridge_action(
            {"turn_intent_envelope_vnext": envelope},
            tool_name="spawner.dispatch",
            owner_system="spawner-ui",
            mutation_class="launches_mission",
            state_db=self.state_db,
            request_id="request-r30-spawner-ledger-cli",
            actor_id="spawner-ui",
            component="spawner_canonical_ledger_ingest_test",
        )
        self.assertTrue(verdict.allowed, verdict.reason_codes)
        self.assertIsNotNone(verdict.tool_call_ledger)
        ledger = verdict.tool_call_ledger
        assert ledger is not None
        authorization = ledger["authorization"]
        result = ledger["result"]
        return {
            "ledger_id": ledger["ledger_id"],
            "turn_id": ledger["turn_id"],
            "action_id": ledger["action_id"],
            "capability_id": ledger["capability_id"],
            "authorization_decision_id": authorization["decision_id"],
            "tool_name": ledger["tool_name"],
            "owner_system": "spawner-ui",
            "mutation_class": "launches_mission",
            "surface": "spawner",
            "request_id": "request-r30-spawner-ledger-cli",
            "trace_ref": "trace:r30-spawner-ledger-cli",
            "status": result["status"],
            "ledger_json": ledger,
        }

    def test_ingest_tool_ledger_reads_stdin_and_persists_validated_row(self) -> None:
        row = self._bound_row()

        with patch("sys.stdin", io.StringIO(json.dumps({"row": row}))):
            exit_code, stdout, stderr = self.run_cli(
                "gateway",
                "ingest-tool-ledger",
                "-",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(stderr, "")
        result = self.read_json(stdout)
        self.assertEqual(result["ledger_id"], row["ledger_id"])
        self.assertEqual(result["turn_id"], row["turn_id"])
        self.assertEqual(result["surface"], "spawner")

    def test_ingest_tool_ledger_rejects_malformed_stdin_without_traceback(self) -> None:
        with patch("sys.stdin", io.StringIO("{not-json")):
            exit_code, stdout, stderr = self.run_cli(
                "gateway",
                "ingest-tool-ledger",
                "-",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Tool ledger ingest input rejected:", stderr)
        self.assertNotIn("Traceback", stderr)
