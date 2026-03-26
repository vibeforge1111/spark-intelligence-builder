from __future__ import annotations

import json
from unittest.mock import patch

from nacl.signing import SigningKey

from spark_intelligence.channel.service import add_channel
from spark_intelligence.gateway.discord_webhook import DISCORD_WEBHOOK_PATH, handle_discord_webhook

from tests.test_support import SparkTestCase


class DiscordWebhookIngressTests(SparkTestCase):
    def _add_discord_channel(
        self,
        *,
        webhook_secret: str | None = "discord-webhook-secret",
        interaction_public_key: str | None = None,
        allow_legacy_message_webhook: bool = True,
    ) -> None:
        metadata = {"webhook_auth_ref": "DISCORD_WEBHOOK_SECRET"} if webhook_secret else None
        if webhook_secret:
            self.config_manager.upsert_env_secret("DISCORD_WEBHOOK_SECRET", webhook_secret)
        if allow_legacy_message_webhook:
            metadata = {**(metadata or {}), "allow_legacy_message_webhook": True}
        if interaction_public_key:
            metadata = {**(metadata or {}), "interaction_public_key": interaction_public_key}
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="discord",
            bot_token="discord-test-token",
            allowed_users=[],
            pairing_mode="pairing",
            metadata=metadata,
        )

    @staticmethod
    def _signed_headers(signing_key: SigningKey, body: bytes, *, timestamp: str = "1700000000") -> dict[str, str]:
        signature = signing_key.sign(timestamp.encode("utf-8") + body).signature.hex()
        return {
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
        }

    def test_rejects_wrong_method_before_payload_parsing(self) -> None:
        self._add_discord_channel()
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="GET",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "discord-webhook-secret"},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 405)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects method", payload["error"])

    def test_rejects_wrong_content_type_before_payload_parsing(self) -> None:
        self._add_discord_channel()
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="text/plain",
            headers={"X-Spark-Webhook-Secret": "discord-webhook-secret"},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 415)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects Content-Type", payload["error"])

    def test_rejects_invalid_json_body(self) -> None:
        self._add_discord_channel()
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json; charset=utf-8",
            headers={"X-Spark-Webhook-Secret": "discord-webhook-secret"},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord webhook body must be valid JSON.")

    def test_rejects_missing_secret_header(self) -> None:
        self._add_discord_channel()
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord webhook secret header is missing.")

    def test_rejects_invalid_secret_header(self) -> None:
        self._add_discord_channel()
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "wrong-secret"},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord webhook secret is invalid.")

    def test_rejects_when_webhook_secret_is_not_configured(self) -> None:
        self._add_discord_channel(webhook_secret=None, allow_legacy_message_webhook=True)
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "discord-webhook-secret"},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord webhook auth secret is not configured.")

    def test_rejects_legacy_message_webhook_when_compatibility_is_disabled(self) -> None:
        self._add_discord_channel(allow_legacy_message_webhook=False)
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "discord-webhook-secret"},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(
            payload["error"],
            "Discord legacy message webhook is disabled. Configure an interaction public key or enable legacy compatibility.",
        )

    def test_rejects_missing_signature_header_when_public_key_is_configured(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Signature-Timestamp": "1700000000"},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord signature header is missing.")

    def test_rejects_invalid_signature_when_public_key_is_configured(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={
                "X-Signature-Ed25519": "00" * 64,
                "X-Signature-Timestamp": "1700000000",
            },
            body=b"{}",
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "Discord request signature is invalid.")

    def test_handles_valid_signed_ping_payload(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps({"type": 1}).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"type": 1})

    def test_handles_signed_application_command_in_dm_context(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-1",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [
                        {"name": "message", "type": 3, "value": "hello from discord command"}
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertIn("Pairing approval is required", payload["data"]["content"])

    def test_rejects_signed_application_command_with_non_chat_input_type(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-non-chat-input",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 2,
                    "name": "spark",
                    "options": [
                        {"name": "message", "type": 3, "value": "hello from discord command"}
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertEqual(
            payload["data"]["content"],
            "Discord DM commands must use the chat-input slash command type in Spark v1.",
        )

    def test_rejects_signed_application_command_with_wrong_name(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-bad-name",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "ask",
                    "options": [
                        {"name": "message", "type": 3, "value": "hello from discord command"}
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertEqual(payload["data"]["content"], "Discord DM commands must use /spark in Spark v1.")

    def test_rejects_signed_application_command_without_message_option(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-missing-message",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [
                        {"name": "prompt", "type": 3, "value": "hello from discord command"}
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertEqual(
            payload["data"]["content"],
            "Discord DM commands must provide exactly one message option in Spark v1.",
        )

    def test_rejects_signed_application_command_with_non_string_message_option(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-non-string-option",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [
                        {"name": "message", "type": 4, "value": 7}
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertEqual(
            payload["data"]["content"],
            "Discord DM commands must provide one plain string message option in Spark v1.",
        )

    def test_rejects_signed_application_command_with_extra_options(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-extra-options",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [
                        {"name": "message", "type": 3, "value": "hello from discord command"},
                        {"name": "extra", "type": 3, "value": "should not pass"},
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertEqual(
            payload["data"]["content"],
            "Discord DM commands must provide exactly one message option in Spark v1.",
        )

    def test_rejects_signed_application_command_in_guild_context(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-2",
                "type": 2,
                "channel_id": "guild-channel-1",
                "guild_id": "guild-1",
                "context": 0,
                "member": {"user": {"id": "user-1", "username": "alice"}},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [
                        {"name": "message", "type": 3, "value": "hello from guild"}
                    ],
                },
            }
        ).encode("utf-8")
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertEqual(payload["data"]["content"], "Discord interactions are DM-only in Spark v1.")

    def test_truncates_signed_application_command_reply_to_discord_limit(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-long-reply",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [
                        {"name": "message", "type": 3, "value": "hello from discord command"}
                    ],
                },
            }
        ).encode("utf-8")
        with patch("spark_intelligence.gateway.discord_webhook.resolve_simulated_dm") as resolve_simulated_dm:
            resolve_simulated_dm.return_value.ok = True
            resolve_simulated_dm.return_value.decision = "allowed"
            resolve_simulated_dm.return_value.detail = {
                "response_text": "x" * 2600,
                "bridge_mode": "external_autodiscovered",
            }
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers=self._signed_headers(signing_key, body),
                body=body,
            )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["type"], 4)
        self.assertEqual(payload["data"]["flags"], 64)
        self.assertLessEqual(len(payload["data"]["content"]), 2000)
        self.assertTrue(payload["data"]["content"].endswith("[truncated for delivery]"))

    def test_handles_valid_dm_payload(self) -> None:
        self._add_discord_channel(allow_legacy_message_webhook=True)
        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "discord-webhook-secret"},
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
