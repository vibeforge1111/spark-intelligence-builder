from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from spark_intelligence.memory.constitution import build_memory_review_card_from_resolution
from spark_intelligence.observability.store import record_contradiction
from spark_intelligence.state.db import StateDB


SOURCE_HIERARCHY_SCHEMA_VERSION = "spark.source_hierarchy.v1"
SourceFreshness = Literal["fresh", "stale", "contradicted", "unknown", "live_probed"]

SOURCE_AUTHORITY: tuple[tuple[str, int], ...] = (
    ("current_user_message", 100),
    ("operator_override", 98),
    ("operator_supplied_access", 96),
    ("current_diagnostics", 94),
    ("runner_preflight", 92),
    ("live_probe", 90),
    ("approved_memory", 78),
    ("memory", 68),
    ("wiki_doctrine", 58),
    ("mission_trace", 48),
    ("raw_chat_history", 30),
    ("inference", 20),
    ("unknown", 0),
)


@dataclass(frozen=True)
class SourceClaim:
    claim_key: str
    value: str
    source: str
    source_ref: str | None = None
    freshness: SourceFreshness = "unknown"
    summary: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "claim_key": self.claim_key,
            "value": self.value,
            "source": self.source,
            "source_ref": self.source_ref,
            "freshness": self.freshness,
            "summary": self.summary,
            "authority_rank": source_authority_rank(self.source),
        }


@dataclass(frozen=True)
class SourceConflictResolution:
    claim_key: str
    winner: SourceClaim
    stale_claims: list[SourceClaim] = field(default_factory=list)
    contradicted_claims: list[SourceClaim] = field(default_factory=list)
    resolution: str = ""
    suggested_action: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SOURCE_HIERARCHY_SCHEMA_VERSION,
            "claim_key": self.claim_key,
            "winner": self.winner.to_payload(),
            "stale_claims": [claim.to_payload() for claim in self.stale_claims],
            "contradicted_claims": [claim.to_payload() for claim in self.contradicted_claims],
            "resolution": self.resolution,
            "suggested_action": self.suggested_action,
        }


def source_authority_rank(source: str) -> int:
    normalized = str(source or "").strip()
    ranks = dict(SOURCE_AUTHORITY)
    if normalized in ranks:
        return ranks[normalized]
    if "diagnostic" in normalized:
        return ranks["current_diagnostics"]
    if "memory" in normalized:
        return ranks["memory"]
    if "wiki" in normalized:
        return ranks["wiki_doctrine"]
    if "mission" in normalized:
        return ranks["mission_trace"]
    return ranks["unknown"]


def resolve_source_claims(claims: list[SourceClaim]) -> list[SourceConflictResolution]:
    grouped: dict[str, list[SourceClaim]] = {}
    for claim in claims:
        key = str(claim.claim_key or "").strip()
        if not key:
            continue
        grouped.setdefault(key, []).append(claim)

    resolutions: list[SourceConflictResolution] = []
    for claim_key, claim_group in grouped.items():
        distinct_values = {_normalize_value(claim.value) for claim in claim_group}
        if len(distinct_values) <= 1:
            continue
        winner = max(
            claim_group,
            key=lambda claim: (
                source_authority_rank(claim.source),
                _freshness_rank(claim.freshness),
                str(claim.source_ref or ""),
            ),
        )
        stale_claims: list[SourceClaim] = []
        contradicted_claims: list[SourceClaim] = []
        for claim in claim_group:
            if claim is winner or _normalize_value(claim.value) == _normalize_value(winner.value):
                continue
            if source_authority_rank(claim.source) < source_authority_rank(winner.source):
                stale_claims.append(claim)
            else:
                contradicted_claims.append(claim)
        loser_sources = ", ".join(claim.source for claim in [*stale_claims, *contradicted_claims]) or "none"
        resolutions.append(
            SourceConflictResolution(
                claim_key=claim_key,
                winner=winner,
                stale_claims=stale_claims,
                contradicted_claims=contradicted_claims,
                resolution=f"{winner.source} wins for {claim_key}; lower-authority conflicting sources: {loser_sources}.",
                suggested_action=_suggested_action(stale_claims=stale_claims, contradicted_claims=contradicted_claims),
            )
        )
    return resolutions


def record_source_conflict_resolutions(
    state_db: StateDB,
    resolutions: list[SourceConflictResolution],
    *,
    component: str = "source_hierarchy",
    request_id: str = "",
    trace_ref: str = "",
) -> list[str]:
    contradiction_ids: list[str] = []
    for resolution in resolutions:
        facts = resolution.to_payload()
        facts["memory_review_card"] = build_memory_review_card_from_resolution(
            resolution,
            request_id=request_id,
            trace_ref=trace_ref,
        )
        contradiction_ids.append(
            record_contradiction(
                state_db,
                contradiction_key=f"source_hierarchy:{resolution.claim_key}",
                component=component,
                reason_code="source_hierarchy_conflict",
                summary=f"Source hierarchy conflict for {resolution.claim_key}.",
                detail=resolution.resolution,
                severity="medium" if resolution.contradicted_claims else "low",
                facts=facts,
                provenance={
                    "source": "source_hierarchy",
                    "winner_source": resolution.winner.source,
                    "stale_sources": [claim.source for claim in resolution.stale_claims],
                    "contradicted_sources": [claim.source for claim in resolution.contradicted_claims],
                },
                request_id=request_id or None,
                trace_ref=trace_ref or None,
            )
        )
    return contradiction_ids


def _suggested_action(*, stale_claims: list[SourceClaim], contradicted_claims: list[SourceClaim]) -> str:
    if contradicted_claims:
        return "Ask for clarification or run a live probe before using the conflicted claim."
    if any("memory" in claim.source for claim in stale_claims):
        return "Mark lower-authority memory stale and keep current state visible in AOC."
    return "Treat lower-authority source as stale for this turn."


def _freshness_rank(freshness: str) -> int:
    return {
        "live_probed": 5,
        "fresh": 4,
        "unknown": 3,
        "stale": 2,
        "contradicted": 1,
    }.get(str(freshness or "unknown"), 3)


def _normalize_value(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())
