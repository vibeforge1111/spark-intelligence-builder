from __future__ import annotations

import re
from typing import Any


# Create a chip FROM a natural-language brief.
# Patterns target explicit creation verbs paired with chip vocabulary.
_CREATE_PATTERNS = (
    # "make / build / create / scaffold / generate / spin up ... chip"
    re.compile(
        r"\b(?:make|build|create|scaffold|generate|spin\s+up|cook\s+up|craft|author|whip\s+up)\b"
        r"[^.\n]{0,40}\b(?:chip|domain[\s\-]chip)\b",
        re.IGNORECASE,
    ),
    # "I need / I want a chip ..."
    re.compile(
        r"\bi\s+(?:need|want|could\s+use|would\s+like)\b[^.\n]{0,20}\b(?:a|another|new)?\s*(?:chip|domain[\s\-]chip)\b",
        re.IGNORECASE,
    ),
    # "new chip for ..."
    re.compile(r"\b(?:a\s+)?new\s+(?:domain[\s\-])?chip\s+(?:for|that|which|to)\b", re.IGNORECASE),
    # Imperative "chip for X please"
    re.compile(r"^\s*chip\s+(?:for|that|which|to)\b", re.IGNORECASE),
)

# If any of these negative anchors appear, do NOT fire chip-create - the user
# is likely talking ABOUT chips, not asking for one.
_NEGATIVE_ANCHORS = (
    re.compile(r"\b(?:use|load|activate|pin|unpin|disable|delete|remove|cancel|kill)\s+(?:the\s+)?[\w\-]+\s*chip\b", re.IGNORECASE),
    re.compile(r"\b(?:which|what)\s+chips?\b", re.IGNORECASE),
    re.compile(r"\bhow\s+does\s+(?:the\s+)?[\w\-]+\s*chip\s+work\b", re.IGNORECASE),
)


def _extract_brief(message: str) -> str:
    """Strip the command-verb + 'chip' tokens from the front so we pass the
    remaining description through as the brief."""
    text = str(message or "").strip()
    if not text:
        return ""
    # Remove leading patterns like "build me a chip for X" -> "X"
    stripped = re.sub(
        r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:could\s+you\s+)?"
        r"(?:make|build|create|scaffold|generate|spin\s+up|cook\s+up|craft|author|whip\s+up)\s+"
        r"(?:me\s+)?(?:a\s+|another\s+|new\s+)?"
        r"(?:domain[\s\-])?chip\s+(?:for\s+|that\s+|which\s+|to\s+)?",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if stripped == text:
        stripped = re.sub(
            r"^\s*i\s+(?:need|want|could\s+use|would\s+like)\s+"
            r"(?:a\s+|another\s+|new\s+)?(?:domain[\s\-])?chip\s+(?:for\s+|that\s+|which\s+|to\s+)?",
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    return stripped.strip().rstrip(".!?")


def detect_chip_create_intent(message: str) -> dict | None:
    """Detect intent to scaffold a NEW domain chip from a brief."""
    text = str(message or "").strip()
    if not text:
        return None
    for neg in _NEGATIVE_ANCHORS:
        if neg.search(text):
            return None
    for pat in _CREATE_PATTERNS:
        if pat.search(text):
            brief = _extract_brief(text) or text
            return {"action": "create", "brief": brief}
    return None


def format_chip_create_suggestion(brief: str) -> str:
    trimmed = (brief or "").strip()
    if not trimmed:
        return (
            "Sounds like you want me to scaffold a new chip. Give me a one-line "
            "description and tap /chip create <description>."
        )
    return (
        f"Got it - scaffolding a chip for {trimmed!r}. "
        f"Tap /chip create {trimmed} to fire it (takes 30-60s). "
        f"I hand this off to the slash command so you can see the scaffolder's "
        f"output directly and cancel if the brief needs tweaking."
    )
