from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.harness_runtime import (
    build_harness_task_envelope,
    execute_harness_task,
)

from tests.test_support import SparkTestCase


def _disabled_status() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=False,
        configured=False,
        researcher_ready=False,
        payload_ready=False,
        api_ready=False,
        auth_state="missing",
        workspace_id=None,
        api_url=None,
        last_decision=None,
        last_failure=None,
    )


def _ready_status() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        configured=True,
        researcher_ready=True,
        payload_ready=True,
        api_ready=True,
        auth_state="configured",
        workspace_id="workspace-1",
        api_url="https://swarm.example",
        last_decision={"mode": "manual_recommended"},
        last_failure=None,
    )


class SwarmHarnessBlockedPathsTests(SparkTestCase):
    def test_swarm_harness_blocked_when_bridge_disabled(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Coordinate this through Swarm.",
            forced_harness_id="swarm.escalation",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._load_swarm_status",
            return_value=_disabled_status(),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        swarm_status = result.artifacts["swarm_status"]
        self.assertFalse(swarm_status["enabled"])
        self.assertFalse(swarm_status["configured"])
        resume = result.artifacts["resume_token"]
        self.assertEqual(resume["resume_kind"], "swarm_enablement_required")
        self.assertNotIn("swarm_sync_result", result.artifacts)

    def test_swarm_harness_returns_needs_input_when_dry_run_fails(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Coordinate this through Swarm.",
            forced_harness_id="swarm.escalation",
        )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._load_swarm_status",
                return_value=_ready_status(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_swarm_sync_dry_run",
                return_value=SimpleNamespace(
                    ok=False,
                    mode="dry_run",
                    message="Researcher payload missing.",
                    payload_path=None,
                    api_url="https://swarm.example",
                    workspace_id="workspace-1",
                    accepted=None,
                    response_body=None,
                ),
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "needs_input")
        sync = result.artifacts["swarm_sync_result"]
        self.assertFalse(sync["ok"])
        self.assertEqual(sync["message"], "Researcher payload missing.")
        retry = result.artifacts["retry_token"]
        self.assertIn("--dry-run", retry["retry_command"])

    def test_swarm_harness_preserves_status_fields_in_payload(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Coordinate this through Swarm.",
            forced_harness_id="swarm.escalation",
        )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._load_swarm_status",
                return_value=SimpleNamespace(
                    enabled=True,
                    configured=True,
                    researcher_ready=True,
                    payload_ready=True,
                    api_ready=False,
                    auth_state="refreshable",
                    workspace_id="workspace-2",
                    api_url="https://swarm.example/v2",
                    last_decision={"mode": "auto"},
                    last_failure={"reason": "stale_token"},
                ),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_swarm_sync_dry_run",
                return_value=SimpleNamespace(
                    ok=True,
                    mode="dry_run",
                    message="Built payload",
                    payload_path="/tmp/swarm-payload.json",
                    api_url="https://swarm.example/v2",
                    workspace_id="workspace-2",
                    accepted=None,
                    response_body={"payload_keys": ["collective"]},
                ),
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "prepared")
        swarm_status = result.artifacts["swarm_status"]
        # Identity fields from the status fixture must flow through unchanged
        self.assertEqual(swarm_status["workspace_id"], "workspace-2")
        self.assertEqual(swarm_status["api_url"], "https://swarm.example/v2")
        self.assertEqual(swarm_status["auth_state"], "refreshable")
        self.assertEqual(swarm_status["last_failure"], {"reason": "stale_token"})
