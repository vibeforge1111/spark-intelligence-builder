from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from spark_intelligence.observability.store import record_event, utc_now_iso
from spark_intelligence.state.db import StateDB


ACTIVE_PROCEDURAL_LESSON_STATUSES = {"active", "candidate", "confirmed"}


@dataclass(frozen=True)
class ProceduralLessonRecord:
    lesson_id: str
    lesson_key: str
    lesson_kind: str
    status: str
    trigger_pattern: str
    corrective_action: str
    failure_summary: str | None
    target_repo: str | None
    target_component: str | None
    applies_to_component: str | None
    source_event_id: str | None
    source_task_key: str | None
    confidence: float
    occurrence_count: int
    evidence: dict[str, Any]
    created_at: str
    updated_at: str
    last_seen_at: str
    retired_at: str | None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_PROCEDURAL_LESSON_STATUSES and not self.retired_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "lesson_key": self.lesson_key,
            "lesson_kind": self.lesson_kind,
            "status": self.status,
            "trigger_pattern": self.trigger_pattern,
            "corrective_action": self.corrective_action,
            "failure_summary": self.failure_summary,
            "target_repo": self.target_repo,
            "target_component": self.target_component,
            "applies_to_component": self.applies_to_component,
            "source_event_id": self.source_event_id,
            "source_task_key": self.source_task_key,
            "confidence": self.confidence,
            "occurrence_count": self.occurrence_count,
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
            "retired_at": self.retired_at,
            "is_active": self.is_active,
        }

    def to_context_text(self) -> str:
        parts = [
            f"Procedural lesson: {self.lesson_kind}",
            f"Trigger: {self.trigger_pattern}",
            f"Do next time: {self.corrective_action}",
        ]
        if self.failure_summary:
            parts.append(f"Failure learned from: {self.failure_summary}")
        if self.target_repo or self.target_component or self.applies_to_component:
            target = " / ".join(
                part
                for part in (
                    self.target_repo,
                    self.target_component,
                    self.applies_to_component,
                )
                if part
            )
            parts.append(f"Applies to: {target}")
        parts.append(f"Confidence: {self.confidence:.2f}; occurrences: {self.occurrence_count}")
        return "\n".join(parts)


def upsert_procedural_lesson(
    state_db: StateDB,
    *,
    lesson_key: str,
    lesson_kind: str,
    trigger_pattern: str,
    corrective_action: str,
    status: str = "active",
    failure_summary: str | None = None,
    target_repo: str | None = None,
    target_component: str | None = None,
    applies_to_component: str | None = None,
    source_event_id: str | None = None,
    source_task_key: str | None = None,
    confidence: float = 0.7,
    evidence: dict[str, Any] | None = None,
    actor_id: str = "procedural_memory",
) -> ProceduralLessonRecord:
    normalized_key = _required_text(lesson_key, "lesson_key")
    normalized_kind = _required_text(lesson_kind, "lesson_kind")
    normalized_trigger = _required_text(trigger_pattern, "trigger_pattern")
    normalized_action = _required_text(corrective_action, "corrective_action")
    normalized_status = _normalize_status(status)
    now = utc_now_iso()
    lesson_id = f"lesson-{uuid4().hex[:12]}"
    retired_at = now if normalized_status in {"retired", "superseded"} else None
    evidence_json = json.dumps(dict(evidence or {}), sort_keys=True, ensure_ascii=True, default=str)
    bounded_confidence = max(0.0, min(1.0, float(confidence)))

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO procedural_lesson_records(
                lesson_id, lesson_key, lesson_kind, status, trigger_pattern,
                corrective_action, failure_summary, target_repo, target_component,
                applies_to_component, source_event_id, source_task_key, confidence,
                occurrence_count, evidence_json, created_at, updated_at, last_seen_at,
                retired_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(lesson_key) DO UPDATE SET
                lesson_kind = excluded.lesson_kind,
                status = excluded.status,
                trigger_pattern = excluded.trigger_pattern,
                corrective_action = excluded.corrective_action,
                failure_summary = COALESCE(excluded.failure_summary, procedural_lesson_records.failure_summary),
                target_repo = COALESCE(excluded.target_repo, procedural_lesson_records.target_repo),
                target_component = COALESCE(excluded.target_component, procedural_lesson_records.target_component),
                applies_to_component = COALESCE(excluded.applies_to_component, procedural_lesson_records.applies_to_component),
                source_event_id = COALESCE(excluded.source_event_id, procedural_lesson_records.source_event_id),
                source_task_key = COALESCE(excluded.source_task_key, procedural_lesson_records.source_task_key),
                confidence = MAX(procedural_lesson_records.confidence, excluded.confidence),
                occurrence_count = procedural_lesson_records.occurrence_count + 1,
                evidence_json = excluded.evidence_json,
                updated_at = excluded.updated_at,
                last_seen_at = excluded.last_seen_at,
                retired_at = excluded.retired_at
            """,
            (
                lesson_id,
                normalized_key,
                normalized_kind,
                normalized_status,
                normalized_trigger,
                normalized_action,
                failure_summary,
                target_repo,
                target_component,
                applies_to_component,
                source_event_id,
                source_task_key,
                bounded_confidence,
                evidence_json,
                now,
                now,
                now,
                retired_at,
            ),
        )

    record = get_procedural_lesson(state_db, lesson_key=normalized_key)
    if record is None:
        raise RuntimeError(f"procedural_lesson_upsert_failed:{normalized_key}")
    record_event(
        state_db,
        event_type="procedural_lesson_recorded",
        component="workflow_recovery",
        summary=f"Spark recorded procedural lesson `{normalized_key}`.",
        request_id=source_task_key or normalized_key,
        actor_id=actor_id,
        reason_code=f"procedural_lesson:{normalized_kind}",
        facts=record.to_dict(),
        provenance={"source_kind": "procedural_memory", "source_ref": normalized_key},
    )
    return record


def record_target_resolution_lesson(
    state_db: StateDB,
    *,
    requested_target: str,
    resolved_target: str,
    corrective_action: str = "Confirm the target repo/component before building or rating work.",
    source_task_key: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> ProceduralLessonRecord:
    return upsert_procedural_lesson(
        state_db,
        lesson_key=f"target_resolution:{_key_fragment(requested_target)}:{_key_fragment(resolved_target)}",
        lesson_kind="target_resolution",
        trigger_pattern=f"User request targets {requested_target}, but resolver selects or drifts toward {resolved_target}.",
        corrective_action=corrective_action,
        failure_summary=f"Target resolution drift from {requested_target} to {resolved_target}.",
        target_repo=requested_target,
        target_component=resolved_target,
        applies_to_component="repo_resolution",
        source_task_key=source_task_key,
        confidence=0.85,
        evidence=evidence,
    )


def record_wrong_build_target_lesson(
    state_db: StateDB,
    *,
    expected_repo: str,
    actual_repo: str,
    artifact: str,
    source_task_key: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> ProceduralLessonRecord:
    return upsert_procedural_lesson(
        state_db,
        lesson_key=f"wrong_build_target:{_key_fragment(expected_repo)}:{_key_fragment(actual_repo)}:{_key_fragment(artifact)}",
        lesson_kind="wrong_build_target",
        trigger_pattern=f"Build artifact {artifact} lands in {actual_repo} while the request expected {expected_repo}.",
        corrective_action="Stop and rebind the build to the confirmed repo before continuing; do not leave standalone artifacts orphaned.",
        failure_summary=f"{artifact} was built in {actual_repo} instead of {expected_repo}.",
        target_repo=expected_repo,
        target_component=artifact,
        applies_to_component="builder_target_binding",
        source_task_key=source_task_key,
        confidence=0.9,
        evidence=evidence,
    )


def record_bad_self_review_lesson(
    state_db: StateDB,
    *,
    reviewed_subject: str,
    missing_evidence: list[str],
    source_task_key: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> ProceduralLessonRecord:
    missing = ", ".join(str(item) for item in missing_evidence)
    return upsert_procedural_lesson(
        state_db,
        lesson_key=f"bad_self_review:{_key_fragment(reviewed_subject)}",
        lesson_kind="bad_self_review",
        trigger_pattern=f"Spark rates or summarizes {reviewed_subject} without inspecting required build evidence.",
        corrective_action="Inspect target repo, diff, route/demo state, and tests before rating build quality.",
        failure_summary=f"Self-review missed required evidence: {missing}.",
        target_component=reviewed_subject,
        applies_to_component="quality_review",
        source_task_key=source_task_key,
        confidence=0.88,
        evidence={"missing_evidence": list(missing_evidence), **(evidence or {})},
    )


def record_timeout_recovery_lesson(
    state_db: StateDB,
    *,
    task_key: str,
    timeout_point: str,
    next_retry_step: str,
    evidence: dict[str, Any] | None = None,
) -> ProceduralLessonRecord:
    return upsert_procedural_lesson(
        state_db,
        lesson_key=f"timeout_recovery:{_key_fragment(task_key)}",
        lesson_kind="timeout_recovery",
        trigger_pattern=f"Work times out or resumes after interruption at {timeout_point}.",
        corrective_action=f"Load the pending-task ledger, state the last evidence, then continue with: {next_retry_step}",
        failure_summary=f"Task {task_key} was interrupted at {timeout_point}.",
        applies_to_component="workflow_recovery",
        source_task_key=task_key,
        confidence=0.82,
        evidence=evidence,
    )


def get_procedural_lesson(state_db: StateDB, *, lesson_key: str) -> ProceduralLessonRecord | None:
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM procedural_lesson_records
            WHERE lesson_key = ?
            """,
            (str(lesson_key or "").strip(),),
        ).fetchone()
    return _row_to_procedural_lesson(row) if row else None


def latest_procedural_lessons(
    state_db: StateDB,
    *,
    lesson_kind: str | None = None,
    applies_to_component: str | None = None,
    active_only: bool = True,
    limit: int = 20,
) -> list[ProceduralLessonRecord]:
    query = """
        SELECT *
        FROM procedural_lesson_records
    """
    clauses: list[str] = []
    params: list[Any] = []
    if lesson_kind:
        clauses.append("lesson_kind = ?")
        params.append(_normalize_status(lesson_kind))
    if applies_to_component:
        clauses.append("applies_to_component = ?")
        params.append(applies_to_component)
    if active_only:
        placeholders = ", ".join("?" for _ in ACTIVE_PROCEDURAL_LESSON_STATUSES)
        clauses.append(f"status IN ({placeholders})")
        params.extend(sorted(ACTIVE_PROCEDURAL_LESSON_STATUSES))
        clauses.append("retired_at IS NULL")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC, lesson_id DESC LIMIT ?"
    params.append(int(limit))
    with state_db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [record for row in rows if (record := _row_to_procedural_lesson(row)) is not None]


def build_procedural_lesson_context(
    state_db: StateDB,
    *,
    lesson_kind: str | None = None,
    applies_to_component: str | None = None,
    limit: int = 5,
) -> str:
    lessons = latest_procedural_lessons(
        state_db,
        lesson_kind=lesson_kind,
        applies_to_component=applies_to_component,
        active_only=True,
        limit=limit,
    )
    if not lessons:
        return "No active procedural lessons are recorded."
    return "\n\n".join(f"{index}. {lesson.to_context_text()}" for index, lesson in enumerate(lessons, start=1))


def retire_procedural_lesson(
    state_db: StateDB,
    *,
    lesson_key: str,
    reason: str,
    actor_id: str = "procedural_memory",
) -> ProceduralLessonRecord:
    existing = get_procedural_lesson(state_db, lesson_key=lesson_key)
    if existing is None:
        raise ValueError(f"unknown_procedural_lesson:{lesson_key}")
    evidence = dict(existing.evidence)
    evidence["retirement_reason"] = reason
    now = utc_now_iso()
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE procedural_lesson_records
            SET status = 'retired',
                evidence_json = ?,
                updated_at = ?,
                retired_at = ?
            WHERE lesson_key = ?
            """,
            (json.dumps(evidence, sort_keys=True, ensure_ascii=True, default=str), now, now, existing.lesson_key),
        )
    record = get_procedural_lesson(state_db, lesson_key=existing.lesson_key)
    if record is None:
        raise RuntimeError(f"procedural_lesson_retire_failed:{existing.lesson_key}")
    record_event(
        state_db,
        event_type="procedural_lesson_retired",
        component="workflow_recovery",
        summary=f"Spark retired procedural lesson `{existing.lesson_key}`.",
        request_id=existing.source_task_key or existing.lesson_key,
        actor_id=actor_id,
        reason_code="procedural_lesson_retired",
        facts=record.to_dict(),
        provenance={"source_kind": "procedural_memory", "source_ref": existing.lesson_key},
    )
    return record


def _row_to_procedural_lesson(row: Any) -> ProceduralLessonRecord | None:
    if row is None:
        return None
    payload = dict(row)
    return ProceduralLessonRecord(
        lesson_id=str(payload.get("lesson_id") or ""),
        lesson_key=str(payload.get("lesson_key") or ""),
        lesson_kind=str(payload.get("lesson_kind") or ""),
        status=str(payload.get("status") or ""),
        trigger_pattern=str(payload.get("trigger_pattern") or ""),
        corrective_action=str(payload.get("corrective_action") or ""),
        failure_summary=_optional_text(payload.get("failure_summary")),
        target_repo=_optional_text(payload.get("target_repo")),
        target_component=_optional_text(payload.get("target_component")),
        applies_to_component=_optional_text(payload.get("applies_to_component")),
        source_event_id=_optional_text(payload.get("source_event_id")),
        source_task_key=_optional_text(payload.get("source_task_key")),
        confidence=float(payload.get("confidence") or 0.0),
        occurrence_count=int(payload.get("occurrence_count") or 0),
        evidence=_json_dict(payload.get("evidence_json")),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        last_seen_at=str(payload.get("last_seen_at") or ""),
        retired_at=_optional_text(payload.get("retired_at")),
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


def _normalize_status(value: str | None) -> str:
    cleaned = str(value or "").strip().casefold()
    return cleaned or "active"


def _key_fragment(value: str) -> str:
    cleaned = str(value or "").strip().casefold()
    chars = [char if char.isalnum() else "-" for char in cleaned]
    return "-".join("".join(chars).split("-"))[:80] or "unknown"
