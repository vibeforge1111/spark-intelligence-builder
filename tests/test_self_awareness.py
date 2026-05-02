from __future__ import annotations

import json

from spark_intelligence.observability.store import record_event
from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.memory import run_memory_sdk_smoke_test
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply
from spark_intelligence.self_awareness import build_self_awareness_capsule, build_self_improvement_plan

from tests.test_support import SparkTestCase, create_fake_hook_chip, make_telegram_update


class SelfAwarenessCapsuleTests(SparkTestCase):
    def test_self_awareness_capsule_separates_observed_recent_unverified_lacks_and_improvements(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Startup chip route succeeded",
            status="succeeded",
            facts={
                "routing_decision": "researcher_advisory",
                "bridge_mode": "external_typed",
                "active_chip_key": "startup-yc",
                "route_latency_ms": 432,
                "eval_suite": "self-awareness-route-regression",
            },
            provenance={"source_kind": "chip_hook", "source_ref": "startup-yc"},
        )
        record_event(
            self.state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Browser route timed out",
            status="failed",
            facts={
                "routing_decision": "browser_search",
                "failure_reason": "timeout",
            },
            provenance={"source_kind": "route", "source_ref": "browser_search"},
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="where do you lack and how can you improve?",
        )
        payload = capsule.to_payload()

        self.assertTrue(payload["observed_now"])
        self.assertTrue(payload["recently_verified"])
        self.assertTrue(payload["available_unverified"])
        self.assertTrue(payload["lacks"])
        self.assertTrue(payload["improvement_options"])
        self.assertTrue(payload["capability_evidence"])
        self.assertTrue(payload["natural_language_routes"])
        lack_text = json.dumps(payload["lacks"])
        self.assertIn("Registry visibility does not prove", lack_text)
        self.assertIn("Natural-language invocability", lack_text)
        recent_text = json.dumps(payload["recently_verified"])
        self.assertIn("startup-yc", recent_text)
        evidence_text = json.dumps(payload["capability_evidence"])
        self.assertIn("last_success_at", evidence_text)
        self.assertIn("last_failure_at", evidence_text)
        self.assertIn("route_latency_ms", evidence_text)
        self.assertIn("eval_coverage_status", evidence_text)

    def test_self_status_cli_emits_machine_readable_capsule(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "status",
            "--home",
            str(self.home),
            "--user-message",
            "what can you improve?",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["workspace_id"], "default")
        self.assertIn("observed_now", payload)
        self.assertIn("capability_evidence", payload)
        self.assertIn("lacks", payload)
        self.assertIn("improvement_options", payload)
        self.assertIn("source_ledger", payload)
        self.assertIn("memory_cognition", payload)
        self.assertNotIn("self_status_memory", payload["memory_cognition"])

    def test_self_awareness_adds_memory_cognition_after_wiki_source_families_are_visible(self) -> None:
        kb_dir = self.home / "artifacts" / "spark-memory-kb" / "wiki" / "current-state"
        kb_dir.mkdir(parents=True)
        (kb_dir / "runtime.md").write_text(
            "---\n"
            "title: Runtime Memory KB\n"
            "authority: supporting_not_authoritative\n"
            "owner_system: domain-chip-memory\n"
            "wiki_family: memory_kb_current_state\n"
            "scope_kind: governed_memory\n"
            "source_of_truth: SparkMemorySDK\n"
            "freshness: snapshot_generated\n"
            "---\n"
            "# Runtime Memory KB\n\nCurrent-state memory snapshots are downstream of governed memory.",
            encoding="utf-8",
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="how does your memory cognition work?",
        )
        payload = capsule.to_payload()

        cognition = payload["memory_cognition"]
        self.assertTrue(cognition["wiki_packets"]["source_families_visible"])
        self.assertTrue(cognition["wiki_packets"]["memory_kb"]["present"])
        self.assertEqual(cognition["wiki_packets"]["memory_kb"]["authority"], "supporting_not_authoritative")
        self.assertEqual(cognition["self_status_memory"]["current_state_for_mutable_user_facts"], "authoritative")
        self.assertIn("current_state_memory_outranks_wiki", cognition["authority_boundary"])
        self.assertIn("Memory cognition", capsule.to_text())

    def test_self_status_cli_can_refresh_wiki_and_include_wiki_context(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "status",
            "--home",
            str(self.home),
            "--user-message",
            "where do you lack and what systems can you call?",
            "--refresh-wiki",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["wiki_refresh"]["authority"], "supporting_not_authoritative")
        self.assertIn("system/current-system-status.md", payload["wiki_refresh"]["generated_files"])
        self.assertEqual(payload["wiki_context"]["wiki_status"], "supported")
        self.assertTrue(payload["wiki_context"]["project_knowledge_first"])

    def test_self_improvement_plan_combines_live_lacks_with_wiki_support(self) -> None:
        result = build_self_improvement_plan(
            config_manager=self.config_manager,
            state_db=self.state_db,
            goal="Improve natural-language invocability and capability confidence",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            refresh_wiki=True,
            limit=3,
        )

        payload = result.payload
        self.assertEqual(payload["mode"], "plan_only_probe_first")
        self.assertEqual(payload["evidence_level"], "live_self_snapshot_with_wiki_support")
        self.assertTrue(payload["priority_actions"])
        self.assertTrue(payload["wiki_sources"])
        self.assertIn("probe", payload["guardrail"].lower())
        action_text = json.dumps(payload["priority_actions"])
        self.assertIn("evidence_to_collect", action_text)
        self.assertIn("Natural-language invocability", action_text)

    def test_self_improve_cli_emits_machine_readable_plan(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "improve",
            "Improve weak spots in self-awareness",
            "--home",
            str(self.home),
            "--refresh-wiki",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["authority"], "current_snapshot_plus_supporting_wiki_not_execution")
        self.assertTrue(payload["priority_actions"])
        self.assertTrue(payload["natural_language_invocations"])
        self.assertEqual(payload["mode"], "plan_only_probe_first")

    def test_self_awareness_capsule_uses_personality_style_lens_without_raw_trait_dump(self) -> None:
        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="what do you know about yourself?",
            personality_profile={
                "agent_persona_name": "Spark AGI",
                "agent_persona_summary": "warm, curious, and direct without turning into a status page",
                "traits": {
                    "warmth": 0.82,
                    "directness": 0.78,
                    "playfulness": 0.62,
                    "pacing": 0.74,
                    "assertiveness": 0.7,
                },
                "agent_behavioral_rules": [
                    "keep evidence visible",
                    "sound conversational",
                ],
                "agent_persona_applied": True,
                "user_deltas_applied": True,
            },
        )

        payload = capsule.to_payload()
        text = capsule.to_text()
        self.assertEqual(payload["style_lens"]["persona_name"], "Spark AGI")
        self.assertIn("How I am tuned for you", text)
        self.assertIn("warm, curious, and direct", text)
        self.assertIn("Tone: direct, warm, and fast-moving", text)
        self.assertIn("keeping the evidence visible", text)
        self.assertNotIn("trait", text.lower())
        self.assertLess(len(text), 2800)

    def test_natural_self_awareness_query_uses_capsule_direct_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Where do you lack and how can you improve those parts?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Spark self-awareness", result.reply_text)
        self.assertIn("Memory cognition", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertIn("current-state memory wins over wiki", result.reply_text)
        self.assertIn("Where I still lack", result.reply_text)
        self.assertIn("What I should improve next", result.reply_text)
        self.assertNotIn("LLM wiki", result.reply_text)
        self.assertLess(len(result.reply_text), 3000)
        self.assertIn("wiki_refresh=skipped", result.evidence_summary)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_natural_self_awareness_query_exposes_memory_kb_families_without_promoting_wiki(self) -> None:
        kb_dir = self.home / "artifacts" / "spark-memory-kb" / "wiki" / "current-state"
        kb_dir.mkdir(parents=True)
        (kb_dir / "focus.md").write_text(
            "---\n"
            "title: Current Focus Snapshot\n"
            "authority: supporting_not_authoritative\n"
            "owner_system: domain-chip-memory\n"
            "wiki_family: memory_kb_current_state\n"
            "scope_kind: governed_memory\n"
            "source_of_truth: SparkMemorySDK\n"
            "freshness: snapshot_generated\n"
            "---\n"
            "# Current Focus Snapshot\n\nA downstream memory KB snapshot for source-family discovery.",
            encoding="utf-8",
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-memory-kb",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message=(
                "What systems can you call, what do you know about your memory system, "
                "what outranks wiki, and where do you lack?"
            ),
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertIn("Memory KB: present", result.reply_text)
        self.assertIn("memory_kb_current_state", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertIn("current-state memory wins over wiki", result.reply_text)
        self.assertIn("Graph sidecar: advisory until evals pass", result.reply_text)
        self.assertIn("user memory stays separate from Spark doctrine", result.reply_text)
        self.assertNotIn("LLM wiki", result.reply_text)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_memory_self_awareness_phrase_routes_without_systems_magic_words(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:self-awareness:movement",
            predicate="system.memory.route_detection",
            value="ok",
            cleanup=False,
        )
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-memory-phrase",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about your memory system and what outranks wiki?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Memory cognition", result.reply_text)
        self.assertIn("Memory movement", result.reply_text)
        self.assertIn("captured=", result.reply_text)
        self.assertIn("current-state memory wins over wiki", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_memory_self_awareness_without_kb_names_gap_without_overclaiming(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-memory-no-kb",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about your memory system and where do you lack?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertIn("Memory cognition", result.reply_text)
        self.assertIn("families=not visible", result.reply_text)
        self.assertNotIn("Memory KB: present", result.reply_text)
        self.assertIn("Where I still lack", result.reply_text)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_self_awareness_keeps_user_memory_separate_from_spark_doctrine(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:telegram:123",
            predicate="profile.favorite_color",
            value="cobalt blue",
            cleanup=False,
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-user-memory-separation",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about your memory system and what belongs to Spark doctrine?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertIn("user memory stays separate from Spark doctrine", result.reply_text)
        self.assertNotIn("cobalt blue", result.reply_text)
        self.assertNotIn("favorite color", result.reply_text.lower())
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_self_awareness_query_beats_entity_state_summary_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-summary-trap",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about yourself and where do you lack?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Spark self-awareness", result.reply_text)
        self.assertNotIn("saved entity state", result.reply_text)

    def test_normal_telegram_message_uses_self_awareness_with_wiki_context(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1001,
                user_id="111",
                username="alice",
                text="What systems can you call, where do you lack, and how can you improve?",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.decision, "allowed")
        self.assertEqual(result.detail["bridge_mode"], "self_awareness_direct")
        self.assertEqual(result.detail["routing_decision"], "self_awareness_direct")
        self.assertIn("Spark self-awareness", result.detail["response_text"])
        self.assertIn("Memory cognition", result.detail["response_text"])
        self.assertIn("current-state memory wins over wiki", result.detail["response_text"])
        self.assertIn("Where I still lack", result.detail["response_text"])
        self.assertNotIn("LLM wiki", result.detail["response_text"])
        self.assertLess(len(result.detail["response_text"]), 3000)
