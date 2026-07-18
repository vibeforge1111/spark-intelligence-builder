from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ResolvedLocalSpawnerEndpoint:
    hostname: str
    port: int
    request_target: str
    addresses: tuple[str, ...]


def resolve_local_spawner_endpoint(
    *,
    configured_url: str,
    requested_url: str | None,
    route_path: str,
) -> ResolvedLocalSpawnerEndpoint:
    configured_origin = _parse_local_origin(configured_url)
    if requested_url is not None:
        requested_origin = _parse_local_origin(requested_url)
        if requested_origin != configured_origin:
            raise _policy_error("the requested origin does not match the configured Spawner")

    hostname, port = configured_origin
    addresses = _resolve_loopback_addresses(hostname, port)
    return ResolvedLocalSpawnerEndpoint(
        hostname=hostname,
        port=port,
        request_target=_validate_route_path(route_path),
        addresses=addresses,
    )


def request_local_spawner_json(
    endpoint: ResolvedLocalSpawnerEndpoint,
    *,
    method: str,
    query: Mapping[str, str] | None = None,
    timeout_seconds: float,
    max_response_bytes: int,
) -> Any:
    normalized_method = method.upper()
    if normalized_method not in {"GET", "DELETE"}:
        raise _policy_error("the HTTP method is not permitted")
    if timeout_seconds <= 0 or max_response_bytes <= 0:
        raise _policy_error("the request bounds are invalid")

    request_target = endpoint.request_target
    if query:
        request_target = f"{request_target}?{urllib.parse.urlencode(query)}"

    for address in endpoint.addresses:
        connection = _connection_for_endpoint(
            endpoint,
            address,
            timeout_seconds=timeout_seconds,
        )
        try:
            connection.request(
                normalized_method,
                request_target,
                headers={
                    "Accept": "application/json",
                    "Host": _host_header(endpoint.hostname, endpoint.port),
                },
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise RuntimeError("Local Spawner redirect blocked by endpoint policy.")
            if not 200 <= response.status < 300:
                raise RuntimeError("Local Spawner request failed safely.")
            response_body = response.read(max_response_bytes + 1)
            if len(response_body) > max_response_bytes:
                raise RuntimeError("Local Spawner response exceeded the safe size limit.")
            try:
                return json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise RuntimeError("Local Spawner returned an invalid response.") from None
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()

    raise RuntimeError("Local Spawner request failed safely.") from None


def _parse_local_origin(url: str) -> tuple[str, int]:
    if not isinstance(url, str) or not url:
        raise _policy_error("an origin URL is required")
    if any(ord(character) <= 32 or ord(character) == 127 for character in url):
        raise _policy_error("control characters are not permitted")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port or 80
    except ValueError:
        raise _policy_error("the origin URL is malformed") from None

    if parsed.scheme.lower() != "http":
        raise _policy_error("the local Spawner must use HTTP")
    if parsed.username is not None or parsed.password is not None:
        raise _policy_error("embedded credentials are not permitted")
    if not parsed.hostname:
        raise _policy_error("a hostname is required")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise _policy_error("only an origin URL is permitted")
    if not 1 <= port <= 65535:
        raise _policy_error("the port is invalid")

    hostname = _normalize_hostname(parsed.hostname)
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            raise _policy_error("the hostname is not the local loopback") from None
        if not _is_loopback(address):
            raise _policy_error("the hostname is not the local loopback")
    return hostname, port


def _normalize_hostname(hostname: str) -> str:
    normalized = hostname.rstrip(".")
    if not normalized or "%" in normalized:
        raise _policy_error("the hostname is invalid")
    try:
        return normalized.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise _policy_error("the hostname is invalid") from None


def _resolve_loopback_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            rows = socket.getaddrinfo(
                hostname,
                port,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except OSError:
            raise _policy_error("the local hostname could not be resolved") from None
        addresses = tuple(dict.fromkeys(str(row[4][0]) for row in rows if row[4]))

    if not addresses:
        raise _policy_error("the local hostname resolved to no usable addresses")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError:
            raise _policy_error("the local hostname resolved to an invalid address") from None
        if not _is_loopback(parsed_address):
            raise _policy_error("the local hostname resolved outside loopback")
    return addresses


def _is_loopback(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _validate_route_path(route_path: str) -> str:
    if any(ord(character) <= 32 or ord(character) == 127 for character in route_path):
        raise _policy_error("the route contains control characters")
    parsed = urllib.parse.urlsplit(route_path)
    if (
        not route_path.startswith("/")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != route_path
    ):
        raise _policy_error("the route is invalid")
    return route_path


def _connection_for_endpoint(
    endpoint: ResolvedLocalSpawnerEndpoint,
    address: str,
    *,
    timeout_seconds: float,
) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(address, endpoint.port, timeout=timeout_seconds)


def _host_header(hostname: str, port: int) -> str:
    formatted = f"[{hostname}]" if ":" in hostname else hostname
    return formatted if port == 80 else f"{formatted}:{port}"


def _policy_error(reason: str) -> RuntimeError:
    return RuntimeError(f"Local Spawner endpoint policy rejected the request: {reason}.")
