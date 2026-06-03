"""Resilience tests for schedule_bridge._save_pending temp-file cleanup."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from spark_intelligence.schedule_bridge import service as schedule_bridge_service


class SavePendingTempCleanupTests(unittest.TestCase):
    """Regression for orphaned-temp leak in `_save_pending`.

    Prior to the try/finally release, every failure of tmp.replace (EXDEV
    cross-device rename, PermissionError destination locked, transient EIO)
    left a `pending_confirmations.json.tmp` orphan in the user's
    .spark-intelligence/ home. Because the telegram bot shells out to a
    fresh `python -m spark_intelligence.cli` per message (per the comment
    above _PENDING_TTL_SECONDS), each invocation is a fresh chance to leak
    one .tmp.
    """

    def test_save_pending_releases_temp_when_replace_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "spark-home"
            with mock.patch.dict(
                "os.environ",
                {"SPARK_INTELLIGENCE_HOME": str(home)},
                clear=False,
            ):
                # Force replace to raise after the .tmp file is written.
                original_replace = Path.replace

                def _boom(self, target):
                    raise OSError("simulated cross-device rename / EXDEV")

                with mock.patch.object(Path, "replace", _boom):
                    try:
                        schedule_bridge_service._save_pending(
                            {"user-1": {"expires_at": 1e18, "schedule_id": "s-1"}}
                        )
                    except OSError:
                        pass

                # The orphan .tmp must NOT remain.
                self.assertFalse(
                    (home / "pending_confirmations.json.tmp").exists(),
                    "orphan pending_confirmations.json.tmp left behind after replace failure",
                )
                # Real file should NOT exist either (replace failed).
                self.assertFalse((home / "pending_confirmations.json").exists())

                # Restore (no-op since context manager unwinds, but documents intent).
                _ = original_replace

    def test_save_pending_happy_path_no_leftover_tmp(self) -> None:
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "spark-home"
            with mock.patch.dict(
                "os.environ",
                {"SPARK_INTELLIGENCE_HOME": str(home)},
                clear=False,
            ):
                schedule_bridge_service._save_pending(
                    {"user-1": {"expires_at": 1e18, "schedule_id": "s-1"}}
                )

                self.assertTrue((home / "pending_confirmations.json").exists())
                self.assertFalse(
                    (home / "pending_confirmations.json.tmp").exists(),
                    "no leftover .tmp on the happy path",
                )

    def test_save_pending_releases_temp_when_write_text_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "spark-home"
            with mock.patch.dict(
                "os.environ",
                {"SPARK_INTELLIGENCE_HOME": str(home)},
                clear=False,
            ):
                def _boom_write_text(self, *_args, **_kwargs):
                    # Simulate ENOSPC / EIO: file gets created (touch) but write fails.
                    self.touch()
                    raise OSError("simulated ENOSPC / EIO during write_text")

                with mock.patch.object(Path, "write_text", _boom_write_text):
                    try:
                        schedule_bridge_service._save_pending(
                            {"user-1": {"expires_at": 1e18, "schedule_id": "s-1"}}
                        )
                    except OSError:
                        pass

                self.assertFalse(
                    (home / "pending_confirmations.json.tmp").exists(),
                    "orphan pending_confirmations.json.tmp left behind after write_text failure",
                )


if __name__ == "__main__":
    unittest.main()
