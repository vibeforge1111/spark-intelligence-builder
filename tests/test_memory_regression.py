from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory import (
    TelegramMemoryAcceptancePackExportResult,
    TelegramMemoryAcceptanceResult,
    TelegramMemoryRegressionResult,
    export_telegram_memory_acceptance_pack,
    run_telegram_memory_acceptance,
    run_telegram_memory_regression,
)
from spark_intelligence.memory.acceptance import DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES, TelegramMemoryAcceptanceCase
from spark_intelligence.memory.regression import (
    DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES,
    _allocate_regression_identity,
    _prepare_regression_identity,
)

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
                "runtime_memory_architecture": "dual_store_event_calendar_hybrid",
                "baseline_names": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
                "documented_frontier_architecture": "dual_store_event_calendar_hybrid",
                "runtime_matches_documented_frontier": True,
                "product_memory_leader_names": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
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
                "baseline_names": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
                "case_count": 8,
                "leader_names": ["dual_store_event_calendar_hybrid"],
                "recommended_runtime_architecture": "dual_store_event_calendar_hybrid",
                "current_runtime_memory_architecture": "dual_store_event_calendar_hybrid",
                "runtime_matches_live_leader": True,
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
                "--benchmark-pack",
                "identity_under_recency_pressure",
                "--case-id",
                "country_query",
                "--category",
                "overwrite",
                "--baseline",
                "summary_synthesis_memory",
                "--baseline",
                "dual_store_event_calendar_hybrid",
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
        self.assertEqual(kwargs["benchmark_pack_ids"], ["identity_under_recency_pressure"])
        self.assertEqual(kwargs["case_ids"], ["country_query"])
        self.assertEqual(kwargs["categories"], ["overwrite"])
        self.assertEqual(
            kwargs["baseline_names"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )

    def test_memory_run_telegram_acceptance_dispatches_runner(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-acceptance"
        write_path = output_dir / "summary.json"
        payload = {
            "summary": {
                "status": "passed",
                "case_count": len(DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES),
                "matched_case_count": len(DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES),
                "mismatched_case_count": 0,
                "promotion_gate_status": "pass",
                "selected_user_id": "12345",
                "selected_chat_id": "12345",
            }
        }

        with patch(
            "spark_intelligence.cli.run_telegram_memory_acceptance",
            return_value=TelegramMemoryAcceptanceResult(output_dir=output_dir, payload=payload),
        ) as run_acceptance:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "run-telegram-acceptance",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--user-id",
                "12345",
                "--chat-id",
                "12345",
                "--write",
                str(write_path),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["status"], "passed")
        kwargs = run_acceptance.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["user_id"], "12345")
        self.assertEqual(kwargs["chat_id"], "12345")
        self.assertEqual(kwargs["write_path"], str(write_path))

    def test_memory_export_telegram_acceptance_pack_dispatches_exporter(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-acceptance-supervised"
        write_path = output_dir / "pack.json"
        markdown_path = output_dir / "pack.md"
        payload = {
            "summary": {
                "pack_kind": "telegram_memory_acceptance_supervised",
                "case_count": len(DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES),
            },
            "artifact_paths": {
                "pack_json": str(write_path),
                "operator_markdown": str(markdown_path),
            },
        }

        with patch(
            "spark_intelligence.cli.export_telegram_memory_acceptance_pack",
            return_value=TelegramMemoryAcceptancePackExportResult(output_dir=output_dir, payload=payload),
        ) as export_pack:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "export-telegram-acceptance-pack",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--write",
                str(write_path),
                "--markdown",
                str(markdown_path),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["pack_kind"], "telegram_memory_acceptance_supervised")
        kwargs = export_pack.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["write_path"], str(write_path))
        self.assertEqual(kwargs["markdown_path"], str(markdown_path))

    def test_export_telegram_memory_acceptance_pack_writes_operator_files(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-acceptance-supervised"

        result = export_telegram_memory_acceptance_pack(
            config_manager=self.config_manager,
            output_dir=output_dir,
        )

        pack_json = output_dir / "telegram-memory-acceptance-pack.json"
        pack_markdown = output_dir / "telegram-memory-acceptance-pack.md"
        self.assertTrue(pack_json.exists())
        self.assertTrue(pack_markdown.exists())
        self.assertEqual(result.payload["summary"]["case_count"], len(DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES))
        self.assertEqual(result.payload["cases"][0]["case_id"], "seed_focus")
        self.assertIn("Set my current focus to persistent memory quality evaluation.", pack_markdown.read_text(encoding="utf-8"))
        self.assertEqual(result.payload["capture_template"][0]["live_response"], "")

    def test_run_telegram_memory_acceptance_asserts_cases_and_promotion_gates(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-acceptance-runner"

        def allowed_payload(message: str, bridge_mode: str, routing_decision: str, response_text: str) -> str:
            return json.dumps(
                {
                    "message": message,
                    "user_id": "12345",
                    "chat_id": "12345",
                    "result": {
                        "ok": True,
                        "decision": "allowed",
                        "detail": {
                            "response_text": response_text,
                            "bridge_mode": bridge_mode,
                            "routing_decision": routing_decision,
                            "trace_ref": "trace:test",
                        },
                    },
                }
            )

        gateway_payloads = [
            allowed_payload(
                case.message,
                {
                    "current_focus_plan_query": "memory_current_focus_plan",
                    "open_ended_next_step": "memory_kernel_next_step",
                    "source_explanation": "context_source_debug",
                    "natural_fact_recall": "memory_open_recall",
                    "natural_fact_updated_recall": "memory_open_recall",
                    "natural_fact_history_recall": "memory_profile_fact_history",
                    "entity_location_recall_desk": "memory_open_recall",
                    "entity_location_recall_office": "memory_open_recall",
                    "entity_location_updated_recall_desk": "memory_open_recall",
                    "entity_location_history_recall_desk": "memory_entity_state_history",
                    "entity_owner_recall_launch": "memory_open_recall",
                    "entity_owner_recall_investor": "memory_open_recall",
                    "entity_owner_updated_recall_launch": "memory_open_recall",
                    "entity_owner_history_recall_launch": "memory_entity_state_history",
                    "entity_owner_history_source_explanation": "context_source_debug",
                    "entity_status_recall_launch": "memory_open_recall",
                    "entity_status_history_launch": "memory_entity_state_history",
                    "entity_deadline_recall_launch": "memory_open_recall",
                    "entity_deadline_history_launch": "memory_entity_state_history",
                    "entity_relation_recall_launch": "memory_open_recall",
                    "entity_relation_history_launch": "memory_entity_state_history",
                    "entity_preference_recall_launch": "memory_open_recall",
                    "entity_preference_history_launch": "memory_entity_state_history",
                    "entity_project_recall_launch": "memory_open_recall",
                    "entity_project_history_launch": "memory_entity_state_history",
                }.get(case.case_id, "memory_generic_observation_update"),
                {
                    "current_focus_plan_query": "memory_current_focus_plan_query",
                    "open_ended_next_step": "memory_kernel_next_step",
                    "source_explanation": "context_source_debug",
                    "natural_fact_recall": "memory_open_recall_query",
                    "natural_fact_updated_recall": "memory_open_recall_query",
                    "natural_fact_history_recall": "memory_profile_fact_history_query",
                    "entity_location_recall_desk": "memory_open_recall_query",
                    "entity_location_recall_office": "memory_open_recall_query",
                    "entity_location_updated_recall_desk": "memory_open_recall_query",
                    "entity_location_history_recall_desk": "memory_entity_state_history_query",
                    "entity_owner_recall_launch": "memory_open_recall_query",
                    "entity_owner_recall_investor": "memory_open_recall_query",
                    "entity_owner_updated_recall_launch": "memory_open_recall_query",
                    "entity_owner_history_recall_launch": "memory_entity_state_history_query",
                    "entity_owner_history_source_explanation": "context_source_debug",
                    "entity_status_recall_launch": "memory_open_recall_query",
                    "entity_status_history_launch": "memory_entity_state_history_query",
                    "entity_deadline_recall_launch": "memory_open_recall_query",
                    "entity_deadline_history_launch": "memory_entity_state_history_query",
                    "entity_relation_recall_launch": "memory_open_recall_query",
                    "entity_relation_history_launch": "memory_entity_state_history_query",
                    "entity_preference_recall_launch": "memory_open_recall_query",
                    "entity_preference_history_launch": "memory_entity_state_history_query",
                    "entity_project_recall_launch": "memory_open_recall_query",
                    "entity_project_history_launch": "memory_entity_state_history_query",
                }.get(case.case_id, "memory_generic_observation"),
                {
                    "seed_focus": "I'll remember that your current focus is persistent memory quality evaluation.",
                    "seed_old_plan": "Done. Your current plan is now: verify scheduled memory cleanup.",
                    "replace_plan": "Done. Your current plan is now: evaluate open-ended persistent memory recall.",
                    "natural_fact_seed": "I'll remember that the tiny desk plant is named Mira.",
                    "current_focus_plan_query": (
                        "Your current focus is persistent memory quality evaluation.\n"
                        "Your current plan is to evaluate open-ended persistent memory recall."
                    ),
                    "open_ended_next_step": (
                        "Your active focus is persistent memory quality evaluation.\n"
                        "Your active plan is evaluate open-ended persistent memory recall."
                    ),
                    "source_explanation": (
                        "I answered from the memory kernel next-step route.\n"
                        "- promotion gates: pass\n"
                        "- focus source: current_state via get_current_state"
                    ),
                    "natural_fact_recall": "You named the tiny desk plant Mira.",
                    "natural_fact_replace": "I'll remember that the tiny desk plant is named Sol.",
                    "natural_fact_updated_recall": "You named the tiny desk plant Sol.",
                    "natural_fact_history_recall": "Before Sol, the tiny desk plant was named Mira.",
                    "entity_location_seed_desk": "I'll remember that the tiny desk plant is on the kitchen shelf.",
                    "entity_location_seed_office": "I'll remember that the office plant is on the balcony.",
                    "entity_location_recall_desk": "The tiny desk plant is on the kitchen shelf.",
                    "entity_location_recall_office": "The office plant is on the balcony.",
                    "entity_location_replace_desk": "I'll remember that the tiny desk plant is on the windowsill.",
                    "entity_location_updated_recall_desk": "The tiny desk plant is on the windowsill.",
                    "entity_location_history_recall_desk": (
                        "Before the tiny desk plant was on the windowsill, it was on the kitchen shelf."
                    ),
                    "entity_owner_seed_launch": "I'll remember that the launch checklist is owned by Omar.",
                    "entity_owner_seed_investor": "I'll remember that the investor update is owned by Lina.",
                    "entity_owner_recall_launch": "The launch checklist is owned by Omar.",
                    "entity_owner_recall_investor": "The investor update is owned by Lina.",
                    "entity_owner_replace_launch": "I'll remember that the launch checklist is owned by Maya.",
                    "entity_owner_updated_recall_launch": "The launch checklist is owned by Maya.",
                    "entity_owner_history_recall_launch": (
                        "Before the launch checklist was owned by Maya, it was owned by Omar."
                    ),
                    "entity_owner_history_source_explanation": (
                        "I answered from the entity-state history route. "
                        "predicate=entity.owner attribute=owner entity-scoped owner history"
                    ),
                    "entity_status_seed_launch": "I'll remember that the launch checklist status is blocked.",
                    "entity_status_replace_launch": "I'll remember that the launch checklist status is ready.",
                    "entity_status_recall_launch": "The launch checklist status is ready.",
                    "entity_status_history_launch": (
                        "Before the launch checklist status was ready, it was blocked."
                    ),
                    "entity_deadline_seed_launch": "I'll remember that the launch checklist is due Friday.",
                    "entity_deadline_replace_launch": "I'll remember that the launch checklist is due Monday.",
                    "entity_deadline_recall_launch": "The launch checklist is due Monday.",
                    "entity_deadline_history_launch": (
                        "Before the launch checklist was due Monday, it was due Friday."
                    ),
                    "entity_relation_seed_launch": "I'll remember that the launch checklist is related to investor update.",
                    "entity_relation_replace_launch": "I'll remember that the launch checklist is related to board prep.",
                    "entity_relation_recall_launch": "The launch checklist is related to board prep.",
                    "entity_relation_history_launch": (
                        "Before the launch checklist was related to board prep, it was related to investor update."
                    ),
                    "entity_preference_seed_launch": "I'll remember that the launch checklist preference is concise bullets.",
                    "entity_preference_replace_launch": "I'll remember that the launch checklist preference is short sections.",
                    "entity_preference_recall_launch": "The launch checklist preference is short sections.",
                    "entity_preference_history_launch": (
                        "Before the launch checklist preference was short sections, it was concise bullets."
                    ),
                    "entity_project_seed_launch": "I'll remember that the launch checklist project is Neon Harbor.",
                    "entity_project_replace_launch": "I'll remember that the launch checklist project is Seed Round.",
                    "entity_project_recall_launch": "The launch checklist project is Seed Round.",
                    "entity_project_history_launch": (
                        "Before the launch checklist project was Seed Round, it was Neon Harbor."
                    ),
                }[case.case_id],
            )
            for case in DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES
        ]
        fake_packet = SimpleNamespace(
            to_payload=lambda: {
                "source_mix": {"current_state": 1, "structured_evidence": 1},
                "sections": [{"section": "active_current_state"}],
                "trace": {
                    "promotion_gates": {
                        "status": "pass",
                        "mode": "trace_only",
                        "gates": {
                            "source_swamp_resistance": {"status": "pass"},
                            "stale_current_conflict": {"status": "pass"},
                            "recent_conversation_noise": {"status": "pass"},
                            "source_mix_stability": {"status": "pass"},
                        },
                    }
                },
            }
        )

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            side_effect=gateway_payloads,
        ) as ask_telegram, patch(
            "spark_intelligence.memory.acceptance.hybrid_memory_retrieve",
            return_value=SimpleNamespace(context_packet=fake_packet),
        ) as retrieve:
            result = run_telegram_memory_acceptance(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
            )

        self.assertEqual(result.payload["summary"]["status"], "passed")
        self.assertEqual(result.payload["summary"]["promotion_gate_status"], "pass")
        self.assertEqual(result.payload["summary"]["promotion_gate_enforcement"], "blocking_acceptance")
        self.assertFalse(result.payload["summary"]["promotion_gate_blocking"])
        self.assertEqual(result.payload["summary"]["mismatched_case_count"], 0)
        self.assertEqual(result.payload["gate_assertions"]["mismatches"], [])
        self.assertEqual(result.payload["gate_assertions"]["enforcement"]["mode"], "blocking_acceptance")
        self.assertFalse(result.payload["gate_assertions"]["enforcement"]["blocking"])
        self.assertEqual(ask_telegram.call_count, len(DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES))
        self.assertEqual(retrieve.call_args.kwargs["subject"], "human:telegram:12345")
        self.assertEqual(retrieve.call_args.kwargs["predicate"], "profile.current_focus")
        self.assertTrue((output_dir / "telegram-memory-acceptance.json").exists())

    def test_run_telegram_memory_acceptance_blocks_on_promotion_gate_warning(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-acceptance-gate-fail"

        def allowed_payload(case: TelegramMemoryAcceptanceCase, response_text: str) -> str:
            return json.dumps(
                {
                    "message": case.message,
                    "user_id": "12345",
                    "chat_id": "12345",
                    "result": {
                        "ok": True,
                        "decision": "allowed",
                        "detail": {
                            "response_text": response_text,
                            "bridge_mode": {
                                "current_focus_plan_query": "memory_current_focus_plan",
                                "open_ended_next_step": "memory_kernel_next_step",
                                "source_explanation": "context_source_debug",
                                "natural_fact_recall": "memory_open_recall",
                                "natural_fact_updated_recall": "memory_open_recall",
                                "natural_fact_history_recall": "memory_profile_fact_history",
                                "entity_location_recall_desk": "memory_open_recall",
                                "entity_location_recall_office": "memory_open_recall",
                                "entity_location_updated_recall_desk": "memory_open_recall",
                                "entity_location_history_recall_desk": "memory_entity_state_history",
                                "entity_owner_recall_launch": "memory_open_recall",
                                "entity_owner_recall_investor": "memory_open_recall",
                                "entity_owner_updated_recall_launch": "memory_open_recall",
                                "entity_owner_history_recall_launch": "memory_entity_state_history",
                                "entity_owner_history_source_explanation": "context_source_debug",
                                "entity_status_recall_launch": "memory_open_recall",
                                "entity_status_history_launch": "memory_entity_state_history",
                                "entity_deadline_recall_launch": "memory_open_recall",
                                "entity_deadline_history_launch": "memory_entity_state_history",
                                "entity_relation_recall_launch": "memory_open_recall",
                                "entity_relation_history_launch": "memory_entity_state_history",
                                "entity_preference_recall_launch": "memory_open_recall",
                                "entity_preference_history_launch": "memory_entity_state_history",
                                "entity_project_recall_launch": "memory_open_recall",
                                "entity_project_history_launch": "memory_entity_state_history",
                            }.get(case.case_id, "memory_generic_observation_update"),
                            "routing_decision": {
                                "current_focus_plan_query": "memory_current_focus_plan_query",
                                "open_ended_next_step": "memory_kernel_next_step",
                                "source_explanation": "context_source_debug",
                                "natural_fact_recall": "memory_open_recall_query",
                                "natural_fact_updated_recall": "memory_open_recall_query",
                                "natural_fact_history_recall": "memory_profile_fact_history_query",
                                "entity_location_recall_desk": "memory_open_recall_query",
                                "entity_location_recall_office": "memory_open_recall_query",
                                "entity_location_updated_recall_desk": "memory_open_recall_query",
                                "entity_location_history_recall_desk": "memory_entity_state_history_query",
                                "entity_owner_recall_launch": "memory_open_recall_query",
                                "entity_owner_recall_investor": "memory_open_recall_query",
                                "entity_owner_updated_recall_launch": "memory_open_recall_query",
                                "entity_owner_history_recall_launch": "memory_entity_state_history_query",
                                "entity_owner_history_source_explanation": "context_source_debug",
                                "entity_status_recall_launch": "memory_open_recall_query",
                                "entity_status_history_launch": "memory_entity_state_history_query",
                                "entity_deadline_recall_launch": "memory_open_recall_query",
                                "entity_deadline_history_launch": "memory_entity_state_history_query",
                                "entity_relation_recall_launch": "memory_open_recall_query",
                                "entity_relation_history_launch": "memory_entity_state_history_query",
                                "entity_preference_recall_launch": "memory_open_recall_query",
                                "entity_preference_history_launch": "memory_entity_state_history_query",
                                "entity_project_recall_launch": "memory_open_recall_query",
                                "entity_project_history_launch": "memory_entity_state_history_query",
                            }.get(case.case_id, "memory_generic_observation"),
                            "trace_ref": "trace:test",
                        },
                    },
                }
            )

        gateway_payloads = [
            allowed_payload(
                case,
                {
                    "seed_focus": "I'll remember that your current focus is persistent memory quality evaluation.",
                    "seed_old_plan": "Done. Your current plan is now: verify scheduled memory cleanup.",
                    "replace_plan": "Done. Your current plan is now: evaluate open-ended persistent memory recall.",
                    "natural_fact_seed": "I'll remember that the tiny desk plant is named Mira.",
                    "current_focus_plan_query": (
                        "Your current focus is persistent memory quality evaluation.\n"
                        "Your current plan is to evaluate open-ended persistent memory recall."
                    ),
                    "open_ended_next_step": (
                        "Your active focus is persistent memory quality evaluation.\n"
                        "Your active plan is evaluate open-ended persistent memory recall."
                    ),
                    "source_explanation": (
                        "I answered from the memory kernel next-step route.\n"
                        "- promotion gates: warn\n"
                        "- focus source: current_state via get_current_state"
                    ),
                    "natural_fact_recall": "You named the tiny desk plant Mira.",
                    "natural_fact_replace": "I'll remember that the tiny desk plant is named Sol.",
                    "natural_fact_updated_recall": "You named the tiny desk plant Sol.",
                    "natural_fact_history_recall": "Before Sol, the tiny desk plant was named Mira.",
                    "entity_location_seed_desk": "I'll remember that the tiny desk plant is on the kitchen shelf.",
                    "entity_location_seed_office": "I'll remember that the office plant is on the balcony.",
                    "entity_location_recall_desk": "The tiny desk plant is on the kitchen shelf.",
                    "entity_location_recall_office": "The office plant is on the balcony.",
                    "entity_location_replace_desk": "I'll remember that the tiny desk plant is on the windowsill.",
                    "entity_location_updated_recall_desk": "The tiny desk plant is on the windowsill.",
                    "entity_location_history_recall_desk": (
                        "Before the tiny desk plant was on the windowsill, it was on the kitchen shelf."
                    ),
                    "entity_owner_seed_launch": "I'll remember that the launch checklist is owned by Omar.",
                    "entity_owner_seed_investor": "I'll remember that the investor update is owned by Lina.",
                    "entity_owner_recall_launch": "The launch checklist is owned by Omar.",
                    "entity_owner_recall_investor": "The investor update is owned by Lina.",
                    "entity_owner_replace_launch": "I'll remember that the launch checklist is owned by Maya.",
                    "entity_owner_updated_recall_launch": "The launch checklist is owned by Maya.",
                    "entity_owner_history_recall_launch": (
                        "Before the launch checklist was owned by Maya, it was owned by Omar."
                    ),
                    "entity_owner_history_source_explanation": (
                        "I answered from the entity-state history route. "
                        "predicate=entity.owner attribute=owner entity-scoped owner history"
                    ),
                    "entity_status_seed_launch": "I'll remember that the launch checklist status is blocked.",
                    "entity_status_replace_launch": "I'll remember that the launch checklist status is ready.",
                    "entity_status_recall_launch": "The launch checklist status is ready.",
                    "entity_status_history_launch": (
                        "Before the launch checklist status was ready, it was blocked."
                    ),
                    "entity_deadline_seed_launch": "I'll remember that the launch checklist is due Friday.",
                    "entity_deadline_replace_launch": "I'll remember that the launch checklist is due Monday.",
                    "entity_deadline_recall_launch": "The launch checklist is due Monday.",
                    "entity_deadline_history_launch": (
                        "Before the launch checklist was due Monday, it was due Friday."
                    ),
                    "entity_relation_seed_launch": "I'll remember that the launch checklist is related to investor update.",
                    "entity_relation_replace_launch": "I'll remember that the launch checklist is related to board prep.",
                    "entity_relation_recall_launch": "The launch checklist is related to board prep.",
                    "entity_relation_history_launch": (
                        "Before the launch checklist was related to board prep, it was related to investor update."
                    ),
                    "entity_preference_seed_launch": "I'll remember that the launch checklist preference is concise bullets.",
                    "entity_preference_replace_launch": "I'll remember that the launch checklist preference is short sections.",
                    "entity_preference_recall_launch": "The launch checklist preference is short sections.",
                    "entity_preference_history_launch": (
                        "Before the launch checklist preference was short sections, it was concise bullets."
                    ),
                    "entity_project_seed_launch": "I'll remember that the launch checklist project is Neon Harbor.",
                    "entity_project_replace_launch": "I'll remember that the launch checklist project is Seed Round.",
                    "entity_project_recall_launch": "The launch checklist project is Seed Round.",
                    "entity_project_history_launch": (
                        "Before the launch checklist project was Seed Round, it was Neon Harbor."
                    ),
                }[case.case_id],
            )
            for case in DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES
        ]
        fake_packet = SimpleNamespace(
            to_payload=lambda: {
                "source_mix": {"current_state": 1, "recent_conversation": 3},
                "sections": [{"section": "active_current_state"}],
                "trace": {
                    "promotion_gates": {
                        "status": "warn",
                        "mode": "trace_only",
                        "gates": {
                            "source_swamp_resistance": {"status": "pass"},
                            "stale_current_conflict": {"status": "pass"},
                            "recent_conversation_noise": {"status": "warn"},
                            "source_mix_stability": {"status": "pass"},
                        },
                    }
                },
            }
        )

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            side_effect=gateway_payloads,
        ), patch(
            "spark_intelligence.memory.acceptance.hybrid_memory_retrieve",
            return_value=SimpleNamespace(context_packet=fake_packet),
        ):
            result = run_telegram_memory_acceptance(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
            )

        self.assertEqual(result.payload["summary"]["status"], "failed")
        self.assertEqual(result.payload["summary"]["promotion_gate_status"], "warn")
        self.assertTrue(result.payload["summary"]["promotion_gate_blocking"])
        self.assertIn("promotion_gate_status:warn", result.payload["gate_assertions"]["mismatches"])
        self.assertIn("recent_conversation_noise:warn", result.payload["gate_assertions"]["mismatches"])
        self.assertEqual(
            result.payload["gate_assertions"]["enforcement"]["blockers"],
            result.payload["gate_assertions"]["mismatches"],
        )

    def test_prepare_regression_identity_sets_agent_name_before_runtime(self) -> None:
        with patch(
            "spark_intelligence.memory.regression.approve_pairing",
        ) as approve_pairing_mock, patch(
            "spark_intelligence.memory.regression.rename_agent_identity",
        ) as rename_agent_identity_mock, patch(
            "spark_intelligence.memory.regression.consume_pairing_welcome",
        ) as consume_pairing_welcome_mock:
            _prepare_regression_identity(
                state_db=self.state_db,
                external_user_id="regression-user-123",
                username="memory-regression",
            )

        approve_pairing_mock.assert_called_once()
        rename_agent_identity_mock.assert_called_once_with(
            state_db=self.state_db,
            human_id="human:telegram:regression-user-123",
            new_name="Atlas",
            source_surface="memory_regression",
            source_ref="memory-regression-setup",
        )
        consume_pairing_welcome_mock.assert_called_once()

    def test_allocate_regression_identity_uses_telegram_compatible_decimal_user_id(self) -> None:
        user_id, chat_id = _allocate_regression_identity(chat_id=None)

        self.assertTrue(user_id.isdecimal())
        self.assertGreater(int(user_id), 0)
        self.assertEqual(chat_id, user_id)

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
                    "response_text": "Access is not authorized for this channel. Ask the operator to review access.",
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

    def test_run_telegram_memory_regression_selects_custom_cases_from_benchmark_pack(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-pack"
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
            "probe_rows": [],
        }

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            return_value=json.dumps(allowed_payload),
        ), patch(
            "spark_intelligence.memory.regression.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(to_json=lambda: json.dumps({"read_result": {"records": []}})),
        ), patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
            return_value=SimpleNamespace(payload=kb_payload),
        ) as compile_kb, patch(
            "spark_intelligence.memory.regression.benchmark_memory_architectures",
            return_value=SimpleNamespace(payload=self._benchmark_payload(output_dir)),
        ) as run_benchmark, patch(
            "spark_intelligence.memory.regression.compare_telegram_memory_architectures",
            return_value=SimpleNamespace(payload=self._live_comparison_payload(output_dir)),
        ) as live_compare:
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
                benchmark_pack_ids=["identity_under_recency_pressure"],
                case_ids=["identity_summary_after_recency_pressure_rich"],
            )

        self.assertEqual(
            result.payload["summary"]["requested_benchmark_pack_ids"],
            ["identity_under_recency_pressure"],
        )
        self.assertEqual(
            result.payload["summary"]["selected_case_ids"],
            ["identity_summary_after_recency_pressure_rich"],
        )
        self.assertEqual(
            result.payload["summary"]["skipped_post_analysis_labels"],
            [
                "architecture_benchmark_skipped_for_focused_slice",
                "architecture_live_comparison_skipped_for_focused_slice",
            ],
        )
        self.assertFalse(run_benchmark.called)
        self.assertFalse(live_compare.called)
        self.assertTrue(compile_kb.called)

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
        self.assertEqual(kwargs["timeout_seconds"], 120.0)
        repo_sources = kwargs["repo_sources"]
        self.assertEqual(len(repo_sources), 4)
        summary_path = Path(repo_sources[0])
        self.assertTrue(summary_path.exists())
        summary_text = summary_path.read_text(encoding="utf-8")
        self.assertIn("# Telegram Memory Regression Summary", summary_text)
        self.assertIn("## Live Architecture Comparison", summary_text)
        self.assertIn("ProductMemory contenders", summary_text)
        self.assertIn("Live Telegram contenders", summary_text)
        self.assertIn("## Category Coverage", summary_text)
        self.assertIn("## Route Coverage", summary_text)
        self.assertIn("## Quality Lanes", summary_text)
        self.assertIn("## Current Memory Snapshot", summary_text)
        self.assertIn("## Recommended Next Actions", summary_text)
        self.assertIn("Keep `dual_store_event_calendar_hybrid` pinned while expanding benchmark coverage.", summary_text)
        self.assertIn("Only promote a memory change after it stays green", summary_text)
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
            "dual_store_event_calendar_hybrid",
        )
        self.assertEqual(
            result.payload["summary"]["architecture_compared_baselines"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(
            result.payload["summary"]["architecture_runtime_memory_architecture"],
            "dual_store_event_calendar_hybrid",
        )
        self.assertTrue(result.payload["summary"]["architecture_runtime_matches_documented_frontier"])
        self.assertEqual(
            result.payload["summary"]["architecture_product_memory_leaders"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(result.payload["summary"]["live_architecture_case_count"], 8)
        self.assertEqual(
            result.payload["summary"]["live_architecture_compared_baselines"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(
            result.payload["summary"]["live_architecture_leaders"],
            ["dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(
            result.payload["summary"]["live_architecture_recommended_runtime"],
            "dual_store_event_calendar_hybrid",
        )
        self.assertTrue(result.payload["summary"]["live_architecture_runtime_matches_leader"])
        self.assertEqual(result.payload["summary"]["issue_labels"], [])
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

    def test_run_telegram_memory_regression_surfaces_architecture_promotion_gap(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-architecture-gap"
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
            "probe_rows": [],
        }
        live_payload = self._live_comparison_payload(output_dir)
        live_payload["summary"]["leader_names"] = ["dual_store_event_calendar_hybrid"]
        live_payload["summary"]["recommended_runtime_architecture"] = "dual_store_event_calendar_hybrid"
        live_payload["summary"]["current_runtime_memory_architecture"] = "summary_synthesis_memory"
        live_payload["summary"]["runtime_matches_live_leader"] = False

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            return_value=json.dumps(allowed_payload),
        ), patch(
            "spark_intelligence.memory.regression.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(to_json=lambda: json.dumps({"read_result": {"records": []}})),
        ), patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
            return_value=SimpleNamespace(payload=kb_payload),
        ), patch(
            "spark_intelligence.memory.regression.benchmark_memory_architectures",
            return_value=SimpleNamespace(payload=self._benchmark_payload(output_dir)),
        ), patch(
            "spark_intelligence.memory.regression.compare_telegram_memory_architectures",
            return_value=SimpleNamespace(payload=live_payload),
        ):
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="12345",
                chat_id="12345",
            )

        self.assertEqual(result.payload["summary"]["issue_labels"], ["architecture_promotion_gap"])
        summary_text = Path(result.payload["artifact_paths"]["regression_report_markdown"]).read_text(
            encoding="utf-8"
        )
        self.assertIn("Promote `dual_store_event_calendar_hybrid` into the Builder runtime selector", summary_text)
        self.assertIn("Treat `architecture_promotion_gap` as unresolved", summary_text)

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

        self.assertEqual(len(seen_pairs), 2)
        self.assertNotEqual(seen_pairs[0], seen_pairs[1])
        for user_id, chat_id in seen_pairs:
            self.assertIsNotNone(user_id)
            assert user_id is not None
            self.assertTrue(user_id.isdecimal())
            self.assertGreater(int(user_id), 0)
            self.assertEqual(chat_id, user_id)
        self.assertEqual(result.payload["summary"]["case_count"], 2)
        self.assertEqual(result.payload["summary"]["selected_categories"], ["abstention"])
