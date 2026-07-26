from __future__ import annotations

import json
import socket
from dataclasses import replace
from unittest.mock import patch

import pytest

from spark_intelligence.auth.providers import get_provider_spec
from spark_intelligence.auth.service import (
    exchange_oauth_authorization_code,
    exchange_oauth_refresh_token,
)

_TOKEN_EXCHANGE_FAILURE = (
    r"^OAuth token exchange for 'openai-codex' failed safely\. "
    r"Check network connectivity and the provider OAuth configuration, then retry\.$"
)
_REFRESH_FAILURE = (
    r"^OAuth refresh for 'openai-codex' failed safely\. "
    r"Check network connectivity and the provider OAuth configuration, then retry\.$"
)


def _addrinfo(*addresses: str) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
        rows.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return rows


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: object | None = None,
        body: bytes | None = None,
    ) -> None:
        self.status = status
        self._body = (
            body
            if body is not None
            else json.dumps(
                payload if payload is not None else {"access_token": "access"}
            ).encode("utf-8")
        )

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            return self._body
        return self._body[:amount]


class _FakeConnection:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.response = response or _FakeResponse()
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, target: str, *, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, target, body, headers))

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "token_url",
    [
        "http://auth.example/oauth/token",
        "https://user:secret@auth.example/oauth/token",
        "https://auth.example/oauth/token?next=internal",
        "https://auth.example/oauth/token#fragment",
        "https://auth.example:99999/oauth/token",
    ],
)
def test_oauth_token_exchange_rejects_unsafe_registry_url_without_echoing_it(
    token_url: str,
) -> None:
    spec = get_provider_spec("openai-codex")
    assert spec.oauth is not None
    unsafe_spec = replace(spec, oauth=replace(spec.oauth, token_url=token_url))

    with patch("spark_intelligence.auth.service.get_provider_spec", return_value=unsafe_spec):
        with pytest.raises(
            RuntimeError,
            match=_TOKEN_EXCHANGE_FAILURE,
        ) as raised:
            exchange_oauth_authorization_code(
                provider="openai-codex",
                code="sensitive-authorization-code",
                redirect_uri="http://127.0.0.1:1455/auth/callback",
                code_verifier="sensitive-code-verifier",
            )

    assert token_url not in str(raised.value)
    assert "sensitive-authorization-code" not in str(raised.value)
    assert "sensitive-code-verifier" not in str(raised.value)


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("172.31.255.254",),
        ("192.168.1.2",),
        ("::1",),
        ("fc00::1",),
        ("8.8.8.8", "10.0.0.4"),
    ],
)
def test_oauth_token_exchange_rejects_private_or_mixed_dns_answers(
    addresses: tuple[str, ...],
) -> None:
    with patch(
        "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
        return_value=_addrinfo(*addresses),
    ):
        with pytest.raises(
            RuntimeError,
            match=_TOKEN_EXCHANGE_FAILURE,
        ):
            exchange_oauth_authorization_code(
                provider="openai-codex",
                code="sensitive-authorization-code",
                redirect_uri="http://127.0.0.1:1455/auth/callback",
                code_verifier="sensitive-code-verifier",
            )


def test_oauth_token_exchange_resolves_once_and_connects_to_validated_address() -> None:
    connection = _FakeConnection()
    with patch(
        "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
        return_value=_addrinfo("8.8.8.8"),
    ) as resolver, patch(
        "spark_intelligence.security.https_endpoint._connection_for_endpoint",
        return_value=connection,
    ) as connection_factory, patch.dict(
        "os.environ",
        {"HTTPS_PROXY": "http://127.0.0.1:8080"},
        clear=False,
    ):
        payload = exchange_oauth_authorization_code(
            provider="openai-codex",
            code="authorization-code",
            redirect_uri="http://127.0.0.1:1455/auth/callback",
            code_verifier="code-verifier",
        )

    assert payload == {"access_token": "access"}
    resolver.assert_called_once()
    endpoint = connection_factory.call_args.args[0]
    assert endpoint.hostname == "auth.openai.com"
    assert connection_factory.call_args.args[1] == "8.8.8.8"
    assert connection_factory.call_args.kwargs == {"timeout_seconds": 20}
    assert connection.requests[0][0:2] == ("POST", "/oauth/token")
    assert connection.requests[0][3] == {"Content-Type": "application/x-www-form-urlencoded"}
    assert b"grant_type=authorization_code" in connection.requests[0][2]
    assert connection.closed


def test_oauth_refresh_uses_same_pinned_transport() -> None:
    connection = _FakeConnection()
    with patch(
        "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
        return_value=_addrinfo("8.8.4.4"),
    ), patch(
        "spark_intelligence.security.https_endpoint._connection_for_endpoint",
        return_value=connection,
    ):
        payload = exchange_oauth_refresh_token(
            provider="openai-codex",
            refresh_token="sensitive-refresh-token",
        )

    assert payload == {"access_token": "access"}
    assert b"grant_type=refresh_token" in connection.requests[0][2]
    assert b"sensitive-refresh-token" in connection.requests[0][2]
    assert connection.closed


def test_oauth_redirect_is_blocked_without_following_location_or_leaking_secret() -> None:
    connection = _FakeConnection(
        _FakeResponse(status=302, payload={"access_token": "ignored"})
    )
    with patch(
        "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
        return_value=_addrinfo("8.8.8.8"),
    ), patch(
        "spark_intelligence.security.https_endpoint._connection_for_endpoint",
        return_value=connection,
    ):
        with pytest.raises(
            RuntimeError,
            match=_REFRESH_FAILURE,
        ) as raised:
            exchange_oauth_refresh_token(
                provider="openai-codex",
                refresh_token="sensitive-refresh-token",
            )

    assert "sensitive-refresh-token" not in str(raised.value)
    assert connection.closed


def test_oauth_response_is_bounded_before_json_parsing() -> None:
    connection = _FakeConnection(_FakeResponse(body=b"x" * (1024 * 1024 + 1)))
    with patch(
        "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
        return_value=_addrinfo("8.8.8.8"),
    ), patch(
        "spark_intelligence.security.https_endpoint._connection_for_endpoint",
        return_value=connection,
    ):
        with pytest.raises(
            RuntimeError,
            match=_REFRESH_FAILURE,
        ):
            exchange_oauth_refresh_token(
                provider="openai-codex",
                refresh_token="sensitive-refresh-token",
            )

    assert connection.closed
