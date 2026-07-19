import json
from unittest.mock import patch

from spark_intelligence.harness_evolution import (
    build_harness_self_evolution_snapshot,
    review_harness_change_manifests,
)
from spark_intelligence.observability.store import latest_events_by_type, record_event
from tests.test_support import SparkTestCase


class HarnessSelfEvolutionTests(SparkTestCase):
    def _persist_ledger(self) -> str:
        return record_event(
            self.state_db,
            event_type="tool_call_ledger_recorded",
            component="telegram_runtime",
            summary="Governed memory write prepared.",
            facts={
                "ledger_id": "ledger:self-evolution",
                "tool_name": "memory.write",
                "surface": "telegram",
                "status": "authorized",
                "tool_call_ledger": {
                    "schema_version": "tool-call-ledger-v1",
                    "ledger_id": "ledger:self-evolution",
                    "turn_id": "turn:self-evolution",
                    "tool_name": "memory.write",
                    "result": {"status": "not_started", "summary": "Prepared only."},
                },
            },
        )

    def _write_manifest(self):
        from spark_harness_core import HarnessKernel, evidence_ref

        kernel = HarnessKernel(surface="builder")
        component = kernel.component(
            component_id="component:builder-observer",
            component_type="middleware",
            owner_repo="spark-intelligence-builder",
            path="src/spark_intelligence/observer.py",
            summary="Observe Builder evidence.",
            tests=["python -m unittest malicious.module"],
        )
        manifest = kernel.change_manifest(
            target_component=component,
            failure_evidence=[
                evidence_ref("policy", "test:self-evolution", "Observation needs a bounded review lane.", confidence=1.0)
            ],
            root_cause_hypothesis="No bounded manifest review existed.",
            edit_summary="Review the manifest without running it.",
            predicted_fixes=["Operators can inspect a proposal."],
            predicted_regression_risks=["A test command could execute untrusted code."],
            required_tests=["python -m unittest malicious.module"],
            rollback_plan="Discard the proposal packet.",
            verdict="accepted",
        )
        path = self.home / "change-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_observe_snapshot_uses_canonical_events_and_never_claims_live_proof(self) -> None:
        self._persist_ledger()

        payload = build_harness_self_evolution_snapshot(self.state_db)

        self.assertEqual(payload["mode"], "observe")
        self.assertEqual(payload["ledger_count"], 1)
        self.assertFalse(payload["commands_executed"])
        self.assertFalse(payload["authority"]["telegram_live_proven"])
        self.assertEqual(payload["readiness_score"]["overall"]["status"], "blocked")
        self.assertEqual(payload["self_evolution_run"]["promotion_decision"]["verdict"], "not_ready")
        self.assertIn("state.db:builder_events", json.dumps(payload["experience_index"]))
        self.assertNotIn("state.db:tool_call_ledger", json.dumps(payload["experience_index"]))

    def test_observe_snapshot_event_is_idempotent_for_same_evidence(self) -> None:
        self._persist_ledger()

        first = build_harness_self_evolution_snapshot(self.state_db)
        second = build_harness_self_evolution_snapshot(self.state_db)

        self.assertEqual(first["evidence_digest"], second["evidence_digest"])
        self.assertEqual(first["event_id"], second["event_id"])
        events = latest_events_by_type(self.state_db, event_type="harness_self_evolution_observed", limit=10)
        self.assertEqual(len(events), 1)

    def test_manifest_review_never_executes_commands_or_emits_promotion(self) -> None:
        self._persist_ledger()
        manifest = self._write_manifest()

        with patch("subprocess.run") as subprocess_run:
            payload = review_harness_change_manifests(self.state_db, manifest_paths=[str(manifest)])

        subprocess_run.assert_not_called()
        self.assertEqual(payload["mode"], "propose")
        self.assertFalse(payload["commands_executed"])
        self.assertEqual(payload["requested_commands"], ["python -m unittest malicious.module"])
        self.assertEqual(payload["self_evolution_run"]["promotion_decision"]["verdict"], "not_ready")
        self.assertIn("human_approval_missing", payload["release_blockers"])
        self.assertIn("sandbox_execution_missing", payload["release_blockers"])
        event = latest_events_by_type(self.state_db, event_type="harness_change_manifest_reviewed", limit=1)[0]
        self.assertNotIn("malicious.module", json.dumps(event["facts_json"]))

    def test_cli_exposes_review_not_command_runner(self) -> None:
        self._persist_ledger()
        manifest = self._write_manifest()

        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "change-manifest-review",
            "--home",
            str(self.home),
            "--manifest",
            str(manifest),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["mode"], "propose")
        self.assertFalse(payload["commands_executed"])
        self.assertEqual(payload["self_evolution_run"]["promotion_decision"]["verdict"], "not_ready")
