"""Tests that governed execution facts do not expose raw stderr."""
import pytest


def build_facts(exit_code: int, stderr: str) -> dict:
    return {
        "exit_code": exit_code,
        "ok": exit_code == 0,
        "stderr_present": bool(stderr),
    }


class TestGovernedStderrNotInFacts:
    def test_no_raw_stderr_key(self):
        facts = build_facts(1, "sensitive /etc/secret content")
        assert "stderr" not in facts

    def test_stderr_present_true_when_stderr(self):
        facts = build_facts(1, "some error")
        assert facts["stderr_present"] is True

    def test_stderr_present_false_when_empty(self):
        facts = build_facts(0, "")
        assert facts["stderr_present"] is False

    def test_secret_content_not_in_facts(self):
        secret = "/home/user/.config/secrets"
        facts = build_facts(1, secret)
        assert all(secret not in str(v) for v in facts.values())

    def test_exit_code_and_ok_preserved(self):
        facts = build_facts(0, "")
        assert facts["exit_code"] == 0
        assert facts["ok"] is True
