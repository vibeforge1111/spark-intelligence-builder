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
