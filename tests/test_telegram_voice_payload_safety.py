from __future__ import annotations

import base64
import binascii
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import (
    _safe_voice_error_message,
    _synthesize_telegram_voice_reply,
)
from spark_intelligence.cli import _json_object_from_text

from tests.test_support import SparkTestCase


class TelegramVoicePayloadSafetyTests(SparkTestCase):
    def test_voice_synthesis_rejects_noncanonical_base64(self) -> None:
        execution = SimpleNamespace(
            ok=True,
            chip_key="spark-voice-comms",
            stdout="",
            stderr="",
            output={
                "result": {
                    "audio_base64": "aGVs!!bG8=",
                    "mime_type": "audio/ogg",
                }
            },
        )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            return_value=execution,
        ), self.assertRaises(binascii.Error):
            _synthesize_telegram_voice_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                text="hello",
                human_id="human:test",
                agent_id="agent:test",
            )

    def test_voice_synthesis_still_accepts_canonical_base64(self) -> None:
        execution = SimpleNamespace(
            ok=True,
            chip_key="spark-voice-comms",
            stdout="",
            stderr="",
            output={
                "result": {
                    "audio_base64": base64.b64encode(b"hello").decode("ascii"),
                    "mime_type": "audio/ogg",
                }
            },
        )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            return_value=execution,
        ):
            result = _synthesize_telegram_voice_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                text="hello",
                human_id="human:test",
                agent_id="agent:test",
            )

        self.assertEqual(result["audio_bytes"], b"hello")

    def test_voice_error_hides_urls_and_credentials(self) -> None:
        secret = "sk-proj-" + "A" * 30
        rendered = _safe_voice_error_message(
            RuntimeError(f"failed at https://internal.example/private?token={secret}")
        )

        self.assertNotIn("https://", rendered)
        self.assertNotIn("internal.example", rendered)
        self.assertNotIn(secret, rendered)

    def test_voice_error_uses_canonical_authorization_scheme_redaction(self) -> None:
        secrets = {
            "Token": "placeholder-token-value-123456",
            "ApiKey": "placeholder-apikey-value-123456",
            "OAuth": "placeholder-oauth-value-123456",
        }
        rendered = _safe_voice_error_message(
            RuntimeError(
                ", ".join(f"Authorization: {scheme} {secret}" for scheme, secret in secrets.items())
            )
        )

        for scheme, secret in secrets.items():
            self.assertNotIn(secret, rendered)
            self.assertIn(f"Authorization: {scheme} <redacted>", rendered)

    def test_invalid_internal_json_does_not_echo_raw_text(self) -> None:
        malformed = '{"token":"secret-value-should-not-return"'

        self.assertEqual(_json_object_from_text(malformed), {"status": "unknown"})
