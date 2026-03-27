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


def _trust_level_from_keepability(value: Any) -> str:
    keepability = str(value or "")
    if keepability in {"user_preference_ephemeral"}:
        return "user_preference_ephemeral"
    if keepability in {"ephemeral_context", "operator_debug_only"}:
        return "ephemeral_context"
    return "context_only"
