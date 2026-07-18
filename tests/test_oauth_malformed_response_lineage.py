from __future__ import annotations

import traceback
from collections.abc import Callable
from unittest.mock import patch

import pytest

from spark_intelligence.auth.service import (
    exchange_oauth_authorization_code,
    exchange_oauth_refresh_token,
)


def _authorization_exchange() -> dict[str, object]:
    return exchange_oauth_authorization_code(
        provider="openai-codex",
        code="sensitive-authorization-code",
        redirect_uri="http://127.0.0.1:1455/auth/callback",
        code_verifier="sensitive-code-verifier",
    )


def _refresh_exchange() -> dict[str, object]:
    return exchange_oauth_refresh_token(
        provider="openai-codex",
        refresh_token="sensitive-refresh-token",
    )


@pytest.mark.parametrize(
    ("exchange", "expected_message"),
    (
        (
            _authorization_exchange,
            "OAuth token exchange for 'openai-codex' returned an invalid response.",
        ),
        (
            _refresh_exchange,
            "OAuth refresh for 'openai-codex' returned an invalid response.",
        ),
    ),
)
@pytest.mark.parametrize(
    "body",
    (
        b"<html>secret-provider-body</html>",
        b"\xffsecret-provider-body",
        b"[]",
        b'"secret-provider-body"',
        b"null",
    ),
)
def test_oauth_malformed_response_lineage_uses_one_nonreflective_owner(
    exchange: Callable[[], dict[str, object]],
    expected_message: str,
    body: bytes,
) -> None:
    with patch(
        "spark_intelligence.auth.service.resolve_public_https_endpoint",
        return_value=object(),
    ), patch(
        "spark_intelligence.auth.service.post_https_bytes",
        return_value=body,
    ):
        with pytest.raises(RuntimeError) as raised:
            exchange()

    rendered = "".join(traceback.format_exception(raised.value))
    message = str(raised.value)
    assert message == expected_message
    assert raised.value.__cause__ is None
    if raised.value.__context__ is not None:
        assert raised.value.__suppress_context__
    assert "secret-provider-body" not in rendered
    assert "sensitive-authorization-code" not in message
    assert "sensitive-code-verifier" not in message
    assert "sensitive-refresh-token" not in message
