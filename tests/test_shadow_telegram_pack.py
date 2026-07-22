from __future__ import annotations

import pytest

from spark_intelligence.cli import _load_shadow_telegram_pack


def test_shadow_telegram_pack_rejects_malformed_json_without_echoing_payload(tmp_path) -> None:
    pack = tmp_path / "private-pack.json"
    secret_payload = '[{"message":"secret-user-text"}'
    pack.write_text(secret_payload, encoding="utf-8")

    with pytest.raises(ValueError) as captured:
        _load_shadow_telegram_pack(pack)

    assert str(captured.value) == "Shadow Telegram pack must contain valid JSON."
    assert "secret-user-text" not in str(captured.value)
    assert str(pack) not in str(captured.value)
