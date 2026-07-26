from __future__ import annotations

from unittest.mock import patch

import pytest

from spark_intelligence.harness_runtime import build_harness_task_envelope, execute_harness_task
from tests.test_support import SparkTestCase


class VoiceHarnessExceptionBoundaryTests(SparkTestCase):
    def _envelope(self):
        return build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello from Spark voice.",
            forced_harness_id="voice.io",
        )

    def test_status_hook_programming_error_propagates(self) -> None:
        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=AttributeError("typo in attachments helper"),
        ):
            with pytest.raises(AttributeError, match="typo in attachments helper"):
                execute_harness_task(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=self._envelope(),
                )

    def test_speak_hook_programming_error_propagates(self) -> None:
        def fake_voice_hook(*, hook, **_kwargs):
            if hook == "voice.status":
                return (
                    {"result": {"ready": True, "reason": "voice ready", "reply_text": "Voice chip is ready."}},
                    "domain-chip-voice-comms",
                )
            raise AttributeError("typo in voice.speak helper")

        with patch("spark_intelligence.harness_runtime.service._run_voice_hook", side_effect=fake_voice_hook):
            with pytest.raises(AttributeError, match="typo in voice.speak helper"):
                execute_harness_task(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=self._envelope(),
                )
