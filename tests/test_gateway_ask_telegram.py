from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.gateway.runtime import gateway_ask_telegram

from tests.test_support import SparkTestCase


class GatewayAskTelegramTests(SparkTestCase):
    def test_gateway_ask_telegram_uses_single_allowed_user_and_formats_reply(self) -> None:
        self.add_telegram_channel(allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        simulated_result = SimpleNamespace(
            ok=True,
            decision="allowed",
            detail={
                "bridge_mode": "researcher_advisory",
                "routing_decision": "stay_builder",
                "trace_ref": "trace-123",
                "response_text": "Spark reply text",
            },
        )

        with patch(
            "spark_intelligence.gateway.runtime.simulate_telegram_update",
            return_value=simulated_result,
        ) as simulate:
            output = gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="What are you connected to right now?",
            )

        payload = simulate.call_args.kwargs["update_payload"]
        self.assertEqual(payload["message"]["from"]["id"], "111")
        self.assertEqual(payload["message"]["chat"]["id"], "111")
        self.assertEqual(payload["message"]["text"], "What are you connected to right now?")
        self.assertIn("Telegram direct ask", output)
        self.assertIn("- user: 111", output)
        self.assertIn("- decision: allowed", output)
        self.assertIn("- mode: researcher_advisory", output)
        self.assertIn("- route: stay_builder", output)
        self.assertIn("Spark reply text", output)

    def test_gateway_ask_telegram_prefers_latest_recent_telegram_user(self) -> None:
        self.add_telegram_channel(allowed_users=["111", "222"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        simulated_result = SimpleNamespace(
            ok=True,
            decision="allowed",
            detail={"response_text": "Spark reply text"},
        )

        with (
            patch(
                "spark_intelligence.gateway.runtime.read_gateway_traces",
                return_value=[{"channel_id": "telegram", "external_user_id": "222"}],
            ),
            patch("spark_intelligence.gateway.runtime.read_outbound_audit", return_value=[]),
            patch(
                "spark_intelligence.gateway.runtime.simulate_telegram_update",
                return_value=simulated_result,
            ) as simulate,
        ):
            output = gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="hello",
                as_json=True,
            )

        payload = simulate.call_args.kwargs["update_payload"]
        self.assertEqual(payload["message"]["from"]["id"], "222")
        rendered = json.loads(output)
        self.assertEqual(rendered["user_id"], "222")
        self.assertEqual(rendered["message"], "hello")
        self.assertEqual(rendered["result"]["decision"], "allowed")

    def test_gateway_ask_telegram_requires_explicit_user_when_multiple_candidates_exist(self) -> None:
        self.add_telegram_channel(allowed_users=["111", "222"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)

        with (
            patch("spark_intelligence.gateway.runtime.read_gateway_traces", return_value=[]),
            patch("spark_intelligence.gateway.runtime.read_outbound_audit", return_value=[]),
        ):
            with self.assertRaisesRegex(ValueError, "multiple possible Telegram users"):
                gateway_ask_telegram(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    message="hello",
                )

    def test_gateway_ask_telegram_fails_closed_when_bridge_is_not_enabled(self) -> None:
        self.add_telegram_channel(allowed_users=["111"])

        with self.assertRaisesRegex(ValueError, "Telegram terminal bridge is disabled"):
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="hello",
            )
