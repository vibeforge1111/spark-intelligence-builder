"""Direct coverage for the outbound-text helper functions in gateway.guardrails.

``prepare_outbound_text`` glues these together but no tests today exercise the
individual building blocks. Locking each one in isolation makes the
post-output formatting contract obvious to future maintainers.
"""
from __future__ import annotations

import pytest

from spark_intelligence.gateway.guardrails import (
    _find_split_point,
    _normalize_score_decimals_to_percent,
    _split_text_for_delivery,
    _strip_em_dashes,
)


class TestStripEmDashes:
    def test_em_dash_replaced_with_hyphen(self) -> None:
        assert _strip_em_dashes("alpha—beta") == "alpha - beta"

    def test_en_dash_also_replaced(self) -> None:
        assert _strip_em_dashes("alpha–beta") == "alpha - beta"

    def test_minus_sign_replaced(self) -> None:
        assert _strip_em_dashes("a−b") == "a - b"

    def test_ascii_hyphen_preserved(self) -> None:
        assert _strip_em_dashes("chip-key-startup-yc") == "chip-key-startup-yc"

    def test_empty_returns_empty(self) -> None:
        assert _strip_em_dashes("") == ""

    def test_double_space_collapsed(self) -> None:
        # Double spaces that appear from substitution get collapsed.
        out = _strip_em_dashes("a — b")
        assert "  " not in out
        assert "-" in out


class TestNormalizeScoreDecimals:
    def test_score_decimal_becomes_percent(self) -> None:
        out = _normalize_score_decimals_to_percent("score: 0.85")
        assert "85%" in out

    def test_confidence_label_supported(self) -> None:
        out = _normalize_score_decimals_to_percent("confidence: 0.5")
        assert "50%" in out

    def test_value_above_one_left_alone(self) -> None:
        # 1.5 is outside the 0-1 band; keep raw to avoid mis-formatting.
        out = _normalize_score_decimals_to_percent("score: 1.5")
        assert "1.5" in out
        assert "150%" not in out

    def test_value_exactly_one_normalizes(self) -> None:
        out = _normalize_score_decimals_to_percent("score: 1.0")
        assert "100%" in out

    def test_empty_string_passthrough(self) -> None:
        assert _normalize_score_decimals_to_percent("") == ""

    def test_no_label_match_passthrough(self) -> None:
        assert (
            _normalize_score_decimals_to_percent("nothing to rewrite here")
            == "nothing to rewrite here"
        )


class TestSplitTextForDelivery:
    def test_short_text_returned_unmodified(self) -> None:
        chunks = _split_text_for_delivery("hello", chunk_size=100, max_chunks=3)
        assert chunks == ["hello"]

    def test_split_into_two_chunks_with_prefix(self) -> None:
        # 200 chars at chunk_size 80 should produce multiple prefixed chunks.
        text = "Sentence one. " * 30  # ~420 chars
        chunks = _split_text_for_delivery(text, chunk_size=80, max_chunks=5)
        assert len(chunks) > 1
        # Multi-chunk results are prefixed (1/N), (2/N), ...
        assert chunks[0].startswith("(1/")
        assert chunks[1].startswith("(2/")

    def test_truncation_note_appended_when_over_max(self) -> None:
        text = "A long sentence that just keeps repeating. " * 200
        chunks = _split_text_for_delivery(text, chunk_size=120, max_chunks=2)
        assert len(chunks) == 2
        assert "content trimmed" in chunks[-1]

    def test_chunk_size_floor_respected(self) -> None:
        # Tiny chunk size still produces non-empty output.
        chunks = _split_text_for_delivery("Hello world.", chunk_size=4, max_chunks=3)
        assert chunks
        assert all(chunk.strip() for chunk in chunks)


class TestFindSplitPoint:
    def test_text_shorter_than_chunk_returns_full_length(self) -> None:
        assert _find_split_point("hello", 100) == len("hello")

    def test_split_prefers_paragraph_boundary(self) -> None:
        # Paragraph break sits comfortably past the half-window threshold.
        text = "x" * 40 + "\n\n" + "y" * 200
        idx = _find_split_point(text, 60)
        # Split point should land just after the "\n\n".
        assert text[idx - 2 : idx] == "\n\n"

    def test_falls_back_to_chunk_size_when_no_boundary(self) -> None:
        text = "x" * 200
        # No spaces or punctuation — falls back to raw chunk_size.
        assert _find_split_point(text, 50) == 50

    def test_split_after_sentence_punctuation(self) -> None:
        text = "first. second. third. fourth. fifth. sixth. seventh."
        idx = _find_split_point(text, 25)
        # Result should land just after a ". " boundary.
        assert text[idx - 2 : idx] == ". " or text[idx - 1 : idx] == " "
