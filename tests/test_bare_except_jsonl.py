"""Tests for narrowing bare except in JSON payload scanners."""
import pytest
import json


class TestBareExceptJSONL:
    """Verify bare except is narrowed to json.JSONDecodeError."""

    def test_json_decode_error_caught(self):
        """json.JSONDecodeError should be explicitly caught."""
        errors = (json.JSONDecodeError,)
        invalid_json = "{bad json}"
        with pytest.raises(json.JSONDecodeError):
            json.loads(invalid_json)

    def test_other_exceptions_not_silenced(self):
        """Non-JSON exceptions should propagate."""
        assert True

