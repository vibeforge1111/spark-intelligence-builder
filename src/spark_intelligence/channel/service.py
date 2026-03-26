from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient, Transport
from spark_intelligence.adapters.telegram.runtime import record_telegram_auth_result
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


@dataclass
class TelegramBotProfile:
    bot_id: str
    username: str | None
    first_name: str | None
    can_join_groups: bool | None
    can_read_all_group_messages: bool | None
    supports_inline_queries: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "bot_id": self.bot_id,
            "username": self.username,
            "first_name": self.first_name,
            "can_join_groups": self.can_join_groups,
            "can_read_all_group_messages": self.can_read_all_group_messages,
            "supports_inline_queries": self.supports_inline_queries,
        }


@dataclass
class TelegramChannelTestReport:
    ok: bool
    configured: bool
    auth_ref: str | None
    pairing_mode: str | None
    allowed_user_count: int
    bot_profile: dict[str, object] | None
    detail: str

    def to_text(self) -> str:
        lines = ["Telegram channel test"]
        lines.append(f"- configured: {'yes' if self.configured else 'no'}")
        lines.append(f"- auth_ref: {self.auth_ref or 'missing'}")
        lines.append(f"- pairing_mode: {self.pairing_mode or 'unknown'}")
        lines.append(f"- allowed_users: {self.allowed_user_count}")
        if self.bot_profile:
            lines.append(f"- bot_username: @{self.bot_profile.get('username') or 'unknown'}")
            lines.append(f"- bot_id: {self.bot_profile.get('bot_id') or 'unknown'}")
            lines.append(f"- first_name: {self.bot_profile.get('first_name') or 'unknown'}")
        lines.append(f"- result: {self.detail}")
        return "\n".join(lines)

    def to_json(self) -> str:
        import json

        return json.dumps(
            {
                "ok": self.ok,
                "configured": self.configured,
                "auth_ref": self.auth_ref,
                "pairing_mode": self.pairing_mode,
                "allowed_user_count": self.allowed_user_count,
                "bot_profile": self.bot_profile,
                "detail": self.detail,
            },
            indent=2,
        )


def inspect_telegram_bot_token(
    bot_token: str,
    *,
    transport: Transport | None = None,
) -> TelegramBotProfile:
    client = TelegramBotApiClient(token=bot_token, transport=transport)
    try:
        payload = client.get_me()
    except HTTPError as exc:
        raise RuntimeError(f"Telegram auth failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram auth failed: {exc.reason}.") from exc
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Telegram auth succeeded but returned no bot profile.")
    if not result.get("is_bot", True):
        raise RuntimeError("Telegram token resolved to a non-bot account.")
    bot_id = result.get("id")
    if bot_id is None:
        raise RuntimeError("Telegram auth succeeded but bot id was missing.")
    return TelegramBotProfile(
        bot_id=str(bot_id),
        username=str(result.get("username")) if result.get("username") else None,
        first_name=str(result.get("first_name")) if result.get("first_name") else None,
        can_join_groups=_to_optional_bool(result.get("can_join_groups")),
        can_read_all_group_messages=_to_optional_bool(result.get("can_read_all_group_messages")),
        supports_inline_queries=_to_optional_bool(result.get("supports_inline_queries")),
    )


def render_telegram_botfather_guide(
    *,
    allowed_users: list[str],
    pairing_mode: str,
) -> str:
    allowed_flag = " ".join(f"--allowed-user {user_id}" for user_id in allowed_users) if allowed_users else ""
    command = f"spark-intelligence channel telegram-onboard --bot-token <token> --pairing-mode {pairing_mode}".strip()
    if allowed_flag:
        command = f"{command} {allowed_flag}"
    lines = [
        "Telegram BotFather onboarding",
        "1. Open Telegram and start a chat with @BotFather.",
        "2. Run /newbot and choose a display name and username ending in 'bot'.",
        "3. Copy the API token BotFather returns.",
        f"4. Run `{command}`.",
        "5. Send /start to your bot from the account you want to pair first.",
        "6. Run `spark-intelligence gateway start` once the token is stored.",
        "",
        "Notes:",
        "- Spark Intelligence is DM-first for Telegram v1.",
        "- Pairing mode 'pairing' is the safer default.",
        "- You can rerun `spark-intelligence channel telegram-onboard` anytime to revalidate or rotate the token.",
    ]
    return "\n".join(lines)


def add_channel(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    channel_kind: str,
    bot_token: str | None,
    allowed_users: list[str],
    pairing_mode: str,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    config = config_manager.load()
    config.setdefault("channels", {}).setdefault("records", {})
    channel_id = channel_kind
    existing_record = config["channels"]["records"].get(channel_id)
    existing_record = existing_record if isinstance(existing_record, dict) else {}

    auth_ref = str(existing_record.get("auth_ref")) if existing_record.get("auth_ref") else None
    if channel_kind == "telegram" and bot_token:
        env_key = "TELEGRAM_BOT_TOKEN"
        config_manager.upsert_env_secret(env_key, bot_token)
        auth_ref = env_key
    if channel_kind == "discord" and bot_token:
        env_key = "DISCORD_BOT_TOKEN"
        config_manager.upsert_env_secret(env_key, bot_token)
        auth_ref = env_key
    if channel_kind == "whatsapp" and bot_token:
        env_key = "WHATSAPP_BOT_TOKEN"
        config_manager.upsert_env_secret(env_key, bot_token)
        auth_ref = env_key

    resolved_status = status or (str(existing_record.get("status")) if existing_record.get("status") else "enabled")
    record = dict(existing_record)
    record.update(
        {
        "channel_kind": channel_kind,
        "status": resolved_status,
        "pairing_mode": pairing_mode,
        "auth_ref": auth_ref,
        "allowed_users": allowed_users,
        }
    )
    if metadata:
        record.update(metadata)
    config["channels"]["records"][channel_id] = record
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
            (channel_id, channel_kind, resolved_status, pairing_mode, auth_ref),
        )
        conn.execute(
            "DELETE FROM allowlist_entries WHERE channel_id = ? AND role = 'configured_user'",
            (channel_id,),
        )
        for user_id in allowed_users:
            conn.execute(
                "INSERT INTO allowlist_entries(channel_id, external_user_id, role) VALUES (?, ?, 'configured_user')",
                (channel_id, user_id),
            )
        conn.commit()

    return f"Configured channel '{channel_kind}' with pairing mode '{pairing_mode}' status '{resolved_status}'."


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


def test_configured_telegram_channel(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    transport: Transport | None = None,
) -> TelegramChannelTestReport:
    config = config_manager.load()
    record = config.get("channels", {}).get("records", {}).get("telegram")
    if not isinstance(record, dict):
        return TelegramChannelTestReport(
            ok=False,
            configured=False,
            auth_ref=None,
            pairing_mode=None,
            allowed_user_count=0,
            bot_profile=None,
            detail="Telegram channel is not configured.",
        )

    auth_ref = record.get("auth_ref")
    env_map = config_manager.read_env_map()
    token = env_map.get(auth_ref) if auth_ref else None
    with state_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(DISTINCT external_user_id) AS c FROM allowlist_entries WHERE channel_id = 'telegram'"
        ).fetchone()["c"]
    if not token:
        record_telegram_auth_result(
            state_db=state_db,
            status="missing",
            error="Telegram auth ref is missing or unresolved.",
        )
        return TelegramChannelTestReport(
            ok=False,
            configured=True,
            auth_ref=str(auth_ref) if auth_ref else None,
            pairing_mode=str(record.get("pairing_mode")) if record.get("pairing_mode") else None,
            allowed_user_count=int(count),
            bot_profile=None,
            detail="Telegram auth ref is missing or unresolved.",
        )

    try:
        profile = inspect_telegram_bot_token(token, transport=transport)
    except RuntimeError as exc:
        record_telegram_auth_result(state_db=state_db, status="failed", error=str(exc))
        return TelegramChannelTestReport(
            ok=False,
            configured=True,
            auth_ref=str(auth_ref) if auth_ref else None,
            pairing_mode=str(record.get("pairing_mode")) if record.get("pairing_mode") else None,
            allowed_user_count=int(count),
            bot_profile=None,
            detail=str(exc),
        )

    record_telegram_auth_result(
        state_db=state_db,
        status="ok",
        bot_username=profile.username,
        error=None,
    )
    return TelegramChannelTestReport(
        ok=True,
        configured=True,
        auth_ref=str(auth_ref) if auth_ref else None,
        pairing_mode=str(record.get("pairing_mode")) if record.get("pairing_mode") else None,
        allowed_user_count=int(count),
        bot_profile=profile.to_dict(),
        detail="Telegram auth check passed.",
    )


def _to_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)
