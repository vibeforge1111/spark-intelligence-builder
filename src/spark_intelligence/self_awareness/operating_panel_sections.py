from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


AGENT_PANEL_SECTIONS_SCHEMA_VERSION = "spark.agent_panel_sections.v1"


@dataclass(frozen=True)
class AgentPanelSection:
    section_id: str
    title: str
    status: str
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "status": self.status,
            "items": [dict(item) for item in self.items],
        }


@dataclass(frozen=True)
class AgentPanelSections:
    sections: list[AgentPanelSection]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_PANEL_SECTIONS_SCHEMA_VERSION,
            "sections": [section.to_payload() for section in self.sections],
        }


def build_agent_panel_sections(
    *,
    aoc_payload: dict[str, Any],
    scratchpad_payload: dict[str, Any],
    black_box_payload: dict[str, Any],
    source_ledger_payload: dict[str, Any],
    stale_sweep_payload: dict[str, Any],
    trace_repair_payload: dict[str, Any] | None = None,
) -> AgentPanelSections:
    access = _dict(aoc_payload.get("access"))
    runner = _dict(aoc_payload.get("runner"))
    access_automation = _dict(aoc_payload.get("access_automation"))
    frame = _dict(aoc_payload.get("conversation_frame"))
    task_fit = _dict(aoc_payload.get("task_fit"))
    routes = [_dict(route) for route in _list(aoc_payload.get("routes"))]
    agent_needs = [_dict(need) for need in _list(aoc_payload.get("agent_needs"))]
    black_box_counts = _dict(black_box_payload.get("counts"))
    black_box_entries = [_dict(entry) for entry in _list(black_box_payload.get("entries"))]
    stale_counts = _dict(stale_sweep_payload.get("counts"))
    trace_repair = _dict(trace_repair_payload)
    spark_system_map = _dict(aoc_payload.get("spark_system_map"))
    authority_status = _dict(spark_system_map.get("authority_status"))
    capability_garden = _dict(spark_system_map.get("capability_garden"))
    return AgentPanelSections(
        sections=[
            AgentPanelSection(
                section_id="permissions",
                title="Permissions",
                status="known" if access.get("spark_access_level") else "unknown",
                items=[
                    _item("Access", access.get("label") or "unknown"),
                    _item("Local workspace", _yes_no_unknown(access.get("local_workspace_allowed"))),
                    _item("Destructive actions", "require approval"),
                ],
            ),
            AgentPanelSection(
                section_id="runner_capability",
                title="Runner Capability",
                status=_runner_status(runner),
                items=[
                    _item("Runner", runner.get("label") or "unknown"),
                    _item("Can edit files", _yes_no_unknown(runner.get("writable"))),
                    _item("Can run tests", _yes_no_unknown(runner.get("writable"))),
                    _item("Can start server", _yes_no_unknown(runner.get("writable"))),
                ],
            ),
            AgentPanelSection(
                section_id="route_health",
                title="Route Health",
                status=_route_section_status(routes),
                items=[
                    _item(str(route.get("label") or route.get("key") or "unknown"), route.get("status") or "unknown")
                    for route in routes[:8]
                ],
            ),
            AgentPanelSection(
                section_id="current_task_fit",
                title="Current Task Fit",
                status=str(task_fit.get("recommended_route") or "unknown"),
                items=[
                    _item("Task", frame.get("latest_user_message_summary") or "unknown"),
                    _item("Recommended route", task_fit.get("recommended_route_label") or "unknown"),
                    _item("Blocked here by", ", ".join(_strings(task_fit.get("blocked_here_by"))) or "none"),
                    _item("Safe next action", scratchpad_payload.get("next_safe_action") or "answer_in_chat"),
                ],
            ),
            AgentPanelSection(
                section_id="access_automation",
                title="Access Automation",
                status=str(access_automation.get("recommended_run_policy") or "unknown"),
                items=[
                    _item("Next safe access action", access_automation.get("next_safe_access_action") or "unknown"),
                    _item("Recommended lane", access_automation.get("recommended_lane") or "unknown"),
                    _item("Run policy", access_automation.get("recommended_run_policy") or "unknown"),
                    _item("Confirmation required", _yes_no_unknown(access_automation.get("requires_confirmation"))),
                    _item("Auto-run allowed", _yes_no_unknown(access_automation.get("allowed_to_auto_run"))),
                    _item("Boundary", access_automation.get("claim_boundary") or "read-only policy context"),
                ],
            ),
            AgentPanelSection(
                section_id="authority_status",
                title="Authority Status",
                status=_authority_status(authority_status),
                items=[
                    _item("Authority", authority_status.get("authority") or "observability_non_authoritative"),
                    _item("Default access level", int(authority_status.get("default_access_level") or 0)),
                    _item("Default sandbox lane", authority_status.get("default_sandbox_lane") or "unknown"),
                    _item("Telegram profiles", int(authority_status.get("telegram_profile_count") or 0)),
                    _item("Spawner lanes", int(authority_status.get("spawner_lane_count") or 0)),
                    _item("Browser hooks", int(authority_status.get("browser_hook_count") or 0)),
                    _item(
                        "Browser approval-required hooks",
                        int(authority_status.get("browser_approval_required_hook_count") or 0),
                    ),
                    _item("Toxic capability pairs", int(authority_status.get("toxic_pair_count") or 0)),
                    _item("Publication checks", int(authority_status.get("publication_checks_required") or 0)),
                    _item("Required publication checks", _list(authority_status.get("required_publication_checks"))),
                    _item("Trace verdicts", int(authority_status.get("trace_verdict_count") or 0)),
                    _item("Verdict counts", _dict(authority_status.get("trace_verdict_counts"))),
                    _item("Verdict actions", _dict(authority_status.get("trace_verdict_action_family_counts"))),
                    _item("Boundary", authority_status.get("claim_boundary") or "compiled policy evidence only"),
                ],
            ),
            AgentPanelSection(
                section_id="source_ledger",
                title="Source Ledger",
                status=_source_section_status(source_ledger_payload),
                items=[
                    _item(
                        str(item.get("source") or "unknown"),
                        f"{item.get('freshness') or 'unknown'}; present={bool(item.get('present'))}",
                    )
                    for item in _list(source_ledger_payload.get("items"))
                    if isinstance(item, dict)
                ],
            ),
            AgentPanelSection(
                section_id="trace_repair_queue",
                title="Trace Repair Queue",
                status=str(trace_repair.get("status") or "missing"),
                items=[
                    _item("Authority", trace_repair.get("authority") or "observability_non_authoritative"),
                    _item("Health flags", len(_list(trace_repair.get("health_flags")))),
                    _item("Missing trace refs", _trace_count(trace_repair, "missing_trace_ref_count")),
                    _item("High severity open", _trace_count(trace_repair, "high_severity_open_count")),
                    _item("Orphan parent events", _trace_count(trace_repair, "orphan_parent_event_id_count")),
                    _item("Trace topology groups", _trace_count(trace_repair, "topology_group_count")),
                    *[
                        _item(
                            f"{row.get('component') or '[missing]'}/{row.get('event_type') or '[missing]'}",
                            {
                                "event_count": int(row.get("event_count") or 0),
                                "status": row.get("status") or "[missing]",
                                "severity": row.get("severity") or "[missing]",
                                "target_surface": row.get("target_surface") or "[missing]",
                                "evidence_lane": row.get("evidence_lane") or "[missing]",
                            },
                        )
                        for row in [_dict(item) for item in _list(trace_repair.get("top_missing_trace_ref_sources"))[:3]]
                    ],
                    *[
                        _item(
                            f"Orphan {row.get('component') or '[missing]'}/{row.get('event_type') or '[missing]'}",
                            {
                                "event_count": int(row.get("event_count") or 0),
                                "status": row.get("status") or "[missing]",
                                "severity": row.get("severity") or "[missing]",
                                "target_surface": row.get("target_surface") or "[missing]",
                                "evidence_lane": row.get("evidence_lane") or "[missing]",
                            },
                        )
                        for row in [_dict(item) for item in _list(trace_repair.get("top_orphan_parent_sources"))[:3]]
                    ],
                    *[
                        _item(
                            f"Window {row.get('window') or 'unknown'}",
                            {
                                "row_count": int(row.get("row_count") or 0),
                                "missing_trace_ref_count": int(row.get("missing_trace_ref_count") or 0),
                                "missing_trace_ref_ratio": float(row.get("missing_trace_ref_ratio") or 0.0),
                            },
                        )
                        for row in [_dict(item) for item in _list(trace_repair.get("recent_windows"))[:3]]
                    ],
                    _item("Boundary", trace_repair.get("claim_boundary") or "observability guidance only"),
                ],
            ),
            AgentPanelSection(
                section_id="capability_garden",
                title="Capability Garden",
                status=_capability_garden_status(capability_garden),
                items=[
                    _item("Authority", "observability_non_authoritative"),
                    _item("Capability cards", int(capability_garden.get("card_count") or 0)),
                    _item("Creator system surfaces", int(capability_garden.get("creator_system_surfaces") or 0)),
                    _item("Specialization path surfaces", int(capability_garden.get("specialization_path_surfaces") or 0)),
                    _item("Statuses", _dict(capability_garden.get("status_counts"))),
                    _item("Trust statuses", _dict(capability_garden.get("trust_counts"))),
                    _item("Proof states", _dict(capability_garden.get("proof_state_counts"))),
                    _item("Top proof gap", capability_garden.get("top_missing_proof") or "none"),
                    *[
                        _item(
                            str(card.get("id") or card.get("name") or "capability"),
                            {
                                "status": card.get("status") or "unknown",
                                "trust_status": card.get("trust_status") or "untrusted",
                                "proof_state": card.get("proof_state") or "missing",
                                "surface_type": card.get("surface_type") or "unknown",
                                "owner_repo": card.get("owner_repo") or "unknown",
                                "blocker_count": len(_list(card.get("blockers"))),
                                "missing_proof_count": len(_list(card.get("missing_proofs"))),
                                "next_action": card.get("next_action") or "",
                            },
                        )
                        for card in [_dict(item) for item in _list(capability_garden.get("cards"))[:5]]
                    ],
                    _item("Boundary", capability_garden.get("claim_boundary") or "metadata-only capability projection"),
                ],
            ),
            AgentPanelSection(
                section_id="black_box_recorder",
                title="Black Box Recorder",
                status="present" if int(black_box_counts.get("entries") or 0) else "clear",
                items=[
                    _item("Entries", int(black_box_counts.get("entries") or 0)),
                    _item("Blocker events", int(black_box_counts.get("blocker_events") or 0)),
                    _item("Memory candidates", int(black_box_counts.get("memory_candidates") or 0)),
                    *[
                        _item(
                            str(entry.get("event_type") or "event"),
                            {
                                "perceived_intent": entry.get("perceived_intent"),
                                "route_chosen": entry.get("route_chosen"),
                                "blockers": entry.get("blockers") or [],
                                "changed": entry.get("changed") or [],
                                "summary": entry.get("summary") or "",
                            },
                        )
                        for entry in black_box_entries[:3]
                    ],
                ],
            ),
            AgentPanelSection(
                section_id="contradictions",
                title="Contradictions",
                status="needs_review"
                if int(stale_counts.get("stale") or 0) or int(stale_counts.get("contradicted") or 0)
                else "clear",
                items=[
                    _item("Stale", int(stale_counts.get("stale") or 0)),
                    _item("Contradicted", int(stale_counts.get("contradicted") or 0)),
                    _item("Recorded contradictions", int(stale_counts.get("recorded_contradictions") or 0)),
                ],
            ),
            AgentPanelSection(
                section_id="what_rec_needs",
                title="What Rec Needs",
                status="needed" if agent_needs else "clear",
                items=[
                    _item(
                        str(need.get("need") or "unknown"),
                        {
                            "status": str(need.get("status") or "unknown"),
                            "next_action": str(need.get("next_action") or ""),
                            "reason": str(need.get("reason") or ""),
                        },
                    )
                    for need in agent_needs
                ],
            ),
            AgentPanelSection(
                section_id="agent_instruction",
                title="Agent Instruction",
                status=str(scratchpad_payload.get("current_mode") or "unknown"),
                items=[
                    _item("Current goal", scratchpad_payload.get("current_goal") or "unknown"),
                    _item("Next safe action", scratchpad_payload.get("next_safe_action") or "answer_in_chat"),
                    _item("Do not do", ", ".join(_strings(scratchpad_payload.get("do_not_do"))) or "none"),
                ],
            ),
        ]
    )


def _item(label: str, value: object) -> dict[str, Any]:
    return {"label": str(label), "value": value}


def _runner_status(runner: dict[str, Any]) -> str:
    if runner.get("writable") is True:
        return "writable"
    if runner.get("writable") is False:
        return "read_only"
    return "unknown"


def _route_section_status(routes: list[dict[str, Any]]) -> str:
    if any(bool(route.get("degraded")) for route in routes):
        return "degraded"
    if any(str(route.get("status") or "") in {"missing", "unavailable", "unknown"} for route in routes):
        return "needs_probe"
    return "healthy"


def _source_section_status(source_ledger_payload: dict[str, Any]) -> str:
    counts = _dict(source_ledger_payload.get("counts"))
    if int(counts.get("contradicted") or 0):
        return "contradicted"
    if int(counts.get("stale") or 0):
        return "stale"
    return "fresh"


def _capability_garden_status(capability_garden: dict[str, Any]) -> str:
    if not capability_garden.get("present"):
        return "missing"
    status_counts = _dict(capability_garden.get("status_counts"))
    if int(status_counts.get("local-artifacts") or 0):
        return "review_needed"
    if int(capability_garden.get("card_count") or 0):
        return "observed"
    return "empty"


def _authority_status(authority_status: dict[str, Any]) -> str:
    if not authority_status.get("present"):
        return "missing"
    if int(authority_status.get("browser_approval_required_hook_count") or 0) or int(
        authority_status.get("publication_checks_required") or 0
    ):
        return "gated"
    return "observed"


def _trace_count(trace_repair_payload: dict[str, Any], key: str) -> int:
    counts = _dict(trace_repair_payload.get("counts"))
    try:
        return int(counts.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _yes_no_unknown(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]
