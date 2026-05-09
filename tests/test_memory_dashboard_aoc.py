from __future__ import annotations

from spark_intelligence.memory.doctor import run_memory_doctor
from spark_intelligence.self_awareness.stale_context_sweeper import build_stale_context_sweep
from spark_intelligence.self_awareness.turn_recorder import record_agent_turn_trace

from tests.test_support import SparkTestCase


class MemoryDashboardAocTests(SparkTestCase):
    def test_memory_doctor_dashboard_surfaces_approval_inbox_and_stale_actions(self) -> None:
        record_agent_turn_trace(
            self.state_db,
            request_id="req-memory-dashboard-aoc",
            user_message="Worth remembering: AOC updates should stay concise.",
            memory_candidate={
                "text": "AOC updates should stay concise.",
                "memory_role": "preference",
                "target_scope": "personal_preference",
            },
        )
        build_stale_context_sweep(
            live_claims=[
                {
                    "claim_key": "spark_access_level",
                    "value": "Level 4",
                    "source": "current_diagnostics",
                    "freshness": "live_probed",
                }
            ],
            context_claims=[
                {
                    "claim_key": "spark_access_level",
                    "value": "Level 1",
                    "source": "approved_memory",
                    "freshness": "stale",
                }
            ],
            state_db=self.state_db,
            record_contradictions=True,
            request_id="req-memory-dashboard-aoc",
        )

        report = run_memory_doctor(self.state_db, config_manager=self.config_manager)
        dashboard = report.to_dict()["dashboard"]

        self.assertEqual(dashboard["memory_approval_inbox"]["counts"]["pending"], 1)
        self.assertEqual(dashboard["stale_context_actions"]["counts"]["mark_memory_stale"], 1)
