from __future__ import annotations

from spark_intelligence.memory.approval_inbox import (
    build_memory_approval_inbox,
    record_memory_approval_decision,
)
from spark_intelligence.observability.store import record_event, repair_missing_event_trace_refs
from spark_intelligence.self_awareness.agent_events import (
    AgentEvent,
    AgentSourceRef,
    build_agent_black_box_report,
    record_agent_event,
)

from tests.test_support import SparkTestCase


class MemoryApprovalInboxTests(SparkTestCase):
    def test_inbox_lists_agent_memory_candidate_until_decided(self) -> None:
        candidate_event_id = record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="memory_candidate_created",
                summary="Candidate memory proposed for review.",
                memory_candidate={
                    "text": "Preferred mission updates are concise and outcome-focused.",
                    "memory_role": "preference",
                    "target_scope": "personal_preference",
                    "reason": "explicit_user_preference",
                    "source_refs": [
                        {
                            "source": "recent_chat",
                            "role": "candidate_evidence",
                            "freshness": "fresh",
                            "source_ref": "turn-1",
                        }
                    ],
                },
                sources=[
                    AgentSourceRef(
                        source="recent_chat",
                        role="candidate_evidence",
                        freshness="fresh",
                        source_ref="turn-1",
                    )
                ],
            ),
            request_id="req-memory-candidate",
        )

        pending = build_memory_approval_inbox(self.state_db)

        self.assertEqual(pending.counts["pending"], 1)
        self.assertEqual(pending.items[0].candidate_event_id, candidate_event_id)
        self.assertEqual(pending.items[0].status, "pending_review")
        self.assertEqual(pending.items[0].recommended_action, "save_as_personal_preference")
        self.assertIn("edit", pending.items[0].review_actions)
        self.assertEqual(pending.items[0].source_refs[0]["source"], "recent_chat")

        record_memory_approval_decision(
            self.state_db,
            candidate_event_id=candidate_event_id,
            decision="approve",
            reason="Cem approved it.",
            request_id="req-memory-candidate",
        )

        after_decision = build_memory_approval_inbox(self.state_db)
        self.assertEqual(after_decision.items, [])
        all_items = build_memory_approval_inbox(self.state_db, status="all")
        self.assertEqual(all_items.items[0].status, "decided")
        self.assertEqual(all_items.items[0].decision["decision"], "approve")

        black_box = build_agent_black_box_report(self.state_db, request_id="req-memory-candidate").to_payload()
        self.assertTrue(any(entry["event_type"] == "user_override_received" for entry in black_box["entries"]))
        self.assertTrue(
            any(entry["route_chosen"] == "memory_approval_inbox" for entry in black_box["entries"])
        )

    def test_inbox_accepts_existing_assessed_candidates_but_not_drops(self) -> None:
        assessed_event_id = record_event(
            self.state_db,
            event_type="memory_candidate_assessed",
            component="researcher_bridge",
            summary="Memory candidate assessed as belief candidate.",
            request_id="req-assessed",
            facts={
                "outcome": "belief_candidate",
                "memory_role": "belief",
                "belief_text": "AOC should be a shared read-model, not a new brain.",
                "reason": "architecture_doctrine",
            },
            provenance={
                "source_refs": [
                    {"source": "recent_chat", "role": "candidate_evidence", "freshness": "fresh"}
                ]
            },
        )
        dropped_event_id = record_event(
            self.state_db,
            event_type="memory_candidate_assessed",
            component="researcher_bridge",
            summary="Memory candidate dropped.",
            request_id="req-dropped",
            facts={
                "outcome": "drop",
                "memory_role": "unknown",
                "evidence_text": "Random transient phrasing.",
                "reason": "low_salience",
            },
        )

        inbox = build_memory_approval_inbox(self.state_db)

        self.assertEqual(len(inbox.items), 1)
        self.assertEqual(inbox.items[0].target_scope, "spark_doctrine")
        self.assertEqual(inbox.items[0].recommended_action, "save_as_spark_doctrine")
        self.assertIn("shared read-model", inbox.items[0].proposed_text)
        with self.state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, trace_ref
                FROM builder_events
                WHERE event_id IN (?, ?)
                ORDER BY event_id
                """,
                (assessed_event_id, dropped_event_id),
            ).fetchall()
        trace_refs = {row["event_id"]: row["trace_ref"] for row in rows}
        self.assertEqual(trace_refs[assessed_event_id], "trace:req-assessed")
        self.assertEqual(trace_refs[dropped_event_id], "trace:req-dropped")

    def test_policy_gate_block_with_request_gets_trace_ref(self) -> None:
        event_id = record_event(
            self.state_db,
            event_type="policy_gate_blocked",
            component="researcher_bridge",
            summary="Policy blocked a memory candidate.",
            request_id="req-policy-block",
            status="blocked",
            severity="low",
            reason_code="memory_candidate_blocked",
            facts={
                "policy_domain": "researcher_bridge",
                "gate_name": "memory_candidate_gate",
                "source_kind": "memory_candidate",
                "action": "save_memory_candidate",
            },
        )

        with self.state_db.connect() as conn:
            builder_row = conn.execute(
                "SELECT trace_ref FROM builder_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            event_log_row = conn.execute(
                "SELECT trace_ref FROM event_log WHERE event_id = ?",
                (event_id,),
            ).fetchone()

        self.assertEqual(builder_row["trace_ref"], "trace:req-policy-block")
        self.assertEqual(event_log_row["trace_ref"], "trace:req-policy-block")

    def test_follow_on_promotion_block_uses_derived_trace_ref(self) -> None:
        event_id = record_event(
            self.state_db,
            event_type="memory_candidate_assessed",
            component="researcher_bridge",
            summary="Ephemeral memory candidate assessed.",
            request_id="req-promotion-block",
            facts={
                "keepability": "ephemeral_context",
                "promotion_disposition": "promote_personal_preference",
                "memory_role": "preference",
                "message_text": "Temporary phrasing from the current turn.",
            },
            provenance={"source_kind": "recent_chat", "source_ref": "turn-promotion-block"},
        )

        with self.state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, trace_ref
                FROM builder_events
                WHERE event_id = ?
                   OR facts_json LIKE ?
                ORDER BY event_type
                """,
                (event_id, f'%"source_event_id": "{event_id}"%'),
            ).fetchall()

        trace_refs = {row["event_type"]: row["trace_ref"] for row in rows}
        self.assertEqual(trace_refs["memory_candidate_assessed"], "trace:req-promotion-block")
        self.assertEqual(trace_refs["policy_gate_blocked"], "trace:req-promotion-block")

    def test_repair_missing_event_trace_refs_backfills_request_scoped_rows(self) -> None:
        event_id = record_event(
            self.state_db,
            event_type="memory_candidate_assessed",
            component="researcher_bridge",
            summary="Memory candidate assessed before trace derivation repair.",
            request_id="req-repair-trace",
            facts={
                "outcome": "belief_candidate",
                "memory_role": "belief",
                "belief_text": "Trace repairs should stay source-owned.",
                "keepability": "supporting_memory",
                "promotion_disposition": "promote_spark_doctrine",
            },
        )
        with self.state_db.connect() as conn:
            conn.execute("UPDATE builder_events SET trace_ref = NULL WHERE event_id = ?", (event_id,))
            conn.execute("UPDATE event_log SET trace_ref = NULL WHERE event_id = ?", (event_id,))
            conn.execute("UPDATE memory_lane_records SET trace_ref = NULL WHERE event_id = ?", (event_id,))
            conn.commit()

        repaired = repair_missing_event_trace_refs(self.state_db)

        self.assertEqual(repaired, 1)
        with self.state_db.connect() as conn:
            builder_row = conn.execute("SELECT trace_ref FROM builder_events WHERE event_id = ?", (event_id,)).fetchone()
            event_log_row = conn.execute("SELECT trace_ref FROM event_log WHERE event_id = ?", (event_id,)).fetchone()
            lane_row = conn.execute("SELECT trace_ref FROM memory_lane_records WHERE event_id = ?", (event_id,)).fetchone()

        self.assertEqual(builder_row["trace_ref"], "trace:req-repair-trace")
        self.assertEqual(event_log_row["trace_ref"], "trace:req-repair-trace")
        self.assertEqual(lane_row["trace_ref"], "trace:req-repair-trace")

    def test_inbox_keeps_plain_memory_write_logs_out_unless_approval_gated(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Automatic memory write requested.",
            facts={
                "memory_role": "current_state",
                "observations": [{"predicate": "profile.preference", "value": "quiet updates"}],
            },
        )
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Approval-gated memory write requested.",
            facts={
                "requires_approval": True,
                "approval_state": "needs_review",
                "memory_role": "current_state",
                "observations": [{"predicate": "profile.preference", "value": "concise mission updates"}],
            },
        )

        inbox = build_memory_approval_inbox(self.state_db)

        self.assertEqual(len(inbox.items), 1)
        self.assertEqual(inbox.items[0].proposed_text, "concise mission updates")
        self.assertIn("raw logs stay out", inbox.source_policy)
