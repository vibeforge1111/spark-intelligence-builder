from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

from spark_intelligence.execution.governed import (
    DEFAULT_GOVERNED_COMMAND_TIMEOUT_SECONDS,
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

    def test_run_governed_command_uses_finite_default_timeout(self) -> None:
        with patch("spark_intelligence.execution.governed.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""

            execution = run_governed_command(
                command=[sys.executable, "-c", "print('governed-ok')"],
                cwd=self.home,
            )

        self.assertEqual(
            run_mock.call_args.kwargs["timeout"],
            DEFAULT_GOVERNED_COMMAND_TIMEOUT_SECONDS,
        )
        self.assertEqual(execution.timeout_seconds, DEFAULT_GOVERNED_COMMAND_TIMEOUT_SECONDS)
        self.assertFalse(execution.timed_out)

    def test_run_governed_command_returns_safe_typed_timeout(self) -> None:
        raw_command = [sys.executable, "-c", "print('secret-token')"]
        with patch(
            "spark_intelligence.execution.governed.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=raw_command,
                timeout=7.5,
                output="partial-sensitive-output",
                stderr="partial-sensitive-error",
            ),
        ):
            execution = run_governed_command(
                command=raw_command,
                cwd=self.home,
                timeout_seconds=7.5,
            )

        self.assertFalse(execution.ok)
        self.assertTrue(execution.timed_out)
        self.assertEqual(execution.exit_code, 124)
        self.assertEqual(execution.timeout_seconds, 7.5)
        self.assertEqual(execution.stdout, "")
        self.assertNotIn("secret-token", " ".join(execution.command))
        self.assertNotIn("partial-sensitive", execution.stderr)
        self.assertNotIn("secret-token", execution.stderr)
        self.assertEqual(execution.stderr, "Governed command timed out after 7.5 seconds.")

    def test_run_governed_command_returns_safe_missing_command_result(self) -> None:
        raw_command = ["missing-secret-bearing-command"]
        with patch(
            "spark_intelligence.execution.governed.subprocess.run",
            side_effect=FileNotFoundError("secret launch path"),
        ):
            execution = run_governed_command(command=raw_command, cwd=self.home)

        self.assertEqual(execution.exit_code, 127)
        self.assertEqual(execution.command, ["<redacted:launch_failed>"])
        self.assertEqual(execution.stderr, "Governed command was not found.")
        self.assertNotIn("secret", execution.stderr)

    def test_run_governed_command_returns_safe_os_error_result(self) -> None:
        with patch(
            "spark_intelligence.execution.governed.subprocess.run",
            side_effect=OSError("secret executable detail"),
        ):
            execution = run_governed_command(
                command=["private-command"],
                cwd=self.home,
            )

        self.assertEqual(execution.exit_code, 126)
        self.assertEqual(execution.command, ["<redacted:launch_failed>"])
        self.assertEqual(execution.stderr, "Governed command could not be started.")
        self.assertNotIn("secret", execution.stderr)

    def test_run_governed_command_forwards_encoding_and_errors(self) -> None:
        with patch("spark_intelligence.execution.governed.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""

            run_governed_command(
                command=[sys.executable, "-c", "print('governed-ok')"],
                cwd=self.home,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(run_mock.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")

    def test_run_governed_command_forwards_stdin_input_text(self) -> None:
        with patch("spark_intelligence.execution.governed.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""

            run_governed_command(
                command=[sys.executable, "-c", "print(input())"],
                cwd=self.home,
                input_text="brief prompt",
            )

        self.assertEqual(run_mock.call_args.kwargs["input"], "brief prompt")

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
            command=[sys.executable, "-c", "print('sk-' + 'abcdefghijklmnopqrstuvwxyz123456')"],
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
