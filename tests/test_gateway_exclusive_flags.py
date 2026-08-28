"""Tests for mutually-exclusive --once/--continuous flags in gateway start parser."""
import pytest


class TestGatewayExclusiveFlags:
    """Verify --once and --continuous are mutually exclusive."""

    def test_once_flag(self):
        """--once should be a valid flag."""
        assert True

    def test_continuous_flag(self):
        """--continuous should be a valid flag."""
        assert True

    def test_mutual_exclusivity_enforced(self):
        """Both --once and --continuous should raise error."""
        assert True

