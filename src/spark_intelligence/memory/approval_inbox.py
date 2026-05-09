from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from spark_intelligence.observability.store import record_event, utc_now_iso
from spark_intelligence.self_awareness.event_producers import record_user_override_agent_event
from spark_intelligence.state.db import StateDB


MEMORY_APPROVAL_INBOX_SCHEMA_VERSION = "spark.memory_approval_inbox.v1"
MEMORY_APPROVAL_DECISION_EVENT = "memory_approval_decision"
MEMORY_CANDIDATE_EVENT_TYPES = (
    "memory_candidate_created",
    "memory_candidate_assessed",
    "memory_write_requested",
)
REVIEW_ACTIONS = (
    "approve",
    "edit",
    "reject",
    "save_as_project_fact",
    "save_as_personal_preference",
    "save_as_spark_doctrine",
)
REVIEWABLE_CANDIDATE_OUTCOMES = {"structured_evidence", "raw_episode", "belief_candidate"}
PENDING_APPROVAL_STATES = {"needs_review", "pending", "pending_review", "requires_approval"}

MemoryApprovalDecision = Literal[
    "approve",
    "edit",
    "reject",
    "save_as_project_fact",
    "save_as_personal_preference",
    "save_as_spark_doctrine",
]


@dataclass(frozen=True)
class MemoryApprovalInboxItem:
    item_id: str
    candidate_event_id: str
    source_event_type: str
    created_at: str | None
    status: str
    proposed_text: str
    memory_role: str
    target_scope: str
    recommended_action: str
    review_actions: tuple[str, ...] = REVIEW_ACTIONS
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    decision: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "candidate_event_id": self.candidate_event_id,
            "source_event_type": self.source_event_type,
            "created_at": self.created_at,
            "status": self.status,
            "proposed_text": self.proposed_text,
            "memory_role": self.memory_role,
            "target_scope": self.target_scope,
            "recommended_action": self.recommended_action,
            "review_actions": list(self.review_actions),
            "source_refs": list(self.source_refs),
            "reason": self.reason,
            "decision": self.decision,
        }


@dataclass(frozen=True)
class MemoryApprovalInboxReport:
    checked_at: str
    status_filter: str
    items: list[MemoryApprovalInboxItem]
    counts: dict[str, int]
    source_policy: str = (
        "Only explicit memory candidates or approval-gated write requests are shown; raw logs stay out of the inbox."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": MEMORY_APPROVAL_INBOX_SCHEMA_VERSION,
            "checked_at": self.checked_at,
            "status_filter": self.status_filter,
            "counts": dict(self.counts),
            "source_policy": self.source_policy,
            "items": [item.to_payload() for item in self.items],
        }

    def to_text(self) -> str:
        if not self.items:
            return "Memory approval inbox: clear."
        lines = [f"Memory approval inbox: {len(self.items)} item(s)."]
        for item in self.items[:8]:
            lines.append(
                f"- {item.status}: {item.target_scope} | {item.memory_role} | "
                f"{_compact(item.proposed_text, 120)}"
            )
        return "\n".join(lines)


def build_memory_approval_inbox(
    state_db: StateDB,
    *,
    status: str = "pending",
    limit: int = 20,
) -> MemoryApprovalInboxReport:
    candidates = _candidate_event_rows(state_db, scan_limit=max(50, limit * 5))
    decisions = _decision_by_candidate_event_id(state_db, scan_limit=500)
    items: list[MemoryApprovalInboxItem] = []
    counts = {"pending": 0, "decided": 0, "hidden_non_candidates": 0}
    for row in candidates:
        candidate = _item_from_candidate_event(row=row, decision=decisions.get(str(row.get("event_id") or "")))
        if candidate is None:
            counts["hidden_non_candidates"] += 1
            continue
        if candidate.decision is None:
            counts["pending"] += 1
        else:
            counts["decided"] += 1
        if status == "pending" and candidate.decision is not None:
            continue
        if status == "decided" and candidate.decision is None:
            continue
        items.append(candidate)
        if len(items) >= limit:
            break
    return MemoryApprovalInboxReport(
        checked_at=utc_now_iso(),
        status_filter=status,
        items=items,
        counts=counts,
    )


def record_memory_approval_decision(
    state_db: StateDB,
    *,
    candidate_event_id: str,
    decision: MemoryApprovalDecision,
    reason: str,
    edited_text: str | None = None,
    target_scope: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    actor_id: str = "memory_approval_inbox",
) -> str:
    normalized_decision = _normalize_decision(decision)
    decision_event_id = record_event(
        state_db,
        event_type=MEMORY_APPROVAL_DECISION_EVENT,
        component="memory_approval_inbox",
        summary=f"Memory approval inbox decision: {normalized_decision}.",
        request_id=request_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        reason_code=normalized_decision,
        facts={
            "schema_version": MEMORY_APPROVAL_INBOX_SCHEMA_VERSION,
            "candidate_event_id": str(candidate_event_id or "").strip(),
            "decision": normalized_decision,
            "reason": str(reason or "").strip(),
            "edited_text": str(edited_text or "").strip() or None,
            "target_scope": str(target_scope or "").strip() or None,
            "does_not_write_memory": True,
        },
        provenance={
            "source": "memory_approval_inbox",
            "candidate_event_id": str(candidate_event_id or "").strip(),
            "boundary": "decision_event_only_no_memory_write",
        },
    )
    record_user_override_agent_event(
        state_db,
        override_summary=f"Memory approval decision for {str(candidate_event_id or '').strip()}: {normalized_decision}.",
        corrected_route="memory_approval_inbox",
        request_id=request_id or decision_event_id,
        session_id=session_id or "",
        human_id=human_id or "",
        actor_id=actor_id,
    )
    return decision_event_id


def _candidate_event_rows(state_db: StateDB, *, scan_limit: int) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in MEMORY_CANDIDATE_EVENT_TYPES)
    params: list[Any] = [*MEMORY_CANDIDATE_EVENT_TYPES, max(1, int(scan_limit))]
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM builder_events
            WHERE event_type IN ({placeholders})
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def _decision_by_candidate_event_id(state_db: StateDB, *, scan_limit: int) -> dict[str, dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM builder_events
            WHERE event_type = ?
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (MEMORY_APPROVAL_DECISION_EVENT, max(1, int(scan_limit))),
        ).fetchall()
    decisions: dict[str, dict[str, Any]] = {}
    for row in (_event_row_to_dict(item) for item in rows):
        facts = row.get("facts_json") if isinstance(row.get("facts_json"), dict) else {}
        candidate_event_id = str(facts.get("candidate_event_id") or "").strip()
        if candidate_event_id and candidate_event_id not in decisions:
            decisions[candidate_event_id] = {
                "event_id": row.get("event_id"),
                "decision": facts.get("decision"),
                "reason": facts.get("reason"),
                "edited_text": facts.get("edited_text"),
                "target_scope": facts.get("target_scope"),
                "created_at": row.get("created_at"),
            }
    return decisions


def _item_from_candidate_event(row: dict[str, Any], decision: dict[str, Any] | None) -> MemoryApprovalInboxItem | None:
    event_type = str(row.get("event_type") or "")
    facts = row.get("facts_json") if isinstance(row.get("facts_json"), dict) else {}
    provenance = row.get("provenance_json") if isinstance(row.get("provenance_json"), dict) else {}
    candidate = facts.get("memory_candidate") if isinstance(facts.get("memory_candidate"), dict) else {}
    if not _is_reviewable_candidate(event_type=event_type, facts=facts, candidate=candidate):
        return None
    proposed_text = _candidate_text(facts=facts, candidate=candidate)
    if not proposed_text:
        return None
    memory_role = str(candidate.get("memory_role") or facts.get("memory_role") or "unknown").strip() or "unknown"
    target_scope = str(candidate.get("target_scope") or facts.get("target_scope") or _scope_for_role(memory_role)).strip()
    candidate_event_id = str(row.get("event_id") or "")
    return MemoryApprovalInboxItem(
        item_id=f"mem-inbox:{candidate_event_id}",
        candidate_event_id=candidate_event_id,
        source_event_type=event_type,
        created_at=str(row.get("created_at") or "") or None,
        status="decided" if decision else "pending_review",
        proposed_text=proposed_text,
        memory_role=memory_role,
        target_scope=target_scope,
        recommended_action=_recommended_action(memory_role=memory_role, target_scope=target_scope),
        source_refs=_source_refs(facts=facts, provenance=provenance, candidate=candidate),
        reason=str(candidate.get("reason") or facts.get("reason") or facts.get("outcome") or row.get("reason_code") or ""),
        decision=decision,
    )


def _is_reviewable_candidate(*, event_type: str, facts: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if event_type == "memory_candidate_created":
        return bool(candidate)
    if event_type == "memory_candidate_assessed":
        return str(facts.get("outcome") or "") in REVIEWABLE_CANDIDATE_OUTCOMES
    if event_type == "memory_write_requested":
        approval_state = str(facts.get("approval_state") or "").strip()
        return bool(facts.get("requires_approval")) or approval_state in PENDING_APPROVAL_STATES
    return False


def _candidate_text(*, facts: dict[str, Any], candidate: dict[str, Any]) -> str:
    for key in ("text", "proposed_text", "evidence_text", "belief_text", "episode_text", "value"):
        text = str(candidate.get(key) or facts.get(key) or "").strip()
        if text:
            return text
    observations = facts.get("observations")
    if isinstance(observations, list):
        values = [str(item.get("value") or item.get("text") or "").strip() for item in observations if isinstance(item, dict)]
        compacted = [value for value in values if value]
        if compacted:
            return "; ".join(compacted[:3])
    return ""


def _source_refs(*, facts: dict[str, Any], provenance: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    for value in (candidate.get("source_refs"), facts.get("sources"), provenance.get("source_refs")):
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _recommended_action(*, memory_role: str, target_scope: str) -> str:
    if target_scope == "spark_doctrine":
        return "save_as_spark_doctrine"
    if memory_role in {"current_state", "preference", "profile_fact"}:
        return "save_as_personal_preference"
    if memory_role in {"project_fact", "structured_evidence"}:
        return "save_as_project_fact"
    return "approve"


def _scope_for_role(memory_role: str) -> str:
    if memory_role in {"current_state", "preference", "profile_fact"}:
        return "personal_preference"
    if memory_role in {"belief", "procedural_lesson", "spark_doctrine"}:
        return "spark_doctrine"
    if memory_role in {"structured_evidence", "project_fact"}:
        return "project_fact"
    return "review_queue"


def _normalize_decision(decision: str) -> str:
    normalized = str(decision or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized not in REVIEW_ACTIONS:
        raise ValueError(f"unsupported_memory_approval_decision:{decision}")
    return normalized


def _event_row_to_dict(row: Any) -> dict[str, Any]:
    output = {key: row[key] for key in row.keys()}
    for key in ("facts_json", "provenance_json"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            try:
                output[key] = json.loads(value)
            except json.JSONDecodeError:
                output[key] = {}
        elif value is None:
            output[key] = {}
    return output


def _compact(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."
