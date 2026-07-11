"""Tests: fetch_schedules() spawner_url SSRF fix — hostname allowlist validation."""
from __future__ import annotations

import pytest

from spark_intelligence.schedule_bridge.service import _validate_spawner_url, fetch_schedules


class TestValidateSpawnerUrlSchedules:
    def test_localhost_allowed(self):
        _validate_spawner_url("http://localhost:4174")

    def test_loopback_ipv4_allowed(self):
        _validate_spawner_url("http://127.0.0.1:5173")

    def test_aws_metadata_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://169.254.169.254/latest/meta-data/")

    def test_private_rfc1918_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://10.0.0.50/api/scheduled")

    def test_external_host_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://attacker.example.com/steal")

    def test_internal_kubernetes_service_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://kube-apiserver.kube-system.svc.cluster.local")


class TestFetchSchedulesSsrf:
    def test_fetch_schedules_rejects_metadata_url(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            fetch_schedules(spawner_url="http://169.254.169.254/")

    def test_fetch_schedules_rejects_internal_host(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            fetch_schedules(spawner_url="http://172.16.0.1/api/scheduled")

    def test_fetch_schedules_rejects_external_domain(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            fetch_schedules(spawner_url="http://evil.com/fake-schedules")
