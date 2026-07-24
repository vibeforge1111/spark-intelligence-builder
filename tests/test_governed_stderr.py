"""Regression test: governed tool-result facts must not leak raw stderr.

record_governed_tool_result previously stored a 200-char stderr snippet in the
state-DB facts dict, exposing internal paths / secrets to anyone reading the
event log. It now records only a `stderr_present` boolean. This test exercises
the real function (capturing the facts handed to record_event) rather than
re-implementing the dict.
"""
from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.execution.governed import (
    GovernedCommandExecution,
    record_governed_tool_result,
)


def _execution(stderr: str, exit_code: int = 1) -> GovernedCommandExecution:
    return GovernedCommandExecution(
        command=["do", "thing"],
        cwd="/work",
        exit_code=exit_code,
        stdout="",
        stderr=stderr,
    )


def _capture_facts(execution: GovernedCommandExecution) -> dict:
    """Invoke record_governed_tool_result and return the facts dict it builds."""
    captured: dict = {}

    def _fake_record_event(_state_db, **kwargs):
        captured.update(kwargs.get("facts") or {})

    with patch(
        "spark_intelligence.execution.governed.record_event",
        side_effect=_fake_record_event,
    ):
        record_governed_tool_result(
            state_db=object(),  # unused: record_event is mocked
            execution=execution,
            component="test",
            actor_id="tester",
            summary="s",
            reason_code="rc",
            source_kind="sk",
            source_ref="sr",
        )
    return captured


class TestGovernedStderrNotInFacts:
    def test_no_raw_stderr_key(self) -> None:
        facts = _capture_facts(_execution("sensitive /etc/secret content"))
        assert "stderr" not in facts

    def test_stderr_present_true_when_stderr(self) -> None:
        facts = _capture_facts(_execution("some error"))
        assert facts["stderr_present"] is True

    def test_stderr_present_false_when_empty(self) -> None:
        facts = _capture_facts(_execution("", exit_code=0))
        assert facts["stderr_present"] is False

    def test_secret_content_not_in_any_fact_value(self) -> None:
        secret = "/home/user/.config/secrets TOKEN=abc123"
        facts = _capture_facts(_execution(secret))
        assert all(secret not in str(value) for value in facts.values())

    def test_exit_code_and_ok_preserved(self) -> None:
        facts = _capture_facts(_execution("", exit_code=0))
        assert facts["exit_code"] == 0
        assert facts["ok"] is True
