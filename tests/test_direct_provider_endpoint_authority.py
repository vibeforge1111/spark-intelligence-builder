from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import patch

from spark_intelligence.llm.direct_provider import (
    DirectProviderRequest,
    _post_json,
    _resolve_provider_endpoint,
)


def _provider(*, provider_id: str = "custom", base_url: str = "https://api.example.com/v1") -> DirectProviderRequest:
    return DirectProviderRequest(
        provider_id=provider_id,
        provider_kind=provider_id,
        auth_method="api_key_env",
        api_mode="chat_completions",
        base_url=base_url,
        model="test-model",
        secret_value="provider-secret",
    )


def _addrinfo(*addresses: str) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
        rows.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return rows


class _FakeResponse:
    def __init__(self, *, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self._body = json.dumps(payload if payload is not None else {"ok": True}).encode("utf-8")

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


class DirectProviderEndpointAuthorityTests(unittest.TestCase):
    def test_rejects_non_https_userinfo_query_fragment_and_invalid_port(self) -> None:
        rejected = (
            "http://api.example.com/v1/chat/completions",
            "https://user:pass@api.example.com/v1/chat/completions",
            "https://api.example.com/v1/chat/completions?next=internal",
            "https://api.example.com/v1/chat/completions#fragment",
            "https://api.example.com:99999/v1/chat/completions",
        )

        for url in rejected:
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "endpoint policy"):
                _resolve_provider_endpoint(url, provider=_provider())

    def test_rejects_private_or_mixed_dns_answers_fail_closed(self) -> None:
        for addresses in (
            ("0.0.0.0",),
            ("10.0.0.4",),
            ("127.0.0.1",),
            ("169.254.169.254",),
            ("172.16.0.4",),
            ("192.168.0.4",),
            ("::1",),
            ("fc00::1",),
            ("fe80::1",),
            ("8.8.8.8", "10.0.0.4"),
        ):
            with self.subTest(addresses=addresses), patch(
                "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
                return_value=_addrinfo(*addresses),
            ):
                with self.assertRaisesRegex(RuntimeError, "endpoint policy"):
                    _resolve_provider_endpoint(
                        "https://api.example.com/v1/chat/completions",
                        provider=_provider(),
                    )

    def test_known_provider_secret_is_bound_to_registered_origin(self) -> None:
        with patch(
            "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
        ) as rejected_resolver:
            with self.assertRaisesRegex(RuntimeError, "registered origin"):
                _resolve_provider_endpoint(
                    "https://attacker.example/v1/chat/completions",
                    provider=_provider(provider_id="openai"),
                )
        rejected_resolver.assert_not_called()

        with patch(
            "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
            return_value=_addrinfo("8.8.8.8"),
        ):
            endpoint = _resolve_provider_endpoint(
                "https://api.openai.com/v1/chat/completions",
                provider=_provider(provider_id="openai", base_url="https://api.openai.com/v1"),
            )

        self.assertEqual(endpoint.hostname, "api.openai.com")
        self.assertEqual(endpoint.addresses, ("8.8.8.8",))

    def test_request_resolves_once_and_connects_to_the_validated_ip(self) -> None:
        connection = _FakeConnection(_FakeResponse(payload={"ok": True}))
        resolver_rows = _addrinfo("8.8.8.8")

        with patch(
            "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
            return_value=resolver_rows,
        ) as resolver, patch(
            "spark_intelligence.security.https_endpoint._connection_for_endpoint",
            return_value=connection,
        ) as connection_factory, patch.dict(
            "os.environ",
            {"HTTPS_PROXY": "http://127.0.0.1:8080"},
            clear=False,
        ):
            result = _post_json(
                "https://api.example.com/v1/chat/completions",
                headers={"Authorization": "Bearer provider-secret"},
                payload={"model": "test-model"},
                provider=_provider(),
            )

        self.assertEqual(result, {"ok": True})
        resolver.assert_called_once()
        endpoint = connection_factory.call_args.args[0]
        self.assertEqual(connection_factory.call_args.args[1], "8.8.8.8")
        self.assertEqual(connection_factory.call_args.kwargs, {"timeout_seconds": 60})
        self.assertEqual(endpoint.hostname, "api.example.com")
        self.assertEqual(connection.requests[0][0:2], ("POST", "/v1/chat/completions"))
        self.assertTrue(connection.closed)

    def test_pinned_connection_preserves_hostname_for_tls_verification(self) -> None:
        endpoint = _resolve_provider_endpoint(
            "https://8.8.8.8/v1/chat/completions",
            provider=_provider(),
        )
        from spark_intelligence.llm.direct_provider import _PinnedHTTPSConnection

        connection = _PinnedHTTPSConnection(endpoint, "8.8.8.8", timeout_seconds=60)
        raw_socket = object()
        wrapped_socket = object()
        with patch.object(connection, "_create_connection", return_value=raw_socket) as create, patch.object(
            connection._context,
            "wrap_socket",
            return_value=wrapped_socket,
        ) as wrap:
            connection.connect()

        create.assert_called_once_with(("8.8.8.8", 443), 60, None)
        wrap.assert_called_once_with(raw_socket, server_hostname="8.8.8.8")
        self.assertIs(connection.sock, wrapped_socket)

    def test_redirect_is_rejected_without_following_the_location(self) -> None:
        connection = _FakeConnection(_FakeResponse(status=302, payload={"location": "ignored"}))
        with patch(
            "spark_intelligence.security.https_endpoint.socket.getaddrinfo",
            return_value=_addrinfo("8.8.8.8"),
        ), patch(
            "spark_intelligence.security.https_endpoint._connection_for_endpoint",
            return_value=connection,
        ) as connection_factory:
            with self.assertRaisesRegex(RuntimeError, "redirect"):
                _post_json(
                    "https://api.example.com/v1/chat/completions",
                    headers={"Authorization": "Bearer provider-secret"},
                    payload={"model": "test-model"},
                    provider=_provider(),
                )

        connection_factory.assert_called_once()
        self.assertTrue(connection.closed)

    def test_policy_errors_do_not_echo_attacker_controlled_url_or_secret(self) -> None:
        malicious = "http://provider-secret@127.0.0.1/internal"
        with self.assertRaises(RuntimeError) as caught:
            _resolve_provider_endpoint(malicious, provider=_provider())

        message = str(caught.exception)
        self.assertNotIn("provider-secret", message)
        self.assertNotIn("127.0.0.1", message)
        self.assertNotIn("internal", message)


if __name__ == "__main__":
    unittest.main()
