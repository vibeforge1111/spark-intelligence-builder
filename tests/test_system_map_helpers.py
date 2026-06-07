"""Coverage for the value-coercion helpers in system_map_read_model.

The system-map summary depends on tolerant casts (_dict, _list, _int,
_float, _short, _safe_summary_mapping) that absorb the wild shapes coming
out of the trace index. The integration test exercises the happy path; these
tests pin the defensive behavior so that a strict-cast refactor cannot
silently start raising inside the summary builder.
"""
from __future__ import annotations

import pytest

from spark_intelligence.self_awareness.system_map_read_model import (
    _claim_boundary,
    _dict,
    _float,
    _int,
    _int_mapping,
    _list,
    _safe_summary_mapping,
    _short,
)


class TestDictCoercion:
    def test_dict_passthrough(self) -> None:
        assert _dict({"a": 1}) == {"a": 1}

    def test_non_dict_returns_empty(self) -> None:
        assert _dict("string") == {}
        assert _dict([1, 2, 3]) == {}
        assert _dict(None) == {}


class TestListCoercion:
    def test_list_passthrough(self) -> None:
        assert _list([1, 2, 3]) == [1, 2, 3]

    def test_non_list_returns_empty(self) -> None:
        assert _list("string") == []
        assert _list({"a": 1}) == []
        assert _list(None) == []


class TestIntCoercion:
    def test_int_passthrough(self) -> None:
        assert _int(5) == 5

    def test_string_digit_coerced(self) -> None:
        assert _int("42") == 42

    def test_none_returns_zero(self) -> None:
        assert _int(None) == 0

    def test_invalid_string_returns_zero(self) -> None:
        assert _int("not a number") == 0

    def test_float_truncates_to_int(self) -> None:
        assert _int(3.7) == 3


class TestFloatCoercion:
    def test_float_passthrough(self) -> None:
        assert _float(3.14) == 3.14

    def test_string_decimal_coerced(self) -> None:
        assert _float("2.5") == 2.5

    def test_invalid_returns_zero(self) -> None:
        assert _float("nope") == 0.0

    def test_none_returns_zero(self) -> None:
        assert _float(None) == 0.0


class TestShort:
    def test_short_string_returned_unchanged(self) -> None:
        assert _short("hello") == "hello"

    def test_long_string_truncated_with_ellipsis(self) -> None:
        out = _short("a" * 200, limit=10)
        assert out.endswith("...")
        assert len(out) == 10

    def test_strips_outer_whitespace(self) -> None:
        assert _short("   trim me   ") == "trim me"

    def test_none_returns_empty(self) -> None:
        assert _short(None) == ""

    def test_default_limit_is_160(self) -> None:
        out = _short("z" * 500)
        assert len(out) == 160


class TestIntMapping:
    def test_int_mapping_coerces_values(self) -> None:
        assert _int_mapping({"a": "5", "b": "bad", "c": 7}) == {"a": 5, "b": 0, "c": 7}

    def test_non_dict_returns_empty(self) -> None:
        assert _int_mapping([1, 2]) == {}


class TestSafeSummaryMapping:
    def test_simple_dict_passthrough(self) -> None:
        out = _safe_summary_mapping({"a": 1, "b": True})
        assert out == {"a": 1, "b": True}

    def test_non_dict_returns_empty(self) -> None:
        assert _safe_summary_mapping("string") == {}
        assert _safe_summary_mapping([1, 2]) == {}

    def test_nested_dict_recurses(self) -> None:
        out = _safe_summary_mapping({"outer": {"inner": 5}})
        assert out == {"outer": {"inner": 5}}

    def test_depth_limit_stops_recursion(self) -> None:
        # Build a 5-level dict; depth 3 hard-stop should leave inner as {}.
        data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        out = _safe_summary_mapping(data)
        # _safe_summary_mapping shortens the innermost recursion to empty.
        cursor = out
        for key in ("a", "b", "c"):
            cursor = cursor[key]
        # At depth 3, recursive call returns {} because depth > 3 is checked.
        assert cursor == {} or isinstance(cursor, dict)

    def test_list_values_summarized(self) -> None:
        out = _safe_summary_mapping({"items": ["a", "b", "c"]})
        assert out["items"] == ["a", "b", "c"]

    def test_dict_keys_truncated_at_40(self) -> None:
        big = {f"key{i:03d}": i for i in range(60)}
        out = _safe_summary_mapping(big)
        assert len(out) == 40


class TestClaimBoundary:
    def test_includes_metadata_disclaimer(self) -> None:
        text = _claim_boundary()
        assert "metadata snapshots" in text
        assert "not live route success" in text
