from __future__ import annotations

from spark_intelligence.self_awareness.agent_events import AgentEvent, record_agent_event
from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel

from tests.test_support import SparkTestCase


class AgentPanelSectionsTests(SparkTestCase):
    def test_panel_sections_expose_drilldown_contract(self) -> None:
        record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="route_selected",
                summary="Route selected for patch work.",
                user_intent="edit",
                selected_route="writable_spawner_codex_mission",
                route_confidence="high",
            ),
            request_id="req-sections",
        )
        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-sections",
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        sections = panel.to_payload()["sections"]
        by_id = {section["section_id"]: section for section in sections["sections"]}

        self.assertEqual(sections["schema_version"], "spark.agent_panel_sections.v1")
        self.assertEqual(by_id["permissions"]["status"], "known")
        self.assertEqual(by_id["runner_capability"]["status"], "read_only")
        self.assertEqual(by_id["current_task_fit"]["status"], "writable_spawner_codex_mission")
        self.assertEqual(by_id["black_box_recorder"]["status"], "present")
        self.assertTrue(any(item["label"] == "Entries" for item in by_id["black_box_recorder"]["items"]))
        self.assertEqual(by_id["contradictions"]["status"], "clear")
        self.assertEqual(by_id["what_rec_needs"]["status"], "needed")
        self.assertTrue(any(item["label"] == "writable_runner" for item in by_id["what_rec_needs"]["items"]))
        self.assertTrue(
            any(item["label"] == "Safe next action" for item in by_id["current_task_fit"]["items"])
        )
