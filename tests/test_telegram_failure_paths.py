from __future__ import annotations

import json
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

    def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
        if self.update_error is not None:
            raise self.update_error
        return self.updates

    def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
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
        self.assertIn("auth check failed", report)
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

        self.assertIn("Telegram polling failure", report)
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
