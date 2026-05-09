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
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    active_reference_items: list[str] | None = None,
    proposed_action: str | None = None,
    draft_answer: str | None = None,
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
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
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
