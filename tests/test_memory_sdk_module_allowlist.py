from __future__ import annotations

import pytest

from spark_intelligence.memory.orchestrator import (
    _ALLOWED_SDK_MODULE_ROOTS,
    _validate_sdk_module_name,
)


@pytest.mark.parametrize(
    "module_name",
    ["domain_chip_memory", "domain_chip_memory.sdk"],
)
def test_sdk_module_allowlist_accepts_canonical_memory_modules(module_name: str) -> None:
    assert _validate_sdk_module_name(module_name) == module_name


@pytest.mark.parametrize(
    "module_name",
    ["os", "subprocess.run", "domain_chip_memory_evil"],
)
def test_sdk_module_allowlist_rejects_arbitrary_or_lookalike_modules(
    module_name: str,
) -> None:
    with pytest.raises(ValueError, match="not in the allowlist"):
        _validate_sdk_module_name(module_name)


def test_sdk_module_allowlist_is_immutable_and_auditable() -> None:
    assert isinstance(_ALLOWED_SDK_MODULE_ROOTS, frozenset)
    assert _ALLOWED_SDK_MODULE_ROOTS == frozenset({"domain_chip_memory"})
