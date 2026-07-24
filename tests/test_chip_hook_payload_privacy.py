from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import _build_voice_chip_payload
from spark_intelligence.harness_runtime.service import _build_voice_hook_payload, build_harness_task_envelope
from spark_intelligence.self_awareness.route_probe import _run_voice_status_probe

from tests.test_support import SparkTestCase


class ChipHookPayloadPrivacyTests(SparkTestCase):
    def test_telegram_voice_chip_payload_does_not_include_env_file_path(self) -> None:
        payload = _build_voice_chip_payload(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            agent_id="agent-1",
        )

        self.assertNotIn("builder_env_file_path", payload)

    def test_harness_voice_hook_payload_does_not_include_env_file_path(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say this aloud.",
            forced_harness_id="voice.io",
            channel_kind="telegram",
            human_id="human-1",
            agent_id="agent-1",
        )

        payload = _build_voice_hook_payload(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            text="Hello.",
        )

        self.assertNotIn("builder_env_file_path", payload)

    def test_route_probe_voice_status_payload_does_not_include_env_file_path(self) -> None:
        with patch(
            "spark_intelligence.attachments.hooks.run_first_chip_hook_supporting",
            return_value=None,
        ) as hook:
            _run_voice_status_probe(self.config_manager, self.state_db)

        payload = hook.call_args.kwargs["payload"]
        self.assertNotIn("builder_env_file_path", payload)
