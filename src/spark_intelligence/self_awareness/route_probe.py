from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB
from spark_intelligence.system_registry import build_system_registry


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
    probe_summary: str = ""

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
        if self.probe_summary:
            payload["probe_summary"] = self.probe_summary
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
        if self.probe_summary:
            parts.append(f"- probe: {self.probe_summary}")
        return "\n".join(parts)


def run_route_probe_and_record(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    capability_key: str,
    actor_id: str = "",
    request_id: str = "",
    session_id: str = "",
    human_id: str = "",
) -> RouteProbeEvidenceResult:
    normalized_key = _normalize_capability_key(capability_key)
    started = perf_counter()
    try:
        probe = _run_route_probe(config_manager, state_db, capability_key=normalized_key)
    except Exception as exc:
        return record_route_probe_evidence(
            state_db,
            capability_key=normalized_key,
            status="failure",
            route_latency_ms=_elapsed_ms(started),
            eval_ref="self.route-probe.run",
            source_ref=f"route_probe_run:{normalized_key}",
            failure_reason=f"{type(exc).__name__}: {str(exc)[:180]}",
            actor_id=actor_id,
            request_id=request_id,
            session_id=session_id,
            human_id=human_id,
        )
    return record_route_probe_evidence(
        state_db,
        capability_key=normalized_key,
        status=probe["status"],
        route_latency_ms=probe.get("route_latency_ms") or _elapsed_ms(started),
        eval_ref="self.route-probe.run",
        source_ref=f"route_probe_run:{normalized_key}",
        failure_reason=str(probe.get("failure_reason") or ""),
        actor_id=actor_id,
        request_id=request_id,
        session_id=session_id,
        human_id=human_id,
        probe_summary=str(probe.get("summary") or ""),
    )


def _run_route_probe(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    capability_key: str,
) -> dict[str, Any]:
    if capability_key == "spark_intelligence_builder":
        return _run_builder_status_probe(config_manager, state_db)
    if capability_key == "spark_spawner":
        return _run_spawner_status_probe(config_manager, state_db)
    if capability_key == "spark_memory":
        return _run_memory_smoke_probe(config_manager, state_db)
    if capability_key == "spark_researcher":
        return _run_researcher_status_probe(config_manager, state_db)
    if capability_key == "spark_swarm":
        return _run_swarm_status_probe(config_manager, state_db)
    return _run_registry_route_probe(config_manager, state_db, capability_key=capability_key)


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
    probe_summary: str = "",
) -> RouteProbeEvidenceResult:
    normalized_key = _normalize_capability_key(capability_key)
    normalized_status = _normalize_status(status)
    normalized_eval_ref = str(eval_ref or "").strip()
    normalized_source_ref = str(source_ref or "").strip() or f"route_probe:{normalized_key}"
    normalized_failure = str(failure_reason or "").strip()
    normalized_probe_summary = str(probe_summary or "").strip()
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
    if normalized_probe_summary:
        facts["probe_summary"] = normalized_probe_summary
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
        probe_summary=normalized_probe_summary,
    )


def _run_builder_status_probe(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    from spark_intelligence.gateway.runtime import gateway_status

    status = gateway_status(config_manager, state_db)
    ok = bool(status.doctor_blocking_ok)
    return {
        "status": "success" if ok else "failure",
        "failure_reason": "" if ok else _first_nonempty(status.doctor_blocking_failures) or status.provider_runtime_detail,
        "summary": (
            f"gateway ready={status.ready} doctor_blocking_ok={status.doctor_blocking_ok} "
            f"providers={len(status.configured_providers)} channels={len(status.configured_channels)}"
        ),
    }


def _run_spawner_status_probe(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    from spark_intelligence.mission_control import build_mission_control_snapshot

    snapshot = build_mission_control_snapshot(config_manager, state_db)
    payload = snapshot.to_payload()
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    panels = payload.get("panels") if isinstance(payload.get("panels"), dict) else {}
    drift = panels.get("spawner_payload_drift") if isinstance(panels.get("spawner_payload_drift"), dict) else {}
    drift_status = str(drift.get("status") or "unknown")
    mission_status = str(summary.get("top_level_state") or "unknown")
    mission_degraded = mission_status in {"degraded", "execution_impaired", "blocked", "error", "failed"}
    ok = not mission_degraded and drift_status in {"ok", "none", "unknown"} and "Spark Spawner" in [
        str(item) for item in (summary.get("active_systems") or []) if str(item).strip()
    ]
    return {
        "status": "success" if ok else "failure",
        "failure_reason": "" if ok else f"mission status={mission_status} drift={drift_status}",
        "summary": (
            f"mission status={mission_status} "
            f"drift={drift_status} active_systems={len(summary.get('active_systems') or [])}"
        ),
    }


def _run_memory_smoke_probe(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    from spark_intelligence.memory import run_memory_sdk_smoke_test

    result = run_memory_sdk_smoke_test(
        config_manager=config_manager,
        state_db=state_db,
        subject="human:route-probe:aoc",
        predicate="system.route_probe.spark_memory",
        value="ok",
        cleanup=True,
        actor_id="route_probe",
    )
    cleanup_ok = result.cleanup_result is None or result.cleanup_result.accepted_count > 0
    ok = result.write_result.accepted_count > 0 and not result.read_result.abstained and bool(result.read_result.records) and cleanup_ok
    summary = (
        f"memory smoke write={result.write_result.status}/{result.write_result.accepted_count} "
        f"read_records={len(result.read_result.records)} cleanup={'ok' if cleanup_ok else 'failed'}"
    )
    return {
        "status": "success" if ok else "failure",
        "failure_reason": "" if ok else result.read_result.reason or result.write_result.reason or "memory_smoke_failed",
        "summary": summary,
    }


def _run_researcher_status_probe(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    from spark_intelligence.researcher_bridge import researcher_bridge_status

    status = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    return {
        "status": "success" if status.available else "failure",
        "failure_reason": "" if status.available else (status.last_failure or {}).get("message") or "researcher_unavailable",
        "summary": f"researcher available={status.available} configured={status.configured} mode={status.mode}",
    }


def _run_swarm_status_probe(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    from spark_intelligence.swarm_bridge import swarm_status

    status = swarm_status(config_manager, state_db)
    ok = bool(status.payload_ready)
    return {
        "status": "success" if ok else "failure",
        "failure_reason": "" if ok else (status.last_failure or {}).get("message") or "swarm_payload_not_ready",
        "summary": f"swarm payload_ready={status.payload_ready} api_ready={status.api_ready} auth_state={status.auth_state}",
    }


def _run_registry_route_probe(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    capability_key: str,
) -> dict[str, Any]:
    snapshot = build_system_registry(config_manager, state_db, probe_browser=capability_key == "spark_browser", probe_git=False)
    payload = snapshot.to_payload()
    record = next(
        (
            item
            for item in payload.get("records", [])
            if isinstance(item, dict)
            and str(item.get("kind") or "") == "system"
            and str(item.get("key") or "") == capability_key
        ),
        None,
    )
    if not isinstance(record, dict):
        return {
            "status": "failure",
            "failure_reason": "route_not_visible_in_system_registry",
            "summary": "System registry did not include this route.",
        }
    status = str(record.get("status") or "unknown")
    limitations = [str(item) for item in (record.get("limitations") or []) if str(item).strip()]
    ok = bool(record.get("available")) and not bool(record.get("degraded")) and status not in {"missing", "unavailable", "error"}
    if ok:
        return {
            "status": "success",
            "summary": f"registry status={status} available={bool(record.get('available'))}",
        }
    return {
        "status": "failure",
        "failure_reason": limitations[0] if limitations else f"registry status={status}",
        "summary": f"registry status={status} available={bool(record.get('available'))}",
    }


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


def _first_nonempty(values: list[str]) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
