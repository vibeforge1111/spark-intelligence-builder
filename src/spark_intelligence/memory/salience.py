from __future__ import annotations

import re
from dataclasses import dataclass


_EXPLICIT_SAVE_RE = re.compile(
    r"\b(?:for later|remember|save this|save that|remember this|remember that)\b",
    re.I,
)
_CURRENT_STATE_RE = re.compile(r"\b(?:current|right now|active|priority|plan|focus|owner|blocker|status)\b", re.I)
_TENTATIVE_DIRECTION_RE = re.compile(
    r"\b(?:leaning toward|leaning towards|inclined toward|inclined towards|probably going with|likely going with)\b",
    re.I,
)
_CORRECTION_RE = re.compile(r"\b(?:actually|correction|not\s+.+?\s+(?:i(?:'m| am)|my name is|is|owns|owned))\b", re.I)
_SECRET_LIKE_RE = re.compile(
    r"\b(?:api[_\s-]?key|botfather|password|secret|private[_\s-]?key|access[_\s-]?token|bearer\s+[a-z0-9._-]+|sk-[a-z0-9]{8,})\b",
    re.I,
)

_HIGH_SIGNAL_PREDICATE_PARTS = {
    "owner",
    "blocker",
    "decision",
    "deadline",
    "location",
    "metric",
    "name",
    "next_action",
    "plan",
    "focus",
    "status",
    "priority",
    "preference",
    "preferred_name",
}


@dataclass(frozen=True)
class MemorySalienceDecision:
    action: str
    salience_score: float
    confidence: float
    promotion_stage: str
    keepability: str
    promotion_disposition: str
    why_saved: str
    reason_code: str
    reasons: tuple[str, ...]

    @property
    def should_write(self) -> bool:
        return self.action != "drop"

    def metadata(self) -> dict[str, object]:
        return {
            "salience_score": self.salience_score,
            "confidence": self.confidence,
            "promotion_stage": self.promotion_stage,
            "keepability": self.keepability,
            "promotion_disposition": self.promotion_disposition,
            "why_saved": self.why_saved,
            "salience_reasons": list(self.reasons),
        }


def evaluate_memory_salience(
    *,
    predicate: str,
    value: str | None,
    evidence_text: str,
    operation: str = "update",
) -> MemorySalienceDecision:
    """Score a candidate durable memory write before promotion.

    This is deliberately deterministic and dependency-free. LLM extraction can
    propose candidate facts later, but Builder owns the final write gate.
    """

    normalized_predicate = str(predicate or "").strip()
    normalized_value = str(value or "").strip()
    text = " ".join(str(evidence_text or "").strip().split())
    lowered_predicate = normalized_predicate.lower()
    reasons: list[str] = []
    score = 0.0

    if operation == "delete":
        reasons.append("explicit_deletion")
        return MemorySalienceDecision(
            action="write",
            salience_score=0.95,
            confidence=0.95,
            promotion_stage="current_state_confirmed",
            keepability="durable_user_memory",
            promotion_disposition="promote_current_state",
            why_saved="explicit_deletion_or_forget_request",
            reason_code="salience_deletion_allowed",
            reasons=tuple(reasons),
        )

    if not normalized_predicate or not normalized_value:
        return MemorySalienceDecision(
            action="drop",
            salience_score=0.0,
            confidence=0.0,
            promotion_stage="drop",
            keepability="not_keepable",
            promotion_disposition="blocked",
            why_saved="missing_predicate_or_value",
            reason_code="salience_missing_fact",
            reasons=("missing_predicate_or_value",),
        )

    if _SECRET_LIKE_RE.search(text) or _SECRET_LIKE_RE.search(normalized_value):
        return MemorySalienceDecision(
            action="drop",
            salience_score=0.0,
            confidence=0.98,
            promotion_stage="drop",
            keepability="not_keepable",
            promotion_disposition="blocked",
            why_saved="secret_like_material_blocked",
            reason_code="salience_secret_like_material",
            reasons=("privacy_or_secret_risk",),
        )

    if _EXPLICIT_SAVE_RE.search(text):
        score += 0.20
        reasons.append("explicit_save_signal")
    if _CURRENT_STATE_RE.search(text) or normalized_predicate.startswith(("profile.current_", "entity.")):
        score += 0.35
        reasons.append("current_state_signal")
    if _CORRECTION_RE.search(text):
        score += 0.25
        reasons.append("correction_or_supersession_signal")
    if _TENTATIVE_DIRECTION_RE.search(text):
        score += 0.15
        reasons.append("tentative_direction_signal")
    if any(part in lowered_predicate for part in _HIGH_SIGNAL_PREDICATE_PARTS):
        score += 0.25
        reasons.append("high_signal_predicate")
    if lowered_predicate == "profile.preferred_name" and _CORRECTION_RE.search(text):
        score += 0.35
        reasons.append("identity_correction_supersession")

    if not reasons:
        score += 0.20
        reasons.append("detected_structured_fact")

    score = min(1.0, round(score, 2))
    confidence = min(0.99, round(0.55 + (score * 0.4), 2))
    if "identity_correction_supersession" in reasons:
        why_saved = "identity_correction_supersession"
    elif "correction_or_supersession_signal" in reasons:
        why_saved = "correction_or_supersession"
    elif "explicit_save_signal" in reasons:
        why_saved = "explicit_user_memory_request"
    elif "tentative_direction_signal" in reasons:
        why_saved = "tentative_project_direction"
    elif "current_state_signal" in reasons:
        why_saved = "active_current_state_fact"
    else:
        why_saved = "structured_fact_detection"

    promotion_stage = "current_state_confirmed" if score >= 0.55 else "structured_evidence"
    promotion_disposition = (
        "promote_current_state" if promotion_stage == "current_state_confirmed" else "promote_structured_evidence"
    )
    keepability = "durable_user_memory" if promotion_stage == "current_state_confirmed" else "supporting_memory"

    return MemorySalienceDecision(
        action="write",
        salience_score=score,
        confidence=confidence,
        promotion_stage=promotion_stage,
        keepability=keepability,
        promotion_disposition=promotion_disposition,
        why_saved=why_saved,
        reason_code="salience_write_allowed",
        reasons=tuple(reasons),
    )


def evaluate_generic_memory_salience(
    *,
    outcome: str,
    memory_role: str | None,
    retention_class: str | None,
    predicate: str | None,
    value: str | None,
    evidence_text: str,
    reason: str | None,
    operation: str | None = "create",
) -> MemorySalienceDecision:
    """Classify non-profile Telegram memory candidates before persistence.

    Generic observations can become current state, structured evidence, raw
    episodes, derived beliefs, or drops. They need the same lane metadata as
    profile facts so later maintenance can tell "useful but weak" apart from
    "never should have been saved".
    """

    normalized_outcome = str(outcome or "drop").strip()
    normalized_role = str(memory_role or normalized_outcome or "").strip()
    normalized_retention = str(retention_class or "").strip()
    normalized_reason = str(reason or "").strip() or normalized_outcome

    if normalized_outcome == "current_state":
        return evaluate_memory_salience(
            predicate=str(predicate or ""),
            value=value,
            evidence_text=evidence_text,
            operation=str(operation or "update"),
        )

    if normalized_outcome == "structured_evidence":
        return MemorySalienceDecision(
            action="write",
            salience_score=0.68,
            confidence=0.78,
            promotion_stage="structured_evidence",
            keepability="durable_intelligence_memory",
            promotion_disposition="promote_structured_evidence",
            why_saved=normalized_reason,
            reason_code="salience_structured_evidence_allowed",
            reasons=("structured_evidence_marker", normalized_retention or "episodic_archive"),
        )

    if normalized_outcome == "belief_candidate":
        return MemorySalienceDecision(
            action="write",
            salience_score=0.58,
            confidence=0.68,
            promotion_stage="belief_candidate",
            keepability="supporting_memory",
            promotion_disposition="promote_belief_candidate",
            why_saved=normalized_reason,
            reason_code="salience_belief_candidate_allowed",
            reasons=("belief_marker", normalized_retention or "derived_belief"),
        )

    if normalized_outcome == "raw_episode":
        return MemorySalienceDecision(
            action="write",
            salience_score=0.18,
            confidence=0.48,
            promotion_stage="raw_episode",
            keepability="episodic_trace",
            promotion_disposition="capture_raw_episode",
            why_saved=normalized_reason,
            reason_code="salience_raw_episode_observed",
            reasons=("meaningful_but_unpromoted", normalized_role or "raw_episode"),
        )

    return MemorySalienceDecision(
        action="drop",
        salience_score=0.0,
        confidence=0.75,
        promotion_stage="drop",
        keepability="not_keepable",
        promotion_disposition="blocked",
        why_saved=normalized_reason,
        reason_code="salience_generic_candidate_dropped",
        reasons=("not_memoryworthy", normalized_reason),
    )
