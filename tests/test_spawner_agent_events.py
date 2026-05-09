from __future__ import annotations

import json

from spark_intelligence.self_awareness.agent_events import AgentEvent, record_agent_event
from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel
from spark_intelligence.self_awareness.spawner_agent_events import read_spawner_black_box_entries

from tests.test_support import SparkTestCase


class SpawnerAgentEventLedgerTests(SparkTestCase):
    def test_reads_spawner_jsonl_as_black_box_entries(self) -> None:
        ledger_path = self.home / "spawner-ui" / ".spawner" / "agent-events.jsonl"
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "schema_version": "spark.agent_event.v1",
                            "event_id": "agent-spawner-1",
                            "event_type": "mission_changed_state",
                            "component": "agent_event_model",
                            "created_at": "2026-05-09T12:00:00.000Z",
                            "request_id": "req-spawner",
                            "session_id": "mission-control:mission-1",
                            "user_intent": None,
                            "selected_route": "mission_control",
                            "route_confidence": "high",
                            "facts": {"mission_id": "mission-1", "mission_event_type": "mission_completed"},
                            "sources": [
                                {
                                    "source": "mission_trace",
                                    "role": "work_state_evidence",
                                    "freshness": "fresh",
                                    "source_ref": "mission-1",
                                    "summary": "Mission completed.",
                                }
                            ],
                            "assumptions": [],
                            "blockers": [],
                            "changed": ["mission-1:state=completed"],
                            "memory_candidate": None,
                            "summary": "Mission completed.",
                        }
                    ),
                    "{not-json}",
                ]
            ),
            encoding="utf-8",
        )

        entries = read_spawner_black_box_entries(ledger_path, request_id="req-spawner")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].event_id, "agent-spawner-1")
        self.assertEqual(entries[0].event_type, "mission_changed_state")
        self.assertEqual(entries[0].route_chosen, "mission_control")
        self.assertEqual(entries[0].sources_used[0]["source"], "mission_trace")
        self.assertEqual(entries[0].changed, ["mission-1:state=completed"])

    def test_operating_panel_merges_builder_and_spawner_black_box_entries(self) -> None:
        ledger_path = self.home / "spawner-ui" / ".spawner" / "agent-events.jsonl"
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text(
            json.dumps(
                {
                    "schema_version": "spark.agent_event.v1",
                    "event_id": "agent-spawner-2",
                    "event_type": "mission_changed_state",
                    "component": "agent_event_model",
                    "created_at": "2026-05-09T12:00:01.000Z",
                    "request_id": "req-panel-spawner",
                    "session_id": "mission-control:mission-2",
                    "user_intent": None,
                    "selected_route": "mission_control",
                    "route_confidence": "high",
                    "facts": {"mission_id": "mission-2", "mission_event_type": "mission_started"},
                    "sources": [
                        {
                            "source": "mission_trace",
                            "role": "work_state_evidence",
                            "freshness": "fresh",
                            "source_ref": "mission-2",
                            "summary": "Mission started.",
                        }
                    ],
                    "assumptions": [],
                    "blockers": [],
                    "changed": ["mission-2:state=running"],
                    "memory_candidate": None,
                    "summary": "Mission started.",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.config_manager.set_path("spark.spawner.agent_event_ledger_path", str(ledger_path))
        record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="route_selected",
                summary="Route selected in Builder.",
                selected_route="writable_spawner_codex_mission",
            ),
            request_id="req-panel-spawner",
        )

        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-panel-spawner",
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )
        payload = panel.to_payload()
        event_ids = {entry["event_id"] for entry in payload["black_box"]["entries"]}

        self.assertEqual(payload["black_box"]["counts"]["entries"], 2)
        self.assertIn("agent-spawner-2", event_ids)
        self.assertTrue(
            any(item["source"] == "agent_black_box" for item in payload["source_ledger"]["items"])
        )
