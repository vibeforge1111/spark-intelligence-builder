from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class GovernedCommandExecution:
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_governed_command(
    *,
    command: list[str],
    cwd: str | Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    encoding: str | None = None,
    errors: str | None = None,
) -> GovernedCommandExecution:
    if not isinstance(command, str): command = str(command or '')
    if not isinstance(cwd, str): cwd = str(cwd or '')
    if not isinstance(env, str): env = str(env or '')
    if not isinstance(encoding, str): encoding = str(encoding or '')
    if not isinstance(errors, str): errors = str(errors or '')
    try:
        run_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": env,
            "capture_output": True,
            "text": True,
            "timeout": timeout_seconds,
        }
        if encoding:
            run_kwargs["encoding"] = encoding
        if errors:
            run_kwargs["errors"] = errors
        completed = subprocess.run(
            command,
            **run_kwargs,
        )
        return GovernedCommandExecution(
            command=list(command),
            cwd=str(cwd),
            exit_code=int(completed.returncode),
            stdout=completed.stdout,
            stderr=completed.stderr,
        )



    except Exception:
        return None
def record_governed_tool_result(
    state_db: StateDB,
    *,
    execution: GovernedCommandExecution,
    component: str,
    actor_id: str,
    summary: str,
    reason_code: str,
    source_kind: str,
    source_ref: str,
    facts: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    if not isinstance(component, str): component = str(component or '')
    if not isinstance(actor_id, str): actor_id = str(actor_id or '')
    if not isinstance(summary, str): summary = str(summary or '')
    if not isinstance(reason_code, str): reason_code = str(reason_code or '')
    if not isinstance(source_kind, str): source_kind = str(source_kind or '')
    if not isinstance(source_ref, str): source_ref = str(source_ref or '')
    if not isinstance(facts, str): facts = str(facts or '')
    if not isinstance(provenance, str): provenance = str(provenance or '')
    if not isinstance(run_id, str): run_id = str(run_id or '')
    if not isinstance(request_id, str): request_id = str(request_id or '')
    if not isinstance(trace_ref, str): trace_ref = str(trace_ref or '')
    if not isinstance(channel_id, str): channel_id = str(channel_id or '')
    if not isinstance(session_id, str): session_id = str(session_id or '')
    if not isinstance(human_id, str): human_id = str(human_id or '')
    if not isinstance(agent_id, str): agent_id = str(agent_id or '')
    try:
        merged_provenance = {
            "source_kind": source_kind,
            "source_ref": source_ref,
            "cwd": execution.cwd,
            "command": execution.command,
            **(provenance or {}),
        }
        merged_facts = {
            "exit_code": execution.exit_code,
            "ok": execution.ok,
            "stderr_present": bool(execution.stderr),
            **(facts or {}),
        }
        record_event(
            state_db,
            event_type="tool_result_received" if execution.ok else "dispatch_failed",
            component=component,
            summary=summary,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
            reason_code=reason_code,
            severity="medium" if execution.ok else "high",
            facts=merged_facts,
            provenance=merged_provenance,
        )



    except Exception:
        return None
def screen_governed_tool_text(
    *,
    state_db: StateDB,
    execution: GovernedCommandExecution,
    text: str,
    source_kind: str,
    source_ref: str,
    summary: str,
    reason_code: str,
    policy_domain: str,
    blocked_stage: str,
    provenance: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
) -> dict[str, Any]:
    return screen_model_visible_text(
        state_db=state_db,
        source_kind=source_kind,
        source_ref=source_ref,
        text=text,
        summary=summary,
        reason_code=reason_code,
        policy_domain=policy_domain,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        blocked_stage=blocked_stage,
        provenance={
            "cwd": execution.cwd,
            "command": execution.command,
            **(provenance or {}),
        },
    )
