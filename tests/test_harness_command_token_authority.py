from __future__ import annotations

import os
import shlex
import sys
from unittest.mock import patch

from spark_intelligence.harness_runtime import (
    build_harness_task_envelope,
    execute_harness_task,
    with_harness_local_operator_turn_intent,
)
from spark_intelligence.harness_runtime import service as harness_service

from tests.test_support import SparkTestCase


class HarnessCommandTokenAuthorityTests(SparkTestCase):
    def assert_command_token(
        self,
        token: dict[str, object],
        *,
        kind: str,
        expected_argv: list[str],
    ) -> None:
        argv_key = f"{kind}_argv"
        command_key = f"{kind}_command"
        self.assertEqual(token[argv_key], expected_argv)
        self.assertEqual(token["command_platform"], "windows_cmd" if os.name == "nt" else "posix_shell")
        if os.name != "nt":
            self.assertEqual(shlex.split(str(token[command_key])), expected_argv)

    def test_resume_token_keeps_task_home_harness_and_channel_as_exact_argv(self) -> None:
        task = 'say "hello"; $(touch should-never-run)'
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task=task,
            forced_harness_id="voice.io",
            channel_kind="telegram; echo unsafe",
        )

        token = harness_service._build_harness_resume_token(
            config_manager=self.config_manager,
            envelope=envelope,
            step="voice_input_required",
        )

        self.assert_command_token(
            token,
            kind="resume",
            expected_argv=[
                sys.executable,
                "-m",
                "spark_intelligence.cli",
                "harness",
                "execute",
                task,
                "--home",
                str(self.config_manager.paths.home),
                "--harness-id",
                "voice.io",
                "--channel-kind",
                "telegram; echo unsafe",
            ],
        )

    def test_voice_retry_token_uses_the_same_exact_argv_authority(self) -> None:
        task = 'say: hello; $(touch should-never-run) "quoted"'
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task=task,
            forced_harness_id="voice.io",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=[
                (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                ),
                RuntimeError("voice provider unavailable"),
            ],
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        self.assert_command_token(
            result.artifacts["retry_token"],
            kind="retry",
            expected_argv=[
                sys.executable,
                "-m",
                "spark_intelligence.cli",
                "harness",
                "execute",
                task,
                "--home",
                str(self.config_manager.paths.home),
                "--harness-id",
                "voice.io",
            ],
        )

    def test_swarm_retry_and_resume_tokens_keep_home_as_exact_argv(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Coordinate this through Swarm.",
            forced_harness_id="swarm.escalation",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._load_swarm_status",
            return_value=type(
                "Status",
                (),
                {
                    "enabled": True,
                    "configured": True,
                    "researcher_ready": True,
                    "payload_ready": False,
                    "api_ready": False,
                    "auth_state": "refreshable",
                    "workspace_id": "workspace-1",
                    "api_url": "https://swarm.example",
                    "last_decision": None,
                    "last_failure": {"mode": "researcher_missing"},
                },
            )(),
        ):
            repair_result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assert_command_token(
            repair_result.artifacts["retry_token"],
            kind="retry",
            expected_argv=[
                sys.executable,
                "-m",
                "spark_intelligence.cli",
                "swarm",
                "status",
                "--home",
                str(self.config_manager.paths.home),
            ],
        )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._load_swarm_status",
                return_value=type(
                    "Status",
                    (),
                    {
                        "enabled": True,
                        "configured": True,
                        "researcher_ready": True,
                        "payload_ready": True,
                        "api_ready": True,
                        "auth_state": "configured",
                        "workspace_id": "workspace-1",
                        "api_url": "https://swarm.example",
                        "last_decision": None,
                        "last_failure": None,
                    },
                )(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_swarm_sync_dry_run",
                return_value=type(
                    "SyncResult",
                    (),
                    {
                        "ok": True,
                        "mode": "dry_run",
                        "message": "Built payload",
                        "payload_path": "payload.json",
                        "api_url": "https://swarm.example",
                        "workspace_id": "workspace-1",
                        "accepted": None,
                        "response_body": {},
                    },
                )(),
            ),
        ):
            ready_result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assert_command_token(
            ready_result.artifacts["resume_token"],
            kind="resume",
            expected_argv=[
                sys.executable,
                "-m",
                "spark_intelligence.cli",
                "swarm",
                "sync",
                "--home",
                str(self.config_manager.paths.home),
            ],
        )
        self.assert_command_token(
            ready_result.artifacts["retry_token"],
            kind="retry",
            expected_argv=[
                sys.executable,
                "-m",
                "spark_intelligence.cli",
                "swarm",
                "sync",
                "--dry-run",
                "--home",
                str(self.config_manager.paths.home),
            ],
        )
