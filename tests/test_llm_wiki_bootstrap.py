from __future__ import annotations

import json

from spark_intelligence.llm_wiki import (
    bootstrap_llm_wiki,
    build_llm_wiki_answer,
    build_llm_wiki_candidate_inbox,
    build_llm_wiki_candidate_scan,
    build_llm_wiki_inventory,
    build_llm_wiki_query,
    build_llm_wiki_status,
    compile_system_wiki,
    promote_llm_wiki_improvement,
    promote_llm_wiki_user_note,
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
        self.assertIn("system/index.md", result.created_files)
        self.assertIn("routes/index.md", result.created_files)
        self.assertIn("tools/index.md", result.created_files)
        self.assertIn("user/index.md", result.created_files)
        self.assertIn("projects/index.md", result.created_files)
        self.assertIn("improvements/index.md", result.created_files)
        self.assertIn("system/spark-self-awareness-contract.md", result.created_files)
        self.assertIn("memory/llm-wiki-memory-policy.md", result.created_files)

        self_awareness = (self.home / "wiki" / "system" / "spark-self-awareness-contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("authority: supporting_not_authoritative", self_awareness)
        self.assertIn("observed_now", self_awareness)
        self.assertIn("Live `self status`", self_awareness)
        user_index = (self.home / "wiki" / "user" / "index.md").read_text(encoding="utf-8")
        self.assertIn("wiki/users/<human>/", user_index)
        self.assertIn("User notes do not become global Spark doctrine", user_index)
        improvements_index = (self.home / "wiki" / "improvements" / "index.md").read_text(encoding="utf-8")
        self.assertIn("wiki scan-candidates", improvements_index)
        self.assertIn("supporting and revalidatable", improvements_index)

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
        self.assertIn("environment/spark-environment.md", result.generated_files)
        self.assertIn("tools/capability-index.md", result.generated_files)
        self.assertIn("routes/live-route-index.md", result.generated_files)
        self.assertIn("diagnostics/self-awareness-gaps.md", result.generated_files)
        status_page = (self.home / "wiki" / "system" / "current-system-status.md").read_text(encoding="utf-8")
        self.assertIn("source_class: spark_llm_wiki_system_compile", status_page)
        self.assertIn("## Memory Runtime", status_page)
        environment_page = (self.home / "wiki" / "environment" / "spark-environment.md").read_text(encoding="utf-8")
        self.assertIn("## Local Paths", environment_page)
        self.assertIn("secret_values: `redacted`", environment_page)
        self.assertIn("env_file_contents: `not_read_by_environment_page`", environment_page)
        self.assertIn("## Safe Probes", environment_page)
        self.assertNotIn("Spark Intelligence secrets", environment_page)

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
        self.assertIn("environment/spark-environment.md", payload["generated_files"])

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
        self.assertEqual(result.payload["wiki_packet_metadata"]["authority"], "supporting_not_authoritative")
        self.assertTrue(result.payload["wiki_packet_metadata"]["source_families_visible"])
        self.assertEqual(result.payload["freshness_health"]["stale_page_count"], 0)
        self.assertIn("live_compile_snapshot", result.payload["freshness_health"]["thresholds_days"])

    def test_wiki_status_warns_about_stale_pages_and_old_candidates(self) -> None:
        bootstrap_llm_wiki(config_manager=self.config_manager)
        stale_system_page = self.home / "wiki" / "system" / "current-system-status.md"
        stale_system_page.parent.mkdir(parents=True, exist_ok=True)
        stale_system_page.write_text(
            "---\n"
            'title: "Old system status"\n'
            'date_created: "2000-01-01"\n'
            'date_modified: "2000-01-01"\n'
            "type: llm_wiki_system_compile\n"
            "status: generated\n"
            "authority: supporting_not_authoritative\n"
            "freshness: live_compile_snapshot\n"
            "---\n"
            "# Old system status\n\n"
            "## Invalidation Trigger\n"
            "- A newer live compile snapshot exists.\n",
            encoding="utf-8",
        )
        stale_candidate = self.home / "wiki" / "improvements" / "2000-01-01-old-candidate.md"
        stale_candidate.parent.mkdir(parents=True, exist_ok=True)
        stale_candidate.write_text(
            "---\n"
            'title: "Old candidate"\n'
            'date_created: "2000-01-01"\n'
            'date_modified: "2000-01-01"\n'
            "type: llm_wiki_improvement\n"
            "status: candidate\n"
            "promotion_status: candidate\n"
            "authority: supporting_not_authoritative\n"
            "freshness: revalidatable_improvement_note\n"
            'evidence_refs: ["pytest:old"]\n'
            "---\n"
            "# Old candidate\n\n"
            "## Invalidation Trigger\n"
            "- Re-run before reuse.\n",
            encoding="utf-8",
        )

        result = build_llm_wiki_status(config_manager=self.config_manager, state_db=self.state_db)

        freshness = result.payload["freshness_health"]
        self.assertGreaterEqual(freshness["stale_page_count"], 2)
        self.assertEqual(freshness["stale_candidate_count"], 1)
        stale_paths = {page["path"] for page in freshness["stale_pages"]}
        self.assertIn("system/current-system-status.md", stale_paths)
        self.assertIn("improvements/2000-01-01-old-candidate.md", stale_paths)
        self.assertIn("wiki_stale_pages_detected", result.payload["warnings"])
        self.assertIn("wiki_old_candidates_need_review", result.payload["warnings"])
        self.assertEqual(
            freshness["live_status_boundary"],
            "stale wiki pages warn; live traces, tests, and current-state memory still outrank wiki",
        )

    def test_wiki_status_discovers_memory_kb_families_from_packet_metadata(self) -> None:
        kb_root = self.home / "artifacts" / "spark-memory-kb" / "wiki"
        for family_dir, family in (
            ("current-state", "memory_kb_current_state"),
            ("evidence", "memory_kb_evidence"),
            ("events", "memory_kb_event"),
            ("syntheses", "memory_kb_synthesis"),
        ):
            target_dir = kb_root / family_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / f"{family}.md").write_text(
                "---\n"
                f"title: {family}\n"
                "authority: supporting_not_authoritative\n"
                "owner_system: domain-chip-memory\n"
                f"wiki_family: {family}\n"
                "scope_kind: governed_memory\n"
                "source_of_truth: SparkMemorySDK\n"
                "freshness: snapshot_generated\n"
                "---\n"
                f"# {family}\n\nMemory KB packet for {family}.",
                encoding="utf-8",
            )

        result = build_llm_wiki_status(config_manager=self.config_manager, state_db=self.state_db)

        discovery = result.payload["memory_kb_discovery"]
        self.assertTrue(discovery["present"])
        self.assertEqual(discovery["authority"], "supporting_not_authoritative")
        self.assertEqual(discovery["source_of_truth"], "SparkMemorySDK")
        self.assertEqual(discovery["family_counts"]["memory_kb_current_state"], 1)
        self.assertEqual(discovery["family_counts"]["memory_kb_evidence"], 1)
        self.assertEqual(discovery["family_counts"]["memory_kb_event"], 1)
        self.assertEqual(discovery["family_counts"]["memory_kb_synthesis"], 1)

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
        self.assertIn("projects/index.md", payload["expected_bootstrap_files"])
        self.assertIn("improvements/index.md", payload["expected_bootstrap_files"])
        self.assertIn("environment/spark-environment.md", payload["expected_system_compile_files"])

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
        self.assertIn("system/index.md", page_paths)
        self.assertIn("routes/index.md", page_paths)
        self.assertIn("tools/index.md", page_paths)
        self.assertIn("user/index.md", page_paths)
        self.assertIn("projects/index.md", page_paths)
        self.assertIn("improvements/index.md", page_paths)
        self.assertIn("system/current-system-status.md", page_paths)
        self.assertIn("environment/spark-environment.md", page_paths)
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

    def test_wiki_query_retrieves_relevant_supporting_packets(self) -> None:
        result = build_llm_wiki_query(
            config_manager=self.config_manager,
            state_db=self.state_db,
            query="How should Spark use recursive self-improvement loops?",
            refresh=True,
            limit=3,
        )

        self.assertEqual(result.payload["wiki_retrieval_status"], "supported")
        self.assertGreater(result.payload["hit_count"], 0)
        self.assertTrue(result.payload["project_knowledge_first"])
        hit_text = "\n".join(hit["text"] for hit in result.payload["hits"])
        self.assertIn("Recursive Self-Improvement", hit_text)
        self.assertEqual(result.payload["authority"], "supporting_not_authoritative")
        hit = result.payload["hits"][0]
        self.assertIn("wiki_family", hit)
        self.assertIn("owner_system", hit)
        self.assertIn("scope_kind", hit)
        self.assertIn("source_of_truth", hit)
        self.assertEqual(hit["authority"], "supporting_not_authoritative")

    def test_wiki_query_cli_can_emit_machine_readable_hits(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "query",
            "Spark self-awareness contract",
            "--home",
            str(self.home),
            "--refresh",
            "--limit",
            "2",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["wiki_retrieval_status"], "supported")
        self.assertGreater(payload["hit_count"], 0)
        self.assertLessEqual(payload["hit_count"], 2)
        self.assertTrue(payload["hits"][0]["source_path"])

    def test_wiki_answer_builds_source_backed_answer_with_live_verification_boundary(self) -> None:
        result = build_llm_wiki_answer(
            config_manager=self.config_manager,
            state_db=self.state_db,
            question="How should Spark use recursive self-improvement loops now?",
            refresh=True,
            limit=3,
        )

        self.assertEqual(result.payload["evidence_level"], "wiki_backed_with_live_self_snapshot")
        self.assertGreater(result.payload["hit_count"], 0)
        self.assertIn("From the LLM wiki", result.payload["answer"])
        self.assertIn("Live self snapshot", result.payload["answer"])
        self.assertTrue(result.payload["sources"][0]["source_path"])
        self.assertIn("wiki_family", result.payload["sources"][0])
        self.assertIn("owner_system", result.payload["sources"][0])
        self.assertTrue(result.payload["missing_live_verification"])
        self.assertEqual(result.payload["live_context_status"], "included")
        self.assertTrue(result.payload["live_self_awareness"]["observed_now"])
        self.assertTrue(result.payload["live_self_awareness"]["lacks"])
        self.assertEqual(result.payload["authority"], "supporting_not_authoritative")
        policy = result.payload["deep_search_policy"]
        self.assertEqual(policy["status"], "live_probe_required")
        self.assertTrue(policy["should_probe"])
        self.assertFalse(policy["should_deep_search"])
        self.assertIn("current_or_mutable_fact", policy["triggers"])
        self.assertIn("live_verification_needed", policy["triggers"])
        self.assertIn("deep_search_or_probe_required", result.payload["warnings"])

    def test_wiki_answer_cli_can_emit_machine_readable_answer(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "answer",
            "How should Spark use route tracing?",
            "--home",
            str(self.home),
            "--refresh",
            "--limit",
            "3",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["evidence_level"], "wiki_backed_with_live_self_snapshot")
        self.assertGreater(payload["hit_count"], 0)
        self.assertTrue(payload["sources"])
        self.assertEqual(payload["live_context_status"], "included")
        self.assertIn("deep_search_policy", payload)
        self.assertIn("answer", payload)

    def test_wiki_answer_flags_deep_search_for_high_stakes_current_operational_claims(self) -> None:
        result = build_llm_wiki_answer(
            config_manager=self.config_manager,
            state_db=self.state_db,
            question="Is the Telegram gateway healthy and provider auth ready for production right now?",
            refresh=True,
            limit=3,
        )

        policy = result.payload["deep_search_policy"]
        self.assertTrue(policy["should_probe"])
        self.assertTrue(policy["should_deep_search"])
        self.assertEqual(policy["status"], "live_probe_and_deep_search_required")
        self.assertIn("high_stakes_or_operational_claim", policy["triggers"])
        self.assertIn("current_or_mutable_fact", policy["triggers"])
        self.assertIn("live_verification_needed", policy["triggers"])
        self.assertIn("deep_search_or_probe_required", result.payload["warnings"])
        self.assertEqual(result.payload["authority"], "supporting_not_authoritative")

    def test_wiki_answer_flags_under_sourced_questions_for_deep_search(self) -> None:
        result = build_llm_wiki_answer(
            config_manager=self.config_manager,
            state_db=self.state_db,
            question="How should Spark operate the zyxqq nonexistent scheduler?",
            refresh=False,
            limit=3,
            include_live_self=False,
        )

        policy = result.payload["deep_search_policy"]
        self.assertEqual(result.payload["hit_count"], 0)
        self.assertTrue(policy["should_deep_search"])
        self.assertFalse(policy["should_probe"])
        self.assertEqual(policy["status"], "deep_search_required")
        self.assertIn("under_sourced_wiki", policy["triggers"])
        self.assertIn("no_matching_wiki_packets", result.payload["warnings"])
        self.assertIn("deep_search_or_probe_required", result.payload["warnings"])
        self.assertIn("supporting_not_authoritative", policy["authority_boundary"])

    def test_wiki_promote_improvement_writes_bounded_obsidian_note(self) -> None:
        result = promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Track verified route confidence",
            summary="Spark should only claim a route is strong after recent success evidence is attached.",
            evidence_refs=["pytest tests/test_self_awareness.py"],
            source_refs=["operator_session:self-awareness-build"],
            next_probe="Run the route and persist last_success_at before claiming it is live.",
        )

        self.assertEqual(result.output_dir, self.home / "wiki")
        self.assertTrue(result.relative_path.startswith("improvements/"))
        note = (self.home / "wiki" / result.relative_path).read_text(encoding="utf-8")
        self.assertIn("type: llm_wiki_improvement", note)
        self.assertIn("promotion_status: candidate", note)
        self.assertIn("authority: supporting_not_authoritative", note)
        self.assertIn("pytest tests/test_self_awareness.py", note)
        self.assertIn("not live runtime truth", note)

        query = build_llm_wiki_query(
            config_manager=self.config_manager,
            state_db=self.state_db,
            query="verified route confidence recent success evidence",
            limit=3,
        )

        self.assertGreater(query.payload["hit_count"], 0)
        hit_text = "\n".join(hit["text"] for hit in query.payload["hits"])
        self.assertIn("route is strong after recent success evidence", hit_text)

    def test_wiki_promote_improvement_records_typed_route_lineage(self) -> None:
        result = promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Route lineage fields",
            summary="Spark should keep wiki promotion trace lineage machine-readable.",
            evidence_refs=["pytest tests/test_llm_wiki_bootstrap.py::route_lineage"],
            source_refs=["operator_session:self-awareness-hardening"],
            request_id="req-route-123",
            route_decision="self_awareness_direct",
            source_packet_refs=["wiki_packet:system/spark-self-awareness-contract.md", "memory_packet:current-state:focus"],
            probe_refs=["pytest tests/test_natural_language_route_eval_matrix.py -q"],
        )

        note = (self.home / "wiki" / result.relative_path).read_text(encoding="utf-8")
        self.assertIn('request_id: "req-route-123"', note)
        self.assertIn('route_decision: "self_awareness_direct"', note)
        self.assertIn("source_packet_refs:", note)
        self.assertIn("wiki_packet:system/spark-self-awareness-contract.md", note)
        self.assertIn("probe_refs:", note)
        self.assertIn("tests/test_natural_language_route_eval_matrix.py", note)
        self.assertEqual(result.payload["trace_lineage"]["request_id"], "req-route-123")
        self.assertEqual(result.payload["trace_lineage"]["route_decision"], "self_awareness_direct")
        self.assertEqual(result.payload["trace_lineage"]["source_packet_refs"][0], "wiki_packet:system/spark-self-awareness-contract.md")
        self.assertEqual(result.payload["trace_lineage"]["probe_refs"], ["pytest tests/test_natural_language_route_eval_matrix.py -q"])

        inbox = build_llm_wiki_candidate_inbox(config_manager=self.config_manager)
        note_payload = inbox.payload["notes"][0]
        self.assertEqual(note_payload["request_id"], "req-route-123")
        self.assertEqual(note_payload["route_decision"], "self_awareness_direct")
        self.assertEqual(note_payload["lineage"]["source_packet_ref_count"], 2)
        self.assertEqual(note_payload["lineage"]["probe_ref_count"], 1)
        self.assertTrue(note_payload["lineage"]["has_route_trace_lineage"])

        scan = build_llm_wiki_candidate_scan(config_manager=self.config_manager)
        finding = scan.payload["findings"][0]
        self.assertEqual(finding["trace_lineage"]["request_id"], "req-route-123")
        self.assertEqual(finding["trace_lineage"]["route_decision"], "self_awareness_direct")
        self.assertEqual(finding["source_packet_refs"][1], "memory_packet:current-state:focus")
        self.assertEqual(finding["probe_refs"], ["pytest tests/test_natural_language_route_eval_matrix.py -q"])

    def test_wiki_promote_improvement_requires_source_or_evidence(self) -> None:
        with self.assertRaises(ValueError):
            promote_llm_wiki_improvement(
                config_manager=self.config_manager,
                title="Residue should not promote",
                summary="This should be blocked without a source.",
            )

    def test_wiki_promote_improvement_cli_emits_machine_readable_result(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "promote-improvement",
            "Capability evidence notes",
            "--home",
            str(self.home),
            "--summary",
            "Capability notes should separate registration from recent invocation success.",
            "--evidence-ref",
            "pytest tests/test_llm_wiki_bootstrap.py",
            "--source",
            "operator_session:self-awareness-build",
            "--request-id",
            "req-cli-route-1",
            "--route-decision",
            "llm_wiki_candidate_inbox",
            "--source-packet-ref",
            "wiki_packet:improvements/example.md",
            "--probe-ref",
            "pytest tests/test_llm_wiki_bootstrap.py::cli_lineage",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["promotion_status"], "candidate")
        self.assertEqual(payload["authority"], "supporting_not_authoritative")
        self.assertEqual(payload["trace_lineage"]["request_id"], "req-cli-route-1")
        self.assertEqual(payload["trace_lineage"]["route_decision"], "llm_wiki_candidate_inbox")
        self.assertEqual(payload["trace_lineage"]["source_packet_refs"], ["wiki_packet:improvements/example.md"])
        self.assertEqual(payload["trace_lineage"]["probe_refs"], ["pytest tests/test_llm_wiki_bootstrap.py::cli_lineage"])
        self.assertTrue(payload["relative_path"].startswith("improvements/"))
        self.assertTrue((self.home / "wiki" / payload["relative_path"]).exists())

    def test_wiki_user_note_requires_explicit_consent_ref(self) -> None:
        with self.assertRaises(ValueError):
            promote_llm_wiki_user_note(
                config_manager=self.config_manager,
                human_id="human:test-user",
                title="User likes terse replies",
                summary="The user prefers concise operational replies.",
                evidence_refs=["telegram_trace:turn-123"],
            )

    def test_wiki_user_note_writes_separate_user_lane(self) -> None:
        result = promote_llm_wiki_user_note(
            config_manager=self.config_manager,
            human_id="human:test-user",
            title="User likes terse replies",
            summary="The user prefers concise operational replies for builder status checks.",
            consent_ref="consent:telegram:turn-123",
            evidence_refs=["telegram_trace:turn-123"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

        self.assertEqual(result.output_dir, self.home / "wiki")
        self.assertTrue(result.relative_path.startswith("users/human-test-user/candidate-notes/"))
        note = (self.home / "wiki" / result.relative_path).read_text(encoding="utf-8")
        self.assertIn("type: llm_wiki_user_note", note)
        self.assertIn("authority: supporting_not_authoritative", note)
        self.assertIn("owner_system: spark-intelligence-builder", note)
        self.assertIn("wiki_family: user_context_candidate", note)
        self.assertIn("scope_kind: user_specific", note)
        self.assertIn("source_of_truth: governed_user_memory_or_explicit_consent", note)
        self.assertIn('human_id: "human:test-user"', note)
        self.assertIn('consent_ref: "consent:telegram:turn-123"', note)
        self.assertIn("not global Spark doctrine", note)
        self.assertIn("does not override governed current-state memory", note)
        self.assertFalse(result.payload["can_override_global_doctrine"])
        self.assertFalse(result.payload["can_override_current_state_memory"])

    def test_wiki_user_note_stays_out_of_global_candidate_inbox(self) -> None:
        promote_llm_wiki_user_note(
            config_manager=self.config_manager,
            human_id="human:test-user",
            title="User project preference candidate",
            summary="The user wants project-specific progress grouped by hardening phase.",
            consent_ref="consent:telegram:turn-456",
            evidence_refs=["telegram_trace:turn-456"],
        )
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Global route confidence candidate",
            summary="Spark should verify route confidence with fresh trace evidence.",
            evidence_refs=["pytest tests/test_self_awareness.py::route_confidence"],
        )

        result = build_llm_wiki_candidate_inbox(config_manager=self.config_manager, status="all")

        self.assertEqual(result.payload["total_improvement_count"], 1)
        self.assertEqual(result.payload["returned_count"], 1)
        self.assertTrue(result.payload["notes"][0]["path"].startswith("improvements/"))
        self.assertNotIn("users/", result.payload["notes"][0]["path"])

    def test_wiki_promote_user_note_cli_emits_machine_readable_result(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "promote-user-note",
            "User builder pacing",
            "--home",
            str(self.home),
            "--human-id",
            "human:test-user",
            "--summary",
            "The user wants Builder hardening updates to name the current phase.",
            "--consent-ref",
            "consent:telegram:turn-789",
            "--evidence-ref",
            "telegram_trace:turn-789",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["promotion_status"], "candidate")
        self.assertEqual(payload["authority"], "supporting_not_authoritative")
        self.assertEqual(payload["scope_kind"], "user_specific")
        self.assertEqual(payload["human_id"], "human:test-user")
        self.assertEqual(payload["consent_ref"], "consent:telegram:turn-789")
        self.assertTrue(payload["relative_path"].startswith("users/human-test-user/candidate-notes/"))
        self.assertTrue((self.home / "wiki" / payload["relative_path"]).exists())

    def test_wiki_candidate_inbox_lists_source_bounded_candidates(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Probe route confidence before claims",
            summary="Spark should show candidate route confidence only with fresh trace evidence.",
            evidence_refs=["pytest tests/test_self_awareness.py::route_confidence"],
            source_refs=["operator_session:self-awareness-hardening"],
        )
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Verified generated pages are supporting only",
            summary="Generated wiki pages can assist answers but live traces still decide current truth.",
            promotion_status="verified",
            evidence_refs=["pytest tests/test_llm_wiki_bootstrap.py::wiki_answer"],
            source_refs=["commit:a81acf2"],
        )

        result = build_llm_wiki_candidate_inbox(config_manager=self.config_manager)

        self.assertEqual(result.payload["authority"], "supporting_not_authoritative")
        self.assertEqual(result.payload["status_filter"], "candidate")
        self.assertEqual(result.payload["candidate_count"], 1)
        self.assertEqual(result.payload["verified_count"], 1)
        self.assertEqual(result.payload["returned_count"], 1)
        note = result.payload["notes"][0]
        self.assertEqual(note["promotion_status"], "candidate")
        self.assertEqual(note["review_state"], "candidate_needs_probe")
        self.assertEqual(note["authority"], "supporting_not_authoritative")
        self.assertFalse(note["can_override_runtime_truth"])
        self.assertEqual(note["evidence_refs"], ["pytest tests/test_self_awareness.py::route_confidence"])
        self.assertEqual(note["source_refs"], ["operator_session:self-awareness-hardening"])
        self.assertTrue(note["lineage"]["has_source_or_evidence"])
        self.assertIn("candidate_notes_are_not_runtime_truth", result.payload["non_override_rules"])
        self.assertIn("conversational_residue_is_not_promotion_evidence", result.payload["non_override_rules"])

    def test_wiki_candidate_inbox_cli_can_emit_all_review_notes(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Candidate inbox CLI note",
            summary="Candidate review must expose source and evidence refs without promoting truth.",
            evidence_refs=["pytest tests/test_llm_wiki_bootstrap.py::candidate_inbox"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "candidates",
            "--home",
            str(self.home),
            "--status",
            "all",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["status_filter"], "all")
        self.assertEqual(payload["authority"], "supporting_not_authoritative")
        self.assertEqual(payload["returned_count"], 1)
        self.assertEqual(payload["notes"][0]["promotion_status"], "candidate")
        self.assertFalse(payload["notes"][0]["can_override_runtime_truth"])

    def test_wiki_candidate_scan_keeps_clean_candidates_and_flags_promotion_hazards(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Clean route confidence candidate",
            summary="Spark should show route confidence only after fresh trace evidence.",
            evidence_refs=["pytest tests/test_self_awareness.py::route_confidence"],
            source_refs=["operator_session:self-awareness-hardening"],
        )
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Verified note needs evidence",
            summary="This verified note should be rewritten because it has only a source ref.",
            promotion_status="verified",
            source_refs=["docs:self-awareness-plan"],
        )
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="User prefers short replies",
            summary="User prefers short replies forever.",
            evidence_refs=["pytest tests/test_memory_orchestrator.py::current_state"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

        result = build_llm_wiki_candidate_scan(config_manager=self.config_manager)

        self.assertEqual(result.payload["authority"], "supporting_not_authoritative")
        self.assertEqual(result.payload["scanned_count"], 3)
        findings = {finding["title"]: finding for finding in result.payload["findings"]}
        self.assertEqual(findings["Clean route confidence candidate"]["recommendation"], "keep")
        self.assertEqual(findings["Clean route confidence candidate"]["issues"], [])
        self.assertEqual(findings["Verified note needs evidence"]["recommendation"], "rewrite")
        self.assertIn(
            "verified_without_explicit_evidence",
            [issue["code"] for issue in findings["Verified note needs evidence"]["issues"]],
        )
        self.assertEqual(findings["User prefers short replies"]["recommendation"], "rewrite")
        self.assertIn(
            "mutable_user_fact_requires_user_memory_lane",
            [issue["code"] for issue in findings["User prefers short replies"]["issues"]],
        )
        self.assertEqual(
            result.payload["live_evidence_boundary"]["mutable_user_facts"],
            "current_state_memory_outranks_wiki",
        )

    def test_wiki_candidate_scan_accepts_probe_refs_as_live_health_evidence(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Capability is verified",
            summary="The route is live after the latest smoke probe.",
            source_refs=["operator_session:self-awareness-hardening"],
            request_id="req-live-health",
            route_decision="self_awareness_direct",
            probe_refs=["pytest tests/test_natural_language_route_eval_matrix.py -q"],
        )

        result = build_llm_wiki_candidate_scan(config_manager=self.config_manager)

        finding = result.payload["findings"][0]
        self.assertEqual(finding["recommendation"], "keep")
        self.assertEqual(finding["issues"], [])
        self.assertEqual(finding["probe_refs"], ["pytest tests/test_natural_language_route_eval_matrix.py -q"])

    def test_wiki_candidate_scan_cli_emits_keep_rewrite_drop_recommendations(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Conversation residue source",
            summary="A raw conversation source without evidence should stay rewrite-only.",
            source_refs=["conversation:raw-turn-123"],
        )

        exit_code, stdout, stderr = self.run_cli(
            "wiki",
            "scan-candidates",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["scanned_count"], 1)
        self.assertEqual(payload["findings"][0]["recommendation"], "rewrite")
        self.assertIn("conversation_residue_without_evidence", payload["issue_counts"])
        self.assertIn("wiki_is_supporting_not_authoritative", payload["non_override_rules"])

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
