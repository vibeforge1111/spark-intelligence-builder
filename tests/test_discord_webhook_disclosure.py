"""Tests: Discord webhook 503 response does not leak internal secret reference name."""
from __future__ import annotations

import json

from spark_intelligence.gateway.discord_webhook import handle_discord_webhook

from tests.test_support import SparkTestCase


class DiscordWebhookSecretRefDisclosureTests(SparkTestCase):
    def _add_discord_channel_with_unresolvable_ref(self, ref_name: str = "MISSING_SECRET_REF") -> None:
        from spark_intelligence.channel.service import add_channel
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="discord",
            bot_token="discord-test-token",
            allowed_users=[],
            pairing_mode="pairing",
            metadata={"webhook_auth_ref": ref_name, "allow_legacy_message_webhook": True},
        )

    def _call_webhook(self, secret: str | None = None) -> object:
        body = json.dumps({"id": "1", "author": {"id": "u1"}, "content": "hello"}).encode()
        headers = {}
        if secret is not None:
            headers["X-Spark-Webhook-Secret"] = secret
        return handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path="/webhooks/discord",
            method="POST",
            content_type="application/json",
            headers=headers,
            body=body,
        )

    def test_503_response_does_not_contain_secret_ref_value(self) -> None:
        self._add_discord_channel_with_unresolvable_ref("INTERNAL_DISCORD_SECRET_REF")
        response = self._call_webhook()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("INTERNAL_DISCORD_SECRET_REF", response.body)

    def test_generic_error_message_returned(self) -> None:
        self._add_discord_channel_with_unresolvable_ref("MY_SECRET_KEY")
        response = self._call_webhook()
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.body)
        self.assertIn("error", body)
        self.assertNotIn("MY_SECRET_KEY", body["error"])

    def test_specific_ref_name_not_in_response_body(self) -> None:
        self._add_discord_channel_with_unresolvable_ref("WEBHOOK_AUTH_TOKEN_REF")
        response = self._call_webhook()
        self.assertNotIn("WEBHOOK_AUTH_TOKEN_REF", response.body)

    def test_normal_webhook_requests_work_correctly(self) -> None:
        from spark_intelligence.channel.service import add_channel
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="discord",
            bot_token="discord-test-token",
            allowed_users=[],
            pairing_mode="pairing",
            metadata={"webhook_auth_ref": "DISCORD_WEBHOOK_SECRET", "allow_legacy_message_webhook": True},
        )
        self.config_manager.upsert_env_secret("DISCORD_WEBHOOK_SECRET", "correct-secret")
        response = self._call_webhook(secret="wrong-secret")
        self.assertEqual(response.status_code, 401)
