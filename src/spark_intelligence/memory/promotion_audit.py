from __future__ import annotations

import json
from collections import Counter
from typing import Any

from spark_intelligence.state.db import StateDB


def build_promotion_audit_payload(
    *,
    state_db: StateDB,
    human_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Review structured-evidence promotion policy decisions from Builder state."""

    normalized_limit = max(1, min(int(limit or 100), 500))
    rows = _load_promotion_events(state_db=state_db, human_id=human_id, agent_id=agent_id, limit=normalized_limit)
    chronological = sorted(rows, key=lambda row: (str(row.get("created_at") or ""), str(row.get("event_id") or "")))
    later_promotions = _later_promotions_by_key(chronological)
    decisions = [
        _audit_decision(row=row, later_promotions=later_promotions)
        for row in sorted(rows, key=lambda row: (str(row.get("created_at") or ""), str(row.get("event_id") or "")), reverse=True)
    ]
    counts = Counter(decision["audit_label"] for decision in decisions)
    disposition_counts = Counter(decision["promotion_disposition"] for decision in decisions)
    return {
        "view": "memory_promotion_audit",
        "scope": {"human_id": human_id, "agent_id": agent_id, "limit": normalized_limit},
        "decision_count": len(decisions),
        "counts": {
            "promoted": int(disposition_counts.get("promote_current_state", 0)),
            "blocked": int(disposition_counts.get("blocked", 0)),
            "held_as_evidence": int(counts.get("held_as_structured_evidence", 0)),
            "resolved_by_later_promotion": int(counts.get("resolved_by_later_promotion", 0)),
            "false_positive_risk": int(counts.get("false_positive_risk", 0)),
            "false_negative_risk": int(counts.get("false_negative_risk", 0)),
            "trace_gap": int(counts.get("trace_gap", 0)),
        },
        "decisions": decisions,
    }


def _load_promotion_events(
    *,
    state_db: StateDB,
    human_id: str | None,
    agent_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    conditions = ["event_type = ?"]
    params: list[Any] = ["memory_promotion_evaluated"]
    if human_id:
        conditions.append("human_id = ?")
        params.append(str(human_id))
    if agent_id:
        conditions.append("agent_id = ?")
        params.append(str(agent_id))
    params.append(int(limit))
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, session_id, human_id, agent_id,
                   actor_id, status, summary, reason_code, facts_json, provenance_json, created_at
            FROM builder_events
            WHERE {" AND ".join(f"({condition})" for condition in conditions)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _later_promotions_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], str]:
    latest: dict[tuple[str, str, str], str] = {}
    for row in rows:
        facts = _json_object(row.get("facts_json"))
        if str(facts.get("promotion_disposition") or "") != "promote_current_state":
            continue
        key = _decision_key(row=row, facts=facts)
        if key[1] and key[2]:
            latest[key] = str(row.get("created_at") or "")
    return latest


def _audit_decision(
    *,
    row: dict[str, Any],
    later_promotions: dict[tuple[str, str, str], str],
) -> dict[str, Any]:
    facts = _json_object(row.get("facts_json"))
    provenance = _json_object(row.get("provenance_json"))
    disposition = str(facts.get("promotion_disposition") or "").strip()
    reason = str(facts.get("promotion_reason_code") or row.get("reason_code") or "").strip()
    target_predicate = str(facts.get("target_predicate") or "").strip()
    target_value = str(facts.get("target_value") or "").strip()
    corroborating_count = _int_or_zero(facts.get("corroborating_evidence_count"))
    required_count = _int_or_zero(facts.get("required_corroborating_evidence_count"))
    direct_allowed = bool(facts.get("direct_promotion_without_corroboration"))
    label = _audit_label(
        row=row,
        facts=facts,
        disposition=disposition,
        target_predicate=target_predicate,
        target_value=target_value,
        corroborating_count=corroborating_count,
        required_count=required_count,
        direct_allowed=direct_allowed,
        later_promotions=later_promotions,
    )
    return {
        "event_id": row.get("event_id"),
        "created_at": row.get("created_at"),
        "session_id": row.get("session_id"),
        "human_id": row.get("human_id"),
        "agent_id": row.get("agent_id"),
        "promotion_policy": facts.get("promotion_policy"),
        "promotion_disposition": disposition,
        "audit_label": label,
        "reason": reason,
        "target_predicate": target_predicate,
        "target_value": target_value,
        "corroborating_evidence_count": corroborating_count,
        "required_corroborating_evidence_count": required_count,
        "direct_promotion_without_corroboration": direct_allowed,
        "source_observation_ids": _string_list(facts.get("source_observation_ids")),
        "source": {
            "kind": provenance.get("source_kind") or "structured_evidence",
            "ref": provenance.get("source_ref") or row.get("request_id") or row.get("event_id"),
        },
    }


def _audit_label(
    *,
    row: dict[str, Any],
    facts: dict[str, Any],
    disposition: str,
    target_predicate: str,
    target_value: str,
    corroborating_count: int,
    required_count: int,
    direct_allowed: bool,
    later_promotions: dict[tuple[str, str, str], str],
) -> str:
    if not target_predicate or not target_value or not facts.get("promotion_policy"):
        return "trace_gap"
    if disposition == "promote_current_state":
        if required_count > corroborating_count and not direct_allowed:
            return "false_positive_risk"
        return "policy_supported_promotion"
    if disposition == "blocked":
        if direct_allowed or corroborating_count >= max(required_count, 1):
            return "false_negative_risk"
        key = _decision_key(row=row, facts=facts)
        later_at = later_promotions.get(key)
        if later_at and str(later_at) >= str(row.get("created_at") or ""):
            return "resolved_by_later_promotion"
        return "held_as_structured_evidence"
    return "trace_gap"


def _decision_key(*, row: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("human_id") or ""),
        str(facts.get("target_predicate") or ""),
        str(facts.get("target_value") or "").casefold(),
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _string_list(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit] if str(item or "").strip()]


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
