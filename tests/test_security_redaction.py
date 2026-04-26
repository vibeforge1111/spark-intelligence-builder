from __future__ import annotations

from spark_intelligence.security.redaction import redact_text


def test_redact_text_masks_common_credential_shapes() -> None:
    text = "\n".join(
        [
            "TELEGRAM_BOT_TOKEN=123456789:AAGabcdefghijklmnopqrstuvwxyz123",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            '"api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz123456"',
            "db=postgres://user:pass@example.com/db",
            "call me at 555-123-4567",
        ]
    )

    redacted = redact_text(text)

    assert "123456789:AAG" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in redacted
    assert "sk-proj-" not in redacted
    assert "postgres://user:pass" not in redacted
    assert "555-123-4567" not in redacted
    assert "Bearer <redacted>" in redacted
