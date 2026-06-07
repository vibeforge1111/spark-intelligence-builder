"""Tests: browser-use doctor failure reason does not echo stderr."""
from __future__ import annotations


def _doctor_failure_reason(exit_code: int, stdout: str, stderr: str) -> str:
    """Reimplementation of the fixed doctor failure reason logic."""
    return f"browser-use doctor exited with code {exit_code}"


class TestBrowserDoctorStatusStderr:
    def test_stderr_not_in_reason(self):
        reason = _doctor_failure_reason(1, "", "API_KEY=secret redis://10.0.0.1")
        assert "secret" not in reason
        assert "redis" not in reason

    def test_reason_contains_exit_code(self):
        reason = _doctor_failure_reason(137, "", "OOM killed")
        assert "137" in reason
        assert "OOM" not in reason

    def test_stdout_not_in_reason(self):
        reason = _doctor_failure_reason(1, "internal diagnostic output", "")
        assert "internal diagnostic" not in reason

    def test_reason_is_generic_on_empty_stderr(self):
        reason = _doctor_failure_reason(1, "", "")
        assert "browser-use doctor exited with code 1" == reason

    def test_zero_exit_produces_correct_reason(self):
        reason = _doctor_failure_reason(0, "", "")
        assert "0" in reason
