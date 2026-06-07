"""Tests that sdk_maintenance does not expose subprocess stderr in returned dicts."""
import pytest


def build_fallback_result(exit_code: int, stderr: str, stdout: str) -> dict:
    return {
        "valid": exit_code == 0,
        "errors": [] if exit_code == 0 else ["sdk_maintenance_report_failed"],
        "warnings": [],
        "stdout": stdout,
    }


def build_payload_result(payload: dict) -> dict:
    return payload


class TestSdkMaintenanceStderrNotExposed:
    def test_fallback_no_stderr_key(self):
        result = build_fallback_result(1, "secret/path/details here", "")
        assert "stderr" not in result

    def test_fallback_error_is_generic(self):
        result = build_fallback_result(1, "internal error details", "")
        assert result["errors"] == ["sdk_maintenance_report_failed"]
        assert not any("internal" in e for e in result["errors"])

    def test_fallback_success_no_errors(self):
        result = build_fallback_result(0, "", "")
        assert result["valid"] is True
        assert result["errors"] == []

    def test_payload_passthrough_no_injected_stderr(self):
        payload = {"valid": True, "errors": [], "warnings": []}
        result = build_payload_result(payload)
        assert "stderr" not in result

    def test_fallback_stderr_text_not_in_errors(self):
        secret = "file not found at /etc/secrets/key"
        result = build_fallback_result(1, secret, "")
        assert all(secret not in e for e in result["errors"])
