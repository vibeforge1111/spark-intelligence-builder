from __future__ import annotations

import re
from typing import Any

from spark_intelligence.observability.store import record_event, record_quarantine
from spark_intelligence.state.db import StateDB


def looks_secret_like(text: str) -> bool:
    patterns = [
        r"(?i)bearer\s+[A-Za-z0-9._-]{20,}",
        r"(?m)^[A-Z0-9_]{3,}=(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-|live-|test-|ant-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|[0-9]{7,10}:[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})$",
        r"ghp_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"sk-(?:proj-|live-|test-|ant-)?[A-Za-z0-9_-]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{20,}",
        r"\b[0-9]{7,10}:[A-Za-z0-9_-]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)(?:secret|token|api[_ -]?key|access[_ -]?key|refresh[_ -]?token|password)\s*[:=]\s*['\"]?[A-Za-z0-9._:/+=-]{16,}",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


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
    return {"allowed": False, "text": "", "quarantine_id": quarantine_id}
