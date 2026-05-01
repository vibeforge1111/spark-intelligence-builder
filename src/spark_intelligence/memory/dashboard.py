from __future__ import annotations

import json
from collections import Counter
from typing import Any

from spark_intelligence.state.db import StateDB


MOVEMENT_BUCKETS: tuple[str, ...] = (
    "captured",
    "blocked",
    "promoted",
    "saved",
    "decayed",
    "summarized",
    "retrieved",
)

SUMMARY_EVENT_TYPES = {
    "memory_session_summary_written",
    "memory_daily_summary_written",
    "memory_project_summary_written",
}


def build_memory_dashboard_payload(
    *,
    state_db: StateDB,
    human_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Compile recent memory movement into human and agent dashboard views."""

    normalized_limit = max(1, min(int(limit or 50), 200))
    events = _load_recent_memory_events(state_db, limit=max(normalized_limit * 4, normalized_limit))
    lane_rows = _load_memory_lane_rows(state_db, limit=max(normalized_limit * 4, normalized_limit))
    lane_by_event_id = {str(row.get("event_id") or ""): row for row in lane_rows}

    scoped_events = [
        event
        for event in events
        if _matches_scope(event, human_id=human_id, agent_id=agent_id)
    ][:normalized_limit]
    rows = [
        _movement_row(event, lane_by_event_id.get(str(event.get("event_id") or "")))
        for event in scoped_events
    ]
    movement_counts = Counter(row["movement"] for row in rows)
    counts = {bucket: int(movement_counts.get(bucket, 0)) for bucket in MOVEMENT_BUCKETS}
    total_movement = sum(counts.values())
    return {
        "view": "memory_dashboard",
        "scope": {
            "human_id": human_id,
            "agent_id": agent_id,
            "limit": normalized_limit,
        },
        "counts": counts,
        "total_movement": total_movement,
        "human_view": _human_view_rows(rows),
        "agent_view": rows,
        "movement_paths": _movement_path_rows(rows),
        "source_mix": _source_mix(rows),
        "recent_blockers": [row for row in rows if row["movement"] == "blocked"][:10],
    }


def _load_recent_memory_events(state_db: StateDB, *, limit: int) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                event_id,
                event_type,
                component,
                run_id,
                request_id,
                trace_ref,
                channel_id,
                session_id,
                human_id,
                agent_id,
                actor_id,
                status,
                summary,
                reason_code,
                provenance_json,
                facts_json,
                created_at
            FROM builder_events
            WHERE event_type LIKE 'memory_%'
               OR event_type = 'policy_gate_blocked'
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_memory_lane_rows(state_db: StateDB, *, limit: int) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM memory_lane_records
            ORDER BY recorded_at DESC, lane_record_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


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


def _matches_scope(event: dict[str, Any], *, human_id: str | None, agent_id: str | None) -> bool:
    facts = _json_object(event.get("facts_json"))
    human_matched = False
    if human_id:
        candidates = {str(human_id)}
        if not str(human_id).startswith("human:"):
            candidates.add(f"human:{human_id}")
        event_human = str(event.get("human_id") or "")
        fact_subject = str(facts.get("subject") or "")
        if event_human not in candidates and fact_subject not in candidates:
            return False
        human_matched = True
    if agent_id:
        event_agent = str(event.get("agent_id") or "")
        if event_agent != str(agent_id) and not (human_matched and not event_agent):
            return False
    return True


def _movement_row(event: dict[str, Any], lane: dict[str, Any] | None) -> dict[str, Any]:
    facts = _json_object(event.get("facts_json"))
    provenance = _json_object(event.get("provenance_json"))
    lane_evidence = _json_object((lane or {}).get("evidence_json"))
    movement = _movement_bucket(event_type=str(event.get("event_type") or ""), facts=facts, lane=lane)
    return {
        "movement": movement,
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "created_at": event.get("created_at"),
        "summary": event.get("summary"),
        "component": event.get("component"),
        "status": event.get("status"),
        "reason": facts.get("reason") or event.get("reason_code"),
        "human_id": event.get("human_id"),
        "agent_id": event.get("agent_id"),
        "session_id": event.get("session_id"),
        "request_id": event.get("request_id"),
        "trace_ref": event.get("trace_ref"),
        "actor_id": event.get("actor_id"),
        "memory_role": facts.get("memory_role") or provenance.get("memory_role") or (lane or {}).get("artifact_lane"),
        "predicate": _first_predicate(facts),
        "subject": facts.get("subject"),
        "source": {
            "kind": provenance.get("source_kind") or (lane or {}).get("source_kind") or event.get("component"),
            "ref": provenance.get("source_ref") or (lane or {}).get("source_ref") or event.get("request_id"),
            "lane": (lane or {}).get("artifact_lane"),
        },
        "promotion": {
            "keepability": facts.get("keepability") or (lane or {}).get("keepability"),
            "disposition": facts.get("promotion_disposition") or (lane or {}).get("promotion_disposition"),
            "target_lane": (lane or {}).get("promotion_target_lane"),
            "lane_status": (lane or {}).get("status"),
        },
        "lifecycle": {
            "action": facts.get("lifecycle_action"),
            "transition_kind": facts.get("transition_kind"),
            "destination": facts.get("destination"),
            "transition_count": facts.get("transition_count"),
        },
        "lineage": {
            "source_event_ids": _string_list(facts.get("source_event_ids")),
            "source_session_ids": _string_list(facts.get("source_session_ids")),
            "source_event_count": facts.get("source_event_count") or facts.get("transition_count"),
            "source_session_count": facts.get("source_session_count"),
            "source_predicate": facts.get("source_predicate"),
            "destination": facts.get("destination"),
        },
        "retrieval": _retrieval_summary(facts),
        "trace": {
            "facts": _compact_trace_facts(facts),
            "provenance": provenance,
            "lane": lane_evidence,
        },
    }


def _movement_bucket(*, event_type: str, facts: dict[str, Any], lane: dict[str, Any] | None) -> str:
    lifecycle_action = str(facts.get("lifecycle_action") or "")
    transition_kind = str(facts.get("transition_kind") or "")
    promotion_disposition = str(facts.get("promotion_disposition") or (lane or {}).get("promotion_disposition") or "")
    lane_status = str((lane or {}).get("status") or "")
    if event_type == "policy_gate_blocked" or event_type in {"memory_write_abstained", "memory_write_failed"}:
        return "blocked"
    if lifecycle_action == "blocked_by_salience" or lane_status == "blocked":
        return "blocked"
    if event_type == "memory_write_requested" or lifecycle_action == "captured_by_salience" or lane_status == "captured":
        return "captured"
    if lifecycle_action == "promoted_by_salience" or promotion_disposition.startswith("promote_"):
        return "promoted"
    if event_type == "memory_write_succeeded":
        return "saved"
    if event_type in SUMMARY_EVENT_TYPES or transition_kind == "compaction" or lifecycle_action == "compacted":
        return "summarized"
    if transition_kind == "decay" or lifecycle_action in {"stale_preserved", "decayed"}:
        return "decayed"
    if event_type == "memory_read_succeeded":
        return "retrieved"
    if event_type == "memory_lifecycle_transition":
        return "saved"
    return "captured"


def _first_predicate(facts: dict[str, Any]) -> str | None:
    predicate = facts.get("predicate") or facts.get("source_predicate")
    if predicate:
        return str(predicate)
    predicates = facts.get("predicates")
    if isinstance(predicates, list) and predicates:
        return str(predicates[0])
    observations = facts.get("observations")
    if isinstance(observations, list) and observations:
        first = observations[0] if isinstance(observations[0], dict) else {}
        if first.get("predicate"):
            return str(first.get("predicate"))
    return None


def _compact_trace_facts(facts: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "operation",
        "method",
        "memory_role",
        "subject",
        "predicate",
        "predicate_count",
        "record_count",
        "accepted_count",
        "rejected_count",
        "skipped_count",
        "keepability",
        "promotion_disposition",
        "promotion_stage",
        "salience_score",
        "confidence",
        "why_saved",
        "lifecycle_action",
        "transition_kind",
        "destination",
        "transition_count",
        "source_event_count",
        "source_session_count",
        "reason",
    )
    return {key: facts.get(key) for key in keys if key in facts}


def _string_list(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value[:limit] if str(item or "").strip()]


def _retrieval_summary(facts: dict[str, Any]) -> dict[str, Any] | None:
    if str(facts.get("method") or "") != "hybrid_memory_retrieve" and "record_count" not in facts:
        return None
    answer = facts.get("answer_explanation") if isinstance(facts.get("answer_explanation"), dict) else {}
    retrieval_trace = facts.get("retrieval_trace") if isinstance(facts.get("retrieval_trace"), dict) else {}
    hybrid_trace = retrieval_trace.get("hybrid_memory_retrieve") if isinstance(retrieval_trace.get("hybrid_memory_retrieve"), dict) else {}
    source_mix = answer.get("context_packet_source_mix")
    sections = answer.get("context_packet_sections")
    return {
        "method": facts.get("method"),
        "record_count": facts.get("record_count"),
        "selected_count": answer.get("selected_count") or hybrid_trace.get("selected_count"),
        "source_mix": source_mix if isinstance(source_mix, dict) else {},
        "sections": sections if isinstance(sections, list) else [],
    }


def _human_view_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "when": row.get("created_at"),
            "movement": row.get("movement"),
            "line": _human_line(row),
            "source": row.get("source"),
            "trace_ref": row.get("trace_ref"),
        }
        for row in rows
    ]


def _movement_path_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = []
    for row in rows:
        path = _movement_path_for_row(row)
        if path:
            paths.append(path)
    return paths[:25]


def _movement_path_for_row(row: dict[str, Any]) -> dict[str, Any] | None:
    movement = str(row.get("movement") or "")
    lineage = row.get("lineage") if isinstance(row.get("lineage"), dict) else {}
    retrieval = row.get("retrieval") if isinstance(row.get("retrieval"), dict) else None
    source_count = lineage.get("source_event_count") or len(lineage.get("source_event_ids") or [])
    destination = lineage.get("destination") or (row.get("lifecycle") or {}).get("destination")
    if movement == "summarized":
        return {
            "from": "captured",
            "to": "summarized",
            "event_id": row.get("event_id"),
            "when": row.get("created_at"),
            "source_event_count": _int_or_zero(source_count),
            "source_event_ids": lineage.get("source_event_ids") or [],
            "destination": destination,
            "line": _path_line("captured", "summarized", source_count=source_count, destination=destination),
        }
    if movement == "decayed":
        return {
            "from": "saved",
            "to": "decayed",
            "event_id": row.get("event_id"),
            "when": row.get("created_at"),
            "source_event_count": _int_or_zero(source_count),
            "source_event_ids": lineage.get("source_event_ids") or [],
            "destination": destination,
            "line": _path_line("saved", "decayed", source_count=source_count, destination=destination),
        }
    if movement == "retrieved" and retrieval:
        source_mix = retrieval.get("source_mix") if isinstance(retrieval.get("source_mix"), dict) else {}
        source_label = ", ".join(f"{key}={value}" for key, value in sorted(source_mix.items())) or "memory"
        return {
            "from": "saved",
            "to": "retrieved",
            "event_id": row.get("event_id"),
            "when": row.get("created_at"),
            "record_count": retrieval.get("record_count"),
            "selected_count": retrieval.get("selected_count"),
            "source_mix": source_mix,
            "line": f"saved -> retrieved: {retrieval.get('record_count') or 0} record(s) from {source_label}.",
        }
    if movement == "promoted":
        return {
            "from": "captured",
            "to": "promoted",
            "event_id": row.get("event_id"),
            "when": row.get("created_at"),
            "destination": destination,
            "line": _path_line("captured", "promoted", source_count=source_count, destination=destination),
        }
    if movement == "blocked":
        return {
            "from": "captured",
            "to": "blocked",
            "event_id": row.get("event_id"),
            "when": row.get("created_at"),
            "destination": destination,
            "line": f"captured -> blocked: {row.get('predicate') or 'memory'} reason={row.get('reason') or 'unknown'}.",
        }
    return None


def _path_line(
    origin: str,
    destination_state: str,
    *,
    source_count: Any,
    destination: Any,
) -> str:
    count = _int_or_zero(source_count)
    target = str(destination or destination_state)
    if count:
        return f"{origin} -> {destination_state}: {count} source event(s) -> {target}."
    return f"{origin} -> {destination_state}: destination={target}."


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _human_line(row: dict[str, Any]) -> str:
    predicate = row.get("predicate") or "memory"
    reason = row.get("reason") or (row.get("promotion") or {}).get("disposition") or row.get("status") or "recorded"
    movement = str(row.get("movement") or "captured").replace("_", " ")
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    source_ref = source.get("ref") or row.get("request_id") or row.get("event_id")
    return f"{movement.title()}: {predicate} ({reason}; source {source_ref})."


def _source_mix(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        counts[str(source.get("kind") or "unknown")] += 1
    return dict(sorted(counts.items()))
