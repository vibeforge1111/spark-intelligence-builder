from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


EXPECTED_GUARDRAIL_DENIAL_REASONS = {
    "external_network_not_authorized",
    "mutation_class_not_authorized",
    "no_execution_boundary",
    "no_publish_boundary",
    "proposed_action_not_authorized",
    "tool_denied_by_policy",
    "tool_not_allowed_by_policy",
}
RESOLVED_STATUSES = {"closed", "ok", "recorded", "resolved", "succeeded"}
RESOLVED_SEVERITIES = {"", "info", "low", "medium"}


def resolve_expected_guardrail_denial_events(
    state_db: StateDB,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    groups = _expected_guardrail_denial_groups(state_db)
    resolved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for group in groups:
        latest = _latest_group_lifecycle(state_db, group)
        if _is_resolved_lifecycle(latest):
            skipped.append({**group, "skip_reason": "latest_lifecycle_resolved"})
            continue
        if apply:
            event_id = _append_guardrail_resolution(state_db, group)
            resolved.append({**group, "resolution_event_id": event_id})
        else:
            resolved.append(group)
    return {
        "applied": apply,
        "candidate_group_count": len(groups),
        "resolved_group_count": len(resolved) if apply else 0,
        "would_resolve_group_count": len(resolved) if not apply else 0,
        "skipped_group_count": len(skipped),
        "resolved_groups": resolved,
        "skipped_groups": skipped,
    }


def default_builder_state_db() -> StateDB:
    return StateDB(Path.home() / ".spark" / "state" / "spark-intelligence" / "state.db")


def _expected_guardrail_denial_groups(state_db: StateDB) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            select
              coalesce(nullif(trim(component), ''), '[missing]') as component,
              coalesce(nullif(trim(event_type), ''), '[missing]') as event_type,
              coalesce(nullif(trim(reason_code), ''), '[missing]') as reason_code,
              coalesce(nullif(trim(target_surface), ''), '[missing]') as target_surface,
              coalesce(nullif(trim(evidence_lane), ''), '[missing]') as evidence_lane,
              count(*) as event_count,
              max(datetime(created_at)) as latest_open_created_at
            from builder_events
            where lower(coalesce(severity, '')) in ('high', 'critical')
              and lower(coalesce(status, '')) in ('open', 'failed', 'error', 'blocked')
              and event_type = 'tool_call_ledger_recorded'
              and reason_code in (
                'external_network_not_authorized',
                'mutation_class_not_authorized',
                'no_execution_boundary',
                'no_publish_boundary',
                'proposed_action_not_authorized',
                'tool_denied_by_policy',
                'tool_not_allowed_by_policy'
              )
            group by component, event_type, reason_code, target_surface, evidence_lane
            order by latest_open_created_at desc
            """
        ).fetchall()
    return [
        {
            "component": str(row["component"]),
            "event_type": str(row["event_type"]),
            "reason_code": str(row["reason_code"]),
            "target_surface": str(row["target_surface"]),
            "evidence_lane": str(row["evidence_lane"]),
            "event_count": int(row["event_count"] or 0),
            "latest_open_created_at": str(row["latest_open_created_at"] or ""),
        }
        for row in rows
    ]


def _latest_group_lifecycle(state_db: StateDB, group: dict[str, Any]) -> dict[str, str] | None:
    with state_db.connect() as conn:
        row = conn.execute(
            """
            select status, severity, created_at
            from builder_events
            where coalesce(nullif(trim(component), ''), '[missing]') = ?
              and coalesce(nullif(trim(event_type), ''), '[missing]') = ?
              and coalesce(nullif(trim(reason_code), ''), '[missing]') = ?
              and coalesce(nullif(trim(target_surface), ''), '[missing]') = ?
              and coalesce(nullif(trim(evidence_lane), ''), '[missing]') = ?
            order by datetime(created_at) desc, rowid desc
            limit 1
            """,
            (
                group["component"],
                group["event_type"],
                group["reason_code"],
                group["target_surface"],
                group["evidence_lane"],
            ),
        ).fetchone()
    if row is None:
        return None
    return {
        "status": str(row["status"] or "").strip().lower(),
        "severity": str(row["severity"] or "").strip().lower(),
        "created_at": str(row["created_at"] or ""),
    }


def _is_resolved_lifecycle(latest: dict[str, str] | None) -> bool:
    if latest is None:
        return False
    return latest["status"] in RESOLVED_STATUSES and latest["severity"] in RESOLVED_SEVERITIES


def _append_guardrail_resolution(state_db: StateDB, group: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        "|".join(
            str(group.get(key) or "")
            for key in ("component", "event_type", "reason_code", "target_surface", "evidence_lane")
        ).encode("utf-8")
    ).hexdigest()[:16]
    return record_event(
        state_db,
        event_type=str(group["event_type"]),
        component=str(group["component"]),
        target_surface=str(group["target_surface"]),
        evidence_lane=str(group["evidence_lane"]),
        request_id=f"repair:expected_guardrail_denial:{digest}",
        trace_ref=f"trace:repair:expected_guardrail_denial:{digest}",
        summary="Expected Harness Core guardrail denial reclassified as recorded proof.",
        reason_code=str(group["reason_code"]),
        severity="medium",
        status="recorded",
        provenance={
            "source_kind": "expected_guardrail_denial_lifecycle_repair",
            "source_ref": f"builder.guardrail_denial:{digest}",
            "repair_scope": "append_only_resolution",
        },
        facts={
            "resolved_event_family": {
                "component": group["component"],
                "event_type": group["event_type"],
                "reason_code": group["reason_code"],
                "target_surface": group["target_surface"],
                "evidence_lane": group["evidence_lane"],
            },
            "resolved_event_count": int(group.get("event_count") or 0),
            "latest_open_created_at": group.get("latest_open_created_at"),
        },
    )
