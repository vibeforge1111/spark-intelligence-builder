from __future__ import annotations

from spark_intelligence.self_awareness.agent_events import AgentEvent, record_agent_event
from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel
from spark_intelligence.self_awareness.source_hierarchy import SourceClaim

from tests.test_support import SparkTestCase


class AgentOperatingPanelTests(SparkTestCase):
    def test_panel_combines_aoc_black_box_memory_inbox_and_stale_sweep(self) -> None:
        record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="route_selected",
                summary="Route selected for patch work.",
                user_intent="edit",
                selected_route="writable_spawner_codex_mission",
                route_confidence="high",
            ),
            request_id="req-panel",
        )
        record_agent_event(
            self.state_db,
            AgentEvent(
                event_type="memory_candidate_created",
                summary="Memory candidate proposed.",
                memory_candidate={
                    "text": "Mission updates should be concise.",
                    "memory_role": "preference",
                    "target_scope": "personal_preference",
                },
            ),
            request_id="req-panel",
        )

        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-panel",
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
            stale_context_claims=[
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 1",
                    source="approved_memory",
                    freshness="stale",
                    source_ref="memory:old",
                )
            ],
        )

        payload = panel.to_payload()

        self.assertEqual(payload["strip"]["schema_version"], "spark.agent_operating_strip.v1")
        self.assertEqual(payload["strip"]["best_route"], "writable Spawner/Codex mission")
        self.assertEqual(payload["strip"]["access"], "Level 4 - sandboxed workspace allowed")
        self.assertEqual(payload["strip"]["runner"], "read-only chat runner")
        section_ids = {section["section_id"] for section in payload["sections"]["sections"]}
        self.assertIn("permissions", section_ids)
        self.assertIn("runner_capability", section_ids)
        self.assertIn("route_health", section_ids)
        self.assertIn("current_task_fit", section_ids)
        self.assertIn("access_automation", section_ids)
        self.assertIn("source_ledger", section_ids)
        self.assertIn("trace_repair_queue", section_ids)
        self.assertIn("black_box_recorder", section_ids)
        self.assertIn("contradictions", section_ids)
        self.assertIn("what_rec_needs", section_ids)
        self.assertIn("agent_instruction", section_ids)
        self.assertEqual(payload["aoc"]["conversation_frame"]["current_mode"], "patch_work")
        self.assertEqual(payload["black_box"]["counts"]["entries"], 2)
        self.assertEqual(payload["trace_repair_queue"]["status"], "missing")
        self.assertEqual(payload["memory_approval_inbox"]["counts"]["pending"], 1)
        self.assertEqual(payload["stale_context_sweep"]["counts"]["stale"], 1)
        self.assertEqual(payload["source_ledger"]["counts"]["stale"], 1)
        self.assertTrue(
            any(
                item["source"] == "runner_preflight" and item["freshness"] == "live_probed"
                for item in payload["source_ledger"]["items"]
            )
        )
        self.assertEqual(
            payload["agent_scratchpad"]["next_safe_action"],
            "start_or_route_to_writable_spawner_codex_mission",
        )
        self.assertIn("claim_patch_files_here", payload["agent_scratchpad"]["do_not_do"])
        rendered = panel.to_text()
        self.assertIn("Agent Operating Panel", rendered)
        self.assertIn("AOC: Ready with warnings | Best route: writable Spawner/Codex mission", rendered)
        self.assertIn("Next safe access action: spark access setup", rendered)
        self.assertIn("Sources:", rendered)
        self.assertIn("Trace repair: missing; run spark os compile", rendered)
        self.assertIn("Next safe action: start_or_route_to_writable_spawner_codex_mission", rendered)
        self.assertIn("Memory approvals pending: 1", rendered)
        self.assertIn("Stale context: 1 stale", rendered)
