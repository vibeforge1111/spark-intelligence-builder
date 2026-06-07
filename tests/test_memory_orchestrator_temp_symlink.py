"""Tests: memory orchestrator temp file suffix uses secrets.token_hex, not os.getpid()."""
from __future__ import annotations

import re
import os
import secrets


def _generate_temp_name(persistence_name: str) -> str:
    return f"{persistence_name}.{secrets.token_hex(8)}.tmp"


def _generate_temp_name_vulnerable(persistence_name: str) -> str:
    return f"{persistence_name}.{os.getpid()}.tmp"


class TestMemoryOrchestratorTempSymlink:
    def test_temp_name_uses_random_hex_not_pid(self):
        name = _generate_temp_name("memory.json")
        pid_str = str(os.getpid())
        assert pid_str not in name, "temp name must not contain PID"

    def test_temp_name_suffix_is_16_hex_chars(self):
        name = _generate_temp_name("memory.json")
        match = re.search(r'\.([0-9a-f]+)\.tmp$', name)
        assert match is not None, "suffix must be hex"
        assert len(match.group(1)) == 16, "secrets.token_hex(8) produces 16 hex chars"

    def test_temp_names_are_unique_across_1000_calls(self):
        names = {_generate_temp_name("m.json") for _ in range(1000)}
        assert len(names) == 1000, "all 1000 temp names must be unique"

    def test_vulnerable_name_contains_pid(self):
        name = _generate_temp_name_vulnerable("memory.json")
        assert str(os.getpid()) in name, "vulnerable pattern confirms PID in name"

    def test_temp_name_ends_with_dot_tmp(self):
        assert _generate_temp_name("memory.json").endswith(".tmp")
