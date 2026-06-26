"""Regression tests for the sdk_module import allowlist (RCE hardening).

A malicious or mistaken ``spark.memory.sdk_module`` config value must not be
able to drive ``importlib.import_module`` to an arbitrary module. Only roots in
``_ALLOWED_SDK_MODULE_ROOTS`` are permitted.
"""
from __future__ import annotations

import pytest

from spark_intelligence.memory.orchestrator import (
    _ALLOWED_SDK_MODULE_ROOTS,
    _validate_sdk_module_name,
)


class TestValidateSdkModuleName:
    def test_allows_allowlisted_root(self) -> None:
        assert _validate_sdk_module_name("domain_chip_memory") == "domain_chip_memory"

    def test_allows_allowlisted_submodule(self) -> None:
        # Submodules of an allowlisted root are permitted (root match only).
        assert (
            _validate_sdk_module_name("domain_chip_memory.sdk")
            == "domain_chip_memory.sdk"
        )

    def test_blocks_arbitrary_module(self) -> None:
        with pytest.raises(ValueError, match="not in the allowlist"):
            _validate_sdk_module_name("os")

    def test_blocks_dotted_arbitrary_module(self) -> None:
        with pytest.raises(ValueError, match="not in the allowlist"):
            _validate_sdk_module_name("subprocess.run")

    def test_blocks_lookalike_root(self) -> None:
        # A prefix collision must not slip through: only the exact root counts.
        with pytest.raises(ValueError, match="not in the allowlist"):
            _validate_sdk_module_name("domain_chip_memory_evil")

    def test_error_lists_allowed_roots(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            _validate_sdk_module_name("malicious_pkg")
        assert "domain_chip_memory" in str(exc_info.value)

    def test_allowlist_is_frozen(self) -> None:
        assert isinstance(_ALLOWED_SDK_MODULE_ROOTS, frozenset)
        assert "domain_chip_memory" in _ALLOWED_SDK_MODULE_ROOTS
