from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import resolve_inbound_dm
from spark_intelligence.observability.store import record_event
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply, record_researcher_bridge_result
from spark_intelligence.state.db import StateDB


@dataclass
class SimulatedDmBridgeResult:
    ok: bool
    decision: str
    detail: dict[str, Any]


def resolve_simulated_dm(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    channel_id: str,
    request_id: str,
    external_user_id: str,
    display_name: str,
    user_message: str,
    run_id: str | None = None,
    origin_surface: str = "gateway_simulated_dm",
) -> SimulatedDmBridgeResult:
    resolution = resolve_inbound_dm(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
        display_name=display_name,
    )
    outbound_text = resolution.response_text
    trace_ref = None
    bridge_mode = None
    attachment_context = None
    output_keepability = None
    promotion_disposition = None
    if resolution.allowed and resolution.agent_id and resolution.human_id and resolution.session_id:
        record_event(
            state_db,
            event_type="intent_committed",
            component=origin_surface,
            summary=f"{channel_id} simulated DM committed to bridge execution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_id,
            session_id=resolution.session_id,
            human_id=resolution.human_id,
            agent_id=resolution.agent_id,
            actor_id=origin_surface,
            reason_code="user_message_allowed",
            facts={
                "external_user_id": external_user_id,
                "message_length": len(user_message),
            },
        )
        bridge_result = build_researcher_reply(
            config_manager=config_manager,
            state_db=state_db,
            request_id=request_id,
            agent_id=resolution.agent_id,
            human_id=resolution.human_id,
            session_id=resolution.session_id,
            channel_kind=channel_id,
            user_message=user_message,
            run_id=run_id,
        )
        record_researcher_bridge_result(state_db=state_db, result=bridge_result)
        outbound_text = bridge_result.reply_text
        trace_ref = bridge_result.trace_ref
        bridge_mode = bridge_result.mode
        attachment_context = bridge_result.attachment_context
        output_keepability = bridge_result.output_keepability
        promotion_disposition = bridge_result.promotion_disposition
    return SimulatedDmBridgeResult(
        ok=resolution.allowed,
        decision=resolution.decision,
        detail={
            "session_id": resolution.session_id,
            "human_id": resolution.human_id,
            "agent_id": resolution.agent_id,
            "message_text": user_message,
            "response_text": outbound_text,
            "trace_ref": trace_ref,
            "bridge_mode": bridge_mode,
            "attachment_context": attachment_context,
            "output_keepability": output_keepability,
            "promotion_disposition": promotion_disposition,
        },
    )
