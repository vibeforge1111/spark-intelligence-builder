from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from spark_intelligence.observability.store import record_event, utc_now_iso
from spark_intelligence.state.db import StateDB


OPEN_PENDING_TASK_STATUSES = {"open", "pending", "blocked", "timed_out", "interrupted"}


@dataclass(frozen=True)
class PendingTaskRecord:
    pending_task_id: str
    task_key: str
    status: str
    human_id: str | None
    agent_id: str | None
    session_id: str | None
    original_request: str
    target_repo: str | None
    target_component: str | None
    command: str | None
    mission_id: str | None
    timeout_point: str | None
    last_evidence: str | None
    next_retry_step: str | None
    source_event_id: str | None
    evidence: dict[str, Any]
    created_at: str
    updated_at: str
    closed_at: str | None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PENDING_TASK_STATUSES and not self.closed_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_task_id": self.pending_task_id,
            "task_key": self.task_key,
            "status": self.status,
            "human_id": self.human_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "original_request": self.original_request,
            "target_repo": self.target_repo,
            "target_component": self.target_component,
            "command": self.command,
            "mission_id": self.mission_id,
            "timeout_point": self.timeout_point,
            "last_evidence": self.last_evidence,
            "next_retry_step": self.next_retry_step,
            "source_event_id": self.source_event_id,
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "is_open": self.is_open,
        }

    def to_resume_text(self) -> str:
        lines = [
            f"Pending task: {self.original_request}",
            f"Status: {self.status}",
        ]
        if self.target_repo or self.target_component:
            target_parts = [part for part in (self.target_repo, self.target_component) if part]
            lines.append(f"Target: {' / '.join(target_parts)}")
        if self.command:
            lines.append(f"Command: {self.command}")
        if self.mission_id:
            lines.append(f"Mission id: {self.mission_id}")
        if self.timeout_point:
            lines.append(f"Interrupted at: {self.timeout_point}")
        if self.last_evidence:
            lines.append(f"Last verified evidence: {self.last_evidence}")
        if self.next_retry_step:
            lines.append(f"Next retry step: {self.next_retry_step}")
        return "\n".join(lines)


def upsert_pending_task(
    state_db: StateDB,
    *,
    task_key: str,
    original_request: str,
    status: str = "open",
    human_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    target_repo: str | None = None,
    target_component: str | None = None,
    command: str | None = None,
    mission_id: str | None = None,
    timeout_point: str | None = None,
    last_evidence: str | None = None,
    next_retry_step: str | None = None,
    source_event_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    actor_id: str = "pending_task_ledger",
) -> PendingTaskRecord:
    normalized_key = _required_text(task_key, "task_key")
    normalized_request = _required_text(original_request, "original_request")
    normalized_status = _normalize_status(status)
    now = utc_now_iso()
    evidence_json = json.dumps(dict(evidence or {}), sort_keys=True, default=str)
    pending_task_id = f"pending-task-{uuid4().hex[:12]}"
    closed_at = now if normalized_status in {"closed", "completed", "cancelled"} else None
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_task_records(
                pending_task_id, task_key, status, human_id, agent_id, session_id,
                original_request, target_repo, target_component, command, mission_id,
                timeout_point, last_evidence, next_retry_step, source_event_id,
                evidence_json, created_at, updated_at, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_key) DO UPDATE SET
                status = excluded.status,
                human_id = COALESCE(excluded.human_id, pending_task_records.human_id),
                agent_id = COALESCE(excluded.agent_id, pending_task_records.agent_id),
                session_id = COALESCE(excluded.session_id, pending_task_records.session_id),
                original_request = excluded.original_request,
                target_repo = COALESCE(excluded.target_repo, pending_task_records.target_repo),
                target_component = COALESCE(excluded.target_component, pending_task_records.target_component),
                command = COALESCE(excluded.command, pending_task_records.command),
                mission_id = COALESCE(excluded.mission_id, pending_task_records.mission_id),
                timeout_point = COALESCE(excluded.timeout_point, pending_task_records.timeout_point),
                last_evidence = COALESCE(excluded.last_evidence, pending_task_records.last_evidence),
                next_retry_step = COALESCE(excluded.next_retry_step, pending_task_records.next_retry_step),
                source_event_id = COALESCE(excluded.source_event_id, pending_task_records.source_event_id),
                evidence_json = excluded.evidence_json,
                updated_at = excluded.updated_at,
                closed_at = excluded.closed_at
            """,
            (
                pending_task_id,
                normalized_key,
                normalized_status,
                human_id,
                agent_id,
                session_id,
                normalized_request,
                target_repo,
                target_component,
                command,
                mission_id,
                timeout_point,
                last_evidence,
                next_retry_step,
                source_event_id,
                evidence_json,
                now,
                now,
                closed_at,
            ),
        )
    record = get_pending_task(state_db, task_key=normalized_key)
    if record is None:
        raise RuntimeError(f"pending_task_upsert_failed:{normalized_key}")
    record_event(
        state_db,
        event_type="pending_task_recorded",
        component="workflow_recovery",
        summary=f"Spark recorded pending task `{normalized_key}` for workflow recovery.",
        request_id=normalized_key,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
        reason_code="pending_task_upserted",
        facts=record.to_dict(),
        provenance={"source_kind": "pending_task_ledger", "source_ref": normalized_key},
    )
    return record


def record_pending_task_timeout(
    state_db: StateDB,
    *,
    task_key: str,
    original_request: str,
    timeout_point: str,
    last_evidence: str,
    next_retry_step: str,
    human_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    target_repo: str | None = None,
    target_component: str | None = None,
    command: str | None = None,
    mission_id: str | None = None,
    source_event_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    actor_id: str = "pending_task_ledger",
) -> PendingTaskRecord:
    return upsert_pending_task(
        state_db,
        task_key=task_key,
        original_request=original_request,
        status="timed_out",
        human_id=human_id,
        agent_id=agent_id,
        session_id=session_id,
        target_repo=target_repo,
        target_component=target_component,
        command=command,
        mission_id=mission_id,
        timeout_point=timeout_point,
        last_evidence=last_evidence,
        next_retry_step=next_retry_step,
        source_event_id=source_event_id,
        evidence=evidence,
        actor_id=actor_id,
    )


def close_pending_task(
    state_db: StateDB,
    *,
    task_key: str,
    completion_summary: str,
    status: str = "completed",
    actor_id: str = "pending_task_ledger",
) -> PendingTaskRecord:
    existing = get_pending_task(state_db, task_key=task_key)
    if existing is None:
        raise ValueError(f"unknown_pending_task:{task_key}")
    evidence = dict(existing.evidence)
    evidence["completion_summary"] = completion_summary
    record = upsert_pending_task(
        state_db,
        task_key=existing.task_key,
        original_request=existing.original_request,
        status=status,
        human_id=existing.human_id,
        agent_id=existing.agent_id,
        session_id=existing.session_id,
        target_repo=existing.target_repo,
        target_component=existing.target_component,
        command=existing.command,
        mission_id=existing.mission_id,
        timeout_point=existing.timeout_point,
        last_evidence=completion_summary,
        next_retry_step=existing.next_retry_step,
        source_event_id=existing.source_event_id,
        evidence=evidence,
        actor_id=actor_id,
    )
    record_event(
        state_db,
        event_type="pending_task_closed",
        component="workflow_recovery",
        summary=f"Spark closed pending task `{existing.task_key}`.",
        request_id=existing.task_key,
        session_id=existing.session_id,
        human_id=existing.human_id,
        agent_id=existing.agent_id,
        actor_id=actor_id,
        reason_code="pending_task_closed",
        facts=record.to_dict(),
        provenance={"source_kind": "pending_task_ledger", "source_ref": existing.task_key},
    )
    return record


def get_pending_task(state_db: StateDB, *, task_key: str) -> PendingTaskRecord | None:
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM pending_task_records
            WHERE task_key = ?
            """,
            (str(task_key or "").strip(),),
        ).fetchone()
    return _row_to_pending_task(row) if row else None


def latest_pending_tasks(
    state_db: StateDB,
    *,
    human_id: str | None = None,
    status: str | None = None,
    open_only: bool = True,
    limit: int = 20,
) -> list[PendingTaskRecord]:
    query = """
        SELECT *
        FROM pending_task_records
    """
    clauses: list[str] = []
    params: list[Any] = []
    if human_id:
        clauses.append("human_id = ?")
        params.append(human_id)
    if status:
        clauses.append("status = ?")
        params.append(_normalize_status(status))
    elif open_only:
        placeholders = ", ".join("?" for _ in OPEN_PENDING_TASK_STATUSES)
        clauses.append(f"status IN ({placeholders})")
        params.extend(sorted(OPEN_PENDING_TASK_STATUSES))
        clauses.append("closed_at IS NULL")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC, pending_task_id DESC LIMIT ?"
    params.append(int(limit))
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [record for row in rows if (record := _row_to_pending_task(row)) is not None]


def build_pending_task_resume_context(
    state_db: StateDB,
    *,
    human_id: str | None = None,
    limit: int = 5,
) -> str:
    tasks = latest_pending_tasks(state_db, human_id=human_id, open_only=True, limit=limit)
    if not tasks:
        return "No open pending tasks are recorded."
    blocks = []
    for index, task in enumerate(tasks, start=1):
        blocks.append(f"{index}. {task.to_resume_text()}")
    return "\n\n".join(blocks)


def _row_to_pending_task(row: Any) -> PendingTaskRecord | None:
    if row is None:
        return None
    payload = dict(row)
    evidence = _json_dict(payload.get("evidence_json"))
    return PendingTaskRecord(
        pending_task_id=str(payload.get("pending_task_id") or ""),
        task_key=str(payload.get("task_key") or ""),
        status=str(payload.get("status") or ""),
        human_id=_optional_text(payload.get("human_id")),
        agent_id=_optional_text(payload.get("agent_id")),
        session_id=_optional_text(payload.get("session_id")),
        original_request=str(payload.get("original_request") or ""),
        target_repo=_optional_text(payload.get("target_repo")),
        target_component=_optional_text(payload.get("target_component")),
        command=_optional_text(payload.get("command")),
        mission_id=_optional_text(payload.get("mission_id")),
        timeout_point=_optional_text(payload.get("timeout_point")),
        last_evidence=_optional_text(payload.get("last_evidence")),
        next_retry_step=_optional_text(payload.get("next_retry_step")),
        source_event_id=_optional_text(payload.get("source_event_id")),
        evidence=evidence,
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        closed_at=_optional_text(payload.get("closed_at")),
    )


def _json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _required_text(value: str | None, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    return cleaned


def _optional_text(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _normalize_status(status: str | None) -> str:
    cleaned = str(status or "open").strip().casefold()
    return cleaned or "open"
