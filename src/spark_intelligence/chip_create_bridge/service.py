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
    remaining description through as the brief. Collapses all whitespace
    (including newlines) so the brief stays clean on a single line."""
    text = str(message or "").strip()
    if not text:
        return ""
    # Normalize whitespace first - convert any newlines/tabs/double-spaces to
    # single spaces so later processing and the Telegram echo stay clean.
    text = re.sub(r"\s+", " ", text)
    # Strip polite/lead-in tokens
    text = re.sub(
        r"^\s*(?:let[']?s\s+|please\s+|hey\s+|ok\s+|okay\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Iteratively strip lead tokens: creation verb, "a/another/new", domain-chip
    # prefix, the explicit chip-name token, optional connector words. Repeat
    # until no further trimming happens so compound phrasings collapse cleanly.
    stripped = text
    for _ in range(6):
        before = stripped
        stripped = re.sub(
            r"^\s*(?:make|build|create|scaffold|generate|spin\s+up|cook\s+up|craft|author|whip\s+up)\s+(?:me\s+)?",
            "",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(
            r"^\s*i\s+(?:need|want|could\s+use|would\s+like)\s+",
            "",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(r"^\s*(?:a|an|another|new)\s+", "", stripped, count=1, flags=re.IGNORECASE)
        stripped = re.sub(
            r"^\s*(?:domain[\s\-])?chip\s+(?:called\s+|named\s+)?",
            "",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(
            r"^\s*domain-chip-[\w\-]+\s*[:\-,]?\s*",
            "",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        stripped = re.sub(
            r"^\s*(?:for|that|which|to|about)\s+",
            "",
            stripped,
            count=1,
            flags=re.IGNORECASE,
        )
        if stripped == before:
            break
    return stripped.strip().rstrip(".!?,")


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
    trimmed = re.sub(r"\s+", " ", (brief or "").strip())
    if not trimmed:
        return (
            "Sounds like you want me to scaffold a new chip.\n\n"
            "Give me a one-line description and tap:\n"
            "/chip create <description>"
        )
    # Three short paragraphs, blank line between each.
    return (
        f"Got it - a chip for:\n{trimmed}\n\n"
        f"Tap this to scaffold it (takes 30-60s):\n"
        f"/chip create {trimmed}\n\n"
        f"I hand off to the slash command so you see the scaffolder's output "
        f"live and can cancel if the brief needs tweaking."
    )
