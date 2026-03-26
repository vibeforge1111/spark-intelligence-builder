from __future__ import annotations

import hashlib
import hmac
import json

from spark_intelligence.channel.service import add_channel
from spark_intelligence.gateway.whatsapp_webhook import WHATSAPP_WEBHOOK_PATH, handle_whatsapp_webhook

from tests.test_support import SparkTestCase


class WhatsAppWebhookIngressTests(SparkTestCase):
    def _add_whatsapp_channel(
        self,
        *,
        webhook_secret: str | None = "whatsapp-app-secret",
        webhook_verify_token: str | None = "whatsapp-verify-token",
    ) -> None:
        metadata: dict[str, str] | None = None
        if webhook_secret:
            self.config_manager.upsert_env_secret("WHATSAPP_WEBHOOK_SECRET", webhook_secret)
            metadata = {**(metadata or {}), "webhook_auth_ref": "WHATSAPP_WEBHOOK_SECRET"}
        if webhook_verify_token:
            self.config_manager.upsert_env_secret("WHATSAPP_WEBHOOK_VERIFY_TOKEN", webhook_verify_token)
            metadata = {**(metadata or {}), "webhook_verify_token_ref": "WHATSAPP_WEBHOOK_VERIFY_TOKEN"}
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="whatsapp",
            bot_token="whatsapp-test-token",
            allowed_users=[],
            pairing_mode="pairing",
            metadata=metadata,
        )

    @staticmethod
    def _signature_headers(secret: str, body: bytes) -> dict[str, str]:
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return {"X-Hub-Signature-256": f"sha256={digest}"}

    def test_rejects_wrong_method_for_post_route(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="DELETE",
            content_type="application/json",
            headers={},
            body=b"",
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
            headers={},
            body=b"{not-json",
        )

        self.assertEqual(response.status_code, 415)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertIn("rejects Content-Type", payload["error"])

    def test_verification_returns_challenge_for_valid_verify_token(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "whatsapp-verify-token",
                "hub.challenge": "challenge-code",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/plain")
        self.assertEqual(response.body, "challenge-code")

    def test_verification_rejects_invalid_verify_token(self) -> None:
        self._add_whatsapp_channel()
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-code",
            },
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook verify token is invalid.")

    def test_verification_rejects_missing_verify_token_configuration(self) -> None:
        self._add_whatsapp_channel(webhook_verify_token=None)
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "whatsapp-verify-token",
                "hub.challenge": "challenge-code",
            },
        )

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook verify token is not configured.")

    def test_rejects_missing_signature_header(self) -> None:
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
        self.assertEqual(payload["error"], "WhatsApp webhook signature header is missing.")

    def test_rejects_invalid_signature_header(self) -> None:
        self._add_whatsapp_channel()
        body = b"{}"
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            body=body,
        )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook signature is invalid.")

    def test_rejects_invalid_json_body(self) -> None:
        self._add_whatsapp_channel()
        body = b"{not-json"
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json; charset=utf-8",
            headers=self._signature_headers("whatsapp-app-secret", body),
            body=body,
        )

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "WhatsApp webhook body must be valid JSON.")

    def test_handles_valid_meta_dm_payload(self) -> None:
        self._add_whatsapp_channel()
        body = json.dumps(
            {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "changes": [
                            {
                                "field": "messages",
                                "value": {
                                    "metadata": {"phone_number_id": "phone-1"},
                                    "contacts": [
                                        {"wa_id": "wa-user-1", "profile": {"name": "alice"}}
                                    ],
                                    "messages": [
                                        {
                                            "from": "wa-user-1",
                                            "id": "wamid-1",
                                            "type": "text",
                                            "text": {"body": "hello from whatsapp webhook"},
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                ],
            }
        ).encode("utf-8")
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signature_headers("whatsapp-app-secret", body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["decision"], "pending_pairing")
        self.assertEqual(payload["detail"]["whatsapp_user_id"], "wa-user-1")

    def test_ignores_status_event_payload(self) -> None:
        self._add_whatsapp_channel()
        body = json.dumps(
            {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "changes": [
                            {
                                "field": "messages",
                                "value": {"statuses": [{"id": "status-1"}]},
                            }
                        ]
                    }
                ],
            }
        ).encode("utf-8")
        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signature_headers("whatsapp-app-secret", body),
            body=body,
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["decision"], "ignored")
        self.assertEqual(payload["detail"]["reason"], "status_event")
