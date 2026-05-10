from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


CurrentMode = Literal[
    "concept_chat",
    "code_inspection",
    "patch_work",
    "mission_control",
    "diagnostics",
    "research",
    "memory_review",
    "unknown",
]
UserIntent = Literal[
    "answer",
    "inspect",
    "edit",
    "run",
    "recall",
    "diagnose",
    "approve_memory",
    "unknown",
]
GateDecision = Literal["allowed", "blocked", "requires_confirmation"]


@dataclass(frozen=True)
class ConversationOperatingFrame:
    current_mode: CurrentMode
    user_intent: UserIntent
    latest_user_message_summary: str
    allowed_next_actions: list[str] = field(default_factory=list)
    disallowed_next_actions: list[str] = field(default_factory=list)
    active_reference_list: dict[str, Any] = field(default_factory=dict)
    must_confirm_before: list[str] = field(default_factory=list)
    source_policy: str = "latest_user_message_wins"
    expires_at: str = "end_of_turn"

    def to_payload(self) -> dict[str, Any]:
        return {
            "current_mode": self.current_mode,
            "user_intent": self.user_intent,
            "latest_user_message_summary": self.latest_user_message_summary,
            "allowed_next_actions": list(self.allowed_next_actions),
            "disallowed_next_actions": list(self.disallowed_next_actions),
            "active_reference_list": dict(self.active_reference_list),
            "must_confirm_before": list(self.must_confirm_before),
            "source_policy": self.source_policy,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ActionGateResult:
    proposed_action: str
    decision: GateDecision
    reason: str
    safe_next_action: str

    def to_payload(self) -> dict[str, str]:
        return {
            "proposed_action": self.proposed_action,
            "decision": self.decision,
            "reason": self.reason,
            "safe_next_action": self.safe_next_action,
        }


@dataclass(frozen=True)
class FinalAnswerDriftCheck:
    user_asked: str
    answer_does: str
    match: bool
    drift_type: str | None = None
    rewrite_required: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_asked": self.user_asked,
            "answer_does": self.answer_does,
            "match": self.match,
            "drift_type": self.drift_type,
            "rewrite_required": self.rewrite_required,
        }


def build_conversation_operating_frame(
    *,
    user_message: str,
    active_reference_items: list[str] | None = None,
    source_turn_id: str | None = None,
) -> ConversationOperatingFrame:
    normalized = _normalize_text(user_message)
    lowered = normalized.casefold()
    current_mode, user_intent = _classify_mode_and_intent(lowered)
    return ConversationOperatingFrame(
        current_mode=current_mode,
        user_intent=user_intent,
        latest_user_message_summary=_summarize_user_message(normalized),
        allowed_next_actions=_allowed_actions(current_mode),
        disallowed_next_actions=_disallowed_actions(current_mode),
        active_reference_list=_active_reference_payload(
            normalized,
            active_reference_items=list(active_reference_items or []),
            source_turn_id=source_turn_id,
        ),
        must_confirm_before=["saving_memory", "destructive_action", "external_posting"],
    )


def evaluate_action_gate(
    frame: ConversationOperatingFrame,
    *,
    proposed_action: str,
    user_explicitly_requested_action: bool | None = None,
) -> ActionGateResult:
    action = _normalize_action(proposed_action)
    explicit = _action_is_explicit(frame, action) if user_explicitly_requested_action is None else user_explicitly_requested_action
    if action in frame.must_confirm_before:
        return ActionGateResult(
            proposed_action=action,
            decision="requires_confirmation",
            reason=f"{action} requires explicit confirmation before proceeding.",
            safe_next_action="ask_confirmation",
        )
    if action in frame.disallowed_next_actions:
        return ActionGateResult(
            proposed_action=action,
            decision="blocked",
            reason=f"Current mode is {frame.current_mode} and the latest user turn did not authorize {action}.",
            safe_next_action=_safe_next_action(frame),
        )
    if action not in frame.allowed_next_actions and not explicit:
        return ActionGateResult(
            proposed_action=action,
            decision="blocked",
            reason=f"{action} is outside the allowed next actions for {frame.current_mode}.",
            safe_next_action=_safe_next_action(frame),
        )
    return ActionGateResult(
        proposed_action=action,
        decision="allowed",
        reason=f"{action} matches {frame.current_mode}/{frame.user_intent}.",
        safe_next_action=action,
    )


def check_final_answer_drift(
    frame: ConversationOperatingFrame,
    *,
    draft_answer: str,
) -> FinalAnswerDriftCheck:
    answer = _normalize_text(draft_answer)
    lowered = answer.casefold()
    if frame.current_mode == "concept_chat" and _looks_like_unrequested_mission_status(lowered):
        return FinalAnswerDriftCheck(
            user_asked=frame.latest_user_message_summary,
            answer_does="Starts or reports mission/build execution.",
            match=False,
            drift_type="unrequested_mission_status",
            rewrite_required=True,
        )
    if frame.current_mode == "concept_chat" and _looks_like_unrequested_diagnostics_plan(lowered):
        return FinalAnswerDriftCheck(
            user_asked=frame.latest_user_message_summary,
            answer_does="Switches to diagnostics or self-improvement probe planning.",
            match=False,
            drift_type="unrequested_diagnostics_plan",
            rewrite_required=True,
        )
    if "live probe" in lowered and "claim_live_probe" in frame.disallowed_next_actions:
        return FinalAnswerDriftCheck(
            user_asked=frame.latest_user_message_summary,
            answer_does="Claims live probe evidence.",
            match=False,
            drift_type="unsupported_live_probe_claim",
            rewrite_required=True,
        )
    return FinalAnswerDriftCheck(
        user_asked=frame.latest_user_message_summary,
        answer_does=_summarize_answer(answer),
        match=True,
    )


def _classify_mode_and_intent(lowered: str) -> tuple[CurrentMode, UserIntent]:
    if _negates_mission_control(lowered):
        return "concept_chat", "answer"
    if _is_conceptual_design_question(lowered):
        return "concept_chat", "answer"
    if _has_any(lowered, ("approve memory", "memory inbox", "save this memory", "remember this")):
        return "memory_review", "approve_memory"
    if _has_any(lowered, ("research web", "browse web", "look up", "latest ", "search the web")):
        return "research", "run"
    if _has_any(lowered, ("run diagnostics", "diagnose route", "/probe", "route probe", "health probe")):
        return "diagnostics", "diagnose"
    if _has_any(lowered, ("open mission control", "show mission control", "/mission", "mission board")):
        return "mission_control", "run"
    if _has_any(lowered, ("inspect code", "read the repo", "look at the repo", "check the files")):
        return "code_inspection", "inspect"
    if _has_any(lowered, ("fix", "patch", "implement", "edit", "commit", "build it", "let's do it", "lets do it")):
        return "patch_work", "edit"
    if _has_any(lowered, ("what do you remember", "recall", "what did i tell you")):
        return "memory_review", "recall"
    return "concept_chat", "answer"


def _is_conceptual_design_question(lowered: str) -> bool:
    conceptual = _has_any(
        lowered,
        (
            "how should",
            "how would",
            "how could",
            "how can",
            "can we",
            "could we",
            "should we",
            "conceptually",
            "before coding",
            "fit into",
            "without becoming",
            "design",
        ),
    )
    if not conceptual:
        return False
    if not re.search(
        r"\b(?:aoc|agent operating context|mission board|mission control|route probe|health probe|route hijack|deterministic|access level|runner|sandbox(?:es|ed)?|workspace|docker|ssh|modal)\b",
        lowered,
    ):
        return False
    explicit_action = _has_any(
        lowered,
        (
            "/probe",
            "run diagnostics",
            "diagnose route",
            "open mission control",
            "show mission control",
            "show mission board",
            "mission board status",
            "patch this",
            "implement this",
            "edit the files",
            "commit this",
        ),
    )
    conceptual_override = _has_any(lowered, ("conceptually", "before coding", "fit into", "without becoming", "design"))
    return conceptual_override or not explicit_action


def _active_reference_payload(
    user_message: str,
    *,
    active_reference_items: list[str],
    source_turn_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_turn_id": source_turn_id,
        "items": list(active_reference_items),
        "resolution_status": "not_requested",
        "selected_index": None,
        "selected_item": None,
    }
    selected_index = _selected_option_index(user_message)
    if selected_index is None:
        return payload
    payload["selected_index"] = selected_index
    zero_based_index = selected_index - 1
    if 0 <= zero_based_index < len(active_reference_items):
        payload["resolution_status"] = "resolved"
        payload["selected_item"] = active_reference_items[zero_based_index]
    else:
        payload["resolution_status"] = "out_of_range"
    return payload


def _selected_option_index(user_message: str) -> int | None:
    match = re.search(r"\b(?:option|choice|#)\s*(\d{1,2})\b", user_message.casefold())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _allowed_actions(current_mode: CurrentMode) -> list[str]:
    base = ["answer_in_chat"]
    by_mode = {
        "concept_chat": ["update_planning_doc"],
        "code_inspection": ["inspect_code", "answer_in_chat"],
        "patch_work": ["inspect_code", "edit_files", "run_tests", "commit_changes"],
        "mission_control": ["open_mission_control", "answer_in_chat"],
        "diagnostics": ["run_diagnostics", "run_route_probe", "answer_in_chat"],
        "research": ["research_web", "answer_in_chat"],
        "memory_review": ["review_memory", "answer_in_chat"],
        "unknown": [],
    }
    actions = [*base, *by_mode.get(current_mode, [])]
    return list(dict.fromkeys(actions))


def _disallowed_actions(current_mode: CurrentMode) -> list[str]:
    route_changing = [
        "open_mission_control",
        "start_mission",
        "run_diagnostics",
        "run_route_probe",
        "claim_live_probe",
        "edit_files",
        "run_tests",
        "commit_changes",
    ]
    if current_mode == "concept_chat":
        return route_changing
    return []


def _action_is_explicit(frame: ConversationOperatingFrame, action: str) -> bool:
    summary = frame.latest_user_message_summary.casefold()
    explicit_markers = {
        "open_mission_control": ("open mission control", "show mission control"),
        "start_mission": ("start mission", "run mission"),
        "run_diagnostics": ("run diagnostics", "diagnose"),
        "run_route_probe": ("route probe", "/probe"),
        "edit_files": ("patch", "fix", "implement", "edit"),
        "commit_changes": ("commit",),
    }
    return any(marker in summary for marker in explicit_markers.get(action, (action.replace("_", " "),)))


def _safe_next_action(frame: ConversationOperatingFrame) -> str:
    if frame.allowed_next_actions:
        return frame.allowed_next_actions[0]
    return "answer_in_chat"


def _looks_like_unrequested_mission_status(lowered_answer: str) -> bool:
    return "spark picked up the build" in lowered_answer or "mission-" in lowered_answer or "mission board:" in lowered_answer


def _looks_like_unrequested_diagnostics_plan(lowered_answer: str) -> bool:
    return (
        "spark self-improvement plan" in lowered_answer
        or "mode: plan_only_probe_first" in lowered_answer
        or "run the safest probe" in lowered_answer
    )


def _summarize_user_message(text: str) -> str:
    return _compact(text or "no user message supplied", 220)


def _summarize_answer(text: str) -> str:
    return _compact(text or "empty answer", 220)


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_action(value: str) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _negates_mission_control(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "do not open mission control",
            "don't open mission control",
            "dont open mission control",
            "never open mission control",
            "do not launch mission control",
            "don't launch mission control",
            "dont launch mission control",
        )
    )


def _compact(text: str, limit: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."
