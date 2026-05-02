from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.scan import build_llm_wiki_candidate_scan
from spark_intelligence.llm_wiki.status import build_llm_wiki_status
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class LlmWikiHeartbeatResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload.get("summary"), dict) else {}
        lines = [
            "Spark LLM wiki heartbeat",
            f"- status: {self.payload.get('status') or 'unknown'}",
            f"- output_dir: {self.output_dir}",
            f"- stale_pages: {summary.get('stale_page_count', 0)}",
            f"- broken_links: {summary.get('broken_link_count', 0)}",
            f"- candidate_backlog: {summary.get('candidate_backlog_count', 0)}",
            f"- report_path: {self.payload.get('report_path') or 'not_written'}",
            "- authority: observability_non_authoritative",
            "- memory_policy: typed_report_not_chat_memory",
        ]
        actions = [str(item) for item in self.payload.get("recommended_actions") or [] if str(item).strip()]
        if actions:
            lines.append("Recommended actions")
            lines.extend(f"- {item}" for item in actions[:8])
        return "\n".join(lines)


def build_llm_wiki_heartbeat(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    refresh: bool = False,
    write_report: bool = True,
) -> LlmWikiHeartbeatResult:
    status = build_llm_wiki_status(
        config_manager=config_manager,
        state_db=state_db,
        output_dir=output_dir,
        refresh=refresh,
    )
    scan = build_llm_wiki_candidate_scan(
        config_manager=config_manager,
        output_dir=output_dir,
        status="all",
        limit=200,
    )
    root = status.output_dir
    markdown_files = sorted(root.rglob("*.md")) if root.exists() else []
    link_health = _link_health(root=root, markdown_files=markdown_files)
    candidate_backlog = _candidate_backlog(scan.payload)
    freshness = status.payload.get("freshness_health") if isinstance(status.payload.get("freshness_health"), dict) else {}
    summary = {
        "healthy": bool(status.payload.get("healthy")),
        "stale_page_count": int(freshness.get("stale_page_count") or 0),
        "broken_link_count": int(link_health.get("broken_link_count") or 0),
        "candidate_backlog_count": int(candidate_backlog.get("candidate_backlog_count") or 0),
        "candidate_rewrite_count": int(candidate_backlog.get("rewrite_count") or 0),
        "candidate_drop_count": int(candidate_backlog.get("drop_count") or 0),
    }
    heartbeat_status = _heartbeat_status(summary=summary, wiki_status=status.payload)
    warnings = list(status.payload.get("warnings") or []) + list(scan.payload.get("warnings") or [])
    if summary["broken_link_count"] > 0:
        warnings.append("wiki_broken_links_detected")
    if summary["candidate_backlog_count"] > 0:
        warnings.append("wiki_candidate_backlog_present")
    payload = {
        "kind": "wiki_health_heartbeat",
        "output_dir": str(root),
        "checked_at": _utc_timestamp(),
        "status": heartbeat_status,
        "healthy": heartbeat_status == "pass",
        "summary": summary,
        "wiki_status": _compact_wiki_status(status.payload),
        "link_health": link_health,
        "broken_links": link_health,
        "candidate_backlog": candidate_backlog,
        "recommended_actions": _recommended_actions(
            status=heartbeat_status,
            summary=summary,
            wiki_status=status.payload,
            link_health=link_health,
            candidate_backlog=candidate_backlog,
        ),
        "authority": "observability_non_authoritative",
        "memory_boundary": "heartbeat_reports_do_not_promote_chat_memory_or_runtime_truth",
        "memory_policy": "typed_report_not_chat_memory",
        "warnings": list(dict.fromkeys(warnings)),
    }
    if write_report:
        report_path = _report_path(config_manager=config_manager, checked_at=str(payload["checked_at"]))
        payload["report_path"] = str(report_path)
        payload["report_written"] = True
        _write_report(config_manager=config_manager, report_path=report_path, payload=payload)
    else:
        payload["report_path"] = ""
        payload["report_written"] = False
    return LlmWikiHeartbeatResult(output_dir=root, payload=payload)


def _compact_wiki_status(payload: dict[str, Any]) -> dict[str, Any]:
    freshness = payload.get("freshness_health") if isinstance(payload.get("freshness_health"), dict) else {}
    return {
        "healthy": bool(payload.get("healthy")),
        "exists": bool(payload.get("exists")),
        "wiki_retrieval_status": payload.get("wiki_retrieval_status"),
        "wiki_record_count": int(payload.get("wiki_record_count") or 0),
        "missing_bootstrap_files": list(payload.get("missing_bootstrap_files") or []),
        "missing_system_compile_files": list(payload.get("missing_system_compile_files") or []),
        "stale_pages": list(freshness.get("stale_pages") or [])[:20],
        "freshness_boundary": freshness.get("live_status_boundary"),
    }


def _link_health(*, root: Path, markdown_files: list[Path]) -> dict[str, Any]:
    broken_links: list[dict[str, str]] = []
    checked_count = 0
    for path in markdown_files:
        content = path.read_text(encoding="utf-8", errors="replace")
        for target in _local_link_targets(content):
            checked_count += 1
            if _target_exists(root=root, source_path=path, target=target):
                continue
            broken_links.append(
                {
                    "source_path": path.relative_to(root).as_posix(),
                    "target": target,
                    "reason": "missing_local_markdown_target",
                }
            )
    return {
        "checked_link_count": checked_count,
        "broken_link_count": len(broken_links),
        "broken_links": broken_links[:50],
        "authority": "local_markdown_links_only",
    }


def _local_link_targets(content: str) -> list[str]:
    targets: list[str] = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", content):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            targets.append(target)
    for match in re.finditer(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", content):
        target = match.group(1).strip()
        if _is_external_or_anchor(target):
            continue
        target = target.split("#", 1)[0].strip()
        if target:
            targets.append(unquote(target))
    return targets


def _is_external_or_anchor(target: str) -> bool:
    lowered = target.casefold()
    return (
        lowered.startswith("#")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("mailto:")
        or lowered.startswith("obsidian:")
    )


def _target_exists(*, root: Path, source_path: Path, target: str) -> bool:
    clean = target.strip().lstrip("/")
    if not clean:
        return True
    candidates: list[Path] = []
    target_path = Path(clean)
    if target_path.suffix:
        candidates.extend([root / target_path, source_path.parent / target_path])
    else:
        candidates.extend(
            [
                root / f"{clean}.md",
                root / clean / "index.md",
                source_path.parent / f"{clean}.md",
                source_path.parent / clean / "index.md",
            ]
        )
    root_resolved = root.resolve(strict=False)
    return any(_is_within_root(candidate, root_resolved) and candidate.exists() for candidate in candidates)


def _is_within_root(candidate: Path, root_resolved: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root_resolved)
    except ValueError:
        return False
    return True


def _candidate_backlog(scan_payload: dict[str, Any]) -> dict[str, Any]:
    findings = [item for item in scan_payload.get("findings") or [] if isinstance(item, dict)]
    rewrite = [item for item in findings if item.get("recommendation") == "rewrite"]
    drop = [item for item in findings if item.get("recommendation") == "drop"]
    candidates = [item for item in findings if item.get("promotion_status") == "candidate"]
    return {
        "candidate_backlog_count": len(candidates),
        "rewrite_count": len(rewrite),
        "drop_count": len(drop),
        "issue_counts": dict(scan_payload.get("issue_counts") or {}),
        "findings": findings[:50],
        "non_override_rules": list(scan_payload.get("non_override_rules") or []),
    }


def _heartbeat_status(*, summary: dict[str, int | bool], wiki_status: dict[str, Any]) -> str:
    if not bool(wiki_status.get("exists")) or not bool(wiki_status.get("healthy")):
        return "fail"
    if (
        int(summary.get("broken_link_count") or 0) > 0
        or int(summary.get("candidate_backlog_count") or 0) > 0
        or int(summary.get("candidate_drop_count") or 0) > 0
        or int(summary.get("candidate_rewrite_count") or 0) > 0
        or int(summary.get("stale_page_count") or 0) > 0
    ):
        return "warn"
    return "pass"


def _recommended_actions(
    *,
    status: str,
    summary: dict[str, int | bool],
    wiki_status: dict[str, Any],
    link_health: dict[str, Any],
    candidate_backlog: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if status == "fail":
        actions.append("Run `spark-intelligence wiki status --refresh --json` and repair missing bootstrap/system pages.")
    if int(summary.get("stale_page_count") or 0) > 0:
        actions.append("Refresh generated wiki pages or revalidate stale candidate/user notes before relying on them.")
    if int(link_health.get("broken_link_count") or 0) > 0:
        actions.append("Repair broken local Markdown or Obsidian links in the wiki vault.")
    if int(candidate_backlog.get("candidate_backlog_count") or 0) > 0:
        actions.append("Review the LLM wiki candidate backlog before treating proposed improvements as reusable knowledge.")
    if int(candidate_backlog.get("rewrite_count") or 0) > 0:
        actions.append("Review candidate scan rewrite findings before promotion.")
    if int(candidate_backlog.get("drop_count") or 0) > 0:
        actions.append("Drop or quarantine candidate notes that violate authority/runtime truth boundaries.")
    if not actions and wiki_status.get("healthy"):
        actions.append("No maintenance action needed; keep heartbeat reports as observability, not memory truth.")
    return actions


def _report_path(*, config_manager: ConfigManager, checked_at: str) -> Path:
    reports_dir = config_manager.paths.home / "artifacts" / "wiki-heartbeat"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = checked_at.replace(":", "").replace("+", "Z")
    return reports_dir / f"{timestamp}.json"


def _write_report(*, config_manager: ConfigManager, report_path: Path, payload: dict[str, Any]) -> None:
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path = config_manager.paths.home / "artifacts" / "wiki-heartbeat" / "latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
