"""Regression test for Rayiea compete item #50."""

from __future__ import annotations

import json
from pathlib import Path


def test_rayiea_50_module_imports() -> None:
    """Smoke import for patched module."""
    import importlib

    importlib.import_module("src.spark_intelligence.adapters.telegram.client")
