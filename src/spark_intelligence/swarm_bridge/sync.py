from __future__ import annotations

import base64
import importlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import build_attachment_context
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import latest_events_by_type, record_environment_snapshot, record_event
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, resolve_researcher_config_path
from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import JSON_RICHNESS_MERGE_GUARD, upsert_runtime_state


@dataclass
class SwarmStatus:
    enabled: bool
    configured: bool
    researcher_ready: bool
    payload_ready: bool
    api_ready: bool
    auth_state: str
    runtime_root: str | None
    researcher_runtime_root: str | None
    researcher_config_path: str | None
    api_url: str | None
    supabase_url: str | None
    workspace_id: str | None
    access_token_env: str | None
    refresh_token_env: str | None
    auth_client_key_env: str | None
    access_token_expires_at: str | None
    attachment_context: dict[str, Any]
    last_sync: dict[str, Any] | None
    last_decision: dict[str, Any] | None
    failure_count: int
    last_failure: dict[str, Any] | None
    last_refresh_at: str | None
    last_refresh_error: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "configured": self.configured,
                "enabled": self.enabled,
                "researcher_ready": self.researcher_ready,
                "payload_ready": self.payload_ready,
                "api_ready": self.api_ready,
                "auth_state": self.auth_state,
                "runtime_root": self.runtime_root,
                "researcher_runtime_root": self.researcher_runtime_root,
                "researcher_config_path": self.researcher_config_path,
                "api_url": self.api_url,
                "supabase_url": self.supabase_url,
                "workspace_id": self.workspace_id,
                "access_token_env": self.access_token_env,
                "refresh_token_env": self.refresh_token_env,
                "auth_client_key_env": self.auth_client_key_env,
                "access_token_expires_at": self.access_token_expires_at,
                "attachment_context": self.attachment_context,
                "last_sync": self.last_sync,
                "last_decision": self.last_decision,
                "failure_count": self.failure_count,
                "last_failure": self.last_failure,
                "last_refresh_at": self.last_refresh_at,
                "last_refresh_error": self.last_refresh_error,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Swarm enabled: {'yes' if self.enabled else 'no'}",
            f"Swarm configured: {'yes' if self.configured else 'no'}",
            f"- researcher_ready: {'yes' if self.researcher_ready else 'no'}",
            f"- payload_ready: {'yes' if self.payload_ready else 'no'}",
            f"- api_ready: {'yes' if self.api_ready else 'no'}",
            f"- auth_state: {self.auth_state}",
            f"- runtime_root: {self.runtime_root or 'missing'}",
            f"- researcher_runtime_root: {self.researcher_runtime_root or 'missing'}",
            f"- researcher_config_path: {self.researcher_config_path or 'missing'}",
            f"- api_url: {self.api_url or 'missing'}",
            f"- supabase_url: {self.supabase_url or 'missing'}",
            f"- workspace_id: {self.workspace_id or 'missing'}",
            f"- access_token_env: {self.access_token_env or 'missing'}",
            f"- refresh_token_env: {self.refresh_token_env or 'missing'}",
            f"- auth_client_key_env: {self.auth_client_key_env or 'missing'}",
            f"- access_token_expires_at: {self.access_token_expires_at or 'unknown'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
            f"- last_sync_mode: {(self.last_sync or {}).get('mode', 'none')}",
            f"- last_decision_mode: {(self.last_decision or {}).get('mode', 'none')}",
            f"- failure_count: {self.failure_count}",
        ]
        if self.last_refresh_at:
            lines.append(f"- last_refresh_at: {self.last_refresh_at}")
        if self.last_refresh_error:
            lines.append(f"- last_refresh_error: {self.last_refresh_error}")
        if self.last_failure:
            lines.append(
                f"- last_failure: mode={self.last_failure.get('mode') or 'unknown'} "
                f"message={self.last_failure.get('message') or 'unknown'}"
            )
            response_body = self.last_failure.get("response_body")
            if isinstance(response_body, dict) and response_body.get("error"):
                lines.append(f"- last_failure_error: {response_body.get('error')}")
        return "\n".join(lines)


@dataclass
class SwarmDoctorReport:
    enabled: bool
    configured: bool
    api_ready: bool
    payload_ready: bool
    auth_state: str
    auth_source: str
    workspace_env_path: str
    api_url: str | None
    workspace_id: str | None
    access_token_env: str | None
    active_path_key: str | None
    active_path_repo_root: str | None
    active_path_manifest_path: str | None
    scenario_path: str | None
    mutation_target_path: str | None
    payload_source: str
    payload_path: str | None
    expected_bridge_repo_env: str | None
    expected_bridge_repo_value: str | None
    last_sync_mode: str | None
    last_failure_mode: str | None
    last_failure_error: str | None
    blockers: list[str]
    recommendations: list[str]

    def to_json(self) -> str:
        return json.dumps(
            {
                "enabled": self.enabled,
                "configured": self.configured,
                "api_ready": self.api_ready,
                "payload_ready": self.payload_ready,
                "auth_state": self.auth_state,
                "auth_source": self.auth_source,
                "workspace_env_path": self.workspace_env_path,
                "api_url": self.api_url,
                "workspace_id": self.workspace_id,
                "access_token_env": self.access_token_env,
                "active_path_key": self.active_path_key,
                "active_path_repo_root": self.active_path_repo_root,
                "active_path_manifest_path": self.active_path_manifest_path,
                "scenario_path": self.scenario_path,
                "mutation_target_path": self.mutation_target_path,
                "payload_source": self.payload_source,
                "payload_path": self.payload_path,
                "expected_bridge_repo_env": self.expected_bridge_repo_env,
                "expected_bridge_repo_value": self.expected_bridge_repo_value,
                "last_sync_mode": self.last_sync_mode,
                "last_failure_mode": self.last_failure_mode,
                "last_failure_error": self.last_failure_error,
                "blockers": self.blockers,
                "recommendations": self.recommendations,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            "Swarm doctor",
            f"- enabled: {'yes' if self.enabled else 'no'}",
            f"- configured: {'yes' if self.configured else 'no'}",
            f"- auth_state: {self.auth_state}",
            f"- auth_source: {self.auth_source}",
            f"- api_ready: {'yes' if self.api_ready else 'no'}",
            f"- payload_ready: {'yes' if self.payload_ready else 'no'}",
            f"- api_url: {self.api_url or 'missing'}",
            f"- workspace_id: {self.workspace_id or 'missing'}",
            f"- workspace_env_path: {self.workspace_env_path}",
            f"- active_path_key: {self.active_path_key or 'none'}",
            f"- active_path_repo_root: {self.active_path_repo_root or 'missing'}",
            f"- active_path_manifest_path: {self.active_path_manifest_path or 'missing'}",
            f"- scenario_path: {self.scenario_path or 'missing'}",
            f"- mutation_target_path: {self.mutation_target_path or 'missing'}",
            f"- payload_source: {self.payload_source}",
            f"- payload_path: {self.payload_path or 'missing'}",
            f"- expected_bridge_repo_env: {self.expected_bridge_repo_env or 'n/a'}",
            f"- expected_bridge_repo_value: {self.expected_bridge_repo_value or 'n/a'}",
            f"- last_sync_mode: {self.last_sync_mode or 'none'}",
            f"- last_failure_mode: {self.last_failure_mode or 'none'}",
        ]
        if self.last_failure_error:
            lines.append(f"- last_failure_error: {self.last_failure_error}")
        if self.blockers:
            lines.append("- blockers:")
            lines.extend(f"  - {item}" for item in self.blockers)
        else:
            lines.append("- blockers: none")
        if self.recommendations:
            lines.append("- next:")
            lines.extend(f"  - {item}" for item in self.recommendations)
        return "\n".join(lines)


@dataclass
class SwarmSyncResult:
    ok: bool
    mode: str
    message: str
    payload_path: str | None
    api_url: str | None
    workspace_id: str | None
    accepted: bool | None
    response_body: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "mode": self.mode,
                "message": self.message,
                "payload_path": self.payload_path,
                "api_url": self.api_url,
                "workspace_id": self.workspace_id,
                "accepted": self.accepted,
                "response_body": self.response_body,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Swarm sync: {'ok' if self.ok else 'failed'}", f"- mode: {self.mode}", f"- message: {self.message}"]
        if self.payload_path:
            lines.append(f"- payload_path: {self.payload_path}")
        if self.api_url:
            lines.append(f"- api_url: {self.api_url}")
        if self.workspace_id:
            lines.append(f"- workspace_id: {self.workspace_id}")
        if self.accepted is not None:
            lines.append(f"- accepted: {'yes' if self.accepted else 'no'}")
        if self.response_body is not None:
            lines.append(f"- response_body: {json.dumps(self.response_body, sort_keys=True)}")
        return "\n".join(lines)


@dataclass
class SwarmDecisionResult:
    ok: bool
    escalate: bool
    mode: str
    reason: str
    triggers: list[str]
    task: str
    attachment_context: dict[str, Any]
    swarm_available: bool
    api_ready: bool

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "escalate": self.escalate,
                "mode": self.mode,
                "reason": self.reason,
                "triggers": self.triggers,
                "task": self.task,
                "attachment_context": self.attachment_context,
                "swarm_available": self.swarm_available,
                "api_ready": self.api_ready,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Swarm escalation: {'recommended' if self.escalate else 'hold local'}",
            f"- mode: {self.mode}",
            f"- reason: {self.reason}",
            f"- triggers: {', '.join(self.triggers) if self.triggers else 'none'}",
            f"- swarm_available: {'yes' if self.swarm_available else 'no'}",
            f"- api_ready: {'yes' if self.api_ready else 'no'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
        ]
        return "\n".join(lines)


@dataclass
class SwarmSession:
    access_token_env: str | None
    access_token: str | None
    refresh_token_env: str | None
    refresh_token: str | None
    auth_client_key_env: str | None
    auth_client_key: str | None
    supabase_url: str | None
    access_token_expires_at: str | None
    auth_state: str


def swarm_read_overview(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/overview",
    )
    return payload if isinstance(payload, dict) else {}


def swarm_read_runtime_pulse(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/runtime/pulse",
    )
    return payload if isinstance(payload, dict) else {}


def swarm_read_live_session(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/live",
    )
    return payload if isinstance(payload, dict) else {}


def swarm_read_collective_snapshot(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/collective-snapshot",
    )
    return payload if isinstance(payload, dict) else {}


def swarm_read_specializations(config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/specializations",
    )
    return payload if isinstance(payload, list) else []


def swarm_read_insights(config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/insights",
    )
    return payload if isinstance(payload, list) else []


def swarm_read_masteries(config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/masteries",
    )
    return payload if isinstance(payload, list) else []


def swarm_read_upgrades(config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/upgrades",
    )
    return payload if isinstance(payload, list) else []


def swarm_read_operator_issues(config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/operator-issues",
    )
    return payload if isinstance(payload, list) else []


def swarm_read_evolution_inbox(config_manager: ConfigManager, state_db: StateDB) -> dict[str, Any]:
    payload = _fetch_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path="api/workspaces/{workspace_id}/evolution-inbox",
    )
    return payload if isinstance(payload, dict) else {}


def swarm_absorb_insight(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    insight_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = _post_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path=f"api/workspaces/{{workspace_id}}/insights/{urllib.parse.quote(insight_id, safe='')}/absorb",
        body={"reason": reason} if reason else {},
    )
    return payload if isinstance(payload, dict) else {}


def swarm_review_mastery(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    mastery_id: str,
    decision: str,
    reason: str,
    recommended_next_step: str | None = None,
    rollback_condition: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"decision": decision, "reason": reason}
    if recommended_next_step:
        body["recommendedNextStep"] = recommended_next_step
    if rollback_condition:
        body["rollbackCondition"] = rollback_condition
    payload = _post_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path=f"api/workspaces/{{workspace_id}}/masteries/{urllib.parse.quote(mastery_id, safe='')}/review",
        body=body,
    )
    return payload if isinstance(payload, dict) else {}


def swarm_set_evolution_mode(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    specialization_id: str,
    evolution_mode: str,
) -> dict[str, Any]:
    payload = _post_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path=f"api/workspaces/{{workspace_id}}/specializations/{urllib.parse.quote(specialization_id, safe='')}/evolution-mode",
        body={"evolutionMode": evolution_mode},
    )
    return payload if isinstance(payload, dict) else {}


def swarm_deliver_upgrade(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    upgrade_id: str,
    evolution_mode: str | None = None,
    pr_url: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if evolution_mode:
        body["evolutionMode"] = evolution_mode
    if pr_url:
        body["prUrl"] = pr_url
    payload = _post_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path=f"api/workspaces/{{workspace_id}}/upgrades/{urllib.parse.quote(upgrade_id, safe='')}/deliver",
        body=body,
    )
    return payload if isinstance(payload, dict) else {}


def swarm_sync_upgrade_delivery_status(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    upgrade_id: str,
    pr_url: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if pr_url:
        body["prUrl"] = pr_url
    payload = _post_swarm_api_json(
        config_manager=config_manager,
        state_db=state_db,
        route_path=f"api/workspaces/{{workspace_id}}/upgrades/{urllib.parse.quote(upgrade_id, safe='')}/sync-delivery-status",
        body=body,
    )
    return payload if isinstance(payload, dict) else {}


def swarm_status(config_manager: ConfigManager, state_db: StateDB | None = None) -> SwarmStatus:
    enabled = bool(config_manager.get_path("spark.swarm.enabled", default=True))
    runtime_root, _ = _discover_swarm_runtime_root(config_manager)
    researcher_root, _ = discover_researcher_runtime_root(config_manager)
    researcher_config_path = resolve_researcher_config_path(config_manager, researcher_root) if researcher_root else None
    api_url = _resolve_swarm_api_url(config_manager)
    workspace_id = _resolve_swarm_workspace_id(config_manager)
    session = _resolve_swarm_session(config_manager, state_db=state_db)
    attachment_context = build_attachment_context(config_manager)
    runtime_state = _read_swarm_runtime_state(state_db) if state_db is not None else {}
    typed_status = _read_typed_swarm_status(state_db) if state_db is not None else {}
    researcher_ready = enabled and bool(researcher_root and researcher_config_path and researcher_config_path.exists())
    path_collective = _resolve_active_path_collective_payload(config_manager, attachment_context=attachment_context)
    payload_ready = bool(path_collective) or (researcher_ready and _researcher_has_ledger(researcher_config_path))
    api_ready = enabled and bool(api_url and workspace_id and session.access_token)
    return SwarmStatus(
        enabled=enabled,
        configured=bool(runtime_root or api_url or workspace_id or session.access_token_env or session.refresh_token_env),
        researcher_ready=researcher_ready,
        payload_ready=payload_ready,
        api_ready=api_ready,
        auth_state=session.auth_state,
        runtime_root=str(runtime_root) if runtime_root else None,
        researcher_runtime_root=str(researcher_root) if researcher_root else None,
        researcher_config_path=str(researcher_config_path) if researcher_config_path else None,
        api_url=api_url,
        supabase_url=session.supabase_url,
        workspace_id=workspace_id,
        access_token_env=session.access_token_env,
        refresh_token_env=session.refresh_token_env,
        auth_client_key_env=session.auth_client_key_env,
        access_token_expires_at=session.access_token_expires_at,
        attachment_context=attachment_context,
        last_sync=typed_status.get("last_sync") or _loads_json_object(runtime_state.get("swarm:last_sync")),
        last_decision=typed_status.get("last_decision") or _loads_json_object(runtime_state.get("swarm:last_decision")),
        failure_count=typed_status.get("failure_count") or _parse_int(runtime_state.get("swarm:failure_count")),
        last_failure=typed_status.get("last_failure") or _loads_json_object(runtime_state.get("swarm:last_failure")),
        last_refresh_at=typed_status.get("last_refresh_at") or runtime_state.get("swarm:last_auth_refresh_at") or None,
        last_refresh_error=typed_status.get("last_refresh_error") or runtime_state.get("swarm:last_auth_refresh_error") or None,
    )


def swarm_doctor(config_manager: ConfigManager, state_db: StateDB | None = None) -> SwarmDoctorReport:
    status = swarm_status(config_manager, state_db)
    attachment_context = status.attachment_context if isinstance(status.attachment_context, dict) else {}
    active_path_record = _resolve_active_path_record(attachment_context)
    active_path_key = str(attachment_context.get("active_path_key") or "").strip() or None
    active_path_repo_root = (
        _resolve_attachment_repo_root(config_manager, active_path_record.get("repo_root"))
        if active_path_record is not None
        else None
    )
    manifest_path = active_path_repo_root / "specialization-path.json" if active_path_repo_root is not None else None
    manifest_payload = _read_json_file_if_exists(manifest_path) if manifest_path is not None else None
    scenario_path = (
        _resolve_specialization_default_scenario_path(active_path_repo_root, manifest_payload)
        if active_path_repo_root is not None
        else None
    )
    mutation_target_path = (
        _resolve_specialization_default_mutation_target_path(active_path_repo_root, manifest_payload)
        if active_path_repo_root is not None
        else None
    )
    path_collective = _resolve_active_path_collective_payload(config_manager, attachment_context=attachment_context)
    payload_source = "missing"
    payload_path: Path | None = None
    if path_collective is not None:
        payload_source = "specialization_path"
        payload_path = path_collective[1]
    elif status.researcher_ready and status.researcher_config_path and _researcher_has_ledger(Path(status.researcher_config_path)):
        payload_source = "spark_researcher"
    auth_source = _resolve_swarm_auth_source(config_manager, status.access_token_env)
    blockers = _build_swarm_doctor_blockers(
        config_manager=config_manager,
        status=status,
        active_path_key=active_path_key,
        active_path_repo_root=active_path_repo_root,
        manifest_path=manifest_path,
        scenario_path=scenario_path,
        mutation_target_path=mutation_target_path,
        payload_source=payload_source,
    )
    recommendations = _build_swarm_doctor_recommendations(
        status=status,
        auth_source=auth_source,
        active_path_key=active_path_key,
        payload_source=payload_source,
    )
    last_failure = status.last_failure if isinstance(status.last_failure, dict) else {}
    response_body = last_failure.get("response_body") if isinstance(last_failure, dict) else {}
    last_failure_error = response_body.get("error") if isinstance(response_body, dict) else None
    expected_bridge_repo_env = _specialization_repo_env_var(active_path_key) if active_path_key else None
    expected_bridge_repo_value = str(active_path_repo_root) if active_path_repo_root is not None else None
    return SwarmDoctorReport(
        enabled=status.enabled,
        configured=status.configured,
        api_ready=status.api_ready,
        payload_ready=status.payload_ready,
        auth_state=status.auth_state,
        auth_source=auth_source,
        workspace_env_path=str(config_manager.paths.env_file),
        api_url=status.api_url,
        workspace_id=status.workspace_id,
        access_token_env=status.access_token_env,
        active_path_key=active_path_key,
        active_path_repo_root=str(active_path_repo_root) if active_path_repo_root is not None else None,
        active_path_manifest_path=str(manifest_path) if manifest_path is not None else None,
        scenario_path=str(scenario_path) if scenario_path is not None else None,
        mutation_target_path=str(mutation_target_path) if mutation_target_path is not None else None,
        payload_source=payload_source,
        payload_path=str(payload_path) if payload_path is not None else None,
        expected_bridge_repo_env=expected_bridge_repo_env,
        expected_bridge_repo_value=expected_bridge_repo_value,
        last_sync_mode=(status.last_sync or {}).get("mode") if isinstance(status.last_sync, dict) else None,
        last_failure_mode=last_failure.get("mode") if isinstance(last_failure, dict) else None,
        last_failure_error=str(last_failure_error).strip() or None if last_failure_error is not None else None,
        blockers=blockers,
        recommendations=recommendations,
    )


def sync_swarm_collective(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    dry_run: bool = False,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str = "swarm_bridge",
) -> SwarmSyncResult:
    status = swarm_status(config_manager, state_db)
    path_collective = _resolve_active_path_collective_payload(config_manager, attachment_context=status.attachment_context)
    record_environment_snapshot(
        state_db,
        surface="swarm_bridge",
        run_id=run_id,
        request_id=request_id,
        summary="Swarm bridge environment snapshot recorded.",
        runtime_root=status.runtime_root,
        config_path=status.researcher_config_path,
        env_refs={
            "access_token_env": status.access_token_env,
            "refresh_token_env": status.refresh_token_env,
            "auth_client_key_env": status.auth_client_key_env,
        },
        facts={
            "api_url": status.api_url,
            "workspace_id": status.workspace_id,
            "auth_state": status.auth_state,
            "payload_ready": status.payload_ready,
            "api_ready": status.api_ready,
        },
    )
    if not status.enabled:
        result = SwarmSyncResult(
            ok=False,
            mode="disabled",
            message="Spark Swarm bridge is disabled by operator.",
            payload_path=None,
            api_url=status.api_url,
            workspace_id=status.workspace_id,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result
    if path_collective is None and (
        not status.researcher_ready or not status.researcher_config_path or not status.researcher_runtime_root
    ):
        result = SwarmSyncResult(
            ok=False,
            mode="payload_source_missing",
            message="No active specialization-path collective payload or Spark Researcher runtime/config is available.",
            payload_path=None,
            api_url=status.api_url,
            workspace_id=status.workspace_id,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result

    workspace_id = status.workspace_id
    if not workspace_id:
        result = SwarmSyncResult(
            ok=False,
            mode="workspace_id_missing",
            message="spark.swarm.workspace_id is required before Swarm sync.",
            payload_path=None,
            api_url=status.api_url,
            workspace_id=None,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result

    if path_collective is not None:
        payload, payload_path = path_collective
    else:
        researcher_root = Path(status.researcher_runtime_root)
        researcher_config_path = Path(status.researcher_config_path)
        payload, payload_path = _build_collective_payload(
            config_manager=config_manager,
            researcher_root=researcher_root,
            researcher_config_path=researcher_config_path,
            workspace_id=workspace_id,
        )
    payload_changed = False
    if _normalize_collective_workspace(payload, workspace_id):
        payload_changed = True
    if _normalize_collective_payload(payload):
        payload_changed = True
    if payload_changed:
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _record_swarm_sync_state(
        state_db,
        mode="payload_built",
        payload_path=str(payload_path),
        api_url=status.api_url,
        workspace_id=workspace_id,
        accepted=None,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )

    if dry_run:
        record_event(
            state_db,
            event_type="tool_result_received",
            component="swarm_bridge",
            summary="Swarm sync dry-run produced a payload without external dispatch.",
            reason_code="swarm_sync_dry_run",
            facts={
                "swarm_operation": "sync",
                "mode": "dry_run",
                "payload_path": str(payload_path),
                "api_url": status.api_url,
                "workspace_id": workspace_id,
                "accepted": None,
                "payload_keys": sorted(payload.keys()),
            },
            **_swarm_event_context(
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id=actor_id,
            ),
        )
        return SwarmSyncResult(
            ok=True,
            mode="dry_run",
            message="Built the latest Spark Swarm collective payload without uploading it.",
            payload_path=str(payload_path),
            api_url=status.api_url,
            workspace_id=workspace_id,
            accepted=None,
            response_body={"payload_keys": sorted(payload.keys())},
        )

    api_url = status.api_url
    session = _resolve_swarm_session(config_manager, state_db=state_db)
    if not api_url or (not session.access_token and session.auth_state != "refreshable"):
        result = SwarmSyncResult(
            ok=False,
            mode="api_not_configured",
            message="Swarm API URL or access token is missing; payload was built but not uploaded.",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result

    if session.auth_state == "expired":
        result = SwarmSyncResult(
            ok=False,
            mode="auth_expired",
            message="Swarm access token is expired and no refresh path is configured.",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            response_body={"error": "access_token_expired"},
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result

    if session.auth_state == "refreshable":
        try:
            session = _refresh_swarm_access_token(config_manager=config_manager, state_db=state_db, session=session)
        except RuntimeError as exc:
            result = SwarmSyncResult(
                ok=False,
                mode="refresh_error",
                message=str(exc),
                payload_path=str(payload_path),
                api_url=api_url,
                workspace_id=workspace_id,
                accepted=False,
                response_body={"error": "refresh_failed"},
            )
            _record_swarm_failure_state(
                state_db,
                kind="sync",
                result=result,
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id=actor_id,
            )
            return result

    record_event(
        state_db,
        event_type="dispatch_started",
        component="swarm_bridge",
        summary="Swarm sync dispatch started.",
        reason_code="swarm_sync_upload",
        facts={
            "swarm_operation": "sync",
            "payload_path": str(payload_path),
            "api_url": api_url,
            "workspace_id": workspace_id,
        },
        **_swarm_event_context(
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        ),
    )
    try:
        response_body = _post_collective_payload(
            api_url=api_url,
            workspace_id=workspace_id,
            access_token=session.access_token or "",
            payload=payload,
        )
    except urllib.error.HTTPError as exc:
        body = _read_http_error_body(exc)
        if (
            exc.code == 401
            and _http_error_requires_auth(body)
            and session.refresh_token
            and session.auth_client_key
            and session.supabase_url
        ):
            try:
                session = _refresh_swarm_access_token(config_manager=config_manager, state_db=state_db, session=session)
                response_body = _post_collective_payload(
                    api_url=api_url,
                    workspace_id=workspace_id,
                    access_token=session.access_token or "",
                    payload=payload,
                )
            except RuntimeError as refresh_exc:
                result = SwarmSyncResult(
                    ok=False,
                    mode="refresh_error",
                    message=str(refresh_exc),
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=False,
                    response_body=body,
                )
                _record_swarm_failure_state(
                    state_db,
                    kind="sync",
                    result=result,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_id,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                )
                return result
            except urllib.error.HTTPError as retry_exc:
                retry_body = _read_http_error_body(retry_exc)
                _record_swarm_sync_state(
                    state_db,
                    mode="http_error",
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=False,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_id,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                )
                result = SwarmSyncResult(
                    ok=False,
                    mode="http_error",
                    message=f"Swarm API rejected the sync with HTTP {retry_exc.code} after refresh retry.",
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=False,
                    response_body=retry_body,
                )
                _record_swarm_failure_state(
                    state_db,
                    kind="sync",
                    result=result,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_id,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                )
                return result
            except urllib.error.URLError as retry_exc:
                _record_swarm_sync_state(
                    state_db,
                    mode="network_error",
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=False,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_id,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                )
                result = SwarmSyncResult(
                    ok=False,
                    mode="network_error",
                    message=f"Could not reach Swarm API after refresh retry: {retry_exc.reason}",
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=False,
                    response_body=None,
                )
                _record_swarm_failure_state(
                    state_db,
                    kind="sync",
                    result=result,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_id,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                )
                return result
            else:
                accepted = bool(response_body.get("accepted"))
                _record_swarm_sync_state(
                    state_db,
                    mode="uploaded",
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=accepted,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_id,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                )
                result = SwarmSyncResult(
                    ok=accepted,
                    mode="uploaded",
                    message="Uploaded the latest Spark Swarm collective payload after refreshing the session.",
                    payload_path=str(payload_path),
                    api_url=api_url,
                    workspace_id=workspace_id,
                    accepted=accepted,
                    response_body=response_body,
                )
                if not accepted:
                    _record_swarm_failure_state(
                        state_db,
                        kind="sync",
                        result=result,
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        channel_id=channel_id,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                        actor_id=actor_id,
                    )
                return result
        _record_swarm_sync_state(
            state_db,
            mode="http_error",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        result = SwarmSyncResult(
            ok=False,
            mode="http_error",
            message=f"Swarm API rejected the sync with HTTP {exc.code}.",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            response_body=body,
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result
    except urllib.error.URLError as exc:
        _record_swarm_sync_state(
            state_db,
            mode="network_error",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        result = SwarmSyncResult(
            ok=False,
            mode="network_error",
            message=f"Could not reach Swarm API: {exc.reason}",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            response_body=None,
        )
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result

    accepted = bool(response_body.get("accepted"))
    _record_swarm_sync_state(
        state_db,
        mode="uploaded",
        payload_path=str(payload_path),
        api_url=api_url,
        workspace_id=workspace_id,
        accepted=accepted,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    result = SwarmSyncResult(
        ok=accepted,
        mode="uploaded",
        message="Uploaded the latest Spark Swarm collective payload.",
        payload_path=str(payload_path),
        api_url=api_url,
        workspace_id=workspace_id,
        accepted=accepted,
        response_body=response_body,
    )
    if not accepted:
        _record_swarm_failure_state(
            state_db,
            kind="sync",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
    return result


def evaluate_swarm_escalation(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str = "swarm_bridge",
) -> SwarmDecisionResult:
    status = swarm_status(config_manager, state_db)
    record_environment_snapshot(
        state_db,
        surface="swarm_bridge",
        run_id=run_id,
        request_id=request_id,
        summary="Swarm escalation environment snapshot recorded.",
        runtime_root=status.runtime_root,
        config_path=status.researcher_config_path,
        env_refs={
            "access_token_env": status.access_token_env,
            "refresh_token_env": status.refresh_token_env,
            "auth_client_key_env": status.auth_client_key_env,
        },
        facts={
            "api_url": status.api_url,
            "workspace_id": status.workspace_id,
            "task_length": len(task.split()),
            "auth_state": status.auth_state,
        },
    )
    if not status.enabled:
        result = SwarmDecisionResult(
            ok=False,
            escalate=False,
            mode="disabled",
            reason="Spark Swarm is disabled by operator for this workspace.",
            triggers=[],
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=False,
            api_ready=False,
        )
        _record_swarm_decision_state(
            state_db,
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        _record_swarm_failure_state(
            state_db,
            kind="decision",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        return result
    lowered = task.lower()
    triggers: list[str] = []
    auto_recommend_enabled = bool(
        config_manager.get_path("spark.swarm.routing.auto_recommend_enabled", default=True)
    )
    long_task_word_count = int(
        config_manager.get_path("spark.swarm.routing.long_task_word_count", default=40)
    )
    keyword_groups = {
        "explicit_swarm": ["swarm", "delegate", "delegation"],
        "parallel_work": ["parallel", "multi-agent", "multi agent", "coordinate"],
        "deep_research": ["research deeply", "investigate deeply", "comprehensive"],
        "multi_step": ["break down", "multi-step", "orchestrate", "workflow"],
    }
    for trigger, phrases in keyword_groups.items():
        if any(phrase in lowered for phrase in phrases):
            triggers.append(trigger)
    if len(task.split()) >= long_task_word_count:
        triggers.append("long_task")
    if len(status.attachment_context.get("active_chip_keys", [])) >= 2:
        triggers.append("multi_chip_context")
    recommendation_triggers = [trigger for trigger in triggers if trigger != "multi_chip_context"]

    if not status.payload_ready:
        result = SwarmDecisionResult(
            ok=False,
            escalate=False,
            mode="unavailable",
            reason="Spark Swarm cannot be recommended because the local payload path is not ready.",
            triggers=triggers,
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=False,
            api_ready=status.api_ready,
        )
        _record_swarm_failure_state(
            state_db,
            kind="decision",
            result=result,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
    elif recommendation_triggers and auto_recommend_enabled:
        result = SwarmDecisionResult(
            ok=True,
            escalate=True,
            mode="manual_recommended",
            reason="This task shows explicit escalation signals and Spark Swarm is available.",
            triggers=triggers,
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=True,
            api_ready=status.api_ready,
        )
    else:
        result = SwarmDecisionResult(
            ok=True,
            escalate=False,
            mode="hold_local",
            reason="No strong escalation signals were detected; keep the task on the primary agent.",
            triggers=triggers,
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=True,
            api_ready=status.api_ready,
        )

    _record_swarm_decision_state(
        state_db,
        result=result,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_id,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    if status.attachment_context.get("active_chip_keys") or status.attachment_context.get("active_path_key"):
        record_event(
            state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="swarm_bridge",
            summary="Swarm escalation considered active chip or path context.",
            reason_code="swarm_attachment_context",
            facts={
                "swarm_operation": "decision",
                "active_chip_keys": status.attachment_context.get("active_chip_keys") or [],
                "pinned_chip_keys": status.attachment_context.get("pinned_chip_keys") or [],
                "active_path_key": status.attachment_context.get("active_path_key"),
                "decision_mode": result.mode,
                "keepability": "ephemeral_context",
            },
            provenance={
                "source_kind": "attachment_snapshot",
                "source_ref": "swarm_status_attachment_context",
            },
            **_swarm_event_context(
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id=actor_id,
            ),
        )
    return result


def _discover_swarm_runtime_root(config_manager: ConfigManager) -> tuple[Path | None, str]:
    configured_root = config_manager.get_path("spark.swarm.runtime_root")
    if configured_root:
        path = config_manager.normalize_runtime_path(configured_root)
        return (path if path.exists() else None, "configured")
    autodetect = Path.home() / "Desktop" / "spark-swarm"
    if autodetect.exists():
        return autodetect, "autodiscovered"
    return None, "missing"


def _resolve_swarm_api_url(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.api_url")
    if configured:
        return str(configured).rstrip("/")
    return None


def _resolve_swarm_workspace_id(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.workspace_id")
    if configured:
        return str(configured)
    env_value = config_manager.read_env_map().get("SPARK_SWARM_WORKSPACE_ID")
    return env_value or None


def _resolve_swarm_access_token_env(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.access_token_env")
    if configured:
        return str(configured)
    env_map = config_manager.read_env_map()
    if "SPARK_SWARM_ACCESS_TOKEN" in env_map:
        return "SPARK_SWARM_ACCESS_TOKEN"
    return None


def _resolve_swarm_access_token(config_manager: ConfigManager) -> str | None:
    env_ref = _resolve_swarm_access_token_env(config_manager)
    if not env_ref:
        return None
    return config_manager.read_env_map().get(env_ref)


def _resolve_swarm_refresh_token_env(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.refresh_token_env")
    if configured:
        return str(configured)
    env_map = config_manager.read_env_map()
    if "SPARK_SWARM_REFRESH_TOKEN" in env_map:
        return "SPARK_SWARM_REFRESH_TOKEN"
    return None


def _resolve_swarm_refresh_token(config_manager: ConfigManager) -> str | None:
    env_ref = _resolve_swarm_refresh_token_env(config_manager)
    if not env_ref:
        return None
    return config_manager.read_env_map().get(env_ref)


def _resolve_swarm_auth_client_key_env(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.auth_client_key_env")
    if configured:
        return str(configured)
    env_map = config_manager.read_env_map()
    if "SPARK_SWARM_AUTH_CLIENT_KEY" in env_map:
        return "SPARK_SWARM_AUTH_CLIENT_KEY"
    local_env = _read_local_swarm_env_map(config_manager)
    for key in (
        "SUPABASE_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ):
        if local_env.get(key):
            return f"local:{key}"
    return None


def _resolve_swarm_auth_client_key(config_manager: ConfigManager) -> str | None:
    env_ref = _resolve_swarm_auth_client_key_env(config_manager)
    if not env_ref:
        return None
    if env_ref.startswith("local:"):
        return _read_local_swarm_env_map(config_manager).get(env_ref.split(":", 1)[1])
    return config_manager.read_env_map().get(env_ref)


def _resolve_swarm_supabase_url(config_manager: ConfigManager, access_token: str | None) -> str | None:
    configured = config_manager.get_path("spark.swarm.supabase_url")
    if configured:
        return str(configured).rstrip("/")
    env_map = config_manager.read_env_map()
    if env_map.get("SPARK_SWARM_SUPABASE_URL"):
        return env_map["SPARK_SWARM_SUPABASE_URL"].rstrip("/")
    local_env = _read_local_swarm_env_map(config_manager)
    if local_env.get("SUPABASE_URL"):
        return str(local_env["SUPABASE_URL"]).rstrip("/")
    claims = _decode_jwt_claims(access_token)
    issuer = claims.get("iss") if isinstance(claims, dict) else None
    if isinstance(issuer, str) and issuer:
        if issuer.endswith("/auth/v1"):
            return issuer[: -len("/auth/v1")]
        return issuer.rstrip("/")
    return None


def _resolve_swarm_session(config_manager: ConfigManager, *, state_db: StateDB | None = None) -> SwarmSession:
    access_token_env = _resolve_swarm_access_token_env(config_manager)
    access_token = _resolve_swarm_access_token(config_manager)
    refresh_token_env = _resolve_swarm_refresh_token_env(config_manager)
    refresh_token = _resolve_swarm_refresh_token(config_manager)
    auth_client_key_env = _resolve_swarm_auth_client_key_env(config_manager)
    auth_client_key = _resolve_swarm_auth_client_key(config_manager)
    supabase_url = _resolve_swarm_supabase_url(config_manager, access_token)
    access_token_expires_at = _token_expiry_iso(access_token)
    token_expired = _token_is_expired(access_token)
    last_failure = {}
    if state_db is not None:
        runtime_state = _read_swarm_runtime_state(state_db)
        last_failure = _loads_json_object(runtime_state.get("swarm:last_failure")) or {}
    auth_state = "missing"
    if access_token:
        if token_expired:
            auth_state = "refreshable" if refresh_token and auth_client_key and supabase_url else "expired"
        elif _http_error_requires_auth(last_failure.get("response_body")):
            auth_state = "auth_rejected"
        else:
            auth_state = "configured"
    elif refresh_token and auth_client_key and supabase_url:
        auth_state = "refreshable"
    return SwarmSession(
        access_token_env=access_token_env,
        access_token=access_token,
        refresh_token_env=refresh_token_env,
        refresh_token=refresh_token,
        auth_client_key_env=auth_client_key_env,
        auth_client_key=auth_client_key,
        supabase_url=supabase_url,
        access_token_expires_at=access_token_expires_at,
        auth_state=auth_state,
    )


def _researcher_has_ledger(config_path: Path) -> bool:
    try:
        resolve_runtime_root = _import_researcher_symbol(config_path.parent.resolve(), "spark_researcher.paths", "resolve_runtime_root")
        ledger_path = _import_researcher_symbol(config_path.parent.resolve(), "spark_researcher.paths", "ledger_path")
        runtime_root = resolve_runtime_root(config_path)
        return ledger_path(runtime_root).exists()
    except Exception:
        return False


def _resolve_attachment_repo_root(config_manager: ConfigManager, repo_root_value: Any) -> Path | None:
    raw = str(repo_root_value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (config_manager.paths.home / candidate).resolve()
    if candidate.exists():
        return candidate
    normalized = config_manager.normalize_runtime_path(raw)
    if normalized and normalized.exists():
        return normalized
    return candidate if candidate.exists() else None


def _resolve_active_path_record(attachment_context: dict[str, Any]) -> dict[str, Any] | None:
    active_path_key = str(attachment_context.get("active_path_key") or "").strip()
    if not active_path_key:
        return None
    records = list(attachment_context.get("attached_path_records") or [])
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("key") or "").strip() == active_path_key:
            return record
    return None


def _read_json_file_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_specialization_default_scenario_path(
    repo_root: Path,
    manifest_payload: dict[str, Any] | None,
) -> Path | None:
    profile = (manifest_payload or {}).get("benchmarkProfile")
    if not isinstance(profile, dict):
        return None
    raw = str(profile.get("defaultScenario") or "").strip()
    if not raw:
        return None
    raw_path = Path(raw)
    if raw_path.suffix.lower() == ".json":
        candidate = repo_root / raw_path
        return candidate
    stem = raw_path.stem
    return repo_root / "benchmarks" / "scenarios" / f"{stem}.json"


def _resolve_specialization_default_mutation_target_path(
    repo_root: Path,
    manifest_payload: dict[str, Any] | None,
) -> Path | None:
    templates = (manifest_payload or {}).get("templates")
    if not isinstance(templates, list):
        return None
    for template in templates:
        if not isinstance(template, dict):
            continue
        destination = str(template.get("destination") or "").strip()
        if destination:
            return repo_root / destination
    return None


def _resolve_swarm_auth_source(config_manager: ConfigManager, env_ref: str | None) -> str:
    if not env_ref:
        return "unconfigured"
    if env_ref.startswith("local:"):
        return "workspace_env"
    workspace_env = config_manager.read_env_map()
    if str(workspace_env.get(env_ref) or "").strip():
        return "workspace_env"
    if str(os.environ.get(env_ref) or "").strip():
        return "process_env_only"
    return "missing_from_workspace_env"


def _specialization_repo_env_var(path_key: str) -> str:
    normalized = str(path_key).strip().upper().replace("-", "_")
    return f"SPARK_SWARM_SPECIALIZATION_PATH_{normalized}_REPO"


def _build_swarm_doctor_blockers(
    *,
    config_manager: ConfigManager,
    status: SwarmStatus,
    active_path_key: str | None,
    active_path_repo_root: Path | None,
    manifest_path: Path | None,
    scenario_path: Path | None,
    mutation_target_path: Path | None,
    payload_source: str,
) -> list[str]:
    blockers: list[str] = []
    if not status.enabled:
        blockers.append("Spark Swarm is disabled in this workspace config.")
    if not status.api_url:
        blockers.append("Swarm API URL is not configured.")
    if not status.workspace_id:
        blockers.append("Swarm workspace id is not configured.")
    if not status.access_token_env:
        blockers.append("Swarm access token env ref is not configured.")
    else:
        auth_source = _resolve_swarm_auth_source(config_manager, status.access_token_env)
        if auth_source == "missing_from_workspace_env":
            blockers.append(f"Configured Swarm access token env `{status.access_token_env}` is missing from the workspace .env.")
    if active_path_key and active_path_repo_root is None:
        blockers.append(f"Active specialization path `{active_path_key}` does not resolve to a reachable repo root.")
    if active_path_key and manifest_path is not None and not manifest_path.exists():
        blockers.append(f"Active specialization path is missing `{manifest_path.name}`.")
    if scenario_path is not None and not scenario_path.exists():
        blockers.append(f"Default benchmark scenario is missing at `{scenario_path}`.")
    if mutation_target_path is not None and not mutation_target_path.exists():
        blockers.append(f"Default mutation target is missing at `{mutation_target_path}`.")
    if active_path_key and payload_source == "missing":
        blockers.append(
            f"No collective payload is ready for `{active_path_key}`. Run an autoloop or specialization-path run first."
        )
    if not active_path_key and not status.researcher_ready:
        blockers.append("Neither an active specialization path nor a ready Spark Researcher runtime is available.")
    return blockers


def _build_swarm_doctor_recommendations(
    *,
    status: SwarmStatus,
    auth_source: str,
    active_path_key: str | None,
    payload_source: str,
) -> list[str]:
    recommendations: list[str] = []
    if not status.enabled:
        recommendations.append("Re-enable Spark Swarm in `spark.swarm.enabled` before using Telegram Swarm commands.")
    if not status.api_url or not status.workspace_id:
        recommendations.append("Configure `spark.swarm.api_url` and `spark.swarm.workspace_id` for this workspace.")
    if auth_source == "unconfigured":
        recommendations.append("Store the Swarm access token in the workspace `.env` and set `spark.swarm.access_token_env` to that key.")
    elif auth_source == "process_env_only":
        recommendations.append("Copy the Swarm access token into the workspace `.env` so Telegram and background runs share the same auth.")
    elif auth_source == "missing_from_workspace_env":
        recommendations.append("Restore the configured Swarm access token env key in the workspace `.env` or point config at the correct key.")
    if active_path_key and payload_source == "missing":
        recommendations.append(f"Run `/swarm autoloop {active_path_key}` to generate a fresh specialization-path collective payload.")
    elif payload_source != "missing" and status.api_ready:
        recommendations.append("Run `/swarm sync` to upload the latest collective payload to Spark Swarm.")
    elif payload_source != "missing":
        recommendations.append("Fix Swarm auth, then run `/swarm sync`.")
    if active_path_key:
        recommendations.append(f"Use `/swarm session {active_path_key}` to inspect the latest autoloop session and kept changes.")
    return recommendations


def _resolve_active_path_collective_payload(
    config_manager: ConfigManager,
    *,
    attachment_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path] | None:
    context = attachment_context or build_attachment_context(config_manager)
    active_path_key = str(context.get("active_path_key") or "").strip()
    if not active_path_key:
        return None
    record = _resolve_active_path_record(context)
    if not isinstance(record, dict):
        return None
    repo_root = _resolve_attachment_repo_root(config_manager, record.get("repo_root"))
    if repo_root is None:
        return None
    payload_path = repo_root / ".spark-swarm" / "collective-sync.json"
    if not payload_path.exists():
        return None
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if _normalize_collective_payload(payload):
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, payload_path


def _build_collective_payload(
    *,
    config_manager: ConfigManager,
    researcher_root: Path,
    researcher_config_path: Path,
    workspace_id: str,
) -> tuple[dict[str, Any], Path]:
    load_config = _import_researcher_symbol(researcher_root, "spark_researcher.config", "load_config")
    resolve_runtime_root = _import_researcher_symbol(researcher_root, "spark_researcher.paths", "resolve_runtime_root")
    write_payload = _import_researcher_symbol(
        researcher_root,
        "spark_researcher.collective",
        "write_spark_swarm_collective_payload_from_latest",
    )

    config = load_config(researcher_config_path)
    runtime_root = resolve_runtime_root(researcher_config_path)
    with _temporary_env("SPARK_SWARM_WORKSPACE_ID", workspace_id):
        export_info = write_payload(researcher_root, runtime_root, config)
    payload_path = Path(str(export_info["payload_path"]))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if _normalize_collective_payload(payload):
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, payload_path


def _fetch_swarm_api_json(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    route_path: str,
) -> Any:
    status = swarm_status(config_manager, state_db)
    if not status.enabled:
        raise RuntimeError("Spark Swarm bridge is disabled by operator.")
    if not status.api_url:
        raise RuntimeError("Swarm API URL is missing.")
    if not status.workspace_id:
        raise RuntimeError("Swarm workspace id is missing.")

    session = _resolve_swarm_session(config_manager, state_db=state_db)
    if session.auth_state == "expired":
        raise RuntimeError("Swarm access token is expired and no refresh path is configured.")
    if not session.access_token and session.auth_state != "refreshable":
        raise RuntimeError("Swarm access token is missing.")
    if session.auth_state == "refreshable":
        session = _refresh_swarm_access_token(config_manager=config_manager, state_db=state_db, session=session)

    request_path = route_path.format(workspace_id=status.workspace_id)
    try:
        return _get_swarm_api_json(
            api_url=status.api_url,
            route_path=request_path,
            access_token=session.access_token or "",
        )
    except urllib.error.HTTPError as exc:
        body = _read_http_error_body(exc)
        if (
            exc.code == 401
            and _http_error_requires_auth(body)
            and session.refresh_token
            and session.auth_client_key
            and session.supabase_url
        ):
            session = _refresh_swarm_access_token(config_manager=config_manager, state_db=state_db, session=session)
            return _get_swarm_api_json(
                api_url=status.api_url,
                route_path=request_path,
                access_token=session.access_token or "",
            )
        message = f"Swarm API request failed with HTTP {exc.code}."
        if isinstance(body, dict) and body.get("message"):
            message = f"{message} {body['message']}"
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Swarm API: {exc.reason}") from exc


def _post_swarm_api_json(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    route_path: str,
    body: dict[str, Any],
) -> Any:
    status = swarm_status(config_manager, state_db)
    if not status.enabled:
        raise RuntimeError("Spark Swarm bridge is disabled by operator.")
    if not status.api_url:
        raise RuntimeError("Swarm API URL is missing.")
    if not status.workspace_id:
        raise RuntimeError("Swarm workspace id is missing.")

    session = _resolve_swarm_session(config_manager, state_db=state_db)
    if session.auth_state == "expired":
        raise RuntimeError("Swarm access token is expired and no refresh path is configured.")
    if not session.access_token and session.auth_state != "refreshable":
        raise RuntimeError("Swarm access token is missing.")
    if session.auth_state == "refreshable":
        session = _refresh_swarm_access_token(config_manager=config_manager, state_db=state_db, session=session)

    request_path = route_path.format(workspace_id=status.workspace_id)
    try:
        return _request_swarm_api_json(
            api_url=status.api_url,
            route_path=request_path,
            access_token=session.access_token or "",
            method="POST",
            body=body,
        )
    except urllib.error.HTTPError as exc:
        body_payload = _read_http_error_body(exc)
        if (
            exc.code == 401
            and _http_error_requires_auth(body_payload)
            and session.refresh_token
            and session.auth_client_key
            and session.supabase_url
        ):
            session = _refresh_swarm_access_token(config_manager=config_manager, state_db=state_db, session=session)
            return _request_swarm_api_json(
                api_url=status.api_url,
                route_path=request_path,
                access_token=session.access_token or "",
                method="POST",
                body=body,
            )
        message = f"Swarm API request failed with HTTP {exc.code}."
        if isinstance(body_payload, dict) and body_payload.get("message"):
            message = f"{message} {body_payload['message']}"
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Swarm API: {exc.reason}") from exc


def _get_swarm_api_json(
    *,
    api_url: str,
    route_path: str,
    access_token: str,
) -> Any:
    request = urllib.request.Request(
        url=urllib.parse.urljoin(f"{api_url}/", route_path),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _request_swarm_api_json(
    *,
    api_url: str,
    route_path: str,
    access_token: str,
    method: str,
    body: dict[str, Any] | None = None,
) -> Any:
    encoded_body = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if encoded_body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url=urllib.parse.urljoin(f"{api_url}/", route_path),
        data=encoded_body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _post_collective_payload(
    *,
    api_url: str,
    workspace_id: str,
    access_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url=urllib.parse.urljoin(f"{api_url}/", f"api/workspaces/{workspace_id}/collective/sync"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _normalize_collective_payload(payload: dict[str, Any]) -> bool:
    changed = False
    if _normalize_runtime_source(payload):
        changed = True
    if _normalize_contradictions(payload):
        changed = True
    return changed


def _normalize_collective_workspace(payload: dict[str, Any], workspace_id: str) -> bool:
    workspace_value = payload.get("workspaceId")
    if workspace_value is None or (isinstance(workspace_value, str) and not workspace_value.strip()):
        payload["workspaceId"] = workspace_id
        return True
    return False


def _normalize_runtime_source(payload: dict[str, Any]) -> bool:
    runtime_source = payload.get("runtimeSource")
    if not isinstance(runtime_source, dict):
        runtime_source = {}
        payload["runtimeSource"] = runtime_source

    changed = False
    agent_id = str(payload.get("agentId") or "").strip()
    if agent_id and not str(runtime_source.get("sourceInstanceId") or "").strip():
        runtime_source["sourceInstanceId"] = agent_id
        changed = True

    emitted_at = str(payload.get("emittedAt") or "").strip()
    runtime_kind = str(runtime_source.get("kind") or "spark_researcher").strip() or "spark_researcher"
    run_prefix = "spark-researcher" if runtime_kind == "spark_researcher" else runtime_kind.replace("_", "-")
    if emitted_at and not str(runtime_source.get("sourceRunId") or "").strip():
        runtime_source["sourceRunId"] = f"{run_prefix}:{emitted_at}"
        changed = True

    return changed


def _normalize_contradictions(payload: dict[str, Any]) -> bool:
    contradictions = payload.get("contradictions")
    if not isinstance(contradictions, list):
        return False

    changed = False
    for contradiction in contradictions:
        if not isinstance(contradiction, dict):
            continue
        status = str(contradiction.get("status") or "").strip().lower()
        if not status:
            contradiction["status"] = "open"
            changed = True
    return changed


def _record_swarm_sync_state(
    state_db: StateDB,
    *,
    mode: str,
    payload_path: str,
    api_url: str | None,
    workspace_id: str | None,
    accepted: bool | None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str = "swarm_bridge",
) -> None:
    facts = {
        "swarm_operation": "sync",
        "mode": mode,
        "payload_path": payload_path,
        "api_url": api_url,
        "workspace_id": workspace_id,
        "accepted": accepted,
    }
    with state_db.connect() as conn:
        _set_runtime_state(
            conn,
            "swarm:last_sync",
            json.dumps(facts, sort_keys=True),
            guard_strategy=JSON_RICHNESS_MERGE_GUARD,
        )
        if accepted:
            conn.execute("DELETE FROM runtime_state WHERE state_key = ?", ("swarm:last_failure",))
        conn.commit()
    record_event(
        state_db,
        event_type="tool_result_received",
        component="swarm_bridge",
        summary=f"Swarm sync state recorded as {mode}.",
        reason_code=f"swarm_sync_{mode}",
        facts=facts,
        **_swarm_event_context(
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        ),
    )


def _record_swarm_decision_state(
    state_db: StateDB,
    *,
    result: SwarmDecisionResult,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str = "swarm_bridge",
) -> None:
    facts = {
        "swarm_operation": "decision",
        "mode": result.mode,
        "escalate": result.escalate,
        "reason": result.reason,
        "triggers": result.triggers,
        "task": result.task,
        "swarm_available": result.swarm_available,
        "api_ready": result.api_ready,
    }
    with state_db.connect() as conn:
        _set_runtime_state(
            conn,
            "swarm:last_decision",
            json.dumps(facts, sort_keys=True),
            guard_strategy=JSON_RICHNESS_MERGE_GUARD,
        )
        conn.commit()
    record_event(
        state_db,
        event_type="tool_result_received",
        component="swarm_bridge",
        summary=f"Swarm escalation decision recorded as {result.mode}.",
        reason_code=f"swarm_decision_{result.mode}",
        facts=facts,
        **_swarm_event_context(
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        ),
    )


def _record_swarm_failure_state(
    state_db: StateDB,
    *,
    kind: str,
    result: SwarmSyncResult | SwarmDecisionResult,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str = "swarm_bridge",
) -> None:
    with state_db.connect() as conn:
        failure_count = _read_failure_count(conn, "swarm:failure_count")
        _set_runtime_state(conn, "swarm:failure_count", str(failure_count + 1))
        if isinstance(result, SwarmSyncResult):
            payload = {
                "kind": kind,
                "mode": result.mode,
                "message": result.message,
                "api_url": result.api_url,
                "workspace_id": result.workspace_id,
                "payload_path": result.payload_path,
                "response_body": result.response_body,
                "recorded_at": _utc_now_iso(),
            }
        else:
            payload = {
                "kind": kind,
                "mode": result.mode,
                "message": result.reason,
                "api_ready": result.api_ready,
                "swarm_available": result.swarm_available,
                "triggers": result.triggers,
                "recorded_at": _utc_now_iso(),
            }
        _set_runtime_state(
            conn,
            "swarm:last_failure",
            json.dumps(payload, sort_keys=True),
            guard_strategy=JSON_RICHNESS_MERGE_GUARD,
        )
        conn.commit()
    record_event(
        state_db,
        event_type="dispatch_failed",
        component="swarm_bridge",
        summary=f"Swarm {kind} failed in mode {payload.get('mode') or 'unknown'}.",
        reason_code=f"swarm_{kind}_{payload.get('mode') or 'failed'}",
        severity="high",
        facts={
            "swarm_operation": kind,
            "failure_count": failure_count + 1,
            **payload,
        },
        **_swarm_event_context(
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_id,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id=actor_id,
        ),
    )


def _read_swarm_runtime_state(state_db: StateDB) -> dict[str, str]:
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'swarm:%'"
        ).fetchall()
    return {str(row["state_key"]): str(row["value"] or "") for row in rows}


def _loads_json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_int(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _read_failure_count(conn: Any, state_key: str) -> int:
    row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return 0
    try:
        return int(str(row["value"]))
    except ValueError:
        return 0


def _set_runtime_state(
    conn: Any,
    state_key: str,
    value: str,
    *,
    guard_strategy: str | None = None,
) -> None:
    upsert_runtime_state(
        conn,
        state_key=state_key,
        value=value,
        component="swarm_bridge",
        guard_strategy=guard_strategy,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _swarm_event_context(
    *,
    run_id: str | None,
    request_id: str | None,
    trace_ref: str | None,
    channel_id: str | None,
    session_id: str | None,
    human_id: str | None,
    agent_id: str | None,
    actor_id: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "request_id": request_id,
        "trace_ref": trace_ref,
        "channel_id": channel_id,
        "session_id": session_id,
        "human_id": human_id,
        "agent_id": agent_id,
        "actor_id": actor_id,
    }


def _read_typed_swarm_status(state_db: StateDB) -> dict[str, Any]:
    sync_event = _latest_swarm_event_payload(state_db, operation="sync")
    decision_event = _latest_swarm_event_payload(state_db, operation="decision")
    refresh_event = _latest_swarm_event_payload(state_db, operation="auth_refresh")
    return {
        "last_sync": sync_event.get("facts") if sync_event else None,
        "last_decision": decision_event.get("facts") if decision_event else None,
        "last_failure": _latest_swarm_failure_payload(state_db),
        "failure_count": _count_swarm_failures(state_db),
        "last_refresh_at": ((refresh_event or {}).get("facts") or {}).get("refreshed_at"),
        "last_refresh_error": ((refresh_event or {}).get("facts") or {}).get("error"),
    }


def _latest_swarm_event_payload(state_db: StateDB, *, operation: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for event_type in ("tool_result_received", "dispatch_failed"):
        for event in latest_events_by_type(state_db, event_type=event_type, limit=200):
            if str(event.get("component") or "") != "swarm_bridge":
                continue
            facts = event.get("facts_json") or {}
            if not isinstance(facts, dict):
                continue
            if str(facts.get("swarm_operation") or "") != operation:
                continue
            candidates.append(event)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")), reverse=True)
    selected = candidates[0]
    return {
        "event_id": selected.get("event_id"),
        "event_type": selected.get("event_type"),
        "created_at": selected.get("created_at"),
        "facts": selected.get("facts_json") if isinstance(selected.get("facts_json"), dict) else {},
    }


def _latest_swarm_failure_payload(state_db: StateDB) -> dict[str, Any] | None:
    failures = [
        event
        for event in latest_events_by_type(state_db, event_type="dispatch_failed", limit=200)
        if str(event.get("component") or "") == "swarm_bridge"
    ]
    if not failures:
        return None
    failures.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")), reverse=True)
    facts = failures[0].get("facts_json") or {}
    return facts if isinstance(facts, dict) else None


def _count_swarm_failures(state_db: StateDB) -> int:
    failures = [
        event
        for event in latest_events_by_type(state_db, event_type="dispatch_failed", limit=500)
        if str(event.get("component") or "") == "swarm_bridge"
    ]
    return len(failures)


def _import_researcher_symbol(runtime_root: Path, module_name: str, symbol: str):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module(module_name)
    return getattr(module, symbol)


def _refresh_swarm_access_token(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    session: SwarmSession,
) -> SwarmSession:
    if not session.refresh_token:
        raise RuntimeError("Swarm refresh token is missing.")
    if not session.auth_client_key:
        raise RuntimeError("Swarm auth client key is missing.")
    if not session.supabase_url:
        raise RuntimeError("Swarm Supabase URL is missing.")
    request = urllib.request.Request(
        url=urllib.parse.urljoin(f"{session.supabase_url}/", "auth/v1/token?grant_type=refresh_token"),
        data=json.dumps({"refresh_token": session.refresh_token}).encode("utf-8"),
        headers={
            "apikey": session.auth_client_key,
            "Authorization": f"Bearer {session.auth_client_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = _read_http_error_body(exc)
        message = f"Swarm session refresh failed with HTTP {exc.code}."
        if isinstance(body, dict) and body.get("msg"):
            message = f"{message} {body['msg']}"
        _record_swarm_refresh_state(state_db, error=message)
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        message = f"Could not reach Swarm auth endpoint: {exc.reason}"
        _record_swarm_refresh_state(state_db, error=message)
        raise RuntimeError(message) from exc

    payload = json.loads(raw) if raw.strip() else {}
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or session.refresh_token or "").strip()
    if not access_token:
        message = "Swarm refresh completed without returning a new access token."
        _record_swarm_refresh_state(state_db, error=message)
        raise RuntimeError(message)

    access_env = session.access_token_env or "SPARK_SWARM_ACCESS_TOKEN"
    refresh_env = session.refresh_token_env or "SPARK_SWARM_REFRESH_TOKEN"
    config_manager.upsert_env_secret(
        access_env,
        access_token,
        actor_id="swarm_bridge",
        actor_type="service",
        reason_code="swarm_auth_refresh",
        request_source="swarm_bridge.refresh",
    )
    config_manager.set_path(
        "spark.swarm.access_token_env",
        access_env,
        actor_id="swarm_bridge",
        actor_type="service",
        reason_code="swarm_auth_refresh",
        request_source="swarm_bridge.refresh",
    )
    config_manager.upsert_env_secret(
        refresh_env,
        refresh_token,
        actor_id="swarm_bridge",
        actor_type="service",
        reason_code="swarm_auth_refresh",
        request_source="swarm_bridge.refresh",
    )
    config_manager.set_path(
        "spark.swarm.refresh_token_env",
        refresh_env,
        actor_id="swarm_bridge",
        actor_type="service",
        reason_code="swarm_auth_refresh",
        request_source="swarm_bridge.refresh",
    )
    _record_swarm_refresh_state(state_db, refreshed=True)
    return _resolve_swarm_session(config_manager, state_db=state_db)


def _record_swarm_refresh_state(
    state_db: StateDB,
    *,
    refreshed: bool = False,
    error: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    actor_id: str = "swarm_bridge",
) -> None:
    refreshed_at = _utc_now_iso() if refreshed else None
    with state_db.connect() as conn:
        if refreshed:
            _set_runtime_state(conn, "swarm:last_auth_refresh_at", refreshed_at or _utc_now_iso())
            _set_runtime_state(conn, "swarm:last_auth_refresh_error", "")
        if error is not None:
            _set_runtime_state(conn, "swarm:last_auth_refresh_error", error)
        conn.commit()
    if refreshed:
        record_event(
            state_db,
            event_type="tool_result_received",
            component="swarm_bridge",
            summary="Swarm auth refresh succeeded.",
            reason_code="swarm_auth_refresh_succeeded",
            facts={"swarm_operation": "auth_refresh", "refreshed": True, "refreshed_at": refreshed_at},
            **_swarm_event_context(
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id=actor_id,
            ),
        )
    if error is not None:
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="swarm_bridge",
            summary="Swarm auth refresh failed.",
            reason_code="swarm_auth_refresh_failed",
            severity="high",
            facts={"swarm_operation": "auth_refresh", "error": error},
            **_swarm_event_context(
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id=actor_id,
            ),
        )


def _read_local_swarm_env_map(config_manager: ConfigManager) -> dict[str, str]:
    runtime_root, _ = _discover_swarm_runtime_root(config_manager)
    if not runtime_root:
        return {}
    mapping: dict[str, str] = {}
    for path in (
        runtime_root / ".env.alpha",
        runtime_root / "apps" / "api" / ".env",
        runtime_root / "apps" / "web" / ".env",
    ):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            mapping.setdefault(key, value)
    return mapping


def _decode_jwt_claims(token: str | None) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    segment = token.split(".")[1]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _token_expiry_iso(token: str | None) -> str | None:
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(float(exp), tz=timezone.utc).isoformat(timespec="seconds")


def _token_is_expired(token: str | None, *, skew_seconds: int = 60) -> bool:
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    expires_at = datetime.fromtimestamp(float(exp), tz=timezone.utc)
    return expires_at <= datetime.now(timezone.utc).replace(microsecond=0) if skew_seconds <= 0 else (
        expires_at.timestamp() - skew_seconds <= datetime.now(timezone.utc).timestamp()
    )


def _http_error_requires_auth(body: dict[str, Any] | None) -> bool:
    return isinstance(body, dict) and body.get("error") == "authentication_required"


def _read_http_error_body(exc: urllib.error.HTTPError) -> dict[str, Any] | None:
    try:
        raw = exc.read().decode("utf-8")
    except Exception:
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


@contextmanager
def _temporary_env(key: str, value: str):
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
