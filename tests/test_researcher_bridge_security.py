from __future__ import annotations

import sys
import types
from dataclasses import dataclass

from spark_intelligence.researcher_bridge import advisory


@dataclass
class _FakeProviderSpec:
    base_url: str
    model: str
    api_key: str


def test_spark_character_provider_resolver_ignores_process_env(monkeypatch) -> None:
    fake_module = types.SimpleNamespace(ProviderSpec=_FakeProviderSpec)
    monkeypatch.setitem(sys.modules, "spark_character", fake_module)
    monkeypatch.setattr(advisory, "ensure_spark_character_path", lambda: None)
    monkeypatch.setenv("ZAI_API_KEY", "ambient-secret")
    monkeypatch.setenv("ZAI_MODEL", "ambient-model")

    provider = advisory._resolve_spark_character_provider({})

    assert provider is None


def test_spark_character_provider_resolver_uses_explicit_env_map(monkeypatch) -> None:
    fake_module = types.SimpleNamespace(ProviderSpec=_FakeProviderSpec)
    monkeypatch.setitem(sys.modules, "spark_character", fake_module)
    monkeypatch.setattr(advisory, "ensure_spark_character_path", lambda: None)
    monkeypatch.setenv("ZAI_API_KEY", "ambient-secret")
    monkeypatch.setenv("ZAI_MODEL", "ambient-model")

    provider = advisory._resolve_spark_character_provider({"ZAI_API_KEY": "mapped-secret"})

    assert provider == _FakeProviderSpec(
        base_url="https://api.z.ai/api/coding/paas/v4/",
        model="glm-5.1",
        api_key="mapped-secret",
    )
