"""Coverage for the pure helpers in self_awareness.handoff_check.

``build_handoff_freshness_check`` already has integration coverage, but the
internal path-normalisation, trigger-prefix matching, warning-aggregation, and
text/json rendering helpers are not asserted directly. These tests pin the
contract so a future refactor of the trigger set or warning vocabulary cannot
slip through silently.
"""
from __future__ import annotations

import json

import pytest

from spark_intelligence.self_awareness.handoff_check import (
    HandoffFreshnessCheckResult,
    REQUIRED_DOCS,
    TRIGGER_PREFIXES,
    _is_trigger_path,
    _normalize_paths,
    _warnings,
)


class TestNormalizePaths:
    def test_strips_leading_dot_slash(self) -> None:
        assert _normalize_paths(["./src/foo.py"]) == ["src/foo.py"]

    def test_replaces_backslashes_with_forward(self) -> None:
        assert _normalize_paths(["src\\foo\\bar.py"]) == ["src/foo/bar.py"]

    def test_drops_empty_and_whitespace_only(self) -> None:
        assert _normalize_paths(["", "   ", "real.py"]) == ["real.py"]

    def test_deduplicates_while_preserving_order(self) -> None:
        assert _normalize_paths(["a.py", "b.py", "a.py"]) == ["a.py", "b.py"]

    def test_accepts_tuple_input(self) -> None:
        assert _normalize_paths(("x.py", "y.py")) == ["x.py", "y.py"]


class TestIsTriggerPath:
    def test_recognized_self_awareness_prefix(self) -> None:
        assert _is_trigger_path("src/spark_intelligence/self_awareness/handoff_check.py") is True

    def test_recognized_llm_wiki_prefix(self) -> None:
        assert _is_trigger_path("src/spark_intelligence/llm_wiki/anything.py") is True

    def test_recognized_exact_match(self) -> None:
        assert _is_trigger_path("ops/natural-language-live-commands.json") is True

    def test_unrelated_path_rejected(self) -> None:
        assert _is_trigger_path("src/spark_intelligence/unrelated/file.py") is False

    def test_prefix_must_match_start(self) -> None:
        # Substring match in the middle should NOT count as a trigger.
        assert _is_trigger_path("docs/self_awareness/spec.md") is False


class TestWarnings:
    def test_required_change_without_doc_update_warns(self) -> None:
        out = _warnings(
            doc_update_required=True,
            doc_update_present=False,
            missing_docs=[],
            docs_with_missing_tokens=[],
        )
        assert "handoff_docs_not_updated_with_self_awareness_change" in out

    def test_required_with_doc_update_no_warning(self) -> None:
        out = _warnings(
            doc_update_required=True,
            doc_update_present=True,
            missing_docs=[],
            docs_with_missing_tokens=[],
        )
        assert out == []

    def test_missing_doc_warning(self) -> None:
        out = _warnings(
            doc_update_required=False,
            doc_update_present=False,
            missing_docs=[{"path": "docs/x.md"}],
            docs_with_missing_tokens=[],
        )
        assert "handoff_required_docs_missing" in out

    def test_missing_tokens_warning(self) -> None:
        out = _warnings(
            doc_update_required=False,
            doc_update_present=False,
            missing_docs=[],
            docs_with_missing_tokens=[{"path": "docs/x.md", "missing_tokens": ["self handoff-check"]}],
        )
        assert "handoff_docs_missing_current_surface_tokens" in out

    def test_clean_inputs_produce_no_warnings(self) -> None:
        assert _warnings(
            doc_update_required=False,
            doc_update_present=False,
            missing_docs=[],
            docs_with_missing_tokens=[],
        ) == []


class TestResultRendering:
    def _result(self, *, status: str = "pass", warnings: list[str] | None = None) -> HandoffFreshnessCheckResult:
        payload = {
            "status": status,
            "summary": {
                "triggered_change_count": 1,
                "doc_count": 3,
                "docs_with_missing_tokens": 0,
            },
            "report_path": "artifacts/handoff-freshness/x.json",
            "warnings": warnings or [],
        }
        return HandoffFreshnessCheckResult(payload=payload)

    def test_to_text_pass_omits_warning_lines(self) -> None:
        text = self._result().to_text()
        assert "warning:" not in text
        assert "status: pass" in text

    def test_to_text_blocked_includes_warning_lines(self) -> None:
        text = self._result(status="blocked", warnings=["handoff_docs_not_updated_with_self_awareness_change"]).to_text()
        assert "warning: handoff_docs_not_updated_with_self_awareness_change" in text
        assert "status: blocked" in text

    def test_to_json_is_round_trippable(self) -> None:
        result = self._result()
        decoded = json.loads(result.to_json())
        assert decoded["status"] == "pass"
        assert decoded["summary"]["doc_count"] == 3

    def test_to_text_includes_authority_disclaimer(self) -> None:
        text = self._result().to_text()
        assert "authority: observability_non_authoritative" in text


class TestModuleContracts:
    def test_trigger_prefixes_is_tuple(self) -> None:
        # Tuples (not lists) signal "frozen public surface" — verify shape.
        assert isinstance(TRIGGER_PREFIXES, tuple)
        assert all(isinstance(item, str) for item in TRIGGER_PREFIXES)

    def test_required_docs_is_dict_of_tuples(self) -> None:
        assert isinstance(REQUIRED_DOCS, dict)
        for path, tokens in REQUIRED_DOCS.items():
            assert isinstance(path, str)
            assert isinstance(tokens, tuple)
            assert tokens  # required tokens list must not be empty
