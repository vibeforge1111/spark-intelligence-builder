from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.bootstrap import bootstrap_llm_wiki
from spark_intelligence.llm_wiki.compile_system import compile_system_wiki
from spark_intelligence.memory import hybrid_memory_retrieve, inspect_wiki_packet_metadata
from spark_intelligence.state.db import StateDB


BOOTSTRAP_WIKI_FILES: tuple[str, ...] = (
    "index.md",
    "system/spark-self-awareness-contract.md",
    "system/spark-system-map.md",
    "system/natural-language-route-map.md",
    "system/tracing-and-observability-map.md",
    "system/recursive-self-improvement-loops.md",
    "memory/llm-wiki-memory-policy.md",
    "tools/tool-and-chip-inventory-contract.md",
    "user/user-environment-profile-template.md",
)

SYSTEM_COMPILE_WIKI_FILES: tuple[str, ...] = (
    "system/current-system-status.md",
    "tools/capability-index.md",
    "routes/live-route-index.md",
    "diagnostics/self-awareness-gaps.md",
)


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
            f"- memory_kb: {'yes' if (self.payload.get('memory_kb_discovery') or {}).get('present') else 'no'} "
            f"({(self.payload.get('memory_kb_discovery') or {}).get('packet_count', 0)} packet(s))",
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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
