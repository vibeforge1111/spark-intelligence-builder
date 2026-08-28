from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.harness_runtime.service import (
    HarnessTaskEnvelope,
    _build_voice_hook_payload,
)

from tests.test_support import SparkTestCase


def _make_envelope(
    *,
    channel_kind: str | None = "telegram",
    human_id: str | None = "human-1",
    agent_id: str | None = "agent-1",
) -> HarnessTaskEnvelope:
    return HarnessTaskEnvelope(
        envelope_id="htask:voice-payload",
        task="Say: Hello.",
        harness_id="voice.io",
        owner_system="Spark Voice",
        backend_kind="voice_bridge",
        session_scope="message_turn",
        prompt_strategy="transcript_then_spoken_reply_render",
        route_mode="voice_io",
        required_capabilities=[],
        artifacts_expected=[],
        next_actions=[],
        limitations=[],
        channel_kind=channel_kind,
        session_id="session-1",
        human_id=human_id,
        agent_id=agent_id,
    )


class BuildVoiceHookPayloadTests(SparkTestCase):
    def test_payload_includes_surface_and_identity_fields(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.harness_runtime.service.build_runtime_provider_reference_payload",
            return_value={"provider_id": "elevenlabs", "voice_id": "voice-1"},
        ):
            payload = _build_voice_hook_payload(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(payload["surface"], "telegram")
        self.assertEqual(payload["human_id"], "human-1")
        self.assertEqual(payload["agent_id"], "agent-1")
        self.assertEqual(payload["provider"], {"provider_id": "elevenlabs", "voice_id": "voice-1"})
        self.assertNotIn("provider_error", payload)
        self.assertNotIn("text", payload)

    def test_payload_falls_back_to_cli_surface_when_channel_kind_missing(self) -> None:
        envelope = _make_envelope(channel_kind=None)
        with patch(
            "spark_intelligence.harness_runtime.service.build_runtime_provider_reference_payload",
            return_value={"provider_id": "elevenlabs"},
        ):
            payload = _build_voice_hook_payload(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(payload["surface"], "cli")

    def test_payload_records_provider_error_when_reference_build_raises(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.harness_runtime.service.build_runtime_provider_reference_payload",
            side_effect=RuntimeError("provider lookup failed"),
        ):
            payload = _build_voice_hook_payload(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(payload["provider_error"], "provider lookup failed")
        self.assertNotIn("provider", payload)
        self.assertEqual(payload["surface"], "telegram")

    def test_payload_omits_text_field_when_text_is_none(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.harness_runtime.service.build_runtime_provider_reference_payload",
            return_value={},
        ):
            payload = _build_voice_hook_payload(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                text=None,
            )
        self.assertNotIn("text", payload)

    def test_payload_includes_text_field_when_text_provided(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.harness_runtime.service.build_runtime_provider_reference_payload",
            return_value={},
        ):
            payload = _build_voice_hook_payload(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                text="Hello from Spark.",
            )
        self.assertEqual(payload["text"], "Hello from Spark.")

    def test_payload_includes_empty_string_text_when_explicitly_provided(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.harness_runtime.service.build_runtime_provider_reference_payload",
            return_value={},
        ):
            payload = _build_voice_hook_payload(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                text="",
            )
        # text="" is still an explicit operator choice; payload must preserve it
        self.assertIn("text", payload)
        self.assertEqual(payload["text"], "")
