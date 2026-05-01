from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.context.capsule import build_spark_context_capsule
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.state.db import StateDB
from spark_intelligence.system_registry import build_system_registry


@dataclass(frozen=True)
class SelfAwarenessClaim:
    claim: str
    source: str
    source_kind: str
    confidence: str
    verification_status: str
    freshness: str = "current_snapshot"
    capability_key: str | None = None
    next_probe: str | None = None
    improvement_action: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim": self.claim,
            "source": self.source,
            "source_kind": self.source_kind,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
            "freshness": self.freshness,
        }
        if self.capability_key:
            payload["capability_key"] = self.capability_key
        if self.next_probe:
            payload["next_probe"] = self.next_probe
        if self.improvement_action:
            payload["improvement_action"] = self.improvement_action
        return payload


@dataclass(frozen=True)
class SelfAwarenessCapsule:
    generated_at: str
    workspace_id: str
    observed_now: list[SelfAwarenessClaim] = field(default_factory=list)
    recently_verified: list[SelfAwarenessClaim] = field(default_factory=list)
    available_unverified: list[SelfAwarenessClaim] = field(default_factory=list)
    degraded_or_missing: list[SelfAwarenessClaim] = field(default_factory=list)
    inferred_strengths: list[SelfAwarenessClaim] = field(default_factory=list)
    lacks: list[SelfAwarenessClaim] = field(default_factory=list)
    improvement_options: list[SelfAwarenessClaim] = field(default_factory=list)
    recommended_probes: list[str] = field(default_factory=list)
    natural_language_routes: list[str] = field(default_factory=list)
    source_ledger: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "observed_now": _claims_payload(self.observed_now),
            "recently_verified": _claims_payload(self.recently_verified),
            "available_unverified": _claims_payload(self.available_unverified),
            "degraded_or_missing": _claims_payload(self.degraded_or_missing),
            "inferred_strengths": _claims_payload(self.inferred_strengths),
            "lacks": _claims_payload(self.lacks),
            "improvement_options": _claims_payload(self.improvement_options),
            "recommended_probes": self.recommended_probes,
            "natural_language_routes": self.natural_language_routes,
            "source_ledger": self.source_ledger,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        lines = [
            "Spark self-awareness",
            f"- generated_at: {self.generated_at}",
            f"- workspace: {self.workspace_id}",
            "",
        ]
        _extend_claim_lines(lines, "Observed now", self.observed_now, limit=5)
        _extend_claim_lines(lines, "Recently verified", self.recently_verified, limit=3)
        _extend_claim_lines(lines, "Where Spark is strong", self.inferred_strengths, limit=3)
        _extend_claim_lines(lines, "Where Spark lacks", self.lacks, limit=5)
        _extend_claim_lines(lines, "How Spark can improve", self.improvement_options, limit=5)
        if self.recommended_probes:
            lines.append("Recommended probes")
            lines.extend(f"- {item}" for item in self.recommended_probes[:4])
            lines.append("")
        if self.natural_language_routes:
            lines.append("Natural-language routes")
            lines.extend(f"- {item}" for item in self.natural_language_routes[:4])
        return "\n".join(lines).strip()


def build_self_awareness_capsule(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str = "",
    session_id: str = "",
    channel_kind: str = "",
    request_id: str | None = None,
    user_message: str = "",
) -> SelfAwarenessCapsule:
    registry_payload = build_system_registry(config_manager, state_db, probe_browser=False, probe_git=False).to_payload()
    context_capsule = build_spark_context_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message,
    )
    records = [record for record in (registry_payload.get("records") or []) if isinstance(record, dict)]
    generated_at = _now_iso()
    workspace_id = str(registry_payload.get("workspace_id") or "default")

    observed_now = _build_observed_claims(records)
    recently_verified = _build_recent_invocation_claims(state_db)
    available_unverified = _build_available_unverified_claims(records)
    degraded_or_missing = _build_degraded_claims(records)
    inferred_strengths = _build_strength_claims(registry_payload=registry_payload, context_capsule=context_capsule)
    lacks = _build_lack_claims(records=records, degraded_claims=degraded_or_missing)
    improvement_options = _build_improvement_claims(lacks=lacks, degraded_claims=degraded_or_missing)
    recommended_probes = _recommended_probes(degraded_claims=degraded_or_missing)
    natural_language_routes = [
        "Ask: 'Spark, what do you know about your current systems?' to get the grounded registry view.",
        "Ask: 'Spark, test the browser route now' to turn browser availability into last-success evidence.",
        "Ask: 'Spark, check which chips are active and what they can improve for this goal' to map capability fit.",
        "Ask: 'Spark, improve the weak spots you just found' to run the safest next probes before changing behavior.",
    ]

    source_ledger = [
        {
            "source": "system_registry",
            "source_kind": "runtime_snapshot",
            "present": True,
            "claim_boundary": "Configuration and attachment visibility. Not proof of successful invocation.",
            "record_count": int(registry_payload.get("record_count") or 0),
        },
        {
            "source": "context_capsule",
            "source_kind": "runtime_context",
            "present": not context_capsule.is_empty(),
            "claim_boundary": "Current focus, memory, workflow, and recent conversation signals when identifiers are available.",
            "source_counts": context_capsule.source_counts,
        },
        {
            "source": "observability_events",
            "source_kind": "recent_invocation_log",
            "present": bool(recently_verified),
            "claim_boundary": "Recent route/tool outcomes only. A previous success can go stale.",
        },
    ]
    return SelfAwarenessCapsule(
        generated_at=generated_at,
        workspace_id=workspace_id,
        observed_now=observed_now,
        recently_verified=recently_verified,
        available_unverified=available_unverified,
        degraded_or_missing=degraded_or_missing,
        inferred_strengths=inferred_strengths,
        lacks=lacks,
        improvement_options=improvement_options,
        recommended_probes=recommended_probes,
        natural_language_routes=natural_language_routes,
        source_ledger=source_ledger,
    )


def _build_observed_claims(records: list[dict[str, Any]]) -> list[SelfAwarenessClaim]:
    claims: list[SelfAwarenessClaim] = []
    for record in records:
        kind = str(record.get("kind") or "")
        if kind not in {"system", "adapter"}:
            continue
        status = str(record.get("status") or "unknown")
        if not bool(record.get("available")) and status not in {"ready", "configured", "available"}:
            continue
        label = str(record.get("label") or record.get("key") or "unknown").strip()
        claims.append(
            SelfAwarenessClaim(
                claim=f"{label} is visible in the Builder registry with status={status}.",
                source=f"registry:{record.get('record_id') or record.get('key')}",
                source_kind="system_registry",
                confidence="high",
                verification_status="observed_configuration",
                capability_key=str(record.get("key") or "") or None,
                next_probe=f"Run a health or invocation check for {record.get('key') or label} before claiming it worked.",
            )
        )
    return claims[:12]


def _build_recent_invocation_claims(state_db: StateDB) -> list[SelfAwarenessClaim]:
    claims: list[SelfAwarenessClaim] = []
    for event_type in ("tool_result_received", "dispatch_failed"):
        try:
            events = latest_events_by_type(state_db, event_type=event_type, limit=8)
        except Exception:
            events = []
        for event in events:
            facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
            provenance = event.get("provenance_json") if isinstance(event.get("provenance_json"), dict) else {}
            route = str(facts.get("routing_decision") or facts.get("bridge_mode") or event.get("reason_code") or "").strip()
            chip = str(facts.get("active_chip_key") or facts.get("chip_key") or "").strip()
            source_ref = str(provenance.get("source_ref") or event.get("component") or event_type).strip()
            status = str(event.get("status") or "unknown").strip()
            if not route and not chip and not source_ref:
                continue
            detail = route or chip or source_ref
            suffix = f" via {chip}" if chip else ""
            claims.append(
                SelfAwarenessClaim(
                    claim=f"Recent {event_type}: {detail}{suffix} status={status}.",
                    source=f"event:{event.get('event_id') or source_ref}",
                    source_kind="observability_event",
                    confidence="medium" if event_type == "tool_result_received" else "high",
                    verification_status="recent_success" if event_type == "tool_result_received" else "recent_failure",
                    freshness=str(event.get("created_at") or "recent_event"),
                    capability_key=chip or route or None,
                    next_probe="Repeat the route if the user needs current proof, because recent events can go stale.",
                )
            )
    return claims[:8]


def _build_available_unverified_claims(records: list[dict[str, Any]]) -> list[SelfAwarenessClaim]:
    claims: list[SelfAwarenessClaim] = []
    for record in records:
        kind = str(record.get("kind") or "")
        if kind not in {"chip", "path", "provider", "repo"}:
            continue
        if bool(record.get("degraded")) or not bool(record.get("available")):
            continue
        label = str(record.get("label") or record.get("key") or "unknown").strip()
        capabilities = [str(item).strip() for item in (record.get("capabilities") or []) if str(item).strip()]
        capability_text = f" capabilities={', '.join(capabilities[:4])}" if capabilities else ""
        claims.append(
            SelfAwarenessClaim(
                claim=f"{kind} {label} is available in configuration{capability_text}, but this capsule has not invoked it.",
                source=f"registry:{record.get('record_id') or record.get('key')}",
                source_kind="system_registry",
                confidence="medium",
                verification_status="available_unverified",
                capability_key=str(record.get("key") or "") or None,
                next_probe=_probe_for_kind(kind, str(record.get("key") or label)),
                improvement_action=_improvement_for_kind(kind, str(record.get("key") or label)),
            )
        )
    return claims[:12]


def _build_degraded_claims(records: list[dict[str, Any]]) -> list[SelfAwarenessClaim]:
    claims: list[SelfAwarenessClaim] = []
    for record in records:
        status = str(record.get("status") or "unknown")
        if not bool(record.get("degraded")) and status not in {"missing", "degraded", "blocked", "error"}:
            continue
        label = str(record.get("label") or record.get("key") or "unknown").strip()
        limitations = [str(item).strip() for item in (record.get("limitations") or []) if str(item).strip()]
        limitation = f" Main limit: {limitations[0]}" if limitations else ""
        claims.append(
            SelfAwarenessClaim(
                claim=f"{label} is not fully healthy or available: status={status}.{limitation}",
                source=f"registry:{record.get('record_id') or record.get('key')}",
                source_kind="system_registry",
                confidence="high",
                verification_status="degraded_or_missing",
                capability_key=str(record.get("key") or "") or None,
                next_probe=f"Run diagnostics or a direct route check for {record.get('key') or label}.",
                improvement_action=f"Repair configuration, auth, attachment, or runtime readiness for {record.get('key') or label}, then record last-success evidence.",
            )
        )
    return claims[:12]


def _build_strength_claims(*, registry_payload: dict[str, Any], context_capsule: Any) -> list[SelfAwarenessClaim]:
    summary = registry_payload.get("summary") if isinstance(registry_payload.get("summary"), dict) else {}
    capabilities = [str(item).strip() for item in (summary.get("current_capabilities") or []) if str(item).strip()]
    claims: list[SelfAwarenessClaim] = []
    if capabilities:
        claims.append(
            SelfAwarenessClaim(
                claim=f"Spark can describe current capabilities from registry evidence: {', '.join(capabilities[:5])}.",
                source="system_registry:summary.current_capabilities",
                source_kind="system_registry",
                confidence="high",
                verification_status="inferred_from_observed_registry",
            )
        )
    if not context_capsule.is_empty():
        present_sources = [
            source
            for source, count in sorted(context_capsule.source_counts.items())
            if int(count or 0) > 0
        ]
        claims.append(
            SelfAwarenessClaim(
                claim=f"Spark can use turn context sources for continuity: {', '.join(present_sources[:6])}.",
                source="context_capsule:source_counts",
                source_kind="runtime_context",
                confidence="medium",
                verification_status="inferred_from_context_sources",
            )
        )
    claims.append(
        SelfAwarenessClaim(
            claim="Spark is strongest when it separates observed runtime state, recent invocation evidence, memory/context, and inference.",
            source="self_awareness_capsule:claim_policy",
            source_kind="design_policy",
            confidence="high",
            verification_status="policy",
        )
    )
    return claims


def _build_lack_claims(
    *,
    records: list[dict[str, Any]],
    degraded_claims: list[SelfAwarenessClaim],
) -> list[SelfAwarenessClaim]:
    claims: list[SelfAwarenessClaim] = list(degraded_claims)
    has_provider = any(str(record.get("kind") or "") == "provider" and bool(record.get("available")) for record in records)
    if not has_provider:
        claims.append(
            SelfAwarenessClaim(
                claim="No available provider auth is visible, so provider-backed reasoning may be unavailable or degraded.",
                source="registry:provider_records",
                source_kind="system_registry",
                confidence="high",
                verification_status="missing_visible_provider",
                next_probe="Run auth status and provider resolution checks.",
                improvement_action="Connect or repair at least one provider profile, then record a successful provider invocation.",
            )
        )
    claims.extend(
        [
            SelfAwarenessClaim(
                claim="Registry visibility does not prove a chip, browser route, provider, or workflow succeeded this turn.",
                source="self_awareness_capsule:claim_boundary",
                source_kind="design_policy",
                confidence="high",
                verification_status="known_boundary",
                next_probe="Run the target route and persist last-success, latency, and failure-mode evidence.",
                improvement_action="Add per-capability last_success_at, last_failure_reason, and eval coverage fields.",
            ),
            SelfAwarenessClaim(
                claim="Spark cannot inspect secrets, hidden prompts, private infrastructure, or deployment health unless a safe diagnostic surface exposes them.",
                source="self_awareness_capsule:security_boundary",
                source_kind="design_policy",
                confidence="high",
                verification_status="known_boundary",
                next_probe="Expose only safe redacted diagnostics for secret-bound systems.",
                improvement_action="Add redacted health summaries instead of raw secret or private infra access.",
            ),
            SelfAwarenessClaim(
                claim="Natural-language invocability is only real when a user phrase maps to a route that exists, is authorized, and emits traceable evidence.",
                source="self_awareness_capsule:natural_language_contract",
                source_kind="design_policy",
                confidence="high",
                verification_status="known_boundary",
                next_probe="Run route-selection evals for self-awareness and improvement requests.",
                improvement_action="Add eval cases for 'improve this weak spot', stale status traps, and capability overclaim traps.",
            ),
        ]
    )
    return claims[:14]


def _build_improvement_claims(
    *,
    lacks: list[SelfAwarenessClaim],
    degraded_claims: list[SelfAwarenessClaim],
) -> list[SelfAwarenessClaim]:
    claims: list[SelfAwarenessClaim] = []
    for claim in lacks:
        if not claim.improvement_action:
            continue
        claims.append(
            SelfAwarenessClaim(
                claim=claim.improvement_action,
                source=claim.source,
                source_kind=claim.source_kind,
                confidence=claim.confidence,
                verification_status="improvement_option",
                capability_key=claim.capability_key,
                next_probe=claim.next_probe,
            )
        )
    if not degraded_claims:
        claims.append(
            SelfAwarenessClaim(
                claim="No degraded core system is visible in this fast snapshot; improve quality by probing last-success evidence for the route the user cares about.",
                source="registry:fast_snapshot",
                source_kind="system_registry",
                confidence="medium",
                verification_status="improvement_option",
                next_probe="Ask the user goal, select the target capability, then run a bounded route check.",
            )
        )
    return claims[:10]


def _recommended_probes(*, degraded_claims: list[SelfAwarenessClaim]) -> list[str]:
    probes = [
        "Run `spark-intelligence self status --json` before self-knowledge answers that need provenance.",
        "Run a direct health/invocation check for the exact capability the user wants to rely on.",
        "Record last_success_at, last_failure_reason, and route_latency_ms for each important system path.",
    ]
    for claim in degraded_claims[:4]:
        if claim.next_probe:
            probes.append(claim.next_probe)
    return probes


def _probe_for_kind(kind: str, key: str) -> str:
    if kind == "chip":
        return f"Run an evaluate/watchtower hook for chip {key} and record the result."
    if kind == "provider":
        return f"Resolve provider {key} and run a minimal inference smoke test."
    if kind == "repo":
        return f"Inspect git status and project index for repo {key} before editing or rating quality."
    if kind == "path":
        return f"Load path {key} content and verify which intents it supports."
    return f"Run a bounded health check for {key}."


def _improvement_for_kind(kind: str, key: str) -> str:
    if kind == "chip":
        return f"Improve chip {key} by adding last-success telemetry, failure modes, and route-selection eval examples."
    if kind == "provider":
        return f"Improve provider {key} by adding auth freshness, model availability, latency, and fallback diagnostics."
    if kind == "repo":
        return f"Improve repo awareness for {key} by adding component ownership, test commands, and dirty-state handling."
    if kind == "path":
        return f"Improve path {key} by loading its playbook depth and mapping natural-language intents to supported actions."
    return f"Improve {key} by adding a current health probe and evidence-backed route contract."


def _extend_claim_lines(
    lines: list[str],
    title: str,
    claims: list[SelfAwarenessClaim],
    *,
    limit: int | None = None,
) -> None:
    selected_claims = claims[:limit] if limit is not None else claims
    if not selected_claims:
        return
    lines.append(title)
    for claim in selected_claims:
        suffix_parts = [claim.verification_status, claim.confidence]
        if claim.next_probe:
            suffix_parts.append(f"next: {claim.next_probe}")
        lines.append(f"- {claim.claim} ({'; '.join(suffix_parts)})")
    lines.append("")


def _claims_payload(claims: list[SelfAwarenessClaim]) -> list[dict[str, Any]]:
    return [claim.to_payload() for claim in claims]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
