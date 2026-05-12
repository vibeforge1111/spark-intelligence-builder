from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.approval_inbox import MemoryApprovalInboxReport, build_memory_approval_inbox
from spark_intelligence.self_awareness.agent_scratchpad import AgentScratchpad, build_agent_scratchpad
from spark_intelligence.self_awareness.agent_events import AgentBlackBoxReport, build_agent_black_box_report
from spark_intelligence.self_awareness.operating_context import AgentOperatingContextResult, build_agent_operating_context
from spark_intelligence.self_awareness.operating_panel_sections import AgentPanelSections, build_agent_panel_sections
from spark_intelligence.self_awareness.operating_source_ledger import AgentSourceLedger, build_agent_source_ledger
from spark_intelligence.self_awareness.operating_strip import AgentOperatingStrip, build_agent_operating_strip
from spark_intelligence.self_awareness.source_hierarchy import SourceClaim
from spark_intelligence.self_awareness.spawner_agent_events import read_configured_spawner_black_box_entries
from spark_intelligence.self_awareness.stale_context_sweeper import (
    StaleContextSweepReport,
    build_stale_context_sweep,
)
from spark_intelligence.state.db import StateDB


AGENT_OPERATING_PANEL_SCHEMA_VERSION = "spark.agent_operating_panel.v1"
TRACE_REPAIR_QUEUE_SCHEMA_VERSION = "spark.trace_repair_queue.v1"


@dataclass(frozen=True)
class AgentOperatingPanel:
    aoc: AgentOperatingContextResult
    strip: AgentOperatingStrip
    agent_scratchpad: AgentScratchpad
    source_ledger: AgentSourceLedger
    sections: AgentPanelSections
    trace_repair_queue: dict[str, Any]
    authority_status: dict[str, Any]
    black_box: AgentBlackBoxReport
    memory_approval_inbox: MemoryApprovalInboxReport
    stale_context_sweep: StaleContextSweepReport

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_OPERATING_PANEL_SCHEMA_VERSION,
            "strip": self.strip.to_payload(),
            "aoc": self.aoc.to_payload(),
            "agent_scratchpad": self.agent_scratchpad.to_payload(),
            "source_ledger": self.source_ledger.to_payload(),
            "sections": self.sections.to_payload(),
            "trace_repair_queue": dict(self.trace_repair_queue),
            "authority_status": dict(self.authority_status),
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
        source_counts = payload["source_ledger"]["counts"]
        trace_repair_queue = payload["trace_repair_queue"]
        authority_status = payload["authority_status"]
        scratchpad = payload["agent_scratchpad"]
        capability_garden = _dict(_dict(aoc.get("spark_system_map")).get("capability_garden"))
        lines = [
            "Agent Operating Panel",
            self.strip.to_text(),
            f"AOC: {str(aoc.get('status') or 'unknown').replace('_', ' ')}",
            f"Mode: {(aoc.get('conversation_frame') or {}).get('current_mode') or 'unknown'}",
            f"Best route: {(aoc.get('task_fit') or {}).get('recommended_route_label') or 'unknown'}",
            f"Route confidence: {(aoc.get('route_confidence') or {}).get('confidence') or 'unknown'}",
            f"Execution lane: {_execution_lane_text(aoc.get('execution_lane') or {})}",
            f"Next safe access action: {_access_automation_text(aoc.get('access_automation') or {})}",
            f"Current goal: {scratchpad.get('current_goal') or 'unknown'}",
            f"Next safe action: {scratchpad.get('next_safe_action') or 'answer_in_chat'}",
            f"Sources: {source_counts.get('present', 0)} present, {source_counts.get('stale', 0)} stale, {source_counts.get('contradicted', 0)} contradicted",
            _trace_repair_text(trace_repair_queue),
            _authority_status_text(authority_status),
            _capability_garden_text(capability_garden),
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
    execution_lane_state: dict[str, Any] | None = None,
    live_state: dict[str, Any] | None = None,
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
        execution_lane_state=execution_lane_state,
        live_state=live_state,
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
    aoc_payload = aoc.to_payload()
    trace_repair_queue = _build_trace_repair_queue(aoc_payload)
    authority_status = _dict(_dict(aoc_payload.get("spark_system_map")).get("authority_status"))
    strip = build_agent_operating_strip(aoc_payload)
    scratchpad = build_agent_scratchpad(aoc_payload)
    spawner_black_box_entries = read_configured_spawner_black_box_entries(
        config_manager,
        request_id=request_id,
    )
    black_box = build_agent_black_box_report(
        state_db,
        request_id=request_id,
        external_entries=spawner_black_box_entries,
    )
    memory_inbox = build_memory_approval_inbox(state_db, status=memory_inbox_status)
    stale_sweep = build_stale_context_sweep(
        live_claims=live_claims,
        context_claims=list(stale_context_claims or []),
    )
    source_ledger = build_agent_source_ledger(
        aoc_payload=aoc_payload,
        black_box_payload=black_box.to_payload(),
        memory_inbox_payload=memory_inbox.to_payload(),
        stale_sweep_payload=stale_sweep.to_payload(),
    )
    sections = build_agent_panel_sections(
        aoc_payload=aoc_payload,
        scratchpad_payload=scratchpad.to_payload(),
        black_box_payload=black_box.to_payload(),
        source_ledger_payload=source_ledger.to_payload(),
        stale_sweep_payload=stale_sweep.to_payload(),
        trace_repair_payload=trace_repair_queue,
    )
    return AgentOperatingPanel(
        aoc=aoc,
        strip=strip,
        agent_scratchpad=scratchpad,
        source_ledger=source_ledger,
        sections=sections,
        trace_repair_queue=trace_repair_queue,
        authority_status=authority_status,
        black_box=black_box,
        memory_approval_inbox=memory_inbox,
        stale_context_sweep=stale_sweep,
    )


def _execution_lane_text(execution_lane: dict[str, Any]) -> str:
    docker = execution_lane.get("docker") if isinstance(execution_lane.get("docker"), dict) else {}
    return (
        f"docker available={_optional_bool_text(docker.get('available'))}, "
        f"selected={_optional_bool_text(docker.get('selected'))}, "
        f"probed={_optional_bool_text(docker.get('probed'))}; "
        f"workspace sandbox={_optional_bool_text(execution_lane.get('workspace_sandbox'))}; "
        f"level5 whole-computer claim={bool(execution_lane.get('level5_whole_computer_claim_allowed'))}"
    )


def _access_automation_text(access_automation: dict[str, Any]) -> str:
    action = str(access_automation.get("next_safe_access_action") or access_automation.get("recommended_action") or "unknown")
    policy = str(access_automation.get("recommended_run_policy") or "unknown")
    confirmation = "yes" if access_automation.get("requires_confirmation") else "no"
    return f"{action} (run policy: {policy}, confirmation required: {confirmation})"


def _optional_bool_text(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _build_trace_repair_queue(aoc_payload: dict[str, Any]) -> dict[str, Any]:
    spark_system_map = _dict(aoc_payload.get("spark_system_map"))
    trace_health = _dict(spark_system_map.get("trace_health"))
    trace_topology = _dict(spark_system_map.get("trace_topology"))
    missing_sources = _dict(trace_health.get("missing_trace_ref_sources"))
    orphan_sources = _dict(trace_health.get("orphan_parent_event_sources"))
    rows = [_dict(row) for row in _list(missing_sources.get("rows"))[:5]]
    orphan_rows = [_dict(row) for row in _list(orphan_sources.get("rows"))[:5]]
    recent_windows = [_dict(row) for row in _list(trace_health.get("recent_windows"))[:3]]
    health_flags = [str(flag) for flag in _list(trace_health.get("health_flags")) if str(flag or "").strip()]
    counts = {
        "health_flags": len(health_flags),
        "missing_trace_ref_count": _int(trace_health.get("missing_trace_ref_count")),
        "high_severity_open_count": _int(trace_health.get("high_severity_open_count")),
        "orphan_parent_event_id_count": _int(trace_health.get("orphan_parent_event_id_count")),
        "trace_group_count": _int(trace_health.get("trace_group_count")),
        "top_missing_source_count": len(rows),
        "top_orphan_parent_source_count": len(orphan_rows),
        "topology_group_count": _int(trace_topology.get("group_count")),
        "topology_parent_link_count": _int(trace_topology.get("parent_link_count")),
        "topology_edge_sample_count": _int(trace_topology.get("edge_sample_count")),
    }
    present = bool(spark_system_map.get("present")) and bool(trace_health.get("present"))
    status = _trace_repair_status(present=present, counts=counts)
    return {
        "schema_version": TRACE_REPAIR_QUEUE_SCHEMA_VERSION,
        "present": present,
        "status": status,
        "authority": "observability_non_authoritative",
        "source": "spark_os_system_map.trace_health",
        "source_ref": spark_system_map.get("source_ref") or "spark os compile",
        "health_flags": health_flags,
        "counts": counts,
        "top_missing_trace_ref_sources": rows,
        "top_orphan_parent_sources": orphan_rows,
        "trace_topology": {
            "present": bool(trace_topology.get("present")),
            "group_count": _int(trace_topology.get("group_count")),
            "projected_group_count": _int(trace_topology.get("projected_group_count")),
            "parent_link_count": _int(trace_topology.get("parent_link_count")),
            "orphan_parent_event_count": _int(trace_topology.get("orphan_parent_event_count")),
            "edge_sample_count": _int(trace_topology.get("edge_sample_count")),
            "groups": [_dict(group) for group in _list(trace_topology.get("groups"))[:3]],
            "claim_boundary": trace_topology.get("claim_boundary"),
        },
        "recent_windows": recent_windows,
        "next_actions": _trace_repair_next_actions(
            present=present,
            counts=counts,
            top_sources=rows,
            orphan_sources=orphan_rows,
            recent_windows=recent_windows,
        ),
        "claim_boundary": (
            "Trace repair queue is black-box observability guidance. It ranks trace propagation gaps only; "
            "it is not task outcome, memory truth, or permission evidence."
        ),
    }


def _trace_repair_status(*, present: bool, counts: dict[str, int]) -> str:
    if not present:
        return "missing"
    if (
        int(counts.get("missing_trace_ref_count") or 0)
        or int(counts.get("high_severity_open_count") or 0)
        or int(counts.get("orphan_parent_event_id_count") or 0)
    ):
        return "needs_repair"
    return "healthy"


def _trace_repair_next_actions(
    *,
    present: bool,
    counts: dict[str, int],
    top_sources: list[dict[str, Any]],
    orphan_sources: list[dict[str, Any]],
    recent_windows: list[dict[str, Any]],
) -> list[str]:
    if not present:
        return ["Run `spark os compile` before using trace health as operating-panel evidence."]

    actions: list[str] = []
    if int(counts.get("missing_trace_ref_count") or 0):
        if top_sources:
            first = top_sources[0]
            actions.append(
                "Repair trace propagation at the top producer boundary: "
                f"{first.get('component') or '[missing]'}/{first.get('event_type') or '[missing]'}."
            )
        else:
            actions.append("Repair missing trace propagation in builder event producers.")
    if int(counts.get("high_severity_open_count") or 0):
        actions.append("Resolve open high-severity events before treating black-box health as launch evidence.")
    if int(counts.get("orphan_parent_event_id_count") or 0):
        if orphan_sources:
            first_orphan = orphan_sources[0]
            actions.append(
                "Repair parent propagation at the top orphan boundary: "
                f"{first_orphan.get('component') or '[missing]'}/{first_orphan.get('event_type') or '[missing]'}."
            )
        else:
            actions.append("Repair parent propagation for events with missing parent links.")
    if recent_windows:
        actions.append("After fresh traffic, rerun `spark os compile` and compare recent-window ratios.")
    if not actions:
        actions.append("Keep trace propagation checks in the operating panel during new producer work.")
    return actions


def _trace_repair_text(trace_repair_queue: dict[str, Any]) -> str:
    if not trace_repair_queue.get("present"):
        return "Trace repair: missing; run spark os compile"
    counts = _dict(trace_repair_queue.get("counts"))
    top_sources = [_dict(row) for row in _list(trace_repair_queue.get("top_missing_trace_ref_sources"))]
    recent_windows = [_dict(row) for row in _list(trace_repair_queue.get("recent_windows"))]
    top = top_sources[0] if top_sources else {}
    top_text = (
        f"; top {top.get('component') or '[missing]'}/{top.get('event_type') or '[missing]'} "
        f"({int(top.get('event_count') or 0)})"
        if top
        else ""
    )
    window = _preferred_recent_window(recent_windows)
    window_text = f"; {window.get('window')} {_trace_window_ratio_text(window)}" if window else ""
    return (
        "Trace repair: "
        f"{trace_repair_queue.get('status') or 'unknown'}, "
        f"missing refs={int(counts.get('missing_trace_ref_count') or 0)}, "
        f"high severity={int(counts.get('high_severity_open_count') or 0)}, "
        f"orphan parents={int(counts.get('orphan_parent_event_id_count') or 0)}"
        f"{top_text}{window_text}"
    )


def _capability_garden_text(capability_garden: dict[str, Any]) -> str:
    if not capability_garden.get("present"):
        return "Capability garden: missing; run spark os compile"
    status_counts = _dict(capability_garden.get("status_counts"))
    return (
        "Capability garden: "
        f"{int(capability_garden.get('card_count') or 0)} cards, "
        f"local artifacts={int(status_counts.get('local-artifacts') or 0)}, "
        f"schema-shaped={int(status_counts.get('schema-shaped') or 0)}, "
        f"seen={int(status_counts.get('seen') or 0)}"
    )


def _authority_status_text(authority_status: dict[str, Any]) -> str:
    if not authority_status.get("present"):
        return "Authority status: missing; run spark os compile"
    return (
        "Authority status: "
        f"L{int(authority_status.get('default_access_level') or 0)} "
        f"{authority_status.get('default_sandbox_lane') or 'unknown'}, "
        f"Telegram profiles={int(authority_status.get('telegram_profile_count') or 0)}, "
        f"Spawner lanes={int(authority_status.get('spawner_lane_count') or 0)}, "
        f"browser approvals={int(authority_status.get('browser_approval_required_hook_count') or 0)}, "
        f"publication checks={int(authority_status.get('publication_checks_required') or 0)}"
    )


def _preferred_recent_window(recent_windows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in recent_windows:
        if row.get("window") == "24h":
            return row
    return recent_windows[0] if recent_windows else {}


def _trace_window_ratio_text(window: dict[str, Any]) -> str:
    row_count = int(window.get("row_count") or 0)
    missing_count = int(window.get("missing_trace_ref_count") or 0)
    ratio = _float(window.get("missing_trace_ref_ratio"))
    return f"missing {missing_count}/{row_count} ({ratio:.1%})"


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
