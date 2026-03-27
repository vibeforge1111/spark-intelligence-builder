from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.personality.loader import (
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
)

from tests.test_support import SparkTestCase


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.observation_calls: list[dict[str, object]] = []
        self.current_state_calls: list[dict[str, object]] = []

    def write_observation(self, **payload):
        self.observation_calls.append(payload)
        return {
            "status": "accepted",
            "memory_role": "current_state",
            "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-write"},
        }

    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "current_state",
            "records": [
                {
                    "subject": payload["subject"],
                    "predicate": "personality.preference.directness",
                    "value": 0.35,
                }
            ],
            "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-read"},
            "answer_explanation": {"method": "get_current_state"},
        }


class _AbstainingMemoryClient(_FakeMemoryClient):
    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "abstained",
            "reason": "not_found",
            "memory_role": "current_state",
            "provenance": [],
        }


class MemoryOrchestratorTests(SparkTestCase):
    def test_durable_preference_updates_write_structured_memory_observations(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            deltas = detect_and_persist_nl_preferences(
                human_id="human:test",
                user_message="be more direct and stop hedging",
                state_db=self.state_db,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-write",
                channel_kind="telegram",
            )

        self.assertIsNotNone(deltas)
        self.assertTrue(fake_client.observation_calls)
        first_call = fake_client.observation_calls[0]
        self.assertEqual(first_call["subject"], "human:human:test")
        self.assertEqual(first_call["memory_role"], "current_state")
        self.assertEqual(first_call["session_id"], "session:memory")
        self.assertEqual(first_call["turn_id"], "turn:memory-write")
        self.assertIn("personality.preference.", str(first_call["predicate"]))
        events = latest_events_by_type(self.state_db, event_type="memory_write_succeeded", limit=10)
        self.assertTrue(events)

    def test_personality_reset_deletes_memory_preferences(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            detect_and_persist_nl_preferences(
                human_id="human:test",
                user_message="be more direct",
                state_db=self.state_db,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-create",
                channel_kind="telegram",
            )
            fake_client.observation_calls.clear()
            query = detect_personality_query(
                user_message="reset personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=load_personality_profile(
                    human_id="human:test",
                    state_db=self.state_db,
                    config_manager=self.config_manager,
                ),
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-reset",
            )

        self.assertEqual(query.kind, "reset")
        self.assertTrue(fake_client.observation_calls)
        self.assertTrue(all(call["operation"] == "delete" for call in fake_client.observation_calls))

    def test_status_query_uses_narrow_current_state_read_when_memory_is_live(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()
        profile = load_personality_profile(
            human_id="human:test",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            query = detect_personality_query(
                user_message="what's my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=profile,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-read",
            )

        self.assertEqual(query.kind, "status")
        self.assertEqual(len(fake_client.current_state_calls), 1)
        self.assertIn("Memory-backed current-state facts:", query.context_injection)
        self.assertIn("directness: 0.35", query.context_injection)
        events = latest_events_by_type(self.state_db, event_type="memory_read_succeeded", limit=10)
        self.assertTrue(events)

    def test_shadow_mode_preserves_memory_uncertainty_in_status_queries(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", True)
        fake_client = _AbstainingMemoryClient()
        profile = load_personality_profile(
            human_id="human:test",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            query = detect_personality_query(
                user_message="show my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=profile,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-shadow",
            )

        self.assertEqual(query.kind, "status")
        self.assertNotIn("Memory-backed current-state facts:", query.context_injection)
        events = latest_events_by_type(self.state_db, event_type="memory_read_abstained", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"] or {}
        self.assertTrue(facts.get("shadow_only"))
