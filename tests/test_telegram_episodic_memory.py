from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from spark_intelligence.memory import write_telegram_event_to_memory
from spark_intelligence.observability.store import latest_events_by_type, record_event
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

    def test_build_researcher_reply_does_not_persist_hypothetical_event_text(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:hypothetical-event-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None, **kwargs):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-hypothetical-event",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-hypothetical-event",
                channel_kind="telegram",
                user_message="Maybe my meeting with Omar is on May 3.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertFalse(write_events)

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

    def test_build_researcher_reply_answers_latest_flight_query_from_consolidated_state(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        write_telegram_event_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="telegram.event.flight",
            value="flight to London on May 6",
            evidence_text="My flight to London is on May 6.",
            event_name="telegram_event_flight",
            session_id="session-flight-query-write",
            turn_id="turn-flight-query-write",
            channel_kind="telegram",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for latest Telegram event replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for latest Telegram event replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-flight-latest-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-flight-latest-query",
                channel_kind="telegram",
                user_message="What flight do I have?",
            )

        self.assertEqual(result.reply_text, "Your latest saved flight is flight to London on May 6.")
        self.assertEqual(result.mode, "memory_telegram_event_latest")
        self.assertEqual(result.routing_decision, "memory_telegram_event_latest_query")

    def test_build_researcher_reply_answers_daily_episodic_recall_from_event_ledger(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark saved GTM launch work.",
            session_id="session-daily-episodic-source",
            human_id="human-1",
            facts={
                "repo": "spark-memory-quality-dashboard",
                "artifact_path": "docs/TODAY_MEMORY_IMPROVEMENT_LEDGER_2026-05-01.md",
                "observations": [
                    {
                        "predicate": "entity.decision",
                        "value": "GTM launch decision is founder-led onboarding",
                    },
                    {
                        "predicate": "entity.next_action",
                        "value": "wire source-aware episodic recall",
                    },
                ],
            },
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for daily episodic recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for daily episodic recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-daily-episodic",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-daily-episodic-query",
                channel_kind="telegram",
                user_message="What changed today?",
            )

        self.assertEqual(result.mode, "memory_episodic_daily_recall")
        self.assertEqual(result.routing_decision, "memory_episodic_daily_recall")
        self.assertIn("From today's episodic memory:", result.reply_text)
        self.assertIn("GTM launch decision is founder-led onboarding", result.reply_text)
        self.assertIn("Source: daily event ledger rollup", result.reply_text)
        self.assertIn("status=memory_episodic_daily_recall", result.evidence_summary)

    def test_daily_episodic_recall_source_explanation_names_route(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark saved a memory dashboard next action.",
            session_id="session-daily-source-explanation-source",
            human_id="human-1",
            facts={
                "observations": [
                    {
                        "predicate": "entity.next_action",
                        "value": "add accepted-memory visibility",
                    }
                ],
            },
        )

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-daily-source-first",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-daily-source-explanation",
            channel_kind="telegram",
            user_message="What else do you remember today?",
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-daily-source-why",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-daily-source-explanation",
            channel_kind="telegram",
            user_message="Why did you answer that?",
        )

        self.assertIn("daily episodic memory route", result.reply_text)
        self.assertIn("daily event ledger rollup", result.reply_text)
        self.assertIn("memory_episodic_daily_recall", result.reply_text)
