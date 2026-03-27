from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from spark_intelligence.attachments import (
    activate_chip,
    add_attachment_root,
    attachment_status,
    clear_active_path,
    deactivate_chip,
    list_attachments,
    pin_chip,
    record_chip_hook_execution,
    run_chip_hook,
    run_first_active_chip_hook,
    screen_chip_hook_text,
    set_active_path,
    sync_attachment_snapshot,
    unpin_chip,
)
from spark_intelligence.auth.providers import list_api_key_provider_ids, list_oauth_provider_ids, list_provider_specs
from spark_intelligence.auth.runtime import build_auth_status_report
from spark_intelligence.auth.service import complete_oauth_login, connect_provider, logout_provider, refresh_provider, start_oauth_login
from spark_intelligence.channel.service import (
    add_channel,
    inspect_telegram_bot_token,
    render_telegram_botfather_guide,
    set_channel_status,
    test_configured_telegram_channel,
)
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.gateway.runtime import (
    gateway_outbound_view,
    gateway_simulate_discord_message,
    gateway_simulate_telegram_update,
    gateway_simulate_whatsapp_message,
    gateway_start,
    gateway_status,
    gateway_trace_view,
)
from spark_intelligence.gateway.tracing import read_gateway_traces
from spark_intelligence.gateway.oauth_callback import pending_oauth_redirect_uri, serve_gateway_oauth_callback
from spark_intelligence.identity.service import (
    agent_inspect,
    approve_latest_pairing,
    approve_pairing,
    hold_pairing,
    hold_latest_pairing,
    list_pairings,
    list_sessions,
    pairing_summary,
    peek_latest_pairing_external_user_id,
    revoke_latest_pairing,
    review_pairings,
    revoke_pairing,
    revoke_session,
)
from spark_intelligence.jobs.service import jobs_list, jobs_tick
from spark_intelligence.memory import (
    export_sdk_maintenance_replay,
    export_shadow_replay,
    export_shadow_replay_batch,
    run_memory_sdk_smoke_test,
)
from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.observability.store import build_watchtower_snapshot, close_run, open_run, record_event
from spark_intelligence.ops import (
    build_operator_inbox,
    build_operator_security_report,
    clear_webhook_alert_snooze,
    list_operator_events,
    list_webhook_alert_events,
    list_webhook_alert_snoozes,
    log_operator_event,
    snooze_webhook_alert,
)
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, resolve_researcher_config_path
from spark_intelligence.researcher_bridge import researcher_bridge_status
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import evaluate_swarm_escalation, swarm_status, sync_swarm_collective


@dataclass
class SystemStatus:
    doctor_ok: bool
    gateway_ready: bool
    researcher_available: bool
    swarm_payload_ready: bool
    attachment_warning_count: int
    payload: dict

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = ["Spark Intelligence status"]
        lines.append(f"- doctor: {'ok' if self.doctor_ok else 'degraded'}")
        lines.append(f"- gateway: {'ready' if self.gateway_ready else 'not_ready'}")
        lines.append(f"- researcher: {'available' if self.researcher_available else 'missing'}")
        lines.append(f"- swarm: {'payload_ready' if self.swarm_payload_ready else 'not_ready'}")
        lines.append(
            f"- attachments: {self.payload['attachments']['record_count']} records "
            f"warnings={self.attachment_warning_count}"
        )
        lines.append(
            f"- active chips: {', '.join(self.payload['attachments']['active_chip_keys']) if self.payload['attachments']['active_chip_keys'] else 'none'}"
        )
        lines.append(f"- active path: {self.payload['attachments']['active_path_key'] or 'none'}")
        lines.append(
            f"- providers: {', '.join(self.payload['gateway']['configured_providers']) if self.payload['gateway']['configured_providers'] else 'none'}"
        )
        lines.append(
            f"- channels: {', '.join(self.payload['gateway']['configured_channels']) if self.payload['gateway']['configured_channels'] else 'none'}"
        )
        watchtower = self.payload.get("watchtower") or {}
        if watchtower:
            lines.append(f"- watchtower: {watchtower.get('top_level_state') or 'unknown'}")
            for key in ("ingress_health", "execution_health", "delivery_health", "scheduler_freshness", "environment_parity"):
                dimension = (watchtower.get("health_dimensions") or {}).get(key) or {}
                lines.append(f"- {key}: {dimension.get('state') or 'unknown'}")
            contradiction_counts = (watchtower.get("contradictions") or {}).get("counts") or {}
            lines.append(
                f"- contradictions: open={int(contradiction_counts.get('open') or 0)} "
                f"resolved={int(contradiction_counts.get('resolved') or 0)}"
            )
        runtime_payload = self.payload.get("runtime") or {}
        autostart_payload = runtime_payload.get("autostart") or {}
        lines.append(f"- install profile: {runtime_payload.get('install_profile') or 'none'}")
        lines.append(f"- default gateway mode: {runtime_payload.get('default_gateway_mode') or 'none'}")
        if autostart_payload.get("enabled"):
            lines.append(
                f"- autostart: enabled {autostart_payload.get('platform') or 'unknown'} "
                f"task={autostart_payload.get('task_name') or 'unknown'}"
            )
        else:
            lines.append("- autostart: disabled")
        lines.append(
            f"- provider runtime: {'ok' if self.payload['gateway'].get('provider_runtime_ok') else 'degraded'}"
        )
        if self.payload['gateway'].get('provider_runtime_detail'):
            lines.append(f"- provider runtime detail: {self.payload['gateway']['provider_runtime_detail']}")
        lines.append(
            f"- provider execution: {'ok' if self.payload['gateway'].get('provider_execution_ok') else 'degraded'}"
        )
        if self.payload['gateway'].get('provider_execution_detail'):
            lines.append(f"- provider execution detail: {self.payload['gateway']['provider_execution_detail']}")
        lines.append(
            f"- oauth maintenance: {'ok' if self.payload['gateway'].get('oauth_maintenance_ok') else 'degraded'}"
        )
        if self.payload['gateway'].get('oauth_maintenance_detail'):
            lines.append(f"- oauth maintenance detail: {self.payload['gateway']['oauth_maintenance_detail']}")
        lines.extend(
            f"- repair hint: {hint}"
            for hint in (self.payload['gateway'].get('repair_hints') or [])
        )
        if self.payload['researcher'].get('last_provider_execution_transport'):
            lines.append(
                f"- last provider transport: {self.payload['researcher']['last_provider_execution_transport']}"
            )
        if self.payload['researcher'].get('last_routing_decision'):
            lines.append(f"- last bridge route: {self.payload['researcher']['last_routing_decision']}")
        if self.payload['researcher'].get('last_active_chip_key'):
            chip_suffix = (
                f":{self.payload['researcher']['last_active_chip_task_type']}"
                if self.payload['researcher'].get('last_active_chip_task_type')
                else ""
            )
            lines.append(
                f"- last active chip route: {self.payload['researcher']['last_active_chip_key']}{chip_suffix} "
                f"used={'yes' if self.payload['researcher'].get('last_active_chip_evaluate_used') else 'no'}"
            )
        lines.append(f"- last researcher trace: {self.payload['researcher'].get('last_trace_ref') or 'none'}")
        lines.append(f"- last swarm decision: {(self.payload['swarm'].get('last_decision') or {}).get('mode', 'none')}")
        lines.append(f"- last swarm sync: {(self.payload['swarm'].get('last_sync') or {}).get('mode', 'none')}")
        return "\n".join(lines)


@dataclass
class ConnectionPhaseStatus:
    phase_id: str
    title: str
    status: str
    summary: str
    checks: list[str]
    next_steps: list[str]

    def to_payload(self) -> dict[str, object]:
        return {
            "phase_id": self.phase_id,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "checks": self.checks,
            "next_steps": self.next_steps,
        }


@dataclass
class ConnectionPlanStatus:
    overall_status: str
    current_phase: str
    current_phase_title: str
    phases: list[ConnectionPhaseStatus]

    def to_json(self) -> str:
        return json.dumps(
            {
                "overall_status": self.overall_status,
                "current_phase": self.current_phase,
                "current_phase_title": self.current_phase_title,
                "phases": [phase.to_payload() for phase in self.phases],
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark Intelligence connection plan"]
        lines.append(f"- overall: {self.overall_status}")
        lines.append(f"- current phase: {self.current_phase} {self.current_phase_title}")
        for phase in self.phases:
            lines.append(f"- {phase.phase_id}: {phase.status} {phase.title}")
            lines.append(f"  summary: {phase.summary}")
            lines.extend(f"  check: {check}" for check in phase.checks)
            lines.extend(f"  next: {step}" for step in phase.next_steps)
        return "\n".join(lines)


@dataclass
class RoutingContractStatus:
    payload: dict[str, object]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        researcher = self.payload["researcher"]
        swarm = self.payload["swarm"]
        lines = ["Spark Intelligence routing contract"]
        lines.append(f"- bridge enabled: {'yes' if researcher['bridge_enabled'] else 'no'}")
        lines.append(f"- researcher available: {'yes' if researcher['available'] else 'no'}")
        lines.append(f"- provider runtime: {'ok' if researcher['provider_runtime_ok'] else 'degraded'}")
        lines.append(f"- provider execution: {'ok' if researcher['provider_execution_ok'] else 'degraded'}")
        lines.append(f"- swarm api: {'ready' if swarm['api_ready'] else 'not_ready'}")
        lines.append(f"- swarm api auth: {swarm.get('auth_state') or 'missing'}")
        lines.append(
            f"- conversational fallback policy: "
            f"{'enabled' if researcher['conversational_fallback_enabled'] else 'disabled'} "
            f"(max_chars={researcher['conversational_fallback_max_chars']})"
        )
        lines.append(
            f"- swarm recommendation policy: "
            f"{'enabled' if swarm['auto_recommend_enabled'] else 'disabled'} "
            f"(long_task_word_count={swarm['long_task_word_count']})"
        )
        lines.append(f"- last bridge route: {researcher['last_routing_decision'] or 'none'}")
        if researcher.get("last_active_chip_key"):
            chip_suffix = (
                f":{researcher['last_active_chip_task_type']}"
                if researcher.get("last_active_chip_task_type")
                else ""
            )
            lines.append(
                f"- last active chip route: {researcher['last_active_chip_key']}{chip_suffix} "
                f"used={'yes' if researcher.get('last_active_chip_evaluate_used') else 'no'}"
            )
        lines.append(f"- last swarm decision: {swarm['last_decision_mode'] or 'none'}")
        if swarm.get("last_failure_mode"):
            lines.append(f"- last swarm failure: {swarm['last_failure_mode']}")
        if swarm.get("last_failure_error"):
            lines.append(f"- last swarm failure error: {swarm['last_failure_error']}")
        lines.append("Bridge routes:")
        for route in self.payload["bridge_routes"]:
            lines.append(f"- {route['route']}: {route['when']}")
        lines.append("Swarm escalation:")
        for route in self.payload["swarm_routes"]:
            lines.append(f"- {route['mode']}: {route['when']}")
        lines.append("Operator checks:")
        for command in self.payload["operator_checks"]:
            lines.append(f"- {command}")
        return "\n".join(lines)


@dataclass
class BootstrapTelegramAgentStatus:
    home: str
    provider_id: str
    provider_result: str
    channel_result: str
    setup_notes: list[str]
    gateway_ready: bool
    gateway_detail: str
    repair_hints: list[str]
    run_command: str
    verify_commands: list[str]

    def to_json(self) -> str:
        return json.dumps(
            {
                "home": self.home,
                "provider_id": self.provider_id,
                "provider_result": self.provider_result,
                "channel_result": self.channel_result,
                "setup_notes": self.setup_notes,
                "gateway_ready": self.gateway_ready,
                "gateway_detail": self.gateway_detail,
                "repair_hints": self.repair_hints,
                "run_command": self.run_command,
                "verify_commands": self.verify_commands,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark Intelligence bootstrap: telegram-agent"]
        lines.append(f"- home: {self.home}")
        lines.append(f"- provider: {self.provider_id}")
        lines.append(f"- provider_result: {self.provider_result}")
        lines.append(f"- channel_result: {self.channel_result}")
        lines.append(f"- gateway_ready: {'yes' if self.gateway_ready else 'no'}")
        lines.append(f"- gateway_detail: {self.gateway_detail}")
        if self.setup_notes:
            lines.append("- setup_notes:")
            lines.extend(f"  - {note}" for note in self.setup_notes)
        if self.repair_hints:
            lines.append("- repair_hints:")
            lines.extend(f"  - {hint}" for hint in self.repair_hints)
        lines.append(f"- run_command: {self.run_command}")
        lines.append("- verify_commands:")
        lines.extend(f"  - {command}" for command in self.verify_commands)
        return "\n".join(lines)


def _swarm_last_failure_payload(swarm) -> dict[str, object]:
    payload = getattr(swarm, "last_failure", None)
    return payload if isinstance(payload, dict) else {}


def _swarm_auth_state(swarm) -> str:
    auth_state = getattr(swarm, "auth_state", None)
    if auth_state:
        return str(auth_state)
    last_failure = _swarm_last_failure_payload(swarm)
    response_body = last_failure.get("response_body")
    if isinstance(response_body, dict) and response_body.get("error") == "authentication_required":
        return "auth_rejected"
    if bool(getattr(swarm, "api_ready", False)):
        return "configured"
    return "missing"


def build_connection_plan_status(config_manager: ConfigManager, state_db: StateDB) -> ConnectionPlanStatus:
    gateway = gateway_status(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    swarm = swarm_status(config_manager, state_db)
    attachments = attachment_status(config_manager)
    active_chip_keys = config_manager.get_path("spark.chips.active_keys", default=[]) or []
    active_path_key = config_manager.get_path("spark.specialization_paths.active_path_key")
    autostart_enabled = bool(config_manager.get_path("runtime.autostart.enabled", default=False))
    telegram_configured = "telegram" in gateway.configured_channels
    providers_configured = bool(gateway.configured_providers)

    phase_a_checks = [
        f"telegram_configured={'yes' if telegram_configured else 'no'}",
        f"gateway_ready={'yes' if gateway.ready else 'no'}",
        f"researcher_available={'yes' if researcher.available else 'no'}",
        f"provider_runtime_ok={'yes' if gateway.provider_runtime_ok else 'no'}",
        f"provider_execution_ok={'yes' if gateway.provider_execution_ok else 'no'}",
    ]
    if telegram_configured and gateway.ready and researcher.available and gateway.provider_runtime_ok and gateway.provider_execution_ok:
        phase_a = ConnectionPhaseStatus(
            phase_id="phase-a-telegram-core",
            title="Lock Telegram core",
            status="ready",
            summary="Telegram, Researcher, and the current provider path are live together on the canonical home.",
            checks=phase_a_checks,
            next_steps=[],
        )
    else:
        phase_a_steps: list[str] = []
        if not telegram_configured:
            phase_a_steps.append("spark-intelligence channel telegram-onboard")
        if not providers_configured:
            phase_a_steps.append(
                "spark-intelligence auth connect custom --api-key-env CUSTOM_API_KEY --base-url https://api.minimax.io/v1 --model MiniMax-M2.7"
            )
        if providers_configured and not gateway.provider_runtime_ok:
            phase_a_steps.extend(gateway.repair_hints or ["spark-intelligence auth status"])
        if not researcher.available:
            phase_a_steps.append("spark-intelligence researcher status")
        if telegram_configured and providers_configured and not gateway.ready:
            phase_a_steps.append("spark-intelligence gateway status")
        phase_a = ConnectionPhaseStatus(
            phase_id="phase-a-telegram-core",
            title="Lock Telegram core",
            status="in_progress" if telegram_configured or providers_configured or researcher.available else "blocked",
            summary="The live Telegram shell exists, but the full Telegram + Researcher + provider loop is not completely green yet.",
            checks=phase_a_checks,
            next_steps=phase_a_steps,
        )

    attachment_chip_count = len([record for record in attachments.records if record.kind == "chip"])
    attachment_path_count = len([record for record in attachments.records if record.kind == "path"])
    phase_b_checks = [
        f"active_chip_count={len(active_chip_keys)}",
        f"active_path={'yes' if bool(active_path_key) else 'no'}",
        f"discovered_chip_count={attachment_chip_count}",
        f"discovered_path_count={attachment_path_count}",
    ]
    if active_chip_keys and active_path_key:
        phase_b = ConnectionPhaseStatus(
            phase_id="phase-b-specialization",
            title="Activate Spark specialization",
            status="ready",
            summary="At least one chip set and one specialization path are active in the runtime state.",
            checks=phase_b_checks,
            next_steps=[],
        )
    else:
        phase_b = ConnectionPhaseStatus(
            phase_id="phase-b-specialization",
            title="Activate Spark specialization",
            status="in_progress" if attachment_chip_count or attachment_path_count or active_chip_keys or active_path_key else "blocked",
            summary="Spark can already discover external chips and paths, but the Telegram agent is not yet shaped by active assets.",
            checks=phase_b_checks,
            next_steps=[
                "spark-intelligence attachments list --kind chip",
                "spark-intelligence attachments activate-chip <chip-key>",
                "spark-intelligence attachments list --kind path",
                "spark-intelligence attachments set-path <path-key>",
                "spark-intelligence attachments snapshot --json",
                "spark-intelligence agent inspect",
            ],
        )

    phase_c_checks = [
        f"payload_ready={'yes' if swarm.payload_ready else 'no'}",
        f"api_ready={'yes' if swarm.api_ready else 'no'}",
        f"api_auth={_swarm_auth_state(swarm)}",
        f"api_url={swarm.api_url or 'missing'}",
        f"workspace_id={swarm.workspace_id or 'missing'}",
        f"access_token_env={swarm.access_token_env or 'missing'}",
        f"refresh_token_env={getattr(swarm, 'refresh_token_env', None) or 'missing'}",
    ]
    auth_state = _swarm_auth_state(swarm)
    if swarm.payload_ready and swarm.api_ready and auth_state == "configured":
        phase_c = ConnectionPhaseStatus(
            phase_id="phase-c-swarm-api",
            title="Connect Spark Swarm",
            status="ready",
            summary="Swarm is payload-ready and API-connected, so live sync and escalation work can start safely.",
            checks=phase_c_checks,
            next_steps=[],
        )
    else:
        phase_c_summary = "Swarm plumbing exists, but the bridge is not yet fully API-connected and proven reachable."
        phase_c_steps = [
            "spark-intelligence swarm status",
            "spark-intelligence swarm configure --api-url <url> --workspace-id <workspace-id> --access-token <token>",
            "spark-intelligence swarm sync --dry-run",
            "spark-intelligence swarm sync",
        ]
        if auth_state == "refreshable":
            phase_c_summary = "Swarm payload build is ready, and the local session is refreshable, but a fresh sync still needs to re-establish hosted acceptance."
            phase_c_steps = [
                "spark-intelligence swarm status",
                "spark-intelligence swarm sync --dry-run",
                "spark-intelligence swarm sync",
            ]
        elif auth_state == "expired":
            phase_c_summary = "Swarm payload build is ready, but the local session is expired and no refresh path is configured yet."
            phase_c_steps = [
                "spark-intelligence swarm status",
                "spark-intelligence swarm configure --access-token <fresh-token> --refresh-token <refresh-token> --auth-client-key-env <env>",
                "spark-intelligence swarm sync --dry-run",
                "spark-intelligence swarm sync",
            ]
        elif auth_state == "auth_rejected":
            phase_c_summary = "Swarm payload build is ready, but the hosted API rejected the current access token or session."
            phase_c_steps = [
                "spark-intelligence swarm status",
                "spark-intelligence swarm configure --access-token <fresh-token> --refresh-token <refresh-token> --auth-client-key-env <env>",
                "spark-intelligence swarm sync --dry-run",
                "spark-intelligence swarm sync",
            ]
        phase_c = ConnectionPhaseStatus(
            phase_id="phase-c-swarm-api",
            title="Connect Spark Swarm",
            status="in_progress" if swarm.payload_ready or swarm.runtime_root or swarm.api_url else "blocked",
            summary=phase_c_summary,
            checks=phase_c_checks,
            next_steps=phase_c_steps,
        )

    phase_d_prereq_ready = phase_a.status == "ready" and phase_b.status == "ready" and phase_c.status == "ready"
    phase_d = ConnectionPhaseStatus(
        phase_id="phase-d-runtime-routing",
        title="Lock runtime routing contract",
        status="in_progress" if phase_d_prereq_ready or phase_a.status == "ready" else "blocked",
        summary=(
            "The next runtime contract is to make Researcher-first, direct-provider fallback, and Swarm escalation explicit and operator-visible."
            if phase_d_prereq_ready or phase_a.status == "ready"
            else "Runtime routing should be locked only after the live Telegram core and system connections above are in place."
        ),
        checks=[
            f"telegram_core={phase_a.status}",
            f"specialization={phase_b.status}",
            f"swarm_api={phase_c.status}",
            f"last_provider_transport={researcher.last_provider_execution_transport or 'none'}",
            f"last_bridge_route={researcher.last_routing_decision or 'none'}",
        ],
        next_steps=[
            "spark-intelligence connect route-policy",
            "docs/SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md",
            "spark-intelligence gateway traces --limit 20",
        ],
    )

    install_profile = config_manager.get_path("runtime.install.profile")
    default_gateway_mode = config_manager.get_path("runtime.run.default_gateway_mode")
    phase_e = ConnectionPhaseStatus(
        phase_id="phase-e-productization",
        title="Productize install and run",
        status=(
            "in_progress"
            if install_profile or phase_a.status == "ready"
            else "blocked"
        ),
        summary=(
            "A supported bootstrap profile and native autostart wrapper now exist for the Telegram plus API-key path, but fresh-operator install validation still remains."
            if install_profile and autostart_enabled
            else "A supported bootstrap profile now exists for the Telegram plus API-key path, but fresh-operator install validation and a cleaner always-on run wrapper still remain."
            if install_profile
            else "Bootstrap, setup, and continuous gateway mode exist, but the installer and always-on run story are not yet fully packaged for another operator."
        ),
        checks=[
            "setup_command=yes",
            "continuous_gateway=yes",
            f"install_profile={install_profile or 'none'}",
            f"default_gateway_mode={default_gateway_mode or 'none'}",
            f"autostart_enabled={'yes' if autostart_enabled else 'no'}",
            "fresh_operator_install=validated_by_clean_home_smoke",
        ],
        next_steps=[
            "spark-intelligence bootstrap telegram-agent",
            "spark-intelligence install-autostart",
            "spark-intelligence connect status",
            "docs/SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md",
        ],
    )

    phases = [phase_a, phase_b, phase_c, phase_d, phase_e]
    current = next((phase for phase in phases if phase.status != "ready"), phases[-1])
    overall_status = "ready" if all(phase.status == "ready" for phase in phases) else "in_progress"
    return ConnectionPlanStatus(
        overall_status=overall_status,
        current_phase=current.phase_id,
        current_phase_title=current.title,
        phases=phases,
    )


def build_routing_contract_status(config_manager: ConfigManager, state_db: StateDB) -> RoutingContractStatus:
    gateway = gateway_status(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    swarm = swarm_status(config_manager, state_db)
    payload = {
        "researcher": {
            "bridge_enabled": researcher.enabled,
            "available": researcher.available,
            "provider_runtime_ok": gateway.provider_runtime_ok,
            "provider_execution_ok": gateway.provider_execution_ok,
            "conversational_fallback_enabled": bool(
                config_manager.get_path("spark.researcher.routing.conversational_fallback_enabled", default=True)
            ),
            "conversational_fallback_max_chars": int(
                config_manager.get_path("spark.researcher.routing.conversational_fallback_max_chars", default=240)
            ),
            "last_routing_decision": researcher.last_routing_decision,
            "last_active_chip_key": researcher.last_active_chip_key,
            "last_active_chip_task_type": researcher.last_active_chip_task_type,
            "last_active_chip_evaluate_used": researcher.last_active_chip_evaluate_used,
            "last_provider_transport": researcher.last_provider_execution_transport,
        },
        "swarm": {
            "enabled": swarm.enabled,
            "payload_ready": swarm.payload_ready,
            "api_ready": swarm.api_ready,
            "auth_state": _swarm_auth_state(swarm),
            "auto_recommend_enabled": bool(
                config_manager.get_path("spark.swarm.routing.auto_recommend_enabled", default=True)
            ),
            "long_task_word_count": int(
                config_manager.get_path("spark.swarm.routing.long_task_word_count", default=40)
            ),
            "last_decision_mode": (swarm.last_decision or {}).get("mode"),
            "last_failure_mode": _swarm_last_failure_payload(swarm).get("mode"),
            "last_failure_error": (
                (_swarm_last_failure_payload(swarm).get("response_body") or {}).get("error")
                if isinstance(_swarm_last_failure_payload(swarm).get("response_body"), dict)
                else None
            ),
        },
        "bridge_routes": [
            {
                "route": "provider_execution",
                "when": (
                    "Use external Spark Researcher plus provider execution when a provider is active and "
                    "its transport supports direct HTTP or CLI execution."
                ),
            },
            {
                "route": "researcher_advisory",
                "when": (
                    "Use external Spark Researcher advisory output when the runtime is available but no "
                    "executable provider path is active."
                ),
            },
            {
                "route": "provider_fallback_chat",
                "when": (
                    "Use direct provider chat for short under-supported conversational traffic when the "
                    "active provider uses direct_http transport."
                ),
            },
            {
                "route": "provider_resolution_failed",
                "when": "Fail closed when provider auth or runtime resolution breaks before bridge execution starts.",
            },
            {
                "route": "bridge_disabled",
                "when": "Return the explicit disabled response when the operator turns the bridge off.",
            },
            {
                "route": "bridge_error",
                "when": "Fail closed when the external Researcher bridge raises during execution.",
            },
            {
                "route": "stub",
                "when": "Use the local stub response only when no external Spark Researcher runtime is configured or discovered.",
            },
        ],
        "swarm_routes": [
            {
                "mode": "manual_recommended",
                "when": (
                    "Recommend Swarm when the task explicitly asks for swarm or delegation, parallel multi-agent work, "
                    "deep research, orchestration, long tasks, or multi-chip context."
                ),
            },
            {
                "mode": "hold_local",
                "when": "Keep the task on the primary agent when no strong escalation signals are present.",
            },
            {
                "mode": "unavailable",
                "when": "Do not recommend Swarm when the local payload path is not ready.",
            },
            {
                "mode": "disabled",
                "when": "Do not recommend Swarm when the operator has disabled the Swarm bridge.",
            },
        ],
        "operator_checks": [
            "spark-intelligence connect status",
            "spark-intelligence gateway traces --limit 20",
            "spark-intelligence gateway outbound --limit 20",
            'spark-intelligence swarm evaluate --task "<task>"',
        ],
    }
    return RoutingContractStatus(payload=payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark-intelligence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Bootstrap config and state")
    setup_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    setup_parser.add_argument("--researcher-root", help="Connect a local spark-researcher repo")
    setup_parser.add_argument("--researcher-config", help="Override spark-researcher config path")
    setup_parser.add_argument("--swarm-runtime-root", help="Connect a local spark-swarm repo")
    setup_parser.add_argument("--swarm-api-url", help="Set the Spark Swarm API base URL")
    setup_parser.add_argument("--swarm-workspace-id", help="Set the Spark Swarm workspace id")
    setup_parser.add_argument("--swarm-access-token", help="Store a Spark Swarm access token")
    setup_parser.add_argument(
        "--swarm-access-token-env",
        default="SPARK_SWARM_ACCESS_TOKEN",
        help="Env var name used to store the Spark Swarm access token",
    )

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Run one supported end-to-end bootstrap profile")
    bootstrap_subparsers = bootstrap_parser.add_subparsers(dest="bootstrap_command", required=True)
    bootstrap_telegram_parser = bootstrap_subparsers.add_parser(
        "telegram-agent",
        help="Bootstrap the supported Telegram + API-key provider + continuous-gateway path",
    )
    bootstrap_telegram_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    bootstrap_telegram_parser.add_argument("--researcher-root", help="Connect a local spark-researcher repo")
    bootstrap_telegram_parser.add_argument("--researcher-config", help="Override spark-researcher config path")
    bootstrap_telegram_parser.add_argument("--swarm-runtime-root", help="Connect a local spark-swarm repo")
    bootstrap_telegram_parser.add_argument("--swarm-api-url", help="Set the Spark Swarm API base URL")
    bootstrap_telegram_parser.add_argument("--swarm-workspace-id", help="Set the Spark Swarm workspace id")
    bootstrap_telegram_parser.add_argument("--swarm-access-token", help="Store a Spark Swarm access token")
    bootstrap_telegram_parser.add_argument(
        "--swarm-access-token-env",
        default="SPARK_SWARM_ACCESS_TOKEN",
        help="Env var name used to store the Spark Swarm access token",
    )
    bootstrap_telegram_parser.add_argument(
        "--provider",
        choices=list_api_key_provider_ids(),
        default="custom",
        help="API-key-backed provider to bootstrap",
    )
    bootstrap_telegram_parser.add_argument("--api-key", help="Provider API key to store in the home env")
    bootstrap_telegram_parser.add_argument("--api-key-env", help="Provider API key env var name")
    bootstrap_telegram_parser.add_argument("--model", help="Default model id for the provider")
    bootstrap_telegram_parser.add_argument("--base-url", help="Custom provider base URL")
    bootstrap_telegram_parser.add_argument("--bot-token", help="Telegram bot token to store in the home env")
    bootstrap_telegram_parser.add_argument("--bot-token-env", help="Existing env var name holding the Telegram bot token")
    bootstrap_telegram_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed Telegram user id")
    bootstrap_telegram_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default="pairing",
        help="Inbound DM authorization mode",
    )
    bootstrap_telegram_parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip remote Telegram token validation during bootstrap",
    )
    bootstrap_telegram_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    install_autostart_parser = subparsers.add_parser(
        "install-autostart",
        help="Install the supported native always-on wrapper for the foreground gateway",
    )
    install_autostart_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    install_autostart_parser.add_argument("--task-name", help="Windows Task Scheduler task name override")

    uninstall_autostart_parser = subparsers.add_parser(
        "uninstall-autostart",
        help="Remove the supported native always-on wrapper",
    )
    uninstall_autostart_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    uninstall_autostart_parser.add_argument("--task-name", help="Windows Task Scheduler task name override")

    doctor_parser = subparsers.add_parser("doctor", help="Run environment and state checks")
    doctor_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    status_parser = subparsers.add_parser("status", help="Show unified runtime, bridge, and attachment state")
    status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    connect_parser = subparsers.add_parser("connect", help="Inspect phased system-connection progress")
    connect_subparsers = connect_parser.add_subparsers(dest="connect_command", required=True)
    connect_status_parser = connect_subparsers.add_parser(
        "status",
        help="Show the current connection phase, blockers, and next steps",
    )
    connect_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    connect_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    connect_route_policy_parser = connect_subparsers.add_parser(
        "route-policy",
        help="Show the current bridge and Swarm routing contract",
    )
    connect_route_policy_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    connect_route_policy_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    connect_set_route_policy_parser = connect_subparsers.add_parser(
        "set-route-policy",
        help="Update the operator-facing routing policy knobs",
    )
    connect_set_route_policy_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    connect_set_route_policy_parser.add_argument(
        "--conversational-fallback",
        choices=["on", "off"],
        help="Enable or disable direct provider fallback for short under-supported conversational traffic",
    )
    connect_set_route_policy_parser.add_argument(
        "--conversational-max-chars",
        type=int,
        help="Maximum message length that still qualifies for conversational direct-provider fallback",
    )
    connect_set_route_policy_parser.add_argument(
        "--swarm-auto-recommend",
        choices=["on", "off"],
        help="Enable or disable automatic Swarm recommendation when escalation triggers are present",
    )
    connect_set_route_policy_parser.add_argument(
        "--swarm-long-task-word-count",
        type=int,
        help="Word-count threshold that marks a task as long enough to trigger Swarm recommendation",
    )

    operator_parser = subparsers.add_parser("operator", help="Safe operator controls for bridges and pairing review")
    operator_subparsers = operator_parser.add_subparsers(dest="operator_command", required=True)
    operator_set_bridge_parser = operator_subparsers.add_parser("set-bridge", help="Enable or disable a bridge")
    operator_set_bridge_parser.add_argument("bridge", choices=["researcher", "swarm"])
    operator_set_bridge_parser.add_argument("mode", choices=["enabled", "disabled"])
    operator_set_bridge_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_set_bridge_parser.add_argument("--reason", help="Short audit reason for this change")
    operator_review_pairings_parser = operator_subparsers.add_parser("review-pairings", help="Show pending and held pairing requests")
    operator_review_pairings_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_review_pairings_parser.add_argument("--channel-id", choices=["telegram", "discord", "whatsapp"], help="Filter review queue to one channel")
    operator_review_pairings_parser.add_argument("--status", choices=["pending", "held"], help="Filter review queue to one status")
    operator_review_pairings_parser.add_argument("--limit", type=int, help="Limit the number of rows shown")
    operator_review_pairings_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_pairing_summary_parser = operator_subparsers.add_parser(
        "pairing-summary",
        help="Show compact pairing state for one channel",
    )
    operator_pairing_summary_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_pairing_summary_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_pairing_summary_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_approve_pairing_parser = operator_subparsers.add_parser("approve-pairing", help="Approve a pending or held pairing")
    operator_approve_pairing_parser.add_argument("channel_id")
    operator_approve_pairing_parser.add_argument("external_user_id")
    operator_approve_pairing_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_approve_pairing_parser.add_argument("--display-name", help="Friendly display name")
    operator_approve_pairing_parser.add_argument("--reason", help="Short audit reason for this approval")
    operator_approve_latest_parser = operator_subparsers.add_parser(
        "approve-latest",
        help="Approve the newest pending pairing for a channel",
    )
    operator_approve_latest_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_approve_latest_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_approve_latest_parser.add_argument("--display-name", help="Friendly display name override")
    operator_approve_latest_parser.add_argument("--reason", help="Short audit reason for this approval")
    operator_hold_latest_parser = operator_subparsers.add_parser(
        "hold-latest",
        help="Hold the newest pending pairing for a channel",
    )
    operator_hold_latest_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_hold_latest_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_hold_latest_parser.add_argument("--reason", help="Short audit reason for holding this request")
    operator_revoke_latest_parser = operator_subparsers.add_parser(
        "revoke-latest",
        help="Revoke the newest pending or held pairing for a channel",
    )
    operator_revoke_latest_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_revoke_latest_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_revoke_latest_parser.add_argument("--reason", help="Short audit reason for revoking this request")
    operator_hold_pairing_parser = operator_subparsers.add_parser("hold-pairing", help="Mark a pairing request as held")
    operator_hold_pairing_parser.add_argument("channel_id")
    operator_hold_pairing_parser.add_argument("external_user_id")
    operator_hold_pairing_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_hold_pairing_parser.add_argument("--reason", help="Short audit reason for holding this request")
    operator_set_channel_parser = operator_subparsers.add_parser("set-channel", help="Set channel ingress state")
    operator_set_channel_parser.add_argument("channel_id")
    operator_set_channel_parser.add_argument("mode", choices=["enabled", "paused", "disabled"])
    operator_set_channel_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_set_channel_parser.add_argument("--reason", help="Short audit reason for this change")
    operator_history_parser = operator_subparsers.add_parser("history", help="Show recent operator actions")
    operator_history_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_history_parser.add_argument("--limit", type=int, default=20, help="Number of events to show")
    operator_history_parser.add_argument("--action", help="Filter history to one action")
    operator_history_parser.add_argument("--target-kind", help="Filter history to one target kind")
    operator_history_parser.add_argument("--contains", help="Filter history by target, reason, or details substring")
    operator_history_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_inbox_parser = operator_subparsers.add_parser("inbox", help="Show actionable operator items")
    operator_inbox_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_inbox_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_security_parser = operator_subparsers.add_parser("security", help="Show recent security-relevant operator state")
    operator_security_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_security_parser.add_argument("--limit", type=int, default=100, help="Number of recent trace/audit events to scan")
    operator_security_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_snooze_webhook_parser = operator_subparsers.add_parser(
        "snooze-webhook-alert",
        help="Temporarily suppress one webhook alert family from operator surfaces",
    )
    operator_snooze_webhook_parser.add_argument("event", choices=list_webhook_alert_events())
    operator_snooze_webhook_parser.add_argument("--minutes", type=int, default=60, help="Snooze duration in minutes")
    operator_snooze_webhook_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_snooze_webhook_parser.add_argument("--reason", help="Short audit reason for this snooze")
    operator_list_webhook_snoozes_parser = operator_subparsers.add_parser(
        "webhook-alert-snoozes",
        help="Show active webhook alert snoozes",
    )
    operator_list_webhook_snoozes_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_list_webhook_snoozes_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_clear_webhook_snooze_parser = operator_subparsers.add_parser(
        "clear-webhook-alert-snooze",
        help="Remove one active webhook alert snooze",
    )
    operator_clear_webhook_snooze_parser.add_argument("event", choices=list_webhook_alert_events())
    operator_clear_webhook_snooze_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_clear_webhook_snooze_parser.add_argument("--reason", help="Short audit reason for clearing this snooze")

    gateway_parser = subparsers.add_parser("gateway", help="Gateway operations")
    gateway_subparsers = gateway_parser.add_subparsers(dest="gateway_command", required=True)
    gateway_start_parser = gateway_subparsers.add_parser("start", help="Start foreground gateway")
    gateway_start_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_start_parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    gateway_start_parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep polling in the foreground until interrupted or --max-cycles is reached",
    )
    gateway_start_parser.add_argument("--max-cycles", type=int, help="Limit gateway poll cycles")
    gateway_start_parser.add_argument(
        "--poll-timeout-seconds",
        type=int,
        default=5,
        help="Telegram polling timeout in seconds",
    )
    gateway_status_parser = gateway_subparsers.add_parser("status", help="Inspect gateway readiness")
    gateway_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_oauth_callback_parser = gateway_subparsers.add_parser(
        "oauth-callback",
        help="Serve one loopback OAuth callback through the gateway surface and complete login automatically",
    )
    gateway_oauth_callback_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_oauth_callback_parser.add_argument("--redirect-uri", help="Explicit redirect URI to bind for the callback")
    gateway_oauth_callback_parser.add_argument("--provider", choices=list_oauth_provider_ids(), help="Restrict callback completion to one provider")
    gateway_oauth_callback_parser.add_argument("--timeout-seconds", type=int, default=120, help="Maximum time to wait for the OAuth callback")
    gateway_oauth_callback_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_simulate_parser = gateway_subparsers.add_parser(
        "simulate-telegram-update",
        help="Simulate one Telegram update through normalization and authorization routing",
    )
    gateway_simulate_parser.add_argument("update_file", help="Path to a Telegram update JSON file")
    gateway_simulate_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_simulate_discord_parser = gateway_subparsers.add_parser(
        "simulate-discord-message",
        help="Simulate one Discord DM message through normalization and authorization routing",
    )
    gateway_simulate_discord_parser.add_argument("message_file", help="Path to a Discord message JSON file")
    gateway_simulate_discord_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_discord_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_simulate_whatsapp_parser = gateway_subparsers.add_parser(
        "simulate-whatsapp-message",
        help="Simulate one WhatsApp DM message through normalization and authorization routing",
    )
    gateway_simulate_whatsapp_parser.add_argument("message_file", help="Path to a WhatsApp message JSON file")
    gateway_simulate_whatsapp_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_whatsapp_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_traces_parser = gateway_subparsers.add_parser("traces", help="Show recent gateway traces")
    gateway_traces_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_traces_parser.add_argument("--limit", type=int, default=20, help="Number of trace events to show")
    gateway_traces_parser.add_argument("--channel-id", help="Filter trace events by channel id")
    gateway_traces_parser.add_argument("--event", help="Filter trace events by event name")
    gateway_traces_parser.add_argument("--user", help="Filter trace events by user id or chat id")
    gateway_traces_parser.add_argument("--decision", help="Filter trace events by decision")
    gateway_traces_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_outbound_parser = gateway_subparsers.add_parser("outbound", help="Show recent outbound audit records")
    gateway_outbound_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_outbound_parser.add_argument("--limit", type=int, default=20, help="Number of outbound events to show")
    gateway_outbound_parser.add_argument("--channel-id", help="Filter outbound events by channel id")
    gateway_outbound_parser.add_argument("--event", help="Filter outbound events by event name")
    gateway_outbound_parser.add_argument("--user", help="Filter outbound events by user id or chat id")
    gateway_outbound_parser.add_argument("--decision", help="Filter outbound events by decision")
    gateway_outbound_parser.add_argument(
        "--delivery",
        choices=["ok", "failed"],
        help="Filter outbound events by delivery result",
    )
    gateway_outbound_parser.add_argument("--contains", help="Filter outbound preview text by substring")
    gateway_outbound_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    channel_parser = subparsers.add_parser("channel", help="Manage channel adapters")
    channel_subparsers = channel_parser.add_subparsers(dest="channel_command", required=True)
    channel_add_parser = channel_subparsers.add_parser("add", help="Add a channel adapter")
    channel_add_parser.add_argument("channel_kind", choices=["telegram", "discord", "whatsapp"], help="Adapter kind")
    channel_add_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_add_parser.add_argument("--bot-token", help="Adapter bot token")
    channel_add_parser.add_argument("--webhook-secret", help="Static webhook secret for adapter HTTP ingress")
    channel_add_parser.add_argument("--webhook-secret-env", help="Env var name used to store the webhook secret")
    channel_add_parser.add_argument("--webhook-verify-token", help="Verification token for adapter webhook handshake flows")
    channel_add_parser.add_argument("--webhook-verify-token-env", help="Env var name used to store the webhook verification token")
    channel_add_parser.add_argument("--interaction-public-key", help="Discord interactions public key for signed HTTP ingress")
    channel_add_parser.add_argument(
        "--allow-legacy-message-webhook",
        action="store_true",
        help="Enable the legacy Discord message-shaped webhook compatibility path.",
    )
    channel_add_parser.add_argument(
        "--disable-legacy-message-webhook",
        action="store_true",
        help="Disable the legacy Discord message-shaped webhook compatibility path and clear its auth ref.",
    )
    channel_add_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed adapter user id")
    channel_add_parser.add_argument(
        "--clear-allowed-users",
        action="store_true",
        help="Clear any existing configured allowed users before applying this update.",
    )
    channel_add_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default=None,
        help="Inbound DM authorization mode",
    )
    channel_add_parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip remote token validation. Useful only for offline setup or scripted recovery.",
    )
    channel_telegram_onboard_parser = channel_subparsers.add_parser(
        "telegram-onboard",
        help="Guide or complete BotFather-based Telegram setup",
    )
    channel_telegram_onboard_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_telegram_onboard_parser.add_argument("--bot-token", help="Telegram bot token from BotFather")
    channel_telegram_onboard_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed Telegram user id")
    channel_telegram_onboard_parser.add_argument(
        "--clear-allowed-users",
        action="store_true",
        help="Clear any existing configured allowed users before applying this update.",
    )
    channel_telegram_onboard_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default=None,
        help="Inbound DM authorization mode",
    )
    channel_telegram_onboard_parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the Telegram token without storing channel config",
    )
    channel_telegram_onboard_parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip remote token validation. Useful only for offline setup or scripted recovery.",
    )
    channel_test_parser = channel_subparsers.add_parser(
        "test",
        help="Run lightweight diagnostics for one configured channel",
    )
    channel_test_parser.add_argument("channel_kind", choices=["telegram"], help="Configured channel to test")
    channel_test_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_test_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    attachments_parser = subparsers.add_parser("attachments", help="Inspect and manage chip/path attachment roots")
    attachments_subparsers = attachments_parser.add_subparsers(dest="attachments_command", required=True)
    attachments_status_parser = attachments_subparsers.add_parser("status", help="Scan chip and path attachments")
    attachments_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    attachments_list_parser = attachments_subparsers.add_parser("list", help="List discovered chip/path attachments")
    attachments_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_list_parser.add_argument("--kind", choices=["all", "chip", "path"], default="all")
    attachments_list_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    attachments_add_root_parser = attachments_subparsers.add_parser("add-root", help="Add a chip or path root")
    attachments_add_root_parser.add_argument("target", choices=["chips", "paths"], help="Which attachment root list to update")
    attachments_add_root_parser.add_argument("root", help="Root path to add")
    attachments_add_root_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_snapshot_parser = attachments_subparsers.add_parser("snapshot", help="Build and persist the attachment snapshot")
    attachments_snapshot_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_snapshot_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    attachments_activate_chip_parser = attachments_subparsers.add_parser("activate-chip", help="Mark a chip active")
    attachments_activate_chip_parser.add_argument("chip_key", help="Chip key to activate")
    attachments_activate_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_deactivate_chip_parser = attachments_subparsers.add_parser("deactivate-chip", help="Remove a chip from active and pinned state")
    attachments_deactivate_chip_parser.add_argument("chip_key", help="Chip key to deactivate")
    attachments_deactivate_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_pin_chip_parser = attachments_subparsers.add_parser("pin-chip", help="Pin a chip so it remains active")
    attachments_pin_chip_parser.add_argument("chip_key", help="Chip key to pin")
    attachments_pin_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_unpin_chip_parser = attachments_subparsers.add_parser("unpin-chip", help="Remove a chip from pinned state")
    attachments_unpin_chip_parser.add_argument("chip_key", help="Chip key to unpin")
    attachments_unpin_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_set_path_parser = attachments_subparsers.add_parser("set-path", help="Set the active specialization path")
    attachments_set_path_parser.add_argument("path_key", help="Specialization path key")
    attachments_set_path_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_clear_path_parser = attachments_subparsers.add_parser("clear-path", help="Clear the active specialization path")
    attachments_clear_path_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_run_hook_parser = attachments_subparsers.add_parser(
        "run-hook",
        help="Run a manifest-backed chip hook using the standard spark-hook-io.v1 contract",
    )
    attachments_run_hook_parser.add_argument("hook", choices=["evaluate", "suggest", "packets", "watchtower"])
    attachments_run_hook_parser.add_argument("--chip-key", help="Chip key to run. Defaults to the first active chip that supports the hook.")
    attachments_run_hook_parser.add_argument("--payload-json", default="{}", help="JSON payload to send to the hook")
    attachments_run_hook_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_run_hook_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    auth_parser = subparsers.add_parser("auth", help="Manage model providers")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_providers_parser = auth_subparsers.add_parser("providers", help="List supported provider/auth options")
    auth_providers_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_connect_parser = auth_subparsers.add_parser("connect", help="Connect a model provider")
    auth_connect_parser.add_argument("provider", choices=list_api_key_provider_ids())
    auth_connect_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_connect_parser.add_argument("--api-key", help="API key for the provider")
    auth_connect_parser.add_argument("--api-key-env", help="Existing or target env var name for the provider secret")
    auth_connect_parser.add_argument("--model", help="Default model id")
    auth_connect_parser.add_argument("--base-url", help="Custom provider base URL")
    auth_login_parser = auth_subparsers.add_parser("login", help="Start or complete an OAuth provider login or reconnect flow")
    auth_login_parser.add_argument("provider", choices=list_oauth_provider_ids())
    auth_login_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_login_parser.add_argument("--redirect-uri", help="Override the default OAuth callback URI")
    auth_login_parser.add_argument("--callback-url", help="Full callback URL captured after OAuth approval")
    auth_login_parser.add_argument("--listen", action="store_true", help="Wait for the loopback OAuth callback and complete login automatically")
    auth_login_parser.add_argument("--timeout-seconds", type=int, default=120, help="Maximum time to wait for a loopback OAuth callback")
    auth_login_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_logout_parser = auth_subparsers.add_parser("logout", help="Revoke locally stored OAuth credentials for a provider")
    auth_logout_parser.add_argument("provider", choices=list_oauth_provider_ids())
    auth_logout_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_logout_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_refresh_parser = auth_subparsers.add_parser("refresh", help="Refresh locally stored OAuth credentials for a provider immediately")
    auth_refresh_parser.add_argument("provider", choices=list_oauth_provider_ids())
    auth_refresh_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_refresh_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_status_parser = auth_subparsers.add_parser("status", help="Show configured auth profiles, expiry state, and secret readiness")
    auth_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    researcher_parser = subparsers.add_parser("researcher", help="Inspect Spark Researcher bridge state")
    researcher_subparsers = researcher_parser.add_subparsers(dest="researcher_command", required=True)
    researcher_status_parser = researcher_subparsers.add_parser("status", help="Show Spark Researcher bridge readiness")
    researcher_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    researcher_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    memory_parser = subparsers.add_parser("memory", help="Export or inspect Spark memory shadow artifacts")
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_export_parser = memory_subparsers.add_parser(
        "export-shadow-replay",
        help="Export a Spark shadow replay JSON file for domain-chip-memory validation",
    )
    memory_export_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_export_parser.add_argument("--write", help="Replay JSON output path")
    memory_export_parser.add_argument("--conversation-limit", type=int, default=20, help="Maximum conversations to export")
    memory_export_parser.add_argument("--event-limit", type=int, default=2000, help="Maximum Builder events to scan")
    memory_export_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for validation")
    memory_export_parser.add_argument("--skip-validate", action="store_true", help="Write the replay without calling validate-spark-shadow-replay")
    memory_export_parser.add_argument("--run-report", action="store_true", help="Run run-spark-shadow-report after export")
    memory_export_parser.add_argument("--report-write", help="Optional output path for the generated shadow report JSON")
    memory_export_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_export_batch_parser = memory_subparsers.add_parser(
        "export-shadow-replay-batch",
        help="Export a directory of Spark shadow replay JSON files for domain-chip-memory batch validation",
    )
    memory_export_batch_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_export_batch_parser.add_argument("--output-dir", help="Replay batch output directory")
    memory_export_batch_parser.add_argument("--conversation-limit", type=int, default=20, help="Maximum conversations to export")
    memory_export_batch_parser.add_argument("--event-limit", type=int, default=2000, help="Maximum Builder events to scan")
    memory_export_batch_parser.add_argument("--conversations-per-file", type=int, default=10, help="Maximum conversations per replay file")
    memory_export_batch_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for validation")
    memory_export_batch_parser.add_argument("--skip-validate", action="store_true", help="Write the replay directory without calling validate-spark-shadow-replay-batch")
    memory_export_batch_parser.add_argument("--run-report", action="store_true", help="Run run-spark-shadow-report-batch after export")
    memory_export_batch_parser.add_argument("--report-write", help="Optional output path for the generated batch shadow report JSON")
    memory_export_batch_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_maintenance_parser = memory_subparsers.add_parser(
        "export-sdk-maintenance-replay",
        help="Export accepted Spark memory writes as a Domain Chip Memory SDK maintenance replay JSON file",
    )
    memory_maintenance_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_maintenance_parser.add_argument("--write", help="Maintenance replay JSON output path")
    memory_maintenance_parser.add_argument("--event-limit", type=int, default=2000, help="Maximum Builder events to scan")
    memory_maintenance_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for maintenance replay")
    memory_maintenance_parser.add_argument("--run-report", action="store_true", help="Run run-sdk-maintenance-report after export")
    memory_maintenance_parser.add_argument("--report-write", help="Optional output path for the generated SDK maintenance report JSON")
    memory_maintenance_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_direct_smoke_parser = memory_subparsers.add_parser(
        "direct-smoke",
        help="Run an in-process Spark -> Domain Chip Memory write/read smoke test without changing persisted config",
    )
    memory_direct_smoke_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_direct_smoke_parser.add_argument("--sdk-module", help="Override the SDK module for this smoke run")
    memory_direct_smoke_parser.add_argument("--subject", default="human:smoke:test", help="Structured memory subject to write and read")
    memory_direct_smoke_parser.add_argument("--predicate", default="system.memory.smoke", help="Structured memory predicate to write and read")
    memory_direct_smoke_parser.add_argument("--value", default="ok", help="Structured memory value to write and read")
    memory_direct_smoke_parser.add_argument("--no-cleanup", action="store_true", help="Leave the smoke key in memory instead of deleting it after the read")
    memory_direct_smoke_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    config_parser = subparsers.add_parser("config", help="Inspect and update config values")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="Show config or one config path")
    config_show_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    config_show_parser.add_argument("--path", help="Dot-path under config.yaml")
    config_show_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    config_set_parser = config_subparsers.add_parser("set", help="Set one config value")
    config_set_parser.add_argument("path", help="Dot-path under config.yaml")
    config_set_parser.add_argument("value", help="YAML-parsed value to store")
    config_set_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    config_unset_parser = config_subparsers.add_parser("unset", help="Remove one config value")
    config_unset_parser.add_argument("path", help="Dot-path under config.yaml")
    config_unset_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    swarm_parser = subparsers.add_parser("swarm", help="Inspect and sync Spark Swarm bridge state")
    swarm_subparsers = swarm_parser.add_subparsers(dest="swarm_command", required=True)
    swarm_status_parser = swarm_subparsers.add_parser("status", help="Show Spark Swarm bridge readiness")
    swarm_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    swarm_configure_parser = swarm_subparsers.add_parser("configure", help="Configure Spark Swarm API settings")
    swarm_configure_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_configure_parser.add_argument("--api-url", help="Base URL for the Spark Swarm API")
    swarm_configure_parser.add_argument("--supabase-url", help="Supabase auth URL used for Swarm session refresh")
    swarm_configure_parser.add_argument("--workspace-id", help="Workspace id used by Spark Swarm")
    swarm_configure_parser.add_argument("--access-token", help="Access token for the Spark Swarm API")
    swarm_configure_parser.add_argument(
        "--access-token-env",
        default="SPARK_SWARM_ACCESS_TOKEN",
        help="Env var name used to store the Spark Swarm access token",
    )
    swarm_configure_parser.add_argument("--refresh-token", help="Refresh token for the Spark Swarm API session")
    swarm_configure_parser.add_argument(
        "--refresh-token-env",
        default="SPARK_SWARM_REFRESH_TOKEN",
        help="Env var name used to store the Spark Swarm refresh token",
    )
    swarm_configure_parser.add_argument("--auth-client-key", help="Supabase client key used for refresh exchange")
    swarm_configure_parser.add_argument(
        "--auth-client-key-env",
        default="SPARK_SWARM_AUTH_CLIENT_KEY",
        help="Env var name used to store the Swarm auth client key",
    )
    swarm_configure_parser.add_argument("--runtime-root", help="Override local spark-swarm repo path")
    swarm_sync_parser = swarm_subparsers.add_parser(
        "sync",
        help="Build the latest Spark Researcher collective payload and sync it to Spark Swarm",
    )
    swarm_sync_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_sync_parser.add_argument("--dry-run", action="store_true", help="Build the payload without uploading it")
    swarm_sync_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    swarm_evaluate_parser = swarm_subparsers.add_parser(
        "evaluate",
        help="Evaluate whether a task should be escalated to Spark Swarm",
    )
    swarm_evaluate_parser.add_argument("task", help="Task description to evaluate")
    swarm_evaluate_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_evaluate_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    jobs_parser = subparsers.add_parser("jobs", help="Inspect and run operator-driven maintenance jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    jobs_tick_parser = jobs_subparsers.add_parser("tick", help="Run due maintenance work once, including OAuth refresh repair")
    jobs_tick_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    jobs_list_parser = jobs_subparsers.add_parser("list", help="List known jobs and the latest maintenance result")
    jobs_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    agent_parser = subparsers.add_parser("agent", help="Inspect agent and workspace state")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_inspect_parser = agent_subparsers.add_parser("inspect", help="Inspect current workspace identity state")
    agent_inspect_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_inspect_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    pairing_parser = subparsers.add_parser("pairings", help="Manage pairings")
    pairing_subparsers = pairing_parser.add_subparsers(dest="pairings_command", required=True)
    pairing_list_parser = pairing_subparsers.add_parser("list", help="List pairings")
    pairing_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    pairing_approve_parser = pairing_subparsers.add_parser("approve", help="Approve a pairing")
    pairing_approve_parser.add_argument("channel_id", help="Channel installation id")
    pairing_approve_parser.add_argument("external_user_id", help="External user id")
    pairing_approve_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    pairing_approve_parser.add_argument("--display-name", help="Friendly display name")
    pairing_revoke_parser = pairing_subparsers.add_parser("revoke", help="Revoke a pairing")
    pairing_revoke_parser.add_argument("channel_id", help="Channel installation id")
    pairing_revoke_parser.add_argument("external_user_id", help="External user id")
    pairing_revoke_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    sessions_parser = subparsers.add_parser("sessions", help="Inspect and revoke session bindings")
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_list_parser = sessions_subparsers.add_parser("list", help="List sessions")
    sessions_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    sessions_revoke_parser = sessions_subparsers.add_parser("revoke", help="Revoke a session")
    sessions_revoke_parser.add_argument("session_id", help="Canonical session id")
    sessions_revoke_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    return parser


def handle_setup(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    created = config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    setup_notes = _apply_setup_integrations(config_manager, args)
    print(f"Spark Intelligence home: {config_manager.paths.home}")
    if created:
        print("Created config, env, and state bootstrap.")
    else:
        print("Existing config and env preserved; verified state bootstrap.")
    if setup_notes:
        print("Setup integrations:")
        for note in setup_notes:
            print(f"  - {note}")
    print("Next steps:")
    print("  1. spark-intelligence auth connect openai --api-key <key> --model <model>")
    print("  2. spark-intelligence channel telegram-onboard")
    print("  3. spark-intelligence connect status")
    print("  4. spark-intelligence doctor")
    print("  5. spark-intelligence gateway start")
    print("Optional Spark hookups:")
    print("  - spark-intelligence swarm status")
    print("  - spark-intelligence swarm sync --dry-run")
    return 0


def _sync_secret_from_env_if_present(config_manager: ConfigManager, env_name: str | None) -> bool:
    if not env_name:
        return False
    env_map = config_manager.read_env_map()
    if env_map.get(env_name):
        return False
    process_value = os.environ.get(env_name)
    if not process_value:
        return False
    config_manager.upsert_env_secret(env_name, process_value)
    return True


def _resolve_bootstrap_secret(
    *,
    config_manager: ConfigManager,
    explicit_value: str | None,
    env_name: str | None,
    label: str,
) -> str | None:
    if explicit_value:
        return explicit_value
    if not env_name:
        return None
    _sync_secret_from_env_if_present(config_manager, env_name)
    env_map = config_manager.read_env_map()
    value = env_map.get(env_name)
    if value:
        return value
    raise ValueError(f"{label} env '{env_name}' is not set in the home env or process environment.")


def handle_bootstrap_telegram_agent(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    created = config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    setup_notes = _apply_setup_integrations(config_manager, args)
    if created:
        setup_notes.insert(0, "created config, env, and state bootstrap")
    else:
        setup_notes.insert(0, "verified existing config, env, and state bootstrap")

    try:
        bot_token = _resolve_bootstrap_secret(
            config_manager=config_manager,
            explicit_value=args.bot_token,
            env_name=args.bot_token_env,
            label="Telegram bot token",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not bot_token:
        print("Provide --bot-token or --bot-token-env for bootstrap telegram-agent.", file=sys.stderr)
        return 2

    if args.api_key_env:
        _sync_secret_from_env_if_present(config_manager, args.api_key_env)

    if args.provider == "custom" and not args.base_url:
        print("Provider 'custom' requires --base-url during bootstrap.", file=sys.stderr)
        return 2

    provider_result = connect_provider(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        model=args.model,
        base_url=args.base_url,
    )

    profile = None
    if not args.skip_validate:
        try:
            profile = inspect_telegram_bot_token(bot_token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    channel_result = add_channel(
        config_manager=config_manager,
        state_db=state_db,
        channel_kind="telegram",
        bot_token=bot_token,
        allowed_users=args.allowed_user,
        pairing_mode=args.pairing_mode,
        status="enabled",
        metadata={"bot_profile": profile.to_dict()} if profile else None,
    )
    config_manager.set_path("runtime.install.profile", "telegram-agent")
    config_manager.set_path("runtime.run.default_gateway_mode", "continuous")

    gateway = gateway_status(config_manager, state_db)
    run_command = f"spark-intelligence gateway start --home {config_manager.paths.home} --continuous"
    verify_commands = [
        f"spark-intelligence connect status --home {config_manager.paths.home}",
        f"spark-intelligence status --home {config_manager.paths.home}",
        f"spark-intelligence gateway status --home {config_manager.paths.home}",
        run_command,
    ]
    status = BootstrapTelegramAgentStatus(
        home=str(config_manager.paths.home),
        provider_id=args.provider,
        provider_result=provider_result,
        channel_result=channel_result,
        setup_notes=setup_notes,
        gateway_ready=gateway.ready,
        gateway_detail=gateway.provider_runtime_detail or gateway.provider_execution_detail or "ready",
        repair_hints=list(gateway.repair_hints or []),
        run_command=run_command,
        verify_commands=verify_commands,
    )
    print(status.to_json() if args.json else status.to_text())
    return 0 if gateway.ready else 1


def _default_autostart_task_name(config_manager: ConfigManager) -> str:
    return f"Spark Intelligence Gateway ({config_manager.paths.home.name})"


def _resolve_autostart_task_name(config_manager: ConfigManager, override: str | None) -> str:
    if override:
        return override
    configured = config_manager.get_path("runtime.autostart.task_name")
    if configured:
        return str(configured)
    return _default_autostart_task_name(config_manager)


def _build_gateway_autostart_command(config_manager: ConfigManager) -> str:
    command_parts = [
        sys.executable,
        "-m",
        "spark_intelligence.cli",
        "gateway",
        "start",
        "--home",
        str(config_manager.paths.home),
        "--continuous",
    ]
    return subprocess.list2cmdline(command_parts)


def _windows_startup_wrapper_path(config_manager: ConfigManager, task_name: str) -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot resolve the Windows Startup folder.")
    safe_name = "".join(char if char.isalnum() or char in ("-", "_", " ") else "_" for char in task_name).strip()
    filename = f"{safe_name or 'Spark Intelligence Gateway'}.cmd"
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / filename
    )


def _install_windows_startup_wrapper(config_manager: ConfigManager, task_name: str, task_command: str) -> Path:
    wrapper_path = _windows_startup_wrapper_path(config_manager, task_name)
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    wrapper_path.write_text(f"@echo off\r\n{task_command}\r\n", encoding="utf-8")
    return wrapper_path


def handle_install_autostart(args: argparse.Namespace) -> int:
    if os.name != "nt":
        print("install-autostart is currently implemented only for Windows Task Scheduler.", file=sys.stderr)
        return 1
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    gateway = gateway_status(config_manager, state_db)
    if not gateway.ready:
        print("Gateway is not ready; refusing to install autostart.", file=sys.stderr)
        for hint in gateway.repair_hints or []:
            print(hint, file=sys.stderr)
        return 1

    task_name = _resolve_autostart_task_name(config_manager, args.task_name)
    task_command = _build_gateway_autostart_command(config_manager)
    task_user = ConfigManager._windows_current_principal()
    installed_platform = "windows_task_scheduler"
    wrapper_path: Path | None = None
    try:
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                task_name,
                "/SC",
                "ONLOGON",
                "/RL",
                "LIMITED",
                "/RU",
                task_user,
                "/TR",
                task_command,
                "/F",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "Task Scheduler install failed.").strip()
        if "Access is denied" not in message:
            print(message, file=sys.stderr)
            return 1
        wrapper_path = _install_windows_startup_wrapper(config_manager, task_name, task_command)
        installed_platform = "windows_startup_folder"

    config_manager.set_path("runtime.autostart.enabled", True)
    config_manager.set_path("runtime.autostart.platform", installed_platform)
    config_manager.set_path("runtime.autostart.task_name", task_name)
    config_manager.set_path("runtime.autostart.command", task_command)
    print("Installed autostart.")
    print(f"- platform: {installed_platform}")
    print(f"- task_name: {task_name}")
    if installed_platform == "windows_task_scheduler":
        print(f"- task_user: {task_user}")
    if wrapper_path:
        print(f"- startup_wrapper: {wrapper_path}")
    print(f"- command: {task_command}")
    return 0


def handle_uninstall_autostart(args: argparse.Namespace) -> int:
    if os.name != "nt":
        print("uninstall-autostart is currently implemented only for Windows Task Scheduler.", file=sys.stderr)
        return 1
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    task_name = _resolve_autostart_task_name(config_manager, args.task_name)
    platform = config_manager.get_path("runtime.autostart.platform")
    if platform == "windows_startup_folder":
        wrapper_path = _windows_startup_wrapper_path(config_manager, task_name)
        if wrapper_path.exists():
            wrapper_path.unlink()
    else:
        try:
            subprocess.run(
                [
                    "schtasks",
                    "/Delete",
                    "/TN",
                    task_name,
                    "/F",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            print((exc.stderr or exc.stdout or "Task Scheduler uninstall failed.").strip(), file=sys.stderr)
            return 1

    config_manager.set_path("runtime.autostart.enabled", False)
    config_manager.set_path("runtime.autostart.platform", None)
    config_manager.set_path("runtime.autostart.task_name", None)
    config_manager.set_path("runtime.autostart.command", None)
    print("Removed autostart.")
    print(f"- platform: {platform or 'windows_task_scheduler'}")
    print(f"- task_name: {task_name}")
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    report = run_doctor(config_manager, state_db)
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 0 if report.ok else 1


def handle_operator_set_bridge(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    config_manager.set_path(f"spark.{args.bridge}.enabled", args.mode == "enabled")
    log_operator_event(
        state_db=state_db,
        action="set_bridge",
        target_kind="bridge",
        target_ref=args.bridge,
        reason=args.reason,
        details={"enabled": args.mode == "enabled"},
    )
    print(f"Set spark.{args.bridge}.enabled = {json.dumps(args.mode == 'enabled')}")
    return 0


def handle_operator_review_pairings(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = review_pairings(
        state_db,
        channel_id=args.channel_id,
        status=args.status,
        limit=args.limit,
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_pairing_summary(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = pairing_summary(state_db=state_db, channel_id=args.channel_id)
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_approve_pairing(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = approve_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
        external_user_id=args.external_user_id,
        display_name=args.display_name,
    )
    log_operator_event(
        state_db=state_db,
        action="approve_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{args.external_user_id}",
        reason=args.reason,
        details={"display_name": args.display_name},
    )
    print(result)
    return 0


def handle_operator_approve_latest(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=args.channel_id,
        statuses=("pending",),
    )
    result = approve_latest_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
        display_name=args.display_name,
    )
    log_operator_event(
        state_db=state_db,
        action="approve_latest_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{external_user_id}",
        reason=args.reason,
        details={"display_name": args.display_name},
    )
    print(result)
    return 0


def handle_operator_hold_latest(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=args.channel_id,
        statuses=("pending",),
    )
    result = hold_latest_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
    )
    log_operator_event(
        state_db=state_db,
        action="hold_latest_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{external_user_id}",
        reason=args.reason,
    )
    print(result)
    return 0


def handle_operator_revoke_latest(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=args.channel_id,
        statuses=("pending", "held"),
    )
    result = revoke_latest_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
    )
    log_operator_event(
        state_db=state_db,
        action="revoke_latest_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{external_user_id}",
        reason=args.reason,
    )
    print(result)
    return 0


def handle_operator_hold_pairing(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = hold_pairing(state_db=state_db, channel_id=args.channel_id, external_user_id=args.external_user_id)
    log_operator_event(
        state_db=state_db,
        action="hold_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{args.external_user_id}",
        reason=args.reason,
    )
    print(result)
    return 0


def handle_operator_set_channel(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = set_channel_status(
        config_manager=config_manager,
        state_db=state_db,
        channel_id=args.channel_id,
        status=args.mode,
    )
    log_operator_event(
        state_db=state_db,
        action="set_channel",
        target_kind="channel",
        target_ref=args.channel_id,
        reason=args.reason,
        details={"status": args.mode},
    )
    print(result)
    return 0


def handle_operator_history(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = list_operator_events(
        state_db,
        limit=args.limit,
        action=args.action,
        target_kind=args.target_kind,
        contains=args.contains,
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_inbox(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = build_operator_inbox(config_manager=config_manager, state_db=state_db)
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_security(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = build_operator_security_report(config_manager=config_manager, state_db=state_db, limit=args.limit)
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_snooze_webhook_alert(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    snooze_until = snooze_webhook_alert(
        state_db=state_db,
        event_name=args.event,
        minutes=args.minutes,
        reason=args.reason,
    )
    log_operator_event(
        state_db=state_db,
        action="snooze_webhook_alert",
        target_kind="webhook_alert",
        target_ref=args.event,
        reason=args.reason,
        details={"minutes": args.minutes, "snooze_until": snooze_until},
    )
    print(f"Snoozed webhook alert '{args.event}' until {snooze_until}.")
    return 0


def handle_operator_webhook_alert_snoozes(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = list_webhook_alert_snoozes(
        state_db=state_db,
        traces=read_gateway_traces(config_manager, limit=100),
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_clear_webhook_alert_snooze(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    cleared = clear_webhook_alert_snooze(state_db=state_db, event_name=args.event)
    log_operator_event(
        state_db=state_db,
        action="clear_webhook_alert_snooze",
        target_kind="webhook_alert",
        target_ref=args.event,
        reason=args.reason,
        details={"removed": cleared is not None, "cleared_snooze": cleared},
    )
    if cleared is not None:
        detail_suffix = ""
        if cleared.get("snoozed_at") and cleared.get("snooze_until"):
            detail_suffix = f" (set at {cleared['snoozed_at']}, until {cleared['snooze_until']})"
        print(f"Cleared webhook alert snooze for '{args.event}'{detail_suffix}.")
    else:
        print(f"No active webhook alert snooze found for '{args.event}'.")
    return 0


def handle_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    doctor_report = run_doctor(config_manager, state_db)
    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    gateway = gateway_status(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    swarm = swarm_status(config_manager, state_db)
    attachments = attachment_status(config_manager)
    watchtower = build_watchtower_snapshot(state_db)
    active_chip_keys = config_manager.get_path("spark.chips.active_keys", default=[]) or []
    active_path_key = config_manager.get_path("spark.specialization_paths.active_path_key")
    autostart_payload = {
        "enabled": bool(config_manager.get_path("runtime.autostart.enabled", default=False)),
        "platform": config_manager.get_path("runtime.autostart.platform"),
        "task_name": config_manager.get_path("runtime.autostart.task_name"),
        "command": config_manager.get_path("runtime.autostart.command"),
    }

    payload = {
        "doctor": {"ok": doctor_report.ok, "checks": [{"name": check.name, "ok": check.ok, "detail": check.detail} for check in doctor_report.checks]},
        "auth": json.loads(auth_report.to_json()),
        "gateway": json.loads(gateway.to_json()),
        "researcher": json.loads(researcher.to_json()),
        "swarm": json.loads(swarm.to_json()),
        "runtime": {
            "install_profile": config_manager.get_path("runtime.install.profile"),
            "default_gateway_mode": config_manager.get_path("runtime.run.default_gateway_mode"),
            "autostart": autostart_payload,
        },
        "attachments": {
            "record_count": len(attachments.records),
            "warning_count": len(attachments.warnings),
            "chip_count": len([record for record in attachments.records if record.kind == "chip"]),
            "path_count": len([record for record in attachments.records if record.kind == "path"]),
            "active_chip_keys": active_chip_keys,
            "active_path_key": active_path_key,
            "snapshot_path": str(config_manager.paths.home / "attachments.snapshot.json"),
        },
        "watchtower": watchtower,
    }
    status = SystemStatus(
        doctor_ok=doctor_report.ok,
        gateway_ready=gateway.ready,
        researcher_available=researcher.available,
        swarm_payload_ready=swarm.payload_ready,
        attachment_warning_count=len(attachments.warnings),
        payload=payload,
    )
    print(status.to_json() if args.json else status.to_text())
    return 0 if doctor_report.ok else 1


def handle_connect_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    status = build_connection_plan_status(config_manager=config_manager, state_db=state_db)
    print(status.to_json() if args.json else status.to_text())
    return 0


def handle_connect_route_policy(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    status = build_routing_contract_status(config_manager=config_manager, state_db=state_db)
    print(status.to_json() if args.json else status.to_text())
    return 0


def handle_connect_set_route_policy(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    updates: list[str] = []
    if args.conversational_fallback:
        enabled = args.conversational_fallback == "on"
        config_manager.set_path("spark.researcher.routing.conversational_fallback_enabled", enabled)
        updates.append(f"spark.researcher.routing.conversational_fallback_enabled={json.dumps(enabled)}")
    if args.conversational_max_chars is not None:
        if args.conversational_max_chars <= 0:
            print("--conversational-max-chars must be greater than zero.", file=sys.stderr)
            return 2
        config_manager.set_path(
            "spark.researcher.routing.conversational_fallback_max_chars",
            args.conversational_max_chars,
        )
        updates.append(
            "spark.researcher.routing.conversational_fallback_max_chars="
            f"{args.conversational_max_chars}"
        )
    if args.swarm_auto_recommend:
        enabled = args.swarm_auto_recommend == "on"
        config_manager.set_path("spark.swarm.routing.auto_recommend_enabled", enabled)
        updates.append(f"spark.swarm.routing.auto_recommend_enabled={json.dumps(enabled)}")
    if args.swarm_long_task_word_count is not None:
        if args.swarm_long_task_word_count <= 0:
            print("--swarm-long-task-word-count must be greater than zero.", file=sys.stderr)
            return 2
        config_manager.set_path("spark.swarm.routing.long_task_word_count", args.swarm_long_task_word_count)
        updates.append(
            f"spark.swarm.routing.long_task_word_count={args.swarm_long_task_word_count}"
        )
    if not updates:
        print("No route-policy updates requested.", file=sys.stderr)
        return 2
    print("Updated route policy:")
    for update in updates:
        print(f"- {update}")
    return 0


def handle_gateway_start(args: argparse.Namespace) -> int:
    if args.once and args.continuous:
        print("Choose either --once or --continuous, not both.", file=sys.stderr)
        return 2
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = gateway_start(
        config_manager,
        state_db,
        once=args.once,
        continuous=args.continuous,
        max_cycles=args.max_cycles,
        poll_timeout_seconds=args.poll_timeout_seconds,
    )
    print(report.text)
    return 0 if report.ok else 1


def handle_gateway_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    status = gateway_status(config_manager, state_db)
    if args.json:
        print(status.to_json())
    else:
        print(status.to_text())
    return 0 if status.ready else 1


def handle_gateway_oauth_callback(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    redirect_uri = args.redirect_uri or pending_oauth_redirect_uri(
        state_db=state_db,
        provider_id=args.provider,
    )
    if not redirect_uri:
        print("No pending OAuth callback redirect URI was found. Start auth login first or pass --redirect-uri.", file=sys.stderr)
        return 1
    try:
        result = serve_gateway_oauth_callback(
            config_manager=config_manager,
            state_db=state_db,
            redirect_uri=redirect_uri,
            expected_provider=args.provider,
            timeout_seconds=args.timeout_seconds,
        )
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_gateway_simulate_telegram_update(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        gateway_simulate_telegram_update(
            config_manager,
            state_db,
            Path(args.update_file),
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_simulate_discord_message(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        gateway_simulate_discord_message(
            config_manager,
            state_db,
            Path(args.message_file),
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_simulate_whatsapp_message(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        gateway_simulate_whatsapp_message(
            config_manager,
            state_db,
            Path(args.message_file),
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_traces(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    print(
        gateway_trace_view(
            config_manager,
            limit=args.limit,
            channel_id=args.channel_id,
            event=args.event,
            user=args.user,
            decision=args.decision,
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_outbound(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    print(
        gateway_outbound_view(
            config_manager,
            limit=args.limit,
            channel_id=args.channel_id,
            event=args.event,
            user=args.user,
            decision=args.decision,
            delivery=args.delivery,
            contains=args.contains,
            as_json=args.json,
        )
    )
    return 0


def handle_channel_add(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    if args.clear_allowed_users and args.allowed_user:
        print("Cannot combine --clear-allowed-users with --allowed-user.", file=sys.stderr)
        return 2
    if args.allow_legacy_message_webhook and args.disable_legacy_message_webhook:
        print(
            "Cannot combine --allow-legacy-message-webhook with --disable-legacy-message-webhook.",
            file=sys.stderr,
        )
        return 2
    existing_record = config_manager.get_path(f"channels.records.{args.channel_kind}", default={}) or {}
    existing_allowed_users = existing_record.get("allowed_users") if isinstance(existing_record, dict) else []
    existing_pairing_mode = existing_record.get("pairing_mode") if isinstance(existing_record, dict) else None
    existing_legacy_message_webhook = bool(existing_record.get("allow_legacy_message_webhook")) if isinstance(existing_record, dict) else False
    if args.clear_allowed_users:
        effective_allowed_users: list[str] = []
    else:
        effective_allowed_users = args.allowed_user or (existing_allowed_users if isinstance(existing_allowed_users, list) else [])
    effective_pairing_mode = args.pairing_mode or (str(existing_pairing_mode) if existing_pairing_mode else "pairing")
    metadata: dict[str, object] | None = None
    validation_note: str | None = None
    if args.allow_legacy_message_webhook or args.disable_legacy_message_webhook:
        if args.channel_kind != "discord":
            print(
                "--allow-legacy-message-webhook and --disable-legacy-message-webhook are only supported for Discord.",
                file=sys.stderr,
            )
            return 2
    effective_legacy_message_webhook = existing_legacy_message_webhook
    if args.allow_legacy_message_webhook:
        effective_legacy_message_webhook = True
    if args.disable_legacy_message_webhook:
        effective_legacy_message_webhook = False
    if args.webhook_secret:
        if args.channel_kind == "discord":
            if not effective_legacy_message_webhook:
                print(
                    "Discord legacy message webhooks require --allow-legacy-message-webhook.",
                    file=sys.stderr,
                )
                return 2
            env_key = args.webhook_secret_env or "DISCORD_WEBHOOK_SECRET"
        elif args.channel_kind == "whatsapp":
            env_key = args.webhook_secret_env or "WHATSAPP_WEBHOOK_SECRET"
        else:
            print("--webhook-secret is only supported for webhook-based adapters.", file=sys.stderr)
            return 2
        config_manager.upsert_env_secret(env_key, args.webhook_secret)
        metadata = {**(metadata or {}), "webhook_auth_ref": env_key}
    if args.webhook_verify_token:
        if args.channel_kind != "whatsapp":
            print("--webhook-verify-token is only supported for WhatsApp.", file=sys.stderr)
            return 2
        verify_env_key = args.webhook_verify_token_env or "WHATSAPP_WEBHOOK_VERIFY_TOKEN"
        config_manager.upsert_env_secret(verify_env_key, args.webhook_verify_token)
        metadata = {**(metadata or {}), "webhook_verify_token_ref": verify_env_key}
    if args.channel_kind == "discord":
        if args.disable_legacy_message_webhook:
            metadata = {**(metadata or {}), "webhook_auth_ref": None}
        if (
            args.allow_legacy_message_webhook
            or args.disable_legacy_message_webhook
            or existing_legacy_message_webhook
        ):
            metadata = {**(metadata or {}), "allow_legacy_message_webhook": effective_legacy_message_webhook}
    if args.interaction_public_key:
        if args.channel_kind != "discord":
            print("--interaction-public-key is only supported for Discord.", file=sys.stderr)
            return 2
        metadata = {**(metadata or {}), "interaction_public_key": args.interaction_public_key.strip()}
    if args.channel_kind == "telegram" and args.bot_token and not args.skip_validate:
        try:
            profile = inspect_telegram_bot_token(args.bot_token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print("", file=sys.stderr)
            print(
                render_telegram_botfather_guide(
                    allowed_users=effective_allowed_users,
                    pairing_mode=effective_pairing_mode,
                ),
                file=sys.stderr,
            )
            return 1
        metadata = {"bot_profile": profile.to_dict()}
        validation_note = (
            f"Validated Telegram bot @{profile.username or 'unknown'} "
            f"(id={profile.bot_id}, first_name={profile.first_name or 'unknown'})."
        )
    result = add_channel(
        config_manager=config_manager,
        state_db=state_db,
        channel_kind=args.channel_kind,
        bot_token=args.bot_token,
        allowed_users=effective_allowed_users,
        pairing_mode=effective_pairing_mode,
        metadata=metadata,
    )
    if validation_note:
        print(validation_note)
    print(result)
    if args.channel_kind == "telegram":
        print("Next Telegram steps:")
        print("  1. Open Telegram and send /start to the bot from the account you want to pair.")
        print("  2. Run spark-intelligence gateway start")
        print("  3. Use spark-intelligence operator review-pairings if pairing approval is required.")
    return 0


def handle_channel_telegram_onboard(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    if args.clear_allowed_users and args.allowed_user:
        print("Cannot combine --clear-allowed-users with --allowed-user.", file=sys.stderr)
        return 2
    existing_record = config_manager.get_path("channels.records.telegram", default={}) or {}
    existing_allowed_users = existing_record.get("allowed_users") if isinstance(existing_record, dict) else []
    existing_pairing_mode = existing_record.get("pairing_mode") if isinstance(existing_record, dict) else None
    existing_status = existing_record.get("status") if isinstance(existing_record, dict) else None
    if args.clear_allowed_users:
        effective_allowed_users: list[str] = []
    else:
        effective_allowed_users = args.allowed_user or (existing_allowed_users if isinstance(existing_allowed_users, list) else [])
    effective_pairing_mode = args.pairing_mode or (str(existing_pairing_mode) if existing_pairing_mode else "pairing")
    effective_status = str(existing_status) if existing_status else "enabled"

    if not args.bot_token:
        print(
            render_telegram_botfather_guide(
                allowed_users=effective_allowed_users,
                pairing_mode=effective_pairing_mode,
            )
        )
        return 0

    if args.skip_validate:
        profile = None
    else:
        try:
            profile = inspect_telegram_bot_token(args.bot_token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print("", file=sys.stderr)
            print(
                render_telegram_botfather_guide(
                    allowed_users=effective_allowed_users,
                    pairing_mode=effective_pairing_mode,
                ),
                file=sys.stderr,
            )
            return 1

    if profile:
        print(
            f"Validated Telegram bot @{profile.username or 'unknown'} "
            f"(id={profile.bot_id}, first_name={profile.first_name or 'unknown'})."
        )
        if args.validate_only:
            print("Token validation passed. No config was changed.")
            return 0
    elif args.validate_only:
        print("Cannot use --validate-only together with --skip-validate.", file=sys.stderr)
        return 2

    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = add_channel(
        config_manager=config_manager,
        state_db=state_db,
        channel_kind="telegram",
        bot_token=args.bot_token,
        allowed_users=effective_allowed_users,
        pairing_mode=effective_pairing_mode,
        status=effective_status,
        metadata={"bot_profile": profile.to_dict()} if profile else None,
    )
    print(result)
    print("Telegram onboarding next steps:")
    print("  1. Open Telegram and send /start to the bot.")
    if effective_allowed_users:
        print("  2. Confirm the listed allowed user ids are the accounts you want paired first.")
    else:
        print("  2. Run spark-intelligence operator review-pairings after the first DM arrives.")
    print("  3. Run spark-intelligence gateway start")
    return 0


def handle_channel_test(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    if args.channel_kind == "telegram":
        report = test_configured_telegram_channel(
            config_manager=config_manager,
            state_db=state_db,
        )
        print(report.to_json() if args.json else report.to_text())
        return 0 if report.ok else 1
    print(f"Unsupported channel test target: {args.channel_kind}", file=sys.stderr)
    return 2


def handle_attachments_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = attachment_status(config_manager)
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_attachments_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = list_attachments(config_manager, kind=args.kind)
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_attachments_add_root(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    values = add_attachment_root(config_manager, target=args.target, root=args.root)
    sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    dotted_path = "spark.chips.roots" if args.target == "chips" else "spark.specialization_paths.roots"
    print(f"Updated {dotted_path}:")
    for value in values:
        print(f"- {value}")
    return 0


def handle_attachments_snapshot(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(snapshot.to_json() if args.json else snapshot.to_text())
    return 0


def handle_attachments_activate_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    active_keys = activate_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active chips: {', '.join(active_keys) if active_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_deactivate_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    active_keys = deactivate_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active chips: {', '.join(active_keys) if active_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_pin_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    active_keys = pin_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active chips: {', '.join(active_keys) if active_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_unpin_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    pinned_keys = unpin_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Pinned chips: {', '.join(pinned_keys) if pinned_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_set_path(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    path_key = set_active_path(config_manager, path_key=args.path_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active path: {path_key}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_clear_path(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    clear_active_path(config_manager)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print("Active path: none")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_run_hook(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    try:
        payload = json.loads(args.payload_json)
    except json.JSONDecodeError as exc:
        print(f"Invalid --payload-json: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("Hook payload must be a JSON object.", file=sys.stderr)
        return 2
    run = open_run(
        state_db,
        run_kind=f"operator:attachments_hook:{args.hook}",
        origin_surface="attachments_cli",
        summary="Operator started an attachments chip hook execution.",
        request_id=f"attachments-hook:{args.hook}",
        actor_id="local-operator",
        reason_code="attachments_run_hook",
        facts={"chip_key": args.chip_key or "active", "hook": args.hook},
    )
    try:
        if args.chip_key:
            execution = run_chip_hook(config_manager, chip_key=args.chip_key, hook=args.hook, payload=payload)
        else:
            execution = run_first_active_chip_hook(config_manager, hook=args.hook, payload=payload)
            if execution is None:
                close_run(
                    state_db,
                    run_id=run.run_id,
                    status="stalled",
                    close_reason="no_active_chip_for_hook",
                    summary="Attachments CLI run-hook found no active chip for the requested hook.",
                    facts={"hook": args.hook},
                )
                print(
                    f"No active chip exposes hook '{args.hook}'. Activate a chip first or pass --chip-key.",
                    file=sys.stderr,
                )
                return 1
    except ValueError as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="attachments_hook_invalid",
            summary="Attachments CLI run-hook failed validation before execution.",
            facts={"hook": args.hook, "error": str(exc)},
        )
        print(str(exc), file=sys.stderr)
        return 2

    output_text = execution.to_json() if args.json else execution.to_text()
    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="attachments_cli",
        actor_id="local-operator",
        summary="Operator executed a chip hook via the attachments CLI.",
        reason_code="attachments_run_hook",
        keepability="operator_debug_only",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    screened_output = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=output_text,
        summary="Attachments CLI blocked secret-like chip hook output before operator display.",
        reason_code="attachments_run_hook_secret_like",
        policy_domain="attachments_cli",
        blocked_stage="operator_output",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    if not screened_output["allowed"]:
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="attachments_cli",
            summary="Attachments CLI blocked chip hook output because it contained secret-like material.",
            run_id=run.run_id,
            request_id=run.request_id,
            actor_id="local-operator",
            reason_code="secret_boundary_blocked",
            severity="high",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="secret_boundary_blocked",
            summary="Attachments CLI run-hook was blocked by the secret boundary.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        print(
            "Chip hook output was blocked because it contained secret-like material. "
            "Review quarantine records instead of raw output.",
            file=sys.stderr,
        )
        return 1
    close_run(
        state_db,
        run_id=run.run_id,
        status="closed",
        close_reason="attachments_hook_completed",
        summary="Attachments CLI run-hook completed.",
        facts={
            "chip_key": execution.chip_key,
            "hook": execution.hook,
            "ok": execution.ok,
            "exit_code": execution.exit_code,
        },
    )
    print(output_text)
    return 0 if execution.ok else 1


def handle_auth_connect(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = connect_provider(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        model=args.model,
        base_url=args.base_url,
    )
    print(result)
    return 0


def handle_auth_providers(args: argparse.Namespace) -> int:
    payload = {
        "providers": [
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "auth_methods": list(spec.auth_methods),
                "default_model": spec.default_model,
                "default_base_url": spec.default_base_url,
                "default_api_key_env": spec.default_api_key_env,
                "execution_transport": spec.execution_transport,
                "oauth_redirect_uri": spec.oauth.redirect_uri if spec.oauth else None,
            }
            for spec in list_provider_specs()
        ]
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("Supported providers")
    for provider in payload["providers"]:
        methods = ", ".join(provider["auth_methods"])
        print(f"- {provider['id']}: {provider['display_name']} ({methods})")
        if provider["default_model"]:
            print(f"  default_model={provider['default_model']}")
        if provider["default_base_url"]:
            print(f"  default_base_url={provider['default_base_url']}")
        if provider["default_api_key_env"]:
            print(f"  default_api_key_env={provider['default_api_key_env']}")
        print(f"  execution_transport={provider['execution_transport']}")
        if provider["oauth_redirect_uri"]:
            print(f"  oauth_redirect_uri={provider['oauth_redirect_uri']}")
    return 0


def handle_auth_login(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    if args.listen and args.callback_url:
        print("Cannot combine --listen with --callback-url.", file=sys.stderr)
        return 2

    if args.callback_url:
        result = complete_oauth_login(
            config_manager=config_manager,
            state_db=state_db,
            provider=args.provider,
            callback_url=args.callback_url,
        )
        print(result.to_json() if args.json else result.to_text())
        return 0

    start = start_oauth_login(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
        redirect_uri=args.redirect_uri,
    )

    if not args.listen:
        print(start.to_json() if args.json else start.to_text())
        return 0

    if not args.json:
        print(start.to_text(), flush=True)
        print("", flush=True)
        print(
            f"Waiting up to {args.timeout_seconds} seconds for OAuth callback on {start.redirect_uri} ...",
            flush=True,
        )
        print("", flush=True)

    try:
        gateway_result = serve_gateway_oauth_callback(
            config_manager=config_manager,
            state_db=state_db,
            redirect_uri=start.redirect_uri,
            expected_provider=args.provider,
            timeout_seconds=args.timeout_seconds,
        )
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "start": json.loads(start.to_json()),
                    "callback": {
                        "callback_url": gateway_result.callback_url,
                        "path": gateway_result.path,
                        "query": gateway_result.query,
                    },
                    "result": json.loads(gateway_result.to_json()),
                },
                indent=2,
            )
        )
        return 0

    print(gateway_result.to_text())
    return 0


def handle_auth_logout(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = logout_provider(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
    )
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_auth_refresh(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    try:
        result = refresh_provider(
            config_manager=config_manager,
            state_db=state_db,
            provider=args.provider,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_auth_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    print(report.to_json() if args.json else report.to_text())
    return 0 if report.ok else 1


def handle_researcher_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    status = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    print(status.to_json() if args.json else status.to_text())
    return 0 if status.available else 1


def handle_memory_export_shadow_replay(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = export_shadow_replay(
        config_manager=config_manager,
        state_db=state_db,
        write_path=args.write,
        conversation_limit=args.conversation_limit,
        event_limit=args.event_limit,
        validate=not args.skip_validate,
        validator_root=args.validator_root,
        run_report=bool(args.run_report),
        report_write_path=args.report_write,
    )
    print(result.to_json() if args.json else result.to_text())
    if result.conversation_count == 0:
        return 1
    if args.run_report and _memory_report_failed(result.report):
        return 1
    if result.validation is None:
        return 0
    return 0 if bool(result.validation.get("valid")) and not (result.validation.get("errors") or []) else 1


def handle_memory_export_shadow_replay_batch(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = export_shadow_replay_batch(
        config_manager=config_manager,
        state_db=state_db,
        output_dir=args.output_dir,
        conversation_limit=args.conversation_limit,
        event_limit=args.event_limit,
        conversations_per_file=args.conversations_per_file,
        validate=not args.skip_validate,
        validator_root=args.validator_root,
        run_report=bool(args.run_report),
        report_write_path=args.report_write,
    )
    print(result.to_json() if args.json else result.to_text())
    if not result.files:
        return 1
    if args.run_report and _memory_report_failed(result.report):
        return 1
    if result.validation is None:
        return 0
    return 0 if bool(result.validation.get("valid")) and not (result.validation.get("errors") or []) else 1


def handle_memory_export_sdk_maintenance_replay(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = export_sdk_maintenance_replay(
        config_manager=config_manager,
        state_db=state_db,
        write_path=args.write,
        event_limit=args.event_limit,
        run_report=bool(args.run_report),
        report_write_path=args.report_write,
        validator_root=args.validator_root,
    )
    print(result.to_json() if args.json else result.to_text())
    if result.write_count == 0:
        return 1
    if args.run_report and _memory_report_failed(result.report):
        return 1
    return 0


def handle_memory_direct_smoke(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = run_memory_sdk_smoke_test(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=args.sdk_module,
        subject=args.subject,
        predicate=args.predicate,
        value=args.value,
        cleanup=not bool(args.no_cleanup),
    )
    print(result.to_json() if args.json else result.to_text())
    if result.write_result.accepted_count <= 0:
        return 1
    if result.read_result.abstained or not result.read_result.records:
        return 1
    if result.cleanup_result is not None and result.cleanup_result.accepted_count <= 0:
        return 1
    return 0


def _memory_report_failed(report: dict[str, object] | None) -> bool:
    if report is None:
        return True
    errors = report.get("errors") if isinstance(report, dict) else None
    if errors:
        return True
    valid = report.get("valid") if isinstance(report, dict) else None
    return valid is False


def handle_config_show(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    value = config_manager.get_path(args.path) if args.path else config_manager.load()
    if args.json:
        print(json.dumps(value, indent=2))
    elif isinstance(value, (str, int, float, bool)) or value is None:
        print("null" if value is None else value)
    else:
        print(yaml.safe_dump(value, sort_keys=False).rstrip())
    return 0


def handle_config_set(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    parsed_value = yaml.safe_load(args.value)
    config_manager.set_path(args.path, parsed_value)
    print(f"Set {args.path} = {json.dumps(parsed_value)}")
    return 0


def handle_config_unset(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    removed = config_manager.unset_path(args.path)
    if removed:
        print(f"Removed {args.path}")
        return 0
    print(f"Config path not found: {args.path}", file=sys.stderr)
    return 1


def handle_swarm_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    status = swarm_status(config_manager, state_db)
    print(status.to_json() if args.json else status.to_text())
    return 0 if status.payload_ready else 1


def handle_swarm_configure(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    _configure_swarm(
        config_manager,
        api_url=args.api_url,
        supabase_url=args.supabase_url,
        workspace_id=args.workspace_id,
        runtime_root=args.runtime_root,
        access_token=args.access_token,
        access_token_env=args.access_token_env,
        refresh_token=args.refresh_token,
        refresh_token_env=args.refresh_token_env,
        auth_client_key=args.auth_client_key,
        auth_client_key_env=args.auth_client_key_env,
    )
    print("Spark Swarm bridge config updated.")
    print("Recommended checks:")
    print("  1. spark-intelligence swarm status")
    print("  2. spark-intelligence swarm sync --dry-run")
    print("  3. spark-intelligence swarm sync")
    return 0


def handle_swarm_sync(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = sync_swarm_collective(config_manager=config_manager, state_db=state_db, dry_run=args.dry_run)
    print(result.to_json() if args.json else result.to_text())
    return 0 if result.ok else 1


def handle_swarm_evaluate(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = evaluate_swarm_escalation(config_manager=config_manager, state_db=state_db, task=args.task)
    print(result.to_json() if args.json else result.to_text())
    return 0 if result.ok else 1


def _apply_setup_integrations(config_manager: ConfigManager, args: argparse.Namespace) -> list[str]:
    notes: list[str] = []

    researcher_root_value = args.researcher_root
    if not researcher_root_value:
        current_root = config_manager.get_path("spark.researcher.runtime_root")
        discovered_root, source = discover_researcher_runtime_root(config_manager)
        if not current_root and discovered_root and source == "autodiscovered":
            researcher_root_value = str(discovered_root)
            notes.append(f"autoconnected spark-researcher at {discovered_root}")

    if researcher_root_value:
        normalized_root = str(Path(researcher_root_value).expanduser())
        config_manager.set_path("spark.researcher.runtime_root", normalized_root)
        notes.append(f"configured spark.researcher.runtime_root = {normalized_root}")
        if args.researcher_config:
            normalized_config = str(Path(args.researcher_config).expanduser())
            config_manager.set_path("spark.researcher.config_path", normalized_config)
            notes.append(f"configured spark.researcher.config_path = {normalized_config}")
        else:
            resolved = resolve_researcher_config_path(config_manager, Path(normalized_root))
            if resolved.exists():
                config_manager.set_path("spark.researcher.config_path", str(resolved))
                notes.append(f"discovered spark-researcher config at {resolved}")

    swarm_runtime_root = args.swarm_runtime_root
    if not swarm_runtime_root:
        current_swarm_root = config_manager.get_path("spark.swarm.runtime_root")
        default_swarm_root = Path.home() / "Desktop" / "spark-swarm"
        if not current_swarm_root and default_swarm_root.exists():
            swarm_runtime_root = str(default_swarm_root)
            notes.append(f"autoconnected spark-swarm at {default_swarm_root}")

    if any(
        [
            swarm_runtime_root,
            args.swarm_api_url,
            args.swarm_workspace_id,
            args.swarm_access_token,
        ]
    ):
        _configure_swarm(
            config_manager,
            api_url=args.swarm_api_url,
            supabase_url=None,
            workspace_id=args.swarm_workspace_id,
            runtime_root=swarm_runtime_root,
            access_token=args.swarm_access_token,
            access_token_env=args.swarm_access_token_env if args.swarm_access_token else None,
            refresh_token=None,
            refresh_token_env=None,
            auth_client_key=None,
            auth_client_key_env=None,
        )
        if args.swarm_api_url:
            notes.append(f"configured spark.swarm.api_url = {args.swarm_api_url.rstrip('/')}")
        if args.swarm_workspace_id:
            notes.append(f"configured spark.swarm.workspace_id = {args.swarm_workspace_id}")
        if swarm_runtime_root:
            notes.append(f"configured spark.swarm.runtime_root = {Path(swarm_runtime_root).expanduser()}")
        if args.swarm_access_token:
            notes.append(f"stored spark.swarm access token in {args.swarm_access_token_env}")

    attachment_scan = attachment_status(config_manager)
    if attachment_scan.chip_source == "autodiscovered" and attachment_scan.chip_roots:
        config_manager.set_path("spark.chips.roots", attachment_scan.chip_roots)
        notes.append(f"autoconnected {len(attachment_scan.chip_roots)} chip root(s)")
    if attachment_scan.path_source == "autodiscovered" and attachment_scan.path_roots:
        config_manager.set_path("spark.specialization_paths.roots", attachment_scan.path_roots)
        notes.append(f"autoconnected {len(attachment_scan.path_roots)} specialization path root(s)")

    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    notes.append(f"wrote attachment snapshot to {snapshot.snapshot_path}")

    return notes


def _configure_swarm(
    config_manager: ConfigManager,
    *,
    api_url: str | None,
    supabase_url: str | None,
    workspace_id: str | None,
    runtime_root: str | None,
    access_token: str | None,
    access_token_env: str | None,
    refresh_token: str | None,
    refresh_token_env: str | None,
    auth_client_key: str | None,
    auth_client_key_env: str | None,
) -> None:
    if api_url:
        config_manager.set_path("spark.swarm.api_url", api_url.rstrip("/"))
    if supabase_url:
        config_manager.set_path("spark.swarm.supabase_url", supabase_url.rstrip("/"))
    if workspace_id:
        config_manager.set_path("spark.swarm.workspace_id", workspace_id)
        config_manager.upsert_env_secret("SPARK_SWARM_WORKSPACE_ID", workspace_id)
    if runtime_root:
        config_manager.set_path("spark.swarm.runtime_root", str(Path(runtime_root).expanduser()))
    if access_token:
        env_name = access_token_env or "SPARK_SWARM_ACCESS_TOKEN"
        config_manager.upsert_env_secret(env_name, access_token)
        config_manager.set_path("spark.swarm.access_token_env", env_name)
    elif access_token_env:
        config_manager.set_path("spark.swarm.access_token_env", access_token_env)
    if refresh_token:
        env_name = refresh_token_env or "SPARK_SWARM_REFRESH_TOKEN"
        config_manager.upsert_env_secret(env_name, refresh_token)
        config_manager.set_path("spark.swarm.refresh_token_env", env_name)
    elif refresh_token_env:
        config_manager.set_path("spark.swarm.refresh_token_env", refresh_token_env)
    if auth_client_key:
        env_name = auth_client_key_env or "SPARK_SWARM_AUTH_CLIENT_KEY"
        config_manager.upsert_env_secret(env_name, auth_client_key)
        config_manager.set_path("spark.swarm.auth_client_key_env", env_name)
    elif auth_client_key_env:
        config_manager.set_path("spark.swarm.auth_client_key_env", auth_client_key_env)


def handle_jobs_tick(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(jobs_tick(config_manager, state_db))
    return 0


def handle_jobs_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(jobs_list(state_db))
    return 0


def handle_agent_inspect(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    owner = config_manager.load().get("workspace", {}).get("owner_human_id", "local-operator")
    report = agent_inspect(state_db=state_db, workspace_owner=owner)
    attachments = attachment_status(config_manager)
    report.payload["attachments"] = {
        "chip_source": attachments.chip_source,
        "path_source": attachments.path_source,
        "chip_count": len([record for record in attachments.records if record.kind == "chip"]),
        "path_count": len([record for record in attachments.records if record.kind == "path"]),
        "warning_count": len(attachments.warnings),
        "active_chip_keys": config_manager.get_path("spark.chips.active_keys", default=[]) or [],
        "pinned_chip_keys": config_manager.get_path("spark.chips.pinned_keys", default=[]) or [],
        "active_path_key": config_manager.get_path("spark.specialization_paths.active_path_key"),
        "snapshot_path": str(config_manager.paths.home / "attachments.snapshot.json"),
    }
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 0


def handle_pairings_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(list_pairings(state_db))
    return 0


def handle_pairings_approve(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        approve_pairing(
            state_db=state_db,
            channel_id=args.channel_id,
            external_user_id=args.external_user_id,
            display_name=args.display_name,
        )
    )
    return 0


def handle_pairings_revoke(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(revoke_pairing(state_db=state_db, channel_id=args.channel_id, external_user_id=args.external_user_id))
    return 0


def handle_sessions_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(list_sessions(state_db))
    return 0


def handle_sessions_revoke(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(revoke_session(state_db=state_db, session_id=args.session_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup":
        return handle_setup(args)
    if args.command == "bootstrap" and args.bootstrap_command == "telegram-agent":
        return handle_bootstrap_telegram_agent(args)
    if args.command == "install-autostart":
        return handle_install_autostart(args)
    if args.command == "uninstall-autostart":
        return handle_uninstall_autostart(args)
    if args.command == "doctor":
        return handle_doctor(args)
    if args.command == "status":
        return handle_status(args)
    if args.command == "connect" and args.connect_command == "status":
        return handle_connect_status(args)
    if args.command == "connect" and args.connect_command == "route-policy":
        return handle_connect_route_policy(args)
    if args.command == "connect" and args.connect_command == "set-route-policy":
        return handle_connect_set_route_policy(args)
    if args.command == "operator" and args.operator_command == "set-bridge":
        return handle_operator_set_bridge(args)
    if args.command == "operator" and args.operator_command == "review-pairings":
        return handle_operator_review_pairings(args)
    if args.command == "operator" and args.operator_command == "pairing-summary":
        return handle_operator_pairing_summary(args)
    if args.command == "operator" and args.operator_command == "approve-pairing":
        return handle_operator_approve_pairing(args)
    if args.command == "operator" and args.operator_command == "approve-latest":
        return handle_operator_approve_latest(args)
    if args.command == "operator" and args.operator_command == "hold-latest":
        return handle_operator_hold_latest(args)
    if args.command == "operator" and args.operator_command == "revoke-latest":
        return handle_operator_revoke_latest(args)
    if args.command == "operator" and args.operator_command == "hold-pairing":
        return handle_operator_hold_pairing(args)
    if args.command == "operator" and args.operator_command == "set-channel":
        return handle_operator_set_channel(args)
    if args.command == "operator" and args.operator_command == "history":
        return handle_operator_history(args)
    if args.command == "operator" and args.operator_command == "inbox":
        return handle_operator_inbox(args)
    if args.command == "operator" and args.operator_command == "security":
        return handle_operator_security(args)
    if args.command == "operator" and args.operator_command == "snooze-webhook-alert":
        return handle_operator_snooze_webhook_alert(args)
    if args.command == "operator" and args.operator_command == "webhook-alert-snoozes":
        return handle_operator_webhook_alert_snoozes(args)
    if args.command == "operator" and args.operator_command == "clear-webhook-alert-snooze":
        return handle_operator_clear_webhook_alert_snooze(args)
    if args.command == "gateway" and args.gateway_command == "start":
        return handle_gateway_start(args)
    if args.command == "gateway" and args.gateway_command == "status":
        return handle_gateway_status(args)
    if args.command == "gateway" and args.gateway_command == "oauth-callback":
        return handle_gateway_oauth_callback(args)
    if args.command == "gateway" and args.gateway_command == "simulate-telegram-update":
        return handle_gateway_simulate_telegram_update(args)
    if args.command == "gateway" and args.gateway_command == "simulate-discord-message":
        return handle_gateway_simulate_discord_message(args)
    if args.command == "gateway" and args.gateway_command == "simulate-whatsapp-message":
        return handle_gateway_simulate_whatsapp_message(args)
    if args.command == "gateway" and args.gateway_command == "traces":
        return handle_gateway_traces(args)
    if args.command == "gateway" and args.gateway_command == "outbound":
        return handle_gateway_outbound(args)
    if args.command == "channel" and args.channel_command == "add":
        return handle_channel_add(args)
    if args.command == "channel" and args.channel_command == "telegram-onboard":
        return handle_channel_telegram_onboard(args)
    if args.command == "channel" and args.channel_command == "test":
        return handle_channel_test(args)
    if args.command == "attachments" and args.attachments_command == "status":
        return handle_attachments_status(args)
    if args.command == "attachments" and args.attachments_command == "list":
        return handle_attachments_list(args)
    if args.command == "attachments" and args.attachments_command == "add-root":
        return handle_attachments_add_root(args)
    if args.command == "attachments" and args.attachments_command == "snapshot":
        return handle_attachments_snapshot(args)
    if args.command == "attachments" and args.attachments_command == "activate-chip":
        return handle_attachments_activate_chip(args)
    if args.command == "attachments" and args.attachments_command == "deactivate-chip":
        return handle_attachments_deactivate_chip(args)
    if args.command == "attachments" and args.attachments_command == "pin-chip":
        return handle_attachments_pin_chip(args)
    if args.command == "attachments" and args.attachments_command == "unpin-chip":
        return handle_attachments_unpin_chip(args)
    if args.command == "attachments" and args.attachments_command == "set-path":
        return handle_attachments_set_path(args)
    if args.command == "attachments" and args.attachments_command == "clear-path":
        return handle_attachments_clear_path(args)
    if args.command == "attachments" and args.attachments_command == "run-hook":
        return handle_attachments_run_hook(args)
    if args.command == "auth" and args.auth_command == "providers":
        return handle_auth_providers(args)
    if args.command == "auth" and args.auth_command == "connect":
        return handle_auth_connect(args)
    if args.command == "auth" and args.auth_command == "login":
        return handle_auth_login(args)
    if args.command == "auth" and args.auth_command == "logout":
        return handle_auth_logout(args)
    if args.command == "auth" and args.auth_command == "refresh":
        return handle_auth_refresh(args)
    if args.command == "auth" and args.auth_command == "status":
        return handle_auth_status(args)
    if args.command == "researcher" and args.researcher_command == "status":
        return handle_researcher_status(args)
    if args.command == "memory" and args.memory_command == "export-shadow-replay":
        return handle_memory_export_shadow_replay(args)
    if args.command == "memory" and args.memory_command == "export-shadow-replay-batch":
        return handle_memory_export_shadow_replay_batch(args)
    if args.command == "memory" and args.memory_command == "export-sdk-maintenance-replay":
        return handle_memory_export_sdk_maintenance_replay(args)
    if args.command == "memory" and args.memory_command == "direct-smoke":
        return handle_memory_direct_smoke(args)
    if args.command == "config" and args.config_command == "show":
        return handle_config_show(args)
    if args.command == "config" and args.config_command == "set":
        return handle_config_set(args)
    if args.command == "config" and args.config_command == "unset":
        return handle_config_unset(args)
    if args.command == "swarm" and args.swarm_command == "status":
        return handle_swarm_status(args)
    if args.command == "swarm" and args.swarm_command == "configure":
        return handle_swarm_configure(args)
    if args.command == "swarm" and args.swarm_command == "sync":
        return handle_swarm_sync(args)
    if args.command == "swarm" and args.swarm_command == "evaluate":
        return handle_swarm_evaluate(args)
    if args.command == "jobs" and args.jobs_command == "tick":
        return handle_jobs_tick(args)
    if args.command == "jobs" and args.jobs_command == "list":
        return handle_jobs_list(args)
    if args.command == "agent" and args.agent_command == "inspect":
        return handle_agent_inspect(args)
    if args.command == "pairings" and args.pairings_command == "list":
        return handle_pairings_list(args)
    if args.command == "pairings" and args.pairings_command == "approve":
        return handle_pairings_approve(args)
    if args.command == "pairings" and args.pairings_command == "revoke":
        return handle_pairings_revoke(args)
    if args.command == "sessions" and args.sessions_command == "list":
        return handle_sessions_list(args)
    if args.command == "sessions" and args.sessions_command == "revoke":
        return handle_sessions_revoke(args)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
