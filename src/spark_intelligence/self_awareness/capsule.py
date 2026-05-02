from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.context.capsule import build_spark_context_capsule
from spark_intelligence.memory import inspect_memory_movement_status, inspect_memory_sdk_runtime, inspect_wiki_packet_metadata
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
    eval_coverage_status: str = "missing"
    eval_coverage_sources: list[str] = field(default_factory=list)
    evidence_count: int = 0
    confidence_level: str = "unknown"
    freshness_status: str = "unknown"
    goal_relevance: str = "unknown"
    can_claim_confidently: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "capability_key": self.capability_key,
            "source": self.source,
            "eval_coverage_status": self.eval_coverage_status,
            "eval_coverage_sources": list(self.eval_coverage_sources),
            "evidence_count": self.evidence_count,
            "confidence_level": self.confidence_level,
            "freshness_status": self.freshness_status,
            "goal_relevance": self.goal_relevance,
            "can_claim_confidently": self.can_claim_confidently,
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
    memory_cognition: dict[str, Any] = field(default_factory=dict)
    user_awareness: dict[str, Any] = field(default_factory=dict)
    project_awareness: dict[str, Any] = field(default_factory=dict)
    capability_probe_registry: list[dict[str, Any]] = field(default_factory=list)

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
            "memory_cognition": self.memory_cognition,
            "user_awareness": self.user_awareness,
            "project_awareness": self.project_awareness,
            "capability_probe_registry": self.capability_probe_registry,
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
        _extend_memory_cognition_lines(lines, self.memory_cognition)
        _extend_user_awareness_lines(lines, self.user_awareness)
        _extend_project_awareness_lines(lines, self.project_awareness)
        _extend_claim_lines(lines, "What looks live", self.observed_now, limit=4, compact=True)
        _extend_claim_lines(lines, "What I recently proved", self.recently_verified, limit=2, compact=True)
        _extend_capability_evidence_lines(lines, self.capability_evidence, limit=3)
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
    capability_evidence = _build_capability_evidence(state_db, user_message=user_message)
    recently_verified = _build_recent_invocation_claims(state_db, capability_evidence=capability_evidence)
    available_unverified = _build_available_unverified_claims(records)
    degraded_or_missing = _build_degraded_claims(records)
    inferred_strengths = _build_strength_claims(registry_payload=registry_payload, context_capsule=context_capsule)
    lacks = _build_lack_claims(records=records, degraded_claims=degraded_or_missing)
    improvement_options = _build_improvement_claims(lacks=lacks, degraded_claims=degraded_or_missing)
    capability_probe_registry = _build_capability_probe_registry(records)
    recommended_probes = _recommended_probes(
        degraded_claims=degraded_or_missing,
        probe_registry=capability_probe_registry,
    )
    memory_cognition = _build_memory_cognition(config_manager)
    user_awareness = _build_user_awareness(
        config_manager=config_manager,
        context_capsule=context_capsule,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        personality_profile=personality_profile,
    )
    project_awareness = _build_project_awareness(
        config_manager=config_manager,
        registry_payload=registry_payload,
    )
    natural_language_routes = [
        "Ask: 'Spark, what do you know about your current systems?' to get the grounded registry view.",
        "Ask: 'Spark, test the browser route now' to turn browser availability into last-success evidence.",
        "Ask: 'Spark, check which chips are active and what they can improve for this goal' to map capability fit.",
        "Ask: 'Spark, improve the weak spots you just found' to run the safest next probes before changing behavior.",
    ]
    style_lens = _build_style_lens(personality_profile)

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
            "source": "memory_cognition",
            "source_kind": "memory_runtime_and_wiki_metadata",
            "present": bool(memory_cognition),
            "claim_boundary": "Memory KB wiki pages are supporting context; current-state memory remains authoritative for mutable user facts.",
            "source_families_visible": bool((memory_cognition.get("wiki_packets") or {}).get("source_families_visible")),
        },
        {
            "source": "user_awareness",
            "source_kind": "scoped_user_context_summary",
            "present": bool(user_awareness.get("present")),
            "claim_boundary": "User awareness is scoped to the active human/session; user context is not global Spark doctrine.",
            "label_counts": dict(user_awareness.get("label_counts") or {}),
        },
        {
            "source": "project_awareness",
            "source_kind": "local_project_index_summary",
            "present": bool(project_awareness.get("present")),
            "claim_boundary": "Project awareness is a registry/config snapshot; live git, filesystem, CI, and tool output outrank it.",
            "project_count": int(project_awareness.get("project_count") or 0),
        },
        {
            "source": "capability_probe_registry",
            "source_kind": "safe_probe_contract",
            "present": bool(capability_probe_registry),
            "claim_boundary": "Probe entries describe safe checks; a probe must actually run before Spark claims current success.",
            "probe_count": len(capability_probe_registry),
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
        memory_cognition=memory_cognition,
        user_awareness=user_awareness,
        project_awareness=project_awareness,
        capability_probe_registry=capability_probe_registry,
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


def _build_capability_evidence(state_db: StateDB, *, user_message: str = "") -> list[CapabilityEvidence]:
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
                "eval_coverage_status": "missing",
                "eval_coverage_sources": [],
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
        eval_status, eval_sources = _event_eval_coverage(event)
        if eval_status == "covered" or (eval_status == "observed" and row.get("eval_coverage_status") == "missing"):
            row["eval_coverage_status"] = eval_status
        row["eval_coverage_sources"] = list(
            dict.fromkeys([*list(row.get("eval_coverage_sources") or []), *eval_sources])
        )[:8]
        if str(event.get("event_type") or "") == "tool_result_received" and row.get("last_success_at") is None:
            row["last_success_at"] = created_at
        if str(event.get("event_type") or "") == "dispatch_failed" and row.get("last_failure_at") is None:
            row["last_failure_at"] = created_at
            row["last_failure_reason"] = _failure_reason(event)
    query_tokens = _tokens(user_message)
    evidence_rows: list[CapabilityEvidence] = []
    for row in sorted(
        rows.values(),
        key=lambda item: str(item.get("last_success_at") or item.get("last_failure_at") or ""),
        reverse=True,
    ):
        confidence = _capability_confidence(row)
        freshness = _capability_freshness(row)
        goal_relevance = _capability_goal_relevance(str(row["capability_key"]), query_tokens)
        evidence_rows.append(
            CapabilityEvidence(
                capability_key=str(row["capability_key"]),
                source=str(row.get("source") or "observability_events"),
                last_success_at=row.get("last_success_at"),
                last_failure_at=row.get("last_failure_at"),
                last_failure_reason=row.get("last_failure_reason"),
                route_latency_ms=row.get("route_latency_ms"),
                eval_coverage_status=str(row.get("eval_coverage_status") or "missing"),
                eval_coverage_sources=[str(item) for item in row.get("eval_coverage_sources") or [] if str(item).strip()],
                evidence_count=int(row.get("evidence_count") or 0),
                confidence_level=confidence,
                freshness_status=freshness,
                goal_relevance=goal_relevance,
                can_claim_confidently=confidence == "recent_success" and freshness == "fresh",
            )
        )
    return evidence_rows[:12]


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


def _event_eval_coverage(event: dict[str, Any]) -> tuple[str, list[str]]:
    facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
    explicit_status = str(facts.get("eval_coverage_status") or "").strip().casefold()
    source_candidates = [
        str(value).strip()
        for value in (
            facts.get("eval_ref"),
            facts.get("eval_suite"),
            facts.get("test_name"),
            facts.get("coverage"),
            event.get("summary"),
        )
        if str(value or "").strip()
    ]
    if explicit_status in {"missing", "observed", "covered"}:
        return explicit_status, source_candidates[:8]
    haystack = " ".join(source_candidates).casefold()
    if any(token in haystack for token in ("eval", "test", "coverage", "regression", "pytest", "smoke")):
        return "covered", source_candidates[:8]
    if source_candidates:
        return "observed", source_candidates[:4]
    return "missing", []


def _failure_reason(event: dict[str, Any]) -> str:
    facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
    for key in ("failure_reason", "error_code", "error", "message"):
        value = str(facts.get(key) or "").strip()
        if value:
            return value[:240]
    return str(event.get("summary") or event.get("status") or "dispatch_failed").strip()[:240]


def _capability_confidence(row: dict[str, Any]) -> str:
    last_success = _parse_iso(row.get("last_success_at"))
    last_failure = _parse_iso(row.get("last_failure_at"))
    if last_failure and (last_success is None or last_failure >= last_success):
        return "recent_failure"
    if last_success is None:
        return "observed_without_success"
    age_days = max(0, (datetime.now(UTC) - last_success).days)
    if age_days <= 7:
        return "recent_success"
    return "stale_success"


def _capability_freshness(row: dict[str, Any]) -> str:
    latest = _parse_iso(row.get("last_success_at")) or _parse_iso(row.get("last_failure_at"))
    if latest is None:
        return "unknown"
    age_days = max(0, (datetime.now(UTC) - latest).days)
    if age_days <= 7:
        return "fresh"
    if age_days <= 30:
        return "aging"
    return "stale"


def _capability_goal_relevance(capability_key: str, query_tokens: set[str]) -> str:
    if not query_tokens:
        return "unknown"
    capability_tokens = _tokens(capability_key.replace("_", " "))
    if capability_tokens & query_tokens:
        return "direct"
    return "not_detected"


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", str(value or "").casefold())}



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


def _build_memory_cognition(config_manager: ConfigManager) -> dict[str, Any]:
    try:
        runtime = inspect_memory_sdk_runtime(config_manager=config_manager)
    except Exception as exc:
        runtime = {"ready": False, "reason": f"runtime_inspection_failed:{exc.__class__.__name__}"}
    try:
        wiki_metadata = inspect_wiki_packet_metadata(config_manager=config_manager)
    except Exception as exc:
        wiki_metadata = {
            "status": "error",
            "reason": f"wiki_metadata_inspection_failed:{exc.__class__.__name__}",
            "source_families_visible": False,
            "memory_kb": {"present": False, "packet_count": 0, "family_counts": {}},
        }
    try:
        movement_status = inspect_memory_movement_status(config_manager=config_manager)
    except Exception as exc:
        movement_status = {
            "status": "error",
            "reason": f"movement_inspection_failed:{exc.__class__.__name__}",
            "movement_counts": {},
            "authority": "observability_non_authoritative",
        }

    wiki_packets = {
        "status": wiki_metadata.get("status"),
        "packet_count": int(wiki_metadata.get("packet_count") or 0),
        "wiki_family_counts": dict(wiki_metadata.get("wiki_family_counts") or {}),
        "owner_system_counts": dict(wiki_metadata.get("owner_system_counts") or {}),
        "source_of_truth_counts": dict(wiki_metadata.get("source_of_truth_counts") or {}),
        "authority_counts": dict(wiki_metadata.get("authority_counts") or {}),
        "source_families_visible": bool(wiki_metadata.get("source_families_visible")),
        "memory_kb": dict(wiki_metadata.get("memory_kb") or {}),
    }
    payload: dict[str, Any] = {
        "runtime": {
            "ready": bool(runtime.get("ready")),
            "client_kind": runtime.get("client_kind"),
            "runtime_memory_architecture": runtime.get("runtime_memory_architecture"),
            "runtime_memory_provider": runtime.get("runtime_memory_provider"),
            "reason": runtime.get("reason"),
        },
        "wiki_packets": wiki_packets,
        "movement": {
            "status": movement_status.get("status"),
            "authority": movement_status.get("authority") or "observability_non_authoritative",
            "movement_counts": dict(movement_status.get("movement_counts") or {}),
            "row_count": int(movement_status.get("row_count") or 0),
            "non_override_rules": list(movement_status.get("non_override_rules") or [])[:4],
        },
        "authority_boundary": "current_state_memory_outranks_wiki_for_mutable_user_facts",
    }
    if wiki_packets["source_families_visible"]:
        payload["self_status_memory"] = {
            "can_name_source_families": True,
            "can_detect_memory_kb": bool((wiki_packets.get("memory_kb") or {}).get("present")),
            "wiki_authority": "supporting_not_authoritative",
            "current_state_for_mutable_user_facts": "authoritative",
            "graphiti_status": "advisory_until_evals_pass",
            "residue_promotion": "blocked",
        }
    return payload


def _build_user_awareness(
    *,
    config_manager: ConfigManager,
    context_capsule: Any,
    human_id: str,
    session_id: str,
    channel_kind: str,
    personality_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_human_id = str(human_id or "").strip()
    current_state_lines = _context_lines(context_capsule, "current_state")
    pending_task_lines = _context_lines(context_capsule, "pending_tasks")
    recent_conversation_lines = _context_lines(context_capsule, "recent_conversation")
    current_state_items = [_parse_context_line(line) for line in current_state_lines]
    current_state_items = [item for item in current_state_items if item]
    stable_context = [
        _user_context_entry(
            kind=f"user_{item['label']}",
            label="stable",
            value=item["value"],
            source="context_capsule.current_state",
            source_kind="governed_current_state_memory",
        )
        for item in current_state_items
        if item["label"] in {"preferred_name", "startup", "founder_of", "occupation", "city", "country", "timezone"}
    ]
    current_goal = _first_entry_for_labels(
        current_state_items,
        labels=("current_focus", "current_plan", "current_milestone"),
        kind="current_goal",
        label="recent",
    )
    recent_decisions = [
        _user_context_entry(
            kind=item["label"],
            label="recent",
            value=item["value"],
            source="context_capsule.current_state",
            source_kind="governed_current_state_memory",
        )
        for item in current_state_items
        if item["label"] in {"current_decision", "current_constraint", "current_blocker", "current_status"}
    ][:6]
    stable_preferences: list[dict[str, Any]] = []
    if isinstance(personality_profile, dict) and personality_profile.get("user_deltas_applied"):
        stable_preferences.append(
            _user_context_entry(
                kind="style_preference_overlay",
                label="stable",
                value="active",
                source="personality_profile.user_deltas_applied",
                source_kind="personality_preference_overlay",
            )
        )
    stable_preferences.extend(stable_context[:6])
    if not current_goal and recent_conversation_lines:
        current_goal = _user_context_entry(
            kind="current_goal",
            label="inferred",
            value="recent conversation is available, but no governed current focus/plan was found",
            source="context_capsule.recent_conversation",
            source_kind="recent_turn_context",
        )

    user_wiki = _user_wiki_context(config_manager=config_manager, human_id=normalized_human_id)
    label_counts = _label_counts(
        [
            *(stable_preferences or []),
            *([current_goal] if current_goal else []),
            *recent_decisions,
            *(
                [
                    {
                        "label": "candidate",
                    }
                ]
                if user_wiki.get("candidate_note_count")
                else []
            ),
        ]
    )
    payload: dict[str, Any] = {
        "present": bool(normalized_human_id or stable_preferences or current_goal or recent_decisions or user_wiki.get("candidate_note_count")),
        "human_id": normalized_human_id or None,
        "session_id": str(session_id or "").strip() or None,
        "channel_kind": str(channel_kind or "").strip() or None,
        "scope_kind": "user_specific" if normalized_human_id else "unknown_user",
        "current_goal": current_goal
        or _user_context_entry(
            kind="current_goal",
            label="unknown",
            value="no governed current focus or plan found",
            source="context_capsule.current_state",
            source_kind="governed_current_state_memory",
        ),
        "stable_preferences": stable_preferences[:8],
        "recent_decisions": recent_decisions[:6],
        "pending_task_count": len(pending_task_lines),
        "recent_conversation_turn_count": len(recent_conversation_lines),
        "user_wiki_context": user_wiki,
        "label_counts": label_counts,
        "source_labels": {
            "stable": "durable governed memory or explicit personality preference overlay",
            "recent": "current-state memory or open task context",
            "inferred": "derived from recent context and must be confirmed",
            "unknown": "not visible in this capsule",
            "candidate": "consent-bounded wiki note that cannot override current-state memory",
        },
        "boundaries": [
            "user_memory_stays_separate_from_global_spark_doctrine",
            "governed_current_state_memory_outranks_user_wiki_notes_for_mutable_facts",
            "recent_conversation_is_continuity_context_not_promotion_evidence",
            "candidate_user_wiki_notes_require_consent_and_revalidation_before_reuse",
        ],
    }
    return payload


def _build_project_awareness(*, config_manager: ConfigManager, registry_payload: dict[str, Any]) -> dict[str, Any]:
    records = [record for record in (registry_payload.get("records") or []) if isinstance(record, dict)]
    repo_records = [record for record in records if str(record.get("kind") or "") == "repo"]
    workspace = config_manager.load().get("workspace") or {}
    if not isinstance(workspace, dict):
        workspace = {}
    workspace_home = str(workspace.get("home") or config_manager.paths.home).strip()
    projects = [_project_context_entry(record) for record in repo_records]
    active_project = _active_project(projects=projects, workspace_home=workspace_home)
    summary = registry_payload.get("summary") if isinstance(registry_payload.get("summary"), dict) else {}
    return {
        "present": bool(projects or workspace_home),
        "workspace_id": str(workspace.get("id") or registry_payload.get("workspace_id") or "default"),
        "workspace_home": workspace_home,
        "active_project": active_project,
        "project_count": len(projects),
        "known_project_keys": [str(project.get("key") or "") for project in projects if project.get("key")][:20],
        "projects": projects[:20],
        "repo_summary": {
            "repo_count": int(summary.get("repo_count") or len(projects)),
            "dirty_repo_count": int(summary.get("dirty_repo_count") or 0),
        },
        "source_refs": [
            "system_registry:repo_records",
            "local_project_index",
            "config.workspace.home",
        ],
        "authority": "observed_configuration_not_live_git_truth",
        "boundaries": [
            "live_git_status_outranks_project_awareness_snapshot",
            "live_filesystem_checks_outrank_project_awareness_snapshot",
            "project_awareness_does_not_select_write_targets_without_user_intent",
            "source_refs_required_for_project_claims",
        ],
    }


def _build_capability_probe_registry(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    probe_records: list[dict[str, Any]] = []
    for record in records:
        kind = str(record.get("kind") or "").strip()
        key = str(record.get("key") or "").strip()
        if not key or kind not in {"system", "adapter", "provider", "chip", "path", "repo"}:
            continue
        probe_records.append(
            {
                "target_kind": kind,
                "target_key": key,
                "label": str(record.get("label") or key).strip(),
                "status": str(record.get("status") or "unknown").strip(),
                "available": bool(record.get("available")),
                "active": bool(record.get("active")),
                "safe_probe": _safe_probe_for_record(kind=kind, key=key),
                "access_boundary": _probe_access_boundary(kind=kind),
                "claim_boundary": "configured_or_available_is_not_recent_success",
                "records_current_success": False,
            }
        )
    probe_records.sort(key=lambda item: (str(item.get("target_kind") or ""), str(item.get("target_key") or "")))
    return probe_records[:40]


def _safe_probe_for_record(*, kind: str, key: str) -> str:
    if kind == "adapter" and key == "telegram":
        return "spark-intelligence channel test telegram --json"
    if kind == "provider":
        return "spark-intelligence auth status --json"
    if kind == "chip":
        return f"spark-intelligence chips why 'test {key} capability' --json"
    if kind == "path":
        return "spark-intelligence attachments status --json"
    if kind == "repo":
        return f"git -C <repo:{key}> status --short"
    if kind == "system" and key == "spark_memory":
        return "spark-intelligence memory status --json"
    if kind == "system" and key == "spark_browser":
        return "spark-intelligence browser status --json"
    if kind == "system" and key == "spark_intelligence_builder":
        return "spark-intelligence self status --json"
    return "spark-intelligence status --json"


def _probe_access_boundary(*, kind: str) -> str:
    if kind in {"system", "adapter", "provider"}:
        return "read_only_health_check"
    if kind in {"chip", "path"}:
        return "read_only_routing_or_attachment_check"
    if kind == "repo":
        return "read_only_filesystem_or_git_check"
    return "read_only_probe"


def _project_context_entry(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    key = str(record.get("key") or "").strip()
    path = str(metadata.get("path") or "").strip()
    return {
        "key": key,
        "label": str(record.get("label") or key or "project").strip(),
        "status": str(record.get("status") or "unknown").strip(),
        "path": path,
        "exists": bool(record.get("available")),
        "is_git": bool(metadata.get("is_git")),
        "dirty": metadata.get("dirty"),
        "branch": metadata.get("branch"),
        "source": str(metadata.get("source") or "unknown").strip(),
        "owner_system": str(metadata.get("owner_system") or "spark_local_work").strip(),
        "components": [str(item).strip() for item in (metadata.get("components") or []) if str(item).strip()][:8],
        "source_ref": f"registry:repo:{key}" if key else "registry:repo",
        "source_kind": "local_project_index",
        "authority": "observed_configuration_not_live_git_truth",
        "live_state_boundary": "refresh git/filesystem/tool checks before claiming current repo state",
    }


def _active_project(*, projects: list[dict[str, Any]], workspace_home: str) -> dict[str, Any]:
    normalized_workspace = workspace_home.casefold()
    for project in projects:
        path = str(project.get("path") or "").casefold()
        if path and path == normalized_workspace:
            return dict(project)
    for project in projects:
        key = str(project.get("key") or "")
        if key == "spark-intelligence-builder":
            return dict(project)
    return dict(projects[0]) if projects else {
        "key": "unknown",
        "label": "Unknown project",
        "status": "unknown",
        "source_ref": "config.workspace.home",
        "source_kind": "workspace_config",
        "authority": "observed_configuration_not_live_git_truth",
        "live_state_boundary": "inspect filesystem/git before claiming project state",
    }


def _context_lines(context_capsule: Any, section: str) -> list[str]:
    sections = getattr(context_capsule, "sections", {}) if context_capsule is not None else {}
    lines = sections.get(section) if isinstance(sections, dict) else []
    return [str(line).strip() for line in (lines or []) if str(line).strip()]


def _parse_context_line(line: str) -> dict[str, str]:
    text = str(line or "").strip()
    if text.startswith("- "):
        text = text[2:].strip()
    if ":" not in text:
        return {}
    label, value = text.split(":", 1)
    value = value.strip()
    if " (as_of=" in value:
        value = value.split(" (as_of=", 1)[0].strip()
    return {"label": label.strip(), "value": value}


def _first_entry_for_labels(
    items: list[dict[str, str]],
    *,
    labels: tuple[str, ...],
    kind: str,
    label: str,
) -> dict[str, Any] | None:
    for item_label in labels:
        for item in items:
            if item.get("label") != item_label:
                continue
            return _user_context_entry(
                kind=kind,
                label=label,
                value=item.get("value") or "",
                source="context_capsule.current_state",
                source_kind="governed_current_state_memory",
            )
    return None


def _user_context_entry(*, kind: str, label: str, value: str, source: str, source_kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "value": str(value or "").strip()[:260],
        "source": source,
        "source_kind": source_kind,
    }


def _user_wiki_context(*, config_manager: ConfigManager, human_id: str) -> dict[str, Any]:
    if not human_id:
        return {
            "status": "unknown_user",
            "candidate_note_count": 0,
            "authority": "supporting_not_authoritative",
            "scope_kind": "user_specific",
        }
    wiki_root = config_manager.paths.home / "wiki"
    note_dir = wiki_root / "users" / _human_slug(human_id) / "candidate-notes"
    note_paths = sorted(note_dir.glob("*.md")) if note_dir.exists() else []
    return {
        "status": "present" if note_paths else "empty",
        "candidate_note_count": len(note_paths),
        "relative_dir": note_dir.relative_to(wiki_root).as_posix() if note_dir.exists() else f"users/{_human_slug(human_id)}/candidate-notes",
        "authority": "supporting_not_authoritative",
        "scope_kind": "user_specific",
        "can_override_current_state_memory": False,
    }


def _human_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "human")[:72].strip("-") or "human"


def _label_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        label = str(item.get("label") or "unknown").strip() or "unknown"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _recommended_probes(
    *,
    degraded_claims: list[SelfAwarenessClaim],
    probe_registry: list[dict[str, Any]] | None = None,
) -> list[str]:
    probes = [
        "Run `spark-intelligence self status --json` before self-knowledge answers that need provenance.",
        "Run a direct health/invocation check for the exact capability the user wants to rely on.",
        "Record last_success_at, last_failure_reason, and route_latency_ms for each important system path.",
    ]
    for probe in list(probe_registry or [])[:4]:
        safe_probe = str(probe.get("safe_probe") or "").strip()
        target_key = str(probe.get("target_key") or "").strip()
        if safe_probe and target_key:
            probes.append(f"{target_key}: {safe_probe}")
    for claim in degraded_claims[:4]:
        if claim.next_probe:
            probes.append(claim.next_probe)
    return list(dict.fromkeys(probes))


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
        if evidence.eval_coverage_status:
            extras.append(f"eval={evidence.eval_coverage_status}")
        if evidence.confidence_level != "unknown":
            extras.append(f"confidence={evidence.confidence_level}")
        if evidence.goal_relevance not in {"unknown", "not_detected"}:
            extras.append(f"goal={evidence.goal_relevance}")
        if evidence.last_failure_reason and not evidence.last_success_at:
            extras.append(evidence.last_failure_reason)
        suffix = f" ({'; '.join(extras)})" if extras else ""
        lines.append(f"- {evidence.capability_key}: {status} at {timestamp}{suffix}")
    lines.append("")


def _extend_memory_cognition_lines(lines: list[str], memory_cognition: dict[str, Any]) -> None:
    if not memory_cognition:
        return
    runtime = memory_cognition.get("runtime") if isinstance(memory_cognition.get("runtime"), dict) else {}
    wiki_packets = (
        memory_cognition.get("wiki_packets") if isinstance(memory_cognition.get("wiki_packets"), dict) else {}
    )
    memory_kb = wiki_packets.get("memory_kb") if isinstance(wiki_packets.get("memory_kb"), dict) else {}
    movement = memory_cognition.get("movement") if isinstance(memory_cognition.get("movement"), dict) else {}
    lines.append("Memory cognition")
    runtime_label = str(runtime.get("runtime_memory_architecture") or runtime.get("client_kind") or "unknown").strip()
    lines.append(f"- Runtime: {'ready' if runtime.get('ready') else 'not ready'} ({runtime_label})")
    lines.append(
        f"- Wiki packets: {wiki_packets.get('status') or 'unknown'}; "
        f"families={'visible' if wiki_packets.get('source_families_visible') else 'not visible'}"
    )
    if memory_kb.get("present"):
        families = memory_kb.get("family_counts") if isinstance(memory_kb.get("family_counts"), dict) else {}
        family_text = ", ".join(f"{key}={value}" for key, value in sorted(families.items())[:4])
        lines.append(f"- Memory KB: present ({family_text or memory_kb.get('packet_count', 0)})")
    movement_counts = movement.get("movement_counts") if isinstance(movement.get("movement_counts"), dict) else {}
    if movement.get("status") == "supported" and movement_counts:
        movement_text = ", ".join(
            f"{state}={int(movement_counts.get(state) or 0)}"
            for state in ("captured", "blocked", "promoted", "saved", "decayed", "summarized", "retrieved")
        )
        lines.append(f"- Memory movement: {movement_text}")
    lines.append("- Wiki authority: supporting_not_authoritative; it helps me orient, but it is not mutable user truth.")
    lines.append("- Boundary: current-state memory wins over wiki for mutable user facts; user memory stays separate from Spark doctrine.")
    lines.append("- Graph sidecar: advisory until evals pass; conversational residue is not promotion evidence.")
    lines.append("")


def _extend_user_awareness_lines(lines: list[str], user_awareness: dict[str, Any]) -> None:
    if not user_awareness:
        return
    label_counts = user_awareness.get("label_counts") if isinstance(user_awareness.get("label_counts"), dict) else {}
    user_wiki = user_awareness.get("user_wiki_context") if isinstance(user_awareness.get("user_wiki_context"), dict) else {}
    current_goal = user_awareness.get("current_goal") if isinstance(user_awareness.get("current_goal"), dict) else {}
    goal_label = str(current_goal.get("label") or "unknown")
    lines.append("User awareness")
    lines.append(f"- Scope: {user_awareness.get('scope_kind') or 'unknown_user'}")
    lines.append(f"- Current goal: {goal_label}")
    lines.append(
        "- Context labels: "
        + ", ".join(f"{key}={value}" for key, value in sorted(label_counts.items()))
        if label_counts
        else "- Context labels: none visible"
    )
    lines.append(f"- User wiki candidates: {int(user_wiki.get('candidate_note_count') or 0)}")
    lines.append("- Boundary: user context helps personalize, but it is not global Spark doctrine.")
    lines.append("")


def _extend_project_awareness_lines(lines: list[str], project_awareness: dict[str, Any]) -> None:
    if not project_awareness:
        return
    active_project = (
        project_awareness.get("active_project")
        if isinstance(project_awareness.get("active_project"), dict)
        else {}
    )
    label = str(active_project.get("label") or active_project.get("key") or "unknown").strip()
    status = str(active_project.get("status") or "unknown").strip()
    lines.append("Project awareness")
    lines.append(f"- Workspace: {project_awareness.get('workspace_id') or 'default'}")
    lines.append(f"- Active project: {label} ({status})")
    lines.append(f"- Known projects: {int(project_awareness.get('project_count') or 0)}")
    lines.append("- Boundary: live git, filesystem, CI, and tool output outrank this project snapshot.")
    lines.append("")


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
