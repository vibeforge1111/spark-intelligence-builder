from __future__ import annotations

import re
from dataclasses import dataclass


_CITY_PATTERNS = [
    re.compile(r"\bi\s+moved\s+to\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi\s+live\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_STARTUP_PATTERNS = [
    re.compile(r"\bmy\s+startup\s+is\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\b([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})\s+is\s+my\s+startup", re.I),
    re.compile(r"\bi\s+created\s+a\s+startup\s+called\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+run\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
]
_FOUNDER_PATTERNS = [
    re.compile(r"\bi\s+founded\s+a\s+startup\s+called\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi(?:'m| am)\s+the\s+founder\s+of\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+founded\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+started\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+built\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
    re.compile(r"\bi\s+launched\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
]
_HACK_PATTERNS = [
    re.compile(r"\bwe\s+were\s+hacked\s+by\s+([A-Za-z][A-Za-z0-9\s\-'`.&_]{1,60})", re.I),
]
_MISSION_PATTERNS = [
    re.compile(r"\bi(?:'m| am)\s+trying\s+to\s+([A-Za-z][A-Za-z0-9\s\-'`.&,]{3,120})", re.I),
    re.compile(r"\bi(?:'m| am)\s+(rebuilding\s+after\s+the\s+hack|reviving\s+the\s+companies)(?:[.!?,]|$)", re.I),
]
_SPARK_ROLE_PATTERNS = [
    re.compile(r"\bspark\s+(?:is\s+going\s+to\s+be|will\s+be)\s+an\s+important\s+part\s+of\s+this(?:\s+rebuild)?", re.I),
]
_OCCUPATION_PATTERNS = [
    re.compile(r"\bi(?:'m| am)\s+an\s+(entrepreneur)(?:\s+(?:now|today|currently))?(?:[.!?,]|$)", re.I),
]
_NAME_PATTERNS = [
    re.compile(r"\bmy\s+name\s+is\s+([a-z][a-z\s\-'.`]{0,40})", re.I),
    re.compile(r"\bcall\s+me\s+([a-z][a-z\s\-'.`]{0,40})", re.I),
]
_COUNTRY_PATTERNS = [
    re.compile(r"\bmy\s+country\s+is\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+from\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+based\s+in\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
    re.compile(r"\bi(?:'m| am)\s+based\s+out\s+of\s+([a-z][a-z\s\-'`.]{1,40})", re.I),
]
_TIMEZONE_PATTERNS = [
    re.compile(r"\bmy\s+timezone\s+is\s+([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)", re.I),
    re.compile(r"\bi(?:'m| am)\s+in\s+timezone\s+([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)", re.I),
    re.compile(r"\bi(?:'m| am)\s+on\s+(utc[+-]\d{1,2}(?::\d{2})?)", re.I),
    re.compile(r"\bmy\s+timezone\s+is\s+(utc[+-]\d{1,2}(?::\d{2})?)", re.I),
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


@dataclass(frozen=True)
class ProfileFactQuery:
    predicate: str
    fact_name: str
    label: str


def detect_profile_fact_observation(user_message: str) -> ProfileFactObservation | None:
    text = str(user_message or "").strip()
    if not text:
        return None
    preferred_name = _extract_name(text)
    if preferred_name:
        return ProfileFactObservation(
            predicate="profile.preferred_name",
            value=preferred_name,
            operation="update",
            evidence_text=text,
            fact_name="profile_preferred_name",
        )
    country = _extract_country(text)
    if country:
        return ProfileFactObservation(
            predicate="profile.home_country",
            value=country,
            operation="update",
            evidence_text=text,
            fact_name="profile_home_country",
        )
    timezone = _extract_timezone(text)
    if timezone:
        return ProfileFactObservation(
            predicate="profile.timezone",
            value=timezone,
            operation="update",
            evidence_text=text,
            fact_name="profile_timezone",
        )
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


def detect_profile_fact_query(user_message: str) -> ProfileFactQuery | None:
    text = str(user_message or "").strip().lower()
    if not text:
        return None
    if any(
        phrase in text
        for phrase in (
            "what name do you have for me",
            "what name do you have saved for me",
            "which name do you have for me",
            "what's my name",
            "what is my name",
        )
    ):
        return ProfileFactQuery(predicate="profile.preferred_name", fact_name="profile_preferred_name", label="name")
    if any(
        phrase in text
        for phrase in (
            "what country do you have for me",
            "what country do you have saved for me",
            "which country do you have for me",
            "what's my country",
            "what is my country",
        )
    ):
        return ProfileFactQuery(predicate="profile.home_country", fact_name="profile_home_country", label="country")
    if any(
        phrase in text
        for phrase in (
            "what timezone do you have for me",
            "what timezone do you have saved for me",
            "which timezone do you have for me",
            "what's my timezone",
            "what is my timezone",
        )
    ):
        return ProfileFactQuery(predicate="profile.timezone", fact_name="profile_timezone", label="timezone")
    if any(
        phrase in text
        for phrase in (
            "where do i live",
            "what city do i live in",
            "what city am i in",
            "what city do you have for me",
            "what city do you have saved for me",
            "which city do you have for me",
        )
    ):
        return ProfileFactQuery(predicate="profile.city", fact_name="profile_city", label="city")
    return None


def build_profile_fact_query_context(*, query: ProfileFactQuery, value: str | None) -> str:
    if value:
        return (
            "[Memory action: PROFILE_FACT_STATUS]\n"
            f"The user is asking about their saved {query.label}. "
            f"Memory-backed current-state fact: {query.label}: {value}.\n"
            "Answer naturally and briefly using that fact."
        )
    return (
        "[Memory action: PROFILE_FACT_STATUS_MISSING]\n"
        f"The user is asking about their saved {query.label}, but no memory-backed current-state fact is available.\n"
        "Do not pretend you know. Say you do not currently have that saved and invite the user to tell you if they want."
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


def _extract_name(text: str) -> str | None:
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_name(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_country(text: str) -> str | None:
    for pattern in _COUNTRY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_place(match.group(1))
        if candidate:
            return candidate
    return None


def _extract_timezone(text: str) -> str | None:
    for pattern in _TIMEZONE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _normalize_timezone(match.group(1))
        if candidate:
            return candidate
    return None


def _normalize_city(raw: str) -> str | None:
    return _normalize_place(raw)


def _normalize_name(raw: str) -> str | None:
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
        if len(parts) >= 3:
            break
    if not parts:
        return None
    normalized = []
    for token in parts:
        if token.isupper() and len(token) <= 4:
            normalized.append(token)
        else:
            normalized.append(token[0].upper() + token[1:].lower())
    return " ".join(normalized)


def _normalize_place(raw: str) -> str | None:
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
        elif token.isupper() and len(token) <= 4:
            normalized.append(token)
        else:
            normalized.append(lowered[0].upper() + lowered[1:])
    return " ".join(normalized)


def _normalize_timezone(raw: str) -> str | None:
    candidate = re.split(r"[.!?,;:\n]", str(raw or ""), maxsplit=1)[0].strip(" '\"`")
    if not candidate:
        return None
    if "/" in candidate:
        parts = [part for part in candidate.split("/") if part]
        if len(parts) < 2:
            return None
        normalized = []
        for part in parts[:3]:
            cleaned = re.sub(r"[^A-Za-z_]", "", part)
            if not cleaned:
                return None
            normalized.append("_".join(token.capitalize() for token in cleaned.split("_") if token))
        return "/".join(normalized)
    compact = candidate.replace(" ", "").upper()
    if re.fullmatch(r"UTC[+-]\d{1,2}(?::\d{2})?", compact):
        return compact
    return None
