from __future__ import annotations

import pytest

from spark_intelligence import __version__
from spark_intelligence.cli import build_parser

from tests.test_support import SparkTestCase


class CliGuidanceLayoutTests(SparkTestCase):
    def test_setup_separates_action_sections(self) -> None:
        exit_code, stdout, stderr = self.run_cli("setup", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("\n\nNext steps:\n", stdout)
        self.assertIn("\n\nOptional Spark hookups:\n", stdout)

    def test_telegram_add_separates_next_steps(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "telegram",
            "--home",
            str(self.home),
            "--bot-token",
            "test-token",
            "--skip-validate",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("\n\nNext Telegram steps:\n", stdout)



def test_global_version_action_reports_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["--version"])

    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == f"spark-intelligence {__version__}"
