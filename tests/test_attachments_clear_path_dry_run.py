from __future__ import annotations

from tests.test_support import SparkTestCase


class AttachmentsClearPathDryRunTests(SparkTestCase):
    def test_dry_run_reports_path_without_clearing_or_writing_snapshot(self) -> None:
        self.config_manager.set_path("spark.specialization_paths.active_path_key", "researcher")
        snapshot = self.home / "state" / "attachments.snapshot.json"

        exit_code, stdout, stderr = self.run_cli(
            "attachments",
            "clear-path",
            "--home",
            str(self.home),
            "--dry-run",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Active path would be cleared: researcher", stdout)
        self.assertEqual(
            self.config_manager.get_path("spark.specialization_paths.active_path_key"),
            "researcher",
        )
        self.assertFalse(snapshot.exists())

    def test_dry_run_reports_none_for_an_unconfigured_path(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "attachments",
            "clear-path",
            "--home",
            str(self.home),
            "--dry-run",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Active path would be cleared: none", stdout)
