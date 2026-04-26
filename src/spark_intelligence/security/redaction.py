from __future__ import annotations

import re


SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"), "<redacted private key>"),
    (re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_-]{20,}\b"), "<redacted telegram bot token>"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"), "<redacted api key>"),
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"), "<redacted github token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "<redacted github token>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "<redacted slack token>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "<redacted google api key>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted aws access key>"),
    (re.compile(r"\b(?:sk_live|sk_test)_[A-Za-z0-9]{20,}\b"), "<redacted stripe key>"),
    (re.compile(r"\bgAAAA[A-Za-z0-9_-]{20,}\b"), "<redacted encrypted secret>"),
    (re.compile(r"\bbb_live_[A-Za-z0-9]{20,}\b"), "<redacted secret>"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "<redacted huggingface token>"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"), "<redacted npm token>"),
    (re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"), "<redacted pypi token>"),
    (re.compile(r"\bdop_v1_[A-Za-z0-9_-]{20,}\b"), "<redacted doppler token>"),
    (re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis)://[^\s'\"<>]+", re.I), "<redacted connection string>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.I), "Bearer <redacted>"),
    (
        re.compile(
            r"(?P<key>\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|BOT[_-]?TOKEN)[A-Z0-9_]*\s*=\s*)(?P<value>[^\s#'\";]{8,})",
            re.I,
        ),
        "assignment",
    ),
    (
        re.compile(
            r"(?P<prefix>[\"'](?:api[_-]?key|token|secret|password|bot[_-]?token|authorization)[\"']\s*:\s*[\"'])(?P<value>[^\"']{8,})(?P<suffix>[\"'])",
            re.I,
        ),
        "json_field",
    ),
    (re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"), "<redacted phone>"),
)


def mask_secret(value: str) -> str:
    if len(value) < 18:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def redact_text(text: str | None) -> str:
    if text is None:
        return ""
    redacted = str(text)
    for pattern, replacement in SECRET_PATTERNS:
        if replacement == "assignment":
            redacted = pattern.sub(lambda match: f"{match.group('key')}{mask_secret(match.group('value'))}", redacted)
        elif replacement == "json_field":
            redacted = pattern.sub(
                lambda match: f"{match.group('prefix')}{mask_secret(match.group('value'))}{match.group('suffix')}",
                redacted,
            )
        else:
            redacted = pattern.sub(replacement, redacted)
    return redacted
