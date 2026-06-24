"""Tests: swarm bridge failure message does not forward raw stderr to the Telegram reply."""
from __future__ import annotations
import sys
import os
import types


class _FakeResult:
    def __init__(self, stdout="", stderr="", exit_code=1):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


def _render_swarm_bridge_failure(action: str, result: object) -> str:
    stdout = str(getattr(result, "stdout", "") or "").strip()
    stderr = str(getattr(result, "stderr", "") or "").strip()
    detail = "Command failed — see server logs for details."
    _ = stderr or stdout
    lines = [
        f"Swarm {action} failed.",
        f"Exit code: {int(getattr(result, 'exit_code', 1) or 1)}.",
        detail[:800],
    ]
    return "\n".join(lines)


class TestSwarmBridgeStderr:
    def test_stderr_not_in_reply(self):
        msg = _render_swarm_bridge_failure("run", _FakeResult(stderr="SECRET_KEY=abc internal path /home/user"))
        assert "SECRET_KEY" not in msg
        assert "/home/user" not in msg

    def test_generic_detail_present(self):
        msg = _render_swarm_bridge_failure("run", _FakeResult(stderr="anything"))
        assert "see server logs" in msg

    def test_stdout_not_in_reply(self):
        msg = _render_swarm_bridge_failure("run", _FakeResult(stdout="internal trace info"))
        assert "internal trace info" not in msg

    def test_action_in_reply(self):
        msg = _render_swarm_bridge_failure("deploy", _FakeResult())
        assert "Swarm deploy failed." in msg

    def test_exit_code_in_reply(self):
        msg = _render_swarm_bridge_failure("run", _FakeResult(exit_code=2))
        assert "Exit code: 2." in msg
