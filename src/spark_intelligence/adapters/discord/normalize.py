from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NormalizedDiscordMessage:
    message_id: str
    channel_id: str
    discord_user_id: str
    discord_username: str | None
    content: str
    is_dm: bool
    guild_id: str | None


def normalize_discord_message(payload: dict[str, Any]) -> NormalizedDiscordMessage:
    content = payload.get("content")
    author = payload.get("author") or {}
    message_id = payload.get("id")
    channel_id = payload.get("channel_id")
    discord_user_id = author.get("id")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Discord payload does not contain a text message")
    if message_id is None or channel_id is None or discord_user_id is None:
        raise ValueError("Discord payload is missing required identifiers")
    guild_id = payload.get("guild_id")
    is_dm = guild_id in {None, "", "null"}
    return NormalizedDiscordMessage(
        message_id=str(message_id),
        channel_id=str(channel_id),
        discord_user_id=str(discord_user_id),
        discord_username=author.get("username"),
        content=content.strip(),
        is_dm=is_dm,
        guild_id=str(guild_id) if guild_id not in {None, ""} else None,
    )
