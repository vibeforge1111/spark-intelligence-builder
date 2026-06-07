"""Coverage for the capability-drift heartbeat helpers.

The end-to-end ``build_capability_drift_heartbeat`` is exercised in
``test_self_awareness``, but the inner classifiers and probe-registry
de-duplicators have no direct coverage. These tests fix the confidence/
freshness/warning vocabulary so a future refactor of the heartbeat report
schema cannot silently change which probes run.
"""
from __future__ import annotations

import pytest

from spark_intelligence.self_awareness.heartbeat import (
    _age_days,
    _drift_kind,
    _needs_probe,
    _parse_iso,
    _probe_registry_by_key,
    _safe_probe,
    _warnings,
)


class TestNeedsProbe:
    def test_recent_failure_needs_probe(self) -> None:
        assert _needs_probe({"confidence_level": "recent_failure"}) is True

    def test_observed_without_success_needs_probe(self) -> None:
        assert _needs_probe({"confidence_level": "observed_without_success"}) is True

    def test_stale_success_needs_probe(self) -> None:
        assert _needs_probe({"confidence_level": "stale_success"}) is True

    def test_aging_freshness_without_confident_claim_needs_probe(self) -> None:
        row = {"freshness_status": "aging", "can_claim_confidently": False}
        assert _needs_probe(row) is True

    def test_confident_claim_skips_probe(self) -> None:
        row = {"freshness_status": "aging", "can_claim_confidently": True}
        assert _needs_probe(row) is False

    def test_fresh_state_no_probe(self) -> None:
        assert _needs_probe({"freshness_status": "fresh"}) is False


class TestDriftKind:
    def test_confidence_level_takes_precedence(self) -> None:
        assert _drift_kind({"confidence_level": "recent_failure"}) == "recent_failure"

    def test_freshness_status_appended_with_suffix(self) -> None:
        assert _drift_kind({"freshness_status": "aging"}) == "aging_freshness"
        assert _drift_kind({"freshness_status": "stale"}) == "stale_freshness"

    def test_unknown_freshness_appended(self) -> None:
        assert _drift_kind({"freshness_status": "unknown"}) == "unknown_freshness"

    def test_no_signals_returns_default(self) -> None:
        assert _drift_kind({}) == "needs_probe"


class TestWarnings:
    def test_stale_success_warning(self) -> None:
        out = _warnings(summary={"stale_success_count": 1}, unverified_count=0)
        assert "capability_last_success_stale" in out

    def test_recent_failure_warning(self) -> None:
        out = _warnings(summary={"recent_failure_count": 2}, unverified_count=0)
        assert "capability_recent_failure_needs_probe" in out

    def test_observed_without_success_warning(self) -> None:
        out = _warnings(summary={"observed_without_success_count": 1}, unverified_count=0)
        assert "capability_observed_without_success" in out

    def test_missing_eval_coverage_warning(self) -> None:
        out = _warnings(summary={"missing_eval_coverage_count": 1}, unverified_count=0)
        assert "capability_eval_coverage_missing" in out

    def test_unverified_count_warning(self) -> None:
        out = _warnings(summary={}, unverified_count=3)
        assert "configured_capabilities_missing_recent_success" in out

    def test_clean_summary_produces_no_warnings(self) -> None:
        assert _warnings(summary={}, unverified_count=0) == []


class TestParseIso:
    def test_z_suffix_normalized(self) -> None:
        out = _parse_iso("2026-05-29T12:00:00Z")
        assert out is not None
        assert out.tzinfo is not None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_iso("") is None

    def test_invalid_input_returns_none(self) -> None:
        assert _parse_iso("garbage") is None

    def test_naive_datetime_gets_utc_offset(self) -> None:
        out = _parse_iso("2026-05-29T12:00:00")
        assert out is not None
        assert out.tzinfo is not None


class TestAgeDays:
    def test_unparseable_returns_none(self) -> None:
        assert _age_days("not iso") is None

    def test_age_in_days_non_negative(self) -> None:
        # Some past date.
        out = _age_days("2020-01-01T00:00:00Z")
        assert out is not None
        assert out >= 0

    def test_empty_value_returns_none(self) -> None:
        assert _age_days("") is None


class TestProbeRegistryByKey:
    def test_higher_priority_kind_wins(self) -> None:
        rows = [
            {"target_key": "k1", "target_kind": "path"},
            {"target_key": "k1", "target_kind": "chip"},
        ]
        out = _probe_registry_by_key(rows)
        assert out["k1"]["target_kind"] == "chip"

    def test_blank_key_skipped(self) -> None:
        rows = [{"target_key": "", "target_kind": "chip"}, {"target_key": "k1", "target_kind": "path"}]
        out = _probe_registry_by_key(rows)
        assert out == {"k1": {"target_key": "k1", "target_kind": "path"}}

    def test_unknown_kind_treated_as_zero_priority(self) -> None:
        # Lower priority "exotic" should be replaced by "chip".
        rows = [
            {"target_key": "k1", "target_kind": "exotic"},
            {"target_key": "k1", "target_kind": "chip"},
        ]
        out = _probe_registry_by_key(rows)
        assert out["k1"]["target_kind"] == "chip"


class TestSafeProbe:
    def test_probe_record_override_used(self) -> None:
        assert _safe_probe(key="k1", probe_record={"safe_probe": "override"}) == "override"

    def test_falls_back_to_default_format(self) -> None:
        out = _safe_probe(key="k1", probe_record=None)
        assert "spark-intelligence self status" in out
        assert "probe k1" in out

    def test_blank_record_safe_probe_falls_back(self) -> None:
        out = _safe_probe(key="k1", probe_record={"safe_probe": ""})
        assert "spark-intelligence self status" in out
