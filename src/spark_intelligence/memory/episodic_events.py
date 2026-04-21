from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_TELEGRAM_EVENT_PREDICATE_PREFIX = "telegram.event."

_SCHEDULED_EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "meeting",
        re.compile(
            r"^(?:my|i have (?:a|an))\s+meeting\s+with\s+(?P<counterparty>.+?)\s+(?:is\s+)?on\s+(?P<date>.+?)[.!]?$",
            re.IGNORECASE,
        ),
    ),
    (
        "call",
        re.compile(
            r"^(?:my|i have (?:a|an))\s+call\s+with\s+(?P<counterparty>.+?)\s+(?:is\s+)?on\s+(?P<date>.+?)[.!]?$",
            re.IGNORECASE,
        ),
    ),
    (
        "appointment",
        re.compile(
            r"^(?:my|i have (?:a|an))\s+appointment\s+with\s+(?P<counterparty>.+?)\s+(?:is\s+)?on\s+(?P<date>.+?)[.!]?$",
            re.IGNORECASE,
        ),
    ),
)

_GENERIC_EVENT_QUERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat\s+events?\b.*\b(?:mention|remember|saved|have)\b", re.IGNORECASE),
    re.compile(r"\bshow\b.*\b(?:events?|memories)\b", re.IGNORECASE),
    re.compile(r"\blist\b.*\b(?:events?|memories)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+event\b.*\bmention\b", re.IGNORECASE),
)

_EVENT_QUERY_INTENT_PATTERN = re.compile(
    r"\b(?:what|which|show|list|remember|remind)\b.*\b(?:mention|mentioned|remember|saved|have)\b",
    re.IGNORECASE,
)

_EVENT_TYPE_QUERY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("meeting", re.compile(r"\b(?:meeting|meetings)\b", re.IGNORECASE)),
    ("call", re.compile(r"\b(?:call|calls)\b", re.IGNORECASE)),
    ("appointment", re.compile(r"\b(?:appointment|appointments)\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class TelegramMemoryEventObservation:
    predicate: str
    value: str
    evidence_text: str
    event_name: str
    label: str


@dataclass(frozen=True)
class TelegramMemoryEventQuery:
    predicate: str | None
    label: str
    query_kind: str = "recent_events"


def detect_telegram_memory_event_observation(user_message: str) -> TelegramMemoryEventObservation | None:
    text = _clean_text(user_message)
    if not text or "?" in text:
        return None
    if detect_telegram_memory_event_query(text) is not None:
        return None
    for label, pattern in _SCHEDULED_EVENT_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        counterparty = _clean_fragment(match.group("counterparty"))
        date_text = _clean_fragment(match.group("date"))
        if not counterparty or not date_text:
            return None
        value = f"{label} with {counterparty} on {date_text}"
        return TelegramMemoryEventObservation(
            predicate=f"{_TELEGRAM_EVENT_PREDICATE_PREFIX}{label}",
            value=value,
            evidence_text=text,
            event_name=f"telegram_event_{label}",
            label=label,
        )
    return None


def detect_telegram_memory_event_query(user_message: str) -> TelegramMemoryEventQuery | None:
    text = _clean_text(user_message)
    if not text:
        return None
    for label, pattern in _EVENT_TYPE_QUERY_PATTERNS:
        if not pattern.search(text):
            continue
        if _EVENT_QUERY_INTENT_PATTERN.search(text) or any(
            query_pattern.search(text) for query_pattern in _GENERIC_EVENT_QUERY_PATTERNS
        ):
            return TelegramMemoryEventQuery(
                predicate=f"{_TELEGRAM_EVENT_PREDICATE_PREFIX}{label}",
                label=f"{label} events",
            )
    if any(pattern.search(text) for pattern in _GENERIC_EVENT_QUERY_PATTERNS):
        return TelegramMemoryEventQuery(predicate=None, label="events")
    return None


def build_telegram_memory_event_observation_answer(
    *, observation: TelegramMemoryEventObservation
) -> str:
    value = str(observation.value or "").strip()
    if not value:
        return "I'll remember that."
    return f"I'll remember your {value}."


def build_telegram_memory_event_query_answer(
    *, query: TelegramMemoryEventQuery, records: list[dict[str, Any]]
) -> str:
    ordered_values = _ordered_unique_event_values(records)
    if not ordered_values:
        return "I don't currently have any saved events from this chat."
    if len(ordered_values) == 1:
        return f"I have 1 saved event: {ordered_values[0]}."
    if len(ordered_values) == 2:
        return f"I have 2 saved events: {ordered_values[0]} then {ordered_values[1]}."
    preview = ordered_values[:3]
    remainder = len(ordered_values) - len(preview)
    suffix = f", and {remainder} more" if remainder > 0 else ""
    return f"I have {len(ordered_values)} saved events: {' then '.join(preview)}{suffix}."


def filter_telegram_memory_event_records(
    *, query: TelegramMemoryEventQuery, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    filtered = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        if query.predicate is not None:
            if predicate != query.predicate:
                continue
        elif not predicate.startswith(_TELEGRAM_EVENT_PREDICATE_PREFIX):
            continue
        filtered.append(record)
    return sorted(filtered, key=_event_record_sort_key)


def _ordered_unique_event_values(records: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for record in sorted(records, key=_event_record_sort_key):
        value = _clean_fragment(
            str(record.get("value") or (record.get("metadata") or {}).get("value") or record.get("text") or "")
        )
        if not value:
            continue
        dedupe_key = value.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        values.append(value)
    return values


def _event_record_sort_key(record: dict[str, Any]) -> tuple[datetime, str]:
    timestamp = _parse_timestamp(record.get("timestamp"))
    turn_id = ",".join(str(item) for item in list(record.get("turn_ids") or []))
    return (timestamp, turn_id)


def _parse_timestamp(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _clean_text(value: str) -> str:
    return _collapse_whitespace(str(value or "").strip())


def _clean_fragment(value: str) -> str:
    text = _collapse_whitespace(str(value or "").strip())
    while text.endswith((".", "!", ",")):
        text = text[:-1].rstrip()
    return text


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
