from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NormalizedTelegramUpdate:
    update_id: int
    channel_id: str
    message_id: int
    telegram_user_id: str
    telegram_username: str | None
    chat_id: str
    chat_type: str
    text: str
    is_dm: bool


def normalize_telegram_update(update: dict[str, Any], *, channel_id: str = "telegram") -> NormalizedTelegramUpdate:
    message = update.get("message")
    if not isinstance(message, dict):
        raise ValueError("Telegram update does not contain a supported message payload")

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = message.get("text")

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Telegram update does not contain a text message")

    update_id = update.get("update_id")
    message_id = message.get("message_id")
    chat_id = chat.get("id")
    telegram_user_id = sender.get("id")

    if update_id is None or message_id is None or chat_id is None or telegram_user_id is None:
        raise ValueError("Telegram update is missing required identifiers")

    chat_type = str(chat.get("type") or "")
    is_dm = chat_type == "private"

    return NormalizedTelegramUpdate(
        update_id=int(update_id),
        channel_id=channel_id,
        message_id=int(message_id),
        telegram_user_id=str(telegram_user_id),
        telegram_username=sender.get("username"),
        chat_id=str(chat_id),
        chat_type=chat_type,
        text=text.strip(),
        is_dm=is_dm,
    )
