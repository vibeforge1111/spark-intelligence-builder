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
    ("profile.manager_name", "manager", re.compile(r"^my\s+manager\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.assistant_name", "assistant", re.compile(r"^my\s+assistant\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.partner_name", "partner", re.compile(r"^my\s+partner\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.partner_name", "wife", re.compile(r"^my\s+wife\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.partner_name", "husband", re.compile(r"^my\s+husband\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.mother_name", "mother", re.compile(r"^my\s+mother\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.father_name", "father", re.compile(r"^my\s+father\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.sister_name", "sister", re.compile(r"^my\s+sister\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
    ("profile.brother_name", "brother", re.compile(r"^my\s+brother\s+is\s+(.+?)[.!]?$", re.IGNORECASE)),
)

_PLAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)\s+plan\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
)

_FOCUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i(?:'m| am)|we(?:'re| are))\s+focusing\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+priority\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_DECISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)\s+decided\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+decided\s+that\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+decision\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+decision\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:i|we)(?:'re| are)\s+going\s+with\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+chose\s+(.+?)[.!]?$", re.IGNORECASE),
)

_BLOCKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)(?:'re| are)\s+blocked\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+blocker\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+main\s+blocker\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+bottleneck\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_STATUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^status\s+update[:,-]?\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+status\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^project\s+status\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+status\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_COMMITMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)\s+committed\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+commitment\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+commitment\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
)

_MILESTONE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^our\s+next\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+next\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_RISK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^our\s+current\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+main\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+main\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+biggest\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+biggest\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_DEPENDENCY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^our\s+dependency\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+dependency\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+dependency\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+dependency\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:i|we)(?:'re| are)\s+dependent\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
)

_CONSTRAINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^our\s+constraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+constraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+constraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+constraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+main\s+constraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+main\s+constraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_ASSUMPTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^our\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+main\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+main\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)


@dataclass(frozen=True)
class TelegramGenericObservation:
    predicate: str
    value: str
    evidence_text: str
    fact_name: str
    label: str


@dataclass(frozen=True)
class TelegramGenericDeletion:
    predicate: str
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

    for pattern in _DECISION_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_decision",
                value=value,
                evidence_text=text,
                fact_name="current_decision",
                label="current decision",
            )

    for pattern in _BLOCKER_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_blocker",
                value=value,
                evidence_text=text,
                fact_name="current_blocker",
                label="current blocker",
            )

    for pattern in _STATUS_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_status",
                value=value,
                evidence_text=text,
                fact_name="current_status",
                label="current status",
            )

    for pattern in _COMMITMENT_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_commitment",
                value=value,
                evidence_text=text,
                fact_name="current_commitment",
                label="current commitment",
            )

    for pattern in _MILESTONE_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_milestone",
                value=value,
                evidence_text=text,
                fact_name="current_milestone",
                label="current milestone",
            )

    for pattern in _RISK_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_risk",
                value=value,
                evidence_text=text,
                fact_name="current_risk",
                label="current risk",
            )

    for pattern in _DEPENDENCY_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_dependency",
                value=value,
                evidence_text=text,
                fact_name="current_dependency",
                label="current dependency",
            )

    for pattern in _CONSTRAINT_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_constraint",
                value=value,
                evidence_text=text,
                fact_name="current_constraint",
                label="current constraint",
            )

    for pattern in _ASSUMPTION_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return TelegramGenericObservation(
                predicate="profile.current_assumption",
                value=value,
                evidence_text=text,
                fact_name="current_assumption",
                label="current assumption",
            )

    return None


def detect_telegram_generic_deletion(user_message: str) -> TelegramGenericDeletion | None:
    text = _clean_text(user_message)
    if not _is_memoryworthy_text(text):
        return None
    normalized = _strip_correction_prefix(text)
    lowered = normalized.lower()

    relationship_deletions: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("profile.cofounder_name", "cofounder", ("cofounder",)),
        ("profile.mentor_name", "mentor", ("mentor",)),
        ("profile.manager_name", "manager", ("manager",)),
        ("profile.assistant_name", "assistant", ("assistant",)),
        ("profile.partner_name", "partner", ("partner", "wife", "husband")),
        ("profile.mother_name", "mother", ("mother",)),
        ("profile.father_name", "father", ("father",)),
        ("profile.sister_name", "sister", ("sister",)),
        ("profile.brother_name", "brother", ("brother",)),
    )
    for predicate, label, aliases in relationship_deletions:
        if any(
            lowered == phrase
            for alias in aliases
            for phrase in (
                f"forget my {alias}.",
                f"forget my {alias}",
                f"delete my {alias}.",
                f"delete my {alias}",
                f"remove my {alias}.",
                f"remove my {alias}",
                f"i no longer have a {alias}.",
                f"i no longer have a {alias}",
                f"i no longer have an {alias}.",
                f"i no longer have an {alias}",
                f"i don't have a {alias} anymore.",
                f"i don't have a {alias} anymore",
                f"i don't have an {alias} anymore.",
                f"i don't have an {alias} anymore",
            )
        ):
            return TelegramGenericDeletion(
                predicate=predicate,
                evidence_text=text,
                fact_name=label,
                label=label,
            )

    if lowered in {
        "forget my current plan.",
        "forget my current plan",
        "delete my current plan.",
        "delete my current plan",
        "remove my current plan.",
        "remove my current plan",
        "forget the plan.",
        "forget the plan",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_plan",
            evidence_text=text,
            fact_name="current_plan",
            label="current plan",
        )

    if lowered in {
        "forget my current focus.",
        "forget my current focus",
        "delete my current focus.",
        "delete my current focus",
        "remove my current focus.",
        "remove my current focus",
        "forget our priority.",
        "forget our priority",
        "delete our priority.",
        "delete our priority",
        "remove our priority.",
        "remove our priority",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_focus",
            evidence_text=text,
            fact_name="current_focus",
            label="current focus",
        )

    if lowered in {
        "forget my current decision.",
        "forget my current decision",
        "delete my current decision.",
        "delete my current decision",
        "remove my current decision.",
        "remove my current decision",
        "forget our decision.",
        "forget our decision",
        "delete our decision.",
        "delete our decision",
        "remove our decision.",
        "remove our decision",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_decision",
            evidence_text=text,
            fact_name="current_decision",
            label="current decision",
        )

    if lowered in {
        "forget my current blocker.",
        "forget my current blocker",
        "delete my current blocker.",
        "delete my current blocker",
        "remove my current blocker.",
        "remove my current blocker",
        "forget our blocker.",
        "forget our blocker",
        "delete our blocker.",
        "delete our blocker",
        "remove our blocker.",
        "remove our blocker",
        "forget our bottleneck.",
        "forget our bottleneck",
        "delete our bottleneck.",
        "delete our bottleneck",
        "remove our bottleneck.",
        "remove our bottleneck",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_blocker",
            evidence_text=text,
            fact_name="current_blocker",
            label="current blocker",
        )

    if lowered in {
        "forget my current status.",
        "forget my current status",
        "delete my current status.",
        "delete my current status",
        "remove my current status.",
        "remove my current status",
        "forget our status.",
        "forget our status",
        "delete our status.",
        "delete our status",
        "remove our status.",
        "remove our status",
        "forget the project status.",
        "forget the project status",
        "delete the project status.",
        "delete the project status",
        "remove the project status.",
        "remove the project status",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_status",
            evidence_text=text,
            fact_name="current_status",
            label="current status",
        )

    if lowered in {
        "forget my current commitment.",
        "forget my current commitment",
        "delete my current commitment.",
        "delete my current commitment",
        "remove my current commitment.",
        "remove my current commitment",
        "forget our commitment.",
        "forget our commitment",
        "delete our commitment.",
        "delete our commitment",
        "remove our commitment.",
        "remove our commitment",
        "forget the commitment.",
        "forget the commitment",
        "delete the commitment.",
        "delete the commitment",
        "remove the commitment.",
        "remove the commitment",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_commitment",
            evidence_text=text,
            fact_name="current_commitment",
            label="current commitment",
        )

    if lowered in {
        "forget my current milestone.",
        "forget my current milestone",
        "delete my current milestone.",
        "delete my current milestone",
        "remove my current milestone.",
        "remove my current milestone",
        "forget our milestone.",
        "forget our milestone",
        "delete our milestone.",
        "delete our milestone",
        "remove our milestone.",
        "remove our milestone",
        "forget the milestone.",
        "forget the milestone",
        "delete the milestone.",
        "delete the milestone",
        "remove the milestone.",
        "remove the milestone",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_milestone",
            evidence_text=text,
            fact_name="current_milestone",
            label="current milestone",
        )

    if lowered in {
        "forget my current risk.",
        "forget my current risk",
        "delete my current risk.",
        "delete my current risk",
        "remove my current risk.",
        "remove my current risk",
        "forget our risk.",
        "forget our risk",
        "delete our risk.",
        "delete our risk",
        "remove our risk.",
        "remove our risk",
        "forget the risk.",
        "forget the risk",
        "delete the risk.",
        "delete the risk",
        "remove the risk.",
        "remove the risk",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_risk",
            evidence_text=text,
            fact_name="current_risk",
            label="current risk",
        )

    if lowered in {
        "forget my current dependency.",
        "forget my current dependency",
        "delete my current dependency.",
        "delete my current dependency",
        "remove my current dependency.",
        "remove my current dependency",
        "forget our dependency.",
        "forget our dependency",
        "delete our dependency.",
        "delete our dependency",
        "remove our dependency.",
        "remove our dependency",
        "forget the dependency.",
        "forget the dependency",
        "delete the dependency.",
        "delete the dependency",
        "remove the dependency.",
        "remove the dependency",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_dependency",
            evidence_text=text,
            fact_name="current_dependency",
            label="current dependency",
        )

    if lowered in {
        "forget my current constraint.",
        "forget my current constraint",
        "delete my current constraint.",
        "delete my current constraint",
        "remove my current constraint.",
        "remove my current constraint",
        "forget our constraint.",
        "forget our constraint",
        "delete our constraint.",
        "delete our constraint",
        "remove our constraint.",
        "remove our constraint",
        "forget the constraint.",
        "forget the constraint",
        "delete the constraint.",
        "delete the constraint",
        "remove the constraint.",
        "remove the constraint",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_constraint",
            evidence_text=text,
            fact_name="current_constraint",
            label="current constraint",
        )

    if lowered in {
        "forget my current assumption.",
        "forget my current assumption",
        "delete my current assumption.",
        "delete my current assumption",
        "remove my current assumption.",
        "remove my current assumption",
        "forget our assumption.",
        "forget our assumption",
        "delete our assumption.",
        "delete our assumption",
        "remove our assumption.",
        "remove our assumption",
        "forget the assumption.",
        "forget the assumption",
        "delete the assumption.",
        "delete the assumption",
        "remove the assumption.",
        "remove the assumption",
    }:
        return TelegramGenericDeletion(
            predicate="profile.current_assumption",
            evidence_text=text,
            fact_name="current_assumption",
            label="current assumption",
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
    if observation.predicate == "profile.current_decision":
        return f"I'll remember that your current decision is {value}."
    if observation.predicate == "profile.current_blocker":
        return f"I'll remember that your current blocker is {value}."
    if observation.predicate == "profile.current_status":
        return f"I'll remember that your current status is {value}."
    if observation.predicate == "profile.current_commitment":
        return f"I'll remember that your current commitment is to {value}."
    if observation.predicate == "profile.current_milestone":
        return f"I'll remember that your current milestone is {value}."
    if observation.predicate == "profile.current_risk":
        return f"I'll remember that your current risk is {value}."
    if observation.predicate == "profile.current_dependency":
        return f"I'll remember that your current dependency is {value}."
    if observation.predicate == "profile.current_constraint":
        return f"I'll remember that your current constraint is {value}."
    if observation.predicate == "profile.current_assumption":
        return f"I'll remember that your current assumption is {value}."
    return f"I'll remember that your {observation.label} is {value}."


def build_telegram_generic_deletion_answer(*, deletion: TelegramGenericDeletion) -> str:
    if deletion.predicate == "profile.current_plan":
        return "I'll forget your current plan."
    if deletion.predicate == "profile.current_focus":
        return "I'll forget your current focus."
    if deletion.predicate == "profile.current_decision":
        return "I'll forget your current decision."
    if deletion.predicate == "profile.current_blocker":
        return "I'll forget your current blocker."
    if deletion.predicate == "profile.current_status":
        return "I'll forget your current status."
    if deletion.predicate == "profile.current_commitment":
        return "I'll forget your current commitment."
    if deletion.predicate == "profile.current_milestone":
        return "I'll forget your current milestone."
    if deletion.predicate == "profile.current_risk":
        return "I'll forget your current risk."
    if deletion.predicate == "profile.current_dependency":
        return "I'll forget your current dependency."
    if deletion.predicate == "profile.current_constraint":
        return "I'll forget your current constraint."
    if deletion.predicate == "profile.current_assumption":
        return "I'll forget your current assumption."
    return f"I'll forget your {deletion.label}."


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
