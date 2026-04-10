from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.memory import orchestrator as memory_orchestrator
from spark_intelligence.memory import (
    build_sdk_maintenance_payload,
    build_shadow_replay_payload,
    export_shadow_replay_batch,
    inspect_human_memory_in_memory,
    lookup_current_state_in_memory,
    run_memory_sdk_smoke_test,
    write_profile_fact_to_memory,
)
from spark_intelligence.memory.profile_facts import detect_profile_fact_observation
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
        self.assertEqual(call["subject"], "human:human:test")
        self.assertEqual(call["predicate"], "profile.city")
        self.assertEqual(call["value"], "Dubai")
        self.assertEqual(call["text"], "I moved to Dubai.")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.city")
        self.assertEqual(observations[0]["value"], "Dubai")

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
        self.assertEqual(panel["counts"]["read_requests"], 1)
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
                subject="human:human:test",
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
                                    "subject": "human:human:test",
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
                                    "subject": "human:human:test",
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
        self.assertEqual(conversation["turns"][0]["metadata"]["subject"], "human:human:test")
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
                                "subject": "human:human:test",
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
                                "subject": "human:human:test",
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
                                    "subject": "human:human:test",
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
                                    "subject": "human:human:test",
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
                                    "subject": "human:human:test",
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
                                    "subject": "human:human:test",
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
        self.assertEqual(write["subject"], "human:human:test")
        self.assertEqual(write["predicate"], "profile.city")
        self.assertEqual(write["value"], "Dubai")
        self.assertEqual(payload["checks"]["current_state"][0]["predicate"], "profile.city")
        self.assertNotIn("historical_state", payload["checks"])
