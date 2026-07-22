from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class ResearcherDetectionObservabilityTests(SparkTestCase):
    def test_personality_load_failure_records_only_non_secret_recovery_facts(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.personality.nl_preference_detection", False)
        secret_marker = "personality-secret-must-not-enter-observability"

        with patch(
            "spark_intelligence.researcher_bridge.advisory.load_personality_profile",
            side_effect=RuntimeError(secret_marker),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-personality-observability",
                agent_id="agent:human:telegram:111",
                human_id="human:telegram:111",
                session_id="session:telegram:dm:111",
                channel_kind="telegram",
                user_message="hello there",
            )

        self.assertTrue(result.reply_text)
        events = latest_events_by_type(
            self.state_db,
            event_type="researcher_personality_load_failed",
            limit=1,
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["reason_code"], "personality_load_failed")
        self.assertEqual(event["facts_json"]["exception_type"], "RuntimeError")
        self.assertEqual(event["facts_json"]["recovery"], "continue_without_personality_profile")
        self.assertNotIn(secret_marker, str(event))

    def test_memory_query_detection_failure_records_only_non_secret_recovery_facts(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.personality.nl_preference_detection", False)
        secret_marker = "user-secret-marker-must-not-enter-observability"

        with patch(
            "spark_intelligence.researcher_bridge.advisory.detect_profile_fact_query",
            side_effect=RuntimeError(secret_marker),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-detection-observability",
                agent_id="agent:human:telegram:111",
                human_id="human:telegram:111",
                session_id="session:telegram:dm:111",
                channel_kind="telegram",
                user_message="hello there",
            )

        self.assertTrue(result.reply_text)
        events = latest_events_by_type(
            self.state_db,
            event_type="researcher_memory_query_detection_failed",
            limit=1,
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["component"], "researcher_bridge")
        self.assertEqual(event["reason_code"], "memory_query_detection_failed")
        self.assertEqual(event["facts_json"]["exception_type"], "RuntimeError")
        self.assertEqual(event["facts_json"]["recovery"], "continue_without_memory_query_detection")
        self.assertNotIn(secret_marker, str(event))
