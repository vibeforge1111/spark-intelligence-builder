from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.execution import run_governed_command


DEFAULT_VALIDATOR_ROOT = Path.home() / "Desktop" / "domain-chip-memory"
DEFAULT_BUILDER_KB_REPO_SOURCE_MANIFEST = (
    Path(__file__).resolve().parents[3] / "docs" / "manifests" / "spark_memory_kb_repo_sources.json"
)


@dataclass(frozen=True)
class TelegramStateKnowledgeBaseResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = ["Spark memory Telegram KB compile"]
        lines.append(f"- builder_home: {self.payload.get('builder_home')}")
        lines.append(f"- output_dir: {self.output_dir}")
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        if isinstance(summary, dict):
            selected_chat_id = summary.get("selected_chat_id")
            if selected_chat_id:
                lines.append(f"- selected_chat_id: {selected_chat_id}")
            lines.append(f"- conversations: {summary.get('conversation_count', 0)}")
            lines.append(f"- accepted_writes: {summary.get('accepted_writes', 0)}")
            lines.append(f"- rejected_writes: {summary.get('rejected_writes', 0)}")
            lines.append(f"- skipped_turns: {summary.get('skipped_turns', 0)}")
            lines.append(f"- kb_valid: {'yes' if summary.get('kb_valid') else 'no'}")
        health_report = self.payload.get("health_report") if isinstance(self.payload, dict) else None
        if isinstance(health_report, dict):
            lines.append(f"- health_valid: {'yes' if health_report.get('valid') else 'no'}")
            lines.append(f"- health_errors: {len(health_report.get('errors') or [])}")
        errors = self.payload.get("errors") if isinstance(self.payload, dict) else None
        if errors:
            lines.append(f"- errors: {len(errors)}")
        return "\n".join(lines)


def build_telegram_state_knowledge_base(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    limit: int = 25,
    chat_id: str | None = None,
    repo_sources: list[str] | None = None,
    repo_source_manifest_files: list[str] | None = None,
    write_path: str | Path | None = None,
    validator_root: str | Path | None = None,
) -> TelegramStateKnowledgeBaseResult:
    resolved_output_dir = Path(output_dir) if output_dir else _default_output_dir(config_manager)
    resolved_repo_sources, resolved_repo_source_manifest_files = _resolve_repo_source_inputs(
        repo_sources=repo_sources,
        repo_source_manifest_files=repo_source_manifest_files,
    )
    command_args = [
        str(config_manager.paths.home),
        str(resolved_output_dir),
        "--limit",
        str(max(int(limit), 1)),
    ]
    if chat_id:
        command_args.extend(["--chat-id", str(chat_id)])
    for repo_source in resolved_repo_sources:
        command_args.extend(["--repo-source", str(repo_source)])
    for manifest in resolved_repo_source_manifest_files:
        command_args.extend(["--repo-source-manifest", str(manifest)])
    if write_path:
        command_args.extend(["--write", str(Path(write_path))])
    payload = _run_domain_chip_memory_cli(
        "run-spark-builder-state-telegram-intake",
        *command_args,
        validator_root=validator_root,
    )
    return TelegramStateKnowledgeBaseResult(output_dir=resolved_output_dir, payload=payload)


def _run_domain_chip_memory_cli(
    command_name: str,
    *command_args: str,
    validator_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(validator_root) if validator_root else DEFAULT_VALIDATOR_ROOT
    if not root.exists():
        return {
            "valid": False,
            "errors": [f"validator_root_missing:{root}"],
            "warnings": [],
        }
    execution = run_governed_command(
        command=[
            sys.executable,
            "-m",
            "domain_chip_memory.cli",
            command_name,
            *command_args,
        ],
        cwd=str(root),
    )
    stdout = execution.stdout.strip()
    parsed: dict[str, Any] | None = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            parsed = payload
    if parsed is not None:
        parsed.setdefault("stderr", execution.stderr.strip())
        return parsed
    return {
        "valid": execution.exit_code == 0,
        "errors": [] if execution.exit_code == 0 else [execution.stderr.strip() or stdout or "kb_compile_failed"],
        "warnings": [],
        "stdout": stdout,
        "stderr": execution.stderr.strip(),
    }


def _default_output_dir(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "spark-memory-kb"


def _resolve_repo_source_inputs(
    *,
    repo_sources: list[str] | None,
    repo_source_manifest_files: list[str] | None,
) -> tuple[list[str], list[str]]:
    resolved_repo_sources = [str(item) for item in (repo_sources or []) if str(item).strip()]
    resolved_repo_source_manifest_files = [
        str(item) for item in (repo_source_manifest_files or []) if str(item).strip()
    ]
    if resolved_repo_sources or resolved_repo_source_manifest_files:
        return resolved_repo_sources, resolved_repo_source_manifest_files
    if DEFAULT_BUILDER_KB_REPO_SOURCE_MANIFEST.exists():
        resolved_repo_source_manifest_files.append(str(DEFAULT_BUILDER_KB_REPO_SOURCE_MANIFEST))
    return resolved_repo_sources, resolved_repo_source_manifest_files
