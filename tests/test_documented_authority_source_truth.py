from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_harness_contract_names_current_authority_chain_without_private_source_path() -> None:
    text = _read("docs/SPARK_HARNESS_CONTRACT.md")

    for marker in (
        "TurnIntentEnvelopeVNext",
        "GovernorDecisionV1",
        "AuthorizationDecisionV1",
        "ToolCallLedgerV1",
        "owner consumer verification",
        "installed distribution",
        "registered clean module source",
    ):
        assert marker in text
    assert "spark.turn_intent.v1" in text
    assert "not sufficient execution authority by itself" in text
    assert "/Users/" not in text
    assert "work/repos/spark-harness-core" not in text


def test_route_firewall_remains_evidence_and_early_denial_not_action_authority() -> None:
    doctrine = _read("docs/ROUTE_CONFIDENCE_DOCTRINE_V1_2026-05-12.md")
    hierarchy = _read("docs/ROUTE_CONFIDENCE_GATE_V1.md")

    assert "route/firewall prechecks as evidence and early denial" in doctrine
    assert "must not become route-confidence or execution authorities" in doctrine
    assert "Harness Core envelope plus route/firewall read-only classification" in hierarchy


def test_telegram_docs_require_both_firewall_and_harness_authority_proof() -> None:
    for path in ("docs/TURNINTENT_AGENTS_ADOPTION.md", "docs/TURNINTENT_HARNESS_RULESET.md"):
        text = _read(path)
        assert "route firewall tests" in text
        assert "Harness Core action authority tests" in text


def test_runtime_source_truth_is_discovered_not_hard_coded_to_one_checkout() -> None:
    agents = _read("AGENTS.md")
    gate = _read("docs/ROUTE_CONFIDENCE_GATE_V1_2026-05-12.md")

    assert "registry/install metadata, import root, and Git cleanliness agree" in agents
    assert "Port backlog or mirror work by capability slice" in agents
    assert "spark-intelligence self route-confidence-gate" in gate
    assert "spark-intelligence-builder-release" not in gate
    assert "spark-intelligence-builder\\source" not in gate
