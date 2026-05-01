from __future__ import annotations

import json

from spark_intelligence.llm_wiki import (
    bootstrap_llm_wiki,
    build_llm_wiki_inventory,
    build_llm_wiki_status,
    compile_system_wiki,
)
from spark_intelligence.memory import orchestrator as memory_orchestrator
from spark_intelligence.memory.orchestrator import hybrid_memory_retrieve

from tests.test_support import SparkTestCase
from unittest.mock import patch


class LlmWikiBootstrapTests(SparkTestCase):
    def test_bootstrap_creates_supporting_wiki_pages_under_default_home_wiki(self) -> None:
        result = bootstrap_llm_wiki(config_manager=self.config_manager)

        self.assertEqual(result.output_dir, self.home / "wiki")
        self.assertIn("index.md", result.created_files)
        self.assertIn("system/spark-self-awareness-contract.md", result.created_files)
        self.assertIn("memory/llm-wiki-memory-policy.md", result.created_files)

        self_awareness = (self.home / "wiki" / "system" / "spark-self-awareness-contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("authority: supporting_not_authoritative", self_awareness)
        self.assertIn("observed_now", self_awareness)
        self.assertIn("Live `self status`", self_awareness)

    def test_bootstrap_preserves_existing_pages_unless_forced(self) -> None:
        result = bootstrap_llm_wiki(config_manager=self.config_manager)
        target = self.home / "wiki" / "system" / "spark-system-map.md"
        target.write_text("local notes", encoding="utf-8")

        preserved = bootstrap_llm_wiki(config_manager=self.config_manager)
        self.assertIn("system/spark-system-map.md", preserved.preserved_files)
        self.assertEqual(target.read_text(encoding="utf-8"), "local notes")

        overwritten = bootstrap_llm_wiki(config_manager=self.config_manager, overwrite=True)
        self.assertIn("system/spark-system-map.md", overwritten.overwritten_files)
        self.assertIn("Spark System Map", target.read_text(encoding="utf-8"))
        self.assertTrue(result.created_files)

    def test_wiki_bootstrap_cli_emits_machine_readable_result(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "bootstrap",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["output_dir"], str(self.home / "wiki"))
        self.assertEqual(payload["authority"], "supporting_not_authoritative")
        self.assertEqual(payload["runtime_hook"], "hybrid_memory_retrieve.wiki_packets")
        self.assertGreater(payload["created_count"], 0)

    def test_compile_system_generates_live_system_pages(self) -> None:
        bootstrap_llm_wiki(config_manager=self.config_manager)

        result = compile_system_wiki(config_manager=self.config_manager, state_db=self.state_db)

        self.assertEqual(result.output_dir, self.home / "wiki")
        self.assertIn("system/current-system-status.md", result.generated_files)
        self.assertIn("tools/capability-index.md", result.generated_files)
        self.assertIn("routes/live-route-index.md", result.generated_files)
        self.assertIn("diagnostics/self-awareness-gaps.md", result.generated_files)
        status_page = (self.home / "wiki" / "system" / "current-system-status.md").read_text(encoding="utf-8")
        self.assertIn("source_class: spark_llm_wiki_system_compile", status_page)
        self.assertIn("## Memory Runtime", status_page)

    def test_compile_system_cli_emits_machine_readable_result(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "compile-system",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["output_dir"], str(self.home / "wiki"))
        self.assertIn("system_registry", payload["source_refs"])
        self.assertIn("system/current-system-status.md", payload["generated_files"])

    def test_wiki_status_reports_missing_vault_without_refresh(self) -> None:
        result = build_llm_wiki_status(config_manager=self.config_manager, state_db=self.state_db)

        self.assertFalse(result.payload["healthy"])
        self.assertFalse(result.payload["exists"])
        self.assertIn("index.md", result.payload["missing_bootstrap_files"])
        self.assertIn("wiki_root_missing", result.payload["warnings"])

    def test_wiki_status_refreshes_and_verifies_retrievable_project_knowledge(self) -> None:
        result = build_llm_wiki_status(config_manager=self.config_manager, state_db=self.state_db, refresh=True)

        self.assertTrue(result.payload["healthy"])
        self.assertTrue(result.payload["exists"])
        self.assertEqual(result.payload["missing_bootstrap_files"], [])
        self.assertEqual(result.payload["missing_system_compile_files"], [])
        self.assertEqual(result.payload["wiki_retrieval_status"], "supported")
        self.assertGreater(result.payload["wiki_record_count"], 0)
        self.assertTrue(result.payload["project_knowledge_first"])
        self.assertIn("system/current-system-status.md", result.payload["refreshed_files"])

    def test_wiki_status_cli_can_refresh_and_emit_machine_readable_result(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "status",
            "--home",
            str(self.home),
            "--refresh",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["healthy"])
        self.assertEqual(payload["wiki_retrieval_status"], "supported")
        self.assertGreater(payload["wiki_record_count"], 0)
        self.assertTrue(payload["project_knowledge_first"])

    def test_wiki_inventory_refreshes_and_lists_pages_with_metadata(self) -> None:
        result = build_llm_wiki_inventory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            refresh=True,
        )

        self.assertTrue(result.payload["exists"])
        self.assertEqual(result.payload["missing_expected_files"], [])
        self.assertGreaterEqual(result.payload["page_count"], 13)
        page_paths = {page["path"] for page in result.payload["pages"]}
        self.assertIn("index.md", page_paths)
        self.assertIn("system/current-system-status.md", page_paths)
        index_page = next(page for page in result.payload["pages"] if page["path"] == "index.md")
        self.assertEqual(index_page["title"], "Spark LLM Wiki")
        self.assertEqual(index_page["authority"], "supporting_not_authoritative")
        self.assertIn("system", result.payload["section_counts"])

    def test_wiki_inventory_cli_can_emit_limited_machine_readable_result(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "inventory",
            "--home",
            str(self.home),
            "--refresh",
            "--limit",
            "3",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["returned_page_count"], 3)
        self.assertGreaterEqual(payload["page_count"], 13)
        self.assertEqual(payload["missing_expected_files"], [])

    def test_project_knowledge_intent_boosts_wiki_over_generic_events(self) -> None:
        query = "How should Spark use recursive self-improvement loops?"
        wiki_score, wiki_reasons = memory_orchestrator._score_hybrid_memory_record(
            lane="wiki_packets",
            source_class="obsidian_llm_wiki_packets",
            record={
                "predicate": "knowledge.packet",
                "text": "Recursive Self-Improvement Loops for Spark systems and route improvement.",
                "metadata": {"source_surface": "obsidian_llm_wiki_packets"},
            },
            query=query,
            predicate=None,
            entity_key=None,
        )
        event_score, event_reasons = memory_orchestrator._score_hybrid_memory_record(
            lane="events",
            source_class="event",
            record={
                "predicate": "profile.current_assumption",
                "text": "users will self-serve after onboarding",
                "metadata": {"source_surface": "event"},
            },
            query=query,
            predicate=None,
            entity_key=None,
        )

        self.assertGreater(wiki_score, event_score)
        self.assertIn("project_knowledge_intent_boost", wiki_reasons)
        self.assertIn("project_knowledge_intent_generic_memory_penalty", event_reasons)

    def test_bootstrapped_wiki_is_available_to_hybrid_memory_wiki_packet_lane(self) -> None:
        bootstrap_llm_wiki(config_manager=self.config_manager)

        with patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="How should Spark use recursive self-improvement loops?",
                subject="human:test",
                limit=4,
                actor_id="test",
            )

        trace = result.read_result.retrieval_trace["hybrid_memory_retrieve"]
        wiki_lane = next(lane for lane in trace["lane_summaries"] if lane["lane"] == "wiki_packets")
        self.assertEqual(wiki_lane["status"], "supported")
        self.assertEqual(wiki_lane["authority"], "supporting_not_authoritative")
        self.assertTrue(any(candidate.lane == "wiki_packets" for candidate in result.candidates))
