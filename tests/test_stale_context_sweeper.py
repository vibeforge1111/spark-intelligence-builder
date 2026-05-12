from __future__ import annotations

from spark_intelligence.observability.store import recent_contradictions
from spark_intelligence.self_awareness.source_hierarchy import SourceClaim
from spark_intelligence.self_awareness.stale_context_sweeper import build_stale_context_sweep

from tests.test_support import SparkTestCase


class StaleContextSweeperTests(SparkTestCase):
    def test_sweeper_marks_old_memory_stale_against_live_access(self) -> None:
        report = build_stale_context_sweep(
            live_claims=[
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 4",
                    source="current_diagnostics",
                    freshness="live_probed",
                    source_ref="diag:current",
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
        )

        payload = report.to_payload()

        self.assertEqual(report.status, "needs_review")
        self.assertEqual(payload["counts"]["stale"], 1)
        self.assertEqual(report.stale_items[0].winning_source, "current_diagnostics")
        self.assertEqual(report.stale_items[0].action_type, "mark_memory_stale")
        self.assertTrue(report.stale_items[0].review_required)
        self.assertIn("Mark lower-authority memory stale", report.stale_items[0].suggested_action)

    def test_sweeper_records_contradictions_when_requested(self) -> None:
        report = build_stale_context_sweep(
            live_claims=[
                {
                    "claim_key": "allowed_next_action",
                    "value": "answer in chat",
                    "source": "current_user_message",
                    "freshness": "fresh",
                    "source_ref": "turn:latest",
                }
            ],
            context_claims=[
                {
                    "claim_key": "allowed_next_action",
                    "value": "open Mission Control",
                    "source": "wiki_doctrine",
                    "freshness": "unknown",
                    "source_ref": "wiki:route-selection",
                }
            ],
            state_db=self.state_db,
            record_contradictions=True,
        )

        rows = recent_contradictions(self.state_db, status="open", limit=5)

        self.assertEqual(len(report.recorded_contradiction_ids), 1)
        self.assertEqual(len(report.recorded_agent_event_ids), 1)
        self.assertEqual(rows[0]["component"], "stale_context_sweeper")
        self.assertEqual(rows[0]["facts_json"]["winner"]["source"], "current_user_message")
        self.assertEqual(rows[0]["facts_json"]["memory_review_card"]["review_type"], "source_freshness")
        self.assertEqual(rows[0]["facts_json"]["memory_review_card"]["claim_key"], "allowed_next_action")
        self.assertNotIn("answer in chat", str(rows[0]["facts_json"]["memory_review_card"]))
        self.assertNotIn("open Mission Control", str(rows[0]["facts_json"]["memory_review_card"]))
        self.assertEqual(report.stale_items[0].action_type, "mark_wiki_claim_stale")
        self.assertIn("action=mark_wiki_claim_stale", report.to_text())

    def test_sweeper_is_clear_when_values_agree(self) -> None:
        report = build_stale_context_sweep(
            live_claims=[
                {"claim_key": "memory_status", "value": "healthy", "source": "current_diagnostics"},
            ],
            context_claims=[
                {"claim_key": "memory_status", "value": "healthy", "source": "approved_memory"},
            ],
        )

        self.assertEqual(report.status, "clear")
        self.assertEqual(report.to_text(), "Stale context sweep: clear.")
