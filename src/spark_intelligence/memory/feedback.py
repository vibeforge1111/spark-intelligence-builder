from __future__ import annotations

import json
from collections import Counter
from typing import Any

from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


ALLOWED_FEEDBACK_VERDICTS: tuple[str, ...] = (
    "good",
    "bad",
    "ugly",
    "wrong",
    "missing",
    "useful",
    "not_useful",
)

REVIEWABLE_MEMORY_EVENT_TYPES: tuple[str, ...] = (
    "memory_write_succeeded",
    "memory_write_abstained",
    "memory_write_failed",
    "memory_promotion_evaluated",
    "memory_read_succeeded",
    "memory_session_summary_written",
    "memory_daily_summary_written",
    "memory_project_summary_written",
    "policy_gate_blocked",
)


def normalize_feedback_verdict(value: str) -> str:
    verdict = str(value or "").strip().lower().replace("-", "_")
    if verdict not in ALLOWED_FEEDBACK_VERDICTS:
        allowed = ", ".join(ALLOWED_FEEDBACK_VERDICTS)
        raise ValueError(f"Unsupported memory feedback verdict {value!r}. Use one of: {allowed}.")
    return verdict


def record_memory_feedback(
    *,
    state_db: StateDB,
    verdict: str,
    note: str,
    human_id: str | None = None,
    agent_id: str | None = None,
    target_event_id: str | None = None,
    target_trace_ref: str | None = None,
    feedback_surface: str = "cli",
    feedback_scope: str = "memory_quality",
    expected_outcome: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    actor_id: str = "operator",
) -> dict[str, Any]:
    normalized_verdict = normalize_feedback_verdict(verdict)
    clean_note = str(note or "").strip()
    if not clean_note:
        raise ValueError("Memory feedback note is required.")
    clean_target_event_id = str(target_event_id or "").strip() or None
    clean_target_trace_ref = str(target_trace_ref or "").strip() or None
    target_event = _load_event_by_id(state_db, clean_target_event_id) if clean_target_event_id else None
    facts = {
        "feedback_verdict": normalized_verdict,
        "feedback_note": clean_note,
        "feedback_scope": str(feedback_scope or "memory_quality"),
        "feedback_surface": str(feedback_surface or "cli"),
        "target_event_id": clean_target_event_id,
        "target_trace_ref": clean_target_trace_ref,
        "target_event_type": target_event.get("event_type") if target_event else None,
        "target_summary": target_event.get("summary") if target_event else None,
        "expected_outcome": str(expected_outcome).strip() if expected_outcome else None,
    }
    event_id = record_event(
        state_db,
        event_type="memory_feedback_recorded",
        component="memory_feedback",
        summary=f"Memory feedback recorded: {normalized_verdict}.",
        request_id=request_id,
        trace_ref=clean_target_trace_ref,
        session_id=session_id or (target_event.get("session_id") if target_event else None),
        human_id=human_id or (target_event.get("human_id") if target_event else None),
        agent_id=agent_id or (target_event.get("agent_id") if target_event else None),
        actor_id=actor_id,
        evidence_lane="operator_feedback",
        status="recorded",
        reason_code=normalized_verdict,
        provenance={
            "source_kind": "operator_feedback",
            "source_ref": feedback_surface or "cli",
            "target_event_id": clean_target_event_id,
            "target_trace_ref": clean_target_trace_ref,
        },
        facts={key: value for key, value in facts.items() if value is not None},
    )
    return {
        "view": "memory_feedback_recorded",
        "event_id": event_id,
        "verdict": normalized_verdict,
        "target_event_id": clean_target_event_id,
        "target_trace_ref": clean_target_trace_ref,
        "note": clean_note,
        "target": _feedback_target_preview(target_event) if target_event else None,
    }


def build_memory_feedback_review_payload(
    *,
    state_db: StateDB,
    human_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    normalized_limit = max(1, min(int(limit or 50), 200))
    feedback_rows = _load_feedback_events(
        state_db,
        human_id=human_id,
        agent_id=agent_id,
        limit=normalized_limit,
    )
    target_event_ids = {
        str(row["facts"].get("target_event_id"))
        for row in feedback_rows
        if str(row["facts"].get("target_event_id") or "").strip()
    }
    target_events = _load_events_by_ids(state_db, sorted(target_event_ids))
    review_candidates = _load_reviewable_memory_events(
        state_db,
        human_id=human_id,
        agent_id=agent_id,
        limit=max(normalized_limit * 3, 25),
    )
    unreviewed = [
        _memory_decision_preview(row)
        for row in review_candidates
        if str(row.get("event_id") or "") not in target_event_ids
        and str(row.get("event_type") or "") != "memory_feedback_recorded"
    ][: min(normalized_limit, 25)]
    recent_feedback = [
        _feedback_preview(row, target_events.get(str(row["facts"].get("target_event_id") or "")))
        for row in feedback_rows
    ]
    verdict_counts = Counter(row["verdict"] for row in recent_feedback)
    targeted_count = sum(1 for row in recent_feedback if row.get("target_event_id"))
    return {
        "view": "memory_feedback_review",
        "scope": {"human_id": human_id, "agent_id": agent_id, "limit": normalized_limit},
        "counts": {
            "total_feedback": len(recent_feedback),
            "targeted_feedback": targeted_count,
            "general_feedback": len(recent_feedback) - targeted_count,
            **{verdict: int(verdict_counts.get(verdict, 0)) for verdict in ALLOWED_FEEDBACK_VERDICTS},
        },
        "recent_feedback": recent_feedback,
        "review_queue": unreviewed,
    }


def build_memory_feedback_summary(
    *,
    state_db: StateDB,
    human_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    payload = build_memory_feedback_review_payload(
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
        limit=limit,
    )
    return {
        "counts": payload["counts"],
        "recent_feedback": payload["recent_feedback"][:10],
        "review_queue": payload["review_queue"][:10],
    }


def _load_feedback_events(
    state_db: StateDB,
    *,
    human_id: str | None,
    agent_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    conditions = ["event_type = ?"]
    params: list[Any] = ["memory_feedback_recorded"]
    if human_id:
        conditions.append("human_id = ?")
        params.append(str(human_id))
    if agent_id:
        conditions.append("agent_id = ?")
        params.append(str(agent_id))
    params.append(int(limit))
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, trace_ref, session_id, human_id, agent_id,
                   actor_id, status, summary, reason_code, facts_json, provenance_json, created_at
            FROM builder_events
            WHERE {" AND ".join(f"({condition})" for condition in conditions)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [_event_row(row) for row in rows]


def _load_reviewable_memory_events(
    state_db: StateDB,
    *,
    human_id: str | None,
    agent_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in REVIEWABLE_MEMORY_EVENT_TYPES)
    conditions = [f"event_type IN ({placeholders})"]
    params: list[Any] = list(REVIEWABLE_MEMORY_EVENT_TYPES)
    if human_id:
        conditions.append("human_id = ?")
        params.append(str(human_id))
    if agent_id:
        conditions.append("agent_id = ?")
        params.append(str(agent_id))
    params.append(int(limit))
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, trace_ref, session_id, human_id, agent_id,
                   actor_id, status, summary, reason_code, facts_json, provenance_json, created_at
            FROM builder_events
            WHERE {" AND ".join(f"({condition})" for condition in conditions)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [_event_row(row) for row in rows]


def _load_event_by_id(state_db: StateDB, event_id: str | None) -> dict[str, Any] | None:
    if not event_id:
        return None
    events = _load_events_by_ids(state_db, [event_id])
    return events.get(str(event_id))


def _load_events_by_ids(state_db: StateDB, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    cleaned = [str(event_id).strip() for event_id in event_ids if str(event_id or "").strip()]
    if not cleaned:
        return {}
    placeholders = ", ".join("?" for _ in cleaned)
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, trace_ref, session_id, human_id, agent_id,
                   actor_id, status, summary, reason_code, facts_json, provenance_json, created_at
            FROM builder_events
            WHERE event_id IN ({placeholders})
            """,
            tuple(cleaned),
        ).fetchall()
    events = [_event_row(row) for row in rows]
    return {str(row.get("event_id")): row for row in events}


def _event_row(row: Any) -> dict[str, Any]:
    event = {key: row[key] for key in row.keys()}
    event["facts"] = _json_object(event.pop("facts_json", None))
    event["provenance"] = _json_object(event.pop("provenance_json", None))
    return event


def _feedback_preview(row: dict[str, Any], target_event: dict[str, Any] | None) -> dict[str, Any]:
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    verdict = normalize_feedback_verdict(str(facts.get("feedback_verdict") or row.get("reason_code") or "good"))
    return {
        "event_id": row.get("event_id"),
        "created_at": row.get("created_at"),
        "human_id": row.get("human_id"),
        "agent_id": row.get("agent_id"),
        "verdict": verdict,
        "note": facts.get("feedback_note"),
        "feedback_surface": facts.get("feedback_surface"),
        "feedback_scope": facts.get("feedback_scope"),
        "expected_outcome": facts.get("expected_outcome"),
        "target_event_id": facts.get("target_event_id"),
        "target_trace_ref": facts.get("target_trace_ref") or row.get("trace_ref"),
        "target": _feedback_target_preview(target_event) if target_event else None,
    }


def _memory_decision_preview(row: dict[str, Any]) -> dict[str, Any]:
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    return {
        "event_id": row.get("event_id"),
        "created_at": row.get("created_at"),
        "event_type": row.get("event_type"),
        "human_id": row.get("human_id"),
        "agent_id": row.get("agent_id"),
        "status": row.get("status"),
        "reason": facts.get("promotion_reason_code") or facts.get("reason") or row.get("reason_code"),
        "predicate": facts.get("predicate") or facts.get("target_predicate") or _first_predicate(facts),
        "movement_hint": _movement_hint(row),
        "summary": row.get("summary"),
        "source": {
            "kind": provenance.get("source_kind") or row.get("component"),
            "ref": provenance.get("source_ref") or row.get("request_id") or row.get("event_id"),
        },
    }


def _feedback_target_preview(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    preview = _memory_decision_preview(row)
    return {
        "event_id": preview["event_id"],
        "event_type": preview["event_type"],
        "movement_hint": preview["movement_hint"],
        "predicate": preview["predicate"],
        "reason": preview["reason"],
        "summary": preview["summary"],
    }


def _movement_hint(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "")
    status = str(row.get("status") or "")
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    disposition = str(facts.get("promotion_disposition") or "")
    if event_type == "memory_read_succeeded":
        return "retrieved"
    if event_type in {"memory_session_summary_written", "memory_daily_summary_written", "memory_project_summary_written"}:
        return "summarized"
    if event_type == "policy_gate_blocked" or status == "blocked" or disposition == "blocked":
        return "blocked"
    if event_type == "memory_promotion_evaluated":
        return "promoted" if disposition.startswith("promote_") else "promotion_review"
    if event_type == "memory_write_succeeded":
        return "saved"
    if event_type in {"memory_write_abstained", "memory_write_failed"}:
        return "blocked"
    return "review"


def _first_predicate(facts: dict[str, Any]) -> str | None:
    observations = facts.get("observations")
    if isinstance(observations, list) and observations:
        first = observations[0] if isinstance(observations[0], dict) else {}
        if first.get("predicate"):
            return str(first.get("predicate"))
    predicates = facts.get("predicates")
    if isinstance(predicates, list) and predicates:
        return str(predicates[0])
    return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
