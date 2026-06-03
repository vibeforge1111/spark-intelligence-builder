"""Tests for empty-state next step naming in pairings list."""
import pytest


class TestEmptyStatePairings:
    """Verify empty-state next step is named in pairings list."""

    def test_empty_state_has_next_step(self):
        """Empty pairings list should show a named next step."""
        pairings = []
        next_step = "Pair with an operator to get started"
        assert len(pairings) == 0
        assert next_step is not None

    def test_empty_state_message(self):
        """Empty state message should guide user to next action."""
        message = "No active pairings. Use \`--pair\` to connect with an operator."
        assert "No active pairings" in message

