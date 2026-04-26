from __future__ import annotations

import re
from dataclasses import dataclass


INVISIBLE_UNICODE_CHARS = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "BYTE ORDER MARK",
    "\u202a": "LEFT-TO-RIGHT EMBEDDING",
    "\u202b": "RIGHT-TO-LEFT EMBEDDING",
    "\u202c": "POP DIRECTIONAL FORMATTING",
    "\u202d": "LEFT-TO-RIGHT OVERRIDE",
    "\u202e": "RIGHT-TO-LEFT OVERRIDE",
}
PROMPT_BOUNDARY_PREFIX = r"(?:^|[:\-]\s*)"
STORED_PROMPT_INJECTION_PATTERNS = (
    (
        "instruction-override",
        re.compile(PROMPT_BOUNDARY_PREFIX + r"(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+instructions\b", re.I),
    ),
    (
        "system-prompt-override",
        re.compile(PROMPT_BOUNDARY_PREFIX + r"(system|developer)\s+(prompt|message|instruction)s?\b.*\b(override|replace|ignore)\b", re.I),
    ),
    ("hidden-html", re.compile(r"<!--|<\s*(?:div|span)[^>]*(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.I)),
    ("secret-exfiltration", re.compile(r"\b(curl|wget|fetch)\b.*\b(\.env|secret|token|api[_-]?key|password)\b", re.I)),
    ("secret-file-request", re.compile(r"\b(read|open|print|cat|get-content)\b.*(\.env|secrets\.local\.json|id_rsa|\.ssh|api[_-]?key)\b", re.I)),
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.I)),
)


@dataclass(frozen=True)
class PromptBoundaryFinding:
    category: str
    detail: str


def scan_invisible_unicode(text: str) -> list[PromptBoundaryFinding]:
    return [
        PromptBoundaryFinding("invisible-unicode", f"U+{ord(char):04X} {name}")
        for char, name in INVISIBLE_UNICODE_CHARS.items()
        if char in text
    ]


def scan_stored_prompt_injection(text: str) -> list[PromptBoundaryFinding]:
    findings: list[PromptBoundaryFinding] = []
    for category, pattern in STORED_PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            findings.append(PromptBoundaryFinding(category, "prompt text matched a stored-injection pattern"))
    return findings


def scan_prompt_boundary_text(text: str) -> list[PromptBoundaryFinding]:
    return [*scan_invisible_unicode(text), *scan_stored_prompt_injection(text)]


def sanitize_prompt_boundary_text(text: str | None) -> str:
    if not text:
        return ""
    sanitized = str(text)
    for char, name in INVISIBLE_UNICODE_CHARS.items():
        sanitized = sanitized.replace(char, _invisible_marker(char, name))
    output_lines: list[str] = []
    for line in sanitized.splitlines():
        matched_category = None
        for category, pattern in STORED_PROMPT_INJECTION_PATTERNS:
            if pattern.search(line):
                matched_category = category
                break
        if matched_category:
            output_lines.append(f"[blocked stored prompt-injection content: {matched_category}]")
            output_lines.extend(_line_invisible_markers(line))
        else:
            output_lines.append(line)
    return "\n".join(output_lines)


def _invisible_marker(char: str, name: str) -> str:
    return f"[blocked invisible unicode U+{ord(char):04X} {name}]"


def _line_invisible_markers(line: str) -> list[str]:
    markers: list[str] = []
    for char, name in INVISIBLE_UNICODE_CHARS.items():
        marker = _invisible_marker(char, name)
        if marker in line:
            markers.append(marker)
    return markers
