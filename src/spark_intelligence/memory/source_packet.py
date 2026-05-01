from __future__ import annotations

from typing import Any

from spark_intelligence.memory.orchestrator import HybridMemoryRetrievalResult


AUTHORITY_SOURCE_CLASSES = {"current_state", "entity_state", "historical_state"}
SOFT_SOURCE_CLASSES = {"recent_conversation", "raw_episode", "structured_evidence", "belief_candidate"}
ADVISORY_SOURCE_CLASSES = {"graphiti_temporal_graph", "obsidian_llm_wiki_packets", "procedural_lesson", "pending_task"}


def build_memory_answer_source_packet(result: HybridMemoryRetrievalResult) -> dict[str, Any]:
    """Build a compact explanation of which memory source would support an answer."""

    context_packet = result.context_packet.to_payload() if result.context_packet is not None else {}
    selected = [candidate for candidate in result.candidates if candidate.selected]
    discarded = [candidate for candidate in result.candidates if not candidate.selected]
    source_mix = context_packet.get("source_mix") if isinstance(context_packet.get("source_mix"), dict) else {}
    promotion_gates = _promotion_gates(context_packet)
    dominant_source = _dominant_source(source_mix)
    selected_sources = [_candidate_source(candidate) for candidate in selected[:8]]
    ignored_sources = [_candidate_source(candidate) for candidate in discarded[:8]]
    stale_ignored = [
        source
        for source in ignored_sources
        if "stale" in str(source.get("reason") or "").lower()
        or "superseded" in str(source.get("reason") or "").lower()
    ]
    return {
        "view": "memory_answer_source_packet",
        "query": result.query,
        "subject": result.subject,
        "predicate": result.predicate,
        "entity_key": result.entity_key,
        "retrieval_method": result.read_result.method,
        "status": result.read_result.status,
        "abstained": result.read_result.abstained,
        "reason": result.read_result.reason,
        "source_class": dominant_source or "none",
        "source_authority": _source_authority(dominant_source),
        "source_mix": source_mix,
        "selected_count": len(selected),
        "candidate_count": len(result.candidates),
        "confidence": _confidence(selected=selected, source_mix=source_mix, promotion_gates=promotion_gates),
        "why_source_won": _why_source_won(
            dominant_source=dominant_source,
            selected=selected,
            source_mix=source_mix,
            promotion_gates=promotion_gates,
        ),
        "stale_current_status": _gate_status(promotion_gates, "stale_current_conflict"),
        "source_mix_status": _gate_status(promotion_gates, "source_mix_stability"),
        "selected_sources": selected_sources,
        "ignored_sources": ignored_sources,
        "ignored_stale_count": len(stale_ignored),
        "context_sections": [
            {
                "section": section.get("section"),
                "authority": section.get("authority"),
                "item_count": len(section.get("items") or []) if isinstance(section, dict) else 0,
            }
            for section in context_packet.get("sections", [])
            if isinstance(section, dict)
        ],
        "trace": {
            "read_event_recorded": not result.read_result.abstained,
            "promotion_gates": promotion_gates,
            "lane_summaries": result.lane_summaries,
        },
    }


def _candidate_source(candidate: Any) -> dict[str, Any]:
    record = candidate.record if isinstance(candidate.record, dict) else {}
    return {
        "source_class": candidate.source_class,
        "lane": candidate.lane,
        "score": candidate.score,
        "selected": candidate.selected,
        "reason": candidate.reason_selected or candidate.reason_discarded,
        "event_id": record.get("event_id") or record.get("source_event_id") or record.get("id"),
        "source_ref": record.get("source_ref") or record.get("request_id") or record.get("turn_id"),
        "predicate": record.get("predicate") or record.get("source_predicate"),
        "preview": _preview(record),
    }


def _preview(record: dict[str, Any]) -> str | None:
    for key in ("value", "text", "summary", "answer", "content"):
        value = str(record.get(key) or "").strip()
        if value:
            return value[:180]
    return None


def _promotion_gates(context_packet: dict[str, Any]) -> dict[str, Any]:
    trace = context_packet.get("trace") if isinstance(context_packet.get("trace"), dict) else {}
    gates = trace.get("promotion_gates") if isinstance(trace.get("promotion_gates"), dict) else {}
    return gates


def _dominant_source(source_mix: dict[str, Any]) -> str | None:
    if not source_mix:
        return None
    return max(source_mix, key=lambda key: int(source_mix.get(key) or 0))


def _source_authority(source_class: str | None) -> str:
    if not source_class:
        return "none"
    if source_class in AUTHORITY_SOURCE_CLASSES:
        return "authoritative"
    if source_class in SOFT_SOURCE_CLASSES:
        return "episodic_or_supporting"
    if source_class in ADVISORY_SOURCE_CLASSES:
        return "advisory"
    return "supporting"


def _confidence(*, selected: list[Any], source_mix: dict[str, Any], promotion_gates: dict[str, Any]) -> str:
    if not selected:
        return "none"
    stale_status = _gate_status(promotion_gates, "stale_current_conflict")
    mix_status = _gate_status(promotion_gates, "source_mix_stability")
    if stale_status == "fail" or mix_status == "fail":
        return "low"
    if any(candidate.source_class in AUTHORITY_SOURCE_CLASSES for candidate in selected):
        return "high"
    if source_mix:
        return "medium"
    return "low"


def _why_source_won(
    *,
    dominant_source: str | None,
    selected: list[Any],
    source_mix: dict[str, Any],
    promotion_gates: dict[str, Any],
) -> str:
    if not selected:
        return "No memory source was selected, so the answer should abstain or ask for more context."
    selected_authority = [candidate for candidate in selected if candidate.source_class in AUTHORITY_SOURCE_CLASSES]
    if selected_authority:
        return f"{selected_authority[0].source_class} was selected as the strongest current or historical state source."
    if dominant_source:
        count = int(source_mix.get(dominant_source) or 0)
        return f"{dominant_source} had the strongest selected source presence ({count} selected item(s))."
    status = _gate_status(promotion_gates, "source_mix_stability")
    return f"Selected memory passed retrieval gates with source mix status {status}."


def _gate_status(promotion_gates: dict[str, Any], gate_name: str) -> str:
    gates = promotion_gates.get("gates") if isinstance(promotion_gates.get("gates"), dict) else {}
    gate = gates.get(gate_name) if isinstance(gates.get(gate_name), dict) else {}
    return str(gate.get("status") or promotion_gates.get("status") or "unknown")
