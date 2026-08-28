"""Pure-helper coverage for chip_create.pipeline.

The end-to-end ``create_chip_from_prompt`` is exercised via integration in
``test_chip_create_paths``, but the small inner helpers — fence stripping,
brief validation, command normalisation, and the ``ChipCreateResult``
serialiser — are not asserted directly. These tests pin the contract so a
future refactor of the LLM brief parser cannot silently relax validation.
"""
from __future__ import annotations

import pytest

from spark_intelligence.chip_create.pipeline import (
    ChipCreateResult,
    _normalize_commands,
    _strip_code_fences,
    _validate_brief,
)


class TestStripCodeFences:
    def test_plain_json_unchanged(self) -> None:
        assert _strip_code_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fenced_block_stripped(self) -> None:
        assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_bare_fenced_block_stripped(self) -> None:
        assert _strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_leading_and_trailing_whitespace_collapsed(self) -> None:
        assert _strip_code_fences('   \n```json\n{"a":1}\n```   ') == '{"a":1}'

    def test_no_closing_fence_still_strips_opener(self) -> None:
        assert _strip_code_fences('```json\n{"a":1}') == '{"a":1}'


class TestValidateBrief:
    def test_empty_brief_collects_all_missing_field_errors(self) -> None:
        errors = _validate_brief({})
        assert "missing domain_id" in errors
        assert "missing domain_name" in errors
        assert "missing mutation_axes" in errors
        assert "missing primary_metric" in errors

    def test_complete_brief_returns_no_errors(self) -> None:
        brief = {
            "domain_id": "supply-chain-risk",
            "domain_name": "Supply Chain Risk",
            "mutation_axes": [{"name": "axis", "values": ["a", "b"]}],
            "primary_metric": "risk_coverage_score",
        }
        assert _validate_brief(brief) == []

    def test_mutation_axes_non_dict_flagged(self) -> None:
        brief = {
            "domain_id": "x",
            "domain_name": "X",
            "mutation_axes": ["not a dict"],
            "primary_metric": "score",
        }
        errors = _validate_brief(brief)
        assert any("mutation_axes[0] must be object" in err for err in errors)

    def test_mutation_axes_missing_values_flagged(self) -> None:
        brief = {
            "domain_id": "x",
            "domain_name": "X",
            "mutation_axes": [{"name": "axis", "values": "not a list"}],
            "primary_metric": "score",
        }
        errors = _validate_brief(brief)
        assert any("needs name + values list" in err for err in errors)

    def test_mutation_axes_missing_name_flagged(self) -> None:
        brief = {
            "domain_id": "x",
            "domain_name": "X",
            "mutation_axes": [{"values": ["a", "b"]}],
            "primary_metric": "score",
        }
        errors = _validate_brief(brief)
        assert any("needs name + values list" in err for err in errors)


class TestNormalizeCommands:
    def test_non_dict_returns_empty_dict(self) -> None:
        assert _normalize_commands("not a dict") == {}
        assert _normalize_commands(None) == {}

    def test_list_command_parts_preserved(self) -> None:
        out = _normalize_commands({"run": ["python", "-m", "chip"]})
        assert out["run"] == ["python", "-m", "chip"]

    def test_string_command_split_on_whitespace(self) -> None:
        out = _normalize_commands({"run": "python -m chip"})
        assert out["run"] == ["python", "-m", "chip"]

    def test_input_output_placeholders_stripped(self) -> None:
        out = _normalize_commands({"run": "python -m chip --input {input} --output {output}"})
        assert "--input" not in out["run"]
        assert "{input}" not in out["run"]

    def test_trailing_placeholder_tokens_removed(self) -> None:
        out = _normalize_commands({"run": ["python", "-m", "chip", "--input", "{input}"]})
        assert out["run"] == ["python", "-m", "chip"]

    def test_unsupported_value_type_skipped(self) -> None:
        # Non-string/list values are dropped entirely.
        out = _normalize_commands({"run": 123})
        assert "run" not in out

    def test_empty_string_command_dropped(self) -> None:
        out = _normalize_commands({"run": ""})
        assert "run" not in out


class TestChipCreateResult:
    def test_to_dict_includes_all_fields(self) -> None:
        result = ChipCreateResult(
            ok=True,
            chip_key="supply-chain-risk",
            chip_path="/tmp/chip",
            brief={"domain_id": "supply-chain-risk"},
            router_invokable=True,
            warnings=["w1"],
        )
        payload = result.to_dict()
        assert payload["ok"] is True
        assert payload["chip_key"] == "supply-chain-risk"
        assert payload["warnings"] == ["w1"]
        assert payload["error"] is None

    def test_default_warnings_is_empty_list_not_shared(self) -> None:
        # Ensure dataclass field(default_factory=list) is per-instance.
        result_a = ChipCreateResult(ok=False)
        result_b = ChipCreateResult(ok=False)
        result_a.warnings.append("only-on-a")
        assert result_b.warnings == []

    def test_failure_state_round_trips(self) -> None:
        result = ChipCreateResult(ok=False, error="bad LLM brief")
        payload = result.to_dict()
        assert payload["ok"] is False
        assert payload["error"] == "bad LLM brief"
        assert payload["chip_key"] is None
