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

    def test_build_researcher_reply_persists_generic_decision_memory(self) -> None:
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
                request_id="req-generic-decision-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-decision-update",
                channel_kind="telegram",
                user_message="We decided to launch Atlas through agency partners first.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current decision is launch Atlas through agency partners first.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_decision")
        self.assertEqual(recorded_observations[0]["value"], "launch Atlas through agency partners first")

    def test_build_researcher_reply_persists_generic_blocker_memory(self) -> None:
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
                request_id="req-generic-blocker-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-blocker-update",
                channel_kind="telegram",
                user_message="We're blocked on onboarding instrumentation.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current blocker is onboarding instrumentation.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_blocker")
        self.assertEqual(recorded_observations[0]["value"], "onboarding instrumentation")

    def test_build_researcher_reply_persists_generic_status_memory(self) -> None:
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
                request_id="req-generic-status-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-status-update",
                channel_kind="telegram",
                user_message="Status update: private beta is live with 14 design partners.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current status is private beta is live with 14 design partners.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_status")
        self.assertEqual(recorded_observations[0]["value"], "private beta is live with 14 design partners")

    def test_build_researcher_reply_persists_generic_commitment_memory(self) -> None:
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
                request_id="req-generic-commitment-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-commitment-update",
                channel_kind="telegram",
                user_message="We committed to closing the pilot by June 1.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current commitment is to closing the pilot by June 1.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_commitment")
        self.assertEqual(recorded_observations[0]["value"], "closing the pilot by June 1")

    def test_build_researcher_reply_persists_generic_milestone_memory(self) -> None:
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
                request_id="req-generic-milestone-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-milestone-update",
                channel_kind="telegram",
                user_message="Our next milestone is activation above 50 weekly teams.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current milestone is activation above 50 weekly teams.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_milestone")
        self.assertEqual(recorded_observations[0]["value"], "activation above 50 weekly teams")

    def test_build_researcher_reply_persists_generic_risk_memory(self) -> None:
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
                request_id="req-generic-risk-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-risk-update",
                channel_kind="telegram",
                user_message="Our main risk is enterprise churn during onboarding.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current risk is enterprise churn during onboarding.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_risk")
        self.assertEqual(recorded_observations[0]["value"], "enterprise churn during onboarding")

    def test_build_researcher_reply_persists_generic_dependency_memory(self) -> None:
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
                request_id="req-generic-dependency-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-dependency-update",
                channel_kind="telegram",
                user_message="Our dependency is Stripe approval.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current dependency is Stripe approval.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_dependency")
        self.assertEqual(recorded_observations[0]["value"], "Stripe approval")

    def test_build_researcher_reply_persists_generic_constraint_memory(self) -> None:
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
                request_id="req-generic-constraint-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-constraint-update",
                channel_kind="telegram",
                user_message="Our constraint is limited founder bandwidth.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current constraint is limited founder bandwidth.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_constraint")
        self.assertEqual(recorded_observations[0]["value"], "limited founder bandwidth")

    def test_build_researcher_reply_persists_generic_assumption_memory(self) -> None:
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
                request_id="req-generic-assumption-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-assumption-update",
                channel_kind="telegram",
                user_message="Our assumption is users will self-serve after onboarding.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current assumption is users will self-serve after onboarding.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_assumption")
        self.assertEqual(recorded_observations[0]["value"], "users will self-serve after onboarding")

    def test_build_researcher_reply_persists_generic_owner_memory(self) -> None:
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
                request_id="req-generic-owner-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-owner-update",
                channel_kind="telegram",
                user_message="Our owner is Omar.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current owner is Omar.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_owner")
        self.assertEqual(recorded_observations[0]["value"], "Omar")

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

    def test_build_researcher_reply_answers_generic_relationship_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-memory-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-memory-write-query-seed",
            channel_kind="telegram",
            user_message="My cofounder is Omar.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-memory-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-memory-query",
                channel_kind="telegram",
                user_message="Who is my cofounder?",
            )

        self.assertEqual(result.reply_text, "Your cofounder is Omar.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_plan_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-plan-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-plan-write-query-seed",
            channel_kind="telegram",
            user_message="We plan to launch Atlas in enterprise first.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-plan-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-plan-query",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )

        self.assertEqual(result.reply_text, "Your current plan is to launch Atlas in enterprise first.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_focus_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-focus-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-focus-write-query-seed",
            channel_kind="telegram",
            user_message="Actually, our priority is fixing onboarding retention.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-focus-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-focus-query",
                channel_kind="telegram",
                user_message="What is our priority?",
            )

        self.assertEqual(result.reply_text, "Your current focus is fixing onboarding retention.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_decision_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-write-query-seed",
            channel_kind="telegram",
            user_message="We decided to launch Atlas through agency partners first.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-decision-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-decision-query",
                channel_kind="telegram",
                user_message="What did we decide?",
            )

        self.assertEqual(
            result.reply_text,
            "Your current decision is launch Atlas through agency partners first.",
        )
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_blocker_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-write-query-seed",
            channel_kind="telegram",
            user_message="We're blocked on onboarding instrumentation.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-blocker-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-blocker-query",
                channel_kind="telegram",
                user_message="What are we blocked on?",
            )

        self.assertEqual(
            result.reply_text,
            "Your current blocker is onboarding instrumentation.",
        )
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_status_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-write-query-seed",
            channel_kind="telegram",
            user_message="Status update: private beta is live with 14 design partners.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-status-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-status-query",
                channel_kind="telegram",
                user_message="What is the project status?",
            )

        self.assertEqual(
            result.reply_text,
            "Your current status is private beta is live with 14 design partners.",
        )
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_commitment_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-write-query-seed",
            channel_kind="telegram",
            user_message="We committed to closing the pilot by June 1.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-commitment-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-commitment-query",
                channel_kind="telegram",
                user_message="What is our commitment?",
            )

        self.assertEqual(
            result.reply_text,
            "Your current commitment is to closing the pilot by June 1.",
        )
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_milestone_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-write-query-seed",
            channel_kind="telegram",
            user_message="Our next milestone is activation above 50 weekly teams.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-milestone-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-milestone-query",
                channel_kind="telegram",
                user_message="What is our milestone?",
            )

        self.assertEqual(
            result.reply_text,
            "Your current milestone is activation above 50 weekly teams.",
        )
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_risk_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-write-query-seed",
            channel_kind="telegram",
            user_message="Our main risk is enterprise churn during onboarding.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-risk-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-risk-query",
                channel_kind="telegram",
                user_message="What is our risk?",
            )

        self.assertEqual(result.reply_text, "Your current risk is enterprise churn during onboarding.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_dependency_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-write-query-seed",
            channel_kind="telegram",
            user_message="Our dependency is Stripe approval.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-dependency-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-dependency-query",
                channel_kind="telegram",
                user_message="What is our dependency?",
            )

        self.assertEqual(result.reply_text, "Your current dependency is Stripe approval.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_constraint_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-write-query-seed",
            channel_kind="telegram",
            user_message="Our constraint is limited founder bandwidth.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-constraint-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-constraint-query",
                channel_kind="telegram",
                user_message="What is our constraint?",
            )

        self.assertEqual(result.reply_text, "Your current constraint is limited founder bandwidth.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_assumption_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-write-query-seed",
            channel_kind="telegram",
            user_message="Our assumption is users will self-serve after onboarding.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-assumption-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-assumption-query",
                channel_kind="telegram",
                user_message="What is our assumption?",
            )

        self.assertEqual(result.reply_text, "Your current assumption is users will self-serve after onboarding.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_generic_owner_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-write-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-write-query-seed",
            channel_kind="telegram",
            user_message="Our owner is Omar.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic memory queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic memory queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-owner-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-owner-query",
                channel_kind="telegram",
                user_message="Who is the owner?",
            )

        self.assertEqual(result.reply_text, "Your current owner is Omar.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_preserves_generic_relationship_history_across_overwrites(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-history",
            channel_kind="telegram",
            user_message="My cofounder is Omar.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-history",
            channel_kind="telegram",
            user_message="Actually, my cofounder is Sara.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-history",
            channel_kind="telegram",
            user_message="Who is my cofounder?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-history",
            channel_kind="telegram",
            user_message="Who was my cofounder before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-history",
            channel_kind="telegram",
            user_message="Show my cofounder history.",
        )

        self.assertEqual(current_result.reply_text, "Your cofounder is Sara.")
        self.assertEqual(history_result.reply_text, "Before Sara, your cofounder was Omar.")
        self.assertEqual(event_history_result.reply_text, "I have 2 saved cofounder events: Omar then Sara.")
        self.assertEqual(history_result.mode, "memory_profile_fact_history")
        self.assertEqual(event_history_result.mode, "memory_profile_event_history")

    def test_build_researcher_reply_deletes_generic_relationship_memory_but_keeps_history(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-delete-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-delete",
            channel_kind="telegram",
            user_message="My cofounder is Omar.",
        )

        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-delete",
            channel_kind="telegram",
            user_message="Forget my cofounder.",
        )
        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-delete",
            channel_kind="telegram",
            user_message="Who is my cofounder?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-delete",
            channel_kind="telegram",
            user_message="Who was my cofounder before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-cofounder-event-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-cofounder-delete",
            channel_kind="telegram",
            user_message="Show my cofounder history.",
        )

        self.assertEqual(delete_result.reply_text, "I'll forget your cofounder.")
        self.assertEqual(delete_result.mode, "memory_generic_observation_delete")
        self.assertEqual(current_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(history_result.reply_text, "An earlier saved cofounder was Omar.")
        self.assertEqual(event_history_result.reply_text, "I only have one saved cofounder event: Omar.")

    def test_build_researcher_reply_preserves_generic_decision_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="We decided to launch Atlas through agency partners first.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="Update: we're going with self-serve onboarding first.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="What did we decide?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="What did we decide before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="Show our decision history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="Forget our decision.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="What did we decide?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-decision-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-decision-history",
            channel_kind="telegram",
            user_message="What did we decide before?",
        )

        self.assertEqual(
            current_result.reply_text,
            "Your current decision is self-serve onboarding first.",
        )
        self.assertEqual(
            history_result.reply_text,
            "Before your current decision was self-serve onboarding first, it was launch Atlas through agency partners first.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current decision events: launch Atlas through agency partners first then self-serve onboarding first.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current decision.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current decision was self-serve onboarding first.",
        )

    def test_build_researcher_reply_preserves_generic_blocker_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="We're blocked on onboarding instrumentation.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="Our bottleneck is enterprise lead volume.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="What are we blocked on?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="What were we blocked on before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="Show our blocker history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="Forget our blocker.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="What are we blocked on?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-blocker-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-blocker-history",
            channel_kind="telegram",
            user_message="What were we blocked on before?",
        )

        self.assertEqual(
            current_result.reply_text,
            "Your current blocker is enterprise lead volume.",
        )
        self.assertEqual(
            history_result.reply_text,
            "Before your current blocker was enterprise lead volume, it was onboarding instrumentation.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current blocker events: onboarding instrumentation then enterprise lead volume.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current blocker.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current blocker was enterprise lead volume.",
        )

    def test_build_researcher_reply_preserves_generic_status_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="Status update: private beta is live with 14 design partners.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="Project status is onboarding activation is above 40 percent.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="What is the project status?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="What was the project status before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="Show our status history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="Forget our status.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="What is the project status?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-status-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-status-history",
            channel_kind="telegram",
            user_message="What was the project status before?",
        )

        self.assertEqual(
            current_result.reply_text,
            "Your current status is onboarding activation is above 40 percent.",
        )
        self.assertEqual(
            history_result.reply_text,
            "Before your current status was onboarding activation is above 40 percent, it was private beta is live with 14 design partners.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current status events: private beta is live with 14 design partners then onboarding activation is above 40 percent.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current status.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current status was onboarding activation is above 40 percent.",
        )

    def test_build_researcher_reply_preserves_generic_commitment_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="We committed to closing the pilot by June 1.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="Update: our commitment is to close the pilot by June 10.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="What did we commit to?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="What did we commit to before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="Show our commitment history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="Forget our commitment.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="What is our commitment?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-commitment-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-commitment-history",
            channel_kind="telegram",
            user_message="What did we commit to before?",
        )

        self.assertEqual(
            current_result.reply_text,
            "Your current commitment is to close the pilot by June 10.",
        )
        self.assertEqual(
            history_result.reply_text,
            "Before your current commitment was to close the pilot by June 10, it was to closing the pilot by June 1.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current commitment events: closing the pilot by June 1 then close the pilot by June 10.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current commitment.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current commitment was to close the pilot by June 10.",
        )

    def test_build_researcher_reply_preserves_generic_milestone_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="Our next milestone is activation above 50 weekly teams.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="The current milestone is 10 enterprise design partners live.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="What is our milestone?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="What was the milestone before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="Show our milestone history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="Forget our milestone.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="What is our milestone?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-milestone-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-milestone-history",
            channel_kind="telegram",
            user_message="What was the milestone before?",
        )

        self.assertEqual(
            current_result.reply_text,
            "Your current milestone is 10 enterprise design partners live.",
        )
        self.assertEqual(
            history_result.reply_text,
            "Before your current milestone was 10 enterprise design partners live, it was activation above 50 weekly teams.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current milestone events: activation above 50 weekly teams then 10 enterprise design partners live.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current milestone.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current milestone was 10 enterprise design partners live.",
        )

    def test_build_researcher_reply_preserves_generic_risk_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="Our main risk is enterprise churn during onboarding.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="The biggest risk is delayed product instrumentation.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="What is our risk?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="What was the risk before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="Show our risk history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="Forget our risk.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="What is our risk?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-risk-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-risk-history",
            channel_kind="telegram",
            user_message="What was the risk before?",
        )

        self.assertEqual(current_result.reply_text, "Your current risk is delayed product instrumentation.")
        self.assertEqual(
            history_result.reply_text,
            "Before your current risk was delayed product instrumentation, it was enterprise churn during onboarding.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current risk events: enterprise churn during onboarding then delayed product instrumentation.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current risk.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current risk was delayed product instrumentation.",
        )

    def test_build_researcher_reply_preserves_generic_dependency_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="Our dependency is Stripe approval.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="The current dependency is partner API access.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="What is our dependency?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="What was the dependency before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="Show our dependency history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="Forget our dependency.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="What is our dependency?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-dependency-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-dependency-history",
            channel_kind="telegram",
            user_message="What was the dependency before?",
        )

        self.assertEqual(current_result.reply_text, "Your current dependency is partner API access.")
        self.assertEqual(
            history_result.reply_text,
            "Before your current dependency was partner API access, it was Stripe approval.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current dependency events: Stripe approval then partner API access.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current dependency.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current dependency was partner API access.",
        )

    def test_build_researcher_reply_preserves_generic_constraint_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="Our constraint is limited founder bandwidth.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="The current constraint is budget for only one engineer.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="What is our constraint?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="What was the constraint before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="Show our constraint history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="Forget our constraint.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="What is our constraint?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-constraint-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-constraint-history",
            channel_kind="telegram",
            user_message="What was the constraint before?",
        )

        self.assertEqual(current_result.reply_text, "Your current constraint is budget for only one engineer.")
        self.assertEqual(
            history_result.reply_text,
            "Before your current constraint was budget for only one engineer, it was limited founder bandwidth.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current constraint events: limited founder bandwidth then budget for only one engineer.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current constraint.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current constraint was budget for only one engineer.",
        )

    def test_build_researcher_reply_preserves_generic_assumption_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="Our assumption is users will self-serve after onboarding.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="The current assumption is enterprise teams need hands-on setup first.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="What is our assumption?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="What was the assumption before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="Show our assumption history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="Forget our assumption.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="What is our assumption?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-assumption-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-assumption-history",
            channel_kind="telegram",
            user_message="What was the assumption before?",
        )

        self.assertEqual(
            current_result.reply_text,
            "Your current assumption is enterprise teams need hands-on setup first.",
        )
        self.assertEqual(
            history_result.reply_text,
            "Before your current assumption was enterprise teams need hands-on setup first, it was users will self-serve after onboarding.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current assumption events: users will self-serve after onboarding then enterprise teams need hands-on setup first.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current assumption.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current assumption was enterprise teams need hands-on setup first.",
        )

    def test_build_researcher_reply_preserves_generic_owner_history_and_delete_lifecycle(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-history-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="Our owner is Omar.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-history-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="The current owner is Sara.",
        )

        current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="Who is the owner?",
        )
        history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="What was the owner before?",
        )
        event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-event-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="Show our owner history.",
        )
        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="Forget our owner.",
        )
        current_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-current-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="Who is the owner?",
        )
        history_after_delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-owner-history-after-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-owner-history",
            channel_kind="telegram",
            user_message="What was the owner before?",
        )

        self.assertEqual(current_result.reply_text, "Your current owner is Sara.")
        self.assertEqual(
            history_result.reply_text,
            "Before your current owner was Sara, it was Omar.",
        )
        self.assertEqual(
            event_history_result.reply_text,
            "I have 2 saved current owner events: Omar then Sara.",
        )
        self.assertEqual(delete_result.reply_text, "I'll forget your current owner.")
        self.assertEqual(current_after_delete_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(
            history_after_delete_result.reply_text,
            "An earlier saved current owner was Sara.",
        )

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
