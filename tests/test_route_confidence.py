from __future__ import annotations

from spark_intelligence.self_awareness import build_agent_operating_context
from spark_intelligence.self_awareness.route_confidence import build_route_confidence

from tests.test_support import SparkTestCase


class RouteConfidenceTests(SparkTestCase):
    def test_spawner_route_confidence_uses_runner_and_route_health(self) -> None:
        report = build_route_confidence(
            task_fit={
                "recommended_route": "writable_spawner_codex_mission",
                "blocked_here_by": ["current_runner_read_only"],
                "why": ["The request needs local code or file work."],
            },
            runner={"writable": False, "label": "read-only chat runner"},
            access={"label": "Level 4 - local workspace allowed", "local_workspace_allowed": True},
            routes=[
                {
                    "key": "spark_spawner",
                    "status": "healthy",
                    "available": True,
                    "last_success_at": "2026-05-09T10:00:00Z",
                }
            ],
        )

        payload = report.to_payload()
        self.assertEqual(payload["confidence"], "high")
        self.assertGreaterEqual(payload["score"], 80)
        self.assertIn("current_runner_read_only", payload["risks"])
        self.assertTrue(any("Spawner route is healthy" in item for item in payload["evidence"]))

    def test_missing_access_marks_route_confidence_blocked(self) -> None:
        report = build_route_confidence(
            task_fit={
                "recommended_route": "ask_for_access_or_route",
                "blocked_here_by": ["local_workspace_access_unknown_or_denied"],
                "why": ["The request appears to need local workspace work."],
            },
            runner={"writable": None, "label": "unknown"},
            access={"label": "unknown", "local_workspace_allowed": False},
            routes=[],
        )

        self.assertEqual(report.confidence, "blocked")
        self.assertIn("local_workspace_access_not_confirmed", report.risks)

    def test_agent_operating_context_includes_route_confidence(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        payload = context.to_payload()

        self.assertEqual(payload["route_confidence"]["recommended_route"], payload["task_fit"]["recommended_route"])
        self.assertIn(payload["route_confidence"]["confidence"], {"high", "medium", "low", "blocked", "unknown"})
        self.assertIn("Route confidence:", context.to_text())
        ledger_sources = {item["source"] for item in payload["source_ledger"]}
        self.assertIn("route_confidence", ledger_sources)
