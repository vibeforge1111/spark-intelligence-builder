from __future__ import annotations

from unittest.mock import patch

from tests.test_support import SparkTestCase


class GatewayAskTelegramCliTests(SparkTestCase):
    def test_gateway_ask_telegram_command_prints_runtime_reply(self) -> None:
        with patch(
            "spark_intelligence.cli.gateway_ask_telegram",
            return_value="Telegram direct ask\n- user: 111\n\nSpark reply text",
        ) as ask_telegram:
            exit_code, stdout, stderr = self.run_cli(
                "gateway",
                "ask-telegram",
                "What are you connected to right now?",
                "--home",
                str(self.home),
                "--user-id",
                "111",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(stdout.strip(), "Telegram direct ask\n- user: 111\n\nSpark reply text")
        self.assertEqual(stderr, "")
        self.assertEqual(ask_telegram.call_args.kwargs["message"], "What are you connected to right now?")
        self.assertEqual(ask_telegram.call_args.kwargs["user_id"], "111")
        self.assertEqual(ask_telegram.call_args.kwargs["as_json"], False)
