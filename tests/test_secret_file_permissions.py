from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.config.loader import ConfigManager

from tests.test_support import SparkTestCase


class SecretFilePermissionTests(SparkTestCase):
    def test_env_secret_values_round_trip_without_injecting_new_keys(self) -> None:
        value = '  space # marker "quote" \\ slash\nINJECTED_SECRET=wrong\r\nunicode-雪  '

        self.config_manager.upsert_env_secret("ROUND_TRIP_SECRET", value)

        env_map = self.config_manager.read_env_map()
        self.assertEqual(env_map["ROUND_TRIP_SECRET"], value)
        self.assertNotIn("INJECTED_SECRET", env_map)
        content = self.config_manager.paths.env_file.read_text(encoding="utf-8")
        self.assertEqual(len(content.splitlines()), 2)

    def test_env_secret_keys_reject_invalid_and_process_control_names(self) -> None:
        for key in ("", "1BAD", "BAD-NAME", "BAD\nINJECTED", "PATH", "Path", "LANG", "LD_PRELOAD"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.config_manager.upsert_env_secret(key, "secret")

    def test_env_secret_creation_is_owner_only_before_post_write_hardening(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode proof; Windows uses ACL hardening")
        env_path = self.config_manager.paths.env_file
        env_path.unlink(missing_ok=True)

        with patch.object(ConfigManager, "harden_env_file_permissions", lambda self: None):
            previous_umask = os.umask(0o022)
            try:
                self.config_manager.upsert_env_secret("PRIVATE_FROM_CREATE", "secret")
            finally:
                os.umask(previous_umask)

        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_env_secret_atomic_replace_failure_preserves_previous_file(self) -> None:
        self.config_manager.upsert_env_secret("EXISTING_SECRET", "before")
        before = self.config_manager.paths.env_file.read_bytes()

        with patch("spark_intelligence.config.loader.os.replace", side_effect=OSError("replace stopped")):
            with self.assertRaisesRegex(OSError, "replace stopped"):
                self.config_manager.upsert_env_secret("SECOND_SECRET", "after")

        self.assertEqual(self.config_manager.paths.env_file.read_bytes(), before)
        self.assertEqual(self.config_manager.read_env_map(), {"EXISTING_SECRET": "before"})
        self.assertEqual(
            list(self.config_manager.paths.env_file.parent.glob(f".{self.config_manager.paths.env_file.name}.*")),
            [],
        )

    def test_env_secret_write_rejects_symlink_without_touching_target(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX symlink proof")
        env_path = self.config_manager.paths.env_file
        env_path.unlink(missing_ok=True)
        victim = self.home / "victim.env"
        victim.write_text("VICTIM=before\n", encoding="utf-8")
        env_path.symlink_to(victim)

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.config_manager.upsert_env_secret("SAFE_SECRET", "after")

        self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM=before\n")
        self.assertTrue(env_path.is_symlink())
        self.assertEqual(
            self.config_manager.env_file_permission_status(),
            (False, "symbolic-link-rejected"),
        )

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

    @patch.object(ConfigManager, "_harden_windows_env_file_permissions")
    def test_bootstrap_soft_fails_windows_env_acl_hardening(self, mock_harden) -> None:
        mock_harden.side_effect = OSError("acl unavailable")
        config_manager = ConfigManager.from_home(str(self.home / "windows-acl-unavailable"))

        with patch("spark_intelligence.config.loader.os.name", "nt"):
            config_manager.bootstrap()

        self.assertTrue(config_manager.paths.env_file.exists())

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

    @patch("spark_intelligence.config.loader.os.name", "nt")
    @patch.object(ConfigManager, "_harden_windows_env_file_permissions")
    def test_upsert_env_secret_keeps_windows_acl_hardening_strict(self, mock_harden) -> None:
        mock_harden.side_effect = OSError("acl unavailable")

        with self.assertRaises(OSError):
            self.config_manager.upsert_env_secret("TELEGRAM_BOT_TOKEN", "secret")

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

        self.assertIn(exit_code, (0, 1), f"{stderr}\n{stdout}")
        self.assertIn("- [ok] .env-permissions:", stdout)

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

    @patch("spark_intelligence.config.loader.subprocess.run", side_effect=RuntimeError("programming bug"))
    def test_windows_current_principal_does_not_hide_programming_errors(self, _mock_run) -> None:
        with self.assertRaisesRegex(RuntimeError, "programming bug"):
            ConfigManager._windows_current_principal()
