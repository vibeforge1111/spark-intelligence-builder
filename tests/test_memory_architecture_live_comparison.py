from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from spark_intelligence.memory.architecture_live_comparison import (
    build_telegram_regression_sample_specs,
    compare_telegram_memory_architectures,
)
from spark_intelligence.memory.regression import DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES

from tests.test_support import SparkTestCase


class MemoryArchitectureLiveComparisonTests(SparkTestCase):
    def test_build_telegram_regression_sample_specs_uses_prior_context_and_isolated_abstention(self) -> None:
        selected_cases = [
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_write"),
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_query"),
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "spark_role_abstention"),
        ]
        case_payloads = [
            {
                "case_id": "name_write",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact_update",
                "routing_decision": "memory_profile_fact_observation",
                "response_text": "Saved Sarah.",
                "matched_expectations": True,
            },
            {
                "case_id": "name_query",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "Your name is Sarah.",
                "matched_expectations": True,
            },
            {
                "case_id": "spark_role_abstention",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "I don't currently have that saved.",
                "matched_expectations": True,
            },
        ]

        sample_specs = build_telegram_regression_sample_specs(
            case_payloads=case_payloads,
            selected_cases=selected_cases,
        )

        self.assertEqual([item["sample_id"] for item in sample_specs], ["name_query", "spark_role_abstention"])
        first_question = sample_specs[0]["questions"][0]
        self.assertEqual(len(sample_specs[0]["sessions"]), 1)
        self.assertEqual(sample_specs[0]["sessions"][0]["session_id"], "shared:name_write")
        self.assertEqual(first_question["evidence_session_ids"], ["shared:name_write"])
        self.assertIn("name_write:user", first_question["evidence_turn_ids"])

        abstention_question = sample_specs[1]["questions"][0]
        self.assertEqual(sample_specs[1]["sessions"], [])
        self.assertTrue(abstention_question["should_abstain"])
        self.assertEqual(abstention_question["evidence_session_ids"], [])
        self.assertEqual(abstention_question["metadata"]["expected_forbidden_fragments"], [])

    def test_compare_telegram_memory_architectures_writes_summary_and_picks_leader(self) -> None:
        selected_cases = [
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_write"),
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_query"),
        ]
        case_payloads = [
            {
                "case_id": "name_write",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact_update",
                "routing_decision": "memory_profile_fact_observation",
                "response_text": "Saved Sarah.",
                "matched_expectations": True,
            },
            {
                "case_id": "name_query",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "Your name is Sarah.",
                "matched_expectations": True,
            },
        ]
        output_dir = self.home / "artifacts" / "architecture-live-comparison"
        baseline_rows = [
            {
                "baseline_name": "observational_temporal_memory",
                "live_integration_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.1},
            },
            {
                "baseline_name": "dual_store_event_calendar_hybrid",
                "live_integration_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.2},
            },
            {
                "baseline_name": "summary_synthesis_memory",
                "live_integration_overall": {"matched": 1, "total": 1, "accuracy": 1.0},
                "live_by_category": [{"category": "profile_query", "matched": 1, "total": 1, "accuracy": 1.0}],
                "scorecard_alignment": {"rate": 0.9},
            },
        ]

        with patch(
            "spark_intelligence.memory.architecture_live_comparison._run_live_comparison_scorecards",
            return_value=(baseline_rows, "SparkMemorySDK"),
        ):
            result = compare_telegram_memory_architectures(
                config_manager=self.config_manager,
                case_payloads=case_payloads,
                selected_cases=selected_cases,
                output_dir=output_dir,
            )

        self.assertEqual(result.payload["summary"]["leader_names"], ["summary_synthesis_memory"])
        self.assertEqual(
            result.payload["summary"]["recommended_runtime_architecture"],
            "summary_synthesis_memory",
        )
        self.assertFalse(result.payload["summary"]["runtime_matches_live_leader"])
        self.assertTrue(Path(result.payload["artifact_paths"]["summary_json"]).exists())
        self.assertTrue(Path(result.payload["artifact_paths"]["summary_markdown"]).exists())

    def test_compare_row_tracks_forbidden_memory_and_grounding_metrics(self) -> None:
        selected_cases = [
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_write"),
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_query"),
        ]
        abstention_like_case = next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "country_query")
        from dataclasses import replace

        selected_cases.append(
            replace(
                abstention_like_case,
                case_id="favorite_color_missing",
                category="inappropriate_memory_use",
                message="What is my favorite color?",
                expected_response_contains=("don't currently have that saved",),
                expected_response_excludes=("Sarah",),
                benchmark_tags=("anti_hallucination",),
            )
        )
        case_payloads = [
            {
                "case_id": "name_write",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact_update",
                "routing_decision": "memory_profile_fact_observation",
                "response_text": "Saved Sarah.",
                "matched_expectations": True,
            },
            {
                "case_id": "name_query",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "Your name is Sarah.",
                "matched_expectations": True,
            },
            {
                "case_id": "favorite_color_missing",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "I don't currently have that saved.",
                "matched_expectations": True,
            },
        ]
        output_dir = self.home / "artifacts" / "architecture-live-comparison-trust"
        baseline_rows = [
            {
                "baseline_name": "observational_temporal_memory",
                "live_integration_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                "trustworthiness_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                "grounding_overall": {"matched": 1, "total": 1, "accuracy": 1.0},
                "abstention_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "forbidden_memory_overall": {"clean": 0, "total": 1, "accuracy": 0.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.1},
            },
            {
                "baseline_name": "dual_store_event_calendar_hybrid",
                "live_integration_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                "trustworthiness_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                "grounding_overall": {"matched": 1, "total": 1, "accuracy": 1.0},
                "abstention_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "forbidden_memory_overall": {"clean": 0, "total": 1, "accuracy": 0.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.2},
            },
            {
                "baseline_name": "summary_synthesis_memory",
                "live_integration_overall": {"matched": 1, "total": 2, "accuracy": 0.5},
                "trustworthiness_overall": {"matched": 2, "total": 2, "accuracy": 1.0},
                "grounding_overall": {"matched": 1, "total": 1, "accuracy": 1.0},
                "abstention_overall": {"matched": 1, "total": 1, "accuracy": 1.0},
                "forbidden_memory_overall": {"clean": 1, "total": 1, "accuracy": 1.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.1},
            },
        ]

        with patch(
            "spark_intelligence.memory.architecture_live_comparison._run_live_comparison_scorecards",
            return_value=(baseline_rows, "SparkMemorySDK"),
        ):
            result = compare_telegram_memory_architectures(
                config_manager=self.config_manager,
                case_payloads=case_payloads,
                selected_cases=selected_cases,
                output_dir=output_dir,
            )

        self.assertEqual(result.payload["summary"]["leader_names"], ["summary_synthesis_memory"])
        self.assertIn("anti_hallucination", result.payload["cases"][1]["benchmark_tags"])

    def test_compare_telegram_memory_architectures_does_not_report_zero_signal_tie_as_leader(self) -> None:
        selected_cases = [
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "spark_role_abstention"),
        ]
        case_payloads = [
            {
                "case_id": "spark_role_abstention",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "I don't currently have that saved.",
                "matched_expectations": True,
            },
        ]
        output_dir = self.home / "artifacts" / "architecture-live-comparison-zero-signal"
        baseline_rows = [
            {
                "baseline_name": "observational_temporal_memory",
                "live_integration_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "trustworthiness_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "grounding_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "abstention_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "forbidden_memory_overall": {"clean": 1, "total": 1, "accuracy": 1.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.0},
            },
            {
                "baseline_name": "dual_store_event_calendar_hybrid",
                "live_integration_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "trustworthiness_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "grounding_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "abstention_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "forbidden_memory_overall": {"clean": 1, "total": 1, "accuracy": 1.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.0},
            },
            {
                "baseline_name": "summary_synthesis_memory",
                "live_integration_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "trustworthiness_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "grounding_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "abstention_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "forbidden_memory_overall": {"clean": 1, "total": 1, "accuracy": 1.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.0},
            },
        ]

        with patch(
            "spark_intelligence.memory.architecture_live_comparison._run_live_comparison_scorecards",
            return_value=(baseline_rows, "SparkMemorySDK"),
        ):
            result = compare_telegram_memory_architectures(
                config_manager=self.config_manager,
                case_payloads=case_payloads,
                selected_cases=selected_cases,
                output_dir=output_dir,
            )

        self.assertEqual(result.payload["summary"]["leader_names"], [])
        self.assertIsNone(result.payload["summary"]["recommended_runtime_architecture"])
