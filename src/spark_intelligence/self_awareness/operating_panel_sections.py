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
