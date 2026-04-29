from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.orchestrator import MemoryWriteResult, write_structured_evidence_to_memory
from spark_intelligence.memory.salience import evaluate_generic_memory_salience
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


_PROMISE_PREDICATE_PARTS = {"commitment", "next_action", "plan", "milestone"}
_DECISION_PREDICATE_PARTS = {"decision"}
_OPEN_QUESTION_MARKERS = ("?", "open question", "blocked", "blocker", "risk")
_PATH_RE = re.compile(r"\b(?:[A-Za-z]:[\\/][^\s,;]+|(?:src|tests|docs|tasks)\S+)\b")


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    human_id: str | None
    agent_id: str | None
    event_count: int
    started_at: str | None
    ended_at: str | None
    what_changed: tuple[str, ...]
    decisions: tuple[str, ...]
    open_questions: tuple[str, ...]
    repos_touched: tuple[str, ...]
    artifacts_created: tuple[str, ...]
    promises_made: tuple[str, ...]
    next_actions: tuple[str, ...]
    source_event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "human_id": self.human_id,
            "agent_id": self.agent_id,
            "event_count": self.event_count,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "what_changed": list(self.what_changed),
            "decisions": list(self.decisions),
            "open_questions": list(self.open_questions),
            "repos_touched": list(self.repos_touched),
            "artifacts_created": list(self.artifacts_created),
            "promises_made": list(self.promises_made),
            "next_actions": list(self.next_actions),
            "source_event_ids": list(self.source_event_ids),
        }

    def to_text(self) -> str:
        lines = [
            f"Session summary for {self.session_id}",
            f"Events reviewed: {self.event_count}",
        ]
        if self.started_at or self.ended_at:
            lines.append(f"Window: {self.started_at or 'unknown'} -> {self.ended_at or 'unknown'}")
        sections = (
            ("What changed", self.what_changed),
            ("Decisions", self.decisions),
            ("Open questions", self.open_questions),
            ("Repos touched", self.repos_touched),
            ("Artifacts created", self.artifacts_created),
            ("Promises made", self.promises_made),
            ("Next actions", self.next_actions),
        )
        for title, values in sections:
            lines.append("")
            lines.append(f"{title}:")
            if values:
                lines.extend(f"- {value}" for value in values)
            else:
                lines.append("- none observed")
        if self.source_event_ids:
            lines.append("")
            lines.append("Source event ids:")
            lines.extend(f"- {event_id}" for event_id in self.source_event_ids[:20])
        return "\n".join(lines)


@dataclass(frozen=True)
class EpisodicRollupSummary:
    scope: str
    scope_key: str
    human_id: str | None
    agent_id: str | None
    event_count: int
    session_count: int
    started_at: str | None
    ended_at: str | None
    what_changed: tuple[str, ...]
    decisions: tuple[str, ...]
    open_questions: tuple[str, ...]
    repos_touched: tuple[str, ...]
    artifacts_created: tuple[str, ...]
    promises_made: tuple[str, ...]
    next_actions: tuple[str, ...]
    source_session_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "scope_key": self.scope_key,
            "human_id": self.human_id,
            "agent_id": self.agent_id,
            "event_count": self.event_count,
            "session_count": self.session_count,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "what_changed": list(self.what_changed),
            "decisions": list(self.decisions),
            "open_questions": list(self.open_questions),
            "repos_touched": list(self.repos_touched),
            "artifacts_created": list(self.artifacts_created),
            "promises_made": list(self.promises_made),
            "next_actions": list(self.next_actions),
            "source_session_ids": list(self.source_session_ids),
            "source_event_ids": list(self.source_event_ids),
        }

    def to_text(self) -> str:
        title = "Daily" if self.scope == "daily" else "Project"
        lines = [
            f"{title} Spark memory summary for {self.scope_key}",
            f"Events reviewed: {self.event_count}",
            f"Sessions reviewed: {self.session_count}",
        ]
        if self.started_at or self.ended_at:
            lines.append(f"Window: {self.started_at or 'unknown'} -> {self.ended_at or 'unknown'}")
        sections = (
            ("What changed", self.what_changed),
            ("Decisions", self.decisions),
            ("Open questions", self.open_questions),
            ("Repos touched", self.repos_touched),
            ("Artifacts created", self.artifacts_created),
            ("Promises made", self.promises_made),
            ("Next actions", self.next_actions),
        )
        for title, values in sections:
            lines.append("")
            lines.append(f"{title}:")
            if values:
                lines.extend(f"- {value}" for value in values)
            else:
                lines.append("- none observed")
        if self.source_session_ids:
            lines.append("")
            lines.append("Source sessions:")
            lines.extend(f"- {session_id}" for session_id in self.source_session_ids[:20])
        if self.source_event_ids:
            lines.append("")
            lines.append("Source event ids:")
            lines.extend(f"- {event_id}" for event_id in self.source_event_ids[:20])
        return "\n".join(lines)


def build_session_summary(
    *,
    state_db: StateDB,
    session_id: str,
    limit: int = 200,
) -> SessionSummary:
    normalized_session = str(session_id or "").strip()
    if not normalized_session:
        raise ValueError("session_id is required")
    rows = _session_event_rows(state_db=state_db, session_id=normalized_session, limit=limit)
    facts_rows = [(_row_to_event(row), _json_dict(row.get("facts_json"))) for row in rows]
    fields = _summary_fields_from_event_rows(facts_rows=facts_rows)
    source_event_ids = _source_event_ids(rows)
    human_id = _first_nonempty(str(row.get("human_id") or "") for row in rows)
    agent_id = _first_nonempty(str(row.get("agent_id") or "") for row in rows)
    started_at = str(rows[0].get("created_at") or "") if rows else None
    ended_at = str(rows[-1].get("created_at") or "") if rows else None

    return SessionSummary(
        session_id=normalized_session,
        human_id=human_id,
        agent_id=agent_id,
        event_count=len(rows),
        started_at=started_at or None,
        ended_at=ended_at or None,
        what_changed=tuple(fields["what_changed"][:12]),
        decisions=tuple(fields["decisions"][:8]),
        open_questions=tuple(fields["open_questions"][:8]),
        repos_touched=tuple(fields["repos_touched"][:8]),
        artifacts_created=tuple(fields["artifacts_created"][:10]),
        promises_made=tuple(fields["promises_made"][:8]),
        next_actions=tuple(fields["next_actions"][:8]),
        source_event_ids=source_event_ids[:50],
    )


def write_session_summary_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str,
    agent_id: str | None = None,
    channel_kind: str | None = None,
    actor_id: str = "session_summary_writer",
    limit: int = 200,
) -> MemoryWriteResult:
    summary = build_session_summary(state_db=state_db, session_id=session_id, limit=limit)
    if summary.event_count <= 0:
        result = MemoryWriteResult(
            status="skipped",
            operation="create",
            method="write_observation",
            memory_role="structured_evidence",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_session_events",
        )
        record_event(
            state_db,
            event_type="memory_session_summary_skipped",
            component="memory_orchestrator",
            summary="Spark memory skipped session summary because the session had no ledger events.",
            request_id=f"{session_id}:session-summary",
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
            reason_code="no_session_events",
            facts=summary.to_dict(),
            provenance={"memory_role": "structured_evidence", "source_kind": "session_event_ledger"},
        )
        return result
    text = summary.to_text()
    salience_decision = evaluate_generic_memory_salience(
        outcome="structured_evidence",
        memory_role="structured_evidence",
        retention_class="episodic_archive",
        predicate="evidence.telegram.session_summary",
        value=text,
        evidence_text=text,
        reason="session_summary",
    )
    result = write_structured_evidence_to_memory(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        evidence_text=text,
        domain_pack="session_summary",
        evidence_kind="session_summary",
        session_id=session_id,
        turn_id=f"{session_id}:session-summary",
        channel_kind=channel_kind,
        actor_id=actor_id,
        salience_decision=salience_decision,
    )
    record_event(
        state_db,
        event_type="memory_session_summary_written",
        component="memory_orchestrator",
        summary="Spark memory wrote a durable session summary for episodic continuity.",
        request_id=f"{session_id}:session-summary",
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
        reason_code="session_summary_written",
        facts={
            **summary.to_dict(),
            "write_status": result.status,
            "accepted_count": result.accepted_count,
            "memory_role": "structured_evidence",
            "domain_pack": "session_summary",
            **salience_decision.metadata(),
        },
        provenance={"memory_role": "structured_evidence", "source_kind": "session_event_ledger"},
    )
    return result


def build_session_memory_summary(
    *,
    state_db: StateDB,
    session_id: str,
    limit: int = 200,
) -> SessionSummary:
    return build_session_summary(state_db=state_db, session_id=session_id, limit=limit)


def build_daily_summary(
    *,
    state_db: StateDB,
    day: str,
    human_id: str | None = None,
    limit: int = 500,
) -> EpisodicRollupSummary:
    normalized_day = str(day or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_day):
        raise ValueError("day must use YYYY-MM-DD")
    rows = _daily_event_rows(state_db=state_db, day=normalized_day, human_id=human_id, limit=limit)
    return _rollup_summary_from_rows(scope="daily", scope_key=normalized_day, rows=rows)


def build_project_summary(
    *,
    state_db: StateDB,
    project_key: str,
    human_id: str | None = None,
    limit: int = 500,
) -> EpisodicRollupSummary:
    normalized_project = _clean_value(project_key).casefold()
    if not normalized_project:
        raise ValueError("project_key is required")
    rows = _project_event_rows(state_db=state_db, project_key=normalized_project, human_id=human_id, limit=limit)
    return _rollup_summary_from_rows(scope="project", scope_key=normalized_project, rows=rows)


def write_daily_summary_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    day: str,
    channel_kind: str | None = None,
    actor_id: str = "daily_summary_writer",
    limit: int = 500,
) -> MemoryWriteResult:
    summary = build_daily_summary(state_db=state_db, day=day, human_id=human_id, limit=limit)
    return _write_rollup_summary_to_memory(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        summary=summary,
        channel_kind=channel_kind,
        actor_id=actor_id,
    )


def write_project_summary_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    project_key: str,
    channel_kind: str | None = None,
    actor_id: str = "project_summary_writer",
    limit: int = 500,
) -> MemoryWriteResult:
    summary = build_project_summary(state_db=state_db, project_key=project_key, human_id=human_id, limit=limit)
    return _write_rollup_summary_to_memory(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        summary=summary,
        channel_kind=channel_kind,
        actor_id=actor_id,
    )


def build_daily_project_summary(
    *,
    state_db: StateDB,
    scope: str,
    scope_key: str,
    human_id: str | None = None,
    limit: int = 500,
) -> EpisodicRollupSummary:
    normalized_scope = str(scope or "").strip().casefold()
    if normalized_scope == "daily":
        return build_daily_summary(state_db=state_db, day=scope_key, human_id=human_id, limit=limit)
    if normalized_scope == "project":
        return build_project_summary(state_db=state_db, project_key=scope_key, human_id=human_id, limit=limit)
    raise ValueError(f"unknown_summary_scope:{scope}")


def _session_event_rows(*, state_db: StateDB, session_id: str, limit: int) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT event_id, event_type, component, request_id, session_id, human_id, agent_id,
                   actor_id, summary, reason_code, facts_json, created_at
            FROM builder_events
            WHERE session_id = ?
            ORDER BY created_at ASC, event_id ASC
            LIMIT ?
            """,
            (session_id, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def _daily_event_rows(
    *,
    state_db: StateDB,
    day: str,
    human_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    params: list[Any] = [f"{day}%"]
    human_clause = ""
    if human_id:
        human_clause = "AND human_id = ?"
        params.append(str(human_id))
    params.append(int(limit))
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, session_id, human_id, agent_id,
                   actor_id, summary, reason_code, facts_json, created_at
            FROM builder_events
            WHERE created_at LIKE ?
              {human_clause}
            ORDER BY created_at ASC, event_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def _project_event_rows(
    *,
    state_db: StateDB,
    project_key: str,
    human_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    human_clause = ""
    if human_id:
        human_clause = "WHERE human_id = ?"
        params.append(str(human_id))
    params.append(max(int(limit) * 3, int(limit)))
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, session_id, human_id, agent_id,
                   actor_id, summary, reason_code, facts_json, created_at
            FROM builder_events
            {human_clause}
            ORDER BY created_at ASC, event_id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    matching = []
    for row in rows:
        row_dict = dict(row)
        facts = _json_dict(row_dict.get("facts_json"))
        if _row_matches_project(row=row_dict, facts=facts, project_key=project_key):
            matching.append(row_dict)
        if len(matching) >= int(limit):
            break
    return matching


def _row_to_event(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in row if key != "facts_json"}


def _json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iter_observations(facts: dict[str, Any]) -> list[dict[str, Any]]:
    observations = facts.get("observations")
    if isinstance(observations, list):
        return [item for item in observations if isinstance(item, dict)]
    return []


def _summary_fields_from_event_rows(
    *,
    facts_rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, list[str]]:
    what_changed: list[str] = []
    decisions: list[str] = []
    open_questions: list[str] = []
    repos_touched: list[str] = []
    artifacts_created: list[str] = []
    promises_made: list[str] = []
    next_actions: list[str] = []

    for event, facts in facts_rows:
        summary = str(event.get("summary") or "").strip()
        event_type = str(event.get("event_type") or "").strip()
        reason_code = str(event.get("reason_code") or "").strip()
        _collect_references(facts, repos_touched=repos_touched, artifacts_created=artifacts_created)
        for observation in _iter_observations(facts):
            predicate = str(observation.get("predicate") or facts.get("predicate") or "").strip()
            value = _clean_value(observation.get("value") or observation.get("text") or facts.get("value"))
            _collect_fact_fields(
                predicate=predicate,
                value=value,
                what_changed=what_changed,
                decisions=decisions,
                promises_made=promises_made,
                next_actions=next_actions,
            )

        _collect_fact_fields(
            predicate=str(facts.get("predicate") or "").strip(),
            value=_clean_value(facts.get("value")),
            what_changed=what_changed,
            decisions=decisions,
            promises_made=promises_made,
            next_actions=next_actions,
        )

        if _looks_open(summary) or _looks_open(reason_code) or _looks_open(str(facts.get("evidence_summary") or "")):
            _append_unique(open_questions, _compact_line(summary or reason_code or str(facts.get("evidence_summary") or "")))
        if event_type in {"dispatch_failed", "delivery_failed"}:
            _append_unique(open_questions, _compact_line(summary or reason_code or event_type))

    return {
        "what_changed": what_changed,
        "decisions": decisions,
        "open_questions": open_questions,
        "repos_touched": repos_touched,
        "artifacts_created": artifacts_created,
        "promises_made": promises_made,
        "next_actions": next_actions,
    }


def _collect_fact_fields(
    *,
    predicate: str,
    value: str,
    what_changed: list[str],
    decisions: list[str],
    promises_made: list[str],
    next_actions: list[str],
) -> None:
    if not predicate or not value:
        return
    item = _fact_line(predicate=predicate, value=value)
    if item:
        _append_unique(what_changed, item)
    if any(part in predicate for part in _DECISION_PREDICATE_PARTS):
        _append_unique(decisions, item)
    if any(part in predicate for part in _PROMISE_PREDICATE_PARTS):
        _append_unique(promises_made, item)
    if "next_action" in predicate:
        _append_unique(next_actions, item)


def _rollup_summary_from_rows(*, scope: str, scope_key: str, rows: list[dict[str, Any]]) -> EpisodicRollupSummary:
    facts_rows = [(_row_to_event(row), _json_dict(row.get("facts_json"))) for row in rows]
    fields = _summary_fields_from_event_rows(facts_rows=facts_rows)
    source_session_ids = tuple(
        dict.fromkeys(str(row.get("session_id") or "").strip() for row in rows if str(row.get("session_id") or "").strip())
    )
    return EpisodicRollupSummary(
        scope=scope,
        scope_key=scope_key,
        human_id=_first_nonempty(str(row.get("human_id") or "") for row in rows),
        agent_id=_first_nonempty(str(row.get("agent_id") or "") for row in rows),
        event_count=len(rows),
        session_count=len(source_session_ids),
        started_at=str(rows[0].get("created_at") or "") if rows else None,
        ended_at=str(rows[-1].get("created_at") or "") if rows else None,
        what_changed=tuple(fields["what_changed"][:20]),
        decisions=tuple(fields["decisions"][:12]),
        open_questions=tuple(fields["open_questions"][:12]),
        repos_touched=tuple(fields["repos_touched"][:12]),
        artifacts_created=tuple(fields["artifacts_created"][:16]),
        promises_made=tuple(fields["promises_made"][:12]),
        next_actions=tuple(fields["next_actions"][:12]),
        source_session_ids=source_session_ids[:30],
        source_event_ids=_source_event_ids(rows)[:80],
    )


def _write_rollup_summary_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    summary: EpisodicRollupSummary,
    channel_kind: str | None,
    actor_id: str,
) -> MemoryWriteResult:
    domain_pack = f"{summary.scope}_summary"
    if summary.event_count <= 0:
        result = MemoryWriteResult(
            status="skipped",
            operation="create",
            method="write_observation",
            memory_role="structured_evidence",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason=f"no_{summary.scope}_events",
        )
        record_event(
            state_db,
            event_type=f"memory_{summary.scope}_summary_skipped",
            component="memory_orchestrator",
            summary=f"Spark memory skipped {summary.scope} summary because no matching ledger events were found.",
            request_id=f"{summary.scope}:{summary.scope_key}:summary",
            human_id=human_id,
            actor_id=actor_id,
            reason_code=f"no_{summary.scope}_events",
            facts=summary.to_dict(),
            provenance={"memory_role": "structured_evidence", "source_kind": "session_event_ledger"},
        )
        return result

    text = summary.to_text()
    salience_decision = evaluate_generic_memory_salience(
        outcome="structured_evidence",
        memory_role="structured_evidence",
        retention_class="episodic_archive",
        predicate=f"evidence.telegram.{domain_pack}",
        value=text,
        evidence_text=text,
        reason=domain_pack,
    )
    result = write_structured_evidence_to_memory(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        evidence_text=text,
        domain_pack=domain_pack,
        evidence_kind=domain_pack,
        session_id=None,
        turn_id=f"{summary.scope}:{summary.scope_key}:summary",
        channel_kind=channel_kind,
        actor_id=actor_id,
        salience_decision=salience_decision,
    )
    record_event(
        state_db,
        event_type=f"memory_{summary.scope}_summary_written",
        component="memory_orchestrator",
        summary=f"Spark memory wrote a durable {summary.scope} summary for episodic continuity.",
        request_id=f"{summary.scope}:{summary.scope_key}:summary",
        human_id=human_id,
        actor_id=actor_id,
        reason_code=f"{summary.scope}_summary_written",
        facts={
            **summary.to_dict(),
            "write_status": result.status,
            "accepted_count": result.accepted_count,
            "memory_role": "structured_evidence",
            "domain_pack": domain_pack,
            **salience_decision.metadata(),
        },
        provenance={"memory_role": "structured_evidence", "source_kind": "session_event_ledger"},
    )
    return result


def _clean_value(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _source_event_ids(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(row.get("event_id") or "").strip() for row in rows if str(row.get("event_id") or "").strip())


def _row_matches_project(*, row: dict[str, Any], facts: dict[str, Any], project_key: str) -> bool:
    haystack_parts = [
        str(row.get("summary") or ""),
        str(row.get("component") or ""),
        str(row.get("request_id") or ""),
        str(row.get("session_id") or ""),
    ]
    project_keys = {
        "project",
        "project_key",
        "active_project",
        "entity_key",
        "repo",
        "repository",
        "repository_full_name",
        "repo_full_name",
        "target_repo",
        "repo_path",
        "workspace",
        "cwd",
    }
    for key, value in facts.items():
        normalized_key = str(key or "").strip()
        if normalized_key in project_keys:
            haystack_parts.append(str(value or ""))
    for observation in _iter_observations(facts):
        haystack_parts.append(str(observation.get("entity_key") or ""))
        haystack_parts.append(str(observation.get("subject") or ""))
        haystack_parts.append(str(observation.get("predicate") or ""))
        haystack_parts.append(str(observation.get("value") or ""))
        metadata = observation.get("metadata")
        if isinstance(metadata, dict):
            haystack_parts.append(json.dumps(metadata, sort_keys=True, default=str))
    haystack = " ".join(part for part in haystack_parts if part).casefold()
    normalized_project = project_key.casefold()
    slug_project = normalized_project.replace(" ", "-")
    spaced_project = normalized_project.replace("-", " ")
    return normalized_project in haystack or slug_project in haystack or spaced_project in haystack


def _compact_line(value: str, *, limit: int = 180) -> str:
    cleaned = _clean_value(value)
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 3].rstrip()}..."


def _fact_line(*, predicate: str, value: str) -> str:
    normalized_predicate = str(predicate or "").strip()
    if not normalized_predicate:
        return ""
    return _compact_line(f"{normalized_predicate}: {value}")


def _append_unique(items: list[str], value: str) -> None:
    cleaned = _compact_line(value)
    if cleaned and cleaned not in items:
        items.append(cleaned)


def _first_nonempty(values: Any) -> str | None:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return None


def _looks_open(value: str) -> bool:
    lowered = str(value or "").casefold()
    return any(marker in lowered for marker in _OPEN_QUESTION_MARKERS)


def _collect_references(
    facts: dict[str, Any],
    *,
    repos_touched: list[str],
    artifacts_created: list[str],
) -> None:
    repo_keys = {
        "repo",
        "repository",
        "repository_full_name",
        "repo_full_name",
        "target_repo",
        "repo_path",
        "workspace",
        "cwd",
    }
    artifact_keys = {
        "path",
        "file",
        "file_path",
        "artifact",
        "artifact_path",
        "note_path",
        "output_path",
    }
    for key, value in facts.items():
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, sort_keys=True, default=str)
        else:
            value_text = str(value or "")
        normalized_key = str(key or "").strip()
        if normalized_key in repo_keys and value_text.strip():
            _append_unique(repos_touched, value_text)
        if normalized_key in artifact_keys and value_text.strip():
            _append_unique(artifacts_created, value_text)
        for match in _PATH_RE.findall(value_text):
            _append_unique(artifacts_created, match)
