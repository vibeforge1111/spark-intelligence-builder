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
    (
        "flight",
        re.compile(
            r"^(?:my|i have (?:a|an))\s+flight\s+to\s+(?P<destination>.+?)\s+(?:is\s+)?on\s+(?P<date>.+?)[.!]?$",
            re.IGNORECASE,
        ),
    ),
    (
        "deadline",
        re.compile(
            r"^(?:my|the)\s+deadline\s+for\s+(?P<item>.+?)\s+(?:is\s+)?(?:on|by)\s+(?P<date>.+?)[.!]?$",
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
    ("flight", re.compile(r"\b(?:flight|flights)\b", re.IGNORECASE)),
    ("deadline", re.compile(r"\b(?:deadline|deadlines)\b", re.IGNORECASE)),
)

_UNCERTAIN_EVENT_PREFIX_PATTERN = re.compile(
    r"^(?:maybe|perhaps|what if|if|hopefully|i might|i may|i could|i should)\b",
    re.IGNORECASE,
)
_EVENT_TIME_PATTERN = re.compile(
    r"\b(?:"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"today|tonight|tomorrow|tmrw|this week|next week|this month|next month|"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"\d{1,2}(?:st|nd|rd|th)"
    r")\b",
    re.IGNORECASE,
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
    summary_predicate: str | None = None


def detect_telegram_memory_event_observation(user_message: str) -> TelegramMemoryEventObservation | None:
    text = _clean_text(user_message)
    if not text or "?" in text:
        return None
    if not _is_memoryworthy_event_text(text):
        return None
    if detect_telegram_memory_event_query(text) is not None:
        return None
    for label, pattern in _SCHEDULED_EVENT_PATTERNS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        date_text = _clean_fragment(match.group("date"))
        if not date_text or not _looks_like_event_time(date_text):
            return None
        value = _build_event_value(label=label, match=match, date_text=date_text)
        if not value:
            return None
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
        latest_query = _detect_latest_event_query(text=text, label=label)
        if latest_query is not None:
            return latest_query
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
    if query.query_kind == "latest_event":
        if not ordered_values:
            return f"I don't currently have a saved {query.label}."
        return f"Your latest saved {query.label} is {ordered_values[-1]}."
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


def _is_memoryworthy_event_text(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if len(normalized) > 180:
        return False
    if _UNCERTAIN_EVENT_PREFIX_PATTERN.search(normalized):
        return False
    if "http://" in normalized or "https://" in normalized:
        return False
    return True


def _looks_like_event_time(text: str) -> bool:
    return bool(_EVENT_TIME_PATTERN.search(_clean_text(text)))


def _build_event_value(*, label: str, match: re.Match[str], date_text: str) -> str | None:
    if label in {"meeting", "call", "appointment"}:
        counterparty = _clean_fragment(match.group("counterparty"))
        if not counterparty:
            return None
        return f"{label} with {counterparty} on {date_text}"
    if label == "flight":
        destination = _clean_fragment(match.group("destination"))
        if not destination:
            return None
        return f"flight to {destination} on {date_text}"
    if label == "deadline":
        item = _clean_fragment(match.group("item"))
        if not item:
            return None
        return f"deadline for {item} by {date_text}"
    return None


def telegram_event_summary_predicate(predicate: str | None) -> str | None:
    normalized = _clean_text(str(predicate or ""))
    if not normalized.startswith(_TELEGRAM_EVENT_PREDICATE_PREFIX):
        return None
    suffix = normalized[len(_TELEGRAM_EVENT_PREDICATE_PREFIX) :]
    if not suffix:
        return None
    return f"telegram.summary.latest_{suffix}"


def _detect_latest_event_query(*, text: str, label: str) -> TelegramMemoryEventQuery | None:
    pattern = re.compile(
        rf"^(?:what(?:'s| is)?\s+(?:my\s+|the\s+)?(?:next\s+)?{re.escape(label)}\??|"
        rf"what\s+{re.escape(label)}\s+do\s+i\s+have\??)$",
        re.IGNORECASE,
    )
    if not pattern.fullmatch(text):
        return None
    predicate = f"{_TELEGRAM_EVENT_PREDICATE_PREFIX}{label}"
    return TelegramMemoryEventQuery(
        predicate=predicate,
        label=label,
        query_kind="latest_event",
        summary_predicate=telegram_event_summary_predicate(predicate),
    )
