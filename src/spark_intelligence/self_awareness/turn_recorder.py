from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from spark_intelligence.self_awareness.agent_events import (
    AgentEvent,
    AgentSourceRef,
    record_action_gate_event,
    record_agent_event,
    record_conversation_frame_event,
    record_final_answer_check_event,
)
from spark_intelligence.self_awareness.conversation_frame import (
    ActionGateResult,
    ConversationOperatingFrame,
    FinalAnswerDriftCheck,
    build_conversation_operating_frame,
    check_final_answer_drift,
    evaluate_action_gate,
)
from spark_intelligence.self_awareness.event_producers import record_source_used_agent_event
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class AgentTurnTrace:
    frame: ConversationOperatingFrame
    event_ids: list[str] = field(default_factory=list)
    action_gate: ActionGateResult | None = None
    final_answer_check: FinalAnswerDriftCheck | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "frame": self.frame.to_payload(),
            "event_ids": list(self.event_ids),
            "action_gate": self.action_gate.to_payload() if self.action_gate else None,
            "final_answer_check": self.final_answer_check.to_payload() if self.final_answer_check else None,
        }


def record_agent_turn_trace(
    state_db: StateDB,
    *,
    user_message: str,
    request_id: str | None = None,
    trace_ref: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    active_reference_items: list[str] | None = None,
    proposed_action: str | None = None,
    draft_answer: str | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    memory_candidate: dict[str, Any] | None = None,
) -> AgentTurnTrace:
    frame = build_conversation_operating_frame(
        user_message=user_message,
        active_reference_items=active_reference_items,
        source_turn_id=request_id,
    )
    event_ids = [
        record_conversation_frame_event(
            state_db,
            frame,
            request_id=request_id,
            trace_ref=trace_ref,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
    ]
    action_gate: ActionGateResult | None = None
    if proposed_action:
        action_gate = evaluate_action_gate(frame, proposed_action=proposed_action)
        event_ids.append(
            record_action_gate_event(
                state_db,
                frame,
                action_gate,
                request_id=request_id,
                trace_ref=trace_ref,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
        )
    final_answer_check: FinalAnswerDriftCheck | None = None
    if draft_answer is not None:
        final_answer_check = check_final_answer_drift(frame, draft_answer=draft_answer)
        event_ids.append(
            record_final_answer_check_event(
                state_db,
                frame,
                final_answer_check,
                request_id=request_id,
                trace_ref=trace_ref,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
        )
    for source_ref in list(source_refs or []):
        if not isinstance(source_ref, dict):
            continue
        event_ids.append(
            record_source_used_agent_event(
                state_db,
                source=str(source_ref.get("source") or ""),
                role=str(source_ref.get("role") or "supporting_evidence"),
                freshness=str(source_ref.get("freshness") or "unknown"),
                source_ref=str(source_ref.get("source_ref") or ""),
                summary=str(source_ref.get("summary") or ""),
                user_intent=frame.user_intent,
                selected_route=frame.allowed_next_actions[0] if frame.allowed_next_actions else "",
                request_id=str(request_id or ""),
                trace_ref=str(trace_ref or ""),
                session_id=str(session_id or ""),
                human_id=str(human_id or ""),
                actor_id=str(agent_id or "turn_recorder"),
            )
        )
    if memory_candidate:
        event_ids.append(
            record_agent_event(
                state_db,
                AgentEvent(
                    event_type="memory_candidate_created",
                    summary="Agent turn proposed a memory candidate for approval.",
                    user_intent=frame.user_intent,
                    selected_route="memory_approval_inbox",
                    route_confidence="medium",
                    memory_candidate=dict(memory_candidate),
                    sources=[
                        AgentSourceRef(
                            source="current_user_message",
                            role="memory_candidate_evidence",
                            freshness="fresh",
                            source_ref=request_id,
                            summary=frame.latest_user_message_summary,
                        )
                    ],
                ),
                request_id=request_id,
                trace_ref=trace_ref,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
        )
    return AgentTurnTrace(
        frame=frame,
        event_ids=event_ids,
        action_gate=action_gate,
        final_answer_check=final_answer_check,
    )
