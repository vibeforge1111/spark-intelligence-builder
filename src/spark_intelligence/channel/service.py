from __future__ import annotations

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import approve_pairing
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
        "status": "enabled",
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
            (channel_id, channel_kind, "enabled", pairing_mode, auth_ref),
        )
        conn.commit()

    for user_id in allowed_users:
        approve_pairing(
            state_db=state_db,
            channel_id=channel_id,
            external_user_id=user_id,
            display_name=f"{channel_kind} user {user_id}",
        )

    return f"Configured channel '{channel_kind}' with pairing mode '{pairing_mode}'."


def set_channel_status(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    channel_id: str,
    status: str,
) -> str:
    config = config_manager.load()
    records = config.setdefault("channels", {}).setdefault("records", {})
    record = records.get(channel_id)
    if not isinstance(record, dict):
        raise ValueError(f"Unknown channel '{channel_id}'.")
    record["status"] = status
    config_manager.save(config)

    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT channel_kind, pairing_mode, auth_ref FROM channel_installations WHERE channel_id = ? LIMIT 1",
            (channel_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown channel installation '{channel_id}'.")
        conn.execute(
            """
            UPDATE channel_installations
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = ?
            """,
            (status, channel_id),
        )
        conn.commit()

    return f"Set channel '{channel_id}' status = {status}"
