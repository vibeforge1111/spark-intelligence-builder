from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.schedule_bridge.service import _save_pending

from tests.test_support import SparkTestCase


class SchedulePendingStoreTests(SparkTestCase):
    def test_save_pending_removes_temporary_file_when_replace_fails(self) -> None:
        store_path = self.home / "pending_confirmations.json"
        temporary_path = store_path.with_suffix(".json.tmp")
        real_replace = Path.replace

        def fail_target_replace(path: Path, target: Path) -> Path:
            if path == temporary_path and target == store_path:
                raise OSError("injected replace failure")
            return real_replace(path, target)

        with patch(
            "spark_intelligence.schedule_bridge.service._pending_store_path",
            return_value=store_path,
        ), patch.object(Path, "replace", fail_target_replace):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                _save_pending({"111": {"expires_at": 4_102_444_800, "schedule_id": "sched-1"}})

        self.assertFalse(temporary_path.exists())
        self.assertFalse(store_path.exists())

    def test_save_pending_successfully_replaces_store(self) -> None:
        store_path = self.home / "pending_confirmations.json"
        with patch(
            "spark_intelligence.schedule_bridge.service._pending_store_path",
            return_value=store_path,
        ):
            _save_pending({"111": {"expires_at": 4_102_444_800, "schedule_id": "sched-1"}})

        self.assertEqual(
            json.loads(store_path.read_text(encoding="utf-8")),
            {"111": {"expires_at": 4_102_444_800, "schedule_id": "sched-1"}},
        )
        self.assertFalse(store_path.with_suffix(".json.tmp").exists())
