from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram import runtime as telegram_runtime
from spark_intelligence.browser import service as browser_service
from spark_intelligence.execution.governed import GovernedCommandExecution, record_governed_tool_result
from spark_intelligence.memory import sdk_maintenance
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase


class SubprocessStderrBoundaryTests(SparkTestCase):
    @staticmethod
    def _execution(*, exit_code: int, stdout: str = "", stderr: str = "") -> GovernedCommandExecution:
        return GovernedCommandExecution(
            command=["tool"],
            cwd="/private/operator/workspace",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    def test_browser_failure_discards_stderr_and_parsed_stdout_payload(self) -> None:
        secret = "API_KEY=super-secret-value /private/operator/workspace"
        execution = self._execution(
            exit_code=7,
            stdout=json.dumps({"success": False, "internal_token": secret}),
            stderr=secret,
        )

        with patch.object(browser_service, "run_governed_command", return_value=execution):
            result = browser_service._run_browser_use_json(["browser-use", "state"], timeout=1)

        self.assertEqual(
            result,
            {
                "success": False,
                "error": "browser-use exited with code 7.",
                "error_code": "BROWSER_USE_COMMAND_FAILED",
                "exit_code": 7,
            },
        )
        self.assertNotIn(secret, json.dumps(result))

    def test_browser_launch_exception_is_generic(self) -> None:
        secret = "cannot execute /private/operator/bin/browser-use with TOKEN=super-secret-value"
        with patch.object(browser_service, "run_governed_command", side_effect=OSError(secret)):
            result = browser_service._run_browser_use_json(["browser-use", "state"], timeout=1)

        self.assertEqual(
            result,
            {
                "success": False,
                "error": "browser-use could not be started.",
                "error_code": "BROWSER_USE_UNAVAILABLE",
            },
        )
        self.assertNotIn(secret, json.dumps(result))

    def test_browser_doctor_status_keeps_only_exit_evidence(self) -> None:
        secret = "PASSWORD=super-secret-value redis://internal.example:6379/0"
        execution = self._execution(exit_code=9, stdout=secret, stderr=secret)
        status_path = self.home / "browser-use-status.json"

        with patch.object(browser_service, "run_governed_command", return_value=execution):
            status = browser_service._refresh_browser_use_status_from_cli(
                status_path=status_path,
                cli_path="browser-use",
            )

        self.assertEqual(status["last_failure_reason"], "browser-use doctor exited with code 9.")
        self.assertEqual(status["error_code"], "BROWSER_USE_DOCTOR_FAILED")
        self.assertNotIn(secret, json.dumps(status))
        self.assertNotIn(secret, status_path.read_text(encoding="utf-8"))

    def test_browser_doctor_launch_exception_is_generic(self) -> None:
        secret = "permission denied at /private/operator/bin/browser-use"
        status_path = self.home / "browser-use-status.json"

        with patch.object(browser_service, "run_governed_command", side_effect=OSError(secret)):
            status = browser_service._refresh_browser_use_status_from_cli(
                status_path=status_path,
                cli_path="browser-use",
            )

        self.assertEqual(status["last_failure_reason"], "browser-use doctor could not be started.")
        self.assertNotIn(secret, json.dumps(status))

    def test_swarm_failure_is_one_conversational_reply_without_raw_output(self) -> None:
        secret = "TOKEN=super-secret-value /private/operator/swarm.log"
        result = SimpleNamespace(exit_code=2, stdout=secret, stderr=secret)

        reply = telegram_runtime._render_swarm_bridge_failure("rerun request", result)

        self.assertNotIn(secret, reply)
        self.assertNotIn("Exit code:", reply)
        self.assertNotIn("server logs", reply.lower())
        self.assertLessEqual(len(reply.splitlines()), 2)
        self.assertIn("2", reply)

    def test_memory_nonzero_json_payload_becomes_fixed_failure_contract(self) -> None:
        secret = "API_KEY=super-secret-value /private/operator/memory"
        execution = self._execution(
            exit_code=3,
            stdout=json.dumps(
                {
                    "valid": False,
                    "errors": [secret],
                    "stderr": secret,
                    "private_detail": secret,
                }
            ),
            stderr=secret,
        )

        with patch.object(sdk_maintenance, "run_governed_command", return_value=execution):
            result = sdk_maintenance._run_domain_chip_memory_cli(
                "run-sdk-maintenance-report",
                "replay.json",
                validator_root=self.home,
            )

        self.assertEqual(
            result,
            {
                "valid": False,
                "errors": ["sdk_maintenance_report_failed"],
                "warnings": [],
            },
        )
        self.assertNotIn(secret, json.dumps(result))

    def test_memory_success_payload_does_not_gain_stderr(self) -> None:
        secret = "SECRET_KEY=super-secret-value /private/operator/memory"
        execution = self._execution(
            exit_code=0,
            stdout=json.dumps({"valid": True, "errors": [], "warnings": ["safe"], "count": 2}),
            stderr=secret,
        )

        with patch.object(sdk_maintenance, "run_governed_command", return_value=execution):
            result = sdk_maintenance._run_domain_chip_memory_cli(
                "run-sdk-maintenance-report",
                "replay.json",
                validator_root=self.home,
            )

        self.assertEqual(result, {"valid": True, "errors": [], "warnings": ["safe"], "count": 2})
        self.assertNotIn("stderr", result)
        self.assertNotIn(secret, json.dumps(result))

    def test_memory_malformed_failure_discards_stdout_and_stderr(self) -> None:
        secret = "TOKEN=super-secret-value /private/operator/memory"
        execution = self._execution(exit_code=4, stdout=secret, stderr=secret)

        with patch.object(sdk_maintenance, "run_governed_command", return_value=execution):
            result = sdk_maintenance._run_domain_chip_memory_cli(
                "run-sdk-maintenance-report",
                "replay.json",
                validator_root=self.home,
            )

        self.assertEqual(result["errors"], ["sdk_maintenance_report_failed"])
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)
        self.assertNotIn(secret, json.dumps(result))

    def test_governed_event_records_presence_not_raw_or_caller_injected_stderr(self) -> None:
        secret = "PASSWORD=super-secret-value /private/operator/tool"
        execution = self._execution(exit_code=5, stderr=secret)

        record_governed_tool_result(
            self.state_db,
            execution=execution,
            component="test",
            actor_id="test",
            summary="Governed tool failed.",
            reason_code="governed_failure",
            source_kind="test_tool",
            source_ref="tool:test",
            facts={
                "stderr": secret,
                "stdout": secret,
                "exit_code": 999,
                "keepability": "ephemeral_context",
            },
        )

        event = latest_events_by_type(self.state_db, event_type="dispatch_failed", limit=1)[0]
        facts = event["facts_json"]
        self.assertNotIn("stderr", facts)
        self.assertNotIn("stdout", facts)
        self.assertNotIn(secret, json.dumps(facts))
        self.assertTrue(facts["stderr_present"])
        self.assertEqual(facts["exit_code"], 5)
        self.assertEqual(facts["keepability"], "ephemeral_context")
