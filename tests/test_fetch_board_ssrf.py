"""Tests: fetch_board() spawner_url SSRF fix — hostname allowlist validation."""
from __future__ import annotations

import pytest

from spark_intelligence.mission_bridge.service import _validate_spawner_url, fetch_board


class TestValidateSpawnerUrl:
    def test_localhost_allowed(self):
        _validate_spawner_url("http://localhost:4174")

    def test_loopback_ipv4_allowed(self):
        _validate_spawner_url("http://127.0.0.1:5173")

    def test_internal_aws_metadata_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://169.254.169.254/latest/meta-data/")

    def test_private_network_ip_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://192.168.1.1/api/board")

    def test_external_domain_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://evil.attacker.com/ssrf")

    def test_redis_loopback_port_allowed(self):
        _validate_spawner_url("http://127.0.0.1:6379")

    def test_attacker_disguised_as_localhost_subdomain_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://evil.localhost.attacker.com/steal")


class TestFetchBoardSsrf:
    def test_fetch_board_rejects_internal_metadata_url(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            fetch_board(spawner_url="http://169.254.169.254/")

    def test_fetch_board_rejects_rfc1918_url(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            fetch_board(spawner_url="http://10.0.0.1/api/board")
