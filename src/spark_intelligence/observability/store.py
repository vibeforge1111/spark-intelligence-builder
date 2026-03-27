from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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
}
NON_PROMOTABLE_DISPOSITIONS = {
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
        "contradictions": _build_contradiction_panel(state_db),
        "panels": {
            "config_authority": _build_config_authority_panel(state_db),
            "execution_lineage": execution_panel,
            "delivery_truth": delivery_panel,
            "background_freshness": background_panel,
            "environment_parity": environment_panel,
            "provenance_and_quarantine": _build_provenance_and_quarantine_panel(state_db),
            "memory_lane_hygiene": _build_memory_lane_hygiene_panel(state_db),
        },
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
        "provenance_json",
        "evidence_json",
        "facts_json",
        "env_refs_json",
        "details_json",
        "before_summary_json",
        "after_summary_json",
        "rollback_payload_json",
        "payload_json",
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
    keepability = str(facts.get("keepability") or "")
    promotion_disposition = _normalized_promotion_disposition(
        facts.get("promotion_disposition"),
        keepability,
    )
    if not keepability and not promotion_disposition:
        return
    artifact_lane = _artifact_lane_from_keepability(keepability)
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
            _promotion_target_lane(promotion_disposition),
            keepability or None,
            promotion_disposition or None,
            _promotion_record_status(promotion_disposition),
            reason_code,
            _json_or_none(
                {
                    "component": component,
                    "event_type": event_type,
                    "keepability": keepability or None,
                    "promotion_disposition": promotion_disposition or None,
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


def _artifact_lane_from_keepability(value: str) -> str:
    keepability = str(value or "")
    if keepability == "operator_debug_only":
        return "ops_transcripts"
    if keepability == "user_preference_ephemeral":
        return "user_history"
    if keepability == "durable_intelligence_memory":
        return "durable_intelligence_memory"
    return "execution_evidence"


def _promotion_target_lane(disposition: str) -> str | None:
    if not disposition:
        return None
    return "durable_intelligence_memory"


def _promotion_record_status(disposition: str) -> str:
    if disposition in NON_PROMOTABLE_DISPOSITIONS:
        return "blocked"
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
            "SELECT COUNT(*) AS c FROM run_registry WHERE status = 'stalled'",
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
            intent_without_dispatch += 1
            continue
        run_events = {event.get("event_type") for event in events_for_run(state_db, run_id=run_id)}
        if "dispatch_started" not in run_events and "dispatch_failed" not in run_events and "tool_result_received" not in run_events:
            intent_without_dispatch += 1
    for dispatch in dispatches:
        run_id = str(dispatch.get("run_id") or "")
        if not run_id:
            dispatch_without_result += 1
            continue
        run_events = {event.get("event_type") for event in events_for_run(state_db, run_id=run_id)}
        if "tool_result_received" not in run_events and "dispatch_failed" not in run_events:
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
    comparable_fields = (
        "provider_id",
        "provider_model",
        "provider_base_url",
        "provider_execution_transport",
        "runtime_root",
        "config_path",
        "python_executable",
    )
    snapshot_hashes = {
        surface: str(snapshot.get("config_hash") or "")
        for surface, snapshot in snapshots.items()
    }
    mismatch_fields: list[str] = []
    rows = list(snapshots.items())
    if rows:
        baseline_surface, baseline = rows[0]
        for surface, snapshot in rows[1:]:
            for field in comparable_fields:
                left = baseline.get(field)
                right = snapshot.get(field)
                if left and right and left != right:
                    mismatch_fields.append(
                        f"{baseline_surface}:{field}={left} != {surface}:{field}={right}"
                    )
    return {
        "snapshot_hashes": snapshot_hashes,
        "latest_surfaces": snapshots,
        "mismatch_fields": mismatch_fields,
        "current_mismatch_count": len(mismatch_fields),
    }


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
    lanes = {str(record.get("artifact_lane") or "") for record in lane_records}
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
            "reset_or_resume_integrity_incidents": len(resume_integrity_incidents),
        },
        "lane_labels_present": {
            "execution_evidence": "execution_evidence" in lanes,
            "durable_intelligence_memory": "durable_intelligence_memory" in lanes,
            "ops_transcripts": "ops_transcripts" in lanes,
            "user_history": "user_history" in lanes,
        },
        "recent_promotions": lane_records[:10],
        "recent_integrity_incidents": resume_integrity_incidents[:10],
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
