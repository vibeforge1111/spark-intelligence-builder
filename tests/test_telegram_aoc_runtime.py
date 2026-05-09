from __future__ import annotations

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.self_awareness.agent_events import build_agent_black_box_report

from tests.test_support import SparkTestCase, make_telegram_update


class TelegramAocRuntimeTests(SparkTestCase):
    def test_telegram_aoc_command_renders_strip_and_records_turn_trace(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=8101,
                user_id="111",
                username="alice",
                text="/aoc fix mission memory loop",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")
        self.assertIn("AOC:", result.detail["response_text"])
        self.assertIn("Agent Operating Context", result.detail["response_text"])

        request_id = str(result.detail["request_id"])
        black_box = build_agent_black_box_report(self.state_db, request_id=request_id).to_payload()
        event_types = {entry["event_type"] for entry in black_box["entries"]}
        self.assertIn("task_intent_detected", event_types)
        self.assertIn("route_selected", event_types)
        self.assertIn("final_answer_checked", event_types)
