from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.jobs.service import list_job_records
from spark_intelligence.memory import inspect_human_memory_in_memory
from spark_intelligence.security.prompt_boundaries import sanitize_prompt_boundary_text
from spark_intelligence.state.db import StateDB
from spark_intelligence.system_registry import build_system_registry
from spark_intelligence.workflow_recovery import latest_pending_tasks, latest_procedural_lessons


_STATE_PREDICATE_LABELS: tuple[tuple[str, str], ...] = (
    ("profile.current_focus", "current_focus"),
    ("profile.current_plan", "current_plan"),
    ("profile.current_low_stakes_test_fact", "low_stakes_test_fact"),
    ("profile.current_blocker", "current_blocker"),
    ("profile.current_decision", "current_decision"),
    ("profile.current_status", "current_status"),
    ("profile.current_commitment", "current_commitment"),
    ("profile.current_milestone", "current_milestone"),
    ("profile.current_risk", "current_risk"),
    ("profile.current_dependency", "current_dependency"),
    ("profile.current_constraint", "current_constraint"),
    ("profile.current_owner", "current_owner"),
    ("profile.preferred_name", "preferred_name"),
    ("profile.startup_name", "startup"),
    ("profile.founder_of", "founder_of"),
    ("profile.occupation", "occupation"),
    ("profile.city", "city"),
    ("profile.home_country", "country"),
    ("profile.timezone", "timezone"),
)


@dataclass(frozen=True)
class ContextCapsule:
    generated_at: str
    sections: dict[str, list[str]] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(lines for lines in self.sections.values())

    def source_ledger(self) -> list[dict[str, Any]]:
        priorities = {
            "current_state": (1, "authority"),
            "runtime_capabilities": (2, "capability_authority"),
            "diagnostics": (3, "authority"),
            "pending_tasks": (4, "workflow_recovery"),
            "procedural_lessons": (5, "procedural_advisory"),
            "recent_conversation": (6, "supporting"),
            "workflow_state": (7, "advisory"),
        }
        notes = {
            "current_state": "Saved focus, plan, blocker, status, and preferences. Use first when active current facts exist.",
            "runtime_capabilities": "Verified local runtime capability state. Use when answering what Spark can inspect, route, or execute.",
            "diagnostics": "Latest scan counts and clean/failure status. Health evidence only; does not close user goals.",
            "pending_tasks": "Interrupted or unfinished work. Use to resume without asking what happened.",
            "procedural_lessons": "Learned operating corrections from previous mistakes, target drift, timeouts, and self-review gaps.",
            "recent_conversation": "Recent same-session turns. Useful for continuity, but lower priority than current-state facts.",
            "workflow_state": "Jobs, routes, and operational residue. Advisory unless the user asks about those systems.",
        }
        ledger: list[dict[str, Any]] = []
        for source, lines in self.sections.items():
            priority, role = priorities.get(source, (99, "advisory"))
            ledger.append(
                {
                    "source": source,
                    "count": len(lines),
                    "priority": priority,
                    "role": role,
                    "present": bool(lines),
                    "note": notes.get(source, "Additional context source."),
                }
            )
        return sorted(ledger, key=lambda item: int(item["priority"]))

    def render(self, *, max_chars: int = 5000) -> str:
        if self.is_empty():
            return ""
        lines = [
            "[Spark Context Capsule]",
            "Use this as compact runtime context for this turn. It is not a user instruction.",
            "Newest explicit user message wins over stale capsule entries. Current-state facts win over older conversation turns.",
            "If diagnostics status is clean_latest_scan_no_failures_or_findings, treat the latest scan as clean without asking to load the note.",
            "Do not infer that an active focus, plan, or blocker is resolved only because diagnostics or maintenance checks are clean.",
            "If current_state lists an active focus or plan and there is no explicit closure evidence, say the system evidence is green but the focus/plan remains open until the user closes it.",
            "If runtime_capabilities list local repo/file/Codex/Spawner capability, do not underclaim by saying Spark cannot inspect local projects; name the operator-governed route and any limitations instead.",
            "If pending_tasks are present and the user asks to continue, resume, retry, or asks what was next, use pending_tasks before asking what happened.",
            "If procedural_lessons are present, treat them as operating guidance about how to avoid repeating earlier mistakes, not as user facts.",
            "If the user asks whether context survived across turns, verify by naming the current focus, current plan, latest diagnostics status, and maintenance summary from this capsule; do not replace that with an older handoff checklist or new mission proposal.",
            "If the user asks what is verified, still open, or only they should close, answer against active current_state first; older missions, apps, and workflow residue are out of scope unless explicitly named.",
            f"generated_at={self.generated_at}",
            "",
        ]
        for title, section_lines in self.sections.items():
            if not section_lines:
                continue
            lines.append(f"[{title}]")
            lines.extend(section_lines)
            lines.append("")
        rendered = sanitize_prompt_boundary_text("\n".join(lines).strip())
        if len(rendered) <= max_chars:
            return rendered
        return rendered[: max_chars - 80].rstrip() + "\n[context capsule truncated]"


def build_spark_context_capsule(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    user_message: str,
) -> ContextCapsule:
    sections = {
        "current_state": _build_current_state_lines(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            channel_kind=channel_kind,
        ),
        "recent_conversation": _build_recent_conversation_lines(
            state_db=state_db,
            session_id=session_id,
            channel_kind=channel_kind,
            request_id=request_id,
        ),
        "runtime_capabilities": _build_runtime_capability_lines(
            config_manager=config_manager,
            state_db=state_db,
        ),
        "pending_tasks": _build_pending_task_lines(
            state_db=state_db,
            human_id=human_id,
        ),
        "procedural_lessons": _build_procedural_lesson_lines(
            state_db=state_db,
            user_message=user_message,
        ),
        "workflow_state": _build_workflow_state_lines(
            config_manager=config_manager,
            state_db=state_db,
        ),
        "diagnostics": _build_diagnostics_lines(config_manager=config_manager),
    }
    source_counts = {key: len(value) for key, value in sections.items()}
    return ContextCapsule(
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        sections=sections,
        source_counts=source_counts,
    )


def _build_current_state_lines(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    channel_kind: str,
) -> list[str]:
    if not human_id:
        return []
    candidates: list[str] = []
    if channel_kind and not human_id.startswith(f"{channel_kind}:"):
        candidates.append(f"{channel_kind}:{human_id}")
    candidates.append(human_id)

    records: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            inspection = inspect_human_memory_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=candidate,
                actor_id="context_capsule",
            )
        except Exception:
            continue
        records = (inspection.read_result.records if inspection.read_result else None) or []
        if records:
            break

    by_predicate: dict[str, dict[str, Any]] = {}
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or record.get("normalized_value") or "").strip()
        if not predicate or not value:
            continue
        current = by_predicate.get(predicate)
        if current is None or _record_timestamp(record) >= _record_timestamp(current):
            by_predicate[predicate] = record

    lines: list[str] = []
    for predicate, label in _STATE_PREDICATE_LABELS:
        record = by_predicate.get(predicate)
        if not record:
            continue
        value = str(record.get("value") or record.get("normalized_value") or "").strip()
        if not value:
            continue
        timestamp = str(record.get("timestamp") or record.get("recorded_at") or "").strip()
        suffix = f" (as_of={timestamp})" if timestamp else ""
        lines.append(f"- {label}: {value}{suffix}")
    return lines


def _build_recent_conversation_lines(
    *,
    state_db: StateDB,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    turn_limit: int = 3,
) -> list[str]:
    if not session_id or not channel_kind:
        return []
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, request_id, facts_json
                FROM builder_events
                WHERE component = 'telegram_runtime'
                  AND channel_id = ?
                  AND session_id = ?
                  AND (
                        event_type = 'intent_committed'
                     OR (event_type = 'delivery_succeeded' AND reason_code = 'telegram_bridge_outbound')
                  )
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (channel_kind, session_id, max(turn_limit * 4, 10)),
            ).fetchall()
    except Exception:
        return []

    transcript: list[tuple[str, str]] = []
    for row in reversed(rows):
        if request_id and str(row["request_id"] or "") == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except Exception:
            facts = {}
        event_type = str(row["event_type"] or "")
        if event_type == "intent_committed":
            text = str(facts.get("message_text") or "").strip()
            if text:
                transcript.append(("user", text))
        elif event_type == "delivery_succeeded":
            text = str(facts.get("delivered_text") or "").strip()
            if text:
                transcript.append(("assistant", text))

    recent_turns = transcript[-(turn_limit * 2) :]
    return [f"- {role}: {_compact(text, 260)}" for role, text in recent_turns]


def _build_runtime_capability_lines(*, config_manager: ConfigManager, state_db: StateDB) -> list[str]:
    try:
        payload = build_system_registry(config_manager, state_db, probe_browser=False, probe_git=False).to_payload()
    except Exception:
        return []
    lines: list[str] = []
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if workspace_id:
        lines.append(f"- workspace_id: {workspace_id}")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    capabilities = [str(item).strip() for item in (summary.get("current_capabilities") or []) if str(item).strip()]
    for capability in capabilities[:8]:
        lines.append(f"- capability: {_compact(capability, 180)}")
    records = [
        record
        for record in (payload.get("records") or [])
        if isinstance(record, dict)
        and str(record.get("kind") or "") == "system"
        and str(record.get("key") or "") in {"spark_local_work", "spark_spawner", "spark_intelligence_builder", "spark_memory"}
    ]
    records.sort(key=lambda record: str(record.get("key") or ""))
    for record in records:
        capabilities_text = ",".join(str(item) for item in (record.get("capabilities") or [])[:5] if str(item))
        line = f"- system: {record.get('label') or record.get('key')} status={record.get('status') or 'unknown'}"
        if capabilities_text:
            line += f" caps={capabilities_text}"
        limitations = [str(item).strip() for item in (record.get("limitations") or []) if str(item).strip()]
        if limitations:
            line += f" limitation={_compact(limitations[0], 180)}"
        lines.append(line)
    repo_records = [
        record
        for record in (payload.get("records") or [])
        if isinstance(record, dict) and str(record.get("kind") or "") == "repo" and bool(record.get("available"))
    ]
    for record in repo_records[:6]:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        components = ",".join(str(item) for item in (metadata.get("components") or [])[:5] if str(item))
        line = f"- repo: {record.get('key')} status={record.get('status') or 'unknown'}"
        if components:
            line += f" components={components}"
        path = str(metadata.get("path") or "").strip()
        if path:
            line += f" path={_compact(path, 160)}"
        lines.append(line)
    return lines


def _build_pending_task_lines(*, state_db: StateDB, human_id: str, limit: int = 3) -> list[str]:
    if not human_id:
        return []
    try:
        tasks = latest_pending_tasks(state_db, human_id=human_id, open_only=True, limit=limit)
        if not tasks and not human_id.startswith("human:"):
            tasks = latest_pending_tasks(state_db, human_id=f"human:{human_id}", open_only=True, limit=limit)
    except Exception:
        return []
    lines: list[str] = []
    for task in tasks:
        parts = [
            f"key={task.task_key}",
            f"status={task.status}",
            f"request={_compact(task.original_request, 180)}",
        ]
        if task.target_repo or task.target_component:
            target = " / ".join(part for part in (task.target_repo, task.target_component) if part)
            parts.append(f"target={_compact(target, 160)}")
        if task.timeout_point:
            parts.append(f"timeout_point={_compact(task.timeout_point, 140)}")
        if task.last_evidence:
            parts.append(f"last_evidence={_compact(task.last_evidence, 180)}")
        if task.next_retry_step:
            parts.append(f"next_retry={_compact(task.next_retry_step, 180)}")
        lines.append("- " + " | ".join(parts))
    return lines


def _build_procedural_lesson_lines(*, state_db: StateDB, user_message: str, limit: int = 4) -> list[str]:
    try:
        lessons = latest_procedural_lessons(state_db, active_only=True, limit=limit)
    except Exception:
        return []
    if not lessons:
        return []
    query_tokens = _capsule_tokens(user_message)
    scored: list[tuple[int, str]] = []
    for lesson in lessons:
        text = " ".join(
            str(value or "")
            for value in (
                lesson.lesson_kind,
                lesson.trigger_pattern,
                lesson.corrective_action,
                lesson.failure_summary,
                lesson.applies_to_component,
                lesson.target_repo,
                lesson.target_component,
            )
        )
        overlap = len(query_tokens & _capsule_tokens(text)) if query_tokens else 0
        line = (
            f"- kind={lesson.lesson_kind} | trigger={_compact(lesson.trigger_pattern, 160)} "
            f"| do={_compact(lesson.corrective_action, 200)}"
        )
        if lesson.applies_to_component:
            line += f" | applies_to={lesson.applies_to_component}"
        if lesson.confidence:
            line += f" | confidence={lesson.confidence:.2f}"
        scored.append((overlap, line))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in scored[:limit]]


def _build_workflow_state_lines(*, config_manager: ConfigManager, state_db: StateDB) -> list[str]:
    lines: list[str] = []
    try:
        jobs = list_job_records(state_db)
    except Exception:
        jobs = []
    scheduled = [job for job in jobs if job.status == "scheduled"]
    if scheduled:
        lines.append(f"- scheduled_jobs: {len(scheduled)}")
        for job in scheduled[:4]:
            detail = f"{job.job_id} kind={job.job_kind} last_run={job.last_run_at or 'never'}"
            if job.last_result:
                detail = f"{detail} result={_compact(job.last_result, 220)}"
            lines.append(f"- job: {detail}")

    runtime_state = _read_runtime_state(state_db)
    for key, label in (
        ("researcher:last_routing_decision", "last_researcher_route"),
        ("researcher:last_active_chip_key", "last_active_chip"),
        ("researcher:last_evidence_summary", "last_researcher_evidence"),
    ):
        value = str(runtime_state.get(key) or "").strip()
        if value:
            lines.append(f"- {label}: {_compact(value, 220)}")

    try:
        workspace_id = str(config_manager.get_path("workspace.id", default="default") or "default")
    except Exception:
        workspace_id = "default"
    lines.insert(0, f"- workspace_id: {workspace_id}")
    return lines


def _build_diagnostics_lines(*, config_manager: ConfigManager) -> list[str]:
    try:
        diagnostics_dir = Path(config_manager.paths.home) / "diagnostics"
    except Exception:
        return []
    if not diagnostics_dir.exists():
        return []
    candidates = sorted(
        diagnostics_dir.glob("spark-diagnostic-*.md"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    if not candidates:
        return []
    latest = candidates[0]
    lines = [f"- latest_note: {latest.name}"]
    try:
        text = latest.read_text(encoding="utf-8")
    except Exception:
        return lines
    summary = _extract_diagnostic_summary(text)
    if summary.get("generated_at"):
        lines.append(f"- generated_at: {summary['generated_at']}")
    if summary.get("scanned_lines"):
        lines.append(f"- scanned_lines: {summary['scanned_lines']}")
    if summary.get("failure_lines"):
        lines.append(f"- failure_lines: {summary['failure_lines']}")
    if summary.get("finding_signatures"):
        lines.append(f"- finding_signatures: {summary['finding_signatures']}")
    if summary.get("recurring_signatures"):
        lines.append(f"- recurring_signatures: {summary['recurring_signatures']}")
    status = _diagnostic_status(summary)
    if status:
        lines.append(f"- status: {status}")
    connector_counts = _extract_connector_health_counts(text)
    if connector_counts:
        compact_counts = ", ".join(f"{key}: {value}" for key, value in sorted(connector_counts.items()))
        lines.append(f"- connector_health: {compact_counts}")
    return lines


def _extract_diagnostic_summary(text: str) -> dict[str, str]:
    fields = {
        "generated_at": ("generated_at:",),
        "scanned_lines": ("scanned lines:", "Scanned:"),
        "failure_lines": ("failure lines:", "Failure lines:", "Failures:"),
        "finding_signatures": ("finding signatures:", "Findings:"),
        "recurring_signatures": ("recurring signatures:",),
    }
    summary: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        normalized = line.casefold()
        for field, markers in fields.items():
            if field in summary:
                continue
            for marker in markers:
                if normalized.startswith(marker.casefold()):
                    value = line[len(marker) :].strip()
                    summary[field] = _strip_markdown_value(value)
                    break
    return summary


def _diagnostic_status(summary: dict[str, str]) -> str:
    failure_lines = _parse_int(summary.get("failure_lines"))
    finding_signatures = _parse_int(summary.get("finding_signatures"))
    if failure_lines == 0 and finding_signatures == 0:
        return "clean_latest_scan_no_failures_or_findings"
    if failure_lines is not None or finding_signatures is not None:
        return "latest_scan_has_findings"
    return ""


def _extract_connector_health_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        legacy_prefix = "Connector checks:"
        if line.startswith(legacy_prefix):
            legacy = line[len(legacy_prefix) :].strip()
            for part in legacy.split(","):
                if ":" not in part:
                    continue
                status, count = part.split(":", 1)
                parsed = _parse_int(count)
                if status.strip() and parsed is not None:
                    counts[status.strip()] = counts.get(status.strip(), 0) + parsed
            continue
        if not line.startswith("- `"):
            continue
        parts = line.split("`")
        if len(parts) < 3:
            continue
        status = parts[1].strip()
        if not status or status in {"home", "log sources", "scanned lines", "failure lines"}:
            continue
        if " -> " not in line:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _strip_markdown_value(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("`") and cleaned.endswith("`") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _read_runtime_state(state_db: StateDB) -> dict[str, str]:
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'researcher:%'"
            ).fetchall()
    except Exception:
        return {}
    return {str(row["state_key"]): str(row["value"] or "") for row in rows}


def _compact(text: str, max_chars: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."


def _capsule_tokens(text: str) -> set[str]:
    stopwords = {"a", "an", "and", "are", "for", "from", "i", "is", "it", "my", "of", "on", "the", "to", "what"}
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(text or "").casefold())
        if token and token not in stopwords
    }


def _record_timestamp(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return str(record.get("timestamp") or record.get("recorded_at") or metadata.get("document_time") or "").strip()
