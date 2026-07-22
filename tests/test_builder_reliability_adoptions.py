from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.cli import build_parser
from spark_intelligence.self_awareness.capsule import _build_capability_evidence
from spark_intelligence.self_awareness.handoff_check import build_handoff_freshness_check

from tests.test_support import SparkTestCase


class BuilderReliabilityAdoptionTests(SparkTestCase):
    def test_handoff_freshness_blocks_when_git_enumeration_is_unavailable(self) -> None:
        with patch(
            "spark_intelligence.self_awareness.handoff_check.subprocess.run",
            side_effect=OSError("git unavailable"),
        ):
            result = build_handoff_freshness_check(
                config_manager=self.config_manager,
                write_report=False,
            ).payload

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["healthy"])
        self.assertFalse(result["summary"]["git_changed_paths_available"])
        self.assertIn("handoff_git_changed_paths_unavailable", result["warnings"])

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
