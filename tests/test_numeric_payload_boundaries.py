from __future__ import annotations

import pytest

from spark_intelligence.adapters.telegram.normalize import normalize_telegram_update
from spark_intelligence.schedule_bridge.service import _human_summary


def _telegram_update(*, update_id: object = 10, message_id: object = 20, duration: object = 3) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": 30, "username": "operator"},
            "chat": {"id": 30, "type": "private"},
            "voice": {"file_id": "voice-1", "duration": duration},
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (("update_id", "not-a-number"), ("message_id", "not-a-number"), ("update_id", True)),
)
def test_required_telegram_identifiers_reject_non_numeric_values(field: str, value: object) -> None:
    payload = _telegram_update(**{field: value})

    with pytest.raises(ValueError, match="non-numeric update_id or message_id"):
        normalize_telegram_update(payload)


def test_invalid_optional_media_duration_is_ignored() -> None:
    normalized = normalize_telegram_update(_telegram_update(duration="unknown"))

    assert normalized.update_id == 10
    assert normalized.message_id == 20
    assert normalized.media_duration_seconds is None


def test_numeric_string_identifiers_and_duration_remain_supported() -> None:
    normalized = normalize_telegram_update(_telegram_update(update_id="10", message_id="20", duration="3"))

    assert normalized.update_id == 10
    assert normalized.message_id == 20
    assert normalized.media_duration_seconds == 3


def test_schedule_summary_defaults_invalid_rounds_to_one() -> None:
    summary = _human_summary({"action": "loop", "payload": {"chipKey": "research", "rounds": "many"}})

    assert summary == "Run 1 loop round on research"
