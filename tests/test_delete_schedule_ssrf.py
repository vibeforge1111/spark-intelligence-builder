"""Tests: delete_schedule_via_spawner() spawner_url SSRF fix — hostname allowlist."""
from __future__ import annotations

import pytest

from spark_intelligence.schedule_bridge.service import (
    _validate_spawner_url,
    delete_schedule_via_spawner,
)


class TestValidateSpawnerUrlDelete:
    def test_localhost_allowed(self):
        _validate_spawner_url("http://localhost:4174")

    def test_loopback_allowed(self):
        _validate_spawner_url("http://127.0.0.1:5173")

    def test_aws_metadata_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://169.254.169.254/")

    def test_rfc1918_class_b_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://172.16.99.1/api/scheduled")

    def test_external_attacker_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://attacker.evil.io/ssrf")

    def test_internal_gcp_metadata_rejected(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            _validate_spawner_url("http://metadata.google.internal/")


class TestDeleteScheduleSsrf:
    def test_delete_rejects_aws_metadata(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            delete_schedule_via_spawner("sched-abc123", spawner_url="http://169.254.169.254/")

    def test_delete_rejects_internal_host(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            delete_schedule_via_spawner("sched-xyz", spawner_url="http://10.0.0.1/")

    def test_delete_rejects_external_domain(self):
        with pytest.raises(ValueError, match="allowed spawner host"):
            delete_schedule_via_spawner("sched-abc", spawner_url="http://evil.com/")
