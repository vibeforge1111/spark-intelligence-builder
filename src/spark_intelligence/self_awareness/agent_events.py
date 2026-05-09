from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from spark_intelligence.observability.store import record_event, utc_now_iso
from spark_intelligence.self_awareness.conversation_frame import (
    ActionGateResult,
    ConversationOperatingFrame,
    FinalAnswerDriftCheck,
)
from spark_intelligence.state.db import StateDB


AGENT_EVENT_SCHEMA_VERSION = "spark.agent_event.v1"
AGENT_EVENT_COMPONENT = "agent_event_model"

AgentEventType = Literal[
    "task_intent_detected",
    "route_selected",
    "source_used",
    "capability_probed",
    "blocker_detected",
    "memory_candidate_created",
    "contradiction_found",
    "mission_changed_state",
    "agent_drift_detected",
    "user_override_received",
    "action_gate_evaluated",
    "final_answer_checked",
]
SourceFreshness = Literal["fresh", "stale", "contradicted", "unknown", "live_probed"]

AGENT_EVENT_TYPES: tuple[str, ...] = (
    "task_intent_detected",
    "route_selected",
    "source_used",
    "capability_probed",
    "blocker_detected",
    "memory_candidate_created",
    "contradiction_found",
    "mission_changed_state",
    "agent_drift_detected",
    "user_override_received",
    "action_gate_evaluated",
    "final_answer_checked",
)


@dataclass(frozen=True)
class AgentSourceRef:
    source: str
    role: str
    freshness: SourceFreshness = "unknown"
    source_ref: str | None = None
    summary: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "role": self.role,
            "freshness": self.freshness,
            "source_ref": self.source_ref,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class AgentEvent:
    event_type: AgentEventType
    summary: str
    user_intent: str | None = None
    selected_route: str | None = None
    route_confidence: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    sources: list[AgentSourceRef] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    memory_candidate: dict[str, Any] | None = None

    def to_facts(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_EVENT_SCHEMA_VERSION,
            "event_type": self.event_type,
            "user_intent": self.user_intent,
            "selected_route": self.selected_route,
            "route_confidence": self.route_confidence,
            "sources": [source.to_payload() for source in self.sources],
            "assumptions": list(self.assumptions),
            "blockers": list(self.blockers),
            "changed": list(self.changed),
            "memory_candidate": self.memory_candidate,
            **dict(self.facts),
        }

    def to_provenance(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_EVENT_SCHEMA_VERSION,
            "source_refs": [source.to_payload() for source in self.sources],
        }


@dataclass(frozen=True)
class BlackBoxEntry:
    event_id: str
    event_type: str
    created_at: str | None
    perceived_intent: str | None
    route_chosen: str | None
    sources_used: list[dict[str, Any]]
    assumptions: list[str]
    blockers: list[str]
    changed: list[str]
    memory_candidate: dict[str, Any] | None
    summary: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "perceived_intent": self.perceived_intent,
            "route_chosen": self.route_chosen,
            "sources_used": list(self.sources_used),
            "assumptions": list(self.assumptions),
            "blockers": list(self.blockers),
            "changed": list(self.changed),
            "memory_candidate": self.memory_candidate,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class AgentBlackBoxReport:
    checked_at: str
    request_id: str | None
    entries: list[BlackBoxEntry]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_EVENT_SCHEMA_VERSION,
            "checked_at": self.checked_at,
            "request_id": self.request_id,
            "counts": {
                "entries": len(self.entries),
                "blocker_events": sum(1 for entry in self.entries if entry.blockers),
                "memory_candidates": sum(1 for entry in self.entries if entry.memory_candidate),
            },
            "entries": [entry.to_payload() for entry in self.entries],
        }

    def to_text(self) -> str:
        if not self.entries:
            return "Agent black box: no matching events."
        lines = [f"Agent black box: {len(self.entries)} event(s)."]
        for entry in self.entries[:8]:
            route = entry.route_chosen or "unknown_route"
            intent = entry.perceived_intent or "unknown_intent"
            blockers = f" blockers={len(entry.blockers)}" if entry.blockers else ""
            lines.append(f"- {entry.event_type}: intent={intent} route={route}{blockers}")
        return "\n".join(lines)


def record_agent_event(
    state_db: StateDB,
    event: AgentEvent,
    *,
    request_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str | None = None,
    parent_event_id: str | None = None,
    correlation_id: str | None = None,
) -> str:
    return record_event(
        state_db,
        event_type=event.event_type,
        component=AGENT_EVENT_COMPONENT,
        summary=event.summary,
        run_id=run_id,
        parent_event_id=parent_event_id,
        correlation_id=correlation_id,
        request_id=request_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
        reason_code=event.event_type,
        provenance=event.to_provenance(),
        facts=event.to_facts(),
    )


def record_conversation_frame_event(
    state_db: StateDB,
    frame: ConversationOperatingFrame,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> str:
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="task_intent_detected",
            summary=f"Conversation frame classified the turn as {frame.current_mode}/{frame.user_intent}.",
            user_intent=frame.user_intent,
            selected_route=frame.allowed_next_actions[0] if frame.allowed_next_actions else None,
            route_confidence="medium",
            facts={"conversation_frame": frame.to_payload()},
            sources=[
                AgentSourceRef(
                    source="current_user_message",
                    role="latest_turn_authority",
                    freshness="fresh",
                    source_ref=str(frame.active_reference_list.get("source_turn_id") or request_id or "") or None,
                    summary=frame.latest_user_message_summary,
                )
            ],
        ),
        request_id=request_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )


def record_action_gate_event(
    state_db: StateDB,
    frame: ConversationOperatingFrame,
    gate: ActionGateResult,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> str:
    blocked = gate.decision != "allowed"
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="blocker_detected" if blocked else "route_selected",
            summary=f"Action gate {gate.decision}: {gate.proposed_action}.",
            user_intent=frame.user_intent,
            selected_route=gate.safe_next_action,
            route_confidence="high" if not blocked else "blocked",
            facts={"conversation_frame": frame.to_payload(), "action_gate": gate.to_payload()},
            sources=[
                AgentSourceRef(
                    source="conversation_operating_frame",
                    role="action_boundary",
                    freshness="fresh",
                    source_ref=str(frame.active_reference_list.get("source_turn_id") or request_id or "") or None,
                    summary=frame.latest_user_message_summary,
                )
            ],
            blockers=[gate.reason] if blocked else [],
        ),
        request_id=request_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )


def record_final_answer_check_event(
    state_db: StateDB,
    frame: ConversationOperatingFrame,
    drift_check: FinalAnswerDriftCheck,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> str:
    drifted = not drift_check.match
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="agent_drift_detected" if drifted else "final_answer_checked",
            summary=f"Final answer drift check {'failed' if drifted else 'passed'}.",
            user_intent=frame.user_intent,
            selected_route="rewrite_answer" if drifted else "answer_in_chat",
            route_confidence="high",
            facts={"conversation_frame": frame.to_payload(), "drift_check": drift_check.to_payload()},
            sources=[
                AgentSourceRef(
                    source="conversation_operating_frame",
                    role="drift_check_authority",
                    freshness="fresh",
                    source_ref=str(frame.active_reference_list.get("source_turn_id") or request_id or "") or None,
                    summary=frame.latest_user_message_summary,
                )
            ],
            blockers=[drift_check.drift_type or "answer_mismatch"] if drifted else [],
            changed=["rewrite_required"] if drift_check.rewrite_required else [],
        ),
        request_id=request_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )


def build_agent_black_box_entries(
    state_db: StateDB,
    *,
    request_id: str | None = None,
    limit: int = 20,
) -> list[BlackBoxEntry]:
    rows = _recent_agent_event_rows(state_db, request_id=request_id, limit=limit)
    return [_black_box_entry_from_row(row) for row in rows]


def build_agent_black_box_report(
    state_db: StateDB,
    *,
    request_id: str | None = None,
    limit: int = 20,
) -> AgentBlackBoxReport:
    return AgentBlackBoxReport(
        checked_at=utc_now_iso(),
        request_id=request_id,
        entries=build_agent_black_box_entries(state_db, request_id=request_id, limit=limit),
    )


def _recent_agent_event_rows(
    state_db: StateDB,
    *,
    request_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in AGENT_EVENT_TYPES)
    params: list[Any] = [*AGENT_EVENT_TYPES]
    where = [f"event_type IN ({placeholders})", "component = ?"]
    params.append(AGENT_EVENT_COMPONENT)
    if request_id:
        where.append("request_id = ?")
        params.append(request_id)
    params.append(max(1, int(limit)))
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM builder_events
            WHERE {" AND ".join(where)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def _black_box_entry_from_row(row: dict[str, Any]) -> BlackBoxEntry:
    facts = row.get("facts_json") if isinstance(row.get("facts_json"), dict) else {}
    return BlackBoxEntry(
        event_id=str(row.get("event_id") or ""),
        event_type=str(row.get("event_type") or ""),
        created_at=str(row.get("created_at") or "") or None,
        perceived_intent=_as_optional_text(facts.get("user_intent")),
        route_chosen=_as_optional_text(facts.get("selected_route")),
        sources_used=_as_payload_list(facts.get("sources")),
        assumptions=_as_text_list(facts.get("assumptions")),
        blockers=_as_text_list(facts.get("blockers")),
        changed=_as_text_list(facts.get("changed")),
        memory_candidate=facts.get("memory_candidate") if isinstance(facts.get("memory_candidate"), dict) else None,
        summary=str(row.get("summary") or ""),
    )


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


def _as_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _as_payload_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
