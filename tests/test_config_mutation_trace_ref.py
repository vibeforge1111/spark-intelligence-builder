from __future__ import annotations

from spark_intelligence.observability.store import latest_events_by_type, record_config_mutation

from tests.test_support import SparkTestCase


class ConfigMutationTraceRefTests(SparkTestCase):
    def test_config_mutation_events_emit_trace_ref(self) -> None:
        mutation_id = record_config_mutation(
            self.state_db,
            target_document="spark.config",
            target_path="spark.llm.provider",
            actor_id="hunt-test",
            actor_type="operator",
            reason_code="hunt_verify",
            request_source="unit_test",
            before_payload={"provider": "openai"},
            after_payload={"provider": "openai", "model": "deepseek-chat"},
            status="applied",
            rollback_payload={"provider": "openai"},
        )

        requested = [
            event
            for event in latest_events_by_type(self.state_db, event_type="config_mutation_requested", limit=5)
            if event.get("actor_id") == "hunt-test"
        ]
        applied = [
            event
            for event in latest_events_by_type(self.state_db, event_type="config_mutation_applied", limit=5)
            if event.get("actor_id") == "hunt-test"
        ]

        self.assertEqual(len(requested), 1)
        self.assertEqual(len(applied), 1)
        expected_request_id = f"config_mutation:{mutation_id}"
        self.assertEqual(requested[0]["request_id"], expected_request_id)
        self.assertEqual(applied[0]["request_id"], expected_request_id)
        self.assertEqual(requested[0]["trace_ref"], f"trace:{expected_request_id}")
        self.assertEqual(applied[0]["trace_ref"], f"trace:{expected_request_id}")
