from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.bootstrap import bootstrap_llm_wiki
from spark_intelligence.llm_wiki.compile_system import compile_system_wiki
from spark_intelligence.memory import hybrid_memory_retrieve, inspect_wiki_packet_metadata
from spark_intelligence.state.db import StateDB


BOOTSTRAP_WIKI_FILES: tuple[str, ...] = (
    "index.md",
    "system/index.md",
    "system/spark-self-awareness-contract.md",
    "system/spark-system-map.md",
    "system/natural-language-route-map.md",
    "system/tracing-and-observability-map.md",
    "system/recursive-self-improvement-loops.md",
    "routes/index.md",
    "memory/llm-wiki-memory-policy.md",
    "tools/index.md",
    "tools/tool-and-chip-inventory-contract.md",
    "user/index.md",
    "user/user-environment-profile-template.md",
    "projects/index.md",
    "improvements/index.md",
)

SYSTEM_COMPILE_WIKI_FILES: tuple[str, ...] = (
    "system/current-system-status.md",
    "environment/spark-environment.md",
    "tools/capability-index.md",
    "routes/live-route-index.md",
    "diagnostics/self-awareness-gaps.md",
)

FRESHNESS_THRESHOLDS_DAYS: dict[str, int] = {
    "live_compile_snapshot": 2,
    "snapshot_generated": 7,
    "revalidatable_improvement_note": 30,
    "revalidatable_user_context": 30,
    "bootstrap_static": 180,
    "unknown": 90,
}


@dataclass(frozen=True)
class LlmWikiStatusResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = [
            "Spark LLM wiki status",
            f"- output_dir: {self.output_dir}",
            f"- exists: {'yes' if self.payload.get('exists') else 'no'}",
            f"- valid: {'yes' if self.payload.get('valid') else 'no'}",
            f"- markdown_pages: {self.payload.get('markdown_page_count', 0)}",
            f"- missing_bootstrap_pages: {len(self.payload.get('missing_bootstrap_files') or [])}",
            f"- missing_system_pages: {len(self.payload.get('missing_system_compile_files') or [])}",
            f"- retrieval: {self.payload.get('wiki_retrieval_status') or 'unknown'} ({self.payload.get('wiki_record_count', 0)} hit(s))",
            f"- project_knowledge_first: {'yes' if self.payload.get('project_knowledge_first') else 'no'}",
            f"- stale_pages: {(self.payload.get('freshness_health') or {}).get('stale_page_count', 0)}",
            f"- memory_kb: {'yes' if (self.payload.get('memory_kb_discovery') or {}).get('present') else 'no'} "
            f"({(self.payload.get('memory_kb_discovery') or {}).get('packet_count', 0)} packet(s))",
            f"- authority: {self.payload.get('authority') or 'supporting_not_authoritative'}",
        ]
        if self.payload.get("refreshed"):
            lines.append(f"- refreshed: yes ({self.payload.get('refreshed_file_count', 0)} generated file(s))")
        warnings = [str(item) for item in self.payload.get("warnings") or [] if str(item).strip()]
        if warnings:
            lines.append("Warnings")
            lines.extend(f"- {item}" for item in warnings[:8])
        return "\n".join(lines)


def build_llm_wiki_status(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    refresh: bool = False,
) -> LlmWikiStatusResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    refreshed_files: tuple[str, ...] = ()
    if refresh:
        bootstrap_llm_wiki(config_manager=config_manager, output_dir=root)
        compile_result = compile_system_wiki(config_manager=config_manager, state_db=state_db, output_dir=root)
        refreshed_files = compile_result.generated_files

    exists = root.exists()
    markdown_files = sorted(path for path in root.rglob("*.md") if path.is_file()) if exists else []
    missing_bootstrap = _missing_files(root, BOOTSTRAP_WIKI_FILES)
    missing_system = _missing_files(root, SYSTEM_COMPILE_WIKI_FILES)
    wiki_status, wiki_records, project_knowledge_first, source_mix = _wiki_retrieval_probe(
        config_manager=config_manager,
        state_db=state_db,
    )
    wiki_packet_metadata = inspect_wiki_packet_metadata(config_manager=config_manager)
    memory_kb_discovery = dict(wiki_packet_metadata.get("memory_kb") or {})
    freshness_health = _freshness_health(root=root, markdown_files=markdown_files)
    healthy = exists and not missing_bootstrap and not missing_system and wiki_status == "supported" and wiki_records > 0
    warnings: list[str] = []
    if not exists:
        warnings.append("wiki_root_missing")
    if missing_bootstrap:
        warnings.append("bootstrap_pages_missing")
    if missing_system:
        warnings.append("system_compile_pages_missing")
    if wiki_status != "supported":
        warnings.append("wiki_packet_retrieval_not_supported")
    if wiki_status == "supported" and wiki_records <= 0:
        warnings.append("wiki_packet_retrieval_returned_no_hits")
    if freshness_health.get("stale_page_count", 0) > 0:
        warnings.append("wiki_stale_pages_detected")
    if freshness_health.get("stale_candidate_count", 0) > 0:
        warnings.append("wiki_old_candidates_need_review")
    payload = {
        "output_dir": str(root),
        "checked_at": _utc_timestamp(),
        "exists": exists,
        "healthy": healthy,
        "valid": healthy,
        "markdown_page_count": len(markdown_files),
        "newest_markdown_modified_at": _newest_modified_at(markdown_files),
        "expected_bootstrap_files": list(BOOTSTRAP_WIKI_FILES),
        "expected_system_compile_files": list(SYSTEM_COMPILE_WIKI_FILES),
        "missing_bootstrap_files": list(missing_bootstrap),
        "missing_system_compile_files": list(missing_system),
        "wiki_retrieval_status": wiki_status,
        "wiki_record_count": wiki_records,
        "project_knowledge_first": project_knowledge_first,
        "source_mix": source_mix,
        "wiki_packet_metadata": wiki_packet_metadata,
        "memory_kb_discovery": memory_kb_discovery,
        "freshness_health": freshness_health,
        "refreshed": refresh,
        "refreshed_files": list(refreshed_files),
        "refreshed_file_count": len(refreshed_files),
        "authority": "supporting_not_authoritative",
        "warnings": warnings,
    }
    return LlmWikiStatusResult(output_dir=root, payload=payload)


def _missing_files(root: Path, relative_paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(relative_path for relative_path in relative_paths if not (root / relative_path).exists())


def _wiki_retrieval_probe(*, config_manager: ConfigManager, state_db: StateDB) -> tuple[str, int, bool, dict[str, int]]:
    try:
        result = hybrid_memory_retrieve(
            config_manager=config_manager,
            state_db=state_db,
            query="Spark self-awareness system map capabilities routes LLM wiki",
            limit=3,
            actor_id="wiki_status",
            source_surface="wiki_status_probe",
            record_activity=False,
        )
    except Exception:
        return "error", 0, False, {}
    wiki_lane = next(
        (
            lane
            for lane in result.lane_summaries
            if isinstance(lane, dict) and str(lane.get("lane") or "") == "wiki_packets"
        ),
        {},
    )
    return (
        str(wiki_lane.get("status") or "unknown"),
        int(wiki_lane.get("record_count") or 0),
        bool((result.context_packet.trace or {}).get("project_knowledge_first")),
        dict(result.context_packet.source_mix or {}),
    )


def _newest_modified_at(paths: list[Path]) -> str | None:
    if not paths:
        return None
    newest = max(path.stat().st_mtime for path in paths)
    return datetime.fromtimestamp(newest, timezone.utc).replace(microsecond=0).isoformat()


def _freshness_health(*, root: Path, markdown_files: list[Path]) -> dict[str, Any]:
    page_records = [_freshness_page_record(root=root, path=path) for path in markdown_files]
    stale_pages = [page for page in page_records if page.get("stale")]
    return {
        "thresholds_days": dict(FRESHNESS_THRESHOLDS_DAYS),
        "page_count": len(page_records),
        "stale_page_count": len(stale_pages),
        "stale_candidate_count": sum(
            1
            for page in stale_pages
            if str(page.get("promotion_status") or page.get("status") or "").strip() == "candidate"
        ),
        "oldest_page_age_days": max((int(page.get("age_days") or 0) for page in page_records), default=0),
        "page_counts_by_freshness": _counts_by_key(page_records, "freshness"),
        "stale_pages": stale_pages[:20],
        "authority": "supporting_not_authoritative",
        "live_status_boundary": "stale wiki pages warn; live traces, tests, and current-state memory still outrank wiki",
    }


def _freshness_page_record(*, root: Path, path: Path) -> dict[str, Any]:
    relative_path = path.relative_to(root).as_posix()
    content = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _frontmatter(content)
    freshness = str(frontmatter.get("freshness") or "unknown").strip() or "unknown"
    observed_at = _observed_at(frontmatter=frontmatter, path=path)
    age_days = max(0, (datetime.now(timezone.utc) - observed_at).days)
    threshold_days = FRESHNESS_THRESHOLDS_DAYS.get(freshness, FRESHNESS_THRESHOLDS_DAYS["unknown"])
    stale = age_days > threshold_days
    return {
        "path": relative_path,
        "title": str(frontmatter.get("title") or _heading_title(content) or path.stem).strip(),
        "type": str(frontmatter.get("type") or "").strip(),
        "status": str(frontmatter.get("status") or "").strip(),
        "promotion_status": str(frontmatter.get("promotion_status") or "").strip(),
        "authority": str(frontmatter.get("authority") or "supporting_not_authoritative").strip(),
        "freshness": freshness,
        "observed_at": observed_at.replace(microsecond=0).isoformat(),
        "age_days": age_days,
        "threshold_days": threshold_days,
        "stale": stale,
        "invalidation_trigger": _section_bullet(content, "Invalidation Trigger"),
    }


def _frontmatter(content: str) -> dict[str, Any]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    block: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block.append(line)
    try:
        parsed = yaml.safe_load("\n".join(block)) or {}
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _observed_at(*, frontmatter: dict[str, Any], path: Path) -> datetime:
    for key in ("last_verified_at", "generated_at", "date_modified", "date_created"):
        parsed = _parse_datetime(frontmatter.get(key))
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _heading_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _section_bullet(content: str, heading: str) -> str:
    lines = content.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"## {heading}":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            return stripped[2:].strip()
    return ""


def _counts_by_key(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(key) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
