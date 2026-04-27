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
# Messages that look like requests, instructions, or questions to the agent
# should not be filed as raw episodes. They expect a real reply.
_ASK_LIKE_PREFIX_PATTERN = re.compile(
    r"^(?:"
    r"list|explain|tell me|show me|give me|describe|summari[sz]e|compare|help me|"
    r"draft|write|outline|recommend|suggest|propose|analy[sz]e|review|critique|"
    r"check|investigate|research|find|search|look up|"
    r"what|why|how|where|when|which|who|whom|whose|"
    r"should|can|could|would|will|shall|do|does|did|are|is|was|were|am|may|might|have|has|had"
    r")\b",
    re.IGNORECASE,
)

# Emotional self-reports. The user is telling Spark how they feel, not filing
# a fact for later recall. These messages expect engagement with the emotional
# state, not a "Noted: ..." filing acknowledgement. The agent's chip-defined
# empathy_style decides how the LLM responds; the classifier just routes
# them out of the raw_episode bucket so the LLM actually sees them.
_FEELING_STATE_INTENSIFIERS = r"(?:so|really|genuinely|kind of|a bit|totally|very|pretty|incredibly|absolutely|honestly|just)"
_FEELING_STATE_WORDS = (
    r"anxious|tired|exhausted|frustrated|fried|wrecked|done|burnt|burned|"
    r"stressed|overwhelmed|depressed|lonely|isolated|worried|nervous|scared|"
    r"angry|upset|sad|hopeless|helpless|lost|stuck|confused|drained|fed up|"
    r"pumped|energized|excited|thrilled|elated|amped|hyped|nervous|terrified|"
    r"freaking out|losing it|cracking|breaking down"
)
_FEELING_STATE_PATTERN = re.compile(
    r"\b(?:"
    # I'm/I am/I've been + emotional state
    r"i(?:'m| am)\s+(?:" + _FEELING_STATE_INTENSIFIERS + r"\s+)*(?:" + _FEELING_STATE_WORDS + r")"
    r"|i(?:'ve| have)\s+been\s+(?:" + _FEELING_STATE_INTENSIFIERS + r"\s+)*(?:" + _FEELING_STATE_WORDS + r")"
    r"|i\s+(?:feel|felt|am feeling|was feeling)\s+(?:" + _FEELING_STATE_INTENSIFIERS + r"\s+)*(?:" + _FEELING_STATE_WORDS + r")"
    r"|i(?:'m| am)\s+(?:starting to|getting|going to|about to)\s+(?:lose it|cry|break|crack|snap|panic)"
    # body/sleep self-reports
    r"|i(?:'ve| have)\s+been\s+up\s+(?:since|all|for)"
    r"|i(?:'m| am)\s+up\s+at\s+\d"
    r"|i\s+(?:haven't|have not|cant|can't|cannot)\s+(?:slept|sleep|focus|think|breathe|stop)"
    r"|i\s+haven't\s+(?:taken|had)\s+a\s+(?:real\s+)?(?:day|break|night|moment)"
    # burnout / quitting signals
    r"|(?:burnt|burned)\s+out"
    r"|dream(?:ing|t)?\s+about\s+quitting"
    r"|i\s+want\s+to\s+quit"
    # general emotional flags appearing anywhere
    r"|my\s+(?:bandwidth|energy|capacity)\s+is\s+(?:low|gone|tapped|fried)"
    r")\b",
    re.IGNORECASE,
)

_PLAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)\s+plan\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(
        r"^(?:memory\s+update:\s*)?(?:my|our|the)\s+current\s+plan\s+is\s+(?:to\s+)?(.+?)[.!]?\s+"
        r"please\s+save(?:\s+this)?(?:\s+as\s+my\s+current\s+plan)?[.!]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:memory\s+update:\s*)?(?:my|our|the)\s+current\s+plan\s+is\s+(?:to\s+)?(.+?)[.!]?$",
        re.IGNORECASE,
    ),
    re.compile(r"^our\s+current\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
)

_FOCUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i(?:'m| am)|we(?:'re| are))\s+focusing\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+priority\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:memory\s+update:\s*)?(?:my|our|the)\s+current\s+focus\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+focus\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+focus\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
)

_DECISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:i|we)\s+decided\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^(?:i|we)\s+decided\s+that\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+decision\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+decision\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+decision\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^our\s+current\s+decision\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+decision\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+decision\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
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
    re.compile(r"^our\s+current\s+commitment\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
    re.compile(r"^the\s+current\s+commitment\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
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
    re.compile(r"^.+?\s+is\s+(?:(?:currently|still)\s+)?owned\s+by\s+(.+?)(?:\s+during\b.+?)?[.!]?$", re.IGNORECASE),
    re.compile(r"^.+?\s+is\s+handled\s+by\s+(.+?)(?:\s+during\b.+?)?[.!]?$", re.IGNORECASE),
    re.compile(r"^([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+owns\s+.+?[.!]?$", re.IGNORECASE),
)

_KINSHIP_ALIAS_TO_CANONICAL = {
    "mom": "mother",
    "mum": "mother",
    "mother": "mother",
    "dad": "father",
    "father": "father",
    "sister": "sister",
    "brother": "brother",
}
_KINSHIP_ALIAS_PATTERN = r"mom|mum|mother|dad|father|sister|brother"
_FAMILY_SHARED_TIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\bmy\s+({_KINSHIP_ALIAS_PATTERN})\s+(?:came over|dropped by|visited(?:\s+(?:me|us))?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:i\s+)?(?:spent|spend|spending)\s+time\s+with\s+my\s+({_KINSHIP_ALIAS_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\bhung out with\s+my\s+({_KINSHIP_ALIAS_PATTERN})\b", re.IGNORECASE),
)

TelegramGenericMemoryRole = Literal["current_state", "structured_evidence", "event", "belief_candidate"]
TelegramGenericOperation = Literal["update", "delete"]
TelegramGenericAssessmentOutcome = Literal[
    "drop",
    "raw_episode",
    "structured_evidence",
    "current_state",
    "event",
    "belief_candidate",
]
TelegramAssessmentMemoryRole = Literal["raw_episode", "current_state", "structured_evidence", "event", "belief_candidate"]


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
    revalidation_days: int | None = None


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
        domain_pack="relationships",
        predicate="profile.recent_family_members",
        fact_name="recent_family_members",
        label="family members you spent time with recently",
        retention_class="active_state",
        update_patterns=(),
        delete_phrases=_simple_delete_phrases("my recent family time", "recent family members"),
        observation_answer_template="I'll remember you recently spent time with: {value}.",
        deletion_answer_template="I'll forget your recent family time.",
        revalidation_days=14,
    ),
    TelegramGenericPack(
        domain_pack="preferences",
        predicate="profile.favorite_color",
        fact_name="favorite_color",
        label="favorite color",
        retention_class="durable_profile",
        update_patterns=(
            re.compile(r"^my\s+favou?rite\s+color\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        delete_phrases=_simple_delete_phrases("my favorite color", "my favourite color"),
        observation_answer_template="I'll remember that your favorite color is {value}.",
        deletion_answer_template="I'll forget your favorite color.",
    ),
    TelegramGenericPack(
        domain_pack="preferences",
        predicate="profile.favorite_food",
        fact_name="favorite_food",
        label="favorite food",
        retention_class="durable_profile",
        update_patterns=(
            re.compile(r"^my\s+favou?rite\s+food\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"^the\s+food\s+i\s+love\s+the\s+most\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        delete_phrases=_simple_delete_phrases("my favorite food", "my favourite food"),
        observation_answer_template="I'll remember that your favorite food is {value}.",
        deletion_answer_template="I'll forget your favorite food.",
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
        revalidation_days=30,
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
        revalidation_days=21,
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
        revalidation_days=21,
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
        revalidation_days=21,
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
        revalidation_days=30,
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
        revalidation_days=14,
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
        revalidation_days=14,
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
        revalidation_days=14,
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
        revalidation_days=14,
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
        revalidation_days=14,
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
        revalidation_days=30,
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
        revalidation_days=21,
    ),
)

_GENERIC_PACKS_BY_PREDICATE = {pack.predicate: pack for pack in _GENERIC_PACKS}


def pack_revalidation_days(predicate: str | None) -> int | None:
    if predicate is None:
        return None
    pack = _GENERIC_PACKS_BY_PREDICATE.get(predicate)
    if pack is None:
        return None
    return pack.revalidation_days

_BELIEF_PREFIX_PATTERN = re.compile(
    r"^(?:i think|we think|i believe|we believe|it seems|it looks like|probably|likely)\b",
    re.IGNORECASE,
)
_STRUCTURED_EVIDENCE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:because|customer said|users? said|we saw|i saw|we found|i found|metrics show|data show|observed|learned that)\b"
    r"|^(?:(?:the\s+)?(?:customer interviews?|interview notes?|weekly review|roadmap notes?|user research|research notes?|call notes?|meeting notes?))\s+"
    r"(?:confirm|confirms|show|shows|suggest|suggests|say|says)\b"
    r"|^after testing\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TelegramGenericAssessment:
    outcome: TelegramGenericAssessmentOutcome
    evidence_text: str
    reason: str
    memory_role: TelegramAssessmentMemoryRole | None = None
    retention_class: str | None = None
    domain_pack: str | None = None
    predicate: str | None = None
    value: str | None = None
    operation: TelegramGenericOperation | None = None
    fact_name: str | None = None
    label: str | None = None


def assess_telegram_generic_memory_candidate(user_message: str) -> TelegramGenericAssessment:
    text = _clean_text(user_message)
    if not text:
        return TelegramGenericAssessment(outcome="drop", evidence_text=text, reason="empty")

    candidate = classify_telegram_generic_memory_candidate(user_message)
    if candidate is not None:
        return TelegramGenericAssessment(
            outcome="current_state",
            evidence_text=candidate.evidence_text,
            reason="domain_pack_match",
            memory_role="current_state",
            retention_class=candidate.retention_class,
            domain_pack=candidate.domain_pack,
            predicate=candidate.predicate,
            value=candidate.value,
            operation=candidate.operation,
            fact_name=candidate.fact_name,
            label=candidate.label,
        )

    if not _is_memoryworthy_text(text):
        return TelegramGenericAssessment(outcome="drop", evidence_text=text, reason="not_memoryworthy")

    normalized = _strip_correction_prefix(text)
    if _BELIEF_PREFIX_PATTERN.search(normalized):
        return TelegramGenericAssessment(
            outcome="belief_candidate",
            evidence_text=text,
            reason="belief_marker",
            memory_role="belief_candidate",
            retention_class="derived_belief",
            domain_pack="beliefs_and_inferences",
            value=normalized,
        )

    if _STRUCTURED_EVIDENCE_PATTERN.search(normalized):
        return TelegramGenericAssessment(
            outcome="structured_evidence",
            evidence_text=text,
            reason="evidence_marker",
            memory_role="structured_evidence",
            retention_class="episodic_archive",
            domain_pack="evidence",
            value=normalized,
        )

    return TelegramGenericAssessment(
        outcome="raw_episode",
        evidence_text=text,
        reason="meaningful_but_unpromoted",
        memory_role="raw_episode",
        retention_class="episodic_archive",
        domain_pack="raw_episode",
        value=normalized,
    )


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

    family_shared_time_value = _family_shared_time_members_value(normalized)
    if family_shared_time_value:
        return TelegramGenericCandidate(
            predicate="profile.recent_family_members",
            value=family_shared_time_value,
            evidence_text=text,
            fact_name="recent_family_members",
            label="family members you spent time with recently",
            operation="update",
            memory_role="current_state",
            retention_class="active_state",
            domain_pack="relationships",
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


def _family_shared_time_members_value(text: str) -> str | None:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _FAMILY_SHARED_TIME_PATTERNS:
        for match in pattern.finditer(text):
            canonical = _KINSHIP_ALIAS_TO_CANONICAL.get(match.group(1).lower())
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            found.append(canonical)
    if not found:
        return None
    return ", ".join(found)


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
    if observation.predicate == "profile.current_plan":
        return f"I'll remember that your current plan is {_current_plan_value_clause(value)}."
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


_CURRENT_PLAN_VERB_STARTERS = {
    "add",
    "analyze",
    "build",
    "check",
    "complete",
    "confirm",
    "create",
    "debug",
    "deploy",
    "do",
    "finish",
    "fix",
    "get",
    "improve",
    "inspect",
    "launch",
    "make",
    "migrate",
    "patch",
    "refactor",
    "restart",
    "review",
    "run",
    "save",
    "seed",
    "send",
    "ship",
    "simplify",
    "start",
    "test",
    "update",
    "validate",
    "verify",
    "wire",
    "work",
    "write",
}


def _current_plan_value_clause(value: str) -> str:
    normalized = _clean_text(value)
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.startswith("to "):
        return normalized
    first_word_match = re.match(r"[a-z']+", lowered)
    first_word = first_word_match.group(0) if first_word_match else ""
    if first_word in _CURRENT_PLAN_VERB_STARTERS:
        return f"to {normalized}"
    return normalized


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
    if _ASK_LIKE_PREFIX_PATTERN.match(text.lstrip()):
        return False
    if _FEELING_STATE_PATTERN.search(text):
        return False
    return True


def _clean_value(value: str) -> str:
    cleaned = _clean_text(value)
    while cleaned.endswith((".", "!", ",")):
        cleaned = cleaned[:-1].rstrip()
    return cleaned
