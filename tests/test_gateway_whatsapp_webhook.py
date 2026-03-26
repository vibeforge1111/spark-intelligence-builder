from __future__ import annotations

import json

from spark_intelligence.channel.service import add_channel
from spark_intelligence.gateway.whatsapp_webhook import WHATSAPP_WEBHOOK_PATH, handle_whatsapp_webhook

from tests.test_support import SparkTestCase


class WhatsAppWebhookIngressTests(SparkTestCase):
    def _add_whatsapp_channel(self, *, webhook_secret: str | None = "whatsapp-webhook-secret") -> None:
        metadata = {"webhook_auth_ref": "WHATSAPP_WEBHOOK_SECRET"} if webhook_secret else None
        if webhook_secret:
            self.config_manager.upsert_env_secret("WHATSAPP_WEBHOOK_SECRET", webhook_secret)
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="whatsapp",
            bot_token="whatsapp-test-token",
            allowed_users=[],
            pairing_mode="pairing",
            metadata=metadata,
        )

    def test_rejects_wrong_method_before_payload_parsing(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "whatsapp-webhook-secret"},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 405)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects method", payload["error"])

    def test_rejects_wrong_content_type_before_payload_parsing(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="text/plain",
            headers={"X-Spark-Webhook-Secret": "whatsapp-webhook-secret"},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 415)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects Content-Type", payload["error"])

    def test_rejects_invalid_json_body(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json; charset=utf-8",
            headers={"X-Spark-Webhook-Secret": "whatsapp-webhook-secret"},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook body must be valid JSON.")

    def test_rejects_missing_secret_header(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook secret header is missing.")

    def test_rejects_invalid_secret_header(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "wrong-secret"},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook secret is invalid.")

    def test_rejects_when_webhook_secret_is_not_configured(self) -> None:
        self._add_whatsapp_channel(webhook_secret=None)
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "whatsapp-webhook-secret"},
            body=b"{}",
        )

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook auth secret is not configured.")

    def test_handles_valid_dm_payload(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "whatsapp-webhook-secret"},
            body=json.dumps(
                {
                    "id": "wa-msg-1",
                    "chat_id": "chat-1",
                    "from": "wa-user-1",
                    "profile_name": "alice",
                    "text": "hello from whatsapp webhook",
                }
            ).encode("utf-8"),
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["decision"], "pending_pairing")
        self.assertEqual(payload["detail"]["whatsapp_user_id"], "wa-user-1")

    def test_handles_group_payload_as_ignored(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Spark-Webhook-Secret": "whatsapp-webhook-secret"},
            body=json.dumps(
                {
                    "id": "wa-msg-2",
                    "chat_id": "chat-2",
                    "from": "wa-user-2",
                    "profile_name": "alice",
                    "text": "hello from group webhook",
                    "group_id": "group-1",
                }
            ).encode("utf-8"),
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["decision"], "ignored")
        self.assertEqual(payload["detail"]["reason"], "non_dm_surface")
