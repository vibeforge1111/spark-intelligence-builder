from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import _render_telegram_route_probe_reply
from spark_intelligence.self_awareness.route_probe import _run_swarm_status_probe

from tests.test_support import SparkTestCase


class SwarmProbeRecoveryTruthTests(SparkTestCase):
    def _probe(self, **overrides: object) -> dict[str, object]:
        status = SimpleNamespace(
            payload_ready=False,
            api_ready=False,
            auth_state="missing",
            last_failure={"message": "raw hosted failure should not replace local diagnosis"},
        )
        for key, value in overrides.items():
            setattr(status, key, value)
        with patch("spark_intelligence.swarm_bridge.swarm_status", return_value=status):
            return _run_swarm_status_probe(self.config_manager, self.state_db)

    def test_missing_local_payload_recommends_specialization_not_auth(self) -> None:
        result = self._probe()

        assert result["status"] == "failure"
        reason = str(result["failure_reason"])
        assert "specialization path" in reason
        assert "auth" not in reason.casefold()
        assert "api_ready" not in reason
        assert "raw hosted failure" not in reason

    def test_local_payload_diagnosis_does_not_change_when_api_is_ready(self) -> None:
        result = self._probe(api_ready=True, auth_state="configured")

        assert result["status"] == "failure"
        assert "specialization path" in str(result["failure_reason"])

    def test_local_payload_ready_remains_success_without_hosted_auth(self) -> None:
        result = self._probe(payload_ready=True)

        assert result["status"] == "success"
        assert result["failure_reason"] == ""

    def test_telegram_swarm_failure_is_a_human_recovery_reply(self) -> None:
        reply = _render_telegram_route_probe_reply(
            SimpleNamespace(
                status="failure",
                capability_key="spark_swarm",
                route_latency_ms=7,
                failure_reason="Spark Swarm local payload is not ready.",
                probe_summary="swarm payload_ready=False api_ready=False auth_state=missing",
            )
        )

        assert reply == (
            "⚠️ Swarm's local payload isn't ready yet. "
            "Connect a specialization path, then run `/probe swarm` again."
        )
        assert "auth_state" not in reply
        assert "Status:" not in reply

    def test_telegram_swarm_success_keeps_the_proof_boundary_plain(self) -> None:
        reply = _render_telegram_route_probe_reply(
            SimpleNamespace(
                status="success",
                capability_key="spark_swarm",
                route_latency_ms=7,
                failure_reason="",
                probe_summary="swarm payload_ready=True api_ready=False auth_state=missing",
            )
        )

        assert reply == (
            "✨ Swarm's local payload route is ready. "
            "That proves local shaping, not a hosted sync."
        )
        assert "api_ready" not in reply
        assert "Status:" not in reply
