from __future__ import annotations

import logging
import sqlite3
from unittest.mock import patch

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.checks import evaluate_stop_ship_issues
from spark_intelligence.personality.loader import load_personality_profile

from tests.test_support import SparkTestCase


class BoundedFailureDiagnosticsTests(SparkTestCase):
    def test_personality_state_load_logs_only_exception_class(self) -> None:
        secret_marker = "personality-state-secret-must-not-enter-log"
        evolver_path = self.home / "personality-evolver.json"
        evolver_path.write_text(secret_marker, encoding="utf-8")
        self.config_manager.set_path("spark.personality.evolver_state_path", str(evolver_path))

        with self.assertLogs("spark_intelligence.personality.loader", level=logging.WARNING) as captured:
            profile = load_personality_profile(
                human_id="human:test",
                state_db=self.state_db,
                config_manager=self.config_manager,
            )

        self.assertIsNotNone(profile)
        rendered = "\n".join(captured.output)
        self.assertIn("error_type=JSONDecodeError", rendered)
        self.assertNotIn(secret_marker, rendered)

    def test_windows_principal_fallback_logs_only_exception_class(self) -> None:
        secret_marker = "principal-secret-must-not-enter-log"
        with (
            patch(
                "spark_intelligence.config.loader.subprocess.run",
                side_effect=RuntimeError(secret_marker),
            ),
            patch.dict(
                "spark_intelligence.config.loader.os.environ",
                {"USERDOMAIN": "DOMAIN", "USERNAME": "user"},
                clear=False,
            ),
            self.assertLogs("spark_intelligence.config.loader", level=logging.DEBUG) as captured,
        ):
            principal = ConfigManager._windows_current_principal()

        self.assertEqual(principal, "DOMAIN\\user")
        rendered = "\n".join(captured.output)
        self.assertIn("error_type=RuntimeError", rendered)
        self.assertNotIn(secret_marker, rendered)

    def test_stop_ship_reconciliation_logs_only_exception_class(self) -> None:
        secret_marker = "database-secret-must-not-enter-log"
        with (
            patch(
                "spark_intelligence.observability.checks._reconcile_stop_ship_contradictions",
                side_effect=sqlite3.OperationalError(secret_marker),
            ),
            self.assertLogs("spark_intelligence.observability.checks", level=logging.WARNING) as captured,
        ):
            issues = evaluate_stop_ship_issues(
                config_manager=self.config_manager,
                state_db=self.state_db,
                emit_contradictions=True,
            )

        self.assertTrue(issues)
        rendered = "\n".join(captured.output)
        self.assertIn("error_type=OperationalError", rendered)
        self.assertNotIn(secret_marker, rendered)
