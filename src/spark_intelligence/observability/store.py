from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from spark_intelligence.memory_contracts import (
    effective_memory_role,
    is_memory_contract_reason,
    memory_contract_reason,
    normalize_memory_role,
    persisted_memory_contract_reason,
)
from spark_intelligence.state.db import StateDB


FACT_TRUTH_KIND = "fact"
DEFAULT_EVIDENCE_LANE = "realworld_validated"
DEFAULT_SEVERITY = "medium"
DEFAULT_STATUS = "recorded"
BUILDER_TARGET_SURFACE = "spark_intelligence_builder"
WATCHTOWER_BACKGROUND_STALE_SECONDS = 900
NON_PROMOTABLE_KEEPABILITY = {
    "ephemeral_context",
    "user_preference_ephemeral",
    "operator_debug_only",
    "not_keepable",
}
NON_PROMOTABLE_DISPOSITIONS = {
    "blocked",
    "not_promotable",
    "quarantined",
    "quarantined_blocked",
}


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    run_kind: str
    origin_surface: str
    request_id: str | None
    trace_ref: str | None
    channel_id: str | None
    session_id: str | None
    human_id: str | None
    agent_id: str | None
    actor_id: str | None


@dataclass(frozen=True)
class EnvironmentSnapshotRecord:
    snapshot_id: str
    surface: str
    summary: str


def open_run(
    state_db: StateDB,
    *,
    run_kind: str,
    origin_surface: str,
    summary: str,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
    parent_run_id: str | None = None,
    reason_code: str | None = None,
    facts: dict[str, Any] | None = None,
) -> RunRecord:
    run_id = _prefixed_id("run")
    opened_at = utc_now_iso()
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO builder_runs(
                run_id,
                run_kind,
                origin_surface,
                parent_run_id,
                request_id,
                trace_ref,
                channel_id,
                session_id,
                human_id,
                agent_id,
                actor_id,
                status,
                opened_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                run_id,
                run_kind,
                origin_surface,
                parent_run_id,
                request_id,
                trace_ref,
                channel_id,
                session_id,
                human_id,
                agent_id,
                actor_id,
                opened_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO run_registry(
                run_id,
                run_kind,
                parent_run_id,
                session_id,
                surface_kind,
                status,
                opened_at,
                freshness_deadline,
                closure_reason
            )
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)
            """,
            (
                run_id,
                run_kind,
                parent_run_id,
                session_id,
                origin_surface,
                opened_at,
                None,
                None,
            ),
        )
        conn.commit()
    record_event(
        state_db,
        event_type="run_opened",
        component=origin_surface,
        summary=summary,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
        reason_code=reason_code,
        facts=facts or {"run_kind": run_kind, "origin_surface": origin_surface},
    )
    return RunRecord(
        run_id=run_id,
        run_kind=run_kind,
        origin_surface=origin_surface,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )


def close_run(
    state_db: StateDB,
    *,
    run_id: str,
    summary: str,
    close_reason: str,
    status: str = "closed",
    reason_code: str | None = None,
    facts: dict[str, Any] | None = None,
) -> None:
    event_type = _run_status_event_type(status)
    closed_at = utc_now_iso()
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE builder_runs
            SET
                status = ?,
                close_reason = ?,
                summary_json = ?,
                closed_at = ? 
            WHERE run_id = ?
            """,
            (
                status,
                close_reason,
                json.dumps(facts or {}, sort_keys=True),
                closed_at,
                run_id,
            ),
        )
        conn.execute(
            """
            UPDATE run_registry
            SET
                status = ?,
                closed_at = ?,
                closure_reason = ?
            WHERE run_id = ?
            """,
            (
                status,
                closed_at,
                close_reason,
                run_id,
            ),
        )
        row = conn.execute(
            """
            SELECT request_id, trace_ref, channel_id, session_id, human_id, agent_id, actor_id, origin_surface, run_kind
            FROM builder_runs
            WHERE run_id = ?
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        conn.commit()
    record_event(
        state_db,
        event_type=event_type,
        component=str(row["origin_surface"]) if row else "run_registry",
        summary=summary,
        run_id=run_id,
        request_id=str(row["request_id"]) if row and row["request_id"] else None,
        trace_ref=str(row["trace_ref"]) if row and row["trace_ref"] else None,
        channel_id=str(row["channel_id"]) if row and row["channel_id"] else None,
        session_id=str(row["session_id"]) if row and row["session_id"] else None,
        human_id=str(row["human_id"]) if row and row["human_id"] else None,
        agent_id=str(row["agent_id"]) if row and row["agent_id"] else None,
        actor_id=str(row["actor_id"]) if row and row["actor_id"] else None,
        reason_code=reason_code or close_reason,
        facts={
            "run_kind": str(row["run_kind"]) if row and row["run_kind"] else None,
            "status": status,
            "close_reason": close_reason,
            **(facts or {}),
        },
        severity="high" if status == "stalled" else DEFAULT_SEVERITY,
        status=status,
    )


def record_event(
    state_db: StateDB,
    *,
    event_type: str,
    component: str,
    summary: str,
    truth_kind: str = FACT_TRUTH_KIND,
    target_surface: str = BUILDER_TARGET_SURFACE,
    run_id: str | None = None,
    parent_event_id: str | None = None,
    correlation_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
    evidence_lane: str = DEFAULT_EVIDENCE_LANE,
    severity: str = DEFAULT_SEVERITY,
    status: str = DEFAULT_STATUS,
    reason_code: str | None = None,
    provenance: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> str:
    event_id = _prefixed_id("evt")
    recorded_at = utc_now_iso()
    normalized_facts = dict(facts or {})
    normalized_provenance = dict(provenance or {})
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO builder_events(
                event_id,
                event_type,
                truth_kind,
                target_surface,
                component,
                run_id,
                parent_event_id,
                correlation_id,
                request_id,
                trace_ref,
                channel_id,
                session_id,
                human_id,
                agent_id,
                actor_id,
                evidence_lane,
                severity,
                status,
                summary,
                reason_code,
                provenance_json,
                facts_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_type,
                truth_kind,
                target_surface,
                component,
                run_id,
                parent_event_id,
                correlation_id,
                request_id,
                trace_ref,
                channel_id,
                session_id,
                human_id,
                agent_id,
                actor_id,
                evidence_lane,
                severity,
                status,
                summary,
                reason_code,
                _json_or_none(normalized_provenance),
                _json_or_none(normalized_facts),
            ),
        )
        conn.execute(
            """
            INSERT INTO event_log(
                event_id,
                event_type,
                recorded_at,
                workspace_id,
                trace_ref,
                request_id,
                run_id,
                session_id,
                surface_kind,
                channel_kind,
                actor_kind,
                actor_id,
                status,
                severity,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_type,
                recorded_at,
                None,
                trace_ref,
                request_id,
                run_id,
                session_id,
                component,
                channel_id,
                _derive_actor_kind(component=component, actor_id=actor_id, provenance=normalized_provenance),
                actor_id,
                status,
                severity,
                json.dumps(
                    {
                        "summary": summary,
                        "truth_kind": truth_kind,
                        "target_surface": target_surface,
                        "component": component,
                        "evidence_lane": evidence_lane,
                        "reason_code": reason_code,
                        "provenance": normalized_provenance,
                        "facts": normalized_facts,
                    },
                    sort_keys=True,
                    ensure_ascii=True,
                    default=str,
                ),
            ),
        )
        _mirror_delivery_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            recorded_at=recorded_at,
            trace_ref=trace_ref,
            run_id=run_id,
            request_id=request_id,
            session_id=session_id,
            channel_id=channel_id,
            facts=normalized_facts,
        )
        _mirror_provenance_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            recorded_at=recorded_at,
            component=component,
            trace_ref=trace_ref,
            request_id=request_id,
            run_id=run_id,
            actor_id=actor_id,
            reason_code=reason_code,
            provenance=normalized_provenance,
            facts=normalized_facts,
        )
        _mirror_policy_gate_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            recorded_at=recorded_at,
            component=component,
            reason_code=reason_code,
            severity=severity,
            facts=normalized_facts,
            provenance=normalized_provenance,
        )
        _mirror_memory_lane_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            recorded_at=recorded_at,
            component=component,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            reason_code=reason_code,
            facts=normalized_facts,
            provenance=normalized_provenance,
        )
        conn.commit()
    _record_follow_on_policy_block_if_needed(
        state_db,
        event_id=event_id,
        event_type=event_type,
        component=component,
        request_id=request_id,
        trace_ref=trace_ref,
        run_id=run_id,
        channel_id=channel_id,
        session_id=session_id,
        actor_id=actor_id,
        reason_code=reason_code,
        severity=severity,
        provenance=normalized_provenance,
        facts=normalized_facts,
    )
    _record_follow_on_promotion_gate_block_if_needed(
        state_db,
        event_id=event_id,
        event_type=event_type,
        component=component,
        request_id=request_id,
        trace_ref=trace_ref,
        run_id=run_id,
        channel_id=channel_id,
        session_id=session_id,
        actor_id=actor_id,
        reason_code=reason_code,
        severity=severity,
        provenance=normalized_provenance,
        facts=normalized_facts,
    )
    return event_id


def record_policy_gate_block(
    state_db: StateDB,
    *,
    component: str,
    policy_domain: str,
    gate_name: str,
    source_kind: str,
    source_ref: str | None,
    summary: str,
    action: str,
    reason_code: str,
    blocked_stage: str | None = None,
    input_ref: str | None = None,
    output_ref: str | None = None,
    severity: str = "high",
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    provenance: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> str:
    event_id = record_event(
        state_db,
        event_type="policy_gate_blocked",
        component=component,
        summary=summary,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        actor_id=actor_id,
        reason_code=reason_code,
        severity=severity,
        status="blocked",
        facts={
            "policy_domain": policy_domain,
            "gate_name": gate_name,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "blocked_stage": blocked_stage,
            "input_ref": input_ref,
            "output_ref": output_ref,
            "action": action,
            **(facts or {}),
        },
        provenance=provenance,
    )
    return event_id


def record_config_mutation(
    state_db: StateDB,
    *,
    target_document: str,
    target_path: str,
    actor_id: str,
    actor_type: str,
    reason_code: str,
    request_source: str,
    before_payload: Any,
    after_payload: Any,
    status: str,
    rollback_payload: Any,
    error_message: str | None = None,
    summary: str | None = None,
) -> str:
    mutation_id = _prefixed_id("cfg")
    rollback_ref = f"rollback:{mutation_id}"
    before_hash = payload_hash(before_payload)
    after_hash = payload_hash(after_payload)
    before_summary = summarize_payload(before_payload)
    after_summary = summarize_payload(after_payload)
    semantic_diff = {
        "before": before_summary,
        "after": after_summary,
        "changed": before_hash != after_hash,
    }
    validation_verdict = "semantic_noop" if status == "rejected" and error_message == "semantic_noop" else status
    request_summary = summary or f"{target_document}:{target_path} requested"
    record_event(
        state_db,
        event_type="config_mutation_requested",
        component="config_manager",
        summary=request_summary,
        actor_id=actor_id,
        reason_code=reason_code,
        facts={
            "target_document": target_document,
            "target_path": target_path,
            "mutation_reason": reason_code,
            "source_surface": request_source,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "semantic_diff": semantic_diff,
            "validation_verdict": validation_verdict,
            "rollback_ref": rollback_ref,
        },
        provenance={"request_source": request_source, "actor_type": actor_type},
    )
    recorded_at = utc_now_iso()
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO config_mutation_audit(
                mutation_id,
                target_document,
                target_path,
                actor_id,
                actor_type,
                reason_code,
                request_source,
                status,
                rollback_ref,
                before_hash,
                after_hash,
                before_summary_json,
                after_summary_json,
                rollback_payload_json,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mutation_id,
                target_document,
                target_path,
                actor_id,
                actor_type,
                reason_code,
                request_source,
                status,
                rollback_ref,
                before_hash,
                after_hash,
                json.dumps(before_summary, sort_keys=True),
                json.dumps(after_summary, sort_keys=True),
                _json_or_none(rollback_payload),
                error_message,
            ),
        )
        conn.execute(
            """
            INSERT INTO config_mutation_log(
                mutation_id,
                recorded_at,
                actor_kind,
                actor_id,
                source_surface,
                target_path,
                before_hash,
                after_hash,
                semantic_diff_json,
                validation_verdict,
                rollback_ref,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mutation_id,
                recorded_at,
                actor_type,
                actor_id,
                request_source,
                target_path,
                before_hash,
                after_hash,
                json.dumps(semantic_diff, sort_keys=True),
                validation_verdict,
                rollback_ref,
                status,
            ),
        )
        conn.commit()
    event_type = "config_mutation_applied" if status == "applied" else "config_mutation_rejected"
    record_event(
        state_db,
        event_type=event_type,
        component="config_manager",
        summary=summary or f"{target_document}:{target_path} {status}",
        actor_id=actor_id,
        reason_code=reason_code,
        severity="high" if status == "rejected" else DEFAULT_SEVERITY,
        status=status,
        facts={
            "mutation_id": mutation_id,
            "target_document": target_document,
            "target_path": target_path,
            "rollback_ref": rollback_ref,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "semantic_diff": semantic_diff,
            "validation_verdict": validation_verdict,
            "error_message": error_message,
        },
        provenance={"request_source": request_source, "actor_type": actor_type},
    )
    return mutation_id


def record_environment_snapshot(
    state_db: StateDB,
    *,
    surface: str,
    summary: str,
    provider_id: str | None = None,
    provider_model: str | None = None,
    provider_base_url: str | None = None,
    provider_execution_transport: str | None = None,
    runtime_root: str | None = None,
    config_path: str | None = None,
    env_refs: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> EnvironmentSnapshotRecord:
    snapshot_id = _prefixed_id("env")
    merged_facts = {
        "surface": surface,
        "provider_id": provider_id,
        "provider_model": provider_model,
        "provider_base_url": provider_base_url,
        "provider_execution_transport": provider_execution_transport,
        "runtime_root": runtime_root,
        "config_path": config_path,
        **(facts or {}),
    }
    config_hash = payload_hash(merged_facts)
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_environment_snapshots(
                snapshot_id,
                surface,
                run_id,
                request_id,
                summary,
                provider_id,
                provider_model,
                provider_base_url,
                provider_execution_transport,
                runtime_root,
                config_path,
                python_executable,
                config_hash,
                env_refs_json,
                facts_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                surface,
                run_id,
                request_id,
                summary,
                provider_id,
                provider_model,
                provider_base_url,
                provider_execution_transport,
                runtime_root,
                config_path,
                sys.executable,
                config_hash,
                _json_or_none(env_refs),
                _json_or_none(merged_facts),
            ),
        )
        conn.commit()
    record_event(
        state_db,
        event_type="runtime_environment_snapshot",
        component=surface,
        summary=summary,
        run_id=run_id,
        request_id=request_id,
        reason_code="environment_snapshot",
        facts={"snapshot_id": snapshot_id, "config_hash": config_hash, **merged_facts},
        provenance={"env_refs": env_refs or {}},
    )
    return EnvironmentSnapshotRecord(snapshot_id=snapshot_id, surface=surface, summary=summary)


def record_quarantine(
    state_db: StateDB,
    *,
    source_kind: str,
    policy_domain: str,
    reason_code: str,
    summary: str,
    payload_preview: str,
    source_ref: str | None = None,
    provenance: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    event_id: str | None = None,
) -> str:
    quarantine_id = _prefixed_id("q")
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO quarantine_records(
                quarantine_id,
                event_id,
                run_id,
                request_id,
                source_kind,
                source_ref,
                policy_domain,
                reason_code,
                status,
                payload_hash,
                payload_preview,
                provenance_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'quarantined', ?, ?, ?)
            """,
            (
                quarantine_id,
                event_id,
                run_id,
                request_id,
                source_kind,
                source_ref,
                policy_domain,
                reason_code,
                payload_hash(payload_preview),
                payload_preview,
                _json_or_none(provenance),
            ),
        )
        conn.commit()
    record_event(
        state_db,
        event_type="quarantine_recorded",
        component=policy_domain,
        summary=summary,
        run_id=run_id,
        request_id=request_id,
        reason_code=reason_code,
        severity="high",
        facts={
            "quarantine_id": quarantine_id,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "policy_domain": policy_domain,
        },
        provenance=provenance,
    )
    return quarantine_id


def latest_events_by_type(state_db: StateDB, *, event_type: str, limit: int = 20) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM builder_events
            WHERE event_type = ?
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (event_type, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def events_for_run(state_db: StateDB, *, run_id: str) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM builder_events
            WHERE run_id = ?
            ORDER BY created_at ASC, event_id ASC
            """,
            (run_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def latest_snapshots_by_surface(state_db: StateDB) -> dict[str, dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM runtime_environment_snapshots
            ORDER BY created_at DESC, snapshot_id DESC
            """
        ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        surface = str(row["surface"])
        if surface in output:
            continue
        output[surface] = _row_to_dict(row)
    return output


def open_runs(state_db: StateDB) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM builder_runs
            WHERE status = 'open'
            ORDER BY opened_at ASC, run_id ASC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_config_mutations(state_db: StateDB, *, limit: int = 50) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM config_mutation_audit
            ORDER BY created_at DESC, mutation_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_quarantine_records(state_db: StateDB, *, limit: int = 50) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM quarantine_records
            ORDER BY created_at DESC, quarantine_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_delivery_records(
    state_db: StateDB,
    *,
    limit: int = 50,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM delivery_registry
    """
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY attempted_at DESC, delivery_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_provenance_mutations(
    state_db: StateDB,
    *,
    limit: int = 50,
    quarantined: bool | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM provenance_mutation_log
    """
    params: list[Any] = []
    if quarantined is not None:
        query += " WHERE quarantined = ?"
        params.append(1 if quarantined else 0)
    query += " ORDER BY recorded_at DESC, mutation_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_runs(
    state_db: StateDB,
    *,
    limit: int = 50,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM run_registry
    """
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY opened_at DESC, run_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_contradictions(
    state_db: StateDB,
    *,
    limit: int = 50,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM contradiction_records
    """
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY last_seen_at DESC, contradiction_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_policy_gate_records(
    state_db: StateDB,
    *,
    limit: int = 50,
    gate_name: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM policy_gate_records
    """
    params: list[Any] = []
    if gate_name:
        query += " WHERE gate_name = ?"
        params.append(gate_name)
    query += " ORDER BY recorded_at DESC, policy_gate_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_memory_lane_records(
    state_db: StateDB,
    *,
    limit: int = 50,
    artifact_lane: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM memory_lane_records
    """
    params: list[Any] = []
    if artifact_lane:
        query += " WHERE artifact_lane = ?"
        params.append(artifact_lane)
    query += " ORDER BY recorded_at DESC, lane_record_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def memory_lane_records_for_event_ids(state_db: StateDB, *, event_ids: list[str]) -> list[dict[str, Any]]:
    normalized_ids = [str(event_id) for event_id in event_ids if str(event_id or "").strip()]
    if not normalized_ids:
        return []
    placeholders = ", ".join("?" for _ in normalized_ids)
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM memory_lane_records
            WHERE event_id IN ({placeholders})
            """,
            tuple(normalized_ids),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_reset_sensitive_state_registry(
    state_db: StateDB,
    *,
    limit: int = 50,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM reset_sensitive_state_registry
    """
    params: list[Any] = []
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY updated_at DESC, registry_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_resume_richness_guard_records(
    state_db: StateDB,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM resume_richness_guard_records
            ORDER BY created_at DESC, guard_record_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_pending_task_records(
    state_db: StateDB,
    *,
    limit: int = 50,
    open_only: bool = True,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM pending_task_records
    """
    params: list[Any] = []
    if open_only:
        query += " WHERE status IN ('open', 'pending', 'blocked', 'timed_out', 'interrupted') AND closed_at IS NULL"
    query += " ORDER BY updated_at DESC, pending_task_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_procedural_lesson_records(
    state_db: StateDB,
    *,
    limit: int = 50,
    lesson_kind: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM procedural_lesson_records
    """
    params: list[Any] = []
    clauses: list[str] = []
    if lesson_kind:
        clauses.append("lesson_kind = ?")
        params.append(lesson_kind)
    if active_only:
        clauses.append("status IN ('active', 'candidate', 'confirmed') AND retired_at IS NULL")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC, lesson_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_personality_trait_profiles(
    state_db: StateDB,
    *,
    limit: int = 50,
    human_id: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM personality_trait_profiles
    """
    params: list[Any] = []
    if human_id:
        query += " WHERE human_id = ?"
        params.append(human_id)
    query += " ORDER BY updated_at DESC, created_at DESC, human_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_personality_observations(
    state_db: StateDB,
    *,
    limit: int = 50,
    human_id: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM personality_observations
    """
    params: list[Any] = []
    if human_id:
        query += " WHERE human_id = ?"
        params.append(human_id)
    query += " ORDER BY observed_at DESC, created_at DESC, observation_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def recent_personality_evolution_events(
    state_db: StateDB,
    *,
    limit: int = 50,
    human_id: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM personality_evolution_events
    """
    params: list[Any] = []
    if human_id:
        query += " WHERE human_id = ?"
        params.append(human_id)
    query += " ORDER BY evolved_at DESC, created_at DESC, evolution_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def personality_typed_human_sets(state_db: StateDB) -> dict[str, set[str]]:
    with state_db.connect() as conn:
        profile_rows = conn.execute(
            "SELECT DISTINCT human_id FROM personality_trait_profiles"
        ).fetchall()
        observation_rows = conn.execute(
            "SELECT DISTINCT human_id FROM personality_observations"
        ).fetchall()
        evolution_rows = conn.execute(
            "SELECT DISTINCT human_id FROM personality_evolution_events"
        ).fetchall()
    return {
        "trait_profiles": {
            str(row["human_id"] or "") for row in profile_rows if str(row["human_id"] or "")
        },
        "observations": {
            str(row["human_id"] or "") for row in observation_rows if str(row["human_id"] or "")
        },
        "evolution_events": {
            str(row["human_id"] or "") for row in evolution_rows if str(row["human_id"] or "")
        },
    }


def recent_observer_packet_records(
    state_db: StateDB,
    *,
    limit: int = 50,
    packet_kind: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM observer_packet_records
    """
    clauses: list[str] = []
    params: list[Any] = []
    if active_only:
        clauses.append("active = 1")
    if packet_kind:
        clauses.append("packet_kind = ?")
        params.append(packet_kind)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY last_seen_at DESC, created_at DESC, packet_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    payload = [_row_to_dict(row) for row in rows]
    for row in payload:
        row["active"] = bool(row.get("active"))
    return payload


def recent_observer_handoff_records(
    state_db: StateDB,
    *,
    limit: int = 20,
    chip_key: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM observer_handoff_records
    """
    clauses: list[str] = []
    params: list[Any] = []
    if chip_key:
        clauses.append("chip_key = ?")
        params.append(chip_key)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, handoff_id DESC LIMIT ?"
    params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    payload = [_row_to_dict(row) for row in rows]
    for row in payload:
        row["active_only"] = bool(row.get("active_only"))
    return payload


def record_observer_handoff_record(
    state_db: StateDB,
    *,
    handoff_id: str,
    chip_key: str,
    hook: str,
    run_id: str | None,
    request_id: str | None,
    bundle_path: str,
    result_path: str | None,
    packet_count: int,
    packet_kind_filter: str | None,
    active_only: bool,
    status: str,
    summary: str,
    exit_code: int | None = None,
    error_text: str | None = None,
    payload: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    created_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    handoff_created_at = str(created_at or utc_now_iso())
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO observer_handoff_records(
                handoff_id,
                chip_key,
                hook,
                run_id,
                request_id,
                bundle_path,
                result_path,
                packet_count,
                packet_kind_filter,
                active_only,
                status,
                exit_code,
                summary,
                error_text,
                payload_json,
                output_json,
                created_at,
                completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                chip_key,
                hook,
                run_id,
                request_id,
                bundle_path,
                result_path,
                int(packet_count),
                packet_kind_filter,
                1 if active_only else 0,
                status,
                exit_code,
                summary,
                error_text,
                json.dumps(payload, ensure_ascii=True, sort_keys=True) if payload is not None else None,
                json.dumps(output, ensure_ascii=True, sort_keys=True) if output is not None else None,
                handoff_created_at,
                completed_at,
            ),
        )
        conn.commit()


def build_watchtower_snapshot(
    state_db: StateDB,
    *,
    background_stale_seconds: int = WATCHTOWER_BACKGROUND_STALE_SECONDS,
) -> dict[str, Any]:
    health_facts = _compute_health_facts(state_db)
    execution_panel = _build_execution_lineage_panel(state_db)
    delivery_panel = _build_delivery_truth_panel(state_db, health_facts=health_facts)
    background_panel = _build_background_freshness_panel(
        state_db,
        health_facts=health_facts,
        background_stale_seconds=background_stale_seconds,
    )
    environment_panel = _build_environment_parity_panel(state_db)
    dimensions = {
        "ingress_health": _build_ingress_health_dimension(health_facts=health_facts),
        "execution_health": _build_execution_health_dimension(execution_panel=execution_panel),
        "delivery_health": _build_delivery_health_dimension(
            delivery_panel=delivery_panel,
            health_facts=health_facts,
        ),
        "scheduler_freshness": _build_scheduler_freshness_dimension(background_panel=background_panel),
        "environment_parity": _build_environment_parity_dimension(environment_panel=environment_panel),
    }
    return {
        "health_facts": health_facts,
        "health_dimensions": dimensions,
        "top_level_state": _derive_watchtower_top_level_state(dimensions),
        "contradictions": _safe_watchtower_panel(
            "contradictions",
            lambda: _build_contradiction_panel(state_db),
        ),
        "panels": {
            "config_authority": _safe_watchtower_panel(
                "config_authority",
                lambda: _build_config_authority_panel(state_db),
            ),
            "execution_lineage": execution_panel,
            "delivery_truth": delivery_panel,
            "background_freshness": background_panel,
            "environment_parity": environment_panel,
            "provenance_and_quarantine": _safe_watchtower_panel(
                "provenance_and_quarantine",
                lambda: _build_provenance_and_quarantine_panel(state_db),
            ),
            "memory_lane_hygiene": _safe_watchtower_panel(
                "memory_lane_hygiene",
                lambda: _build_memory_lane_hygiene_panel(state_db),
            ),
            "agent_identity": _safe_watchtower_panel(
                "agent_identity",
                lambda: _build_agent_identity_panel(state_db),
            ),
            "personality": _safe_watchtower_panel(
                "personality",
                lambda: _build_personality_panel(state_db),
            ),
            "session_integrity": _safe_watchtower_panel(
                "session_integrity",
                lambda: _build_session_integrity_panel(state_db),
            ),
            "procedural_memory": _safe_watchtower_panel(
                "procedural_memory",
                lambda: _build_procedural_memory_panel(state_db),
            ),
            "observer_incidents": _safe_watchtower_panel(
                "observer_incidents",
                lambda: _build_observer_incident_panel(state_db),
            ),
            "observer_packets": _safe_watchtower_panel(
                "observer_packets",
                lambda: _build_observer_packet_panel(state_db),
            ),
            "observer_handoffs": _safe_watchtower_panel(
                "observer_handoffs",
                lambda: _build_observer_handoff_panel(state_db),
            ),
            "memory_shadow": _safe_watchtower_panel(
                "memory_shadow",
                lambda: _build_memory_shadow_panel(state_db),
            ),
            "memory_doctor_brain": _safe_watchtower_panel(
                "memory_doctor_brain",
                lambda: _build_memory_doctor_brain_panel(state_db),
            ),
        },
    }


def _safe_watchtower_panel(panel_name: str, factory: Any) -> dict[str, Any]:
    try:
        return factory()
    except sqlite3.Error as exc:
        return {
            "status": "degraded",
            "panel": panel_name,
            "error": f"SQLite error while building watchtower panel: {exc}",
        }


def summarize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {
            "type": "dict",
            "key_count": len(payload),
            "keys": sorted(str(key) for key in payload.keys())[:20],
            "hash": payload_hash(payload),
        }
    if isinstance(payload, list):
        return {
            "type": "list",
            "length": len(payload),
            "hash": payload_hash(payload),
        }
    if isinstance(payload, str):
        return {
            "type": "str",
            "length": len(payload),
            "preview": payload[:80],
            "hash": payload_hash(payload),
        }
    return {
        "type": type(payload).__name__,
        "value": payload,
        "hash": payload_hash(payload),
    }


def payload_hash(payload: Any) -> str:
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_text_mutation_facts(
    *,
    raw_text: str | None,
    mutated_text: str | None,
    mutation_actions: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    actions = [str(item) for item in list(mutation_actions or []) if str(item)]
    text_mutated = bool(actions) or (
        raw_text is not None and mutated_text is not None and str(raw_text) != str(mutated_text)
    )
    facts: dict[str, Any] = {
        "text_mutated": text_mutated,
    }
    if raw_text is not None:
        facts["raw_text_ref"] = f"text:sha256:{payload_hash(str(raw_text))}"
        facts["raw_text_length"] = len(str(raw_text))
    if mutated_text is not None:
        facts["mutated_text_ref"] = f"text:sha256:{payload_hash(str(mutated_text))}"
        facts["mutated_text_length"] = len(str(mutated_text))
    if actions:
        facts["mutation_actions"] = actions
    return facts


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def record_contradiction(
    state_db: StateDB,
    *,
    contradiction_key: str,
    component: str,
    reason_code: str,
    summary: str,
    detail: str | None,
    severity: str,
    facts: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> str:
    now = utc_now_iso()
    contradiction_id: str
    occurrence_count = 1
    with state_db.connect() as conn:
        existing = conn.execute(
            """
            SELECT contradiction_id, occurrence_count
            FROM contradiction_records
            WHERE contradiction_key = ?
            LIMIT 1
            """,
            (contradiction_key,),
        ).fetchone()
        if existing:
            contradiction_id = str(existing["contradiction_id"])
            occurrence_count = int(existing["occurrence_count"] or 0) + 1
            conn.execute(
                """
                UPDATE contradiction_records
                SET
                    component = ?,
                    reason_code = ?,
                    status = 'open',
                    severity = ?,
                    summary = ?,
                    detail = ?,
                    last_seen_at = ?,
                    resolved_at = NULL,
                    occurrence_count = ?,
                    evidence_json = ?,
                    facts_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE contradiction_key = ?
                """,
                (
                    component,
                    reason_code,
                    severity,
                    summary,
                    detail,
                    now,
                    occurrence_count,
                    _json_or_none(provenance),
                    _json_or_none(facts),
                    contradiction_key,
                ),
            )
        else:
            contradiction_id = _prefixed_id("ctr")
            conn.execute(
                """
                INSERT INTO contradiction_records(
                    contradiction_id,
                    contradiction_key,
                    component,
                    reason_code,
                    status,
                    severity,
                    summary,
                    detail,
                    first_seen_at,
                    last_seen_at,
                    occurrence_count,
                    evidence_json,
                    facts_json
                )
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contradiction_id,
                    contradiction_key,
                    component,
                    reason_code,
                    severity,
                    summary,
                    detail,
                    now,
                    now,
                    occurrence_count,
                    _json_or_none(provenance),
                    _json_or_none(facts),
                ),
            )
        conn.commit()
    event_id = record_event(
        state_db,
        event_type="contradiction_recorded",
        component=component,
        summary=summary,
        reason_code=reason_code,
        severity=severity,
        status="open",
        facts={
            "contradiction_id": contradiction_id,
            "contradiction_key": contradiction_key,
            "detail": detail,
            "occurrence_count": occurrence_count,
            **(facts or {}),
        },
        provenance=provenance,
    )
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE contradiction_records
            SET
                first_event_id = COALESCE(first_event_id, ?),
                last_event_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE contradiction_key = ?
            """,
            (event_id, event_id, contradiction_key),
        )
        conn.commit()
    return contradiction_id


def resolve_contradiction(
    state_db: StateDB,
    *,
    contradiction_key: str,
    component: str,
    reason_code: str,
    summary: str,
    detail: str | None = None,
    facts: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> bool:
    now = utc_now_iso()
    contradiction_id: str | None = None
    with state_db.connect() as conn:
        existing = conn.execute(
            """
            SELECT contradiction_id
            FROM contradiction_records
            WHERE contradiction_key = ? AND status = 'open'
            LIMIT 1
            """,
            (contradiction_key,),
        ).fetchone()
        if not existing:
            return False
        contradiction_id = str(existing["contradiction_id"])
        conn.execute(
            """
            UPDATE contradiction_records
            SET
                status = 'resolved',
                summary = ?,
                detail = COALESCE(?, detail),
                last_seen_at = ?,
                resolved_at = ?,
                evidence_json = COALESCE(?, evidence_json),
                facts_json = COALESCE(?, facts_json),
                updated_at = CURRENT_TIMESTAMP
            WHERE contradiction_key = ?
            """,
            (
                summary,
                detail,
                now,
                now,
                _json_or_none(provenance),
                _json_or_none(facts),
                contradiction_key,
            ),
        )
        conn.commit()
    event_id = record_event(
        state_db,
        event_type="contradiction_recorded",
        component=component,
        summary=summary,
        reason_code=reason_code,
        severity="low",
        status="resolved",
        facts={
            "contradiction_id": contradiction_id,
            "contradiction_key": contradiction_key,
            "detail": detail,
            **(facts or {}),
        },
        provenance=provenance,
    )
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE contradiction_records
            SET last_event_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE contradiction_key = ?
            """,
            (event_id, contradiction_key),
        )
        conn.commit()
    return True


def _json_or_none(payload: Any) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _row_to_dict(row: Any) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "deltas_json",
        "traits_json",
        "state_weights_json",
        "provenance_json",
        "evidence_json",
        "facts_json",
        "related_event_ids_json",
        "related_packet_ids_json",
        "contradiction_ids_json",
        "content_json",
        "env_refs_json",
        "details_json",
        "before_summary_json",
        "after_summary_json",
        "rollback_payload_json",
        "payload_json",
        "output_json",
        "semantic_diff_json",
    ):
        value = payload.get(key)
        if not value:
            continue
        try:
            payload[key] = json.loads(value)
        except json.JSONDecodeError:
            payload[key] = value
    return payload


def _run_status_event_type(status: str) -> str:
    mapping = {
        "closed": "run_closed",
        "failed": "run_failed",
        "overlap_skipped": "run_overlap_skipped",
    }
    return mapping.get(status, "run_stalled")


def _derive_actor_kind(*, component: str, actor_id: str | None, provenance: dict[str, Any]) -> str:
    source_kind = str(provenance.get("source_kind") or "")
    lowered_actor = str(actor_id or "").lower()
    lowered_component = component.lower()
    if source_kind.startswith("personality_") or source_kind == "attachment_snapshot":
        return "plugin"
    if source_kind == "chip_hook":
        return "chip"
    if lowered_component in {"telegram_runtime", "discord_webhook", "whatsapp_webhook"}:
        return "gateway_adapter"
    if lowered_component in {"researcher_bridge", "swarm_bridge"}:
        return "researcher_bridge"
    if lowered_component == "config_manager" and lowered_actor == "local-operator":
        return "operator"
    if lowered_actor == "local-operator" or lowered_actor.startswith("operator"):
        return "operator"
    if lowered_actor in {"jobs_tick", "job_runner"}:
        return "job_runner"
    return "builder_core"


def _mirror_delivery_event(
    conn: Any,
    *,
    event_id: str,
    event_type: str,
    recorded_at: str,
    trace_ref: str | None,
    run_id: str | None,
    request_id: str | None,
    session_id: str | None,
    channel_id: str | None,
    facts: dict[str, Any],
) -> None:
    if event_type not in {"delivery_attempted", "delivery_succeeded", "delivery_failed"}:
        return
    channel_kind = channel_id or str(facts.get("channel_kind") or "")
    target_ref = (
        facts.get("delivery_target")
        or facts.get("target_ref")
        or facts.get("chat_id")
        or facts.get("telegram_user_id")
        or facts.get("discord_user_id")
        or facts.get("whatsapp_user_id")
    )
    message_ref = (
        facts.get("message_ref")
        or facts.get("ack_ref")
        or (f"{channel_kind}:{facts['update_id']}" if facts.get("update_id") is not None else None)
        or request_id
        or trace_ref
        or event_id
    )
    failure_family = (
        facts.get("failure_family")
        or _delivery_failure_family(facts.get("delivery_error"))
    )
    if event_type == "delivery_attempted":
        delivery_id = str(facts.get("delivery_id") or _prefixed_id("del"))
        conn.execute(
            """
            INSERT INTO delivery_registry(
                delivery_id,
                trace_ref,
                run_id,
                session_id,
                channel_kind,
                target_ref,
                message_ref,
                attempted_at,
                acked_at,
                status,
                failure_family
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery_id,
                trace_ref,
                run_id,
                session_id,
                channel_kind,
                str(target_ref) if target_ref is not None else None,
                str(message_ref) if message_ref is not None else None,
                recorded_at,
                None,
                "attempted",
                str(failure_family) if failure_family else None,
            ),
        )
        return

    row = conn.execute(
        """
        SELECT delivery_id
        FROM delivery_registry
        WHERE status = 'attempted'
          AND (? IS NULL OR run_id = ?)
          AND (? IS NULL OR session_id = ?)
          AND (? IS NULL OR trace_ref = ?)
          AND (? IS NULL OR channel_kind = ?)
        ORDER BY attempted_at DESC, delivery_id DESC
        LIMIT 1
        """,
        (
            run_id,
            run_id,
            session_id,
            session_id,
            trace_ref,
            trace_ref,
            channel_kind,
            channel_kind,
        ),
    ).fetchone()
    delivery_id = str(row["delivery_id"]) if row else _prefixed_id("del")
    if not row:
        conn.execute(
            """
            INSERT INTO delivery_registry(
                delivery_id,
                trace_ref,
                run_id,
                session_id,
                channel_kind,
                target_ref,
                message_ref,
                attempted_at,
                acked_at,
                status,
                failure_family
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'attempted', ?)
            """,
            (
                delivery_id,
                trace_ref,
                run_id,
                session_id,
                channel_kind,
                str(target_ref) if target_ref is not None else None,
                str(message_ref) if message_ref is not None else None,
                recorded_at,
                None,
                str(failure_family) if failure_family else None,
            ),
        )
    conn.execute(
        """
        UPDATE delivery_registry
        SET
            acked_at = ?,
            status = ?,
            failure_family = ?
        WHERE delivery_id = ?
        """,
        (
            recorded_at if event_type == "delivery_succeeded" else None,
            "succeeded" if event_type == "delivery_succeeded" else "failed",
            str(failure_family) if failure_family else None,
            delivery_id,
        ),
    )


def _mirror_provenance_event(
    conn: Any,
    *,
    event_id: str,
    event_type: str,
    recorded_at: str,
    component: str,
    trace_ref: str | None,
    request_id: str | None,
    run_id: str | None,
    actor_id: str | None,
    reason_code: str | None,
    provenance: dict[str, Any],
    facts: dict[str, Any],
) -> None:
    if event_type != "plugin_or_chip_influence_recorded":
        return
    source_kind = str(provenance.get("source_kind") or "unknown")
    source_id = str(provenance.get("source_ref") or actor_id or "unknown")
    trust_level = str(facts.get("trust_level") or _trust_level_from_keepability(facts.get("keepability")))
    quarantined = 1 if facts.get("quarantined") else 0
    if source_kind == "unknown" or source_id == "unknown":
        quarantined = 1
    mutation_id = f"prv-{event_id}"
    conn.execute(
        """
        INSERT INTO provenance_mutation_log(
            mutation_id,
            recorded_at,
            surface,
            mutation_type,
            source_kind,
            source_id,
            trace_ref,
            input_ref,
            output_ref,
            trust_level,
            quarantined,
            reason_code
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mutation_id,
            recorded_at,
            component,
            str(facts.get("mutation_type") or reason_code or event_type),
            source_kind,
            source_id,
            trace_ref,
            str(facts.get("input_ref") or request_id) if (facts.get("input_ref") or request_id) is not None else None,
            str(facts.get("output_ref") or run_id or trace_ref) if (facts.get("output_ref") or run_id or trace_ref) is not None else None,
            trust_level,
            quarantined,
            reason_code,
        ),
    )
    if not quarantined:
        return
    payload_preview = json.dumps(
        {
            "surface": component,
            "source_kind": source_kind,
            "source_id": source_id,
            "trust_level": trust_level,
            "reason_code": reason_code,
        },
        sort_keys=True,
        ensure_ascii=True,
    )[:160]
    conn.execute(
        """
        INSERT INTO quarantine_records(
            quarantine_id,
            event_id,
            run_id,
            request_id,
            source_kind,
            source_ref,
            policy_domain,
            reason_code,
            status,
            payload_hash,
            payload_preview,
            provenance_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'quarantined', ?, ?, ?)
        """,
        (
            f"q-{mutation_id}",
            event_id,
            run_id,
            request_id,
            "provenance_mutation",
            source_id,
            "provenance_mutation",
            reason_code or "unlabeled_provenance",
            payload_hash(payload_preview),
            payload_preview,
            _json_or_none(
                {
                    "surface": component,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "trust_level": trust_level,
                }
            ),
        ),
    )


def _mirror_policy_gate_event(
    conn: Any,
    *,
    event_id: str,
    event_type: str,
    recorded_at: str,
    component: str,
    reason_code: str | None,
    severity: str,
    facts: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    if event_type != "policy_gate_blocked":
        return
    policy_gate_id = f"pol-{event_id}"
    conn.execute(
        """
        INSERT INTO policy_gate_records(
            policy_gate_id,
            recorded_at,
            event_id,
            component,
            policy_domain,
            gate_name,
            blocked_stage,
            source_kind,
            source_ref,
            input_ref,
            output_ref,
            action,
            reason_code,
            severity,
            evidence_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy_gate_id,
            recorded_at,
            event_id,
            component,
            str(facts.get("policy_domain") or component),
            str(facts.get("gate_name") or reason_code or "policy_gate"),
            str(facts.get("blocked_stage") or "") or None,
            str(facts.get("source_kind") or "unknown"),
            str(facts.get("source_ref")) if facts.get("source_ref") is not None else None,
            str(facts.get("input_ref")) if facts.get("input_ref") is not None else None,
            str(facts.get("output_ref")) if facts.get("output_ref") is not None else None,
            str(facts.get("action") or "blocked"),
            reason_code,
            severity,
            _json_or_none({"provenance": provenance, "facts": facts}),
        ),
    )


def _memory_trace_message_text(facts: dict[str, Any]) -> str | None:
    for key in (
        "message_text",
        "evidence_text",
        "query_text",
        "user_message_preview",
        "episode_text",
        "text",
    ):
        value = facts.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for collection_key in ("observations", "events", "records"):
        collection = facts.get(collection_key)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            for key in ("message_text", "evidence_text", "query_text", "episode_text", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _memory_trace_salience_present(facts: dict[str, Any]) -> bool:
    if any(
        facts.get(key) not in (None, "", [])
        for key in ("salience_score", "why_saved", "salience_reasons", "promotion_stage", "retention_class")
    ):
        return True
    for collection_key in ("observations", "events", "records"):
        collection = facts.get(collection_key)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict) and any(
                item.get(key) not in (None, "", [])
                for key in ("salience_score", "why_saved", "salience_reasons", "promotion_stage", "retention_class")
            ):
                return True
    return False


def _memory_trace_flat_fields(facts: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in (
        "message_text",
        "evidence_text",
        "query_text",
        "episode_text",
        "salience_score",
        "why_saved",
        "salience_reasons",
        "promotion_stage",
        "retention_class",
        "memory_role",
    ):
        value = facts.get(key)
        if value not in (None, "", []):
            fields[key] = value
    message_text = _memory_trace_message_text(facts)
    if message_text and "message_text" not in fields:
        fields["message_text"] = message_text
    return fields


def _memory_trace_contract(
    *,
    facts: dict[str, Any],
    request_id: str | None,
    trace_ref: str | None,
    keepability: str,
    promotion_disposition: str,
    artifact_lane: str | None = None,
    promotion_target_lane: str | None = None,
) -> dict[str, Any]:
    message_text = _memory_trace_message_text(facts)
    salience_present = _memory_trace_salience_present(facts)
    memory_role = str(facts.get("memory_role") or "")
    required = bool(
        salience_present
        or message_text
        or memory_role in {"current_state", "entity_state", "structured_evidence", "belief", "raw_episode", "episodic"}
        or promotion_disposition.startswith("promote_")
        or promotion_disposition in {"capture_raw_episode", "blocked"}
        or keepability in {"durable_user_memory", "supporting_memory", "episodic_trace", "not_keepable"}
    )
    destination = promotion_target_lane or artifact_lane or _promotion_target_lane(promotion_disposition)
    return {
        "version": "memory_trace_v1",
        "trace_kind": "memory_decision" if required else "ops_trace",
        "trace_completeness_required": required,
        "has_request_id": bool(request_id),
        "has_trace_ref": bool(trace_ref),
        "has_message_text": bool(message_text),
        "has_salience": salience_present,
        "message_text": message_text,
        "decision": promotion_disposition or None,
        "destination": destination,
        "answer_ref": facts.get("answer_ref") or facts.get("reply_ref") or facts.get("explained_request_id"),
    }


def _mirror_memory_lane_event(
    conn: Any,
    *,
    event_id: str,
    event_type: str,
    recorded_at: str,
    component: str,
    run_id: str | None,
    request_id: str | None,
    trace_ref: str | None,
    reason_code: str | None,
    facts: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    if event_type == "policy_gate_blocked":
        return
    keepability = str(facts.get("keepability") or "")
    promotion_disposition = _normalized_promotion_disposition(
        facts.get("promotion_disposition"),
        keepability,
    )
    if not keepability and not promotion_disposition:
        return
    artifact_lane = _artifact_lane_from_keepability(keepability)
    promotion_target_lane = _promotion_target_lane(promotion_disposition)
    status = _promotion_record_status(promotion_disposition)
    trace_contract = _memory_trace_contract(
        facts=facts,
        request_id=request_id,
        trace_ref=trace_ref,
        keepability=keepability,
        promotion_disposition=promotion_disposition,
        artifact_lane=artifact_lane,
        promotion_target_lane=promotion_target_lane,
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO memory_lane_records(
            lane_record_id,
            recorded_at,
            event_id,
            component,
            artifact_kind,
            source_kind,
            source_ref,
            run_id,
            request_id,
            trace_ref,
            artifact_lane,
            promotion_target_lane,
            keepability,
            promotion_disposition,
            status,
            reason_code,
            evidence_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"mln:{event_id}",
            recorded_at,
            event_id,
            component,
            _artifact_kind_for_memory_lane(component=component, event_type=event_type),
            str(provenance.get("source_kind") or component or "classified_artifact"),
            str(
                provenance.get("source_ref")
                or facts.get("source_ref")
                or request_id
                or trace_ref
                or run_id
                or ""
            )
            or None,
            run_id,
            request_id,
            trace_ref,
            artifact_lane,
            promotion_target_lane,
            keepability or None,
            promotion_disposition or None,
            status,
            reason_code,
            _json_or_none(
                {
                    "component": component,
                    "event_type": event_type,
                    "facts": {**facts, "trace_contract": trace_contract},
                    "provenance": provenance,
                    "artifact_lane": artifact_lane,
                    "promotion_target_lane": promotion_target_lane,
                    "keepability": keepability or None,
                    "promotion_disposition": promotion_disposition or None,
                    "status": status,
                    "trace_contract": trace_contract,
                }
            ),
        ),
    )


def _record_follow_on_policy_block_if_needed(
    state_db: StateDB,
    *,
    event_id: str,
    event_type: str,
    component: str,
    request_id: str | None,
    trace_ref: str | None,
    run_id: str | None,
    channel_id: str | None,
    session_id: str | None,
    actor_id: str | None,
    reason_code: str | None,
    severity: str,
    provenance: dict[str, Any],
    facts: dict[str, Any],
) -> None:
    if event_type != "plugin_or_chip_influence_recorded":
        return
    source_kind = str(provenance.get("source_kind") or "unknown")
    source_ref = str(provenance.get("source_ref") or actor_id or "unknown")
    if source_kind != "unknown" and source_ref != "unknown":
        return
    quarantine_id = f"q-prv-{event_id}"
    record_policy_gate_block(
        state_db,
        component="provenance_mutation",
        policy_domain="provenance_mutation",
        gate_name="provenance_missing",
        source_kind="provenance_mutation",
        source_ref=source_ref,
        summary="Unlabeled provenance mutation was quarantined by policy.",
        action="quarantine_blocked",
        reason_code=reason_code or "unlabeled_provenance",
        blocked_stage="provenance_ingest",
        input_ref=str(facts.get("input_ref") or request_id) if (facts.get("input_ref") or request_id) is not None else None,
        output_ref=quarantine_id,
        severity="high" if severity == "critical" else severity,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        actor_id=actor_id,
        provenance={
            "source_kind": source_kind,
            "source_ref": source_ref,
            "origin_component": component,
        },
        facts={"source_event_id": event_id},
    )


def _record_follow_on_promotion_gate_block_if_needed(
    state_db: StateDB,
    *,
    event_id: str,
    event_type: str,
    component: str,
    request_id: str | None,
    trace_ref: str | None,
    run_id: str | None,
    channel_id: str | None,
    session_id: str | None,
    actor_id: str | None,
    reason_code: str | None,
    severity: str,
    provenance: dict[str, Any],
    facts: dict[str, Any],
) -> None:
    if event_type == "policy_gate_blocked":
        return
    keepability = str(facts.get("keepability") or "")
    promotion_disposition = _normalized_promotion_disposition(
        facts.get("promotion_disposition"),
        keepability,
    )
    if not promotion_disposition or promotion_disposition in NON_PROMOTABLE_DISPOSITIONS:
        return

    source_kind = str(provenance.get("source_kind") or facts.get("source_kind") or "unknown")
    source_ref = str(
        provenance.get("source_ref")
        or facts.get("source_ref")
        or request_id
        or trace_ref
        or run_id
        or "unknown"
    )
    artifact_lane = _artifact_lane_from_keepability(keepability)
    open_contradictions = recent_contradictions(state_db, limit=25, status="open")
    gate_records: list[tuple[str, str, str]] = []

    if source_kind == "unknown" or source_ref == "unknown":
        gate_records.append(
            (
                "provenance_check",
                "Promotion candidate was blocked because provenance is incomplete.",
                "promotion_provenance_missing",
            )
        )
    if keepability in NON_PROMOTABLE_KEEPABILITY:
        gate_records.append(
            (
                "keepability_check",
                "Promotion candidate was blocked because its keepability is non-promotable.",
                "promotion_keepability_blocked",
            )
        )
    if artifact_lane in {"ops_transcripts", "execution_evidence", "working_scratchpad"}:
        gate_records.append(
            (
                "residue_check",
                "Promotion candidate was blocked because it still lives in an ops or execution lane.",
                "promotion_residue_blocked",
            )
        )
    if open_contradictions:
        gate_records.append(
            (
                "contradiction_check",
                "Promotion candidate was blocked because open contradictions are still active.",
                "promotion_contradiction_blocked",
            )
        )

    for gate_name, summary, gate_reason in gate_records:
        record_policy_gate_block(
            state_db,
            component=component,
            policy_domain="memory_promotion",
            gate_name=gate_name,
            source_kind=source_kind,
            source_ref=source_ref,
            summary=summary,
            action="promotion_blocked",
            reason_code=gate_reason,
            blocked_stage="promotion",
            input_ref=event_id,
            output_ref=_promotion_target_lane(promotion_disposition),
            severity="high" if severity == "critical" else severity,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            actor_id=actor_id,
            provenance={
                "source_kind": source_kind,
                "source_ref": source_ref,
                "origin_component": component,
            },
            facts={
                "source_event_id": event_id,
                "event_type": event_type,
                "keepability": keepability or None,
                "promotion_disposition": promotion_disposition,
                "artifact_lane": artifact_lane,
                "open_contradiction_count": len(open_contradictions),
                "reason_code": reason_code,
                **_memory_trace_flat_fields(facts),
                "trace_contract": _memory_trace_contract(
                    facts=facts,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    keepability=keepability,
                    promotion_disposition=promotion_disposition,
                    artifact_lane=artifact_lane,
                    promotion_target_lane=_promotion_target_lane(promotion_disposition),
                ),
            },
        )


def _delivery_failure_family(error_value: Any) -> str | None:
    if error_value in {None, ""}:
        return None
    message = str(error_value).lower()
    if message.startswith("http "):
        return "http_error"
    if "timeout" in message:
        return "timeout"
    if "unauthorized" in message or "forbidden" in message or "auth" in message:
        return "auth_error"
    if "network" in message or "connection" in message:
        return "network_error"
    return "runtime_error"


def _normalized_promotion_disposition(value: Any, keepability: str) -> str:
    disposition = str(value or "").strip()
    if disposition:
        return disposition
    if keepability in NON_PROMOTABLE_KEEPABILITY:
        return "not_promotable"
    return ""


def repair_non_promotable_chip_hook_dispositions(state_db: StateDB) -> int:
    with state_db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE builder_events
            SET facts_json = json_set(
                COALESCE(facts_json, '{}'),
                '$.promotion_disposition',
                'not_promotable'
            )
            WHERE component = 'researcher_bridge'
              AND event_type IN ('tool_result_received', 'dispatch_failed')
              AND json_extract(provenance_json, '$.source_kind') = 'chip_hook'
              AND json_extract(facts_json, '$.keepability') IN (
                    'ephemeral_context',
                    'user_preference_ephemeral',
                    'operator_debug_only'
              )
              AND json_extract(facts_json, '$.promotion_disposition') IS NULL
            """
        )
        conn.commit()
    return int(cursor.rowcount or 0)


def repair_foreground_browser_hook_failures(state_db: StateDB) -> int:
    params = (
        "failed",
        "operator:browser_hook:%",
        "stalled",
        "browser_hook_failed",
        "browser_hook_invalid",
        "no_active_chip_for_hook",
        "secret_boundary_blocked",
    )
    with state_db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE builder_runs
            SET status = ?
            WHERE run_kind LIKE ?
              AND status = ?
              AND close_reason IN (?, ?, ?, ?)
            """,
            params,
        )
        conn.execute(
            """
            UPDATE run_registry
            SET status = ?
            WHERE run_kind LIKE ?
              AND status = ?
              AND closure_reason IN (?, ?, ?, ?)
            """,
            params,
        )
        conn.commit()
    return int(cursor.rowcount or 0)


def repair_missing_memory_lane_records(state_db: StateDB, *, limit: int = 1000) -> int:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                be.event_id,
                be.event_type,
                be.created_at,
                be.component,
                be.run_id,
                be.request_id,
                be.trace_ref,
                be.reason_code,
                be.provenance_json,
                be.facts_json
            FROM builder_events AS be
            LEFT JOIN memory_lane_records AS mlr
              ON mlr.event_id = be.event_id
            WHERE mlr.event_id IS NULL
              AND (
                instr(COALESCE(be.facts_json, ''), '"keepability"') > 0
                OR instr(COALESCE(be.facts_json, ''), '"promotion_disposition"') > 0
              )
            ORDER BY be.created_at ASC, be.event_id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        repaired = 0
        for row in rows:
            try:
                facts = json.loads(row["facts_json"]) if row["facts_json"] else {}
            except json.JSONDecodeError:
                facts = {}
            try:
                provenance = json.loads(row["provenance_json"]) if row["provenance_json"] else {}
            except json.JSONDecodeError:
                provenance = {}
            if not isinstance(facts, dict):
                facts = {}
            if not isinstance(provenance, dict):
                provenance = {}
            if not facts.get("keepability") and not facts.get("promotion_disposition"):
                continue
            _mirror_memory_lane_event(
                conn,
                event_id=str(row["event_id"]),
                event_type=str(row["event_type"]),
                recorded_at=str(row["created_at"]),
                component=str(row["component"]),
                run_id=str(row["run_id"]) if row["run_id"] is not None else None,
                request_id=str(row["request_id"]) if row["request_id"] is not None else None,
                trace_ref=str(row["trace_ref"]) if row["trace_ref"] is not None else None,
                reason_code=str(row["reason_code"]) if row["reason_code"] is not None else None,
                facts=facts,
                provenance=provenance,
            )
            lane_row = conn.execute(
                "SELECT 1 FROM memory_lane_records WHERE event_id = ? LIMIT 1",
                (str(row["event_id"]),),
            ).fetchone()
            if lane_row:
                repaired += 1
        conn.commit()
    return repaired


def repair_memory_lane_artifact_lanes(state_db: StateDB, *, limit: int = 50000) -> int:
    repaired = 0
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                lane_record_id,
                artifact_lane,
                promotion_target_lane,
                keepability,
                promotion_disposition,
                evidence_json
            FROM memory_lane_records
            WHERE keepability IS NOT NULL
            ORDER BY recorded_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            keepability = str(row["keepability"] or "")
            promotion_disposition = str(row["promotion_disposition"] or "")
            expected_lane = _artifact_lane_from_keepability(keepability)
            expected_target_lane = _promotion_target_lane(promotion_disposition)
            if (
                str(row["artifact_lane"] or "") == expected_lane
                and (row["promotion_target_lane"] or None) == expected_target_lane
            ):
                continue
            evidence = _repair_memory_lane_evidence_json(
                row["evidence_json"],
                artifact_lane=expected_lane,
                promotion_target_lane=expected_target_lane,
            )
            conn.execute(
                """
                UPDATE memory_lane_records
                SET artifact_lane = ?,
                    promotion_target_lane = ?,
                    evidence_json = ?
                WHERE lane_record_id = ?
                """,
                (expected_lane, expected_target_lane, evidence, row["lane_record_id"]),
            )
            repaired += 1
        conn.commit()
    return repaired


def _repair_memory_lane_evidence_json(
    value: Any,
    *,
    artifact_lane: str,
    promotion_target_lane: str | None,
) -> str | None:
    try:
        payload = json.loads(str(value)) if value else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["artifact_lane"] = artifact_lane
    payload["promotion_target_lane"] = promotion_target_lane
    facts = payload.get("facts")
    if isinstance(facts, dict):
        facts["artifact_lane"] = artifact_lane
        facts["promotion_target_lane"] = promotion_target_lane
        trace_contract = facts.get("trace_contract")
        if isinstance(trace_contract, dict):
            trace_contract["destination"] = promotion_target_lane or artifact_lane
    return _json_or_none(payload)


def _artifact_lane_from_keepability(value: str) -> str:
    keepability = str(value or "")
    if keepability == "not_keepable":
        return "rejected_memory_candidates"
    if keepability == "ephemeral_context":
        return "working_scratchpad"
    if keepability == "operator_debug_only":
        return "ops_transcripts"
    if keepability == "user_preference_ephemeral":
        return "user_history"
    if keepability == "durable_user_memory":
        return "durable_user_memory"
    if keepability == "durable_intelligence_memory":
        return "durable_intelligence_memory"
    if keepability == "supporting_memory":
        return "supporting_memory"
    if keepability == "episodic_trace":
        return "episodic_trace"
    return "execution_evidence"


def _promotion_target_lane(disposition: str) -> str | None:
    if not disposition:
        return None
    if disposition in NON_PROMOTABLE_DISPOSITIONS:
        return None
    if disposition == "promote_current_state":
        return "durable_user_memory"
    if disposition == "promote_structured_evidence":
        return "durable_intelligence_memory"
    if disposition == "promote_belief_candidate":
        return "belief_candidates"
    if disposition == "capture_raw_episode":
        return "episodic_trace"
    return "durable_intelligence_memory"


def _promotion_record_status(disposition: str) -> str:
    if disposition in NON_PROMOTABLE_DISPOSITIONS:
        return "blocked"
    if disposition == "capture_raw_episode":
        return "captured"
    if disposition:
        return "candidate"
    return "observed"


def _artifact_kind_for_memory_lane(*, component: str, event_type: str) -> str:
    if event_type == "plugin_or_chip_influence_recorded":
        return "operational_influence"
    if component == "researcher_bridge" and event_type in {"tool_result_received", "dispatch_failed"}:
        return "bridge_output"
    if event_type in {"delivery_attempted", "delivery_succeeded", "delivery_failed"}:
        return "delivery_output"
    return "classified_artifact"


def _trust_level_from_keepability(value: Any) -> str:
    keepability = str(value or "")
    if keepability in {"user_preference_ephemeral"}:
        return "user_preference_ephemeral"
    if keepability in {"ephemeral_context", "operator_debug_only"}:
        return "ephemeral_context"
    return "context_only"


def _compute_health_facts(state_db: StateDB) -> dict[str, Any]:
    return {
        "last_ingress_at": _latest_event_created_at(
            state_db,
            event_types=("intent_committed",),
            components=("telegram_runtime", "discord_webhook", "whatsapp_webhook", "simulated_dm"),
        ),
        "last_dispatch_started_at": _latest_event_created_at(
            state_db,
            event_types=("dispatch_started",),
        ),
        "last_tool_result_at": _latest_event_created_at(
            state_db,
            event_types=("tool_result_received",),
        ),
        "last_delivery_attempt_at": _latest_event_created_at(
            state_db,
            event_types=("delivery_attempted",),
        ),
        "last_delivery_acked_at": _latest_event_created_at(
            state_db,
            event_types=("delivery_succeeded",),
        ),
        "last_background_run_opened_at": _latest_background_event_created_at(
            state_db,
            event_types=("run_opened",),
        ),
        "last_background_run_closed_at": _latest_background_event_created_at(
            state_db,
            event_types=("run_closed", "run_failed", "run_stalled", "run_overlap_skipped"),
        ),
        "last_environment_snapshot_at": _latest_snapshot_created_at(state_db),
        "current_stalled_run_count": _count_rows(
            state_db,
            """
            SELECT COUNT(*) AS c
            FROM run_registry
            WHERE status = 'stalled'
              AND (run_kind LIKE 'job:%' OR surface_kind = 'jobs_tick')
            """,
        ),
        "current_unacked_delivery_count": _count_rows(
            state_db,
            "SELECT COUNT(*) AS c FROM delivery_registry WHERE status = 'attempted'",
        ),
    }


def _build_config_authority_panel(state_db: StateDB) -> dict[str, Any]:
    with state_db.connect() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS total_mutations,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_mutations,
                SUM(CASE WHEN error_message = 'semantic_noop' THEN 1 ELSE 0 END) AS suppressed_noops,
                SUM(
                    CASE
                        WHEN actor_type NOT IN ('operator', 'user')
                        THEN 1
                        ELSE 0
                    END
                ) AS runtime_mutation_attempts
            FROM config_mutation_audit
            """
        ).fetchone()
        repeated_internal_writes = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM (
                SELECT target_path
                FROM config_mutation_audit
                WHERE actor_type NOT IN ('operator', 'user')
                GROUP BY target_path
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()
        failed_validation = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM config_mutation_audit
            WHERE status = 'rejected' AND COALESCE(error_message, '') <> 'semantic_noop'
            """
        ).fetchone()
    return {
        "latest_mutation_attempts": recent_config_mutations(state_db, limit=10),
        "counts": {
            "total_mutations": int((totals["total_mutations"] if totals else 0) or 0),
            "runtime_mutation_attempts": int((totals["runtime_mutation_attempts"] if totals else 0) or 0),
            "rejected_mutations": int((totals["rejected_mutations"] if totals else 0) or 0),
            "suppressed_noop_writes": int((totals["suppressed_noops"] if totals else 0) or 0),
            "repeated_internal_writes": int((repeated_internal_writes["c"] if repeated_internal_writes else 0) or 0),
            "failed_validation_after_mutation": int((failed_validation["c"] if failed_validation else 0) or 0),
        },
    }


def _build_execution_lineage_panel(state_db: StateDB) -> dict[str, Any]:
    terminal_run_events = {"run_closed", "run_failed", "run_stalled"}
    request_proof_events = {"dispatch_started", "tool_result_received", "dispatch_failed", *terminal_run_events}
    with state_db.connect() as conn:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN event_type = 'intent_committed' THEN 1 ELSE 0 END) AS intent_count,
                SUM(CASE WHEN event_type = 'dispatch_started' THEN 1 ELSE 0 END) AS dispatch_started_count,
                SUM(CASE WHEN event_type = 'tool_result_received' THEN 1 ELSE 0 END) AS tool_result_count,
                SUM(CASE WHEN event_type = 'dispatch_failed' THEN 1 ELSE 0 END) AS dispatch_failed_count
            FROM builder_events
            WHERE event_type IN ('intent_committed', 'dispatch_started', 'tool_result_received', 'dispatch_failed')
            """
        ).fetchone()
    intent_without_dispatch = 0
    dispatch_without_result = 0
    intents = latest_events_by_type(state_db, event_type="intent_committed", limit=300)
    dispatches = latest_events_by_type(state_db, event_type="dispatch_started", limit=300)
    for intent in intents:
        run_id = str(intent.get("run_id") or "")
        if not run_id:
            request_id = str(intent.get("request_id") or "")
            if request_id and _request_has_event_type(
                state_db,
                request_id=request_id,
                excluded_event_id=str(intent.get("event_id") or ""),
                event_types=request_proof_events,
            ):
                continue
            intent_without_dispatch += 1
            continue
        run_events = {event.get("event_type") for event in events_for_run(state_db, run_id=run_id)}
        if (
            "dispatch_started" not in run_events
            and "dispatch_failed" not in run_events
            and "tool_result_received" not in run_events
            and terminal_run_events.isdisjoint(run_events)
        ):
            intent_without_dispatch += 1
    for dispatch in dispatches:
        run_id = str(dispatch.get("run_id") or "")
        if not run_id:
            continue
        run_events = {event.get("event_type") for event in events_for_run(state_db, run_id=run_id)}
        if "tool_result_received" not in run_events and "dispatch_failed" not in run_events and terminal_run_events.isdisjoint(run_events):
            dispatch_without_result += 1
    return {
        "counts": {
            "intents_committed": int((counts["intent_count"] if counts else 0) or 0),
            "dispatches_started": int((counts["dispatch_started_count"] if counts else 0) or 0),
            "tool_results_received": int((counts["tool_result_count"] if counts else 0) or 0),
            "dispatch_failures": int((counts["dispatch_failed_count"] if counts else 0) or 0),
            "intent_without_dispatch": intent_without_dispatch,
            "dispatch_without_result_closure": dispatch_without_result,
        }
    }


def _request_has_event_type(
    state_db: StateDB,
    *,
    request_id: str,
    excluded_event_id: str,
    event_types: set[str],
) -> bool:
    placeholders = ", ".join("?" for _ in event_types)
    params = [request_id, excluded_event_id, *sorted(event_types)]
    with state_db.connect() as conn:
        row = conn.execute(
            f"""
            SELECT event_id
            FROM builder_events
            WHERE request_id = ?
              AND event_id != ?
              AND event_type IN ({placeholders})
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row is not None


def _build_delivery_truth_panel(state_db: StateDB, *, health_facts: dict[str, Any]) -> dict[str, Any]:
    with state_db.connect() as conn:
        bridge_generated = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM builder_events
            WHERE event_type = 'tool_result_received'
              AND component = 'researcher_bridge'
            """
        ).fetchone()
        delivery_counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'attempted' THEN 1 ELSE 0 END) AS attempted_count,
                SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS acked_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM delivery_registry
            """
        ).fetchone()
        failure_rows = conn.execute(
            """
            SELECT failure_family, COUNT(*) AS failure_count
            FROM delivery_registry
            WHERE status = 'failed' AND failure_family IS NOT NULL
            GROUP BY failure_family
            HAVING COUNT(*) > 1
            ORDER BY failure_count DESC, failure_family ASC
            """
        ).fetchall()
    return {
        "counts": {
            "generated_replies": int((bridge_generated["c"] if bridge_generated else 0) or 0),
            "attempted_deliveries": int((delivery_counts["attempted_count"] if delivery_counts else 0) or 0),
            "acked_deliveries": int((delivery_counts["acked_count"] if delivery_counts else 0) or 0),
            "failed_deliveries": int((delivery_counts["failed_count"] if delivery_counts else 0) or 0),
            "current_unacked_delivery_count": int(health_facts.get("current_unacked_delivery_count") or 0),
        },
        "repeated_failure_families": [
            {"failure_family": str(row["failure_family"]), "count": int(row["failure_count"])}
            for row in failure_rows
        ],
    }


def _build_background_freshness_panel(
    state_db: StateDB,
    *,
    health_facts: dict[str, Any],
    background_stale_seconds: int,
) -> dict[str, Any]:
    with state_db.connect() as conn:
        overlap_row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM builder_events
            WHERE event_type = 'run_overlap_skipped'
            """
        ).fetchone()
        open_background = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM builder_runs
            WHERE status = 'open' AND (run_kind LIKE 'job:%' OR origin_surface = 'jobs_tick')
            """
        ).fetchone()
    lag_seconds = _freshness_lag_seconds(
        health_facts.get("last_background_run_opened_at"),
        health_facts.get("last_background_run_closed_at"),
    )
    return {
        "last_run_opened_at": health_facts.get("last_background_run_opened_at"),
        "last_run_closed_at": health_facts.get("last_background_run_closed_at"),
        "current_stalled_run_count": int(health_facts.get("current_stalled_run_count") or 0),
        "current_open_background_runs": int((open_background["c"] if open_background else 0) or 0),
        "overlap_skips": int((overlap_row["c"] if overlap_row else 0) or 0),
        "freshness_lag_seconds": lag_seconds,
        "freshness_threshold_seconds": background_stale_seconds,
    }


def _build_environment_parity_panel(state_db: StateDB) -> dict[str, Any]:
    snapshots = latest_snapshots_by_surface(state_db)
    snapshot_hashes = {
        surface: str(snapshot.get("config_hash") or "")
        for surface, snapshot in snapshots.items()
    }
    return {
        "snapshot_hashes": snapshot_hashes,
        "latest_surfaces": snapshots,
        "mismatch_fields": _environment_snapshot_disagreements(snapshots),
        "current_mismatch_count": len(_environment_snapshot_disagreements(snapshots)),
    }


def _environment_snapshot_disagreements(
    snapshots: dict[str, dict[str, Any]],
) -> list[str]:
    comparable_fields = (
        "provider_id",
        "provider_model",
        "provider_base_url",
        "provider_execution_transport",
        "runtime_root",
        "config_path",
        "python_executable",
    )
    mismatch_fields: list[str] = []
    rows = list(snapshots.items())
    if not rows:
        return mismatch_fields
    baseline_surface, baseline = rows[0]
    for surface, snapshot in rows[1:]:
        for field in comparable_fields:
            if not _environment_field_should_match(baseline_surface, surface, field):
                continue
            left = _normalize_environment_snapshot_field(field, baseline.get(field))
            right = _normalize_environment_snapshot_field(field, snapshot.get(field))
            if left and right and left != right:
                mismatch_fields.append(
                    f"{baseline_surface}:{field}={left} != {surface}:{field}={right}"
                )
    return mismatch_fields


def _normalize_environment_snapshot_field(field: str, value: Any) -> Any:
    normalized = str(value).strip() if value is not None else None
    if not normalized:
        return value
    if field not in {"runtime_root", "config_path", "python_executable"}:
        return normalized
    translated = _canonicalize_runtime_snapshot_path(normalized)
    return translated or normalized


def _canonicalize_runtime_snapshot_path(value: str) -> str | None:
    wsl_match = re.match(r"^/mnt/([A-Za-z])/(.*)$", value)
    if wsl_match:
        drive = wsl_match.group(1).lower()
        remainder = wsl_match.group(2).replace("\\", "/")
        return str(Path("/mnt") / drive / remainder)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
    if not match:
        return None
    drive = match.group(1).lower()
    remainder = match.group(2).replace("\\", "/")
    return str(Path("/mnt") / drive / remainder)


def _environment_field_should_match(left_surface: str, right_surface: str, field: str) -> bool:
    if field == "python_executable":
        return False
    if field == "runtime_root" and "swarm_bridge" in {left_surface, right_surface}:
        return False
    return True


def _build_provenance_and_quarantine_panel(state_db: StateDB) -> dict[str, Any]:
    with state_db.connect() as conn:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS mutation_count,
                SUM(
                    CASE
                        WHEN source_kind = 'unknown' OR source_id = 'unknown'
                        THEN 1
                        ELSE 0
                    END
                ) AS unlabeled_count
            FROM provenance_mutation_log
            """
        ).fetchone()
        quarantine_count = conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine_records"
        ).fetchone()
        policy_gate_count = conn.execute(
            "SELECT COUNT(*) AS c FROM policy_gate_records"
        ).fetchone()
        policy_families = conn.execute(
            """
            SELECT gate_name, COUNT(*) AS gate_count
            FROM policy_gate_records
            GROUP BY gate_name
            ORDER BY gate_count DESC, gate_name ASC
            """
        ).fetchall()
        extension_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM provenance_mutation_log
            WHERE source_kind IN ('chip_hook', 'attachment_snapshot')
               OR source_kind LIKE 'personality_%'
            """
        ).fetchone()
    return {
        "counts": {
            "plugin_or_chip_mutations": int((counts["mutation_count"] if counts else 0) or 0),
            "quarantined_outputs": int((quarantine_count["c"] if quarantine_count else 0) or 0),
            "policy_gate_blocks": int((policy_gate_count["c"] if policy_gate_count else 0) or 0),
            "unlabeled_mutation_incidents": int((counts["unlabeled_count"] if counts else 0) or 0),
            "extension_influence_events": int((extension_count["c"] if extension_count else 0) or 0),
        },
        "policy_gate_families": [
            {"gate_name": str(row["gate_name"]), "count": int(row["gate_count"])}
            for row in policy_families
        ],
        "recent_policy_blocks": recent_policy_gate_records(state_db, limit=10),
        "recent_incidents": recent_provenance_mutations(state_db, limit=10),
        "recent_quarantines": recent_quarantine_records(state_db, limit=10),
    }


def _build_contradiction_panel(state_db: StateDB) -> dict[str, Any]:
    with state_db.connect() as conn:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved_count
            FROM contradiction_records
            """
        ).fetchone()
    return {
        "counts": {
            "open": int((counts["open_count"] if counts else 0) or 0),
            "resolved": int((counts["resolved_count"] if counts else 0) or 0),
        },
        "recent_open": recent_contradictions(state_db, limit=10, status="open"),
        "recent_resolved": recent_contradictions(state_db, limit=10, status="resolved"),
    }


def _build_memory_lane_hygiene_panel(state_db: StateDB) -> dict[str, Any]:
    lane_records = recent_memory_lane_records(state_db, limit=300)
    promotion_attempts = len(lane_records)
    blocked_promotions = sum(1 for record in lane_records if str(record.get("status") or "") == "blocked")
    ops_residue_volume = sum(
        1 for record in lane_records if str(record.get("artifact_lane") or "") == "ops_transcripts"
    )
    rejected_candidate_volume = sum(
        1 for record in lane_records if str(record.get("artifact_lane") or "") == "rejected_memory_candidates"
    )
    lanes = {str(record.get("artifact_lane") or "") for record in lane_records}
    lane_counts: dict[str, int] = {}
    for record in lane_records:
        lane = str(record.get("artifact_lane") or "unknown")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    contradiction_rows = recent_contradictions(state_db, limit=100, status="open")
    resume_integrity_incidents = [
        row
        for row in contradiction_rows
        if str(row.get("reason_code") or "")
        in {"stop_ship_runtime_state_authority", "stop_ship_bridge_output_governance"}
    ]
    return {
        "counts": {
            "promotion_attempts": promotion_attempts,
            "blocked_promotions": blocked_promotions,
            "ops_residue_volume": ops_residue_volume,
            "rejected_candidate_volume": rejected_candidate_volume,
            "reset_or_resume_integrity_incidents": len(resume_integrity_incidents),
        },
        "lane_counts": lane_counts,
        "lane_labels_present": {
            "execution_evidence": "execution_evidence" in lanes,
            "durable_intelligence_memory": "durable_intelligence_memory" in lanes,
            "durable_user_memory": "durable_user_memory" in lanes,
            "supporting_memory": "supporting_memory" in lanes,
            "episodic_trace": "episodic_trace" in lanes,
            "rejected_memory_candidates": "rejected_memory_candidates" in lanes,
            "working_scratchpad": "working_scratchpad" in lanes,
            "ops_transcripts": "ops_transcripts" in lanes,
            "user_history": "user_history" in lanes,
        },
        "recent_promotions": lane_records[:10],
        "recent_integrity_incidents": resume_integrity_incidents[:10],
    }


def _build_memory_doctor_brain_panel(state_db: StateDB) -> dict[str, Any]:
    events = latest_events_by_type(state_db, event_type="memory_doctor_brain_evaluated", limit=50)
    if not events:
        return {
            "panel": "memory_doctor_brain",
            "status": "no_data",
            "authority": "observability_non_authoritative",
            "counts": {"evaluations": 0},
            "trend": [],
            "repeated_missing_senses": {},
            "repeated_gaps": {},
            "intake_trigger_counts": {},
            "intake_calibration_counts": {},
            "previous_failure_signal_counts": {},
            "root_cause_primary_gap_counts": {},
            "root_cause_failure_layer_counts": {},
            "root_cause_owner_surface_counts": {},
            "root_cause_audit_focus_counts": {},
            "repair_priority": {"status": "no_data"},
            "creator_alignment": {
                "status": "no_data",
                "artifact_targets": [],
                "validation_issue_count": 0,
            },
            "recent_intake_triggers": [],
            "recent_root_causes": [],
            "recent_probes": [],
        }

    trend: list[dict[str, Any]] = []
    scores: list[int] = []
    gap_counts: list[int] = []
    missing_sense_counts: dict[str, int] = {}
    gap_name_counts: dict[str, int] = {}
    intake_trigger_counts: dict[str, int] = {}
    intake_calibration_counts: dict[str, int] = {}
    previous_failure_signal_counts: dict[str, int] = {}
    root_cause_primary_gap_counts: dict[str, int] = {}
    root_cause_failure_layer_counts: dict[str, int] = {}
    root_cause_owner_surface_counts: dict[str, int] = {}
    root_cause_audit_focus_counts: dict[str, int] = {}
    recent_intakes: list[dict[str, Any]] = []
    recent_root_causes: list[dict[str, Any]] = []
    humans: set[str] = set()
    topics: set[str] = set()

    for event in reversed(events):
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        score = _optional_int(facts.get("coverage_score"))
        missing_senses = _string_list(facts.get("missing_senses"))
        gap_names = _string_list(facts.get("gap_names"))
        telegram_intake = facts.get("telegram_intake") if isinstance(facts.get("telegram_intake"), dict) else {}
        root_cause_status = str(facts.get("root_cause_status") or "").strip()
        root_cause_primary_gap = str(facts.get("root_cause_primary_gap") or "").strip()
        root_cause_failure_layer = str(facts.get("root_cause_failure_layer") or "").strip()
        root_cause_summary = str(facts.get("root_cause_summary") or "").strip()
        root_cause_chain = _string_list(facts.get("root_cause_chain"))
        root_cause_confidence = str(facts.get("root_cause_confidence") or "").strip()
        root_cause_owner_surface = str(facts.get("root_cause_owner_surface") or "").strip()
        root_cause_audit_focus = _string_list(facts.get("root_cause_audit_focus"))
        root_cause_repair_action = str(facts.get("root_cause_repair_action") or "").strip()
        root_cause_replay_probe = str(facts.get("root_cause_replay_probe") or "").strip()
        creator_alignment_status = str(facts.get("creator_alignment_status") or "").strip()
        creator_alignment_artifact_targets = _string_list(facts.get("creator_alignment_artifact_targets"))
        creator_alignment_issue_count = _optional_int(facts.get("creator_alignment_validation_issue_count"))
        topic = str(facts.get("topic") or "").strip()
        human_id = str(event.get("human_id") or "").strip()
        if human_id:
            humans.add(human_id)
        if topic:
            topics.add(topic)
        for sense in missing_senses:
            missing_sense_counts[sense] = missing_sense_counts.get(sense, 0) + 1
        for gap_name in gap_names:
            gap_name_counts[gap_name] = gap_name_counts.get(gap_name, 0) + 1
        intake_signals = _string_list(telegram_intake.get("contextual_trigger_signals")) if telegram_intake else []
        previous_failure_signals = _string_list(telegram_intake.get("previous_failure_signals")) if telegram_intake else []
        for signal in intake_signals:
            intake_trigger_counts[signal] = intake_trigger_counts.get(signal, 0) + 1
        for signal in previous_failure_signals:
            previous_failure_signal_counts[signal] = previous_failure_signal_counts.get(signal, 0) + 1
        if root_cause_status == "identified":
            if root_cause_primary_gap:
                root_cause_primary_gap_counts[root_cause_primary_gap] = (
                    root_cause_primary_gap_counts.get(root_cause_primary_gap, 0) + 1
                )
            if root_cause_failure_layer:
                root_cause_failure_layer_counts[root_cause_failure_layer] = (
                    root_cause_failure_layer_counts.get(root_cause_failure_layer, 0) + 1
                )
            if root_cause_owner_surface:
                root_cause_owner_surface_counts[root_cause_owner_surface] = (
                    root_cause_owner_surface_counts.get(root_cause_owner_surface, 0) + 1
                )
            for audit_focus in root_cause_audit_focus:
                root_cause_audit_focus_counts[audit_focus] = (
                    root_cause_audit_focus_counts.get(audit_focus, 0) + 1
                )
            recent_root_causes.append(
                {
                    "created_at": event.get("created_at"),
                    "human_id": human_id or None,
                    "topic": topic or None,
                    "request_id": facts.get("request_id"),
                    "primary_gap": root_cause_primary_gap or None,
                    "failure_layer": root_cause_failure_layer or None,
                    "summary": root_cause_summary or None,
                    "chain": root_cause_chain,
                    "confidence": root_cause_confidence or None,
                    "owner_surface": root_cause_owner_surface or None,
                    "audit_focus": root_cause_audit_focus,
                    "repair_action": root_cause_repair_action or None,
                    "replay_probe": root_cause_replay_probe or None,
                }
            )
        if telegram_intake:
            intake_score = _optional_int(telegram_intake.get("contextual_trigger_score"))
            intake_threshold = _optional_int(telegram_intake.get("contextual_trigger_threshold"))
            calibration_label = _memory_doctor_intake_calibration_label(
                score=intake_score,
                threshold=intake_threshold,
                previous_failure_signal=bool(telegram_intake.get("previous_failure_signal")),
            )
            if calibration_label:
                intake_calibration_counts[calibration_label] = intake_calibration_counts.get(calibration_label, 0) + 1
            recent_intakes.append(
                {
                    "created_at": event.get("created_at"),
                    "human_id": human_id or None,
                    "diagnosed_request_id": facts.get("request_id"),
                    "doctor_request_id": telegram_intake.get("request_id"),
                    "user_message_preview": telegram_intake.get("user_message_preview"),
                    "request_selector": telegram_intake.get("request_selector"),
                    "contextual_trigger_score": intake_score,
                    "contextual_trigger_threshold": intake_threshold,
                    "contextual_trigger_margin": intake_score - intake_threshold
                    if intake_score is not None and intake_threshold is not None
                    else None,
                    "calibration_label": calibration_label,
                    "contextual_trigger_signals": intake_signals,
                    "previous_failure_signal": telegram_intake.get("previous_failure_signal"),
                    "previous_failure_signals": previous_failure_signals,
                }
            )
        if score is not None:
            scores.append(score)
        gap_counts.append(len(gap_names))
        trend.append(
            {
                "event_id": event.get("event_id"),
                "created_at": event.get("created_at"),
                "human_id": human_id or None,
                "topic": topic or None,
                "coverage_score": score,
                "gap_count": len(gap_names),
                "highest_severity": facts.get("highest_severity"),
                "next_probe": facts.get("next_probe"),
                "telegram_intake": recent_intakes[-1] if telegram_intake else None,
                "root_cause": recent_root_causes[-1] if root_cause_status == "identified" else None,
                "creator_alignment": {
                    "status": creator_alignment_status or None,
                    "artifact_targets": creator_alignment_artifact_targets,
                    "validation_issue_count": creator_alignment_issue_count,
                },
            }
        )

    latest = trend[-1]
    latest_score = latest.get("coverage_score")
    latest_gap_count = int(latest.get("gap_count") or 0)
    oldest_score = scores[0] if scores else None
    score_delta = int(latest_score) - int(oldest_score) if latest_score is not None and oldest_score is not None else None
    status = "healthy" if latest_score is not None and int(latest_score) >= 80 and latest_gap_count == 0 else "watching"
    if latest_score is not None and int(latest_score) < 50:
        status = "degraded"
    repair_priority = _memory_doctor_repair_priority(
        owner_surface_counts=root_cause_owner_surface_counts,
        audit_focus_counts=root_cause_audit_focus_counts,
        recent_root_causes=recent_root_causes,
    )

    return {
        "panel": "memory_doctor_brain",
        "status": status,
        "authority": "observability_non_authoritative",
        "counts": {
            "evaluations": len(events),
            "humans": len(humans),
            "topics": len(topics),
            "latest_gap_count": latest_gap_count,
            "max_gap_count": max(gap_counts) if gap_counts else 0,
        },
        "latest": latest,
        "score": {
            "latest": latest_score,
            "oldest": oldest_score,
            "delta": score_delta,
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
        },
        "repeated_missing_senses": _top_counts(missing_sense_counts),
        "repeated_gaps": _top_counts(gap_name_counts),
        "intake_trigger_counts": _top_counts(intake_trigger_counts),
        "intake_calibration_counts": _top_counts(intake_calibration_counts),
        "previous_failure_signal_counts": _top_counts(previous_failure_signal_counts),
        "root_cause_primary_gap_counts": _top_counts(root_cause_primary_gap_counts),
        "root_cause_failure_layer_counts": _top_counts(root_cause_failure_layer_counts),
        "root_cause_owner_surface_counts": _top_counts(root_cause_owner_surface_counts),
        "root_cause_audit_focus_counts": _top_counts(root_cause_audit_focus_counts),
        "repair_priority": repair_priority,
        "creator_alignment": latest.get("creator_alignment") or {},
        "recent_intake_triggers": list(reversed(recent_intakes))[:5],
        "recent_root_causes": list(reversed(recent_root_causes))[:5],
        "recent_probes": [
            probe
            for probe in (str(item.get("next_probe") or "").strip() for item in reversed(trend))
            if probe
        ][:5],
        "trend": trend[-10:],
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _memory_doctor_intake_calibration_label(
    *,
    score: int | None,
    threshold: int | None,
    previous_failure_signal: bool,
) -> str | None:
    if score is None or threshold is None:
        return None
    if score < threshold:
        return "below_threshold_recorded"
    if previous_failure_signal:
        raw_score = max(score - 2, 0)
        if raw_score < 4:
            return "previous_turn_boosted"
        return "direct_with_previous_context"
    if score == threshold:
        return "borderline_direct"
    return "strong_direct"


def _memory_doctor_repair_priority(
    *,
    owner_surface_counts: dict[str, int],
    audit_focus_counts: dict[str, int],
    recent_root_causes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not owner_surface_counts:
        return {"status": "no_data"}
    top_owner_surface, top_owner_count = sorted(
        owner_surface_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[0]
    top_audit_focus = None
    top_audit_focus_count = 0
    if audit_focus_counts:
        top_audit_focus, top_audit_focus_count = sorted(
            audit_focus_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
    latest_matching = next(
        (
            item
            for item in reversed(recent_root_causes)
            if item.get("owner_surface") == top_owner_surface
        ),
        {},
    )
    repeated = top_owner_count > 1
    return {
        "status": "ready" if repeated else "candidate",
        "basis": "repeated_root_cause_owner_surface" if repeated else "single_root_cause_owner_surface",
        "owner_surface": top_owner_surface,
        "owner_surface_count": top_owner_count,
        "audit_focus": top_audit_focus,
        "audit_focus_count": top_audit_focus_count,
        "repair_action": latest_matching.get("repair_action"),
        "replay_probe": latest_matching.get("replay_probe"),
        "latest_request_id": latest_matching.get("request_id"),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> dict[str, int]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {key: value for key, value in ordered[:limit]}


def _build_procedural_memory_panel(state_db: StateDB) -> dict[str, Any]:
    lessons = recent_procedural_lesson_records(state_db, limit=100, active_only=True)
    kind_counts: dict[str, int] = {}
    component_counts: dict[str, int] = {}
    for lesson in lessons:
        kind = str(lesson.get("lesson_kind") or "unknown")
        component = str(lesson.get("applies_to_component") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        component_counts[component] = component_counts.get(component, 0) + 1
    return {
        "counts": {
            "active_procedural_lessons": len(lessons),
            "target_resolution_lessons": kind_counts.get("target_resolution", 0),
            "wrong_build_target_lessons": kind_counts.get("wrong_build_target", 0),
            "bad_self_review_lessons": kind_counts.get("bad_self_review", 0),
            "timeout_recovery_lessons": kind_counts.get("timeout_recovery", 0),
        },
        "kind_counts": kind_counts,
        "component_counts": component_counts,
        "recent_lessons": lessons[:10],
    }


def _build_personality_panel(state_db: StateDB) -> dict[str, Any]:
    profiles = recent_personality_trait_profiles(state_db, limit=50)
    observations = recent_personality_observations(state_db, limit=100)
    evolutions = recent_personality_evolution_events(state_db, limit=50)
    typed_human_sets = personality_typed_human_sets(state_db)

    with state_db.connect() as conn:
        runtime_rows = conn.execute(
            """
            SELECT state_key
            FROM runtime_state
            WHERE state_key LIKE 'personality:%'
            """
        ).fetchall()
        attachment_snapshot_row = conn.execute(
            """
            SELECT generated_at, summary_json
            FROM attachment_state_snapshots
            ORDER BY generated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()

    profile_mirror_humans: set[str] = set()
    observation_mirror_humans: set[str] = set()
    evolution_mirror_humans: set[str] = set()
    for row in runtime_rows:
        state_key = str(row["state_key"] or "")
        if not state_key.startswith("personality:"):
            continue
        human_id, _, suffix = state_key[len("personality:"):].rpartition(":")
        if not human_id or not suffix:
            continue
        if suffix == "trait_deltas":
            profile_mirror_humans.add(human_id)
        elif suffix == "observations":
            observation_mirror_humans.add(human_id)
        elif suffix == "evolution_log":
            evolution_mirror_humans.add(human_id)

    typed_profile_humans = typed_human_sets["trait_profiles"]
    typed_observation_humans = typed_human_sets["observations"]
    typed_evolution_humans = typed_human_sets["evolution_events"]

    profile_drift = {
        "missing_runtime_mirrors": sorted(typed_profile_humans - profile_mirror_humans),
        "runtime_only_mirrors": sorted(profile_mirror_humans - typed_profile_humans),
    }
    observation_drift = {
        "missing_runtime_mirrors": sorted(typed_observation_humans - observation_mirror_humans),
        "runtime_only_mirrors": sorted(observation_mirror_humans - typed_observation_humans),
    }
    evolution_drift = {
        "missing_runtime_mirrors": sorted(typed_evolution_humans - evolution_mirror_humans),
        "runtime_only_mirrors": sorted(evolution_mirror_humans - typed_evolution_humans),
    }

    active_profiles = 0
    recent_humans: dict[str, dict[str, Any]] = {}
    for row in profiles:
        human_id = str(row.get("human_id") or "")
        if not human_id:
            continue
        deltas = row.get("deltas_json")
        if isinstance(deltas, dict) and deltas:
            active_profiles += 1
        recent_humans.setdefault(
            human_id,
            {
                "human_id": human_id,
                "profile_updated_at": row.get("updated_at"),
                "last_observed_at": None,
                "last_evolved_at": None,
            },
        )
    for row in observations:
        human_id = str(row.get("human_id") or "")
        if not human_id:
            continue
        recent_humans.setdefault(
            human_id,
            {
                "human_id": human_id,
                "profile_updated_at": None,
                "last_observed_at": None,
                "last_evolved_at": None,
            },
        )["last_observed_at"] = row.get("observed_at")
    for row in evolutions:
        human_id = str(row.get("human_id") or "")
        if not human_id:
            continue
        recent_humans.setdefault(
            human_id,
            {
                "human_id": human_id,
                "profile_updated_at": None,
                "last_observed_at": None,
                "last_evolved_at": None,
            },
        )["last_evolved_at"] = row.get("evolved_at")

    total_drift = sum(
        len(payload["missing_runtime_mirrors"]) + len(payload["runtime_only_mirrors"])
        for payload in (profile_drift, observation_drift, evolution_drift)
    )
    attachment_summary: dict[str, Any] = {}
    if attachment_snapshot_row and attachment_snapshot_row["summary_json"]:
        try:
            parsed_summary = json.loads(str(attachment_snapshot_row["summary_json"]))
        except json.JSONDecodeError:
            parsed_summary = {}
        if isinstance(parsed_summary, dict):
            attachment_summary = parsed_summary
    personality_import = (
        attachment_summary.get("personality_import")
        if isinstance(attachment_summary.get("personality_import"), dict)
        else {}
    )

    return {
        "counts": {
            "trait_profiles": len(profiles),
            "active_profiles": active_profiles,
            "observation_rows": len(observations),
            "evolution_rows": len(evolutions),
            "distinct_humans": len(recent_humans),
            "mirror_drift": total_drift,
            "personality_hook_chip_records": int(personality_import.get("available_chip_count") or 0),
            "personality_hook_active_chip_records": int(personality_import.get("active_chip_count") or 0),
            "personality_import_ready": 1 if personality_import.get("ready") else 0,
        },
        "mirror_drift": {
            "trait_profiles": profile_drift,
            "observations": observation_drift,
            "evolution_events": evolution_drift,
        },
        "personality_import": {
            "ready": bool(personality_import.get("ready")),
            "available_chip_keys": personality_import.get("available_chip_keys") or [],
            "active_chip_keys": personality_import.get("active_chip_keys") or [],
            "snapshot_generated_at": attachment_snapshot_row["generated_at"] if attachment_snapshot_row else None,
        },
        "recent_profiles": profiles[:10],
        "recent_observations": observations[:10],
        "recent_evolutions": evolutions[:10],
        "recent_humans": sorted(
            recent_humans.values(),
            key=lambda item: (
                str(item.get("profile_updated_at") or ""),
                str(item.get("last_observed_at") or ""),
                str(item.get("last_evolved_at") or ""),
                str(item.get("human_id") or ""),
            ),
            reverse=True,
        )[:10],
    }


def _build_agent_identity_panel(state_db: StateDB) -> dict[str, Any]:
    with state_db.connect() as conn:
        link_rows = conn.execute(
            """
            SELECT human_id, canonical_agent_id, preferred_source, status, conflict_agent_id, conflict_reason, updated_at
            FROM canonical_agent_links
            ORDER BY updated_at DESC, human_id DESC
            """
        ).fetchall()
        alias_rows = conn.execute(
            """
            SELECT alias_agent_id, canonical_agent_id, alias_kind, reason_code, created_at
            FROM agent_identity_aliases
            ORDER BY created_at DESC, alias_agent_id DESC
            """
        ).fetchall()
        rename_rows = conn.execute(
            """
            SELECT agent_id, human_id, old_name, new_name, source_surface, created_at
            FROM agent_rename_history
            ORDER BY created_at DESC, rename_id DESC
            LIMIT 20
            """
        ).fetchall()
        profile_rows = conn.execute(
            """
            SELECT agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, updated_at
            FROM agent_profiles
            ORDER BY updated_at DESC, agent_id DESC
            """
        ).fetchall()
        attachment_snapshot_row = conn.execute(
            """
            SELECT generated_at, summary_json
            FROM attachment_state_snapshots
            ORDER BY generated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()

    profile_by_agent = {str(row["agent_id"]): dict(row) for row in profile_rows}
    attachment_summary: dict[str, Any] = {}
    if attachment_snapshot_row and attachment_snapshot_row["summary_json"]:
        try:
            parsed_summary = json.loads(str(attachment_snapshot_row["summary_json"]))
        except json.JSONDecodeError:
            parsed_summary = {}
        if isinstance(parsed_summary, dict):
            attachment_summary = parsed_summary
    identity_import = attachment_summary.get("identity_import") if isinstance(attachment_summary.get("identity_import"), dict) else {}
    source_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    recent_conflicts: list[dict[str, Any]] = []
    for row in link_rows:
        preferred_source = str(row["preferred_source"] or "builder_local")
        status = str(row["status"] or "active")
        source_counts[preferred_source] = source_counts.get(preferred_source, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        if status != "identity_conflict":
            continue
        canonical_agent_id = str(row["canonical_agent_id"] or "")
        profile = profile_by_agent.get(canonical_agent_id) or {}
        recent_conflicts.append(
            {
                "human_id": row["human_id"],
                "canonical_agent_id": canonical_agent_id,
                "canonical_agent_name": profile.get("agent_name"),
                "preferred_source": preferred_source,
                "conflict_agent_id": row["conflict_agent_id"],
                "conflict_reason": row["conflict_reason"],
                "updated_at": row["updated_at"],
            }
        )

    return {
        "counts": {
            "canonical_agents": len(link_rows),
            "builder_local": int(source_counts.get("builder_local") or 0),
            "spark_swarm": int(source_counts.get("spark_swarm") or 0),
            "identity_conflicts": int(status_counts.get("identity_conflict") or 0),
            "aliases": len(alias_rows),
            "rename_events": len(rename_rows),
            "identity_hook_chip_records": int(identity_import.get("available_chip_count") or 0),
            "identity_hook_active_chip_records": int(identity_import.get("active_chip_count") or 0),
            "identity_import_ready": 1 if identity_import.get("ready") else 0,
        },
        "status_counts": status_counts,
        "identity_import": {
            "ready": bool(identity_import.get("ready")),
            "available_chip_keys": identity_import.get("available_chip_keys") or [],
            "active_chip_keys": identity_import.get("active_chip_keys") or [],
            "snapshot_generated_at": attachment_snapshot_row["generated_at"] if attachment_snapshot_row else None,
        },
        "recent_conflicts": recent_conflicts[:10],
        "recent_aliases": [dict(row) for row in alias_rows[:10]],
        "recent_renames": [dict(row) for row in rename_rows[:10]],
        "recent_agents": [dict(row) for row in profile_rows[:10]],
    }


def _build_session_integrity_panel(state_db: StateDB) -> dict[str, Any]:
    reset_registry = recent_reset_sensitive_state_registry(state_db, limit=200)
    active_registry = [row for row in reset_registry if int(row.get("active") or 0) == 1]
    cleared_registry = [row for row in reset_registry if int(row.get("active") or 0) == 0]
    guard_rows = recent_resume_richness_guard_records(state_db, limit=50)
    pending_tasks = recent_pending_task_records(state_db, limit=50, open_only=True)
    reset_events = latest_events_by_type(state_db, event_type="session_reset_performed", limit=20)
    return {
        "counts": {
            "registered_reset_sensitive_keys": len(reset_registry),
            "active_reset_sensitive_keys": len(active_registry),
            "cleared_reset_sensitive_keys": len(cleared_registry),
            "resume_richness_guard_interventions": len(guard_rows),
            "open_pending_tasks": len(pending_tasks),
            "recent_reset_events": len(reset_events),
        },
        "recent_reset_sensitive_keys": active_registry[:10],
        "recent_guard_interventions": guard_rows[:10],
        "recent_pending_tasks": pending_tasks[:10],
        "recent_reset_events": reset_events[:10],
    }


def _build_observer_incident_panel(state_db: StateDB) -> dict[str, Any]:
    incidents = _collect_observer_incidents(state_db)
    counts_by_class: dict[str, int] = {}
    counts_by_severity: dict[str, int] = {}
    actionable_total = 0
    for item in incidents:
        incident_class = str(item.get("incident_class") or "unknown")
        severity = str(item.get("severity") or "medium")
        counts_by_class[incident_class] = counts_by_class.get(incident_class, 0) + 1
        counts_by_severity[severity] = counts_by_severity.get(severity, 0) + 1
        if severity != "info":
            actionable_total += 1

    incidents.sort(
        key=lambda item: (
            str(item.get("severity") or ""),
            str(item.get("recorded_at") or ""),
            str(item.get("item_ref") or ""),
        ),
        reverse=True,
    )
    return {
        "counts": {
            "total": len(incidents),
            "actionable_total": actionable_total,
            "informational_total": counts_by_severity.get("info", 0),
            "distinct_classes": len(counts_by_class),
        },
        "counts_by_class": counts_by_class,
        "counts_by_severity": counts_by_severity,
        "recent_incidents": incidents[:15],
    }


def _collect_observer_incidents(state_db: StateDB) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    policy_blocks = recent_policy_gate_records(state_db, limit=100)
    contradictions = recent_contradictions(state_db, limit=100, status="open")
    provenance_rows = recent_provenance_mutations(state_db, limit=100)
    guard_rows = recent_resume_richness_guard_records(state_db, limit=100)
    memory_events = _recent_memory_contract_events(state_db)

    for row in provenance_rows:
        source_kind = str(row.get("source_kind") or "")
        if not bool(row.get("quarantined")) and source_kind != "unknown":
            continue
        incidents.append(
            {
                "incident_class": "provenance_contamination",
                "severity": "high" if source_kind == "unknown" else "medium",
                "summary": "Unlabeled or quarantined provenance mutation was recorded.",
                "item_ref": str(row.get("mutation_id") or "provenance-incident"),
                "source_kind": source_kind or "unknown",
                "source_ref": str(row.get("source_id") or "unknown"),
                "recorded_at": str(row.get("recorded_at") or "") or None,
                "evidence_refs": [f"provenance_mutation:{str(row.get('mutation_id') or 'unknown')}"],
            }
        )

    for row in policy_blocks:
        policy_domain = str(row.get("policy_domain") or "")
        gate_name = str(row.get("gate_name") or "")
        if policy_domain == "memory_promotion" or gate_name in {
            "keepability_check",
            "residue_check",
            "contradiction_check",
            "provenance_check",
        }:
            incident_class = "promotion_contamination"
        elif gate_name == "secret_boundary":
            incident_class = "secret_boundary"
        elif gate_name == "provenance_missing" or policy_domain == "provenance_mutation":
            incident_class = "provenance_contamination"
        else:
            continue
        incidents.append(
            {
                "incident_class": incident_class,
                "severity": str(row.get("severity") or "medium"),
                "summary": str(row.get("action") or "policy_blocked"),
                "item_ref": str(row.get("policy_gate_id") or "policy-block"),
                "source_kind": str(row.get("source_kind") or "unknown"),
                "source_ref": str(row.get("source_ref") or "unknown"),
                "recorded_at": str(row.get("recorded_at") or "") or None,
                "event_id": str(row.get("event_id") or "") or None,
                "evidence_refs": [f"policy_gate:{str(row.get('policy_gate_id') or 'unknown')}"],
            }
        )

    for row in contradictions:
        reason_code = str(row.get("reason_code") or "")
        if reason_code in {"stop_ship_reset_integrity", "stop_ship_runtime_state_authority"}:
            incident_class = "session_integrity"
        elif reason_code in {"stop_ship_bridge_residue_persistence", "stop_ship_bridge_output_governance"}:
            incident_class = "residue_contamination"
        else:
            continue
        incidents.append(
            {
                "incident_class": incident_class,
                "severity": str(row.get("severity") or "high"),
                "summary": str(row.get("summary") or reason_code),
                "item_ref": str(row.get("contradiction_id") or "contradiction"),
                "source_kind": "contradiction_record",
                "source_ref": reason_code,
                "recorded_at": str(row.get("last_seen_at") or row.get("first_seen_at") or "") or None,
                "contradiction_id": str(row.get("contradiction_id") or "") or None,
                "related_event_ids": [
                    item
                    for item in (
                        str(row.get("first_event_id") or "") or None,
                        str(row.get("last_event_id") or "") or None,
                    )
                    if item
                ],
                "evidence_refs": [f"contradiction:{str(row.get('contradiction_id') or 'unknown')}"],
            }
        )

    for row in guard_rows:
        incidents.append(
            {
                "incident_class": "resume_risk_intercepted",
                "severity": "info",
                "summary": "Sparse runtime-state overwrite was intercepted and merged with richer state.",
                "item_ref": str(row.get("guard_record_id") or "resume-guard"),
                "source_kind": str(row.get("component") or "runtime_state"),
                "source_ref": str(row.get("state_key") or "unknown"),
                "recorded_at": str(row.get("created_at") or "") or None,
                "evidence_refs": [f"resume_guard:{str(row.get('guard_record_id') or 'unknown')}"],
            }
        )

    for row in memory_events:
        incidents.append(
            {
                "incident_class": "memory_contract_drift",
                "severity": str(row.get("severity") or "high"),
                "summary": str(row.get("summary") or "Memory event violated the Builder memory role contract."),
                "item_ref": str(row.get("event_id") or row.get("request_id") or "memory-contract"),
                "source_kind": "memory_orchestrator",
                "source_ref": str(row.get("reason") or row.get("event_type") or "memory_contract"),
                "recorded_at": str(row.get("created_at") or "") or None,
                "event_id": str(row.get("event_id") or "") or None,
                "evidence_refs": [f"memory_event:{str(row.get('event_id') or 'unknown')}"],
            }
        )
    return incidents


def _recent_memory_contract_events(state_db: StateDB) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_type in (
        "memory_write_requested",
        "memory_write_succeeded",
        "memory_write_abstained",
        "memory_read_succeeded",
        "memory_read_abstained",
    ):
        rows.extend(
            [
                event
                for event in latest_events_by_type(state_db, event_type=event_type, limit=100)
                if str(event.get("component") or "") == "memory_orchestrator"
            ]
        )

    incidents: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for row in rows:
        event_id = str(row.get("event_id") or "")
        if event_id and event_id in seen_event_ids:
            continue
        facts = row.get("facts_json") or {}
        if not isinstance(facts, dict):
            continue
        event_type = str(row.get("event_type") or "")
        violation_reason: str | None = None
        if event_type == "memory_write_requested":
            observations = facts.get("observations")
            if isinstance(observations, list):
                for observation in observations:
                    if not isinstance(observation, dict):
                        continue
                    violation_reason = memory_contract_reason(
                        memory_role=observation.get("memory_role"),
                        operation=str(observation.get("operation") or ""),
                        allow_unknown=False,
                    )
                    if violation_reason:
                        break
        else:
            reason = str(facts.get("reason") or "")
            if "operation" in facts:
                allow_unknown = int(facts.get("accepted_count") or 0) == 0
                effective_role = effective_memory_role(
                    facts.get("memory_role"),
                    allow_unknown=allow_unknown,
                    provenance=row.get("provenance_json"),
                )
                violation_reason = persisted_memory_contract_reason(
                    reason=reason,
                    raw_memory_role=facts.get("memory_role"),
                    effective_role=effective_role,
                    operation=str(facts.get("operation") or ""),
                    allow_unknown=allow_unknown,
                )
            elif "method" in facts:
                allow_unknown = int(facts.get("record_count") or 0) == 0
                effective_role = effective_memory_role(
                    facts.get("memory_role"),
                    allow_unknown=allow_unknown,
                    provenance=row.get("provenance_json"),
                )
                violation_reason = persisted_memory_contract_reason(
                    reason=reason,
                    raw_memory_role=facts.get("memory_role"),
                    effective_role=effective_role,
                    method=str(facts.get("method") or ""),
                    allow_unknown=allow_unknown,
                )
            elif is_memory_contract_reason(reason):
                violation_reason = reason
        if not violation_reason:
            continue
        if event_id:
            seen_event_ids.add(event_id)
        incidents.append(
            {
                "event_id": event_id or None,
                "request_id": str(row.get("request_id") or "") or None,
                "event_type": event_type,
                "created_at": str(row.get("created_at") or "") or None,
                "severity": "high",
                "reason": violation_reason,
                "summary": "Memory event violated the Builder memory role contract.",
            }
        )
    return incidents


def build_observer_packets(state_db: StateDB) -> list[dict[str, Any]]:
    packets = _derive_observer_packets(state_db)
    _sync_observer_packet_records(state_db, packets=packets)
    return packets


def export_observer_packet_bundle(
    state_db: StateDB,
    *,
    write_path: str | Path | None = None,
    packet_kind: str | None = None,
    active_only: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    build_observer_packets(state_db)
    packets = recent_observer_packet_records(
        state_db,
        limit=max(limit, 1),
        packet_kind=packet_kind,
        active_only=active_only,
    )
    counts_by_kind: dict[str, int] = {}
    for packet in packets:
        kind = str(packet.get("packet_kind") or "unknown")
        counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1
    payload = {
        "generated_at": utc_now_iso(),
        "active_only": active_only,
        "packet_kind_filter": packet_kind,
        "packet_count": len(packets),
        "counts_by_kind": counts_by_kind,
        "packets": packets,
    }
    if write_path is not None:
        destination = Path(write_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        payload["write_path"] = str(destination)
    return payload


def _derive_observer_packets(state_db: StateDB) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    digest_inputs: list[dict[str, Any]] = []
    for incident in _collect_observer_incidents(state_db):
        self_observation = _build_self_observation_packet(incident)
        packets.append(self_observation)
        digest_inputs.append({"incident": incident, "self_observation": self_observation})

        incident_report = _build_incident_report_packet(
            incident,
            related_packet_ids=[self_observation["packet_id"]],
        )
        packets.append(incident_report)

        repair_plan = _build_repair_plan_packet(
            incident,
            related_packet_ids=[self_observation["packet_id"], incident_report["packet_id"]],
        )
        packets.append(repair_plan)

        security_advisory = _build_security_advisory_packet(
            incident,
            related_packet_ids=[self_observation["packet_id"], incident_report["packet_id"]],
        )
        if security_advisory is not None:
            packets.append(security_advisory)

    reflection_digest = _build_reflection_digest_packet(digest_inputs=digest_inputs, packets=packets)
    if reflection_digest is not None:
        packets.append(reflection_digest)

    packets.sort(
        key=lambda item: (
            _observer_packet_sort_order(str(item.get("severity") or "")),
            str(item.get("created_at") or ""),
            str(item.get("packet_id") or ""),
        ),
        reverse=True,
    )
    return packets


def build_self_observation_packets(state_db: StateDB) -> list[dict[str, Any]]:
    return [packet for packet in build_observer_packets(state_db) if str(packet.get("packet_kind") or "") == "self_observation"]


def _sync_observer_packet_records(
    state_db: StateDB,
    *,
    packets: list[dict[str, Any]],
) -> None:
    seen_packet_ids = [str(packet.get("packet_id") or "") for packet in packets if str(packet.get("packet_id") or "")]
    sync_time = utc_now_iso()
    with state_db.connect() as conn:
        for packet in packets:
            packet_id = str(packet.get("packet_id") or "")
            if not packet_id:
                continue
            source_incident_class = None
            source_item_ref = None
            source_ref = None
            content = packet.get("content") or {}
            if isinstance(content, dict):
                observed_facts = content.get("observed_facts") or {}
                if isinstance(observed_facts, dict):
                    source_incident_class = str(observed_facts.get("incident_class") or "") or None
                    source_item_ref = str(observed_facts.get("item_ref") or "") or None
                    source_ref = str(observed_facts.get("source_ref") or "") or None
            conn.execute(
                """
                INSERT INTO observer_packet_records(
                    packet_id,
                    packet_kind,
                    target_surface,
                    created_at,
                    created_by,
                    evidence_lane,
                    severity,
                    confidence,
                    status,
                    summary,
                    owner_target,
                    source_incident_class,
                    source_item_ref,
                    source_ref,
                    active,
                    first_recorded_at,
                    last_seen_at,
                    archived_at,
                    evidence_json,
                    related_event_ids_json,
                    related_packet_ids_json,
                    contradiction_ids_json,
                    content_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(packet_id) DO UPDATE SET
                    packet_kind = excluded.packet_kind,
                    target_surface = excluded.target_surface,
                    created_at = excluded.created_at,
                    created_by = excluded.created_by,
                    evidence_lane = excluded.evidence_lane,
                    severity = excluded.severity,
                    confidence = excluded.confidence,
                    status = excluded.status,
                    summary = excluded.summary,
                    owner_target = excluded.owner_target,
                    source_incident_class = excluded.source_incident_class,
                    source_item_ref = excluded.source_item_ref,
                    source_ref = excluded.source_ref,
                    active = 1,
                    last_seen_at = excluded.last_seen_at,
                    archived_at = NULL,
                    evidence_json = excluded.evidence_json,
                    related_event_ids_json = excluded.related_event_ids_json,
                    related_packet_ids_json = excluded.related_packet_ids_json,
                    contradiction_ids_json = excluded.contradiction_ids_json,
                    content_json = excluded.content_json
                """,
                (
                    packet_id,
                    str(packet.get("packet_kind") or "unknown"),
                    str(packet.get("target_surface") or "spark_intelligence_builder"),
                    str(packet.get("created_at") or sync_time),
                    str(packet.get("created_by") or "builder_core"),
                    str(packet.get("evidence_lane") or "realworld_validated"),
                    str(packet.get("severity") or "medium"),
                    str(packet.get("confidence") or "medium"),
                    str(packet.get("status") or "open"),
                    str(packet.get("summary") or "observer packet"),
                    str(packet.get("owner_target") or "operator"),
                    source_incident_class,
                    source_item_ref,
                    source_ref,
                    sync_time,
                    sync_time,
                    json.dumps(list(packet.get("evidence_refs") or []), ensure_ascii=True),
                    json.dumps(list(packet.get("related_event_ids") or []), ensure_ascii=True),
                    json.dumps(list(packet.get("related_packet_ids") or []), ensure_ascii=True),
                    json.dumps(list(packet.get("contradiction_ids") or []), ensure_ascii=True),
                    json.dumps(dict(packet.get("content") or {}), ensure_ascii=True, sort_keys=True),
                ),
            )
        if seen_packet_ids:
            placeholders = ",".join("?" for _ in seen_packet_ids)
            conn.execute(
                f"""
                UPDATE observer_packet_records
                SET active = 0,
                    archived_at = ?,
                    last_seen_at = CASE WHEN active = 1 THEN ? ELSE last_seen_at END
                WHERE active = 1
                  AND packet_id NOT IN ({placeholders})
                """,
                (sync_time, sync_time, *seen_packet_ids),
            )
        else:
            conn.execute(
                """
                UPDATE observer_packet_records
                SET active = 0,
                    archived_at = ?,
                    last_seen_at = CASE WHEN active = 1 THEN ? ELSE last_seen_at END
                WHERE active = 1
                """,
                (sync_time, sync_time),
            )


def _build_self_observation_packet(incident: dict[str, Any]) -> dict[str, Any]:
    incident_class = str(incident.get("incident_class") or "unknown")
    item_ref = str(incident.get("item_ref") or "observer-incident")
    severity = str(incident.get("severity") or "medium")
    status = "watching" if severity == "info" else "open"
    return _observer_packet_base(
        incident,
        packet_kind="self_observation",
        created_by="builder_core",
        confidence="high",
        status=status,
        owner_target="builder_core" if severity == "info" else "operator",
        summary=str(incident.get("summary") or incident_class),
        related_packet_ids=[],
        content={
            "observation_type": _observer_observation_type(incident_class),
            "observed_facts": {
                "incident_class": incident_class,
                "item_ref": item_ref,
                "source_kind": str(incident.get("source_kind") or "unknown"),
                "source_ref": str(incident.get("source_ref") or "unknown"),
            },
            "comparison_basis": "typed observer incident classification",
            "hypothesis": None,
        },
    )


def _build_incident_report_packet(
    incident: dict[str, Any],
    *,
    related_packet_ids: list[str],
) -> dict[str, Any]:
    incident_class = str(incident.get("incident_class") or "unknown")
    contradiction_id = str(incident.get("contradiction_id") or "")
    status = "watching" if str(incident.get("severity") or "") == "info" else "open"
    return _observer_packet_base(
        incident,
        packet_kind="incident_report",
        created_by="builder_core",
        confidence="low" if contradiction_id else "medium",
        status=status,
        owner_target="operator",
        summary=f"Incident report scaffold for {incident_class}.",
        related_packet_ids=related_packet_ids,
        content={
            "issue_type": _observer_issue_type(incident_class),
            "suspected_cause": f"Possible {incident_class.replace('_', ' ')} indicated by typed observer evidence.",
            "suspected_cause_confidence": "low",
            "impact": {
                "blast_radius": _observer_blast_radius(incident_class),
                "user_visible": _observer_user_visible(incident_class),
            },
            "contradiction_summary": {
                "has_active_contradiction": bool(contradiction_id),
                "contradiction_note": (
                    f"Active contradiction {contradiction_id} remains open."
                    if contradiction_id
                    else "No active contradiction is currently attached."
                ),
            },
            "next_checks": _observer_next_checks(incident_class),
            "hypothesis_basis": "typed observer incident classification",
        },
    )


def _build_repair_plan_packet(
    incident: dict[str, Any],
    *,
    related_packet_ids: list[str],
) -> dict[str, Any]:
    incident_class = str(incident.get("incident_class") or "unknown")
    severity = str(incident.get("severity") or "medium")
    return _observer_packet_base(
        incident,
        packet_kind="repair_plan",
        created_by="builder_core",
        confidence="medium" if severity in {"critical", "high"} else "low",
        status="proposed_fix" if severity != "info" else "watching",
        owner_target="operator",
        summary=f"Repair-plan scaffold for {incident_class}.",
        related_packet_ids=related_packet_ids,
        content={
            "recommended_fix": _observer_recommended_fix(incident_class),
            "fix_scope": _observer_fix_scope(incident_class),
            "autonomy_class": "proposal_only",
            "verification_step": _observer_verification_steps(incident_class),
            "rollback_condition": _observer_rollback_conditions(incident_class),
            "expected_outcome": _observer_expected_outcomes(incident_class),
        },
    )


def _build_security_advisory_packet(
    incident: dict[str, Any],
    *,
    related_packet_ids: list[str],
) -> dict[str, Any] | None:
    incident_class = str(incident.get("incident_class") or "unknown")
    advisory_type = _observer_security_advisory_type(incident_class)
    if advisory_type is None:
        return None
    source_kind = str(incident.get("source_kind") or "typed_observer_incident")
    return _observer_packet_base(
        incident,
        packet_kind="security_advisory",
        created_by="builder_core",
        confidence="medium",
        status="escalated" if str(incident.get("severity") or "") in {"critical", "high"} else "open",
        owner_target="operator",
        summary=f"Security advisory scaffold for {incident_class}.",
        related_packet_ids=related_packet_ids,
        content={
            "advisory_type": advisory_type,
            "claim": _observer_security_claim(incident_class),
            "required_evidence_sources": ["typed_observer_incident", source_kind],
            "falsifiability": _observer_security_falsifiability(incident_class),
            "recommendation_class": _observer_security_recommendation_class(incident_class),
        },
    )


def _build_reflection_digest_packet(
    *,
    digest_inputs: list[dict[str, Any]],
    packets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not digest_inputs:
        return None
    related_packet_ids = [str(packet.get("packet_id") or "") for packet in packets if str(packet.get("packet_id") or "")]
    evidence_refs: list[str] = []
    contradiction_ids: list[str] = []
    what_broke: list[str] = []
    what_improved: list[str] = []
    what_is_uncertain: list[str] = []
    next_best_action: list[str] = []
    latest_created_at = utc_now_iso()
    highest_severity = "low"
    for item in digest_inputs:
        incident = item["incident"]
        self_observation = item["self_observation"]
        incident_class = str(incident.get("incident_class") or "unknown")
        summary = str(self_observation.get("summary") or incident_class)
        severity = str(incident.get("severity") or "medium")
        if _observer_packet_sort_order(severity) > _observer_packet_sort_order(highest_severity):
            highest_severity = severity
        latest_created_at = max(latest_created_at, str(self_observation.get("created_at") or latest_created_at))
        evidence_refs.extend(list(self_observation.get("evidence_refs") or []))
        contradiction_ids.extend(list(self_observation.get("contradiction_ids") or []))
        next_best_action.extend(_observer_next_checks(incident_class)[:1])
        if incident_class == "resume_risk_intercepted":
            what_improved.append(summary)
        else:
            what_broke.append(summary)
        if contradiction_ids or severity in {"high", "critical"}:
            what_is_uncertain.append(f"Root-cause diagnosis for {incident_class} is still provisional.")
    evidence_refs = list(dict.fromkeys(evidence_refs))
    contradiction_ids = list(dict.fromkeys(contradiction_ids))
    what_broke = list(dict.fromkeys(what_broke))[:3]
    what_improved = list(dict.fromkeys(what_improved))[:3]
    what_is_uncertain = list(dict.fromkeys(what_is_uncertain))[:3]
    next_best_action = list(dict.fromkeys(next_best_action))[:3]
    packet_key = f"reflection_digest:{latest_created_at}:{len(digest_inputs)}"
    return {
        "packet_id": f"pkt:{payload_hash(packet_key)[:24]}",
        "packet_kind": "reflection_digest",
        "target_surface": "spark_intelligence_builder",
        "created_at": latest_created_at,
        "created_by": "builder_core",
        "evidence_lane": "realworld_validated",
        "severity": highest_severity,
        "confidence": "medium",
        "status": "watching",
        "summary": f"Reflection digest for {len(digest_inputs)} observer incident(s).",
        "evidence_refs": evidence_refs,
        "related_event_ids": [],
        "related_packet_ids": related_packet_ids,
        "contradiction_ids": contradiction_ids,
        "owner_target": "operator",
        "content": {
            "what_broke": what_broke,
            "what_improved": what_improved,
            "what_is_uncertain": what_is_uncertain or ["No new contradiction-linked uncertainty was recorded."],
            "next_best_action": next_best_action,
        },
    }


def _observer_packet_base(
    incident: dict[str, Any],
    *,
    packet_kind: str,
    created_by: str,
    confidence: str,
    status: str,
    owner_target: str,
    summary: str,
    related_packet_ids: list[str],
    content: dict[str, Any],
) -> dict[str, Any]:
    incident_class = str(incident.get("incident_class") or "unknown")
    item_ref = str(incident.get("item_ref") or "observer-incident")
    packet_key = f"{packet_kind}:{incident_class}:{item_ref}"
    related_event_ids = [
        item
        for item in (
            str(incident.get("event_id") or "") or None,
            *list(incident.get("related_event_ids") or []),
        )
        if item
    ]
    contradiction_ids = [item for item in (str(incident.get("contradiction_id") or "") or None,) if item]
    return {
        "packet_id": f"pkt:{payload_hash(packet_key)[:24]}",
        "packet_kind": packet_kind,
        "target_surface": "spark_intelligence_builder",
        "created_at": str(incident.get("recorded_at") or utc_now_iso()),
        "created_by": created_by,
        "evidence_lane": "realworld_validated",
        "severity": str(incident.get("severity") or "medium"),
        "confidence": confidence,
        "status": status,
        "summary": summary,
        "evidence_refs": list(incident.get("evidence_refs") or [f"observer_incident:{item_ref}"]),
        "related_event_ids": related_event_ids,
        "related_packet_ids": related_packet_ids,
        "contradiction_ids": contradiction_ids,
        "owner_target": owner_target,
        "content": content,
    }


def _observer_observation_type(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "security_signal",
        "promotion_contamination": "contract_drift",
        "memory_contract_drift": "contract_drift",
        "secret_boundary": "security_signal",
        "session_integrity": "regression",
        "residue_contamination": "contract_drift",
        "resume_risk_intercepted": "regression",
    }
    return mapping.get(incident_class, "regression")


def _build_observer_packet_panel(state_db: StateDB) -> dict[str, Any]:
    packets = build_observer_packets(state_db)
    counts_by_status: dict[str, int] = {}
    counts_by_kind: dict[str, int] = {}
    for packet in packets:
        status = str(packet.get("status") or "unknown")
        packet_kind = str(packet.get("packet_kind") or "unknown")
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        counts_by_kind[packet_kind] = counts_by_kind.get(packet_kind, 0) + 1
    return {
        "counts": {
            "total": len(packets),
            "open": counts_by_status.get("open", 0),
            "watching": counts_by_status.get("watching", 0),
            "escalated": counts_by_status.get("escalated", 0),
            "proposed_fix": counts_by_status.get("proposed_fix", 0),
            "distinct_kinds": len(counts_by_kind),
        },
        "counts_by_status": counts_by_status,
        "counts_by_kind": counts_by_kind,
        "recent_packets": packets[:10],
    }


def _build_observer_handoff_panel(state_db: StateDB) -> dict[str, Any]:
    rows = recent_observer_handoff_records(state_db, limit=20)
    counts_by_status: dict[str, int] = {}
    counts_by_chip: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        chip_key = str(row.get("chip_key") or "unknown")
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        counts_by_chip[chip_key] = counts_by_chip.get(chip_key, 0) + 1
    problematic = (
        counts_by_status.get("failed", 0)
        + counts_by_status.get("blocked", 0)
        + counts_by_status.get("stalled", 0)
    )
    return {
        "counts": {
            "total": len(rows),
            "completed": counts_by_status.get("completed", 0),
            "failed": counts_by_status.get("failed", 0),
            "blocked": counts_by_status.get("blocked", 0),
            "stalled": counts_by_status.get("stalled", 0),
            "problematic": problematic,
            "distinct_chips": len(counts_by_chip),
        },
        "counts_by_status": counts_by_status,
        "counts_by_chip": counts_by_chip,
        "recent_handoffs": rows[:10],
    }


def _observer_issue_type(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "provenance_gap",
        "promotion_contamination": "promotion_gate_drift",
        "memory_contract_drift": "memory_role_contract_drift",
        "secret_boundary": "secret_exposure_risk",
        "session_integrity": "session_integrity_regression",
        "residue_contamination": "residue_persistence",
        "resume_risk_intercepted": "resume_state_overwrite_risk",
    }
    return mapping.get(incident_class, "observer_incident")


def _observer_blast_radius(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "builder_and_downstream_lineage",
        "promotion_contamination": "builder_memory_promotion",
        "memory_contract_drift": "builder_memory_boundary",
        "secret_boundary": "operator_and_delivery_security",
        "session_integrity": "session_state_and_resume_flow",
        "residue_contamination": "bridge_and_delivery_surfaces",
        "resume_risk_intercepted": "runtime_state_only",
    }
    return mapping.get(incident_class, "builder_core")


def _observer_user_visible(incident_class: str) -> bool:
    return incident_class in {"secret_boundary", "residue_contamination", "session_integrity"}


def _observer_next_checks(incident_class: str) -> list[str]:
    mapping = {
        "provenance_contamination": [
            "verify typed provenance refs on the affected mutation path",
            "confirm high-risk delivery or bridge events preserve source lineage",
        ],
        "promotion_contamination": [
            "confirm keepability and promotion disposition stayed aligned",
            "review the typed memory-lane record for the blocked artifact",
        ],
        "memory_contract_drift": [
            "inspect the violating memory read or write event in Watchtower memory-shadow history",
            "confirm downstream memory_role values match the Builder-facing operation contract",
        ],
        "secret_boundary": [
            "inspect recent secret-boundary policy violations",
            "confirm blocked output was quarantined before delivery",
        ],
        "session_integrity": [
            "review reset-sensitive registry coverage for the affected state key",
            "confirm richer resume state was preserved after the latest write",
        ],
        "residue_contamination": [
            "inspect raw-vs-mutated refs for the affected bridge or delivery event",
            "confirm residue-bearing artifacts did not cross promotion gates",
        ],
        "resume_risk_intercepted": [
            "confirm the richer runtime-state fields were preserved after interception",
            "review whether the incoming write should be narrowed or suppressed upstream",
        ],
    }
    return mapping.get(incident_class, ["review the linked observer incident and verify typed evidence refs"])


def _observer_recommended_fix(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "Restore explicit provenance refs on the affected mutation path and re-run the contract check.",
        "promotion_contamination": "Tighten the promotion gate so the artifact stays blocked until classification and lane labels agree.",
        "memory_contract_drift": "Repair the downstream memory_role mapping so Builder reads and writes fail closed only on real contract drift.",
        "secret_boundary": "Keep the boundary blocked, review the source text, and harden the relevant output path before retrying.",
        "session_integrity": "Expand reset-sensitive coverage or merge logic on the affected runtime-state path before another resume cycle.",
        "residue_contamination": "Preserve raw-vs-mutated refs and prevent residue-bearing artifacts from being promoted.",
        "resume_risk_intercepted": "Keep merge protection enabled and narrow the sparse overwrite path that triggered the guard.",
    }
    return mapping.get(incident_class, "Review the incident and prepare a bounded repair before mutating state.")


def _observer_fix_scope(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "mutation_lineage",
        "promotion_contamination": "promotion_gate_policy",
        "memory_contract_drift": "memory_contract_boundary",
        "secret_boundary": "delivery_and_boundary_controls",
        "session_integrity": "runtime_state_integrity",
        "residue_contamination": "bridge_output_governance",
        "resume_risk_intercepted": "runtime_state_guardrails",
    }
    return mapping.get(incident_class, "builder_core")


def _observer_verification_steps(incident_class: str) -> list[str]:
    return [
        _observer_next_checks(incident_class)[0],
        "re-run Watchtower and confirm the packet stays resolved or downgraded.",
    ]


def _observer_rollback_conditions(incident_class: str) -> list[str]:
    return [
        f"If the {incident_class.replace('_', ' ')} signal persists after the change, revert the local fix and escalate for review."
    ]


def _observer_expected_outcomes(incident_class: str) -> list[str]:
    mapping = {
        "resume_risk_intercepted": ["Resume writes preserve richer runtime-state fields without new integrity incidents."],
        "secret_boundary": ["Secret-like output stays blocked and quarantined before delivery."],
    }
    return mapping.get(incident_class, ["The linked observer incident no longer appears as an open Watchtower packet."])


def _observer_security_advisory_type(incident_class: str) -> str | None:
    mapping = {
        "provenance_contamination": "hardening_gap",
        "secret_boundary": "secret_exposure",
        "residue_contamination": "unsafe_automation",
    }
    return mapping.get(incident_class)


def _observer_security_claim(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "The current mutation path likely lacks sufficient provenance hardening.",
        "secret_boundary": "Recent evidence suggests secret-like material reached a delivery boundary and required blocking.",
        "residue_contamination": "A guarded output path likely still allows residue-bearing material to approach delivery or promotion.",
    }
    return mapping.get(incident_class, "Recent observer evidence indicates a security-relevant hardening gap.")


def _observer_security_falsifiability(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "If all linked high-risk events already carry typed source lineage, downgrade this advisory.",
        "secret_boundary": "If the blocked output cannot reach any delivery path, downgrade this advisory.",
        "residue_contamination": "If raw-vs-mutated refs are present and promotion gates stay closed, downgrade this advisory.",
    }
    return mapping.get(incident_class, "If linked evidence does not reproduce the condition, downgrade this advisory.")


def _observer_security_recommendation_class(incident_class: str) -> str:
    mapping = {
        "provenance_contamination": "hardening",
        "secret_boundary": "block",
        "residue_contamination": "investigate",
    }
    return mapping.get(incident_class, "investigate")


def _observer_packet_sort_order(severity: str) -> int:
    return {
        "critical": 5,
        "high": 4,
        "medium": 3,
        "warning": 3,
        "low": 2,
        "info": 1,
    }.get(severity, 0)


def _build_memory_shadow_panel(state_db: StateDB) -> dict[str, Any]:
    write_requested = _typed_component_events(
        state_db,
        event_types=("memory_write_requested",),
        component="memory_orchestrator",
        limit=200,
    )
    write_results = _typed_component_events(
        state_db,
        event_types=("memory_write_succeeded", "memory_write_abstained"),
        component="memory_orchestrator",
        limit=200,
    )
    read_requested = _typed_component_events(
        state_db,
        event_types=("memory_read_requested",),
        component="memory_orchestrator",
        limit=200,
    )
    read_results = _typed_component_events(
        state_db,
        event_types=("memory_read_succeeded", "memory_read_abstained"),
        component="memory_orchestrator",
        limit=200,
    )

    accepted = 0
    rejected = 0
    skipped = 0
    shadow_only_reads = 0
    read_hits = 0
    abstention_reasons: dict[str, int] = {}
    memory_roles: dict[str, int] = {}
    contract_violations = 0
    invalid_role_events = 0

    for event in write_results + read_results:
        facts = event.get("facts_json") or {}
        if not isinstance(facts, dict):
            continue
        accepted += int(facts.get("accepted_count") or 0)
        rejected += int(facts.get("rejected_count") or 0)
        skipped += int(facts.get("skipped_count") or 0)
        if int(facts.get("record_count") or 0) > 0:
            read_hits += 1
        if bool(facts.get("shadow_only")):
            shadow_only_reads += 1
        reason = str(facts.get("reason") or "")
        if reason:
            abstention_reasons[reason] = abstention_reasons.get(reason, 0) + 1
        raw_role = effective_memory_role(
            facts.get("memory_role"),
            allow_unknown=True,
            provenance=event.get("provenance_json"),
        )
        role = normalize_memory_role(raw_role, allow_unknown=True)
        if role:
            memory_roles[role] = memory_roles.get(role, 0) + 1
        violation = None
        if str(facts.get("operation") or ""):
            allow_unknown = int(facts.get("accepted_count") or 0) == 0
            violation = persisted_memory_contract_reason(
                reason=reason,
                raw_memory_role=facts.get("memory_role"),
                effective_role=raw_role,
                operation=str(facts.get("operation") or ""),
                allow_unknown=allow_unknown,
            )
        elif str(facts.get("method") or ""):
            allow_unknown = int(facts.get("record_count") or 0) == 0
            violation = persisted_memory_contract_reason(
                reason=reason,
                raw_memory_role=facts.get("memory_role"),
                effective_role=raw_role,
                method=str(facts.get("method") or ""),
                allow_unknown=allow_unknown,
            )
        elif is_memory_contract_reason(reason):
            violation = reason
        if violation:
            contract_violations += 1
            if violation == "invalid_memory_role":
                invalid_role_events += 1

    return {
        "counts": {
            "write_requests": len(write_requested),
            "write_results": len(write_results),
            "accepted_observations": accepted,
            "rejected_observations": rejected,
            "skipped_observations": skipped,
            "read_requests": len(read_requested),
            "read_results": len(read_results),
            "read_hits": read_hits,
            "shadow_only_reads": shadow_only_reads,
            "contract_violations": contract_violations,
            "invalid_role_events": invalid_role_events,
        },
        "memory_role_mix": [
            {"memory_role": role, "count": count}
            for role, count in sorted(memory_roles.items())
        ],
        "abstention_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(abstention_reasons.items())
        ],
        "recent_writes": write_results[:10],
        "recent_reads": read_results[:10],
    }


def _build_ingress_health_dimension(*, health_facts: dict[str, Any]) -> dict[str, Any]:
    last_ingress = health_facts.get("last_ingress_at")
    if not last_ingress:
        return {"state": "unknown", "detail": "No ingress intent has been recorded yet."}
    return {"state": "healthy", "detail": f"Last ingress at {last_ingress}."}


def _build_execution_health_dimension(*, execution_panel: dict[str, Any]) -> dict[str, Any]:
    counts = execution_panel.get("counts") or {}
    if int(counts.get("intent_without_dispatch") or 0) > 0:
        return {
            "state": "execution_impaired",
            "detail": f"{counts['intent_without_dispatch']} intent(s) lack dispatch proof.",
        }
    if int(counts.get("dispatch_without_result_closure") or 0) > 0:
        return {
            "state": "execution_impaired",
            "detail": f"{counts['dispatch_without_result_closure']} dispatch(es) lack result closure.",
        }
    if int(counts.get("dispatches_started") or 0) == 0 and int(counts.get("tool_results_received") or 0) == 0:
        return {"state": "unknown", "detail": "No execution lineage has been recorded yet."}
    return {"state": "healthy", "detail": "Intent, dispatch, and result closure are aligned."}


def _build_delivery_health_dimension(
    *,
    delivery_panel: dict[str, Any],
    health_facts: dict[str, Any],
) -> dict[str, Any]:
    counts = delivery_panel.get("counts") or {}
    unacked = int(health_facts.get("current_unacked_delivery_count") or 0)
    failed = int(counts.get("failed_deliveries") or 0)
    generated = int(counts.get("generated_replies") or 0)
    acked = int(counts.get("acked_deliveries") or 0)
    attempted = int(counts.get("attempted_deliveries") or 0)
    if unacked > 0:
        return {
            "state": "delivery_impaired",
            "detail": f"{unacked} delivery attempt(s) remain unacked.",
        }
    if failed > 0 and acked == 0:
        return {
            "state": "delivery_impaired",
            "detail": f"{failed} delivery failure(s) were recorded without successful acknowledgments.",
        }
    if generated == 0 and attempted == 0:
        return {"state": "unknown", "detail": "No delivery lineage has been recorded yet."}
    return {"state": "healthy", "detail": "Generated, attempted, and acked delivery truth is aligned."}


def _build_scheduler_freshness_dimension(*, background_panel: dict[str, Any]) -> dict[str, Any]:
    stalled = int(background_panel.get("current_stalled_run_count") or 0)
    open_runs = int(background_panel.get("current_open_background_runs") or 0)
    lag_seconds = background_panel.get("freshness_lag_seconds")
    threshold = int(background_panel.get("freshness_threshold_seconds") or WATCHTOWER_BACKGROUND_STALE_SECONDS)
    if stalled > 0:
        return {"state": "stalled", "detail": f"{stalled} background run(s) are marked stalled."}
    if open_runs > 0:
        return {"state": "stalled", "detail": f"{open_runs} background run(s) remain open."}
    if isinstance(lag_seconds, int) and lag_seconds > threshold:
        return {
            "state": "degraded",
            "detail": f"Background freshness lag is {lag_seconds}s, above the {threshold}s threshold.",
        }
    if background_panel.get("last_run_opened_at") or background_panel.get("last_run_closed_at"):
        return {"state": "healthy", "detail": "Background freshness is within the current operator-driven window."}
    return {"state": "unknown", "detail": "No background run activity has been recorded yet."}


def _build_environment_parity_dimension(*, environment_panel: dict[str, Any]) -> dict[str, Any]:
    mismatches = environment_panel.get("mismatch_fields") or []
    if mismatches:
        return {"state": "parity_broken", "detail": mismatches[0]}
    latest_surfaces = environment_panel.get("latest_surfaces") or {}
    if len(latest_surfaces) < 2:
        return {"state": "unknown", "detail": "Not enough runtime surfaces have emitted snapshots yet."}
    return {"state": "healthy", "detail": "Runtime surfaces agree on critical environment fields."}


def _derive_watchtower_top_level_state(dimensions: dict[str, dict[str, Any]]) -> str:
    states = {str(value.get("state") or "unknown") for value in dimensions.values()}
    if "parity_broken" in states:
        return "parity_broken"
    if "stalled" in states:
        return "stalled"
    if "execution_impaired" in states:
        return "execution_impaired"
    if "delivery_impaired" in states:
        return "delivery_impaired"
    if "degraded" in states:
        return "degraded"
    return "healthy" if "healthy" in states else "unknown"


def _typed_component_events(
    state_db: StateDB,
    *,
    event_types: tuple[str, ...],
    component: str,
    limit: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event_type in event_types:
        events.extend(
            [
                event
                for event in latest_events_by_type(state_db, event_type=event_type, limit=limit)
                if str(event.get("component") or "") == component
            ]
        )
    return events


def _latest_event_created_at(
    state_db: StateDB,
    *,
    event_types: tuple[str, ...],
    components: tuple[str, ...] | None = None,
) -> str | None:
    placeholders = ", ".join("?" for _ in event_types)
    query = f"""
        SELECT created_at
        FROM builder_events
        WHERE event_type IN ({placeholders})
    """
    params: list[Any] = list(event_types)
    if components:
        component_placeholders = ", ".join("?" for _ in components)
        query += f" AND component IN ({component_placeholders})"
        params.extend(components)
    query += " ORDER BY created_at DESC, event_id DESC LIMIT 1"
    with state_db.connect() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    return str(row["created_at"]) if row and row["created_at"] else None


def _latest_background_event_created_at(
    state_db: StateDB,
    *,
    event_types: tuple[str, ...],
) -> str | None:
    placeholders = ", ".join("?" for _ in event_types)
    with state_db.connect() as conn:
        row = conn.execute(
            f"""
            SELECT created_at
            FROM builder_events
            WHERE event_type IN ({placeholders})
              AND (
                    component = 'jobs_tick'
                    OR json_extract(facts_json, '$.run_kind') LIKE 'job:%'
                  )
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            tuple(event_types),
        ).fetchone()
    return str(row["created_at"]) if row and row["created_at"] else None


def _latest_snapshot_created_at(state_db: StateDB) -> str | None:
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT created_at
            FROM runtime_environment_snapshots
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row["created_at"]) if row and row["created_at"] else None


def _count_rows(state_db: StateDB, query: str, params: tuple[Any, ...] = ()) -> int:
    with state_db.connect() as conn:
        row = conn.execute(query, params).fetchone()
    return int((row["c"] if row else 0) or 0)


def _freshness_lag_seconds(last_opened_at: Any, last_closed_at: Any) -> int | None:
    latest = _latest_iso_timestamp(last_opened_at, last_closed_at)
    if latest is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - latest).total_seconds()))


def _latest_iso_timestamp(*values: Any) -> datetime | None:
    parsed: list[datetime] = []
    for value in values:
        timestamp = _parse_iso_datetime(value)
        if timestamp is not None:
            parsed.append(timestamp)
    if not parsed:
        return None
    return max(parsed)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
