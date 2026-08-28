from __future__ import annotations

import json
import re
from unittest.mock import patch

from tests.test_support import SparkTestCase


class GatewayShadowTelegramPackProgressTests(SparkTestCase):
    """Regression coverage for the progress step counter in
    `handle_gateway_shadow_telegram_pack`. Each `gateway_ask_telegram` call is a
    full LLM gateway round-trip (seconds to tens of seconds per entry); without
    a per-entry stderr signal the operator has no progress visibility.
    """

    def _write_pack(self, messages: list[str]) -> str:
        pack_path = self.home / "shadow-pack.json"
        pack_path.write_text(json.dumps(messages), encoding="utf-8")
        return str(pack_path)

    def _fake_gateway_payload(self, message: str, index: int) -> str:
        return json.dumps(
            {
                "user_id": "u-1",
                "username": "operator",
                "chat_id": "c-1",
                "result": {"decision": "noop", "echo": message, "index": index},
            }
        )

    def test_handle_gateway_shadow_telegram_pack_emits_step_counter_per_entry(self) -> None:
        pack_path = self._write_pack(["msg one", "msg two", "msg three"])

        call_index = {"i": 0}

        def fake_ask(*, message: str, **kwargs: object) -> str:
            call_index["i"] += 1
            return self._fake_gateway_payload(message, call_index["i"])

        with patch("spark_intelligence.cli.gateway_ask_telegram", side_effect=fake_ask):
            exit_code, stdout, stderr = self.run_cli(
                "gateway",
                "shadow-telegram-pack",
                pack_path,
                "--home",
                str(self.home),
                "--user-id",
                "u-1",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        # The JSON payload on stdout is byte-identical to pre-PR shape: a
        # downstream `| jq` pipe consumes the same single payload.
        payload = json.loads(stdout)
        self.assertEqual(payload["ingress_owner"], "spark-telegram-bot")
        self.assertEqual(len(payload["results"]), 3)

        # The new progress signal: one stderr line per pack entry, in order.
        progress_lines = [
            line for line in stderr.splitlines()
            if re.match(r"^\[\d+/3\] running shadow Telegram pack entry\.\.\.$", line)
        ]
        self.assertEqual(
            progress_lines,
            [
                "[1/3] running shadow Telegram pack entry...",
                "[2/3] running shadow Telegram pack entry...",
                "[3/3] running shadow Telegram pack entry...",
            ],
        )
