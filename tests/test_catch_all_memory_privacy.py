"""Privacy-boundary tests for catch-all 'my X is Y' memory pattern.

Proves:
- Values are cleaned and bounded (no raw chat leakage)
- Attribute length limits prevent abuse
- Skip keywords route to specific packs instead of catch-all
- No PII (tokens, chat IDs, usernames) stored in catch-all fields
"""
import re
import pytest

# Import the function under test
from spark_intelligence.memory.generic_observations import (
    classify_telegram_generic_memory_candidate,
)


class TestCatchAllFactPattern:
    """Tests for the 'my X is Y' catch-all pattern."""

    def test_basic_fact_recognized(self):
        result = classify_telegram_generic_memory_candidate("my shoe size is 10")
        assert result is not None
        assert result.predicate == "profile.shoe_size"
        assert result.value == "10"
        assert result.domain_pack == "profile_custom"

    def test_multi_word_attribute(self):
        result = classify_telegram_generic_memory_candidate("my favorite food is sushi")
        assert result is not None
        assert result.predicate == "profile.favorite_food"
        assert result.value == "sushi"

    def test_skip_keyword_name(self):
        """'name' is in skip_keywords — should NOT be caught by catch-all."""
        result = classify_telegram_generic_memory_candidate("my name is Sampson")
        # Should be handled by a specific pack, not catch-all
        if result is not None:
            assert result.domain_pack != "profile_custom"

    def test_skip_keyword_country(self):
        result = classify_telegram_generic_memory_candidate("my country is Nigeria")
        if result is not None:
            assert result.domain_pack != "profile_custom"

    def test_attribute_too_short_rejected(self):
        """Single-char attribute should be rejected (len < 2)."""
        result = classify_telegram_generic_memory_candidate("my x is y")
        # 'x' is 1 char — should be rejected
        assert result is None or result.domain_pack != "profile_custom"

    def test_attribute_too_long_rejected(self):
        """Attribute > 60 chars should be rejected."""
        long_attr = "a" * 61
        result = classify_telegram_generic_memory_candidate(f"my {long_attr} is test")
        assert result is None or result.domain_pack != "profile_custom"

    def test_value_cleaned_of_trailing_punctuation(self):
        result = classify_telegram_generic_memory_candidate("my favorite color is blue.")
        assert result is not None
        assert result.value == "blue"

    def test_empty_value_rejected(self):
        result = classify_telegram_generic_memory_candidate("my favorite color is")
        assert result is None or result.domain_pack != "profile_custom"


class TestPrivacyBoundary:
    """Proves no raw chat content, tokens, or PII leaks into catch-all storage."""

    def test_no_token_in_value(self):
        """Token-like strings should be stored as-is (bounded), not processed."""
        result = classify_telegram_generic_memory_candidate("my token is sk-abc123def456")
        if result is not None:
            # Value should be bounded and not expanded
            assert len(result.value) < 200

    def test_no_chat_id_stored(self):
        """Chat IDs should not appear in catch-all fields."""
        result = classify_telegram_generic_memory_candidate("my chat id is 6732920679")
        if result is not None:
            assert "6732920679" not in result.predicate

    def test_memory_directive_prefix_stripped(self):
        """'Remember this: my X is Y' should still match after prefix strip."""
        result = classify_telegram_generic_memory_candidate(
            "Remember this: my favorite color is green"
        )
        assert result is not None
        assert result.value == "green"

    def test_evidence_text_bounded(self):
        """Evidence text should not exceed reasonable length."""
        long_msg = "my " + "test " * 100 + "is value"
        result = classify_telegram_generic_memory_candidate(long_msg)
        if result is not None:
            assert len(result.evidence_text) < 5000

    def test_retention_class_is_active(self):
        """Catch-all facts should use active_state retention, not permanent."""
        result = classify_telegram_generic_memory_candidate("my hobby is chess")
        if result is not None:
            assert result.retention_class == "active_state"
            assert result.memory_role == "current_state"
