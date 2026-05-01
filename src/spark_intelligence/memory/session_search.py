from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from spark_intelligence.state.db import StateDB


TEXT_FACT_KEYS: tuple[str, ...] = (
    "text",
    "message_text",
    "user_text",
    "assistant_text",
    "delivered_text",
    "response_text",
    "summary",
)

SEARCHABLE_EVENT_TYPES: tuple[str, ...] = (
    "memory_turn_captured",
    "memory_session_summary_written",
    "memory_daily_summary_written",
    "memory_project_summary_written",
)


def build_session_search_payload(
    *,
    state_db: StateDB,
    query: str,
    human_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 5,
    event_scan_limit: int = 1000,
) -> dict[str, Any]:
    """Search cold episodic conversation evidence stored in Builder state."""

    normalized_query = " ".join(str(query or "").split())
    normalized_limit = max(1, min(int(limit or 5), 25))
    tokens = _query_tokens(normalized_query)
    if not tokens:
        return {
            "view": "memory_session_search",
            "query": normalized_query,
            "scope": {"human_id": human_id, "agent_id": agent_id, "limit": normalized_limit},
            "status": "abstained",
            "reason": "query_has_no_search_terms",
            "sessions": [],
            "matched_event_count": 0,
            "scanned_event_count": 0,
        }

    rows = _load_search_rows(
        state_db,
        human_id=human_id,
        agent_id=agent_id,
        limit=max(normalized_limit * 25, min(max(int(event_scan_limit or 1000), 50), 5000)),
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    for row in rows:
        scanned += 1
        facts = _json_object(row.get("facts_json"))
        text = _event_text(row, facts)
        if not text:
            continue
        score, matched_terms = _score_text(text, normalized_query=normalized_query, tokens=tokens)
        if score <= 0:
            continue
        session_id = str(row.get("session_id") or "session:unknown")
        grouped[session_id].append(
            {
                "event_id": row.get("event_id"),
                "event_type": row.get("event_type"),
                "created_at": row.get("created_at"),
                "role": _event_role(row, facts),
                "text": text,
                "snippet": _snippet(text, matched_terms=matched_terms),
                "matched_terms": matched_terms,
                "score": score,
                "source": {
                    "kind": facts.get("source_surface") or row.get("component") or "builder_events",
                    "ref": facts.get("source_event") or row.get("request_id") or row.get("event_id"),
                    "authority": "episodic_evidence",
                },
            }
        )

    sessions = []
    for session_id, events in grouped.items():
        events.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")), reverse=True)
        score = sum(float(event.get("score") or 0) for event in events)
        sessions.append(
            {
                "session_id": session_id,
                "score": round(score, 3),
                "matched_event_count": len(events),
                "latest_at": events[0].get("created_at"),
                "events": events[:5],
                "recap": _recap_line(events[:3]),
            }
        )
    sessions.sort(key=lambda item: (float(item.get("score") or 0), str(item.get("latest_at") or "")), reverse=True)
    sessions = sessions[:normalized_limit]
    return {
        "view": "memory_session_search",
        "query": normalized_query,
        "scope": {"human_id": human_id, "agent_id": agent_id, "limit": normalized_limit},
        "status": "supported" if sessions else "not_found",
        "reason": None if sessions else "no_matching_episodic_sessions",
        "sessions": sessions,
        "matched_event_count": sum(int(session.get("matched_event_count") or 0) for session in sessions),
        "scanned_event_count": scanned,
        "source_mix": _source_mix(sessions),
    }


def _load_search_rows(
    state_db: StateDB,
    *,
    human_id: str | None,
    agent_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in SEARCHABLE_EVENT_TYPES)
    conditions = [f"event_type IN ({placeholders})"]
    params: list[Any] = list(SEARCHABLE_EVENT_TYPES)
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
            SELECT
                event_id,
                event_type,
                component,
                request_id,
                session_id,
                human_id,
                agent_id,
                summary,
                facts_json,
                created_at
            FROM builder_events
            WHERE {" AND ".join(f"({condition})" for condition in conditions)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


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


def _event_text(row: dict[str, Any], facts: dict[str, Any]) -> str:
    for key in TEXT_FACT_KEYS:
        value = str(facts.get(key) or "").strip()
        if value:
            return value
    observations = facts.get("observations")
    if isinstance(observations, list):
        parts = []
        for item in observations[:8]:
            if not isinstance(item, dict):
                continue
            value = item.get("value") or item.get("summary") or item.get("predicate")
            if value:
                parts.append(str(value))
        if parts:
            return " ".join(parts)
    return str(row.get("summary") or "").strip()


def _event_role(row: dict[str, Any], facts: dict[str, Any]) -> str:
    role = str(facts.get("role") or facts.get("conversation_role") or "").strip().lower()
    if role in {"user", "assistant", "system"}:
        return role
    event_type = str(row.get("event_type") or "").casefold()
    if "assistant" in event_type or "reply" in event_type:
        return "assistant"
    return "memory"


def _query_tokens(query: str) -> set[str]:
    stop_words = {
        "about",
        "after",
        "again",
        "that",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "your",
        "you",
        "did",
        "was",
        "were",
        "the",
        "and",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(query or "").casefold())
        if token not in stop_words
    }


def _score_text(text: str, *, normalized_query: str, tokens: set[str]) -> tuple[float, list[str]]:
    haystack = str(text or "").casefold()
    matched = sorted(token for token in tokens if token in haystack)
    if not matched:
        return 0.0, []
    score = float(len(matched) * 10)
    if normalized_query and normalized_query.casefold() in haystack:
        score += 25.0
    score += min(len(matched) / max(len(tokens), 1), 1.0) * 5
    return score, matched


def _snippet(text: str, *, matched_terms: list[str], width: int = 220) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= width:
        return compact
    lowered = compact.casefold()
    first_index = min(
        (lowered.find(term) for term in matched_terms if lowered.find(term) >= 0),
        default=0,
    )
    start = max(0, first_index - 60)
    end = min(len(compact), start + width)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


def _recap_line(events: list[dict[str, Any]]) -> str:
    parts = []
    for event in events:
        role = str(event.get("role") or "memory")
        snippet = str(event.get("snippet") or "")
        if snippet:
            parts.append(f"{role}: {snippet}")
    return " | ".join(parts)


def _source_mix(sessions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for session in sessions:
        for event in session.get("events") or []:
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            key = str(source.get("kind") or "unknown")
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
