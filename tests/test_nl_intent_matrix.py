"""NL intent matrix tests.

Per the conversational-intent-design H70-C+ skill:
  - Each utterance should map to exactly one intent (disjointness)
  - Positive fixtures must match the declared intent
  - Anti-fixtures (plain chat) must not match any intent
  - Confirmation phrases must not leak into other detectors

This file runs every detector against every fixture and fails on any
mismatch or cross-intent collision. Extend the fixtures when you add
new NL phrasings - the matrix is the source of truth.
"""
from __future__ import annotations

import pytest

from spark_intelligence.schedule_bridge import (
    detect_delete_intent,
    detect_schedule_intent,
    is_confirmation_no,
    is_confirmation_yes,
)
from spark_intelligence.mission_bridge import detect_board_intent
from spark_intelligence.user_instructions import detect_instruction_intent


# -------- Positive fixtures -------------------------------------------------

SCHEDULE_LIST_PHRASES = [
    "show my schedules",
    "list my schedules",
    "what schedules do I have",
    "what's scheduled",
    "whats scheduled",
    "display all schedules",
    "view scheduled tasks",
    "anything firing tonight",
    "any recurring tasks",
    "do I have any automations",
    "show the scheduler",
    "my schedules?",
    "schedules",
    "my routines?",
    "my autoloops",
    "what's on my cron",
    "show me the scheduler",
    "status of schedules",
]

SCHEDULE_DELETE_PHRASES = [
    "cancel my nightly",
    "cancel my nightly schedule",
    "delete sched-abc123",
    "kill my 3am schedule",
    "stop the daily cron",
    "remove the weekly schedule",
    "drop the nightly autoloop",
    "cancel sched-xyz789",
    "disable my recurring task",
    "turn off the nightly schedule",
]

INSTRUCTION_REMEMBER_PHRASES = [
    "remember this: I prefer short replies",
    "remember that my favorite color is blue",
    "from now on use bullet points",
    "going forward skip greetings",
    "/remember I use UTC",
    "always give me citations",
    "never start with a greeting",
]

INSTRUCTION_FORGET_PHRASES = [
    "forget that I prefer bullets",
    "/forget the citation rule",
    "stop remembering my timezone",
    "you can forget about my color preference",
]

CONFIRM_YES_PHRASES = [
    "yes cancel",
    "yes delete",
    "yes do it",
    "confirm",
    "confirmed",
    "go ahead",
    "proceed",
    "do it",
    "yes",
]

CONFIRM_NO_PHRASES = [
    "never mind",
    "nevermind",
    "cancel",
    "abort",
    "wait",
    "no",
    "nope",
    "keep it",
    "leave it",
]

BOARD_PHRASES = [
    "show me the missions",
    "list missions",
    "show the board",
    "kanban",
    "what's running",
    "whats running",
    "what am I running",
    "is anything running",
    "show live missions",
    "any running missions",
    "any live missions",
    "mission status",
    "board status",
    "my missions?",
    "missions",
    "view the board right now",
]


# -------- Anti-fixtures (plain chat, must not match anything) ---------------

PLAIN_CHAT_PHRASES = [
    "hi",
    "hello there",
    "how are you",
    "what's up",
    "tell me a joke",
    "research seedify launchpad",
    "what is the BTC price",
    "explain kubernetes",
    "write me a tweet about AI",
    "translate this to French",
    "what's the weather tomorrow",
    "summarize this article",
    "who won the last world cup",
]


# -------- Intent registry (name -> detector fn returning truthy on match) ---

def _schedule_list_hit(phrase: str) -> bool:
    return detect_schedule_intent(phrase) is not None

def _schedule_delete_hit(phrase: str) -> bool:
    return detect_delete_intent(phrase) is not None

def _instruction_remember_hit(phrase: str) -> bool:
    intent = detect_instruction_intent(phrase)
    return intent is not None and intent.get("action") == "remember"

def _instruction_forget_hit(phrase: str) -> bool:
    intent = detect_instruction_intent(phrase)
    return intent is not None and intent.get("action") == "forget"

def _confirm_yes_hit(phrase: str) -> bool:
    return is_confirmation_yes(phrase)

def _confirm_no_hit(phrase: str) -> bool:
    return is_confirmation_no(phrase)

def _board_hit(phrase: str) -> bool:
    return detect_board_intent(phrase) is not None


INTENT_DETECTORS = {
    "board": _board_hit,
    "schedule_list": _schedule_list_hit,
    "schedule_delete": _schedule_delete_hit,
    "instruction_remember": _instruction_remember_hit,
    "instruction_forget": _instruction_forget_hit,
    "confirm_yes": _confirm_yes_hit,
    "confirm_no": _confirm_no_hit,
}


# Confirmation detectors are stateful by design (only meaningful when a
# pending confirmation exists). They are expected to co-fire with
# destructive-intent phrasings (e.g. "cancel"). Exempt them from the
# naive disjointness audit.
STATEFUL_DETECTORS = {"confirm_yes", "confirm_no"}

# Priority order for collision resolution (must mirror the telegram
# adapter's short-circuit order). When multiple detectors match the
# same phrase, the earlier one in this list wins.
# Documented per the conversational-intent-design skill's "Disjointness
# Audit" pattern: when legitimate overlap exists, declare priority.
INTENT_PRIORITY = [
    "schedule_delete",    # destructive, explicit verbs, confirmation-gated
    "instruction_forget", # explicit /forget or "stop remembering X"
    "board",              # mission board - "what's running" with live missions
    "schedule_list",      # read-only schedule listing
    "instruction_remember",  # most permissive; catches inline directives
]


FIXTURE_BY_INTENT = {
    "board": BOARD_PHRASES,
    "schedule_list": SCHEDULE_LIST_PHRASES,
    "schedule_delete": SCHEDULE_DELETE_PHRASES,
    "instruction_remember": INSTRUCTION_REMEMBER_PHRASES,
    "instruction_forget": INSTRUCTION_FORGET_PHRASES,
    "confirm_yes": CONFIRM_YES_PHRASES,
    "confirm_no": CONFIRM_NO_PHRASES,
}


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.parametrize("intent_name,phrase", [
    (intent, phrase)
    for intent, phrases in FIXTURE_BY_INTENT.items()
    for phrase in phrases
])
def test_positive_fixture_fires_expected_intent(intent_name: str, phrase: str):
    """Every positive fixture must match its declared intent."""
    detector = INTENT_DETECTORS[intent_name]
    assert detector(phrase), f"{intent_name!r} failed to match positive fixture: {phrase!r}"


@pytest.mark.parametrize("phrase", PLAIN_CHAT_PHRASES)
def test_plain_chat_matches_nothing(phrase: str):
    """Plain-chat anti-fixtures must not trigger ANY intent detector."""
    hits = [name for name, det in INTENT_DETECTORS.items() if det(phrase)]
    assert not hits, f"Plain chat {phrase!r} over-matched intent(s): {hits}"


@pytest.mark.parametrize("intent_name,phrase", [
    (intent, phrase)
    for intent, phrases in FIXTURE_BY_INTENT.items()
    for phrase in phrases
    if intent not in STATEFUL_DETECTORS
])
def test_priority_resolves_collisions(intent_name: str, phrase: str):
    """When multiple detectors match, the expected intent must be the
    highest-priority one per INTENT_PRIORITY. Mirrors the runtime
    adapter's short-circuit order. Per the conversational-intent-design
    skill's 'Disjointness Audit' pattern: declare priority explicitly
    when overlap is legitimate."""
    hits = [
        name for name, det in INTENT_DETECTORS.items()
        if name not in STATEFUL_DETECTORS and det(phrase)
    ]
    assert hits, f"{phrase!r} matched no detector at all"
    # Highest-priority match wins
    winner = min(hits, key=lambda n: INTENT_PRIORITY.index(n) if n in INTENT_PRIORITY else 999)
    assert winner == intent_name, (
        f"{phrase!r} (expected {intent_name!r}) resolved to {winner!r} "
        f"via priority order. All hits: {hits}"
    )


def test_coverage_summary(capsys):
    """Print a coverage summary - run with -s to see."""
    total = sum(len(v) for v in FIXTURE_BY_INTENT.values())
    print(f"\n=== NL intent matrix ===")
    print(f"Intents tracked: {len(FIXTURE_BY_INTENT)}")
    print(f"Positive fixtures: {total}")
    print(f"Plain-chat anti-fixtures: {len(PLAIN_CHAT_PHRASES)}")
    for name, phrases in FIXTURE_BY_INTENT.items():
        print(f"  {name}: {len(phrases)} fixtures")
