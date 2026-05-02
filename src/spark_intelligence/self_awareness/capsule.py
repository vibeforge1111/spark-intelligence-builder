from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.context.capsule import build_spark_context_capsule
from spark_intelligence.memory.orchestrator import export_memory_dashboard_movement_in_memory
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
class CapabilityEvidence:
    capability_key: str
    source: str
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_reason: str | None = None
    route_latency_ms: int | None = None
    eval_coverage_status: str = "unknown"
    evidence_count: int = 0

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "capability_key": self.capability_key,
            "source": self.source,
            "eval_coverage_status": self.eval_coverage_status,
            "evidence_count": self.evidence_count,
        }
        if self.last_success_at:
            payload["last_success_at"] = self.last_success_at
        if self.last_failure_at:
            payload["last_failure_at"] = self.last_failure_at
        if self.last_failure_reason:
            payload["last_failure_reason"] = self.last_failure_reason
        if self.route_latency_ms is not None:
            payload["route_latency_ms"] = self.route_latency_ms
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
    capability_evidence: list[CapabilityEvidence] = field(default_factory=list)
    recommended_probes: list[str] = field(default_factory=list)
    natural_language_routes: list[str] = field(default_factory=list)
    source_ledger: list[dict[str, Any]] = field(default_factory=list)
    style_lens: dict[str, Any] = field(default_factory=dict)
    memory_movement: dict[str, Any] = field(default_factory=dict)

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
            "capability_evidence": _capability_evidence_payload(self.capability_evidence),
            "recommended_probes": self.recommended_probes,
            "natural_language_routes": self.natural_language_routes,
            "source_ledger": self.source_ledger,
            "style_lens": self.style_lens,
            "memory_movement": self.memory_movement,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        short_version = (
            "Short version: I can see the live Spark stack. I should stay grounded and prove a route worked before I sound certain."
        )
        if self.style_lens:
            short_version = (
                "Short version: I can see the live Spark stack. I should keep the answer grounded, "
                "but it should sound like your Spark instead of a pasted status report."
            )
        lines = [
            "Spark self-awareness",
            "",
            short_version,
            "",
            f"Workspace: {self.workspace_id}",
            f"Checked: {self.generated_at}",
            "",
        ]
        _extend_style_lens_lines(lines, self.style_lens)
        _extend_claim_lines(lines, "What looks live", self.observed_now, limit=4, compact=True)
        _extend_claim_lines(lines, "What I recently proved", self.recently_verified, limit=2, compact=True)
        _extend_capability_evidence_lines(lines, self.capability_evidence, limit=3)
        _extend_memory_movement_lines(lines, self.memory_movement)
        _extend_claim_lines(lines, "Where I am useful", self.inferred_strengths, limit=2, compact=True)
        _extend_claim_lines(lines, "Where I still lack", self.lacks, limit=3, compact=True)
        _extend_claim_lines(lines, "What I should improve next", self.improvement_options, limit=3, compact=True)
        routes = [route for route in self.natural_language_routes[:2] if route]
        if routes:
            lines.append("Good next probes")
            lines.extend(f"- {_compact_route_text(item)}" for item in routes)
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
    personality_profile: dict[str, Any] | None = None,
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
    capability_evidence = _build_capability_evidence(state_db)
    recently_verified = _build_recent_invocation_claims(state_db, capability_evidence=capability_evidence)
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
    style_lens = _build_style_lens(personality_profile)
    memory_movement = export_memory_dashboard_movement_in_memory(config_manager=config_manager, limit=6)

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
            "present": bool(capability_evidence),
            "claim_boundary": "Recent route/tool outcomes only. A previous success can go stale and absence is not proof of absence.",
            "capability_count": len(capability_evidence),
        },
        {
            "source": "memory_dashboard_movement",
            "source_kind": "memory_lifecycle_observability",
            "present": memory_movement.get("status") == "supported",
            "claim_boundary": "Trace evidence only. Movement rows are not prompt instructions and do not override current-state memory.",
            "authority": memory_movement.get("authority") or "observability_non_authoritative",
            "row_count": int(memory_movement.get("row_count") or 0),
            "movement_counts": dict(memory_movement.get("movement_counts") or {}),
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
        capability_evidence=capability_evidence,
        recommended_probes=recommended_probes,
        natural_language_routes=natural_language_routes,
        source_ledger=source_ledger,
        style_lens=style_lens,
        memory_movement=memory_movement,
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


def _build_recent_invocation_claims(
    state_db: StateDB,
    *,
    capability_evidence: list[CapabilityEvidence] | None = None,
) -> list[SelfAwarenessClaim]:
    if capability_evidence is not None:
        claims: list[SelfAwarenessClaim] = []
        for evidence in capability_evidence[:8]:
            if evidence.last_success_at:
                claims.append(
                    SelfAwarenessClaim(
                        claim=(
                            f"Capability {evidence.capability_key} last succeeded at {evidence.last_success_at}"
                            f"{f' with latency={evidence.route_latency_ms}ms' if evidence.route_latency_ms is not None else ''}."
                        ),
                        source=evidence.source,
                        source_kind="capability_evidence",
                        confidence="medium",
                        verification_status="recent_success",
                        freshness=evidence.last_success_at,
                        capability_key=evidence.capability_key,
                        next_probe="Repeat the capability route if the user needs current proof.",
                    )
                )
            elif evidence.last_failure_at:
                claims.append(
                    SelfAwarenessClaim(
                        claim=f"Capability {evidence.capability_key} last failed at {evidence.last_failure_at}: {evidence.last_failure_reason or 'unknown failure'}.",
                        source=evidence.source,
                        source_kind="capability_evidence",
                        confidence="high",
                        verification_status="recent_failure",
                        freshness=evidence.last_failure_at,
                        capability_key=evidence.capability_key,
                        next_probe="Repair or rerun the capability route before claiming it is healthy.",
                    )
                )
        return claims[:8]
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


def _build_capability_evidence(state_db: StateDB) -> list[CapabilityEvidence]:
    rows: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for event_type in ("tool_result_received", "dispatch_failed"):
        try:
            events.extend(latest_events_by_type(state_db, event_type=event_type, limit=80))
        except Exception:
            continue
    events.sort(key=lambda event: str(event.get("created_at") or ""), reverse=True)
    for event in events:
        capability_key = _capability_key_for_event(event)
        if not capability_key:
            continue
        row = rows.setdefault(
            capability_key,
            {
                "capability_key": capability_key,
                "source": "",
                "last_success_at": None,
                "last_failure_at": None,
                "last_failure_reason": None,
                "route_latency_ms": None,
                "eval_coverage_status": "unknown",
                "evidence_count": 0,
            },
        )
        row["evidence_count"] = int(row.get("evidence_count") or 0) + 1
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        created_at = str(event.get("created_at") or "").strip() or None
        event_id = str(event.get("event_id") or "").strip()
        if not row.get("source") and event_id:
            row["source"] = f"event:{event_id}"
        latency = _latency_ms(facts)
        if latency is not None and row.get("route_latency_ms") is None:
            row["route_latency_ms"] = latency
        if _event_has_eval_coverage(event):
            row["eval_coverage_status"] = "observed"
        if str(event.get("event_type") or "") == "tool_result_received" and row.get("last_success_at") is None:
            row["last_success_at"] = created_at
        if str(event.get("event_type") or "") == "dispatch_failed" and row.get("last_failure_at") is None:
            row["last_failure_at"] = created_at
            row["last_failure_reason"] = _failure_reason(event)
    return [
        CapabilityEvidence(
            capability_key=str(row["capability_key"]),
            source=str(row.get("source") or "observability_events"),
            last_success_at=row.get("last_success_at"),
            last_failure_at=row.get("last_failure_at"),
            last_failure_reason=row.get("last_failure_reason"),
            route_latency_ms=row.get("route_latency_ms"),
            eval_coverage_status=str(row.get("eval_coverage_status") or "unknown"),
            evidence_count=int(row.get("evidence_count") or 0),
        )
        for row in sorted(
            rows.values(),
            key=lambda item: str(item.get("last_success_at") or item.get("last_failure_at") or ""),
            reverse=True,
        )
    ][:12]


def _capability_key_for_event(event: dict[str, Any]) -> str:
    facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
    provenance = event.get("provenance_json") if isinstance(event.get("provenance_json"), dict) else {}
    candidates = (
        facts.get("capability_key"),
        facts.get("active_chip_key"),
        facts.get("chip_key"),
        facts.get("routing_decision"),
        facts.get("bridge_mode"),
        event.get("reason_code"),
        provenance.get("source_ref"),
        event.get("component"),
    )
    for candidate in candidates:
        key = str(candidate or "").strip()
        if key:
            return key.replace(" ", "_")
    return ""


def _latency_ms(facts: dict[str, Any]) -> int | None:
    for key in ("route_latency_ms", "latency_ms", "duration_ms", "elapsed_ms"):
        value = facts.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _event_has_eval_coverage(event: dict[str, Any]) -> bool:
    facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
    haystack = " ".join(
        str(value)
        for value in (
            facts.get("eval_coverage_status"),
            facts.get("eval_suite"),
            facts.get("test_name"),
            facts.get("coverage"),
            event.get("summary"),
        )
        if value is not None
    ).casefold()
    return any(token in haystack for token in ("eval", "test", "coverage", "regression"))


def _failure_reason(event: dict[str, Any]) -> str:
    facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
    for key in ("failure_reason", "error_code", "error", "message"):
        value = str(facts.get(key) or "").strip()
        if value:
            return value[:240]
    return str(event.get("summary") or event.get("status") or "dispatch_failed").strip()[:240]



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
    compact: bool = False,
) -> None:
    selected_claims = claims[:limit] if limit is not None else claims
    if not selected_claims:
        return
    lines.append(title)
    for claim in selected_claims:
        if compact:
            lines.append(f"- {_compact_claim_text(claim)}")
            continue
        suffix_parts = [claim.verification_status, claim.confidence]
        if claim.next_probe:
            suffix_parts.append(f"next: {claim.next_probe}")
        lines.append(f"- {claim.claim} ({'; '.join(suffix_parts)})")
    lines.append("")


def _extend_capability_evidence_lines(
    lines: list[str],
    evidence_rows: list[CapabilityEvidence],
    *,
    limit: int,
) -> None:
    selected_rows = evidence_rows[:limit]
    if not selected_rows:
        return
    lines.append("Capability evidence")
    for evidence in selected_rows:
        status = "last success" if evidence.last_success_at else "last failure" if evidence.last_failure_at else "seen"
        timestamp = evidence.last_success_at or evidence.last_failure_at or "unknown time"
        extras: list[str] = []
        if evidence.route_latency_ms is not None:
            extras.append(f"{evidence.route_latency_ms}ms")
        if evidence.eval_coverage_status != "unknown":
            extras.append(f"eval={evidence.eval_coverage_status}")
        if evidence.last_failure_reason and not evidence.last_success_at:
            extras.append(evidence.last_failure_reason)
        suffix = f" ({'; '.join(extras)})" if extras else ""
        lines.append(f"- {evidence.capability_key}: {status} at {timestamp}{suffix}")
    lines.append("")


def _extend_memory_movement_lines(lines: list[str], movement: dict[str, Any]) -> None:
    if not isinstance(movement, dict) or movement.get("status") != "supported":
        return
    counts = movement.get("movement_counts") if isinstance(movement.get("movement_counts"), dict) else {}
    ordered_states = [
        "captured",
        "blocked",
        "promoted",
        "saved",
        "decayed",
        "summarized",
        "retrieved",
        "selected",
        "dropped",
    ]
    parts = [
        f"{state}={_positive_count(counts.get(state))}"
        for state in ordered_states
        if _positive_count(counts.get(state)) > 0
    ]
    if not parts:
        return
    lines.append("Memory movement")
    lines.append(f"- Trace rows: {', '.join(parts[:8])}.")
    lines.append("- These rows are observability evidence, not instructions or authority over current-state memory.")
    lines.append("")


def _positive_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _compact_claim_text(claim: SelfAwarenessClaim) -> str:
    text = claim.claim.strip()
    text = text.replace("Spark Intelligence Builder", "Builder")
    text = text.replace("Spark Local Work", "Local Work")
    text = text.replace("Telegram adapter", "Telegram")
    marker = " is visible in the Builder registry with status="
    if marker in text:
        name, rest = text.split(marker, 1)
        status = rest.split(".", 1)[0].strip()
        return f"{name}: {status}"
    marker = " is not fully healthy or available: status="
    if marker in text:
        name, rest = text.split(marker, 1)
        status = rest.split(".", 1)[0].strip()
        limit = ""
        if "Main limit:" in rest:
            limit = rest.split("Main limit:", 1)[1].strip().rstrip(".")
        return f"{name}: {status}{f' - {limit}' if limit else ''}"
    if text.startswith("Recent tool_result_received:"):
        short = text.replace("Recent tool_result_received:", "Route worked recently:")
        return short.replace(" status=recorded.", "").strip()
    if text.startswith("Spark can describe current capabilities"):
        return "I can map Spark capabilities from the registry, but I need route checks for proof."
    if text.startswith("Spark can use turn context sources"):
        return "I can use current state, diagnostics, runtime capabilities, and workflow context for continuity."
    if text.startswith("Spark is strongest when"):
        return "I am best when I separate live evidence, memory, and inference."
    if text.startswith("Registry visibility does not prove"):
        return "Seeing a system in the registry is not proof it worked this turn."
    if text.startswith("Spark cannot inspect secrets"):
        return "I cannot inspect secrets or private infra unless a safe diagnostic exposes redacted status."
    if text.startswith("Natural-language invocability"):
        return "Natural-language control only counts when the phrase maps to an authorized, traceable route."
    if text.startswith("Repair configuration"):
        return text.replace("Repair configuration, auth, attachment, or runtime readiness for ", "Repair readiness for ")
    if text.startswith("Add per-capability"):
        return "Track last_success_at, last_failure_reason, latency, and eval coverage per capability."
    if text.startswith("Add redacted health"):
        return "Expose redacted health summaries for secret-bound systems."
    return text


def _compact_route_text(route: str) -> str:
    route = route.strip()
    if route.startswith("Ask: "):
        route = route[len("Ask: ") :]
    return route


def _build_style_lens(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict) or not profile:
        return {}
    traits = profile.get("traits")
    if not isinstance(traits, dict):
        traits = {}
    rules = [
        str(rule).strip()
        for rule in (profile.get("agent_behavioral_rules") or [])
        if str(rule).strip()
    ][:3]
    lens = {
        "persona_name": _clean_style_text(profile.get("agent_persona_name") or profile.get("personality_name")),
        "persona_summary": _clean_style_text(profile.get("agent_persona_summary")),
        "style_sentence": _style_sentence_from_traits(traits),
        "agent_persona_applied": bool(profile.get("agent_persona_applied")),
        "user_deltas_applied": bool(profile.get("user_deltas_applied")),
        "behavioral_rules": rules,
    }
    return {key: value for key, value in lens.items() if value not in ("", [], None)}


def _extend_style_lens_lines(lines: list[str], style_lens: dict[str, Any]) -> None:
    if not style_lens:
        return
    style_sentence = str(style_lens.get("style_sentence") or "").strip()
    persona_summary = str(style_lens.get("persona_summary") or "").strip()
    rules = [
        str(rule).strip()
        for rule in (style_lens.get("behavioral_rules") or [])
        if str(rule).strip()
    ][:2]

    lines.append("How I am tuned for you")
    if persona_summary:
        lines.append(f"- I should {_humanize_style_instruction(persona_summary)}.")
    if style_sentence:
        lines.append(f"- Tone: {style_sentence}.")
    if rules and not persona_summary:
        lines.append(f"- Style promises: {'; '.join(rules)}.")
    if style_lens.get("user_deltas_applied"):
        lines.append("- I should let that tuning shape the answer, while keeping the evidence visible.")
    lines.append("")


def _style_sentence_from_traits(traits: dict[str, Any]) -> str:
    if not traits:
        return ""
    fragments: list[str] = []
    warmth = _float_trait(traits.get("warmth"))
    directness = _float_trait(traits.get("directness"))
    playfulness = _float_trait(traits.get("playfulness"))
    pacing = _float_trait(traits.get("pacing"))
    assertiveness = _float_trait(traits.get("assertiveness"))

    if directness >= 0.68:
        fragments.append("direct")
    elif directness <= 0.32:
        fragments.append("gentle")
    if warmth >= 0.65:
        fragments.append("warm")
    elif warmth <= 0.28:
        fragments.append("reserved")
    if pacing >= 0.68:
        fragments.append("fast-moving")
    elif pacing <= 0.32:
        fragments.append("unhurried")
    if playfulness >= 0.68:
        fragments.append("a little playful")
    elif playfulness <= 0.25:
        fragments.append("serious")
    if assertiveness >= 0.72:
        fragments.append("decisive")
    elif assertiveness <= 0.28:
        fragments.append("measured")

    fragments = _dedupe_preserving_order(fragments)
    if not fragments:
        return "balanced, grounded, and evidence-first"
    if len(fragments) == 1:
        return f"{fragments[0]}, grounded, and evidence-first"
    return f"{_human_join(fragments)}, while staying evidence-first"


def _float_trait(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _clean_style_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:220].rstrip()


def _humanize_style_instruction(value: str) -> str:
    parts = [part.strip(" .") for part in value.replace("\n", ";").split(";") if part.strip(" .")]
    cleaned: list[str] = []
    for part in parts[:3]:
        lower_part = part.lower()
        if lower_part.startswith("do not say "):
            part = f"avoid saying {part[11:]}"
        elif lower_part.startswith("don't say "):
            part = f"avoid saying {part[10:]}"
        elif lower_part.startswith("dont say "):
            part = f"avoid saying {part[9:]}"
        elif lower_part.startswith("do not claim "):
            part = f"avoid claiming {part[13:]}"
        elif lower_part.startswith("don't claim "):
            part = f"avoid claiming {part[12:]}"
        elif lower_part.startswith("dont claim "):
            part = f"avoid claiming {part[11:]}"
        elif lower_part.startswith("do not "):
            part = f"avoid {part[7:]}"
        elif lower_part.startswith("don't "):
            part = f"avoid {part[6:]}"
        elif lower_part.startswith("dont "):
            part = f"avoid {part[5:]}"
        cleaned.append(part[:1].lower() + part[1:])
    if not cleaned:
        return value
    return _human_join(cleaned)


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _human_join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _claims_payload(claims: list[SelfAwarenessClaim]) -> list[dict[str, Any]]:
    return [claim.to_payload() for claim in claims]


def _capability_evidence_payload(evidence_rows: list[CapabilityEvidence]) -> list[dict[str, Any]]:
    return [evidence.to_payload() for evidence in evidence_rows]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
