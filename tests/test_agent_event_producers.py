from __future__ import annotations

import json

from spark_intelligence.self_awareness.agent_events import build_agent_black_box_report
from spark_intelligence.self_awareness.event_producers import (
    record_mission_state_agent_event,
    record_route_selection_agent_event,
    record_source_used_agent_event,
    record_user_override_agent_event,
)
from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel
from spark_intelligence.self_awareness.route_probe import record_route_probe_evidence
from spark_intelligence.self_awareness.source_hierarchy import SourceClaim
from spark_intelligence.self_awareness.stale_context_sweeper import build_stale_context_sweep
from spark_intelligence.memory.approval_inbox import record_memory_approval_decision
from spark_intelligence.memory.constitution import validate_memory_proof_card_export

from tests.test_support import SparkTestCase


class AgentEventProducerTests(SparkTestCase):
    def test_route_probe_emits_capability_probed_agent_event_end_to_end(self) -> None:
        result = record_route_probe_evidence(
            self.state_db,
            capability_key="spark_memory",
            status="success",
            route_latency_ms=42,
            eval_ref="pytest:memory-smoke",
            source_ref="test:memory-smoke",
            request_id="req-probe",
            actor_id="operator:test",
            probe_summary="memory smoke write=succeeded read_records=1 cleanup=ok",
        )

        black_box = build_agent_black_box_report(self.state_db, request_id="req-probe").to_payload()
        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-probe",
            user_message="check memory route",
        ).to_payload()

        self.assertTrue(result.agent_event_id)
        self.assertEqual(black_box["counts"]["entries"], 1)
        self.assertEqual(black_box["entries"][0]["event_type"], "capability_probed")
        self.assertEqual(black_box["entries"][0]["route_chosen"], "spark_memory")
        self.assertEqual(panel["black_box"]["counts"]["entries"], 1)
        section_ids = {section["section_id"] for section in panel["sections"]["sections"]}
        self.assertIn("black_box_recorder", section_ids)

    def test_route_selection_mission_state_and_override_emit_black_box_events(self) -> None:
        record_route_selection_agent_event(
            self.state_db,
            selected_route="writable_spawner_codex_mission",
            user_intent="edit",
            confidence="high",
            reason="Needs file edits and current runner is read-only.",
            request_id="req-flow",
            actor_id="operator:test",
        )
        record_mission_state_agent_event(
            self.state_db,
            mission_id="mission-123",
            from_state="queued",
            to_state="running",
            summary="Writable mission started.",
            request_id="req-flow",
            trace_ref="trace:req-flow",
            actor_id="operator:test",
        )
        record_user_override_agent_event(
            self.state_db,
            override_summary="Stay in chat; do not open Mission Control.",
            corrected_route="answer_in_chat",
            request_id="req-flow",
            actor_id="operator:test",
        )

        report = build_agent_black_box_report(self.state_db, request_id="req-flow").to_payload()
        event_types = {entry["event_type"] for entry in report["entries"]}

        self.assertIn("route_selected", event_types)
        self.assertIn("mission_changed_state", event_types)
        self.assertIn("user_override_received", event_types)
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT trace_ref FROM builder_events WHERE event_type = 'mission_changed_state' AND request_id = ?",
                ("req-flow",),
            ).fetchone()
        self.assertEqual(row["trace_ref"], "trace:req-flow")

    def test_source_used_event_records_fresh_source_refs(self) -> None:
        record_source_used_agent_event(
            self.state_db,
            source="current_diagnostics",
            role="health_truth",
            freshness="live_probed",
            source_ref="diagnostics:scan-1",
            summary="Builder healthy, Browser unavailable.",
            user_intent="diagnose",
            selected_route="answer_in_chat",
            confidence="high",
            request_id="req-source-used",
            actor_id="operator:test",
        )

        report = build_agent_black_box_report(self.state_db, request_id="req-source-used").to_payload()
        entry = report["entries"][0]

        self.assertEqual(entry["event_type"], "source_used")
        self.assertEqual(entry["perceived_intent"], "diagnose")
        self.assertEqual(entry["sources_used"][0]["source"], "current_diagnostics")
        self.assertEqual(entry["sources_used"][0]["freshness"], "live_probed")
        self.assertEqual(entry["changed"], ["source_ledger_updated"])

    def test_memory_preflight_source_used_event_feeds_memory_lane_without_payload(self) -> None:
        record_source_used_agent_event(
            self.state_db,
            source="memory_preflight",
            role="memory_boundary",
            freshness="live_probed",
            source_ref="telegram:preflight",
            summary="Memory preflight completed.",
            user_intent="run",
            selected_route="writable_spawner_codex_mission",
            confidence="high",
            request_id="req-memory-preflight",
            trace_ref="trace:req-memory-preflight",
            actor_id="operator:test",
        )

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    request_id,
                    trace_ref,
                    artifact_lane,
                    keepability,
                    promotion_disposition,
                    status,
                    evidence_json
                FROM memory_lane_records
                WHERE request_id = ?
                """,
                ("req-memory-preflight",),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["trace_ref"], "trace:req-memory-preflight")
        self.assertEqual(row["artifact_lane"], "working_scratchpad")
        self.assertEqual(row["keepability"], "ephemeral_context")
        self.assertEqual(row["promotion_disposition"], "not_promotable")
        self.assertEqual(row["status"], "blocked")
        evidence = json.loads(row["evidence_json"])
        self.assertFalse(evidence["trace_contract"]["has_message_text"])
        self.assertIsNone(evidence["trace_contract"]["message_text"])
        proof_card = evidence["facts"]["memory_proof_card"]
        self.assertEqual(proof_card["schema_version"], "spark.memory_proof_card.v1")
        self.assertEqual(proof_card["owner_system"], "spark-intelligence-builder")
        self.assertEqual(proof_card["durability_tier"], "ephemeral_context")
        self.assertEqual(proof_card["decision"], "support_only")
        self.assertEqual(validate_memory_proof_card_export(proof_card), [])
        self.assertNotIn("memory_body", row["evidence_json"])
        self.assertNotIn("chat_id", row["evidence_json"])
        self.assertNotIn("provider_output", row["evidence_json"])

    def test_stale_context_sweep_emits_contradiction_found_agent_event(self) -> None:
        report = build_stale_context_sweep(
            live_claims=[
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 4",
                    source="current_diagnostics",
                    freshness="live_probed",
                    source_ref="diag:now",
                )
            ],
            context_claims=[
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 1",
                    source="approved_memory",
                    freshness="stale",
                    source_ref="memory:old",
                )
            ],
            state_db=self.state_db,
            record_contradictions=True,
            request_id="req-stale",
            actor_id="operator:test",
        )

        black_box = build_agent_black_box_report(self.state_db, request_id="req-stale").to_payload()

        self.assertEqual(report.to_payload()["counts"]["recorded_contradictions"], 1)
        self.assertEqual(report.to_payload()["counts"]["recorded_agent_events"], 1)
        self.assertEqual(black_box["entries"][0]["event_type"], "contradiction_found")
        self.assertEqual(black_box["entries"][0]["route_chosen"], "source_hierarchy_review")

    def test_memory_approval_decision_emits_user_override_agent_event(self) -> None:
        record_memory_approval_decision(
            self.state_db,
            candidate_event_id="candidate-1",
            decision="reject",
            reason="Not durable enough.",
            request_id="req-memory-decision",
            actor_id="operator:test",
        )

        report = build_agent_black_box_report(self.state_db, request_id="req-memory-decision").to_payload()

        self.assertEqual(report["entries"][0]["event_type"], "user_override_received")
        self.assertEqual(report["entries"][0]["route_chosen"], "memory_approval_inbox")
