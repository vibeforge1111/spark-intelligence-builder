from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.cli import build_parser
from spark_intelligence.self_awareness.capsule import _build_capability_evidence

from tests.test_support import SparkTestCase


class BuilderReliabilityAdoptionTests(SparkTestCase):
    def test_auth_login_rejects_callback_url_with_listener_mode(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "auth",
                    "login",
                    "openai",
                    "--callback-url",
                    "http://127.0.0.1:1455/auth/callback?code=test",
                    "--listen",
                ]
            )

    def test_capability_evidence_surfaces_recent_event_sensor_failure(self) -> None:
        with patch(
            "spark_intelligence.self_awareness.capsule.latest_events_by_type",
            side_effect=OSError("private database path"),
        ):
            evidence = _build_capability_evidence(self.state_db)

        sensor = next(item for item in evidence if item.capability_key == "self_awareness_event_sensor")
        self.assertEqual(sensor.source, "observability.store.latest_events_by_type")
        self.assertIn("tool_result_received", sensor.last_failure_reason or "")
        self.assertIn("dispatch_failed", sensor.last_failure_reason or "")
        self.assertNotIn("private database path", sensor.last_failure_reason or "")
        self.assertFalse(sensor.can_claim_confidently)
