from __future__ import annotations

import json

from spark_intelligence.channel.service import add_channel
from spark_intelligence.gateway.discord_webhook import DISCORD_WEBHOOK_PATH, handle_discord_webhook

from tests.test_support import SparkTestCase


class DiscordWebhookIngressTests(SparkTestCase):
    def test_rejects_wrong_method_before_payload_parsing(self) -> None:
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="GET",
            content_type="application/json",
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 405)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects method", payload["error"])

    def test_rejects_wrong_content_type_before_payload_parsing(self) -> None:
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="text/plain",
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 415)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects Content-Type", payload["error"])

    def test_rejects_invalid_json_body(self) -> None:
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json; charset=utf-8",
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord webhook body must be valid JSON.")

    def test_handles_valid_dm_payload(self) -> None:
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="discord",
            bot_token="discord-test-token",
            allowed_users=[],
            pairing_mode="pairing",
        )
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            body=json.dumps(
                {
                    "id": "msg-1",
                    "channel_id": "dm-1",
                    "content": "hello from discord webhook",
                    "author": {"id": "user-1", "username": "alice"},
                }
            ).encode("utf-8"),
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["decision"], "pending_pairing")
        self.assertEqual(payload["detail"]["discord_user_id"], "user-1")
