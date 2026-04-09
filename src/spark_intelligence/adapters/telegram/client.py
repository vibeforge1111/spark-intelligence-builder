from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib import parse, request


Transport = Callable[[str, dict[str, Any] | None], dict[str, Any]]


@dataclass
class TelegramBotApiClient:
    token: str
    transport: Transport | None = None

    def __post_init__(self) -> None:
        self.base_url = f"https://api.telegram.org/bot{self.token}"

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
        payload = {"chat_id": chat_id, "text": text}
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
            payload["caption"] = caption
        if self.transport is not None:
            return self.transport("sendAudio", payload)
        return self._call_multipart(
            "sendAudio",
            fields={"chat_id": chat_id, "caption": caption},
            file_field="audio",
            filename=filename,
            mime_type=str(payload["mime_type"]),
            file_bytes=audio_bytes,
        )

    def get_file(self, *, file_id: str) -> dict[str, Any]:
        response = self._call("getFile", {"file_id": file_id})
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Telegram API getFile returned no file metadata.")
        return result

    def download_file(self, *, file_path: str) -> bytes:
        url = f"https://api.telegram.org/file/bot{self.token}/{str(file_path).lstrip('/')}"
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=30) as response:
            return response.read()

    def _call(self, method: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(method, payload)

        url = f"{self.base_url}/{method}"
        encoded_payload = None
        headers = {}
        if payload is not None:
            encoded_payload = parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = request.Request(url, data=encoded_payload, headers=headers, method="POST")
        with request.urlopen(req, timeout=30) as response:
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
            f"{self.base_url}/{method}",
            data=b"".join(body_parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
        return self._decode_response(method, body)

    def _decode_response(self, method: str, body: str) -> dict[str, Any]:
        data = json.loads(body)
        if not data.get("ok"):
            description = data.get("description", "unknown Telegram API error")
            raise RuntimeError(f"Telegram API {method} failed: {description}")
        return data
