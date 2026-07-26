from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from nacl.signing import SigningKey

from spark_intelligence.channel.service import add_channel
from spark_intelligence.gateway.runtime import gateway_trace_view
from spark_intelligence.gateway.discord_webhook import (
    DISCORD_WEBHOOK_PATH,
    _claim_discord_interaction_request,
    handle_discord_webhook,
)
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.ops.service import _build_webhook_alerts

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
    def _signed_headers(signing_key: SigningKey, body: bytes, *, timestamp: str | None = None) -> dict[str, str]:
        timestamp = timestamp or str(int(time.time()))
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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")
        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                channel_id="discord",
                event="discord_webhook_auth_failed",
                decision="rejected",
                as_json=True,
            )
        )
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["reason"], "Discord webhook secret header is missing.")
        self.assertEqual(traces[0]["status_code"], 401)

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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")

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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")

    def test_unresolved_webhook_secret_ref_stays_out_of_public_error(self) -> None:
        secret_ref = "INTERNAL_DISCORD_WEBHOOK_SECRET_REF"
        self._add_discord_channel(webhook_secret=None, allow_legacy_message_webhook=True)
        self.config_manager.set_path("channels.records.discord.webhook_auth_ref", secret_ref)

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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")
        self.assertNotIn(secret_ref, response.body)
        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                channel_id="discord",
                event="discord_webhook_auth_failed",
                decision="rejected",
                as_json=True,
            )
        )
        self.assertEqual(len(traces), 1)
        self.assertIn(secret_ref, traces[0]["reason"])

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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")

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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")

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
        self.assertEqual(payload["error"], "Discord webhook authentication failed.")
        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                channel_id="discord",
                event="discord_webhook_auth_failed",
                decision="rejected",
                as_json=True,
            )
        )
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["reason"], "Discord request signature is invalid.")
        self.assertEqual(traces[0]["status_code"], 401)

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

    def test_rejects_stale_and_far_future_signed_timestamps(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps({"type": 1}).encode("utf-8")

        with patch("spark_intelligence.gateway.discord_webhook.time.time", return_value=1_700_000_000):
            for timestamp in ("1699999699", "1700000301"):
                with self.subTest(timestamp=timestamp):
                    response = handle_discord_webhook(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        path=DISCORD_WEBHOOK_PATH,
                        method="POST",
                        content_type="application/json",
                        headers=self._signed_headers(signing_key, body, timestamp=timestamp),
                        body=body,
                    )

                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(json.loads(response.body)["error"], "Discord webhook authentication failed.")

    def test_accepts_signed_timestamp_at_clock_skew_boundary(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps({"type": 1}).encode("utf-8")

        with patch("spark_intelligence.gateway.discord_webhook.time.time", return_value=1_700_000_000):
            for timestamp in ("1699999700", "1700000300"):
                with self.subTest(timestamp=timestamp):
                    response = handle_discord_webhook(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        path=DISCORD_WEBHOOK_PATH,
                        method="POST",
                        content_type="application/json",
                        headers=self._signed_headers(signing_key, body, timestamp=timestamp),
                        body=body,
                    )

                    self.assertEqual(response.status_code, 200)

    def test_rejects_non_ascii_or_non_integer_signed_timestamps(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps({"type": 1}).encode("utf-8")

        for timestamp in ("nan", "1700000000.0", "1.7e9", "+1700000000", "١٧٠٠٠٠٠٠٠٠"):
            with self.subTest(timestamp=timestamp):
                response = handle_discord_webhook(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    path=DISCORD_WEBHOOK_PATH,
                    method="POST",
                    content_type="application/json",
                    headers=self._signed_headers(signing_key, body, timestamp=timestamp),
                    body=body,
                )

                self.assertEqual(response.status_code, 401)
                self.assertEqual(json.loads(response.body)["error"], "Discord webhook authentication failed.")

    def test_rejects_replayed_signed_interaction_before_mission_resolution(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps(
            {
                "id": "interaction-replay-1",
                "type": 2,
                "channel_id": "dm-1",
                "context": 1,
                "user": {"id": "user-1", "username": "alice"},
                "data": {
                    "type": 1,
                    "name": "spark",
                    "options": [{"name": "message", "type": 3, "value": "run this once"}],
                },
            }
        ).encode("utf-8")
        headers = self._signed_headers(signing_key, body)

        with patch("spark_intelligence.gateway.discord_webhook.resolve_simulated_dm") as resolve_simulated_dm:
            resolve_simulated_dm.return_value.ok = True
            resolve_simulated_dm.return_value.decision = "allowed"
            resolve_simulated_dm.return_value.detail = {
                "response_text": "done once",
                "bridge_mode": "external_autodiscovered",
                "trace_ref": "trace:discord-replay",
                "output_keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
            }
            first = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers=headers,
                body=body,
            )
            replay = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers=headers,
                body=body,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(json.loads(replay.body)["error"], "Discord interaction was already received.")
        resolve_simulated_dm.assert_called_once()
        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                channel_id="discord",
                event="discord_interaction_replay_rejected",
                decision="rejected",
                as_json=True,
            )
        )
        self.assertEqual(len(traces), 1)
        self.assertNotIn("interaction-replay-1", json.dumps(traces))
        alerts = _build_webhook_alerts(traces=traces, state_db=self.state_db)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["status"], "replay_rejected")
        self.assertIn("Discord interaction replay rejected", alerts[0]["summary"])

    def test_interaction_replay_claim_is_atomic_across_connections(self) -> None:
        now_epoch = int(time.time())

        def claim(_: int) -> bool:
            return _claim_discord_interaction_request(
                state_db=self.state_db,
                interaction_id="interaction-concurrent-claim",
                payload_sha256="a" * 64,
                signed_at_epoch=now_epoch,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(claim, range(8)))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 7)

    def test_rejects_signed_non_ping_interaction_without_durable_id(self) -> None:
        signing_key = SigningKey.generate()
        self._add_discord_channel(interaction_public_key=signing_key.verify_key.encode().hex(), webhook_secret=None)
        body = json.dumps({"type": 2, "user": {"id": "user-1"}}).encode("utf-8")

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers=self._signed_headers(signing_key, body),
            body=body,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            json.loads(response.body)["error"],
            "Discord interaction payload is missing its interaction id.",
        )

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
        self.assertIn("Access is not authorized for this channel", payload["data"]["content"])

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
                "trace_ref": "trace:discord-interaction",
                "output_keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
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
        with self.state_db.connect() as conn:
            run_row = conn.execute(
                """
                SELECT status, close_reason
                FROM builder_runs
                WHERE run_kind = 'webhook:discord_interaction'
                ORDER BY opened_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(run_row)
        self.assertEqual(run_row["status"], "closed")
        self.assertEqual(run_row["close_reason"], "discord_interaction_processed")
        events = latest_events_by_type(self.state_db, event_type="delivery_succeeded", limit=10)
        discord_events = [event for event in events if event.get("component") == "discord_webhook"]
        self.assertTrue(discord_events)
        facts = discord_events[0]["facts_json"]
        self.assertEqual(facts["bridge_mode"], "external_autodiscovered")
        self.assertEqual(facts["keepability"], "ephemeral_context")
        self.assertEqual(facts["promotion_disposition"], "not_promotable")
        self.assertTrue(facts["text_mutated"])
        self.assertIn("truncate_reply", facts["mutation_actions"])
        self.assertNotEqual(facts["raw_text_ref"], facts["mutated_text_ref"])

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
        with self.state_db.connect() as conn:
            run_row = conn.execute(
                """
                SELECT status, close_reason
                FROM builder_runs
                WHERE run_kind = 'webhook:discord_message'
                ORDER BY opened_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(run_row)
        self.assertEqual(run_row["status"], "closed")
        self.assertEqual(run_row["close_reason"], "discord_webhook_processed")
