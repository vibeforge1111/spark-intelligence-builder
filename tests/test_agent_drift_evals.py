from __future__ import annotations

from spark_intelligence.self_awareness.drift_evals import (
    AGENT_DRIFT_EVAL_SCHEMA_VERSION,
    default_agent_drift_eval_cases,
    run_agent_drift_eval_suite,
)

from tests.test_support import SparkTestCase


class AgentDriftEvalTests(SparkTestCase):
    def test_default_drift_eval_suite_covers_rec_named_failures(self) -> None:
        report = run_agent_drift_eval_suite(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )

        payload = report.to_payload()

        self.assertEqual(payload["schema_version"], AGENT_DRIFT_EVAL_SCHEMA_VERSION)
        self.assertEqual(payload["counts"]["failed"], 0)
        self.assertGreaterEqual(payload["counts"]["total"], 6)
        case_ids = {result["case_id"] for result in payload["results"]}
        self.assertIn("concept_question_does_not_run_diagnostics", case_ids)
        self.assertIn("old_access_memory_goes_stale", case_ids)
        self.assertIn("level_four_read_only_routes_to_writable_mission", case_ids)
        self.assertIn("option_two_resolves_latest_list", case_ids)

    def test_eval_report_text_names_failed_checks(self) -> None:
        report = run_agent_drift_eval_suite(
            config_manager=self.config_manager,
            state_db=self.state_db,
            cases=[
                default_agent_drift_eval_cases()[0],
                default_agent_drift_eval_cases()[0].__class__(
                    case_id="intentional_failure",
                    user_message="option 2",
                    active_reference_items=("one", "two"),
                    expected_selected_item="three",
                ),
            ],
        )

        text = report.to_text()

        self.assertIn("Agent drift evals: 1/2 passed", text)
        self.assertIn("FAIL intentional_failure", text)
        self.assertIn("selected_item", text)
