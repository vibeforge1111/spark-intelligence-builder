from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory import TelegramMemoryRegressionResult, run_telegram_memory_regression
from spark_intelligence.memory.regression import DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES

from tests.test_support import SparkTestCase


class MemoryRegressionTests(SparkTestCase):
    @staticmethod
    def _benchmark_payload(output_dir: Path) -> dict[str, object]:
        benchmark_dir = output_dir / "architecture-benchmark"
        benchmark_markdown = benchmark_dir / "memory-architecture-benchmark.md"
        benchmark_dir.mkdir(parents=True, exist_ok=True)
        benchmark_markdown.write_text("# Memory Architecture Benchmark Summary\n", encoding="utf-8")
        return {
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
                "summary_markdown": str(benchmark_markdown),
            },
            "errors": [],
        }

    @staticmethod
    def _live_comparison_payload(output_dir: Path) -> dict[str, object]:
        comparison_dir = output_dir / "architecture-live-comparison"
        comparison_markdown = comparison_dir / "telegram-memory-architecture-live-comparison.md"
        comparison_dir.mkdir(parents=True, exist_ok=True)
        comparison_markdown.write_text("# Telegram Memory Architecture Live Comparison\n", encoding="utf-8")
        return {
            "summary": {
                "case_count": 8,
                "leader_names": ["summary_synthesis_memory"],
                "recommended_runtime_architecture": "summary_synthesis_memory",
                "runtime_matches_live_leader": False,
            },
            "artifact_paths": {
                "summary_markdown": str(comparison_markdown),
            },
            "errors": [],
        }

    def test_memory_run_telegram_regression_dispatches_runner(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression"
        write_path = output_dir / "summary.json"
        payload = {
            "summary": {
                "case_count": len(DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES),
                "matched_case_count": len(DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES),
                "mismatched_case_count": 0,
                "selected_user_id": "12345",
                "selected_chat_id": "12345",
                "kb_has_probe_coverage": True,
                "kb_current_state_hits": 10,
                "kb_current_state_total": 10,
                "kb_evidence_hits": 10,
                "kb_evidence_total": 10,
            }
        }

        with patch(
            "spark_intelligence.cli.run_telegram_memory_regression",
            return_value=TelegramMemoryRegressionResult(output_dir=output_dir, payload=payload),
        ) as run_regression:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "run-telegram-regression",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
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
                str(write_path),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(
            json.loads(stdout)["summary"]["case_count"],
            len(DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES),
        )
        kwargs = run_regression.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["user_id"], "12345")
        self.assertEqual(kwargs["chat_id"], "12345")
        self.assertEqual(kwargs["kb_limit"], 12)
        self.assertEqual(kwargs["validator_root"], "C:/validator")
        self.assertEqual(kwargs["write_path"], str(write_path))
        self.assertEqual(kwargs["case_ids"], ["country_query"])
        self.assertEqual(kwargs["categories"], ["overwrite"])

    def test_run_telegram_memory_regression_blocks_fast_when_user_is_not_paired(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-blocked"
        unauthorized_payload = {
            "message": "My name is Sarah.",
            "user_id": "22345",
            "chat_id": "22345",
            "result": {
                "ok": False,
                "decision": "pending_pairing",
                "detail": {
                    "response_text": "Unauthorized DM. Pairing approval is required before this agent will respond.",
                },
            },
        }

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            return_value=json.dumps(unauthorized_payload),
        ) as ask_telegram, patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
        ) as compile_kb:
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="22345",
                chat_id="22345",
            )

        payload = result.payload
        self.assertEqual(payload["summary"]["status"], "blocked_precondition")
        self.assertIn("pending_pairing", payload["summary"]["blocked_reason"])
        self.assertEqual(len(payload["cases"]), 1)
        ask_telegram.assert_called_once()
        compile_kb.assert_not_called()

    def test_run_telegram_memory_regression_writes_operator_summary_and_passes_it_to_kb_compile(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-with-summary"
        allowed_payload = {
            "message": "ok",
            "user_id": "12345",
            "chat_id": "12345",
            "result": {
                "ok": True,
                "decision": "allowed",
                "detail": {
                    "response_text": "ok",
                    "bridge_mode": "memory_profile_fact",
                    "routing_decision": "memory_profile_fact_query",
                    "trace_ref": "trace:test",
                },
            },
        }
        kb_payload = {
            "failure_taxonomy": {"summary": {"has_probe_coverage": True, "issue_labels": []}},
            "probe_rows": [
                {"probe_type": "current_state", "hits": 1, "total": 1},
                {"probe_type": "evidence", "hits": 1, "total": 1},
            ],
        }

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            return_value=json.dumps(allowed_payload),
        ), patch(
            "spark_intelligence.memory.regression.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(
                to_json=lambda: json.dumps(
                    {
                        "read_result": {
                            "records": [
                                {
                                    "predicate": "profile.startup_name",
                                    "normalized_value": "Seedify",
                                }
                            ]
                        }
                    }
                )
            ),
        ), patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
            return_value=SimpleNamespace(payload=kb_payload),
        ) as compile_kb, patch(
            "spark_intelligence.memory.regression.benchmark_memory_architectures",
            return_value=SimpleNamespace(payload=self._benchmark_payload(output_dir)),
        ), patch(
            "spark_intelligence.memory.regression.compare_telegram_memory_architectures",
            return_value=SimpleNamespace(payload=self._live_comparison_payload(output_dir)),
        ) as run_benchmark:
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
            )

        kwargs = compile_kb.call_args.kwargs
        self.assertTrue(run_benchmark.called)
        repo_sources = kwargs["repo_sources"]
        self.assertEqual(len(repo_sources), 4)
        summary_path = Path(repo_sources[0])
        self.assertTrue(summary_path.exists())
        summary_text = summary_path.read_text(encoding="utf-8")
        self.assertIn("# Telegram Memory Regression Summary", summary_text)
        self.assertIn("## Live Architecture Comparison", summary_text)
        self.assertIn("## Category Coverage", summary_text)
        self.assertIn("## Route Coverage", summary_text)
        self.assertIn("## Quality Lanes", summary_text)
        self.assertIn("## Current Memory Snapshot", summary_text)
        self.assertIn("## Recommended Next Actions", summary_text)
        self.assertIn("`profile.startup_name`: `Seedify`", summary_text)
        self.assertIn("startup_query_after_founder", summary_text)
        self.assertIn("country_query_after_overwrite", summary_text)
        cases_json_path = Path(repo_sources[1])
        self.assertEqual(cases_json_path, output_dir / "regression-cases.json")
        self.assertTrue(cases_json_path.exists())
        benchmark_markdown_path = Path(repo_sources[2])
        self.assertEqual(
            benchmark_markdown_path,
            output_dir / "architecture-benchmark" / "memory-architecture-benchmark.md",
        )
        live_comparison_markdown_path = Path(repo_sources[3])
        self.assertEqual(
            live_comparison_markdown_path,
            output_dir / "architecture-live-comparison" / "telegram-memory-architecture-live-comparison.md",
        )
        cases_payload = json.loads(cases_json_path.read_text(encoding="utf-8"))
        self.assertEqual(cases_payload["selected_user_id"], "12345")
        self.assertEqual(cases_payload["cases"][0]["case_id"], "name_write")
        self.assertIn("architecture_live_comparison", cases_payload)
        self.assertEqual(result.payload["summary"]["category_counts"]["overwrite"], 4)
        self.assertTrue(result.payload["summary"]["quality_lanes"]["overwrite"])
        self.assertEqual(
            result.payload["summary"]["architecture_documented_frontier"],
            "summary_synthesis_memory",
        )
        self.assertFalse(result.payload["summary"]["architecture_runtime_matches_documented_frontier"])
        self.assertEqual(
            result.payload["summary"]["architecture_product_memory_leaders"],
            ["observational_temporal_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(result.payload["summary"]["live_architecture_case_count"], 8)
        self.assertEqual(
            result.payload["summary"]["live_architecture_leaders"],
            ["summary_synthesis_memory"],
        )
        self.assertEqual(
            result.payload["summary"]["live_architecture_recommended_runtime"],
            "summary_synthesis_memory",
        )
        self.assertFalse(result.payload["summary"]["live_architecture_runtime_matches_leader"])
        self.assertEqual(
            Path(result.payload["artifact_paths"]["regression_report_markdown"]),
            summary_path,
        )
        self.assertEqual(
            Path(result.payload["artifact_paths"]["regression_cases_json"]),
            cases_json_path,
        )
        self.assertEqual(
            Path(result.payload["artifact_paths"]["architecture_benchmark_markdown"]),
            benchmark_markdown_path,
        )
        self.assertEqual(
            Path(result.payload["artifact_paths"]["architecture_live_comparison_markdown"]),
            live_comparison_markdown_path,
        )

    def test_run_telegram_memory_regression_filters_cases_by_category(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-overwrite-only"

        def fake_gateway_payload(*, message: str, **_: object) -> str:
            return json.dumps(
                {
                    "message": message,
                    "user_id": "12345",
                    "chat_id": "12345",
                    "result": {
                        "ok": True,
                        "decision": "allowed",
                        "detail": {
                            "response_text": message,
                            "bridge_mode": "memory_profile_fact",
                            "routing_decision": "memory_profile_fact_query",
                            "trace_ref": "trace:test",
                        },
                    },
                }
            )

        kb_payload = {
            "failure_taxonomy": {"summary": {"has_probe_coverage": True, "issue_labels": []}},
            "probe_rows": [
                {"probe_type": "current_state", "hits": 1, "total": 1},
                {"probe_type": "evidence", "hits": 1, "total": 1},
            ],
        }

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            side_effect=fake_gateway_payload,
        ) as ask_telegram, patch(
            "spark_intelligence.memory.regression.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(to_json=lambda: json.dumps({"records": []})),
        ), patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
            return_value=SimpleNamespace(payload=kb_payload),
        ), patch(
            "spark_intelligence.memory.regression.benchmark_memory_architectures",
            return_value=SimpleNamespace(payload=self._benchmark_payload(output_dir)),
        ), patch(
            "spark_intelligence.memory.regression.compare_telegram_memory_architectures",
            return_value=SimpleNamespace(payload=self._live_comparison_payload(output_dir)),
        ):
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
                categories=["overwrite"],
            )

        self.assertEqual(ask_telegram.call_count, 4)
        self.assertEqual(result.payload["summary"]["case_count"], 4)
        self.assertEqual(result.payload["summary"]["selected_categories"], ["overwrite"])
        self.assertEqual(
            result.payload["summary"]["selected_case_ids"],
            [
                "city_overwrite",
                "country_overwrite",
                "country_query_after_overwrite",
                "city_query_after_overwrite",
            ],
        )
        self.assertEqual(result.payload["summary"]["category_counts"], {"overwrite": 4})
        self.assertEqual(
            result.payload["summary"]["quality_lanes"],
            {"staleness": False, "overwrite": True, "abstention": False},
        )

    def test_run_telegram_memory_regression_isolates_abstention_cases_to_fresh_subjects(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-abstention-only"
        seen_pairs: list[tuple[str | None, str | None]] = []

        def fake_gateway_payload(*, message: str, user_id: str | None = None, chat_id: str | None = None, **_: object) -> str:
            seen_pairs.append((user_id, chat_id))
            return json.dumps(
                {
                    "message": message,
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "result": {
                        "ok": True,
                        "decision": "allowed",
                        "detail": {
                            "response_text": "I don't currently have that saved.",
                            "bridge_mode": "memory_profile_fact",
                            "routing_decision": "memory_profile_fact_query",
                            "trace_ref": "trace:test",
                        },
                    },
                }
            )

        kb_payload = {
            "failure_taxonomy": {"summary": {"has_probe_coverage": True, "issue_labels": []}},
            "probe_rows": [
                {"probe_type": "current_state", "hits": 1, "total": 1},
                {"probe_type": "evidence", "hits": 1, "total": 1},
            ],
        }

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            side_effect=fake_gateway_payload,
        ), patch(
            "spark_intelligence.memory.regression.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(to_json=lambda: json.dumps({"records": []})),
        ), patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
            return_value=SimpleNamespace(payload=kb_payload),
        ), patch(
            "spark_intelligence.memory.regression.benchmark_memory_architectures",
            return_value=SimpleNamespace(payload=self._benchmark_payload(output_dir)),
        ), patch(
            "spark_intelligence.memory.regression.compare_telegram_memory_architectures",
            return_value=SimpleNamespace(payload=self._live_comparison_payload(output_dir)),
        ):
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
                categories=["abstention"],
            )

        self.assertEqual(
            seen_pairs,
            [
                ("12345-spark_role_abstention", "12345-spark_role_abstention"),
                ("12345-hack_actor_query_missing", "12345-hack_actor_query_missing"),
            ],
        )
        self.assertEqual(result.payload["summary"]["case_count"], 2)
        self.assertEqual(result.payload["summary"]["selected_categories"], ["abstention"])
