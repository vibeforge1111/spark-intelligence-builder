from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory.generic_observations import (
    assess_telegram_generic_memory_candidate,
    classify_telegram_generic_memory_candidate,
    detect_telegram_generic_deletions,
)
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit
from spark_intelligence.memory.doctor import run_memory_doctor
from spark_intelligence.memory.orchestrator import write_raw_episode_to_memory
from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.observability.store import (
    latest_events_by_type,
    record_event,
    recent_memory_lane_records,
    recent_policy_gate_records,
)
from spark_intelligence.researcher_bridge.advisory import (
    OpenMemoryRecallQuery,
    ResearcherProviderSelection,
    _build_open_memory_recall_answer,
    _filter_open_memory_recall_records,
    build_researcher_reply,
)

from tests.test_support import SparkTestCase


class TelegramGenericMemoryTests(SparkTestCase):
    def test_open_recall_keeps_metadata_role_entity_current_state_records(self) -> None:
        records = [
            {
                "observation_id": "telegram:plant-location:1",
                "subject": "human:telegram:8319079055",
                "predicate": "entity.location",
                "text": "human:telegram:8319079055 entity.location the windowsill",
                "metadata": {
                    "memory_role": "current_state",
                    "entity_type": "named_object",
                    "entity_key": "named-object:tiny-desk-plant",
                    "entity_label": "tiny desk plant",
                    "entity_attribute": "location",
                    "location_preposition": "on",
                    "value": "the windowsill",
                },
            }
        ]

        filtered = _filter_open_memory_recall_records(records)
        reply = _build_open_memory_recall_answer(
            query=OpenMemoryRecallQuery(topic="the desk plant", query_kind="location_recall"),
            records=filtered,
        )

        self.assertEqual(filtered, records)
        self.assertEqual(reply, "The tiny desk plant is on the windowsill.")

    def test_build_researcher_reply_persists_generic_relationship_memory_before_provider_resolution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="openai_compatible",
            auth_profile_id="default",
            auth_method="api_key",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.example.invalid/v1",
            default_model="test-model",
            secret_ref=None,
            secret_value="test-secret",
            source="test",
        )

        def fake_execute_direct_provider_prompt(*, user_prompt, **kwargs):
            self.assertIn("[Memory write this turn]", user_prompt)
            self.assertIn("cofounder", user_prompt)
            self.assertIn("My cofounder is Omar.", user_prompt)
            return {"raw_response": "Omar is the cofounder. I will use that as context going forward."}

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=ResearcherProviderSelection(provider=provider, model_family="generic"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_execute_direct_provider_prompt,
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

        self.assertIn("Omar", result.reply_text)
        self.assertNotIn("I'll remember", result.reply_text)
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.routing_decision, "provider_fallback_chat")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.cofounder_name")
        self.assertEqual(recorded_observations[0]["value"], "Omar")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("routing_decision") == "provider_fallback_chat"
            ),
            {},
        )
        self.assertEqual(facts.get("bridge_mode"), "external_configured")
        self.assertEqual(facts.get("routing_decision"), "provider_fallback_chat")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        facts = (influence_events[0]["facts_json"] or {}).get("detected_generic_memory_observation") or {}
        self.assertEqual(facts.get("predicate"), "profile.cofounder_name")

    def test_build_researcher_reply_persists_recent_family_shared_time_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for family shared-time observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for family shared-time observations"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-family-shared-time-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-family-shared-time-update",
                channel_kind="telegram",
                user_message="My mom came over yesterday and I spent time with my sister at the park.",
            )

        self.assertEqual(result.reply_text, "I'll remember you recently spent time with: mother, sister.")
        self.assertEqual(result.mode, "memory_generic_observation_update")
        self.assertEqual(result.routing_decision, "memory_generic_observation")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.recent_family_members")
        self.assertEqual(recorded_observations[0]["value"], "mother, sister")

    def test_build_researcher_reply_answers_recent_family_shared_time_query_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-family-shared-time-query-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-family-shared-time-query-seed",
            channel_kind="telegram",
            user_message="My mom came over yesterday and I spent time with my sister at the park.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for family shared-time queries"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for family shared-time queries"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-family-shared-time-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-family-shared-time-query",
                channel_kind="telegram",
                user_message="Which family members did I spend time with recently?",
            )

        self.assertEqual(result.reply_text, "You recently spent time with mother, sister.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")

    def test_build_researcher_reply_answers_plan_and_commitment_queries_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for plan or commitment memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for plan or commitment memory"),
        ):
            plan_update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-commitment",
                channel_kind="telegram",
                user_message="The plan is to run weekly Telegram memory probes.",
            )
            plan_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-commitment",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )
            commitment_update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-commitment-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-commitment",
                channel_kind="telegram",
                user_message="We committed to ship deletion memory checks today.",
            )
            commitment_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-commitment-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-commitment",
                channel_kind="telegram",
                user_message="What did we commit to?",
            )

        self.assertEqual(
            plan_update.reply_text,
            "I'll remember that your current plan is to run weekly Telegram memory probes.",
        )
        self.assertEqual(plan_update.mode, "memory_generic_observation_update")
        self.assertEqual(plan_query.reply_text, "Your current plan is to run weekly Telegram memory probes.")
        self.assertEqual(plan_query.mode, "memory_profile_fact")
        self.assertEqual(
            commitment_update.reply_text,
            "I'll remember that your current commitment is to ship deletion memory checks today.",
        )
        self.assertEqual(commitment_update.mode, "memory_generic_observation_update")
        self.assertEqual(
            commitment_query.reply_text,
            "Your current commitment is to ship deletion memory checks today.",
        )
        self.assertEqual(commitment_query.mode, "memory_profile_fact")

    def test_build_researcher_reply_saves_explicit_current_plan_memory_update(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for explicit plan memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for explicit plan memory"),
        ):
            plan_update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-explicit-plan-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-explicit-plan",
                channel_kind="telegram",
                user_message=(
                    "Memory update: my current plan is Neon Harbor Telegram memory test. "
                    "Please save this as my current plan."
                ),
            )
            plan_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-explicit-plan-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-explicit-plan",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )

        self.assertEqual(plan_update.mode, "memory_generic_observation_update")
        self.assertEqual(plan_update.routing_decision, "memory_generic_observation")
        self.assertEqual(
            plan_update.reply_text,
            "I'll remember that your current plan is Neon Harbor Telegram memory test.",
        )
        self.assertEqual(plan_query.mode, "memory_profile_fact")
        self.assertEqual(plan_query.reply_text, "Your current plan is Neon Harbor Telegram memory test.")

    def test_build_researcher_reply_saves_set_current_plan_command(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current plan transition"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current plan transition"),
        ):
            plan_update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-set-current-plan-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-set-current-plan",
                channel_kind="telegram",
                user_message="Set my current plan to evaluate open-ended persistent memory recall.",
            )
            plan_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-set-current-plan-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-set-current-plan",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )
            authority_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-set-current-plan-authority-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-set-current-plan",
                channel_kind="telegram",
                user_message="What is my current plan, and what older plan should not override it?",
            )

        self.assertEqual(plan_update.mode, "current_plan_transition")
        self.assertEqual(plan_update.routing_decision, "current_plan_transition")
        self.assertEqual(
            plan_update.reply_text,
            "Done. Your current plan is now: evaluate open-ended persistent memory recall.",
        )
        self.assertEqual(plan_query.mode, "memory_profile_fact")
        self.assertEqual(
            plan_query.reply_text,
            "Your current plan is to evaluate open-ended persistent memory recall.",
        )
        self.assertEqual(
            authority_query.reply_text,
            "Use the current plan: evaluate open-ended persistent memory recall.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=5)
        plan_event = next(event for event in tool_events if event["reason_code"] == "current_plan_transition")
        self.assertEqual(plan_event["facts_json"]["predicate"], "profile.current_plan")
        self.assertEqual(plan_event["facts_json"]["new_plan"], "evaluate open-ended persistent memory recall")

    def test_build_researcher_reply_answers_combined_current_focus_and_plan_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current focus and plan query"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current focus and plan query"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-set-current-focus-for-combined-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-current-focus-plan",
                channel_kind="telegram",
                user_message="Set my current focus to persistent memory quality evaluation.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-set-current-plan-for-combined-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-current-focus-plan",
                channel_kind="telegram",
                user_message="Set my current plan to evaluate open-ended persistent memory recall.",
            )
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-current-focus-plan-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-current-focus-plan",
                channel_kind="telegram",
                user_message="What is my current focus and plan?",
            )

        self.assertEqual(result.mode, "memory_current_focus_plan")
        self.assertEqual(result.routing_decision, "memory_current_focus_plan_query")
        self.assertEqual(
            result.reply_text,
            "Your current focus is persistent memory quality evaluation.\n"
            "Your current plan is to evaluate open-ended persistent memory recall.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=5)
        combined_event = next(event for event in tool_events if event["reason_code"] == "memory_current_focus_plan_query")
        self.assertEqual(combined_event["facts_json"]["current_focus"], "persistent memory quality evaluation")
        self.assertEqual(combined_event["facts_json"]["current_plan"], "evaluate open-ended persistent memory recall")
        self.assertEqual(combined_event["facts_json"]["focus_source_class"], "current_state")
        self.assertEqual(combined_event["facts_json"]["plan_source_class"], "current_state")

    def test_build_researcher_reply_answers_memory_authority_policy_without_provider(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for memory authority policy"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for memory authority policy"),
        ):
            mutable = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-authority-mutable",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-memory-authority",
                channel_kind="telegram",
                user_message="What outranks wiki or old conversation when you answer mutable facts about me?",
            )
            task_recovery = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-authority-task-recovery",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-memory-authority",
                channel_kind="telegram",
                user_message=(
                    "If task recovery or an older conversation points somewhere else, but current state says "
                    "my plan is evaluate open-ended persistent memory recall, what should guide your next answer?"
                ),
            )
            rejection_policy = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-authority-rejection-policy",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-memory-authority",
                channel_kind="telegram",
                user_message="What should you refuse to promote from this chat into durable memory?",
            )
            newest_correction = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-authority-newest-correction",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-memory-authority",
                channel_kind="telegram",
                user_message=(
                    "I just corrected a mutable fact in this message: my active memory test label is "
                    "Blue Lantern, not Sol. What should win if older memory says something else?"
                ),
            )
            wiki_conflict = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-authority-wiki-conflict",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-memory-authority",
                channel_kind="telegram",
                user_message=(
                    "If your wiki says Spark memory is fully finished but current state says the evaluation "
                    "is still open, how should you answer?"
                ),
            )
            vague_promotion = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-authority-vague-promotion",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-memory-authority",
                channel_kind="telegram",
                user_message="Promote this as durable memory: Spark is perfect now because this chat feels good.",
            )

        self.assertEqual(mutable.routing_decision, "memory_authority_policy")
        self.assertIn("newest explicit message", mutable.reply_text)
        self.assertIn("current-state memory wins", mutable.reply_text)
        self.assertIn("supporting only", mutable.reply_text)
        self.assertEqual(task_recovery.routing_decision, "memory_authority_policy")
        self.assertIn("Current state should guide", task_recovery.reply_text)
        self.assertIn("evaluate open-ended persistent memory recall", task_recovery.reply_text)
        self.assertEqual(rejection_policy.routing_decision, "memory_authority_policy")
        self.assertIn("conversational residue", rejection_policy.reply_text)
        self.assertIn("durable memory needs", rejection_policy.reply_text)
        self.assertEqual(newest_correction.routing_decision, "memory_authority_policy")
        self.assertIn("newest explicit message", newest_correction.reply_text)
        self.assertEqual(wiki_conflict.routing_decision, "memory_authority_policy")
        self.assertIn("current state", wiki_conflict.reply_text)
        self.assertIn("supporting_not_authoritative", wiki_conflict.reply_text)
        self.assertEqual(vague_promotion.routing_decision, "memory_authority_policy")
        self.assertIn("conversational residue", vague_promotion.reply_text)

    def test_build_researcher_reply_answers_natural_sol_purpose_recall_without_provider(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        write_raw_episode_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            episode_text=(
                "For later, in our memory work today, we used the tiny desk plant named Sol "
                "as a low-stakes episodic recall probe."
            ),
            domain_pack="memory_work",
            session_id="session-sol-purpose",
            turn_id="req-sol-purpose-seed",
            channel_kind="telegram",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for Sol purpose recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for Sol purpose recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-sol-purpose-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-sol-purpose",
                channel_kind="telegram",
                user_message="What exactly did we use Sol for earlier, and how confident are you?",
            )
            boundary_result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-work-boundary-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-sol-purpose",
                channel_kind="telegram",
                user_message="What do you remember about our memory work today, and what is current versus supporting context?",
            )

        self.assertEqual(result.routing_decision, "memory_open_recall_query")
        self.assertIn("Sol", result.reply_text)
        self.assertIn("low-stakes episodic recall probe", result.reply_text)
        self.assertIn("Confidence: medium-high", result.reply_text)
        self.assertEqual(boundary_result.routing_decision, "memory_open_recall_query")
        self.assertIn("supporting", boundary_result.reply_text.lower())
        self.assertIn("Sol", boundary_result.reply_text)

    def test_build_researcher_reply_handles_plan_correction_history_and_deletion(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for plan correction/deletion memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for plan correction/deletion memory"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-original",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-correction",
                channel_kind="telegram",
                user_message="The plan is to run weekly Telegram memory probes.",
            )
            correction = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-correction",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-correction",
                channel_kind="telegram",
                user_message="Actually, the plan is to run live Telegram deletion checks.",
            )
            current_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-current-after-correction",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-correction",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )
            history_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-history-after-correction",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-correction",
                channel_kind="telegram",
                user_message="What was my previous plan?",
            )
            deletion = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-delete",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-correction",
                channel_kind="telegram",
                user_message="Forget my current plan.",
            )
            post_delete_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-plan-after-delete",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-plan-correction",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )

        self.assertEqual(
            correction.reply_text,
            "I'll remember that your current plan is to run live Telegram deletion checks.",
        )
        self.assertEqual(current_query.reply_text, "Your current plan is to run live Telegram deletion checks.")
        self.assertEqual(
            history_query.reply_text,
            "Before your current plan was to run live Telegram deletion checks, it was to run weekly Telegram memory probes.",
        )
        self.assertEqual(deletion.reply_text, "I'll forget your current plan.")
        self.assertEqual(deletion.mode, "memory_generic_observation_delete")
        self.assertEqual(post_delete_query.reply_text, "I don't currently have that saved.")

    def test_build_researcher_reply_handles_preference_update_query_and_deletion(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for favorite preference memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for favorite preference memory"),
        ):
            update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-color-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-color",
                channel_kind="telegram",
                user_message="My favorite color is cobalt blue.",
            )
            query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-color-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-color",
                channel_kind="telegram",
                user_message="What is my favorite color?",
            )
            deletion = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-color-delete",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-color",
                channel_kind="telegram",
                user_message="Forget my favorite color.",
            )
            post_delete_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-color-after-delete",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-color",
                channel_kind="telegram",
                user_message="What is my favorite color?",
            )

        self.assertEqual(update.reply_text, "I'll remember that your favorite color is cobalt blue.")
        self.assertEqual(update.mode, "memory_generic_observation_update")
        self.assertEqual(update.routing_decision, "memory_generic_observation")
        self.assertEqual(query.reply_text, "Your favorite color is cobalt blue.")
        self.assertEqual(query.mode, "memory_profile_fact")
        self.assertEqual(deletion.reply_text, "I'll forget your favorite color.")
        self.assertEqual(deletion.mode, "memory_generic_observation_delete")
        self.assertEqual(post_delete_query.reply_text, "I don't currently have that saved.")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        facts = next(event["facts_json"] for event in tool_events if event.get("reason_code") == "memory_generic_observation")
        self.assertEqual(facts.get("domain_pack"), "preferences")
        self.assertEqual(facts.get("retention_class"), "durable_profile")

    def test_build_researcher_reply_handles_favorite_food_preference_phrase(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for favorite food memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for favorite food memory"),
        ):
            update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-food-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-food",
                channel_kind="telegram",
                user_message="The food I love the most is shakshuka.",
            )
            query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-food-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-food",
                channel_kind="telegram",
                user_message="What is my favorite food?",
            )
            deletion = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-favorite-food-delete",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-favorite-food",
                channel_kind="telegram",
                user_message="Forget my favorite food.",
            )

        self.assertEqual(update.reply_text, "I'll remember that your favorite food is shakshuka.")
        self.assertEqual(update.mode, "memory_generic_observation_update")
        self.assertEqual(query.reply_text, "Your favorite food is shakshuka.")
        self.assertEqual(query.mode, "memory_profile_fact")
        self.assertEqual(deletion.reply_text, "I'll forget your favorite food.")
        self.assertEqual(deletion.mode, "memory_generic_observation_delete")

    def test_build_researcher_reply_handles_explicit_remember_style_preference_directly(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for explicit preference memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for explicit preference memory"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-explicit-reply-style-preference",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-explicit-reply-style-preference",
                channel_kind="telegram",
                user_message="Please remember this: my preferred Spark reply style is concise but warm",
            )

        self.assertEqual(result.reply_text, "Saved that reply style preference.")
        self.assertEqual(result.mode, "personality_preference_update")
        self.assertEqual(result.routing_decision, "personality_preference_update")
        self.assertNotIn("Working Memory", result.reply_text)
        self.assertNotIn("Please remember this", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = tool_events[0]["facts_json"] or {}
        self.assertTrue(facts.get("explicit_memory_message"))
        self.assertEqual(
            facts.get("normalized_memory_message"),
            "my preferred Spark reply style is concise but warm",
        )

    def test_classify_telegram_generic_memory_candidate_assigns_project_state_metadata(self) -> None:
        candidate = classify_telegram_generic_memory_candidate(
            "Actually, the biggest risk is delayed product instrumentation."
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.predicate, "profile.current_risk")
        self.assertEqual(candidate.operation, "update")
        self.assertEqual(candidate.memory_role, "current_state")
        self.assertEqual(candidate.retention_class, "active_state")
        self.assertEqual(candidate.domain_pack, "project_state")

    def test_classify_telegram_generic_memory_candidate_prioritizes_entity_owner_corrections(self) -> None:
        candidate = classify_telegram_generic_memory_candidate("Actually, Maya owns the launch checklist.")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.predicate, "entity.owner")
        self.assertEqual(candidate.value, "Maya")
        self.assertEqual(candidate.label, "launch checklist owner")
        self.assertEqual(candidate.operation, "update")
        self.assertEqual(candidate.memory_role, "current_state")
        self.assertEqual(candidate.retention_class, "active_state")
        self.assertEqual(candidate.domain_pack, "entity_state")

    def test_classify_telegram_generic_memory_candidate_maps_onboarding_direction_to_entity_decision(self) -> None:
        candidate = classify_telegram_generic_memory_candidate(
            "For the GTM launch, the onboarding direction is async walkthroughs."
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.predicate, "entity.decision")
        self.assertEqual(candidate.value, "async walkthroughs")
        self.assertEqual(candidate.label, "GTM launch decision")
        self.assertEqual(candidate.operation, "update")
        self.assertEqual(candidate.memory_role, "current_state")
        self.assertEqual(candidate.retention_class, "active_state")
        self.assertEqual(candidate.domain_pack, "entity_state")

    def test_classify_telegram_generic_memory_candidate_detects_delete_operation(self) -> None:
        candidate = classify_telegram_generic_memory_candidate("Forget our owner.")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.predicate, "profile.current_owner")
        self.assertEqual(candidate.operation, "delete")
        self.assertEqual(candidate.memory_role, "current_state")
        self.assertEqual(candidate.retention_class, "active_state")
        self.assertEqual(candidate.domain_pack, "project_state")

    def test_classify_telegram_generic_memory_candidate_rejects_small_talk(self) -> None:
        candidate = classify_telegram_generic_memory_candidate("thanks")

        self.assertIsNone(candidate)

    def test_assess_telegram_generic_memory_candidate_rejects_direct_questions_without_question_mark(self) -> None:
        assessment = assess_telegram_generic_memory_candidate("what do you know about Spark systems")

        self.assertEqual(assessment.outcome, "drop")
        self.assertEqual(assessment.reason, "not_memoryworthy")
        self.assertIsNone(classify_telegram_generic_memory_candidate("what do you know about Spark systems"))

    def test_assess_telegram_generic_memory_candidate_rejects_low_information_fragments(self) -> None:
        for text in (
            "The second",
            "second",
            "second one",
            "yes the second one",
            "option two",
            "option 2",
            "number two",
            "that one",
            "the latter",
        ):
            with self.subTest(text=text):
                assessment = assess_telegram_generic_memory_candidate(text)

                self.assertEqual(assessment.outcome, "drop")
                self.assertEqual(assessment.reason, "not_memoryworthy")
                self.assertIsNone(classify_telegram_generic_memory_candidate(text))

    def test_assess_telegram_generic_memory_candidate_keeps_factual_second_step(self) -> None:
        assessment = assess_telegram_generic_memory_candidate("The second onboarding step is Stripe recovery.")

        self.assertEqual(assessment.outcome, "raw_episode")
        self.assertEqual(assessment.reason, "meaningful_but_unpromoted")

    def test_build_researcher_reply_records_policy_gate_for_rejected_generic_memory_candidate(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="openai_compatible",
            auth_profile_id="default",
            auth_method="api_key",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.example.invalid/v1",
            default_model="test-model",
            secret_ref=None,
            secret_value="test-secret",
            source="test",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=ResearcherProviderSelection(provider=provider, model_family="generic"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            return_value={"raw_response": "Spark systems are the operating layer we are improving."},
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-rejected-generic-memory",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-rejected-generic-memory",
                channel_kind="telegram",
                user_message="maybe the launch could work someday",
            )

        self.assertTrue(result.reply_text)
        gate_records = recent_policy_gate_records(self.state_db, limit=10, gate_name="memory.generic_candidate")
        self.assertTrue(gate_records)
        evidence = gate_records[0]["evidence_json"] or {}
        facts = evidence.get("facts") or {}
        self.assertEqual(gate_records[0]["reason_code"], "salience_generic_candidate_dropped")
        self.assertEqual(facts.get("outcome"), "drop")
        self.assertEqual(facts.get("promotion_stage"), "drop")
        self.assertEqual(facts.get("keepability"), "not_keepable")
        lane_records = recent_memory_lane_records(self.state_db, limit=10)
        self.assertTrue(any(record.get("artifact_lane") == "rejected_memory_candidates" for record in lane_records))

    def test_assess_telegram_generic_memory_candidate_rejects_emotional_self_reports(self) -> None:
        assessment = assess_telegram_generic_memory_candidate("I'm anxious about the launch tomorrow")

        self.assertEqual(assessment.outcome, "drop")
        self.assertEqual(assessment.reason, "not_memoryworthy")
        self.assertIsNone(classify_telegram_generic_memory_candidate("I'm anxious about the launch tomorrow"))

    def test_assess_telegram_generic_memory_candidate_assigns_structured_evidence(self) -> None:
        assessment = assess_telegram_generic_memory_candidate(
            "Users keep dropping during onboarding because Stripe verification fails."
        )

        self.assertEqual(assessment.outcome, "structured_evidence")
        self.assertEqual(assessment.reason, "evidence_marker")
        self.assertEqual(assessment.memory_role, "structured_evidence")
        self.assertEqual(assessment.retention_class, "episodic_archive")

    def test_assess_telegram_generic_memory_candidate_assigns_source_backed_structured_evidence(self) -> None:
        for text in (
            "Interview notes show our priority is onboarding reliability.",
            "The weekly review says our commitment is to ship onboarding fixes this week.",
            "After testing both flows, our decision is to keep human onboarding support.",
        ):
            with self.subTest(text=text):
                assessment = assess_telegram_generic_memory_candidate(text)

                self.assertEqual(assessment.outcome, "structured_evidence")
                self.assertEqual(assessment.reason, "evidence_marker")
                self.assertEqual(assessment.memory_role, "structured_evidence")
                self.assertEqual(assessment.retention_class, "episodic_archive")

    def test_assess_telegram_generic_memory_candidate_assigns_belief_candidate(self) -> None:
        assessment = assess_telegram_generic_memory_candidate(
            "I think enterprise teams need hands-on onboarding."
        )

        self.assertEqual(assessment.outcome, "belief_candidate")
        self.assertEqual(assessment.reason, "belief_marker")
        self.assertEqual(assessment.memory_role, "belief_candidate")
        self.assertEqual(assessment.retention_class, "derived_belief")

    def test_assess_telegram_generic_memory_candidate_assigns_raw_episode(self) -> None:
        assessment = assess_telegram_generic_memory_candidate(
            "The pricing page felt confusing during the demo."
        )

        self.assertEqual(assessment.outcome, "raw_episode")
        self.assertEqual(assessment.memory_role, "raw_episode")
        self.assertIsNotNone(assessment.salience_decision)
        assert assessment.salience_decision is not None
        self.assertLess(assessment.salience_decision.salience_score, 0.35)
        self.assertEqual(assessment.salience_decision.promotion_disposition, "capture_raw_episode")

    def test_build_researcher_reply_records_uncaptured_memory_candidate_assessment(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="openai_compatible",
            auth_profile_id="default",
            auth_method="api_key",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.example.invalid/v1",
            default_model="test-model",
            secret_ref=None,
            secret_value="test-secret",
            source="test",
        )

        def fake_execute_direct_provider_prompt(*, user_prompt, **kwargs):
            self.assertIn("[Memory write this turn]", user_prompt)
            self.assertIn("structured_evidence", user_prompt)
            self.assertIn("Stripe verification fails", user_prompt)
            return {
                "raw_response": (
                    "That Stripe failure is the bottleneck. Fix verification before spending another cycle on onboarding polish."
                )
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=ResearcherProviderSelection(provider=provider, model_family="generic"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_execute_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-candidate-assessment",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-candidate-assessment",
                channel_kind="telegram",
                user_message="Users keep dropping during onboarding because Stripe verification fails.",
            )

        self.assertTrue(result.reply_text)
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.routing_decision, "provider_fallback_chat")
        self.assertIn("Stripe", result.reply_text)
        self.assertNotIn("logged", result.reply_text.lower())
        assessment_events = latest_events_by_type(self.state_db, event_type="memory_candidate_assessed", limit=10)
        self.assertTrue(assessment_events)
        facts = assessment_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("outcome"), "structured_evidence")
        self.assertEqual(facts.get("memory_role"), "structured_evidence")
        self.assertEqual(facts.get("retention_class"), "episodic_archive")
        self.assertEqual(facts.get("promotion_stage"), "structured_evidence")
        self.assertEqual(facts.get("keepability"), "durable_intelligence_memory")
        self.assertEqual(facts.get("promotion_disposition"), "promote_structured_evidence")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        write_facts = write_events[0]["facts_json"] or {}
        self.assertEqual(write_facts.get("memory_role"), "structured_evidence")
        self.assertEqual(write_facts.get("promotion_stage"), "structured_evidence")
        self.assertEqual(write_facts.get("why_saved"), "evidence_marker")
        recorded_observations = write_facts.get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "evidence.telegram.evidence")
        self.assertEqual(recorded_observations[0]["retention_class"], "episodic_archive")
        self.assertEqual(recorded_observations[0]["promotion_stage"], "structured_evidence")
        lane_records = recent_memory_lane_records(self.state_db, limit=10)
        self.assertTrue(any(record.get("keepability") == "durable_intelligence_memory" for record in lane_records))
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        tool_facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("routing_decision") == "provider_fallback_chat"
            ),
            {},
        )
        self.assertEqual(tool_facts.get("bridge_mode"), "external_configured")
        self.assertEqual(tool_facts.get("routing_decision"), "provider_fallback_chat")

    def test_build_researcher_reply_persists_belief_candidate_as_derived_belief(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="openai_compatible",
            auth_profile_id="default",
            auth_method="api_key",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.example.invalid/v1",
            default_model="test-model",
            secret_ref=None,
            secret_value="test-secret",
            source="test",
        )

        def fake_execute_direct_provider_prompt(*, user_prompt, **kwargs):
            self.assertIn("[Memory write this turn]", user_prompt)
            self.assertIn("belief_candidate", user_prompt)
            self.assertIn("hands-on onboarding", user_prompt)
            return {
                "raw_response": (
                    "I agree with that read. Enterprise teams usually need hands-on onboarding before the product can sell itself."
                )
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=ResearcherProviderSelection(provider=provider, model_family="generic"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_execute_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-belief",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-belief",
                channel_kind="telegram",
                user_message="I think enterprise teams need hands-on onboarding.",
            )

        self.assertTrue(result.reply_text)
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.routing_decision, "provider_fallback_chat")
        self.assertIn("hands-on onboarding", result.reply_text)
        self.assertNotIn("keep that in mind", result.reply_text.lower())
        assessment_events = latest_events_by_type(self.state_db, event_type="memory_candidate_assessed", limit=10)
        self.assertTrue(assessment_events)
        facts = assessment_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("outcome"), "belief_candidate")
        self.assertEqual(facts.get("retention_class"), "derived_belief")
        self.assertEqual(facts.get("promotion_stage"), "belief_candidate")
        self.assertEqual(facts.get("promotion_disposition"), "promote_belief_candidate")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        write_facts = write_events[0]["facts_json"] or {}
        self.assertEqual(write_facts.get("memory_role"), "belief")
        self.assertEqual(write_facts.get("promotion_stage"), "belief_candidate")
        recorded_observations = write_facts.get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(recorded_observations[0]["retention_class"], "derived_belief")
        self.assertEqual(recorded_observations[0]["promotion_stage"], "belief_candidate")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        tool_facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("routing_decision") == "provider_fallback_chat"
            ),
            {},
        )
        self.assertEqual(tool_facts.get("bridge_mode"), "external_configured")
        self.assertEqual(tool_facts.get("routing_decision"), "provider_fallback_chat")

    def test_build_researcher_reply_answers_belief_recall_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-belief-write",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-belief-write",
            channel_kind="telegram",
            user_message="I think enterprise teams need hands-on onboarding.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-belief-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-belief-read",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertEqual(result.routing_decision, "memory_belief_recall_query")
        self.assertIn("onboarding", result.reply_text.lower())
        self.assertIn("hands-on onboarding", result.reply_text)
        self.assertIn("inferred belief", result.reply_text.lower())
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_belief_recall"
            ),
            {},
        )
        self.assertEqual(facts.get("bridge_mode"), "memory_belief_recall")
        self.assertIn("belief", facts.get("retrieved_memory_roles") or [])

    def test_build_researcher_reply_belief_recall_prefers_latest_unsuperseded_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-belief-write-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-belief-write-1",
            channel_kind="telegram",
            user_message="I think self-serve onboarding will work.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-belief-write-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-belief-write-2",
            channel_kind="telegram",
            user_message="I think enterprise teams need hands-on onboarding.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-belief-refresh-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-belief-refresh-read",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("hands-on onboarding", result.reply_text)
        self.assertNotIn("self-serve onboarding will work", result.reply_text)

    def test_build_researcher_reply_belief_recall_downgrades_when_newer_evidence_exists(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-belief-stale-write",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-belief-stale-write",
            channel_kind="telegram",
            user_message="I think self-serve onboarding will work.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-belief-stale-evidence",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-belief-stale-evidence",
            channel_kind="telegram",
            user_message="Users keep needing hands-on onboarding support because enterprise teams ask for setup help.",
        )
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        evidence_write = next(
            (
                (event["facts_json"] or {})
                for event in write_events
                if (event["facts_json"] or {}).get("memory_role") == "structured_evidence"
            ),
            {},
        )
        evidence_observations = evidence_write.get("observations") or []
        self.assertEqual(evidence_observations[0].get("belief_lifecycle_action"), "invalidated")
        self.assertTrue(evidence_observations[0].get("conflicts_with"))

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-belief-stale-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-belief-stale-read",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("newer direct evidence", result.reply_text.lower())
        self.assertIn("hands-on onboarding support", result.reply_text)
        self.assertNotIn("self-serve onboarding will work", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_belief_recall"
            ),
            {},
        )
        self.assertTrue(facts.get("belief_stale_due_to_evidence"))
        self.assertGreaterEqual(int(facts.get("newer_evidence_count") or 0), 1)
        post_recall_write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=20)
        archive_write = next(
            (
                (event["facts_json"] or {})
                for event in post_recall_write_events
                if (event["facts_json"] or {}).get("memory_role") == "belief"
                and ((event["facts_json"] or {}).get("observations") or [{}])[0].get("operation") == "delete"
            ),
            {},
        )
        archive_observations = archive_write.get("observations") or []
        if archive_observations:
            self.assertEqual(archive_observations[0].get("belief_lifecycle_action"), "archived")

    def test_build_researcher_reply_belief_recall_uses_newer_current_state_evidence_in_mixed_sequence(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        for request_id, message in (
            ("req-seed-evidence", "Users keep dropping during onboarding because Stripe verification fails."),
            ("req-seed-belief-1", "I think self-serve onboarding will work."),
            ("req-seed-belief-2", "I think enterprise teams need hands-on onboarding."),
            (
                "req-seed-evidence-2",
                "Users keep needing hands-on onboarding support because enterprise teams ask for setup help.",
            ),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mixed-belief-recall",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-mixed-belief-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mixed-belief-recall",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("newer direct evidence", result.reply_text.lower())
        self.assertIn("hands-on onboarding support", result.reply_text)
        self.assertNotIn("self-serve onboarding will work", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_belief_recall"
            ),
            {},
        )
        self.assertTrue(facts.get("belief_stale_due_to_evidence"))
        self.assertGreaterEqual(int(facts.get("newer_evidence_count") or 0), 1)

    def test_build_researcher_reply_belief_recall_prefers_newer_consolidated_blocker_evidence_over_saved_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        for request_id, message in (
            ("req-seed-evidence", "Users keep dropping during onboarding because Stripe verification fails."),
            ("req-seed-belief-1", "I think self-serve onboarding will work."),
            ("req-seed-belief-2", "I think enterprise teams need hands-on onboarding."),
            (
                "req-seed-evidence-hands-on",
                "Users keep needing hands-on onboarding support because enterprise teams ask for setup help.",
            ),
            ("req-seed-evidence-2", "Users keep dropping during onboarding because Stripe verification fails."),
            (
                "req-seed-evidence-3",
                "Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
            ),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mixed-evidence-consolidation",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-mixed-evidence-belief-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mixed-evidence-consolidation",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("newer direct evidence", result.reply_text.lower())
        self.assertIn("Stripe verification fails", result.reply_text)
        self.assertNotIn("self-serve onboarding will work", result.reply_text)

    def test_build_researcher_reply_returns_empty_belief_recall_when_nothing_saved(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for empty belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for empty belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-belief-empty-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-belief-empty-read",
                channel_kind="telegram",
                user_message="What belief do you have about pricing?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertEqual(result.routing_decision, "memory_belief_recall_query")
        self.assertEqual(result.reply_text, "I don't currently have a saved belief about that.")

    def test_build_researcher_reply_belief_recall_reports_direct_evidence_without_saved_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        for request_id, message in (
            ("req-evidence-only-1", "Users keep dropping during onboarding because Stripe verification fails."),
            (
                "req-evidence-only-2",
                "Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
            ),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-only-belief",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for evidence-only belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for evidence-only belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-only-belief-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-only-belief",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("newer direct evidence", result.reply_text.lower())
        self.assertIn("Stripe verification fails", result.reply_text)
        self.assertNotIn("My saved belief", result.reply_text)
        self.assertNotIn("This is an inferred belief", result.reply_text)

    def test_build_researcher_reply_belief_recall_downgrades_stale_unrevalidated_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch("spark_intelligence.memory.orchestrator._now_iso", return_value="2025-02-01T00:00:00+00:00"):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-belief-old-write",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-belief-old-write",
                channel_kind="telegram",
                user_message="I think enterprise teams need hands-on onboarding.",
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for stale belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for stale belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-belief-old-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-belief-old-read",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
            )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("not been revalidated recently", result.reply_text.lower())
        self.assertIn("hands-on onboarding", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_belief_recall"
            ),
            {},
        )
        self.assertTrue(facts.get("belief_stale_due_to_age"))
        self.assertEqual(facts.get("stale_belief_count"), 1)

    def test_build_researcher_reply_persists_raw_episode_for_meaningful_unpromoted_turn(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="openai_compatible",
            auth_profile_id="default",
            auth_method="api_key",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.example.invalid/v1",
            default_model="test-model",
            secret_ref=None,
            secret_value="test-secret",
            source="test",
        )

        def fake_execute_direct_provider_prompt(*, user_prompt, **kwargs):
            self.assertIn("[Memory write this turn]", user_prompt)
            self.assertIn("pricing page felt confusing", user_prompt)
            return {
                "raw_response": (
                    "That demo signal matters. Let's tighten the pricing page before the next walkthrough."
                )
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=ResearcherProviderSelection(provider=provider, model_family="generic"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_execute_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-raw-episode",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-raw-episode",
                channel_kind="telegram",
                user_message="The pricing page felt confusing during the demo.",
            )

        self.assertTrue(result.reply_text)
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.routing_decision, "provider_fallback_chat")
        self.assertIn("pricing page", result.reply_text)
        self.assertNotIn("noted", result.reply_text.lower())
        assessment_events = latest_events_by_type(self.state_db, event_type="memory_candidate_assessed", limit=10)
        self.assertTrue(assessment_events)
        facts = assessment_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("outcome"), "raw_episode")
        self.assertEqual(facts.get("memory_role"), "raw_episode")
        self.assertEqual(facts.get("promotion_stage"), "raw_episode")
        self.assertEqual(facts.get("promotion_disposition"), "capture_raw_episode")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        write_facts = write_events[0]["facts_json"] or {}
        self.assertEqual(write_facts.get("memory_role"), "episodic")
        self.assertEqual(write_facts.get("promotion_stage"), "raw_episode")
        recorded_observations = write_facts.get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "raw_turn")
        self.assertEqual(recorded_observations[0]["retention_class"], "episodic_archive")
        self.assertEqual(recorded_observations[0]["promotion_stage"], "raw_episode")
        lane_records = recent_memory_lane_records(self.state_db, limit=10)
        self.assertTrue(any(record.get("artifact_lane") == "episodic_trace" for record in lane_records))
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        tool_facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("routing_decision") == "provider_fallback_chat"
            ),
            {},
        )
        self.assertEqual(tool_facts.get("bridge_mode"), "external_configured")
        self.assertEqual(tool_facts.get("routing_decision"), "provider_fallback_chat")

    def test_build_researcher_reply_answers_open_structured_evidence_recall_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-open-evidence-write",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-open-evidence-write",
            channel_kind="telegram",
            user_message="Users keep dropping during onboarding because Stripe verification fails.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for open memory recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for open memory recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-open-evidence-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-open-evidence-read",
                channel_kind="telegram",
                user_message="What evidence do you have about onboarding?",
            )

        self.assertEqual(result.mode, "memory_open_recall")
        self.assertEqual(result.routing_decision, "memory_open_recall_query")
        self.assertIn("onboarding", result.reply_text.lower())
        self.assertIn("Stripe verification fails", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_open_recall"
            ),
            {},
        )
        self.assertEqual(facts.get("bridge_mode"), "memory_open_recall")
        self.assertIn("structured_evidence", facts.get("retrieved_memory_roles") or [])
        self.assertEqual((facts.get("graph_shadow_trace") or {}).get("status"), "recorded")
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_succeeded", limit=20)
        hybrid_read = next(
            (
                event
                for event in read_events
                if (event["facts_json"] or {}).get("method") == "hybrid_memory_retrieve"
                and event.get("request_id") == "req-open-evidence-read:graph-shadow"
            ),
            None,
        )
        self.assertIsNotNone(hybrid_read)
        graph_lane = next(
            lane
            for lane in ((hybrid_read["facts_json"] or {}).get("retrieval_trace") or {})
            .get("hybrid_memory_retrieve", {})
            .get("lane_summaries", [])
            if lane.get("lane") == "typed_temporal_graph"
        )
        self.assertEqual(graph_lane.get("source_class"), "graphiti_temporal_graph")

    def test_build_researcher_reply_answers_what_i_told_you_recall_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-told-you-evidence-write",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-told-you-evidence-write",
            channel_kind="telegram",
            user_message="Users keep dropping during onboarding because Stripe verification fails.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for open memory recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for open memory recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-told-you-evidence-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-told-you-evidence-read",
                channel_kind="telegram",
                user_message="What did I tell you about the onboarding demo?",
            )

        self.assertEqual(result.mode, "memory_open_recall")
        self.assertEqual(result.routing_decision, "memory_open_recall_query")
        self.assertIn("onboarding", result.reply_text.lower())
        self.assertIn("Stripe verification fails", result.reply_text)

    def test_build_researcher_reply_answers_spark_systems_self_knowledge_before_memory_recall(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for Spark systems self-knowledge"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for Spark systems self-knowledge"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.retrieve_memory_evidence_in_memory",
            side_effect=AssertionError("memory recall should not run for Spark systems self-knowledge"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.inspect_human_memory_in_memory",
            side_effect=AssertionError("memory inspection should not run for Spark systems self-knowledge"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-spark-systems-self-knowledge",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-spark-systems-self-knowledge",
                channel_kind="telegram",
                user_message="What do you know about Spark systems?",
            )

        self.assertEqual(result.mode, "spark_systems_self_knowledge")
        self.assertEqual(result.routing_decision, "spark_systems_self_knowledge")
        self.assertIn("spark-cli", result.reply_text)
        self.assertIn("spark-intelligence-builder", result.reply_text)
        self.assertIn("domain-chip-memory", result.reply_text)
        self.assertIn("spark-researcher", result.reply_text)
        self.assertIn("spawner-ui", result.reply_text)
        self.assertIn("spark-telegram-bot", result.reply_text)
        self.assertIn("Mission Control", result.reply_text)
        self.assertNotIn("I have saved memory about", result.reply_text)
        self.assertNotIn("What would you like help with?", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = tool_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("bridge_mode"), "spark_systems_self_knowledge")
        self.assertIn("domain-chip-memory", facts.get("starter_modules") or [])

    def test_build_researcher_reply_reports_repeated_evidence_in_belief_recall(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-belief-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-belief-seed-1",
            channel_kind="telegram",
            user_message="Users keep dropping during onboarding because Stripe verification fails.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-belief-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-belief-seed-2",
            channel_kind="telegram",
            user_message="Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-belief-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-belief-read",
                channel_kind="telegram",
                user_message="What is your current belief about onboarding?",
        )

        self.assertEqual(result.mode, "memory_belief_recall")
        self.assertIn("newer direct evidence", result.reply_text.lower())
        self.assertIn("stripe verification fails", result.reply_text.lower())
        self.assertNotIn("My saved belief", result.reply_text)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_belief_recall"
            ),
            {},
        )
        self.assertEqual(facts.get("newer_evidence_count"), 2)
        self.assertEqual(facts.get("read_method"), "retrieve_evidence(evidence_only)")

    def test_build_researcher_reply_promotes_repeated_evidence_into_current_blocker(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-blocker-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-blocker-seed-1",
            channel_kind="telegram",
            user_message="Users keep dropping during onboarding because Stripe verification fails.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-blocker-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-blocker-seed-2",
            channel_kind="telegram",
            user_message="Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current blocker recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current blocker recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-blocker-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-blocker-read",
                channel_kind="telegram",
                user_message="What are we blocked on?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertIn("current blocker", result.reply_text.lower())
        self.assertIn("stripe verification fails", result.reply_text.lower())

    def test_build_researcher_reply_promotes_repeated_evidence_into_current_dependency(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-dependency-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-dependency-seed-1",
            channel_kind="telegram",
            user_message="Users keep getting stuck during onboarding because we're waiting on Stripe approval.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-dependency-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-dependency-seed-2",
            channel_kind="telegram",
            user_message="Users still get stuck during onboarding because we're waiting on Stripe approval and review is slow.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current dependency recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current dependency recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-dependency-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-dependency-read",
                channel_kind="telegram",
                user_message="What is our dependency?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertIn("current dependency", result.reply_text.lower())
        self.assertIn("stripe approval", result.reply_text.lower())

    def test_build_researcher_reply_promotes_repeated_evidence_into_current_constraint(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-constraint-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-constraint-seed-1",
            channel_kind="telegram",
            user_message="Users keep waiting during onboarding because we're limited by founder bandwidth.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-constraint-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-constraint-seed-2",
            channel_kind="telegram",
            user_message="Users still wait during onboarding because we're limited by founder bandwidth.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current constraint recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current constraint recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-constraint-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-constraint-read",
                channel_kind="telegram",
                user_message="What is our constraint?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertIn("current constraint", result.reply_text.lower())
        self.assertIn("founder bandwidth", result.reply_text.lower())

    def test_build_researcher_reply_promotes_repeated_evidence_into_current_risk(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-risk-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-risk-seed-1",
            channel_kind="telegram",
            user_message="There is still a risk of enterprise churn during onboarding because activation is weak.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-risk-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-risk-seed-2",
            channel_kind="telegram",
            user_message="There is still a risk of enterprise churn during onboarding because activation is weak and teams are delaying rollout.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current risk recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current risk recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-risk-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-risk-read",
                channel_kind="telegram",
                user_message="What is our risk?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertIn("current risk", result.reply_text.lower())
        self.assertIn("enterprise churn during onboarding", result.reply_text.lower())

    def test_build_researcher_reply_promotes_repeated_evidence_into_current_status(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-status-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-status-seed-1",
            channel_kind="telegram",
            user_message="Status update: pending security review for the onboarding rollout.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-status-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-status-seed-2",
            channel_kind="telegram",
            user_message="Status update: still pending security review for the onboarding rollout.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current status recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current status recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-status-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-status-read",
                channel_kind="telegram",
                user_message="What is our status?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertIn("current status", result.reply_text.lower())
        self.assertIn("pending security review", result.reply_text.lower())

    def test_build_researcher_reply_promotes_repeated_evidence_into_current_owner(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-owner-seed-1",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-owner-seed-1",
            channel_kind="telegram",
            user_message="The onboarding rollout is currently owned by Nadia.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-evidence-owner-seed-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-evidence-owner-seed-2",
            channel_kind="telegram",
            user_message="The onboarding rollout is still owned by Nadia during security review.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current owner recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current owner recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-evidence-owner-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-evidence-owner-read",
                channel_kind="telegram",
                user_message="Who is the owner?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertEqual(result.reply_text, "Your current owner is Nadia.")

    def test_build_researcher_reply_promotes_high_confidence_project_state_fields_from_single_evidence_turn(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        cases = (
            (
                "Customer interviews confirm our plan is to simplify onboarding approvals.",
                "What is our plan?",
                "current plan",
                "simplify onboarding approvals",
            ),
            (
                "Interview notes show our priority is onboarding reliability.",
                "What is our priority?",
                "current focus",
                "onboarding reliability",
            ),
            (
                "After testing both flows, our decision is to keep human onboarding support.",
                "What decision did we make?",
                "current decision",
                "keep human onboarding support",
            ),
            (
                "The weekly review says our commitment is to ship onboarding fixes this week.",
                "What is our commitment?",
                "current commitment",
                "ship onboarding fixes this week",
            ),
            (
                "Roadmap notes confirm our next milestone is launch the self-serve onboarding beta.",
                "What is our milestone?",
                "current milestone",
                "launch the self-serve onboarding beta",
            ),
            (
                "Interview notes suggest our assumption is enterprise teams want hands-on onboarding.",
                "What is our assumption?",
                "current assumption",
                "enterprise teams want hands-on onboarding",
            ),
        )

        for index, (seed_message, query_message, expected_label, expected_value) in enumerate(cases, start=1):
            with self.subTest(query=query_message):
                build_researcher_reply(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    request_id=f"req-evidence-project-state-seed-{index}",
                    agent_id="agent-1",
                    human_id=f"human-project-state-{index}",
                    session_id=f"session-evidence-project-state-seed-{index}",
                    channel_kind="telegram",
                    user_message=seed_message,
                )

                with patch(
                    "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
                    side_effect=AssertionError("provider resolution should not run for project-state recall"),
                ), patch(
                    "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
                    side_effect=AssertionError("provider execution should not run for project-state recall"),
                ):
                    result = build_researcher_reply(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        request_id=f"req-evidence-project-state-read-{index}",
                        agent_id="agent-1",
                        human_id=f"human-project-state-{index}",
                        session_id=f"session-evidence-project-state-read-{index}",
                        channel_kind="telegram",
                        user_message=query_message,
                    )

                self.assertEqual(result.mode, "memory_profile_fact")
                self.assertEqual(result.routing_decision, "memory_profile_fact_query")
                self.assertIn(expected_label, result.reply_text.lower())
                self.assertIn(expected_value.lower(), result.reply_text.lower())

    def test_build_researcher_reply_downgrades_stale_current_plan_recall(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.memory.orchestrator._now_iso",
            return_value="2025-03-01T09:00:00Z",
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-stale-plan-seed",
                agent_id="agent-1",
                human_id="human-stale-plan",
                session_id="session-stale-plan",
                channel_kind="telegram",
                user_message="Our current plan is to simplify onboarding approvals.",
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for stale current-state recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for stale current-state recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-stale-plan-read",
                agent_id="agent-1",
                human_id="human-stale-plan",
                session_id="session-stale-plan",
                channel_kind="telegram",
                user_message="What is our plan?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertEqual(
            result.reply_text,
            'I have an older saved current plan: "simplify onboarding approvals" but it has not been revalidated recently, so I would not treat it as current.',
        )

    def test_build_researcher_reply_treats_maintenance_stale_preserved_as_stale_current_state(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        stale_record = {
            "memory_role": "current_state",
            "subject": "human-stale-maintenance",
            "predicate": "profile.current_plan",
            "value": "simplify onboarding approvals",
            "timestamp": "2026-04-20T09:00:00Z",
            "metadata": {
                "memory_role": "current_state",
                "value": "simplify onboarding approvals",
                "active_state_maintenance_action": "stale_preserved",
                "active_state_maintenance_reason": "past_revalidate_at",
            },
        }
        lookup_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[stale_record],
                memory_role="current_state",
            )
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=lookup_result,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._inspect_profile_fact_records",
            return_value=([stale_record], []),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for maintenance-stale current-state recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for maintenance-stale current-state recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-maintenance-stale-plan-read",
                agent_id="agent-1",
                human_id="human-stale-maintenance",
                session_id="session-stale-maintenance",
                channel_kind="telegram",
                user_message="What is my current plan?",
            )

        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        self.assertIn("not been revalidated recently", result.reply_text)
        self.assertIn("active_state_maintenance_actions=stale_preserved", result.evidence_summary)
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        facts = next(
            (
                event["facts_json"] or {}
                for event in tool_events
                if (event["facts_json"] or {}).get("routing_decision") == "memory_profile_fact_query"
            ),
            {},
        )
        self.assertEqual(facts.get("active_state_maintenance_actions"), ["stale_preserved"])
        self.assertTrue(facts.get("stale_current_fact"))

    def test_build_researcher_reply_archives_stale_structured_evidence_when_newer_evidence_exists(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep dropping during onboarding because Stripe verification fails.",
                "timestamp": "2025-02-01T00:00:00+00:00",
                "observation_id": "obs-evidence-1",
                "metadata": {
                    "value": "Users keep dropping during onboarding because Stripe verification fails.",
                    "archive_at": "2025-03-03T00:00:00+00:00",
                },
            },
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users still drop during onboarding because Stripe verification fails and the identity retry flow is confusing.",
                "timestamp": "2025-03-20T00:00:00+00:00",
                "observation_id": "obs-evidence-2",
                "metadata": {
                    "value": "Users still drop during onboarding because Stripe verification fails and the identity retry flow is confusing.",
                    "archive_at": "2025-04-19T00:00:00+00:00",
                },
            },
        ]

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for open memory recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for open memory recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.retrieve_memory_evidence_in_memory",
            return_value=SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=evidence_records)),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=evidence_records)),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.archive_structured_evidence_from_memory",
            return_value=SimpleNamespace(status="succeeded", accepted_count=1),
        ) as mocked_archive:
            with patch(
                "spark_intelligence.researcher_bridge.advisory.datetime",
            ) as mocked_datetime:
                from datetime import datetime, timezone

                mocked_datetime.now.return_value = datetime(2026, 4, 21, tzinfo=timezone.utc)
                mocked_datetime.fromisoformat.side_effect = datetime.fromisoformat
                mocked_datetime.side_effect = datetime
                result = build_researcher_reply(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    request_id="req-open-evidence-archive-read",
                    agent_id="agent-1",
                    human_id="human-1",
                    session_id="session-open-evidence-archive-read",
                    channel_kind="telegram",
                    user_message="What evidence do you have about onboarding?",
                )

        self.assertEqual(result.mode, "memory_open_recall")
        self.assertIn("identity retry flow is confusing", result.reply_text.lower())
        self.assertNotIn("users keep dropping during onboarding because stripe verification fails.", result.reply_text.lower())
        mocked_archive.assert_called_once()
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_open_recall"
            ),
            {},
        )
        self.assertEqual(facts.get("archived_structured_evidence_count"), 1)

    def test_build_researcher_reply_answers_open_raw_episode_recall_from_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-open-episode-write",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-open-episode-write",
            channel_kind="telegram",
            user_message="The pricing page felt confusing during the demo.",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for open memory recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for open memory recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-open-episode-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-open-episode-read",
                channel_kind="telegram",
                user_message="What happened during the demo?",
            )

        self.assertEqual(result.mode, "memory_open_recall")
        self.assertEqual(result.routing_decision, "memory_open_recall_query")
        self.assertIn("demo", result.reply_text.lower())
        self.assertIn("pricing page felt confusing during the demo", result.reply_text.lower())
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_open_recall"
            ),
            {},
        )
        self.assertEqual(facts.get("bridge_mode"), "memory_open_recall")
        self.assertIn("episodic", facts.get("retrieved_memory_roles") or [])

    def test_build_researcher_reply_archives_stale_raw_episode_when_newer_evidence_exists(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch("spark_intelligence.memory.orchestrator._now_iso", return_value="2025-02-01T00:00:00+00:00"):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-open-episode-old-write",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-open-episode-old-write",
                channel_kind="telegram",
                user_message="The pricing page felt confusing during the demo.",
            )

        with patch("spark_intelligence.memory.orchestrator._now_iso", return_value="2025-03-20T00:00:00+00:00"):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-open-episode-evidence-write",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-open-episode-evidence-write",
                channel_kind="telegram",
                user_message="During the demo, users got confused because the pricing page explanation was unclear.",
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for open memory recall"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for open memory recall"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-open-episode-archive-read",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-open-episode-archive-read",
                channel_kind="telegram",
                user_message="What happened during the demo?",
            )

        self.assertEqual(result.mode, "memory_open_recall")
        self.assertIn("during the demo", result.reply_text.lower())
        self.assertIn("pricing page explanation was unclear", result.reply_text.lower())
        self.assertNotIn("pricing page felt confusing during the demo", result.reply_text.lower())
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = next(
            (
                (event["facts_json"] or {})
                for event in tool_events
                if (event["facts_json"] or {}).get("bridge_mode") == "memory_open_recall"
            ),
            {},
        )
        self.assertEqual(facts.get("archived_raw_episode_count"), 1)
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=20)
        archive_write = next(
            (
                (event["facts_json"] or {})
                for event in write_events
                if (event["facts_json"] or {}).get("memory_role") == "episodic"
                and ((event["facts_json"] or {}).get("observations") or [{}])[0].get("operation") == "delete"
            ),
            {},
        )
        archive_observations = archive_write.get("observations") or []
        self.assertTrue(archive_observations)
        self.assertEqual(archive_observations[0].get("raw_episode_lifecycle_action"), "archived")

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

    def test_build_researcher_reply_saves_explicit_current_focus_memory_update(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for explicit focus memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for explicit focus memory"),
        ):
            focus_update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-explicit-focus-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-explicit-focus",
                channel_kind="telegram",
                user_message="Memory update: my current focus is Telegram memory routing cleanup.",
            )
            focus_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-explicit-focus-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-explicit-focus",
                channel_kind="telegram",
                user_message="What is my current focus?",
            )

        self.assertEqual(focus_update.mode, "memory_generic_observation_update")
        self.assertEqual(focus_update.routing_decision, "memory_generic_observation")
        self.assertEqual(
            focus_update.reply_text,
            "I'll remember that your current focus is Telegram memory routing cleanup.",
        )
        self.assertEqual(focus_query.mode, "memory_profile_fact")
        self.assertEqual(focus_query.reply_text, "Your current focus is Telegram memory routing cleanup.")

    def test_build_researcher_reply_saves_low_stakes_test_fact_for_direct_recall(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for low-stakes test fact memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for low-stakes test fact memory"),
        ):
            update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-low-stakes-test-fact-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-low-stakes-test-fact",
                channel_kind="telegram",
                user_message=(
                    "For the natural recall test: remember that my low-stakes test fact "
                    "is that the tiny desk plant is named Mira."
                ),
            )
            query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-low-stakes-test-fact-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-low-stakes-test-fact",
                channel_kind="telegram",
                user_message="What was the low-stakes test fact I gave you?",
            )

        self.assertEqual(update.mode, "memory_generic_observation_update")
        self.assertEqual(update.routing_decision, "memory_generic_observation")
        self.assertEqual(
            update.reply_text,
            "I'll remember that your low-stakes test fact is that the tiny desk plant is named Mira.",
        )
        self.assertEqual(query.mode, "memory_profile_fact")
        self.assertEqual(
            query.reply_text,
            "Your low-stakes test fact was that the tiny desk plant is named Mira.",
        )
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_low_stakes_test_fact")
        self.assertEqual(recorded_observations[0]["value"], "the tiny desk plant is named Mira")

    def test_build_researcher_reply_recalls_natural_named_object_fact(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for natural named-object memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for natural named-object memory"),
        ):
            update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-natural-named-object-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-natural-named-object",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is named Mira.",
            )
            query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-natural-named-object-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-natural-named-object",
                channel_kind="telegram",
                user_message="What did I name the plant?",
            )
            fragment_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-natural-named-object-fragment-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-natural-named-object",
                channel_kind="telegram",
                user_message="Desk plant?",
            )
            correction = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-natural-named-object-correction",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-natural-named-object",
                channel_kind="telegram",
                user_message="Actually, the tiny desk plant is named Sol.",
            )
            updated_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-natural-named-object-updated-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-natural-named-object",
                channel_kind="telegram",
                user_message="What did I name the plant?",
            )
            history_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-natural-named-object-history-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-natural-named-object",
                channel_kind="telegram",
                user_message="What was the plant called before?",
            )

        self.assertEqual(update.mode, "memory_generic_observation_update")
        self.assertEqual(update.routing_decision, "memory_generic_observation")
        self.assertEqual(
            update.reply_text,
            "I'll remember that your low-stakes test fact is that the tiny desk plant is named Mira.",
        )
        self.assertEqual(query.mode, "memory_open_recall")
        self.assertEqual(query.routing_decision, "memory_open_recall_query")
        self.assertEqual(query.reply_text, "You named the tiny desk plant Mira.")
        self.assertEqual(fragment_query.mode, "memory_open_recall")
        self.assertEqual(fragment_query.reply_text, "You named the tiny desk plant Mira.")
        self.assertEqual(correction.mode, "memory_generic_observation_update")
        self.assertEqual(
            correction.reply_text,
            "I'll remember that your low-stakes test fact is that the tiny desk plant is named Sol.",
        )
        self.assertEqual(updated_query.mode, "memory_open_recall")
        self.assertEqual(updated_query.reply_text, "You named the tiny desk plant Sol.")
        self.assertEqual(history_query.mode, "memory_profile_fact_history")
        self.assertEqual(history_query.reply_text, "Before Sol, the tiny desk plant was named Mira.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        recorded_values = {
            observation["value"]
            for observation in recorded_observations
            if observation["predicate"] == "profile.current_low_stakes_test_fact"
        }
        self.assertIn("the tiny desk plant is named Mira", recorded_values)
        self.assertIn("the tiny desk plant is named Sol", recorded_values)

    def test_build_researcher_reply_recalls_generic_entity_location_fact(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            update = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is on the kitchen shelf.",
            )
            query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location",
                channel_kind="telegram",
                user_message="Where is the desk plant?",
            )

        self.assertEqual(update.mode, "memory_generic_observation_update")
        self.assertEqual(update.routing_decision, "memory_generic_observation")
        self.assertEqual(update.reply_text, "I'll remember that the tiny desk plant is on the kitchen shelf.")
        self.assertEqual(query.mode, "memory_open_recall")
        self.assertEqual(query.reply_text, "The tiny desk plant is on the kitchen shelf.")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        location_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-entity-location-query"
        )
        location_facts = location_event["facts_json"] or {}
        self.assertEqual(location_facts.get("record_count"), 1)
        self.assertEqual(location_facts.get("candidate_record_count"), 1)
        self.assertEqual(location_facts.get("read_method"), "get_current_state")
        self.assertEqual(location_facts.get("retrieved_memory_roles"), ["entity_state"])
        self.assertEqual(location_facts.get("candidate_memory_roles"), ["entity_state"])
        self.assertIn("read_method=get_current_state", location_facts.get("evidence_summary") or "")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        entity_records = [item for item in recorded_observations if item["predicate"] == "entity.location"]
        self.assertTrue(entity_records)
        self.assertEqual(entity_records[0]["metadata"]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(entity_records[0]["metadata"]["location_preposition"], "on")

    def test_build_researcher_reply_keeps_unrelated_entity_locations_isolated(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-plant-name-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-isolation",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is named Sol.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-desk-location-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-isolation",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is on the kitchen shelf.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-office-location-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-isolation",
                channel_kind="telegram",
                user_message="For later, the office plant is on the balcony.",
            )
            desk_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-desk-location-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-isolation",
                channel_kind="telegram",
                user_message="Where is the desk plant?",
            )
            office_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-office-location-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-isolation",
                channel_kind="telegram",
                user_message="Where is the office plant?",
            )

        self.assertEqual(desk_query.mode, "memory_open_recall")
        self.assertEqual(desk_query.reply_text, "The tiny desk plant is on the kitchen shelf.")
        self.assertNotIn("Sol", desk_query.reply_text)
        self.assertEqual(office_query.mode, "memory_open_recall")
        self.assertEqual(office_query.reply_text, "The office plant is on the balcony.")
        self.assertNotIn("Sol", office_query.reply_text)
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        entity_keys = {
            item["metadata"]["entity_key"]
            for item in recorded_observations
            if item["predicate"] == "entity.location"
        }
        self.assertEqual(entity_keys, {"named-object:tiny-desk-plant", "named-object:office-plant"})

    def test_build_researcher_reply_uses_newer_entity_location_after_conflict(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-name",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location-conflict",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is named Sol.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-old",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location-conflict",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is on the kitchen shelf.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-new",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location-conflict",
                channel_kind="telegram",
                user_message="Actually, the tiny desk plant is on the balcony.",
            )
            query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-current",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location-conflict",
                channel_kind="telegram",
                user_message="Where is the desk plant?",
            )
            history_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-location-history",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-location-conflict",
                channel_kind="telegram",
                user_message="Where was the desk plant before?",
            )

        self.assertEqual(query.mode, "memory_open_recall")
        self.assertEqual(query.reply_text, "The tiny desk plant is on the balcony.")
        self.assertNotIn("Sol", query.reply_text)
        self.assertNotIn("kitchen shelf", query.reply_text)
        self.assertEqual(history_query.mode, "memory_entity_state_history")
        self.assertEqual(history_query.routing_decision, "memory_entity_state_history_query")
        self.assertEqual(
            history_query.reply_text,
            "Before the tiny desk plant was on the balcony, it was on the kitchen shelf.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        history_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-entity-location-history"
        )
        history_facts = history_event["facts_json"] or {}
        self.assertEqual(history_facts.get("read_method"), "get_historical_state")
        self.assertEqual(history_facts.get("event_record_count"), 2)
        self.assertIn("read_method=get_historical_state", history_facts.get("evidence_summary") or "")

    def test_build_researcher_reply_uses_newer_entity_owner_after_conflict(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-old",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="For later, Omar owns the launch checklist.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-unrelated",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="For later, Lina owns the investor update.",
            )
            old_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-old-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="Who owns the launch checklist?",
            )
            unrelated_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-unrelated-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="Who owns the investor update?",
            )
            correction = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-new",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="Actually, Maya owns the launch checklist.",
            )
            current_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-current",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="Who owns the launch checklist?",
            )
            history_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-owner-history",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-owner-conflict",
                channel_kind="telegram",
                user_message="Who owned the launch checklist before?",
            )

        self.assertEqual(old_query.mode, "memory_open_recall")
        self.assertEqual(old_query.reply_text, "The launch checklist is owned by Omar.")
        self.assertEqual(unrelated_query.mode, "memory_open_recall")
        self.assertEqual(unrelated_query.reply_text, "The investor update is owned by Lina.")
        self.assertEqual(correction.mode, "memory_generic_observation_update")
        self.assertEqual(correction.routing_decision, "memory_generic_observation")
        self.assertEqual(correction.reply_text, "I'll remember that the launch checklist is owned by Maya.")
        self.assertEqual(current_query.mode, "memory_open_recall")
        self.assertEqual(current_query.reply_text, "The launch checklist is owned by Maya.")
        self.assertNotIn("Omar", current_query.reply_text)
        self.assertNotIn("Lina", current_query.reply_text)
        self.assertEqual(history_query.mode, "memory_entity_state_history")
        self.assertEqual(history_query.routing_decision, "memory_entity_state_history_query")
        self.assertEqual(
            history_query.reply_text,
            "Before the launch checklist was owned by Maya, it was owned by Omar.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        history_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-entity-owner-history"
        )
        history_facts = history_event["facts_json"] or {}
        self.assertEqual(history_facts.get("read_method"), "get_historical_state")
        self.assertEqual(history_facts.get("event_record_count"), 2)
        self.assertIn("read_method=get_historical_state", history_facts.get("evidence_summary") or "")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        owner_records = [item for item in recorded_observations if item["predicate"] == "entity.owner"]
        self.assertEqual(
            {(item["metadata"]["entity_key"], item["value"]) for item in owner_records},
            {
                ("named-object:launch-checklist", "Omar"),
                ("named-object:investor-update", "Lina"),
                ("named-object:launch-checklist", "Maya"),
            },
        )

    def test_build_researcher_reply_handles_extended_entity_attributes(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        def ask(request_id: str, message: str):
            return build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-extended-attributes",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            ask("req-entity-status-old", "For later, the launch checklist status is blocked.")
            ask("req-entity-status-new", "Actually, the launch checklist status is ready.")
            status_query = ask("req-entity-status-query", "What is the status of the launch checklist?")
            status_history = ask(
                "req-entity-status-history",
                "What was the previous status of the launch checklist before?",
            )

            ask("req-entity-deadline-old", "For later, the launch checklist deadline is Friday.")
            ask("req-entity-deadline-new", "Actually, the launch checklist is due Monday.")
            deadline_query = ask("req-entity-deadline-query", "When is the launch checklist due?")
            deadline_history = ask("req-entity-deadline-history", "When was the launch checklist due before?")

            ask("req-entity-relation-old", "For later, the launch checklist relates to investor update.")
            ask("req-entity-relation-new", "Actually, the launch checklist relates to board prep.")
            relation_query = ask("req-entity-relation-query", "What is the launch checklist related to?")
            relation_history = ask(
                "req-entity-relation-history",
                "What was the launch checklist related to before?",
            )

            ask("req-entity-preference-old", "For later, the launch checklist preference is concise bullets.")
            ask("req-entity-preference-new", "Actually, the launch checklist prefers short sections.")
            preference_query = ask("req-entity-preference-query", "What does the launch checklist prefer?")
            preference_history = ask("req-entity-preference-history", "What did the launch checklist prefer before?")

            ask("req-entity-project-old", "For later, the launch checklist active project is Neon Harbor.")
            ask("req-entity-project-new", "Actually, the launch checklist project is Seed Round.")
            project_query = ask("req-entity-project-query", "What project is the launch checklist for?")
            project_history = ask("req-entity-project-history", "What was the previous project for the launch checklist?")

            ask("req-entity-blocker-old", "For later, the GTM launch blocker is landing page copy.")
            ask("req-entity-blocker-new", "Actually, the GTM launch blocker is creator approvals.")
            blocker_query = ask("req-entity-blocker-query", "What is the blocker for the GTM launch?")
            blocker_history = ask("req-entity-blocker-history", "What was the previous blocker for the GTM launch?")

            ask("req-entity-priority-old", "For later, the startup ops priority is hiring pipeline.")
            ask("req-entity-priority-new", "Actually, the startup ops priority is revenue instrumentation.")
            priority_query = ask("req-entity-priority-query", "What is the priority for startup ops?")
            priority_history = ask("req-entity-priority-history", "What was the previous priority for startup ops?")

            ask("req-entity-decision-old", "For later, the investor update decision is founder-led notes.")
            ask("req-entity-decision-new", "Actually, the investor update decision is concise metrics first.")
            decision_query = ask("req-entity-decision-query", "What is the decision for the investor update?")
            decision_history = ask(
                "req-entity-decision-history",
                "What was the previous decision for the investor update?",
            )

            ask("req-entity-direction-old", "For the GTM launch, the onboarding direction is async walkthroughs.")
            ask("req-entity-direction-new", "Actually, the GTM launch onboarding direction is founder-led calls.")
            direction_query = ask(
                "req-entity-direction-query",
                "What is the GTM launch onboarding direction?",
            )
            direction_broad_query = ask(
                "req-entity-direction-broad-query",
                "What onboarding direction were we leaning toward?",
            )
            direction_history = ask(
                "req-entity-direction-history",
                "What was the GTM launch onboarding direction before?",
            )

            ask("req-entity-next-action-old", "For later, the onboarding sprint next action is draft QA prompts.")
            ask("req-entity-next-action-new", "Actually, the onboarding sprint next action is test Stripe recovery.")
            next_action_query = ask(
                "req-entity-next-action-query",
                "What is the next action for the onboarding sprint?",
            )
            next_action_history = ask(
                "req-entity-next-action-history",
                "What was the previous next action for the onboarding sprint?",
            )

            ask("req-entity-metric-old", "For later, the creator campaign metric is replies.")
            ask("req-entity-metric-new", "Actually, the creator campaign metric is booked calls.")
            metric_query = ask("req-entity-metric-query", "What is the metric for the creator campaign?")
            metric_history = ask("req-entity-metric-history", "What was the previous metric for the creator campaign?")

        self.assertEqual(status_query.mode, "memory_open_recall")
        self.assertEqual(status_query.reply_text, "The launch checklist status is ready.")
        self.assertEqual(
            status_history.reply_text,
            "Before the launch checklist status was ready, it was blocked.",
        )
        self.assertEqual(deadline_query.reply_text, "The launch checklist is due Monday.")
        self.assertEqual(
            deadline_history.reply_text,
            "Before the launch checklist was due Monday, it was due Friday.",
        )
        self.assertEqual(relation_query.reply_text, "The launch checklist is related to board prep.")
        self.assertEqual(
            relation_history.reply_text,
            "Before the launch checklist was related to board prep, it was related to investor update.",
        )
        self.assertEqual(preference_query.reply_text, "The launch checklist preference is short sections.")
        self.assertEqual(
            preference_history.reply_text,
            "Before the launch checklist preference was short sections, it was concise bullets.",
        )
        self.assertEqual(project_query.reply_text, "The launch checklist project is Seed Round.")
        self.assertEqual(
            project_history.reply_text,
            "Before the launch checklist project was Seed Round, it was Neon Harbor.",
        )
        self.assertEqual(blocker_query.reply_text, "The GTM launch blocker is creator approvals.")
        self.assertEqual(
            blocker_history.reply_text,
            "Before the GTM launch blocker was creator approvals, it was landing page copy.",
        )
        self.assertEqual(priority_query.reply_text, "The startup ops priority is revenue instrumentation.")
        self.assertEqual(
            priority_history.reply_text,
            "Before the startup ops priority was revenue instrumentation, it was hiring pipeline.",
        )
        self.assertEqual(decision_query.reply_text, "The investor update decision is concise metrics first.")
        self.assertEqual(
            decision_history.reply_text,
            "Before the investor update decision was concise metrics first, it was founder-led notes.",
        )
        self.assertEqual(direction_query.reply_text, "The GTM launch decision is founder-led calls.")
        self.assertEqual(direction_broad_query.reply_text, "The GTM launch decision is founder-led calls.")
        self.assertEqual(
            direction_history.reply_text,
            "Before the GTM launch decision was founder-led calls, it was async walkthroughs.",
        )
        self.assertEqual(next_action_query.reply_text, "The onboarding sprint next action is test Stripe recovery.")
        self.assertEqual(
            next_action_history.reply_text,
            "Before the onboarding sprint next action was test Stripe recovery, it was draft QA prompts.",
        )
        self.assertEqual(metric_query.reply_text, "The creator campaign metric is booked calls.")
        self.assertEqual(
            metric_history.reply_text,
            "Before the creator campaign metric was booked calls, it was replies.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=70)
        preference_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-entity-preference-query"
        )
        preference_facts = preference_event["facts_json"] or {}
        self.assertEqual(preference_facts.get("record_count"), 1)
        self.assertEqual(preference_facts.get("candidate_record_count"), 1)
        self.assertEqual(preference_facts.get("read_method"), "get_current_state")
        self.assertEqual(preference_facts.get("retrieved_memory_roles"), ["entity_state"])
        self.assertEqual(preference_facts.get("candidate_memory_roles"), ["entity_state"])
        self.assertIn("record_count=1", preference_facts.get("evidence_summary") or "")
        self.assertIn("candidate_record_count=1", preference_facts.get("evidence_summary") or "")
        self.assertIn("read_method=get_current_state", preference_facts.get("evidence_summary") or "")
        self.assertIn("retrieved_roles=entity_state", preference_facts.get("evidence_summary") or "")

        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=30)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        predicates = {item["predicate"] for item in recorded_observations}
        self.assertTrue(
            {
                "entity.status",
                "entity.deadline",
                "entity.relation",
                "entity.preference",
                "entity.project",
                "entity.blocker",
                "entity.priority",
                "entity.decision",
                "entity.next_action",
                "entity.metric",
            }.issubset(predicates)
        )

    def test_entity_history_followup_uses_last_current_entity_attribute(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        def ask(request_id: str, message: str):
            return build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-history-followup",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            ask("req-followup-direction-old", "For the GTM launch, the onboarding direction is async walkthroughs.")
            ask("req-followup-direction-new", "Actually, the GTM launch onboarding direction is founder-led calls.")
            current_query = ask("req-followup-direction-current", "What is the GTM launch onboarding direction?")
            history_followup = ask("req-followup-direction-history", "What was it before?")
            source_query = ask(
                "req-followup-direction-source",
                "Explain the memory sources you used for your previous answer, and say which ones were current truth versus support.",
            )

        self.assertEqual(current_query.mode, "memory_open_recall")
        self.assertEqual(current_query.reply_text, "The GTM launch decision is founder-led calls.")
        self.assertEqual(history_followup.mode, "memory_entity_state_history")
        self.assertEqual(history_followup.routing_decision, "memory_entity_state_history_query")
        self.assertEqual(
            history_followup.reply_text,
            "Before the GTM launch decision was founder-led calls, it was async walkthroughs.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        history_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-followup-direction-history"
        )
        history_facts = history_event["facts_json"] or {}
        self.assertTrue(history_facts.get("followup_resolved"))
        self.assertEqual(history_facts.get("followup_resolved_from_request_id"), "req-followup-direction-current")
        self.assertEqual(history_facts.get("attribute"), "decision")
        self.assertEqual(history_facts.get("topic"), "GTM launch")
        self.assertEqual(history_facts.get("read_method"), "get_historical_state")
        self.assertEqual(source_query.mode, "context_source_debug")
        self.assertIn("entity-state history route", source_query.reply_text)
        self.assertIn("topic: GTM launch", source_query.reply_text)

    def test_build_researcher_reply_summarizes_entity_state_across_attributes(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        def ask(request_id: str, message: str):
            return build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-state-summary",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for entity state summary memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for entity state summary memory"),
        ):
            ask("req-entity-summary-status", "For later, the GTM launch status is ready.")
            ask("req-entity-summary-owner", "For later, Omar owns the GTM launch.")
            ask("req-entity-summary-deadline", "For later, the GTM launch deadline is Friday.")
            ask("req-entity-summary-blocker", "For later, the GTM launch blocker is creator approvals.")
            ask("req-entity-summary-priority", "For later, the GTM launch priority is creator approvals.")
            ask("req-entity-summary-decision", "For later, the GTM launch decision is concise landing page first.")
            ask("req-entity-summary-next-action", "For later, the GTM launch next action is get creator signoff.")
            ask("req-entity-summary-metric", "For later, the GTM launch metric is booked calls.")
            summary_query = ask("req-entity-summary-query", "What do you know about the GTM launch?")
            source_query = ask("req-entity-summary-source", "Why did you answer that?")

        self.assertEqual(summary_query.mode, "memory_entity_state_summary")
        self.assertEqual(summary_query.routing_decision, "memory_entity_state_summary_query")
        self.assertIn("Here is what I have for the GTM launch:", summary_query.reply_text)
        for expected_line in (
            "- Status: ready",
            "- Owner: Omar",
            "- Deadline: Friday",
            "- Blocker: creator approvals",
            "- Priority: creator approvals",
            "- Decision: concise landing page first",
            "- Next action: get creator signoff",
            "- Metric: booked calls",
        ):
            self.assertIn(expected_line, summary_query.reply_text)
        self.assertNotEqual(summary_query.reply_text, "The GTM launch blocker is creator approvals.")

        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        summary_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-entity-summary-query"
        )
        summary_facts = summary_event["facts_json"] or {}
        self.assertEqual(summary_facts.get("bridge_mode"), "memory_entity_state_summary")
        self.assertEqual(summary_facts.get("read_method"), "get_current_state")
        self.assertEqual(summary_facts.get("attribute_count"), 8)
        self.assertEqual(
            summary_facts.get("attributes"),
            ["status", "owner", "deadline", "blocker", "priority", "decision", "next_action", "metric"],
        )
        self.assertIn("attribute_count=8", summary_facts.get("evidence_summary") or "")
        self.assertIn("read_method=get_current_state", summary_facts.get("evidence_summary") or "")

        self.assertEqual(source_query.mode, "context_source_debug")
        self.assertIn("entity-state summary route", source_query.reply_text)
        self.assertIn("source: entity_state current summary records", source_query.reply_text)
        self.assertIn("attributes: status, owner, deadline, blocker, priority, decision, next_action, metric", source_query.reply_text)
        self.assertIn("current state was the authority", source_query.reply_text)

    def test_entity_history_does_not_treat_duplicate_same_value_as_previous(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        def ask(request_id: str, message: str):
            return build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=request_id,
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-duplicate-history",
                channel_kind="telegram",
                user_message=message,
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            ask("req-entity-next-action-dup-1", "For later, the onboarding sprint next action is draft QA prompts.")
            ask("req-entity-next-action-dup-2", "For later, the onboarding sprint next action is draft QA prompts.")
            current_query = ask("req-entity-next-action-dup-current", "What is the next action for the onboarding sprint?")
            history_query = ask(
                "req-entity-next-action-dup-history",
                "What was the previous next action for the onboarding sprint?",
            )
            source_query = ask("req-entity-next-action-dup-source", "Why did you answer that?")

        self.assertEqual(current_query.reply_text, "The onboarding sprint next action is draft QA prompts.")
        self.assertEqual(history_query.mode, "memory_entity_state_history")
        self.assertEqual(
            history_query.reply_text,
            "I don't currently have an earlier saved next_action for that.",
        )
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        history_event = next(
            event
            for event in tool_events
            if event["request_id"] == "req-entity-next-action-dup-history"
        )
        history_facts = history_event["facts_json"] or {}
        self.assertEqual(history_facts.get("read_method"), "get_historical_state")
        self.assertFalse(history_facts.get("previous_value_found"))
        self.assertIn("previous_value_found=no", history_facts.get("evidence_summary") or "")
        self.assertIn("read_method=get_historical_state", history_facts.get("evidence_summary") or "")
        self.assertNotIn("read_method=retrieve_events", history_facts.get("evidence_summary") or "")
        self.assertEqual(source_query.mode, "context_source_debug")
        self.assertIn("no distinct previous value", source_query.reply_text)
        self.assertIn("read_method: get_historical_state", source_query.reply_text)
        self.assertNotIn("read_method: retrieve_events", source_query.reply_text)

    def test_build_researcher_reply_deletes_one_entity_location_without_affecting_another(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic entity memory"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic entity memory"),
        ):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-delete-desk-seed",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-delete",
                channel_kind="telegram",
                user_message="For later, the tiny desk plant is on the kitchen shelf.",
            )
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-delete-office-seed",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-delete",
                channel_kind="telegram",
                user_message="For later, the office plant is on the balcony.",
            )
            deletion = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-delete-desk",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-delete",
                channel_kind="telegram",
                user_message="Forget where the tiny desk plant is.",
            )
            desk_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-delete-desk-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-delete",
                channel_kind="telegram",
                user_message="Where is the desk plant?",
            )
            office_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-entity-delete-office-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-entity-delete",
                channel_kind="telegram",
                user_message="Where is the office plant?",
            )

        self.assertEqual(deletion.mode, "memory_generic_observation_delete")
        self.assertEqual(deletion.reply_text, "I'll forget the tiny desk plant location.")
        self.assertEqual(desk_query.mode, "memory_open_recall")
        self.assertEqual(desk_query.reply_text, "I don't currently have saved location for that.")
        self.assertEqual(office_query.mode, "memory_open_recall")
        self.assertEqual(office_query.reply_text, "The office plant is on the balcony.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        deletion_records = [
            item
            for item in recorded_observations
            if item["predicate"] == "entity.location" and item["operation"] == "delete"
        ]
        self.assertTrue(deletion_records)
        self.assertEqual(deletion_records[0]["metadata"]["entity_key"], "named-object:tiny-desk-plant")

    def test_build_researcher_reply_directly_acknowledges_current_focus_correction_with_researcher_enabled(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for explicit current-state correction"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for explicit current-state correction"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-focus-correction-direct-ack",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-focus-correction-direct-ack",
                channel_kind="telegram",
                user_message="Actually, my current focus is diagnostics scan verification.",
            )

        self.assertEqual(result.mode, "memory_generic_observation_update")
        self.assertEqual(result.routing_decision, "memory_generic_observation")
        self.assertEqual(
            result.reply_text,
            "I'll remember that your current focus is diagnostics scan verification.",
        )

    def test_build_researcher_reply_closes_focus_and_sets_new_focus_durably(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for current focus transition"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for current focus transition"),
        ):
            transition = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-focus-transition",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-focus-transition",
                channel_kind="telegram",
                user_message=(
                    "Mark context capsule verification closed. "
                    "Set my current focus to persistent memory quality evaluation."
                ),
            )
            focus_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-focus-transition-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-focus-transition",
                channel_kind="telegram",
                user_message="What is my current focus?",
            )
            compound_query = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-focus-transition-compound-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-focus-transition",
                channel_kind="telegram",
                user_message="What is my current focus, what did we just close, and what should we evaluate next?",
            )

        self.assertEqual(transition.routing_decision, "current_focus_transition")
        self.assertIn("context capsule verification is closed", transition.reply_text)
        self.assertIn("New focus: persistent memory quality evaluation.", transition.reply_text)
        self.assertEqual(focus_query.reply_text, "Your current focus is persistent memory quality evaluation.")
        self.assertEqual(compound_query.routing_decision, "active_context_status")
        self.assertIn("Focus: persistent memory quality evaluation", compound_query.reply_text)
        self.assertIn("Recently closed", compound_query.reply_text)
        self.assertIn("context capsule verification", compound_query.reply_text)
        self.assertIn("Evaluate whether current focus updates survive across a new turn", compound_query.reply_text)

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
            "Saved as a decision about general: launch Atlas through agency partners first.\n\n"
            "I will treat older discussion as supporting context unless current state says otherwise.",
        )
        self.assertEqual(result.mode, "memory_explicit_decision_capture")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
        ]
        self.assertIn(
            {
                "predicate": "profile.current_decision",
                "value": "launch Atlas through agency partners first",
            },
            [
                {
                    "predicate": observation.get("predicate"),
                    "value": observation.get("value"),
                }
                for observation in recorded_observations
            ],
        )

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

    def test_build_researcher_reply_persists_still_owned_by_as_generic_owner_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for generic owner observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for generic owner observations"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-generic-owner-still-update",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-owner-still-update",
                channel_kind="telegram",
                user_message="The onboarding rollout is still owned by Nadia during security review.",
            )

        self.assertEqual(
            result.reply_text,
            "I'll remember that your current owner is Nadia.",
        )
        self.assertEqual(result.mode, "memory_generic_observation_update")
        self.assertEqual(result.routing_decision, "memory_generic_observation")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "profile.current_owner")
        self.assertEqual(recorded_observations[0]["value"], "Nadia")

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

    def test_build_researcher_reply_handles_multiple_generic_deletions_in_one_turn(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        detected_deletions = detect_telegram_generic_deletions(
            "Forget my cofounder.\nForget my current owner.\nForget who owns the launch checklist."
        )
        self.assertEqual(
            [deletion.predicate for deletion in detected_deletions],
            ["profile.cofounder_name", "profile.current_owner", "entity.owner"],
        )

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete-cofounder-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message="My cofounder is Omar.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete-owner-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message="My current owner is Maya.",
        )
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete-launch-owner-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message="For later, Maya owns the launch checklist.",
        )

        delete_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message=(
                "Forget my cofounder.\n"
                "Forget my current owner.\n"
                "Forget who owns the launch checklist."
            ),
        )
        cofounder_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete-cofounder-query",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message="Who is my cofounder?",
        )
        owner_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete-owner-query",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message="Who is the owner?",
        )
        launch_owner_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-multi-delete-launch-owner-query",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-multi-delete",
            channel_kind="telegram",
            user_message="Who owns the launch checklist?",
        )

        self.assertEqual(
            delete_result.reply_text,
            "I'll forget your cofounder, your current owner, and the launch checklist owner.",
        )
        self.assertIn("delete_count=3", delete_result.evidence_summary)
        self.assertEqual(cofounder_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(owner_result.reply_text, "I don't currently have that saved.")
        self.assertEqual(launch_owner_result.reply_text, "I don't currently have saved owner for that.")

        doctor_report = run_memory_doctor(self.state_db)
        self.assertTrue(doctor_report.ok)
        self.assertIn("No partial multi-delete writes detected", doctor_report.to_text())

    def test_memory_doctor_flags_partial_multi_delete_writes(self) -> None:
        message_text = (
            "Forget my cofounder.\n"
            "Forget my current owner.\n"
            "Forget who owns the launch checklist."
        )
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="Researcher bridge recorded memory delete influence.",
            request_id="req-partial-delete",
            facts={
                "detected_generic_memory_deletion": {
                    "predicate": "profile.cofounder_name",
                    "fact_name": "cofounder",
                    "label": "cofounder",
                    "message_text": message_text,
                },
            },
        )
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark memory write requested.",
            request_id="req-partial-delete",
            facts={
                "operation": "delete",
                "method": "write_observation",
                "memory_role": "current_state",
            },
        )
        record_event(
            self.state_db,
            event_type="memory_write_succeeded",
            component="memory_orchestrator",
            summary="Spark memory write completed.",
            request_id="req-partial-delete",
            facts={
                "operation": "delete",
                "method": "write_observation",
                "memory_role": "current_state",
                "accepted_count": 1,
            },
        )

        doctor_report = run_memory_doctor(self.state_db)

        self.assertFalse(doctor_report.ok, doctor_report.to_json())
        self.assertEqual(doctor_report.scanned_multi_delete_turns, 1)
        self.assertIn("expected 3 delete write(s), requested 1, accepted 1", doctor_report.to_text())

    def test_memory_doctor_flags_forget_postcondition_active_state_presence(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-forget-postcondition-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-forget-postcondition",
            channel_kind="telegram",
            user_message="Our owner is Maya.",
        )
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="Researcher bridge recorded memory delete influence.",
            request_id="req-forget-postcondition-delete",
            human_id="human-1",
            facts={
                "detected_generic_memory_deletion": {
                    "predicate": "profile.current_owner",
                    "fact_name": "current_owner",
                    "label": "current owner",
                    "message_text": "Forget my current owner.",
                },
            },
        )
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark memory write requested.",
            request_id="req-forget-postcondition-delete",
            human_id="human-1",
            facts={
                "operation": "delete",
                "method": "write_observation",
                "memory_role": "current_state",
                "predicates": ["profile.current_owner"],
                "observations": [{"predicate": "profile.current_owner", "operation": "delete"}],
            },
        )
        record_event(
            self.state_db,
            event_type="memory_write_succeeded",
            component="memory_orchestrator",
            summary="Spark memory write completed.",
            request_id="req-forget-postcondition-delete",
            human_id="human-1",
            facts={
                "operation": "delete",
                "method": "write_observation",
                "memory_role": "current_state",
                "accepted_count": 1,
            },
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
        )

        self.assertFalse(doctor_report.ok, doctor_report.to_json())
        self.assertIn("memory_forget_postcondition_failed", doctor_report.to_text())
        self.assertIn("active current-state memory still contains: current owner", doctor_report.to_text())
        self.assertEqual(
            doctor_report.movement_trace["gaps"][0]["name"],
            "memory_forget_postcondition_failed",
        )
        stages = {stage["stage"]: stage for stage in doctor_report.movement_trace["stages"]}
        self.assertEqual(stages["forget_postconditions"]["stale_target_count"], 1)
        cases = {case["category"]: case for case in doctor_report.benchmark["cases"]}
        self.assertEqual(cases["forgetting"]["status"], "fail")
        self.assertIn("active current-state memory", cases["forgetting"]["detail"])
        self.assertIn("delete postconditions", doctor_report.recommendations[0])

    def test_memory_doctor_reports_topic_active_profile_conflict(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-memory-doctor-topic-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-memory-doctor-topic",
            channel_kind="telegram",
            user_message="Our owner is Maya.",
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Maya",
        )

        self.assertTrue(doctor_report.ok, doctor_report.to_json())
        self.assertIn("topic 'Maya' is active in: current_owner", doctor_report.to_text())
        self.assertIn("If Maya is wrong in that active field", doctor_report.to_telegram_text())

    def test_memory_doctor_scans_entity_current_state_for_topic_presence(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-memory-doctor-entity-topic-seed",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-memory-doctor-entity-topic",
            channel_kind="telegram",
            user_message="For later, Maya owns the launch checklist.",
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Maya",
        )

        self.assertTrue(doctor_report.ok, doctor_report.to_json())
        self.assertEqual(doctor_report.active_profile["current_state"]["record_count"], 1)
        self.assertIn(
            "topic 'Maya' is active in: entity.owner:launch checklist.owner",
            doctor_report.to_text(),
        )
        self.assertIn("Current-state scan: 1 record(s).", doctor_report.to_telegram_text())

    def test_memory_doctor_surfaces_context_capsule_recent_conversation_gap(self) -> None:
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-context-gap",
            human_id="human-1",
            facts={
                "source_counts": {
                    "current_state": 3,
                    "recent_conversation": 0,
                    "task_recovery": 2,
                },
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 3, "priority": 1, "role": "authority"},
                    {
                        "source": "recent_conversation",
                        "present": False,
                        "count": 0,
                        "priority": 8,
                        "role": "supporting",
                    },
                ],
            },
        )

        doctor_report = run_memory_doctor(self.state_db, human_id="human-1")

        self.assertTrue(doctor_report.ok, doctor_report.to_json())
        self.assertIn("context_capsule_recent_conversation_gap", doctor_report.to_text())
        self.assertIn(
            "Context capsule: no recent-conversation turns",
            doctor_report.to_telegram_text(),
        )

    def test_memory_doctor_flags_gateway_messages_missing_from_context_capsule(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-context-prev",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-context-gap",
                "user_message_preview": "i just told you",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-context-gap",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-context-gap",
                "user_message_preview": "are you there",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-context-gap",
            human_id="human-1",
            facts={
                "source_counts": {
                    "current_state": 3,
                    "recent_conversation": 0,
                    "task_recovery": 2,
                },
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 3, "priority": 1, "role": "authority"},
                    {
                        "source": "recent_conversation",
                        "present": False,
                        "count": 0,
                        "priority": 8,
                        "role": "supporting",
                    },
                ],
            },
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
        )

        self.assertFalse(doctor_report.ok, doctor_report.to_json())
        self.assertEqual(
            doctor_report.context_capsule["gateway_trace"]["recent_gateway_message_count"],
            1,
        )
        self.assertIn("context_capsule_gateway_trace_gap", doctor_report.to_text())
        self.assertIn("gateway had 1 earlier same-session message", doctor_report.to_telegram_text())
        self.assertEqual(
            doctor_report.movement_trace["gaps"][0]["name"],
            "gateway_to_context_capsule_gap",
        )

    def test_memory_doctor_replays_context_capsule_by_request_id(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-request-replay-seed",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-request-replay",
                "user_message_preview": "The probe phrase is Cedar Compass 509.",
                "response_preview": "Noted.",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-request-replay-target",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-request-replay",
                "user_message_preview": "What phrase did I just give you? Answer with only the phrase.",
                "response_preview": "I can see context capsule but not the prior message.",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Failed request context capsule.",
            request_id="req-request-replay-target",
            human_id="human-1",
            facts={
                "source_counts": {"current_state": 1, "recent_conversation": 0},
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {
                        "source": "recent_conversation",
                        "present": False,
                        "count": 0,
                        "priority": 8,
                        "role": "supporting",
                    },
                ],
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Newer healthy context capsule.",
            request_id="req-request-replay-latest",
            human_id="human-1",
            facts={
                "source_counts": {"current_state": 1, "recent_conversation": 2},
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {
                        "source": "recent_conversation",
                        "present": True,
                        "count": 2,
                        "priority": 8,
                        "role": "supporting",
                    },
                ],
            },
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Cedar Compass 509",
            request_id="req-request-replay-target",
        )

        self.assertFalse(doctor_report.ok, doctor_report.to_json())
        self.assertEqual(doctor_report.context_capsule["request_id"], "req-request-replay-target")
        self.assertTrue(doctor_report.context_capsule["gateway_trace"]["lineage_gap"])
        self.assertIn("context_capsule_gateway_trace_gap", doctor_report.to_text())
        self.assertIn("request=req-request-replay-target", doctor_report.to_text())
        self.assertIn("Request: req-request-replay-target.", doctor_report.to_telegram_text())

    def test_memory_doctor_flags_close_turn_answer_grounding_gap(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-close-turn-seed",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-close-turn",
                "user_message_preview": "The probe phrase is Cedar Compass 509.",
                "response_preview": "Noted.",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-close-turn-current",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-close-turn",
                "user_message_preview": "What phrase did I just give you? Answer with only the phrase.",
                "response_preview": "Beneficial single mutations may compound when applied together.",
                "bridge_mode": "external_autodiscovered",
                "routing_decision": "researcher_advisory",
                "evidence_summary": "status=partial packets=1 stability=provisional_only",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-close-turn-current",
            human_id="human-1",
            facts={
                "source_counts": {
                    "current_state": 1,
                    "recent_conversation": 2,
                },
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {
                        "source": "recent_conversation",
                        "present": True,
                        "count": 2,
                        "priority": 8,
                        "role": "supporting",
                    },
                ],
            },
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Cedar Compass 509",
        )

        self.assertFalse(doctor_report.ok, doctor_report.to_json())
        self.assertTrue(doctor_report.context_capsule["gateway_trace"]["answer_topic_miss"])
        self.assertIn("context_to_answer_grounding_gap", doctor_report.to_text())
        self.assertIn("Answer grounding: recent context contained the expected topic", doctor_report.to_telegram_text())
        self.assertEqual(
            doctor_report.movement_trace["gaps"][0]["name"],
            "context_to_answer_grounding_gap",
        )
        self.assertEqual(doctor_report.benchmark["weakest_case"]["category"], "close_turn_recall")
        self.assertEqual(doctor_report.benchmark["weakest_case"]["status"], "fail")

        unrelated_topic_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Maya",
        )

        self.assertTrue(unrelated_topic_report.ok, unrelated_topic_report.to_json())
        self.assertFalse(unrelated_topic_report.context_capsule["gateway_trace"]["route_contamination"])

    def test_memory_doctor_flags_delivery_answer_grounding_gap(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-delivery-seed",
                "update_id": "update-seed",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-delivery-gap",
                "user_message_preview": "The probe phrase is Cedar Compass 509.",
                "response_preview": "Noted.",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-delivery-current",
                "update_id": "update-current",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-delivery-gap",
                "user_message_preview": "What phrase did I just give you? Answer with only the phrase.",
                "response_preview": "Cedar Compass 509",
                "bridge_mode": "recent_conversation_recall",
                "routing_decision": "recent_conversation_recall",
                "evidence_summary": "status=recent_conversation recall=direct",
            },
        )
        append_outbound_audit(
            self.config_manager,
            {
                "event": "telegram_bridge_outbound",
                "channel_id": "telegram",
                "update_id": "update-current",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-delivery-gap",
                "delivery_ok": True,
                "user_message_preview": "What phrase did I just give you? Answer with only the phrase.",
                "response_preview": "Beneficial single mutations may compound when applied together.",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-delivery-current",
            human_id="human-1",
            facts={
                "source_counts": {
                    "current_state": 1,
                    "recent_conversation": 2,
                },
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {
                        "source": "recent_conversation",
                        "present": True,
                        "count": 2,
                        "priority": 8,
                        "role": "supporting",
                    },
                ],
            },
        )

        doctor_report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            topic="Cedar Compass 509",
        )

        delivery_trace = doctor_report.context_capsule["gateway_trace"]["delivery_trace"]
        self.assertFalse(doctor_report.ok, doctor_report.to_json())
        self.assertTrue(delivery_trace["response_mismatch"])
        self.assertTrue(delivery_trace["delivery_topic_miss"])
        self.assertIn("delivery_answer_grounding_gap", doctor_report.to_text())
        self.assertIn("Delivery: generated reply contained the expected topic", doctor_report.to_telegram_text())
        self.assertEqual(
            doctor_report.movement_trace["gaps"][0]["name"],
            "delivery_answer_grounding_gap",
        )
        stages = {stage["stage"]: stage for stage in doctor_report.movement_trace["stages"]}
        self.assertTrue(stages["telegram_delivery_trace"]["delivery_topic_miss"])

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

    def test_build_researcher_reply_preserves_long_run_generic_memory_churn_across_lanes(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        seed_messages = (
            "Our owner is Omar.",
            "Our main risk is enterprise churn during onboarding.",
            "The current owner is Sara.",
            "Our dependency is Stripe approval.",
            "Forget our owner.",
            "The current owner is Nadia.",
            "The biggest risk is delayed product instrumentation.",
            "Forget our risk.",
            "Our main risk is model drift in onboarding scoring.",
        )
        for index, message in enumerate(seed_messages, start=1):
            build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=f"req-generic-long-run-{index}",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-generic-long-run",
                channel_kind="telegram",
                user_message=message,
            )

        owner_current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-owner-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="Who is the owner?",
        )
        owner_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-owner-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="What was the owner before?",
        )
        owner_event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-owner-events",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="Show our owner history.",
        )
        risk_current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-risk-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="What is our risk?",
        )
        risk_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-risk-history",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="What was the risk before?",
        )
        risk_event_history_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-risk-events",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="Show our risk history.",
        )
        dependency_current_result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-generic-long-run-dependency-current",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-generic-long-run",
            channel_kind="telegram",
            user_message="What is our dependency?",
        )

        self.assertEqual(owner_current_result.reply_text, "Your current owner is Nadia.")
        self.assertEqual(
            owner_history_result.reply_text,
            "Before your current owner was Nadia, it was Sara.",
        )
        self.assertEqual(
            owner_event_history_result.reply_text,
            "I have 3 saved current owner events: Omar then Sara then Nadia.",
        )
        self.assertEqual(
            risk_current_result.reply_text,
            "Your current risk is model drift in onboarding scoring.",
        )
        self.assertEqual(
            risk_history_result.reply_text,
            "Before your current risk was model drift in onboarding scoring, it was delayed product instrumentation.",
        )
        self.assertEqual(
            risk_event_history_result.reply_text,
            "I have 3 saved current risk events: enterprise churn during onboarding then delayed product instrumentation then model drift in onboarding scoring.",
        )
        self.assertEqual(
            dependency_current_result.reply_text,
            "Your current dependency is Stripe approval.",
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
