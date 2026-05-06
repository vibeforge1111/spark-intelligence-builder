from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


ProbeStatus = Literal["success", "failure"]


@dataclass(frozen=True)
class RouteProbeEvidenceResult:
    event_id: str
    capability_key: str
    status: ProbeStatus
    event_type: str
    route_latency_ms: int | None = None
    eval_ref: str = ""
    failure_reason: str = ""
    source_ref: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "capability_key": self.capability_key,
            "status": self.status,
            "event_type": self.event_type,
            "source_ref": self.source_ref,
        }
        if self.route_latency_ms is not None:
            payload["route_latency_ms"] = self.route_latency_ms
        if self.eval_ref:
            payload["eval_ref"] = self.eval_ref
        if self.failure_reason:
            payload["failure_reason"] = self.failure_reason
        return payload

    def to_text(self) -> str:
        label = "Recorded route probe"
        detail = f"{self.capability_key}: {self.status}"
        parts = [label, f"- route: {detail}", f"- event: {self.event_id}"]
        if self.route_latency_ms is not None:
            parts.append(f"- latency: {self.route_latency_ms}ms")
        if self.eval_ref:
            parts.append(f"- eval: {self.eval_ref}")
        if self.failure_reason:
            parts.append(f"- failure: {self.failure_reason}")
        return "\n".join(parts)


def record_route_probe_evidence(
    state_db: StateDB,
    *,
    capability_key: str,
    status: ProbeStatus,
    route_latency_ms: int | None = None,
    eval_ref: str = "",
    source_ref: str = "",
    failure_reason: str = "",
    actor_id: str = "",
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
) -> RouteProbeEvidenceResult:
    normalized_key = _normalize_capability_key(capability_key)
    normalized_status = _normalize_status(status)
    normalized_eval_ref = str(eval_ref or "").strip()
    normalized_source_ref = str(source_ref or "").strip() or f"route_probe:{normalized_key}"
    normalized_failure = str(failure_reason or "").strip()
    normalized_latency = _normalize_latency(route_latency_ms)
    event_type = "tool_result_received" if normalized_status == "success" else "dispatch_failed"
    facts: dict[str, Any] = {
        "capability_key": normalized_key,
        "routing_decision": normalized_key,
        "eval_coverage_status": "covered" if normalized_eval_ref else "observed",
    }
    if normalized_latency is not None:
        facts["route_latency_ms"] = normalized_latency
    if normalized_eval_ref:
        facts["eval_ref"] = normalized_eval_ref
    if normalized_status == "failure":
        facts["failure_reason"] = normalized_failure or "route_probe_failed"
    event_id = record_event(
        state_db,
        event_type=event_type,
        component="agent_operating_context",
        summary=f"Route probe {normalized_status}: {normalized_key}",
        status="succeeded" if normalized_status == "success" else "failed",
        reason_code=normalized_key,
        actor_id=str(actor_id or "").strip() or None,
        request_id=str(request_id or "").strip() or None,
        session_id=str(session_id or "").strip() or None,
        human_id=str(human_id or "").strip() or None,
        provenance={"source_kind": "route_probe", "source_ref": normalized_source_ref},
        facts=facts,
    )
    return RouteProbeEvidenceResult(
        event_id=event_id,
        capability_key=normalized_key,
        status=normalized_status,
        event_type=event_type,
        route_latency_ms=normalized_latency,
        eval_ref=normalized_eval_ref,
        failure_reason=normalized_failure if normalized_status == "failure" else "",
        source_ref=normalized_source_ref,
    )


def _normalize_capability_key(value: str) -> str:
    normalized = str(value or "").strip().replace(" ", "_")
    if not normalized:
        raise ValueError("capability_key is required")
    return normalized


def _normalize_status(value: str) -> ProbeStatus:
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "succeeded", "ok", "pass", "passed"}:
        return "success"
    if normalized in {"failure", "failed", "error", "fail"}:
        return "failure"
    raise ValueError("status must be success or failure")


def _normalize_latency(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise ValueError("route_latency_ms must be non-negative")
    return int(value)
