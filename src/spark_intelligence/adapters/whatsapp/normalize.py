from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NormalizedWhatsAppMessage:
    message_id: str
    chat_id: str
    whatsapp_user_id: str
    whatsapp_profile_name: str | None
    text: str
    is_dm: bool
    group_id: str | None


def normalize_whatsapp_message(payload: dict[str, Any]) -> NormalizedWhatsAppMessage:
    message_id = payload.get("id")
    chat_id = payload.get("chat_id")
    whatsapp_user_id = payload.get("from")
    text = payload.get("text")
    if message_id is None or chat_id is None or whatsapp_user_id is None:
        raise ValueError("WhatsApp payload is missing required identifiers")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("WhatsApp payload does not contain a text message")
    group_id = payload.get("group_id")
    is_dm = group_id in {None, "", "null"}
    return NormalizedWhatsAppMessage(
        message_id=str(message_id),
        chat_id=str(chat_id),
        whatsapp_user_id=str(whatsapp_user_id),
        whatsapp_profile_name=payload.get("profile_name"),
        text=text.strip(),
        is_dm=is_dm,
        group_id=str(group_id) if group_id not in {None, ""} else None,
    )
