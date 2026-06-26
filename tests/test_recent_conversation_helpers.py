"""Coverage for the parsing helpers inside context.recent_conversation.

Loading recent gateway turns from the JSONL trail relies on these tiny pure
functions to normalise text previews, sort by ISO timestamp, and tail-read the
trail safely when the file is absent or malformed. None of them is exercised
in isolation today; locking them keeps the conversation-context surface
predictable across log-format changes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark_intelligence.context.recent_conversation import (
    _compact_preview,
    _preview_matches_current,
    _read_jsonl_tail,
    _sortable_timestamp,
)


class TestCompactPreview:
    def test_collapses_runs_of_whitespace(self) -> None:
        assert _compact_preview("hello   world") == "hello world"

    def test_strips_outer_whitespace(self) -> None:
        assert _compact_preview("  hi there  ") == "hi there"

    def test_newlines_treated_as_space(self) -> None:
        assert _compact_preview("a\nb\tc") == "a b c"

    def test_empty_input_returns_empty_string(self) -> None:
        assert _compact_preview("") == ""

    def test_none_input_returns_empty_string(self) -> None:
        # The function casts to str(value or "") under the hood.
        assert _compact_preview(None) == ""  # type: ignore[arg-type]


class TestPreviewMatchesCurrent:
    def test_exact_match_returns_true(self) -> None:
        assert _preview_matches_current("hello world", "hello world") is True

    def test_whitespace_normalised_match(self) -> None:
        # Both sides should be compacted before comparison.
        assert _preview_matches_current("  hello   world ", "hello world") is True

    def test_truncated_candidate_matches_full_current(self) -> None:
        # When the candidate ends with "..." we accept a prefix match.
        assert _preview_matches_current("hello ...", "hello world") is True

    def test_truncated_candidate_no_prefix_match(self) -> None:
        assert _preview_matches_current("hello ...", "different message") is False

    def test_empty_candidate_returns_false(self) -> None:
        assert _preview_matches_current("", "hello") is False

    def test_distinct_inputs_return_false(self) -> None:
        assert _preview_matches_current("foo", "bar") is False


class TestSortableTimestamp:
    def test_iso_with_z_suffix_normalises(self) -> None:
        out = _sortable_timestamp("2026-05-30T12:00:00Z")
        # Should be parseable back as ISO.
        assert "2026-05-30T12:00:00" in out

    def test_iso_with_offset_returned_unchanged_shape(self) -> None:
        out = _sortable_timestamp("2026-05-30T12:00:00+00:00")
        assert "2026-05-30T12:00:00" in out

    def test_invalid_input_returned_raw(self) -> None:
        assert _sortable_timestamp("not a date") == "not a date"

    def test_empty_input_returns_empty(self) -> None:
        assert _sortable_timestamp("") == ""

    def test_none_input_returns_empty(self) -> None:
        assert _sortable_timestamp(None) == ""


class TestReadJsonlTail:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _read_jsonl_tail(tmp_path / "does-not-exist.jsonl", limit=10) == []

    def test_reads_each_json_line(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"event": "first"}),
                    json.dumps({"event": "second"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        out = _read_jsonl_tail(path, limit=10)
        assert [row["event"] for row in out] == ["first", "second"]

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text(
            "\n".join(
                [
                    "not json at all",
                    json.dumps({"event": "ok"}),
                    "",
                    "{broken json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        out = _read_jsonl_tail(path, limit=10)
        assert out == [{"event": "ok"}]

    def test_respects_tail_limit(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        lines = [json.dumps({"event": f"e{i}"}) for i in range(10)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = _read_jsonl_tail(path, limit=3)
        assert [row["event"] for row in out] == ["e7", "e8", "e9"]

    def test_drops_non_dict_payloads(self, tmp_path: Path) -> None:
        # JSON arrays at the line level should be ignored.
        path = tmp_path / "log.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps([1, 2, 3]),
                    json.dumps({"event": "ok"}),
                    json.dumps("string-payload"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        out = _read_jsonl_tail(path, limit=10)
        assert out == [{"event": "ok"}]
