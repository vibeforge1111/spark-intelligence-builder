from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import (
    events_for_run,
    latest_events_by_type,
    latest_snapshots_by_surface,
    open_runs,
    recent_config_mutations,
    recent_quarantine_records,
    record_event,
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
        _background_closure_issue(state_db),
        _runtime_state_authority_issue(state_db),
        _plugin_provenance_issue(config_manager=config_manager, state_db=state_db),
        _secret_boundary_issue(state_db),
        _keepability_issue(state_db),
        _environment_parity_issue(state_db),
        _daemon_reentry_issue(config_manager=config_manager),
    ]
    if emit_contradictions:
        for issue in issues:
            if issue.ok:
                continue
            record_event(
                state_db,
                event_type="contradiction_recorded",
                component="stop_ship_checks",
                summary=f"Stop-ship contradiction: {issue.name}.",
                reason_code=issue.name,
                severity=issue.severity,
                facts={"detail": issue.detail},
            )
    return issues


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
    if missing:
        return StopShipIssue(
            name="stop_ship_keepability_rules",
            ok=False,
            detail=f"{len(missing)} influence event(s) are missing keepability classification.",
            severity="high",
        )
    return StopShipIssue(
        name="stop_ship_keepability_rules",
        ok=True,
        detail="Operational influence records include keepability classification.",
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
