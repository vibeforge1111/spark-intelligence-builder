from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


TRIGGER_PREFIXES: tuple[str, ...] = (
    "src/spark_intelligence/self_awareness/",
    "src/spark_intelligence/llm_wiki/",
    "src/spark_intelligence/adapters/telegram/",
    "ops/natural-language-live-commands.json",
    "scripts/run_live_telegram_self_awareness_wiki_probe.ps1",
)

REQUIRED_DOCS: dict[str, tuple[str, ...]] = {
    "docs/SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md": (
        "SAH-504",
        "self handoff-check",
        "self live-telegram-cadence",
    ),
    "docs/SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md": (
        "wiki heartbeat",
        "self heartbeat",
        "self live-telegram-cadence",
        "self handoff-check",
    ),
    "docs/SPARK_LLM_WIKI_ARCHITECTURE_PLAN_2026-05-01.md": (
        "wiki heartbeat",
        "self heartbeat",
        "self live-telegram-cadence",
        "self handoff-check",
    ),
}


@dataclass(frozen=True)
class HandoffFreshnessCheckResult:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload.get("summary"), dict) else {}
        lines = [
            "Spark self-awareness handoff freshness",
            f"- status: {self.payload.get('status') or 'unknown'}",
            f"- triggered_changes: {summary.get('triggered_change_count', 0)}",
            f"- docs_checked: {summary.get('doc_count', 0)}",
            f"- docs_with_missing_tokens: {summary.get('docs_with_missing_tokens', 0)}",
            f"- report_path: {self.payload.get('report_path') or 'not_written'}",
            "- authority: observability_non_authoritative",
        ]
        if self.payload.get("status") != "pass":
            for item in self.payload.get("warnings") or []:
                lines.append(f"- warning: {item}")
        return "\n".join(lines)


def build_handoff_freshness_check(
    *,
    config_manager: ConfigManager,
    changed_paths: list[str] | tuple[str, ...] | None = None,
    write_report: bool = True,
) -> HandoffFreshnessCheckResult:
    repo_root = _repo_root()
    observed_changed_paths = _normalize_paths(changed_paths) if changed_paths is not None else _git_changed_paths(repo_root)
    triggered_paths = [path for path in observed_changed_paths if _is_trigger_path(path)]
    docs_changed = [path for path in observed_changed_paths if path in REQUIRED_DOCS]
    doc_rows = [_doc_row(repo_root=repo_root, relative_path=path, required_tokens=tokens) for path, tokens in REQUIRED_DOCS.items()]
    docs_with_missing_tokens = [row for row in doc_rows if row["missing_tokens"]]
    missing_docs = [row for row in doc_rows if not row["exists"]]
    doc_update_required = bool(triggered_paths)
    doc_update_present = bool(docs_changed)
    warnings = _warnings(
        doc_update_required=doc_update_required,
        doc_update_present=doc_update_present,
        missing_docs=missing_docs,
        docs_with_missing_tokens=docs_with_missing_tokens,
    )
    status = "blocked" if warnings else "pass"
    payload = {
        "kind": "self_awareness_handoff_freshness_check",
        "checked_at": _utc_timestamp(),
        "status": status,
        "healthy": status == "pass",
        "summary": {
            "changed_path_count": len(observed_changed_paths),
            "triggered_change_count": len(triggered_paths),
            "doc_count": len(doc_rows),
            "docs_changed_count": len(docs_changed),
            "docs_with_missing_tokens": len(docs_with_missing_tokens),
        },
        "changed_paths": observed_changed_paths,
        "triggered_changed_paths": triggered_paths,
        "doc_update_required": doc_update_required,
        "doc_update_present": doc_update_present,
        "doc_status": doc_rows,
        "continuation_prompt": _continuation_prompt(repo_root),
        "authority": "observability_non_authoritative",
        "memory_policy": "typed_report_not_chat_memory",
        "promotion_gate": "major_self_awareness_or_wiki_changes_require_handoff_and_architecture_docs",
        "warnings": warnings,
    }
    if write_report:
        report_path = _report_path(config_manager=config_manager, checked_at=str(payload["checked_at"]))
        payload["report_path"] = str(report_path)
        payload["report_written"] = True
        _write_report(config_manager=config_manager, report_path=report_path, payload=payload)
    else:
        payload["report_path"] = ""
        payload["report_written"] = False
    return HandoffFreshnessCheckResult(payload=payload)


def _doc_row(*, repo_root: Path, relative_path: str, required_tokens: tuple[str, ...]) -> dict[str, Any]:
    path = repo_root / relative_path
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    missing_tokens = [token for token in required_tokens if token not in text]
    return {
        "path": relative_path,
        "exists": path.exists(),
        "required_tokens": list(required_tokens),
        "missing_tokens": missing_tokens,
        "last_modified_at": _mtime_iso(path) if path.exists() else "",
    }


def _warnings(
    *,
    doc_update_required: bool,
    doc_update_present: bool,
    missing_docs: list[dict[str, Any]],
    docs_with_missing_tokens: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    if doc_update_required and not doc_update_present:
        warnings.append("handoff_docs_not_updated_with_self_awareness_change")
    if missing_docs:
        warnings.append("handoff_required_docs_missing")
    if docs_with_missing_tokens:
        warnings.append("handoff_docs_missing_current_surface_tokens")
    return warnings


def _git_changed_paths(repo_root: Path) -> list[str]:
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if diff_result.returncode != 0 or untracked_result.returncode != 0:
        return []
    return _normalize_paths([*diff_result.stdout.splitlines(), *untracked_result.stdout.splitlines()])


def _normalize_paths(paths: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for path in paths:
        cleaned = str(path or "").strip().replace("\\", "/").lstrip("./")
        if cleaned:
            normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def _is_trigger_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in TRIGGER_PREFIXES)


def _continuation_prompt(repo_root: Path) -> str:
    handoff_path = repo_root / "docs" / "SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md"
    return "\n".join(
        [
            "Continue Spark self-awareness and LLM wiki hardening.",
            f"Repo: {repo_root}",
            f"Read first: {handoff_path}",
            "Before changing self-awareness, LLM wiki, Telegram route, or memory cognition behavior, run:",
            "python -m spark_intelligence.cli self handoff-check --json",
            "Keep wiki/supporting docs separate from live runtime truth. Update the handoff, architecture plan, and hardening task list when behavior or continuation instructions change.",
        ]
    )


def _report_path(*, config_manager: ConfigManager, checked_at: str) -> Path:
    reports_dir = config_manager.paths.home / "artifacts" / "handoff-freshness"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = checked_at.replace(":", "").replace("+", "Z")
    return reports_dir / f"{timestamp}.json"


def _write_report(*, config_manager: ConfigManager, report_path: Path, payload: dict[str, Any]) -> None:
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path = config_manager.paths.home / "artifacts" / "handoff-freshness" / "latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
