"""Tests for SSRF protection in direct_provider base URL validation."""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from spark_intelligence.llm.direct_provider import (
    _validate_base_url,
    _is_private_or_reserved,
)
import ipaddress


class TestSSRFForProviderBaseUrl:
    """Verify that provider base URLs targeting private/internal hosts are blocked."""

    def test_blocks_http_scheme(self) -> None:
        with pytest.raises(RuntimeError, match="must use HTTPS"):
            _validate_base_url("http://api.openai.com/v1")

    def test_blocks_localhost(self) -> None:
        with pytest.raises(RuntimeError, match="blocked hostname"):
            _validate_base_url("https://localhost/v1")

    def test_blocks_169_254_169_254(self) -> None:
        with pytest.raises(RuntimeError, match="blocked hostname"):
            _validate_base_url("https://169.254.169.254/latest/meta-data/")

    def test_blocks_0_0_0_0(self) -> None:
        with pytest.raises(RuntimeError, match="blocked hostname"):
            _validate_base_url("https://0.0.0.0/secret")

    def test_blocks_metadata_google_internal(self) -> None:
        with pytest.raises(RuntimeError, match="blocked hostname"):
            _validate_base_url("https://metadata.google.internal/computeMetadata/v1/")

    def test_blocks_empty_hostname(self) -> None:
        with pytest.raises(RuntimeError, match="no hostname"):
            _validate_base_url("https:///path")

    def test_blocks_resolved_loopback_ip(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))],
        ):
            with pytest.raises(RuntimeError, match="private/reserved IP.*127.0.0.1"):
                _validate_base_url("https://sneaky-internal.example.com/v1")

    def test_blocks_resolved_private_ip(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))],
        ):
            with pytest.raises(RuntimeError, match="private/reserved IP.*10.0.0.1"):
                _validate_base_url("https://internal.corp.local/v1")

    def test_blocks_resolved_192_168_ip(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.100", 0))],
        ):
            with pytest.raises(RuntimeError, match="private/reserved IP.*192.168.1.100"):
                _validate_base_url("https://router.local/v1")

    def test_blocks_resolved_link_local(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.1.1", 0))],
        ):
            with pytest.raises(RuntimeError, match="private/reserved IP.*169.254.1.1"):
                _validate_base_url("https://link-local-host.example.com/v1")

    def test_blocks_resolved_ipv6_loopback(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))],
        ):
            with pytest.raises(RuntimeError, match="private/reserved IP.*::1"):
                _validate_base_url("https://ipv6-localhost.example.com/v1")

    def test_blocks_resolved_172_16_private(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("172.16.0.5", 0))],
        ):
            with pytest.raises(RuntimeError, match="private/reserved IP.*172.16.0.5"):
                _validate_base_url("https://docker-internal.example.com/v1")

    def test_unreachable_hostname_is_blocked(self) -> None:
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            side_effect=socket.gaierror("Name resolution failed"),
        ):
            with pytest.raises(RuntimeError, match="could not be resolved"):
                _validate_base_url("https://nonexistent.invalid.example/v1")

    def test_allows_valid_public_https(self) -> None:
        """Public HTTPS URLs to non-reserved IPs should pass validation."""
        with patch(
            "spark_intelligence.llm.direct_provider.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("104.18.0.1", 0))
            ],
        ):
            # Should not raise
            _validate_base_url("https://api.openai.com/v1/chat/completions")

    def test_is_private_or_reserved_covers_all_ranges(self) -> None:
        assert _is_private_or_reserved(ipaddress.ip_address("127.0.0.1"))
        assert _is_private_or_reserved(ipaddress.ip_address("10.0.0.1"))
        assert _is_private_or_reserved(ipaddress.ip_address("172.16.0.1"))
        assert _is_private_or_reserved(ipaddress.ip_address("192.168.1.1"))
        assert _is_private_or_reserved(ipaddress.ip_address("169.254.1.1"))
        assert _is_private_or_reserved(ipaddress.ip_address("0.0.0.0"))
        assert _is_private_or_reserved(ipaddress.ip_address("::1"))
        assert not _is_private_or_reserved(ipaddress.ip_address("8.8.8.8"))
        assert not _is_private_or_reserved(ipaddress.ip_address("1.1.1.1"))
