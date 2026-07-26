from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from spark_intelligence.self_awareness.capability_ledger import _write_ledger


def test_capability_ledger_write_uses_unique_atomic_replace(tmp_path: Path) -> None:
    target = tmp_path / "capability-ledger.json"
    payload = {"schema_version": "spark.capability_ledger.v1", "entries": {"chip:a": {}}}

    _write_ledger(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob(".*.tmp")) == []


def test_capability_ledger_write_cleans_unique_temp_after_replace_failure(tmp_path: Path) -> None:
    target = tmp_path / "capability-ledger.json"

    with patch("spark_intelligence.atomic_io.os.replace", side_effect=OSError("replace failed")):
        with pytest.raises(OSError, match="replace failed"):
            _write_ledger(target, {"schema_version": "spark.capability_ledger.v1", "entries": {}})

    assert not target.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
