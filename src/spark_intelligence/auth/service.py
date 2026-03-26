from __future__ import annotations

from spark_intelligence.auth.runtime import build_default_auth_profile_id
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
    api_key_env: str | None,
    model: str | None,
    base_url: str | None,
) -> str:
    config = config_manager.load()
    env_key = api_key_env or PROVIDER_ENV_KEYS[provider]
    profile_id = build_default_auth_profile_id(provider)

    if api_key:
        config_manager.upsert_env_secret(env_key, api_key)

    config.setdefault("providers", {}).setdefault("records", {})
    config["providers"]["records"][provider] = {
        "provider_kind": provider,
        "default_model": model,
        "base_url": base_url,
        "api_key_env": env_key,
        "default_auth_profile_id": profile_id,
    }
    if not config["providers"].get("default_provider"):
        config["providers"]["default_provider"] = provider
    config_manager.save(config)

    env_map = config_manager.read_env_map()
    profile_status = "active" if env_map.get(env_key) else "pending_secret"
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO provider_records(provider_id, provider_kind, default_model, base_url, api_key_env, default_auth_profile_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                provider_kind=excluded.provider_kind,
                default_model=excluded.default_model,
                base_url=excluded.base_url,
                api_key_env=excluded.api_key_env,
                default_auth_profile_id=excluded.default_auth_profile_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (provider, provider, model, base_url, env_key, profile_id),
        )
        conn.execute(
            """
            INSERT INTO auth_profiles(auth_profile_id, provider_id, auth_method, display_label, subject_hint, status, is_default)
            VALUES (?, ?, 'api_key_env', ?, NULL, ?, 1)
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                provider_id=excluded.provider_id,
                auth_method=excluded.auth_method,
                display_label=excluded.display_label,
                status=excluded.status,
                is_default=excluded.is_default,
                updated_at=CURRENT_TIMESTAMP
            """,
            (profile_id, provider, f"{provider} default", profile_status),
        )
        conn.execute(
            """
            INSERT INTO auth_profile_static_refs(auth_profile_id, ref_source, ref_provider, ref_id)
            VALUES (?, 'env', 'default', ?)
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                ref_source=excluded.ref_source,
                ref_provider=excluded.ref_provider,
                ref_id=excluded.ref_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (profile_id, env_key),
        )
        conn.commit()

    return f"Configured provider '{provider}' with auth profile '{profile_id}' and env ref {env_key}."
