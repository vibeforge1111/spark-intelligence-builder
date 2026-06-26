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
from spark_intelligence.harness_contract import MutationClass, verify_governor_tool_authority
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB

DISABLED_LEGACY_BROWSER_CHIP_KEYS = {"spark-browser", "spark_browser"}
DISABLED_LEGACY_BROWSER_HOOK_PREFIXES = ("browser.",)
LEGACY_BROWSER_DISABLED_MESSAGE = (
    "The legacy browser extension lane is disabled. Use the guarded Spark CLI browser-use MCP lane instead."
)


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
    governor_verification: dict[str, Any] | None = None

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
            "governor_verification": self.governor_verification,
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
    governor_decision: dict[str, Any] | None = None,
) -> ChipHookExecution:
    record = resolve_chip_record(config_manager, chip_key=chip_key)
    return execute_chip_hook_record(record, hook=hook, payload=payload, governor_decision=governor_decision)


def run_first_active_chip_hook(
    config_manager: ConfigManager,
    *,
    hook: str,
    payload: dict[str, Any],
    governor_decision: dict[str, Any] | None = None,
) -> ChipHookExecution | None:
    for record in list_active_chip_records(config_manager):
        if hook in record.commands:
            return execute_chip_hook_record(record, hook=hook, payload=payload, governor_decision=governor_decision)
    return None


def run_first_chip_hook_supporting(
    config_manager: ConfigManager,
    *,
    hook: str,
    payload: dict[str, Any],
    governor_decision: dict[str, Any] | None = None,
) -> ChipHookExecution | None:
    active_records = list_active_chip_records(config_manager)
    active_roots = {record.repo_root for record in active_records}
    for record in active_records:
        if hook in record.commands:
            return execute_chip_hook_record(record, hook=hook, payload=payload, governor_decision=governor_decision)
    for record in list_chip_records(config_manager):
        if record.repo_root in active_roots:
            continue
        if hook in record.commands:
            return execute_chip_hook_record(record, hook=hook, payload=payload, governor_decision=governor_decision)
    return None



_MINIMAL_ENV_KEYS = {
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
}


def _build_minimal_chip_env(record: AttachmentRecord, repo_root: Path) -> dict[str, str]:
    """Build minimal environment for chip hook subprocesses.

    Only passes variables needed for execution, preventing
    credential leakage to potentially untrusted chip code.
    """
    env: dict[str, str] = {}
    for key in _MINIMAL_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if "HOME" not in env:
        env["HOME"] = str(Path.home())
    src_root = repo_root / "src"
    if src_root.exists():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(src_root) if not existing else os.pathsep.join([str(src_root), existing])
        )
    env.update(_runtime_env_overrides(record))
    return env


def execute_chip_hook_record(
    record: AttachmentRecord,
    *,
    hook: str,
    payload: dict[str, Any],
    governor_decision: dict[str, Any] | None = None,
) -> ChipHookExecution:
    if record.kind != "chip":
        raise ValueError(f"Attachment '{record.key}' is not a chip.")
    if _is_disabled_legacy_browser_hook(record.key, hook):
        raise ValueError(LEGACY_BROWSER_DISABLED_MESSAGE)
    if record.io_protocol not in {None, "", "spark-hook-io.v1"}:
        raise ValueError(
            f"Chip '{record.key}' uses unsupported io_protocol '{record.io_protocol}'."
        )
    command = list(record.commands.get(hook) or [])
    if not command:
        supported = ", ".join(sorted(record.commands)) if record.commands else "none"
        raise ValueError(f"Chip '{record.key}' does not define hook '{hook}'. Supported hooks: {supported}")
    governor_verification = _verify_chip_hook_governor_authority(
        record=record,
        hook=hook,
        governor_decision=governor_decision,
    )

    repo_root = Path(record.repo_root)
    final_command = _normalize_command(command)
    env = _build_minimal_chip_env(record, repo_root)

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
        governor_verification=governor_verification,
    )


def _verify_chip_hook_governor_authority(
    *,
    record: AttachmentRecord,
    hook: str,
    governor_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    tool_name, owner_system, mutation_class, external_network = chip_hook_authority_contract(hook)
    verification = verify_governor_tool_authority(
        governor_decision,
        tool_name=tool_name,
        owner_system=owner_system,
        mutation_class=mutation_class,
        external_network=external_network,
        require_pre_execution_ledger=True,
        allow_read_only=mutation_class in {"none", "read_only"},
    )
    if verification.get("allowed") is True:
        return verification
    reasons = [str(reason) for reason in verification.get("reason_codes") or [] if str(reason)]
    reason_text = ", ".join(reasons) if reasons else "governor_consumer_verification_failed"
    raise RuntimeError(
        "Chip hook execution requires Harness Core Governor authority "
        f"for {owner_system}:{tool_name} before running {record.key}.{hook}. "
        f"Reason: {reason_text}."
    )


def chip_hook_authority_contract(hook: str) -> tuple[str, str, MutationClass, bool]:
    normalized = str(hook or "").strip()
    if normalized.startswith("browser."):
        return normalized, "spark-browser", "external_network", True
    if normalized.startswith("voice."):
        external = normalized in {"voice.speak", "voice.transcribe"}
        return normalized, "spark-voice-comms", "external_network" if external else "read_only", external
    tool_name = normalized if normalized.startswith("chip.") else f"chip.{normalized}"
    return tool_name, "spark-intelligence-builder", "writes_files", False


def _is_disabled_legacy_browser_hook(chip_key: str, hook: str) -> bool:
    normalized_key = str(chip_key or "").strip().lower().replace("_", "-")
    normalized_hook = str(hook or "").strip().lower()
    if normalized_key in DISABLED_LEGACY_BROWSER_CHIP_KEYS:
        return True
    return any(normalized_hook.startswith(prefix) for prefix in DISABLED_LEGACY_BROWSER_HOOK_PREFIXES)


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
        "promotion_disposition": (
            "not_promotable"
            if keepability in {"ephemeral_context", "user_preference_ephemeral", "operator_debug_only"}
            else None
        ),
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
    # The chip hook output file is written by an external chip subprocess in
    # a temp dir; it can fail to read with OSError (race with cleanup,
    # permission flip) or UnicodeDecodeError (chip emitted a BOM, mojibake,
    # or binary). Treat unreadable output identically to a missing file —
    # the run is reported with exit_code/stderr already and an empty output
    # dict preserves the existing contract used by run_chip_autoloop and
    # the dispatch consumers (loops/runner._extract_best treats empty dict
    # as no metric extracted).
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
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
