from __future__ import annotations

from spark_intelligence.observability.store import recent_contradictions
from spark_intelligence.memory.constitution import validate_memory_review_card_export
from spark_intelligence.self_awareness.source_hierarchy import (
    SourceClaim,
    record_source_conflict_resolutions,
    resolve_source_claims,
    source_authority_rank,
)

from tests.test_support import SparkTestCase


class SourceHierarchyTests(SparkTestCase):
    def test_current_diagnostics_marks_old_memory_stale_for_access_claim(self) -> None:
        resolutions = resolve_source_claims(
            [
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 1",
                    source="approved_memory",
                    source_ref="memory:old-access",
                    freshness="stale",
                    summary="Old memory says chat-only access.",
                ),
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 4",
                    source="current_diagnostics",
                    source_ref="diag:now",
                    freshness="live_probed",
                    summary="Current diagnostics say local workspace is allowed.",
                ),
            ]
        )

        self.assertEqual(len(resolutions), 1)
        resolution = resolutions[0]
        self.assertEqual(resolution.winner.source, "current_diagnostics")
        self.assertEqual(resolution.stale_claims[0].source_ref, "memory:old-access")
        self.assertIn("Mark lower-authority memory stale", resolution.suggested_action)

        contradiction_ids = record_source_conflict_resolutions(self.state_db, resolutions)
        rows = recent_contradictions(self.state_db, status="open", limit=5)

        self.assertEqual(len(contradiction_ids), 1)
        self.assertEqual(rows[0]["reason_code"], "source_hierarchy_conflict")
        self.assertEqual(rows[0]["facts_json"]["winner"]["source"], "current_diagnostics")
        self.assertEqual(rows[0]["facts_json"]["stale_claims"][0]["source"], "approved_memory")
        review_card = rows[0]["facts_json"]["memory_review_card"]
        self.assertEqual(review_card["schema_version"], "spark.memory_review_card.v1")
        self.assertEqual(review_card["owner_system"], "spark-intelligence-builder")
        self.assertEqual(review_card["review_type"], "source_freshness")
        self.assertEqual(review_card["decision"], "needs_review")
        self.assertEqual(review_card["freshness"], "stale")
        self.assertEqual(review_card["winner_source"], "current_diagnostics")
        self.assertEqual(review_card["stale_source_count"], 1)
        self.assertEqual(review_card["contradicted_source_count"], 0)
        self.assertEqual(validate_memory_review_card_export(review_card), [])
        encoded_card = str(review_card)
        self.assertNotIn("Level 1", encoded_card)
        self.assertNotIn("Level 4", encoded_card)

    def test_latest_user_message_outranks_wiki_doctrine(self) -> None:
        resolutions = resolve_source_claims(
            [
                SourceClaim(
                    claim_key="allowed_next_action",
                    value="open Mission Control",
                    source="wiki_doctrine",
                    source_ref="wiki:route-selection",
                    freshness="unknown",
                ),
                SourceClaim(
                    claim_key="allowed_next_action",
                    value="answer in chat",
                    source="current_user_message",
                    source_ref="turn:latest",
                    freshness="fresh",
                ),
            ]
        )

        self.assertEqual(resolutions[0].winner.source, "current_user_message")
        self.assertEqual(resolutions[0].stale_claims[0].source, "wiki_doctrine")

    def test_equal_authority_conflict_exports_contradiction_review_card_without_values(self) -> None:
        resolutions = resolve_source_claims(
            [
                SourceClaim(
                    claim_key="preferred_memory_route",
                    value="durable save",
                    source="current_user_message",
                    source_ref="turn:1",
                    freshness="fresh",
                ),
                SourceClaim(
                    claim_key="preferred_memory_route",
                    value="do not save",
                    source="current_user_message",
                    source_ref="turn:2",
                    freshness="fresh",
                ),
            ]
        )

        contradiction_ids = record_source_conflict_resolutions(
            self.state_db,
            resolutions,
            request_id="req-review-card",
            trace_ref="trace:review-card",
        )
        rows = recent_contradictions(self.state_db, status="open", limit=5)
        review_card = rows[0]["facts_json"]["memory_review_card"]

        self.assertEqual(len(contradiction_ids), 1)
        self.assertEqual(review_card["review_type"], "contradiction")
        self.assertEqual(review_card["freshness"], "contradicted")
        self.assertEqual(review_card["contradicted_source_count"], 1)
        self.assertEqual(review_card["trace_ref"], "trace:review-card")
        self.assertEqual(validate_memory_review_card_export(review_card), [])
        self.assertNotIn("durable save", str(review_card))
        self.assertNotIn("do not save", str(review_card))

    def test_matching_claims_do_not_create_resolution(self) -> None:
        resolutions = resolve_source_claims(
            [
                SourceClaim(claim_key="memory_status", value="healthy", source="current_diagnostics"),
                SourceClaim(claim_key="memory_status", value="healthy", source="approved_memory"),
            ]
        )

        self.assertEqual(resolutions, [])
        self.assertGreater(source_authority_rank("current_diagnostics"), source_authority_rank("approved_memory"))
