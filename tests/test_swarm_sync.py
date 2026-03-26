from __future__ import annotations

from spark_intelligence.swarm_bridge.sync import _normalize_runtime_source

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
