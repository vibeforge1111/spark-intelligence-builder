from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.memory import write_telegram_event_to_memory
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class TelegramEpisodicMemoryTests(SparkTestCase):
    def test_build_researcher_reply_persists_detected_telegram_event_before_provider_resolution(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for Telegram event updates"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for Telegram event updates"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-event-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-event-update",
                channel_kind="telegram",
                user_message="My meeting with Omar is on May 3.",
            )

        self.assertEqual(result.reply_text, "I'll remember your meeting with Omar on May 3.")
        self.assertEqual(result.mode, "memory_telegram_event_update")
        self.assertEqual(result.routing_decision, "memory_telegram_event_observation")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_events = (write_events[0]["facts_json"] or {}).get("events") or []
        self.assertEqual(recorded_events[0]["predicate"], "telegram.event.meeting")
        self.assertEqual(recorded_events[0]["value"], "meeting with Omar on May 3")

    def test_build_researcher_reply_answers_saved_telegram_event_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        write_telegram_event_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="telegram.event.meeting",
            value="meeting with Omar on May 3",
            evidence_text="My meeting with Omar is on May 3.",
            event_name="telegram_event_meeting",
            session_id="session-event-query-write",
            turn_id="turn-event-query-write",
            channel_kind="telegram",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for Telegram event queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for Telegram event queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-event-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-event-query",
                channel_kind="telegram",
                user_message="What event did I mention?",
            )

        self.assertEqual(result.reply_text, "I have 1 saved event: meeting with Omar on May 3.")
        self.assertEqual(result.mode, "memory_telegram_event_history")
        self.assertEqual(result.routing_decision, "memory_telegram_event_query")
