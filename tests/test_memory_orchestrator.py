from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.memory import orchestrator as memory_orchestrator
from spark_intelligence.memory import (
    archive_belief_from_memory,
    archive_raw_episode_from_memory,
    archive_structured_evidence_from_memory,
    build_sdk_maintenance_payload,
    build_shadow_replay_payload,
    export_shadow_replay_batch,
    inspect_human_memory_in_memory,
    lookup_current_state_in_memory,
    lookup_historical_state_in_memory,
    retrieve_memory_events_in_memory,
    run_memory_sdk_smoke_test,
    write_belief_to_memory,
    write_profile_fact_to_memory,
    write_raw_episode_to_memory,
    write_structured_evidence_to_memory,
    write_telegram_event_to_memory,
)
from spark_intelligence.memory.episodic_events import (
    build_telegram_memory_event_observation_answer,
    build_telegram_memory_event_query_answer,
    detect_telegram_memory_event_observation,
    detect_telegram_memory_event_query,
    filter_telegram_memory_event_records,
    telegram_event_summary_predicate,
)
from spark_intelligence.memory.profile_facts import (
    build_profile_fact_event_history_answer,
    build_profile_fact_history_answer,
    build_profile_fact_observation_answer,
    build_profile_fact_query_answer,
    build_profile_identity_summary_answer,
    build_profile_fact_query_context,
    build_profile_identity_summary_context,
    detect_profile_fact_observation,
    detect_profile_fact_query,
)
from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    latest_events_by_type,
    recent_reset_sensitive_state_registry,
)
from spark_intelligence.personality.loader import (
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
)

from tests.test_support import SparkTestCase


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.observation_calls: list[dict[str, object]] = []
        self.event_calls: list[dict[str, object]] = []
        self.current_state_calls: list[dict[str, object]] = []

    def write_observation(self, **payload):
        self.observation_calls.append(payload)
        memory_role = (
            "state_deletion"
            if str(payload.get("operation") or "").strip().lower() == "delete"
            else str(payload.get("memory_role") or "current_state")
        )
        return {
            "status": "accepted",
            "memory_role": memory_role,
            "provenance": [{"memory_role": memory_role, "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-write"},
        }

    def write_event(self, **payload):
        self.event_calls.append(payload)
        return {
            "status": "accepted",
            "memory_role": "event",
            "provenance": [{"memory_role": "event", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-event-write"},
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


class _InvalidMemoryRoleClient(_FakeMemoryClient):
    def write_observation(self, **payload):
        self.observation_calls.append(payload)
        return {
            "status": "accepted",
            "memory_role": "timeline",
            "provenance": [{"memory_role": "timeline", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-write-invalid"},
        }

    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "timeline",
            "records": [
                {
                    "subject": payload["subject"],
                    "predicate": "personality.preference.directness",
                    "value": 0.35,
                }
            ],
            "provenance": [{"memory_role": "timeline", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-read-invalid"},
            "answer_explanation": {"method": "get_current_state"},
        }


class MemoryOrchestratorTests(SparkTestCase):
    def test_profile_city_detection_and_write_use_structured_current_state_observation(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        detected = detect_profile_fact_observation("I moved to Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Dubai")

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate=detected.predicate,
                value=detected.value,
                evidence_text=detected.evidence_text,
                fact_name=detected.fact_name,
                session_id="session:city",
                turn_id="turn:city",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["subject"], "human:test")
        self.assertEqual(call["predicate"], "profile.city")
        self.assertEqual(call["value"], "Dubai")
        self.assertEqual(call["text"], "I moved to Dubai.")
        self.assertEqual(call["retention_class"], "durable_profile")
        self.assertTrue(call["document_time"])
        self.assertTrue(call["valid_from"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.city")
        self.assertEqual(observations[0]["value"], "Dubai")
        self.assertEqual(observations[0]["retention_class"], "durable_profile")

    def test_profile_current_state_predicates_request_active_state_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.current_plan",
                value="launch Atlas in enterprise first",
                evidence_text="Our current plan is to launch Atlas in enterprise first.",
                fact_name="current_plan",
                session_id="session:plan",
                turn_id="turn:plan",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(fake_client.observation_calls[0]["retention_class"], "active_state")

    def test_structured_evidence_writes_use_evidence_role_and_archive_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users keep dropping during onboarding because Stripe verification fails.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence",
                turn_id="turn:evidence",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "evidence.telegram.evidence")
        self.assertEqual(call["memory_role"], "structured_evidence")
        self.assertEqual(call["retention_class"], "episodic_archive")
        self.assertEqual(call["metadata"]["evidence_kind"], "evidence_marker")
        self.assertEqual(call["metadata"]["domain_pack"], "evidence")
        self.assertEqual(call["metadata"]["archive_after_days"], 30)
        self.assertTrue(call["metadata"]["archive_at"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(events[0]["facts_json"].get("memory_role"), "structured_evidence")
        self.assertEqual(observations[0]["predicate"], "evidence.telegram.evidence")
        self.assertEqual(observations[0]["retention_class"], "episodic_archive")

    def test_structured_evidence_write_marks_related_beliefs_invalidated(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_records = [
            {
                "memory_role": "belief",
                "predicate": "belief.telegram.beliefs_and_inferences",
                "text": "I think enterprise teams need hands-on onboarding.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-belief-1",
                "metadata": {"value": "I think enterprise teams need hands-on onboarding."},
                "lifecycle": {},
            }
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=prior_records,
                )
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users keep needing hands-on onboarding support because enterprise teams ask for setup help.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:2",
                turn_id="turn:evidence:2",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["conflicts_with"], ["obs-belief-1"])
        self.assertEqual(call["metadata"]["belief_lifecycle_action"], "invalidated")
        self.assertEqual(call["metadata"]["invalidated_belief_ids"], ["obs-belief-1"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["conflicts_with"], ["obs-belief-1"])
        self.assertEqual(observations[0]["belief_lifecycle_action"], "invalidated")

    def test_structured_evidence_write_promotes_corroborated_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep dropping during onboarding because Stripe verification fails.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-1",
                "metadata": {"value": "Users keep dropping during onboarding because Stripe verification fails."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ) as mocked_belief_write:
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:consolidation",
                turn_id="turn:evidence:consolidation",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        mocked_belief_write.assert_called_once()
        belief_kwargs = mocked_belief_write.call_args.kwargs
        self.assertEqual(belief_kwargs["belief_kind"], "evidence_consolidation")
        self.assertEqual(belief_kwargs["domain_pack"], "evidence_onboarding_stripe_verification")
        self.assertIn("I think users still drop during onboarding", belief_kwargs["belief_text"])

    def test_structured_evidence_write_promotes_corroborated_current_blocker(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep dropping during onboarding because Stripe verification fails.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-1",
                "metadata": {"value": "Users keep dropping during onboarding because Stripe verification fails."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:current-state",
                turn_id="turn:evidence:current-state",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        current_state_calls = [
            call for call in fake_client.observation_calls if call.get("predicate") == "profile.current_blocker"
        ]
        self.assertEqual(len(current_state_calls), 1)
        promoted_call = current_state_calls[0]
        self.assertEqual(promoted_call["memory_role"], "current_state")
        self.assertEqual(promoted_call["retention_class"], "active_state")
        self.assertEqual(promoted_call["value"], "Stripe verification fails and the retry flow is confusing")
        self.assertEqual(promoted_call["metadata"]["fact_name"], "current_blocker")

    def test_raw_episode_writes_use_raw_turn_predicate_and_archive_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_raw_episode_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                episode_text="The pricing page felt confusing during the demo.",
                domain_pack="raw_episode",
                session_id="session:raw",
                turn_id="turn:raw",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "raw_turn")
        self.assertEqual(call["memory_role"], "episodic")
        self.assertEqual(call["retention_class"], "episodic_archive")
        self.assertTrue(call["metadata"]["raw_episode"])
        self.assertEqual(call["metadata"]["archive_after_days"], 14)
        self.assertTrue(call["metadata"]["archive_at"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(events[0]["facts_json"].get("memory_role"), "episodic")
        self.assertEqual(observations[0]["predicate"], "raw_turn")
        self.assertEqual(observations[0]["retention_class"], "episodic_archive")

    def test_archive_raw_episode_from_memory_writes_delete_tombstone(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = archive_raw_episode_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                episode_text="The pricing page felt confusing during the demo.",
                raw_episode_observation_id="obs-episode-1",
                archive_reason="covered_by_newer_structured_evidence",
                session_id="session:raw:archive",
                turn_id="turn:raw:archive",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "raw_turn")
        self.assertEqual(call["supersedes"], "obs-episode-1")
        self.assertTrue(call["metadata"]["raw_episode"])
        self.assertEqual(call["metadata"]["raw_episode_lifecycle_action"], "archived")
        self.assertEqual(call["metadata"]["archive_reason"], "covered_by_newer_structured_evidence")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["operation"], "delete")
        self.assertEqual(observations[0]["raw_episode_lifecycle_action"], "archived")

    def test_archive_structured_evidence_from_memory_writes_delete_tombstone(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = archive_structured_evidence_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="evidence.telegram.evidence",
                evidence_text="Users keep dropping during onboarding because Stripe verification fails.",
                evidence_observation_id="obs-evidence-1",
                archive_reason="eclipsed_by_newer_structured_evidence",
                session_id="session:evidence:archive",
                turn_id="turn:evidence:archive",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "evidence.telegram.evidence")
        self.assertEqual(call["supersedes"], "obs-evidence-1")
        self.assertEqual(call["metadata"]["structured_evidence_lifecycle_action"], "archived")
        self.assertEqual(call["metadata"]["archive_reason"], "eclipsed_by_newer_structured_evidence")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["operation"], "delete")
        self.assertEqual(observations[0]["structured_evidence_lifecycle_action"], "archived")

    def test_belief_writes_use_belief_role_and_derived_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_belief_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                belief_text="I think enterprise teams need hands-on onboarding.",
                domain_pack="beliefs_and_inferences",
                belief_kind="belief_marker",
                session_id="session:belief",
                turn_id="turn:belief",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(call["memory_role"], "belief")
        self.assertEqual(call["retention_class"], "derived_belief")
        self.assertEqual(call["metadata"]["belief_kind"], "belief_marker")
        self.assertEqual(call["metadata"]["revalidate_after_days"], 30)
        self.assertTrue(call["metadata"]["revalidate_at"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(events[0]["facts_json"].get("memory_role"), "belief")
        self.assertEqual(observations[0]["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(observations[0]["retention_class"], "derived_belief")

    def test_belief_writes_supersede_latest_active_belief_for_same_pack(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_records = [
            {
                "memory_role": "belief",
                "predicate": "belief.telegram.beliefs_and_inferences",
                "text": "I think self-serve onboarding will work.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-belief-1",
                "metadata": {"value": "I think self-serve onboarding will work."},
                "lifecycle": {},
            }
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=prior_records,
                )
            ),
        ) as retrieve_mock:
            result = write_belief_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                belief_text="I think enterprise teams need hands-on onboarding.",
                domain_pack="beliefs_and_inferences",
                belief_kind="belief_marker",
                session_id="session:belief:2",
                turn_id="turn:belief:2",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertFalse(retrieve_mock.call_args.kwargs["record_activity"])
        call = fake_client.observation_calls[0]
        self.assertEqual(call["supersedes"], "obs-belief-1")
        self.assertEqual(call["conflicts_with"], ["obs-belief-1"])
        self.assertEqual(call["metadata"]["previous_belief_text"], "I think self-serve onboarding will work.")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["supersedes"], "obs-belief-1")
        self.assertEqual(observations[0]["conflicts_with"], ["obs-belief-1"])

    def test_archive_belief_from_memory_writes_delete_tombstone(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = archive_belief_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="belief.telegram.beliefs_and_inferences",
                belief_text="I think enterprise teams need hands-on onboarding.",
                belief_observation_id="obs-belief-1",
                archive_reason="invalidated_and_past_revalidation",
                session_id="session:belief:archive",
                turn_id="turn:belief:archive",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(call["supersedes"], "obs-belief-1")
        self.assertEqual(call["metadata"]["belief_lifecycle_action"], "archived")
        self.assertEqual(call["metadata"]["archive_reason"], "invalidated_and_past_revalidation")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["operation"], "delete")
        self.assertEqual(observations[0]["belief_lifecycle_action"], "archived")

    def test_telegram_event_detection_write_and_answer_use_event_memory_lane(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        detected = detect_telegram_memory_event_observation("My meeting with Omar is on May 3.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "telegram.event.meeting")
        self.assertEqual(detected.value, "meeting with Omar on May 3")

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_telegram_event_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate=detected.predicate,
                value=detected.value,
                evidence_text=detected.evidence_text,
                event_name=detected.event_name,
                session_id="session:event",
                turn_id="turn:event",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.event_calls), 1)
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.event_calls[0]
        self.assertEqual(call["subject"], "human:test")
        self.assertEqual(call["predicate"], "telegram.event.meeting")
        self.assertEqual(call["value"], "meeting with Omar on May 3")
        self.assertEqual(call["text"], "My meeting with Omar is on May 3.")
        self.assertEqual(call["retention_class"], "time_bound_event")
        self.assertTrue(call["document_time"])
        self.assertTrue(call["valid_from"])
        summary_call = fake_client.observation_calls[0]
        self.assertEqual(summary_call["predicate"], "telegram.summary.latest_meeting")
        self.assertEqual(summary_call["value"], "meeting with Omar on May 3")
        self.assertEqual(summary_call["metadata"]["entity_key"], "telegram.summary.latest_meeting")
        self.assertEqual(summary_call["retention_class"], "active_state")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_events = (write_events[0]["facts_json"] or {}).get("events") or []
        self.assertEqual(recorded_events[0]["predicate"], "telegram.event.meeting")
        self.assertEqual(recorded_events[0]["value"], "meeting with Omar on May 3")
        self.assertEqual(recorded_events[0]["retention_class"], "time_bound_event")
        self.assertEqual(
            build_telegram_memory_event_observation_answer(observation=detected),
            "I'll remember your meeting with Omar on May 3.",
        )

    def test_telegram_event_summary_current_state_overwrites_while_event_history_is_preserved(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        write_telegram_event_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test",
            predicate="telegram.event.flight",
            value="flight to London on May 6",
            evidence_text="My flight to London is on May 6.",
            event_name="telegram_event_flight",
            session_id="session:flight:1",
            turn_id="turn:flight:1",
            channel_kind="telegram",
        )
        write_telegram_event_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test",
            predicate="telegram.event.flight",
            value="flight to Paris on May 9",
            evidence_text="My flight to Paris is on May 9.",
            event_name="telegram_event_flight",
            session_id="session:flight:2",
            turn_id="turn:flight:2",
            channel_kind="telegram",
        )

        latest_lookup = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:test",
            predicate="telegram.summary.latest_flight",
            sdk_module="domain_chip_memory",
        )
        history_lookup = retrieve_memory_events_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            query="What flight did I mention?",
            subject="human:test",
            predicate="telegram.event.flight",
            sdk_module="domain_chip_memory",
        )
        inspection = inspect_human_memory_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="test",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(latest_lookup.read_result.abstained)
        self.assertEqual(len(latest_lookup.read_result.records), 1)
        self.assertEqual(latest_lookup.read_result.records[0]["value"], "flight to Paris on May 9")
        self.assertEqual(len(history_lookup.read_result.records), 2)
        self.assertEqual(
            [record["value"] for record in history_lookup.read_result.records],
            ["flight to Paris on May 9", "flight to London on May 6"],
        )
        summary_records = [
            record
            for record in inspection.read_result.records
            if record.get("predicate") == "telegram.summary.latest_flight"
        ]
        self.assertEqual(len(summary_records), 1)
        self.assertEqual(summary_records[0]["value"], "flight to Paris on May 9")

    def test_telegram_event_detection_rejects_hypothetical_and_non_temporal_phrasing(self) -> None:
        self.assertIsNone(
            detect_telegram_memory_event_observation("Maybe my meeting with Omar is on May 3.")
        )
        self.assertIsNone(
            detect_telegram_memory_event_observation("What if my meeting with Omar is on May 3?")
        )
        self.assertIsNone(
            detect_telegram_memory_event_observation("My meeting with Omar is on track.")
        )

    def test_profile_city_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("I live in Abu Dhabi now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Abu Dhabi")

    def test_profile_startup_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("My startup is Atlas Labs now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.startup_name")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_startup_detection_accepts_run_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I run Atlas Labs.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.startup_name")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_startup_detection_accepts_reverse_startup_phrasing(self) -> None:
        detected = detect_profile_fact_observation("Atlas Labs is my startup.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.startup_name")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_current_mission_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("I am trying to rebuild the company now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.current_mission")
        self.assertEqual(detected.value, "rebuild the company")

    def test_profile_name_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("My name is Sarah now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.preferred_name")
        self.assertEqual(detected.value, "Sarah")

    def test_profile_occupation_detection_accepts_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("I am an entrepreneur now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.occupation")
        self.assertEqual(detected.value, "entrepreneur")

    def test_profile_country_detection_accepts_based_in_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm based in Canada now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_country_detection_accepts_based_out_of_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm based out of Canada.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_country_detection_accepts_im_in_country_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm in Canada now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_city_detection_keeps_im_in_city_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm in Abu Dhabi now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Abu Dhabi")

    def test_profile_country_detection_accepts_moved_to_country_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I moved to Canada.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_city_detection_keeps_moved_to_city_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I moved to Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Dubai")

    def test_profile_country_detection_accepts_live_in_country_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I live in Canada.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_country_detection_accepts_live_in_country_aliases(self) -> None:
        uae = detect_profile_fact_observation("I live in UAE.")
        self.assertIsNotNone(uae)
        assert uae is not None
        self.assertEqual(uae.predicate, "profile.home_country")
        self.assertEqual(uae.value, "UAE")

        us = detect_profile_fact_observation("I live in the US.")
        self.assertIsNotNone(us)
        assert us is not None
        self.assertEqual(us.predicate, "profile.home_country")
        self.assertEqual(us.value, "United States")

    def test_profile_city_detection_keeps_live_in_city_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I live in Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Dubai")

    def test_profile_country_detection_normalizes_explicit_country_aliases_with_the(self) -> None:
        from_us = detect_profile_fact_observation("I'm from the US.")
        self.assertIsNotNone(from_us)
        assert from_us is not None
        self.assertEqual(from_us.predicate, "profile.home_country")
        self.assertEqual(from_us.value, "United States")

        based_in_us = detect_profile_fact_observation("I'm based in the US.")
        self.assertIsNotNone(based_in_us)
        assert based_in_us is not None
        self.assertEqual(based_in_us.predicate, "profile.home_country")
        self.assertEqual(based_in_us.value, "United States")

        based_out_uk = detect_profile_fact_observation("I'm based out of the UK.")
        self.assertIsNotNone(based_out_uk)
        assert based_out_uk is not None
        self.assertEqual(based_out_uk.predicate, "profile.home_country")
        self.assertEqual(based_out_uk.value, "United Kingdom")

        from_uae = detect_profile_fact_observation("I'm from the UAE.")
        self.assertIsNotNone(from_uae)
        assert from_uae is not None
        self.assertEqual(from_uae.predicate, "profile.home_country")
        self.assertEqual(from_uae.value, "UAE")

    def test_profile_founder_detection_accepts_founded_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I founded Atlas Labs.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.founder_of")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_founder_detection_accepts_started_built_and_launched_phrasing(self) -> None:
        started = detect_profile_fact_observation("I started Atlas Labs.")
        self.assertIsNotNone(started)
        assert started is not None
        self.assertEqual(started.predicate, "profile.founder_of")
        self.assertEqual(started.value, "Atlas Labs")

        built = detect_profile_fact_observation("I built Atlas Labs.")
        self.assertIsNotNone(built)
        assert built is not None
        self.assertEqual(built.predicate, "profile.founder_of")
        self.assertEqual(built.value, "Atlas Labs")

        launched = detect_profile_fact_observation("I launched Atlas Labs.")
        self.assertIsNotNone(launched)
        assert launched is not None
        self.assertEqual(launched.predicate, "profile.founder_of")
        self.assertEqual(launched.value, "Atlas Labs")

    def test_profile_founder_detection_accepts_founded_startup_called_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I founded a startup called Atlas Labs.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.founder_of")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_fact_write_does_not_double_prefix_prefixed_human_id(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:telegram:8319079055",
                predicate="profile.startup_name",
                value="Seedify",
                evidence_text="My startup is Seedify.",
                fact_name="profile_startup_name",
                session_id="session:prefixed-human-id",
                turn_id="turn:prefixed-human-id",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["subject"], "human:telegram:8319079055")

    def test_inspect_human_memory_uses_legacy_double_prefixed_fallback_for_prefixed_human_id(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        class _LegacyFallbackMemoryClient:
            def __init__(self) -> None:
                self.current_state_calls: list[dict[str, object]] = []

            def get_current_state(self, **payload):
                self.current_state_calls.append(payload)
                subject = str(payload.get("subject") or "")
                if subject == "human:telegram:8319079055":
                    return {
                        "status": "not_found",
                        "memory_role": "unknown",
                        "records": [],
                        "provenance": [],
                        "retrieval_trace": {"trace_id": "mem-trace-primary"},
                    }
                if subject == "human:human:telegram:8319079055":
                    return {
                        "status": "supported",
                        "memory_role": "current_state",
                        "records": [
                            {
                                "subject": subject,
                                "predicate": "profile.startup_name",
                                "value": "Seedify",
                            }
                        ],
                        "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
                        "retrieval_trace": {"trace_id": "mem-trace-legacy"},
                    }
                raise AssertionError(f"unexpected subject {subject}")

        fake_client = _LegacyFallbackMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = inspect_human_memory_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:telegram:8319079055",
                sdk_module="domain_chip_memory",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "profile.startup_name")
        self.assertEqual(
            [str(call.get("subject") or "") for call in fake_client.current_state_calls],
            ["human:telegram:8319079055", "human:human:telegram:8319079055"],
        )

    def test_inspect_memory_sdk_runtime_falls_back_to_local_domain_chip_memory_src(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        local_root = self.home / "domain-chip-memory"
        package_dir = local_root / "src" / "domain_chip_memory"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text(
            "class SparkMemorySDK:\n"
            "    def __init__(self):\n"
            "        self.ready = True\n"
            "\n"
            "def build_sdk_contract_summary():\n"
            "    return {\n"
            "        'runtime_class': 'SparkMemorySDK',\n"
            "        'runtime_memory_architecture': 'dual_store_event_calendar_hybrid',\n"
            "        'runtime_memory_provider': 'heuristic_v1',\n"
            "    }\n",
            encoding="utf-8",
        )

        original_module = sys.modules.pop("domain_chip_memory", None)
        try:
            with patch.object(memory_orchestrator, "DEFAULT_DOMAIN_CHIP_MEMORY_ROOT", local_root):
                runtime = memory_orchestrator.inspect_memory_sdk_runtime(
                    config_manager=self.config_manager,
                    sdk_module="domain_chip_memory",
                )
        finally:
            sys.modules.pop("domain_chip_memory", None)
            if original_module is not None:
                sys.modules["domain_chip_memory"] = original_module

        self.assertTrue(runtime["ready"])
        self.assertEqual(runtime["configured_module"], "domain_chip_memory")
        self.assertEqual(runtime["resolved_module"], "domain_chip_memory")
        self.assertEqual(runtime["runtime_class"], "SparkMemorySDK")
        self.assertEqual(runtime["runtime_memory_architecture"], "dual_store_event_calendar_hybrid")
        self.assertEqual(runtime["runtime_memory_provider"], "heuristic_v1")

    def test_lookup_current_state_uses_legacy_double_prefixed_fallback_for_prefixed_subject(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        class _LegacyLookupMemoryClient:
            def __init__(self) -> None:
                self.current_state_calls: list[dict[str, object]] = []

            def get_current_state(self, **payload):
                self.current_state_calls.append(payload)
                subject = str(payload.get("subject") or "")
                if subject == "human:telegram:8319079055":
                    return {
                        "status": "not_found",
                        "memory_role": "unknown",
                        "records": [],
                        "provenance": [],
                        "retrieval_trace": {"trace_id": "mem-trace-primary"},
                    }
                if subject == "human:human:telegram:8319079055":
                    return {
                        "status": "supported",
                        "memory_role": "current_state",
                        "records": [
                            {
                                "subject": subject,
                                "predicate": "profile.startup_name",
                                "value": "Seedify",
                            }
                        ],
                        "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
                        "retrieval_trace": {"trace_id": "mem-trace-legacy"},
                    }
                raise AssertionError(f"unexpected subject {subject}")

        fake_client = _LegacyLookupMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = lookup_current_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:telegram:8319079055",
                predicate="profile.startup_name",
                sdk_module="domain_chip_memory",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "profile.startup_name")
        self.assertEqual(
            [str(call.get("subject") or "") for call in fake_client.current_state_calls],
            ["human:telegram:8319079055", "human:human:telegram:8319079055"],
        )

    def test_profile_timezone_detection_normalizes_structured_fact(self) -> None:
        detected = detect_profile_fact_observation("My timezone is Asia/Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.timezone")
        self.assertEqual(detected.value, "Asia/Dubai")

    def test_profile_home_country_detection_normalizes_structured_fact(self) -> None:
        detected = detect_profile_fact_observation("My country is UAE.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "UAE")

    def test_profile_preferred_name_detection_normalizes_structured_fact(self) -> None:
        detected = detect_profile_fact_observation("My name is Sarah.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.preferred_name")
        self.assertEqual(detected.value, "Sarah")

    def test_profile_founder_startup_hack_and_mission_detection_normalize_structured_facts(self) -> None:
        startup = detect_profile_fact_observation("My startup is Seedify.")
        self.assertIsNotNone(startup)
        assert startup is not None
        self.assertEqual(startup.predicate, "profile.startup_name")
        self.assertEqual(startup.value, "Seedify")

        founder = detect_profile_fact_observation("I am the founder of Spark Swarm.")
        self.assertIsNotNone(founder)
        assert founder is not None
        self.assertEqual(founder.predicate, "profile.founder_of")
        self.assertEqual(founder.value, "Spark Swarm")

        hack_actor = detect_profile_fact_observation("We were hacked by North Korea.")
        self.assertIsNotNone(hack_actor)
        assert hack_actor is not None
        self.assertEqual(hack_actor.predicate, "profile.hack_actor")
        self.assertEqual(hack_actor.value, "North Korea")

        mission = detect_profile_fact_observation("I am trying to survive the hack and revive the companies.")
        self.assertIsNotNone(mission)
        assert mission is not None
        self.assertEqual(mission.predicate, "profile.current_mission")
        self.assertEqual(mission.value, "survive the hack and revive the companies")

    def test_profile_fact_query_detects_startup_founder_occupation_and_identity_summary_queries(self) -> None:
        city_query = detect_profile_fact_query("Which city do I live in?")
        self.assertIsNotNone(city_query)
        assert city_query is not None
        self.assertEqual(city_query.predicate, "profile.city")
        self.assertEqual(city_query.query_kind, "single_fact")

        country_query = detect_profile_fact_query("What country do I live in?")
        self.assertIsNotNone(country_query)
        assert country_query is not None
        self.assertEqual(country_query.predicate, "profile.home_country")
        self.assertEqual(country_query.query_kind, "single_fact")

        startup_query = detect_profile_fact_query("What startup did I create?")
        self.assertIsNotNone(startup_query)
        assert startup_query is not None
        self.assertEqual(startup_query.predicate, "profile.founder_of")
        self.assertEqual(startup_query.query_kind, "single_fact")

        founder_query = detect_profile_fact_query("What company did I found?")
        self.assertIsNotNone(founder_query)
        assert founder_query is not None
        self.assertEqual(founder_query.predicate, "profile.founder_of")
        self.assertEqual(founder_query.query_kind, "single_fact")

        occupation_query = detect_profile_fact_query("What is my occupation?")
        self.assertIsNotNone(occupation_query)
        assert occupation_query is not None
        self.assertEqual(occupation_query.predicate, "profile.occupation")
        self.assertEqual(occupation_query.query_kind, "single_fact")

        identity_query = detect_profile_fact_query("Who am I?")
        self.assertIsNotNone(identity_query)
        assert identity_query is not None
        self.assertEqual(identity_query.query_kind, "identity_summary")
        self.assertEqual(identity_query.predicate_prefix, "profile.")

        remember_query = detect_profile_fact_query("What do you remember about me?")
        self.assertIsNotNone(remember_query)
        assert remember_query is not None
        self.assertEqual(remember_query.query_kind, "identity_summary")
        self.assertEqual(remember_query.predicate_prefix, "profile.")

        summarize_profile_query = detect_profile_fact_query("Summarize my profile in one sentence.")
        self.assertIsNotNone(summarize_profile_query)
        assert summarize_profile_query is not None
        self.assertEqual(summarize_profile_query.query_kind, "identity_summary")
        self.assertEqual(summarize_profile_query.predicate_prefix, "profile.")

        full_profile_query = detect_profile_fact_query(
            "Give me a full profile summary with my latest location too."
        )
        self.assertIsNotNone(full_profile_query)
        assert full_profile_query is not None
        self.assertEqual(full_profile_query.query_kind, "identity_summary")
        self.assertEqual(full_profile_query.predicate_prefix, "profile.")

    def test_profile_fact_answers_cover_founder_occupation_and_clean_observation_wording(self) -> None:
        founder_query = detect_profile_fact_query("What company did I found?")
        self.assertIsNotNone(founder_query)
        assert founder_query is not None
        self.assertEqual(
            build_profile_fact_query_answer(query=founder_query, value="Spark Swarm"),
            "You founded Spark Swarm.",
        )

        occupation_query = detect_profile_fact_query("What am I?")
        self.assertIsNotNone(occupation_query)
        assert occupation_query is not None
        self.assertEqual(
            build_profile_fact_query_answer(query=occupation_query, value="entrepreneur"),
            "You're an entrepreneur.",
        )

        occupation_observation = detect_profile_fact_observation("I am an entrepreneur.")
        self.assertIsNotNone(occupation_observation)
        assert occupation_observation is not None
        self.assertEqual(
            build_profile_fact_observation_answer(observation=occupation_observation),
            "I'll remember you're an entrepreneur.",
        )

        spark_role_observation = detect_profile_fact_observation(
            "Spark will be an important part of the rebuild."
        )
        self.assertIsNotNone(spark_role_observation)
        assert spark_role_observation is not None
        self.assertEqual(
            build_profile_fact_observation_answer(observation=spark_role_observation),
            "I'll remember Spark will be an important part of the rebuild.",
        )

        mission_explanation_query = detect_profile_fact_query(
            "How do you know what I'm trying to do now?"
        )
        self.assertIsNotNone(mission_explanation_query)
        assert mission_explanation_query is not None
        self.assertEqual(mission_explanation_query.predicate, "profile.current_mission")
        self.assertEqual(mission_explanation_query.query_kind, "fact_explanation")
        self.assertIsNone(
            detect_profile_fact_observation("How do you know what I'm trying to do now?")
        )

    def test_profile_fact_query_detects_history_and_event_history_queries(self) -> None:
        history_query = detect_profile_fact_query("Where did I live before?")
        self.assertIsNotNone(history_query)
        assert history_query is not None
        self.assertEqual(history_query.predicate, "profile.city")
        self.assertEqual(history_query.query_kind, "fact_history")

        country_history_query = detect_profile_fact_query("What was my previous country?")
        self.assertIsNotNone(country_history_query)
        assert country_history_query is not None
        self.assertEqual(country_history_query.predicate, "profile.home_country")
        self.assertEqual(country_history_query.query_kind, "fact_history")

        event_history_query = detect_profile_fact_query(
            "What memory events do you have about where I live?"
        )
        self.assertIsNotNone(event_history_query)
        assert event_history_query is not None
        self.assertEqual(event_history_query.predicate, "profile.city")
        self.assertEqual(event_history_query.query_kind, "event_history")

        cofounder_history_query = detect_profile_fact_query("Who was my cofounder before?")
        self.assertIsNotNone(cofounder_history_query)
        assert cofounder_history_query is not None
        self.assertEqual(cofounder_history_query.predicate, "profile.cofounder_name")
        self.assertEqual(cofounder_history_query.query_kind, "fact_history")

        cofounder_event_history_query = detect_profile_fact_query("Show my cofounder history.")
        self.assertIsNotNone(cofounder_event_history_query)
        assert cofounder_event_history_query is not None
        self.assertEqual(cofounder_event_history_query.predicate, "profile.cofounder_name")
        self.assertEqual(cofounder_event_history_query.query_kind, "event_history")

    def test_profile_fact_history_and_event_answers_are_grounded_and_compact(self) -> None:
        history_query = detect_profile_fact_query("Where did I live before?")
        self.assertIsNotNone(history_query)
        assert history_query is not None
        self.assertEqual(
            build_profile_fact_history_answer(
                query=history_query,
                previous_value="Dubai",
                current_value="Abu Dhabi",
            ),
            "Before Abu Dhabi, you lived in Dubai.",
        )
        self.assertEqual(
            build_profile_fact_history_answer(
                query=history_query,
                previous_value=None,
                current_value="Abu Dhabi",
            ),
            "I don't currently have an earlier saved city.",
        )

        event_history_query = detect_profile_fact_query(
            "What memory events do you have about where I live?"
        )
        self.assertIsNotNone(event_history_query)
        assert event_history_query is not None
        self.assertEqual(
            build_profile_fact_event_history_answer(
                query=event_history_query,
                records=[
                    {"value": "Dubai"},
                    {"value": "Dubai"},
                    {"value": "Abu Dhabi"},
                ],
            ),
            "I have 2 saved city events: Dubai then Abu Dhabi.",
        )

        cofounder_history_query = detect_profile_fact_query("Who was my cofounder before?")
        self.assertIsNotNone(cofounder_history_query)
        assert cofounder_history_query is not None
        self.assertEqual(
            build_profile_fact_history_answer(
                query=cofounder_history_query,
                previous_value="Omar",
                current_value="Sara",
            ),
            "Before Sara, your cofounder was Omar.",
        )

    def test_telegram_event_query_detection_filtering_and_answers_are_compact(self) -> None:
        query = detect_telegram_memory_event_query("What event did I mention?")
        self.assertIsNotNone(query)
        assert query is not None
        self.assertIsNone(query.predicate)

        meeting_query = detect_telegram_memory_event_query("What meetings did I mention?")
        self.assertIsNotNone(meeting_query)
        assert meeting_query is not None
        self.assertEqual(meeting_query.predicate, "telegram.event.meeting")

        latest_query = detect_telegram_memory_event_query("What flight do I have?")
        self.assertIsNotNone(latest_query)
        assert latest_query is not None
        self.assertEqual(latest_query.query_kind, "latest_event")
        self.assertEqual(latest_query.predicate, "telegram.event.flight")
        self.assertEqual(latest_query.summary_predicate, "telegram.summary.latest_flight")
        self.assertEqual(
            telegram_event_summary_predicate("telegram.event.deadline"),
            "telegram.summary.latest_deadline",
        )

        filtered = filter_telegram_memory_event_records(
            query=query,
            records=[
                {
                    "predicate": "telegram.event.meeting",
                    "value": "meeting with Omar on May 3",
                    "timestamp": "2026-04-10T10:00:00+00:00",
                    "turn_ids": ["turn-1"],
                },
                {
                    "predicate": "profile.city",
                    "value": "Dubai",
                    "timestamp": "2026-04-10T11:00:00+00:00",
                    "turn_ids": ["turn-2"],
                },
                {
                    "predicate": "telegram.event.call",
                    "value": "call with Sarah on May 4",
                    "timestamp": "2026-04-10T12:00:00+00:00",
                    "turn_ids": ["turn-3"],
                },
            ],
        )
        self.assertEqual([record["predicate"] for record in filtered], ["telegram.event.meeting", "telegram.event.call"])
        self.assertEqual(
            build_telegram_memory_event_query_answer(
                query=query,
                records=filtered,
            ),
            "I have 2 saved events: meeting with Omar on May 3 then call with Sarah on May 4.",
        )
        self.assertEqual(
            build_telegram_memory_event_query_answer(
                query=latest_query,
                records=[
                    {
                        "predicate": "telegram.summary.latest_flight",
                        "value": "flight to London on May 6",
                        "timestamp": "2026-04-10T12:00:00+00:00",
                        "turn_ids": ["turn-4"],
                    }
                ],
            ),
            "Your latest saved flight is flight to London on May 6.",
        )

    def test_telegram_event_detection_supports_flight_and_deadline_types(self) -> None:
        flight = detect_telegram_memory_event_observation("My flight to London is on May 6.")
        self.assertIsNotNone(flight)
        assert flight is not None
        self.assertEqual(flight.predicate, "telegram.event.flight")
        self.assertEqual(flight.value, "flight to London on May 6")

        deadline = detect_telegram_memory_event_observation("My deadline for the proposal is by Friday.")
        self.assertIsNotNone(deadline)
        assert deadline is not None
        self.assertEqual(deadline.predicate, "telegram.event.deadline")
        self.assertEqual(deadline.value, "deadline for the proposal by Friday")

    def test_build_profile_identity_summary_context_lists_saved_facts(self) -> None:
        context = build_profile_identity_summary_context(
            records=[
                {"predicate": "profile.occupation", "value": "entrepreneur"},
                {"predicate": "profile.startup_name", "value": "Seedify"},
                {"predicate": "profile.current_mission", "value": "revive the companies"},
            ]
        )

        self.assertIn("[Memory action: PROFILE_IDENTITY_SUMMARY]", context)
        self.assertIn("- occupation: entrepreneur", context)
        self.assertIn("- startup: Seedify", context)
        self.assertIn("- current mission: revive the companies", context)

    def test_build_profile_identity_summary_prefers_latest_values_per_predicate(self) -> None:
        context = build_profile_identity_summary_context(
            records=[
                {"predicate": "profile.home_country", "value": "UAE"},
                {"predicate": "profile.home_country", "value": "Canada"},
                {"predicate": "profile.founder_of", "value": "Spark Swarm"},
                {"predicate": "profile.founder_of", "value": "Atlas Labs"},
            ]
        )

        self.assertIn("- country: Canada", context)
        self.assertIn("- founder of: Atlas Labs", context)
        self.assertNotIn("- country: UAE", context)
        self.assertNotIn("- founder of: Spark Swarm", context)

        answer = build_profile_identity_summary_answer(
            records=[
                {"predicate": "profile.home_country", "value": "UAE"},
                {"predicate": "profile.home_country", "value": "Canada"},
                {"predicate": "profile.startup_name", "value": "Seedify"},
                {"predicate": "profile.startup_name", "value": "Atlas Labs"},
                {"predicate": "profile.founder_of", "value": "Spark Swarm"},
                {"predicate": "profile.founder_of", "value": "Atlas Labs"},
            ]
        )

        self.assertIn("You founded Atlas Labs.", answer)
        self.assertIn("You're based in Canada.", answer)
        self.assertNotIn("Spark Swarm", answer)
        self.assertNotIn("Seedify", answer)
        self.assertNotIn("UAE", answer)

    def test_build_profile_identity_summary_answer_compacts_saved_facts(self) -> None:
        answer = build_profile_identity_summary_answer(
            records=[
                {"predicate": "profile.occupation", "value": "entrepreneur"},
                {"predicate": "profile.city", "value": "Dubai"},
                {"predicate": "profile.startup_name", "value": "Seedify"},
                {"predicate": "profile.founder_of", "value": "Spark Swarm"},
                {"predicate": "profile.home_country", "value": "UAE"},
                {"predicate": "profile.current_mission", "value": "survive the hack and revive the companies"},
                {"predicate": "profile.spark_role", "value": "important part of the rebuild"},
            ]
        )

        self.assertIn("You're an entrepreneur in Dubai.", answer)
        self.assertIn("You founded Spark Swarm.", answer)
        self.assertIn("Your startup is Seedify.", answer)
        self.assertIn("Your country is UAE.", answer)
        self.assertIn("Your current mission is to survive the hack and revive the companies.", answer)
        self.assertIn("Spark will be an important part of the rebuild.", answer)

    def test_build_profile_identity_summary_answer_deduplicates_matching_founder_and_startup(self) -> None:
        answer = build_profile_identity_summary_answer(
            records=[
                {"predicate": "profile.startup_name", "value": "Atlas Labs"},
                {"predicate": "profile.founder_of", "value": "Atlas Labs"},
            ]
        )

        self.assertEqual(answer, "You founded Atlas Labs.")

    def test_build_profile_fact_query_context_demands_single_sentence_grounded_answer(self) -> None:
        query = detect_profile_fact_query("What role will Spark play in this?")
        self.assertIsNotNone(query)
        assert query is not None

        context = build_profile_fact_query_context(
            query=query,
            value="important part of the rebuild",
        )

        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", context)
        self.assertIn("Expected concise answer: Spark will be an important part of the rebuild.", context)
        self.assertIn("Answer in one sentence only.", context)
        self.assertIn("Do not add broader narrative", context)

    def test_memory_sdk_smoke_test_runs_real_domain_chip_roundtrip(self) -> None:
        result = run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:smoke:test",
            predicate="system.memory.smoke",
            value="ok",
        )

        self.assertEqual(result.sdk_module, "domain_chip_memory")
        self.assertGreaterEqual(result.write_result.accepted_count, 1)
        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "system.memory.smoke")
        self.assertEqual(result.read_result.records[0]["value"], "ok")
        self.assertIsNotNone(result.cleanup_result)
        self.assertGreaterEqual(result.cleanup_result.accepted_count, 1)
        smoke_events = latest_events_by_type(self.state_db, event_type="memory_smoke_succeeded", limit=5)
        self.assertTrue(smoke_events)
        smoke_facts = smoke_events[0]["facts_json"] or {}
        self.assertEqual(smoke_facts.get("sdk_module"), "domain_chip_memory")
        self.assertEqual(smoke_facts.get("predicate"), "system.memory.smoke")

    def test_lookup_current_state_in_memory_reads_back_structured_fact(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:lookup:test",
            predicate="system.memory.lookup",
            value="ok",
            cleanup=False,
        )

        result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:lookup:test",
            predicate="system.memory.lookup",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "system.memory.lookup")
        self.assertEqual(result.read_result.records[0]["value"], "ok")

    def test_lookup_current_state_in_memory_rehydrates_domain_chip_state_after_cache_clear(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:lookup:persisted",
            predicate="system.memory.persisted",
            value="ok",
            cleanup=False,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:lookup:persisted",
            predicate="system.memory.persisted",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "system.memory.persisted")
        self.assertEqual(result.read_result.records[0]["value"], "ok")

    def test_domain_chip_persistence_merges_concurrent_client_writes(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:a",
            predicate="profile.current_owner",
            value="Omar",
            text="Our owner is Omar.",
            session_id="session:merge:test:a",
            turn_id="turn:merge:test:a",
            timestamp="2026-04-21T10:00:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Omar",
            },
        )
        client_b.write_observation(
            operation="update",
            subject="human:merge:test:b",
            predicate="profile.current_constraint",
            value="budget for one engineer",
            text="Our constraint is budget for only one engineer.",
            session_id="session:merge:test:b",
            turn_id="turn:merge:test:b",
            timestamp="2026-04-21T10:00:01+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_constraint",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_constraint",
                "normalized_value": "budget for one engineer",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        owner_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:a",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )
        constraint_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:b",
            predicate="profile.current_constraint",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(owner_result.read_result.abstained)
        self.assertEqual(owner_result.read_result.records[0]["value"], "Omar")
        self.assertFalse(constraint_result.read_result.abstained)
        self.assertEqual(constraint_result.read_result.records[0]["value"], "budget for one engineer")

    def test_domain_chip_persistence_merges_concurrent_current_state_and_event_writes(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:mixed",
            predicate="profile.current_owner",
            value="Nadia",
            text="The current owner is Nadia.",
            session_id="session:merge:test:mixed:owner",
            turn_id="turn:merge:test:mixed:owner",
            timestamp="2026-04-21T10:01:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Nadia",
            },
        )
        client_b.write_event(
            operation="event",
            subject="human:merge:test:mixed",
            predicate="telegram.event.meeting",
            value="meeting with Omar on May 3",
            text="My meeting with Omar is on May 3.",
            session_id="session:merge:test:mixed:event",
            turn_id="turn:merge:test:mixed:event",
            timestamp="2026-04-21T10:01:01+00:00",
            metadata={
                "entity_type": "human",
                "memory_role": "event",
                "source_surface": "test",
                "event_name": "telegram_event_meeting",
                "normalized_value": "meeting with Omar on May 3",
                "value": "meeting with Omar on May 3",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        owner_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:mixed",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )
        event_result = retrieve_memory_events_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            query="What event did I mention?",
            subject="human:merge:test:mixed",
            predicate="telegram.event.meeting",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(owner_result.read_result.abstained)
        self.assertEqual(owner_result.read_result.records[0]["value"], "Nadia")
        self.assertFalse(event_result.read_result.abstained)
        self.assertEqual(len(event_result.read_result.records), 1)
        self.assertEqual(event_result.read_result.records[0]["value"], "meeting with Omar on May 3")

    def test_domain_chip_persistence_preserves_stale_client_delete(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:delete",
            predicate="profile.current_owner",
            value="Omar",
            text="Our owner is Omar.",
            session_id="session:merge:test:delete:write",
            turn_id="turn:merge:test:delete:write",
            timestamp="2026-04-21T10:02:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Omar",
            },
        )
        client_b.write_observation(
            operation="delete",
            subject="human:merge:test:delete",
            predicate="profile.current_owner",
            value=None,
            text="Forget our owner.",
            session_id="session:merge:test:delete:delete",
            turn_id="turn:merge:test:delete:delete",
            timestamp="2026-04-21T10:02:01+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        current_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:delete",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )

        self.assertTrue(current_result.read_result.abstained)
        self.assertEqual(current_result.read_result.records, [])

    def test_domain_chip_persistence_preserves_repeated_stale_client_overwrites_across_predicates(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_c = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)
        self.assertIsNotNone(client_c)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_owner",
            value="Omar",
            text="Our owner is Omar.",
            session_id="session:merge:test:multi:owner:1",
            turn_id="turn:merge:test:multi:owner:1",
            timestamp="2026-04-21T10:03:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Omar",
            },
        )
        client_b.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_risk",
            value="delayed instrumentation",
            text="Our main risk is delayed instrumentation.",
            session_id="session:merge:test:multi:risk",
            turn_id="turn:merge:test:multi:risk",
            timestamp="2026-04-21T10:03:01+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_risk",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_risk",
                "normalized_value": "delayed instrumentation",
            },
        )
        client_c.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_dependency",
            value="partner API access",
            text="Our dependency is partner API access.",
            session_id="session:merge:test:multi:dependency",
            turn_id="turn:merge:test:multi:dependency",
            timestamp="2026-04-21T10:03:02+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_dependency",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_dependency",
                "normalized_value": "partner API access",
            },
        )
        client_a.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_owner",
            value="Sara",
            text="The current owner is Sara.",
            session_id="session:merge:test:multi:owner:2",
            turn_id="turn:merge:test:multi:owner:2",
            timestamp="2026-04-21T10:03:03+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Sara",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        owner_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:multi",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )
        risk_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:multi",
            predicate="profile.current_risk",
            sdk_module="domain_chip_memory",
        )
        dependency_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:multi",
            predicate="profile.current_dependency",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(owner_result.read_result.abstained)
        self.assertEqual(owner_result.read_result.records[0]["value"], "Sara")
        self.assertFalse(risk_result.read_result.abstained)
        self.assertEqual(risk_result.read_result.records[0]["value"], "delayed instrumentation")
        self.assertFalse(dependency_result.read_result.abstained)
        self.assertEqual(dependency_result.read_result.records[0]["value"], "partner API access")

    def test_lookup_historical_state_in_memory_reads_prior_value_after_overwrite(self) -> None:
        expected_read_result = memory_orchestrator.MemoryReadResult(
            status="supported",
            method="get_historical_state",
            memory_role="structured_evidence",
            records=[
                {
                    "predicate": "profile.city",
                    "value": "Dubai",
                    "timestamp": "2026-04-10T10:00:00+00:00",
                }
            ],
            provenance=[],
            retrieval_trace={"operation": "get_historical_state"},
            answer_explanation=None,
            abstained=False,
        )

        with patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "configured_module": "domain_chip_memory"},
        ), patch(
            "spark_intelligence.memory.orchestrator._load_sdk_client_for_module",
            return_value=object(),
        ), patch(
            "spark_intelligence.memory.orchestrator._get_historical_state_with_subject_fallback",
            return_value=("human:history:test", expected_read_result),
        ) as history_lookup_mock:
            result = lookup_historical_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:history:test",
                predicate="profile.city",
                as_of="2026-04-10T10:00:00+00:00",
                sdk_module="domain_chip_memory",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertEqual(result.as_of, "2026-04-10T10:00:00+00:00")
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "profile.city")
        self.assertEqual(result.read_result.records[0]["value"], "Dubai")
        history_lookup_mock.assert_called_once()

    def test_inspect_human_memory_in_memory_returns_all_current_state_records_for_subject(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:inspect:test",
            predicate="system.memory.one",
            value="alpha",
            cleanup=False,
        )
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:inspect:test",
            predicate="system.memory.two",
            value="beta",
            cleanup=False,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        result = inspect_human_memory_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="inspect:test",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(result.read_result.abstained)
        predicates = {str(record.get("predicate") or "") for record in result.read_result.records}
        self.assertEqual(predicates, {"system.memory.one", "system.memory.two"})

    def test_domain_chip_memory_sdk_module_supports_live_preference_write_then_read(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        self.config_manager.set_path("spark.memory.sdk_module", "domain_chip_memory")

        deltas = detect_and_persist_nl_preferences(
            human_id="human:test",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
            config_manager=self.config_manager,
            session_id="session:domain-chip",
            turn_id="turn:domain-chip-write",
            channel_kind="telegram",
        )

        self.assertIsNotNone(deltas)

        query = detect_personality_query(
            user_message="what's my personality",
            human_id="human:test",
            state_db=self.state_db,
            profile=load_personality_profile(
                human_id="human:test",
                state_db=self.state_db,
                config_manager=self.config_manager,
            ),
            config_manager=self.config_manager,
            session_id="session:domain-chip",
            turn_id="turn:domain-chip-read",
        )

        self.assertEqual(query.kind, "status")
        self.assertIn("Memory-backed current-state facts:", query.context_injection)
        self.assertIn("directness:", query.context_injection)
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_succeeded", limit=10)
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_succeeded", limit=10)
        self.assertTrue(write_events)
        self.assertTrue(read_events)
        self.assertFalse(bool((read_events[0]["facts_json"] or {}).get("shadow_only")))

    def test_load_personality_profile_gracefully_handles_missing_runtime_dependencies(self) -> None:
        missing_state_path = self.home / "missing-personality-evolver.json"
        with patch("spark_intelligence.personality.loader._PERSONALITY_EVOLUTION_FILE", missing_state_path):
            profile = load_personality_profile(
                human_id="human:test",
                state_db=None,
                config_manager=None,
            )

        assert profile is not None
        self.assertEqual(profile["source"], "defaults")
        self.assertFalse(profile["user_deltas_applied"])
        self.assertEqual(profile["traits"]["warmth"], 0.5)
        self.assertEqual(profile["traits"]["directness"], 0.5)

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
        self.assertEqual(first_call["subject"], "human:test")
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
        reset_events = latest_events_by_type(self.state_db, event_type="session_reset_performed", limit=10)
        self.assertTrue(reset_events)
        registry_rows = recent_reset_sensitive_state_registry(self.state_db, limit=20)
        human_rows = [row for row in registry_rows if row["scope_ref"] == "human:test"]
        self.assertTrue(human_rows)
        self.assertTrue(all(int(row["active"]) == 0 for row in human_rows))

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

    def test_watchtower_memory_shadow_panel_counts_sdk_outcomes(self) -> None:
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
                turn_id="turn:memory-write",
                channel_kind="telegram",
            )
            detect_personality_query(
                user_message="show my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=load_personality_profile(
                    human_id="human:test",
                    state_db=self.state_db,
                    config_manager=self.config_manager,
                ),
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-read",
            )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["memory_shadow"]

        self.assertEqual(panel["counts"]["write_requests"], 1)
        self.assertEqual(panel["counts"]["accepted_observations"], 1)
        self.assertEqual(panel["counts"]["read_requests"], 2)
        self.assertEqual(panel["counts"]["read_hits"], 1)
        self.assertEqual(panel["counts"]["shadow_only_reads"], 0)

    def test_doctor_flags_live_memory_when_all_reads_abstain(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _AbstainingMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            detect_personality_query(
                user_message="what's my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=load_personality_profile(
                    human_id="human:test",
                    state_db=self.state_db,
                    config_manager=self.config_manager,
                ),
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-read",
            )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertIn("watchtower-memory-shadow", checks)
        self.assertFalse(checks["watchtower-memory-shadow"].ok)
        self.assertIn("memory_abstaining", checks["watchtower-memory-shadow"].detail)

    def test_invalid_sdk_memory_role_is_abstained_and_counted_in_watchtower(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _InvalidMemoryRoleClient()

        with (
            patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client),
            patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client),
        ):
            write_result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.city",
                value="Dubai",
                evidence_text="I moved to Dubai.",
                fact_name="city",
                session_id="session:invalid-role",
                turn_id="turn:invalid-role-write",
                channel_kind="telegram",
            )
            lookup_result = lookup_current_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:test",
                predicate="profile.city",
            )

        self.assertTrue(write_result.abstained)
        self.assertEqual(write_result.reason, "invalid_memory_role")
        self.assertTrue(lookup_result.read_result.abstained)
        self.assertEqual(lookup_result.read_result.reason, "invalid_memory_role")

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["memory_shadow"]
        self.assertEqual(panel["counts"]["contract_violations"], 2)
        self.assertEqual(panel["counts"]["invalid_role_events"], 2)

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-memory-contract", checks)
        self.assertFalse(checks["watchtower-memory-contract"].ok)
        self.assertIn("contract_violations=2", checks["watchtower-memory-contract"].detail)

    def test_retrieve_evidence_uses_metadata_memory_role_when_sdk_record_is_unknown(self) -> None:
        result = SimpleNamespace(
            items=[
                SimpleNamespace(
                    memory_role="unknown",
                    subject="human:test",
                    predicate="profile.city",
                    text="I moved to Dubai.",
                    session_id="session:test",
                    turn_ids=["turn:test"],
                    timestamp="2026-04-20T12:00:00Z",
                    metadata={"memory_role": "current_state", "value": "Dubai"},
                )
            ],
            trace={"trace_id": "trace:test"},
        )

        normalized = memory_orchestrator._normalize_domain_retrieval_result(
            result=result,
            method="retrieve_evidence",
        )

        self.assertEqual(normalized["status"], "supported")
        self.assertEqual(normalized["memory_role"], "current_state")
        self.assertEqual(normalized["records"][0]["memory_role"], "current_state")
        self.assertEqual(normalized["records"][0]["value"], "Dubai")

    def test_shadow_replay_payload_uses_real_turns_and_memory_observation_probes(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-1",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-1",
                    "turn-1",
                    "telegram",
                    "session-1",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    json.dumps({"message_text": "Please be more direct with me."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-assistant-1",
                    "delivery_succeeded",
                    "delivery",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-1",
                    "turn-1",
                    "telegram",
                    "session-1",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "ok",
                    "Delivery succeeded.",
                    json.dumps({"delivered_text": "Noted. I will be more direct.", "ack_ref": "telegram:1"}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-1",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-1",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "personality.preference.directness",
                                    "value": 0.35,
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-1",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-1",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-2",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-2",
                    "turn-2",
                    "telegram",
                    "session-1",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    json.dumps({"message_text": "Actually, be much more blunt."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-2",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-2",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "personality.preference.directness",
                                    "value": 0.75,
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-2",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-2",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        self.assertEqual(payload["writable_roles"], ["user"])
        self.assertEqual(len(payload["conversations"]), 1)
        conversation = payload["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "session-1")
        self.assertEqual(len(conversation["turns"]), 3)
        self.assertEqual(conversation["turns"][0]["role"], "user")
        self.assertEqual(conversation["turns"][0]["content"], "Please be more direct with me.")
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_kind"], "observation")
        self.assertEqual(conversation["turns"][0]["metadata"]["subject"], "human:test")
        self.assertEqual(conversation["turns"][0]["metadata"]["predicate"], "personality.preference.directness")
        self.assertEqual(conversation["turns"][0]["metadata"]["value"], 0.35)
        self.assertEqual(conversation["turns"][0]["metadata"]["operation"], "update")
        self.assertEqual(conversation["turns"][1]["role"], "assistant")
        self.assertEqual(conversation["turns"][1]["content"], "Noted. I will be more direct.")
        probe_types = {probe["probe_type"] for probe in conversation["probes"]}
        self.assertIn("current_state", probe_types)
        self.assertIn("evidence", probe_types)
        self.assertIn("historical_state", probe_types)
        historical_probe = next(probe for probe in conversation["probes"] if probe["probe_type"] == "historical_state")
        self.assertEqual(historical_probe["expected_value"], 0.35)

    def test_shadow_replay_payload_supports_bridge_native_memory_turns(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-write-req",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "sim:write-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-04-10T11:45:07Z",
                    json.dumps(
                        {
                            "memory_role": "current_state",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:telegram:test",
                                    "predicate": "profile.startup_name",
                                    "value": "Seedify",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                    "text": "My startup is Seedify.",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-write-ok",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "sim:write-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-04-10T11:45:07Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-write-result",
                    "tool_result_received",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:write-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Researcher bridge acknowledged a profile fact update directly from memory.",
                    "2026-04-10T11:45:07Z",
                    json.dumps(
                        {
                            "bridge_mode": "memory_profile_fact_update",
                            "routing_decision": "memory_profile_fact_observation",
                            "fact_name": "profile_startup_name",
                            "predicate": "profile.startup_name",
                            "value": "Seedify",
                            "operation": "update",
                            "keepability": "ephemeral_context",
                            "promotion_disposition": "not_promotable",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-query-influence",
                    "plugin_or_chip_influence_recorded",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:query-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Personality influence was recorded before bridge execution.",
                    "2026-04-10T11:45:08Z",
                    json.dumps(
                        {
                            "detected_profile_fact_query": {
                                "fact_name": "profile_startup_name",
                                "label": "startup",
                                "predicate": "profile.startup_name",
                                "query_kind": "single_fact",
                                "message_text": "What is my startup?",
                            }
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-query-result",
                    "tool_result_received",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:query-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Researcher bridge answered a single-fact profile query directly from memory.",
                    "2026-04-10T11:45:08Z",
                    json.dumps(
                        {
                            "bridge_mode": "memory_profile_fact",
                            "routing_decision": "memory_profile_fact_query",
                            "fact_name": "profile_startup_name",
                            "predicate": "profile.startup_name",
                            "label": "startup",
                            "value_found": True,
                            "keepability": "ephemeral_context",
                            "promotion_disposition": "not_promotable",
                        }
                    ),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        self.assertEqual(len(payload["conversations"]), 1)
        conversation = payload["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "session-bridge")
        self.assertEqual(len(conversation["turns"]), 4)
        self.assertEqual(
            [turn["content"] for turn in conversation["turns"]],
            [
                "My startup is Seedify.",
                "I'll remember you created Seedify.",
                "What is my startup?",
                "You created Seedify.",
            ],
        )
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_kind"], "observation")
        self.assertEqual(conversation["turns"][0]["metadata"]["predicate"], "profile.startup_name")
        probe_types = {probe["probe_type"] for probe in conversation["probes"]}
        self.assertEqual(probe_types, {"current_state", "evidence"})

    def test_shadow_replay_payload_preserves_explicit_identity_summary_prompt(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-identity-query-influence",
                    "plugin_or_chip_influence_recorded",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:identity-query-1",
                    "session-identity-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Personality influence was recorded before bridge execution.",
                    "2026-04-11T12:00:00Z",
                    json.dumps(
                        {
                            "detected_profile_fact_query": {
                                "fact_name": "profile_identity_summary",
                                "label": "identity summary",
                                "predicate_prefix": "profile.",
                                "query_kind": "identity_summary",
                                "message_text": "Give me a full profile summary with my latest location too.",
                            }
                        }
                    ),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=20,
        )

        self.assertEqual(len(payload["conversations"]), 1)
        conversation = payload["conversations"][0]
        self.assertEqual(len(conversation["turns"]), 1)
        self.assertEqual(
            conversation["turns"][0]["content"],
            "Give me a full profile summary with my latest location too.",
        )

    def test_shadow_replay_delete_probes_skip_current_and_target_pre_delete_history(self) -> None:
        with self.state_db.connect() as conn:
            events = [
                (
                    "evt-user-1",
                    "intent_committed",
                    "run-1",
                    "turn-1",
                    "session-delete",
                    "2026-03-27T10:00:00Z",
                    {"message_text": "My cat is named Nova."},
                ),
                (
                    "evt-user-2",
                    "intent_committed",
                    "run-2",
                    "turn-2",
                    "session-delete",
                    "2026-03-27T10:05:00Z",
                    {"message_text": "I do not have that cat anymore."},
                ),
            ]
            for event_id, event_type, run_id, request_id, session_id, created_at, facts in events:
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                        session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_type,
                        "fact",
                        "spark_intelligence_builder",
                        "telegram_runtime",
                        run_id,
                        request_id,
                        "telegram",
                        session_id,
                        "human:test",
                        "agent:test",
                        "telegram_runtime",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Turn committed.",
                        created_at,
                        json.dumps(facts),
                    ),
                )
            for event_id, request_id, created_at, facts in (
                (
                    "evt-mem-req-1",
                    "turn-1",
                    "2026-03-27T10:00:01Z",
                    {
                        "observations": [
                            {
                                "subject": "human:test",
                                "predicate": "profile.pet_name",
                                "value": "Nova",
                                "operation": "update",
                                "memory_role": "current_state",
                            }
                        ]
                    },
                ),
                (
                    "evt-mem-req-2",
                    "turn-2",
                    "2026-03-27T10:05:01Z",
                    {
                        "observations": [
                            {
                                "subject": "human:test",
                                "predicate": "profile.pet_name",
                                "value": None,
                                "operation": "delete",
                                "memory_role": "current_state",
                            }
                        ]
                    },
                ),
            ):
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                        actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        "memory_write_requested",
                        "fact",
                        "spark_intelligence_builder",
                        "memory_orchestrator",
                        request_id,
                        "session-delete",
                        "human:test",
                        "personality_loader",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Memory write requested.",
                        created_at,
                        json.dumps(facts),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                        actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{event_id}:ok",
                        "memory_write_succeeded",
                        "fact",
                        "spark_intelligence_builder",
                        "memory_orchestrator",
                        request_id,
                        "session-delete",
                        "human:test",
                        "personality_loader",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Memory write succeeded.",
                        created_at,
                        json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                    ),
                )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        conversation = payload["conversations"][0]
        probes = conversation["probes"]
        current_state_probes = [probe for probe in probes if probe["probe_type"] == "current_state"]
        evidence_probes = [probe for probe in probes if probe["probe_type"] == "evidence"]
        historical_probes = [probe for probe in probes if probe["probe_type"] == "historical_state"]

        self.assertEqual(len(current_state_probes), 0)
        self.assertEqual(len(evidence_probes), 2)
        self.assertEqual(len(historical_probes), 0)

    def test_shadow_replay_event_memory_omits_observation_probes(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-event",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-event",
                    "turn-event",
                    "telegram",
                    "session-event",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T11:00:00Z",
                    json.dumps({"message_text": "I booked a dentist appointment for April 4 at 3pm."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-event",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-event",
                    "session-event",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T11:00:01Z",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "calendar.dentist_visit.at",
                                    "value": "2026-04-04T15:00:00+04:00",
                                    "operation": "event",
                                    "memory_role": "event",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-event",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-event",
                    "session-event",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T11:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        conversation = payload["conversations"][0]
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_kind"], "event")
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_role"], "event")
        self.assertNotIn("probes", conversation)

    def test_shadow_replay_omits_invalid_memory_role_observations(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-invalid-role",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-invalid-role",
                    "turn-invalid-role",
                    "telegram",
                    "session-invalid-role",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T12:00:00Z",
                    json.dumps({"message_text": "Remember this."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-invalid-role",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-invalid-role",
                    "session-invalid-role",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T12:00:01Z",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "profile.city",
                                    "value": "Dubai",
                                    "operation": "update",
                                    "memory_role": "timeline",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-invalid-role",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-invalid-role",
                    "session-invalid-role",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T12:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        conversation = payload["conversations"][0]
        self.assertNotIn("memory_kind", conversation["turns"][0].get("metadata") or {})
        self.assertNotIn("probes", conversation)

    def test_shadow_replay_batch_export_runs_validation_and_batch_report(self) -> None:
        with self.state_db.connect() as conn:
            for index in range(1, 4):
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                        session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"evt-user-{index}",
                        "intent_committed",
                        "fact",
                        "spark_intelligence_builder",
                        "telegram_runtime",
                        f"run-{index}",
                        f"turn-{index}",
                        "telegram",
                        f"session-{index}",
                        "human:test",
                        "agent:test",
                        "telegram_runtime",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Turn committed.",
                        json.dumps({"message_text": f"Turn {index}"}),
                    ),
                )
            conn.commit()

        with patch(
            "spark_intelligence.memory.shadow_replay.run_governed_command",
            side_effect=[
                SimpleNamespace(exit_code=0, stdout=json.dumps({"valid": True, "errors": [], "warnings": []}), stderr=""),
                SimpleNamespace(
                    exit_code=0,
                    stdout=json.dumps({"report": {"run_count": 3, "summary": {"accepted_writes": 0, "rejected_writes": 0, "skipped_turns": 3}}}),
                    stderr="",
                ),
            ],
        ) as governed:
            result = export_shadow_replay_batch(
                config_manager=self.config_manager,
                state_db=self.state_db,
                conversation_limit=3,
                conversations_per_file=2,
                validate=True,
                run_report=True,
                report_write_path=self.home / "artifacts" / "spark-shadow-batch-report.json",
            )

        self.assertEqual(len(result.files), 2)
        self.assertTrue(result.validation["valid"])
        self.assertEqual(result.report["report"]["run_count"], 3)
        self.assertEqual(governed.call_count, 2)

    def test_sdk_maintenance_payload_uses_only_accepted_explicit_memory_writes(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-maint-1",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "turn-maint-1",
                    "session-maint",
                    "human:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T10:00:00Z",
                    json.dumps({"message_text": "I moved to Dubai."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-maint-1",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-1",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T10:00:01Z",
                    json.dumps(
                        {
                            "operation": "update",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "profile.city",
                                    "value": "Dubai",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-maint-1",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-1",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T10:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-maint-2",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-2",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T10:05:00Z",
                    json.dumps(
                        {
                            "operation": "update",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "profile.city",
                                    "value": "Abu Dhabi",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-maint-2",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-2",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T10:05:01Z",
                    json.dumps({"accepted_count": 0, "rejected_count": 1, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_sdk_maintenance_payload(
            state_db=self.state_db,
            event_limit=100,
        )

        self.assertEqual(len(payload["writes"]), 1)
        write = payload["writes"][0]
        self.assertEqual(write["write_kind"], "observation")
        self.assertEqual(write["text"], "I moved to Dubai.")
        self.assertEqual(write["subject"], "human:test")
        self.assertEqual(write["predicate"], "profile.city")
        self.assertEqual(write["value"], "Dubai")
        self.assertEqual(payload["checks"]["current_state"][0]["predicate"], "profile.city")
        self.assertNotIn("historical_state", payload["checks"])
