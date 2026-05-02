from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.query import build_llm_wiki_query
from spark_intelligence.self_awareness import build_self_awareness_capsule
from spark_intelligence.state.db import StateDB

WIKI_SOURCE_METADATA_FIELDS: tuple[str, ...] = (
    "wiki_family",
    "owner_system",
    "scope_kind",
    "source_of_truth",
    "freshness",
    "status",
    "generated_at",
    "last_verified_at",
)

CURRENT_FACT_MARKERS: tuple[str, ...] = (
    "current",
    "now",
    "today",
    "active",
    "healthy",
    "working",
    "live",
    "available",
    "latest",
    "recent",
)

RESEARCH_FACT_MARKERS: tuple[str, ...] = (
    "web",
    "internet",
    "external",
    "market",
    "price",
    "law",
    "legal",
    "medical",
    "financial",
    "regulation",
    "news",
    "competitor",
    "benchmark",
    "sota",
    "research",
)

HIGH_STAKES_MARKERS: tuple[str, ...] = (
    "architecture",
    "money",
    "safety",
    "infra",
    "infrastructure",
    "production",
    "deploy",
    "secret",
    "secrets",
    "credential",
    "token",
    "auth",
    "provider",
    "gateway",
    "telegram",
    "bot",
    "channel",
    "delete",
    "data loss",
    "commitment",
    "promise",
)

REVALIDATABLE_FRESHNESS: tuple[str, ...] = (
    "",
    "unknown",
    "bootstrap_static",
    "revalidatable_improvement_note",
)


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
        policy = self.payload.get("deep_search_policy") if isinstance(self.payload.get("deep_search_policy"), dict) else {}
        if policy and policy.get("status") != "wiki_sufficient_supporting_context":
            lines.extend(["", "Deep search / probe policy"])
            lines.append(f"- status: {policy.get('status') or 'unknown'}")
            for action in [str(item) for item in policy.get("recommended_actions") or [] if str(item).strip()][:6]:
                lines.append(f"- {action}")
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
    include_live_self: bool | None = None,
    human_id: str = "",
    session_id: str = "",
    channel_kind: str = "",
    request_id: str | None = None,
    user_message: str = "",
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
    deep_search_policy = _deep_search_policy(
        question=normalized_question,
        hits=hits,
        sources=sources,
        missing_live_verification=missing_live_verification,
        warnings=warnings,
    )
    if deep_search_policy["status"] != "wiki_sufficient_supporting_context":
        warnings.append("deep_search_or_probe_required")
    warnings = list(dict.fromkeys(warnings))
    should_attach_live_self = _should_include_live_self(normalized_question) if include_live_self is None else include_live_self
    live_self_awareness = (
        _build_live_self_context(
            config_manager=config_manager,
            state_db=state_db,
            question=normalized_question,
            human_id=human_id,
            session_id=session_id,
            channel_kind=channel_kind,
            request_id=request_id,
            user_message=user_message,
        )
        if should_attach_live_self
        else {}
    )
    evidence_level = _evidence_level(hits=hits, live_self_awareness=live_self_awareness)
    answer = _compose_answer(
        question=normalized_question,
        hits=hits,
        missing_live_verification=missing_live_verification,
        live_self_awareness=live_self_awareness,
    )
    payload = {
        "output_dir": query_payload.get("output_dir") or str(query_result.output_dir),
        "question": normalized_question,
        "answer": answer,
        "evidence_level": evidence_level,
        "wiki_retrieval_status": query_payload.get("wiki_retrieval_status"),
        "hit_count": len(hits),
        "sources": sources,
        "missing_live_verification": missing_live_verification,
        "deep_search_policy": deep_search_policy,
        "project_knowledge_first": bool(query_payload.get("project_knowledge_first")),
        "source_mix": dict(query_payload.get("source_mix") or {}),
        "context_sections": list(query_payload.get("context_sections") or []),
        "query_payload": query_payload,
        "live_context_status": "included" if live_self_awareness else ("not_requested" if not should_attach_live_self else "unavailable"),
        "live_self_awareness": live_self_awareness,
        "authority": "supporting_not_authoritative",
        "warnings": warnings,
    }
    return LlmWikiAnswerResult(output_dir=query_result.output_dir, payload=payload)


def _compose_answer(
    *,
    question: str,
    hits: list[dict[str, Any]],
    missing_live_verification: list[str],
    live_self_awareness: dict[str, Any],
) -> str:
    if not hits:
        answer = (
            "I could not find a matching LLM wiki packet for that question. "
            "I should either query with a narrower phrase, inspect live runtime state, or create a source-backed wiki page after verification."
        )
        live_sentence = _live_self_sentence(live_self_awareness)
        return f"{answer} {live_sentence}".strip() if live_sentence else answer
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
    live_sentence = _live_self_sentence(live_self_awareness)
    if live_sentence:
        lead = f"{lead} {live_sentence}"
    if missing_live_verification:
        lead = (
            f"{lead} For anything mutable in this answer, Spark should run the named live probe or status route before claiming it is true now."
        )
    return lead


def _evidence_level(*, hits: list[dict[str, Any]], live_self_awareness: dict[str, Any]) -> str:
    if hits and live_self_awareness:
        return "wiki_backed_with_live_self_snapshot"
    if hits:
        return "wiki_backed_supporting_context"
    if live_self_awareness:
        return "live_self_snapshot_without_wiki_hit"
    return "wiki_not_found"


def _build_live_self_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    question: str,
    human_id: str,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    user_message: str,
) -> dict[str, Any]:
    capsule = build_self_awareness_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message or question,
    ).to_payload()
    return {
        "generated_at": capsule.get("generated_at"),
        "workspace_id": capsule.get("workspace_id"),
        "memory_cognition": capsule.get("memory_cognition") or {},
        "observed_now": _claim_summaries(capsule.get("observed_now"), limit=4),
        "recently_verified": _claim_summaries(capsule.get("recently_verified"), limit=3),
        "degraded_or_missing": _claim_summaries(capsule.get("degraded_or_missing"), limit=4),
        "lacks": _claim_summaries(capsule.get("lacks"), limit=4),
        "improvement_options": _claim_summaries(capsule.get("improvement_options"), limit=4),
        "recommended_probes": [str(item) for item in capsule.get("recommended_probes") or [] if str(item).strip()][:4],
        "source_ledger": [
            item for item in capsule.get("source_ledger") or [] if isinstance(item, dict)
        ][:4],
        "authority": "current_snapshot_not_proof_of_invocation",
    }


def _claim_summaries(value: object, *, limit: int) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []
    for item in (value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        claims.append(
            {
                "claim": claim,
                "source": str(item.get("source") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
                "verification_status": str(item.get("verification_status") or "").strip(),
                "next_probe": str(item.get("next_probe") or "").strip(),
            }
        )
    return claims[:limit]


def _live_self_sentence(live_self_awareness: dict[str, Any]) -> str:
    if not live_self_awareness:
        return ""
    observed = _claims_text(live_self_awareness.get("observed_now"), limit=2)
    lacks = _claims_text(live_self_awareness.get("lacks"), limit=2)
    improvements = _claims_text(live_self_awareness.get("improvement_options"), limit=1)
    parts = []
    if observed:
        parts.append(f"Live self snapshot sees: {'; '.join(observed)}.")
    if lacks:
        parts.append(f"It still lacks: {'; '.join(lacks)}.")
    if improvements:
        parts.append(f"Best next improvement: {improvements[0]}.")
    if not parts:
        parts.append("Live self snapshot is attached, but it did not expose compact claim text.")
    return " ".join(parts)


def _claims_text(value: object, *, limit: int) -> list[str]:
    texts: list[str] = []
    for item in (value if isinstance(value, list) else []):
        if isinstance(item, dict):
            text = str(item.get("claim") or "").strip()
        else:
            text = str(item).strip()
        if text:
            texts.append(text)
    return texts[:limit]


def _should_include_live_self(question: str) -> bool:
    lowered = question.casefold()
    live_markers = (
        "current",
        "now",
        "today",
        "active",
        "healthy",
        "working",
        "live",
        "available",
        "self-aware",
        "self awareness",
        "introspect",
        "introspection",
        "what can",
        "can you",
        "capability",
        "capabilities",
        "lack",
        "lacks",
        "missing",
        "gap",
        "gaps",
        "improve",
        "improvement",
        "system",
        "systems",
        "tool",
        "tools",
        "chip",
        "chips",
        "route",
        "routes",
        "provider",
        "gateway",
        "telegram",
        "bot",
        "memory",
        "wiki",
        "knowledge base",
        "kb",
    )
    return any(marker in lowered for marker in live_markers)


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
    payload = {
        "title": str(hit.get("title") or "").strip(),
        "source_path": str(hit.get("source_path") or "").strip(),
        "authority": str(hit.get("authority") or "supporting_not_authoritative").strip(),
        "score": hit.get("score"),
        "selected": bool(hit.get("selected")),
    }
    for field in WIKI_SOURCE_METADATA_FIELDS:
        value = hit.get(field)
        if value is not None and str(value).strip():
            payload[field] = str(value).strip()
    return payload


def _deep_search_policy(
    *,
    question: str,
    hits: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    missing_live_verification: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    lowered = question.casefold()
    is_current_fact = _has_any_marker(lowered, CURRENT_FACT_MARKERS)
    needs_external_research = _has_any_marker(lowered, RESEARCH_FACT_MARKERS)
    high_stakes = _has_any_marker(lowered, HIGH_STAKES_MARKERS)
    revalidatable_sources = _revalidatable_source_paths(sources)
    triggers: list[str] = []
    recommended_actions: list[str] = []

    if not hits or "no_matching_wiki_packets" in warnings:
        triggers.append("under_sourced_wiki")
        recommended_actions.append(
            "Use Researcher/browser/search or a narrower source-backed wiki query before answering confidently."
        )
    if missing_live_verification:
        triggers.append("live_verification_needed")
        recommended_actions.extend(missing_live_verification)
    if is_current_fact:
        triggers.append("current_or_mutable_fact")
    if needs_external_research:
        triggers.append("external_or_research_fact")
        recommended_actions.append(
            "Use a live research route for external, SOTA, legal, medical, financial, market, or news claims."
        )
    if high_stakes:
        triggers.append("high_stakes_or_operational_claim")
        recommended_actions.append(
            "Run the relevant live diagnostic/probe before making architecture, infra, auth, provider, channel, safety, money, or commitment claims."
        )
    if revalidatable_sources and (is_current_fact or high_stakes or needs_external_research):
        triggers.append("revalidatable_or_static_sources")
        recommended_actions.append(
            "Treat stale/static wiki pages as orientation only until newer traces, tests, or live status confirm them."
        )

    should_probe = bool(missing_live_verification or (is_current_fact and (high_stakes or revalidatable_sources)))
    should_deep_search = bool(
        needs_external_research or high_stakes or not hits or ("no_matching_wiki_packets" in warnings)
    )

    if should_probe and should_deep_search:
        status = "live_probe_and_deep_search_required"
    elif should_probe:
        status = "live_probe_required"
    elif should_deep_search:
        status = "deep_search_required"
    else:
        status = "wiki_sufficient_supporting_context"

    return {
        "status": status,
        "should_probe": should_probe,
        "should_deep_search": should_deep_search,
        "triggers": list(dict.fromkeys(triggers)),
        "recommended_actions": list(dict.fromkeys(recommended_actions)),
        "revalidatable_source_paths": revalidatable_sources,
        "authority_boundary": (
            "wiki_is_supporting_not_authoritative; live traces, tests, governed memory, "
            "and current probes outrank wiki for mutable facts"
        ),
    }


def _has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if " " in marker:
            if marker in text:
                return True
            continue
        if re.search(rf"(?<![a-z0-9_]){re.escape(marker)}(?![a-z0-9_])", text):
            return True
    return False


def _revalidatable_source_paths(sources: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for source in sources:
        freshness = str(source.get("freshness") or "").strip().casefold()
        last_verified_at = str(source.get("last_verified_at") or "").strip()
        generated_at = str(source.get("generated_at") or "").strip()
        if freshness in REVALIDATABLE_FRESHNESS or (not last_verified_at and not generated_at):
            path = str(source.get("source_path") or source.get("title") or "").strip()
            if path:
                paths.append(path)
    return paths[:6]


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
