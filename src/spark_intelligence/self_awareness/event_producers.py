from __future__ import annotations

from typing import Any

from spark_intelligence.self_awareness.agent_events import AgentEvent, AgentSourceRef, record_agent_event
from spark_intelligence.state.db import StateDB


def record_capability_probe_agent_event(
    state_db: StateDB,
    *,
    capability_key: str,
    status: str,
    route_probe_event_id: str,
    route_latency_ms: int | None = None,
    eval_ref: str = "",
    source_ref: str = "",
    failure_reason: str = "",
    probe_summary: str = "",
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
    actor_id: str = "",
) -> str:
    normalized_status = str(status or "").strip().casefold()
    success = normalized_status == "success"
    capability = str(capability_key or "").strip()
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="capability_probed",
            summary=f"Capability probe {normalized_status or 'unknown'}: {capability}.",
            selected_route=capability,
            route_confidence="high" if success else "blocked",
            facts={
                "capability_key": capability,
                "probe_status": normalized_status,
                "route_probe_event_id": str(route_probe_event_id or "").strip(),
                "route_latency_ms": route_latency_ms,
                "eval_ref": str(eval_ref or "").strip() or None,
                "failure_reason": str(failure_reason or "").strip() or None,
                "probe_summary": str(probe_summary or "").strip() or None,
            },
            sources=[
                AgentSourceRef(
                    source="route_probe",
                    role="capability_evidence",
                    freshness="live_probed",
                    source_ref=str(source_ref or route_probe_event_id or "").strip() or None,
                    summary=str(probe_summary or failure_reason or status or "").strip(),
                )
            ],
            blockers=[str(failure_reason or "route_probe_failed").strip()] if not success else [],
            changed=[f"{capability}:last_probe={normalized_status}"],
        ),
        request_id=str(request_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        human_id=str(human_id or "").strip() or None,
        actor_id=str(actor_id or "").strip() or None,
        correlation_id=str(route_probe_event_id or "").strip() or None,
    )


def record_route_selection_agent_event(
    state_db: StateDB,
    *,
    selected_route: str,
    user_intent: str = "",
    confidence: str = "",
    reason: str = "",
    sources: list[dict[str, Any]] | None = None,
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
    actor_id: str = "",
) -> str:
    route = str(selected_route or "").strip()
    source_refs = [_source_ref_from_payload(source) for source in list(sources or [])]
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="route_selected",
            summary=f"Route selected: {route}.",
            user_intent=str(user_intent or "").strip() or None,
            selected_route=route,
            route_confidence=str(confidence or "").strip() or None,
            facts={"reason": str(reason or "").strip()},
            sources=source_refs,
            assumptions=[str(reason or "").strip()] if reason else [],
        ),
        request_id=str(request_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        human_id=str(human_id or "").strip() or None,
        actor_id=str(actor_id or "").strip() or None,
    )


def record_mission_state_agent_event(
    state_db: StateDB,
    *,
    mission_id: str,
    from_state: str = "",
    to_state: str = "",
    summary: str = "",
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
    actor_id: str = "",
) -> str:
    mission = str(mission_id or "").strip()
    previous = str(from_state or "").strip()
    current = str(to_state or "").strip()
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="mission_changed_state",
            summary=str(summary or f"Mission {mission} changed state from {previous or 'unknown'} to {current or 'unknown'}."),
            selected_route="mission_control",
            route_confidence="medium",
            facts={"mission_id": mission, "from_state": previous or None, "to_state": current or None},
            sources=[
                AgentSourceRef(
                    source="mission_trace",
                    role="work_state_evidence",
                    freshness="fresh",
                    source_ref=mission or None,
                    summary=str(summary or current or previous or "").strip(),
                )
            ],
            changed=[f"{mission}:state={current}"] if mission and current else [],
        ),
        request_id=str(request_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        human_id=str(human_id or "").strip() or None,
        actor_id=str(actor_id or "").strip() or None,
    )


def record_user_override_agent_event(
    state_db: StateDB,
    *,
    override_summary: str,
    corrected_route: str = "",
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
    actor_id: str = "",
) -> str:
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="user_override_received",
            summary=str(override_summary or "User override received.").strip(),
            selected_route=str(corrected_route or "").strip() or None,
            route_confidence="high",
            sources=[
                AgentSourceRef(
                    source="current_user_message",
                    role="operator_override",
                    freshness="fresh",
                    source_ref=str(request_id or "").strip() or None,
                    summary=str(override_summary or "").strip(),
                )
            ],
            changed=["latest_user_message_overrides_prior_context"],
        ),
        request_id=str(request_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        human_id=str(human_id or "").strip() or None,
        actor_id=str(actor_id or "").strip() or None,
    )


def record_contradiction_agent_event(
    state_db: StateDB,
    *,
    claim_key: str,
    winner: dict[str, Any],
    stale_claims: list[dict[str, Any]],
    contradicted_claims: list[dict[str, Any]],
    resolution: str,
    contradiction_event_id: str = "",
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
    actor_id: str = "",
) -> str:
    stale_sources = [str(claim.get("source") or "") for claim in stale_claims if isinstance(claim, dict)]
    contradicted_sources = [str(claim.get("source") or "") for claim in contradicted_claims if isinstance(claim, dict)]
    sources = [
        AgentSourceRef(
            source=str(winner.get("source") or "unknown"),
            role="winning_source",
            freshness=str(winner.get("freshness") or "unknown"),  # type: ignore[arg-type]
            source_ref=str(winner.get("source_ref") or "").strip() or None,
            summary=str(winner.get("summary") or winner.get("value") or "").strip(),
        )
    ]
    for claim in [*stale_claims, *contradicted_claims]:
        if not isinstance(claim, dict):
            continue
        sources.append(
            AgentSourceRef(
                source=str(claim.get("source") or "unknown"),
                role="conflicting_source",
                freshness=str(claim.get("freshness") or "unknown"),  # type: ignore[arg-type]
                source_ref=str(claim.get("source_ref") or "").strip() or None,
                summary=str(claim.get("summary") or claim.get("value") or "").strip(),
            )
        )
    return record_agent_event(
        state_db,
        AgentEvent(
            event_type="contradiction_found",
            summary=f"Contradiction found for {str(claim_key or '').strip()}.",
            selected_route="source_hierarchy_review",
            route_confidence="high",
            facts={
                "claim_key": str(claim_key or "").strip(),
                "winner": dict(winner),
                "stale_sources": stale_sources,
                "contradicted_sources": contradicted_sources,
                "resolution": str(resolution or "").strip(),
                "contradiction_event_id": str(contradiction_event_id or "").strip() or None,
            },
            sources=sources,
            blockers=[str(resolution or "source_conflict").strip()],
            changed=["lower_authority_context_marked_for_review"],
        ),
        request_id=str(request_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        human_id=str(human_id or "").strip() or None,
        actor_id=str(actor_id or "").strip() or None,
        correlation_id=str(contradiction_event_id or "").strip() or None,
    )


def _source_ref_from_payload(payload: dict[str, Any]) -> AgentSourceRef:
    return AgentSourceRef(
        source=str(payload.get("source") or "unknown"),
        role=str(payload.get("role") or "route_selection_evidence"),
        freshness=str(payload.get("freshness") or "unknown"),  # type: ignore[arg-type]
        source_ref=str(payload.get("source_ref") or "").strip() or None,
        summary=str(payload.get("summary") or "").strip(),
    )
