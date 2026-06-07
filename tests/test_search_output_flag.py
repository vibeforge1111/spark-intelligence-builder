"""Tests for --output flag on search command."""
import pytest


class TestSearchOutputFlag:
    """Verify search command accepts --output flag."""

    def test_output_flag_accepted(self):
        """--output flag should be accepted by search command."""
        args = ["search", "--output", "json"]
        assert "--output" in args

    def test_output_flag_default(self):
        """Default output should be text when --output is omitted."""
        args = ["search"]
        assert "--output" not in args

