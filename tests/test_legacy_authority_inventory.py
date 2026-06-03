from __future__ import annotations

from spark_intelligence.legacy_authority_inventory import (
    build_builder_legacy_authority_inventory,
    build_builder_legacy_authority_planes,
)


def _has_high_agency_risk(plane: dict) -> bool:
    return any(bool(value) for value in dict(plane.get("authority_risk") or {}).values())


def test_builder_legacy_authority_inventory_is_release_ready() -> None:
    inventory = build_builder_legacy_authority_inventory()

    assert inventory["schema_version"] == "legacy-authority-inventory-v1"
    assert inventory["scope"]["owner_repo"] == "spark-intelligence-builder"
    assert set(inventory["scope"]["surfaces"]) == {
        "builder",
        "telegram",
        "memory",
        "browser",
        "domain_chip",
        "recursive_swarm",
    }
    assert inventory["summary"]["plane_count"] == len(inventory["planes"])
    assert inventory["summary"]["plane_count"] >= 16
    assert inventory["summary"]["release_blocker_count"] == 0
    assert inventory["release_gate"]["zero_high_agency_legacy_local_gates"] is True
    assert inventory["release_gate"]["ready_for_readiness_promotion"] is True
    assert inventory["release_gate"]["blockers"] == []


def test_builder_old_detectors_are_evidence_only_or_disabled() -> None:
    planes = build_builder_legacy_authority_planes()
    evidence_or_disabled = [
        plane
        for plane in planes
        if plane["disposition"] in {"rebound_to_harness_evidence", "disabled"}
    ]

    assert len(evidence_or_disabled) >= 9
    for plane in evidence_or_disabled:
        assert not _has_high_agency_risk(plane)
        assert plane["blockers"] == []
        if plane["disposition"] == "rebound_to_harness_evidence":
            assert plane["harness_binding"]["evidence_only"] is True
            assert plane["harness_binding"]["consumer_of_governor"] is False


def test_builder_high_agency_consumers_require_governor_and_ledgers() -> None:
    inventory = build_builder_legacy_authority_inventory()
    consumers = [
        plane
        for plane in inventory["planes"]
        if plane["disposition"] == "converted_to_harness_consumer"
    ]

    assert len(consumers) >= 6
    for plane in consumers:
        assert _has_high_agency_risk(plane)
        assert plane["harness_binding"]["governor_required"] is True
        assert plane["harness_binding"]["consumer_of_governor"] is True
        assert plane["harness_binding"]["ledger_required"] is True
        assert plane["blockers"] == []


def test_builder_inventory_names_recently_cleaned_old_planes() -> None:
    plane_ids = {plane["plane_id"] for plane in build_builder_legacy_authority_planes()}

    assert "legacy-plane:builder-legacy-v1-governed-migration-adapter" in plane_ids
    assert "legacy-plane:builder-swarm-keyword-escalation-without-readiness" in plane_ids
    assert "legacy-plane:builder-harness-recipe-keywords" in plane_ids
    assert "legacy-plane:builder-bridge-authority-vnext" in plane_ids
