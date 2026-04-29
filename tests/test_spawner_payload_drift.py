from __future__ import annotations

import json

from spark_intelligence.gateway.guardrails import set_runtime_state_value
from spark_intelligence.mission_control import build_mission_control_snapshot
from spark_intelligence.spawner_payload_drift import detect_spawner_payload_drift

from tests.test_support import SparkTestCase


class SpawnerPayloadDriftTests(SparkTestCase):
    def setUp(self) -> None:
        super().setUp()
        repo_root = self.home / "spawner-ui"
        repo_root.mkdir(parents=True)
        (repo_root / "package.json").write_text("{}", encoding="utf-8")
        self.config_manager.set_path("spark.local_projects.include_known_spark_repos", False)
        self.config_manager.set_path("spark.local_projects.include_attachment_repos", False)
        self.config_manager.set_path("spark.local_projects.roots", [str(repo_root)])
        self.config_manager.set_path("spark.swarm.enabled", False)

    def test_detects_stale_unknown_spawner_repo_reference(self) -> None:
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="spawner:last_payload",
            value=json.dumps(
                {
                    "targetRepo": "vibeship-spark-intelligence",
                    "route": "/memory-quality",
                    "component": "memory quality dashboard",
                },
                sort_keys=True,
            ),
            component="test",
        )

        report = detect_spawner_payload_drift(self.config_manager, self.state_db).to_payload()

        self.assertEqual(report["status"], "drift_detected")
        self.assertEqual(report["drift_count"], 1)
        record = report["records"][0]
        self.assertEqual(record["source_ref"], "spawner:last_payload")
        self.assertEqual(record["reference"], "vibeship-spark-intelligence")
        self.assertEqual(record["reason_code"], "stale_spark_repo_reference")

    def test_known_spawner_repo_reference_is_clean(self) -> None:
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="spawner:last_payload",
            value=json.dumps({"targetRepo": "spawner-ui", "route": "/memory-quality"}, sort_keys=True),
            component="test",
        )

        report = detect_spawner_payload_drift(self.config_manager, self.state_db).to_payload()

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["drift_count"], 0)

    def test_mission_control_surfaces_spawner_payload_drift(self) -> None:
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="spawner:last_payload",
            value=json.dumps({"targetRepo": "vibeship-spark-intelligence"}, sort_keys=True),
            component="test",
        )

        payload = build_mission_control_snapshot(self.config_manager, self.state_db).to_payload()

        self.assertIn("Spawner payload drift", payload["summary"]["degraded_surfaces"])
        self.assertIn(
            "Confirm the target repo and refresh stale Spawner payload context before dispatching build work.",
            payload["summary"]["recommended_actions"],
        )
        self.assertEqual(payload["panels"]["spawner_payload_drift"]["status"], "drift_detected")
