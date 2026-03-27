from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.memory import (
    build_sdk_maintenance_payload,
    build_shadow_replay_payload,
    export_shadow_replay_batch,
)
from spark_intelligence.observability.store import build_watchtower_snapshot, latest_events_by_type
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
        self.assertEqual(conversation["turns"][1]["role"], "assistant")
        self.assertEqual(conversation["turns"][1]["content"], "Noted. I will be more direct.")
        probe_types = {probe["probe_type"] for probe in conversation["probes"]}
        self.assertIn("current_state", probe_types)
        self.assertIn("evidence", probe_types)
        self.assertIn("historical_state", probe_types)

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
