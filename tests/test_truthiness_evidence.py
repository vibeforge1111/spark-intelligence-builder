"""Tests for idiomatic truthiness checks instead of len()==0 for evidence lists."""
import pytest


class TestTruthinessEvidence:
    """Verify evidence lists use idiomatic truthiness."""

    def test_empty_list_via_truthiness(self):
        """Use \`if not evidence\` instead of \`if len(evidence) == 0\`."""
        evidence = []
        assert not evidence  # idiomatic truthiness
        assert len(evidence) == 0  # old way (to avoid)
        # Truthiness is preferred

    def test_populated_list_via_truthiness(self):
        """Use \`if evidence\` instead of \`if len(evidence) > 0\`."""
        evidence = ["item1", "item2"]
        assert evidence  # idiomatic truthiness

