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


def test_telegram_send_message_preserves_user_facing_payload_before_transport() -> None:
    captured: dict[str, object] = {}
    api_key_fixture = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"

    def fake_transport(method: str, payload: dict[str, object] | None) -> dict[str, object]:
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True, "result": {}}

    client = TelegramBotApiClient(token="123456:secret-token", transport=fake_transport)

    client.send_message(chat_id="123", text=f"token {api_key_fixture}")

    assert captured["method"] == "sendMessage"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["text"] == f"token {api_key_fixture}"


def test_telegram_document_caption_preserves_user_facing_payload_before_transport() -> None:
    captured: dict[str, object] = {}
    api_key_fixture = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"

    def fake_transport(method: str, payload: dict[str, object] | None) -> dict[str, object]:
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True, "result": {}}

    client = TelegramBotApiClient(token="123456:secret-token", transport=fake_transport)

    client.send_document(
        chat_id="123",
        document_bytes=b"proof",
        filename="proof.txt",
        caption=f"token {api_key_fixture}",
    )

    assert captured["method"] == "sendDocument"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["caption"] == f"token {api_key_fixture}"


def test_telegram_api_error_description_is_redacted() -> None:
    client = TelegramBotApiClient(token="123456:secret-token")
    api_key_fixture = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"

    with pytest.raises(RuntimeError) as exc_info:
        client._decode_response("sendMessage", f'{{"ok": false, "description": "bad {api_key_fixture}"}}')

    message = str(exc_info.value)
    assert "sk-proj-" not in message
    assert "<redacted api key>" in message


def test_download_file_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_file must reject file_path that doesn't start with documents/ or photos/."""
    client = TelegramBotApiClient(token="123456:secret-token")

    with pytest.raises(RuntimeError, match="rejected untrusted file_path"):
        client.download_file(file_path="../etc/passwd")


def test_download_file_rejects_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_file must reject absolute-style paths."""
    client = TelegramBotApiClient(token="123456:secret-token")

    with pytest.raises(RuntimeError, match="rejected untrusted file_path"):
        client.download_file(file_path="/etc/passwd")


def test_download_file_rejects_arbitrary_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_file must reject paths with unexpected prefixes."""
    client = TelegramBotApiClient(token="123456:secret-token")

    with pytest.raises(RuntimeError, match="rejected untrusted file_path"):
        client.download_file(file_path="secrets/token.txt")


def test_download_file_accepts_valid_documents_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_file must accept paths starting with documents/."""
    client = TelegramBotApiClient(token="123456:secret-token")

    def fake_urlopen(req, timeout):
        assert "/file/bot123456:secret-token/documents/file_123.bin" in req.full_url

        class FakeResp:
            def read(self):
                return b"content"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        return FakeResp()

    monkeypatch.setattr(
        "spark_intelligence.adapters.telegram.client.request.urlopen",
        fake_urlopen,
    )
    result = client.download_file(file_path="documents/file_123.bin")
    assert result == b"content"


def test_download_file_accepts_valid_photos_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_file must accept paths starting with photos/."""
    client = TelegramBotApiClient(token="123456:secret-token")

    def fake_urlopen(req, timeout):
        assert "/file/bot123456:secret-token/photos/photo_456.jpg" in req.full_url

        class FakeResp:
            def read(self):
                return b"image-data"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        return FakeResp()

    monkeypatch.setattr(
        "spark_intelligence.adapters.telegram.client.request.urlopen",
        fake_urlopen,
    )
    result = client.download_file(file_path="photos/photo_456.jpg")
    assert result == b"image-data"
