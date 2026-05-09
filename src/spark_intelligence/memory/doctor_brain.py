from __future__ import annotations

from typing import Any

from spark_intelligence.creator.contracts import ArtifactManifest, validate_artifact_manifest
import spark_intelligence.memory.orchestrator as memory_orchestrator
from spark_intelligence.state.db import StateDB


MEMORY_DOCTOR_BRAIN_VERSION = "2026-05-09.v4"

AUTHORITY_BOUNDARIES: tuple[str, ...] = (
    "current_state_memory_outranks_wiki_for_mutable_user_facts",
    "diagnostics_explain_memory_movement_but_do_not_rewrite_memory",
    "dashboard_and_wiki_outputs_are_observability_not_prompt_instructions",
    "automatic_repair_requires_explicit_repair_authority_gate",
)

MEMORY_EVAL_CAPABILITIES: tuple[str, ...] = (
    "information_extraction",
    "multi_session_reasoning",
    "temporal_reasoning",
    "knowledge_updates",
    "abstention",
    "source_explanation",
    "close_turn_context_recall",
)


def build_memory_doctor_brain(
    *,
    state_db: StateDB,
    config_manager: Any | None,
    findings: list[Any],
    event_counts: dict[str, int],
    active_profile: dict[str, object],
    topic_scan: dict[str, object],
    context_capsule: dict[str, object],
    movement_trace: dict[str, object],
    root_cause: dict[str, object],
    dashboard: dict[str, object],
    path_traces: list[dict[str, object]],
) -> dict[str, object]:
    runtime = _inspect_runtime(config_manager)
    wiki_packets = _inspect_wiki_packets(config_manager)
    dashboard_movement = _inspect_dashboard_movement(config_manager)
    llm_wiki = _inspect_llm_wiki_status(config_manager=config_manager, state_db=state_db)
    creator_system_alignment = _build_creator_system_alignment()
    senses = _build_senses(
        runtime=runtime,
        wiki_packets=wiki_packets,
        dashboard_movement=dashboard_movement,
        llm_wiki=llm_wiki,
        active_profile=active_profile,
        context_capsule=context_capsule,
        movement_trace=movement_trace,
        root_cause=root_cause,
        dashboard=dashboard,
        event_counts=event_counts,
        path_traces=path_traces,
    )
    coverage = _score_coverage(senses)
    gaps = _build_gaps(
        senses=senses,
        coverage=coverage,
        findings=findings,
        active_profile=active_profile,
        topic_scan=topic_scan,
        context_capsule=context_capsule,
        movement_trace=movement_trace,
        root_cause=root_cause,
        wiki_packets=wiki_packets,
        dashboard_movement=dashboard_movement,
        llm_wiki=llm_wiki,
        event_counts=event_counts,
        path_traces=path_traces,
    )
    improvements = _build_proactive_improvements(gaps)
    if not improvements:
        improvements.append(
            {
                "name": "keep_memory_doctor_regression_loop_warm",
                "priority": "low",
                "metric": "coverage_score",
                "evidence": f"visibility_score={coverage['score']}",
                "action": "Keep running Memory Doctor after memory fixes and add focused probes when a new failure class appears.",
                "next_probe": "run memory doctor for the exact confusing value or route after each memory-path change",
            }
        )

    return {
        "version": MEMORY_DOCTOR_BRAIN_VERSION,
        "mode": "diagnostic_and_proactive_recommendation",
        "coverage": coverage,
        "senses": senses,
        "gaps": gaps,
        "proactive_improvements": improvements[:8],
        "root_cause": root_cause,
        "runtime": runtime,
        "wiki_packets": wiki_packets,
        "llm_wiki": llm_wiki,
        "dashboard_movement": dashboard_movement,
        "creator_system_alignment": creator_system_alignment,
        "eval_capabilities": list(MEMORY_EVAL_CAPABILITIES),
        "authority_boundaries": list(AUTHORITY_BOUNDARIES),
        "summary": _brain_summary(coverage=coverage, gaps=gaps, improvements=improvements),
    }


def memory_doctor_brain_summary(brain: dict[str, object]) -> str:
    summary = brain.get("summary") if isinstance(brain, dict) else {}
    if not isinstance(summary, dict):
        return "visibility unknown"
    score = summary.get("coverage_score")
    gap_count = summary.get("gap_count")
    next_probe = str(summary.get("next_probe") or "").strip()
    base = f"visibility {score}/100, {gap_count} gap(s)"
    if next_probe:
        return f"{base}, next probe: {next_probe}"
    return base


def _inspect_runtime(config_manager: Any | None) -> dict[str, object]:
    if config_manager is None:
        return {"status": "not_requested", "ready": False, "reason": "config_manager_missing"}
    try:
        payload = memory_orchestrator.inspect_memory_sdk_runtime(config_manager=config_manager)
    except Exception as exc:  # pragma: no cover - diagnostics should survive broken optional imports
        return {"status": "error", "ready": False, "reason": f"runtime_inspection_failed:{exc.__class__.__name__}"}
    result = dict(payload)
    result["status"] = "ready" if result.get("ready") else "unavailable"
    return result


def _inspect_wiki_packets(config_manager: Any | None) -> dict[str, object]:
    if config_manager is None:
        return {
            "status": "not_requested",
            "reason": "config_manager_missing",
            "packet_count": 0,
            "source_families_visible": False,
            "memory_kb": {"present": False, "packet_count": 0, "family_counts": {}},
        }
    try:
        return dict(memory_orchestrator.inspect_wiki_packet_metadata(config_manager=config_manager))
    except Exception as exc:  # pragma: no cover
        return {
            "status": "error",
            "reason": f"wiki_packet_metadata_failed:{exc.__class__.__name__}",
            "packet_count": 0,
            "source_families_visible": False,
            "memory_kb": {"present": False, "packet_count": 0, "family_counts": {}},
        }


def _inspect_dashboard_movement(config_manager: Any | None) -> dict[str, object]:
    if config_manager is None:
        return {"status": "not_requested", "reason": "config_manager_missing", "row_count": 0}
    try:
        return dict(memory_orchestrator.inspect_memory_movement_status(config_manager=config_manager))
    except Exception as exc:  # pragma: no cover
        return {
            "status": "error",
            "reason": f"movement_inspection_failed:{exc.__class__.__name__}",
            "row_count": 0,
            "movement_counts": {},
        }


def _inspect_llm_wiki_status(*, config_manager: Any | None, state_db: StateDB) -> dict[str, object]:
    if config_manager is None:
        return {"status": "not_requested", "reason": "config_manager_missing", "healthy": False}
    root = config_manager.paths.home / "wiki"
    if not root.exists():
        return {"status": "missing", "reason": "wiki_root_missing", "healthy": False, "output_dir": str(root)}
    try:
        from spark_intelligence.llm_wiki.status import build_llm_wiki_status

        status = build_llm_wiki_status(config_manager=config_manager, state_db=state_db)
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "reason": f"llm_wiki_status_failed:{exc.__class__.__name__}", "healthy": False}
    payload = status.payload
    freshness = payload.get("freshness_health") if isinstance(payload.get("freshness_health"), dict) else {}
    return {
        "status": "healthy" if payload.get("healthy") else "degraded",
        "healthy": bool(payload.get("healthy")),
        "output_dir": payload.get("output_dir"),
        "markdown_page_count": int(payload.get("markdown_page_count") or 0),
        "wiki_retrieval_status": payload.get("wiki_retrieval_status"),
        "wiki_record_count": int(payload.get("wiki_record_count") or 0),
        "memory_kb_present": bool((payload.get("memory_kb_discovery") or {}).get("present"))
        if isinstance(payload.get("memory_kb_discovery"), dict)
        else False,
        "stale_page_count": int(freshness.get("stale_page_count") or 0),
        "warnings": list(payload.get("warnings") or [])[:10],
        "authority": payload.get("authority") or "supporting_not_authoritative",
    }


def _build_creator_system_alignment() -> dict[str, object]:
    manifests = [
        ArtifactManifest(
            artifact_id="memory-doctor-domain-chip-candidate-v1",
            artifact_type="domain_chip",
            repo="domain-chip-memory",
            inputs=["memory-doctor-brain", "memory-doctor-benchmark"],
            outputs=["spark-chip.json", "hooks/evaluate", "hooks/watchtower", "packets/memory-doctor.md"],
            validation_commands=[
                "python -m pytest tests/test_memory_doctor_brain.py tests/test_memory_doctor_benchmark.py"
            ],
            promotion_gates=["schema_gate", "lineage_gate", "memory_hygiene_gate", "benchmark_gate", "rollback_gate"],
            rollback_plan="Disable the attached memory-doctor chip candidate and keep Builder's diagnosis-only runtime path.",
        ),
        ArtifactManifest(
            artifact_id="memory-doctor-specialization-path-v1",
            artifact_type="specialization_path",
            repo="specialization-path-memory-doctor",
            inputs=["memory-doctor-domain-chip-candidate-v1"],
            outputs=["specialization-path.json", "benchmarks/memory-doctor.blankness.json", "AUTORESEARCH.md"],
            validation_commands=["spark-swarm specialization-path autoloop memory-doctor <repo> --plan-only"],
            promotion_gates=["schema_gate", "lineage_gate", "benchmark_gate", "complexity_gate", "rollback_gate"],
            rollback_plan="Clear the active memory-doctor specialization path and revert the path repo commit.",
        ),
        ArtifactManifest(
            artifact_id="memory-doctor-benchmark-pack-v1",
            artifact_type="benchmark_pack",
            repo="spark-intelligence-builder",
            inputs=["gateway-traces", "context-capsule-events", "memory-doctor-brain-snapshots"],
            outputs=["tests/test_gateway_ask_telegram.py", "tests/test_memory_doctor_benchmark.py"],
            validation_commands=[
                "python -m pytest tests/test_gateway_ask_telegram.py tests/test_memory_doctor_benchmark.py -k memory_doctor"
            ],
            promotion_gates=["schema_gate", "lineage_gate", "benchmark_gate", "memory_hygiene_gate", "risk_gate"],
            rollback_plan="Revert the benchmark-pack commit and keep the previous Memory Doctor runtime thresholds.",
        ),
        ArtifactManifest(
            artifact_id="memory-doctor-creator-report-v1",
            artifact_type="creator_report",
            repo="specialization-path-memory-doctor",
            inputs=[
                "memory-doctor-domain-chip-candidate-v1",
                "memory-doctor-specialization-path-v1",
                "memory-doctor-benchmark-pack-v1",
            ],
            outputs=[
                "reports/memory-doctor-creator-run-summary.json",
                "reports/memory-doctor-creator-run-summary.md",
                "reports/memory-doctor-validation-ledger.jsonl",
            ],
            validation_commands=["python -m json.tool reports/memory-doctor-creator-run-summary.json"],
            promotion_gates=["schema_gate", "lineage_gate", "transfer_gate", "rollback_gate"],
            rollback_plan="Delete the candidate creator report artifacts and preserve Builder observability events only.",
        ),
    ]
    validation_issues = [
        issue.to_text()
        for manifest in manifests
        for issue in validate_artifact_manifest(manifest)
    ]
    artifact_targets = [manifest.artifact_type for manifest in manifests]
    promotion_gates = sorted({gate for manifest in manifests for gate in manifest.promotion_gates})
    return {
        "schema_version": "spark-creator-intent.v1",
        "status": "aligned_candidate" if not validation_issues else "alignment_issues",
        "authority": "creator_alignment_non_authoritative",
        "intent_id": "creator-intent-memory-doctor-specialization-path",
        "target_domain": "memory-doctor",
        "target_operator_surface": "telegram",
        "artifact_targets": artifact_targets,
        "promotion_gates": promotion_gates,
        "usage_surfaces": ["telegram", "watchtower", "cli"],
        "claim_boundary": (
            "Memory Doctor is diagnosis and recommendation authority only; repair or specialization activation "
            "requires explicit creator trace evidence and operator approval."
        ),
        "validation_issues": validation_issues,
        "manifests": [manifest.to_dict() for manifest in manifests],
    }


def _build_senses(
    *,
    runtime: dict[str, object],
    wiki_packets: dict[str, object],
    dashboard_movement: dict[str, object],
    llm_wiki: dict[str, object],
    active_profile: dict[str, object],
    context_capsule: dict[str, object],
    movement_trace: dict[str, object],
    root_cause: dict[str, object],
    dashboard: dict[str, object],
    event_counts: dict[str, int],
    path_traces: list[dict[str, object]],
) -> list[dict[str, object]]:
    gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
    cross_scope = gateway_trace.get("cross_scope_lineage") if isinstance(gateway_trace.get("cross_scope_lineage"), dict) else {}
    source_counts = context_capsule.get("source_counts") if isinstance(context_capsule.get("source_counts"), dict) else {}
    diagnostic_invocations = (
        gateway_trace.get("diagnostic_invocations")
        if isinstance(gateway_trace.get("diagnostic_invocations"), list)
        else []
    )
    return [
        _sense(
            "builder_observability_events",
            bool(sum(int(value or 0) for value in event_counts.values())),
            "recent Builder event counts are available",
            {"event_counts": dict(event_counts)},
            10,
        ),
        _sense(
            "active_current_state_profile",
            active_profile.get("status") == "checked",
            f"active profile status={active_profile.get('status') or 'unknown'}",
            {"facts": active_profile.get("facts"), "current_state": active_profile.get("current_state")},
            15,
        ),
        _sense(
            "context_capsule_source_ledger",
            context_capsule.get("status") == "checked",
            f"context capsule status={context_capsule.get('status') or 'unknown'}",
            {"source_counts": dict(source_counts), "request_id": context_capsule.get("request_id")},
            15,
        ),
        _sense(
            "gateway_trace_lineage",
            isinstance(gateway_trace, dict) and gateway_trace.get("status") == "checked",
            f"gateway trace status={gateway_trace.get('status') if isinstance(gateway_trace, dict) else 'unknown'}",
            {"gateway_trace": gateway_trace},
            10,
        ),
        _sense(
            "cross_session_channel_lineage",
            isinstance(cross_scope, dict) and cross_scope.get("status") == "checked",
            f"cross-scope lineage status={cross_scope.get('status') if isinstance(cross_scope, dict) else 'unknown'}",
            {
                "identity_key": cross_scope.get("identity_key") if isinstance(cross_scope, dict) else None,
                "session_count": int(cross_scope.get("session_count") or 0) if isinstance(cross_scope, dict) else 0,
                "channel_count": int(cross_scope.get("channel_count") or 0) if isinstance(cross_scope, dict) else 0,
                "cross_session_visible": bool(cross_scope.get("cross_session_visible"))
                if isinstance(cross_scope, dict)
                else False,
                "cross_channel_visible": bool(cross_scope.get("cross_channel_visible"))
                if isinstance(cross_scope, dict)
                else False,
            },
            10,
        ),
        _sense(
            "telegram_doctor_intake_lineage",
            bool(diagnostic_invocations),
            f"doctor intake invocations={len(diagnostic_invocations)}",
            {"diagnostic_invocations": diagnostic_invocations[:3]},
            5,
        ),
        _sense(
            "watchtower_dashboard",
            bool(dashboard) and dashboard.get("status") != "unavailable",
            f"dashboard state={dashboard.get('top_level_state') or dashboard.get('status') or 'unknown'}",
            {"dashboard": dashboard},
            10,
        ),
        _sense(
            "doctor_movement_trace",
            movement_trace.get("status") == "checked",
            f"doctor movement status={movement_trace.get('status') or 'unknown'}",
            {
                "stage_count": len(movement_trace.get("stages") or []),
                "gap_count": len(movement_trace.get("gaps") or []),
                "unknowns": list(movement_trace.get("unknowns") or []),
            },
            10,
        ),
        _sense(
            "root_cause_classification",
            root_cause.get("status") in {"clear", "identified"},
            f"root cause status={root_cause.get('status') or 'unknown'}",
            {
                "status": root_cause.get("status"),
                "primary_gap": root_cause.get("primary_gap"),
                "failure_layer": root_cause.get("failure_layer"),
                "chain": list(root_cause.get("chain") or []),
                "confidence": root_cause.get("confidence"),
                "audit_handoff": dict(root_cause.get("audit_handoff") or {})
                if isinstance(root_cause.get("audit_handoff"), dict)
                else {},
            },
            5,
        ),
        _sense(
            "memory_sdk_runtime",
            bool(runtime.get("ready")),
            f"sdk runtime status={runtime.get('status') or 'unknown'}",
            {"client_kind": runtime.get("client_kind"), "reason": runtime.get("reason")},
            10,
        ),
        _sense(
            "dashboard_movement_export",
            dashboard_movement.get("status") == "supported",
            f"dashboard movement status={dashboard_movement.get('status') or 'unknown'}",
            {
                "row_count": int(dashboard_movement.get("row_count") or 0),
                "movement_counts": dict(dashboard_movement.get("movement_counts") or {}),
                "reason": dashboard_movement.get("reason"),
            },
            10,
        ),
        _sense(
            "llm_wiki_packet_metadata",
            wiki_packets.get("status") == "supported",
            f"wiki packet status={wiki_packets.get('status') or 'unknown'}",
            {
                "packet_count": int(wiki_packets.get("packet_count") or 0),
                "source_families_visible": bool(wiki_packets.get("source_families_visible")),
                "memory_kb": dict(wiki_packets.get("memory_kb") or {}),
                "reason": wiki_packets.get("reason"),
            },
            10,
        ),
        _sense(
            "llm_wiki_health",
            bool(llm_wiki.get("healthy")),
            f"LLM wiki status={llm_wiki.get('status') or 'unknown'}",
            {
                "wiki_record_count": int(llm_wiki.get("wiki_record_count") or 0),
                "stale_page_count": int(llm_wiki.get("stale_page_count") or 0),
                "warnings": list(llm_wiki.get("warnings") or []),
                "reason": llm_wiki.get("reason"),
            },
            10,
        ),
        _sense(
            "incident_path_replay",
            bool(path_traces),
            f"path traces={len(path_traces)}",
            {"path_trace_count": len(path_traces), "path_traces": path_traces[:3]},
            5,
        ),
    ]


def _sense(name: str, present: bool, detail: str, evidence: dict[str, object], weight: int) -> dict[str, object]:
    return {
        "name": name,
        "present": bool(present),
        "detail": detail,
        "weight": weight,
        "evidence": evidence,
    }


def _score_coverage(senses: list[dict[str, object]]) -> dict[str, object]:
    total = sum(int(sense.get("weight") or 0) for sense in senses)
    present = sum(int(sense.get("weight") or 0) for sense in senses if sense.get("present"))
    score = int(round((present / total) * 100)) if total else 0
    return {
        "score": score,
        "present_weight": present,
        "total_weight": total,
        "present": [sense["name"] for sense in senses if sense.get("present")],
        "missing": [sense["name"] for sense in senses if not sense.get("present")],
    }


def _build_gaps(
    *,
    senses: list[dict[str, object]],
    coverage: dict[str, object],
    findings: list[Any],
    active_profile: dict[str, object],
    topic_scan: dict[str, object],
    context_capsule: dict[str, object],
    movement_trace: dict[str, object],
    root_cause: dict[str, object],
    wiki_packets: dict[str, object],
    dashboard_movement: dict[str, object],
    llm_wiki: dict[str, object],
    event_counts: dict[str, int],
    path_traces: list[dict[str, object]],
) -> list[dict[str, object]]:
    gaps: list[dict[str, object]] = []
    missing = set(str(name) for name in coverage.get("missing") or [])
    if int(coverage.get("score") or 0) < 70:
        gaps.append(
            _gap(
                "memory_observability_coverage_low",
                "high",
                f"Memory Doctor can see only {coverage.get('score')}/100 weighted trace coverage.",
                "coverage_score",
                str(coverage.get("score")),
                "run a complete Telegram memory probe, then rerun Memory Doctor with human id and topic",
            )
        )
    if any(not bool(getattr(finding, "ok", True)) for finding in findings):
        gaps.append(
            _gap(
                "active_memory_incident_detected",
                "high",
                "One or more Memory Doctor findings are failing.",
                "failing_findings",
                str(sum(1 for finding in findings if not bool(getattr(finding, "ok", True)))),
                "fix the highest-severity finding, rerun the same doctor command, then replay the triggering Telegram turn",
            )
        )
    if "active_current_state_profile" in missing and topic_scan.get("topic"):
        gaps.append(
            _gap(
                "topic_without_active_profile_scope",
                "medium",
                "A topic was requested, but active current-state profile inspection was unavailable.",
                "active_profile_status",
                str(active_profile.get("status") or "unknown"),
                "rerun from Telegram or pass --human-id so active current state can be checked",
            )
        )
    if "context_capsule_source_ledger" in missing:
        gaps.append(
            _gap(
                "context_capsule_visibility_gap",
                "high",
                "No provider context capsule source ledger was available for the latest memory-path audit.",
                "context_capsule_status",
                str(context_capsule.get("status") or "unknown"),
                "send one Telegram probe that triggers provider context, then rerun Memory Doctor",
            )
        )
    if "gateway_trace_lineage" in missing:
        gaps.append(
            _gap(
                "gateway_trace_visibility_gap",
                "medium",
                "Gateway transcript lineage is not fully correlated with the provider capsule.",
                "gateway_trace_status",
                str(((context_capsule.get("gateway_trace") or {}) if isinstance(context_capsule.get("gateway_trace"), dict) else {}).get("status") or "unknown"),
                "include stable request/session ids across gateway, Builder events, context capsule, and provider dispatch",
            )
        )
    if "cross_session_channel_lineage" in missing:
        gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
        cross_scope = gateway_trace.get("cross_scope_lineage") if isinstance(gateway_trace.get("cross_scope_lineage"), dict) else {}
        gaps.append(
            _gap(
                "cross_session_channel_lineage_gap",
                "medium",
                "Memory Doctor cannot map memory/context movement across sessions or channels for a stable identity.",
                "cross_scope_status",
                str(cross_scope.get("status") or "unknown"),
                "ensure gateway traces carry a stable human/user id across sessions and adapters",
            )
        )
    if "telegram_doctor_intake_lineage" in missing:
        gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
        gaps.append(
            _gap(
                "telegram_doctor_intake_lineage_gap",
                "medium",
                "Memory Doctor cannot see the Telegram command or contextual complaint that invoked this diagnosis.",
                "diagnostic_invocation_count",
                str(gateway_trace.get("diagnostic_invocation_count") or 0),
                "rerun Memory Doctor directly from Telegram against the confusing turn so intake metadata is traceable",
            )
        )
    for movement_gap in movement_trace.get("gaps") or []:
        if not isinstance(movement_gap, dict):
            continue
        gaps.append(
            _gap(
                str(movement_gap.get("name") or "movement_trace_gap"),
                str(movement_gap.get("severity") or "medium"),
                str(movement_gap.get("detail") or "Memory movement trace reported a gap."),
                "movement_trace_gap",
                str(movement_gap.get("request_id") or ""),
                "repair the missing stage and replay the same request id",
            )
        )
    if root_cause.get("status") == "identified":
        audit_handoff = root_cause.get("audit_handoff") if isinstance(root_cause.get("audit_handoff"), dict) else {}
        gaps.append(
            _gap(
                "root_cause_" + str(root_cause.get("primary_gap") or "unknown"),
                "medium",
                "Memory Doctor classified the likely failing memory/context layer.",
                "root_cause_failure_layer",
                str(root_cause.get("failure_layer") or "unknown"),
                "aggregate this root-cause layer across incidents and prioritize the most repeated path repair",
            )
        )
        if audit_handoff.get("status") != "ready":
            gaps.append(
                _gap(
                    "root_cause_audit_handoff_gap",
                    "medium",
                    "Memory Doctor identified a failure layer without a targeted audit handoff.",
                    "root_cause_audit_handoff_status",
                    str(audit_handoff.get("status") or "missing"),
                    "attach audit focus, falsification questions, sample strategy, and stop gate to the root cause",
                )
            )
    if wiki_packets.get("status") != "supported":
        gaps.append(
            _gap(
                "llm_wiki_packet_visibility_gap",
                "medium",
                "LLM wiki packet metadata is not supported or has no packets.",
                "wiki_packet_status",
                str(wiki_packets.get("status") or "unknown"),
                "run wiki compile-system or point spark.memory.wiki_packet_paths at the governed memory KB",
            )
        )
    if dashboard_movement.get("status") != "supported":
        gaps.append(
            _gap(
                "dashboard_movement_export_gap",
                "medium",
                "The domain memory dashboard movement export is not visible to the doctor brain.",
                "dashboard_movement_status",
                str(dashboard_movement.get("status") or "unknown"),
                "run a memory SDK write/read/export probe and verify SparkMemoryDashboardMovementExport is populated",
            )
        )
    if llm_wiki.get("status") in {"degraded", "error"} or llm_wiki.get("warnings"):
        gaps.append(
            _gap(
                "llm_wiki_health_gap",
                "medium",
                "LLM wiki health has warnings, stale pages, or retrieval degradation.",
                "llm_wiki_status",
                str(llm_wiki.get("status") or "unknown"),
                "run spark-intelligence wiki status --refresh, then scan candidate notes before trusting wiki support",
            )
        )
    if not path_traces and sum(int(value or 0) for value in event_counts.values()) > 0:
        gaps.append(
            _gap(
                "incident_path_replay_sparse",
                "low",
                "Memory events exist, but no request-scoped path trace was assembled for this doctor run.",
                "path_trace_count",
                "0",
                "rerun Memory Doctor with the exact topic or request id from the confusing turn",
            )
        )
    return _dedupe_gaps(gaps)


def _gap(
    name: str,
    severity: str,
    detail: str,
    metric: str,
    observed: str,
    next_probe: str,
) -> dict[str, object]:
    return {
        "name": name,
        "severity": severity,
        "detail": detail,
        "metric": metric,
        "observed": observed,
        "next_probe": next_probe,
    }


def _dedupe_gaps(gaps: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for gap in gaps:
        name = str(gap.get("name") or "")
        if name in seen:
            continue
        seen.add(name)
        deduped.append(gap)
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(deduped, key=lambda item: (severity_rank.get(str(item.get("severity") or "low"), 3), str(item.get("name") or "")))


def _build_proactive_improvements(gaps: list[dict[str, object]]) -> list[dict[str, object]]:
    improvements: list[dict[str, object]] = []
    for gap in gaps:
        name = str(gap.get("name") or "")
        improvements.append(
            {
                "name": f"improve_{name}",
                "priority": str(gap.get("severity") or "medium"),
                "metric": str(gap.get("metric") or name),
                "evidence": str(gap.get("observed") or ""),
                "action": _improvement_action(name),
                "next_probe": str(gap.get("next_probe") or ""),
            }
        )
    return improvements


def _improvement_action(gap_name: str) -> str:
    if gap_name in {
        "context_capsule_visibility_gap",
        "gateway_trace_visibility_gap",
        "gateway_to_context_capsule_gap",
        "cross_session_channel_lineage_gap",
    }:
        return "Strengthen close-turn context tracing so Telegram turns, Builder events, provider capsules, and memory reads share correlated lineage."
    if gap_name == "memory_observability_coverage_low":
        return "Raise doctor visibility by wiring missing senses before changing memory behavior."
    if gap_name == "llm_wiki_packet_visibility_gap":
        return "Compile or connect governed LLM wiki packets so the doctor can compare live traces with documented memory architecture."
    if gap_name == "llm_wiki_health_gap":
        return "Refresh and scan the LLM wiki so stale support notes cannot mislead runtime diagnosis."
    if gap_name == "dashboard_movement_export_gap":
        return "Populate the dashboard movement export during memory SDK reads, writes, deletes, and snapshot exports."
    if gap_name == "topic_without_active_profile_scope":
        return "Require active current-state inspection for topic-specific identity, owner, preference, and correction audits."
    if gap_name == "active_memory_incident_detected":
        return "Treat the failing doctor finding as the repair target, then replay the triggering turn as a regression."
    if gap_name.startswith("root_cause_"):
        if gap_name == "root_cause_audit_handoff_gap":
            return "Make each root-cause diagnosis actionable by handing it to a targeted audit with a replay gate."
        return "Use repeated root-cause layers to prioritize the memory/context path that most often breaks recall."
    if gap_name == "incident_path_replay_sparse":
        return "Attach topic and request ids to doctor path traces so failures become replayable, not just visible."
    return "Add a focused probe for this gap and require it to pass before promoting the memory-path change."


def _brain_summary(
    *,
    coverage: dict[str, object],
    gaps: list[dict[str, object]],
    improvements: list[dict[str, object]],
) -> dict[str, object]:
    first_improvement = improvements[0] if improvements else {}
    return {
        "coverage_score": int(coverage.get("score") or 0),
        "gap_count": len(gaps),
        "highest_severity": str(gaps[0].get("severity") or "none") if gaps else "none",
        "next_action": first_improvement.get("action"),
        "next_probe": first_improvement.get("next_probe"),
    }
