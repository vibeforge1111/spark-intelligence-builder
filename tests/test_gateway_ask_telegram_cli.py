from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.gateway.runtime import gateway_serve_stdio

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

    def test_gateway_stdio_worker_serves_telegram_update_lines(self) -> None:
        input_stream = StringIO(
            "\n".join(
                [
                    json.dumps(
                        {
                            "request_id": "req-1",
                            "command": "simulate_telegram_update",
                            "simulation": False,
                            "update_payload": {"message": {"text": "status"}},
                        }
                    ),
                    json.dumps({"request_id": "req-stop", "command": "shutdown"}),
                    "",
                ]
            )
        )
        output_stream = StringIO()

        with patch(
            "spark_intelligence.gateway.runtime.simulate_telegram_update",
            return_value=SimpleNamespace(
                ok=True,
                decision="answered",
                detail={"response_text": "Spark is connected.", "bridge_mode": "direct"},
            ),
        ) as simulate:
            exit_code = gateway_serve_stdio(
                self.config_manager,
                self.state_db,
                input_stream=input_stream,
                output_stream=output_stream,
                simulation=True,
            )

        lines = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(lines[0]["protocol"], "spark.gateway.stdio.v1")
        self.assertEqual(lines[1]["request_id"], "req-1")
        self.assertEqual(lines[1]["decision"], "answered")
        self.assertEqual(lines[1]["detail"]["response_text"], "Spark is connected.")
        self.assertEqual(lines[2]["status"], "shutdown")
        self.assertEqual(simulate.call_args.kwargs["simulation"], False)
