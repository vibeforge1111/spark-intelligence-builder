from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

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


def test_spark_character_fallback_does_not_enable_client_side_search(monkeypatch) -> None:
    captured: dict = {}

    def fake_generate(_text: str, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(final="fallback reply")

    fake_module = types.SimpleNamespace(
        ProviderSpec=_FakeProviderSpec,
        generate=fake_generate,
        generate_with_critique=lambda *args, **kwargs: SimpleNamespace(final="fallback reply"),
        load_persona=lambda *args, **kwargs: SimpleNamespace(version="v-test"),
    )
    monkeypatch.setitem(sys.modules, "spark_character", fake_module)
    monkeypatch.setattr(advisory, "ensure_spark_character_path", lambda: None)
    monkeypatch.setattr(
        advisory,
        "_resolve_spark_character_provider",
        lambda _env: _FakeProviderSpec(
            base_url="https://api.z.ai/api/coding/paas/v4/",
            model="glm-5.1",
            api_key="mapped-secret",
        ),
    )
    monkeypatch.setattr(advisory, "_spark_character_provider_tools", lambda _provider: [{"type": "web_search"}])
    monkeypatch.setattr(
        advisory,
        "_resolve_chip_or_persona",
        lambda **_kwargs: SimpleNamespace(version="v-test"),
    )
    config = SimpleNamespace(read_env_map=lambda: {"ZAI_API_KEY": "mapped-secret"})

    reply = advisory.try_spark_character_fallback(
        user_message="What's the current BTC price?",
        config_manager=config,
        surface="telegram",
    )

    assert reply == "fallback reply"
    assert "enable_search" not in captured
    assert captured["tools"] == [{"type": "web_search"}]
    assert captured["surface"] == "telegram"
