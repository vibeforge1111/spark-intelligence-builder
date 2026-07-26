from __future__ import annotations

import pytest

from spark_intelligence.gateway.runtime import _load_gateway_json_object


def test_gateway_payload_rejects_malformed_json_without_local_details(tmp_path) -> None:
    payload_path = tmp_path / "private-update.json"
    payload_path.write_text('{"token":"secret-value"', encoding="utf-8")

    with pytest.raises(ValueError) as captured:
        _load_gateway_json_object(payload_path, surface="Telegram update")

    assert str(captured.value) == "Telegram update payload must contain valid JSON."
    assert "secret-value" not in str(captured.value)
    assert str(payload_path) not in str(captured.value)


def test_gateway_payload_requires_an_object(tmp_path) -> None:
    payload_path = tmp_path / "update.json"
    payload_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a JSON object"):
        _load_gateway_json_object(payload_path, surface="Discord message")
