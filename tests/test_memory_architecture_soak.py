from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory.benchmark_packs import default_telegram_memory_benchmark_packs
from spark_intelligence.memory.architecture_soak import run_telegram_memory_architecture_soak

from tests.test_support import SparkTestCase


class MemoryArchitectureSoakTests(SparkTestCase):
    def test_run_telegram_memory_architecture_soak_aggregates_runs(self) -> None:
        payload_one = {
            "summary": {"matched_case_count": 29, "mismatched_case_count": 2},
            "architecture_live_comparison": {
                "summary": {
                    "baseline_names": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
                    "leader_names": ["summary_synthesis_memory"],
                },
                "baseline_results": [
                    {
                        "baseline_name": "observational_temporal_memory",
                        "live_integration_overall": {"matched": 4, "total": 19, "accuracy": 4 / 19},
                    },
                    {
                        "baseline_name": "dual_store_event_calendar_hybrid",
                        "live_integration_overall": {"matched": 4, "total": 19, "accuracy": 4 / 19},
                    },
                    {
                        "baseline_name": "summary_synthesis_memory",
                        "live_integration_overall": {"matched": 5, "total": 19, "accuracy": 5 / 19},
                    },
                ],
            },
        }
        payload_two = {
            "summary": {"matched_case_count": 30, "mismatched_case_count": 1},
            "architecture_live_comparison": {
                "summary": {
                    "baseline_names": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
                    "leader_names": ["dual_store_event_calendar_hybrid"],
                },
                "baseline_results": [
                    {
                        "baseline_name": "observational_temporal_memory",
                        "live_integration_overall": {"matched": 3, "total": 19, "accuracy": 3 / 19},
                    },
                    {
                        "baseline_name": "dual_store_event_calendar_hybrid",
                        "live_integration_overall": {"matched": 6, "total": 19, "accuracy": 6 / 19},
                    },
                    {
                        "baseline_name": "summary_synthesis_memory",
                        "live_integration_overall": {"matched": 5, "total": 19, "accuracy": 5 / 19},
                    },
                ],
            },
        }

        with patch(
            "spark_intelligence.memory.architecture_soak.run_telegram_memory_regression",
            side_effect=[
                SimpleNamespace(payload=payload_one),
                SimpleNamespace(payload=payload_two),
            ],
        ):
            result = run_telegram_memory_architecture_soak(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=self.home / "artifacts" / "architecture-soak",
                runs=2,
            )

        summary_path = self.home / "artifacts" / "architecture-soak" / "telegram-memory-architecture-soak.json"
        self.assertTrue(summary_path.exists())
        self.assertEqual(result.payload["summary"]["completed_runs"], 2)
        self.assertEqual(result.payload["summary"]["failed_runs"], 0)
        self.assertEqual(result.payload["summary"]["status"], "completed")
        self.assertEqual(
            result.payload["summary"]["baseline_names"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(
            set(result.payload["summary"]["recommended_top_two"]),
            {"summary_synthesis_memory", "dual_store_event_calendar_hybrid"},
        )
        self.assertEqual(
            set(item["baseline_name"] for item in result.payload["aggregate_results"][:2]),
            {"summary_synthesis_memory", "dual_store_event_calendar_hybrid"},
        )

    def test_run_telegram_memory_architecture_soak_cycles_benchmark_packs_and_isolates_namespaces(self) -> None:
        payload = {
            "summary": {"matched_case_count": 10, "mismatched_case_count": 0},
            "architecture_live_comparison": {
                "summary": {
                    "baseline_names": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
                    "leader_names": ["summary_synthesis_memory"],
                },
                "baseline_results": [
                    {
                        "baseline_name": "observational_temporal_memory",
                        "live_integration_overall": {"matched": 3, "total": 5, "accuracy": 0.6},
                        "live_by_category": [{"category": "profile_query", "matched": 3, "total": 5, "accuracy": 0.6}],
                    },
                    {
                        "baseline_name": "dual_store_event_calendar_hybrid",
                        "live_integration_overall": {"matched": 4, "total": 5, "accuracy": 0.8},
                        "live_by_category": [{"category": "profile_query", "matched": 4, "total": 5, "accuracy": 0.8}],
                    },
                    {
                        "baseline_name": "summary_synthesis_memory",
                        "live_integration_overall": {"matched": 5, "total": 5, "accuracy": 1.0},
                        "live_by_category": [{"category": "profile_query", "matched": 5, "total": 5, "accuracy": 1.0}],
                    },
                ],
            },
        }

        with patch(
            "spark_intelligence.memory.architecture_soak.run_telegram_memory_regression",
            side_effect=[
                SimpleNamespace(payload=payload),
                SimpleNamespace(payload=payload),
            ],
        ) as patched_run:
            result = run_telegram_memory_architecture_soak(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=self.home / "artifacts" / "architecture-soak-varied",
                runs=2,
                user_id="telegram-user",
                chat_id="telegram-chat",
                baseline_names=["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
            )

        first_kwargs = patched_run.call_args_list[0].kwargs
        second_kwargs = patched_run.call_args_list[1].kwargs
        self.assertNotEqual(first_kwargs["user_id"], second_kwargs["user_id"])
        self.assertNotEqual(first_kwargs["chat_id"], second_kwargs["chat_id"])
        self.assertIsNotNone(first_kwargs["cases"])
        self.assertIsNotNone(second_kwargs["cases"])
        self.assertEqual(
            first_kwargs["baseline_names"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(result.payload["summary"]["benchmark_mode"], "varied_pack_suite")
        self.assertGreater(result.payload["summary"]["benchmark_pack_count"], 1)
        self.assertIn("temporal_conflict", result.payload["summary"]["covered_focus_areas"])
        self.assertNotEqual(
            result.payload["runs"][0]["benchmark_pack"]["pack_id"],
            result.payload["runs"][1]["benchmark_pack"]["pack_id"],
        )

    def test_run_telegram_memory_architecture_soak_emits_category_and_pack_results(self) -> None:
        payload = {
            "summary": {"matched_case_count": 8, "mismatched_case_count": 0},
            "architecture_live_comparison": {
                "summary": {
                    "baseline_names": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
                    "leader_names": ["summary_synthesis_memory"],
                },
                "baseline_results": [
                    {
                        "baseline_name": "observational_temporal_memory",
                        "live_integration_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                        "live_by_category": [
                            {"category": "abstention", "matched": 0, "total": 1, "accuracy": 0.0},
                            {"category": "short_term_memory", "matched": 1, "total": 1, "accuracy": 1.0},
                        ],
                    },
                    {
                        "baseline_name": "dual_store_event_calendar_hybrid",
                        "live_integration_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                        "live_by_category": [
                            {"category": "abstention", "matched": 0, "total": 1, "accuracy": 0.0},
                            {"category": "short_term_memory", "matched": 1, "total": 1, "accuracy": 1.0},
                        ],
                    },
                    {
                        "baseline_name": "summary_synthesis_memory",
                        "live_integration_overall": {"matched": 2, "total": 2, "accuracy": 1.0},
                        "live_by_category": [
                            {"category": "abstention", "matched": 1, "total": 1, "accuracy": 1.0},
                            {"category": "short_term_memory", "matched": 1, "total": 1, "accuracy": 1.0},
                        ],
                    },
                ],
            },
        }

        with patch(
            "spark_intelligence.memory.architecture_soak.run_telegram_memory_regression",
            return_value=SimpleNamespace(payload=payload),
        ):
            result = run_telegram_memory_architecture_soak(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=self.home / "artifacts" / "architecture-soak-filtered",
                runs=1,
                case_ids=["name_query"],
            )

        self.assertEqual(result.payload["summary"]["benchmark_mode"], "fixed_selection")
        self.assertEqual(
            result.payload["summary"]["baseline_names"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(result.payload["summary"]["benchmark_pack_count"], 1)
        self.assertEqual(result.payload["benchmark_packs"][0]["pack_id"], "user_selected_slice")
        self.assertEqual(result.payload["benchmark_packs"][0]["focus_areas"], ["user_selected"])
        self.assertEqual(
            [row["category"] for row in result.payload["category_results"]],
            ["abstention", "short_term_memory"],
        )
        self.assertEqual(
            result.payload["category_results"][0]["leader_names"],
            ["summary_synthesis_memory"],
        )
        self.assertEqual(
            result.payload["benchmark_pack_results"][0]["leader_names"],
            ["summary_synthesis_memory"],
        )

    def test_run_telegram_memory_architecture_soak_treats_zero_accuracy_categories_as_unresolved(self) -> None:
        payload = {
            "summary": {"matched_case_count": 4, "mismatched_case_count": 0},
            "architecture_live_comparison": {
                "summary": {
                    "baseline_names": ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
                    "leader_names": [],
                },
                "baseline_results": [
                    {
                        "baseline_name": "observational_temporal_memory",
                        "live_integration_overall": {"matched": 0, "total": 2, "accuracy": 0.0},
                        "live_by_category": [{"category": "abstention", "matched": 0, "total": 2, "accuracy": 0.0}],
                    },
                    {
                        "baseline_name": "dual_store_event_calendar_hybrid",
                        "live_integration_overall": {"matched": 0, "total": 2, "accuracy": 0.0},
                        "live_by_category": [{"category": "abstention", "matched": 0, "total": 2, "accuracy": 0.0}],
                    },
                    {
                        "baseline_name": "summary_synthesis_memory",
                        "live_integration_overall": {"matched": 0, "total": 2, "accuracy": 0.0},
                        "live_by_category": [{"category": "abstention", "matched": 0, "total": 2, "accuracy": 0.0}],
                    },
                ],
            },
        }

        with patch(
            "spark_intelligence.memory.architecture_soak.run_telegram_memory_regression",
            return_value=SimpleNamespace(payload=payload),
        ):
            result = run_telegram_memory_architecture_soak(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=self.home / "artifacts" / "architecture-soak-zero",
                runs=1,
                case_ids=["spark_role_abstention"],
            )

        self.assertEqual(result.payload["summary"]["overall_leader_names"], [])
        self.assertEqual(result.payload["category_results"][0]["leader_names"], [])
        self.assertEqual(result.payload["benchmark_pack_results"][0]["leader_names"], [])

    def test_default_benchmark_pack_suite_grows_beyond_original_nine_packs(self) -> None:
        packs = default_telegram_memory_benchmark_packs()

        self.assertGreaterEqual(len(packs), 13)
