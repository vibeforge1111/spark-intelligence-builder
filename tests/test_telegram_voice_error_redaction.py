from spark_intelligence.adapters.telegram.runtime import _safe_voice_error_message


def test_safe_voice_error_message_redacts_authorization_header_values() -> None:
    message = _safe_voice_error_message(
        RuntimeError(
            "Voice provider failed: Authorization: Token placeholder-token-value-123456, "
            "Authorization: ApiKey placeholder-apikey-value-123456, "
            "Authorization: OAuth placeholder-oauth-value-123456, "
            "and Bearer placeholder-bearer-value-123456"
        )
    )

    assert "placeholder-token-value-123456" not in message
    assert "placeholder-apikey-value-123456" not in message
    assert "placeholder-oauth-value-123456" not in message
    assert "placeholder-bearer-value-123456" not in message
    assert "Authorization: Token ***" in message
    assert "Authorization: ApiKey ***" in message
    assert "Authorization: OAuth ***" in message
    assert "Bearer ***" in message
