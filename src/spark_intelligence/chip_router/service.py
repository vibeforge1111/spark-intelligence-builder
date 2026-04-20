from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from spark_intelligence.attachments.registry import AttachmentRecord


KEYWORD_HIT_WEIGHT = 1.0
TOPIC_EXACT_MATCH_WEIGHT = 2.5
COMBINE_WITH_BOOST = 0.75
SELECTION_THRESHOLD = 1.0
MAX_SELECTED_CHIPS = 2

CONVERSATION_HISTORY_WEIGHT = 0.5
RECENT_CHIP_STICKY_BOOST = 1.5

_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_]*")


@dataclass
class ScoredChip:
    chip_key: str
    score: float
    matched_keywords: list[str] = field(default_factory=list)
    matched_topics: list[str] = field(default_factory=list)
    combine_with_boost_from: list[str] = field(default_factory=list)
    history_keywords: list[str] = field(default_factory=list)
    history_topics: list[str] = field(default_factory=list)
    sticky_boost_applied: bool = False

    def to_dict(self) -> dict:
        return {
            "chip_key": self.chip_key,
            "score": round(self.score, 3),
            "matched_keywords": list(self.matched_keywords),
            "matched_topics": list(self.matched_topics),
            "combine_with_boost_from": list(self.combine_with_boost_from),
            "history_keywords": list(self.history_keywords),
            "history_topics": list(self.history_topics),
            "sticky_boost_applied": self.sticky_boost_applied,
        }


@dataclass
class ChipRoutingDecision:
    message_preview: str
    selected: list[ScoredChip]
    considered: list[ScoredChip]
    threshold: float
    cap: int
    fell_through: bool

    def to_dict(self) -> dict:
        return {
            "message_preview": self.message_preview,
            "threshold": self.threshold,
            "cap": self.cap,
            "fell_through": self.fell_through,
            "selected": [s.to_dict() for s in self.selected],
            "considered": [s.to_dict() for s in self.considered],
        }


def _tokenize(message: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(message.lower()))


def _normalize_keyword(token: str) -> str:
    return str(token or "").strip().lower()


def _match_keywords_and_topics(
    keywords: list[str],
    topics: list[str],
    text_lower: str,
    text_tokens: set[str],
) -> tuple[list[str], list[str], float]:
    matched_keywords: list[str] = []
    matched_topics: list[str] = []
    raw_score = 0.0
    for raw in keywords:
        keyword = _normalize_keyword(raw)
        if not keyword:
            continue
        if " " in keyword or "." in keyword or "-" in keyword:
            if keyword in text_lower:
                matched_keywords.append(keyword)
                raw_score += KEYWORD_HIT_WEIGHT
        elif keyword in text_tokens:
            matched_keywords.append(keyword)
            raw_score += KEYWORD_HIT_WEIGHT
    for raw in topics:
        topic = _normalize_keyword(raw)
        if not topic:
            continue
        plain = topic.replace("_", " ")
        if topic in text_lower or plain in text_lower:
            matched_topics.append(topic)
            raw_score += TOPIC_EXACT_MATCH_WEIGHT
            continue
        topic_tokens = set(plain.split())
        if topic_tokens and topic_tokens.issubset(text_tokens):
            matched_topics.append(topic)
            raw_score += TOPIC_EXACT_MATCH_WEIGHT
    return matched_keywords, matched_topics, raw_score


def _score_chip(
    record: AttachmentRecord,
    message_lower: str,
    message_tokens: set[str],
    history_lower: str = "",
    history_tokens: set[str] | None = None,
) -> ScoredChip | None:
    keywords = _chip_field(record, "task_keywords")
    topics = _chip_field(record, "task_topics")
    if not keywords and not topics:
        return None

    matched_keywords, matched_topics, message_score = _match_keywords_and_topics(
        keywords, topics, message_lower, message_tokens
    )

    history_keywords: list[str] = []
    history_topics: list[str] = []
    history_score = 0.0
    if history_lower and history_tokens is not None:
        history_keywords, history_topics, history_raw = _match_keywords_and_topics(
            keywords, topics, history_lower, history_tokens
        )
        history_score = history_raw * CONVERSATION_HISTORY_WEIGHT

    total_score = message_score + history_score
    if total_score == 0.0:
        return None
    return ScoredChip(
        chip_key=record.key,
        score=total_score,
        matched_keywords=matched_keywords,
        matched_topics=matched_topics,
        history_keywords=history_keywords,
        history_topics=history_topics,
    )


def _apply_combine_with_boost(
    scored: list[ScoredChip],
    chip_index: dict[str, AttachmentRecord],
) -> list[ScoredChip]:
    by_key = {s.chip_key: s for s in scored}
    for s in scored:
        record = chip_index.get(s.chip_key)
        if record is None:
            continue
        partners = _chip_field(record, "combine_with")
        for partner in partners:
            partner_key = str(partner or "").strip()
            if not partner_key or partner_key == s.chip_key:
                continue
            if partner_key in by_key:
                s.score += COMBINE_WITH_BOOST
                s.combine_with_boost_from.append(partner_key)
    return scored


def _chip_field(record: AttachmentRecord, field_name: str) -> list[str]:
    value = getattr(record, field_name, None)
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v or "").strip()]
    return []


def select_chips_for_message(
    message: str,
    active_chips: Iterable[AttachmentRecord],
    *,
    threshold: float = SELECTION_THRESHOLD,
    cap: int = MAX_SELECTED_CHIPS,
    conversation_history: str = "",
    recent_active_chip_keys: Iterable[str] = (),
) -> ChipRoutingDecision:
    message_lower = str(message or "").lower()
    message_tokens = _tokenize(message_lower)
    history_lower = str(conversation_history or "").lower()
    history_tokens = _tokenize(history_lower) if history_lower else set()
    chips = [c for c in active_chips if c.kind == "chip"]
    chip_index = {c.key: c for c in chips}
    sticky_set = {str(k or "").strip() for k in recent_active_chip_keys if str(k or "").strip()}

    considered: list[ScoredChip] = []
    considered_keys: set[str] = set()
    for record in chips:
        scored = _score_chip(
            record,
            message_lower,
            message_tokens,
            history_lower=history_lower,
            history_tokens=history_tokens,
        )
        if scored is not None:
            considered.append(scored)
            considered_keys.add(scored.chip_key)

    for record in chips:
        if record.key not in sticky_set or record.key in considered_keys:
            continue
        keywords = _chip_field(record, "task_keywords")
        topics = _chip_field(record, "task_topics")
        if not keywords and not topics:
            continue
        considered.append(ScoredChip(chip_key=record.key, score=0.0))
        considered_keys.add(record.key)

    for s in considered:
        if s.chip_key in sticky_set:
            s.score += RECENT_CHIP_STICKY_BOOST
            s.sticky_boost_applied = True

    considered = _apply_combine_with_boost(considered, chip_index)
    considered.sort(key=lambda s: (-s.score, s.chip_key))

    selected = [s for s in considered if s.score >= threshold][:cap]
    return ChipRoutingDecision(
        message_preview=message[:140],
        selected=selected,
        considered=considered,
        threshold=threshold,
        cap=cap,
        fell_through=not selected,
    )


def explain_routing(decision: ChipRoutingDecision) -> str:
    lines = [
        "Chip routing decision",
        f"- message: {decision.message_preview}",
        f"- threshold: {decision.threshold}  cap: {decision.cap}",
        f"- selected: {', '.join(s.chip_key for s in decision.selected) if decision.selected else 'none (LLM solo)'}",
    ]
    if decision.considered:
        lines.append("- candidates:")
        for s in decision.considered:
            chosen = " *" if s in decision.selected else ""
            details = []
            if s.matched_topics:
                details.append(f"topics={','.join(s.matched_topics)}")
            if s.matched_keywords:
                details.append(f"keywords={','.join(s.matched_keywords)}")
            if s.history_topics:
                details.append(f"history_topics={','.join(s.history_topics)}")
            if s.history_keywords:
                details.append(f"history_keywords={','.join(s.history_keywords)}")
            if s.sticky_boost_applied:
                details.append("sticky=yes")
            if s.combine_with_boost_from:
                details.append(f"combine_with={','.join(s.combine_with_boost_from)}")
            detail_str = f"  ({'; '.join(details)})" if details else ""
            lines.append(f"  - {s.chip_key} score={s.score:.2f}{chosen}{detail_str}")
    else:
        lines.append("- candidates: none scored")
    if decision.fell_through:
        lines.append("- result: no chip met threshold; LLM responds without chip guidance")
    return "\n".join(lines)
