from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from spark_intelligence.memory.architecture_live_comparison import (
    _baseline_row,
    _leader_rows,
    _required_live_match_fragments,
    build_telegram_regression_sample_specs,
    compare_telegram_memory_architectures,
)
from spark_intelligence.memory.regression import DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES

from tests.test_support import SparkTestCase


class MemoryArchitectureLiveComparisonTests(SparkTestCase):
    def test_leader_rows_uses_grounding_before_scorecard_tiebreaks(self) -> None:
        leaders = _leader_rows(
            [
                {
                    "baseline_name": "summary_synthesis_memory",
                    "live_integration_overall": {"accuracy": 0.5},
                    "trustworthiness_overall": {"accuracy": 0.5},
                    "grounding_overall": {"accuracy": 0.6},
                    "scorecard_overall": {"accuracy": 0.1},
                    "scorecard_alignment": {"rate": 0.0},
                },
                {
                    "baseline_name": "dual_store_event_calendar_hybrid",
                    "live_integration_overall": {"accuracy": 0.5},
                    "trustworthiness_overall": {"accuracy": 0.5},
                    "grounding_overall": {"accuracy": 0.4},
                    "scorecard_overall": {"accuracy": 1.0},
                    "scorecard_alignment": {"rate": 1.0},
                },
            ]
        )

        self.assertEqual([row["baseline_name"] for row in leaders], ["summary_synthesis_memory"])

    def test_leader_rows_uses_scorecard_correctness_when_live_metrics_tie(self) -> None:
        leaders = _leader_rows(
            [
                {
                    "baseline_name": "summary_synthesis_memory",
                    "live_integration_overall": {"accuracy": 0.5},
                    "trustworthiness_overall": {"accuracy": 0.5},
                    "grounding_overall": {"accuracy": 0.5},
                    "scorecard_overall": {"accuracy": 0.0},
                    "scorecard_substantive_overall": {"accuracy": 0.0},
                    "scorecard_alignment": {"rate": 1.0},
                },
                {
                    "baseline_name": "dual_store_event_calendar_hybrid",
                    "live_integration_overall": {"accuracy": 0.5},
                    "trustworthiness_overall": {"accuracy": 0.5},
                    "grounding_overall": {"accuracy": 0.5},
                    "scorecard_overall": {"accuracy": 0.25},
                    "scorecard_substantive_overall": {"accuracy": 0.25},
                    "scorecard_alignment": {"rate": 0.0},
                },
            ]
        )

        self.assertEqual([row["baseline_name"] for row in leaders], ["dual_store_event_calendar_hybrid"])

    def test_leader_rows_ignores_explanation_only_scorecard_differences_when_live_metrics_tie(self) -> None:
        leaders = _leader_rows(
            [
                {
                    "baseline_name": "summary_synthesis_memory",
                    "live_integration_overall": {"accuracy": 0.8},
                    "trustworthiness_overall": {"accuracy": 0.8},
                    "grounding_overall": {"accuracy": 0.8},
                    "scorecard_overall": {"accuracy": 0.0},
                    "scorecard_substantive_overall": {"accuracy": 0.0},
                    "scorecard_alignment": {"rate": 0.0},
                },
                {
                    "baseline_name": "dual_store_event_calendar_hybrid",
                    "live_integration_overall": {"accuracy": 0.8},
                    "trustworthiness_overall": {"accuracy": 0.8},
                    "grounding_overall": {"accuracy": 0.8},
                    "scorecard_overall": {"accuracy": 0.4},
                    "scorecard_substantive_overall": {"accuracy": 0.0},
                    "scorecard_alignment": {"rate": 0.0},
                },
            ]
        )

        self.assertEqual(
            [row["baseline_name"] for row in leaders],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )

    def test_baseline_row_treats_unknown_as_truthful_abstention(self) -> None:
        sample_specs = [
            {
                "sample_id": "favorite_color_missing_after_loaded_context",
                "questions": [
                    {
                        "question_id": "favorite_color_missing_after_loaded_context",
                        "question": "What is my favorite color?",
                        "category": "inappropriate_memory_use",
                        "should_abstain": True,
                        "metadata": {
                            "expected_fragments": ["don't currently have that saved"],
                            "expected_forbidden_fragments": ["Sarah"],
                        },
                    }
                ],
            }
        ]
        scorecard = {
            "predictions": [
                {
                    "question_id": "favorite_color_missing_after_loaded_context",
                    "predicted_answer": "unknown",
                    "metadata": {"retrieved_memory_roles": ["aggregate"]},
                    "is_correct": True,
                }
            ]
        }

        row = _baseline_row(
            baseline_name="summary_synthesis_memory",
            scorecard=scorecard,
            sample_specs=sample_specs,
        )

        self.assertEqual(row["live_integration_overall"]["matched"], 1)
        self.assertEqual(row["abstention_overall"]["matched"], 1)
        self.assertEqual(row["forbidden_memory_overall"]["clean"], 1)

    def test_baseline_row_ignores_runtime_only_explanation_phrase_when_fact_is_present(self) -> None:
        sample_specs = [
            {
                "sample_id": "city_explanation",
                "questions": [
                    {
                        "question_id": "city_explanation",
                        "question": "How do you know where I live?",
                        "category": "explanation",
                        "evidence_session_ids": ["shared:city_write"],
                        "metadata": {
                            "expected_fragments": ["saved memory record", "Dubai"],
                            "expected_forbidden_fragments": [],
                        },
                    }
                ],
            }
        ]
        scorecard = {
            "predictions": [
                {
                    "question_id": "city_explanation",
                    "predicted_answer": "Dubai",
                    "metadata": {"retrieved_memory_roles": ["structured_evidence"]},
                    "is_correct": True,
                }
            ]
        }

        row = _baseline_row(
            baseline_name="summary_synthesis_memory",
            scorecard=scorecard,
            sample_specs=sample_specs,
        )

        self.assertEqual(row["live_integration_overall"]["matched"], 1)
        self.assertEqual(row["grounding_overall"]["matched"], 1)
        self.assertEqual(row["scorecard_substantive_overall"]["total"], 0)

    def test_required_live_match_fragments_for_previous_state_questions_only_requires_historical_value(self) -> None:
        self.assertEqual(
            _required_live_match_fragments(
                category="event_history",
                question_text="Where did I live before?",
                expected_fragments=["Dubai", "Abu Dhabi"],
            ),
            ["Dubai"],
        )
        self.assertEqual(
            _required_live_match_fragments(
                category="event_history",
                question_text="What was my previous country?",
                expected_fragments=["UAE", "Canada"],
            ),
            ["UAE"],
        )

    def test_required_live_match_fragments_keeps_full_history_requirements_for_event_listing_prompts(self) -> None:
        self.assertEqual(
            _required_live_match_fragments(
                category="event_history",
                question_text="What memory events do you have about where I live?",
                expected_fragments=["Dubai", "Abu Dhabi"],
            ),
            ["Dubai", "Abu Dhabi"],
        )

    def test_required_live_match_fragments_strips_explanation_style_only_fragment_for_staleness_explanations(self) -> None:
        self.assertEqual(
            _required_live_match_fragments(
                category="staleness",
                question_text="How do you know my startup?",
                expected_fragments=["saved memory record", "Seedify"],
            ),
            ["Seedify"],
        )

    def test_baseline_row_excludes_explanation_like_questions_from_substantive_scorecard_accuracy(self) -> None:
        sample_specs = [
            {
                "sample_id": "startup_explanation_after_founder",
                "questions": [
                    {
                        "question_id": "startup_explanation_after_founder",
                        "question": "How do you know my startup?",
                        "category": "staleness",
                        "metadata": {
                            "expected_fragments": ["saved memory record", "Seedify"],
                            "expected_forbidden_fragments": [],
                        },
                    }
                ],
            },
            {
                "sample_id": "founder_query",
                "questions": [
                    {
                        "question_id": "founder_query",
                        "question": "What company did I found?",
                        "category": "profile_query",
                        "metadata": {
                            "expected_fragments": ["Spark Swarm"],
                            "expected_forbidden_fragments": [],
                        },
                    }
                ],
            },
        ]
        scorecard = {
            "predictions": [
                {
                    "question_id": "startup_explanation_after_founder",
                    "predicted_answer": "My startup is Seedify",
                    "metadata": {"retrieved_memory_roles": ["structured_evidence"]},
                    "is_correct": False,
                },
                {
                    "question_id": "founder_query",
                    "predicted_answer": "You founded Spark Swarm.",
                    "metadata": {"retrieved_memory_roles": ["structured_evidence"]},
                    "is_correct": True,
                },
            ],
            "overall": {"accuracy": 0.5},
        }

        row = _baseline_row(
            baseline_name="summary_synthesis_memory",
            scorecard=scorecard,
            sample_specs=sample_specs,
        )

        self.assertEqual(row["scorecard_overall"]["accuracy"], 0.5)
        self.assertEqual(row["scorecard_substantive_overall"]["correct"], 1)
        self.assertEqual(row["scorecard_substantive_overall"]["total"], 1)
        self.assertEqual(row["scorecard_substantive_overall"]["accuracy"], 1.0)

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

    def test_build_telegram_regression_sample_specs_sets_explanation_source_alignment_expectation(self) -> None:
        selected_cases = [
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "city_write"),
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "city_explanation"),
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_query"),
        ]
        case_payloads = [
            {
                "case_id": "city_write",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact_update",
                "routing_decision": "memory_profile_fact_observation",
                "response_text": "I'll remember you live in Dubai.",
                "matched_expectations": True,
            },
            {
                "case_id": "city_explanation",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact_explanation",
                "routing_decision": "memory_profile_fact_explanation",
                "response_text": "I know that from a saved memory record about Dubai.",
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

        sample_specs = build_telegram_regression_sample_specs(
            case_payloads=case_payloads,
            selected_cases=selected_cases,
        )

        explanation_question = sample_specs[0]["questions"][0]
        query_question = sample_specs[1]["questions"][0]
        self.assertEqual(explanation_question["question_id"], "city_explanation")
        self.assertEqual(explanation_question["metadata"]["expected_answer_candidate_source"], "evidence_memory")
        self.assertEqual(query_question["question_id"], "name_query")
        self.assertIsNone(query_question["metadata"]["expected_answer_candidate_source"])

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
                "predictions": [
                    {
                        "case_id": "name_query",
                        "matched_expectations": False,
                    }
                ],
            },
            {
                "baseline_name": "dual_store_event_calendar_hybrid",
                "live_integration_overall": {"matched": 0, "total": 1, "accuracy": 0.0},
                "live_by_category": [],
                "scorecard_alignment": {"rate": 0.2},
                "predictions": [
                    {
                        "case_id": "name_query",
                        "matched_expectations": False,
                    }
                ],
            },
            {
                "baseline_name": "summary_synthesis_memory",
                "live_integration_overall": {"matched": 1, "total": 1, "accuracy": 1.0},
                "live_by_category": [{"category": "profile_query", "matched": 1, "total": 1, "accuracy": 1.0}],
                "scorecard_alignment": {"rate": 0.9},
                "predictions": [
                    {
                        "case_id": "name_query",
                        "matched_expectations": True,
                    }
                ],
            },
        ]

        with patch(
            "spark_intelligence.memory.architecture_live_comparison._run_live_comparison_scorecards",
            return_value=(
                baseline_rows,
                {
                    "runtime_class": "SparkMemorySDK",
                    "runtime_memory_architecture": "dual_store_event_calendar_hybrid",
                },
            ),
        ):
            result = compare_telegram_memory_architectures(
                config_manager=self.config_manager,
                case_payloads=case_payloads,
                selected_cases=selected_cases,
                output_dir=output_dir,
            )

        self.assertEqual(
            result.payload["summary"]["baseline_names"],
            ["summary_synthesis_memory", "dual_store_event_calendar_hybrid"],
        )
        self.assertEqual(result.payload["summary"]["leader_names"], ["summary_synthesis_memory"])
        self.assertEqual(
            result.payload["summary"]["recommended_runtime_architecture"],
            "summary_synthesis_memory",
        )
        self.assertEqual(
            result.payload["summary"]["current_runtime_memory_architecture"],
            "dual_store_event_calendar_hybrid",
        )
        self.assertFalse(result.payload["summary"]["runtime_matches_live_leader"])
        self.assertEqual(result.payload["summary"]["shared_failed_case_ids"], [])
        self.assertEqual(result.payload["summary"]["separating_case_ids"], ["name_query"])
        self.assertEqual(
            result.payload["diagnostics"]["baseline_case_diagnostics"],
            [
                {
                    "baseline_name": "dual_store_event_calendar_hybrid",
                    "matched_case_ids": [],
                    "missed_case_ids": ["name_query"],
                },
                {
                    "baseline_name": "summary_synthesis_memory",
                    "matched_case_ids": ["name_query"],
                    "missed_case_ids": [],
                },
            ],
        )
        self.assertTrue(Path(result.payload["artifact_paths"]["summary_json"]).exists())
        self.assertTrue(Path(result.payload["artifact_paths"]["summary_markdown"]).exists())
        self.assertIn(
            "Separating cases: `name_query`",
            Path(result.payload["artifact_paths"]["summary_markdown"]).read_text(encoding="utf-8"),
        )

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
            return_value=(
                baseline_rows,
                {
                    "runtime_class": "SparkMemorySDK",
                    "runtime_memory_architecture": "dual_store_event_calendar_hybrid",
                },
            ),
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
            return_value=(
                baseline_rows,
                {
                    "runtime_class": "SparkMemorySDK",
                    "runtime_memory_architecture": "dual_store_event_calendar_hybrid",
                },
            ),
        ):
            result = compare_telegram_memory_architectures(
                config_manager=self.config_manager,
                case_payloads=case_payloads,
                selected_cases=selected_cases,
                output_dir=output_dir,
            )

        self.assertEqual(result.payload["summary"]["leader_names"], [])
        self.assertIsNone(result.payload["summary"]["recommended_runtime_architecture"])

    def test_compare_telegram_memory_architectures_forwards_explicit_baseline_selection(self) -> None:
        selected_cases = [
            next(case for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES if case.case_id == "name_query"),
        ]
        case_payloads = [
            {
                "case_id": "name_query",
                "decision": "allowed",
                "bridge_mode": "memory_profile_fact",
                "routing_decision": "memory_profile_fact_query",
                "response_text": "Your name is Sarah.",
                "matched_expectations": True,
            },
        ]

        with patch(
            "spark_intelligence.memory.architecture_live_comparison._run_live_comparison_scorecards",
            return_value=(
                [],
                {
                    "runtime_class": "SparkMemorySDK",
                    "runtime_memory_architecture": "dual_store_event_calendar_hybrid",
                },
            ),
        ) as run_scorecards:
            compare_telegram_memory_architectures(
                config_manager=self.config_manager,
                case_payloads=case_payloads,
                selected_cases=selected_cases,
                output_dir=self.home / "artifacts" / "architecture-live-comparison-selected",
                baseline_names=["dual_store_event_calendar_hybrid"],
            )

        self.assertEqual(
            list(run_scorecards.call_args.kwargs["baseline_names"]),
            ["dual_store_event_calendar_hybrid"],
        )
