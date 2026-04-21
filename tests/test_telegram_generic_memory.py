from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory.generic_observations import (
    assess_telegram_generic_memory_candidate,
    classify_telegram_generic_memory_candidate,
)
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
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        facts = tool_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("domain_pack"), "relationships")
        self.assertEqual(facts.get("memory_role"), "current_state")
        self.assertEqual(facts.get("retention_class"), "durable_profile")

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

    def test_build_researcher_reply_records_uncaptured_memory_candidate_assessment(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for structured evidence observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for structured evidence observations"),
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
        self.assertEqual(result.mode, "memory_structured_evidence_update")
        self.assertEqual(result.routing_decision, "memory_structured_evidence_observation")
        self.assertIn("save that as structured evidence", result.reply_text.lower())
        self.assertIn("Stripe verification fails", result.reply_text)
        assessment_events = latest_events_by_type(self.state_db, event_type="memory_candidate_assessed", limit=10)
        self.assertTrue(assessment_events)
        facts = assessment_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("outcome"), "structured_evidence")
        self.assertEqual(facts.get("memory_role"), "structured_evidence")
        self.assertEqual(facts.get("retention_class"), "episodic_archive")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        write_facts = write_events[0]["facts_json"] or {}
        self.assertEqual(write_facts.get("memory_role"), "structured_evidence")
        recorded_observations = write_facts.get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "evidence.telegram.evidence")
        self.assertEqual(recorded_observations[0]["retention_class"], "episodic_archive")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        tool_facts = tool_events[0]["facts_json"] or {}
        self.assertEqual(tool_facts.get("bridge_mode"), "memory_structured_evidence_update")
        self.assertEqual(tool_facts.get("routing_decision"), "memory_structured_evidence_observation")
        self.assertEqual(tool_facts.get("memory_role"), "structured_evidence")

    def test_build_researcher_reply_persists_belief_candidate_as_derived_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for belief observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for belief observations"),
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
        self.assertEqual(result.mode, "memory_belief_update")
        self.assertEqual(result.routing_decision, "memory_belief_observation")
        self.assertIn("save that as a belief", result.reply_text.lower())
        self.assertIn("hands-on onboarding", result.reply_text)
        assessment_events = latest_events_by_type(self.state_db, event_type="memory_candidate_assessed", limit=10)
        self.assertTrue(assessment_events)
        facts = assessment_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("outcome"), "belief_candidate")
        self.assertEqual(facts.get("retention_class"), "derived_belief")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        write_facts = write_events[0]["facts_json"] or {}
        self.assertEqual(write_facts.get("memory_role"), "belief")
        recorded_observations = write_facts.get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(recorded_observations[0]["retention_class"], "derived_belief")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        tool_facts = tool_events[0]["facts_json"] or {}
        self.assertEqual(tool_facts.get("bridge_mode"), "memory_belief_update")
        self.assertEqual(tool_facts.get("routing_decision"), "memory_belief_observation")
        self.assertEqual(tool_facts.get("memory_role"), "belief_candidate")

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
        self.assertEqual(facts.get("newer_evidence_count"), 1)
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
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for raw episode observations"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for raw episode observations"),
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
        self.assertEqual(result.mode, "memory_raw_episode_update")
        self.assertEqual(result.routing_decision, "memory_raw_episode_observation")
        self.assertIn("save that as a raw episode", result.reply_text.lower())
        self.assertIn("pricing page felt confusing", result.reply_text)
        assessment_events = latest_events_by_type(self.state_db, event_type="memory_candidate_assessed", limit=10)
        self.assertTrue(assessment_events)
        facts = assessment_events[0]["facts_json"] or {}
        self.assertEqual(facts.get("outcome"), "raw_episode")
        self.assertEqual(facts.get("memory_role"), "raw_episode")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        write_facts = write_events[0]["facts_json"] or {}
        self.assertEqual(write_facts.get("memory_role"), "episodic")
        recorded_observations = write_facts.get("observations") or []
        self.assertEqual(recorded_observations[0]["predicate"], "raw_turn")
        self.assertEqual(recorded_observations[0]["retention_class"], "episodic_archive")
        tool_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(tool_events)
        tool_facts = tool_events[0]["facts_json"] or {}
        self.assertEqual(tool_facts.get("bridge_mode"), "memory_raw_episode_update")
        self.assertEqual(tool_facts.get("routing_decision"), "memory_raw_episode_observation")
        self.assertEqual(tool_facts.get("memory_role"), "raw_episode")

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

    def test_build_researcher_reply_promotes_repeated_evidence_into_belief_recall(self) -> None:
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
        self.assertIn("inferred belief", result.reply_text.lower())
        self.assertIn("stripe verification fails", result.reply_text.lower())
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
        self.assertIn("belief", facts.get("retrieved_memory_roles") or [])

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
