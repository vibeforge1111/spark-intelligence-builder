from __future__ import annotations

import json
import os
from unittest.mock import patch

from tests.test_support import SparkTestCase, create_fake_hook_chip


class BootstrapInstallerTests(SparkTestCase):
    def test_bootstrap_telegram_agent_guide_lists_provider_and_attachment_choices(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        path_root = self.home / "specialization-path-startup-operator"
        path_root.mkdir(parents=True, exist_ok=True)
        (path_root / "specialization-path.json").write_text(
            json.dumps({"pathKey": "startup-operator"}),
            encoding="utf-8",
        )
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.specialization_paths.roots", [str(path_root)])

        exit_code, stdout, stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--guide",
        )

        self.assertIn(exit_code, (0, 1), stderr)
        self.assertIn("Spark Intelligence install guide: telegram-agent", stdout)
        self.assertIn("minimax", stdout)
        self.assertIn("startup-yc", stdout)
        self.assertIn("startup-operator", stdout)
        self.assertIn("@BotFather", stdout)
        self.assertIn("existing_bot_example", stdout)

    def test_bootstrap_telegram_agent_can_activate_chips_and_path_during_install(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        path_root = self.home / "specialization-path-startup-operator"
        path_root.mkdir(parents=True, exist_ok=True)
        (path_root / "specialization-path.json").write_text(
            json.dumps({"pathKey": "startup-operator"}),
            encoding="utf-8",
        )

        exit_code, stdout, stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--researcher-root",
            str(researcher_root),
            "--researcher-config",
            str(researcher_config),
            "--provider",
            "custom",
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
            "--bot-token",
            "telegram-test-token",
            "--chip-root",
            str(chip_root),
            "--path-root",
            str(path_root),
            "--activate-chip",
            "startup-yc",
            "--pin-chip",
            "startup-yc",
            "--set-path",
            "startup-operator",
            "--skip-validate",
        )

        self.assertIn(exit_code, (0, 1), stderr)
        self.assertIn("Spark Intelligence bootstrap: telegram-agent", stdout)
        self.assertIn("- active_chip_keys: startup-yc", stdout)
        self.assertIn("- pinned_chip_keys: startup-yc", stdout)
        self.assertIn("- active_path_key: startup-operator", stdout)
        self.assertEqual(self.config_manager.get_path("spark.chips.active_keys"), ["startup-yc"])
        self.assertEqual(self.config_manager.get_path("spark.chips.pinned_keys"), ["startup-yc"])
        self.assertEqual(self.config_manager.get_path("spark.specialization_paths.active_path_key"), "startup-operator")

    def test_bootstrap_telegram_agent_can_configure_fallback_provider(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        exit_code, stdout, stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--researcher-root",
            str(researcher_root),
            "--researcher-config",
            str(researcher_config),
            "--provider",
            "minimax",
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
            "--fallback-provider",
            "anthropic",
            "--fallback-api-key",
            "anthropic-secret",
            "--fallback-model",
            "claude-opus-4-6",
            "--bot-token",
            "telegram-test-token",
            "--skip-validate",
        )

        self.assertIn(exit_code, (0, 1), stderr)
        self.assertIn("- primary_provider: minimax", stdout)
        self.assertIn("- fallback_provider: anthropic", stdout)
        self.assertEqual(self.config_manager.get_path("providers.default_provider"), "minimax")
        self.assertEqual(self.config_manager.get_path("providers.fallback_provider"), "anthropic")

    def test_bootstrap_telegram_agent_rejects_same_fallback_provider(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        exit_code, stdout, stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--researcher-root",
            str(researcher_root),
            "--researcher-config",
            str(researcher_config),
            "--provider",
            "minimax",
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
            "--fallback-provider",
            "minimax",
            "--bot-token",
            "telegram-test-token",
            "--skip-validate",
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("Fallback provider must differ from the primary provider.", stderr)

    def test_bootstrap_telegram_agent_interactive_guides_provider_bot_and_attachment_setup(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        path_root = self.home / "specialization-path-startup-operator"
        path_root.mkdir(parents=True, exist_ok=True)
        (path_root / "specialization-path.json").write_text(
            json.dumps({"pathKey": "startup-operator"}),
            encoding="utf-8",
        )

        answers = [
            "minimax",
            "env",
            "MINIMAX_API_KEY",
            "MiniMax-M2.7",
            "https://api.minimax.io/v1",
            "anthropic",
            "env",
            "ANTHROPIC_API_KEY",
            "claude-opus-4-6",
            "",
            "pairing",
            "new",
            "env",
            "TELEGRAM_BOT_TOKEN",
            "",
            "startup-yc",
            "startup-yc",
            "startup-operator",
        ]
        with patch.dict(
            os.environ,
            {
                "MINIMAX_API_KEY": "minimax-secret",
                "ANTHROPIC_API_KEY": "anthropic-secret",
                "TELEGRAM_BOT_TOKEN": "telegram-test-token",
            },
            clear=False,
        ):
            with patch("builtins.input", side_effect=answers):
                exit_code, stdout, stderr = self.run_cli(
                    "bootstrap",
                    "telegram-agent",
                    "--home",
                    str(self.home),
                    "--researcher-root",
                    str(researcher_root),
                    "--researcher-config",
                    str(researcher_config),
                    "--chip-root",
                    str(chip_root),
                    "--path-root",
                    str(path_root),
                    "--interactive",
                    "--skip-validate",
                )

        self.assertIn(exit_code, (0, 1), stderr)
        self.assertIn("Spark Intelligence bootstrap: telegram-agent", stdout)
        self.assertIn("- primary_provider: minimax", stdout)
        self.assertIn("- fallback_provider: anthropic", stdout)
        self.assertIn("- active_chip_keys: startup-yc", stdout)
        self.assertIn("- pinned_chip_keys: startup-yc", stdout)
        self.assertIn("- active_path_key: startup-operator", stdout)
        self.assertEqual(self.config_manager.get_path("providers.default_provider"), "minimax")
        self.assertEqual(self.config_manager.get_path("providers.fallback_provider"), "anthropic")
        self.assertEqual(self.config_manager.get_path("spark.chips.active_keys"), ["startup-yc"])
        self.assertEqual(self.config_manager.get_path("spark.chips.pinned_keys"), ["startup-yc"])
        self.assertEqual(self.config_manager.get_path("spark.specialization_paths.active_path_key"), "startup-operator")

    def test_bootstrap_telegram_agent_interactive_accepts_direct_secrets(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        answers = [
            "custom",
            "direct",
            "MiniMax-M2.7",
            "https://api.minimax.io/v1",
            "",
            "allowlist",
            "existing",
            "direct",
            "12345,67890",
            "",
            "",
            "",
        ]
        with patch("builtins.input", side_effect=answers):
            with patch("getpass.getpass", side_effect=["minimax-secret", "telegram-test-token"]):
                exit_code, stdout, stderr = self.run_cli(
                    "bootstrap",
                    "telegram-agent",
                    "--home",
                    str(self.home),
                    "--researcher-root",
                    str(researcher_root),
                    "--researcher-config",
                    str(researcher_config),
                    "--interactive",
                    "--skip-validate",
                )

        self.assertIn(exit_code, (0, 1), stderr)
        self.assertIn("- primary_provider: custom", stdout)
        self.assertIn("- fallback_provider: none", stdout)
        self.assertEqual(self.config_manager.get_path("providers.default_provider"), "custom")
        self.assertIsNone(self.config_manager.get_path("providers.fallback_provider"))
        channel_record = self.config_manager.get_path("channels.records.telegram")
        self.assertEqual(channel_record.get("pairing_mode"), "allowlist")
        self.assertEqual(channel_record.get("allowed_users"), ["12345", "67890"])
