from __future__ import annotations

import re
from dataclasses import dataclass


_EXPLICIT_SAVE_RE = re.compile(
    r"\b(?:for later|remember|save this|save that|remember this|remember that)\b",
    re.I,
)
_CURRENT_STATE_RE = re.compile(r"\b(?:current|right now|active|priority|plan|focus|owner|blocker|status)\b", re.I)
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
    "metric",
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
        score += 0.35
        reasons.append("explicit_save_signal")
    if _CURRENT_STATE_RE.search(text) or normalized_predicate.startswith(("profile.current_", "entity.")):
        score += 0.25
        reasons.append("current_state_signal")
    if _CORRECTION_RE.search(text):
        score += 0.25
        reasons.append("correction_or_supersession_signal")
    if any(part in lowered_predicate for part in _HIGH_SIGNAL_PREDICATE_PARTS):
        score += 0.20
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
