from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class TurnTraceCliTests(SparkTestCase):
    def test_turn_trace_cli_feeds_aoc_black_box_and_memory_inbox(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "turn-trace",
            "--home",
            str(self.home),
            "--user-message",
            "Any other thing you'd build on top of this?",
            "--proposed-action",
            "start_mission",
            "--draft-answer",
            "Spark picked up the build. Mission: mission-1778325245851.",
            "--source-json",
            json.dumps(
                {
                    "source": "current_diagnostics",
                    "role": "health_truth",
                    "freshness": "live_probed",
                    "source_ref": "diagnostics:scan-1",
                    "summary": "Builder healthy, Browser unavailable.",
                }
            ),
            "--memory-candidate-json",
            json.dumps(
                {
                    "text": "Preferred AOC updates are concise.",
                    "memory_role": "preference",
                    "target_scope": "personal_preference",
                }
            ),
            "--request-id",
            "req-turn-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        trace_payload = json.loads(stdout)
        self.assertEqual(trace_payload["frame"]["current_mode"], "concept_chat")
        self.assertEqual(trace_payload["frame"]["user_intent"], "answer")
        self.assertEqual(trace_payload["action_gate"]["decision"], "blocked")
        self.assertEqual(trace_payload["final_answer_check"]["drift_type"], "unrequested_mission_status")
        self.assertEqual(len(trace_payload["event_ids"]), 5)

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--request-id",
            "req-turn-cli",
            "--memory-inbox-status",
            "all",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        panel_payload = json.loads(stdout)
        event_types = {entry["event_type"] for entry in panel_payload["black_box"]["entries"]}
        self.assertIn("task_intent_detected", event_types)
        self.assertIn("blocker_detected", event_types)
        self.assertIn("agent_drift_detected", event_types)
        self.assertIn("source_used", event_types)
        self.assertIn("memory_candidate_created", event_types)
        self.assertEqual(panel_payload["memory_approval_inbox"]["counts"]["pending"], 1)
