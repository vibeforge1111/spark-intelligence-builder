from __future__ import annotations

import json

from spark_intelligence.self_awareness.agent_events import AgentEvent, record_agent_event

from tests.test_support import SparkTestCase


class MemoryApprovalCliTests(SparkTestCase):
    def test_memory_inbox_and_decision_cli_feed_aoc_black_box(self) -> None:
        candidate_event_id = record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="memory_candidate_created",
                summary="Memory candidate proposed.",
                memory_candidate={
                    "text": "Preferred AOC updates are concise.",
                    "memory_role": "preference",
                    "target_scope": "personal_preference",
                },
            ),
            request_id="req-memory-cli",
        )

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "memory-inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        inbox_payload = json.loads(stdout)
        self.assertEqual(inbox_payload["counts"]["pending"], 1)
        self.assertEqual(inbox_payload["items"][0]["candidate_event_id"], candidate_event_id)

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "memory-decision",
            candidate_event_id,
            "--home",
            str(self.home),
            "--decision",
            "reject",
            "--reason",
            "Too transient.",
            "--request-id",
            "req-memory-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        decision_payload = json.loads(stdout)
        self.assertEqual(decision_payload["decision"], "reject")
        self.assertTrue(decision_payload["does_not_write_memory"])

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "panel",
            "--home",
            str(self.home),
            "--request-id",
            "req-memory-cli",
            "--memory-inbox-status",
            "all",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        panel_payload = json.loads(stdout)
        event_types = {entry["event_type"] for entry in panel_payload["black_box"]["entries"]}
        self.assertIn("memory_candidate_created", event_types)
        self.assertIn("user_override_received", event_types)
        self.assertEqual(panel_payload["memory_approval_inbox"]["counts"]["decided"], 1)
