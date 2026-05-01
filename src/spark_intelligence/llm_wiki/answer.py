from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.query import build_llm_wiki_query
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class LlmWikiAnswerResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = [
            "Spark LLM wiki answer",
            f"- question: {self.payload.get('question') or ''}",
            f"- evidence_level: {self.payload.get('evidence_level') or 'unknown'}",
            f"- retrieval: {self.payload.get('wiki_retrieval_status') or 'unknown'} ({self.payload.get('hit_count', 0)} hit(s))",
            "",
            str(self.payload.get("answer") or "").strip(),
        ]
        sources = [source for source in self.payload.get("sources") or [] if isinstance(source, dict)]
        if sources:
            lines.extend(["", "Sources"])
            for source in sources[:6]:
                title = str(source.get("title") or "").strip()
                path = str(source.get("source_path") or "").strip()
                lines.append(f"- {title}: {path}" if path else f"- {title}")
        missing = [str(item) for item in self.payload.get("missing_live_verification") or [] if str(item).strip()]
        if missing:
            lines.extend(["", "Still needs live verification"])
            lines.extend(f"- {item}" for item in missing[:6])
        warnings = [str(item) for item in self.payload.get("warnings") or [] if str(item).strip()]
        if warnings:
            lines.extend(["", "Warnings"])
            lines.extend(f"- {item}" for item in warnings[:6])
        return "\n".join(line for line in lines if line is not None).strip()


def build_llm_wiki_answer(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    question: str,
    output_dir: str | Path | None = None,
    refresh: bool = False,
    limit: int = 5,
) -> LlmWikiAnswerResult:
    query_result = build_llm_wiki_query(
        config_manager=config_manager,
        state_db=state_db,
        query=question,
        output_dir=output_dir,
        refresh=refresh,
        limit=limit,
    )
    query_payload = query_result.payload
    hits = [hit for hit in query_payload.get("hits") or [] if isinstance(hit, dict)]
    normalized_question = str(query_payload.get("query") or question or "").strip()
    sources = [_source_payload(hit) for hit in hits]
    missing_live_verification = _missing_live_verification(normalized_question)
    warnings = [str(item) for item in query_payload.get("warnings") or [] if str(item).strip()]
    evidence_level = "wiki_backed_supporting_context" if hits else "wiki_not_found"
    answer = _compose_answer(question=normalized_question, hits=hits, missing_live_verification=missing_live_verification)
    payload = {
        "output_dir": query_payload.get("output_dir") or str(query_result.output_dir),
        "question": normalized_question,
        "answer": answer,
        "evidence_level": evidence_level,
        "wiki_retrieval_status": query_payload.get("wiki_retrieval_status"),
        "hit_count": len(hits),
        "sources": sources,
        "missing_live_verification": missing_live_verification,
        "project_knowledge_first": bool(query_payload.get("project_knowledge_first")),
        "source_mix": dict(query_payload.get("source_mix") or {}),
        "context_sections": list(query_payload.get("context_sections") or []),
        "query_payload": query_payload,
        "authority": "supporting_not_authoritative",
        "warnings": warnings,
    }
    return LlmWikiAnswerResult(output_dir=query_result.output_dir, payload=payload)


def _compose_answer(*, question: str, hits: list[dict[str, Any]], missing_live_verification: list[str]) -> str:
    if not hits:
        return (
            "I could not find a matching LLM wiki packet for that question. "
            "I should either query with a narrower phrase, inspect live runtime state, or create a source-backed wiki page after verification."
        )
    lead = (
        "From the LLM wiki, the best-supported answer is: "
        "use the retrieved pages as operating context, not as current runtime truth."
    )
    snippets = []
    for hit in hits[:3]:
        title = str(hit.get("title") or "wiki packet").strip()
        snippet = _first_useful_sentence(str(hit.get("text") or ""))
        if snippet:
            snippets.append(f"{title}: {snippet}")
    if snippets:
        lead = f"{lead} " + " ".join(snippets)
    if missing_live_verification:
        lead = (
            f"{lead} For anything mutable in this answer, Spark should run the named live probe or status route before claiming it is true now."
        )
    return lead


def _first_useful_sentence(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    compact = re.sub(r"^#+\s*", "", compact)
    if not compact:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", compact)
    for part in parts:
        cleaned = part.strip(" -")
        if len(cleaned) >= 40:
            return cleaned[:360]
    return compact[:360]


def _source_payload(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(hit.get("title") or "").strip(),
        "source_path": str(hit.get("source_path") or "").strip(),
        "authority": str(hit.get("authority") or "supporting_not_authoritative").strip(),
        "score": hit.get("score"),
        "selected": bool(hit.get("selected")),
    }


def _missing_live_verification(question: str) -> list[str]:
    lowered = question.casefold()
    checks: list[str] = []
    if any(token in lowered for token in ("current", "now", "today", "active", "healthy", "working", "live", "available")):
        checks.append("Run `spark-intelligence self status --refresh-wiki --json` or the route-specific status command for current truth.")
    if any(token in lowered for token in ("provider", "gateway", "telegram", "bot", "channel")):
        checks.append("Run gateway/channel diagnostics before claiming provider or Telegram health.")
    if any(token in lowered for token in ("tool", "chip", "route", "capability", "can you", "what can")):
        checks.append("Check recent route/tool success evidence before claiming the capability worked this turn.")
    if any(token in lowered for token in ("memory", "wiki", "knowledge base", "kb")):
        checks.append("Check wiki and memory retrieval health when the answer depends on stored project knowledge.")
    return checks
