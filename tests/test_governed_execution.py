from __future__ import annotations

import sys
from unittest.mock import patch

from spark_intelligence.execution.governed import (
    record_governed_tool_result,
    run_governed_command,
    screen_governed_tool_text,
)
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase


class GovernedExecutionTests(SparkTestCase):
    def test_run_governed_command_captures_stdout_and_exit_code(self) -> None:
        execution = run_governed_command(
            command=[sys.executable, "-c", "print('governed-ok')"],
            cwd=self.home,
        )

        self.assertTrue(execution.ok)
        self.assertEqual(execution.exit_code, 0)
        self.assertEqual(execution.stdout.strip(), "governed-ok")

    def test_run_governed_command_forwards_timeout_seconds(self) -> None:
        with patch("spark_intelligence.execution.governed.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""

            run_governed_command(
                command=[sys.executable, "-c", "print('governed-ok')"],
                cwd=self.home,
                timeout_seconds=12.5,
            )

        self.assertEqual(run_mock.call_args.kwargs["timeout"], 12.5)

    def test_record_governed_tool_result_emits_typed_result_event(self) -> None:
        execution = run_governed_command(
            command=[sys.executable, "-c", "print('tool-ok')"],
            cwd=self.home,
        )

        record_governed_tool_result(
            self.state_db,
            execution=execution,
            component="tests",
            actor_id="test",
            summary="Governed tool execution completed.",
            reason_code="governed_test",
            source_kind="governed_test_tool",
            source_ref="tool:test",
            facts={"keepability": "ephemeral_context"},
            run_id="run-governed",
            request_id="req-governed",
            trace_ref="trace:governed",
        )

        events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(events)
        self.assertEqual(events[0]["component"], "tests")
        self.assertEqual((events[0]["provenance_json"] or {})["source_kind"], "governed_test_tool")

    def test_screen_governed_tool_text_quarantines_secret_like_output(self) -> None:
        execution = run_governed_command(
            command=[sys.executable, "-c", "print('sk-abcdefghijklmnopqrstuvwxyz123456')"],
            cwd=self.home,
        )

        screened = screen_governed_tool_text(
            state_db=self.state_db,
            execution=execution,
            text=execution.stdout,
            source_kind="governed_test_tool_output",
            source_ref="tool:test",
            summary="Governed helper blocked secret-like tool output.",
            reason_code="governed_output_secret_like",
            policy_domain="operator_output",
            blocked_stage="operator_output",
            run_id="run-governed",
            request_id="req-governed",
            trace_ref="trace:governed",
        )

        self.assertFalse(screened["allowed"])
        self.assertTrue(latest_events_by_type(self.state_db, event_type="secret_boundary_violation", limit=10))
        with self.state_db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM quarantine_records").fetchone()
        self.assertGreaterEqual(int(row["count"]), 1)
