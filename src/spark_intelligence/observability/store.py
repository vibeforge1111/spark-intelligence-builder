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
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
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
    event_type = "run_closed" if status == "closed" else "run_stalled"
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
                utc_now_iso(),
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
                _json_or_none(provenance),
                _json_or_none(facts),
            ),
        )
        conn.commit()
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
            "before_hash": before_hash,
            "after_hash": after_hash,
        },
        provenance={"request_source": request_source, "actor_type": actor_type},
    )
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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_or_none(payload: Any) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _row_to_dict(row: Any) -> dict[str, Any]:
    payload = dict(row)
    for key in ("provenance_json", "facts_json", "env_refs_json", "details_json", "before_summary_json", "after_summary_json", "rollback_payload_json"):
        value = payload.get(key)
        if not value:
            continue
        try:
            payload[key] = json.loads(value)
        except json.JSONDecodeError:
            payload[key] = value
    return payload
