"""Tests: Discord simulate_discord_message() exception text not returned in HTTP 400."""
from __future__ import annotations

import json
from unittest.mock import patch

from spark_intelligence.gateway.discord_webhook import handle_discord_webhook

from tests.test_support import SparkTestCase


class DiscordExceptionDisclosureTests(SparkTestCase):
    def _add_discord_channel(self) -> None:
        from spark_intelligence.channel.service import add_channel
        self.config_manager.upsert_env_secret("DISCORD_WEBHOOK_SECRET", "correct-secret")
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="discord",
            bot_token="discord-test-token",
            allowed_users=[],
            pairing_mode="pairing",
            metadata={"webhook_auth_ref": "DISCORD_WEBHOOK_SECRET", "allow_legacy_message_webhook": True},
        )

    def _call_webhook(self, body_dict: dict | None = None) -> object:
        body = json.dumps(body_dict or {"id": "1", "author": {"id": "u1"}, "content": "hello"}).encode()
        return handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path="/webhooks/discord",
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "correct-secret"},
            body=body,
        )

    def test_valueerror_message_not_returned_in_400_response(self) -> None:
        self._add_discord_channel()
        internal_path = "/internal/db/path/model-v3.weights"
        with patch("spark_intelligence.adapters.discord.runtime.simulate_discord_message") as mock_sim:
            mock_sim.side_effect = ValueError(f"Internal error at {internal_path}")
            response = self._call_webhook()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(internal_path, response.body)

    def test_generic_error_string_returned_instead(self) -> None:
        self._add_discord_channel()
        with patch("spark_intelligence.adapters.discord.runtime.simulate_discord_message") as mock_sim:
            mock_sim.side_effect = ValueError("secret path: /etc/model.db channel_id=12345")
            response = self._call_webhook()
        body = json.loads(response.body)
        self.assertEqual(body.get("error"), "Discord webhook processing error.")

    def test_internal_paths_not_exposed_in_response(self) -> None:
        self._add_discord_channel()
        with patch("spark_intelligence.adapters.discord.runtime.simulate_discord_message") as mock_sim:
            mock_sim.side_effect = ValueError("model at /usr/local/spark/models/gpt-v2")
            response = self._call_webhook()
        self.assertNotIn("/usr/local/spark", response.body)

    def test_normal_requests_unaffected(self) -> None:
        from spark_intelligence.gateway.discord_webhook import _json_error_response
        resp = _json_error_response(400, "Discord webhook body must be valid JSON.")
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.body)
        self.assertIn("error", body)
