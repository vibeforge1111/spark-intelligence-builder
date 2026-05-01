from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.bootstrap import bootstrap_llm_wiki
from spark_intelligence.llm_wiki.compile_system import compile_system_wiki
from spark_intelligence.memory import hybrid_memory_retrieve
from spark_intelligence.state.db import StateDB

WIKI_PACKET_METADATA_FIELDS: tuple[str, ...] = (
    "wiki_family",
    "owner_system",
    "scope_kind",
    "source_of_truth",
    "freshness",
    "status",
    "generated_at",
    "last_verified_at",
)


@dataclass(frozen=True)
class LlmWikiQueryResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        hits = [hit for hit in self.payload.get("hits") or [] if isinstance(hit, dict)]
        lines = [
            "Spark LLM wiki query",
            f"- query: {self.payload.get('query') or ''}",
            f"- output_dir: {self.output_dir}",
            f"- retrieval: {self.payload.get('wiki_retrieval_status') or 'unknown'} ({self.payload.get('hit_count', 0)} hit(s))",
            f"- project_knowledge_first: {'yes' if self.payload.get('project_knowledge_first') else 'no'}",
            "- authority: supporting_not_authoritative",
        ]
        if self.payload.get("refreshed"):
            lines.append(f"- refreshed: yes ({self.payload.get('refreshed_file_count', 0)} generated file(s))")
        if hits:
            lines.append("Hits")
            for hit in hits[:8]:
                title = str(hit.get("title") or hit.get("source_path") or "").strip()
                source_path = str(hit.get("source_path") or "").strip()
                text = str(hit.get("text") or "").strip()
                lines.append(f"- {title}")
                if source_path:
                    lines.append(f"  source: {source_path}")
                if text:
                    lines.append(f"  {text[:220]}")
        warnings = [str(item) for item in self.payload.get("warnings") or [] if str(item).strip()]
        if warnings:
            lines.append("Warnings")
            lines.extend(f"- {item}" for item in warnings[:8])
        return "\n".join(lines)


def build_llm_wiki_query(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    query: str,
    output_dir: str | Path | None = None,
    refresh: bool = False,
    limit: int = 5,
) -> LlmWikiQueryResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    normalized_query = " ".join(str(query or "").split())
    refreshed_files: tuple[str, ...] = ()
    if refresh:
        bootstrap_llm_wiki(config_manager=config_manager, output_dir=root)
        compile_result = compile_system_wiki(config_manager=config_manager, state_db=state_db, output_dir=root)
        refreshed_files = compile_result.generated_files

    warnings: list[str] = []
    if not normalized_query:
        warnings.append("empty_query")
        return LlmWikiQueryResult(
            output_dir=root,
            payload={
                "output_dir": str(root),
                "query": normalized_query,
                "wiki_retrieval_status": "abstained",
                "hit_count": 0,
                "hits": [],
                "project_knowledge_first": False,
                "source_mix": {},
                "context_sections": [],
                "refreshed": refresh,
                "refreshed_files": list(refreshed_files),
                "refreshed_file_count": len(refreshed_files),
                "authority": "supporting_not_authoritative",
                "warnings": warnings,
            },
        )

    try:
        result = hybrid_memory_retrieve(
            config_manager=config_manager,
            state_db=state_db,
            query=normalized_query,
            limit=max(1, int(limit or 5)),
            actor_id="wiki_query",
            source_surface="wiki_query",
            record_activity=False,
        )
    except Exception as exc:
        return LlmWikiQueryResult(
            output_dir=root,
            payload={
                "output_dir": str(root),
                "query": normalized_query,
                "wiki_retrieval_status": "error",
                "hit_count": 0,
                "hits": [],
                "project_knowledge_first": False,
                "source_mix": {},
                "context_sections": [],
                "refreshed": refresh,
                "refreshed_files": list(refreshed_files),
                "refreshed_file_count": len(refreshed_files),
                "authority": "supporting_not_authoritative",
                "warnings": [f"wiki_query_failed:{exc.__class__.__name__}"],
            },
        )

    wiki_lane = next(
        (
            lane
            for lane in result.lane_summaries
            if isinstance(lane, dict) and str(lane.get("lane") or "") == "wiki_packets"
        ),
        {},
    )
    wiki_candidates = [candidate for candidate in result.candidates if candidate.lane == "wiki_packets"]
    hits = [_candidate_hit_payload(candidate) for candidate in wiki_candidates[: max(1, int(limit or 5))]]
    if not hits:
        warnings.append("no_matching_wiki_packets")
    context_packet = result.context_packet
    payload = {
        "output_dir": str(root),
        "query": normalized_query,
        "wiki_retrieval_status": str(wiki_lane.get("status") or "unknown"),
        "wiki_lane_reason": wiki_lane.get("reason"),
        "hit_count": len(hits),
        "hits": hits,
        "project_knowledge_first": bool((context_packet.trace or {}).get("project_knowledge_first")) if context_packet else False,
        "source_mix": dict(context_packet.source_mix or {}) if context_packet else {},
        "context_sections": [
            {
                "section": section.get("section"),
                "authority": section.get("authority"),
                "item_count": len(section.get("items") or []),
            }
            for section in list(context_packet.sections or []) if context_packet is not None and isinstance(section, dict)
        ],
        "refreshed": refresh,
        "refreshed_files": list(refreshed_files),
        "refreshed_file_count": len(refreshed_files),
        "authority": "supporting_not_authoritative",
        "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
        "warnings": warnings,
    }
    return LlmWikiQueryResult(output_dir=root, payload=payload)


def _candidate_hit_payload(candidate: Any) -> dict[str, Any]:
    record = candidate.record if isinstance(candidate.record, dict) else {}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    provenance = record.get("provenance") if isinstance(record.get("provenance"), list) else []
    payload = {
        "title": str(record.get("value") or record.get("summary") or "").strip(),
        "text": str(record.get("text") or "").strip(),
        "source_path": str(metadata.get("source_path") or "").strip(),
        "packet_id": str(metadata.get("packet_id") or "").strip(),
        "authority": str(metadata.get("authority") or "supporting_not_authoritative").strip(),
        "score": candidate.score,
        "selected": bool(candidate.selected),
        "reason_selected": candidate.reason_selected,
        "reason_discarded": candidate.reason_discarded,
        "provenance": provenance,
    }
    for field in WIKI_PACKET_METADATA_FIELDS:
        value = metadata.get(field)
        if value is not None and str(value).strip():
            payload[field] = str(value).strip()
    payload["metadata"] = {
        field: str(metadata.get(field) or "").strip()
        for field in ("source_path", "packet_id", "authority", *WIKI_PACKET_METADATA_FIELDS)
        if str(metadata.get(field) or "").strip()
    }
    return payload
