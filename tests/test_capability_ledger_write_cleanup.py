"""Resilience tests for self_awareness.capability_ledger._write_ledger temp-file cleanup."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from spark_intelligence.self_awareness import capability_ledger


class WriteLedgerTempCleanupTests(unittest.TestCase):
    """Regression for orphaned-temp leak in `_write_ledger`.

    Prior to the try/finally release, every failure of tmp_path.replace
    (EXDEV cross-device rename when the artifacts/ store crosses a mount
    boundary, PermissionError when the destination is locked by a
    concurrent record_capability_ledger_event writer, transient EIO) left
    a `.json.tmp` orphan beside the capability ledger. The ledger is
    written on every capability state transition (proposed -> scaffolded
    -> probed -> approved -> activated, etc.), so a long-running agent
    accumulates one orphan per failed transition.
    """

    def test_write_ledger_releases_temp_when_replace_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "capability_ledger.json"

            def _boom(self, target):
                raise OSError("simulated cross-device rename / EXDEV")

            with mock.patch.object(Path, "replace", _boom):
                try:
                    capability_ledger._write_ledger(
                        ledger_path,
                        {"schema_version": "spark.capability_ledger.v1", "entries": {}},
                    )
                except OSError:
                    pass

            self.assertFalse(
                ledger_path.with_suffix(".json.tmp").exists(),
                "orphan capability_ledger.json.tmp left behind after replace failure",
            )
            # The real file should NOT exist (replace failed).
            self.assertFalse(ledger_path.exists())

    def test_write_ledger_releases_temp_when_write_text_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "capability_ledger.json"

            def _boom_write_text(self, *_args, **_kwargs):
                self.touch()
                raise OSError("simulated ENOSPC / EIO during write_text")

            with mock.patch.object(Path, "write_text", _boom_write_text):
                try:
                    capability_ledger._write_ledger(
                        ledger_path,
                        {"schema_version": "spark.capability_ledger.v1", "entries": {}},
                    )
                except OSError:
                    pass

            self.assertFalse(
                ledger_path.with_suffix(".json.tmp").exists(),
                "orphan capability_ledger.json.tmp left behind after write_text failure",
            )

    def test_write_ledger_happy_path_no_leftover_tmp(self) -> None:
        with TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "capability_ledger.json"
            capability_ledger._write_ledger(
                ledger_path,
                {"schema_version": "spark.capability_ledger.v1", "entries": {"chip:a": {"state": "proposed"}}},
            )
            self.assertTrue(ledger_path.exists())
            self.assertFalse(
                ledger_path.with_suffix(".json.tmp").exists(),
                "no leftover .tmp on the happy path",
            )
            # And the payload is intact.
            import json
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(data["entries"]["chip:a"]["state"], "proposed")


if __name__ == "__main__":
    unittest.main()
