from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from spark_intelligence.attachments import (
    activate_chip,
    add_attachment_root,
    attachment_status,
    build_attachment_snapshot,
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
from spark_intelligence.auth.providers import get_provider_spec, list_api_key_provider_ids, list_oauth_provider_ids, list_provider_specs
from spark_intelligence.auth.runtime import build_auth_status_report
from spark_intelligence.auth.service import complete_oauth_login, connect_provider, logout_provider, refresh_provider, start_oauth_login
from spark_intelligence.browser import (
    BROWSER_NAVIGATE_HOOK,
    BROWSER_PAGE_SNAPSHOT_HOOK,
    BROWSER_STATUS_HOOK,
    BROWSER_TAB_WAIT_HOOK,
    build_browser_navigate_payload,
    build_browser_page_snapshot_payload,
    build_browser_status_payload,
    build_browser_tab_wait_payload,
    render_browser_page_snapshot,
    render_browser_status,
)
from spark_intelligence.channel.service import (
    add_channel,
    inspect_telegram_bot_token,
    render_telegram_botfather_guide,
    set_channel_status,
    test_configured_telegram_channel,
)
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.diagnostics import build_diagnostic_report
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.gateway.runtime import (
    gateway_ask_telegram,
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
    build_spark_swarm_identity_import_payload,
    consume_pairing_code,
    hold_pairing,
    hold_latest_pairing,
    inspect_canonical_agent,
    issue_pairing_code,
    link_spark_swarm_agent,
    list_pairings,
    list_sessions,
    normalize_spark_swarm_identity_import,
    pairing_summary,
    peek_latest_pairing_external_user_id,
    read_canonical_agent_state,
    rename_agent_identity,
    revoke_latest_pairing,
    review_pairings,
    revoke_pairing,
    revoke_session,
)
from spark_intelligence.jobs.service import jobs_list, jobs_tick
from spark_intelligence.memory import (
    benchmark_memory_architectures,
    build_telegram_state_knowledge_base,
    export_sdk_maintenance_replay,
    export_shadow_replay,
    export_shadow_replay_batch,
    inspect_human_memory_in_memory,
    inspect_memory_sdk_runtime,
    lookup_current_state_in_memory,
    run_memory_sdk_smoke_test,
    run_memory_sdk_maintenance,
    run_telegram_memory_architecture_soak,
    run_telegram_memory_regression,
)
from spark_intelligence.personality import (
    build_personality_import_payload,
    migrate_legacy_human_personality_to_agent_persona,
    normalize_personality_import,
    resolve_builder_persona_agent_id,
    save_agent_persona_profile,
    write_personality_evolver_state,
)
from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    close_run,
    latest_events_by_type,
    open_run,
    record_event,
    record_observer_handoff_record,
)
from spark_intelligence.ops import (
    build_observer_handoff_payload,
    build_personality_report,
    build_operator_inbox,
    export_operator_observer_packets,
    build_operator_security_report,
    clear_webhook_alert_snooze,
    list_observer_handoffs,
    list_observer_packets,
    list_operator_events,
    list_webhook_alert_events,
    list_webhook_alert_snoozes,
    log_operator_event,
    snooze_webhook_alert,
)
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, resolve_researcher_config_path
from spark_intelligence.researcher_bridge import researcher_bridge_status
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import evaluate_swarm_escalation, swarm_doctor, swarm_status, sync_swarm_collective
from spark_intelligence.harness_registry import (
    build_harness_registry,
    select_auto_harness_recipe,
    select_harness_recipe,
)
from spark_intelligence.harness_runtime import (
    build_harness_runtime_snapshot,
    build_harness_task_envelope,
    execute_harness_chain,
    execute_harness_task,
)
from spark_intelligence.mission_control import build_mission_control_plan, build_mission_control_snapshot
from spark_intelligence.system_registry import build_system_registry


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
        browser = self.payload.get("browser") or {}
        if isinstance(browser, dict) and browser:
            browser_state = str(browser.get("status") or "unknown")
            chip_key = str(browser.get("chip_key") or "browser")
            if browser_state == "completed":
                lines.append(f"- browser: connected via {chip_key}")
            else:
                error_code = str(browser.get("error_code") or browser_state)
                browser_label = "standby" if error_code == "BROWSER_SESSION_STALE" else browser_state
                lines.append(f"- browser: {browser_label} via {chip_key} {error_code}")
                if browser.get("error_message"):
                    lines.append(f"- browser detail: {browser['error_message']}")
                repair_hint = _browser_status_repair_hint(browser)
                if repair_hint:
                    lines.append(f"- browser repair: {repair_hint}")
        lines.append(
            f"- attachments: {self.payload['attachments']['record_count']} records "
            f"warnings={self.attachment_warning_count}"
        )
        lines.append(
            f"- active chips: {', '.join(self.payload['attachments']['active_chip_keys']) if self.payload['attachments']['active_chip_keys'] else 'none'}"
        )
        lines.append(f"- active path: {self.payload['attachments']['active_path_key'] or 'none'}")
        registry_summary = (self.payload.get("system_registry") or {}).get("summary") or {}
        current_capabilities = registry_summary.get("current_capabilities") or []
        if current_capabilities:
            lines.append(f"- current capabilities: {len(current_capabilities)}")
            lines.extend(f"- capability: {item}" for item in current_capabilities[:5])
        mission_control = self.payload.get("mission_control") or {}
        mission_summary = mission_control.get("summary") or {}
        if mission_summary:
            lines.append(f"- mission control: {mission_summary.get('top_level_state') or 'unknown'}")
            if mission_summary.get("current_focus"):
                lines.append(f"- mission focus: {mission_summary['current_focus']}")
            degraded_surfaces = mission_summary.get("degraded_surfaces") or []
            if degraded_surfaces:
                lines.append(f"- degraded surfaces: {', '.join(str(item) for item in degraded_surfaces[:5])}")
            active_loops = mission_summary.get("active_loops") or []
            if active_loops:
                lines.append(f"- active loops: {', '.join(str(item) for item in active_loops[:5])}")
            recommended_actions = mission_summary.get("recommended_actions") or []
            lines.extend(f"- next action: {str(item)}" for item in recommended_actions[:2])
        harness_registry = self.payload.get("harness_registry") or {}
        harness_summary = harness_registry.get("summary") or {}
        available_harnesses = harness_summary.get("available_harnesses") or []
        if available_harnesses:
            lines.append(f"- harnesses: {', '.join(str(item) for item in available_harnesses[:5])}")
        harness_runtime = self.payload.get("harness_runtime") or {}
        harness_runtime_summary = harness_runtime.get("summary") or {}
        if harness_runtime_summary.get("recent_run_count"):
            lines.append(
                f"- harness runtime: recent_runs={int(harness_runtime_summary.get('recent_run_count') or 0)} "
                f"last={harness_runtime_summary.get('last_harness_id') or 'unknown'}"
            )
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
                state = str(dimension.get("state") or "unknown")
                lines.append(f"- {key}: {state}")
                if state not in {"healthy", "unknown"}:
                    detail = str(dimension.get("detail") or "").strip()
                    if detail:
                        lines.append(f"- {key} detail: {detail}")
                    repair_hint = _watchtower_dimension_repair_hint(key, dimension)
                    if repair_hint:
                        lines.append(f"- {key} repair: {repair_hint}")
            contradiction_counts = (watchtower.get("contradictions") or {}).get("counts") or {}
            lines.append(
                f"- contradictions: open={int(contradiction_counts.get('open') or 0)} "
                f"resolved={int(contradiction_counts.get('resolved') or 0)}"
            )
            recent_open = (watchtower.get("contradictions") or {}).get("recent_open") or []
            latest_open = recent_open[0] if recent_open and isinstance(recent_open[0], dict) else {}
            contradiction_detail = str(latest_open.get("detail") or latest_open.get("summary") or "").strip()
            contradiction_key = str(latest_open.get("contradiction_key") or "").strip()
            if contradiction_detail:
                lines.append(f"- contradiction detail: {contradiction_detail}")
            if contradiction_key:
                lines.append(f"- contradiction key: {contradiction_key}")
        for check in self.payload.get("doctor", {}).get("checks") or []:
            if bool(check.get("ok")):
                continue
            name = str(check.get("name") or "").strip()
            detail = str(check.get("detail") or "").strip()
            repair_hint = _doctor_check_repair_hint(name, detail, watchtower)
            if not repair_hint:
                continue
            if detail:
                lines.append(f"- {name}: {detail}")
            lines.append(f"- {name} repair: {repair_hint}")
        runtime_payload = self.payload.get("runtime") or {}
        telegram_gateway_payload = self.payload.get("telegram_gateway") or {}
        autostart_payload = runtime_payload.get("autostart") or {}
        lines.append(f"- install profile: {runtime_payload.get('install_profile') or 'none'}")
        lines.append(f"- default gateway mode: {runtime_payload.get('default_gateway_mode') or 'none'}")
        if telegram_gateway_payload:
            lines.append(f"- telegram ingress owner: {telegram_gateway_payload.get('owner') or 'unknown'}")
            lines.append(f"- telegram ingress mode: {telegram_gateway_payload.get('mode') or 'unknown'}")
            lines.append(f"- telegram ingress contract: {telegram_gateway_payload.get('contract') or 'unknown'}")
            lines.append(f"- telegram migration status: {telegram_gateway_payload.get('migration_status') or 'unknown'}")
            lines.append(
                f"- telegram shadow validation: {telegram_gateway_payload.get('shadow_validation_command') or 'none'}"
            )
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


def _browser_status_repair_hint(browser: dict[str, object]) -> str | None:
    error_code = str(browser.get("error_code") or "").strip()
    if error_code == "BROWSER_SESSION_STALE":
        return "Reconnect the Spark Browser extension session, then rerun `spark-intelligence browser status --json`."
    if error_code:
        return "Rerun `spark-intelligence browser status --json` for the full governed browser failure payload."
    return None


def _watchtower_dimension_repair_hint(dimension_key: str, dimension: dict[str, object]) -> str | None:
    state = str(dimension.get("state") or "").strip()
    detail = str(dimension.get("detail") or "").strip()
    if dimension_key == "environment_parity" and state == "parity_broken":
        return "Align the conflicting runtime roots or config paths, then rerun `spark-intelligence doctor`."
    if dimension_key == "scheduler_freshness" and state == "stalled":
        return "Close or repair the stalled background runs, then rerun `spark-intelligence doctor`."
    if dimension_key == "execution_health" and state == "execution_impaired":
        return "Inspect missing dispatch proof in `spark-intelligence operator security` before trusting runtime health."
    if detail:
        return "Use `spark-intelligence doctor` for the full diagnostic detail."
    return None


def _import_hook_repair_hint(*, hook_name: str, import_payload: dict[str, object]) -> str:
    available = [str(item) for item in (import_payload.get("available_chip_keys") or []) if str(item)]
    if available:
        shown = ", ".join(available[:3])
        return f"Activate a chip exposing the `{hook_name}` hook ({shown}), then rerun `spark-intelligence doctor`."
    return f"No configured chip exposes the `{hook_name}` hook. Add or implement one, then rerun `spark-intelligence doctor`."


def _doctor_check_repair_hint(check_name: str, detail: str, watchtower: dict[str, object]) -> str | None:
    panels = (watchtower.get("panels") or {}) if isinstance(watchtower, dict) else {}
    if check_name == "watchtower-personality-import":
        personality_panel = panels.get("personality") or {}
        personality_import = personality_panel.get("personality_import") or {}
        if isinstance(personality_import, dict):
            return _import_hook_repair_hint(hook_name="personality", import_payload=personality_import)
        return "No configured chip exposes the `personality` hook. Add or implement one, then rerun `spark-intelligence doctor`."
    if check_name == "watchtower-agent-identity-import":
        identity_panel = panels.get("agent_identity") or {}
        identity_import = identity_panel.get("identity_import") or {}
        if isinstance(identity_import, dict):
            return _import_hook_repair_hint(hook_name="identity", import_payload=identity_import)
        return "No configured chip exposes the `identity` hook. Add or implement one, then rerun `spark-intelligence doctor`."
    return None


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
    fallback_provider_id: str | None
    fallback_provider_result: str | None
    channel_result: str
    setup_notes: list[str]
    active_chip_keys: list[str]
    pinned_chip_keys: list[str]
    active_path_key: str | None
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
                "fallback_provider_id": self.fallback_provider_id,
                "fallback_provider_result": self.fallback_provider_result,
                "channel_result": self.channel_result,
                "setup_notes": self.setup_notes,
                "active_chip_keys": self.active_chip_keys,
                "pinned_chip_keys": self.pinned_chip_keys,
                "active_path_key": self.active_path_key,
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
        lines.append(f"- primary_provider: {self.provider_id}")
        lines.append(f"- primary_provider_result: {self.provider_result}")
        lines.append(f"- fallback_provider: {self.fallback_provider_id or 'none'}")
        if self.fallback_provider_result:
            lines.append(f"- fallback_provider_result: {self.fallback_provider_result}")
        lines.append(f"- channel_result: {self.channel_result}")
        lines.append(f"- active_chip_keys: {', '.join(self.active_chip_keys) if self.active_chip_keys else 'none'}")
        lines.append(f"- pinned_chip_keys: {', '.join(self.pinned_chip_keys) if self.pinned_chip_keys else 'none'}")
        lines.append(f"- active_path_key: {self.active_path_key or 'none'}")
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


@dataclass
class BootstrapTelegramAgentGuide:
    home: str
    setup_notes: list[str]
    provider_choices: list[dict[str, str | None]]
    discovered_chip_keys: list[str]
    discovered_path_keys: list[str]
    botfather_guide: str
    existing_bot_example: str
    fallback_bot_example: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "home": self.home,
                "setup_notes": self.setup_notes,
                "provider_choices": self.provider_choices,
                "discovered_chip_keys": self.discovered_chip_keys,
                "discovered_path_keys": self.discovered_path_keys,
                "botfather_guide": self.botfather_guide,
                "existing_bot_example": self.existing_bot_example,
                "fallback_bot_example": self.fallback_bot_example,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark Intelligence install guide: telegram-agent"]
        lines.append(f"- home: {self.home}")
        if self.setup_notes:
            lines.append("- setup_notes:")
            lines.extend(f"  - {note}" for note in self.setup_notes)
        lines.append("- provider_choices:")
        for choice in self.provider_choices:
            lines.append(
                "  - "
                + str(choice["provider_id"])
                + f" ({choice['display_name']})"
                + f" model={choice['default_model'] or 'choose_one'}"
                + f" env={choice['api_key_env'] or 'custom_env'}"
            )
        lines.append(
            f"- discovered_chip_keys: {', '.join(self.discovered_chip_keys) if self.discovered_chip_keys else 'none'}"
        )
        lines.append(
            f"- discovered_path_keys: {', '.join(self.discovered_path_keys) if self.discovered_path_keys else 'none'}"
        )
        lines.append("- existing_bot_example:")
        lines.append(f"  {self.existing_bot_example}")
        lines.append("- fallback_bot_example:")
        lines.append(f"  {self.fallback_bot_example}")
        lines.append("- botfather_guide:")
        lines.extend(f"  {line}" for line in self.botfather_guide.splitlines())
        return "\n".join(lines)


@dataclass
class MemoryStatus:
    payload: dict[str, object]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        runtime = self.payload.get("runtime") or {}
        counts = self.payload.get("counts") or {}
        lines = ["Spark memory status"]
        lines.append(
            f"- configured module: {runtime.get('configured_module') or 'unknown'} "
            f"ready={'yes' if runtime.get('ready') else 'no'}"
        )
        lines.append(
            f"- config: enabled={'yes' if runtime.get('memory_enabled') else 'no'} "
            f"shadow_mode={'yes' if runtime.get('shadow_mode') else 'no'}"
        )
        if runtime.get("client_kind"):
            lines.append(f"- client kind: {runtime.get('client_kind')}")
        if runtime.get("reason"):
            lines.append(f"- sdk detail: {runtime.get('reason')}")
        lines.append(
            f"- writes: requests={counts.get('write_requests', 0)} results={counts.get('write_results', 0)} "
            f"accepted={counts.get('accepted_observations', 0)} rejected={counts.get('rejected_observations', 0)} "
            f"skipped={counts.get('skipped_observations', 0)}"
        )
        lines.append(
            f"- reads: requests={counts.get('read_requests', 0)} results={counts.get('read_results', 0)} "
            f"hits={counts.get('read_hits', 0)} shadow_only={counts.get('shadow_only_reads', 0)}"
        )
        role_mix = self.payload.get("memory_role_mix") or []
        if role_mix:
            lines.append(
                "- memory roles: "
                + ", ".join(f"{item['memory_role']}={item['count']}" for item in role_mix if isinstance(item, dict))
            )
        abstentions = self.payload.get("abstention_reasons") or []
        if abstentions:
            lines.append(
                "- abstentions: "
                + ", ".join(f"{item['reason']}={item['count']}" for item in abstentions if isinstance(item, dict))
            )
        last_smoke = self.payload.get("last_smoke") or {}
        if last_smoke:
            lines.append(
                f"- last smoke: {last_smoke.get('event_type') or 'unknown'} "
                f"{last_smoke.get('created_at') or 'unknown'} "
                f"{last_smoke.get('subject') or ''} {last_smoke.get('predicate') or ''}".rstrip()
            )
        failures = self.payload.get("recent_failures") or []
        if failures:
            lines.append("- recent failures:")
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                lines.append(
                    f"  - {failure.get('event_type') or 'unknown'} "
                    f"{failure.get('created_at') or 'unknown'} "
                    f"reason={failure.get('reason') or 'n/a'}"
                )
        return "\n".join(lines)


@dataclass
class MemoryHumanInspection:
    payload: dict[str, object]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        runtime = self.payload.get("runtime") or {}
        current_state = self.payload.get("current_state") or {}
        records = current_state.get("records") or []
        lines = ["Spark memory inspect human"]
        lines.append(f"- human_id: {self.payload.get('human_id') or 'unknown'}")
        lines.append(f"- subject: {self.payload.get('subject') or 'unknown'}")
        lines.append(
            f"- configured module: {runtime.get('configured_module') or 'unknown'} "
            f"ready={'yes' if runtime.get('ready') else 'no'}"
        )
        lines.append(
            f"- current facts: status={current_state.get('status') or 'unknown'} "
            f"records={len(records)} abstained={'yes' if current_state.get('abstained') else 'no'}"
        )
        if records:
            lines.append("- current-state facts:")
            for record in records:
                if not isinstance(record, dict):
                    continue
                lines.append(
                    f"  - {record.get('predicate') or 'unknown'} = {record.get('value')}"
                )
        recent_events = self.payload.get("recent_events") or []
        if recent_events:
            lines.append("- recent memory events:")
            for event in recent_events:
                if not isinstance(event, dict):
                    continue
                lines.append(
                    f"  - {event.get('event_type') or 'unknown'} "
                    f"{event.get('created_at') or 'unknown'} "
                    f"reason={event.get('reason') or 'n/a'} "
                    f"predicate={event.get('predicate') or event.get('predicate_prefix') or 'n/a'}"
                )
        return "\n".join(lines)


@dataclass
class MemoryRegressionScore:
    payload: dict[str, object]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        runtime = self.payload.get("runtime") or {}
        lines = ["Spark memory regression score"]
        lines.append(f"- pack_id: {self.payload.get('pack_id') or 'unknown'}")
        lines.append(f"- title: {self.payload.get('title') or 'unknown'}")
        lines.append(f"- human_id: {self.payload.get('human_id') or 'unknown'}")
        lines.append(
            f"- configured module: {runtime.get('configured_module') or 'unknown'} "
            f"ready={'yes' if runtime.get('ready') else 'no'}"
        )
        lines.append(
            f"- score: matched={self.payload.get('matched_count', 0)}/"
            f"{self.payload.get('fact_count', 0)} "
            f"missing={self.payload.get('missing_count', 0)} "
            f"mismatched={self.payload.get('mismatched_count', 0)}"
        )
        fact_results = self.payload.get("fact_results") or []
        if fact_results:
            lines.append("- fact results:")
            for row in fact_results:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    f"  - {row.get('predicate')}: {row.get('status')} "
                    f"expected={row.get('expected_value')} actual={row.get('actual_value')}"
                )
        probes = self.payload.get("probe_questions") or []
        if probes:
            lines.append("- recall probes:")
            for probe in probes:
                lines.append(f"  - {probe}")
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
        help="Primary API-key-backed provider to bootstrap",
    )
    bootstrap_telegram_parser.add_argument("--api-key", help="Provider API key to store in the home env")
    bootstrap_telegram_parser.add_argument("--api-key-env", help="Provider API key env var name")
    bootstrap_telegram_parser.add_argument("--model", help="Default model id for the provider")
    bootstrap_telegram_parser.add_argument("--base-url", help="Custom provider base URL")
    bootstrap_telegram_parser.add_argument(
        "--fallback-provider",
        choices=list_api_key_provider_ids(),
        help="Optional fallback API-key-backed provider",
    )
    bootstrap_telegram_parser.add_argument("--fallback-api-key", help="Fallback provider API key to store in the home env")
    bootstrap_telegram_parser.add_argument("--fallback-api-key-env", help="Fallback provider API key env var name")
    bootstrap_telegram_parser.add_argument("--fallback-model", help="Default model id for the fallback provider")
    bootstrap_telegram_parser.add_argument("--fallback-base-url", help="Custom fallback provider base URL")
    bootstrap_telegram_parser.add_argument("--bot-token", help="Telegram bot token to store in the home env")
    bootstrap_telegram_parser.add_argument("--bot-token-env", help="Existing env var name holding the Telegram bot token")
    bootstrap_telegram_parser.add_argument("--chip-root", action="append", default=[], help="Additional domain-chip root to attach during bootstrap")
    bootstrap_telegram_parser.add_argument("--path-root", action="append", default=[], help="Additional specialization-path root to attach during bootstrap")
    bootstrap_telegram_parser.add_argument("--activate-chip", action="append", default=[], help="Chip key to activate during bootstrap")
    bootstrap_telegram_parser.add_argument("--pin-chip", action="append", default=[], help="Chip key to pin during bootstrap")
    bootstrap_telegram_parser.add_argument(
        "--no-default-memory-chip",
        action="store_true",
        help="Do not auto-activate the default domain-chip-memory attachment during telegram-agent bootstrap",
    )
    bootstrap_telegram_parser.add_argument("--set-path", help="Specialization path key to activate during bootstrap")
    bootstrap_telegram_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed Telegram user id")
    bootstrap_telegram_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default="pairing",
        help="Inbound DM authorization mode",
    )
    bootstrap_telegram_parser.add_argument(
        "--guide",
        action="store_true",
        help="Print the installer guide with provider choices, BotFather steps, and discovered chips/paths",
    )
    bootstrap_telegram_parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for provider, Telegram, and chip/path choices instead of requiring every flag upfront",
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

    diagnostics_parser = subparsers.add_parser("diagnostics", help="Passively scan logs and write Obsidian diagnostics")
    diagnostics_subparsers = diagnostics_parser.add_subparsers(dest="diagnostics_command", required=True)
    diagnostics_scan_parser = diagnostics_subparsers.add_parser(
        "scan",
        help="Parse Builder, memory, Researcher, and adjacent subsystem logs",
    )
    diagnostics_scan_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    diagnostics_scan_parser.add_argument("--logs-root", help="Override or add an explicit logs root/file to scan")
    diagnostics_scan_parser.add_argument("--output-dir", help="Directory for Obsidian-flavored markdown output")
    diagnostics_scan_parser.add_argument("--max-lines-per-file", type=int, default=2000, help="Tail window per log source")
    diagnostics_scan_parser.add_argument("--recurring-threshold", type=int, default=2, help="Count needed to mark a signature recurring")
    diagnostics_scan_parser.add_argument("--no-write", action="store_true", help="Do not write markdown; only print the scan result")
    diagnostics_scan_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    status_parser = subparsers.add_parser("status", help="Show unified runtime, bridge, and attachment state")
    status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    mission_parser = subparsers.add_parser("mission", help="Inspect mission control and task-specific operator plans")
    mission_subparsers = mission_parser.add_subparsers(dest="mission_command", required=True)
    mission_status_parser = mission_subparsers.add_parser(
        "status",
        help="Show the current mission-control snapshot",
    )
    mission_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    mission_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    mission_plan_parser = mission_subparsers.add_parser(
        "plan",
        help="Show the selected system, harness, recipe, and blockers for one task",
    )
    mission_plan_parser.add_argument("task", help="Task description to plan against mission control")
    mission_plan_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    mission_plan_parser.add_argument("--harness-id", help="Force a specific harness id")
    mission_plan_parser.add_argument("--recipe", help="Force a named harness recipe")
    mission_plan_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

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
    operator_issue_pairing_code_parser = operator_subparsers.add_parser("issue-pairing-code", help="Issue a short-lived pairing code")
    operator_issue_pairing_code_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_issue_pairing_code_parser.add_argument("external_user_id")
    operator_issue_pairing_code_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_issue_pairing_code_parser.add_argument("--reason", help="Short audit reason for issuing this code")
    operator_approve_pairing_code_parser = operator_subparsers.add_parser("approve-pairing-code", help="Approve a pairing by consuming a short-lived code")
    operator_approve_pairing_code_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_approve_pairing_code_parser.add_argument("external_user_id")
    operator_approve_pairing_code_parser.add_argument("code")
    operator_approve_pairing_code_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_approve_pairing_code_parser.add_argument("--display-name", help="Friendly display name")
    operator_approve_pairing_code_parser.add_argument("--reason", help="Short audit reason for this approval")
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
    operator_personality_parser = operator_subparsers.add_parser(
        "personality",
        help="Show personality subsystem overview or inspect one human's typed personality state",
    )
    operator_personality_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_personality_parser.add_argument("--human-id", help="Inspect one human id instead of the global overview")
    operator_personality_parser.add_argument("--observation-limit", type=int, default=10, help="Observation rows to include for one human")
    operator_personality_parser.add_argument("--evolution-limit", type=int, default=10, help="Evolution rows to include for one human")
    operator_personality_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_observer_packets_parser = operator_subparsers.add_parser(
        "observer-packets",
        help="Show persisted observer packet records",
    )
    operator_observer_packets_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_observer_packets_parser.add_argument("--limit", type=int, default=50, help="Number of packet rows to show")
    operator_observer_packets_parser.add_argument("--kind", help="Filter to one packet kind")
    operator_observer_packets_parser.add_argument("--include-archived", action="store_true", help="Include archived packet rows")
    operator_observer_packets_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_export_observer_packets_parser = operator_subparsers.add_parser(
        "export-observer-packets",
        help="Write an observer packet handoff bundle for external consumption",
    )
    operator_export_observer_packets_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_export_observer_packets_parser.add_argument("--limit", type=int, default=200, help="Maximum packets to export")
    operator_export_observer_packets_parser.add_argument("--kind", help="Filter export to one packet kind")
    operator_export_observer_packets_parser.add_argument("--include-archived", action="store_true", help="Include archived packet rows")
    operator_export_observer_packets_parser.add_argument("--write", help="Explicit path for the exported JSON bundle")
    operator_export_observer_packets_parser.add_argument("--reason", help="Short audit reason for this export")
    operator_export_observer_packets_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_handoff_observer_parser = operator_subparsers.add_parser(
        "handoff-observer",
        help="Run the observer packet handoff against a chip packets hook",
    )
    operator_handoff_observer_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_handoff_observer_parser.add_argument("--chip-key", help="Explicit chip key to run. Defaults to the first active chip exposing packets.")
    operator_handoff_observer_parser.add_argument("--limit", type=int, default=200, help="Maximum packets to hand off")
    operator_handoff_observer_parser.add_argument("--kind", help="Filter handoff to one packet kind")
    operator_handoff_observer_parser.add_argument("--include-archived", action="store_true", help="Include archived packet rows")
    operator_handoff_observer_parser.add_argument("--write-bundle", help="Explicit path for the handoff bundle JSON")
    operator_handoff_observer_parser.add_argument("--write-result", help="Explicit path for the chip result JSON")
    operator_handoff_observer_parser.add_argument("--reason", help="Short audit reason for this handoff")
    operator_handoff_observer_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_observer_handoffs_parser = operator_subparsers.add_parser(
        "observer-handoffs",
        help="Show typed observer handoff records",
    )
    operator_observer_handoffs_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_observer_handoffs_parser.add_argument("--limit", type=int, default=20, help="Number of handoff rows to show")
    operator_observer_handoffs_parser.add_argument("--chip-key", help="Filter to one chip key")
    operator_observer_handoffs_parser.add_argument("--status", help="Filter to one handoff status")
    operator_observer_handoffs_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
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
    gateway_simulate_parser.add_argument(
        "--origin",
        choices=("simulation", "telegram-runtime"),
        default="simulation",
        help="Label generated Builder traces as synthetic simulation or real Telegram runtime bridge traffic",
    )
    gateway_simulate_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_ask_telegram_parser = gateway_subparsers.add_parser(
        "ask-telegram",
        help="Send one synthetic DM through the Telegram runtime path and print Spark's reply",
    )
    gateway_ask_telegram_parser.add_argument("message", help="Telegram DM text to inject into the runtime")
    gateway_ask_telegram_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_ask_telegram_parser.add_argument("--user-id", help="Telegram user id to simulate")
    gateway_ask_telegram_parser.add_argument("--username", help="Telegram username to simulate")
    gateway_ask_telegram_parser.add_argument("--chat-id", help="Explicit Telegram chat id override")
    gateway_ask_telegram_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_shadow_telegram_parser = gateway_subparsers.add_parser(
        "shadow-telegram",
        help="Run one Builder-side Telegram shadow-validation turn while spark-telegram-bot remains the live ingress owner",
    )
    gateway_shadow_telegram_parser.add_argument("message", help="Telegram DM text to inject into the runtime")
    gateway_shadow_telegram_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_shadow_telegram_parser.add_argument("--user-id", help="Telegram user id to simulate")
    gateway_shadow_telegram_parser.add_argument("--username", help="Telegram username to simulate")
    gateway_shadow_telegram_parser.add_argument("--chat-id", help="Explicit Telegram chat id override")
    gateway_shadow_telegram_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_shadow_telegram_pack_parser = gateway_subparsers.add_parser(
        "shadow-telegram-pack",
        help="Run a repeatable pack of Builder-side Telegram shadow-validation prompts",
    )
    gateway_shadow_telegram_pack_parser.add_argument("pack_file", help="Path to a .json or line-delimited prompt pack")
    gateway_shadow_telegram_pack_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_shadow_telegram_pack_parser.add_argument("--user-id", help="Default Telegram user id to simulate")
    gateway_shadow_telegram_pack_parser.add_argument("--username", help="Default Telegram username to simulate")
    gateway_shadow_telegram_pack_parser.add_argument("--chat-id", help="Default explicit Telegram chat id override")
    gateway_shadow_telegram_pack_parser.add_argument("--output", help="Optional JSON file path to persist the shadow-validation results")
    gateway_shadow_telegram_pack_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
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

    drafts_parser = subparsers.add_parser("drafts", help="Inspect bot drafts saved per user/channel")
    drafts_subparsers = drafts_parser.add_subparsers(dest="drafts_command", required=True)
    drafts_list_parser = drafts_subparsers.add_parser("list", help="List recent drafts for a user")
    drafts_list_parser.add_argument("--user-id", required=True, help="External user id")
    drafts_list_parser.add_argument("--channel", default="telegram", help="Channel kind (default: telegram)")
    drafts_list_parser.add_argument("--limit", type=int, default=10, help="Max drafts to show")
    drafts_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    drafts_list_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    drafts_show_parser = drafts_subparsers.add_parser("show", help="Show a specific draft by handle")
    drafts_show_parser.add_argument("--user-id", required=True, help="External user id")
    drafts_show_parser.add_argument("--channel", default="telegram", help="Channel kind (default: telegram)")
    drafts_show_parser.add_argument("handle", help="Draft handle (e.g. D-7f3a)")
    drafts_show_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    instr_parser = subparsers.add_parser("instructions", help="Manage persistent per-user instructions injected into prompts")
    instr_subparsers = instr_parser.add_subparsers(dest="instructions_command", required=True)

    instr_list_parser = instr_subparsers.add_parser("list", help="List active instructions for a user")
    instr_list_parser.add_argument("--user-id", required=True, help="External user id (e.g. telegram user id)")
    instr_list_parser.add_argument("--channel", default="telegram", help="Channel kind (default: telegram)")
    instr_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    instr_list_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    instr_add_parser = instr_subparsers.add_parser("add", help="Add an instruction for a user")
    instr_add_parser.add_argument("--user-id", required=True, help="External user id")
    instr_add_parser.add_argument("--channel", default="telegram", help="Channel kind (default: telegram)")
    instr_add_parser.add_argument("--text", required=True, help="The instruction text")
    instr_add_parser.add_argument("--source", default="explicit", help="Source label (default: explicit)")
    instr_add_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    instr_archive_parser = instr_subparsers.add_parser("archive", help="Archive a single instruction by id")
    instr_archive_parser.add_argument("instruction_id", help="The instruction id to archive")
    instr_archive_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    chips_parser = subparsers.add_parser("chips", help="Inspect chip routing decisions for a given message")
    chips_subparsers = chips_parser.add_subparsers(dest="chips_command", required=True)
    chips_why_parser = chips_subparsers.add_parser("why", help="Show which active chips would fire for a message and why")
    chips_why_parser.add_argument("message", help="The user message to classify")
    chips_why_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    chips_why_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    chips_why_parser.add_argument("--history", default="", help="Recent conversation history text to score against (lower weight)")
    chips_why_parser.add_argument("--recent-chip", action="append", default=[], help="Chip key recently selected (sticky boost). Repeat for multiple.")

    loops_parser = subparsers.add_parser("loops", help="Run recursive self-improving loops against a chip")
    loops_subparsers = loops_parser.add_subparsers(dest="loops_command", required=True)
    loops_run_parser = loops_subparsers.add_parser("run", help="Run N rounds of suggest/evaluate against a chip")
    loops_run_parser.add_argument("--chip", required=True, help="Chip key (e.g. domain-chip-brand-sentiment-tracking)")
    loops_run_parser.add_argument("--rounds", type=int, default=3, help="Number of rounds (default 3)")
    loops_run_parser.add_argument("--suggest-limit", type=int, default=3, help="Max candidates per round (default 3)")
    loops_run_parser.add_argument("--pause-seconds", type=float, default=0.0, help="Sleep between rounds")
    loops_run_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    loops_run_parser.add_argument("--json", action="store_true", help="Emit JSON result")

    chips_create_parser = chips_subparsers.add_parser("create", help="Create a new domain chip from a natural-language prompt")
    chips_create_parser.add_argument("--prompt", required=True, help="Natural-language description of the chip to create")
    chips_create_parser.add_argument("--output-dir", default=None, help="Directory to scaffold into (default: C:/Users/USER/Desktop)")
    chips_create_parser.add_argument("--chip-labs-root", default=None, help="Path to spark-domain-chip-labs (default: C:/Users/USER/Desktop/spark-domain-chip-labs)")
    chips_create_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    chips_create_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

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
    attachments_run_hook_parser.add_argument("hook", help="Hook name to run")
    attachments_run_hook_parser.add_argument("--chip-key", help="Chip key to run. Defaults to the first active chip that supports the hook.")
    attachments_run_hook_parser.add_argument("--payload-json", default="{}", help="JSON payload to send to the hook")
    attachments_run_hook_parser.add_argument("--payload-file", help="Path to a JSON payload file to send to the hook")
    attachments_run_hook_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_run_hook_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    browser_parser = subparsers.add_parser("browser", help="Run governed browser capability hooks")
    browser_subparsers = browser_parser.add_subparsers(dest="browser_command", required=True)
    browser_status_parser = browser_subparsers.add_parser("status", help="Query the browser capability runtime")
    browser_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    browser_status_parser.add_argument("--chip-key", help="Chip key to run. Defaults to the first active chip that supports the hook.")
    browser_status_parser.add_argument("--browser-family", default="brave", help="Target browser family")
    browser_status_parser.add_argument("--profile-key", default="spark-default", help="Target browser profile key")
    browser_status_parser.add_argument("--profile-mode", default="dedicated", help="Target browser profile mode")
    browser_status_parser.add_argument("--agent-id", help="Agent id to include in the hook request")
    browser_status_parser.add_argument("--write-payload", help="Write the hook payload to this path")
    browser_status_parser.add_argument("--write-result", help="Write the hook result to this path")
    browser_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    browser_snapshot_parser = browser_subparsers.add_parser("page-snapshot", help="Capture a governed browser page snapshot")
    browser_snapshot_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    browser_snapshot_parser.add_argument("--chip-key", help="Chip key to run. Defaults to the first active chip that supports the hook.")
    browser_snapshot_parser.add_argument("--browser-family", default="brave", help="Target browser family")
    browser_snapshot_parser.add_argument("--profile-key", default="spark-default", help="Target browser profile key")
    browser_snapshot_parser.add_argument("--profile-mode", default="dedicated", help="Target browser profile mode")
    browser_snapshot_parser.add_argument("--agent-id", help="Agent id to include in the hook request")
    browser_snapshot_parser.add_argument("--origin", required=True, help="Origin or page URL to scope the snapshot")
    browser_snapshot_parser.add_argument("--allowed-domain", action="append", default=[], help="Allowed domain for the hook request")
    browser_snapshot_parser.add_argument("--sensitive-domain", action="store_true", help="Mark the page as sensitive for policy purposes")
    browser_snapshot_parser.add_argument("--operator-required", action="store_true", help="Mark the request as operator-only in policy context")
    browser_snapshot_parser.add_argument("--max-text-characters", type=int, default=500, help="Maximum visible text characters to request")
    browser_snapshot_parser.add_argument("--max-controls", type=int, default=10, help="Maximum control count to request")
    browser_snapshot_parser.add_argument("--write-payload", help="Write the hook payload to this path")
    browser_snapshot_parser.add_argument("--write-result", help="Write the hook result to this path")
    browser_snapshot_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

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
    memory_status_parser = memory_subparsers.add_parser(
        "status",
        help="Show Spark memory runtime readiness, recent outcomes, and watchtower memory counts",
    )
    memory_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_lookup_parser = memory_subparsers.add_parser(
        "lookup-current-state",
        help="Read one structured current-state fact directly through the Domain Chip Memory bridge",
    )
    memory_lookup_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_lookup_parser.add_argument("--sdk-module", help="Override the SDK module for this lookup")
    memory_lookup_parser.add_argument("--subject", required=True, help="Structured memory subject to read")
    memory_lookup_parser.add_argument("--predicate", required=True, help="Structured memory predicate to read")
    memory_lookup_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_inspect_human_parser = memory_subparsers.add_parser(
        "inspect-human",
        help="Show current structured memory facts and recent memory activity for one Builder human id",
    )
    memory_inspect_human_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_inspect_human_parser.add_argument("--sdk-module", help="Override the SDK module for this inspection")
    memory_inspect_human_parser.add_argument("--human-id", required=True, help="Builder human id to inspect")
    memory_inspect_human_parser.add_argument("--event-limit", type=int, default=10, help="Maximum recent memory events to include")
    memory_inspect_human_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
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
    memory_run_maintenance_parser = memory_subparsers.add_parser(
        "run-sdk-maintenance",
        help="Run live SDK memory maintenance and record active-state lifecycle counts",
    )
    memory_run_maintenance_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_run_maintenance_parser.add_argument("--sdk-module", help="Override the SDK module for this maintenance run")
    memory_run_maintenance_parser.add_argument("--now", help="Override maintenance clock timestamp for deterministic runs")
    memory_run_maintenance_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_compile_kb_parser = memory_subparsers.add_parser(
        "compile-telegram-kb",
        help="Compile a Spark KB/wiki vault directly from Builder Telegram state.db events",
    )
    memory_compile_kb_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_compile_kb_parser.add_argument("--output-dir", help="Knowledge-base output directory")
    memory_compile_kb_parser.add_argument("--limit", type=int, default=25, help="Maximum Telegram conversations to scan from Builder state.db")
    memory_compile_kb_parser.add_argument("--chat-id", help="Restrict the compile to one Telegram chat id")
    memory_compile_kb_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for KB compilation")
    memory_compile_kb_parser.add_argument("--repo-source", action="append", default=[], help="Additional repo source file to include in the KB")
    memory_compile_kb_parser.add_argument(
        "--repo-source-manifest",
        action="append",
        default=[],
        help="Manifest file listing repo sources to include in the KB",
    )
    memory_compile_kb_parser.add_argument("--write", help="Optional output path for the generated KB compile JSON payload")
    memory_compile_kb_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_regression_parser = memory_subparsers.add_parser(
        "run-telegram-regression",
        help="Run a broad Telegram memory regression matrix, inspect memory, and compile the KB/wiki bundle",
    )
    memory_regression_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_regression_parser.add_argument("--output-dir", help="Regression artifact output directory")
    memory_regression_parser.add_argument("--user-id", help="Explicit Telegram user id to simulate")
    memory_regression_parser.add_argument("--username", help="Telegram username to simulate")
    memory_regression_parser.add_argument("--chat-id", help="Explicit Telegram chat id override")
    memory_regression_parser.add_argument("--benchmark-pack", action="append", default=[], help="Restrict the regression run to one or more named Telegram benchmark packs")
    memory_regression_parser.add_argument("--case-id", action="append", default=[], help="Restrict the regression run to one or more case ids")
    memory_regression_parser.add_argument("--category", action="append", default=[], help="Restrict the regression run to one or more case categories")
    memory_regression_parser.add_argument("--baseline", action="append", default=[], help="Restrict architecture comparison to one or more named memory baselines")
    memory_regression_parser.add_argument("--kb-limit", type=int, default=25, help="Maximum Telegram conversations to scan when compiling the KB")
    memory_regression_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for KB compilation")
    memory_regression_parser.add_argument("--write", help="Optional output path for the regression summary JSON payload")
    memory_regression_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_architecture_benchmark_parser = memory_subparsers.add_parser(
        "benchmark-architectures",
        help="Benchmark Builder's memory substrate against the domain-chip-memory ProductMemory architecture variants",
    )
    memory_architecture_benchmark_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_architecture_benchmark_parser.add_argument("--output-dir", help="Benchmark artifact output directory")
    memory_architecture_benchmark_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for architecture benchmarking")
    memory_architecture_benchmark_parser.add_argument("--baseline", action="append", default=[], help="Restrict ProductMemory benchmarking to one or more named baselines")
    memory_architecture_benchmark_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    memory_architecture_soak_parser = memory_subparsers.add_parser(
        "soak-architectures",
        help="Run a rotating Telegram benchmark-pack suite and aggregate three-way architecture comparison results",
    )
    memory_architecture_soak_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    memory_architecture_soak_parser.add_argument("--output-dir", help="Soak artifact output directory")
    memory_architecture_soak_parser.add_argument("--runs", type=int, default=50, help="Number of benchmark-pack runs to execute")
    memory_architecture_soak_parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Optional delay between runs")
    memory_architecture_soak_parser.add_argument(
        "--run-timeout-seconds",
        type=float,
        default=300.0,
        help="Fail one soak run if its Telegram regression subprocess exceeds this many seconds",
    )
    memory_architecture_soak_parser.add_argument("--user-id", help="Explicit Telegram user id to simulate")
    memory_architecture_soak_parser.add_argument("--username", help="Telegram username to simulate")
    memory_architecture_soak_parser.add_argument("--chat-id", help="Explicit Telegram chat id override")
    memory_architecture_soak_parser.add_argument("--benchmark-pack", action="append", default=[], help="Restrict the soak to one or more named Telegram benchmark packs")
    memory_architecture_soak_parser.add_argument("--case-id", action="append", default=[], help="Restrict the soak to one or more case ids")
    memory_architecture_soak_parser.add_argument("--category", action="append", default=[], help="Restrict the soak to one or more case categories")
    memory_architecture_soak_parser.add_argument("--baseline", action="append", default=[], help="Restrict architecture comparison to one or more named memory baselines")
    memory_architecture_soak_parser.add_argument("--kb-limit", type=int, default=25, help="Maximum Telegram conversations to scan when compiling the KB")
    memory_architecture_soak_parser.add_argument("--validator-root", help="domain-chip-memory repo root used for KB compilation")
    memory_architecture_soak_parser.add_argument("--write", help="Optional output path for the soak summary JSON payload")
    memory_architecture_soak_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
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
    swarm_doctor_parser = swarm_subparsers.add_parser("doctor", help="Diagnose Spark Swarm Telegram and specialization-path readiness")
    swarm_doctor_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
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

    harness_parser = subparsers.add_parser("harness", help="Inspect and exercise Spark harness planning and execution")
    harness_subparsers = harness_parser.add_subparsers(dest="harness_command", required=True)
    harness_status_parser = harness_subparsers.add_parser("status", help="Show harness registry and recent harness runtime state")
    harness_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    harness_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    harness_plan_parser = harness_subparsers.add_parser("plan", help="Plan which harness Spark would use for a task")
    harness_plan_parser.add_argument("task", help="Task description to classify into a harness")
    harness_plan_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    harness_plan_parser.add_argument("--channel-kind", help="Optional channel kind context")
    harness_plan_parser.add_argument("--session-id", help="Optional session id")
    harness_plan_parser.add_argument("--human-id", help="Optional human id")
    harness_plan_parser.add_argument("--agent-id", help="Optional agent id")
    harness_plan_parser.add_argument("--harness-id", help="Force a specific harness id instead of routing automatically")
    harness_plan_parser.add_argument("--recipe", help="Use a named harness recipe instead of raw routing")
    harness_plan_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    harness_execute_parser = harness_subparsers.add_parser("execute", help="Execute a harness task envelope through the harness runtime")
    harness_execute_parser.add_argument("task", help="Task description to execute through the selected harness")
    harness_execute_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    harness_execute_parser.add_argument("--channel-kind", help="Optional channel kind context")
    harness_execute_parser.add_argument("--session-id", help="Optional session id")
    harness_execute_parser.add_argument("--human-id", help="Optional human id")
    harness_execute_parser.add_argument("--agent-id", help="Optional agent id")
    harness_execute_parser.add_argument("--harness-id", help="Force a specific harness id instead of routing automatically")
    harness_execute_parser.add_argument("--recipe", help="Use a named harness recipe instead of raw routing")
    harness_execute_parser.add_argument(
        "--then-harness-id",
        action="append",
        help="Append a follow-up harness after the primary one; repeat to build a small execution chain",
    )
    harness_execute_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    agent_parser = subparsers.add_parser("agent", help="Inspect agent and workspace state")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_inspect_parser = agent_subparsers.add_parser("inspect", help="Inspect current workspace identity state")
    agent_inspect_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_inspect_parser.add_argument("--human-id", help="Inspect one canonical agent instead of the global workspace summary")
    agent_inspect_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    agent_rename_parser = agent_subparsers.add_parser("rename", help="Rename one canonical agent without changing its agent id")
    agent_rename_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_rename_parser.add_argument("--human-id", required=True, help="Human id owning the canonical agent")
    agent_rename_parser.add_argument("--name", required=True, help="New display name for the agent")
    agent_rename_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    agent_link_swarm_parser = agent_subparsers.add_parser("link-swarm", help="Link or canonicalize one human's agent to a Spark Swarm agent id")
    agent_link_swarm_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_link_swarm_parser.add_argument("--human-id", required=True, help="Human id owning the Builder-side canonical agent")
    agent_link_swarm_parser.add_argument("--swarm-agent-id", required=True, help="Spark Swarm agent id to canonicalize onto")
    agent_link_swarm_parser.add_argument("--agent-name", required=True, help="Agent display name to use after link")
    agent_link_swarm_parser.add_argument("--confirmed-at", help="Confirmed timestamp for the inbound Spark Swarm name update")
    agent_link_swarm_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    agent_import_swarm_parser = agent_subparsers.add_parser(
        "import-swarm",
        help="Fetch and canonicalize Spark Swarm identity from an external hook-backed runtime",
    )
    agent_import_swarm_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_import_swarm_parser.add_argument("--human-id", required=True, help="Human id whose Spark Swarm identity should be imported")
    agent_import_swarm_parser.add_argument("--chip-key", help="Explicit chip key exposing the identity hook")
    agent_import_swarm_parser.add_argument("--reason", default="spark swarm identity import", help="Operator reason recorded in history")
    agent_import_swarm_parser.add_argument("--write-payload", help="Optional path to write the import request payload")
    agent_import_swarm_parser.add_argument("--write-result", help="Optional path to write the hook execution result")
    agent_import_swarm_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    agent_import_personality_parser = agent_subparsers.add_parser(
        "import-personality",
        help="Fetch and persist agent personality state from an external hook-backed runtime",
    )
    agent_import_personality_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_import_personality_parser.add_argument("--human-id", required=True, help="Human id whose agent personality should be imported")
    agent_import_personality_parser.add_argument("--chip-key", help="Explicit chip key exposing the personality hook")
    agent_import_personality_parser.add_argument("--reason", default="personality import", help="Operator reason recorded in history")
    agent_import_personality_parser.add_argument("--write-payload", help="Optional path to write the import request payload")
    agent_import_personality_parser.add_argument("--write-result", help="Optional path to write the hook execution result")
    agent_import_personality_parser.add_argument("--write-evolver-state", help="Optional path to write the imported evolver state")
    agent_import_personality_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    agent_migrate_persona_parser = agent_subparsers.add_parser(
        "migrate-legacy-personality",
        help="Move legacy human-scoped personality deltas into the canonical agent persona base",
    )
    agent_migrate_persona_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_migrate_persona_parser.add_argument("--human-id", required=True, help="Human id whose legacy personality state should be migrated")
    agent_migrate_persona_parser.add_argument("--keep-overlay", action="store_true", help="Keep the human-scoped overlay after seeding the agent persona")
    agent_migrate_persona_parser.add_argument("--force", action="store_true", help="Overwrite an existing agent persona with the migrated legacy state")
    agent_migrate_persona_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    identity_parser = subparsers.add_parser(
        "identity",
        help="Manage cross-surface identity aliases (one agent across telegram + tui + Ã¢â‚¬Â¦)",
    )
    identity_subparsers = identity_parser.add_subparsers(dest="identity_command", required=True)

    identity_link_parser = identity_subparsers.add_parser(
        "link",
        help="Link one (alias_channel, alias_user) to a primary (channel, user) so they share an agent",
    )
    identity_link_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    identity_link_parser.add_argument(
        "--primary",
        required=True,
        help="Primary identity in 'channel:user' form (e.g. telegram:123456789)",
    )
    identity_link_parser.add_argument(
        "--as",
        dest="as_",
        required=True,
        help="Alias identity in 'channel:user' form (e.g. tui:local-operator)",
    )
    identity_link_parser.add_argument("--created-by", default="cli", help="Audit field for who created the link")
    identity_link_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    identity_unlink_parser = identity_subparsers.add_parser(
        "unlink",
        help="Remove an identity alias",
    )
    identity_unlink_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    identity_unlink_parser.add_argument(
        "alias",
        help="Alias identity in 'channel:user' form (e.g. tui:local-operator)",
    )
    identity_unlink_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    identity_list_parser = identity_subparsers.add_parser(
        "list",
        help="List all registered identity aliases",
    )
    identity_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    identity_list_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

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


def _apply_bootstrap_attachment_preferences(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    chip_roots: list[str],
    path_roots: list[str],
    activate_chip_keys: list[str],
    pin_chip_keys: list[str],
    active_path_key: str | None,
    default_memory_chip: bool,
) -> tuple[list[str], object]:
    notes: list[str] = []
    changed = False

    for root in chip_roots:
        add_attachment_root(config_manager, target="chips", root=root)
        notes.append(f"added chip root {root}")
        changed = True
    for root in path_roots:
        add_attachment_root(config_manager, target="paths", root=root)
        notes.append(f"added specialization path root {root}")
        changed = True

    if default_memory_chip:
        available_chip_keys = {
            record.key
            for record in attachment_status(config_manager).records
            if record.kind == "chip"
        }
        if "domain-chip-memory" in available_chip_keys:
            activate_chip(config_manager, chip_key="domain-chip-memory")
            notes.append("activated default memory chip domain-chip-memory")
            changed = True
        else:
            notes.append("default memory chip domain-chip-memory not found; continuing without it")

    for chip_key in activate_chip_keys:
        activate_chip(config_manager, chip_key=chip_key)
        notes.append(f"activated chip {chip_key}")
        changed = True
    for chip_key in pin_chip_keys:
        pin_chip(config_manager, chip_key=chip_key)
        notes.append(f"pinned chip {chip_key}")
        changed = True
    if active_path_key:
        set_active_path(config_manager, path_key=active_path_key)
        notes.append(f"set active specialization path {active_path_key}")
        changed = True

    if changed:
        snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
        notes.append(f"refreshed attachment snapshot at {snapshot.snapshot_path}")
        return notes, snapshot
    return notes, build_attachment_snapshot(config_manager)


def _build_bootstrap_provider_choices() -> list[dict[str, str | None]]:
    choices: list[dict[str, str | None]] = []
    for spec in list_provider_specs():
        if not spec.supports_api_key_connect:
            continue
        choices.append(
            {
                "provider_id": spec.id,
                "display_name": spec.display_name,
                "default_model": spec.default_model,
                "default_base_url": spec.default_base_url,
                "api_key_env": spec.default_api_key_env,
            }
        )
    return choices


def _prompt_bootstrap_text(
    label: str,
    *,
    default: str | None = None,
    allow_blank: bool = False,
) -> str | None:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_blank:
            return None
        print("A value is required.")


def _prompt_bootstrap_choice(
    label: str,
    choices: list[str],
    *,
    default: str | None = None,
    allow_blank: bool = False,
) -> str | None:
    choice_text = "/".join(choices)
    while True:
        value = _prompt_bootstrap_text(
            f"{label} ({choice_text})",
            default=default,
            allow_blank=allow_blank,
        )
        if value is None:
            return None
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(choices)}")


def _prompt_bootstrap_multi_choice(
    label: str,
    choices: list[str],
    *,
    default_values: list[str] | None = None,
) -> list[str]:
    default_text = ",".join(default_values or [])
    while True:
        raw = _prompt_bootstrap_text(
            label,
            default=default_text or None,
            allow_blank=not bool(default_values),
        )
        if raw is None:
            return []
        values = [item.strip() for item in raw.split(",") if item.strip()]
        invalid = [item for item in values if item not in choices]
        if not invalid:
            return values
        print(f"Unknown choices: {', '.join(invalid)}")
        print(f"Available choices: {', '.join(choices)}")


def _prompt_bootstrap_secret(
    label: str,
    *,
    default_env_name: str | None,
    existing_value: str | None,
    existing_env_name: str | None,
) -> tuple[str | None, str | None]:
    default_source = "direct" if existing_value else "env"
    source = _prompt_bootstrap_choice(
        f"{label} source",
        ["env", "direct"],
        default=default_source,
    )
    if source == "env":
        env_name = _prompt_bootstrap_text(
            f"{label} env var",
            default=existing_env_name or default_env_name,
        )
        return None, env_name
    prompt = f"{label}: "
    while True:
        secret_value = getpass.getpass(prompt).strip()
        if secret_value:
            return secret_value, None
        if existing_value:
            return existing_value, None
        print("A value is required.")


def _configure_interactive_provider(
    *,
    provider_label: str,
    provider_id: str,
    existing_api_key: str | None,
    existing_api_key_env: str | None,
    existing_model: str | None,
    existing_base_url: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    spec = get_provider_spec(provider_id)
    api_key, api_key_env = _prompt_bootstrap_secret(
        f"{provider_label} API key",
        default_env_name=spec.default_api_key_env,
        existing_value=existing_api_key,
        existing_env_name=existing_api_key_env,
    )
    model = _prompt_bootstrap_text(
        f"{provider_label} model",
        default=existing_model or spec.default_model,
        allow_blank=existing_model is None and spec.default_model is None,
    )
    prompt_for_base_url = provider_id == "custom" or bool(existing_base_url or spec.default_base_url)
    base_url = existing_base_url
    if prompt_for_base_url:
        base_url = _prompt_bootstrap_text(
            f"{provider_label} base URL",
            default=existing_base_url or spec.default_base_url,
            allow_blank=provider_id != "custom",
        )
    return api_key, api_key_env, model, base_url


def _collect_interactive_bootstrap_answers(
    *,
    args: argparse.Namespace,
    attachment_snapshot: object,
) -> None:
    provider_choices = [choice["provider_id"] for choice in _build_bootstrap_provider_choices()]
    configured_default_provider = None
    if getattr(args, "provider", None) and args.provider != "custom":
        configured_default_provider = args.provider
    default_provider = configured_default_provider or ("minimax" if "minimax" in provider_choices else provider_choices[0])
    args.provider = _prompt_bootstrap_choice("Primary provider", provider_choices, default=default_provider) or default_provider
    args.api_key, args.api_key_env, args.model, args.base_url = _configure_interactive_provider(
        provider_label="Primary provider",
        provider_id=args.provider,
        existing_api_key=args.api_key,
        existing_api_key_env=args.api_key_env,
        existing_model=args.model,
        existing_base_url=args.base_url,
    )

    fallback_default = args.fallback_provider if getattr(args, "fallback_provider", None) else None
    args.fallback_provider = _prompt_bootstrap_choice(
        "Fallback provider",
        provider_choices,
        default=fallback_default,
        allow_blank=True,
    )
    if args.fallback_provider:
        args.fallback_api_key, args.fallback_api_key_env, args.fallback_model, args.fallback_base_url = _configure_interactive_provider(
            provider_label="Fallback provider",
            provider_id=args.fallback_provider,
            existing_api_key=args.fallback_api_key,
            existing_api_key_env=args.fallback_api_key_env,
            existing_model=args.fallback_model,
            existing_base_url=args.fallback_base_url,
        )
    else:
        args.fallback_api_key = None
        args.fallback_api_key_env = None
        args.fallback_model = None
        args.fallback_base_url = None

    args.pairing_mode = _prompt_bootstrap_choice(
        "Telegram pairing mode",
        ["pairing", "allowlist"],
        default=args.pairing_mode,
    ) or args.pairing_mode
    setup_mode = _prompt_bootstrap_choice(
        "Telegram bot setup",
        ["existing", "new"],
        default="existing",
    )
    if setup_mode == "new":
        print("")
        print(render_telegram_botfather_guide(allowed_users=args.allowed_user, pairing_mode=args.pairing_mode))
        print("")
    args.bot_token, args.bot_token_env = _prompt_bootstrap_secret(
        "Telegram bot token",
        default_env_name="TELEGRAM_BOT_TOKEN",
        existing_value=args.bot_token,
        existing_env_name=args.bot_token_env,
    )
    allowed_users = _prompt_bootstrap_text(
        "Allowed Telegram user IDs (comma-separated, leave blank for none)",
        default=",".join(args.allowed_user) or None,
        allow_blank=not bool(args.allowed_user),
    )
    args.allowed_user = [item.strip() for item in (allowed_users or "").split(",") if item.strip()]

    discovered_chip_keys = [record["key"] for record in attachment_snapshot.records if str(record.get("kind")) == "chip"]
    discovered_path_keys = [record["key"] for record in attachment_snapshot.records if str(record.get("kind")) == "path"]
    if discovered_chip_keys:
        print(f"Discovered chips: {', '.join(discovered_chip_keys)}")
        args.activate_chip = _prompt_bootstrap_multi_choice(
            "Active chips (comma-separated, leave blank for none)",
            discovered_chip_keys,
            default_values=args.activate_chip,
        )
        args.pin_chip = _prompt_bootstrap_multi_choice(
            "Pinned chips (comma-separated, leave blank for none)",
            discovered_chip_keys,
            default_values=args.pin_chip,
        )
    if discovered_path_keys:
        print(f"Discovered specialization paths: {', '.join(discovered_path_keys)}")
        active_path_default = args.set_path if args.set_path in discovered_path_keys else None
        args.set_path = _prompt_bootstrap_choice(
            "Active specialization path",
            discovered_path_keys,
            default=active_path_default,
            allow_blank=True,
        )


def _configure_bootstrap_fallback_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider_id: str | None,
    api_key: str | None,
    api_key_env: str | None,
    model: str | None,
    base_url: str | None,
    primary_provider_id: str,
) -> tuple[str | None, str | None]:
    if not provider_id:
        config_manager.set_path("providers.fallback_provider", None)
        return None, None
    if provider_id == primary_provider_id:
        raise ValueError("Fallback provider must differ from the primary provider.")
    if api_key_env:
        _sync_secret_from_env_if_present(config_manager, api_key_env)
    if provider_id == "custom" and not base_url:
        raise ValueError("Fallback provider 'custom' requires --fallback-base-url.")
    result = connect_provider(
        config_manager=config_manager,
        state_db=state_db,
        provider=provider_id,
        api_key=api_key,
        api_key_env=api_key_env,
        model=model,
        base_url=base_url,
    )
    config_manager.set_path("providers.fallback_provider", provider_id)
    return provider_id, result


def handle_bootstrap_telegram_agent(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    created = config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    setup_notes = _apply_setup_integrations(config_manager, args)
    attachment_notes, attachment_snapshot = _apply_bootstrap_attachment_preferences(
        config_manager,
        state_db,
        chip_roots=args.chip_root,
        path_roots=args.path_root,
        activate_chip_keys=[] if args.interactive else args.activate_chip,
        pin_chip_keys=[] if args.interactive else args.pin_chip,
        active_path_key=None if args.interactive else args.set_path,
        default_memory_chip=not args.no_default_memory_chip,
    )
    setup_notes.extend(attachment_notes)
    if created:
        setup_notes.insert(0, "created config, env, and state bootstrap")
    else:
        setup_notes.insert(0, "verified existing config, env, and state bootstrap")

    if args.guide:
        existing_bot_example = (
            "spark-intelligence bootstrap telegram-agent "
            f"--home {config_manager.paths.home} "
            "--provider minimax --api-key-env MINIMAX_API_KEY "
            "--model MiniMax-M2.7 --base-url https://api.minimax.io/v1 "
            "--bot-token-env TELEGRAM_BOT_TOKEN"
        )
        fallback_bot_example = (
            "spark-intelligence bootstrap telegram-agent "
            f"--home {config_manager.paths.home} "
            "--provider minimax --api-key-env MINIMAX_API_KEY "
            "--model MiniMax-M2.7 --base-url https://api.minimax.io/v1 "
            "--fallback-provider anthropic --fallback-api-key-env ANTHROPIC_API_KEY "
            "--fallback-model claude-opus-4-6 "
            "--bot-token-env TELEGRAM_BOT_TOKEN"
        )
        guide = BootstrapTelegramAgentGuide(
            home=str(config_manager.paths.home),
            setup_notes=setup_notes,
            provider_choices=_build_bootstrap_provider_choices(),
            discovered_chip_keys=[record["key"] for record in attachment_snapshot.records if str(record.get("kind")) == "chip"],
            discovered_path_keys=[record["key"] for record in attachment_snapshot.records if str(record.get("kind")) == "path"],
            botfather_guide=render_telegram_botfather_guide(
                allowed_users=args.allowed_user,
                pairing_mode=args.pairing_mode,
            ),
            existing_bot_example=existing_bot_example,
            fallback_bot_example=fallback_bot_example,
        )
        print(guide.to_json() if args.json else guide.to_text())
        return 0

    if args.interactive:
        _collect_interactive_bootstrap_answers(
            args=args,
            attachment_snapshot=attachment_snapshot,
        )
        attachment_notes, attachment_snapshot = _apply_bootstrap_attachment_preferences(
            config_manager,
            state_db,
            chip_roots=[],
            path_roots=[],
            activate_chip_keys=args.activate_chip,
            pin_chip_keys=args.pin_chip,
            active_path_key=args.set_path,
            default_memory_chip=False,
        )
        setup_notes.extend(attachment_notes)

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
    try:
        fallback_provider_id, fallback_provider_result = _configure_bootstrap_fallback_provider(
            config_manager=config_manager,
            state_db=state_db,
            provider_id=args.fallback_provider,
            api_key=args.fallback_api_key,
            api_key_env=args.fallback_api_key_env,
            model=args.fallback_model,
            base_url=args.fallback_base_url,
            primary_provider_id=args.provider,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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
        fallback_provider_id=fallback_provider_id,
        fallback_provider_result=fallback_provider_result,
        channel_result=channel_result,
        setup_notes=setup_notes,
        active_chip_keys=attachment_snapshot.active_chip_keys,
        pinned_chip_keys=attachment_snapshot.pinned_chip_keys,
        active_path_key=attachment_snapshot.active_path_key,
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
    config_manager.bootstrap()
    state_db.initialize()
    report = run_doctor(config_manager, state_db)
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 0 if report.ok else 1


def handle_diagnostics_scan(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    report = build_diagnostic_report(
        config_manager,
        logs_root=Path(args.logs_root).expanduser() if args.logs_root else None,
        max_lines_per_file=max(1, int(args.max_lines_per_file)),
        recurring_threshold=max(1, int(args.recurring_threshold)),
        write_markdown=not bool(args.no_write),
        output_dir=Path(args.output_dir).expanduser() if args.output_dir else None,
    )
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 0


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


def handle_operator_issue_pairing_code(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    issued = issue_pairing_code(
        state_db=state_db,
        channel_id=args.channel_id,
        external_user_id=args.external_user_id,
    )
    log_operator_event(
        state_db=state_db,
        action="issue_pairing_code",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{args.external_user_id}",
        reason=args.reason,
        details={"expires_at": issued.expires_at},
    )
    print(f"Pairing code for {issued.channel_id}:{issued.external_user_id}: {issued.code}")
    print(f"Expires at: {issued.expires_at}")
    return 0


def handle_operator_approve_pairing_code(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = consume_pairing_code(
        state_db=state_db,
        channel_id=args.channel_id,
        external_user_id=args.external_user_id,
        code=args.code,
        display_name=args.display_name,
    )
    log_operator_event(
        state_db=state_db,
        action="approve_pairing_code",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{args.external_user_id}",
        reason=args.reason,
        details={"decision": result.decision, "display_name": args.display_name},
    )
    print(result.message)
    return 0 if result.ok else 1


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


def handle_operator_personality(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    report = build_personality_report(
        config_manager=config_manager,
        state_db=state_db,
        human_id=args.human_id,
        observation_limit=args.observation_limit,
        evolution_limit=args.evolution_limit,
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_observer_packets(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = list_observer_packets(
        config_manager=config_manager,
        state_db=state_db,
        limit=args.limit,
        packet_kind=args.kind,
        active_only=not args.include_archived,
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_export_observer_packets(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = export_operator_observer_packets(
        config_manager=config_manager,
        state_db=state_db,
        write_path=args.write,
        limit=args.limit,
        packet_kind=args.kind,
        active_only=not args.include_archived,
    )
    log_operator_event(
        state_db=state_db,
        action="export_observer_packets",
        target_kind="observer_packet_bundle",
        target_ref=str(report.payload.get("write_path") or "observer-packets"),
        reason=args.reason,
        details={
            "packet_kind": args.kind,
            "active_only": not args.include_archived,
            "packet_count": report.payload.get("packet_count"),
        },
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_handoff_observer(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    handoff_id = f"observer-handoff-{uuid4().hex[:12]}"
    run = open_run(
        state_db,
        run_kind="operator:observer_handoff",
        origin_surface="operator_cli",
        summary="Operator started an observer chip handoff.",
        request_id=handoff_id,
        actor_id="local-operator",
        reason_code="observer_handoff_requested",
        facts={
            "chip_key": args.chip_key or "active",
            "hook": "packets",
        },
    )
    payload = build_observer_handoff_payload(
        config_manager=config_manager,
        state_db=state_db,
        handoff_id=handoff_id,
        chip_key=args.chip_key,
        write_path=args.write_bundle,
        limit=args.limit,
        packet_kind=args.kind,
        active_only=not args.include_archived,
    )
    bundle = payload.get("packet_bundle") or {}
    bundle_path = str(bundle.get("write_path") or "")
    packet_count = int(bundle.get("packet_count") or 0)
    payload_summary = {
        "schema_version": payload.get("schema_version"),
        "generated_at": payload.get("generated_at"),
        "workspace_id": payload.get("workspace_id"),
        "hook": payload.get("hook"),
        "target_chip_key": payload.get("target_chip_key"),
        "bundle_path": bundle_path,
        "packet_count": packet_count,
        "counts_by_kind": bundle.get("counts_by_kind") or {},
        "watchtower": payload.get("watchtower") or {},
        "attachments": payload.get("attachments") or {},
    }
    try:
        if args.chip_key:
            execution = run_chip_hook(config_manager, chip_key=args.chip_key, hook="packets", payload=payload)
        else:
            execution = run_first_active_chip_hook(config_manager, hook="packets", payload=payload)
            if execution is None:
                summary = "Observer handoff found no active chip exposing the packets hook."
                record_observer_handoff_record(
                    state_db,
                    handoff_id=handoff_id,
                    chip_key="active",
                    hook="packets",
                    run_id=run.run_id,
                    request_id=run.request_id,
                    bundle_path=bundle_path,
                    result_path=None,
                    packet_count=packet_count,
                    packet_kind_filter=args.kind,
                    active_only=not args.include_archived,
                    status="stalled",
                    summary=summary,
                    error_text="no_active_chip_for_packets_hook",
                    payload=payload_summary,
                    completed_at=bundle.get("generated_at"),
                )
                log_operator_event(
                    state_db=state_db,
                    action="handoff_observer",
                    target_kind="observer_handoff",
                    target_ref=handoff_id,
                    reason=args.reason,
                    details={
                        "status": "stalled",
                        "chip_key": None,
                        "packet_count": packet_count,
                        "bundle_path": bundle_path,
                        "error": "no_active_chip_for_packets_hook",
                    },
                )
                close_run(
                    state_db,
                    run_id=run.run_id,
                    status="stalled",
                    close_reason="no_active_chip_for_packets_hook",
                    summary=summary,
                    facts={"hook": "packets"},
                )
                print(
                    "No active chip exposes hook 'packets'. Activate a chip first or pass --chip-key.",
                    file=sys.stderr,
                )
                return 1
    except ValueError as exc:
        summary = "Observer handoff failed validation before chip execution."
        record_observer_handoff_record(
            state_db,
            handoff_id=handoff_id,
            chip_key=args.chip_key or "active",
            hook="packets",
            run_id=run.run_id,
            request_id=run.request_id,
            bundle_path=bundle_path,
            result_path=None,
            packet_count=packet_count,
            packet_kind_filter=args.kind,
            active_only=not args.include_archived,
            status="stalled",
            summary=summary,
            error_text=str(exc),
            payload=payload_summary,
            completed_at=bundle.get("generated_at"),
        )
        log_operator_event(
            state_db=state_db,
            action="handoff_observer",
            target_kind="observer_handoff",
            target_ref=handoff_id,
            reason=args.reason,
            details={
                "status": "stalled",
                "chip_key": args.chip_key,
                "packet_count": packet_count,
                "bundle_path": bundle_path,
                "error": str(exc),
            },
        )
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="observer_handoff_invalid",
            summary=summary,
            facts={"hook": "packets", "error": str(exc)},
        )
        print(str(exc), file=sys.stderr)
        return 2

    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="operator_cli",
        actor_id="local-operator",
        summary="Operator executed an observer chip handoff via the packets hook.",
        reason_code="observer_handoff",
        keepability="operator_debug_only",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    output_text = execution.to_json()
    screened_output = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=output_text,
        summary="Observer handoff blocked secret-like chip output before operator display.",
        reason_code="observer_handoff_secret_like",
        policy_domain="observer_handoff",
        blocked_stage="operator_output",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    if not screened_output["allowed"]:
        summary = "Observer handoff output was blocked by the secret boundary."
        record_observer_handoff_record(
            state_db,
            handoff_id=handoff_id,
            chip_key=execution.chip_key,
            hook="packets",
            run_id=run.run_id,
            request_id=run.request_id,
            bundle_path=bundle_path,
            result_path=None,
            packet_count=packet_count,
            packet_kind_filter=args.kind,
            active_only=not args.include_archived,
            status="blocked",
            summary=summary,
            exit_code=execution.exit_code,
            error_text=f"secret_boundary_blocked:{screened_output['quarantine_id']}",
            payload=payload_summary,
            completed_at=bundle.get("generated_at"),
        )
        log_operator_event(
            state_db=state_db,
            action="handoff_observer",
            target_kind="observer_handoff",
            target_ref=handoff_id,
            reason=args.reason,
            details={
                "status": "blocked",
                "chip_key": execution.chip_key,
                "packet_count": packet_count,
                "bundle_path": bundle_path,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="operator_cli",
            summary="Observer handoff blocked chip output because it contained secret-like material.",
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
            summary=summary,
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        print(
            "Observer handoff output was blocked because it contained secret-like material. "
            "Review quarantine records instead of raw output.",
            file=sys.stderr,
        )
        return 1

    result_path = Path(args.write_result) if args.write_result else (
        config_manager.paths.home / "artifacts" / "observer-handoffs" / f"{handoff_id}.result.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_payload = execution.to_payload()
    result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=True), encoding="utf-8")
    handoff_status = "completed" if execution.ok else "failed"
    summary = (
        "Observer handoff completed and wrote the chip result artifact."
        if execution.ok
        else "Observer handoff wrote the chip result artifact but the chip returned a non-zero exit code."
    )
    record_observer_handoff_record(
        state_db,
        handoff_id=handoff_id,
        chip_key=execution.chip_key,
        hook="packets",
        run_id=run.run_id,
        request_id=run.request_id,
        bundle_path=bundle_path,
        result_path=str(result_path),
        packet_count=packet_count,
        packet_kind_filter=args.kind,
        active_only=not args.include_archived,
        status=handoff_status,
        summary=summary,
        exit_code=execution.exit_code,
        payload=payload_summary,
        output=execution.output,
        completed_at=bundle.get("generated_at"),
    )
    log_operator_event(
        state_db=state_db,
        action="handoff_observer",
        target_kind="observer_handoff",
        target_ref=handoff_id,
        reason=args.reason,
        details={
            "status": handoff_status,
            "chip_key": execution.chip_key,
            "packet_count": packet_count,
            "bundle_path": bundle_path,
            "result_path": str(result_path),
            "exit_code": execution.exit_code,
        },
    )
    close_run(
        state_db,
        run_id=run.run_id,
        status="closed" if execution.ok else "failed",
        close_reason="observer_handoff_completed" if execution.ok else "observer_handoff_failed",
        summary=summary,
        facts={
            "chip_key": execution.chip_key,
            "hook": execution.hook,
            "packet_count": packet_count,
            "result_path": str(result_path),
            "exit_code": execution.exit_code,
        },
    )
    report_payload = {
        "handoff_id": handoff_id,
        "status": handoff_status,
        "chip_key": execution.chip_key,
        "hook": execution.hook,
        "packet_count": packet_count,
        "packet_kind_filter": args.kind,
        "active_only": not args.include_archived,
        "bundle_path": bundle_path,
        "result_path": str(result_path),
        "counts_by_kind": bundle.get("counts_by_kind") or {},
        "execution": result_payload,
    }
    if args.json:
        print(json.dumps(report_payload, indent=2))
    else:
        print(
            "\n".join(
                [
                    "Observer handoff:",
                    f"- id: {handoff_id}",
                    f"- status: {handoff_status}",
                    f"- chip_key: {execution.chip_key}",
                    f"- packet_count: {packet_count}",
                    f"- bundle_path: {bundle_path}",
                    f"- result_path: {result_path}",
                ]
            )
        )
    return 0 if execution.ok else 1


def handle_operator_observer_handoffs(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = list_observer_handoffs(
        config_manager=config_manager,
        state_db=state_db,
        limit=args.limit,
        chip_key=args.chip_key,
        status=args.status,
    )
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
    browser = _collect_status_browser_payload(config_manager)
    watchtower = build_watchtower_snapshot(state_db)
    system_registry = build_system_registry(config_manager, state_db)
    mission_control = build_mission_control_snapshot(config_manager, state_db)
    harness_registry = build_harness_registry(config_manager, state_db)
    harness_runtime = build_harness_runtime_snapshot(config_manager, state_db)
    active_chip_keys = config_manager.get_path("spark.chips.active_keys", default=[]) or []
    active_path_key = config_manager.get_path("spark.specialization_paths.active_path_key")
    autostart_payload = {
        "enabled": bool(config_manager.get_path("runtime.autostart.enabled", default=False)),
        "platform": config_manager.get_path("runtime.autostart.platform"),
        "task_name": config_manager.get_path("runtime.autostart.task_name"),
        "command": config_manager.get_path("runtime.autostart.command"),
    }
    telegram_gateway_payload = {
        "owner": "spark-telegram-bot",
        "mode": "external_webhook_gateway",
        "contract": "single_owner_webhook",
        "migration_status": "builder_shadow_validation_only",
        "shadow_validation_command": "spark-intelligence gateway ask-telegram \"hello\" --home <home>",
    }

    payload = {
        "doctor": {"ok": doctor_report.ok, "checks": [{"name": check.name, "ok": check.ok, "detail": check.detail} for check in doctor_report.checks]},
        "auth": json.loads(auth_report.to_json()),
        "gateway": json.loads(gateway.to_json()),
        "researcher": json.loads(researcher.to_json()),
        "swarm": json.loads(swarm.to_json()),
        "browser": browser,
        "runtime": {
            "install_profile": config_manager.get_path("runtime.install.profile"),
            "default_gateway_mode": config_manager.get_path("runtime.run.default_gateway_mode"),
            "autostart": autostart_payload,
        },
        "telegram_gateway": telegram_gateway_payload,
        "system_registry": system_registry.to_payload(),
        "mission_control": mission_control.to_payload(),
        "harness_registry": harness_registry.to_payload(),
        "harness_runtime": harness_runtime.to_payload(),
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


def _collect_status_browser_payload(config_manager: ConfigManager) -> dict[str, object] | None:
    payload = build_browser_status_payload(
        config_manager=config_manager,
        browser_family="brave",
        profile_key="spark-default",
        profile_mode="dedicated",
        agent_id=None,
    )
    try:
        execution = run_first_active_chip_hook(config_manager, hook=BROWSER_STATUS_HOOK, payload=payload)
    except ValueError as exc:
        return {
            "status": "unavailable",
            "chip_key": "browser",
            "error_code": "BROWSER_STATUS_INVALID",
            "error_message": str(exc),
        }
    if execution is None:
        return None
    hook_output = execution.output if isinstance(execution.output, dict) else {}
    hook_status = _normalize_browser_hook_status(hook_output)
    hook_error = hook_output.get("error") if isinstance(hook_output.get("error"), dict) else {}
    hook_failed = (not execution.ok) or bool(hook_error) or (
        hook_status is not None and hook_status not in {"succeeded", "completed", "ok", "success"}
    )
    return {
        "status": "failed" if hook_failed else "completed",
        "chip_key": execution.chip_key,
        "hook_status": hook_status or ("failed" if hook_failed else "succeeded"),
        "approval_state": hook_output.get("approval_state") if isinstance(hook_output.get("approval_state"), str) else None,
        "error_code": str(hook_error.get("code") or "").strip() or None,
        "error_message": str(hook_error.get("message") or "").strip() or None,
        "provenance": hook_output.get("provenance") if isinstance(hook_output.get("provenance"), dict) else {},
    }


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
    config_manager.bootstrap()
    state_db.initialize()
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
            simulation=args.origin != "telegram-runtime",
        )
    )
    return 0


def handle_gateway_ask_telegram(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    try:
        print(
            gateway_ask_telegram(
                config_manager=config_manager,
                state_db=state_db,
                message=args.message,
                user_id=args.user_id,
                username=args.username,
                chat_id=args.chat_id,
                as_json=args.json,
            )
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def handle_gateway_shadow_telegram(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    try:
        result = gateway_ask_telegram(
            config_manager=config_manager,
            state_db=state_db,
            message=args.message,
            user_id=args.user_id,
            username=args.username,
            chat_id=args.chat_id,
            as_json=args.json,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "ingress_owner": "spark-telegram-bot",
                    "migration_status": "builder_shadow_validation_only",
                    "result": json.loads(result),
                },
                indent=2,
            )
        )
        return 0
    print("Builder Telegram shadow validation")
    print("- ingress_owner: spark-telegram-bot")
    print("- migration_status: builder_shadow_validation_only")
    print("")
    print(result)
    return 0


def _load_shadow_telegram_pack(path: Path) -> list[dict[str, str | None]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise ValueError("Shadow Telegram pack JSON must be a list.")
        entries: list[dict[str, str | None]] = []
        for item in payload:
            if isinstance(item, str):
                entries.append({"message": item, "user_id": None, "username": None, "chat_id": None})
                continue
            if not isinstance(item, dict):
                raise ValueError("Shadow Telegram pack entries must be strings or objects.")
            message = str(item.get("message") or "").strip()
            if not message:
                raise ValueError("Shadow Telegram pack entries must include a non-empty message.")
            entries.append(
                {
                    "message": message,
                    "user_id": str(item.get("user_id")).strip() if item.get("user_id") is not None else None,
                    "username": str(item.get("username")).strip() if item.get("username") is not None else None,
                    "chat_id": str(item.get("chat_id")).strip() if item.get("chat_id") is not None else None,
                }
            )
        return entries
    entries = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        message = line.strip()
        if not message or message.startswith("#"):
            continue
        entries.append({"message": message, "user_id": None, "username": None, "chat_id": None})
    if not entries:
        raise ValueError("Shadow Telegram pack did not contain any prompts.")
    return entries


def handle_gateway_shadow_telegram_pack(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    try:
        pack_entries = _load_shadow_telegram_pack(Path(args.pack_file))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    results: list[dict[str, object]] = []
    for index, entry in enumerate(pack_entries, start=1):
        try:
            raw = gateway_ask_telegram(
                config_manager=config_manager,
                state_db=state_db,
                message=str(entry["message"] or ""),
                user_id=str(entry["user_id"] or args.user_id or "").strip() or None,
                username=str(entry["username"] or args.username or "").strip() or None,
                chat_id=str(entry["chat_id"] or args.chat_id or "").strip() or None,
                as_json=True,
            )
        except ValueError as exc:
            print(f"Pack entry {index} failed: {exc}", file=sys.stderr)
            return 1
        parsed = json.loads(raw)
        results.append(
            {
                "index": index,
                "message": entry["message"],
                "user_id": parsed.get("user_id"),
                "username": parsed.get("username"),
                "chat_id": parsed.get("chat_id"),
                "result": parsed.get("result"),
            }
        )
    payload = {
        "ingress_owner": "spark-telegram-bot",
        "migration_status": "builder_shadow_validation_only",
        "pack_file": str(Path(args.pack_file)),
        "results": results,
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print("Builder Telegram shadow validation pack")
    print("- ingress_owner: spark-telegram-bot")
    print("- migration_status: builder_shadow_validation_only")
    print(f"- pack_file: {Path(args.pack_file)}")
    if args.output:
        print(f"- output: {Path(args.output)}")
    for item in results:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        print("")
        print(f"[{item['index']}] {item['message']}")
        print(f"- decision: {result.get('decision') or 'unknown'}")
        detail = result.get("detail") if isinstance(result, dict) else {}
        if isinstance(detail, dict):
            bridge_mode = str(detail.get("bridge_mode") or "").strip()
            routing_decision = str(detail.get("routing_decision") or "").strip()
            trace_ref = str(detail.get("trace_ref") or "").strip()
            response_text = str(detail.get("response_text") or "").strip()
            if bridge_mode:
                print(f"- mode: {bridge_mode}")
            if routing_decision:
                print(f"- route: {routing_decision}")
            if trace_ref:
                print(f"- trace_ref: {trace_ref}")
            if response_text:
                print(response_text)
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


def handle_drafts_list(args: argparse.Namespace) -> int:
    from spark_intelligence.bot_drafts import list_recent_drafts

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    drafts = list_recent_drafts(
        state_db,
        external_user_id=args.user_id,
        channel_kind=args.channel,
        limit=args.limit,
    )
    if args.json:
        import json as _json
        print(_json.dumps([d.to_dict() for d in drafts], indent=2))
        return 0
    if not drafts:
        print(f"No drafts for user={args.user_id} channel={args.channel}")
        return 0
    print(f"Recent drafts for user={args.user_id} channel={args.channel}:")
    for d in drafts:
        print(f"- {d.handle}  {d.created_at}  len={d.content_length}  chip={d.chip_used or '-'}  topic={d.topic_hint or '-'}")
    return 0


def handle_drafts_show(args: argparse.Namespace) -> int:
    from spark_intelligence.bot_drafts import find_draft_by_handle

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    draft = find_draft_by_handle(
        state_db,
        external_user_id=args.user_id,
        channel_kind=args.channel,
        handle_or_id=args.handle,
    )
    if draft is None:
        print(f"No draft matching {args.handle} for user={args.user_id} channel={args.channel}")
        return 1
    print(f"=== Draft {draft.handle} ===")
    print(f"created_at: {draft.created_at}")
    print(f"length: {draft.content_length}")
    print(f"chip_used: {draft.chip_used or '-'}")
    print(f"topic_hint: {draft.topic_hint or '-'}")
    print("--- content ---")
    print(draft.content)
    return 0


def handle_instructions_list(args: argparse.Namespace) -> int:
    from spark_intelligence.user_instructions import list_active_instructions

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    items = list_active_instructions(
        state_db,
        external_user_id=args.user_id,
        channel_kind=args.channel,
        limit=100,
    )
    if args.json:
        import json as _json
        print(_json.dumps([i.to_dict() for i in items], indent=2))
        return 0
    if not items:
        print(f"No active instructions for user={args.user_id} channel={args.channel}")
        return 0
    print(f"Active instructions for user={args.user_id} channel={args.channel}:")
    for i in items:
        print(f"- [{i.instruction_id}] ({i.source}) {i.instruction_text}")
    return 0


def handle_instructions_add(args: argparse.Namespace) -> int:
    from spark_intelligence.user_instructions import add_instruction

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    saved = add_instruction(
        state_db,
        external_user_id=args.user_id,
        channel_kind=args.channel,
        instruction_text=args.text,
        source=args.source,
    )
    print(f"Saved instruction {saved.instruction_id}: {saved.instruction_text}")
    return 0


def handle_instructions_archive(args: argparse.Namespace) -> int:
    from spark_intelligence.user_instructions import archive_instruction

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    ok = archive_instruction(state_db, instruction_id=args.instruction_id)
    if ok:
        print(f"Archived instruction {args.instruction_id}")
        return 0
    print(f"No active instruction with id {args.instruction_id}")
    return 1


def handle_chips_why(args: argparse.Namespace) -> int:
    from spark_intelligence.attachments import list_active_chip_records
    from spark_intelligence.chip_router import explain_routing, select_chips_for_message

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    active = list_active_chip_records(config_manager)
    decision = select_chips_for_message(
        args.message,
        active,
        conversation_history=getattr(args, "history", "") or "",
        recent_active_chip_keys=getattr(args, "recent_chip", []) or [],
    )
    if args.json:
        import json as _json
        print(_json.dumps(decision.to_dict(), indent=2))
    else:
        print(explain_routing(decision))
    return 0


def handle_loops_run(args: argparse.Namespace) -> int:
    import json as _json
    from spark_intelligence.loops import run_chip_autoloop

    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = run_chip_autoloop(
        config_manager=config_manager,
        chip_key=args.chip,
        rounds=args.rounds,
        suggest_limit=args.suggest_limit,
        pause_seconds=args.pause_seconds,
    )
    if args.json:
        print(_json.dumps(result.to_dict(), indent=2, default=str))
    else:
        if result.ok:
            print(f"ok: chip={result.chip_key} rounds={result.rounds_completed}/{result.total_rounds}")
            for r in result.history:
                print(f"  round {r['round_index']}: suggestions={r['suggestions_count']} best_verdict={r.get('best_verdict')} best_metric={r.get('best_metric')}")
            if result.status_path:
                print(f"status: {result.status_path}")
        else:
            print(f"error: {result.error}")
            if result.history:
                print(f"completed {result.rounds_completed}/{result.total_rounds} rounds before failure")
    return 0 if result.ok else 1


def handle_chips_create(args: argparse.Namespace) -> int:
    from pathlib import Path as _Path
    import json as _json
    from spark_intelligence.chip_create import create_chip_from_prompt

    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    output_dir = _Path(args.output_dir) if args.output_dir else None
    chip_labs_root = _Path(args.chip_labs_root) if args.chip_labs_root else None
    result = create_chip_from_prompt(
        prompt=args.prompt,
        config_manager=config_manager,
        state_db=state_db,
        output_dir=output_dir,
        chip_labs_root=chip_labs_root,
    )
    if args.json:
        print(_json.dumps(result.to_dict(), indent=2, default=str))
    else:
        if result.ok:
            print(f"ok: chip_key={result.chip_key} path={result.chip_path} router_invokable={result.router_invokable}")
            if result.warnings:
                print("warnings:")
                for w in result.warnings:
                    print(f"  - {w}")
        else:
            print(f"error: {result.error}")
    return 0 if result.ok else 1


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
    if args.payload_file:
        try:
            payload_source = Path(args.payload_file).read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"Invalid --payload-file: {exc}", file=sys.stderr)
            return 2
    else:
        payload_source = args.payload_json
    try:
        payload = json.loads(payload_source)
    except json.JSONDecodeError as exc:
        source_flag = "--payload-file" if args.payload_file else "--payload-json"
        print(f"Invalid {source_flag}: {exc}", file=sys.stderr)
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


def _run_browser_hook(
    args: argparse.Namespace,
    *,
    hook_name: str,
    payload: dict[str, object],
    render_result,
    action: str,
    target_ref: str,
) -> int:
    exit_code, display_payload, error_text = _execute_browser_hook(
        args,
        hook_name=hook_name,
        payload=payload,
        action=action,
        target_ref=target_ref,
    )
    return _emit_browser_hook_output(
        args,
        exit_code=exit_code,
        display_payload=display_payload,
        error_text=error_text,
        render_result=render_result,
    )


def _execute_browser_hook(
    args: argparse.Namespace,
    *,
    hook_name: str,
    payload: dict[str, object],
    action: str,
    target_ref: str,
) -> tuple[int, dict[str, object] | None, str | None]:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    request_id = str(payload.get("request_id") or f"browser-hook:{uuid4().hex[:12]}")
    payload_path = Path(args.write_payload) if getattr(args, "write_payload", None) else (
        config_manager.paths.home / "artifacts" / "browser-hooks" / f"{request_id}.payload.json"
    )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    run = open_run(
        state_db,
        run_kind=f"operator:browser_hook:{hook_name}",
        origin_surface="browser_cli",
        summary="Operator started a browser capability hook execution.",
        request_id=request_id,
        actor_id="local-operator",
        reason_code=action,
        facts={"chip_key": args.chip_key or "active", "hook": hook_name, "payload_path": str(payload_path)},
    )
    try:
        if args.chip_key:
            execution = run_chip_hook(config_manager, chip_key=args.chip_key, hook=hook_name, payload=payload)
        else:
            execution = run_first_active_chip_hook(config_manager, hook=hook_name, payload=payload)
            if execution is None:
                close_run(
                    state_db,
                    run_id=run.run_id,
                    status="failed",
                    close_reason="no_active_chip_for_hook",
                    summary="Browser CLI found no active chip exposing the requested browser hook.",
                    facts={"hook": hook_name},
                )
                return (
                    1,
                    None,
                    f"No active chip exposes hook '{hook_name}'. Activate the browser runtime first or pass --chip-key.",
                )
    except ValueError as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="browser_hook_invalid",
            summary="Browser CLI failed validation before hook execution.",
            facts={"hook": hook_name, "error": str(exc)},
        )
        return 2, None, str(exc)

    result_path = Path(args.write_result) if getattr(args, "write_result", None) else (
        config_manager.paths.home / "artifacts" / "browser-hooks" / f"{request_id}.result.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_payload = execution.to_payload()
    result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="browser_cli",
        actor_id="local-operator",
        summary="Browser CLI executed a browser capability hook.",
        reason_code=action,
        keepability="operator_debug_only",
        run_id=run.run_id,
        request_id=run.request_id,
    )

    hook_output = execution.output if isinstance(execution.output, dict) else {}
    hook_status = _normalize_browser_hook_status(hook_output)
    hook_error = hook_output.get("error") if isinstance(hook_output.get("error"), dict) else None
    hook_failed = (not execution.ok) or bool(hook_error) or (
        hook_status is not None and hook_status not in {"succeeded", "completed", "ok", "success"}
    )
    display_payload = {
        "status": "failed" if hook_failed else "completed",
        "chip_key": execution.chip_key,
        "hook": hook_name,
        "request_id": request_id,
        "payload_path": str(payload_path),
        "result_path": str(result_path),
        "hook_status": hook_status or ("failed" if hook_failed else "succeeded"),
        "approval_state": hook_output.get("approval_state") if isinstance(hook_output.get("approval_state"), str) else None,
        "result": hook_output.get("result") if isinstance(hook_output.get("result"), dict) else {},
        "artifacts": hook_output.get("artifacts") if isinstance(hook_output.get("artifacts"), list) else [],
        "provenance": hook_output.get("provenance") if isinstance(hook_output.get("provenance"), dict) else {},
        "error": hook_error,
        "execution": result_payload,
    }
    if args.json:
        display_text = json.dumps(display_payload, indent=2, ensure_ascii=True)
    elif hook_failed:
        display_text = _render_browser_hook_failure(display_payload)
    else:
        display_text = render_result(display_payload["result"])

    screened_output = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=display_text,
        summary="Browser CLI blocked secret-like browser hook output before operator display.",
        reason_code=f"{action}_secret_like",
        policy_domain="browser_cli",
        blocked_stage="operator_output",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    if not screened_output["allowed"]:
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="browser_cli",
            summary="Browser CLI blocked hook output because it contained secret-like material.",
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
            status="failed",
            close_reason="secret_boundary_blocked",
            summary="Browser CLI hook output was blocked by the secret boundary.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        return (
            1,
            None,
            "Browser hook output was blocked because it contained secret-like material. "
            "Review quarantine records instead of raw output.",
        )

    log_operator_event(
        state_db=state_db,
        action=action,
        target_kind="browser_hook",
        target_ref=target_ref,
        reason=f"Operator executed {hook_name}.",
        details={
            "status": "failed" if hook_failed else "completed",
            "chip_key": execution.chip_key,
            "hook": hook_name,
            "payload_path": str(payload_path),
            "result_path": str(result_path),
            "error_code": hook_error.get("code") if hook_error else None,
        },
    )
    if hook_failed:
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="browser_hook_failed",
            summary="Browser CLI hook returned a governed failure response.",
            facts={
                "chip_key": execution.chip_key,
                "hook": hook_name,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "error_code": hook_error.get("code") if hook_error else None,
            },
        )
        return 1, display_payload, None

    close_run(
        state_db,
        run_id=run.run_id,
        status="closed",
        close_reason="browser_hook_completed",
        summary="Browser CLI hook completed successfully.",
        facts={
            "chip_key": execution.chip_key,
            "hook": hook_name,
            "payload_path": str(payload_path),
            "result_path": str(result_path),
        },
    )
    return 0, display_payload, None


def _emit_browser_hook_output(
    args: argparse.Namespace,
    *,
    exit_code: int,
    display_payload: dict[str, object] | None,
    error_text: str | None,
    render_result,
) -> int:
    if error_text:
        print(error_text, file=sys.stderr)
        return exit_code
    if display_payload is None:
        return exit_code
    display_text = _render_browser_hook_display(args, display_payload, render_result)
    if exit_code != 0 and not args.json:
        print(display_text, file=sys.stderr)
    else:
        print(display_text)
    return exit_code


def _render_browser_hook_display(
    args: argparse.Namespace,
    display_payload: dict[str, object],
    render_result,
) -> str:
    if args.json:
        return json.dumps(display_payload, indent=2, ensure_ascii=True)
    if str(display_payload.get("status") or "").strip().lower() == "failed":
        return _render_browser_hook_failure(display_payload)
    result = display_payload.get("result")
    return render_result(result if isinstance(result, dict) else {})


def _browser_snapshot_failure_requires_page_context(display_payload: dict[str, object] | None) -> bool:
    if not isinstance(display_payload, dict):
        return False
    if str(display_payload.get("status") or "").strip().lower() != "failed":
        return False
    error = display_payload.get("error")
    if not isinstance(error, dict):
        return False
    if str(error.get("code") or "").strip() != "EXTENSION_RUNTIME_FAILED":
        return False
    message = str(error.get("message") or "").strip().lower()
    return "cannot access contents of the page" in message


def _browser_result_tab_id(display_payload: dict[str, object] | None) -> str | None:
    if not isinstance(display_payload, dict):
        return None
    result = display_payload.get("result")
    if isinstance(result, dict):
        tab = result.get("tab")
        if isinstance(tab, dict):
            tab_id = tab.get("id") or tab.get("tab_id")
            if tab_id:
                return str(tab_id)
        tab_id = result.get("tab_id")
        if tab_id:
            return str(tab_id)
    provenance = display_payload.get("provenance")
    if isinstance(provenance, dict) and provenance.get("tab_id"):
        return str(provenance.get("tab_id"))
    execution = display_payload.get("execution")
    if isinstance(execution, dict):
        output = execution.get("output")
        if isinstance(output, dict):
            output_provenance = output.get("provenance")
            if isinstance(output_provenance, dict) and output_provenance.get("tab_id"):
                return str(output_provenance.get("tab_id"))
    return None


def _browser_result_origin(display_payload: dict[str, object] | None, *, default: str) -> str:
    if isinstance(display_payload, dict):
        result = display_payload.get("result")
        if isinstance(result, dict) and result.get("origin"):
            return str(result.get("origin"))
        provenance = display_payload.get("provenance")
        if isinstance(provenance, dict) and provenance.get("origin"):
            return str(provenance.get("origin"))
    return default


def _normalize_browser_hook_status(hook_output: dict[str, object]) -> str | None:
    value = hook_output.get("status")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _render_browser_hook_failure(display_payload: dict[str, object]) -> str:
    error = display_payload.get("error") if isinstance(display_payload.get("error"), dict) else {}
    code = str(error.get("code") or "BROWSER_HOOK_FAILED")
    message = str(error.get("message") or "The browser hook returned a governed failure response.")
    return f"Browser hook failed: {code}: {message}"


def handle_browser_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    payload = build_browser_status_payload(
        config_manager=config_manager,
        browser_family=args.browser_family,
        profile_key=args.profile_key,
        profile_mode=args.profile_mode,
        agent_id=args.agent_id,
    )
    return _run_browser_hook(
        args,
        hook_name=BROWSER_STATUS_HOOK,
        payload=payload,
        render_result=render_browser_status,
        action="browser_status",
        target_ref=f"{args.browser_family}:{args.profile_key}",
    )


def handle_browser_page_snapshot(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    payload = build_browser_page_snapshot_payload(
        config_manager=config_manager,
        origin=args.origin,
        browser_family=args.browser_family,
        profile_key=args.profile_key,
        profile_mode=args.profile_mode,
        agent_id=args.agent_id,
        max_text_characters=args.max_text_characters,
        max_controls=args.max_controls,
        allowed_domains=list(args.allowed_domain or []),
        sensitive_domain=args.sensitive_domain,
        operator_required=args.operator_required,
    )
    exit_code, display_payload, error_text = _execute_browser_hook(
        args,
        hook_name=BROWSER_PAGE_SNAPSHOT_HOOK,
        payload=payload,
        action="browser_page_snapshot",
        target_ref=args.origin,
    )
    if exit_code == 0 or not _browser_snapshot_failure_requires_page_context(display_payload):
        return _emit_browser_hook_output(
            args,
            exit_code=exit_code,
            display_payload=display_payload,
            error_text=error_text,
            render_result=render_browser_page_snapshot,
        )

    navigate_payload = build_browser_navigate_payload(
        config_manager=config_manager,
        url=args.origin,
        browser_family=args.browser_family,
        profile_key=args.profile_key,
        profile_mode=args.profile_mode,
        agent_id=args.agent_id,
    )
    navigate_exit_code, navigate_display_payload, navigate_error_text = _execute_browser_hook(
        args,
        hook_name=BROWSER_NAVIGATE_HOOK,
        payload=navigate_payload,
        action="browser_page_snapshot_navigate",
        target_ref=args.origin,
    )
    if navigate_exit_code != 0:
        return _emit_browser_hook_output(
            args,
            exit_code=navigate_exit_code,
            display_payload=navigate_display_payload,
            error_text=navigate_error_text,
            render_result=lambda result: "Browser navigation completed.",
        )

    tab_id = _browser_result_tab_id(navigate_display_payload)
    if not tab_id:
        return _emit_browser_hook_output(
            args,
            exit_code=1,
            display_payload=None,
            error_text="Browser navigation completed but did not return a tab id for the follow-up snapshot.",
            render_result=render_browser_page_snapshot,
        )

    wait_origin = _browser_result_origin(navigate_display_payload, default=args.origin)
    wait_payload = build_browser_tab_wait_payload(
        config_manager=config_manager,
        origin=wait_origin,
        tab_id=tab_id,
        browser_family=args.browser_family,
        profile_key=args.profile_key,
        profile_mode=args.profile_mode,
        agent_id=args.agent_id,
    )
    wait_exit_code, wait_display_payload, wait_error_text = _execute_browser_hook(
        args,
        hook_name=BROWSER_TAB_WAIT_HOOK,
        payload=wait_payload,
        action="browser_page_snapshot_wait",
        target_ref=tab_id,
    )
    if wait_exit_code != 0:
        return _emit_browser_hook_output(
            args,
            exit_code=wait_exit_code,
            display_payload=wait_display_payload,
            error_text=wait_error_text,
            render_result=lambda result: "Browser tab wait completed.",
        )

    snapshot_payload = build_browser_page_snapshot_payload(
        config_manager=config_manager,
        origin=wait_origin,
        tab_id=tab_id,
        browser_family=args.browser_family,
        profile_key=args.profile_key,
        profile_mode=args.profile_mode,
        agent_id=args.agent_id,
        max_text_characters=args.max_text_characters,
        max_controls=args.max_controls,
        allowed_domains=list(args.allowed_domain or []),
        sensitive_domain=args.sensitive_domain,
        operator_required=args.operator_required,
    )
    return _run_browser_hook(
        args,
        hook_name=BROWSER_PAGE_SNAPSHOT_HOOK,
        payload=snapshot_payload,
        render_result=render_browser_page_snapshot,
        action="browser_page_snapshot",
        target_ref=wait_origin,
    )


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


def handle_memory_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager)
    watchtower = build_watchtower_snapshot(state_db)
    memory_shadow = (watchtower.get("panels") or {}).get("memory_shadow") or {}
    smoke_events = _latest_memory_status_events(
        state_db,
        event_types=("memory_smoke_succeeded", "memory_smoke_failed"),
        limit=10,
    )
    status = MemoryStatus(
        payload={
            "runtime": runtime,
            "counts": dict(memory_shadow.get("counts") or {}),
            "memory_role_mix": list(memory_shadow.get("memory_role_mix") or []),
            "abstention_reasons": list(memory_shadow.get("abstention_reasons") or []),
            "last_smoke": _memory_status_event_payload(smoke_events[0]) if smoke_events else None,
            "recent_failures": _build_recent_memory_failures(state_db),
        }
    )
    print(status.to_json() if args.json else status.to_text())
    return 0


def handle_memory_lookup_current_state(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = lookup_current_state_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        subject=args.subject,
        predicate=args.predicate,
        sdk_module=args.sdk_module,
    )
    print(result.to_json() if args.json else result.to_text())
    return 0 if not result.read_result.abstained and bool(result.read_result.records) else 1


def handle_memory_inspect_human(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = inspect_human_memory_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        human_id=args.human_id,
        sdk_module=args.sdk_module,
    )
    inspection = MemoryHumanInspection(
        payload={
            "sdk_module": result.sdk_module,
            "human_id": args.human_id,
            "subject": result.subject,
            "runtime": result.runtime,
            "current_state": json.loads(result.to_json()).get("read_result"),
            "recent_events": _build_recent_human_memory_events(
                state_db=state_db,
                human_id=args.human_id,
                subject=result.subject,
                limit=args.event_limit,
            ),
        }
    )
    print(inspection.to_json() if args.json else inspection.to_text())
    return 0


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


def handle_memory_run_sdk_maintenance(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = run_memory_sdk_maintenance(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=args.sdk_module,
        now=args.now,
        actor_id="memory_cli",
    )
    print(result.to_json() if args.json else result.to_text())
    return 0 if result.status == "succeeded" else 1


def handle_memory_compile_telegram_kb(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = build_telegram_state_knowledge_base(
        config_manager=config_manager,
        output_dir=args.output_dir,
        limit=args.limit,
        chat_id=args.chat_id,
        repo_sources=args.repo_source,
        repo_source_manifest_files=args.repo_source_manifest,
        write_path=args.write,
        validator_root=args.validator_root,
    )
    print(result.to_json() if args.json else result.to_text())
    payload = result.payload if isinstance(result.payload, dict) else {}
    if payload.get("errors"):
        return 1
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if isinstance(summary, dict) and not summary.get("kb_valid", False):
        return 1
    health_report = payload.get("health_report") if isinstance(payload, dict) else None
    if isinstance(health_report, dict):
        if health_report.get("errors"):
            return 1
        if health_report.get("valid") is False:
            return 1
    return 0


def handle_memory_run_telegram_regression(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = run_telegram_memory_regression(
        config_manager=config_manager,
        state_db=state_db,
        output_dir=args.output_dir,
        user_id=args.user_id,
        username=args.username,
        chat_id=args.chat_id,
        kb_limit=args.kb_limit,
        validator_root=args.validator_root,
        write_path=args.write,
        case_ids=args.case_id,
        categories=args.category,
        benchmark_pack_ids=args.benchmark_pack,
        baseline_names=args.baseline,
    )
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_memory_benchmark_architectures(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = benchmark_memory_architectures(
        config_manager=config_manager,
        output_dir=args.output_dir,
        validator_root=args.validator_root,
        baseline_names=args.baseline,
    )
    print(result.to_json() if args.json else result.to_text())
    payload = result.payload if isinstance(result.payload, dict) else {}
    return 1 if payload.get("errors") else 0


def handle_memory_soak_architectures(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = run_telegram_memory_architecture_soak(
        config_manager=config_manager,
        state_db=state_db,
        output_dir=args.output_dir,
        runs=args.runs,
        sleep_seconds=args.sleep_seconds,
        user_id=args.user_id,
        username=args.username,
        chat_id=args.chat_id,
        kb_limit=args.kb_limit,
        validator_root=args.validator_root,
        write_path=args.write,
        case_ids=args.case_id,
        categories=args.category,
        benchmark_pack_ids=args.benchmark_pack,
        baseline_names=args.baseline,
        run_timeout_seconds=args.run_timeout_seconds,
    )
    print(result.to_json() if args.json else result.to_text())
    payload = result.payload if isinstance(result.payload, dict) else {}
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    if payload.get("errors"):
        return 1
    return 0 if int(summary.get("completed_runs") or 0) > 0 else 1


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


def _latest_memory_status_events(
    state_db: StateDB,
    *,
    event_types: tuple[str, ...],
    limit: int,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for event_type in event_types:
        events.extend(latest_events_by_type(state_db, event_type=event_type, limit=limit))
    return sorted(
        events,
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")),
        reverse=True,
    )[:limit]


def _memory_status_event_payload(event: dict[str, object]) -> dict[str, object]:
    facts = event.get("facts_json")
    facts_dict = facts if isinstance(facts, dict) else {}
    return {
        "event_type": event.get("event_type"),
        "created_at": event.get("created_at"),
        "reason": facts_dict.get("reason"),
        "subject": facts_dict.get("subject"),
        "predicate": facts_dict.get("predicate"),
        "sdk_module": facts_dict.get("sdk_module"),
    }


def _build_recent_memory_failures(state_db: StateDB) -> list[dict[str, object]]:
    events = _latest_memory_status_events(
        state_db,
        event_types=("memory_write_abstained", "memory_read_abstained", "memory_smoke_failed"),
        limit=10,
    )
    return [_memory_status_event_payload(event) for event in events]


def _build_recent_human_memory_events(
    *,
    state_db: StateDB,
    human_id: str,
    subject: str,
    limit: int,
) -> list[dict[str, object]]:
    events = _latest_memory_status_events(
        state_db,
        event_types=(
            "memory_write_requested",
            "memory_write_succeeded",
            "memory_write_abstained",
            "memory_read_requested",
            "memory_read_succeeded",
            "memory_read_abstained",
            "memory_smoke_succeeded",
            "memory_smoke_failed",
        ),
        limit=max(limit * 6, 30),
    )
    filtered: list[dict[str, object]] = []
    for event in events:
        event_human_id = str(event.get("human_id") or "")
        facts = event.get("facts_json")
        facts_dict = facts if isinstance(facts, dict) else {}
        fact_subject = str(facts_dict.get("subject") or "")
        if event_human_id != human_id and fact_subject != subject:
            continue
        filtered.append(
            {
                "event_type": event.get("event_type"),
                "created_at": event.get("created_at"),
                "reason": facts_dict.get("reason"),
                "subject": facts_dict.get("subject"),
                "predicate": facts_dict.get("predicate"),
                "predicate_prefix": facts_dict.get("predicate_prefix"),
                "memory_role": facts_dict.get("memory_role"),
                "accepted_count": facts_dict.get("accepted_count"),
            }
        )
        if len(filtered) >= limit:
            break
    return filtered


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


def handle_swarm_doctor(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = swarm_doctor(config_manager, state_db)
    print(report.to_json() if args.json else report.to_text())
    return 0 if not report.blockers else 1


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


def handle_harness_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    payload = {
        "harness_registry": build_harness_registry(config_manager, state_db).to_payload(),
        "harness_runtime": build_harness_runtime_snapshot(config_manager, state_db).to_payload(),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        registry_summary = payload["harness_registry"]["summary"]
        runtime_summary = payload["harness_runtime"]["summary"]
        lines = ["Spark harness status"]
        lines.append(f"- contracts: {int(registry_summary.get('contract_count') or 0)}")
        lines.append(f"- available: {int(registry_summary.get('available_contract_count') or 0)}")
        available_harnesses = registry_summary.get("available_harnesses") or []
        if available_harnesses:
            lines.append(f"- harnesses: {', '.join(str(item) for item in available_harnesses[:8])}")
        lines.append(f"- recent runs: {int(runtime_summary.get('recent_run_count') or 0)}")
        if runtime_summary.get("last_harness_id"):
            lines.append(f"- last harness: {runtime_summary['last_harness_id']}")
        print("\n".join(lines))
    return 0


def handle_harness_plan(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    recipe_payload = None
    forced_harness_id = args.harness_id
    if args.recipe:
        recipe = select_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            recipe_id=args.recipe,
        )
        recipe_payload = recipe.to_payload()
        forced_harness_id = recipe.primary_harness_id
    elif not forced_harness_id:
        auto_recipe = select_auto_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            task=args.task,
        )
        if auto_recipe is not None:
            recipe_payload = auto_recipe.to_payload()
            forced_harness_id = auto_recipe.recipe.primary_harness_id
    envelope = build_harness_task_envelope(
        config_manager=config_manager,
        state_db=state_db,
        task=args.task,
        forced_harness_id=forced_harness_id,
        channel_kind=args.channel_kind,
        session_id=args.session_id,
        human_id=args.human_id,
        agent_id=args.agent_id,
    )
    if args.json:
        payload = envelope.to_payload()
        if recipe_payload is not None:
            payload["recipe"] = recipe_payload
        print(json.dumps(payload, indent=2))
    else:
        lines = ["Spark harness plan"]
        if recipe_payload is not None:
            lines.append(f"- recipe: {recipe_payload['recipe_id']}")
        lines.append(f"- harness: {envelope.harness_id}")
        lines.append(f"- owner: {envelope.owner_system}")
        lines.append(f"- backend: {envelope.backend_kind}")
        lines.append(f"- session scope: {envelope.session_scope}")
        lines.append(f"- route mode: {envelope.route_mode}")
        if envelope.required_capabilities:
            lines.append(f"- capabilities: {', '.join(envelope.required_capabilities[:8])}")
        if envelope.artifacts_expected:
            lines.append(f"- artifacts: {', '.join(envelope.artifacts_expected[:8])}")
        if envelope.next_actions:
            lines.extend(f"- next action: {item}" for item in envelope.next_actions[:3])
        print("\n".join(lines))
    return 0


def handle_harness_execute(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    recipe_payload = None
    forced_harness_id = args.harness_id
    follow_up_harness_ids = [str(item).strip() for item in (args.then_harness_id or []) if str(item).strip()]
    if args.recipe:
        recipe = select_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            recipe_id=args.recipe,
        )
        recipe_payload = recipe.to_payload()
        forced_harness_id = recipe.primary_harness_id
        if not follow_up_harness_ids:
            follow_up_harness_ids = list(recipe.follow_up_harness_ids)
    elif not forced_harness_id:
        auto_recipe = select_auto_harness_recipe(
            config_manager=config_manager,
            state_db=state_db,
            task=args.task,
        )
        if auto_recipe is not None:
            recipe_payload = auto_recipe.to_payload()
            forced_harness_id = auto_recipe.recipe.primary_harness_id
            if not follow_up_harness_ids:
                follow_up_harness_ids = list(auto_recipe.recipe.follow_up_harness_ids)
    envelope = build_harness_task_envelope(
        config_manager=config_manager,
        state_db=state_db,
        task=args.task,
        forced_harness_id=forced_harness_id,
        channel_kind=args.channel_kind,
        session_id=args.session_id,
        human_id=args.human_id,
        agent_id=args.agent_id,
    )
    if follow_up_harness_ids:
        result = execute_harness_chain(
            config_manager=config_manager,
            state_db=state_db,
            envelope=envelope,
            follow_up_harness_ids=follow_up_harness_ids,
        )
    else:
        result = execute_harness_task(
            config_manager=config_manager,
            state_db=state_db,
            envelope=envelope,
        )
    if args.json:
        payload = result.to_payload()
        if recipe_payload is not None:
            payload["recipe"] = recipe_payload
        print(json.dumps(payload, indent=2))
    else:
        lines = ["Spark harness execution"]
        if recipe_payload is not None:
            lines.append(f"- recipe: {recipe_payload['recipe_id']}")
        lines.append(f"- harness: {result.envelope.harness_id}")
        lines.append(f"- status: {result.status}")
        lines.append(f"- summary: {result.summary}")
        if result.chain_status:
            lines.append(f"- chain status: {result.chain_status}")
        artifact_keys = sorted(result.artifacts.keys())
        if artifact_keys:
            lines.append(f"- artifacts: {', '.join(artifact_keys)}")
        for chained_result in result.chained_results or []:
            lines.append(f"- chained harness: {chained_result.envelope.harness_id} ({chained_result.status})")
        if result.next_actions:
            lines.extend(f"- next action: {item}" for item in result.next_actions[:3])
        print("\n".join(lines))
    return 0


def handle_mission_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    snapshot = build_mission_control_snapshot(config_manager, state_db)
    if args.json:
        print(snapshot.to_json())
    else:
        payload = snapshot.to_payload()
        summary = payload.get("summary") or {}
        lines = ["Spark mission status"]
        lines.append(f"- state: {summary.get('top_level_state') or 'unknown'}")
        if summary.get("current_focus"):
            lines.append(f"- focus: {summary['current_focus']}")
        active_systems = [str(item) for item in (summary.get("active_systems") or []) if str(item)]
        if active_systems:
            lines.append(f"- active systems: {', '.join(active_systems[:6])}")
        degraded_surfaces = [str(item) for item in (summary.get("degraded_surfaces") or []) if str(item)]
        if degraded_surfaces:
            lines.append(f"- degraded surfaces: {', '.join(degraded_surfaces[:6])}")
        next_actions = [str(item) for item in (summary.get("recommended_actions") or []) if str(item)]
        lines.extend(f"- next action: {item}" for item in next_actions[:3])
        print("\n".join(lines))
    return 0


def handle_mission_plan(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    plan = build_mission_control_plan(
        config_manager=config_manager,
        state_db=state_db,
        task=args.task,
        forced_harness_id=args.harness_id,
        forced_recipe_id=args.recipe,
    )
    if args.json:
        print(plan.to_json())
    else:
        payload = plan.to_payload()
        summary = payload.get("summary") or {}
        lines = ["Spark mission plan"]
        lines.append(f"- state: {summary.get('top_level_state') or 'unknown'}")
        lines.append(f"- system: {summary.get('selected_system') or 'unknown'}")
        lines.append(f"- harness: {summary.get('selected_harness') or 'unknown'}")
        if summary.get("selected_recipe"):
            lines.append(f"- recipe: {summary['selected_recipe']}")
        lines.append(f"- selection mode: {summary.get('selection_mode') or 'unknown'}")
        if summary.get("current_focus"):
            lines.append(f"- focus: {summary['current_focus']}")
        blockers = [str(item) for item in (summary.get("blockers") or []) if str(item)]
        lines.extend(f"- blocker: {item}" for item in blockers[:4])
        next_actions = [str(item) for item in (summary.get("next_actions") or []) if str(item)]
        lines.extend(f"- next action: {item}" for item in next_actions[:4])
        print("\n".join(lines))
    return 0
def handle_agent_inspect(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    if args.human_id:
        report = inspect_canonical_agent(state_db=state_db, human_id=args.human_id)
        print(report.to_json() if args.json else report.to_text())
        return 0
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


def handle_agent_rename(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    state = rename_agent_identity(
        state_db=state_db,
        human_id=args.human_id,
        new_name=args.name,
        source_surface="agent_cli",
        source_ref="agent rename",
    )
    payload = state.to_payload()
    print(json.dumps(payload, indent=2) if args.json else f"Renamed {payload['agent_id']} to {payload['agent_name']}.")
    return 0


def handle_agent_link_swarm(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    state = link_spark_swarm_agent(
        state_db=state_db,
        human_id=args.human_id,
        swarm_agent_id=args.swarm_agent_id,
        agent_name=args.agent_name,
        confirmed_at=args.confirmed_at,
        metadata={"linked_via": "agent_cli"},
    )
    payload = state.to_payload()
    print(
        json.dumps(payload, indent=2)
        if args.json
        else f"Linked {args.human_id} to canonical Spark Swarm agent {payload['agent_id']}."
    )
    return 0


def handle_agent_import_swarm(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    import_id = f"swarm-import:{uuid4().hex[:12]}"
    workspace_id = (
        str(config_manager.get_path("spark.swarm.workspace_id") or "").strip()
        or str(config_manager.get_path("workspace.id", default="default"))
    )
    payload = build_spark_swarm_identity_import_payload(
        state_db=state_db,
        human_id=args.human_id,
        workspace_id=workspace_id,
    )
    payload["import_id"] = import_id
    payload["requested_by"] = "local-operator"
    payload["reason"] = args.reason

    payload_path = Path(args.write_payload) if args.write_payload else (
        config_manager.paths.home / "artifacts" / "agent-imports" / f"{import_id}.payload.json"
    )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    run = open_run(
        state_db,
        run_kind="operator:agent_import_swarm",
        origin_surface="agent_cli",
        summary="Operator started a Spark Swarm identity import.",
        request_id=import_id,
        actor_id="local-operator",
        reason_code="agent_import_swarm",
        human_id=args.human_id,
        facts={"chip_key": args.chip_key or "active", "payload_path": str(payload_path)},
    )
    try:
        if args.chip_key:
            execution = run_chip_hook(config_manager, chip_key=args.chip_key, hook="identity", payload=payload)
        else:
            execution = run_first_active_chip_hook(config_manager, hook="identity", payload=payload)
            if execution is None:
                close_run(
                    state_db,
                    run_id=run.run_id,
                    status="stalled",
                    close_reason="no_active_chip_for_hook",
                    summary="Agent import found no active chip exposing the identity hook.",
                    facts={"hook": "identity"},
                )
                print(
                    "No active chip exposes hook 'identity'. Activate the Spark Swarm runtime first or pass --chip-key.",
                    file=sys.stderr,
                )
                return 1
    except ValueError as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="agent_import_invalid",
            summary="Agent import failed validation before Spark Swarm hook execution.",
            facts={"hook": "identity", "error": str(exc)},
        )
        print(str(exc), file=sys.stderr)
        return 2

    result_path = Path(args.write_result) if args.write_result else (
        config_manager.paths.home / "artifacts" / "agent-imports" / f"{import_id}.result.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_payload = execution.to_payload()

    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="agent_cli",
        actor_id="local-operator",
        summary="Agent CLI executed a Spark Swarm identity hook.",
        reason_code="agent_import_swarm",
        keepability="operator_debug_only",
        run_id=run.run_id,
        request_id=run.request_id,
        human_id=args.human_id,
    )
    screened_output = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=json.dumps(
            {
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "exit_code": execution.exit_code,
                "stdout": execution.stdout,
                "result": execution.output.get("result") if isinstance(execution.output, dict) else {},
            },
            indent=2,
            ensure_ascii=True,
        ),
        summary="Agent import blocked secret-like Spark Swarm hook output before operator display.",
        reason_code="agent_import_swarm_secret_like",
        policy_domain="agent_cli",
        blocked_stage="operator_output",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    if not screened_output["allowed"]:
        log_operator_event(
            state_db=state_db,
            action="import_swarm_identity",
            target_kind="agent_identity",
            target_ref=args.human_id,
            reason=args.reason,
            details={
                "status": "blocked",
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="agent_cli",
            summary="Agent import blocked Spark Swarm hook output because it contained secret-like material.",
            run_id=run.run_id,
            request_id=run.request_id,
            actor_id="local-operator",
            reason_code="secret_boundary_blocked",
            severity="high",
            human_id=args.human_id,
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
            summary="Agent import was blocked by the secret boundary.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        print(
            "Spark Swarm identity import output was blocked because it contained secret-like material. "
            "Review quarantine records instead of raw output.",
            file=sys.stderr,
        )
        return 1

    result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    if not execution.ok:
        log_operator_event(
            state_db=state_db,
            action="import_swarm_identity",
            target_kind="agent_identity",
            target_ref=args.human_id,
            reason=args.reason,
            details={
                "status": "failed",
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "exit_code": execution.exit_code,
            },
        )
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="agent_import_hook_failed",
            summary="Spark Swarm identity hook returned a non-zero exit code.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "exit_code": execution.exit_code,
            },
        )
        print(
            json.dumps(
                {
                    "import_id": import_id,
                    "status": "failed",
                    "chip_key": execution.chip_key,
                    "hook": execution.hook,
                    "payload_path": str(payload_path),
                    "result_path": str(result_path),
                    "execution": result_payload,
                },
                indent=2,
            )
            if args.json
            else (
                "Spark Swarm identity import failed because the external hook returned a non-zero exit code. "
                f"Result artifact: {result_path}"
            )
        )
        return 1

    try:
        normalized = normalize_spark_swarm_identity_import(
            human_id=args.human_id,
            hook_output=execution.output,
        )
    except ValueError as exc:
        log_operator_event(
            state_db=state_db,
            action="import_swarm_identity",
            target_kind="agent_identity",
            target_ref=args.human_id,
            reason=args.reason,
            details={
                "status": "invalid",
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "error": str(exc),
            },
        )
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="agent_import_invalid_payload",
            summary="Spark Swarm identity hook returned an invalid import payload.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "error": str(exc),
            },
        )
        print(str(exc), file=sys.stderr)
        return 2

    state = link_spark_swarm_agent(
        state_db=state_db,
        human_id=args.human_id,
        swarm_agent_id=normalized["swarm_agent_id"],
        agent_name=normalized["agent_name"],
        confirmed_at=normalized["confirmed_at"],
        metadata={
            **normalized["metadata"],
            "linked_via": "agent_import_swarm",
            "import_id": import_id,
            "import_chip_key": execution.chip_key,
            "import_result_path": str(result_path),
        },
    )
    payload_out = {
        "import_id": import_id,
        "status": "completed",
        "chip_key": execution.chip_key,
        "hook": execution.hook,
        "payload_path": str(payload_path),
        "result_path": str(result_path),
        "imported_identity": normalized,
        "identity": state.to_payload(),
        "execution": result_payload,
    }
    log_operator_event(
        state_db=state_db,
        action="import_swarm_identity",
        target_kind="agent_identity",
        target_ref=args.human_id,
        reason=args.reason,
        details={
            "status": "completed",
            "chip_key": execution.chip_key,
            "hook": execution.hook,
            "swarm_agent_id": normalized["swarm_agent_id"],
            "payload_path": str(payload_path),
            "result_path": str(result_path),
        },
    )
    close_run(
        state_db,
        run_id=run.run_id,
        status="closed",
        close_reason="agent_import_completed",
        summary="Spark Swarm identity import completed and canonicalized the agent identity.",
        facts={
            "chip_key": execution.chip_key,
            "hook": execution.hook,
            "swarm_agent_id": normalized["swarm_agent_id"],
            "payload_path": str(payload_path),
            "result_path": str(result_path),
        },
    )
    print(
        json.dumps(payload_out, indent=2)
        if args.json
        else f"Imported Spark Swarm identity for {args.human_id} onto canonical agent {state.agent_id}."
    )
    return 0


def handle_agent_import_personality(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    import_id = f"personality-import:{uuid4().hex[:12]}"
    canonical_state = read_canonical_agent_state(state_db=state_db, human_id=args.human_id)
    persona_agent_id = resolve_builder_persona_agent_id(human_id=args.human_id) or canonical_state.agent_id
    payload = build_personality_import_payload(
        human_id=args.human_id,
        agent_id=canonical_state.agent_id,
        state_db=state_db,
        config_manager=config_manager,
    )
    payload["import_id"] = import_id
    payload["requested_by"] = "local-operator"
    payload["reason"] = args.reason

    payload_path = Path(args.write_payload) if args.write_payload else (
        config_manager.paths.home / "artifacts" / "personality-imports" / f"{import_id}.payload.json"
    )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    run = open_run(
        state_db,
        run_kind="operator:agent_import_personality",
        origin_surface="agent_cli",
        summary="Operator started a personality import.",
        request_id=import_id,
        actor_id="local-operator",
        reason_code="agent_import_personality",
        human_id=args.human_id,
        agent_id=canonical_state.agent_id,
        facts={"chip_key": args.chip_key or "active", "payload_path": str(payload_path)},
    )
    try:
        if args.chip_key:
            execution = run_chip_hook(config_manager, chip_key=args.chip_key, hook="personality", payload=payload)
        else:
            execution = run_first_active_chip_hook(config_manager, hook="personality", payload=payload)
            if execution is None:
                close_run(
                    state_db,
                    run_id=run.run_id,
                    status="stalled",
                    close_reason="no_active_chip_for_hook",
                    summary="Personality import found no active chip exposing the personality hook.",
                    facts={"hook": "personality"},
                )
                print(
                    "No active chip exposes hook 'personality'. Activate the personality runtime first or pass --chip-key.",
                    file=sys.stderr,
                )
                return 1
    except ValueError as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="personality_import_invalid",
            summary="Personality import failed validation before hook execution.",
            facts={"hook": "personality", "error": str(exc)},
        )
        print(str(exc), file=sys.stderr)
        return 2

    result_payload = execution.to_payload()
    result_path = Path(args.write_result) if args.write_result else (
        config_manager.paths.home / "artifacts" / "personality-imports" / f"{import_id}.result.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="agent_cli",
        actor_id="local-operator",
        summary="Agent CLI executed a personality hook.",
        reason_code="agent_import_personality",
        keepability="operator_debug_only",
        run_id=run.run_id,
        request_id=run.request_id,
        human_id=args.human_id,
        agent_id=canonical_state.agent_id,
    )
    screened_output = screen_chip_hook_text(
        state_db=state_db,
        execution=execution,
        text=json.dumps(
            {
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "exit_code": execution.exit_code,
                "stdout": execution.stdout,
                "result": execution.output.get("result") if isinstance(execution.output, dict) else {},
            },
            indent=2,
            ensure_ascii=True,
        ),
        summary="Personality import blocked secret-like hook output before operator display.",
        reason_code="agent_import_personality_secret_like",
        policy_domain="agent_cli",
        blocked_stage="operator_output",
        run_id=run.run_id,
        request_id=run.request_id,
    )
    if not screened_output["allowed"]:
        log_operator_event(
            state_db=state_db,
            action="import_personality",
            target_kind="agent_persona",
            target_ref=persona_agent_id,
            reason=args.reason,
            details={
                "status": "blocked",
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="agent_cli",
            summary="Personality import blocked hook output because it contained secret-like material.",
            run_id=run.run_id,
            request_id=run.request_id,
            actor_id="local-operator",
            reason_code="secret_boundary_blocked",
            severity="high",
            human_id=args.human_id,
            agent_id=canonical_state.agent_id,
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
            summary="Personality import was blocked by the secret boundary.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "quarantine_id": screened_output["quarantine_id"],
            },
        )
        print(
            "Personality import output was blocked because it contained secret-like material. "
            "Review quarantine records instead of raw output.",
            file=sys.stderr,
        )
        return 1

    result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    if not execution.ok:
        log_operator_event(
            state_db=state_db,
            action="import_personality",
            target_kind="agent_persona",
            target_ref=persona_agent_id,
            reason=args.reason,
            details={
                "status": "failed",
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "exit_code": execution.exit_code,
            },
        )
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="personality_import_hook_failed",
            summary="Personality hook returned a non-zero exit code.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "exit_code": execution.exit_code,
            },
        )
        print(
            json.dumps(
                {
                    "import_id": import_id,
                    "status": "failed",
                    "chip_key": execution.chip_key,
                    "hook": execution.hook,
                    "payload_path": str(payload_path),
                    "result_path": str(result_path),
                    "execution": result_payload,
                },
                indent=2,
            )
            if args.json
            else f"Personality import failed because the external hook returned a non-zero exit code. Result artifact: {result_path}"
        )
        return 1

    try:
        normalized = normalize_personality_import(
            human_id=args.human_id,
            agent_id=canonical_state.agent_id,
            hook_output=execution.output,
        )
    except ValueError as exc:
        log_operator_event(
            state_db=state_db,
            action="import_personality",
            target_kind="agent_persona",
            target_ref=persona_agent_id,
            reason=args.reason,
            details={
                "status": "invalid",
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "error": str(exc),
            },
        )
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="personality_import_invalid_payload",
            summary="Personality hook returned an invalid import payload.",
            facts={
                "chip_key": execution.chip_key,
                "hook": execution.hook,
                "payload_path": str(payload_path),
                "result_path": str(result_path),
                "error": str(exc),
            },
        )
        print(str(exc), file=sys.stderr)
        return 2

    evolver_state_path = write_personality_evolver_state(
        config_manager=config_manager,
        evolver_state=normalized.evolver_state,
        write_path=args.write_evolver_state,
    )
    persona_profile = save_agent_persona_profile(
        agent_id=canonical_state.agent_id,
        human_id=args.human_id,
        state_db=state_db,
        base_traits=normalized.base_traits,
        persona_name=normalized.persona_name,
        persona_summary=normalized.persona_summary,
        behavioral_rules=normalized.behavioral_rules,
        provenance={
            "source_surface": "agent_cli",
            "source_ref": "agent import-personality",
            "import_id": import_id,
            "chip_key": execution.chip_key,
            "evolver_state_path": evolver_state_path,
        },
        mutation_kind="external_import",
        source_surface="agent_cli",
        source_ref="agent import-personality",
    )
    payload_out = {
        "import_id": import_id,
        "status": "completed",
        "chip_key": execution.chip_key,
        "hook": execution.hook,
        "payload_path": str(payload_path),
        "result_path": str(result_path),
        "evolver_state_path": evolver_state_path,
        "agent_id": persona_profile.get("agent_id") or persona_agent_id,
        "persona_profile": persona_profile,
        "execution": result_payload,
    }
    log_operator_event(
        state_db=state_db,
        action="import_personality",
        target_kind="agent_persona",
        target_ref=persona_agent_id,
        reason=args.reason,
        details={
            "status": "completed",
            "chip_key": execution.chip_key,
            "hook": execution.hook,
            "payload_path": str(payload_path),
            "result_path": str(result_path),
            "evolver_state_path": evolver_state_path,
        },
    )
    close_run(
        state_db,
        run_id=run.run_id,
        status="closed",
        close_reason="personality_import_completed",
        summary="Personality import completed and updated the agent persona.",
        facts={
            "chip_key": execution.chip_key,
            "hook": execution.hook,
            "payload_path": str(payload_path),
            "result_path": str(result_path),
            "evolver_state_path": evolver_state_path,
        },
    )
    print(
        json.dumps(payload_out, indent=2)
        if args.json
        else f"Imported personality for {args.human_id} onto Builder persona agent {persona_agent_id}."
    )
    return 0


def handle_agent_migrate_legacy_personality(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = migrate_legacy_human_personality_to_agent_persona(
        human_id=args.human_id,
        state_db=state_db,
        clear_overlay=not args.keep_overlay,
        force=args.force,
        source_surface="agent_cli",
        source_ref="agent migrate-legacy-personality",
    )
    payload = {
        "human_id": result.human_id,
        "agent_id": result.agent_id,
        "status": result.status,
        "migrated_traits": result.migrated_traits,
        "cleared_overlay": result.cleared_overlay,
        "persona_profile": result.persona_profile,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"Legacy personality migration for {result.human_id}: {result.status} "
            f"(agent_id={result.agent_id}, cleared_overlay={'yes' if result.cleared_overlay else 'no'})."
        )
    return 0


def _parse_identity_pair(value: str) -> tuple[str, str]:
    """Parse 'channel:user' into a (channel, user) tuple. Validates non-empty."""
    if ":" not in value:
        raise ValueError(
            f"Identity '{value}' must be in 'channel:user' form (e.g. telegram:123456789)"
        )
    channel, user = value.split(":", 1)
    channel = channel.strip()
    user = user.strip()
    if not channel or not user:
        raise ValueError(
            f"Identity '{value}' must have a non-empty channel and user"
        )
    return channel, user


def handle_identity_link(args: argparse.Namespace) -> int:
    from spark_intelligence.identity.service import link_identity_alias

    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    try:
        primary_channel, primary_user = _parse_identity_pair(args.primary)
        alias_channel, alias_user = _parse_identity_pair(args.as_)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    alias = link_identity_alias(
        state_db=state_db,
        primary_channel=primary_channel,
        primary_external_user=primary_user,
        alias_channel=alias_channel,
        alias_external_user=alias_user,
        created_by=args.created_by,
    )
    payload = {
        "alias": f"{alias.alias_channel}:{alias.alias_external_user}",
        "primary": f"{alias.primary_channel}:{alias.primary_external_user}",
        "primary_human_id": alias.primary_human_id,
        "primary_agent_id": alias.primary_agent_id,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"Linked {payload['alias']} Ã¢â€ â€™ {payload['primary']}\n"
            f"  human_id: {alias.primary_human_id}\n"
            f"  agent_id: {alias.primary_agent_id}"
        )
    return 0


def handle_identity_unlink(args: argparse.Namespace) -> int:
    from spark_intelligence.identity.service import unlink_identity_alias

    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    try:
        alias_channel, alias_user = _parse_identity_pair(args.alias)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    removed = unlink_identity_alias(
        state_db=state_db,
        alias_channel=alias_channel,
        alias_external_user=alias_user,
    )
    payload = {"alias": args.alias, "removed": removed}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if removed:
            print(f"Unlinked {args.alias}")
        else:
            print(f"No alias found for {args.alias}")
    return 0 if removed else 1


def handle_identity_list(args: argparse.Namespace) -> int:
    from spark_intelligence.identity.service import list_identity_aliases

    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    aliases = list_identity_aliases(state_db)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "alias": f"{a.alias_channel}:{a.alias_external_user}",
                        "primary": f"{a.primary_channel}:{a.primary_external_user}",
                        "primary_human_id": a.primary_human_id,
                        "primary_agent_id": a.primary_agent_id,
                    }
                    for a in aliases
                ],
                indent=2,
            )
        )
    else:
        if not aliases:
            print("No identity aliases registered.")
        else:
            print(f"Identity aliases ({len(aliases)}):")
            for a in aliases:
                print(
                    f"  {a.alias_channel}:{a.alias_external_user}"
                    f"  Ã¢â€ â€™  {a.primary_channel}:{a.primary_external_user}"
                )
                print(f"      human_id: {a.primary_human_id}")
                print(f"      agent_id: {a.primary_agent_id}")
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
    if args.command == "diagnostics" and args.diagnostics_command == "scan":
        return handle_diagnostics_scan(args)
    if args.command == "status":
        return handle_status(args)
    if args.command == "mission" and args.mission_command == "status":
        return handle_mission_status(args)
    if args.command == "mission" and args.mission_command == "plan":
        return handle_mission_plan(args)
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
    if args.command == "operator" and args.operator_command == "issue-pairing-code":
        return handle_operator_issue_pairing_code(args)
    if args.command == "operator" and args.operator_command == "approve-pairing-code":
        return handle_operator_approve_pairing_code(args)
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
    if args.command == "operator" and args.operator_command == "personality":
        return handle_operator_personality(args)
    if args.command == "operator" and args.operator_command == "observer-packets":
        return handle_operator_observer_packets(args)
    if args.command == "operator" and args.operator_command == "export-observer-packets":
        return handle_operator_export_observer_packets(args)
    if args.command == "operator" and args.operator_command == "handoff-observer":
        return handle_operator_handoff_observer(args)
    if args.command == "operator" and args.operator_command == "observer-handoffs":
        return handle_operator_observer_handoffs(args)
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
    if args.command == "gateway" and args.gateway_command == "ask-telegram":
        return handle_gateway_ask_telegram(args)
    if args.command == "gateway" and args.gateway_command == "shadow-telegram":
        return handle_gateway_shadow_telegram(args)
    if args.command == "gateway" and args.gateway_command == "shadow-telegram-pack":
        return handle_gateway_shadow_telegram_pack(args)
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
    if args.command == "chips" and args.chips_command == "why":
        return handle_chips_why(args)
    if args.command == "chips" and args.chips_command == "create":
        return handle_chips_create(args)
    if args.command == "loops" and args.loops_command == "run":
        return handle_loops_run(args)
    if args.command == "drafts" and args.drafts_command == "list":
        return handle_drafts_list(args)
    if args.command == "drafts" and args.drafts_command == "show":
        return handle_drafts_show(args)
    if args.command == "instructions" and args.instructions_command == "list":
        return handle_instructions_list(args)
    if args.command == "instructions" and args.instructions_command == "add":
        return handle_instructions_add(args)
    if args.command == "instructions" and args.instructions_command == "archive":
        return handle_instructions_archive(args)
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
    if args.command == "browser" and args.browser_command == "status":
        return handle_browser_status(args)
    if args.command == "browser" and args.browser_command == "page-snapshot":
        return handle_browser_page_snapshot(args)
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
    if args.command == "memory" and args.memory_command == "status":
        return handle_memory_status(args)
    if args.command == "memory" and args.memory_command == "lookup-current-state":
        return handle_memory_lookup_current_state(args)
    if args.command == "memory" and args.memory_command == "inspect-human":
        return handle_memory_inspect_human(args)
    if args.command == "memory" and args.memory_command == "export-shadow-replay":
        return handle_memory_export_shadow_replay(args)
    if args.command == "memory" and args.memory_command == "export-shadow-replay-batch":
        return handle_memory_export_shadow_replay_batch(args)
    if args.command == "memory" and args.memory_command == "export-sdk-maintenance-replay":
        return handle_memory_export_sdk_maintenance_replay(args)
    if args.command == "memory" and args.memory_command == "run-sdk-maintenance":
        return handle_memory_run_sdk_maintenance(args)
    if args.command == "memory" and args.memory_command == "compile-telegram-kb":
        return handle_memory_compile_telegram_kb(args)
    if args.command == "memory" and args.memory_command == "run-telegram-regression":
        return handle_memory_run_telegram_regression(args)
    if args.command == "memory" and args.memory_command == "benchmark-architectures":
        return handle_memory_benchmark_architectures(args)
    if args.command == "memory" and args.memory_command == "soak-architectures":
        return handle_memory_soak_architectures(args)
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
    if args.command == "swarm" and args.swarm_command == "doctor":
        return handle_swarm_doctor(args)
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
    if args.command == "harness" and args.harness_command == "status":
        return handle_harness_status(args)
    if args.command == "harness" and args.harness_command == "plan":
        return handle_harness_plan(args)
    if args.command == "harness" and args.harness_command == "execute":
        return handle_harness_execute(args)
    if args.command == "agent" and args.agent_command == "inspect":
        return handle_agent_inspect(args)
    if args.command == "agent" and args.agent_command == "rename":
        return handle_agent_rename(args)
    if args.command == "agent" and args.agent_command == "link-swarm":
        return handle_agent_link_swarm(args)
    if args.command == "agent" and args.agent_command == "import-swarm":
        return handle_agent_import_swarm(args)
    if args.command == "agent" and args.agent_command == "import-personality":
        return handle_agent_import_personality(args)
    if args.command == "agent" and args.agent_command == "migrate-legacy-personality":
        return handle_agent_migrate_legacy_personality(args)
    if args.command == "identity" and args.identity_command == "link":
        return handle_identity_link(args)
    if args.command == "identity" and args.identity_command == "unlink":
        return handle_identity_unlink(args)
    if args.command == "identity" and args.identity_command == "list":
        return handle_identity_list(args)
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
