from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ResolvedHTTPSEndpoint:
    hostname: str
    port: int
    request_target: str
    addresses: tuple[str, ...]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated address while verifying TLS for the original host."""

    def __init__(
        self,
        endpoint: ResolvedHTTPSEndpoint,
        address: str,
        *,
        timeout_seconds: int,
    ) -> None:
        super().__init__(
            endpoint.hostname,
            endpoint.port,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = self._create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def canonical_https_origin(url: str) -> tuple[str, int]:
    parsed, hostname, port = _parse_https_url(url)
    if parsed.query or parsed.fragment:
        raise _policy_error("query strings and fragments are not permitted")
    return hostname, port


def resolve_public_https_endpoint(
    url: str,
    *,
    expected_origin: tuple[str, int] | None = None,
) -> ResolvedHTTPSEndpoint:
    parsed, hostname, port = _parse_https_url(url)
    if parsed.query or parsed.fragment:
        raise _policy_error("query strings and fragments are not permitted")
    if expected_origin is not None and (hostname, port) != expected_origin:
        raise _policy_error("the endpoint does not match the registered origin")

    addresses = _resolve_public_addresses(hostname, port)
    return ResolvedHTTPSEndpoint(
        hostname=hostname,
        port=port,
        request_target=urllib.parse.urlunsplit(("", "", parsed.path or "/", "", "")),
        addresses=addresses,
    )


def post_https_bytes(
    endpoint: ResolvedHTTPSEndpoint,
    *,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: int,
    max_response_bytes: int,
    query: Mapping[str, str] | None = None,
) -> bytes:
    request_target = endpoint.request_target
    if query:
        request_target = f"{request_target}?{urllib.parse.urlencode(query)}"
    last_network_error: Exception | None = None
    for address in endpoint.addresses:
        connection = _connection_for_endpoint(
            endpoint,
            address,
            timeout_seconds=timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                request_target,
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise RuntimeError("HTTPS redirect blocked by endpoint policy.")
            if not 200 <= response.status < 300:
                raise RuntimeError(f"HTTPS request failed with HTTP status {response.status}.")
            response_body = response.read(max_response_bytes + 1)
            if len(response_body) > max_response_bytes:
                raise RuntimeError("HTTPS response exceeded the safe size limit.")
            return response_body
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_network_error = exc
        finally:
            connection.close()

    raise RuntimeError("HTTPS network request failed safely.") from last_network_error


def _parse_https_url(url: str) -> tuple[urllib.parse.SplitResult, str, int]:
    if any(ord(character) <= 32 or ord(character) == 127 for character in url):
        raise _policy_error("control characters are not permitted")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise _policy_error("the URL is malformed") from exc

    if parsed.scheme.lower() != "https":
        raise _policy_error("HTTPS is required")
    if parsed.username is not None or parsed.password is not None:
        raise _policy_error("embedded credentials are not permitted")
    if not parsed.hostname:
        raise _policy_error("a hostname is required")
    if not 1 <= port <= 65535:
        raise _policy_error("the port is invalid")
    return parsed, _normalize_hostname(parsed.hostname), port


def _normalize_hostname(hostname: str) -> str:
    normalized = hostname.rstrip(".")
    if not normalized or "%" in normalized:
        raise _policy_error("the hostname is invalid")
    try:
        return normalized.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise _policy_error("the hostname is invalid") from exc


def _resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            address_rows = socket.getaddrinfo(
                hostname,
                port,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise _policy_error("the hostname could not be resolved") from exc
        addresses = tuple(dict.fromkeys(str(row[4][0]) for row in address_rows if row[4]))

    if not addresses:
        raise _policy_error("the hostname resolved to no usable addresses")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise _policy_error("the hostname resolved to an invalid address") from exc
        if not _is_public_address(parsed_address):
            raise _policy_error("the hostname resolved outside the public network")
    return addresses


def _is_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(address.is_global and not address.is_multicast)


def _connection_for_endpoint(
    endpoint: ResolvedHTTPSEndpoint,
    address: str,
    *,
    timeout_seconds: int,
) -> PinnedHTTPSConnection:
    return PinnedHTTPSConnection(
        endpoint,
        address,
        timeout_seconds=timeout_seconds,
    )


def _policy_error(reason: str) -> RuntimeError:
    return RuntimeError(f"HTTPS endpoint policy rejected the request: {reason}.")
