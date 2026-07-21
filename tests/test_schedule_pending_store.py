from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.schedule_bridge.service import _pending_store_path, _save_pending

from tests.test_support import SparkTestCase


class SchedulePendingStoreTests(SparkTestCase):
    def test_pending_store_uses_builder_home_alias_when_primary_is_blank(self) -> None:
        alias_home = self.home / "builder-home"
        with patch.dict(
            "os.environ",
            {
                "SPARK_INTELLIGENCE_HOME": "  ",
                "SPARK_BUILDER_HOME": str(alias_home),
            },
            clear=True,
        ):
            store_path = _pending_store_path()

        self.assertEqual(store_path, alias_home / "pending_confirmations.json")

    def test_pending_store_primary_home_precedes_builder_alias(self) -> None:
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
            store_path = _pending_store_path()

        self.assertEqual(store_path, primary_home / "pending_confirmations.json")

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
