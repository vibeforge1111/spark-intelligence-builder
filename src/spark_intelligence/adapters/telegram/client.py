from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, parse, request

from spark_intelligence.security.redaction import redact_text


Transport = Callable[[str, dict[str, Any] | None], dict[str, Any]]
TELEGRAM_BOT_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+")


@dataclass
class TelegramBotApiClient:
    token: str
    transport: Transport | None = None

    def __post_init__(self) -> None:
        self.api_root = "https://api.telegram.org"

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe", None)

    def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset
        response = self._call("getUpdates", payload)
        result = response.get("result")
        return result if isinstance(result, list) else []

    def send_message(self, *, chat_id: str, text: str) -> dict[str, Any]:
        payload = {"chat_id": chat_id, "text": redact_text(text)}
        return self._call("sendMessage", payload)

    def send_audio(
        self,
        *,
        chat_id: str,
        audio_bytes: bytes,
        filename: str,
        caption: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "audio_bytes": audio_bytes,
            "filename": filename,
            "mime_type": mime_type or "audio/mpeg",
        }
        if caption:
            payload["caption"] = redact_text(caption)
        if self.transport is not None:
            return self.transport("sendAudio", payload)
        return self._call_multipart(
            "sendAudio",
            fields={"chat_id": chat_id, "caption": redact_text(caption) if caption is not None else None},
            file_field="audio",
            filename=filename,
            mime_type=str(payload["mime_type"]),
            file_bytes=audio_bytes,
        )

    def send_voice(
        self,
        *,
        chat_id: str,
        voice_bytes: bytes,
        filename: str,
        caption: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "voice_bytes": voice_bytes,
            "filename": filename,
            "mime_type": mime_type or "audio/ogg",
        }
        if caption:
            payload["caption"] = redact_text(caption)
        if self.transport is not None:
            return self.transport("sendVoice", payload)
        return self._call_multipart(
            "sendVoice",
            fields={"chat_id": chat_id, "caption": redact_text(caption) if caption is not None else None},
            file_field="voice",
            filename=filename,
            mime_type=str(payload["mime_type"]),
            file_bytes=voice_bytes,
        )

    def send_document(
        self,
        *,
        chat_id: str,
        document_bytes: bytes,
        filename: str,
        caption: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "document_bytes": document_bytes,
            "filename": filename,
            "mime_type": mime_type or "application/octet-stream",
        }
        if caption:
            payload["caption"] = redact_text(caption)
        if self.transport is not None:
            return self.transport("sendDocument", payload)
        return self._call_multipart(
            "sendDocument",
            fields={"chat_id": chat_id, "caption": redact_text(caption) if caption is not None else None},
            file_field="document",
            filename=filename,
            mime_type=str(payload["mime_type"]),
            file_bytes=document_bytes,
        )

    def get_file(self, *, file_id: str) -> dict[str, Any]:
        response = self._call("getFile", {"file_id": file_id})
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Telegram API getFile returned no file metadata.")
        return result

    def download_file(self, *, file_path: str) -> bytes:
        url = f"{self.api_root}/file/bot{self.token}/{str(file_path).lstrip('/')}"
        req = request.Request(url, method="GET")
        with self._urlopen(req, timeout=30) as response:
            return response.read()

    def _call(self, method: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(method, payload)

        url = f"{self.api_root}/bot{self.token}/{method}"
        encoded_payload = None
        headers = {}
        if payload is not None:
            encoded_payload = parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = request.Request(url, data=encoded_payload, headers=headers, method="POST")
        with self._urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
        return self._decode_response(method, body)

    def _call_multipart(
        self,
        method: str,
        *,
        fields: dict[str, Any],
        file_field: str,
        filename: str,
        mime_type: str,
        file_bytes: bytes,
    ) -> dict[str, Any]:
        boundary = f"----SparkTelegram{uuid.uuid4().hex}"
        body_parts: list[bytes] = []
        for name, value in fields.items():
            if value is None:
                continue
            body_parts.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        body_parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        req = request.Request(
            f"{self.api_root}/bot{self.token}/{method}",
            data=b"".join(body_parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with self._urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
        return self._decode_response(method, body)

    def _urlopen(self, req: request.Request, *, timeout: int):
        try:
            return request.urlopen(req, timeout=timeout)
        except error.URLError as exc:
            message = TELEGRAM_BOT_TOKEN_IN_URL.sub("/bot<redacted>", str(exc))
            raise RuntimeError(f"Telegram API request failed: {message}") from exc

    def _decode_response(self, method: str, body: str) -> dict[str, Any]:
        data = json.loads(body)
        if not data.get("ok"):
            description = redact_text(str(data.get("description", "unknown Telegram API error")))
            raise RuntimeError(f"Telegram API {method} failed: {description}")
        return data
