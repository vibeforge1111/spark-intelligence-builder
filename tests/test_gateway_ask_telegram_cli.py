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

    def _stdio_request(self, **overrides: object) -> dict[str, object]:
        request: dict[str, object] = {
            "protocol": "spark.gateway.stdio.v2",
            "command": "telegram_update",
            "request_id": "telegram:test:1",
            "session_id": "session-test-1234567890",
            "session_token": "t" * 48,
            "update_payload": {"message": {"text": "status"}},
        }
        request.update(overrides)
        return request

    def _run_stdio(self, *requests: dict[str, object]) -> tuple[int, list[dict[str, object]]]:
        input_stream = StringIO("".join(json.dumps(request) + "\n" for request in requests))
        output_stream = StringIO()
        exit_code = gateway_serve_stdio(
            self.config_manager,
            self.state_db,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=StringIO(),
            simulation=False,
            session_token="t" * 48,
            session_id="session-test-1234567890",
        )
        return exit_code, [json.loads(line) for line in output_stream.getvalue().splitlines()]

    def test_gateway_stdio_v2_binds_runtime_origin_to_parent_session(self) -> None:
        with patch(
            "spark_intelligence.gateway.runtime.simulate_telegram_update",
            return_value=SimpleNamespace(
                ok=True,
                decision="answered",
                detail={"response_text": "Spark is connected.", "bridge_mode": "direct"},
            ),
        ) as simulate:
            exit_code, lines = self._run_stdio(
                self._stdio_request(simulation=True),
                self._stdio_request(
                    command="shutdown",
                    request_id="telegram:test:shutdown",
                    update_payload=None,
                ),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(lines[0]["protocol"], "spark.gateway.stdio.v2")
        self.assertEqual(lines[0]["session_id"], "session-test-1234567890")
        self.assertEqual(lines[1]["request_id"], "telegram:test:1")
        self.assertEqual(lines[1]["decision"], "answered")
        self.assertEqual(lines[2]["status"], "shutdown")
        self.assertEqual(simulate.call_args.kwargs["simulation"], False)

    def test_gateway_stdio_rejects_wrong_session_without_running_turn(self) -> None:
        with patch("spark_intelligence.gateway.runtime.simulate_telegram_update") as simulate:
            _, lines = self._run_stdio(self._stdio_request(session_token="wrong" * 12))

        self.assertEqual(lines[1]["error"]["code"], "unauthorized")
        self.assertNotIn("detail", lines[1]["error"])
        simulate.assert_not_called()

    def test_gateway_stdio_returns_safe_error_code_without_exception_text(self) -> None:
        with patch(
            "spark_intelligence.gateway.runtime.simulate_telegram_update",
            side_effect=RuntimeError("secret-token-value from /private/runtime/path"),
        ):
            _, lines = self._run_stdio(self._stdio_request())

        rendered = json.dumps(lines[1])
        self.assertEqual(lines[1]["error"]["code"], "turn_failed")
        self.assertNotIn("secret-token-value", rendered)
        self.assertNotIn("/private/runtime/path", rendered)

    def test_gateway_stdio_rejects_oversized_request_before_json_decode(self) -> None:
        input_stream = StringIO("x" * 2049 + "\n")
        output_stream = StringIO()
        exit_code = gateway_serve_stdio(
            self.config_manager,
            self.state_db,
            input_stream=input_stream,
            output_stream=output_stream,
            simulation=False,
            session_token="t" * 48,
            session_id="session-test-1234567890",
            max_request_bytes=2048,
        )

        lines = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(lines[1]["error"]["code"], "request_too_large")
