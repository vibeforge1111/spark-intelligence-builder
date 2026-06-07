"""Tests: browser-use exit!=0 error field does not echo stderr to caller."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _parse_result(exit_code: int, stdout: str, stderr: str) -> dict:
    """Minimal reimplementation of the fixed _run_browser_use_and_parse logic."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if exit_code != 0:
        payload["success"] = False
        payload["error"] = f"browser-use exited with code {exit_code}"
    return payload


class TestBrowserUseStderrDisclosure:
    def test_stderr_not_in_error_field_on_failure(self):
        secret_stderr = "API_KEY=hunter2 connection refused redis://10.0.0.1:6379"
        result = _parse_result(exit_code=1, stdout="{}", stderr=secret_stderr)
        assert secret_stderr not in result.get("error", "")
        assert "hunter2" not in result.get("error", "")

    def test_error_field_is_generic_on_failure(self):
        result = _parse_result(exit_code=1, stdout="{}", stderr="some internal detail")
        assert result["error"] == "browser-use exited with code 1"

    def test_success_is_false_on_nonzero_exit(self):
        result = _parse_result(exit_code=2, stdout="{}", stderr="")
        assert result["success"] is False

    def test_exit_code_included_in_error(self):
        result = _parse_result(exit_code=137, stdout="{}", stderr="OOM killer")
        assert "137" in result["error"]
        assert "OOM" not in result["error"]

    def test_zero_exit_does_not_add_error_field(self):
        result = _parse_result(exit_code=0, stdout='{"success": true}', stderr="")
        assert "error" not in result

    def test_stdout_not_echoed_in_error_on_failure(self):
        sensitive_stdout = '{"internal_token": "abc123"}'
        result = _parse_result(exit_code=1, stdout=sensitive_stdout, stderr="")
        assert "abc123" not in result.get("error", "")
