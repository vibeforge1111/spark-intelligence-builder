from pathlib import Path
from unittest.mock import patch

from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class TelegramGenericMemoryTests(SparkTestCase):
    def test_build_researcher_reply_persists_generic_relationship_memory_before_provider_resolution(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory observations"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-memory-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-memory-update",
                channel_kind="telegram",
                user_message="My cofounder is Omar.",
            )

        self.assertEqual(result.reply_text, "I'll remember that your cofounder is Omar.")
        self.assertEqual(result.mode, "memory_generic_observation_update")
        self.assertEqual(result.routing_decision, "memory_generic_observation")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.cofounder_name")
        self.assertEqual(recorded_observations[0]["value"], "Omar")

    def test_build_researcher_reply_persists_generic_plan_memory_before_provider_resolution(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory observations"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-plan-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-plan-update",
                channel_kind="telegram",
                user_message="We plan to launch Atlas in enterprise first.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current plan is to launch Atlas in enterprise first.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_plan")
        self.assertEqual(recorded_observations[0]["value"], "launch Atlas in enterprise first")

    def test_build_researcher_reply_persists_generic_focus_memory_with_correction_prefix(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory observations"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-focus-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-focus-update",
                channel_kind="telegram",
                user_message="Actually, our priority is fixing onboarding retention.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current focus is fixing onboarding retention.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_focus")
        self.assertEqual(recorded_observations[0]["value"], "fixing onboarding retention")

    def test_build_researcher_reply_persists_generic_manager_relationship_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory observations"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-manager-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-manager-update",
                channel_kind="telegram",
                user_message="My manager is Leila.",
            )

        self.assertEqual(result.reply_text, "I'll remember that your manager is Leila.")
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.manager_name")
        self.assertEqual(recorded_observations[0]["value"], "Leila")

    def test_build_researcher_reply_does_not_persist_hypothetical_generic_memory_text(self) -> None:
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
                "trace_path": "trace:hypothetical-generic-memory-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
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
                request_id="req-hypothetical-generic-memory",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-hypothetical-generic-memory",
                channel_kind="telegram",
                user_message="Maybe my cofounder is Omar.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertFalse(write_events)
