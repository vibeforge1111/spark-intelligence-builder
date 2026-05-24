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
    message_kind: str
    media_file_id: str | None
    media_mime_type: str | None
    media_duration_seconds: int | None
    media_audio_base64: str | None
    media_filename: str | None
    media_source: str | None
    caption_text: str | None
    is_dm: bool


def normalize_telegram_update(update: dict[str, Any], *, channel_id: str = "telegram") -> NormalizedTelegramUpdate:
    message = update.get("message")
    if not isinstance(message, dict):
        raise ValueError("Telegram update does not contain a supported message payload")

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text_payload = message.get("text")
    voice_payload = message.get("voice")
    audio_payload = message.get("audio")
    message_kind = "text"
    if isinstance(text_payload, str) and text_payload.strip():
        text = text_payload.strip()
    elif isinstance(voice_payload, dict):
        text = "[voice message]"
        message_kind = "voice"
    elif isinstance(audio_payload, dict):
        text = "[audio message]"
        message_kind = "audio"
    else:
        raise ValueError("Telegram update does not contain a supported text, voice, or audio message")

    update_id = update.get("update_id")
    message_id = message.get("message_id")
    chat_id = chat.get("id")
    telegram_user_id = sender.get("id")

    if update_id is None or message_id is None or chat_id is None or telegram_user_id is None:
        raise ValueError("Telegram update is missing required identifiers")

    chat_type = str(chat.get("type") or "")
    is_dm = chat_type == "private"
    media_payload = voice_payload if isinstance(voice_payload, dict) else (audio_payload if isinstance(audio_payload, dict) else {})
    media_file_id = str(media_payload.get("file_id")).strip() if media_payload.get("file_id") else None
    media_mime_type = str(media_payload.get("mime_type")).strip() if media_payload.get("mime_type") else None
    media_duration_seconds = int(media_payload.get("duration")) if media_payload.get("duration") is not None else None
    spark_media = message.get("spark_media") if isinstance(message.get("spark_media"), dict) else {}
    media_audio_base64 = str(spark_media.get("audio_base64")).strip() if spark_media.get("audio_base64") else None
    media_filename = str(spark_media.get("filename")).strip() if spark_media.get("filename") else None
    media_source = str(spark_media.get("source")).strip() if spark_media.get("source") else None
    caption_text = str(message.get("caption") or "").strip() or None

    return NormalizedTelegramUpdate(
        update_id=int(update_id),
        channel_id=channel_id,
        message_id=int(message_id),
        telegram_user_id=str(telegram_user_id),
        telegram_username=sender.get("username"),
        chat_id=str(chat_id),
        chat_type=chat_type,
        text=text,
        message_kind=message_kind,
        media_file_id=media_file_id,
        media_mime_type=media_mime_type,
        media_duration_seconds=media_duration_seconds,
        media_audio_base64=media_audio_base64,
        media_filename=media_filename,
        media_source=media_source,
        caption_text=caption_text,
        is_dm=is_dm,
    )
