from __future__ import annotations

import json
import sqlite3
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


def _write_agent_persona_profile(home, *, persona_name: str, provenance: dict | None = None) -> None:
    db = home / "state.db"
    with sqlite3.connect(str(db)) as con:
        con.execute(
            """
            CREATE TABLE agent_persona_profiles (
                agent_id TEXT PRIMARY KEY,
                persona_name TEXT,
                persona_summary TEXT,
                base_traits_json TEXT NOT NULL,
                behavioral_rules_json TEXT,
                provenance_json TEXT,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT INTO agent_persona_profiles(
                agent_id, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "agent:test",
                persona_name,
                "Test persona.",
                json.dumps({"warmth": 0.6}),
                json.dumps([]),
                json.dumps(provenance or {}),
                "2026-06-15T00:00:00+00:00",
            ),
        )


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


def test_sib_active_personality_reads_profile_provenance_chip_id(monkeypatch, tmp_path) -> None:
    _write_agent_persona_profile(
        tmp_path,
        persona_name="Founder Operator",
        provenance={"personality_id": "Forge"},
    )
    monkeypatch.setenv("SPARK_INTELLIGENCE_HOME", str(tmp_path))
    monkeypatch.delenv("SPARK_INTELLIGENCE_PERSONALITY", raising=False)
    advisory._ACTIVE_PERSONALITY_CACHE = None

    assert advisory._resolve_active_personality_chip_id() == "forge"


def test_sib_active_personality_normalizes_profile_name(monkeypatch, tmp_path) -> None:
    _write_agent_persona_profile(tmp_path, persona_name="Forge Operator", provenance={})
    monkeypatch.setenv("SPARK_INTELLIGENCE_HOME", str(tmp_path))
    monkeypatch.delenv("SPARK_INTELLIGENCE_PERSONALITY", raising=False)
    advisory._ACTIVE_PERSONALITY_CACHE = None

    assert advisory._resolve_active_personality_chip_id() == "forge-operator"


def test_spark_character_persona_renders_non_default_sib_profile_chip(monkeypatch, tmp_path) -> None:
    _write_agent_persona_profile(
        tmp_path,
        persona_name="Founder Operator",
        provenance={"personality_chip_id": "Forge"},
    )
    monkeypatch.setenv("SPARK_INTELLIGENCE_HOME", str(tmp_path))
    monkeypatch.delenv("SPARK_INTELLIGENCE_PERSONALITY", raising=False)
    advisory._ACTIVE_PERSONALITY_CACHE = None

    captured: dict[str, str] = {}

    @dataclass
    class FakePersonaSpec:
        version: str
        text: str

    def fake_load_chip_by_id(chip_id: str):
        captured["chip_id"] = chip_id
        return SimpleNamespace(id=chip_id, name="Forge")

    fake_spark_character = types.ModuleType("spark_character")
    fake_spark_character.__path__ = []
    fake_spark_character.PersonaSpec = FakePersonaSpec
    fake_spark_character.load_chip_by_id = fake_load_chip_by_id
    fake_spark_character.render_chip_to_system_prompt = (
        lambda chip: f"rendered persona chip={chip.id}"
    )
    fake_persona = types.ModuleType("spark_character.persona")
    fake_persona.load_overlay = lambda _kind: None
    fake_persona.load_surface_overlay = lambda surface: f"surface={surface}"
    monkeypatch.setitem(sys.modules, "spark_character", fake_spark_character)
    monkeypatch.setitem(sys.modules, "spark_character.persona", fake_persona)
    monkeypatch.setattr(advisory, "ensure_spark_character_path", lambda: None)

    spec = advisory._resolve_chip_or_persona(kind=None, surface="telegram")

    assert captured["chip_id"] == "forge"
    assert spec is not None
    assert spec.version == "chip:forge:telegram"
    assert "rendered persona chip=forge" in spec.text
    assert "surface=telegram" in spec.text


def test_spark_character_fallback_passes_governed_client_side_search(monkeypatch) -> None:
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
    assert captured["enable_search"] is True
    assert captured["network_policy"] == {
        "allowed": True,
        "authority": "spark-intelligence-builder.researcher_bridge.spark_character_fallback",
        "risk": "network",
    }
    assert captured["disable_thinking"] is True
    assert captured["tools"] == [{"type": "web_search"}]
    assert captured["surface"] == "telegram"


def test_spark_character_fallback_disables_thinking_payload_for_openai_compat(monkeypatch) -> None:
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
            base_url="https://api.openai.com/v1/",
            model="gpt-4o-mini",
            api_key="mapped-secret",
        ),
    )
    monkeypatch.setattr(advisory, "_spark_character_provider_tools", lambda _provider: None)
    monkeypatch.setattr(
        advisory,
        "_resolve_chip_or_persona",
        lambda **_kwargs: SimpleNamespace(version="v-test"),
    )
    config = SimpleNamespace(read_env_map=lambda: {"OPENAI_API_KEY": "mapped-secret"})

    reply = advisory.try_spark_character_fallback(
        user_message="What's the latest OpenAI news?",
        config_manager=config,
        surface="telegram",
    )

    assert reply == "fallback reply"
    assert captured["disable_thinking"] is False
