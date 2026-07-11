"""Versioned chip hook protocol boundary regressions."""

from __future__ import annotations

import json

from spark_intelligence.attachments import attachment_status
from spark_intelligence.attachments.hooks import (
    SUPPORTED_IO_PROTOCOLS,
    execute_chip_hook_record,
    io_protocol_supported,
)
from tests.test_support import SparkTestCase, create_fake_hook_chip


class IoProtocolContractTests(SparkTestCase):
    def _record(self, chip_key: str):
        for record in attachment_status(self.config_manager).records:
            if record.key == chip_key:
                return record
        raise AssertionError(f"chip {chip_key!r} was not resolved")

    def test_supported_protocol_contract_is_explicit(self) -> None:
        self.assertIn("spark-hook-io.v1", SUPPORTED_IO_PROTOCOLS)
        self.assertTrue(io_protocol_supported("spark-hook-io.v1"))
        self.assertTrue(io_protocol_supported(None))
        self.assertTrue(io_protocol_supported(""))
        self.assertFalse(io_protocol_supported("spark-hook-io.v2"))
        self.assertFalse(io_protocol_supported("bespoke-protocol"))

    def test_v1_chip_passes_the_protocol_gate(self) -> None:
        root = create_fake_hook_chip(self.home, chip_key="protocol-v1")
        self.config_manager.set_path("spark.chips.roots", [str(root)])

        record = self._record("protocol-v1")

        self.assertEqual(record.io_protocol, "spark-hook-io.v1")
        self.assertTrue(io_protocol_supported(record.io_protocol))

    def test_unknown_protocol_is_refused_before_governor_or_subprocess(self) -> None:
        root = create_fake_hook_chip(self.home, chip_key="protocol-v2")
        manifest_path = root / "spark-chip.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["io_protocol"] = "spark-hook-io.v2"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.config_manager.set_path("spark.chips.roots", [str(root)])

        with self.assertRaises(ValueError) as caught:
            execute_chip_hook_record(
                self._record("protocol-v2"),
                hook="evaluate",
                payload={},
                governor_decision={"allowed": True},
            )

        self.assertIn("does not support", str(caught.exception))
        self.assertIn("spark-hook-io.v2", str(caught.exception))
