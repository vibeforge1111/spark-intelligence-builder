"""Low-confidence near-miss disambiguation.

Per the H70-C+ conversational-intent-design skill pattern
"Disambiguation with One Question": when a message contains agent-
related signals but no specific intent detector fires, ask ONE
clarifying question in natural language rather than silently falling
through to chip routing / bridge / web search.

Never a numbered menu. Never twice in a row.
"""
from __future__ import annotations

import re
from typing import Any


# Signal words that indicate the user is talking about an agent surface,
# even when we can't pin down the exact intent. Ordered by specificity.
_SIGNAL_PATTERNS = [
    ("chip", re.compile(r"\b(?:chips?|domain[\s\-]chips?)\b", re.IGNORECASE)),
    ("schedule", re.compile(r"\b(?:schedules?|scheduled|cron(?:\s*job)?|autoloops?|routines?|automations?|recurring)\b", re.IGNORECASE)),
    ("loop", re.compile(r"\b(?:loops?|iterate|iterat\w*)\b", re.IGNORECASE)),
    ("mission", re.compile(r"\b(?:missions?|board|kanban|runs?)\b", re.IGNORECASE)),
    ("critic", re.compile(r"\b(?:critic|self[\s\-]?observ\w*|self[\s\-]?critic)\b", re.IGNORECASE)),
]

# Signals that the message is social / smalltalk - don't fire disambiguation.
_SMALLTALK_ANCHORS = (
    re.compile(r"^\s*(?:hi|hey|hello|yo|sup|howdy|thanks|thank\s+you|lol|haha|ok|okay|cool|nice|great|awesome)\b", re.IGNORECASE),
)

# Minimum length in words before we consider firing disambiguation. Very
# short messages tend to be reactions/smalltalk - the plain-chat path handles
# them gracefully.
_MIN_WORDS = 3


def _detect_signals(message: str) -> list[str]:
    text = str(message or "").strip()
    if not text:
        return []
    return [name for name, pat in _SIGNAL_PATTERNS if pat.search(text)]


def _register(message: str) -> str:
    text = str(message or "").strip()
    if len(text.split()) <= 4:
        return "terse"
    if text.endswith("?") or re.search(r"\b(?:how|what|why|when|where|which)\b", text, re.IGNORECASE):
        return "question"
    return "neutral"


_QUESTIONS_BY_SIGNAL: dict[str, dict[str, str]] = {
    "chip": {
        "terse": "Chip - inspect, loop, or build new?",
        "neutral": (
            "I caught 'chip' in there but I'm not sure what you want.\n\n"
            "Options I can actually do:\n"
            "- Run a loop on a specific chip (say 'loop <chip-key>')\n"
            "- List active chips (say 'which chips are active')\n"
            "- Build a new one (say 'build a chip for <brief>')\n"
            "- Inspect what a specific chip does (say 'what does <chip-key> do')\n\n"
            "Which one, or rephrase?"
        ),
    },
    "schedule": {
        "terse": "Schedule - show, create, or cancel?",
        "neutral": (
            "I caught 'schedule' but I'm not sure which angle.\n\n"
            "I can:\n"
            "- Show what's scheduled\n"
            "- Set up a new one (say 'every X run Y')\n"
            "- Cancel one (say 'cancel my <description>')\n\n"
            "Which?"
        ),
    },
    "loop": {
        "terse": "Loop - run one, or show the last result?",
        "neutral": (
            "I caught 'loop' in there.\n\n"
            "Did you want to:\n"
            "- Run a loop on a chip (say 'loop <chip-key>')\n"
            "- See the last loop's output (say 'show the last loop result')\n\n"
            "Name the chip or rephrase?"
        ),
    },
    "mission": {
        "terse": "Mission - board, or start new?",
        "neutral": (
            "I caught 'mission' in there.\n\n"
            "Options:\n"
            "- Show the mission board (say 'what's running')\n"
            "- Start a new mission (say 'run <goal>' or use /run)\n\n"
            "Which?"
        ),
    },
    "critic": {
        "terse": "Critic - run now, or show last findings?",
        "neutral": (
            "You want the self-critic.\n\n"
            "- Run it now (say 'loop domain-chip-spark-ops-critic')\n"
            "- Show the last critic findings (say 'show the last loop result')\n\n"
            "Which?"
        ),
    },
}


def detect_ambiguous_intent(message: str) -> dict[str, Any] | None:
    """Detect a near-miss intent that deserves a clarifying question.

    Returns None when:
    - message is empty / too short
    - message is pure smalltalk (no agent signals)

    Returns {signals, register, question} when we should short-circuit
    with a single clarifying question.

    Signal words take priority over casual openers: "nice, tell me about
    this chip" still fires because "chip" is a signal even though "nice"
    leads the message.
    """
    text = str(message or "").strip()
    if not text:
        return None
    if len(text.split()) < _MIN_WORDS:
        return None
    signals = _detect_signals(text)
    if not signals:
        # Only then check if it's pure smalltalk to avoid firing on "hi"
        for pat in _SMALLTALK_ANCHORS:
            if pat.match(text):
                return None
        return None
    primary = signals[0]
    register = _register(text)
    question_family = _QUESTIONS_BY_SIGNAL.get(primary, {})
    question = question_family.get(register) or question_family.get("neutral") or ""
    if not question:
        return None
    return {"signals": signals, "primary": primary, "register": register, "question": question}


def format_clarifying_question(intent: dict[str, Any]) -> str:
    return str(intent.get("question") or "").strip()
