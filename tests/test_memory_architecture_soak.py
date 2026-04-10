from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory.architecture_soak import run_telegram_memory_architecture_soak

from tests.test_support import SparkTestCase


class MemoryArchitectureSoakTests(SparkTestCase):
    def test_run_telegram_memory_architecture_soak_aggregates_runs(self) -> None:
        payload_one = {
            "summary": {"matched_case_count": 29, "mismatched_case_count": 2},
            "architecture_live_comparison": {
                "summary": {"leader_names": ["summary_synthesis_memory"]},
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
                "summary": {"leader_names": ["dual_store_event_calendar_hybrid"]},
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
            set(result.payload["summary"]["recommended_top_two"]),
            {"summary_synthesis_memory", "dual_store_event_calendar_hybrid"},
        )
        self.assertEqual(
            set(item["baseline_name"] for item in result.payload["aggregate_results"][:2]),
            {"summary_synthesis_memory", "dual_store_event_calendar_hybrid"},
        )
