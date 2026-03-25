from __future__ import annotations

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


PROVIDER_ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "CUSTOM_API_KEY",
}


def connect_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider: str,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> str:
    config = config_manager.load()
    env_key = PROVIDER_ENV_KEYS[provider]

    if api_key:
        config_manager.upsert_env_secret(env_key, api_key)

    config.setdefault("providers", {}).setdefault("records", {})
    config["providers"]["records"][provider] = {
        "provider_kind": provider,
        "default_model": model,
        "base_url": base_url,
        "api_key_env": env_key,
    }
    if not config["providers"].get("default_provider"):
        config["providers"]["default_provider"] = provider
    config_manager.save(config)

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO provider_records(provider_id, provider_kind, default_model, base_url, api_key_env)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                provider_kind=excluded.provider_kind,
                default_model=excluded.default_model,
                base_url=excluded.base_url,
                api_key_env=excluded.api_key_env,
                updated_at=CURRENT_TIMESTAMP
            """,
            (provider, provider, model, base_url, env_key),
        )
        conn.commit()

    return f"Configured provider '{provider}' with env ref {env_key}."
