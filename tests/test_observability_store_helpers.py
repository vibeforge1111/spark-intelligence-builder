"""Pure-helper coverage for observability.store payload/hash utilities.

``summarize_payload``, ``payload_hash``, ``build_text_mutation_facts``, and the
trace-token sanitiser are used across run/event/quarantine recording paths,
but none are asserted directly today. These tests fix the public shape so a
JSON-key ordering or hash-algorithm regression cannot drift unnoticed.
"""
from __future__ import annotations

import re

import pytest

from spark_intelligence.observability.store import (
    _trace_token,
    build_text_mutation_facts,
    payload_hash,
    summarize_payload,
    utc_now_iso,
)


class TestPayloadHash:
    def test_stable_for_same_dict_regardless_of_order(self) -> None:
        a = {"x": 1, "y": 2}
        b = {"y": 2, "x": 1}
        assert payload_hash(a) == payload_hash(b)

    def test_sha256_hex_shape(self) -> None:
        h = payload_hash({"a": 1})
        assert re.fullmatch(r"[0-9a-f]{64}", h)

    def test_distinct_values_produce_distinct_hashes(self) -> None:
        assert payload_hash({"a": 1}) != payload_hash({"a": 2})


class TestSummarizePayload:
    def test_dict_payload_shape(self) -> None:
        out = summarize_payload({"x": 1, "y": 2})
        assert out["type"] == "dict"
        assert out["key_count"] == 2
        assert out["keys"] == ["x", "y"]
        assert "hash" in out

    def test_list_payload_shape(self) -> None:
        out = summarize_payload([10, 20, 30])
        assert out["type"] == "list"
        assert out["length"] == 3

    def test_string_payload_truncates_preview(self) -> None:
        out = summarize_payload("z" * 200)
        assert out["type"] == "str"
        assert out["length"] == 200
        assert len(out["preview"]) == 80

    def test_scalar_payload_shape(self) -> None:
        out = summarize_payload(42)
        assert out["type"] == "int"
        assert out["value"] == 42
        assert "hash" in out

    def test_dict_keys_truncated_at_20(self) -> None:
        out = summarize_payload({f"k{i}": i for i in range(30)})
        assert out["key_count"] == 30
        assert len(out["keys"]) == 20


class TestBuildTextMutationFacts:
    def test_mutation_actions_set_flag_true(self) -> None:
        facts = build_text_mutation_facts(
            raw_text="hi", mutated_text="hi", mutation_actions=["redact"]
        )
        assert facts["text_mutated"] is True
        assert facts["mutation_actions"] == ["redact"]

    def test_diff_between_raw_and_mutated_sets_flag_true(self) -> None:
        facts = build_text_mutation_facts(raw_text="alpha", mutated_text="beta")
        assert facts["text_mutated"] is True

    def test_no_diff_and_no_actions_yields_false(self) -> None:
        facts = build_text_mutation_facts(raw_text="alpha", mutated_text="alpha")
        assert facts["text_mutated"] is False

    def test_raw_text_includes_ref_and_length(self) -> None:
        facts = build_text_mutation_facts(raw_text="hello world", mutated_text=None)
        assert facts["raw_text_length"] == len("hello world")
        assert facts["raw_text_ref"].startswith("text:sha256:")

    def test_mutated_text_includes_ref_and_length(self) -> None:
        facts = build_text_mutation_facts(raw_text=None, mutated_text="hi")
        assert facts["mutated_text_length"] == 2
        assert facts["mutated_text_ref"].startswith("text:sha256:")

    def test_none_inputs_omit_ref_fields(self) -> None:
        facts = build_text_mutation_facts(raw_text=None, mutated_text=None)
        assert "raw_text_ref" not in facts
        assert "mutated_text_ref" not in facts
        assert facts["text_mutated"] is False

    def test_blank_mutation_actions_filtered(self) -> None:
        facts = build_text_mutation_facts(
            raw_text="x", mutated_text="x", mutation_actions=["", "real"]
        )
        # Empty strings are dropped from the actions list.
        assert facts["mutation_actions"] == ["real"]


class TestTraceToken:
    def test_strips_unsafe_characters(self) -> None:
        assert _trace_token("hello world!") == "hello-world"

    def test_keeps_safe_characters(self) -> None:
        out = _trace_token("session.id:123_abc-9")
        assert out == "session.id:123_abc-9"

    def test_caps_length_at_96(self) -> None:
        assert len(_trace_token("a" * 200)) == 96

    def test_empty_input_returns_unknown(self) -> None:
        assert _trace_token("") == "unknown"

    def test_none_input_returns_unknown(self) -> None:
        assert _trace_token(None) == "unknown"


class TestUtcNowIso:
    def test_returns_iso_string_with_microseconds(self) -> None:
        out = utc_now_iso()
        # ISO 8601 ish: includes T separator and microsecond block.
        assert "T" in out
        # Microsecond precision means ".XXXXXX" should appear before the offset.
        assert "." in out

    def test_includes_timezone_offset(self) -> None:
        out = utc_now_iso()
        assert "+00:00" in out or out.endswith("Z")
