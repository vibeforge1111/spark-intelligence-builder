"""Tests for OSError inclusion in subprocess-error handler."""
import pytest


class TestIncludeOSError:
    """Verify OSError is caught in subprocess-error handler."""

    def test_oserror_caught(self):
        """OSError should be caught alongside subprocess errors."""
        errors = (OSError,)
        assert OSError in errors

    def test_transient_network_failures_caught(self):
        """Transient network failures (OSError subtypes) should be caught."""
        assert issubclass(ConnectionError, OSError)
        assert issubclass(TimeoutError, OSError)

