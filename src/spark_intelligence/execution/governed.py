from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


DEFAULT_GOVERNED_COMMAND_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class GovernedCommandExecution:
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    timeout_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_governed_command(
    *,
    command: list[str],
    cwd: str | Path,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout_seconds: float | None = DEFAULT_GOVERNED_COMMAND_TIMEOUT_SECONDS,
    encoding: str | None = None,
    errors: str | None = None,
) -> GovernedCommandExecution:
    run_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "capture_output": True,
        "text": True,
        "timeout": timeout_seconds,
    }
    if input_text is not None:
        run_kwargs["input"] = input_text
    if encoding:
        run_kwargs["encoding"] = encoding
    if errors:
        run_kwargs["errors"] = errors
    try:
        completed = subprocess.run(
            command,
            **run_kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        effective_timeout = exc.timeout if exc.timeout is not None else timeout_seconds
        rendered_timeout = float(effective_timeout) if effective_timeout is not None else 0.0
        return GovernedCommandExecution(
            command=["<redacted:timed_out>"],
            cwd=str(cwd),
            exit_code=124,
            stdout="",
            stderr=f"Governed command timed out after {rendered_timeout:g} seconds.",
            timed_out=True,
            timeout_seconds=rendered_timeout,
        )
    return GovernedCommandExecution(
        command=list(command),
        cwd=str(cwd),
        exit_code=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        timeout_seconds=timeout_seconds,
    )


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
        "timed_out": execution.timed_out,
        "timeout_seconds": execution.timeout_seconds,
        "stderr": execution.stderr[:200] if execution.stderr else "",
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
