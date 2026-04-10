from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.memory import TelegramStateKnowledgeBaseResult

from tests.test_support import SparkTestCase


class MemoryCliKnowledgeBaseTests(SparkTestCase):
    def test_memory_compile_telegram_kb_dispatches_builder_state_compile(self) -> None:
        output_dir = self.home / "artifacts" / "spark-memory-kb"
        write_path = self.home / "artifacts" / "spark-memory-kb.json"
        payload = {
            "builder_home": str(self.home),
            "summary": {
                "selected_chat_id": "12345",
                "conversation_count": 1,
                "accepted_writes": 2,
                "rejected_writes": 0,
                "skipped_turns": 0,
                "kb_valid": True,
            },
            "health_report": {"valid": True, "errors": []},
            "compile_result": {"filed_output_count": 4},
        }

        with patch(
            "spark_intelligence.cli.build_telegram_state_knowledge_base",
            return_value=TelegramStateKnowledgeBaseResult(output_dir=output_dir, payload=payload),
        ) as compile_kb:
            exit_code, stdout, stderr = self.run_cli(
                "memory",
                "compile-telegram-kb",
                "--home",
                str(self.home),
                "--output-dir",
                str(output_dir),
                "--limit",
                "12",
                "--chat-id",
                "12345",
                "--repo-source",
                "README.md",
                "--repo-source-manifest",
                "repo-sources.json",
                "--write",
                str(write_path),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["summary"]["selected_chat_id"], "12345")
        compile_kb.assert_called_once()
        kwargs = compile_kb.call_args.kwargs
        self.assertEqual(kwargs["config_manager"].paths.home, Path(self.home))
        self.assertEqual(kwargs["output_dir"], str(output_dir))
        self.assertEqual(kwargs["limit"], 12)
        self.assertEqual(kwargs["chat_id"], "12345")
        self.assertEqual(kwargs["repo_sources"], ["README.md"])
        self.assertEqual(kwargs["repo_source_manifest_files"], ["repo-sources.json"])
        self.assertEqual(kwargs["write_path"], str(write_path))
        self.assertIsNone(kwargs["validator_root"])
