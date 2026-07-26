"""Generated and previously shipped chip manifest identity regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spark_intelligence.chip_create.pipeline import (
    _patch_manifest_router_fields,
    repair_chip_manifest,
)


def write_manifest(root: Path, directory: str, document: dict) -> Path:
    manifest = root / directory / "spark-chip.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


class ChipManifestRepairTests(unittest.TestCase):
    def test_generator_emits_identity_capabilities_and_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = write_manifest(
                Path(temp_dir),
                "domain-chip-widget",
                {
                    "schema_version": "spark-chip.v1",
                    "commands": {
                        "evaluate": ["python3", "chip-runner.py", "evaluate"],
                        "suggest": ["python3", "chip-runner.py", "suggest"],
                    },
                },
            )
            _patch_manifest_router_fields(manifest, {}, chip_key="domain-chip-widget")
            document = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(document["chip_key"], "domain-chip-widget")
        self.assertEqual(document["chip_name"], "domain-chip-widget")
        self.assertEqual(document["capabilities"], ["evaluate", "suggest"])
        self.assertEqual(document["io_protocol"], "spark-hook-io.v1")

    def test_generator_preserves_explicit_identity_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = write_manifest(
                Path(temp_dir),
                "domain-chip-widget",
                {
                    "chip_key": "explicit-key",
                    "chip_name": "explicit-name",
                    "commands": {"evaluate": ["runner"]},
                    "capabilities": ["evaluate", "custom"],
                    "io_protocol": "spark-hook-io.v1",
                },
            )
            _patch_manifest_router_fields(manifest, {}, chip_key="domain-chip-widget")
            document = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(document["chip_key"], "explicit-key")
        self.assertEqual(document["chip_name"], "explicit-name")
        self.assertEqual(document["capabilities"], ["evaluate", "custom"])

    def test_repair_backfills_missing_fields_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = write_manifest(
                Path(temp_dir),
                "domain-chip-prd-writing-proof-loop",
                {
                    "schema_version": "spark-domain-chip.runtime_manifest.v1",
                    "chip_name": "domain-chip-prd-writing-proof-loop",
                    "commands": {
                        "evaluate": ["python3", "runner"],
                        "loop-round": ["python3", "runner"],
                    },
                    "description": "flagship",
                    "visibility": "private",
                },
            )
            first = repair_chip_manifest(manifest)
            second = repair_chip_manifest(manifest)
            document = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(document["chip_key"], "domain-chip-prd-writing-proof-loop")
        self.assertEqual(document["capabilities"], ["evaluate", "loop-round"])
        self.assertEqual(document["io_protocol"], "spark-hook-io.v1")
        self.assertEqual(document["schema_version"], "spark-domain-chip.runtime_manifest.v1")
        self.assertEqual(document["description"], "flagship")
        self.assertEqual(document["visibility"], "private")

    def test_repair_uses_directory_name_when_identity_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = write_manifest(
                Path(temp_dir),
                "domain-chip-bare",
                {"schema_version": "spark-chip.v1", "commands": {"evaluate": ["runner"]}},
            )
            repair_chip_manifest(manifest)
            document = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(document["chip_key"], "domain-chip-bare")
        self.assertEqual(document["chip_name"], "domain-chip-bare")


if __name__ == "__main__":
    unittest.main()
