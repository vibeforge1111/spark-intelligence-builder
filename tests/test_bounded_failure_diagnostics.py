from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.checks import evaluate_stop_ship_issues
from spark_intelligence.personality.loader import load_personality_profile
from spark_intelligence.schedule_bridge.service import _load_pending
from spark_intelligence.self_awareness.route_probe import run_route_probe_and_record
from spark_intelligence.self_awareness.system_map_read_model import _read_json_object
from spark_intelligence.spawner_payload_drift import _path_from_reference
from spark_intelligence.system_registry.registry import _collect_browser_registry_payload

from tests.test_support import SparkTestCase


class BoundedFailureDiagnosticsTests(SparkTestCase):
    def test_system_map_parse_failure_logs_only_exception_class(self) -> None:
        secret_marker = "system-map-secret-must-not-enter-log"
        system_map = self.home / "system-map.json"
        system_map.write_text(secret_marker, encoding="utf-8")

        with self.assertLogs(
            "spark_intelligence.self_awareness.system_map_read_model",
            level=logging.DEBUG,
        ) as captured:
            payload = _read_json_object(system_map)

        self.assertEqual(payload, {})
        rendered = "\n".join(captured.output)
        self.assertIn("error_type=JSONDecodeError", rendered)
        self.assertNotIn(secret_marker, rendered)

    def test_spawner_reference_resolution_logs_only_exception_class(self) -> None:
        secret_marker = "spawner-path-secret-must-not-enter-log"
        reference = str(self.home / "project")

        with (
            patch.object(Path, "resolve", side_effect=RuntimeError(secret_marker)),
            self.assertLogs("spark_intelligence.spawner_payload_drift", level=logging.DEBUG) as captured,
        ):
            resolved = _path_from_reference(reference)

        self.assertEqual(resolved, Path(reference).expanduser())
        rendered = "\n".join(captured.output)
        self.assertIn("error_type=RuntimeError", rendered)
        self.assertNotIn(secret_marker, rendered)

    def test_schedule_pending_load_logs_only_exception_class(self) -> None:
        secret_marker = "schedule-secret-must-not-enter-log"
        pending = self.home / "pending_confirmations.json"
        pending.write_text(secret_marker, encoding="utf-8")

        with (
            patch("spark_intelligence.schedule_bridge.service._pending_store_path", return_value=pending),
            self.assertLogs("spark_intelligence.schedule_bridge.service", level=logging.WARNING) as captured,
        ):
            payload = _load_pending()

        self.assertEqual(payload, {})
        rendered = "\n".join(captured.output)
        self.assertIn("error_type=JSONDecodeError", rendered)
        self.assertNotIn(secret_marker, rendered)

    def test_browser_registry_missing_hook_uses_bounded_diagnostic(self) -> None:
        with (
            patch(
                "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
                return_value=None,
            ),
            patch(
                "spark_intelligence.system_registry.registry.run_first_active_chip_hook",
                return_value=None,
            ),
            self.assertLogs("spark_intelligence.system_registry.registry", level=logging.DEBUG) as captured,
        ):
            payload = _collect_browser_registry_payload(self.config_manager)

        self.assertIsNone(payload)
        self.assertIn("browser_registry_hook_unavailable", "\n".join(captured.output))

    def test_route_probe_exception_records_only_exception_class(self) -> None:
        secret_marker = "route-probe-secret-must-not-enter-evidence"
        with patch(
            "spark_intelligence.self_awareness.route_probe._run_route_probe",
            side_effect=RuntimeError(secret_marker),
        ):
            result = run_route_probe_and_record(
                self.config_manager,
                self.state_db,
                capability_key="spark_browser",
                actor_id="operator:test",
            )

        self.assertEqual(result.status, "failure")
        self.assertEqual(result.failure_reason, "route_probe_exception:RuntimeError")
        self.assertNotIn(secret_marker, str(result.to_payload()))

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
                side_effect=OSError(secret_marker),
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
        self.assertIn("error_type=OSError", rendered)
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
