from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.approval_inbox import MemoryApprovalInboxReport, build_memory_approval_inbox
from spark_intelligence.self_awareness.agent_events import AgentBlackBoxReport, build_agent_black_box_report
from spark_intelligence.self_awareness.operating_context import AgentOperatingContextResult, build_agent_operating_context
from spark_intelligence.self_awareness.source_hierarchy import SourceClaim
from spark_intelligence.self_awareness.stale_context_sweeper import (
    StaleContextSweepReport,
    build_stale_context_sweep,
)
from spark_intelligence.state.db import StateDB


AGENT_OPERATING_PANEL_SCHEMA_VERSION = "spark.agent_operating_panel.v1"


@dataclass(frozen=True)
class AgentOperatingPanel:
    aoc: AgentOperatingContextResult
    black_box: AgentBlackBoxReport
    memory_approval_inbox: MemoryApprovalInboxReport
    stale_context_sweep: StaleContextSweepReport

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_OPERATING_PANEL_SCHEMA_VERSION,
            "aoc": self.aoc.to_payload(),
            "black_box": self.black_box.to_payload(),
            "memory_approval_inbox": self.memory_approval_inbox.to_payload(),
            "stale_context_sweep": self.stale_context_sweep.to_payload(),
            "source_policy": (
                "This panel is a shared read-model over AOC, agent events, memory approval, and source hierarchy. "
                "It does not create new authority beyond those sources."
            ),
        }

    def to_text(self) -> str:
        payload = self.to_payload()
        aoc = payload["aoc"]
        memory_counts = payload["memory_approval_inbox"]["counts"]
        black_box_counts = payload["black_box"]["counts"]
        stale_counts = payload["stale_context_sweep"]["counts"]
        lines = [
            "Agent Operating Panel",
            f"AOC: {str(aoc.get('status') or 'unknown').replace('_', ' ')}",
            f"Mode: {(aoc.get('conversation_frame') or {}).get('current_mode') or 'unknown'}",
            f"Best route: {(aoc.get('task_fit') or {}).get('recommended_route_label') or 'unknown'}",
            f"Route confidence: {(aoc.get('route_confidence') or {}).get('confidence') or 'unknown'}",
            f"Black box events: {black_box_counts.get('entries', 0)}",
            f"Memory approvals pending: {memory_counts.get('pending', 0)}",
            f"Stale context: {stale_counts.get('stale', 0)} stale, {stale_counts.get('contradicted', 0)} contradicted",
        ]
        summary = aoc.get("agent_facing_summary")
        if summary:
            lines.extend(["", str(summary)])
        return "\n".join(lines)


def build_agent_operating_panel(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str = "",
    session_id: str = "",
    channel_kind: str = "",
    request_id: str | None = None,
    user_message: str = "",
    spark_access_level: str = "",
    runner_writable: bool | None = None,
    runner_label: str = "",
    memory_inbox_status: str = "pending",
    stale_live_claims: list[SourceClaim | dict[str, Any]] | None = None,
    stale_context_claims: list[SourceClaim | dict[str, Any]] | None = None,
) -> AgentOperatingPanel:
    aoc = build_agent_operating_context(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message,
        spark_access_level=spark_access_level,
        runner_writable=runner_writable,
        runner_label=runner_label,
    )
    live_claims = list(stale_live_claims or [])
    if spark_access_level:
        live_claims.append(
            SourceClaim(
                claim_key="spark_access_level",
                value=f"Level {str(spark_access_level).strip()}",
                source="operator_supplied_access",
                freshness="fresh",
                source_ref=request_id,
            )
        )
    return AgentOperatingPanel(
        aoc=aoc,
        black_box=build_agent_black_box_report(state_db, request_id=request_id),
        memory_approval_inbox=build_memory_approval_inbox(state_db, status=memory_inbox_status),
        stale_context_sweep=build_stale_context_sweep(
            live_claims=live_claims,
            context_claims=list(stale_context_claims or []),
        ),
    )
