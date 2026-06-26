from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from spark_intelligence.adapters.telegram.runtime import poll_telegram_updates_once, read_telegram_runtime_health
from spark_intelligence.gateway.runtime import gateway_outbound_view, gateway_start, gateway_trace_view

from tests.test_support import SparkTestCase, make_telegram_update


class FakePollingClient:
    def __init__(self, *, updates: list[dict[str, object]] | None = None, update_error: Exception | None = None):
        self.updates = updates or []
        self.update_error = update_error
        self.sent_messages: list[dict[str, str]] = []
        self.sent_voices: list[dict[str, object]] = []
        self.sent_documents: list[dict[str, object]] = []

    def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
        if self.update_error is not None:
            raise self.update_error
        return self.updates

    def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    def send_document(
        self,
        *,
        chat_id: str,
        document_bytes: bytes,
        filename: str,
        caption: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, object]:
        self.sent_documents.append(
            {
                "chat_id": chat_id,
                "document_bytes": document_bytes,
                "filename": filename,
                "caption": caption,
                "mime_type": mime_type,
            }
        )
        return {"ok": True}

    def send_voice(
        self,
        *,
        chat_id: str,
        voice_bytes: bytes,
        filename: str,
        caption: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, object]:
        self.sent_voices.append(
            {
                "chat_id": chat_id,
                "voice_bytes": voice_bytes,
                "filename": filename,
                "caption": caption,
                "mime_type": mime_type,
            }
        )
        return {"ok": True}


class TelegramFailurePathTests(SparkTestCase):
    def test_gateway_start_persists_auth_failure(self) -> None:
        self.add_telegram_channel(bot_token="bad-token")

        class FailingAuthClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                raise RuntimeError("unauthorized token")

        with patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", FailingAuthClient):
            report = gateway_start(
                self.config_manager,
                self.state_db,
                once=True,
            )

        health = read_telegram_runtime_health(self.state_db)
        self.assertFalse(report.ok)
        self.assertIn("auth check failed", report.text)
        self.assertEqual(health.auth_status, "failed")
        self.assertIn("unauthorized token", str(health.auth_error))

    def test_gateway_start_persists_poll_failure(self) -> None:
        self.add_telegram_channel(bot_token="good-token")

        class PollFailingClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                return {"result": {"username": "sparkbot"}}

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                raise URLError("offline")

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                return {"ok": True}

        with patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", PollFailingClient):
            report = gateway_start(
                self.config_manager,
                self.state_db,
                once=True,
            )

        health = read_telegram_runtime_health(self.state_db)
        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                event="telegram_poll_failure",
                as_json=True,
            )
        )

        self.assertFalse(report.ok)
        self.assertIn("Telegram polling failure", report.text)
        self.assertEqual(health.auth_status, "ok")
        self.assertEqual(health.last_failure_type, "network_error")
        self.assertEqual(health.consecutive_failures, 1)
        self.assertEqual(health.last_backoff_seconds, 1)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["failure_type"], "network_error")

    def test_poll_telegram_updates_once_suppresses_duplicates(self) -> None:
        self.add_telegram_channel()
        update = make_telegram_update(
            update_id=501,
            user_id="111",
            username="alice",
            text="hello",
        )
        client = FakePollingClient(updates=[update, update])

        result = poll_telegram_updates_once(
            config_manager=self.config_manager,
            state_db=self.state_db,
            client=client,
            timeout_seconds=0,
        )

        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                event="telegram_update_duplicate",
                as_json=True,
            )
        )

        self.assertEqual(result.fetched_update_count, 2)
        self.assertEqual(result.ignored_count, 1)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["update_id"], 501)

    def test_poll_telegram_updates_once_rate_limits_repeated_sender(self) -> None:
        self.add_telegram_channel()
        self.config_manager.set_path("security.telegram.max_messages_per_minute", 1)
        client = FakePollingClient(
            updates=[
                make_telegram_update(
                    update_id=601,
                    user_id="111",
                    username="alice",
                    text="hello",
                ),
                make_telegram_update(
                    update_id=602,
                    user_id="111",
                    username="alice",
                    text="hello again",
                ),
            ]
        )

        result = poll_telegram_updates_once(
            config_manager=self.config_manager,
            state_db=self.state_db,
            client=client,
            timeout_seconds=0,
        )

        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                event="telegram_rate_limited",
                user="111",
                as_json=True,
            )
        )
        outbound = json.loads(
            gateway_outbound_view(
                self.config_manager,
                limit=10,
                event="telegram_rate_limit_outbound",
                user="111",
                as_json=True,
            )
        )

        self.assertEqual(result.blocked_count, 1)
        self.assertEqual(len(traces), 1)
        self.assertTrue(traces[0]["notice_sent"])
        self.assertEqual(len(outbound), 1)
        self.assertTrue(outbound[0]["delivery_ok"])

    def test_poll_runtime_command_gateway_trace_carries_trace_and_delivery_proof(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        client = FakePollingClient(
            updates=[
                make_telegram_update(
                    update_id=701,
                    user_id="111",
                    username="alice",
                    text="/memory doctor help",
                )
            ]
        )

        result = poll_telegram_updates_once(
            config_manager=self.config_manager,
            state_db=self.state_db,
            client=client,
            timeout_seconds=0,
        )

        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                as_json=True,
            )
        )
        command_trace = next(row for row in traces if row.get("event") == "telegram_runtime_command_processed")
        update_trace = next(row for row in traces if row.get("event") == "telegram_update_processed")

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(command_trace["request_id"], "telegram:701")
        self.assertEqual(command_trace["trace_ref"], "trace:telegram:701")
        self.assertEqual(update_trace["request_id"], "telegram:701")
        self.assertEqual(update_trace["trace_ref"], "trace:telegram:701")
        self.assertRegex(command_trace["harnessProofRef"], r"^turn:sha256:[a-f0-9]{16}$")
        self.assertEqual(update_trace["harnessProofRef"], command_trace["harnessProofRef"])
        self.assertEqual(command_trace["proofCapsule"]["schema"], "spark.harness_proof.v1")
        self.assertEqual(command_trace["proofCapsule"]["authority"]["contract"], "spark.turn_intent.v1")
        self.assertEqual(command_trace["proofCapsule"]["governor"]["verified"], True)
        self.assertEqual(command_trace["proofCapsule"]["joins"]["telegram"], "joined")
        self.assertEqual(command_trace["proofCapsule"]["joins"]["builder"], "joined")
        self.assertEqual(update_trace["proofCapsule"]["turnRef"], command_trace["harnessProofRef"])
        self.assertNotIn("proofStatus", command_trace)
        self.assertNotIn("proofStorage", command_trace)
        self.assertNotIn("proofStatus", update_trace)
        self.assertNotIn("proofStorage", update_trace)

    def test_poll_agent_onboarding_gateway_trace_carries_trace_and_delivery_proof(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        client = FakePollingClient(
            updates=[
                make_telegram_update(
                    update_id=702,
                    user_id="111",
                    username="alice",
                    text="hello",
                )
            ]
        )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.maybe_handle_agent_persona_onboarding_turn",
            return_value=SimpleNamespace(
                human_id="human:telegram:111",
                agent_id="agent:telegram:111",
                step="awaiting_agent_name",
                reply_text="Let's set up your agent.",
                agent_name="",
                persona_profile={},
                completed=False,
            ),
        ):
            result = poll_telegram_updates_once(
                config_manager=self.config_manager,
                state_db=self.state_db,
                client=client,
                timeout_seconds=0,
            )

        traces = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                as_json=True,
            )
        )
        onboarding_trace = next(row for row in traces if row.get("event") == "telegram_agent_onboarding_processed")

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(onboarding_trace["request_id"], "telegram:702")
        self.assertEqual(onboarding_trace["trace_ref"], "trace:telegram:702")
        self.assertRegex(onboarding_trace["harnessProofRef"], r"^turn:sha256:[a-f0-9]{16}$")
        self.assertEqual(onboarding_trace["proofCapsule"]["schema"], "spark.harness_proof.v1")
        self.assertEqual(onboarding_trace["proofCapsule"]["turnRef"], onboarding_trace["harnessProofRef"])
        self.assertEqual(onboarding_trace["proofCapsule"]["authority"]["contract"], "spark.turn_intent.v1")
        self.assertEqual(onboarding_trace["proofCapsule"]["governor"]["verified"], True)
        self.assertEqual(onboarding_trace["proofCapsule"]["joins"]["telegram"], "joined")
        self.assertEqual(onboarding_trace["proofCapsule"]["joins"]["builder"], "joined")
        self.assertNotIn("proofStatus", onboarding_trace)
        self.assertNotIn("proofStorage", onboarding_trace)
