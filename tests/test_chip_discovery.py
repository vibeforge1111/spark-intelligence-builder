"""Canonical chip discovery and severed-path regressions."""

from __future__ import annotations

import os
from unittest.mock import patch

from spark_intelligence.attachments import (
    SeveredChipDiscoveryError,
    assert_chip_discovery_healthy,
    attachment_status,
    canonical_chip_home,
    chip_discovery_health,
)
from spark_intelligence.attachments.registry import _resolve_chip_roots
from spark_intelligence.doctor.checks import run_doctor
from tests.test_support import SparkTestCase, create_fake_hook_chip


class ChipDiscoveryTests(SparkTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spark_home = self.home / ".spark"
        self.chips_home = self.spark_home / "chips"
        self.chips_home.mkdir(parents=True, exist_ok=True)

    def test_canonical_home_is_first_class_with_configured_extras(self) -> None:
        configured_parent = self.home / "configured"
        configured_chip = create_fake_hook_chip(configured_parent, chip_key="configured")
        create_fake_hook_chip(self.chips_home, chip_key="canonical")
        self.config_manager.set_path("spark.chips.roots", [str(configured_chip)])

        roots, source = _resolve_chip_roots(self.config_manager)
        scan = attachment_status(self.config_manager)

        self.assertEqual(roots[0], self.chips_home)
        self.assertIn("canonical", source)
        self.assertIn("configured", source)
        keys = {record.key for record in scan.records if record.kind == "chip"}
        self.assertEqual(keys, {"canonical", "configured"})

    def test_canonical_home_resolves_with_empty_config(self) -> None:
        create_fake_hook_chip(self.chips_home, chip_key="canonical")
        self.config_manager.set_path("spark.chips.roots", [])

        scan = attachment_status(self.config_manager)

        self.assertIn("canonical", scan.chip_source)
        self.assertIn("canonical", {record.key for record in scan.records if record.kind == "chip"})

    def test_canonical_home_honors_spark_home(self) -> None:
        relocated = self.home / "relocated"
        create_fake_hook_chip(relocated / "chips", chip_key="relocated")

        with patch.dict(os.environ, {"SPARK_HOME": str(relocated)}):
            self.assertEqual(canonical_chip_home(), relocated / "chips")
            scan = attachment_status(self.config_manager)

        self.assertIn("relocated", {record.key for record in scan.records if record.kind == "chip"})

    def test_legacy_attachment_root_is_additive_without_desktop_scan(self) -> None:
        create_fake_hook_chip(self.chips_home, chip_key="canonical")
        create_fake_hook_chip(self.spark_home / "attachments", chip_key="compatibility")
        create_fake_hook_chip(self.home / "Desktop", chip_key="desktop-only")

        scan = attachment_status(self.config_manager)

        keys = {record.key for record in scan.records if record.kind == "chip"}
        self.assertEqual(keys, {"canonical", "compatibility"})
        self.assertIn("autodiscovered", scan.chip_source)
        self.assertNotIn("desktop-only", keys)

    def test_discovery_health_accepts_resolved_canonical_chips(self) -> None:
        create_fake_hook_chip(self.chips_home, chip_key="canonical")

        health = chip_discovery_health(self.config_manager)
        assert_chip_discovery_healthy(self.config_manager)

        self.assertTrue(health["ok"])
        self.assertEqual(health["chips_on_disk"], 1)
        self.assertGreaterEqual(health["chips_resolved"], 1)

    def test_severed_discovery_fails_loudly(self) -> None:
        create_fake_hook_chip(self.chips_home, chip_key="canonical")
        with patch(
            "spark_intelligence.attachments.registry._resolve_chip_roots",
            return_value=([], "missing"),
        ):
            health = chip_discovery_health(self.config_manager)
            with self.assertRaises(SeveredChipDiscoveryError):
                assert_chip_discovery_healthy(self.config_manager)

        self.assertFalse(health["ok"])
        self.assertIn("SEVERED", health["detail"])

    def test_configured_chip_cannot_mask_severed_canonical_discovery(self) -> None:
        canonical_chip = create_fake_hook_chip(self.chips_home, chip_key="canonical")
        configured_chip = create_fake_hook_chip(self.home / "configured", chip_key="configured")
        self.config_manager.set_path("spark.chips.roots", [str(configured_chip)])
        scan = attachment_status(self.config_manager)
        configured_only = type(scan)(
            chip_source="configured",
            path_source=scan.path_source,
            chip_roots=[str(configured_chip)],
            path_roots=scan.path_roots,
            records=[record for record in scan.records if record.repo_root != str(canonical_chip)],
            warnings=scan.warnings,
        )

        health = chip_discovery_health(self.config_manager, scan=configured_only)

        self.assertFalse(health["ok"])
        self.assertGreaterEqual(health["chips_resolved"], 1)
        self.assertEqual(health["canonical_chips_resolved"], 0)

    def test_empty_install_is_not_severed(self) -> None:
        health = chip_discovery_health(self.config_manager)
        self.assertTrue(health["ok"])
        self.assertEqual(health["chips_on_disk"], 0)

    def test_doctor_reports_discovery_health(self) -> None:
        create_fake_hook_chip(self.chips_home, chip_key="canonical")

        report = run_doctor(self.config_manager, self.state_db)

        discovery_checks = [check for check in report.checks if check.name == "chip-discovery"]
        self.assertEqual(len(discovery_checks), 1)
        self.assertTrue(discovery_checks[0].ok)

    def test_doctor_reports_severed_discovery(self) -> None:
        create_fake_hook_chip(self.chips_home, chip_key="canonical")
        with patch(
            "spark_intelligence.attachments.registry._resolve_chip_roots",
            return_value=([], "missing"),
        ):
            report = run_doctor(self.config_manager, self.state_db)

        discovery_checks = [check for check in report.checks if check.name == "chip-discovery"]
        self.assertEqual(len(discovery_checks), 1)
        self.assertFalse(discovery_checks[0].ok)
        self.assertFalse(report.ok)
