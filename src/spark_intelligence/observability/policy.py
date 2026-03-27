from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlsplit

from spark_intelligence.observability.store import record_event, record_policy_gate_block, record_quarantine
from spark_intelligence.state.db import StateDB


_DIRECT_SECRET_PATTERNS = [
    r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}",
    r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._-]{20,}",
    r"(?i)\bx-api-key\s*:\s*[A-Za-z0-9._-]{16,}",
    r"(?i)\bcookie\s*:\s*[^=\s]{2,}\s*=\s*[^;\s]{16,}",
    r"(?m)^[A-Z0-9_]{3,}=(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-|live-|test-|ant-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|[0-9]{7,10}:[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})$",
    r"ghp_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"sk-(?:proj-|live-|test-|ant-)?[A-Za-z0-9_-]{20,}",
    r"xox[baprs]-[A-Za-z0-9-]{20,}",
    r"\b[0-9]{7,10}:[A-Za-z0-9_-]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]

_SECRET_KEY_PARTS = {
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "id_token",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "session",
    "session_id",
    "session_token",
    "token",
}

_ASSIGNMENT_PATTERN = re.compile(
    r"""(?im)(?:^|[{\s,])(?P<key>["']?[A-Za-z_][A-Za-z0-9_.:-]{1,64}["']?)\s*(?P<sep>[:=])\s*(?P<value>[^,\n]+)"""
)


def looks_secret_like(text: str) -> bool:
    if not text:
        return False
    if any(re.search(pattern, text) for pattern in _DIRECT_SECRET_PATTERNS):
        return True
    if _contains_secret_assignment(text):
        return True
    if _contains_embedded_url_credentials(text):
        return True
    return False


def _contains_secret_assignment(text: str) -> bool:
    for match in _ASSIGNMENT_PATTERN.finditer(text):
        key = str(match.group("key") or "")
        value = _normalize_assignment_value(str(match.group("value") or ""))
        if not value or not _looks_secret_key(key):
            continue
        if _is_structural_secret_value(value):
            return True
    return False


def _contains_embedded_url_credentials(text: str) -> bool:
    for candidate in re.findall(r"https?://[^\s'\"<>]+", text):
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            continue
        if parsed.username and parsed.password and _is_structural_secret_value(parsed.password):
            return True
    return False


def _looks_secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.strip("'\"").lower()).strip("_")
    if not normalized:
        return False
    parts = {part for part in normalized.split("_") if part}
    if normalized in _SECRET_KEY_PARTS:
        return True
    if "api" in parts and "key" in parts:
        return True
    if "client" in parts and "secret" in parts:
        return True
    if "private" in parts and "key" in parts:
        return True
    if "refresh" in parts and "token" in parts:
        return True
    if "access" in parts and ("token" in parts or "key" in parts):
        return True
    if "session" in parts and ("token" in parts or "id" in parts):
        return True
    return False


def _normalize_assignment_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    normalized = normalized.rstrip(",}]").strip()
    normalized = normalized.lstrip("{[").strip()
    if normalized.startswith(("'", '"')) and normalized.endswith(("'", '"')) and len(normalized) >= 2:
        normalized = normalized[1:-1].strip()
    return normalized


def _is_structural_secret_value(value: str) -> bool:
    if len(value) < 12:
        return False
    if any(re.search(pattern, value) for pattern in _DIRECT_SECRET_PATTERNS):
        return True
    if value.lower().startswith("basic "):
        token = value[6:].strip()
        return _looks_encoded_credential_blob(token)
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        return _looks_token_like(token)
    if ";" in value and "=" in value:
        segments = [segment.strip() for segment in value.split(";") if segment.strip()]
        return any(_looks_cookie_pair(segment) for segment in segments)
    if _looks_cookie_pair(value):
        return True
    if _looks_query_token_value(value):
        return True
    if _looks_token_like(value):
        return True
    return False


def _looks_cookie_pair(value: str) -> bool:
    if "=" not in value:
        return False
    name, cookie_value = value.split("=", 1)
    if not _looks_secret_key(name):
        return False
    return _looks_token_like(cookie_value.strip())


def _looks_query_token_value(value: str) -> bool:
    lowered = value.lower()
    if "token=" not in lowered and "key=" not in lowered and "secret=" not in lowered:
        return False
    parts = re.split(r"[?&]", value)
    for part in parts:
        if "=" not in part:
            continue
        key, token_value = part.split("=", 1)
        if _looks_secret_key(key) and _looks_token_like(token_value.strip()):
            return True
    return False


def _looks_token_like(value: str) -> bool:
    normalized = value.strip().strip("'\"")
    if len(normalized) < 16 or " " in normalized:
        return False
    if re.fullmatch(r"[A-Za-z0-9._:/+=-]{16,}", normalized) is None:
        return False
    if normalized.count(".") == 2 and normalized.startswith("eyJ"):
        return True
    if ":" in normalized and re.fullmatch(r"[0-9]{7,10}:[A-Za-z0-9_-]{20,}", normalized):
        return True
    if normalized.startswith(("ghp_", "github_pat_", "sk-", "xox", "AKIA")):
        return True
    if _looks_encoded_credential_blob(normalized):
        return True
    return _shannon_entropy(normalized) >= 3.6 and _character_class_count(normalized) >= 2


def _looks_encoded_credential_blob(value: str) -> bool:
    normalized = value.strip().strip("'\"")
    if len(normalized) < 20:
        return False
    if re.fullmatch(r"[A-Za-z0-9+/=_-]{20,}", normalized) is None:
        return False
    return _shannon_entropy(normalized) >= 3.4


def _character_class_count(value: str) -> int:
    classes = 0
    if any(char.islower() for char in value):
        classes += 1
    if any(char.isupper() for char in value):
        classes += 1
    if any(char.isdigit() for char in value):
        classes += 1
    if any(not char.isalnum() for char in value):
        classes += 1
    return classes


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    length = float(len(value))
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def screen_model_visible_text(
    *,
    state_db: StateDB,
    source_kind: str,
    source_ref: str | None,
    text: str,
    summary: str,
    reason_code: str,
    policy_domain: str,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    blocked_stage: str = "pre_model",
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not looks_secret_like(text):
        return {"allowed": True, "text": text, "quarantine_id": None}
    event_id = record_event(
        state_db,
        event_type="secret_boundary_violation",
        component=policy_domain,
        summary=summary,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        reason_code=reason_code,
        severity="high",
        facts={"source_kind": source_kind, "source_ref": source_ref, "blocked_stage": blocked_stage},
        provenance=provenance,
    )
    quarantine_id = record_quarantine(
        state_db,
        event_id=event_id,
        run_id=run_id,
        request_id=request_id,
        source_kind=source_kind,
        source_ref=source_ref,
        policy_domain=policy_domain,
        reason_code=reason_code,
        summary=summary,
        payload_preview=text[:160],
        provenance=provenance,
    )
    record_policy_gate_block(
        state_db,
        component=policy_domain,
        policy_domain=policy_domain,
        gate_name="secret_boundary",
        source_kind=source_kind,
        source_ref=source_ref,
        summary=summary,
        action="quarantine_blocked",
        reason_code=reason_code,
        blocked_stage=blocked_stage,
        input_ref=str(source_ref or request_id or trace_ref or ""),
        output_ref=quarantine_id,
        severity="high",
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        provenance=provenance,
        facts={"secret_event_id": event_id},
    )
    return {"allowed": False, "text": "", "quarantine_id": quarantine_id}
