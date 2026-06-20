from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.gateway.runtime import gateway_serve_stdio
from spark_intelligence.observability.store import recent_tool_call_ledgers

from tests.test_support import SparkTestCase


class GatewayAskTelegramCliTests(SparkTestCase):
    def _tool_ledger_row(self) -> dict[str, object]:
        return {
            "ledger_id": "ledger:stdio-ingest",
            "turn_id": "turn:stdio-ingest",
            "action_id": "action:stdio-ingest",
            "capability_id": "capability:stdio-ingest",
            "authorization_decision_id": "decision:stdio-ingest",
            "tool_name": "test.tool",
            "surface": "gateway_stdio_test",
            "status": "success",
            "ledger_json": {
                "schema_version": "tool-call-ledger-v1",
                "ledger_id": "ledger:stdio-ingest",
                "turn_id": "turn:stdio-ingest",
                "action_id": "action:stdio-ingest",
                "capability_id": "capability:stdio-ingest",
                "tool_name": "test.tool",
                "authorization": {"decision_id": "decision:stdio-ingest"},
                "result": {"status": "success", "summary": "Recorded from stdio."},
                "trace": {"id": "trace:stdio-ingest"},
            },
        }

    def test_gateway_ingest_tool_ledger_cli_persists_canonical_row(self) -> None:
        ledger_file = self.home / "tool-ledger.json"
        ledger_file.write_text(json.dumps(self._tool_ledger_row()), encoding="utf-8")

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "ingest-tool-ledger",
            str(ledger_file),
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["ledger_id"], "ledger:stdio-ingest")
        self.assertEqual(payload["turn_id"], "turn:stdio-ingest")
        records = recent_tool_call_ledgers(self.state_db, turn_id="turn:stdio-ingest")
        self.assertEqual(records[0]["surface"], "gateway_stdio_test")

    def test_gateway_stdio_worker_ingests_tool_ledger_lines(self) -> None:
        input_stream = StringIO(
            "\n".join(
                [
                    json.dumps(
                        {
                            "request_id": "req-ledger",
                            "command": "ingest_tool_ledger",
                            "row": self._tool_ledger_row(),
                        }
                    ),
                    json.dumps({"request_id": "req-stop", "command": "shutdown"}),
                    "",
                ]
            )
        )
        output_stream = StringIO()

        exit_code = gateway_serve_stdio(
            self.config_manager,
            self.state_db,
            input_stream=input_stream,
            output_stream=output_stream,
            simulation=True,
        )

        lines = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(lines[1]["request_id"], "req-ledger")
        self.assertEqual(lines[1]["status"], "ingested")
        self.assertEqual(lines[1]["turn_id"], "turn:stdio-ingest")
        records = recent_tool_call_ledgers(self.state_db, turn_id="turn:stdio-ingest")
        self.assertEqual(records[0]["ledger_id"], "ledger:stdio-ingest")

    def test_gateway_stdio_worker_rejects_unbound_tool_ledgers(self) -> None:
        row = self._tool_ledger_row()
        row.pop("turn_id")
        row["ledger_json"] = {}
        input_stream = StringIO(
            "\n".join(
                [
                    json.dumps(
                        {
                            "request_id": "req-ledger",
                            "command": "ingest_tool_ledger",
                            "row": row,
                        }
                    ),
                    json.dumps({"request_id": "req-stop", "command": "shutdown"}),
                    "",
                ]
            )
        )
        output_stream = StringIO()

        exit_code = gateway_serve_stdio(
            self.config_manager,
            self.state_db,
            input_stream=input_stream,
            output_stream=output_stream,
            simulation=True,
        )

        lines = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertFalse(lines[1]["ok"])
        self.assertIn("turn_id", lines[1]["error"])
        self.assertEqual(recent_tool_call_ledgers(self.state_db, turn_id="turn:stdio-ingest"), [])

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
