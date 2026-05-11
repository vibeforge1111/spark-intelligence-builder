from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


AGENT_SCRATCHPAD_SCHEMA_VERSION = "spark.agent_scratchpad.v1"


@dataclass(frozen=True)
class AgentScratchpad:
    current_goal: str
    current_mode: str
    active_constraints: list[str] = field(default_factory=list)
    open_blockers: list[str] = field(default_factory=list)
    next_safe_action: str = "answer_in_chat"
    do_not_do: list[str] = field(default_factory=list)
    source_policy: str = (
        "Operational scratchpad is derived from the latest AOC payload; it is a boundary summary, not hidden reasoning."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_SCRATCHPAD_SCHEMA_VERSION,
            "current_goal": self.current_goal,
            "current_mode": self.current_mode,
            "active_constraints": list(self.active_constraints),
            "open_blockers": list(self.open_blockers),
            "next_safe_action": self.next_safe_action,
            "do_not_do": list(self.do_not_do),
            "source_policy": self.source_policy,
        }


def build_agent_scratchpad(aoc_payload: dict[str, Any]) -> AgentScratchpad:
    frame = _dict(aoc_payload.get("conversation_frame"))
    task_fit = _dict(aoc_payload.get("task_fit"))
    runner = _dict(aoc_payload.get("runner"))
    routes = [_dict(route) for route in _list(aoc_payload.get("routes"))]
    agent_needs = [_dict(need) for need in _list(aoc_payload.get("agent_needs"))]

    current_mode = str(frame.get("current_mode") or "unknown")
    allowed_actions = _strings(frame.get("allowed_next_actions"))
    disallowed_actions = _strings(frame.get("disallowed_next_actions"))
    must_confirm = _strings(frame.get("must_confirm_before"))
    recommended_route = str(task_fit.get("recommended_route") or "")

    do_not_do = list(disallowed_actions)
    if "saving_memory" in must_confirm:
        do_not_do.append("save_memory_without_approval")
    if runner.get("writable") is False:
        do_not_do.append("claim_patch_files_here")
    if _route_status(routes, "spark_browser") != "healthy":
        do_not_do.append("claim_live_web_inspection_without_probe")

    return AgentScratchpad(
        current_goal=_current_goal(current_mode=current_mode, task_fit=task_fit),
        current_mode=current_mode,
        active_constraints=_dedupe(
            [
                "latest_user_message_wins",
                f"runner:{_runner_state(runner)}",
                "memory_requires_approval" if "saving_memory" in must_confirm else "",
                "do_not_claim_live_probe_without_evidence",
            ]
        ),
        open_blockers=_dedupe(
            [
                *_strings(task_fit.get("blocked_here_by")),
                *[
                    str(need.get("need") or "")
                    for need in agent_needs
                    if str(need.get("status") or "") in {"needed", "always_required"}
                ],
            ]
        ),
        next_safe_action=_next_safe_action(
            recommended_route=recommended_route,
            allowed_actions=allowed_actions,
            runner=runner,
        ),
        do_not_do=_dedupe(do_not_do),
    )


def _current_goal(*, current_mode: str, task_fit: dict[str, Any]) -> str:
    if current_mode == "patch_work":
        if task_fit.get("recommended_route") == "writable_spawner_codex_mission":
            return "Route the requested code/file work to a writable Spawner/Codex mission."
        return "Complete the requested code/file work inside the allowed runner."
    if current_mode == "concept_chat":
        return "Answer the latest user turn in chat."
    if current_mode == "diagnostics":
        return "Run only the diagnostics the latest user turn authorized."
    if current_mode == "mission_control":
        return "Open or discuss Mission Control only as requested by the latest user turn."
    return f"Handle the latest user turn in {current_mode} mode."


def _next_safe_action(*, recommended_route: str, allowed_actions: list[str], runner: dict[str, Any]) -> str:
    if recommended_route == "writable_spawner_codex_mission" and runner.get("writable") is False:
        return "start_or_route_to_writable_spawner_codex_mission"
    if allowed_actions:
        return allowed_actions[0]
    return "answer_in_chat"


def _runner_state(runner: dict[str, Any]) -> str:
    if runner.get("writable") is True:
        return "writable"
    if runner.get("writable") is False:
        return "read_only"
    return "unknown"


def _route_status(routes: list[dict[str, Any]], key: str) -> str:
    for route in routes:
        if str(route.get("key") or "") == key:
            return str(route.get("status") or "unknown")
    return "unknown"


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped
