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
    media_turn: dict[str, Any] | None
    is_dm: bool


def _clean_media_turn(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schema") != "spark.media_turn.v1":
        return None
    media_kind = str(value.get("media_kind") or "unsupported").strip()
    if media_kind not in {"photo", "document", "voice", "audio", "unsupported"}:
        media_kind = "unsupported"
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    analysis_policy = value.get("analysis_policy") if isinstance(value.get("analysis_policy"), dict) else {}
    authority = value.get("authority") if isinstance(value.get("authority"), dict) else {}
    cleaned: dict[str, Any] = {
        "schema": "spark.media_turn.v1",
        "media_kind": media_kind,
        "chat_surface": "telegram",
        "analysis_policy": {
            "can_read": bool(analysis_policy.get("can_read")),
            "can_store": False,
            "can_execute": False,
        },
        "authority": {
            "requires_turn_intent": True,
            "mutation_allowed": False,
        },
        "source": {
            "has_caption": bool(source.get("has_caption")),
            "has_photo": bool(source.get("has_photo")),
            "has_document": bool(source.get("has_document")),
            "has_voice": bool(source.get("has_voice")),
            "has_audio": bool(source.get("has_audio")),
        },
    }
    turn_ref = str(value.get("turn_ref") or "").strip()
    if turn_ref.startswith("media:sha256:") and len(turn_ref) <= 32:
        cleaned["turn_ref"] = turn_ref
    caption = str(value.get("caption_text") or "").strip()
    if caption:
        cleaned["caption_text"] = " ".join(caption.split())[:500]
    mime_family = str(source.get("mime_family") or "").strip().lower()
    if mime_family in {"image", "audio", "video", "application", "text"}:
        cleaned["source"]["mime_family"] = mime_family
    if source.get("filename_present") is not None:
        cleaned["source"]["filename_present"] = bool(source.get("filename_present"))
    return cleaned


def normalize_telegram_update(update: dict[str, Any], *, channel_id: str = "telegram") -> NormalizedTelegramUpdate:
    message = update.get("message")
    if not isinstance(message, dict):
        raise ValueError("Telegram update does not contain a supported message payload")

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text_payload = message.get("text")
    voice_payload = message.get("voice")
    audio_payload = message.get("audio")
    photo_payload = message.get("photo")
    document_payload = message.get("document")
    caption_text = str(message.get("caption") or "").strip() or None
    message_kind = "text"
    if isinstance(text_payload, str) and text_payload.strip():
        text = text_payload.strip()
    elif isinstance(voice_payload, dict):
        text = "[voice message]"
        message_kind = "voice"
    elif isinstance(audio_payload, dict):
        text = "[audio message]"
        message_kind = "audio"
    elif isinstance(photo_payload, list) and photo_payload:
        text = caption_text or "[photo message]"
        message_kind = "photo"
    elif isinstance(document_payload, dict):
        text = caption_text or "[document message]"
        message_kind = "document"
    else:
        raise ValueError("Telegram update does not contain a supported text, voice, audio, photo, or document message")

    update_id = update.get("update_id")
    message_id = message.get("message_id")
    chat_id = chat.get("id")
    telegram_user_id = sender.get("id")

    if update_id is None or message_id is None or chat_id is None or telegram_user_id is None:
        raise ValueError("Telegram update is missing required identifiers")

    chat_type = str(chat.get("type") or "")
    is_dm = chat_type == "private"
    if isinstance(voice_payload, dict):
        media_payload = voice_payload
    elif isinstance(audio_payload, dict):
        media_payload = audio_payload
    elif isinstance(document_payload, dict):
        media_payload = document_payload
    elif isinstance(photo_payload, list) and photo_payload:
        media_payload = photo_payload[-1] if isinstance(photo_payload[-1], dict) else {}
    else:
        media_payload = {}
    media_file_id = str(media_payload.get("file_id")).strip() if media_payload.get("file_id") else None
    media_mime_type = str(media_payload.get("mime_type")).strip() if media_payload.get("mime_type") else None
    media_duration_seconds = int(media_payload.get("duration")) if media_payload.get("duration") is not None else None
    spark_media = message.get("spark_media") if isinstance(message.get("spark_media"), dict) else {}
    media_audio_base64 = str(spark_media.get("audio_base64")).strip() if spark_media.get("audio_base64") else None
    media_filename = str(spark_media.get("filename")).strip() if spark_media.get("filename") else None
    media_source = str(spark_media.get("source")).strip() if spark_media.get("source") else None
    media_turn = _clean_media_turn(update.get("spark_media_turn")) or _clean_media_turn(message.get("spark_media_turn"))

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
        media_turn=media_turn,
        is_dm=is_dm,
    )
