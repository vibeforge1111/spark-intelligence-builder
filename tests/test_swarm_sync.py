from __future__ import annotations

import json

from spark_intelligence.swarm_bridge.sync import (
    SwarmSyncResult,
    _normalize_runtime_source,
    _record_swarm_failure_state,
    evaluate_swarm_escalation,
)

from tests.test_support import SparkTestCase


class SwarmSyncTests(SparkTestCase):
    def test_normalize_runtime_source_injects_required_swarm_fields(self) -> None:
        payload = {
            "agentId": "agent:spark-researcher",
            "emittedAt": "2026-03-27T10:00:00+00:00",
            "runtimeSource": {
                "kind": "spark_researcher",
                "version": "0.1.0",
                "loopKind": "generalist",
                "chipKey": None,
                "chipLabel": None,
            },
        }

        changed = _normalize_runtime_source(payload)

        self.assertTrue(changed)
        self.assertEqual(payload["runtimeSource"]["sourceInstanceId"], "agent:spark-researcher")
        self.assertEqual(
            payload["runtimeSource"]["sourceRunId"],
            "spark-researcher:2026-03-27T10:00:00+00:00",
        )

    def test_normalize_runtime_source_preserves_existing_values(self) -> None:
        payload = {
            "agentId": "agent:spark-researcher",
            "emittedAt": "2026-03-27T10:00:00+00:00",
            "runtimeSource": {
                "kind": "spark_researcher",
                "version": "0.1.0",
                "loopKind": "generalist",
                "sourceInstanceId": "agent:custom",
                "sourceRunId": "spark-researcher:custom-run",
            },
        }

        changed = _normalize_runtime_source(payload)

        self.assertFalse(changed)
        self.assertEqual(payload["runtimeSource"]["sourceInstanceId"], "agent:custom")
        self.assertEqual(payload["runtimeSource"]["sourceRunId"], "spark-researcher:custom-run")

    def test_evaluate_swarm_escalation_respects_disabled_auto_recommend_policy(self) -> None:
        self.config_manager.set_path("spark.swarm.routing.auto_recommend_enabled", False)

        result = evaluate_swarm_escalation(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Please delegate this as parallel multi-agent work and research deeply.",
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.escalate)
        self.assertEqual(result.mode, "hold_local")
        self.assertIn("explicit_swarm", result.triggers)
        self.assertIn("parallel_work", result.triggers)

    def test_evaluate_swarm_escalation_respects_custom_long_task_threshold(self) -> None:
        self.config_manager.set_path("spark.swarm.routing.long_task_word_count", 3)

        result = evaluate_swarm_escalation(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="one two three",
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.escalate)
        self.assertEqual(result.mode, "manual_recommended")
        self.assertIn("long_task", result.triggers)

    def test_record_swarm_failure_state_persists_http_error_response_body(self) -> None:
        _record_swarm_failure_state(
            self.state_db,
            kind="sync",
            result=SwarmSyncResult(
                ok=False,
                mode="http_error",
                message="Swarm API rejected the sync with HTTP 401.",
                payload_path="payload.json",
                api_url="https://sparkswarm.ai",
                workspace_id="ws_123",
                accepted=False,
                response_body={"error": "authentication_required"},
            ),
        )

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = 'swarm:last_failure' LIMIT 1"
            ).fetchone()

        self.assertIsNotNone(row)
        payload = json.loads(str(row["value"]))
        self.assertEqual(payload["mode"], "http_error")
        self.assertEqual(payload["response_body"]["error"], "authentication_required")
