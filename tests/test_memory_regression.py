from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.memory import TelegramMemoryRegressionResult, run_telegram_memory_regression

from tests.test_support import SparkTestCase


class MemoryRegressionTests(SparkTestCase):
    def test_memory_run_telegram_regression_dispatches_runner(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression"
        write_path = output_dir / "summary.json"
        payload = {
            "summary": {
                "case_count": 20,
                "matched_case_count": 20,
                "mismatched_case_count": 0,
                "selected_user_id": "12345",
                "selected_chat_id": "12345",
                "kb_has_probe_coverage": True,
                "kb_current_state_hits": 10,
                "kb_current_state_total": 10,
                "kb_evidence_hits": 10,
                "kb_evidence_total": 10,
            }
        }

        with patch(
            "spark_intelligence.cli.run_telegram_memory_regression",
            return_value=TelegramMemoryRegressionResult(output_dir=output_dir, payload=payload),
        ) as run_regression:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "run-telegram-regression",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--user-id",
                "12345",
                "--chat-id",
                "12345",
                "--kb-limit",
                "12",
                "--validator-root",
                "C:/validator",
                "--write",
                str(write_path),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["case_count"], 20)
        kwargs = run_regression.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["user_id"], "12345")
        self.assertEqual(kwargs["chat_id"], "12345")
        self.assertEqual(kwargs["kb_limit"], 12)
        self.assertEqual(kwargs["validator_root"], "C:/validator")
        self.assertEqual(kwargs["write_path"], str(write_path))

    def test_run_telegram_memory_regression_blocks_fast_when_user_is_not_paired(self) -> None:
        output_dir = self.home / "artifacts" / "telegram-memory-regression-blocked"
        unauthorized_payload = {
            "message": "My name is Sarah.",
            "user_id": "22345",
            "chat_id": "22345",
            "result": {
                "ok": False,
                "decision": "pending_pairing",
                "detail": {
                    "response_text": "Unauthorized DM. Pairing approval is required before this agent will respond.",
                },
            },
        }

        with patch(
            "spark_intelligence.gateway.runtime.gateway_ask_telegram",
            return_value=json.dumps(unauthorized_payload),
        ) as ask_telegram, patch(
            "spark_intelligence.memory.regression.build_telegram_state_knowledge_base",
        ) as compile_kb:
            result = run_telegram_memory_regression(
                config_manager=self.config_manager,
                state_db=self.state_db,
                output_dir=output_dir,
                user_id="22345",
                chat_id="22345",
            )

        payload = result.payload
        self.assertEqual(payload["summary"]["status"], "blocked_precondition")
        self.assertIn("pending_pairing", payload["summary"]["blocked_reason"])
        self.assertEqual(len(payload["cases"]), 1)
        ask_telegram.assert_called_once()
        compile_kb.assert_not_called()
