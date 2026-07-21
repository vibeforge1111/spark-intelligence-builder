from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.config.loader import ConfigManager

from tests.test_support import SparkTestCase


class ConfigHomeResolutionTests(SparkTestCase):
    def test_builder_home_alias_is_used_when_primary_is_missing(self) -> None:
        alias_home = self.home / "builder-home"
        with patch.dict(
            "os.environ",
            {"SPARK_BUILDER_HOME": str(alias_home)},
            clear=True,
        ):
            manager = ConfigManager.from_home(None)

        self.assertEqual(manager.paths.home, alias_home)

    def test_whitespace_primary_home_falls_through_to_builder_alias(self) -> None:
        alias_home = self.home / "builder-home"
        with patch.dict(
            "os.environ",
            {
                "SPARK_INTELLIGENCE_HOME": "   ",
                "SPARK_BUILDER_HOME": str(alias_home),
            },
            clear=True,
        ):
            manager = ConfigManager.from_home(None)

        self.assertEqual(manager.paths.home, alias_home)

    def test_primary_home_precedes_builder_alias(self) -> None:
        primary_home = self.home / "intelligence-home"
        alias_home = self.home / "builder-home"
        with patch.dict(
            "os.environ",
            {
                "SPARK_INTELLIGENCE_HOME": str(primary_home),
                "SPARK_BUILDER_HOME": str(alias_home),
            },
            clear=True,
        ):
            manager = ConfigManager.from_home(None)

        self.assertEqual(manager.paths.home, primary_home)

    def test_empty_environment_uses_named_default_not_working_directory(self) -> None:
        with patch.dict(
            "os.environ",
            {"SPARK_INTELLIGENCE_HOME": "", "SPARK_BUILDER_HOME": ""},
            clear=True,
        ):
            manager = ConfigManager.from_home(None)

        self.assertEqual(manager.paths.home.name, ".spark-intelligence")
        self.assertNotEqual(str(manager.paths.home), ".")
