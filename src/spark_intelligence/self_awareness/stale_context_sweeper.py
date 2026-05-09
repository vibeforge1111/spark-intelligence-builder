from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from spark_intelligence.observability.store import utc_now_iso
from spark_intelligence.self_awareness.source_hierarchy import (
    SourceClaim,
    SourceConflictResolution,
    record_source_conflict_resolutions,
    resolve_source_claims,
)
from spark_intelligence.state.db import StateDB


STALE_CONTEXT_SWEEP_SCHEMA_VERSION = "spark.stale_context_sweep.v1"


@dataclass(frozen=True)
class StaleContextItem:
    claim_key: str
    stale_source: str
    stale_value: str
    winning_source: str
    winning_value: str
    freshness: str
    action_type: str
    suggested_action: str
    review_required: bool
    source_ref: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "claim_key": self.claim_key,
            "stale_source": self.stale_source,
            "stale_value": self.stale_value,
            "winning_source": self.winning_source,
            "winning_value": self.winning_value,
            "freshness": self.freshness,
            "action_type": self.action_type,
            "suggested_action": self.suggested_action,
            "review_required": self.review_required,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class StaleContextSweepReport:
    checked_at: str
    status: str
    stale_items: list[StaleContextItem] = field(default_factory=list)
    contradicted_items: list[StaleContextItem] = field(default_factory=list)
    resolutions: list[SourceConflictResolution] = field(default_factory=list)
    recorded_contradiction_ids: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": STALE_CONTEXT_SWEEP_SCHEMA_VERSION,
            "checked_at": self.checked_at,
            "status": self.status,
            "counts": {
                "stale": len(self.stale_items),
                "contradicted": len(self.contradicted_items),
                "resolutions": len(self.resolutions),
                "recorded_contradictions": len(self.recorded_contradiction_ids),
            },
            "stale_items": [item.to_payload() for item in self.stale_items],
            "contradicted_items": [item.to_payload() for item in self.contradicted_items],
            "resolutions": [resolution.to_payload() for resolution in self.resolutions],
            "recorded_contradiction_ids": list(self.recorded_contradiction_ids),
            "source_policy": (
                "Current user message, diagnostics, runner preflight, and live probes beat stale memory, wiki, "
                "mission trace, raw chat history, and inference."
            ),
        }

    def to_text(self) -> str:
        if not self.stale_items and not self.contradicted_items:
            return "Stale context sweep: clear."
        lines = [
            f"Stale context sweep: {len(self.stale_items)} stale, {len(self.contradicted_items)} contradicted."
        ]
        for item in [*self.stale_items, *self.contradicted_items][:8]:
            lines.append(
                f"- {item.claim_key}: {item.stale_source}={item.stale_value} "
                f"lost to {item.winning_source}={item.winning_value}; action={item.action_type}"
            )
        return "\n".join(lines)


def build_stale_context_sweep(
    *,
    live_claims: list[SourceClaim | dict[str, Any]],
    context_claims: list[SourceClaim | dict[str, Any]],
    state_db: StateDB | None = None,
    record_contradictions: bool = False,
) -> StaleContextSweepReport:
    claims = [_coerce_claim(claim) for claim in [*context_claims, *live_claims]]
    resolutions = resolve_source_claims([claim for claim in claims if claim is not None])
    stale_items: list[StaleContextItem] = []
    contradicted_items: list[StaleContextItem] = []
    for resolution in resolutions:
        stale_items.extend(_items_from_claims(resolution=resolution, claims=resolution.stale_claims, freshness="stale"))
        contradicted_items.extend(
            _items_from_claims(
                resolution=resolution,
                claims=resolution.contradicted_claims,
                freshness="contradicted",
            )
        )
    recorded_ids: list[str] = []
    if record_contradictions and state_db is not None and resolutions:
        recorded_ids = record_source_conflict_resolutions(state_db, resolutions, component="stale_context_sweeper")
    status = "clear" if not stale_items and not contradicted_items else "needs_review"
    return StaleContextSweepReport(
        checked_at=utc_now_iso(),
        status=status,
        stale_items=stale_items,
        contradicted_items=contradicted_items,
        resolutions=resolutions,
        recorded_contradiction_ids=recorded_ids,
    )


def _items_from_claims(
    *,
    resolution: SourceConflictResolution,
    claims: list[SourceClaim],
    freshness: str,
) -> list[StaleContextItem]:
    return [
        StaleContextItem(
            claim_key=resolution.claim_key,
            stale_source=claim.source,
            stale_value=claim.value,
            winning_source=resolution.winner.source,
            winning_value=resolution.winner.value,
            freshness=freshness,
            action_type=_stale_action_type(claim, freshness=freshness),
            suggested_action=resolution.suggested_action,
            review_required=_review_required(claim, freshness=freshness),
            source_ref=claim.source_ref,
        )
        for claim in claims
    ]


def _coerce_claim(value: SourceClaim | dict[str, Any]) -> SourceClaim | None:
    if isinstance(value, SourceClaim):
        return value
    if not isinstance(value, dict):
        return None
    claim_key = str(value.get("claim_key") or "").strip()
    claim_value = str(value.get("value") or "").strip()
    source = str(value.get("source") or "").strip()
    if not claim_key or not claim_value or not source:
        return None
    return SourceClaim(
        claim_key=claim_key,
        value=claim_value,
        source=source,
        source_ref=str(value.get("source_ref") or "").strip() or None,
        freshness=str(value.get("freshness") or "unknown"),  # type: ignore[arg-type]
        summary=str(value.get("summary") or "").strip(),
    )


def _stale_action_type(claim: SourceClaim, *, freshness: str) -> str:
    source = str(claim.source or "").casefold()
    if freshness == "contradicted":
        return "resolve_contradiction_before_reuse"
    if "memory" in source:
        return "mark_memory_stale"
    if "wiki" in source:
        return "mark_wiki_claim_stale"
    return "treat_as_stale_for_turn"


def _review_required(claim: SourceClaim, *, freshness: str) -> bool:
    source = str(claim.source or "").casefold()
    return freshness == "contradicted" or "memory" in source or "wiki" in source
