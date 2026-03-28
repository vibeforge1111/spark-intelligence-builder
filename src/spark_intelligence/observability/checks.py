from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import (
    events_for_run,
    latest_events_by_type,
    latest_snapshots_by_surface,
    open_runs,
    recent_delivery_records,
    recent_memory_lane_records,
    recent_config_mutations,
    recent_contradictions,
    recent_reset_sensitive_state_registry,
    recent_provenance_mutations,
    recent_quarantine_records,
    record_contradiction,
    resolve_contradiction,
)
from spark_intelligence.state.db import StateDB


ALLOWED_AUTOSTART_PLATFORMS = {
    None,
    "",
    "windows_task_scheduler",
    "windows_startup_folder",
    "systemd_user_unit",
    "launchagent",
}

NON_PROMOTABLE_KEEPABILITY = {
    "ephemeral_context",
    "user_preference_ephemeral",
    "operator_debug_only",
}
NON_PROMOTABLE_DISPOSITIONS = {
    "not_promotable",
    "quarantined",
    "quarantined_blocked",
}


def _expected_artifact_lane(keepability: str) -> str:
    if keepability == "operator_debug_only":
        return "ops_transcripts"
    if keepability == "user_preference_ephemeral":
        return "user_history"
    return "execution_evidence"


@dataclass(frozen=True)
class StopShipIssue:
    name: str
    ok: bool
    detail: str
    severity: str


def evaluate_stop_ship_issues(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    emit_contradictions: bool = False,
) -> list[StopShipIssue]:
    issues = [
        _config_audit_issue(state_db),
        _intent_execution_issue(state_db),
        _delivery_truth_issue(state_db),
        _background_closure_issue(state_db),
        _runtime_state_authority_issue(state_db),
        _reset_integrity_issue(state_db),
        _plugin_provenance_issue(config_manager=config_manager, state_db=state_db),
        _provenance_ledger_issue(state_db),
        _unlabeled_provenance_quarantine_issue(state_db),
        _secret_boundary_issue(state_db),
        _keepability_issue(state_db),
        _bridge_residue_persistence_issue(state_db),
        _environment_parity_issue(state_db),
        _daemon_reentry_issue(config_manager=config_manager),
        _external_execution_governance_issue(),
        _bridge_output_governance_issue(),
    ]
    if emit_contradictions:
        _reconcile_stop_ship_contradictions(state_db=state_db, issues=issues)
    return issues


def _reconcile_stop_ship_contradictions(*, state_db: StateDB, issues: list[StopShipIssue]) -> None:
    open_keys = {
        str(row.get("contradiction_key") or "")
        for row in recent_contradictions(state_db, limit=500, status="open")
        if str(row.get("contradiction_key") or "").startswith("stop_ship:")
    }
    for issue in issues:
        contradiction_key = f"stop_ship:{issue.name}"
        if issue.ok:
            if contradiction_key in open_keys:
                resolve_contradiction(
                    state_db,
                    contradiction_key=contradiction_key,
                    component="stop_ship_checks",
                    reason_code=issue.name,
                    summary=f"Stop-ship contradiction resolved: {issue.name}.",
                    detail=issue.detail,
                    facts={"detail": issue.detail, "issue_name": issue.name},
                    provenance={"source_kind": "stop_ship_registry"},
                )
            continue
        record_contradiction(
            state_db,
            contradiction_key=contradiction_key,
            component="stop_ship_checks",
            reason_code=issue.name,
            summary=f"Stop-ship contradiction: {issue.name}.",
            detail=issue.detail,
            severity=issue.severity,
            facts={"detail": issue.detail, "issue_name": issue.name},
            provenance={"source_kind": "stop_ship_registry"},
        )


def _config_audit_issue(state_db: StateDB) -> StopShipIssue:
    rows = recent_config_mutations(state_db, limit=100)
    bad = [
        row
        for row in rows
        if row.get("status") == "applied"
        and (not row.get("actor_id") or not row.get("reason_code") or not row.get("rollback_ref"))
    ]
    if bad:
        return StopShipIssue(
            name="stop_ship_config_mutation_audit",
            ok=False,
            detail=f"{len(bad)} config mutation(s) are missing actor, reason, or rollback metadata.",
            severity="critical",
        )
    return StopShipIssue(
        name="stop_ship_config_mutation_audit",
        ok=True,
        detail="Applied config mutations carry audit metadata.",
        severity="critical",
    )


def _intent_execution_issue(state_db: StateDB) -> StopShipIssue:
    intents = latest_events_by_type(state_db, event_type="intent_committed", limit=200)
    incomplete: list[str] = []
    for intent in intents:
        run_id = intent.get("run_id")
        if not run_id:
            incomplete.append(str(intent.get("event_id")))
            continue
        events = {event.get("event_type") for event in events_for_run(state_db, run_id=str(run_id))}
        if "tool_result_received" not in events and "dispatch_failed" not in events:
            incomplete.append(str(run_id))
    if incomplete:
        return StopShipIssue(
            name="stop_ship_intent_without_proof",
            ok=False,
            detail=f"{len(incomplete)} run(s) committed intent without dispatch/result proof.",
            severity="critical",
        )
    return StopShipIssue(
        name="stop_ship_intent_without_proof",
        ok=True,
        detail="Intent packets have matching dispatch or result proof.",
        severity="critical",
    )


def _background_closure_issue(state_db: StateDB) -> StopShipIssue:
    stalled = [
        row
        for row in open_runs(state_db)
        if str(row.get("run_kind") or "").startswith("job:") or str(row.get("origin_surface") or "") == "jobs_tick"
    ]
    if stalled:
        return StopShipIssue(
            name="stop_ship_background_closure",
            ok=False,
            detail=f"{len(stalled)} background/job run(s) remain open without closure.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_background_closure",
        ok=True,
        detail="Background and job runs have closure records.",
        severity="high",
    )


def _delivery_truth_issue(state_db: StateDB) -> StopShipIssue:
    attempts = latest_events_by_type(state_db, event_type="delivery_attempted", limit=200)
    successes = latest_events_by_type(state_db, event_type="delivery_succeeded", limit=200)
    failures = latest_events_by_type(state_db, event_type="delivery_failed", limit=200)
    if not attempts and not successes and not failures:
        return StopShipIssue(
            name="stop_ship_delivery_truth",
            ok=True,
            detail="No delivery lineage has executed yet.",
            severity="high",
        )
    registry_rows = recent_delivery_records(state_db, limit=400)
    if not registry_rows:
        return StopShipIssue(
            name="stop_ship_delivery_truth",
            ok=False,
            detail="Delivery events exist without typed delivery registry rows.",
            severity="high",
        )
    terminal_registry = [row for row in registry_rows if str(row.get("status") or "") in {"succeeded", "failed"}]
    terminal_events = len(successes) + len(failures)
    if terminal_events > len(terminal_registry):
        return StopShipIssue(
            name="stop_ship_delivery_truth",
            ok=False,
            detail="Terminal delivery events exceed typed delivery registry rows.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_delivery_truth",
        ok=True,
        detail="Delivery attempts and acknowledgments are mirrored into typed delivery registry rows.",
        severity="high",
    )


def _plugin_provenance_issue(*, config_manager: ConfigManager, state_db: StateDB) -> StopShipIssue:
    active_chip_keys = config_manager.get_path("spark.chips.active_keys", default=[]) or []
    active_path_key = config_manager.get_path("spark.specialization_paths.active_path_key", default=None)
    personality_enabled = bool(config_manager.get_path("spark.personality.enabled", default=True))
    provenance_events = latest_events_by_type(state_db, event_type="plugin_or_chip_influence_recorded", limit=200)
    chip_or_path_events = [
        event
        for event in provenance_events
        if str((event.get("provenance_json") or {}).get("source_kind") or "")
        in {"chip_hook", "attachment_snapshot"}
    ]
    personality_events = [
        event
        for event in provenance_events
        if str((event.get("provenance_json") or {}).get("source_kind") or "").startswith("personality_")
    ]
    bridge_activity = _typed_events(
        state_db,
        event_types=("dispatch_started", "tool_result_received"),
        component="researcher_bridge",
        limit=200,
    )
    if active_chip_keys or active_path_key:
        if not chip_or_path_events:
            return StopShipIssue(
                name="stop_ship_plugin_provenance",
                ok=False,
                detail="Active chip or specialization-path influence exists without provenance events.",
                severity="critical",
            )
    if personality_enabled and bridge_activity and not personality_events:
        return StopShipIssue(
            name="stop_ship_plugin_provenance",
            ok=False,
            detail="Personality influence shaped bridge execution without typed provenance.",
            severity="critical",
        )
    if not active_chip_keys and not active_path_key and not bridge_activity:
        return StopShipIssue(
            name="stop_ship_plugin_provenance",
            ok=True,
            detail="No active bridge influence requiring provenance has executed yet.",
            severity="critical",
        )
    return StopShipIssue(
        name="stop_ship_plugin_provenance",
        ok=True,
        detail="Chip, path, and personality influence is recorded with provenance.",
        severity="critical",
    )


def _provenance_ledger_issue(state_db: StateDB) -> StopShipIssue:
    provenance_events = latest_events_by_type(state_db, event_type="plugin_or_chip_influence_recorded", limit=200)
    if not provenance_events:
        return StopShipIssue(
            name="stop_ship_provenance_ledger",
            ok=True,
            detail="No provenance-bearing influence events have executed yet.",
            severity="high",
        )
    mutations = recent_provenance_mutations(state_db, limit=200)
    if not mutations:
        return StopShipIssue(
            name="stop_ship_provenance_ledger",
            ok=False,
            detail="Provenance-bearing influence events exist without typed provenance mutation rows.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_provenance_ledger",
        ok=True,
        detail="Provenance-bearing influence events are mirrored into typed provenance mutation rows.",
        severity="high",
    )


def _unlabeled_provenance_quarantine_issue(state_db: StateDB) -> StopShipIssue:
    mutations = recent_provenance_mutations(state_db, limit=200)
    unlabeled = [
        row
        for row in mutations
        if str(row.get("source_kind") or "") == "unknown" or str(row.get("source_id") or "") == "unknown"
    ]
    if not unlabeled:
        return StopShipIssue(
            name="stop_ship_unlabeled_provenance_quarantine",
            ok=True,
            detail="No unlabeled provenance mutations were recorded.",
            severity="high",
        )
    missing_quarantine = [row for row in unlabeled if not bool(row.get("quarantined"))]
    if missing_quarantine:
        return StopShipIssue(
            name="stop_ship_unlabeled_provenance_quarantine",
            ok=False,
            detail="Unlabeled provenance mutations were recorded without quarantine.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_unlabeled_provenance_quarantine",
        ok=True,
        detail="Unlabeled provenance mutations are quarantined automatically.",
        severity="high",
    )


def _runtime_state_authority_issue(state_db: StateDB) -> StopShipIssue:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT state_key
            FROM runtime_state
            WHERE
                state_key LIKE 'researcher:%'
                OR state_key LIKE 'swarm:%'
                OR state_key LIKE 'attachments:%'
                OR state_key LIKE 'personality:%'
                OR state_key IN ('telegram:auth_state', 'telegram:poll_state')
            ORDER BY state_key
            """
        ).fetchall()
    if not rows:
        return StopShipIssue(
            name="stop_ship_runtime_state_authority",
            ok=True,
            detail="No critical hidden runtime_state authority keys are active.",
            severity="high",
        )
    missing_domains: list[str] = []
    state_keys = [str(row["state_key"]) for row in rows]
    if any(key.startswith("researcher:") for key in state_keys) and not _typed_events(
        state_db,
        event_types=("dispatch_started", "dispatch_failed", "tool_result_received", "runtime_environment_snapshot"),
        component="researcher_bridge",
        limit=200,
    ):
        missing_domains.append("researcher")
    swarm_events = [
        event
        for event in _typed_events(
            state_db,
            event_types=("dispatch_started", "dispatch_failed", "tool_result_received", "runtime_environment_snapshot"),
            component="swarm_bridge",
            limit=200,
        )
        if str(((event.get("facts_json") or {}).get("swarm_operation") or "")) in {"sync", "decision", "auth_refresh"}
        or str(event.get("event_type") or "") == "runtime_environment_snapshot"
    ]
    if any(key.startswith("swarm:") for key in state_keys) and not swarm_events:
        missing_domains.append("swarm")
    if any(key.startswith("attachments:") for key in state_keys):
        with state_db.connect() as conn:
            attachment_row = conn.execute(
                "SELECT snapshot_id FROM attachment_state_snapshots ORDER BY generated_at DESC, created_at DESC LIMIT 1"
            ).fetchone()
        if not attachment_row:
            missing_domains.append("attachments")
    if any(key.startswith("personality:") for key in state_keys):
        with state_db.connect() as conn:
            personality_row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM personality_trait_profiles) AS trait_profile_count,
                    (SELECT COUNT(*) FROM personality_observations) AS observation_count,
                    (SELECT COUNT(*) FROM personality_evolution_events) AS evolution_count
                """
            ).fetchone()
        if not personality_row or (
            int(personality_row["trait_profile_count"]) == 0
            and int(personality_row["observation_count"]) == 0
            and int(personality_row["evolution_count"]) == 0
        ):
            missing_domains.append("personality")
    telegram_events = _typed_events(
        state_db,
        event_types=("intent_committed", "delivery_attempted", "delivery_succeeded", "delivery_failed"),
        component="telegram_runtime",
        limit=200,
    )
    if any(key.startswith("telegram:") for key in state_keys) and not telegram_events:
        missing_domains.append("telegram")
    if missing_domains:
        return StopShipIssue(
            name="stop_ship_runtime_state_authority",
            ok=False,
            detail=(
                "Critical runtime_state keys exist without typed domain mirrors: "
                + ", ".join(sorted(set(missing_domains)))
            ),
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_runtime_state_authority",
        ok=True,
        detail="Critical runtime_state keys have typed domain mirrors available.",
        severity="high",
    )


def _reset_integrity_issue(state_db: StateDB) -> StopShipIssue:
    reset_events = latest_events_by_type(state_db, event_type="session_reset_performed", limit=50)
    if not reset_events:
        return StopShipIssue(
            name="stop_ship_reset_integrity",
            ok=True,
            detail="No registered reset operations have executed yet.",
            severity="high",
        )
    active_rows = recent_reset_sensitive_state_registry(state_db, limit=500, active_only=True)
    leaked_scopes: list[str] = []
    for event in reset_events:
        facts = event.get("facts_json") or {}
        if not isinstance(facts, dict):
            continue
        scope_kind = str(facts.get("scope_kind") or "")
        scope_ref = str(facts.get("scope_ref") or "")
        if not scope_kind or not scope_ref:
            continue
        leaking = [
            row
            for row in active_rows
            if str(row.get("scope_kind") or "") == scope_kind and str(row.get("scope_ref") or "") == scope_ref
        ]
        if leaking:
            leaked_scopes.append(f"{scope_kind}:{scope_ref}")
    if leaked_scopes:
        return StopShipIssue(
            name="stop_ship_reset_integrity",
            ok=False,
            detail="Reset-sensitive state remained active after reset for " + ", ".join(sorted(set(leaked_scopes))),
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_reset_integrity",
        ok=True,
        detail="Registered reset-sensitive state is cleared when reset events execute.",
        severity="high",
    )


def _secret_boundary_issue(state_db: StateDB) -> StopShipIssue:
    violations = latest_events_by_type(state_db, event_type="secret_boundary_violation", limit=100)
    quarantines = recent_quarantine_records(state_db, limit=100)
    quarantine_event_ids = {row.get("event_id") for row in quarantines if row.get("event_id")}
    unresolved = [event for event in violations if event.get("event_id") not in quarantine_event_ids]
    if unresolved:
        return StopShipIssue(
            name="stop_ship_secret_boundary",
            ok=False,
            detail=f"{len(unresolved)} secret-boundary violation(s) lack quarantine records.",
            severity="critical",
        )
    return StopShipIssue(
        name="stop_ship_secret_boundary",
        ok=True,
        detail="Secret-boundary violations are quarantined before promotion or delivery.",
        severity="critical",
    )


def _keepability_issue(state_db: StateDB) -> StopShipIssue:
    provenance_events = latest_events_by_type(state_db, event_type="plugin_or_chip_influence_recorded", limit=100)
    missing = []
    for event in provenance_events:
        facts = event.get("facts_json") or {}
        if not isinstance(facts, dict) or not facts.get("keepability"):
            missing.append(event)
    bridge_output_events = _typed_events(
        state_db,
        event_types=("tool_result_received", "dispatch_failed"),
        component="researcher_bridge",
        limit=200,
    )
    bridge_delivery_events = [
        event
        for event in _typed_events(
            state_db,
            event_types=("delivery_attempted", "delivery_succeeded", "delivery_failed"),
            component="telegram_runtime",
            limit=200,
        )
        if str((event.get("facts_json") or {}).get("event") or "") == "telegram_bridge_outbound"
    ]
    webhook_delivery_events: list[dict[str, Any]] = []
    for component in ("discord_webhook", "whatsapp_webhook"):
        webhook_delivery_events.extend(
            [
                event
                for event in _typed_events(
                    state_db,
                    event_types=("delivery_attempted", "delivery_succeeded", "delivery_failed"),
                    component=component,
                    limit=200,
                )
                if str((event.get("facts_json") or {}).get("bridge_mode") or "")
            ]
        )
    classified_events = bridge_output_events + bridge_delivery_events + webhook_delivery_events
    for event in classified_events:
        facts = event.get("facts_json") or {}
        if not facts.get("keepability") or not facts.get("promotion_disposition"):
            missing.append(event)
    invalid_promotions = []
    for event in classified_events:
        facts = event.get("facts_json") or {}
        keepability = str(facts.get("keepability") or "")
        promotion_disposition = str(facts.get("promotion_disposition") or "")
        if keepability in NON_PROMOTABLE_KEEPABILITY and promotion_disposition not in NON_PROMOTABLE_DISPOSITIONS:
            invalid_promotions.append(event)
    lane_records = recent_memory_lane_records(state_db, limit=400)
    lane_records_by_event = {
        str(record.get("event_id")): record
        for record in lane_records
        if str(record.get("event_id") or "")
    }
    missing_lane_records = [
        event for event in classified_events if str(event.get("event_id") or "") not in lane_records_by_event
    ]
    invalid_lane_records = []
    for event in classified_events:
        event_id = str(event.get("event_id") or "")
        lane_record = lane_records_by_event.get(event_id)
        if not lane_record:
            continue
        facts = event.get("facts_json") or {}
        keepability = str(facts.get("keepability") or "")
        artifact_lane = str(lane_record.get("artifact_lane") or "")
        if artifact_lane != _expected_artifact_lane(keepability):
            invalid_lane_records.append(lane_record)
    if missing:
        return StopShipIssue(
            name="stop_ship_keepability_rules",
            ok=False,
            detail=(
                f"{len(missing)} influence or bridge output event(s) are missing "
                "keepability or promotion classification."
            ),
            severity="high",
        )
    if invalid_promotions:
        return StopShipIssue(
            name="stop_ship_keepability_rules",
            ok=False,
            detail=(
                f"{len(invalid_promotions)} bridge output event(s) mark ephemeral or debug material "
                "as promotion-eligible."
            ),
            severity="high",
        )
    if missing_lane_records:
        return StopShipIssue(
            name="stop_ship_keepability_rules",
            ok=False,
            detail=(
                f"{len(missing_lane_records)} classified influence or bridge output event(s) "
                "lack typed memory-lane records."
            ),
            severity="high",
        )
    if invalid_lane_records:
        return StopShipIssue(
            name="stop_ship_keepability_rules",
            ok=False,
            detail=(
                f"{len(invalid_lane_records)} classified artifact(s) were stored in the wrong memory lane."
            ),
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_keepability_rules",
        ok=True,
        detail=(
            "Operational influence and bridge outputs include non-promotable keepability "
            "classification and typed memory-lane labels."
        ),
        severity="high",
    )


def _typed_events(
    state_db: StateDB,
    *,
    event_types: tuple[str, ...],
    component: str,
    limit: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event_type in event_types:
        events.extend(
            [
                event
                for event in latest_events_by_type(state_db, event_type=event_type, limit=limit)
                if str(event.get("component") or "") == component
            ]
        )
    return events


def _environment_parity_issue(state_db: StateDB) -> StopShipIssue:
    snapshots = latest_snapshots_by_surface(state_db)
    if len(snapshots) < 2:
        return StopShipIssue(
            name="stop_ship_environment_parity",
            ok=True,
            detail="Not enough runtime surfaces have emitted environment snapshots yet.",
            severity="high",
        )
    disagreement: list[str] = []
    comparable_fields = (
        "provider_id",
        "provider_model",
        "provider_base_url",
        "provider_execution_transport",
        "runtime_root",
        "config_path",
    )
    rows = list(snapshots.items())
    baseline_surface, baseline = rows[0]
    for surface, snapshot in rows[1:]:
        for field in comparable_fields:
            left = baseline.get(field)
            right = snapshot.get(field)
            if left and right and left != right:
                disagreement.append(f"{baseline_surface}:{field}={left} != {surface}:{field}={right}")
    if disagreement:
        return StopShipIssue(
            name="stop_ship_environment_parity",
            ok=False,
            detail="; ".join(disagreement[:3]),
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_environment_parity",
        ok=True,
        detail="Runtime surfaces agree on provider and config lineage.",
        severity="high",
    )


def _bridge_residue_persistence_issue(state_db: StateDB) -> StopShipIssue:
    with state_db.connect() as conn:
        suspicious_rows = conn.execute(
            """
            SELECT state_key
            FROM runtime_state
            WHERE state_key LIKE 'researcher:%reply%'
               OR state_key LIKE 'researcher:%response%'
            ORDER BY state_key
            """
        ).fetchall()
        failure_row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = 'researcher:last_failure' LIMIT 1"
        ).fetchone()
    if suspicious_rows:
        return StopShipIssue(
            name="stop_ship_bridge_residue_persistence",
            ok=False,
            detail=(
                "Researcher bridge runtime_state contains reply-like persistence keys: "
                + ", ".join(str(row["state_key"]) for row in suspicious_rows[:4])
            ),
            severity="high",
        )
    if not failure_row or not failure_row["value"]:
        return StopShipIssue(
            name="stop_ship_bridge_residue_persistence",
            ok=True,
            detail="No durable bridge failure payload is present yet.",
            severity="high",
        )
    try:
        payload = json.loads(str(failure_row["value"]))
    except json.JSONDecodeError:
        return StopShipIssue(
            name="stop_ship_bridge_residue_persistence",
            ok=False,
            detail="Researcher bridge failure payload is not valid JSON.",
            severity="high",
        )
    message = str(payload.get("message") or "")
    suspicious_tokens = (
        "[spark researcher",
        "trace:",
        "packet_refs",
        "memory_refs",
        "selected_packet_ids",
        "quarantine_id",
    )
    if any(token in message.lower() for token in suspicious_tokens):
        return StopShipIssue(
            name="stop_ship_bridge_residue_persistence",
            ok=False,
            detail="Researcher bridge failure payload persists raw reply/debug residue.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_bridge_residue_persistence",
        ok=True,
        detail="Bridge failure persistence is sanitized for operator-status use only.",
        severity="high",
    )


def _daemon_reentry_issue(*, config_manager: ConfigManager) -> StopShipIssue:
    enabled = bool(config_manager.get_path("runtime.autostart.enabled", default=False))
    platform = config_manager.get_path("runtime.autostart.platform", default=None)
    command = str(config_manager.get_path("runtime.autostart.command", default="") or "")
    if enabled and platform not in ALLOWED_AUTOSTART_PLATFORMS:
        return StopShipIssue(
            name="stop_ship_daemon_reentry",
            ok=False,
            detail=f"Unsupported autostart platform configured: {platform}",
            severity="high",
        )
    lowered = command.lower()
    if any(token in lowered for token in ("watchdog", " while ", "restart-loop", "restart_service")):
        return StopShipIssue(
            name="stop_ship_daemon_reentry",
            ok=False,
            detail="Autostart command appears to include daemon-like recovery behavior.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_daemon_reentry",
        ok=True,
        detail="Autostart remains on the native wrapper posture.",
        severity="high",
    )


def _external_execution_governance_issue() -> StopShipIssue:
    allowed_subprocess_paths = {
        "src/spark_intelligence/cli.py",
        "src/spark_intelligence/config/loader.py",
        "src/spark_intelligence/execution/governed.py",
    }
    allowed_direct_provider_paths = {
        "src/spark_intelligence/llm/direct_provider.py",
        "src/spark_intelligence/llm/provider_wrapper.py",
        "src/spark_intelligence/researcher_bridge/advisory.py",
    }
    unexpected_subprocess = _find_source_pattern_paths("subprocess.run(", allowed_paths=allowed_subprocess_paths)
    unexpected_provider = _find_source_pattern_paths(
        "execute_direct_provider_prompt(",
        allowed_paths=allowed_direct_provider_paths,
    )
    offenders = sorted(set(unexpected_subprocess + unexpected_provider))
    if offenders:
        return StopShipIssue(
            name="stop_ship_external_execution_governance",
            ok=False,
            detail="Ungoverned external execution entry points detected: " + ", ".join(offenders[:4]),
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_external_execution_governance",
        ok=True,
        detail="External execution is limited to governed helper and approved wrapper modules.",
        severity="high",
    )


def _bridge_output_governance_issue() -> StopShipIssue:
    allowed_reply_paths = {
        "src/spark_intelligence/researcher_bridge/advisory.py",
        "src/spark_intelligence/adapters/telegram/runtime.py",
        "src/spark_intelligence/gateway/simulated_dm.py",
    }
    unexpected_reply_consumers = _find_source_pattern_paths(
        "reply_text_attribute",
        allowed_paths=allowed_reply_paths,
    )
    if unexpected_reply_consumers:
        return StopShipIssue(
            name="stop_ship_bridge_output_governance",
            ok=False,
            detail=(
                "Raw researcher bridge reply consumption appears outside immediate delivery surfaces: "
                + ", ".join(unexpected_reply_consumers[:4])
            ),
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_bridge_output_governance",
        ok=True,
        detail="Raw bridge replies are limited to immediate delivery surfaces.",
        severity="high",
    )


def _find_source_pattern_paths(pattern: str, *, allowed_paths: set[str]) -> list[str]:
    repo_root = Path(__file__).resolve().parents[3]
    src_root = repo_root / "src"
    matches: list[str] = []
    for path in src_root.rglob("*.py"):
        relative = path.relative_to(repo_root).as_posix()
        if relative in allowed_paths:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _source_contains_governed_pattern(text, pattern):
            matches.append(relative)
    return matches


def _source_contains_governed_pattern(text: str, pattern: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    if pattern == "subprocess.run(":
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" and node.func.attr == "run":
                    return True
        return False
    if pattern == "execute_direct_provider_prompt(":
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "execute_direct_provider_prompt":
                return True
        return False
    if pattern == "reply_text_attribute":
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "reply_text":
                return True
        return False
    return pattern in text
