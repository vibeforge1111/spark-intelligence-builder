from urllib import error

import pytest

from spark_intelligence.adapters.telegram import client as telegram_client
from spark_intelligence.adapters.telegram.client import TelegramBotApiClient


def test_telegram_client_does_not_store_token_bearing_base_url() -> None:
    client = TelegramBotApiClient(token="123456:secret-token")

    assert not hasattr(client, "base_url")
    assert client.api_root == "https://api.telegram.org"


def test_telegram_urlopen_errors_redact_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        raise error.URLError("https://api.telegram.org/bot123456:secret-token/getMe exploded")

    monkeypatch.setattr(telegram_client.request, "urlopen", fake_urlopen)
    client = TelegramBotApiClient(token="123456:secret-token")

    with pytest.raises(RuntimeError) as exc_info:
        client.get_me()

    message = str(exc_info.value)
    assert "123456:secret-token" not in message
    assert "/bot<redacted>" in message


def test_telegram_send_message_redacts_payload_before_transport() -> None:
    captured: dict[str, object] = {}

    def fake_transport(method: str, payload: dict[str, object] | None) -> dict[str, object]:
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True, "result": {}}

    client = TelegramBotApiClient(token="123456:secret-token", transport=fake_transport)

    client.send_message(chat_id="123", text="token sk-proj-abcdefghijklmnopqrstuvwxyz123456")

    assert captured["method"] == "sendMessage"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "sk-proj-" not in str(payload["text"])
    assert "<redacted api key>" in str(payload["text"])


def test_telegram_api_error_description_is_redacted() -> None:
    client = TelegramBotApiClient(token="123456:secret-token")

    with pytest.raises(RuntimeError) as exc_info:
        client._decode_response("sendMessage", '{"ok": false, "description": "bad sk-proj-abcdefghijklmnopqrstuvwxyz123456"}')

    message = str(exc_info.value)
    assert "sk-proj-" not in message
    assert "<redacted api key>" in message
