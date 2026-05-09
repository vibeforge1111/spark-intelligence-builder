from __future__ import annotations

import json

from tests.test_support import SparkTestCase


class BlackBoxCliTests(SparkTestCase):
    def test_black_box_cli_reads_recorded_turn_trace(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(
            "self",
            "turn-trace",
            "--home",
            str(self.home),
            "--user-message",
            "any other thing you'd build on top of this",
            "--proposed-action",
            "start_mission",
            "--request-id",
            "req-black-box-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "black-box",
            "--home",
            str(self.home),
            "--request-id",
            "req-black-box-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["counts"]["entries"], 2)
        event_types = {entry["event_type"] for entry in payload["entries"]}
        self.assertIn("task_intent_detected", event_types)
        self.assertIn("blocker_detected", event_types)
        blocker = [entry for entry in payload["entries"] if entry["event_type"] == "blocker_detected"][0]
        self.assertTrue(any("start_mission" in item for item in blocker["blockers"]))

    def test_black_box_cli_includes_configured_spawner_ledger(self) -> None:
        ledger_path = self.home / "spawner-ui" / ".spawner" / "agent-events.jsonl"
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text(
            json.dumps(
                {
                    "schema_version": "spark.agent_event.v1",
                    "event_id": "agent-spawner-cli",
                    "event_type": "mission_changed_state",
                    "component": "agent_event_model",
                    "created_at": "2026-05-09T12:00:00.000Z",
                    "request_id": "req-black-box-spawner",
                    "session_id": "mission-control:mission-cli",
                    "user_intent": None,
                    "selected_route": "mission_control",
                    "route_confidence": "high",
                    "facts": {"mission_id": "mission-cli", "mission_event_type": "mission_completed"},
                    "sources": [
                        {
                            "source": "mission_trace",
                            "role": "work_state_evidence",
                            "freshness": "fresh",
                            "source_ref": "mission-cli",
                            "summary": "Mission completed.",
                        }
                    ],
                    "assumptions": [],
                    "blockers": [],
                    "changed": ["mission-cli:state=completed"],
                    "memory_candidate": None,
                    "summary": "Mission completed.",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.config_manager.set_path("spark.spawner.agent_event_ledger_path", str(ledger_path))

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "black-box",
            "--home",
            str(self.home),
            "--request-id",
            "req-black-box-spawner",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["counts"]["entries"], 1)
        self.assertEqual(payload["entries"][0]["event_id"], "agent-spawner-cli")
        self.assertEqual(payload["entries"][0]["route_chosen"], "mission_control")
