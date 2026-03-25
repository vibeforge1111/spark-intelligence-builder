from __future__ import annotations

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


def add_channel(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    channel_kind: str,
    bot_token: str | None,
    allowed_users: list[str],
    pairing_mode: str,
) -> str:
    config = config_manager.load()
    config.setdefault("channels", {}).setdefault("records", {})
    channel_id = channel_kind

    auth_ref = None
    if channel_kind == "telegram" and bot_token:
        env_key = "TELEGRAM_BOT_TOKEN"
        config_manager.upsert_env_secret(env_key, bot_token)
        auth_ref = env_key

    config["channels"]["records"][channel_id] = {
        "channel_kind": channel_kind,
        "status": "configured",
        "pairing_mode": pairing_mode,
        "auth_ref": auth_ref,
        "allowed_users": allowed_users,
    }
    config_manager.save(config)

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO channel_installations(channel_id, channel_kind, status, pairing_mode, auth_ref)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_kind=excluded.channel_kind,
                status=excluded.status,
                pairing_mode=excluded.pairing_mode,
                auth_ref=excluded.auth_ref,
                updated_at=CURRENT_TIMESTAMP
            """,
            (channel_id, channel_kind, "configured", pairing_mode, auth_ref),
        )
        conn.execute("DELETE FROM allowlist_entries WHERE channel_id = ?", (channel_id,))
        for user_id in allowed_users:
            conn.execute(
                "INSERT INTO allowlist_entries(channel_id, external_user_id, role) VALUES (?, ?, ?)",
                (channel_id, user_id, "paired_user"),
            )
        conn.commit()

    return f"Configured channel '{channel_kind}' with pairing mode '{pairing_mode}'."
