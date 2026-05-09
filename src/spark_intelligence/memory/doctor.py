from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.memory.doctor_benchmark import (
    memory_doctor_benchmark_summary,
    score_memory_doctor_benchmark,
)
from spark_intelligence.memory.doctor_brain import build_memory_doctor_brain, memory_doctor_brain_summary
from spark_intelligence.memory.generic_observations import (
    TelegramGenericDeletion,
    detect_telegram_generic_deletions,
    parse_entity_state_deletion,
)
from spark_intelligence.observability.store import build_watchtower_snapshot, latest_events_by_type, record_event
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class MemoryDoctorFinding:
    name: str
    ok: bool
    severity: str
    detail: str
    request_id: str | None = None
    expected_delete_count: int = 0
    requested_delete_count: int = 0
    accepted_delete_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
            "request_id": self.request_id,
            "expected_delete_count": self.expected_delete_count,
            "requested_delete_count": self.requested_delete_count,
            "accepted_delete_count": self.accepted_delete_count,
        }


@dataclass(frozen=True)
class MemoryDoctorReport:
    findings: list[MemoryDoctorFinding]
    scanned_delete_turns: int
    scanned_multi_delete_turns: int
    recommendations: list[str]
    path_traces: list[dict[str, object]]
    dashboard: dict[str, object]
    active_profile: dict[str, object]
    topic_scan: dict[str, object]
    context_capsule: dict[str, object]
    movement_trace: dict[str, object]
    root_cause: dict[str, object]
    brain: dict[str, object]
    benchmark: dict[str, object]
    capability: dict[str, object]

    @property
    def ok(self) -> bool:
        return all(finding.ok for finding in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "scanned_delete_turns": self.scanned_delete_turns,
            "scanned_multi_delete_turns": self.scanned_multi_delete_turns,
            "findings": [finding.to_dict() for finding in self.findings],
            "recommendations": self.recommendations,
            "path_traces": self.path_traces,
            "dashboard": self.dashboard,
            "active_profile": self.active_profile,
            "topic_scan": self.topic_scan,
            "context_capsule": self.context_capsule,
            "movement_trace": self.movement_trace,
            "root_cause": self.root_cause,
            "brain": self.brain,
            "benchmark": self.benchmark,
            "capability": self.capability,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_text(self) -> str:
        status = "ok" if self.ok else "degraded"
        lines = [f"Spark memory doctor: {status}"]
        lines.append(
            f"- scanned delete turns: {self.scanned_delete_turns} "
            f"(multi-delete={self.scanned_multi_delete_turns})"
        )
        for finding in self.findings:
            marker = "ok" if finding.ok else "fail"
            lines.append(f"- [{marker}] {finding.name}: {finding.detail}")
        if self.active_profile:
            facts = self.active_profile.get("facts")
            lines.append(f"- active profile: {facts if facts else self.active_profile.get('status')}")
        if self.topic_scan.get("topic"):
            lines.append(
                f"- topic scan: {self.topic_scan.get('topic')} appeared in "
                f"{self.topic_scan.get('occurrence_count', 0)} recent memory event(s)"
            )
        if self.context_capsule.get("status") == "checked":
            lines.append(
                "- context capsule: "
                f"recent_conversation={self.context_capsule.get('recent_conversation_count', 0)} "
                f"request={self.context_capsule.get('request_id') or 'unknown'}"
            )
        elif self.context_capsule.get("request_id"):
            lines.append(
                "- context capsule: "
                f"status={self.context_capsule.get('status') or 'unknown'} "
                f"request={self.context_capsule.get('request_id')}"
            )
        if self.movement_trace.get("status") == "checked":
            lines.append(
                "- movement trace: "
                f"stages={len(self.movement_trace.get('stages') or [])} "
                f"gaps={len(self.movement_trace.get('gaps') or [])}"
            )
        if self.root_cause and self.root_cause.get("status") == "identified":
            root_cause_summary = str(
                self.root_cause.get("telegram_summary") or self.root_cause.get("summary") or ""
            ).strip()
            if root_cause_summary:
                lines.append(f"- root cause: {root_cause_summary}")
        if self.brain:
            lines.append(f"- brain: {memory_doctor_brain_summary(self.brain)}")
        if self.benchmark:
            lines.append(f"- benchmark: {memory_doctor_benchmark_summary(self.benchmark)}")
        if self.dashboard:
            lines.append(f"- dashboard: {self.dashboard}")
        if self.recommendations:
            lines.append("Recommendations:")
            for recommendation in self.recommendations:
                lines.append(f"- {recommendation}")
        return "\n".join(lines)

    def to_telegram_text(self) -> str:
        status = "healthy" if self.ok else "needs attention"
        lines = [f"Memory Doctor: {status}."]
        if self.topic_scan.get("topic"):
            lines.append(f"Topic: {self.topic_scan['topic']}.")
        lines.append(
            f"Scanned {self.scanned_delete_turns} delete turn(s), "
            f"{self.scanned_multi_delete_turns} multi-delete."
        )
        facts = self.active_profile.get("facts") if isinstance(self.active_profile, dict) else None
        if isinstance(facts, dict) and facts:
            compact_facts = ", ".join(f"{key}={value}" for key, value in facts.items())
            lines.append(f"Active profile: {compact_facts}.")
        elif self.active_profile.get("status") == "not_requested":
            lines.append("Active profile: not checked; give me a human id or run it from Telegram.")
        current_state = self.active_profile.get("current_state") if isinstance(self.active_profile, dict) else None
        if isinstance(current_state, dict) and current_state.get("status") not in (None, "error"):
            lines.append(f"Current-state scan: {int(current_state.get('record_count') or 0)} record(s).")
        topic_count = int(self.topic_scan.get("occurrence_count") or 0)
        if self.topic_scan.get("topic") and topic_count:
            lines.append(f"Trace: found {topic_count} recent memory event(s) mentioning that topic.")
        if self.context_capsule.get("request_id"):
            lines.append(f"Request: {self.context_capsule['request_id']}.")
        if self.context_capsule.get("status") == "checked":
            recent_count = int(self.context_capsule.get("recent_conversation_count") or 0)
            gateway_trace = self.context_capsule.get("gateway_trace")
            if isinstance(gateway_trace, dict) and gateway_trace.get("lineage_gap"):
                lines.append(
                    "Context capsule: gateway had "
                    f"{int(gateway_trace.get('recent_gateway_message_count') or 0)} earlier same-session message(s), "
                    "but the provider capsule had no recent-conversation turns."
                )
            elif recent_count == 0:
                lines.append("Context capsule: no recent-conversation turns in the selected provider capsule.")
            if isinstance(gateway_trace, dict):
                if gateway_trace.get("answer_topic_miss"):
                    lines.append(
                        "Answer grounding: recent context contained the expected topic, "
                        "but the delivered reply did not use it."
                    )
                elif gateway_trace.get("route_contamination"):
                    lines.append(
                        "Answer grounding: close-turn recall routed through provisional researcher advisory."
                    )
                delivery_trace = gateway_trace.get("delivery_trace")
                if isinstance(delivery_trace, dict) and delivery_trace.get("delivery_failed"):
                    lines.append(
                        "Delivery: Telegram outbound audit shows delivery failed for the diagnosed turn."
                    )
                elif isinstance(delivery_trace, dict) and delivery_trace.get("delivery_topic_miss"):
                    lines.append(
                        "Delivery: generated reply contained the expected topic, but the delivered reply did not."
                    )
                cross_scope = gateway_trace.get("cross_scope_lineage")
                if isinstance(cross_scope, dict) and cross_scope.get("status") == "checked":
                    lines.append(
                        "Lineage scope: "
                        f"{int(cross_scope.get('session_count') or 0)} session(s), "
                        f"{int(cross_scope.get('channel_count') or 0)} channel(s) visible."
                    )
        elif self.context_capsule.get("status") == "request_not_found":
            gateway_trace = self.context_capsule.get("gateway_trace")
            if isinstance(gateway_trace, dict) and gateway_trace.get("status") == "checked":
                lines.append(
                    "Context capsule: no provider capsule event was recorded for this request, "
                    "but gateway trace exists."
                )
            else:
                lines.append("Context capsule: no provider capsule event was recorded for this request.")
        root_cause_summary = str(self.root_cause.get("telegram_summary") or "").strip()
        if root_cause_summary and self.root_cause.get("status") == "identified":
            lines.append(f"Root cause: {root_cause_summary}.")
            repair_focus = _root_cause_telegram_repair_focus(self.root_cause)
            if repair_focus:
                lines.append(f"Repair focus: {repair_focus}.")
        if self.brain:
            lines.append(f"Brain: {memory_doctor_brain_summary(self.brain)}.")
        if self.benchmark:
            lines.append(f"Benchmark: {memory_doctor_benchmark_summary(self.benchmark)}.")
        failing = [finding for finding in self.findings if not finding.ok]
        if not failing:
            lines.append("Delete integrity looks good: every detected multi-forget turn has matching delete writes.")
            if self.recommendations:
                lines.append(f"Recommendation: {self.recommendations[0]}")
            return "\n".join(lines)
        first = failing[0]
        lines.append(
            "Problem: "
            f"{first.detail} "
            f"(request={first.request_id or 'unknown'})."
        )
        if self.recommendations:
            lines.append(f"Next: {self.recommendations[0]}")
        return "\n".join(lines)


def run_memory_doctor(
    state_db: StateDB,
    *,
    config_manager: Any | None = None,
    human_id: str | None = None,
    topic: str | None = None,
    request_id: str | None = None,
    repair_requested: bool = False,
    limit: int = 200,
) -> MemoryDoctorReport:
    influence_events = latest_events_by_type(
        state_db,
        event_type="plugin_or_chip_influence_recorded",
        limit=limit,
    )
    write_requested_events = latest_events_by_type(
        state_db,
        event_type="memory_write_requested",
        limit=limit * 3,
    )
    write_succeeded_events = latest_events_by_type(
        state_db,
        event_type="memory_write_succeeded",
        limit=limit * 3,
    )
    read_result_events = latest_events_by_type(
        state_db,
        event_type="memory_read_succeeded",
        limit=limit,
    ) + latest_events_by_type(
        state_db,
        event_type="memory_read_abstained",
        limit=limit,
    )
    read_requested_events = latest_events_by_type(
        state_db,
        event_type="memory_read_requested",
        limit=limit,
    )
    tool_result_events = latest_events_by_type(
        state_db,
        event_type="tool_result_received",
        limit=limit,
    )
    context_capsule_events = latest_events_by_type(
        state_db,
        event_type="context_capsule_compiled",
        limit=limit,
    )
    lifecycle_events = latest_events_by_type(
        state_db,
        event_type="memory_lifecycle_transition",
        limit=limit,
    )
    policy_gate_events = latest_events_by_type(
        state_db,
        event_type="policy_gate_blocked",
        limit=limit,
    )
    all_memory_events = influence_events + write_requested_events + write_succeeded_events + read_requested_events + read_result_events
    findings: list[MemoryDoctorFinding] = []
    scanned_delete_turns = 0
    scanned_multi_delete_turns = 0
    seen_delete_messages: set[str] = set()

    for event in influence_events:
        if str(event.get("component") or "") != "researcher_bridge":
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        message_text = _memory_doctor_message_text(facts)
        if not message_text:
            continue
        expected_deletions = detect_telegram_generic_deletions(message_text)
        if not expected_deletions:
            continue
        message_key = _normalized_message_key(message_text)
        if message_key in seen_delete_messages:
            continue
        seen_delete_messages.add(message_key)
        scanned_delete_turns += 1
        if len(expected_deletions) < 2:
            continue
        scanned_multi_delete_turns += 1
        request_id = str(event.get("request_id") or "").strip()
        requested_count = _delete_write_request_count(write_requested_events, request_id=request_id)
        accepted_count = _accepted_delete_write_count(write_succeeded_events, request_id=request_id)
        expected_count = len(expected_deletions)
        ok = requested_count >= expected_count and accepted_count >= expected_count
        if not ok:
            findings.append(
                MemoryDoctorFinding(
                    name="memory_delete_intent_integrity",
                    ok=False,
                    severity="high",
                    detail=(
                        f"multi-delete turn expected {expected_count} delete write(s), "
                        f"requested {requested_count}, accepted {accepted_count}"
                    ),
                    request_id=request_id or None,
                    expected_delete_count=expected_count,
                    requested_delete_count=requested_count,
                    accepted_delete_count=accepted_count,
                )
            )

    if not any(finding.name == "memory_delete_intent_integrity" for finding in findings):
        findings.append(
            MemoryDoctorFinding(
                name="memory_delete_intent_integrity",
                ok=True,
                severity="high",
                detail="No partial multi-delete writes detected.",
            )
        )

    active_profile = _build_active_profile(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
    )
    forget_postcondition = _build_forget_postcondition_audit(
        influence_events=influence_events,
        active_profile=active_profile,
        human_id=human_id,
    )
    topic_scan = _scan_topic(all_memory_events + tool_result_events, topic=topic)
    context_capsule = _build_context_capsule_audit(
        context_capsule_events,
        config_manager=config_manager,
        human_id=human_id,
        topic=topic,
        request_id=request_id,
    )
    dashboard = _build_dashboard_summary(state_db)
    movement_trace = _build_movement_trace(
        influence_events=influence_events,
        write_requested_events=write_requested_events,
        write_succeeded_events=write_succeeded_events,
        read_requested_events=read_requested_events,
        read_result_events=read_result_events,
        lifecycle_events=lifecycle_events,
        policy_gate_events=policy_gate_events,
        tool_result_events=tool_result_events,
        context_capsule=context_capsule,
        dashboard=dashboard,
        forget_postcondition=forget_postcondition,
    )
    topic_findings = _build_topic_findings(topic_scan=topic_scan, active_profile=active_profile)
    findings.extend(
        _build_forget_postcondition_findings(
            forget_postcondition=forget_postcondition,
        )
    )
    findings.extend(topic_findings)
    findings.extend(_build_context_capsule_findings(context_capsule))
    root_cause = _build_root_cause_summary(
        findings=findings,
        context_capsule=context_capsule,
        movement_trace=movement_trace,
    )
    path_traces = _build_path_traces(
        influence_events=influence_events,
        write_requested_events=write_requested_events,
        write_succeeded_events=write_succeeded_events,
        tool_result_events=tool_result_events,
        topic=topic,
        request_id=request_id,
    )
    brain = build_memory_doctor_brain(
        state_db=state_db,
        config_manager=config_manager,
        findings=findings,
        event_counts={
            "builder_influence": len(influence_events),
            "memory_write_requested": len(write_requested_events),
            "memory_write_succeeded": len(write_succeeded_events),
            "memory_read_requested": len(read_requested_events),
            "memory_read_result": len(read_result_events),
            "tool_result": len(tool_result_events),
            "context_capsule": len(context_capsule_events),
            "memory_lifecycle": len(lifecycle_events),
            "policy_gate": len(policy_gate_events),
        },
        active_profile=active_profile,
        topic_scan=topic_scan,
        context_capsule=context_capsule,
        movement_trace=movement_trace,
        root_cause=root_cause,
        dashboard=dashboard,
        path_traces=path_traces,
    )
    _record_brain_snapshot(
        state_db=state_db,
        brain=brain,
        human_id=human_id,
        topic=topic,
        request_id=request_id,
    )
    benchmark = score_memory_doctor_benchmark(
        scanned_delete_turns=scanned_delete_turns,
        scanned_multi_delete_turns=scanned_multi_delete_turns,
        findings=findings,
        active_profile=active_profile,
        topic_scan=topic_scan,
        context_capsule=context_capsule,
        movement_trace=movement_trace,
        dashboard=dashboard,
        root_cause=root_cause,
    )
    recommendations = _build_recommendations(
        findings=findings,
        active_profile=active_profile,
        topic_scan=topic_scan,
        context_capsule=context_capsule,
        root_cause=root_cause,
        repair_requested=repair_requested,
    )
    recommendations.extend(_brain_recommendations(brain))
    return MemoryDoctorReport(
        findings=findings,
        scanned_delete_turns=scanned_delete_turns,
        scanned_multi_delete_turns=scanned_multi_delete_turns,
        recommendations=recommendations,
        path_traces=path_traces,
        dashboard=dashboard,
        active_profile=active_profile,
        topic_scan=topic_scan,
        context_capsule=context_capsule,
        movement_trace=movement_trace,
        root_cause=root_cause,
        brain=brain,
        benchmark=benchmark,
        capability={
            "mode": "diagnosis_only",
            "repair_requested": repair_requested,
            "automatic_repair": False,
            "authority_boundary": "Memory Doctor reads traces and recommends; it does not delete or rewrite memory.",
            "brain": {
                "mode": "diagnostic_and_proactive_recommendation",
                "automatic_repair": False,
                "snapshot_event": "memory_doctor_brain_evaluated",
                "snapshot_authority": "observability_non_authoritative",
            },
            "benchmark": {
                "mode": "diagnostic_score",
                "automatic_repair": False,
                "authority": "diagnostic_score_not_memory_truth",
            },
            "request_id": request_id,
        },
    )


def _build_active_profile(
    *,
    config_manager: Any | None,
    state_db: StateDB,
    human_id: str | None,
) -> dict[str, object]:
    if config_manager is None or not str(human_id or "").strip():
        return {"status": "not_requested"}
    try:
        from spark_intelligence.memory.orchestrator import (
            inspect_human_memory_in_memory,
            lookup_current_state_in_memory,
        )
    except Exception as exc:  # pragma: no cover - defensive import boundary
        return {"status": "unavailable", "reason": f"lookup import failed: {exc}"}

    facts: dict[str, object] = {}
    reads: dict[str, object] = {}
    predicates = {
        "preferred_name": "profile.preferred_name",
        "current_owner": "profile.current_owner",
        "cofounder": "profile.cofounder_name",
    }
    subject = _doctor_subject_for_human_id(str(human_id))
    for label, predicate in predicates.items():
        try:
            result = lookup_current_state_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                subject=subject,
                predicate=predicate,
                actor_id="memory_doctor",
            )
        except Exception as exc:  # pragma: no cover - SDK/runtime dependent
            reads[label] = {"status": "error", "reason": str(exc)}
            continue
        value = _first_memory_value(result.read_result.records)
        if value not in (None, ""):
            facts[label] = value
        reads[label] = {
            "status": result.read_result.status,
            "record_count": len(result.read_result.records),
            "abstained": bool(result.read_result.abstained),
            "reason": result.read_result.reason,
        }
    try:
        inspection = inspect_human_memory_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            human_id=str(human_id),
            actor_id="memory_doctor",
        )
        records = [
            _compact_current_state_record(record)
            for record in inspection.read_result.records
            if isinstance(record, dict)
        ]
        current_state = {
            "status": inspection.read_result.status,
            "record_count": len(inspection.read_result.records),
            "abstained": bool(inspection.read_result.abstained),
            "reason": inspection.read_result.reason,
            "records": records[:50],
        }
    except Exception as exc:  # pragma: no cover - SDK/runtime dependent
        current_state = {"status": "error", "reason": str(exc), "record_count": 0, "records": []}
    return {
        "status": "checked",
        "human_id": human_id,
        "subject": subject,
        "facts": facts,
        "reads": reads,
        "current_state": current_state,
    }


def _doctor_subject_for_human_id(human_id: str) -> str:
    text = str(human_id or "").strip()
    if text.startswith("human:"):
        return text
    return f"human:{text}"


def _first_memory_value(records: list[dict[str, Any]]) -> object | None:
    if not records:
        return None
    record = records[0]
    if not isinstance(record, dict):
        return None
    if record.get("value") not in (None, ""):
        return record.get("value")
    metadata = record.get("metadata")
    if isinstance(metadata, dict) and metadata.get("value") not in (None, ""):
        return metadata.get("value")
    return record.get("text")


def _compact_current_state_record(record: dict[str, Any]) -> dict[str, object]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return {
        "predicate": record.get("predicate"),
        "value": record.get("value") if record.get("value") not in (None, "") else metadata.get("value"),
        "text": _shorten(str(record.get("text") or ""), 180),
        "memory_role": record.get("memory_role") or metadata.get("memory_role"),
        "retention_class": record.get("retention_class") or metadata.get("retention_class"),
        "entity_key": metadata.get("entity_key"),
        "entity_label": metadata.get("entity_label"),
        "entity_attribute": metadata.get("entity_attribute") or metadata.get("field_name"),
        "timestamp": record.get("timestamp"),
    }


def _record_search_text(record: dict[str, object]) -> str:
    return " ".join(
        str(record.get(key) or "")
        for key in (
            "predicate",
            "value",
            "text",
            "memory_role",
            "retention_class",
            "entity_key",
            "entity_label",
            "entity_attribute",
        )
    )


def _record_label(record: dict[str, object]) -> str:
    predicate = str(record.get("predicate") or "current_state")
    entity_label = str(record.get("entity_label") or "").strip()
    entity_attribute = str(record.get("entity_attribute") or "").strip()
    if entity_label and entity_attribute:
        return f"{predicate}:{entity_label}.{entity_attribute}"
    return predicate


def _build_dashboard_summary(state_db: StateDB) -> dict[str, object]:
    try:
        snapshot = build_watchtower_snapshot(state_db)
    except Exception as exc:  # pragma: no cover - dashboard should not block diagnosis
        return {"status": "unavailable", "reason": str(exc)}
    panels = snapshot.get("panels") if isinstance(snapshot, dict) else {}
    memory_shadow = (panels or {}).get("memory_shadow") or {}
    memory_lane_hygiene = (panels or {}).get("memory_lane_hygiene") or {}
    return {
        "top_level_state": snapshot.get("top_level_state"),
        "memory_shadow_counts": dict(memory_shadow.get("counts") or {}),
        "memory_role_mix": list(memory_shadow.get("memory_role_mix") or []),
        "abstention_reasons": list(memory_shadow.get("abstention_reasons") or [])[:5],
        "memory_lane_hygiene_status": memory_lane_hygiene.get("status"),
        "memory_lane_hygiene_counts": dict(memory_lane_hygiene.get("counts") or {}),
    }


def _scan_topic(events: list[dict[str, object]], *, topic: str | None) -> dict[str, object]:
    normalized_topic = str(topic or "").strip()
    if not normalized_topic:
        return {"status": "not_requested", "topic": None, "occurrence_count": 0, "matches": []}
    needle = normalized_topic.lower()
    matches: list[dict[str, object]] = []
    for event in events:
        haystack = _event_search_text(event).lower()
        if needle not in haystack:
            continue
        matches.append(
            {
                "event_type": event.get("event_type"),
                "component": event.get("component"),
                "request_id": event.get("request_id"),
                "created_at": event.get("created_at"),
                "summary": _shorten(str(event.get("summary") or ""), 120),
            }
        )
        if len(matches) >= 8:
            break
    return {
        "status": "checked",
        "topic": normalized_topic,
        "occurrence_count": len(matches),
        "matches": matches,
    }


def _build_topic_findings(
    *,
    topic_scan: dict[str, object],
    active_profile: dict[str, object],
) -> list[MemoryDoctorFinding]:
    topic = str(topic_scan.get("topic") or "").strip()
    if not topic:
        return []
    facts = active_profile.get("facts") if isinstance(active_profile, dict) else {}
    active_fields: list[str] = []
    if isinstance(facts, dict):
        for field, value in facts.items():
            if str(value or "").strip().lower() == topic.lower():
                active_fields.append(str(field))
    current_state = active_profile.get("current_state") if isinstance(active_profile, dict) else {}
    current_matches: list[str] = []
    if isinstance(current_state, dict):
        records = current_state.get("records")
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                if topic.lower() not in _record_search_text(record).lower():
                    continue
                current_matches.append(_record_label(record))
    if active_fields:
        active_fields.extend(match for match in current_matches if match not in active_fields)
        return [
            MemoryDoctorFinding(
                name="topic_active_state_presence",
                ok=True,
                severity="medium",
                detail=f"topic '{topic}' is active in: {', '.join(active_fields)}",
            )
        ]
    if current_matches:
        return [
            MemoryDoctorFinding(
                name="topic_active_state_presence",
                ok=True,
                severity="medium",
                detail=f"topic '{topic}' is active in: {', '.join(current_matches)}",
            )
        ]
    occurrence_count = int(topic_scan.get("occurrence_count") or 0)
    return [
        MemoryDoctorFinding(
            name="topic_trace_scan",
            ok=True,
            severity="medium",
            detail=(
                f"topic '{topic}' appears in {occurrence_count} recent memory event(s), "
                "but not in checked active current-state records"
            ),
        )
    ]


def _build_forget_postcondition_audit(
    *,
    influence_events: list[dict[str, object]],
    active_profile: dict[str, object],
    human_id: str | None,
) -> dict[str, object]:
    if active_profile.get("status") != "checked":
        return {"status": "not_checked", "reason": "active_profile_not_checked", "checked_delete_target_count": 0}
    normalized_human_id = str(human_id or "").strip()
    stale_targets: list[dict[str, object]] = []
    checked_delete_target_count = 0
    seen: set[tuple[str, str, str]] = set()
    for event in influence_events:
        if str(event.get("component") or "") != "researcher_bridge":
            continue
        event_human_id = str(event.get("human_id") or "").strip()
        if normalized_human_id and event_human_id and event_human_id != normalized_human_id:
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        message_text = _memory_doctor_message_text(facts)
        if not message_text:
            continue
        for deletion in detect_telegram_generic_deletions(message_text):
            key = (
                str(event.get("request_id") or ""),
                deletion.predicate,
                _delete_target_label(deletion),
            )
            if key in seen:
                continue
            seen.add(key)
            checked_delete_target_count += 1
            matches = _active_current_state_matches_deletion(
                active_profile=active_profile,
                deletion=deletion,
            )
            if matches:
                stale_targets.append(
                    {
                        "request_id": event.get("request_id"),
                        "target": _delete_target_label(deletion),
                        "predicate": deletion.predicate,
                        "active_matches": matches[:3],
                    }
                )

    return {
        "status": "checked",
        "checked_delete_target_count": checked_delete_target_count,
        "stale_target_count": len(stale_targets),
        "stale_targets": stale_targets,
    }


def _build_forget_postcondition_findings(
    *,
    forget_postcondition: dict[str, object],
) -> list[MemoryDoctorFinding]:
    stale_targets = forget_postcondition.get("stale_targets")
    if not isinstance(stale_targets, list) or not stale_targets:
        return []
    target_names = ", ".join(str(item["target"]) for item in stale_targets[:4])
    return [
        MemoryDoctorFinding(
            name="memory_forget_postcondition_failed",
            ok=False,
            severity="high",
            detail=(
                f"forget request completed, but active current-state memory still contains: {target_names}. "
                "Write counts alone are insufficient; inspect delete postconditions."
            ),
            request_id=str(stale_targets[0].get("request_id") or "") or None,
        )
    ]


def _active_current_state_matches_deletion(
    *,
    active_profile: dict[str, object],
    deletion: TelegramGenericDeletion,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    facts = active_profile.get("facts") if isinstance(active_profile.get("facts"), dict) else {}
    fact_label = _profile_fact_label_for_predicate(deletion.predicate)
    if fact_label and facts.get(fact_label) not in (None, ""):
        matches.append(
            {
                "source": "active_profile_fact",
                "predicate": deletion.predicate,
                "label": fact_label,
                "value": _shorten(str(facts.get(fact_label) or ""), 120),
            }
        )
    current_state = active_profile.get("current_state") if isinstance(active_profile, dict) else {}
    records = current_state.get("records") if isinstance(current_state, dict) else []
    if not isinstance(records, list):
        return matches
    for record in records:
        if not isinstance(record, dict):
            continue
        if not _current_state_record_matches_deletion(record=record, deletion=deletion):
            continue
        matches.append(
            {
                "source": "current_state_record",
                "predicate": record.get("predicate"),
                "value": _shorten(str(record.get("value") or record.get("text") or ""), 120),
                "label": _record_label(record),
            }
        )
    return matches


def _current_state_record_matches_deletion(
    *,
    record: dict[str, object],
    deletion: TelegramGenericDeletion,
) -> bool:
    if str(record.get("predicate") or "").strip() != deletion.predicate:
        return False
    if not _record_has_active_value(record):
        return False
    if not deletion.predicate.startswith("entity."):
        return True
    parsed = parse_entity_state_deletion(deletion.evidence_text)
    if parsed is None:
        return True
    record_entity_key = str(record.get("entity_key") or "").strip()
    record_entity_label = str(record.get("entity_label") or "").strip().lower()
    record_attribute = str(record.get("entity_attribute") or "").strip().lower()
    entity_matches = record_entity_key == parsed.entity_key or record_entity_label == parsed.entity_label.lower()
    attribute_matches = not record_attribute or record_attribute == parsed.attribute.lower()
    return entity_matches and attribute_matches


def _record_has_active_value(record: dict[str, object]) -> bool:
    raw_value = str(record.get("value") or record.get("text") or "").strip()
    if not raw_value:
        return False
    lowered = raw_value.lower()
    return not (
        lowered.startswith("forget ")
        or lowered.startswith("delete ")
        or lowered.startswith("remove ")
    )


def _profile_fact_label_for_predicate(predicate: str) -> str | None:
    return {
        "profile.preferred_name": "preferred_name",
        "profile.current_owner": "current_owner",
        "profile.cofounder_name": "cofounder",
    }.get(predicate)


def _delete_target_label(deletion: TelegramGenericDeletion) -> str:
    if deletion.predicate.startswith("entity."):
        parsed = parse_entity_state_deletion(deletion.evidence_text)
        if parsed is not None:
            return f"{parsed.entity_label}.{parsed.attribute}"
    return deletion.label


def _build_context_capsule_audit(
    events: list[dict[str, object]],
    *,
    config_manager: Any | None,
    human_id: str | None,
    topic: str | None,
    request_id: str | None,
) -> dict[str, object]:
    normalized_request_id = str(request_id or "").strip()
    if not events:
        if normalized_request_id:
            return {
                "status": "request_not_found",
                "request_id": normalized_request_id,
                "gateway_trace": _build_gateway_trace_audit(
                    config_manager=config_manager,
                    capsule_request_id=normalized_request_id,
                    capsule_recent_conversation_count=0,
                    topic=topic,
                ),
            }
        return {"status": "not_found"}
    normalized_human_id = str(human_id or "").strip()
    selected: dict[str, object] | None = None
    for event in events:
        event_human_id = str(event.get("human_id") or "").strip()
        if normalized_human_id and event_human_id and event_human_id != normalized_human_id:
            continue
        if normalized_request_id and str(event.get("request_id") or "").strip() != normalized_request_id:
            continue
        selected = event
        break
    if selected is None:
        if normalized_request_id:
            return {
                "status": "request_not_found",
                "request_id": normalized_request_id,
                "gateway_trace": _build_gateway_trace_audit(
                    config_manager=config_manager,
                    capsule_request_id=normalized_request_id,
                    capsule_recent_conversation_count=0,
                    topic=topic,
                ),
            }
        return {"status": "not_found"}
    facts = selected.get("facts_json") if isinstance(selected.get("facts_json"), dict) else {}
    source_counts = facts.get("source_counts") if isinstance(facts.get("source_counts"), dict) else {}
    source_ledger = facts.get("source_ledger") if isinstance(facts.get("source_ledger"), list) else []
    recent_entry = next(
        (item for item in source_ledger if isinstance(item, dict) and item.get("source") == "recent_conversation"),
        {},
    )
    result = {
        "status": "checked",
        "request_id": selected.get("request_id"),
        "created_at": selected.get("created_at"),
        "reason_code": selected.get("reason_code"),
        "recent_conversation_count": int(source_counts.get("recent_conversation") or 0),
        "recent_conversation_present": bool(recent_entry.get("present")) if isinstance(recent_entry, dict) else False,
        "source_counts": dict(source_counts),
        "source_ledger": [
            {
                "source": item.get("source"),
                "role": item.get("role"),
                "priority": item.get("priority"),
                "present": item.get("present"),
                "count": item.get("count"),
            }
            for item in source_ledger[:12]
            if isinstance(item, dict)
        ],
    }
    result["gateway_trace"] = _build_gateway_trace_audit(
        config_manager=config_manager,
        capsule_request_id=str(selected.get("request_id") or ""),
        capsule_recent_conversation_count=int(source_counts.get("recent_conversation") or 0),
        topic=topic,
    )
    return result


def _build_gateway_trace_audit(
    *,
    config_manager: Any | None,
    capsule_request_id: str,
    capsule_recent_conversation_count: int,
    topic: str | None,
) -> dict[str, object]:
    if config_manager is None:
        return {"status": "not_requested"}
    try:
        from spark_intelligence.gateway.tracing import read_gateway_traces, read_outbound_audit

        traces = read_gateway_traces(config_manager, limit=300)
        outbound_records = read_outbound_audit(config_manager, limit=300)
    except Exception as exc:  # pragma: no cover - trace logs should not block diagnosis
        return {"status": "unavailable", "reason": str(exc)}
    if not traces:
        return {"status": "not_found"}

    target_index = next(
        (
            index
            for index, record in enumerate(traces)
            if str(record.get("request_id") or "").strip() == capsule_request_id
        ),
        None,
    )
    if target_index is None:
        return {"status": "request_not_found", "request_id": capsule_request_id}

    target = traces[target_index]
    session_id = str(target.get("session_id") or "").strip()
    chat_id = str(target.get("chat_id") or "").strip()
    telegram_user_id = str(target.get("telegram_user_id") or "").strip()
    target_user_preview = str(target.get("user_message_preview") or "").strip()
    target_response_preview = str(target.get("response_preview") or "").strip()
    target_routing_decision = str(target.get("routing_decision") or "").strip()
    target_bridge_mode = str(target.get("bridge_mode") or "").strip()
    target_evidence_summary = str(target.get("evidence_summary") or "").strip()
    recent_messages: list[dict[str, object]] = []
    topic_recent_message: dict[str, object] | None = None
    normalized_topic = str(topic or "").strip()
    for record in reversed(traces[:target_index]):
        if str(record.get("event") or "") != "telegram_update_processed":
            continue
        if session_id and str(record.get("session_id") or "") != session_id:
            continue
        if chat_id and str(record.get("chat_id") or "") != chat_id:
            continue
        if telegram_user_id and str(record.get("telegram_user_id") or "") != telegram_user_id:
            continue
        preview = str(record.get("user_message_preview") or "").strip()
        if not preview:
            continue
        recent_messages.append(
            {
                "request_id": record.get("request_id"),
                "recorded_at": record.get("recorded_at"),
                "user_message_preview": _shorten(preview, 120),
                "bridge_mode": record.get("bridge_mode"),
                "routing_decision": record.get("routing_decision"),
            }
        )
        if (
            topic_recent_message is None
            and normalized_topic
            and normalized_topic.lower() in preview.lower()
        ):
            topic_recent_message = recent_messages[-1]
        if len(recent_messages) >= 5:
            break
    close_turn_recall_question = _looks_like_close_turn_recall_question(target_user_preview)
    provisional_researcher_route = _is_provisional_researcher_route(
        bridge_mode=target_bridge_mode,
        routing_decision=target_routing_decision,
        evidence_summary=target_evidence_summary,
    )
    answer_topic_miss = bool(
        close_turn_recall_question
        and normalized_topic
        and topic_recent_message is not None
        and normalized_topic.lower() not in target_response_preview.lower()
    )
    route_contamination = bool(
        close_turn_recall_question
        and provisional_researcher_route
        and recent_messages
        and capsule_recent_conversation_count > 0
        and (not normalized_topic or topic_recent_message is not None)
    )
    delivery_trace = _build_delivery_trace_audit(
        outbound_records=outbound_records,
        target=target,
        close_turn_recall_question=close_turn_recall_question,
        normalized_topic=normalized_topic,
    )
    diagnostic_invocations = _build_diagnostic_invocations(
        traces=traces,
        target_index=target_index,
        diagnosed_request_id=capsule_request_id,
    )

    return {
        "status": "checked",
        "request_id": capsule_request_id,
        "target_recorded_at": target.get("recorded_at"),
        "session_id": session_id,
        "target_user_message_preview": _shorten(target_user_preview, 160),
        "target_response_preview": _shorten(target_response_preview, 160),
        "target_bridge_mode": target_bridge_mode or None,
        "target_routing_decision": target_routing_decision or None,
        "target_evidence_summary": target_evidence_summary or None,
        "close_turn_recall_question": close_turn_recall_question,
        "provisional_researcher_route": provisional_researcher_route,
        "answer_topic_miss": answer_topic_miss,
        "route_contamination": route_contamination,
        "expected_topic": normalized_topic or None,
        "topic_source_request_id": topic_recent_message.get("request_id") if topic_recent_message else None,
        "recent_gateway_message_count": len(recent_messages),
        "capsule_recent_conversation_count": capsule_recent_conversation_count,
        "lineage_gap": bool(recent_messages and capsule_recent_conversation_count == 0),
        "delivery_trace": delivery_trace,
        "diagnostic_invocation_count": len(diagnostic_invocations),
        "diagnostic_invocations": diagnostic_invocations,
        "recent_gateway_messages": recent_messages,
        "cross_scope_lineage": _build_cross_scope_lineage_summary(
            traces=traces,
            target_index=target_index,
            target=target,
        ),
    }


def _build_diagnostic_invocations(
    *,
    traces: list[dict[str, object]],
    target_index: int,
    diagnosed_request_id: str,
) -> list[dict[str, object]]:
    invocations: list[dict[str, object]] = []
    normalized_request_id = str(diagnosed_request_id or "").strip()
    if not normalized_request_id:
        return invocations
    for record in traces[target_index + 1 :]:
        if str(record.get("event") or "") != "telegram_update_processed":
            continue
        metadata = record.get("runtime_command_metadata")
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("diagnosed_request_id") or "").strip() != normalized_request_id:
            continue
        invocations.append(
            {
                "request_id": record.get("request_id"),
                "recorded_at": record.get("recorded_at"),
                "user_message_preview": _shorten(str(record.get("user_message_preview") or ""), 120),
                "request_selector": metadata.get("request_selector"),
                "contextual_trigger_score": metadata.get("contextual_trigger_score"),
                "contextual_trigger_threshold": metadata.get("contextual_trigger_threshold"),
                "contextual_trigger_signals": list(metadata.get("contextual_trigger_signals") or []),
                "previous_failure_signal": metadata.get("previous_failure_signal"),
                "previous_failure_signals": list(metadata.get("previous_failure_signals") or []),
                "memory_doctor_ok": metadata.get("memory_doctor_ok"),
            }
        )
        if len(invocations) >= 5:
            break
    return invocations


def _build_delivery_trace_audit(
    *,
    outbound_records: list[dict[str, object]],
    target: dict[str, object],
    close_turn_recall_question: bool,
    normalized_topic: str,
) -> dict[str, object]:
    if not outbound_records:
        return {"status": "not_found", "reason": "outbound_audit_empty"}

    target_update_id = str(target.get("update_id") or "").strip()
    target_session_id = str(target.get("session_id") or "").strip()
    target_chat_id = str(target.get("chat_id") or "").strip()
    target_user_id = str(target.get("telegram_user_id") or "").strip()
    target_user_preview = str(target.get("user_message_preview") or "").strip()
    best: tuple[int, dict[str, object]] | None = None
    for record in reversed(outbound_records):
        if str(record.get("event") or "") != "telegram_bridge_outbound":
            continue
        score = 0
        if target_update_id and str(record.get("update_id") or "").strip() == target_update_id:
            score += 8
        elif target_user_preview and _preview_equivalent(
            str(record.get("user_message_preview") or ""),
            target_user_preview,
        ):
            score += 4
        else:
            continue
        if target_session_id and str(record.get("session_id") or "").strip() == target_session_id:
            score += 3
        elif target_session_id:
            continue
        if target_chat_id and str(record.get("chat_id") or "").strip() == target_chat_id:
            score += 2
        elif target_chat_id:
            continue
        if target_user_id and str(record.get("telegram_user_id") or "").strip() == target_user_id:
            score += 2
        elif target_user_id:
            continue
        if best is None or score > best[0]:
            best = (score, record)

    if best is None:
        return {
            "status": "not_found",
            "reason": "matching_outbound_audit_not_found",
            "target_update_id": target_update_id or None,
        }

    record = best[1]
    generated_preview = str(target.get("response_preview") or "").strip()
    delivered_preview = str(record.get("response_preview") or record.get("delivered_text") or "").strip()
    delivery_ok = bool(record.get("delivery_ok")) if record.get("delivery_ok") is not None else None
    response_mismatch = bool(
        generated_preview
        and delivered_preview
        and not _preview_equivalent(generated_preview, delivered_preview)
    )
    delivery_topic_miss = bool(
        close_turn_recall_question
        and normalized_topic
        and normalized_topic.lower() in generated_preview.lower()
        and normalized_topic.lower() not in delivered_preview.lower()
    )
    return {
        "status": "checked",
        "recorded_at": record.get("recorded_at"),
        "update_id": record.get("update_id"),
        "delivery_ok": delivery_ok,
        "delivery_failed": delivery_ok is False,
        "response_mismatch": response_mismatch,
        "delivery_topic_miss": delivery_topic_miss,
        "generated_response_preview": _shorten(generated_preview, 160),
        "delivered_response_preview": _shorten(delivered_preview, 160),
        "guardrail_actions": list(record.get("guardrail_actions") or [])[:8]
        if isinstance(record.get("guardrail_actions"), list)
        else [],
        "delivery_medium": record.get("delivery_medium"),
        "voice_requested": record.get("voice_requested"),
        "voice_error": record.get("voice_error"),
    }


def _preview_equivalent(left: str, right: str) -> bool:
    left_text = _compare_preview_text(left)
    right_text = _compare_preview_text(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    if left_text.endswith("...") and right_text.startswith(left_text[:-3].rstrip()):
        return True
    if right_text.endswith("...") and left_text.startswith(right_text[:-3].rstrip()):
        return True
    return False


def _compare_preview_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _build_cross_scope_lineage_summary(
    *,
    traces: list[dict[str, object]],
    target_index: int,
    target: dict[str, object],
) -> dict[str, object]:
    identity_key, identity_value = _gateway_identity_scope(target)
    if not identity_key or not identity_value:
        return {
            "status": "identity_unavailable",
            "identity_key": None,
            "session_count": 0,
            "channel_count": 0,
            "cross_session_visible": False,
            "cross_channel_visible": False,
        }

    target_session = str(target.get("session_id") or "").strip()
    target_channel = str(target.get("channel_id") or "").strip()
    matching_records: list[dict[str, object]] = []
    for record in traces[: target_index + 1]:
        if str(record.get("event") or "") != "telegram_update_processed":
            continue
        if str(record.get(identity_key) or "").strip() != identity_value:
            continue
        matching_records.append(record)

    session_counts: dict[str, int] = {}
    channel_counts: dict[str, int] = {}
    recent_cross_session_messages: list[dict[str, object]] = []
    for record in matching_records:
        session_id = str(record.get("session_id") or "unknown").strip() or "unknown"
        channel_id = str(record.get("channel_id") or "unknown").strip() or "unknown"
        session_counts[session_id] = session_counts.get(session_id, 0) + 1
        channel_counts[channel_id] = channel_counts.get(channel_id, 0) + 1

    for record in reversed(matching_records[:-1]):
        session_id = str(record.get("session_id") or "").strip()
        if target_session and session_id == target_session:
            continue
        preview = str(record.get("user_message_preview") or "").strip()
        if not preview:
            continue
        recent_cross_session_messages.append(
            {
                "request_id": record.get("request_id"),
                "recorded_at": record.get("recorded_at"),
                "session_id": session_id or None,
                "channel_id": record.get("channel_id"),
                "user_message_preview": _shorten(preview, 120),
                "routing_decision": record.get("routing_decision"),
            }
        )
        if len(recent_cross_session_messages) >= 5:
            break

    sessions = {session for session in session_counts if session != "unknown"}
    channels = {channel for channel in channel_counts if channel != "unknown"}
    return {
        "status": "checked",
        "identity_key": identity_key,
        "identity_value_hash": _stable_hash(identity_value),
        "target_session_id": target_session or None,
        "target_channel_id": target_channel or None,
        "session_count": len(sessions),
        "channel_count": len(channels),
        "message_count": len(matching_records),
        "session_counts": session_counts,
        "channel_counts": channel_counts,
        "cross_session_visible": any(session != target_session for session in sessions) if target_session else len(sessions) > 1,
        "cross_channel_visible": any(channel != target_channel for channel in channels) if target_channel else len(channels) > 1,
        "recent_cross_session_messages": recent_cross_session_messages,
    }


def _gateway_identity_scope(record: dict[str, object]) -> tuple[str | None, str | None]:
    for key in ("telegram_user_id", "human_id", "chat_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return key, value
    return None, None


def _looks_like_close_turn_recall_question(text: str) -> bool:
    normalized = text.lower()
    if not normalized.strip():
        return False
    recall_markers = (
        "what did i just",
        "what phrase did i",
        "what did you just",
        "i just told you",
        "message right before",
        "last thing i said",
        "previous message",
    )
    return any(marker in normalized for marker in recall_markers)


def _is_provisional_researcher_route(
    *,
    bridge_mode: str,
    routing_decision: str,
    evidence_summary: str,
) -> bool:
    route_text = f"{bridge_mode} {routing_decision}".lower()
    evidence_text = evidence_summary.lower()
    return (
        "researcher_advisory" in route_text
        or ("status=partial" in evidence_text and "provisional" in evidence_text)
    )


def _stable_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _build_context_capsule_findings(context_capsule: dict[str, object]) -> list[MemoryDoctorFinding]:
    if context_capsule.get("status") == "request_not_found":
        gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
        gateway_status = gateway_trace.get("status") if isinstance(gateway_trace, dict) else None
        return [
            MemoryDoctorFinding(
                name="context_capsule_request_not_found",
                ok=False,
                severity="high",
                detail=(
                    "no provider context capsule event was recorded for the requested id; "
                    f"gateway_trace={gateway_status or 'unknown'}"
                ),
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        ]
    if context_capsule.get("status") != "checked":
        return []
    gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
    if gateway_trace.get("lineage_gap"):
        return [
            MemoryDoctorFinding(
                name="context_capsule_gateway_trace_gap",
                ok=False,
                severity="high",
                detail=(
                    "gateway trace had "
                    f"{int(gateway_trace.get('recent_gateway_message_count') or 0)} earlier Telegram message(s) "
                    "for the same session, but the selected provider capsule had 0 recent-conversation source(s)"
                ),
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        ]
    findings: list[MemoryDoctorFinding] = []
    delivery_trace = gateway_trace.get("delivery_trace") if isinstance(gateway_trace.get("delivery_trace"), dict) else {}
    if delivery_trace.get("delivery_failed"):
        findings.append(
            MemoryDoctorFinding(
                name="telegram_delivery_failure",
                ok=False,
                severity="high",
                detail="generated reply reached the Telegram outbound audit, but delivery failed for the diagnosed turn.",
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        )
    elif delivery_trace.get("delivery_topic_miss"):
        findings.append(
            MemoryDoctorFinding(
                name="delivery_answer_grounding_gap",
                ok=False,
                severity="high",
                detail=(
                    "generated reply contained the expected close-turn topic, "
                    "but the Telegram outbound audit shows a delivered reply without it."
                ),
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        )
    if gateway_trace.get("answer_topic_miss"):
        findings.append(
            MemoryDoctorFinding(
                name="context_to_answer_grounding_gap",
                ok=False,
                severity="high",
                detail=(
                    "recent context contained "
                    f"'{gateway_trace.get('expected_topic')}', but the reply did not use it; "
                    f"route={gateway_trace.get('target_routing_decision') or 'unknown'} "
                    f"evidence={gateway_trace.get('target_evidence_summary') or 'unknown'}"
                ),
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        )
    elif gateway_trace.get("route_contamination"):
        findings.append(
            MemoryDoctorFinding(
                name="close_turn_route_contamination",
                ok=False,
                severity="high",
                detail=(
                    "close-turn recall reached a context capsule, but the answer route used provisional researcher advisory "
                    f"instead of grounded conversation/provider recall; route={gateway_trace.get('target_routing_decision') or 'unknown'}"
                ),
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        )
    if int(context_capsule.get("recent_conversation_count") or 0) != 0:
        findings.append(
            MemoryDoctorFinding(
            name="context_capsule_recent_conversation",
            ok=True,
            severity="medium",
            detail="selected provider capsule included recent same-session conversation.",
                request_id=str(context_capsule.get("request_id") or "") or None,
            )
        )
        return findings
    findings.append(
        MemoryDoctorFinding(
            name="context_capsule_recent_conversation_gap",
            ok=True,
            severity="medium",
            detail=(
                "selected provider capsule had 0 recent-conversation source(s); "
                "close-turn recall may fail even when durable memory is healthy"
            ),
            request_id=str(context_capsule.get("request_id") or "") or None,
        )
    )
    return findings


def _build_movement_trace(
    *,
    influence_events: list[dict[str, object]],
    write_requested_events: list[dict[str, object]],
    write_succeeded_events: list[dict[str, object]],
    read_requested_events: list[dict[str, object]],
    read_result_events: list[dict[str, object]],
    lifecycle_events: list[dict[str, object]],
    policy_gate_events: list[dict[str, object]],
    tool_result_events: list[dict[str, object]],
    context_capsule: dict[str, object],
    dashboard: dict[str, object],
    forget_postcondition: dict[str, object],
) -> dict[str, object]:
    gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
    cross_scope = gateway_trace.get("cross_scope_lineage") if isinstance(gateway_trace.get("cross_scope_lineage"), dict) else {}
    delivery_trace = gateway_trace.get("delivery_trace") if isinstance(gateway_trace.get("delivery_trace"), dict) else {}
    diagnostic_invocations = (
        gateway_trace.get("diagnostic_invocations")
        if isinstance(gateway_trace.get("diagnostic_invocations"), list)
        else []
    )
    contextual_diagnostic_invocations = [
        invocation
        for invocation in diagnostic_invocations
        if isinstance(invocation, dict) and invocation.get("contextual_trigger_score") is not None
    ]
    latest_diagnostic_invocation = next(
        (invocation for invocation in diagnostic_invocations if isinstance(invocation, dict)),
        {},
    )
    read_abstentions = [
        event for event in read_result_events if str(event.get("event_type") or "") == "memory_read_abstained"
    ]
    memory_shadow_counts = dashboard.get("memory_shadow_counts") if isinstance(dashboard.get("memory_shadow_counts"), dict) else {}
    lane_counts = dashboard.get("memory_lane_hygiene_counts") if isinstance(dashboard.get("memory_lane_hygiene_counts"), dict) else {}
    stages = [
        {
            "stage": "telegram_gateway",
            "status": gateway_trace.get("status") or "not_checked",
            "recent_message_count": int(gateway_trace.get("recent_gateway_message_count") or 0)
            if isinstance(gateway_trace, dict)
            else 0,
            "lineage_gap": bool(gateway_trace.get("lineage_gap")) if isinstance(gateway_trace, dict) else False,
        },
        {
            "stage": "answer_grounding",
            "status": gateway_trace.get("status") or "not_checked",
            "answer_topic_miss": bool(gateway_trace.get("answer_topic_miss")) if isinstance(gateway_trace, dict) else False,
            "route_contamination": bool(gateway_trace.get("route_contamination")) if isinstance(gateway_trace, dict) else False,
            "routing_decision": gateway_trace.get("target_routing_decision") if isinstance(gateway_trace, dict) else None,
            "bridge_mode": gateway_trace.get("target_bridge_mode") if isinstance(gateway_trace, dict) else None,
        },
        {
            "stage": "telegram_delivery_trace",
            "status": delivery_trace.get("status") or "not_checked",
            "delivery_ok": delivery_trace.get("delivery_ok"),
            "delivery_failed": bool(delivery_trace.get("delivery_failed")),
            "response_mismatch": bool(delivery_trace.get("response_mismatch")),
            "delivery_topic_miss": bool(delivery_trace.get("delivery_topic_miss")),
            "delivery_medium": delivery_trace.get("delivery_medium"),
        },
        {
            "stage": "memory_doctor_intake",
            "status": "checked" if diagnostic_invocations else "not_seen",
            "diagnostic_invocation_count": len(diagnostic_invocations),
            "contextual_trigger_count": len(contextual_diagnostic_invocations),
            "previous_failure_signal_count": sum(
                1
                for invocation in contextual_diagnostic_invocations
                if isinstance(invocation, dict) and invocation.get("previous_failure_signal")
            ),
            "latest_request_id": latest_diagnostic_invocation.get("request_id")
            if isinstance(latest_diagnostic_invocation, dict)
            else None,
            "latest_contextual_trigger_score": latest_diagnostic_invocation.get("contextual_trigger_score")
            if isinstance(latest_diagnostic_invocation, dict)
            else None,
            "latest_contextual_trigger_threshold": latest_diagnostic_invocation.get("contextual_trigger_threshold")
            if isinstance(latest_diagnostic_invocation, dict)
            else None,
            "latest_contextual_trigger_signals": list(
                latest_diagnostic_invocation.get("contextual_trigger_signals") or []
            )
            if isinstance(latest_diagnostic_invocation, dict)
            else [],
        },
        {
            "stage": "cross_session_channel_lineage",
            "status": cross_scope.get("status") or "not_checked",
            "session_count": int(cross_scope.get("session_count") or 0),
            "channel_count": int(cross_scope.get("channel_count") or 0),
            "cross_session_visible": bool(cross_scope.get("cross_session_visible")),
            "cross_channel_visible": bool(cross_scope.get("cross_channel_visible")),
        },
        {
            "stage": "builder_influence",
            "status": "checked",
            "event_count": len(influence_events),
            "tool_result_count": len(tool_result_events),
        },
        {
            "stage": "memory_writes",
            "status": "checked",
            "requested_count": len(write_requested_events),
            "succeeded_count": len(write_succeeded_events),
        },
        {
            "stage": "forget_postconditions",
            "status": forget_postcondition.get("status") or "not_checked",
            "checked_delete_target_count": int(forget_postcondition.get("checked_delete_target_count") or 0),
            "stale_target_count": int(forget_postcondition.get("stale_target_count") or 0),
        },
        {
            "stage": "memory_reads",
            "status": "checked",
            "requested_count": len(read_requested_events),
            "result_count": len(read_result_events),
            "abstained_count": len(read_abstentions),
        },
        {
            "stage": "memory_lifecycle_and_policy",
            "status": "checked",
            "lifecycle_transition_count": len(lifecycle_events),
            "policy_gate_block_count": len(policy_gate_events),
        },
        {
            "stage": "context_capsule",
            "status": context_capsule.get("status"),
            "request_id": context_capsule.get("request_id"),
            "recent_conversation_count": int(context_capsule.get("recent_conversation_count") or 0),
            "source_counts": context_capsule.get("source_counts") if isinstance(context_capsule.get("source_counts"), dict) else {},
        },
        {
            "stage": "dashboard_watchtower",
            "status": dashboard.get("top_level_state") or "unknown",
            "contract_violations": int(memory_shadow_counts.get("contract_violations") or 0),
            "invalid_role_events": int(memory_shadow_counts.get("invalid_role_events") or 0),
            "blocked_promotions": int(lane_counts.get("blocked_promotions") or 0),
        },
    ]

    gaps: list[dict[str, object]] = []
    if isinstance(gateway_trace, dict) and gateway_trace.get("lineage_gap"):
        gaps.append(
            {
                "name": "gateway_to_context_capsule_gap",
                "severity": "high",
                "detail": "Telegram gateway has same-session messages that are absent from recent_conversation in the provider capsule.",
                "request_id": context_capsule.get("request_id"),
            }
        )
    if isinstance(delivery_trace, dict) and delivery_trace.get("delivery_failed"):
        gaps.append(
            {
                "name": "telegram_delivery_failure",
                "severity": "high",
                "detail": "The diagnosed reply reached outbound audit but did not deliver successfully.",
                "request_id": context_capsule.get("request_id"),
            }
        )
    elif isinstance(delivery_trace, dict) and delivery_trace.get("delivery_topic_miss"):
        gaps.append(
            {
                "name": "delivery_answer_grounding_gap",
                "severity": "high",
                "detail": "Generated close-turn answer included the expected topic, but delivered text did not.",
                "request_id": context_capsule.get("request_id"),
            }
        )
    if isinstance(gateway_trace, dict) and gateway_trace.get("answer_topic_miss"):
        gaps.append(
            {
                "name": "context_to_answer_grounding_gap",
                "severity": "high",
                "detail": "Recent conversation reached the provider capsule, but the delivered reply ignored the expected close-turn topic.",
                "request_id": context_capsule.get("request_id"),
            }
        )
    elif isinstance(gateway_trace, dict) and gateway_trace.get("route_contamination"):
        gaps.append(
            {
                "name": "close_turn_route_contamination",
                "severity": "high",
                "detail": "Close-turn recall was answered through provisional researcher advisory instead of a grounded conversation route.",
                "request_id": context_capsule.get("request_id"),
            }
        )
    if len(write_requested_events) > len(write_succeeded_events):
        gaps.append(
            {
                "name": "memory_write_result_gap",
                "severity": "medium",
                "detail": "Recent memory write requests outnumber write result events.",
            }
        )
    if int(forget_postcondition.get("stale_target_count") or 0) > 0:
        gaps.append(
            {
                "name": "memory_forget_postcondition_failed",
                "severity": "high",
                "detail": "A forget/delete write was accepted, but matching active current-state memory remained visible.",
            }
        )
    if int(memory_shadow_counts.get("contract_violations") or 0) > 0:
        gaps.append(
            {
                "name": "memory_contract_violation",
                "severity": "high",
                "detail": "Watchtower reports memory contract violations.",
            }
        )
    if int(memory_shadow_counts.get("invalid_role_events") or 0) > 0:
        gaps.append(
            {
                "name": "invalid_memory_role_event",
                "severity": "high",
                "detail": "Watchtower reports invalid memory role events.",
            }
        )

    return {
        "status": "checked",
        "scope": (
            "gateway -> delivery audit -> cross-session/channel lineage -> builder events -> memory reads/writes -> "
            "forget postconditions -> lifecycle/policy gates -> context capsule -> watchtower dashboard"
        ),
        "stages": stages,
        "gaps": gaps,
        "unknowns": _movement_trace_unknowns(context_capsule=context_capsule),
    }


def _movement_trace_unknowns(*, context_capsule: dict[str, object]) -> list[str]:
    unknowns: list[str] = []
    gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
    if not isinstance(gateway_trace, dict) or gateway_trace.get("status") in {None, "not_requested", "not_found", "request_not_found"}:
        unknowns.append("Gateway transcript lineage was not fully available for the selected provider capsule.")
    cross_scope = gateway_trace.get("cross_scope_lineage") if isinstance(gateway_trace, dict) else {}
    if not isinstance(cross_scope, dict) or cross_scope.get("status") in {None, "identity_unavailable"}:
        unknowns.append("Cross-session/channel lineage could not be scoped to a stable gateway identity.")
    if context_capsule.get("status") != "checked":
        unknowns.append("No recent context capsule event was available to audit.")
    return unknowns


def _build_path_traces(
    *,
    influence_events: list[dict[str, object]],
    write_requested_events: list[dict[str, object]],
    write_succeeded_events: list[dict[str, object]],
    tool_result_events: list[dict[str, object]],
    topic: str | None,
    request_id: str | None,
) -> list[dict[str, object]]:
    normalized_topic = str(topic or "").strip().lower()
    normalized_request_id = str(request_id or "").strip()
    traces: list[dict[str, object]] = []
    for event in influence_events:
        if str(event.get("component") or "") != "researcher_bridge":
            continue
        event_request_id = str(event.get("request_id") or "").strip()
        if not event_request_id:
            continue
        if normalized_request_id:
            if not _request_id_matches(event_request_id, request_id=normalized_request_id):
                continue
        elif normalized_topic and normalized_topic not in _event_search_text(event).lower():
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        traces.append(
            {
                "request_id": event_request_id,
                "message_text": _shorten(_memory_doctor_message_text(facts), 180),
                "influence_summary": _shorten(str(event.get("summary") or ""), 160),
                "write_requests": _matching_event_count(write_requested_events, request_id=event_request_id),
                "write_results": _matching_event_count(write_succeeded_events, request_id=event_request_id),
                "tool_results": _matching_event_count(tool_result_events, request_id=event_request_id),
            }
        )
        if len(traces) >= 5:
            break
    return traces


def _build_root_cause_summary(
    *,
    findings: list[MemoryDoctorFinding],
    context_capsule: dict[str, object],
    movement_trace: dict[str, object],
) -> dict[str, object]:
    failing = [finding for finding in findings if not finding.ok]
    if not failing:
        return {
            "status": "clear",
            "primary_gap": None,
            "failure_layer": None,
            "chain": [],
            "telegram_summary": "no failing root cause identified",
            "request_id": context_capsule.get("request_id"),
            "confidence": "high",
            "confidence_reason": "no failing findings were present",
            "disconfirming_checks": [],
            "audit_handoff": {
                "status": "not_needed",
                "authority": "diagnostic_handoff_not_repair_authority",
            },
        }

    profiles: dict[str, dict[str, object]] = {
        "context_capsule_gateway_trace_gap": {
            "failure_layer": "context_ingress",
            "chain": ["telegram_gateway", "context_capsule", "provider_context"],
            "telegram_summary": "gateway -> provider context gap",
            "movement_gap": "gateway_to_context_capsule_gap",
            "repair_plan": {
                "owner_surface": "telegram_gateway_to_context_capsule",
                "audit_focus": ["gateway_trace", "context_capsule_source_ledger", "recent_conversation"],
                "next_action": (
                    "Repair the recent-conversation capsule path: compare the gateway transcript, Builder request id, "
                    "and provider capsule source ledger, then replay the same close-turn request."
                ),
                "replay_probe": "repeat the same two-turn Telegram close-turn recall probe",
            },
        },
        "context_capsule_request_not_found": {
            "failure_layer": "provider_context_recording",
            "chain": ["telegram_gateway", "provider_invocation", "context_capsule_event"],
            "telegram_summary": "provider context event missing",
            "repair_plan": {
                "owner_surface": "provider_invocation_context_recording",
                "audit_focus": ["gateway_trace", "provider_invocation", "context_capsule_compiled"],
                "next_action": (
                    "Inspect provider invocation and context-capsule recording for that request id; gateway trace "
                    "without a capsule means the context path was bypassed, failed before compile, or did not emit instrumentation."
                ),
                "replay_probe": "rerun Memory Doctor with the exact request id after restoring capsule emission",
            },
        },
        "context_to_answer_grounding_gap": {
            "failure_layer": "answer_grounding",
            "chain": ["telegram_gateway", "context_capsule", "answer_grounding"],
            "telegram_summary": "provider context -> answer grounding gap",
            "movement_gap": "context_to_answer_grounding_gap",
            "repair_plan": {
                "owner_surface": "answer_arbitration",
                "audit_focus": ["context_capsule", "answer_grounding", "route_arbitration"],
                "next_action": (
                    "Fix answer grounding for close-turn recall: replay the captured provider capsule and make answer "
                    "arbitration prefer recent conversation over unrelated advisory output."
                ),
                "replay_probe": "replay the same request and verify the expected topic appears in the generated answer",
            },
        },
        "close_turn_route_contamination": {
            "failure_layer": "route_arbitration",
            "chain": ["telegram_gateway", "context_capsule", "route_arbitration"],
            "telegram_summary": "close-turn route arbitration gap",
            "movement_gap": "close_turn_route_contamination",
            "repair_plan": {
                "owner_surface": "route_arbitration",
                "audit_focus": ["routing_decision", "bridge_mode", "evidence_summary"],
                "next_action": (
                    "Fix route arbitration: close-turn recall should stay on grounded conversation/provider recall "
                    "instead of provisional researcher advisory."
                ),
                "replay_probe": "rerun the close-turn prompt and verify the route is recent-conversation/provider grounded",
            },
        },
        "delivery_answer_grounding_gap": {
            "failure_layer": "telegram_delivery",
            "chain": ["answer_generation", "telegram_delivery"],
            "telegram_summary": "answer generation -> Telegram delivery gap",
            "movement_gap": "delivery_answer_grounding_gap",
            "repair_plan": {
                "owner_surface": "telegram_delivery",
                "audit_focus": ["generated_response", "outbound_audit", "delivered_response"],
                "next_action": (
                    "Fix Telegram delivery preservation: diff the generated answer against outbound audit text and "
                    "remove any delivery-stage mutation that drops the grounded topic."
                ),
                "replay_probe": "replay the same request and verify generated and delivered replies preserve the same topic",
            },
        },
        "telegram_delivery_failure": {
            "failure_layer": "telegram_delivery",
            "chain": ["answer_generation", "telegram_delivery"],
            "telegram_summary": "Telegram delivery failure",
            "movement_gap": "telegram_delivery_failure",
            "repair_plan": {
                "owner_surface": "telegram_delivery",
                "audit_focus": ["outbound_audit", "delivery_registry", "telegram_bridge"],
                "next_action": "Inspect Telegram delivery errors for the diagnosed request, then replay the same generated reply.",
                "replay_probe": "verify the outbound audit records delivery_ok=true for the replayed request",
            },
        },
        "memory_forget_postcondition_failed": {
            "failure_layer": "current_state_delete",
            "chain": ["forget_request", "current_state", "postcondition"],
            "telegram_summary": "forget postcondition gap",
            "movement_gap": "memory_forget_postcondition_failed",
            "repair_plan": {
                "owner_surface": "current_state_memory",
                "audit_focus": ["delete_write_result", "current_state_lookup", "postcondition"],
                "next_action": (
                    "Inspect current-state delete postconditions: the forget write was accepted, but an active fact "
                    "still matched the deleted target."
                ),
                "replay_probe": "rerun the same forget request and verify active current state no longer contains the target",
            },
        },
        "memory_delete_intent_integrity": {
            "failure_layer": "delete_write_fanout",
            "chain": ["forget_request", "memory_write_requests", "memory_write_results"],
            "telegram_summary": "delete write fanout gap",
            "repair_plan": {
                "owner_surface": "memory_write_fanout",
                "audit_focus": ["delete_intent_parser", "memory_write_requested", "memory_write_succeeded"],
                "next_action": "Rerun the affected forget request, then ask `check memory deletes`.",
                "replay_probe": "verify every requested delete target has a matching accepted delete write",
            },
        },
        "topic_active_state_presence": {
            "failure_layer": "active_current_state",
            "chain": ["topic_scan", "active_current_state"],
            "telegram_summary": "active memory still contains that topic",
            "repair_plan": {
                "owner_surface": "active_current_state",
                "audit_focus": ["topic_scan", "active_profile", "current_state_lookup"],
                "next_action": "If that active field is wrong, forget that specific field and rerun Memory Doctor for the topic.",
                "replay_probe": "rerun Memory Doctor for the stale value and verify active current state no longer contains it",
            },
        },
    }
    priority = [
        "context_capsule_request_not_found",
        "context_capsule_gateway_trace_gap",
        "context_to_answer_grounding_gap",
        "close_turn_route_contamination",
        "delivery_answer_grounding_gap",
        "telegram_delivery_failure",
        "memory_forget_postcondition_failed",
        "memory_delete_intent_integrity",
        "topic_active_state_presence",
    ]
    findings_by_name = {finding.name: finding for finding in failing}
    selected = next((findings_by_name[name] for name in priority if name in findings_by_name), failing[0])
    profile = profiles.get(selected.name)
    gaps = movement_trace.get("gaps") if isinstance(movement_trace.get("gaps"), list) else []
    gap_names = {
        str(gap.get("name") or "")
        for gap in gaps
        if isinstance(gap, dict) and str(gap.get("name") or "").strip()
    }
    movement_gap = str(profile.get("movement_gap") or "") if isinstance(profile, dict) else ""
    if not movement_gap and selected.name in gap_names:
        movement_gap = selected.name
    movement_gap_seen = bool(movement_gap and movement_gap in gap_names)
    confidence = "high" if profile and (not movement_gap or movement_gap_seen) else "medium"
    request_id = selected.request_id or str(context_capsule.get("request_id") or "").strip() or None
    if profile is None:
        disconfirming_checks = ["inspect the raw finding before applying an automated repair plan"]
        return {
            "status": "identified",
            "primary_gap": selected.name,
            "failure_layer": "unknown",
            "chain": [],
            "telegram_summary": selected.name.replace("_", " "),
            "request_id": request_id,
            "confidence": "medium",
            "confidence_reason": "failing finding has no known root-cause profile",
            "disconfirming_checks": disconfirming_checks,
            "audit_handoff": _root_cause_audit_handoff(
                primary_gap=selected.name,
                repair_plan={},
                request_id=request_id,
                chain=[],
                disconfirming_checks=disconfirming_checks,
            ),
            "detail": selected.detail,
            "evidence": {
                "finding": selected.name,
                "context_capsule_status": context_capsule.get("status"),
                "movement_gap_seen": selected.name in gap_names,
            },
        }
    repair_plan = dict(profile.get("repair_plan") or {})
    disconfirming_checks = _root_cause_disconfirming_checks(selected.name)
    chain = list(profile["chain"]) if isinstance(profile.get("chain"), list) else []
    return {
        "status": "identified",
        "primary_gap": selected.name,
        "movement_gap": movement_gap or None,
        "failure_layer": profile["failure_layer"],
        "chain": chain,
        "telegram_summary": profile["telegram_summary"],
        "repair_plan": repair_plan,
        "request_id": request_id,
        "confidence": confidence,
        "confidence_reason": _root_cause_confidence_reason(
            movement_gap=movement_gap,
            movement_gap_seen=movement_gap_seen,
        ),
        "disconfirming_checks": disconfirming_checks,
        "audit_handoff": _root_cause_audit_handoff(
            primary_gap=selected.name,
            repair_plan=repair_plan,
            request_id=request_id,
            chain=chain,
            disconfirming_checks=disconfirming_checks,
        ),
        "detail": selected.detail,
        "evidence": {
            "finding": selected.name,
            "context_capsule_status": context_capsule.get("status"),
            "movement_gap_seen": movement_gap_seen,
        },
    }


def _build_recommendations(
    *,
    findings: list[MemoryDoctorFinding],
    active_profile: dict[str, object],
    topic_scan: dict[str, object],
    context_capsule: dict[str, object],
    root_cause: dict[str, object],
    repair_requested: bool,
) -> list[str]:
    recommendations: list[str] = []
    root_cause_repair = _root_cause_recommendation(root_cause)
    if root_cause_repair:
        recommendations.append(root_cause_repair)
    if any(finding.name == "memory_delete_intent_integrity" and not finding.ok for finding in findings):
        recommendations.append("Rerun the affected forget request, then ask `check memory deletes`.")
    if any(finding.name == "memory_forget_postcondition_failed" and not finding.ok for finding in findings):
        recommendations.append(
            "Inspect current-state delete postconditions: the forget write was accepted, but an active fact still matched the deleted target."
        )
    context_lineage_gap = any(
        finding.name == "context_capsule_gateway_trace_gap" and not finding.ok
        for finding in findings
    )
    if context_lineage_gap:
        recommendations.append(
            "Fix the recent-conversation capsule path: Telegram gateway saw close-turn messages, but provider context did not receive them."
        )
    missing_context_capsule = any(
        finding.name == "context_capsule_request_not_found" and not finding.ok
        for finding in findings
    )
    if missing_context_capsule:
        recommendations.append(
            "Inspect provider invocation and context-capsule recording for that request id; gateway trace without a "
            "capsule means the context path was bypassed, failed before compile, or did not emit instrumentation."
        )
    answer_grounding_gap = any(
        finding.name
        in {
            "context_to_answer_grounding_gap",
            "close_turn_route_contamination",
            "delivery_answer_grounding_gap",
            "telegram_delivery_failure",
        }
        and not finding.ok
        for finding in findings
    )
    if answer_grounding_gap:
        recommendations.append(
            "Fix the answer path for close-turn recall: verify gateway generation, Telegram delivery audit, and route arbitration all carry the same grounded answer."
        )
    active_presence = next((finding for finding in findings if finding.name == "topic_active_state_presence"), None)
    if active_presence is not None:
        topic = str(topic_scan.get("topic") or "that value")
        recommendations.append(
            f"If {topic} is wrong in that active field, ask Spark to forget that specific field, then rerun Memory Doctor for {topic}."
        )
    elif topic_scan.get("topic") and int(topic_scan.get("occurrence_count") or 0) > 0:
        recommendations.append(
            "Treat the topic as historical trace unless it appears in active current state; do not purge trace logs without an audit policy."
        )
    if active_profile.get("status") == "not_requested":
        recommendations.append("Run this from Telegram or pass `--human-id` so the doctor can inspect active profile facts.")
    if (
        not context_lineage_gap
        and
        context_capsule.get("status") == "checked"
        and int(context_capsule.get("recent_conversation_count") or 0) == 0
    ):
        recommendations.append(
            "For close-turn blankness, inspect the context capsule source ledger and recent Telegram transcript path; durable memory alone will not prove immediate context survived."
        )
    if repair_requested:
        recommendations.append("Repair mode is not automatic yet; Memory Doctor is diagnosis-only until repair authority is explicitly gated.")
    if not recommendations:
        recommendations.append("No immediate repair recommended; use `run memory doctor for <topic>` when a specific recall feels wrong.")
    return _dedupe_recommendations(recommendations)


def _root_cause_confidence_reason(*, movement_gap: str, movement_gap_seen: bool) -> str:
    if movement_gap and movement_gap_seen:
        return "failing finding and movement trace gap agree"
    if movement_gap:
        return "failing finding mapped to a known layer, but the movement trace did not include the expected gap"
    return "failing finding maps directly to a known failure layer"


def _root_cause_disconfirming_checks(primary_gap: str) -> list[str]:
    checks_by_gap = {
        "context_capsule_gateway_trace_gap": [
            "provider capsule for this request actually contains recent_conversation",
            "gateway trace belongs to a different human, chat, or session",
        ],
        "context_capsule_request_not_found": [
            "context capsule exists under a correlated replacement request id",
            "provider path intentionally bypassed context capsule for this request",
        ],
        "context_to_answer_grounding_gap": [
            "delivered reply already contains the expected close-turn topic",
            "expected topic was absent from the provider capsule",
        ],
        "close_turn_route_contamination": [
            "route evidence shows grounded recent-conversation recall, not provisional advisory",
        ],
        "delivery_answer_grounding_gap": [
            "outbound audit reply matches the generated answer",
            "delivered reply contains the expected close-turn topic",
        ],
        "telegram_delivery_failure": [
            "delivery registry shows a successful Telegram acknowledgement for the diagnosed request",
        ],
        "memory_forget_postcondition_failed": [
            "fresh current-state lookup no longer contains the forgotten target",
            "delete write applied to a different human or memory scope",
        ],
        "memory_delete_intent_integrity": [
            "every requested delete target has a matching accepted delete write",
        ],
        "topic_active_state_presence": [
            "the active field is intentionally correct and should not be forgotten",
        ],
    }
    return list(checks_by_gap.get(primary_gap) or ["raw trace contradicts the selected failure layer"])


def _root_cause_audit_handoff(
    *,
    primary_gap: str,
    repair_plan: dict[str, object],
    request_id: str | None,
    chain: list[object],
    disconfirming_checks: list[str],
) -> dict[str, object]:
    audit_focus = [
        str(item).strip()
        for item in (repair_plan.get("audit_focus") if isinstance(repair_plan.get("audit_focus"), list) else [])
        if str(item).strip()
    ]
    if not audit_focus:
        audit_focus = ["raw_finding", "movement_trace", "source_events"]
    owner_surface = str(repair_plan.get("owner_surface") or "").strip() or "manual_memory_path_triage"
    return {
        "status": "ready",
        "mode": "targeted_memory_path_audit" if repair_plan else "raw_memory_path_audit",
        "primary_gap": primary_gap,
        "owner_surface": owner_surface,
        "audit_focus": audit_focus,
        "request_id": request_id,
        "chain": [str(item) for item in chain if str(item).strip()],
        "questions": list(disconfirming_checks),
        "sample_strategy": (
            "sample the diagnosed request plus the nearest prior same-session Telegram turn across gateway, "
            "context capsule, memory reads/writes, answer generation, and outbound audit"
        ),
        "stop_ship_gate": (
            "do not promote a memory-path repair until the replay probe passes and each disconfirming check "
            "has been ruled out"
        ),
        "authority": "diagnostic_handoff_not_repair_authority",
    }


def _root_cause_recommendation(root_cause: dict[str, object]) -> str | None:
    if root_cause.get("status") != "identified":
        return None
    repair_plan = root_cause.get("repair_plan") if isinstance(root_cause.get("repair_plan"), dict) else {}
    next_action = str(repair_plan.get("next_action") or "").strip()
    if next_action:
        return next_action
    summary = str(root_cause.get("telegram_summary") or "").strip()
    if not summary:
        return None
    return f"Use the root-cause chain to repair {summary}, then replay the same request."


def _root_cause_telegram_repair_focus(root_cause: dict[str, object]) -> str | None:
    repair_plan = root_cause.get("repair_plan") if isinstance(root_cause.get("repair_plan"), dict) else {}
    owner_surface = str(repair_plan.get("owner_surface") or "").strip()
    labels = {
        "telegram_gateway_to_context_capsule": "recent-conversation capsule path",
        "provider_invocation_context_recording": "provider context recording",
        "answer_arbitration": "answer grounding",
        "route_arbitration": "close-turn route arbitration",
        "telegram_delivery": "Telegram delivery preservation",
        "current_state_memory": "current-state delete",
        "memory_write_fanout": "delete write fanout",
        "active_current_state": "active current-state cleanup",
    }
    if owner_surface in labels:
        return labels[owner_surface]
    if owner_surface:
        return owner_surface.replace("_", " ")
    return None


def _dedupe_recommendations(recommendations: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for recommendation in recommendations:
        text = str(recommendation or "").strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _brain_recommendations(brain: dict[str, object]) -> list[str]:
    improvements = brain.get("proactive_improvements") if isinstance(brain, dict) else []
    if not isinstance(improvements, list):
        return []
    recommendations: list[str] = []
    for improvement in improvements[:2]:
        if not isinstance(improvement, dict):
            continue
        action = str(improvement.get("action") or "").strip()
        next_probe = str(improvement.get("next_probe") or "").strip()
        if not action:
            continue
        recommendation = action
        if next_probe:
            recommendation = f"{recommendation} Next probe: {next_probe}."
        if recommendation not in recommendations:
            recommendations.append(recommendation)
    return recommendations


def _record_brain_snapshot(
    *,
    state_db: StateDB,
    brain: dict[str, object],
    human_id: str | None,
    topic: str | None,
    request_id: str | None,
) -> None:
    summary = brain.get("summary") if isinstance(brain.get("summary"), dict) else {}
    coverage = brain.get("coverage") if isinstance(brain.get("coverage"), dict) else {}
    gaps = [gap for gap in (brain.get("gaps") or []) if isinstance(gap, dict)]
    improvements = [
        improvement
        for improvement in (brain.get("proactive_improvements") or [])
        if isinstance(improvement, dict)
    ]
    telegram_intake = _brain_telegram_intake_snapshot(brain)
    root_cause = brain.get("root_cause") if isinstance(brain.get("root_cause"), dict) else {}
    repair_plan = root_cause.get("repair_plan") if isinstance(root_cause.get("repair_plan"), dict) else {}
    audit_handoff = root_cause.get("audit_handoff") if isinstance(root_cause.get("audit_handoff"), dict) else {}
    creator_alignment = brain.get("creator_system_alignment") if isinstance(brain.get("creator_system_alignment"), dict) else {}
    creator_alignment_issues = (
        creator_alignment.get("validation_issues")
        if isinstance(creator_alignment.get("validation_issues"), list)
        else []
    )
    try:
        record_event(
            state_db,
            event_type="memory_doctor_brain_evaluated",
            component="memory_doctor",
            summary=(
                "Memory Doctor Brain evaluated diagnostic coverage "
                f"score={summary.get('coverage_score', coverage.get('score', 'unknown'))} "
                f"gaps={summary.get('gap_count', len(gaps))}"
            ),
            human_id=human_id,
            actor_id="memory_doctor",
            evidence_lane="observability",
            severity="medium" if gaps else "info",
            status="observed",
            reason_code="memory_doctor_brain_snapshot",
            facts={
                "authority": "observability_non_authoritative",
                "coverage_score": summary.get("coverage_score", coverage.get("score")),
                "present_senses": list(coverage.get("present") or []),
                "missing_senses": list(coverage.get("missing") or []),
                "gap_names": [str(gap.get("name") or "") for gap in gaps],
                "highest_severity": summary.get("highest_severity"),
                "next_probe": summary.get("next_probe"),
                "proactive_improvement_names": [
                    str(improvement.get("name") or "") for improvement in improvements[:5]
                ],
                "topic": topic,
                "request_id": request_id,
                "telegram_intake": telegram_intake,
                "root_cause_status": root_cause.get("status"),
                "root_cause_primary_gap": root_cause.get("primary_gap"),
                "root_cause_failure_layer": root_cause.get("failure_layer"),
                "root_cause_chain": list(root_cause.get("chain") or []),
                "root_cause_confidence": root_cause.get("confidence"),
                "root_cause_confidence_reason": root_cause.get("confidence_reason"),
                "root_cause_disconfirming_checks": list(root_cause.get("disconfirming_checks") or []),
                "root_cause_summary": root_cause.get("telegram_summary"),
                "root_cause_owner_surface": repair_plan.get("owner_surface"),
                "root_cause_audit_focus": list(repair_plan.get("audit_focus") or []),
                "root_cause_repair_action": repair_plan.get("next_action"),
                "root_cause_replay_probe": repair_plan.get("replay_probe"),
                "root_cause_audit_handoff_status": audit_handoff.get("status"),
                "root_cause_audit_handoff_mode": audit_handoff.get("mode"),
                "root_cause_audit_handoff_questions": list(audit_handoff.get("questions") or []),
                "root_cause_audit_handoff_sample_strategy": audit_handoff.get("sample_strategy"),
                "root_cause_audit_handoff_stop_gate": audit_handoff.get("stop_ship_gate"),
                "creator_alignment_status": creator_alignment.get("status"),
                "creator_alignment_artifact_targets": list(creator_alignment.get("artifact_targets") or []),
                "creator_alignment_validation_issue_count": len(creator_alignment_issues),
                "non_override_rule": "doctor brain snapshots are diagnostics, not memory facts or repair authority",
            },
        )
    except Exception:
        return


def _brain_telegram_intake_snapshot(brain: dict[str, object]) -> dict[str, object]:
    senses = brain.get("senses") if isinstance(brain.get("senses"), list) else []
    intake_sense = next(
        (sense for sense in senses if isinstance(sense, dict) and sense.get("name") == "telegram_doctor_intake_lineage"),
        {},
    )
    evidence = intake_sense.get("evidence") if isinstance(intake_sense.get("evidence"), dict) else {}
    invocations = evidence.get("diagnostic_invocations") if isinstance(evidence.get("diagnostic_invocations"), list) else []
    invocation = next((item for item in invocations if isinstance(item, dict)), None)
    if invocation is None:
        return {}
    return {
        "request_id": invocation.get("request_id"),
        "user_message_preview": invocation.get("user_message_preview"),
        "request_selector": invocation.get("request_selector"),
        "contextual_trigger_score": invocation.get("contextual_trigger_score"),
        "contextual_trigger_threshold": invocation.get("contextual_trigger_threshold"),
        "contextual_trigger_signals": list(invocation.get("contextual_trigger_signals") or []),
        "previous_failure_signal": invocation.get("previous_failure_signal"),
        "previous_failure_signals": list(invocation.get("previous_failure_signals") or []),
    }


def _matching_event_count(events: list[dict[str, object]], *, request_id: str) -> int:
    return sum(1 for event in events if _request_id_matches(event.get("request_id"), request_id=request_id))


def _event_search_text(event: dict[str, object]) -> str:
    try:
        facts_text = json.dumps(event.get("facts_json") or {}, sort_keys=True, default=str)
        provenance_text = json.dumps(event.get("provenance_json") or {}, sort_keys=True, default=str)
    except TypeError:
        facts_text = str(event.get("facts_json") or "")
        provenance_text = str(event.get("provenance_json") or "")
    return " ".join(
        str(part or "")
        for part in (
            event.get("event_type"),
            event.get("component"),
            event.get("reason_code"),
            event.get("summary"),
            facts_text,
            provenance_text,
        )
    )


def _shorten(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _memory_doctor_message_text(facts: dict[str, object]) -> str:
    for key in ("detected_generic_memory_deletion", "detected_generic_memory_observation"):
        payload = facts.get(key)
        if isinstance(payload, dict):
            message_text = str(payload.get("message_text") or "").strip()
            if message_text:
                return message_text
    payloads = facts.get("detected_generic_memory_deletions")
    if isinstance(payloads, list):
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            message_text = str(payload.get("message_text") or payload.get("evidence_text") or "").strip()
            if message_text:
                return message_text
    return ""


def _normalized_message_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _request_id_matches(event_request_id: object, *, request_id: str) -> bool:
    candidate = str(event_request_id or "").strip()
    if not candidate or not request_id:
        return False
    return candidate == request_id or candidate.startswith(f"{request_id}:delete-")


def _delete_write_request_count(events: list[dict[str, object]], *, request_id: str) -> int:
    count = 0
    for event in events:
        if str(event.get("component") or "") != "memory_orchestrator":
            continue
        if not _request_id_matches(event.get("request_id"), request_id=request_id):
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        if str(facts.get("operation") or "") == "delete":
            count += 1
    return count


def _accepted_delete_write_count(events: list[dict[str, object]], *, request_id: str) -> int:
    count = 0
    for event in events:
        if str(event.get("component") or "") != "memory_orchestrator":
            continue
        if not _request_id_matches(event.get("request_id"), request_id=request_id):
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        if str(facts.get("operation") or "") != "delete":
            continue
        count += int(facts.get("accepted_count") or 0)
    return count
