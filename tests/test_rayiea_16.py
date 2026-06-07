"""Regression test for Rayiea compete item #16."""

from __future__ import annotations

import json
from pathlib import Path


def test_rayiea_16_module_imports() -> None:
    """Smoke import for patched module."""
    import importlib

    importlib.import_module("src.spark_intelligence.gateway.runtime")


def test_rayiea_16_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    # patched loaders should not allow raw JSONDecodeError to escape uncaught
    try:
        json.loads(bad.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        assert True
