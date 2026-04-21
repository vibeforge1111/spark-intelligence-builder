from __future__ import annotations

import re
from dataclasses import dataclass


_CORRECTION_PREFIX_PATTERN = re.compile(r"^(?:actually|update|correction)[:,]?\s+", re.IGNORECASE)
_HYPOTHETICAL_PREFIX_PATTERN = re.compile(
    r"^(?:maybe|perhaps|what if|if|hopefully|i might|i may|i could|i should)\b",
    re.IGNORECASE,
)
_SMALL_TALK_PATTERN = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|cool|lol|noted|got it)[.!]?$",
    re.IGNORECASE,
)

_RELATIONSHIP_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("profile.cofounder_name", "cofounder", re.compile(r"^my\s+cofounder\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.mentor_name", "mentor", re.compile(r"^my\s+mentor\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.partner_name", "partner", re.compile(r"^my\s+partner\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.partner_name", "wife", re.compile(r"^my\s+wife\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.partner_name", "husband", re.compile(r"^my\s+husband\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
)

_PLAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)\s+plan\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
)

_FOCUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i(?:'m| am)|we(?:'re| are))\s+focusing\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+priority\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+decided\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
)


@dataclass(frozen=True)
class TelegramGenericObservation:
    predicate: str
    value: str
    evidence_text: str
    fact_name: str
    label: str


def detect_telegram_generic_observation(user_message: str) -> TelegramGenericObservation | None:
    text = _clean_text(user_message)
    if not _is_memoryworthy_text(text):
        return None
    normalized = _strip_correction_prefix(text)

    for predicate, label, pattern in _RELATIONSHIP_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate=predicate,
                value=value,
                evidence_text=text,
                fact_name=label,
                label=label,
            )

    for pattern in _PLAN_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_plan",
                value=value,
                evidence_text=text,
                fact_name="current_plan",
                label="current plan",
            )

    for pattern in _FOCUS_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_focus",
                value=value,
                evidence_text=text,
                fact_name="current_focus",
                label="current focus",
            )

    return None


def build_telegram_generic_observation_answer(*, observation: TelegramGenericObservation) -> str:
    value = str(observation.value or "").strip()
    if not value:
        return "I'll remember that."
    if observation.predicate == "profile.current_plan":
        return f"I'll remember that your current plan is to {value}."
    if observation.predicate == "profile.current_focus":
        return f"I'll remember that your current focus is {value}."
    return f"I'll remember that your {observation.label} is {value}."


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _strip_correction_prefix(text: str) -> str:
    return _CORRECTION_PREFIX_PATTERN.sub("", text, count=1).strip()


def _is_memoryworthy_text(text: str) -> bool:
    if not text or "?" in text:
        return False
    if len(text) < 8 or len(text) > 220:
        return False
    if "http://" in text or "https://" in text:
        return False
    if _HYPOTHETICAL_PREFIX_PATTERN.search(text):
        return False
    if _SMALL_TALK_PATTERN.fullmatch(text):
        return False
    return True


def _clean_value(value: str) -> str:
    cleaned = _clean_text(value)
    while cleaned.endswith((".", "!", ",")):
        cleaned = cleaned[:-1].rstrip()
    return cleaned
