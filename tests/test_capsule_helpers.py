"""Coverage for the parsing helpers inside context.capsule.

The capsule builder relies on tolerant text-parsing helpers (markdown-value
strip, digit-only int parse, whitespace compacter, tokenizer, record
timestamp resolver, and the diagnostic/connector summary extractors). The
integration suite exercises the capsule shape end-to-end, but these helpers
have no direct coverage. These tests pin the parser contract so a refactor
of the diagnostic report format cannot silently break the capsule rendering.
"""
from __future__ import annotations

import pytest

from spark_intelligence.context.capsule import (
    _capsule_tokens,
    _compact,
    _extract_connector_health_counts,
    _extract_diagnostic_summary,
    _parse_int,
    _record_timestamp,
    _strip_markdown_value,
)


class TestStripMarkdownValue:
    def test_backtick_wrapped_value_stripped(self) -> None:
        assert _strip_markdown_value("`hello`") == "hello"

    def test_plain_value_passthrough(self) -> None:
        assert _strip_markdown_value("plain") == "plain"

    def test_single_backtick_left_alone(self) -> None:
        # A single trailing backtick should not strip — only matched pairs.
        assert _strip_markdown_value("hello`") == "hello`"

    def test_outer_whitespace_trimmed(self) -> None:
        assert _strip_markdown_value("  hi  ") == "hi"


class TestParseInt:
    def test_digit_string_parsed(self) -> None:
        assert _parse_int("42") == 42

    def test_none_returns_none(self) -> None:
        assert _parse_int(None) is None

    def test_no_digits_returns_none(self) -> None:
        assert _parse_int("no digits") is None

    def test_extracts_digits_from_mixed_value(self) -> None:
        # "123 lines" -> 123
        assert _parse_int("123 lines") == 123

    def test_empty_string_returns_none(self) -> None:
        assert _parse_int("") is None


class TestCompact:
    def test_collapses_runs_of_whitespace(self) -> None:
        assert _compact("hello   world", max_chars=50) == "hello world"

    def test_short_text_returned_as_is(self) -> None:
        assert _compact("short", max_chars=20) == "short"

    def test_long_text_truncated_with_ellipsis(self) -> None:
        # The function returns text[: max_chars - 1] + "...", so length
        # ends at (max_chars - 1) + 3 when truncation triggers.
        out = _compact("a" * 200, max_chars=15)
        assert out.endswith("...")
        assert len(out) == (15 - 1) + 3

    def test_empty_string_passthrough(self) -> None:
        assert _compact("", max_chars=10) == ""

    def test_none_returns_empty_string(self) -> None:
        assert _compact(None, max_chars=10) == ""  # type: ignore[arg-type]


class TestCapsuleTokens:
    def test_extracts_lowercase_tokens(self) -> None:
        tokens = _capsule_tokens("Hello World")
        assert tokens == {"hello", "world"}

    def test_stopwords_dropped(self) -> None:
        tokens = _capsule_tokens("the world for my test")
        assert "the" not in tokens
        assert "for" not in tokens
        assert "my" not in tokens
        # Non-stopwords survive.
        assert "world" in tokens

    def test_hyphenated_token_preserved(self) -> None:
        tokens = _capsule_tokens("session-id matters here")
        assert "session-id" in tokens

    def test_empty_input_returns_empty_set(self) -> None:
        assert _capsule_tokens("") == set()

    def test_none_input_returns_empty_set(self) -> None:
        assert _capsule_tokens(None) == set()  # type: ignore[arg-type]


class TestRecordTimestamp:
    def test_timestamp_field_preferred(self) -> None:
        assert _record_timestamp({"timestamp": "2026-05-29T12:00:00Z"}) == "2026-05-29T12:00:00Z"

    def test_recorded_at_used_when_timestamp_missing(self) -> None:
        assert _record_timestamp({"recorded_at": "2026-01-01"}) == "2026-01-01"

    def test_metadata_document_time_fallback(self) -> None:
        assert _record_timestamp({"metadata": {"document_time": "2025-01-01"}}) == "2025-01-01"

    def test_no_timestamp_returns_empty_string(self) -> None:
        assert _record_timestamp({}) == ""

    def test_non_dict_metadata_handled(self) -> None:
        # Non-dict metadata should not blow up.
        assert _record_timestamp({"metadata": "string"}) == ""


class TestExtractDiagnosticSummary:
    def test_dash_prefixed_lines_parsed(self) -> None:
        text = "- failure lines: 5\n- finding signatures: 3"
        summary = _extract_diagnostic_summary(text)
        assert summary["failure_lines"] == "5"
        assert summary["finding_signatures"] == "3"

    def test_plain_lines_parsed(self) -> None:
        text = "scanned lines: 100"
        summary = _extract_diagnostic_summary(text)
        assert summary["scanned_lines"] == "100"

    def test_unknown_text_yields_empty_summary(self) -> None:
        assert _extract_diagnostic_summary("unrelated lorem ipsum") == {}

    def test_alt_marker_for_failure_lines(self) -> None:
        # "Failures:" should match the failure_lines slot.
        summary = _extract_diagnostic_summary("Failures: 9")
        assert summary["failure_lines"] == "9"

    def test_backtick_value_stripped(self) -> None:
        summary = _extract_diagnostic_summary("failure lines: `7`")
        assert summary["failure_lines"] == "7"


class TestExtractConnectorHealthCounts:
    def test_legacy_inline_format(self) -> None:
        counts = _extract_connector_health_counts("Connector checks: ok: 3, warn: 1")
        assert counts == {"ok": 3, "warn": 1}

    def test_bulleted_status_arrow_format(self) -> None:
        text = "- `ok` -> reachable\n- `error` -> dns failure"
        counts = _extract_connector_health_counts(text)
        assert counts == {"ok": 1, "error": 1}

    def test_skipped_reserved_status_labels(self) -> None:
        # 'home', 'log sources', etc. are reserved and should not count.
        text = "- `home` -> /opt/spark\n- `log sources` -> 5"
        assert _extract_connector_health_counts(text) == {}

    def test_unrelated_input_returns_empty(self) -> None:
        assert _extract_connector_health_counts("no connectors here") == {}
