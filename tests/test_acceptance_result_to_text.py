"""Focused tests for TelegramMemoryAcceptanceResult.to_text()."""

from __future__ import annotations

from pathlib import Path

from spark_intelligence.memory.acceptance import TelegramMemoryAcceptanceResult


def _make_result(payload: dict) -> TelegramMemoryAcceptanceResult:
    return TelegramMemoryAcceptanceResult(output_dir=Path("/tmp/acc"), payload=payload)


class TestToTextSummaryRendering:
    """Covers the summary-dict branch of to_text()."""

    def test_full_summary_renders_status_fields(self) -> None:
        result = _make_result({
            "summary": {
                "status": "passed",
                "case_count": 8,
                "matched_case_count": 8,
                "mismatched_case_count": 0,
                "promotion_gate_status": "pass",
                "selected_user_id": "u-42",
            },
        })
        text = result.to_text()
        assert "- status: passed" in text
        assert "- cases: 8" in text
        assert "- matched: 8" in text
        assert "- mismatched: 0" in text
        assert "- gate_status: pass" in text
        assert "- selected_user_id: u-42" in text

    def test_missing_summary_fields_default_gracefully(self) -> None:
        result = _make_result({"summary": {}})
        text = result.to_text()
        assert "- status: unknown" in text
        assert "- cases: 0" in text
        assert "- matched: 0" in text
        assert "- mismatched: 0" in text
        assert "- gate_status: unknown" in text
        assert "- selected_user_id: unknown" in text

    def test_none_summary_values_render_unknown_or_zero(self) -> None:
        result = _make_result({
            "summary": {
                "status": None,
                "case_count": 3,
                "matched_case_count": 2,
                "mismatched_case_count": 1,
                "promotion_gate_status": None,
                "selected_user_id": None,
            },
        })
        text = result.to_text()
        assert "- status: unknown" in text
        assert "- gate_status: unknown" in text
        assert "- selected_user_id: unknown" in text
        assert "- cases: 3" in text
        assert "- mismatched: 1" in text


class TestToTextMismatches:
    """Covers case-mismatch rendering."""

    def test_mismatches_render_case_id_and_reasons(self) -> None:
        result = _make_result({
            "summary": {"status": "failed"},
            "mismatches": [
                {
                    "case_id": "seed_focus",
                    "mismatches": ["response_missing:persistent memory quality evaluation"],
                },
            ],
        })
        text = result.to_text()
        assert "- mismatches:" in text
        assert "seed_focus:" in text
        assert "response_missing:persistent memory quality evaluation" in text

    def test_mismatch_with_empty_inner_list_shows_case_id_only(self) -> None:
        result = _make_result({
            "summary": {"status": "failed"},
            "mismatches": [
                {"case_id": "ghost", "mismatches": []},
            ],
        })
        text = result.to_text()
        assert "- ghost:" in text

    def test_no_mismatches_section_omitted(self) -> None:
        result = _make_result({"summary": {"status": "passed"}, "mismatches": []})
        text = result.to_text()
        assert "- mismatches:" not in text

    def test_more_than_eight_mismatches_truncated(self) -> None:
        mismatches = [{"case_id": f"case_{i}", "mismatches": ["reason"]} for i in range(10)]
        result = _make_result({"summary": {"status": "failed"}, "mismatches": mismatches})
        text = result.to_text()
        # only first 8 should appear
        assert "case_0:" in text
        assert "case_7:" in text
        assert "case_8:" not in text
        assert "case_9:" not in text


class TestToTextGateMismatches:
    """Covers gate-mismatch rendering."""

    def test_gate_mismatches_rendered(self) -> None:
        result = _make_result({
            "summary": {"status": "failed"},
            "gate_assertions": {
                "mismatches": ["promotion_gate_status:missing", "source_swamp_resistance:missing"],
            },
        })
        text = result.to_text()
        assert "- gate mismatches:" in text
        assert "promotion_gate_status:missing" in text
        assert "source_swamp_resistance:missing" in text

    def test_no_gate_mismatches_omitted(self) -> None:
        result = _make_result({
            "summary": {"status": "passed"},
            "gate_assertions": {"mismatches": []},
        })
        text = result.to_text()
        assert "- gate mismatches:" not in text


class TestToTextGateAssertionsDetail:
    """Covers promotion gate evidence counts and source_mix breakdown
    rendered from gate_assertions.promotion_gates.gates and
    gate_assertions.source_mix."""

    def test_promotion_gates_evidence_rendered(self) -> None:
        result = _make_result({
            "summary": {"status": "passed"},
            "gate_assertions": {
                "status": "pass",
                "mismatches": [],
                "enforcement": {"mode": "blocking_acceptance", "blocking": False},
                "promotion_gates": {
                    "status": "pass",
                    "gates": {
                        "source_swamp_resistance": {"status": "pass", "evidence": 4},
                        "stale_current_conflict": {"status": "pass", "evidence": 2},
                        "recent_conversation_noise": {"status": "warn", "evidence": 1},
                        "source_mix_stability": {"status": "pass", "evidence": 3},
                    },
                },
                "source_mix": {},
            },
        })
        text = result.to_text()
        assert "- promotion gates:" in text
        assert "source_swamp_resistance: pass (evidence: 4)" in text
        assert "stale_current_conflict: pass (evidence: 2)" in text
        assert "recent_conversation_noise: warn (evidence: 1)" in text
        assert "source_mix_stability: pass (evidence: 3)" in text

    def test_source_mix_breakdown_rendered(self) -> None:
        result = _make_result({
            "summary": {"status": "passed"},
            "gate_assertions": {
                "status": "pass",
                "mismatches": [],
                "enforcement": {"mode": "blocking_acceptance", "blocking": False},
                "promotion_gates": {},
                "source_mix": {
                    "current_state": 3,
                    "hybrid_retrieve": 2,
                    "conversation_context": 1,
                },
            },
        })
        text = result.to_text()
        assert "- source_mix:" in text
        assert "current_state: 3" in text
        assert "hybrid_retrieve: 2" in text
        assert "conversation_context: 1" in text

    def test_missing_promotion_gates_omitted(self) -> None:
        result = _make_result({
            "summary": {"status": "passed"},
            "gate_assertions": {
                "status": "pass",
                "mismatches": [],
                "promotion_gates": {},
                "source_mix": {"current_state": 1},
            },
        })
        text = result.to_text()
        assert "- promotion gates:" not in text
        assert "- source_mix:" in text
        assert "current_state: 1" in text

    def test_empty_source_mix_omitted(self) -> None:
        result = _make_result({
            "summary": {"status": "passed"},
            "gate_assertions": {
                "status": "pass",
                "mismatches": [],
                "promotion_gates": {
                    "gates": {"source_swamp_resistance": {"status": "pass", "evidence": 1}},
                },
                "source_mix": {},
            },
        })
        text = result.to_text()
        assert "- promotion gates:" in text
        assert "source_swamp_resistance: pass (evidence: 1)" in text
        assert "- source_mix:" not in text

    def test_no_gate_assertions_at_all(self) -> None:
        result = _make_result({
            "summary": {"status": "passed"},
        })
        text = result.to_text()
        assert "- promotion gates:" not in text
        assert "- source_mix:" not in text


class TestToTextEdgeCases:
    """Covers edge cases: empty payload, non-dict values, missing keys."""

    def test_empty_payload(self) -> None:
        result = _make_result({})
        text = result.to_text()
        assert "Spark Telegram memory acceptance" in text
        assert "- output_dir:" in text
        # no summary fields, no mismatches
        assert "- status:" not in text

    def test_output_dir_always_present(self) -> None:
        result = _make_result({"summary": {"status": "passed"}})
        text = result.to_text()
        assert f"- output_dir: {Path('/tmp/acc')}" in text
