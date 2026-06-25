"""Tests: swarm bridge failure message does not forward raw stderr to the Telegram reply.

These exercise the real ``_render_swarm_bridge_failure`` from the telegram
runtime (not a local re-implementation), so they catch regressions in the
shipped redaction path.
"""
from __future__ import annotations

from spark_intelligence.adapters.telegram.runtime import _render_swarm_bridge_failure


class _FakeResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 1) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class TestSwarmBridgeStderr:
    def test_stderr_not_in_reply(self) -> None:
        msg = _render_swarm_bridge_failure(
            "run", _FakeResult(stderr="SECRET_KEY=abc internal path /home/user")
        )
        assert "SECRET_KEY" not in msg
        assert "/home/user" not in msg

    def test_generic_detail_present(self) -> None:
        msg = _render_swarm_bridge_failure("run", _FakeResult(stderr="anything"))
        assert "see server logs" in msg

    def test_stdout_not_in_reply(self) -> None:
        msg = _render_swarm_bridge_failure("run", _FakeResult(stdout="internal trace info"))
        assert "internal trace info" not in msg

    def test_action_in_reply(self) -> None:
        msg = _render_swarm_bridge_failure("deploy", _FakeResult())
        assert "Swarm deploy failed." in msg

    def test_exit_code_in_reply(self) -> None:
        msg = _render_swarm_bridge_failure("run", _FakeResult(exit_code=2))
        assert "Exit code: 2." in msg

    def test_raw_detail_logged_server_side(self, caplog) -> None:
        # The raw stderr must be retained server-side (debug log) even though it
        # is stripped from the user-facing reply.
        import logging

        with caplog.at_level(logging.DEBUG, logger="spark_intelligence.adapters.telegram.runtime"):
            _render_swarm_bridge_failure("run", _FakeResult(stderr="DIAGNOSTIC_TRACE_42"))
        assert any("DIAGNOSTIC_TRACE_42" in rec.getMessage() for rec in caplog.records)
