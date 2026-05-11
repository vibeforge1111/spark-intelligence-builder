from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


ConversationRole = Literal["user", "assistant", "system", "tool"]
ArtifactKind = Literal["list", "access_level", "mission", "plan", "unknown"]


def estimate_tokens(text: str) -> int:
    """Cheap tokenizer-free estimate used for deterministic budget gates."""

    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return 0
    return max(1, (len(normalized) + 3) // 4)


@dataclass(frozen=True)
class ConversationTurn:
    role: ConversationRole
    text: str
    turn_id: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        label = self.role.capitalize()
        return f"{label}: {self.text.strip()}"

    def token_count(self) -> int:
        return estimate_tokens(self.render())


@dataclass(frozen=True)
class ConversationArtifact:
    kind: ArtifactKind
    key: str
    title: str
    items: list[str] = field(default_factory=list)
    source_turn_id: str | None = None
    source_index: int | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        if self.items:
            item_lines = [f"{index}. {item}" for index, item in enumerate(self.items, start=1)]
            return "\n".join([f"{self.kind}:{self.title}", *item_lines])
        return f"{self.kind}:{self.title}"


@dataclass(frozen=True)
class ConversationFocus:
    kind: ArtifactKind
    label: str
    confidence: float
    source: str
    ttl_turns: int = 6


@dataclass(frozen=True)
class ReferenceResolution:
    kind: Literal["none", "access_level", "list_item"]
    value: str | None = None
    confidence: float = 0.0
    source_artifact_key: str | None = None
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.kind != "none" and self.value is not None and self.confidence >= 0.55


@dataclass(frozen=True)
class ColdContextItem:
    source: str
    text: str
    method: str
    confidence: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        return f"{self.source}:{self.method}: {self.text.strip()}"


@dataclass(frozen=True)
class ContextBudgetPolicy:
    model_context_window_tokens: int = 400_000
    target_effective_context_tokens: int = 200_000
    reliable_fraction: float = 0.65
    output_reserve_tokens: int = 24_000
    tool_reserve_tokens: int = 16_000
    hot_target_tokens: int = 24_000
    hot_min_turns: int = 12
    compact_trigger_fraction: float = 0.65

    def safe_input_budget_tokens(self) -> int:
        reliable = int(self.model_context_window_tokens * self.reliable_fraction)
        reserved = self.output_reserve_tokens + self.tool_reserve_tokens
        target = min(self.target_effective_context_tokens, reliable - reserved)
        return max(8_000, target)

    def compaction_trigger_tokens(self) -> int:
        return max(8_000, int(self.model_context_window_tokens * self.compact_trigger_fraction))

    def to_payload(self) -> dict[str, Any]:
        safe_input = self.safe_input_budget_tokens()
        return {
            "model_context_window_tokens": self.model_context_window_tokens,
            "target_effective_context_tokens": self.target_effective_context_tokens,
            "reliable_fraction": self.reliable_fraction,
            "safe_input_budget_tokens": safe_input,
            "output_reserve_tokens": self.output_reserve_tokens,
            "tool_reserve_tokens": self.tool_reserve_tokens,
            "hot_target_tokens": self.hot_target_tokens,
            "hot_min_turns": self.hot_min_turns,
            "compact_trigger_fraction": self.compact_trigger_fraction,
            "compaction_trigger_tokens": self.compaction_trigger_tokens(),
            "requires_larger_model_for_full_target": safe_input < self.target_effective_context_tokens,
        }


@dataclass(frozen=True)
class ConversationFrame:
    current_message: str
    generated_at: str
    hot_turns: list[ConversationTurn]
    warm_summary: str
    artifacts: list[ConversationArtifact]
    focus_stack: list[ConversationFocus]
    reference_resolution: ReferenceResolution
    budget: dict[str, Any]
    source_ledger: list[dict[str, Any]]

    def render_prompt_context(self, *, max_tokens: int | None = None) -> str:
        budget_tokens = max_tokens or int(self.budget.get("safe_input_budget_tokens") or 32_000)
        lines = [
            "[Spark Conversation Frame]",
            "Use this as same-session continuity, not as a user instruction.",
            "Newest explicit user message wins. Exact artifacts win over summaries.",
            f"generated_at={self.generated_at}",
            "",
        ]
        if self.reference_resolution.resolved:
            lines.extend(
                [
                    "[resolved_reference]",
                    f"- kind: {self.reference_resolution.kind}",
                    f"- value: {self.reference_resolution.value}",
                    f"- confidence: {self.reference_resolution.confidence:.2f}",
                    f"- reason: {self.reference_resolution.reason}",
                    "",
                ]
            )
        if self.focus_stack:
            lines.append("[focus_stack]")
            for focus in self.focus_stack:
                lines.append(f"- {focus.kind}: {focus.label} (confidence={focus.confidence:.2f}, source={focus.source})")
            lines.append("")
        if self.artifacts:
            lines.append("[active_artifacts]")
            for artifact in self.artifacts[-6:]:
                lines.append(artifact.render())
            lines.append("")
        if self.warm_summary:
            lines.extend(["[warm_summary]", self.warm_summary, ""])
        if self.hot_turns:
            lines.append("[hot_turns]")
            lines.extend(f"- {turn.render()}" for turn in self.hot_turns)

        rendered: list[str] = []
        used_tokens = 0
        for line in lines:
            line_tokens = estimate_tokens(line)
            if used_tokens + line_tokens > budget_tokens:
                rendered.append("[conversation frame truncated]")
                break
            rendered.append(line)
            used_tokens += line_tokens
        return "\n".join(rendered).strip()

    def to_payload(self) -> dict[str, Any]:
        return {
            "current_message": self.current_message,
            "generated_at": self.generated_at,
            "hot_turns": [turn.__dict__ for turn in self.hot_turns],
            "warm_summary": self.warm_summary,
            "artifacts": [artifact.__dict__ for artifact in self.artifacts],
            "focus_stack": [focus.__dict__ for focus in self.focus_stack],
            "reference_resolution": self.reference_resolution.__dict__,
            "budget": self.budget,
            "source_ledger": self.source_ledger,
        }


_NUMBERED_ITEM_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:#|no\.?|option\s*)?(\d{1,2})[\).\:-]\s+(.+?)\s*$", re.I)
_ACCESS_RE = re.compile(r"\b(?:spark\s+)?access(?:\s+level)?\b|\blevel\s+[1-4]\b|\bfull\s+access\b", re.I)
_ACCESS_VALUE_RE = re.compile(r"\b(?:level\s*)?([1-4]|one|two|three|four)\b", re.I)
_OPTION_REFERENCE_RE = re.compile(
    r"\b(?:no\.?|number|option|#)\s*(\d{1,2})\b|"
    r"\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:one|option|idea|direction|item)\b",
    re.I,
)
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4"}
_OPTION_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def build_conversation_frame(
    *,
    current_message: str,
    turns: list[ConversationTurn],
    policy: ContextBudgetPolicy | None = None,
    retrieved_context: list[str] | None = None,
) -> ConversationFrame:
    active_policy = policy or ContextBudgetPolicy()
    hot_turns, older_turns = _select_hot_turns(turns, active_policy)
    artifacts = _extract_artifacts(turns)
    focus_stack = _infer_focus_stack(current_message=current_message, turns=turns, artifacts=artifacts)
    resolution = _resolve_reference(current_message=current_message, focus_stack=focus_stack, artifacts=artifacts)
    warm_summary = _compact_older_turns(older_turns, artifacts=artifacts, retrieved_context=retrieved_context or [])
    source_ledger = _build_source_ledger(
        hot_turns=hot_turns,
        older_turns=older_turns,
        artifacts=artifacts,
        retrieved_context=retrieved_context or [],
    )
    hot_tokens = sum(turn.token_count() for turn in hot_turns)
    warm_tokens = estimate_tokens(warm_summary)
    artifact_tokens = sum(estimate_tokens(artifact.render()) for artifact in artifacts)
    return ConversationFrame(
        current_message=current_message,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        hot_turns=hot_turns,
        warm_summary=warm_summary,
        artifacts=artifacts,
        focus_stack=focus_stack,
        reference_resolution=resolution,
        budget={
            **active_policy.to_payload(),
            "hot_turn_tokens": hot_tokens,
            "warm_summary_tokens": warm_tokens,
            "artifact_tokens": artifact_tokens,
            "assembled_estimated_tokens": hot_tokens + warm_tokens + artifact_tokens,
        },
        source_ledger=source_ledger,
    )


def retrieve_domain_chip_cold_context(
    *,
    sdk: Any,
    subject: str,
    query: str,
    limit: int = 6,
) -> list[ColdContextItem]:
    """Retrieve cold memory evidence from domain-chip-memory without making it a hard dependency.

    The harness treats this as supporting context only. Hot turns and exact artifacts
    still win over cold memory during reference resolution.
    """

    if not sdk or not subject or not query:
        return []
    direct_items = _retrieve_domain_chip_direct(sdk=sdk, subject=subject, query=query, limit=limit)
    if direct_items:
        return direct_items[: max(1, int(limit or 1))]
    try:
        from domain_chip_memory.builder_read_adapter import BuilderMemoryReadRequest, execute_builder_memory_read
    except Exception:
        return []

    items: list[ColdContextItem] = []
    for method in ("retrieve_evidence", "retrieve_events"):
        try:
            payload = execute_builder_memory_read(
                sdk,
                BuilderMemoryReadRequest(
                    method=method,
                    subject=subject,
                    query=query,
                    limit=max(1, min(10, int(limit or 1))),
                ),
            )
        except Exception as exc:
            items.append(
                ColdContextItem(
                    source="domain_chip_memory",
                    text=f"{method} unavailable: {exc.__class__.__name__}",
                    method=method,
                    confidence=0.0,
                    metadata={"error": exc.__class__.__name__},
                )
            )
            continue
        facts = payload.get("facts") if isinstance(payload, dict) else {}
        if not isinstance(facts, dict):
            continue
        trace = facts.get("retrieval_trace") if isinstance(facts.get("retrieval_trace"), dict) else {}
        records = _cold_context_records_from_trace(trace)
        for record in records:
            text = _cold_context_record_text(record)
            if not text:
                continue
            items.append(
                ColdContextItem(
                    source="domain_chip_memory",
                    text=text,
                    method=method,
                    confidence=0.75,
                    metadata={
                        "predicate": record.get("predicate"),
                        "memory_role": record.get("memory_role"),
                        "session_id": record.get("session_id"),
                        "turn_ids": record.get("turn_ids"),
                    },
                )
            )
    return items[: max(1, int(limit or 1))]


def build_conversation_frame_with_cold_context(
    *,
    current_message: str,
    turns: list[ConversationTurn],
    sdk: Any | None,
    subject: str,
    policy: ContextBudgetPolicy | None = None,
) -> ConversationFrame:
    cold_items = retrieve_domain_chip_cold_context(
        sdk=sdk,
        subject=subject,
        query=current_message,
        limit=6,
    )
    return build_conversation_frame(
        current_message=current_message,
        turns=turns,
        policy=policy,
        retrieved_context=[item.render() for item in cold_items if item.confidence > 0],
    )


def _select_hot_turns(
    turns: list[ConversationTurn],
    policy: ContextBudgetPolicy,
) -> tuple[list[ConversationTurn], list[ConversationTurn]]:
    selected_reversed: list[ConversationTurn] = []
    used_tokens = 0
    for turn in reversed(turns):
        turn_tokens = turn.token_count()
        must_keep = len(selected_reversed) < policy.hot_min_turns
        if not must_keep and used_tokens + turn_tokens > policy.hot_target_tokens:
            break
        selected_reversed.append(turn)
        used_tokens += turn_tokens
    hot_turns = list(reversed(selected_reversed))
    older_count = max(0, len(turns) - len(hot_turns))
    return hot_turns, turns[:older_count]


def _extract_artifacts(turns: list[ConversationTurn]) -> list[ConversationArtifact]:
    artifacts: list[ConversationArtifact] = []
    for turn_index, turn in enumerate(turns):
        list_items = _extract_numbered_items(turn.text)
        if len(list_items) >= 2:
            title = _artifact_title_from_text(turn.text)
            artifacts.append(
                ConversationArtifact(
                    kind="list",
                    key=f"list:{turn.turn_id or turn_index}",
                    title=title,
                    items=list_items,
                    source_turn_id=turn.turn_id,
                    source_index=turn_index,
                    confidence=0.9 if turn.role == "assistant" else 0.75,
                )
            )
        access_value = _extract_access_value(turn.text)
        if access_value and _ACCESS_RE.search(turn.text):
            artifacts.append(
                ConversationArtifact(
                    kind="access_level",
                    key=f"access:{turn.turn_id or turn_index}",
                    title=f"Spark access level {access_value}",
                    items=[access_value],
                    source_turn_id=turn.turn_id,
                    source_index=turn_index,
                    confidence=0.95,
                )
            )
    return artifacts[-12:]


def _extract_numbered_items(text: str) -> list[str]:
    items: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _NUMBERED_ITEM_RE.match(line)
        if not match:
            continue
        number = int(match.group(1))
        value = match.group(2).strip()
        if value:
            items.append((number, value))
    if not items:
        inline_matches = re.findall(r"(?:^|\s)([1-9])[\).\:-]\s+([^0-9\n]+?)(?=\s+[1-9][\).\:-]\s+|$)", text)
        items = [(int(number), value.strip(" .;")) for number, value in inline_matches if value.strip(" .;")]
    if len(items) < 2:
        return []
    return [value for _, value in sorted(items, key=lambda item: item[0])][:20]


def _artifact_title_from_text(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" :-")
        if stripped and not _NUMBERED_ITEM_RE.match(line):
            return stripped[:120]
    return "recent numbered list"


def _extract_access_value(text: str) -> str | None:
    lowered = text.lower()
    if "full access" in lowered:
        return "5"
    match = _ACCESS_VALUE_RE.search(text)
    if not match:
        return None
    value = match.group(1).lower()
    return _NUMBER_WORDS.get(value, value)


def _infer_focus_stack(
    *,
    current_message: str,
    turns: list[ConversationTurn],
    artifacts: list[ConversationArtifact],
) -> list[ConversationFocus]:
    recent_text = "\n".join([turn.text for turn in turns[-8:]] + [current_message])
    focus: list[ConversationFocus] = []
    if _ACCESS_RE.search(recent_text):
        focus.append(ConversationFocus(kind="access_level", label="Spark access level", confidence=0.92, source="recent_turns"))
    latest_list = next((artifact for artifact in reversed(artifacts) if artifact.kind == "list"), None)
    if latest_list:
        focus.append(
            ConversationFocus(
                kind="list",
                label=latest_list.title,
                confidence=0.82,
                source=latest_list.key,
            )
        )
    return focus[:4]


def _resolve_reference(
    *,
    current_message: str,
    focus_stack: list[ConversationFocus],
    artifacts: list[ConversationArtifact],
) -> ReferenceResolution:
    option_match = _OPTION_REFERENCE_RE.search(current_message)
    if option_match:
        index = int(option_match.group(1)) if option_match.group(1) else _ORDINALS.get(option_match.group(2).lower(), 0)
        latest_list = next((artifact for artifact in reversed(artifacts) if artifact.kind == "list"), None)
        if latest_list and 1 <= index <= len(latest_list.items):
            return ReferenceResolution(
                kind="list_item",
                value=latest_list.items[index - 1],
                confidence=0.86,
                source_artifact_key=latest_list.key,
                reason=f"resolved option {index} against most recent list artifact",
            )

    bare_option_index = _extract_bare_option_reference_index(current_message)
    if bare_option_index:
        latest_list = next((artifact for artifact in reversed(artifacts) if artifact.kind == "list"), None)
        latest_access = next((artifact for artifact in reversed(artifacts) if artifact.kind == "access_level"), None)
        list_is_current_focus = latest_list and (
            latest_access is None or latest_list.source_index is None or latest_access.source_index is None or latest_list.source_index >= latest_access.source_index
        )
        if list_is_current_focus and 1 <= bare_option_index <= len(latest_list.items):
            return ReferenceResolution(
                kind="list_item",
                value=latest_list.items[bare_option_index - 1],
                confidence=0.78,
                source_artifact_key=latest_list.key,
                reason=f"resolved short option {bare_option_index} against newer list artifact",
            )

    access_focus = any(focus.kind == "access_level" for focus in focus_stack)
    if access_focus:
        access_value = _extract_access_value(current_message)
        has_change_shape = re.search(r"\b(?:change|set|switch|make|do|go\s+to|go\s+with|actually|instead)\b", current_message, re.I)
        if access_value and (has_change_shape or re.fullmatch(r"\s*(?:level\s*)?(?:[1-5]|one|two|three|four|five)\s*[.!?]?\s*", current_message, re.I)):
            return ReferenceResolution(
                kind="access_level",
                value=access_value,
                confidence=0.9,
                reason="recent access focus plus short level reference",
            )

    return ReferenceResolution(kind="none", reason="no reliable local reference")


def _extract_bare_option_reference_index(text: str) -> int | None:
    match = re.search(
        r"^(?:let'?s\s+|please\s+|actually\s+|no[, ]*|instead\s+)*(?:do|pick|choose|select|use|go\s+with)\s+(?:the\s+)?(?:option\s+|idea\s+|direction\s+|item\s+)?([1-9]|10|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        text,
        re.I,
    )
    if not match:
        return None
    value = match.group(1).lower()
    return _OPTION_NUMBER_WORDS.get(value) or int(value)


def _compact_older_turns(
    turns: list[ConversationTurn],
    *,
    artifacts: list[ConversationArtifact],
    retrieved_context: list[str],
) -> str:
    lines: list[str] = []
    if turns:
        user_goals = [turn.text.strip() for turn in turns if turn.role == "user" and turn.text.strip()]
        assistant_decisions = [
            turn.text.strip()
            for turn in turns
            if turn.role == "assistant" and re.search(r"\b(?:done|changed|decided|plan|next|options?|level|mission)\b", turn.text, re.I)
        ]
        if user_goals:
            lines.append("Older user goals: " + " | ".join(user_goals[-6:]))
        if assistant_decisions:
            lines.append("Older assistant decisions: " + " | ".join(assistant_decisions[-4:]))
    exact_artifacts = [artifact for artifact in artifacts if artifact.kind in {"list", "access_level"}]
    if exact_artifacts:
        lines.append(
            "Exact artifacts preserved: "
            + "; ".join(f"{artifact.kind}:{artifact.title}" for artifact in exact_artifacts[-6:])
        )
    if retrieved_context:
        lines.append("Retrieved cold context: " + " | ".join(item.strip() for item in retrieved_context if item.strip())[:1600])
    return "\n".join(lines)


def _build_source_ledger(
    *,
    hot_turns: list[ConversationTurn],
    older_turns: list[ConversationTurn],
    artifacts: list[ConversationArtifact],
    retrieved_context: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "source": "hot_turns",
            "role": "verbatim_recent_context",
            "count": len(hot_turns),
            "priority": 1,
        },
        {
            "source": "active_artifacts",
            "role": "exact_reference_support",
            "count": len(artifacts),
            "priority": 2,
        },
        {
            "source": "warm_summary",
            "role": "compacted_older_context",
            "count": len(older_turns),
            "priority": 3,
        },
        {
            "source": "retrieved_context",
            "role": "cold_memory_support",
            "count": len(retrieved_context),
            "priority": 4,
        },
    ]


def _cold_context_records_from_trace(trace: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "records", "evidence", "events", "matches"):
        value = trace.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _retrieve_domain_chip_direct(*, sdk: Any, subject: str, query: str, limit: int) -> list[ColdContextItem]:
    try:
        from domain_chip_memory.sdk import EventRetrievalRequest, EvidenceRetrievalRequest
    except Exception:
        return []
    try:
        from domain_chip_memory.sdk import TaskRecoveryRequest
    except Exception:
        TaskRecoveryRequest = None  # type: ignore[assignment]

    items: list[ColdContextItem] = []
    task_recovery = getattr(sdk, "recover_task_context", None)
    if callable(task_recovery) and TaskRecoveryRequest is not None:
        try:
            recovery_result = task_recovery(
                TaskRecoveryRequest(query=query, subject=subject, limit=max(1, min(6, int(limit or 1))))
            )
        except Exception as exc:
            items.append(
                ColdContextItem(
                    source="domain_chip_memory",
                    text=f"recover_task_context unavailable: {exc.__class__.__name__}",
                    method="recover_task_context",
                    confidence=0.0,
                    metadata={"error": exc.__class__.__name__},
                )
            )
        else:
            items.extend(_cold_context_items_from_task_recovery(recovery_result))

    for method, request_type in (
        ("retrieve_evidence", EvidenceRetrievalRequest),
        ("retrieve_events", EventRetrievalRequest),
    ):
        fn = getattr(sdk, method, None)
        if not callable(fn):
            continue
        try:
            result = fn(request_type(query=query, subject=subject, limit=max(1, min(10, int(limit or 1)))))
        except Exception as exc:
            items.append(
                ColdContextItem(
                    source="domain_chip_memory",
                    text=f"{method} unavailable: {exc.__class__.__name__}",
                    method=method,
                    confidence=0.0,
                    metadata={"error": exc.__class__.__name__},
                )
            )
            continue
        for item in list(getattr(result, "items", []) or []):
            text = str(getattr(item, "text", "") or "").strip()
            if not text:
                continue
            items.append(
                ColdContextItem(
                    source="domain_chip_memory",
                    text=text,
                    method=method,
                    confidence=0.8,
                    metadata={
                        "predicate": getattr(item, "predicate", None),
                        "memory_role": getattr(item, "memory_role", None),
                        "session_id": getattr(item, "session_id", None),
                        "turn_ids": list(getattr(item, "turn_ids", []) or []),
                    },
                )
            )
    return items


def _cold_context_items_from_task_recovery(result: Any) -> list[ColdContextItem]:
    trace = getattr(result, "trace", {}) if result is not None else {}
    labels = trace.get("source_labels") if isinstance(trace, dict) else []
    label_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(labels, list):
        for label in labels:
            if not isinstance(label, dict):
                continue
            for key in ("observation_id", "event_id"):
                value = str(label.get(key) or "").strip()
                if value:
                    label_by_id[value] = label
    items: list[ColdContextItem] = []
    buckets = (
        ("active_goal", [getattr(result, "active_goal", None)]),
        ("completed_steps", list(getattr(result, "completed_steps", []) or [])),
        ("blockers", list(getattr(result, "blockers", []) or [])),
        ("next_actions", list(getattr(result, "next_actions", []) or [])),
        ("episodic_context", list(getattr(result, "episodic_context", []) or [])),
    )
    for bucket, records in buckets:
        for record in records:
            if record is None:
                continue
            text = str(getattr(record, "text", "") or "").strip()
            if not text:
                continue
            observation_id = str(getattr(record, "observation_id", "") or "").strip()
            event_id = str(getattr(record, "event_id", "") or "").strip()
            label = label_by_id.get(observation_id) or label_by_id.get(event_id) or {}
            items.append(
                ColdContextItem(
                    source="domain_chip_memory",
                    text=text,
                    method="recover_task_context",
                    confidence=0.85 if bucket == "active_goal" else 0.75,
                    metadata={
                        "bucket": bucket,
                        "authority": label.get("authority"),
                        "source_family": label.get("source_family"),
                        "predicate": getattr(record, "predicate", None),
                        "memory_role": getattr(record, "memory_role", None),
                        "session_id": getattr(record, "session_id", None),
                        "turn_ids": list(getattr(record, "turn_ids", []) or []),
                    },
                )
            )
    return items


def _cold_context_record_text(record: dict[str, Any]) -> str:
    for key in ("text", "value", "summary", "source_text", "normalized_value"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for key in ("source_text", "source_span", "value"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""
