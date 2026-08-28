from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.harness_runtime import (
    build_harness_task_envelope,
    execute_harness_task,
)

from tests.test_support import SparkTestCase


class VoiceHarnessBlockedPathsTests(SparkTestCase):
    def test_voice_status_hook_failure_returns_blocked_with_resume_token(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello.",
            forced_harness_id="voice.io",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=RuntimeError("No attached chip supports `voice.status`."),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        voice_status = result.artifacts["voice_status"]
        self.assertIsNone(voice_status["chip_key"])
        self.assertFalse(voice_status["ready"])
        self.assertIn("voice.status", voice_status["reason"])
        resume = result.artifacts["resume_token"]
        self.assertEqual(resume["resume_kind"], "voice_status_repair")
        self.assertEqual(resume["harness_id"], "voice.io")

    def test_voice_speak_hook_failure_records_retry_token_with_reason(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello.",
            forced_harness_id="voice.io",
        )

        def fake_voice_hook(*, hook, **kwargs):
            if hook == "voice.status":
                return (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                )
            raise RuntimeError("voice.speak provider unavailable")

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=fake_voice_hook,
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        retry = result.artifacts["retry_token"]
        self.assertIn("voice.io", retry["retry_command"])
        self.assertEqual(retry["reason"], "voice.speak provider unavailable")
        resume = result.artifacts["resume_token"]
        self.assertEqual(resume["resume_kind"], "voice_speak_retry")

    def test_voice_unspecified_task_when_voice_chip_ready_returns_needs_input(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Use voice somehow for this.",
            forced_harness_id="voice.io",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            return_value=(
                {
                    "result": {
                        "ready": True,
                        "reason": "voice ready",
                        "reply_text": "Voice chip is ready.",
                    }
                },
                "domain-chip-voice-comms",
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "needs_input")
        needs = result.artifacts["needs_input"]
        self.assertEqual(needs["mode"], "unspecified")
        self.assertIn("explicit speech text", needs["reason"])

    def test_voice_transcribe_task_when_voice_chip_ready_returns_needs_input(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Transcribe this voice memo.",
            forced_harness_id="voice.io",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            return_value=(
                {
                    "result": {
                        "ready": True,
                        "reason": "voice ready",
                        "reply_text": "Voice chip is ready.",
                    }
                },
                "domain-chip-voice-comms",
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "needs_input")
        needs = result.artifacts["needs_input"]
        self.assertEqual(needs["mode"], "transcribe")
        self.assertIn("audio bytes", needs["reason"])

    def test_voice_speak_blocked_when_voice_chip_not_ready(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello.",
            forced_harness_id="voice.io",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            return_value=(
                {
                    "result": {
                        "ready": False,
                        "reason": "voice provider missing",
                        "reply_text": "",
                    }
                },
                "domain-chip-voice-comms",
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.artifacts["voice_status"]["ready"], False)
        self.assertEqual(result.artifacts["voice_status"]["reason"], "voice provider missing")
        resume = result.artifacts["resume_token"]
        self.assertEqual(resume["resume_kind"], "voice_status_repair")
