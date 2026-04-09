from __future__ import annotations

import json
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
        data = json.loads(body)
        if not data.get("ok"):
            description = data.get("description", "unknown Telegram API error")
            raise RuntimeError(f"Telegram API {method} failed: {description}")
        return data
