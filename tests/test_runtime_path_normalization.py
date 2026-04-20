from __future__ import annotations

import os
from pathlib import Path

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.attachments.registry import list_attachments
from spark_intelligence.researcher_bridge.advisory import (
    discover_researcher_runtime_root,
    resolve_researcher_config_path,
)
from spark_intelligence.swarm_bridge.local import _resolve_swarm_runtime_root
from spark_intelligence.swarm_bridge.sync import _discover_swarm_runtime_root

from tests.test_support import SparkTestCase


class RuntimePathNormalizationTests(SparkTestCase):
    def test_normalize_runtime_path_prefers_local_runtime_surface(self) -> None:
        normalized = self.config_manager.normalize_runtime_path(r"C:\Users\USER\Desktop\spark-intelligence-builder")

        self.assertIsNotNone(normalized)
        assert normalized is not None
        expected = (
            Path(r"C:\Users\USER\Desktop\spark-intelligence-builder")
            if os.name == "nt"
            else Path("/mnt/c/Users/USER/Desktop/spark-intelligence-builder")
        )
        self.assertEqual(normalized, expected)
        self.assertTrue(normalized.exists())

    def test_discover_researcher_runtime_root_uses_translated_windows_path(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        configured_runtime_root = (
            "/mnt/c/Users/USER/Desktop/spark-researcher"
            if os.name == "nt"
            else r"C:\Users\USER\Desktop\spark-researcher"
        )
        configured_config_path = (
            "/mnt/c/Users/USER/Desktop/spark-researcher/spark-researcher.project.json"
            if os.name == "nt"
            else r"C:\Users\USER\Desktop\spark-researcher\spark-researcher.project.json"
        )
        expected_runtime_root = (
            Path(r"C:\Users\USER\Desktop\spark-researcher")
            if os.name == "nt"
            else Path("/mnt/c/Users/USER/Desktop/spark-researcher")
        )
        expected_config_path = (
            Path(r"C:\Users\USER\Desktop\spark-researcher\spark-researcher.project.json")
            if os.name == "nt"
            else Path("/mnt/c/Users/USER/Desktop/spark-researcher/spark-researcher.project.json")
        )
        self.config_manager.set_path("spark.researcher.runtime_root", configured_runtime_root)
        self.config_manager.set_path("spark.researcher.config_path", configured_config_path)

        runtime_root, source = discover_researcher_runtime_root(self.config_manager)
        resolved_config = resolve_researcher_config_path(self.config_manager, runtime_root)  # type: ignore[arg-type]

        self.assertEqual(source, "configured")
        self.assertIsNotNone(runtime_root)
        assert runtime_root is not None
        self.assertEqual(runtime_root, expected_runtime_root)
        self.assertTrue(runtime_root.exists())
        self.assertEqual(resolved_config, expected_config_path)
        self.assertTrue(resolved_config.exists())

    def test_discover_swarm_runtime_root_uses_translated_windows_path(self) -> None:
        self.config_manager.set_path("spark.swarm.runtime_root", r"C:\Users\USER\Desktop\spark-swarm")

        runtime_root, source = _discover_swarm_runtime_root(self.config_manager)

        self.assertEqual(source, "configured")
        self.assertIsNotNone(runtime_root)
        assert runtime_root is not None
        expected = (
            Path(r"C:\Users\USER\Desktop\spark-swarm")
            if os.name == "nt"
            else Path("/mnt/c/Users/USER/Desktop/spark-swarm")
        )
        self.assertEqual(runtime_root, expected)
        self.assertTrue(runtime_root.exists())

    def test_local_swarm_bridge_runtime_root_uses_translated_windows_path(self) -> None:
        self.config_manager.set_path("spark.swarm.runtime_root", r"C:\Users\USER\Desktop\spark-swarm")

        runtime_root = _resolve_swarm_runtime_root(self.config_manager)

        expected = (
            Path(r"C:\Users\USER\Desktop\spark-swarm")
            if os.name == "nt"
            else Path("/mnt/c/Users/USER/Desktop/spark-swarm")
        )
        self.assertEqual(runtime_root, expected)
        self.assertTrue(runtime_root.exists())

    def test_attachment_registry_uses_translated_windows_roots(self) -> None:
        self.config_manager.set_path(
            "spark.chips.roots",
            [r"C:\Users\USER\Desktop\spark-browser-extension", r"C:\Users\USER\Desktop\domain-chip-voice-comms"],
        )
        self.config_manager.set_path(
            "spark.specialization_paths.roots",
            [r"C:\Users\USER\Desktop\specialization-path-startup-operator"],
        )

        chip_records = list_attachments(self.config_manager, kind="chip")
        path_records = list_attachments(self.config_manager, kind="path")

        self.assertTrue(any(record.key == "spark-browser" for record in chip_records.records))
        self.assertTrue(any(record.key == "domain-chip-voice-comms" for record in chip_records.records))
        self.assertTrue(any(record.key == "startup-operator" for record in path_records.records))
