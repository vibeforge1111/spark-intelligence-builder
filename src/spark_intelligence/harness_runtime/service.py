from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import close_run, open_run, record_event
from spark_intelligence.state.db import StateDB


_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)


@dataclass(frozen=True)
class HarnessTaskEnvelope:
    envelope_id: str
    task: str
    harness_id: str
    owner_system: str
    backend_kind: str
    session_scope: str
    prompt_strategy: str
    route_mode: str
    required_capabilities: list[str]
    artifacts_expected: list[str]
    next_actions: list[str]
    limitations: list[str]
    channel_kind: str | None
    session_id: str | None
    human_id: str | None
    agent_id: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "task": self.task,
            "harness_id": self.harness_id,
            "owner_system": self.owner_system,
            "backend_kind": self.backend_kind,
            "session_scope": self.session_scope,
            "prompt_strategy": self.prompt_strategy,
            "route_mode": self.route_mode,
            "required_capabilities": self.required_capabilities,
            "artifacts_expected": self.artifacts_expected,
            "next_actions": self.next_actions,
            "limitations": self.limitations,
            "channel_kind": self.channel_kind,
            "session_id": self.session_id,
            "human_id": self.human_id,
            "agent_id": self.agent_id,
        }


@dataclass(frozen=True)
class HarnessExecutionResult:
    envelope: HarnessTaskEnvelope
    run_id: str
    status: str
    summary: str
    artifacts: dict[str, Any]
    next_actions: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_payload(),
            "run_id": self.run_id,
            "status": self.status,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "next_actions": self.next_actions,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class HarnessRuntimeSnapshot:
    generated_at: str
    workspace_id: str
    summary: dict[str, Any]
    recent_runs: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "summary": self.summary,
            "recent_runs": self.recent_runs,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


def build_harness_task_envelope(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
    channel_kind: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> HarnessTaskEnvelope:
    from spark_intelligence.harness_registry import build_harness_selection

    selection = build_harness_selection(
        config_manager=config_manager,
        state_db=state_db,
        task=task,
    )
    return HarnessTaskEnvelope(
        envelope_id=f"htask:{uuid4().hex[:12]}",
        task=str(task or "").strip(),
        harness_id=selection.harness_id,
        owner_system=selection.owner_system,
        backend_kind=selection.backend_kind,
        session_scope=selection.session_scope,
        prompt_strategy=selection.prompt_strategy,
        route_mode=selection.route_mode,
        required_capabilities=list(selection.required_capabilities),
        artifacts_expected=list(selection.artifacts),
        next_actions=list(selection.next_actions),
        limitations=list(selection.limitations),
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )


def execute_harness_task(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
) -> HarnessExecutionResult:
    run = open_run(
        state_db,
        run_kind=f"harness:{envelope.harness_id}",
        origin_surface="harness_runtime",
        summary=f"Harness execution opened for {envelope.harness_id}.",
        request_id=envelope.envelope_id,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        reason_code="harness_execution_started",
        facts={
            "harness_id": envelope.harness_id,
            "backend_kind": envelope.backend_kind,
            "route_mode": envelope.route_mode,
        },
    )
    record_event(
        state_db,
        event_type="harness_execution_started",
        component="harness_runtime",
        summary=f"Started {envelope.harness_id} for task envelope {envelope.envelope_id}.",
        run_id=run.run_id,
        request_id=envelope.envelope_id,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        reason_code="harness_execution_started",
        facts=envelope.to_payload(),
    )
    try:
        if envelope.harness_id == "builder.direct":
            summary = "Task retained in Builder direct harness."
            artifacts = {
                "execution_contract": {
                    "reply_mode": "builder_local_runtime",
                    "owner_system": envelope.owner_system,
                    "prompt_strategy": envelope.prompt_strategy,
                    "required_capabilities": envelope.required_capabilities,
                }
            }
            status = "prepared"
        elif envelope.harness_id == "browser.grounded":
            artifacts, summary, status = _execute_browser_grounded_harness(
                config_manager=config_manager,
                envelope=envelope,
            )
        else:
            summary = f"{envelope.harness_id} execution contract prepared but no active runner exists yet."
            artifacts = {
                "execution_contract": {
                    "owner_system": envelope.owner_system,
                    "backend_kind": envelope.backend_kind,
                    "session_scope": envelope.session_scope,
                    "required_capabilities": envelope.required_capabilities,
                }
            }
            status = "planned"
        close_run(
            state_db,
            run_id=run.run_id,
            status="closed",
            close_reason="harness_execution_completed",
            summary=summary,
            facts={
                "harness_id": envelope.harness_id,
                "execution_status": status,
                "artifact_keys": sorted(artifacts.keys()),
            },
        )
        record_event(
            state_db,
            event_type="harness_execution_completed",
            component="harness_runtime",
            summary=summary,
            run_id=run.run_id,
            request_id=envelope.envelope_id,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
            actor_id="harness_runtime",
            reason_code="harness_execution_completed",
            facts={
                "harness_id": envelope.harness_id,
                "execution_status": status,
                "artifact_keys": sorted(artifacts.keys()),
            },
        )
        return HarnessExecutionResult(
            envelope=envelope,
            run_id=run.run_id,
            status=status,
            summary=summary,
            artifacts=artifacts,
            next_actions=list(envelope.next_actions),
        )
    except Exception as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="harness_execution_failed",
            summary=f"Harness execution failed for {envelope.harness_id}.",
            facts={"error": str(exc), "harness_id": envelope.harness_id},
        )
        record_event(
            state_db,
            event_type="harness_execution_failed",
            component="harness_runtime",
            summary=f"Harness execution failed for {envelope.harness_id}.",
            run_id=run.run_id,
            request_id=envelope.envelope_id,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
            actor_id="harness_runtime",
            reason_code="harness_execution_failed",
            facts={"error": str(exc), "harness_id": envelope.harness_id},
        )
        raise


def build_harness_runtime_snapshot(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    limit: int = 8,
) -> HarnessRuntimeSnapshot:
    workspace_id = str(config_manager.get_path("workspace.id", default="default"))
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, run_kind, status, request_id, session_id, opened_at, closed_at, close_reason, summary_json
            FROM builder_runs
            WHERE run_kind LIKE 'harness:%'
            ORDER BY opened_at DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    recent_runs: list[dict[str, Any]] = []
    for row in rows:
        run_kind = str(row["run_kind"] or "")
        recent_runs.append(
            {
                "run_id": str(row["run_id"]),
                "harness_id": run_kind.split("harness:", 1)[1] if "harness:" in run_kind else run_kind,
                "status": str(row["status"] or ""),
                "request_id": str(row["request_id"]) if row["request_id"] else None,
                "session_id": str(row["session_id"]) if row["session_id"] else None,
                "opened_at": str(row["opened_at"]) if row["opened_at"] else None,
                "closed_at": str(row["closed_at"]) if row["closed_at"] else None,
                "close_reason": str(row["close_reason"]) if row["close_reason"] else None,
                "summary_json": json.loads(str(row["summary_json"])) if row["summary_json"] else {},
            }
        )
    summary = {
        "recent_run_count": len(recent_runs),
        "open_run_count": len([item for item in recent_runs if item.get("status") == "open"]),
        "failed_run_count": len([item for item in recent_runs if item.get("status") == "failed"]),
        "last_harness_id": recent_runs[0]["harness_id"] if recent_runs else None,
    }
    return HarnessRuntimeSnapshot(
        generated_at=_now_iso(),
        workspace_id=workspace_id,
        summary=summary,
        recent_runs=recent_runs,
    )


def _execute_browser_grounded_harness(
    *,
    config_manager: ConfigManager,
    envelope: HarnessTaskEnvelope,
) -> tuple[dict[str, Any], str, str]:
    from spark_intelligence.browser import build_browser_navigate_payload, build_browser_status_payload

    url = _extract_first_url(envelope.task)
    if not url:
        return (
            {
                "browser_status_payload": build_browser_status_payload(config_manager=config_manager),
                "needs_input": {
                    "reason": "Browser grounded harness requires an explicit URL for the first executable runner.",
                    "task": envelope.task,
                },
            },
            "Browser grounded harness needs an explicit URL before it can prepare a navigate payload.",
            "needs_input",
        )
    return (
        {
            "browser_navigate_payload": build_browser_navigate_payload(
                config_manager=config_manager,
                url=url,
            )
        },
        f"Prepared a governed browser navigate payload for {url}.",
        "prepared",
    )


def _extract_first_url(text: str) -> str | None:
    match = _URL_RE.search(str(text or ""))
    return match.group(0) if match else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
