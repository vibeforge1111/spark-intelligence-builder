from __future__ import annotations

import re
from typing import Any


# Natural language for kicking off a chip loop. Anchored on loop vocabulary
# AND a chip reference so we do not hijack generic "run X" which is the
# mission intent.
_LOOP_PATTERNS = (
    # "loop startup-yc", "loop the ops critic", "loop domain-chip-xcontent 3"
    re.compile(r"\bloop\b\s+(?:the\s+|my\s+)?[\w\-]+", re.IGNORECASE),
    # "run <chip-key>", "run the <chip-key> chip"
    re.compile(r"\brun\b\s+(?:the\s+)?(?:domain-chip-[\w\-]+|[\w\-]+)\s+chip\b", re.IGNORECASE),
    # "run domain-chip-xyz" explicit full key
    re.compile(r"\brun\b\s+domain-chip-[\w\-]+", re.IGNORECASE),
    # "improve / iterate / refine / tune / evaluate <thing>" - extract_chip_key
    # will reject non-chip slugs so non-loop intents (e.g. "improve my writing")
    # don't leak through.
    re.compile(r"\b(?:improve|iterate(?:\s+on)?|refine|tune|evaluate)\b\s+(?:the\s+)?[\w\-]+", re.IGNORECASE),
    # "run the <chip> loop", "run <chip> N times"
    re.compile(r"\brun\b.{0,30}\bloop\b", re.IGNORECASE),
    re.compile(r"\brun\b\s+(?:the\s+)?[\w\-]+\s+(?:a\s+few\s+times|twice|three\s+times|\d+\s+times)", re.IGNORECASE),
)


def extract_chip_key(message: str) -> str | None:
    """Pull a chip key out of a natural-language message.

    Looks for 'domain-chip-<slug>' first, then bare slug words that are
    likely chip references (e.g. 'startup-yc', 'spark-browser').
    """
    text = str(message or "").strip()
    if not text:
        return None
    # Exact fully-qualified key
    m = re.search(r"\b(domain-chip-[\w\-]+)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # Known shorter chip keys (no domain-chip- prefix). Conservative list.
    known_bare = ("startup-yc", "spark-browser", "spark-swarm", "spark-personality-chip-labs")
    for key in known_bare:
        if re.search(rf"\b{re.escape(key)}\b", text, re.IGNORECASE):
            return key
    return None


def _extract_rounds(message: str) -> int | None:
    m = re.search(r"\b(\d+)\s+(?:times|rounds|iterations)\b", message, re.IGNORECASE)
    if m:
        return max(1, min(10, int(m.group(1))))
    for word, n in (("once", 1), ("twice", 2), ("three times", 3), ("a few times", 3)):
        if word in message.lower():
            return n
    return None


def detect_loop_invoke_intent(message: str) -> dict | None:
    text = str(message or "").strip()
    if not text:
        return None
    matched = False
    for pat in _LOOP_PATTERNS:
        if pat.search(text):
            matched = True
            break
    if not matched:
        return None
    chip_key = extract_chip_key(text)
    if not chip_key:
        return None
    rounds = _extract_rounds(text) or 1
    return {"action": "loop", "chip_key": chip_key, "rounds": rounds}
