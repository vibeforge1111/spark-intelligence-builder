from __future__ import annotations

import re
from dataclasses import dataclass


_CITY_PATTERNS = [
    re.compile(r"\bi\s+moved\s+to\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi\s+live\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_STOP_WORDS = {"and", "but", "because", "so", "that", "which", "where"}
_LOWERCASE_JOINERS = {"and", "of", "the", "de", "al", "bin"}


@dataclass(frozen=True)
class ProfileFactObservation:
    predicate: str
    value: str
    operation: str
    evidence_text: str
    fact_name: str


def detect_profile_fact_observation(user_message: str) -> ProfileFactObservation | None:
    text = str(user_message or "").strip()
    if not text:
        return None
    city = _extract_city(text)
    if not city:
        return None
    return ProfileFactObservation(
        predicate="profile.city",
        value=city,
        operation="update",
        evidence_text=text,
        fact_name="profile_city",
    )


def _extract_city(text: str) -> str | None:
    for pattern in _CITY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_city(match.group(1))
        if candidate:
            return candidate
    return None


def _normalize_city(raw: str) -> str | None:
    candidate = re.split(r"[.!?,;:\n]", str(raw or ""), maxsplit=1)[0].strip(" '\"`")
    if not candidate:
        return None
    parts = []
    for token in candidate.split():
        lowered = token.lower()
        if lowered in _STOP_WORDS:
            break
        cleaned = re.sub(r"[^A-Za-z'\-]", "", token)
        if not cleaned:
            continue
        parts.append(cleaned)
        if len(parts) >= 4:
            break
    if not parts:
        return None
    normalized: list[str] = []
    for index, token in enumerate(parts):
        lowered = token.lower()
        if index > 0 and lowered in _LOWERCASE_JOINERS:
            normalized.append(lowered)
        else:
            normalized.append(lowered[0].upper() + lowered[1:])
    return " ".join(normalized)
