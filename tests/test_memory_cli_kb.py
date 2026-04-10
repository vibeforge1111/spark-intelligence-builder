from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory import TelegramStateKnowledgeBaseResult

from tests.test_support import SparkTestCase


class MemoryCliKnowledgeBaseTests(SparkTestCase):
    def test_memory_compile_telegram_kb_dispatches_builder_state_compile(self) -> None:
        output_dir = self.home / "artifacts" / "spark-memory-kb"
        write_path = self.home / "artifacts" / "spark-memory-kb.json"
        payload = {
            "builder_home": str(self.home),
            "summary": {
                "selected_chat_id": "12345",
                "conversation_count": 1,
                "accepted_writes": 2,
                "rejected_writes": 0,
                "skipped_turns": 0,
                "kb_valid": True,
            },
            "health_report": {"valid": True, "errors": []},
            "compile_result": {"filed_output_count": 4},
        }

        with patch(
            "spark_intelligence.cli.build_telegram_state_knowledge_base",
            return_value=TelegramStateKnowledgeBaseResult(output_dir=output_dir, payload=payload),
        ) as compile_kb:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "compile-telegram-kb",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--limit",
                "12",
                "--chat-id",
                "12345",
                "--repo-source",
                "README.md",
                "--repo-source-manifest",
                "repo-sources.json",
                "--write",
                str(write_path),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["selected_chat_id"], "12345")
        compile_kb.assert_called_once()
        kwargs = compile_kb.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["limit"], 12)
        self.assertEqual(kwargs["chat_id"], "12345")
        self.assertEqual(kwargs["repo_sources"], ["README.md"])
        self.assertEqual(kwargs["repo_source_manifest_files"], ["repo-sources.json"])
        self.assertEqual(kwargs["write_path"], str(write_path))
        self.assertIsNone(kwargs["validator_root"])

    def test_memory_benchmark_architectures_dispatches_runner(self) -> None:
        output_dir = self.home / "artifacts" / "memory-architecture-benchmark"
        payload = {
            "summary": {
                "runtime_sdk_class": "SparkMemorySDK",
                "documented_frontier_architecture": "summary_synthesis_memory",
                "runtime_matches_documented_frontier": False,
                "product_memory_leader_names": [
                    "observational_temporal_memory",
                    "dual_store_event_calendar_hybrid",
                ],
            },
            "artifact_paths": {
                "summary_markdown": str(output_dir / "memory-architecture-benchmark.md"),
            },
            "errors": [],
        }

        with patch(
            "spark_intelligence.cli.benchmark_memory_architectures",
            return_value=SimpleNamespace(payload=payload, to_json=lambda: json.dumps(payload), to_text=lambda: "ok"),
        ) as run_benchmark:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "benchmark-architectures",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--validator-root",
                "C:/validator",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["runtime_sdk_class"], "SparkMemorySDK")
        kwargs = run_benchmark.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["validator_root"], "C:/validator")

    def test_memory_soak_architectures_dispatches_runner(self) -> None:
        output_dir = self.home / "artifacts" / "memory-architecture-soak"
        payload = {
            "summary": {
                "requested_runs": 5,
                "completed_runs": 5,
                "failed_runs": 0,
                "overall_leader_names": ["summary_synthesis_memory"],
                "recommended_top_two": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
            },
            "errors": [],
        }

        with patch(
            "spark_intelligence.cli.run_telegram_memory_architecture_soak",
            return_value=SimpleNamespace(payload=payload, to_json=lambda: json.dumps(payload), to_text=lambda: "ok"),
        ) as run_soak:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "soak-architectures",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--runs",
                "5",
                "--sleep-seconds",
                "0.5",
                "--user-id",
                "12345",
                "--chat-id",
                "12345",
                "--case-id",
                "country_query",
                "--category",
                "overwrite",
                "--kb-limit",
                "12",
                "--validator-root",
                "C:/validator",
                "--write",
                str(output_dir / "summary.json"),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["completed_runs"], 5)
        kwargs = run_soak.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["runs"], 5)
        self.assertEqual(kwargs["sleep_seconds"], 0.5)
        self.assertEqual(kwargs["user_id"], "12345")
        self.assertEqual(kwargs["chat_id"], "12345")
        self.assertEqual(kwargs["kb_limit"], 12)
        self.assertEqual(kwargs["validator_root"], "C:/validator")
        self.assertEqual(kwargs["case_ids"], ["country_query"])
        self.assertEqual(kwargs["categories"], ["overwrite"])
