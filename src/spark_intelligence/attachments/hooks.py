from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.attachments.registry import AttachmentRecord, attachment_status
from spark_intelligence.attachments.snapshot import build_attachment_snapshot
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.execution.governed import (
    GovernedCommandExecution,
    record_governed_tool_result,
    run_governed_command,
    screen_governed_tool_text,
)
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


@dataclass
class ChipHookExecution:
    chip_key: str
    hook: str
    repo_root: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    payload: dict[str, Any]
    output: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "chip_key": self.chip_key,
            "hook": self.hook,
            "repo_root": self.repo_root,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "payload": self.payload,
            "output": self.output,
            "ok": self.ok,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        result = self.output.get("result") if isinstance(self.output, dict) else None
        lines = [
            f"Chip hook execution: {self.chip_key}.{self.hook}",
            f"- ok: {'yes' if self.ok else 'no'}",
            f"- repo_root: {self.repo_root}",
            f"- command: {' '.join(self.command)}",
            f"- exit_code: {self.exit_code}",
        ]
        if self.stdout:
            lines.append(f"- stdout: {self.stdout.strip()}")
        if self.stderr:
            lines.append(f"- stderr: {self.stderr.strip()}")
        if isinstance(result, dict):
            lines.append(f"- result keys: {', '.join(sorted(result.keys())) if result else 'none'}")
        return "\n".join(lines)


def list_active_chip_records(config_manager: ConfigManager) -> list[AttachmentRecord]:
    snapshot = build_attachment_snapshot(config_manager)
    active_keys = snapshot.active_chip_keys
    scan = attachment_status(config_manager)
    records = {
        record.key: record
        for record in scan.records
        if record.kind == "chip"
    }
    return [records[key] for key in active_keys if key in records]


def list_chip_records(config_manager: ConfigManager) -> list[AttachmentRecord]:
    scan = attachment_status(config_manager)
    return [record for record in scan.records if record.kind == "chip"]


def resolve_chip_record(config_manager: ConfigManager, *, chip_key: str) -> AttachmentRecord:
    scan = attachment_status(config_manager)
    for record in scan.records:
        if record.kind == "chip" and record.key == chip_key:
            return record
    known = sorted(record.key for record in scan.records if record.kind == "chip")
    raise ValueError(f"Unknown chip key '{chip_key}'. Known chip keys: {', '.join(known) if known else 'none'}")


def run_chip_hook(
    config_manager: ConfigManager,
    *,
    chip_key: str,
    hook: str,
    payload: dict[str, Any],
) -> ChipHookExecution:
    record = resolve_chip_record(config_manager, chip_key=chip_key)
    return execute_chip_hook_record(record, hook=hook, payload=payload)


def run_first_active_chip_hook(
    config_manager: ConfigManager,
    *,
    hook: str,
    payload: dict[str, Any],
) -> ChipHookExecution | None:
    for record in list_active_chip_records(config_manager):
        if hook in record.commands:
            return execute_chip_hook_record(record, hook=hook, payload=payload)
    return None


def run_first_chip_hook_supporting(
    config_manager: ConfigManager,
    *,
    hook: str,
    payload: dict[str, Any],
) -> ChipHookExecution | None:
    active_records = list_active_chip_records(config_manager)
    active_roots = {record.repo_root for record in active_records}
    for record in active_records:
        if hook in record.commands:
            return execute_chip_hook_record(record, hook=hook, payload=payload)
    for record in list_chip_records(config_manager):
        if record.repo_root in active_roots:
            continue
        if hook in record.commands:
            return execute_chip_hook_record(record, hook=hook, payload=payload)
    return None


def execute_chip_hook_record(
    record: AttachmentRecord,
    *,
    hook: str,
    payload: dict[str, Any],
) -> ChipHookExecution:
    if record.kind != "chip":
        raise ValueError(f"Attachment '{record.key}' is not a chip.")
    if record.io_protocol not in {None, "", "spark-hook-io.v1"}:
        raise ValueError(
            f"Chip '{record.key}' uses unsupported io_protocol '{record.io_protocol}'."
        )
    command = list(record.commands.get(hook) or [])
    if not command:
        supported = ", ".join(sorted(record.commands)) if record.commands else "none"
        raise ValueError(f"Chip '{record.key}' does not define hook '{hook}'. Supported hooks: {supported}")

    repo_root = Path(record.repo_root)
    final_command = _normalize_command(command)
    env = os.environ.copy()
    src_root = repo_root / "src"
    if src_root.exists():
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(src_root) if not existing_pythonpath else os.pathsep.join([str(src_root), existing_pythonpath])
        )
    env.update(_runtime_env_overrides(record))

    with tempfile.TemporaryDirectory(prefix=f"spark-chip-{record.key}-{hook}-") as temp_dir:
        temp_root = Path(temp_dir)
        input_path = temp_root / "input.json"
        output_path = temp_root / "output.json"
        input_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        command = [*final_command, "--input", str(input_path), "--output", str(output_path)]
        completed = run_governed_command(
            command=command,
            cwd=repo_root,
            env=env,
        )
        output = _load_json_file(output_path)
    return ChipHookExecution(
        chip_key=record.key,
        hook=hook,
        repo_root=str(repo_root),
        command=completed.command,
        exit_code=completed.exit_code,
        stdout=completed.stdout,
        stderr=completed.stderr,
        payload=payload,
        output=output,
    )


def record_chip_hook_execution(
    state_db: StateDB,
    *,
    execution: ChipHookExecution,
    component: str,
    actor_id: str,
    summary: str,
    reason_code: str,
    keepability: str,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    hook = str(getattr(execution, "hook", "") or "unknown")
    repo_root = str(getattr(execution, "repo_root", "") or "")
    exit_code = int(getattr(execution, "exit_code", 0) or 0)
    stderr = str(getattr(execution, "stderr", "") or "")
    provenance = {
        "source_kind": "chip_hook",
        "source_ref": execution.chip_key,
        "hook": hook,
        "repo_root": repo_root,
    }
    facts = {
        "chip_key": execution.chip_key,
        "hook": hook,
        "ok": execution.ok,
        "exit_code": exit_code,
        "keepability": keepability,
    }
    record_event(
        state_db,
        event_type="plugin_or_chip_influence_recorded",
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
        facts=facts,
        provenance=provenance,
    )
    record_governed_tool_result(
        state_db,
        execution=GovernedCommandExecution(
            command=list(getattr(execution, "command", []) or []),
            cwd=repo_root,
            exit_code=exit_code,
            stdout=str(getattr(execution, "stdout", "") or ""),
            stderr=stderr,
        ),
        component=component,
        actor_id=actor_id,
        summary=f"Chip hook {execution.chip_key}.{hook} {'produced a result' if execution.ok else 'failed'}.",
        reason_code=reason_code,
        source_kind="chip_hook",
        source_ref=execution.chip_key,
        facts=facts,
        provenance=provenance,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )


def screen_chip_hook_text(
    *,
    state_db: StateDB,
    execution: ChipHookExecution,
    text: str,
    summary: str,
    reason_code: str,
    policy_domain: str,
    blocked_stage: str,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
) -> dict[str, Any]:
    hook = str(getattr(execution, "hook", "") or "unknown")
    repo_root = str(getattr(execution, "repo_root", "") or "")
    return screen_governed_tool_text(
        state_db=state_db,
        execution=GovernedCommandExecution(
            command=list(getattr(execution, "command", []) or []),
            cwd=repo_root,
            exit_code=int(getattr(execution, "exit_code", 0) or 0),
            stdout=str(getattr(execution, "stdout", "") or ""),
            stderr=str(getattr(execution, "stderr", "") or ""),
        ),
        text=text,
        source_kind="chip_hook_output",
        source_ref=f"{execution.chip_key}:{hook}",
        summary=summary,
        reason_code=reason_code,
        policy_domain=policy_domain,
        blocked_stage=blocked_stage,
        provenance={
            "source_kind": "chip_hook",
            "source_ref": execution.chip_key,
            "hook": hook,
            "repo_root": repo_root,
        },
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
    )


def _normalize_command(command: list[str]) -> list[str]:
    if not command:
        return command
    first = command[0].lower()
    if first in {"python", "python3"}:
        return [sys.executable, *command[1:]]
    return command


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_env_overrides(record: AttachmentRecord) -> dict[str, str]:
    frontier = record.frontier if isinstance(record.frontier, dict) else {}
    runtime_family = str(frontier.get("runtime_family") or "").strip().lower()
    if runtime_family == "browser-capability":
        return {
            "SPARK_BROWSER_ATTACHMENT_MODE": "native-host-session",
        }
    return {}
