from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory import build_telegram_state_knowledge_base

from tests.test_support import SparkTestCase


class TelegramStateKnowledgeBaseTests(SparkTestCase):
    def _assert_governed_cli_call(self, governed: object, *, command: list[str]) -> None:
        kwargs = governed.call_args.kwargs
        self.assertEqual(kwargs["command"], command)
        self.assertEqual(kwargs["cwd"], str(self.home))
        env = kwargs["env"]
        self.assertIsInstance(env, dict)
        pythonpath_parts = str(env.get("PYTHONPATH") or "").split(os.pathsep)
        self.assertEqual(pythonpath_parts[0], str((self.home / "src").resolve()))

    def test_build_telegram_state_knowledge_base_clears_stale_output_files_before_compile(self) -> None:
        output_dir = self.home / "artifacts" / "spark-memory-kb"
        stale_file = output_dir / "raw" / "repos" / "99-orphan.md"
        stale_file.parent.mkdir(parents=True, exist_ok=True)
        stale_file.write_text("stale", encoding="utf-8")

        with patch(
            "spark_intelligence.memory.knowledge_base.run_governed_command",
            return_value=SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {
                        "builder_home": str(self.home),
                        "summary": {"kb_valid": True},
                        "health_report": {"valid": True, "errors": []},
                    }
                ),
                stderr="",
            ),
        ):
            build_telegram_state_knowledge_base(
                config_manager=self.config_manager,
                output_dir=output_dir,
                validator_root=self.home,
            )

        self.assertFalse(stale_file.exists())

    def test_build_telegram_state_knowledge_base_invokes_domain_chip_memory_cli(self) -> None:
        output_dir = self.home / "artifacts" / "spark-memory-kb"
        write_path = self.home / "artifacts" / "spark-memory-kb.json"
        repo_root = Path(__file__).resolve().parents[1]

        with patch(
            "spark_intelligence.memory.knowledge_base.run_governed_command",
            return_value=SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {
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
                    }
                ),
                stderr="",
            ),
        ) as governed:
            result = build_telegram_state_knowledge_base(
                config_manager=self.config_manager,
                output_dir=output_dir,
                limit=12,
                chat_id="12345",
                repo_sources=["README.md"],
                repo_source_manifest_files=["repo-sources.json"],
                write_path=write_path,
                validator_root=self.home,
            )

        self.assertEqual(result.output_dir, output_dir)
        self.assertEqual(result.payload["summary"]["selected_chat_id"], "12345")
        self.assertIn("accepted_writes: 2", result.to_text())
        governed.assert_called_once()
        self._assert_governed_cli_call(
            governed,
            command=[
                sys.executable,
                "-m",
                "domain_chip_memory.cli",
                "run-spark-builder-state-telegram-intake",
                str(self.home),
                str(output_dir),
                "--limit",
                "12",
                "--chat-id",
                "12345",
                "--repo-source",
                str((repo_root / "README.md").resolve()),
                "--repo-source",
                str((repo_root / "docs" / "MEMORY_EXECUTION_PLAN_2026-04-10.md").resolve()),
                "--repo-source",
                str((repo_root / "docs" / "SPARK_MEMORY_KB_ROLLOUT_PLAN_2026-04-10.md").resolve()),
                "--repo-source",
                str((repo_root / "docs" / "MEMORY_TELEGRAM_HANDOFF_2026-04-10.md").resolve()),
                "--write",
                str(write_path),
            ],
        )

    def test_build_telegram_state_knowledge_base_reports_missing_validator_root(self) -> None:
        missing_root = self.home / "missing-domain-chip-memory"
        result = build_telegram_state_knowledge_base(
            config_manager=self.config_manager,
            validator_root=missing_root,
        )

        self.assertFalse(result.payload["valid"])
        self.assertEqual(result.payload["errors"], [f"validator_root_missing:{missing_root}"])

    def test_build_telegram_state_knowledge_base_uses_default_repo_source_manifest_when_none_provided(self) -> None:
        output_dir = self.home / "artifacts" / "spark-memory-kb"
        default_manifest = self.home / "docs" / "manifests" / "spark_memory_kb_repo_sources.json"
        default_manifest.parent.mkdir(parents=True, exist_ok=True)
        default_manifest.write_text('{"repo_sources":["../../README.md"]}', encoding="utf-8")

        with patch(
            "spark_intelligence.memory.knowledge_base.DEFAULT_BUILDER_KB_REPO_SOURCE_MANIFEST",
            default_manifest,
        ), patch(
            "spark_intelligence.memory.knowledge_base.run_governed_command",
            return_value=SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {
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
                    }
                ),
                stderr="",
            ),
        ) as governed:
            build_telegram_state_knowledge_base(
                config_manager=self.config_manager,
                output_dir=output_dir,
                limit=12,
                chat_id="12345",
                validator_root=self.home,
            )

        governed.assert_called_once()
        self._assert_governed_cli_call(
            governed,
            command=[
                sys.executable,
                "-m",
                "domain_chip_memory.cli",
                "run-spark-builder-state-telegram-intake",
                str(self.home),
                str(output_dir),
                "--limit",
                "12",
                "--chat-id",
                "12345",
                "--repo-source",
                str((self.home / "README.md").resolve()),
            ],
        )

    def test_build_telegram_state_knowledge_base_expands_default_manifest_when_explicit_repo_source_is_added(self) -> None:
        output_dir = self.home / "artifacts" / "spark-memory-kb"
        default_manifest = self.home / "docs" / "manifests" / "spark_memory_kb_repo_sources.json"
        default_manifest.parent.mkdir(parents=True, exist_ok=True)
        default_manifest.write_text('{"repo_sources":["../../README.md"]}', encoding="utf-8")

        with patch(
            "spark_intelligence.memory.knowledge_base.DEFAULT_BUILDER_KB_REPO_SOURCE_MANIFEST",
            default_manifest,
        ), patch(
            "spark_intelligence.memory.knowledge_base.run_governed_command",
            return_value=SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {
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
                    }
                ),
                stderr="",
            ),
        ) as governed:
            build_telegram_state_knowledge_base(
                config_manager=self.config_manager,
                output_dir=output_dir,
                limit=12,
                chat_id="12345",
                repo_sources=["regression-summary.md"],
                validator_root=self.home,
            )

        governed.assert_called_once()
        self._assert_governed_cli_call(
            governed,
            command=[
                sys.executable,
                "-m",
                "domain_chip_memory.cli",
                "run-spark-builder-state-telegram-intake",
                str(self.home),
                str(output_dir),
                "--limit",
                "12",
                "--chat-id",
                "12345",
                "--repo-source",
                str((Path(__file__).resolve().parents[1] / "regression-summary.md").resolve()),
                "--repo-source",
                str((self.home / "README.md").resolve()),
            ],
        )

    def test_build_telegram_state_knowledge_base_passes_absolute_builder_paths_to_domain_cli(self) -> None:
        relative_home = Path("relative-home-fixture")
        relative_output_dir = relative_home / "artifacts" / "spark-memory-kb"
        relative_write_path = relative_home / "artifacts" / "spark-memory-kb.json"
        config_manager = ConfigManager.from_home(str(relative_home))

        with patch(
            "spark_intelligence.memory.knowledge_base.run_governed_command",
            return_value=SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {
                        "builder_home": str(relative_home.resolve()),
                        "summary": {"kb_valid": True},
                        "health_report": {"valid": True, "errors": []},
                    }
                ),
                stderr="",
            ),
        ) as governed:
            build_telegram_state_knowledge_base(
                config_manager=config_manager,
                output_dir=relative_output_dir,
                write_path=relative_write_path,
                validator_root=self.home,
            )

        command = governed.call_args.kwargs["command"]
        self.assertEqual(command[4], str(relative_home.resolve()))
        self.assertEqual(command[5], str(relative_output_dir.resolve()))
        self.assertEqual(command[-1], str(relative_write_path.resolve()))

    def test_build_telegram_state_knowledge_base_reports_validator_timeout(self) -> None:
        with patch(
            "spark_intelligence.memory.knowledge_base.run_governed_command",
            side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=120),
        ):
            result = build_telegram_state_knowledge_base(
                config_manager=self.config_manager,
                validator_root=self.home,
                timeout_seconds=120,
            )

        self.assertFalse(result.payload["valid"])
        self.assertEqual(
            result.payload["errors"],
            ["validator_timeout:run-spark-builder-state-telegram-intake:120s"],
        )
