from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.config.loader import ConfigManager

from tests.test_support import SparkTestCase


class SecretFilePermissionTests(SparkTestCase):
    @patch("spark_intelligence.config.loader.subprocess.run")
    def test_bootstrap_hardens_windows_env_acl(self, mock_run) -> None:
        config_manager = ConfigManager.from_home(str(self.home / "windows-home"))
        config_manager.bootstrap()

        if os.name == "nt":
            self.assertTrue(mock_run.called)
            command = mock_run.call_args[0][0]
            self.assertEqual(command[0], "icacls")
            self.assertEqual(Path(command[1]), config_manager.paths.env_file)
            self.assertIn("/inheritance:r", command)
        else:
            self.assertFalse(mock_run.called)

    @patch("spark_intelligence.config.loader.subprocess.run")
    def test_upsert_env_secret_reapplies_windows_acl(self, mock_run) -> None:
        self.config_manager.upsert_env_secret("TELEGRAM_BOT_TOKEN", "secret")

        if os.name == "nt":
            self.assertTrue(mock_run.called)
            command = mock_run.call_args[0][0]
            self.assertEqual(command[0], "icacls")
            self.assertEqual(Path(command[1]), self.config_manager.paths.env_file)
        else:
            self.assertFalse(mock_run.called)

    @patch("spark_intelligence.config.loader.subprocess.run")
    def test_env_file_permission_status_reports_owner_only_windows_acl(self, mock_run) -> None:
        self.config_manager.bootstrap()
        principal = self.config_manager._windows_current_principal()
        mock_run.return_value.stdout = (
            f"{self.config_manager.paths.env_file} {principal}:(R,W)\n"
            "Successfully processed 1 files; Failed processing 0 files\n"
        )

        ok, detail = self.config_manager.env_file_permission_status()

        if os.name == "nt":
            self.assertTrue(ok)
            self.assertIn(principal, detail)
        else:
            self.assertTrue(ok)

    def test_doctor_reports_env_permission_check(self) -> None:
        exit_code, stdout, stderr = self.run_cli("doctor", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn(".env-permissions", stdout)

    @patch.dict(os.environ, {"USERDOMAIN": "STALE_DOMAIN", "USERNAME": "STALE_USER"})
    @patch("spark_intelligence.config.loader.subprocess.run")
    def test_windows_current_principal_prefers_process_token_over_environment(self, mock_run) -> None:
        mock_run.return_value = SimpleNamespace(stdout="desktop-smvb6c0\\user\n")

        principal = ConfigManager._windows_current_principal()

        self.assertEqual(principal, "desktop-smvb6c0\\user")
        mock_run.assert_called_once_with(["whoami"], check=True, capture_output=True, text=True)

    @patch.dict(os.environ, {"USERDOMAIN": "DESKTOP-SMVB6C0", "USERNAME": "USER"})
    @patch("spark_intelligence.config.loader.subprocess.run")
    def test_windows_current_principal_falls_back_to_environment_when_whoami_fails(self, mock_run) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(1, ["whoami"])

        principal = ConfigManager._windows_current_principal()

        self.assertEqual(principal, "DESKTOP-SMVB6C0\\USER")
