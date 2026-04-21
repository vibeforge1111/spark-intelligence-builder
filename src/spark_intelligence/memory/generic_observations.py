from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


_CORRECTION_PREFIX_PATTERN = re.compile(r"^(?:actually|update|correction)[:,]?\s+", re.IGNORECASE)
_HYPOTHETICAL_PREFIX_PATTERN = re.compile(
    r"^(?:maybe|perhaps|what if|if|hopefully|i might|i may|i could|i should)\b",
    re.IGNORECASE,
)
_SMALL_TALK_PATTERN = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|cool|lol|noted|got it)[.!]?$",
    re.IGNORECASE,
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

_OWNER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^our\s+owner\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+owner\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+owner\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+owner\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+owner\s+for\s+this\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

TelegramGenericMemoryRole = Literal["current_state", "structured_evidence", "event", "belief_candidate"]
TelegramGenericOperation = Literal["update", "delete"]


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


@dataclass(frozen=True)
class TelegramGenericCandidate:
    predicate: str
    value: str | None
    evidence_text: str
    fact_name: str
    label: str
    operation: TelegramGenericOperation
    memory_role: TelegramGenericMemoryRole
    retention_class: str
    domain_pack: str


@dataclass(frozen=True)
class TelegramGenericPack:
    domain_pack: str
    predicate: str
    fact_name: str
    label: str
    retention_class: str
    update_patterns: tuple[re.Pattern[str], ...]
    delete_phrases: tuple[str, ...]
    observation_answer_template: str | None = None
    deletion_answer_template: str | None = None


def _simple_delete_phrases(*targets: str) -> tuple[str, ...]:
    phrases: list[str] = []
    for target in targets:
        for verb in ("forget", "delete", "remove"):
            phrases.append(f"{verb} {target}.")
            phrases.append(f"{verb} {target}")
    return tuple(phrases)


def _relationship_delete_phrases(*aliases: str) -> tuple[str, ...]:
    phrases = list(_simple_delete_phrases(*(f"my {alias}" for alias in aliases)))
    for alias in aliases:
        for article in ("a", "an"):
            phrases.append(f"i no longer have {article} {alias}.")
            phrases.append(f"i no longer have {article} {alias}")
            phrases.append(f"i don't have {article} {alias} anymore.")
            phrases.append(f"i don't have {article} {alias} anymore")
    return tuple(phrases)


_GENERIC_PACKS: tuple[TelegramGenericPack, ...] = (
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.cofounder_name",
        fact_name="cofounder",
        label="cofounder",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+cofounder\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("cofounder"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.mentor_name",
        fact_name="mentor",
        label="mentor",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+mentor\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("mentor"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.manager_name",
        fact_name="manager",
        label="manager",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+manager\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("manager"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.assistant_name",
        fact_name="assistant",
        label="assistant",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+assistant\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("assistant"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.partner_name",
        fact_name="partner",
        label="partner",
        retention_class="durable_profile",
        update_patterns=(
            re.compile(r"^my\s+partner\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"^my\s+wife\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"^my\s+husband\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        delete_phrases=_relationship_delete_phrases("partner", "wife", "husband"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.mother_name",
        fact_name="mother",
        label="mother",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+mother\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("mother"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.father_name",
        fact_name="father",
        label="father",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+father\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("father"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.sister_name",
        fact_name="sister",
        label="sister",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+sister\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("sister"),
    ),
    TelegramGenericPack(
        domain_pack="relationships",
        predicate="profile.brother_name",
        fact_name="brother",
        label="brother",
        retention_class="durable_profile",
        update_patterns=(re.compile(r"^my\s+brother\s+is\s+(.+?)[.!]?$", re.IGNORECASE),),
        delete_phrases=_relationship_delete_phrases("brother"),
    ),
    TelegramGenericPack(
        domain_pack="goals_and_priorities",
        predicate="profile.current_plan",
        fact_name="current_plan",
        label="current plan",
        retention_class="active_state",
        update_patterns=_PLAN_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current plan", "the plan"),
        observation_answer_template="I'll remember that your current plan is to {value}.",
        deletion_answer_template="I'll forget your current plan.",
    ),
    TelegramGenericPack(
        domain_pack="goals_and_priorities",
        predicate="profile.current_focus",
        fact_name="current_focus",
        label="current focus",
        retention_class="active_state",
        update_patterns=_FOCUS_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current focus", "our priority"),
        deletion_answer_template="I'll forget your current focus.",
    ),
    TelegramGenericPack(
        domain_pack="plans_and_commitments",
        predicate="profile.current_commitment",
        fact_name="current_commitment",
        label="current commitment",
        retention_class="active_state",
        update_patterns=_COMMITMENT_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current commitment", "our commitment", "the commitment"),
        observation_answer_template="I'll remember that your current commitment is to {value}.",
        deletion_answer_template="I'll forget your current commitment.",
    ),
    TelegramGenericPack(
        domain_pack="plans_and_commitments",
        predicate="profile.current_milestone",
        fact_name="current_milestone",
        label="current milestone",
        retention_class="active_state",
        update_patterns=_MILESTONE_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current milestone", "our milestone", "the milestone"),
        deletion_answer_template="I'll forget your current milestone.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_decision",
        fact_name="current_decision",
        label="current decision",
        retention_class="active_state",
        update_patterns=_DECISION_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current decision", "our decision"),
        deletion_answer_template="I'll forget your current decision.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_blocker",
        fact_name="current_blocker",
        label="current blocker",
        retention_class="active_state",
        update_patterns=_BLOCKER_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current blocker", "our blocker", "our bottleneck"),
        deletion_answer_template="I'll forget your current blocker.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_status",
        fact_name="current_status",
        label="current status",
        retention_class="active_state",
        update_patterns=_STATUS_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current status", "our status", "the project status"),
        deletion_answer_template="I'll forget your current status.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_risk",
        fact_name="current_risk",
        label="current risk",
        retention_class="active_state",
        update_patterns=_RISK_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current risk", "our risk", "the risk"),
        deletion_answer_template="I'll forget your current risk.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_dependency",
        fact_name="current_dependency",
        label="current dependency",
        retention_class="active_state",
        update_patterns=_DEPENDENCY_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current dependency", "our dependency", "the dependency"),
        deletion_answer_template="I'll forget your current dependency.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_constraint",
        fact_name="current_constraint",
        label="current constraint",
        retention_class="active_state",
        update_patterns=_CONSTRAINT_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current constraint", "our constraint", "the constraint"),
        deletion_answer_template="I'll forget your current constraint.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_assumption",
        fact_name="current_assumption",
        label="current assumption",
        retention_class="active_state",
        update_patterns=_ASSUMPTION_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current assumption", "our assumption", "the assumption"),
        deletion_answer_template="I'll forget your current assumption.",
    ),
    TelegramGenericPack(
        domain_pack="project_state",
        predicate="profile.current_owner",
        fact_name="current_owner",
        label="current owner",
        retention_class="active_state",
        update_patterns=_OWNER_PATTERNS,
        delete_phrases=_simple_delete_phrases("my current owner", "our owner", "the owner"),
        deletion_answer_template="I'll forget your current owner.",
    ),
)

_GENERIC_PACKS_BY_PREDICATE = {pack.predicate: pack for pack in _GENERIC_PACKS}


def classify_telegram_generic_memory_candidate(user_message: str) -> TelegramGenericCandidate | None:
    text = _clean_text(user_message)
    if not _is_memoryworthy_text(text):
        return None
    normalized = _strip_correction_prefix(text)
    lowered = normalized.lower()

    for pack in _GENERIC_PACKS:
        if lowered in pack.delete_phrases:
            return TelegramGenericCandidate(
                predicate=pack.predicate,
                value=None,
                evidence_text=text,
                fact_name=pack.fact_name,
                label=pack.label,
                operation="delete",
                memory_role="current_state",
                retention_class=pack.retention_class,
                domain_pack=pack.domain_pack,
            )

    for pack in _GENERIC_PACKS:
        for pattern in pack.update_patterns:
            match = pattern.fullmatch(normalized)
            if match is None:
                continue
            value = _clean_value(match.group(1))
            if not value:
                continue
            return TelegramGenericCandidate(
                predicate=pack.predicate,
                value=value,
                evidence_text=text,
                fact_name=pack.fact_name,
                label=pack.label,
                operation="update",
                memory_role="current_state",
                retention_class=pack.retention_class,
                domain_pack=pack.domain_pack,
            )
    return None


def detect_telegram_generic_observation(user_message: str) -> TelegramGenericObservation | None:
    candidate = classify_telegram_generic_memory_candidate(user_message)
    if candidate is None or candidate.operation != "update" or not candidate.value:
        return None
    return TelegramGenericObservation(
        predicate=candidate.predicate,
        value=candidate.value,
        evidence_text=candidate.evidence_text,
        fact_name=candidate.fact_name,
        label=candidate.label,
    )


def detect_telegram_generic_deletion(user_message: str) -> TelegramGenericDeletion | None:
    candidate = classify_telegram_generic_memory_candidate(user_message)
    if candidate is None or candidate.operation != "delete":
        return None
    return TelegramGenericDeletion(
        predicate=candidate.predicate,
        evidence_text=candidate.evidence_text,
        fact_name=candidate.fact_name,
        label=candidate.label,
    )


def build_telegram_generic_observation_answer(*, observation: TelegramGenericObservation) -> str:
    value = str(observation.value or "").strip()
    if not value:
        return "I'll remember that."
    pack = _GENERIC_PACKS_BY_PREDICATE.get(observation.predicate)
    if pack is not None and pack.observation_answer_template:
        return pack.observation_answer_template.format(label=observation.label, value=value)
    return f"I'll remember that your {observation.label} is {value}."


def build_telegram_generic_deletion_answer(*, deletion: TelegramGenericDeletion) -> str:
    pack = _GENERIC_PACKS_BY_PREDICATE.get(deletion.predicate)
    if pack is not None and pack.deletion_answer_template:
        return pack.deletion_answer_template.format(label=deletion.label)
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
