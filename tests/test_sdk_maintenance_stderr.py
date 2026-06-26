"""Tests that sdk_maintenance does not expose subprocess stderr in returned dicts.

These exercise the real ``_run_domain_chip_memory_cli`` from the memory module
(not a local re-implementation) by stubbing ``run_governed_command``, so they
catch regressions in the shipped redaction path.
"""
from __future__ import annotations

import logging
import tempfile

import spark_intelligence.memory.sdk_maintenance as sdk_maintenance
from spark_intelligence.execution.governed import GovernedCommandExecution


def _stub_execution(monkeypatch, *, exit_code: int, stdout: str, stderr: str) -> None:
    def fake_run(*_args, **_kwargs) -> GovernedCommandExecution:
        return GovernedCommandExecution(
            command=["python", "-m", "domain_chip_memory.cli"],
            cwd="/tmp",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr(sdk_maintenance, "run_governed_command", fake_run)


class TestSdkMaintenanceStderrNotExposed:
    def test_fallback_no_stderr_key(self, monkeypatch) -> None:
        _stub_execution(monkeypatch, exit_code=1, stdout="", stderr="secret/path/details here")
        with tempfile.TemporaryDirectory() as root:
            result = sdk_maintenance._run_domain_chip_memory_cli("report", validator_root=root)
        assert "stderr" not in result

    def test_fallback_error_is_generic(self, monkeypatch) -> None:
        _stub_execution(monkeypatch, exit_code=1, stdout="", stderr="internal error details")
        with tempfile.TemporaryDirectory() as root:
            result = sdk_maintenance._run_domain_chip_memory_cli("report", validator_root=root)
        assert result["errors"] == ["sdk_maintenance_report_failed"]
        assert not any("internal" in e for e in result["errors"])

    def test_fallback_success_no_errors(self, monkeypatch) -> None:
        _stub_execution(monkeypatch, exit_code=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as root:
            result = sdk_maintenance._run_domain_chip_memory_cli("report", validator_root=root)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_payload_passthrough_no_injected_stderr(self, monkeypatch) -> None:
        # When the CLI emits a JSON payload, it is returned as-is and the
        # function must not inject a stderr key into it.
        _stub_execution(
            monkeypatch,
            exit_code=0,
            stdout='{"valid": true, "errors": [], "warnings": []}',
            stderr="leaky stderr that must not be injected",
        )
        with tempfile.TemporaryDirectory() as root:
            result = sdk_maintenance._run_domain_chip_memory_cli("report", validator_root=root)
        assert "stderr" not in result

    def test_fallback_stderr_text_not_in_errors(self, monkeypatch) -> None:
        secret = "file not found at /etc/secrets/key"
        _stub_execution(monkeypatch, exit_code=1, stdout="", stderr=secret)
        with tempfile.TemporaryDirectory() as root:
            result = sdk_maintenance._run_domain_chip_memory_cli("report", validator_root=root)
        assert all(secret not in e for e in result["errors"])

    def test_stderr_logged_server_side(self, monkeypatch, caplog) -> None:
        # Stderr must be retained server-side for diagnosis even though it is
        # stripped from the returned dict.
        secret = "DIAGNOSTIC_TRACE_SDK_99"
        _stub_execution(monkeypatch, exit_code=1, stdout="", stderr=secret)
        with caplog.at_level(logging.DEBUG, logger="spark_intelligence.memory.sdk_maintenance"):
            with tempfile.TemporaryDirectory() as root:
                sdk_maintenance._run_domain_chip_memory_cli("report", validator_root=root)
        assert any(secret in rec.getMessage() for rec in caplog.records)
