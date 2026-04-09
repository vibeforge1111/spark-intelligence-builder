from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from spark_intelligence.attachments import attachment_status
from spark_intelligence.attachments.snapshot import sync_attachment_snapshot
from spark_intelligence.adapters.discord.runtime import build_discord_runtime_summary
from spark_intelligence.adapters.telegram.runtime import build_telegram_runtime_summary, read_telegram_runtime_health
from spark_intelligence.adapters.whatsapp.runtime import build_whatsapp_runtime_summary
from spark_intelligence.auth.providers import get_provider_spec
from spark_intelligence.auth.runtime import build_auth_status_report, runtime_provider_health
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.jobs.service import oauth_maintenance_health
from spark_intelligence.observability.checks import evaluate_stop_ship_issues
from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    record_environment_snapshot,
    repair_foreground_browser_hook_failures,
    repair_non_promotable_chip_hook_dispositions,
)
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, researcher_bridge_status, resolve_researcher_config_path
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import swarm_status


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class DoctorReport:
    checks: list[DoctorCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_json(self) -> str:
        payload = {
            "ok": self.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "follow_up_surfaces": self.follow_up_surfaces(),
        }
        return json.dumps(payload, indent=2)

    def to_text(self) -> str:
        lines = [f"Doctor status: {'ok' if self.ok else 'degraded'}"]
        for check in self.checks:
            marker = "ok" if check.ok else "fail"
            lines.append(f"- [{marker}] {check.name}: {check.detail}")
        if not self.ok:
            lines.append(
                "- follow-up: use `spark-intelligence status` for repair hints and "
                "`spark-intelligence operator security` for operator/security triage"
            )
        return "\n".join(lines)

    def follow_up_surfaces(self) -> list[str]:
        if self.ok:
            return []
        return [
            "spark-intelligence status",
            "spark-intelligence operator security",
        ]


def run_doctor(config_manager: ConfigManager, state_db: StateDB) -> DoctorReport:
    checks: list[DoctorCheck] = []
    paths = config_manager.paths
    sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    record_environment_snapshot(
        state_db,
        surface="doctor_cli",
        summary="Doctor CLI environment snapshot recorded.",
        provider_id=str(config_manager.get_path("providers.default_provider")) if config_manager.get_path("providers.default_provider") else None,
        runtime_root=str(config_manager.get_path("spark.researcher.runtime_root")) if config_manager.get_path("spark.researcher.runtime_root") else None,
        config_path=str(config_manager.get_path("spark.researcher.config_path")) if config_manager.get_path("spark.researcher.config_path") else None,
        env_refs={"home": str(paths.home)},
        facts={"surface": "doctor_cli"},
    )
    repair_non_promotable_chip_hook_dispositions(state_db)
    repair_foreground_browser_hook_failures(state_db)

    checks.append(DoctorCheck("home", paths.home.exists(), str(paths.home)))
    checks.append(DoctorCheck("config.yaml", paths.config_yaml.exists(), str(paths.config_yaml)))
    checks.append(DoctorCheck(".env", paths.env_file.exists(), str(paths.env_file)))
    checks.append(DoctorCheck("state.db", paths.state_db.exists(), str(paths.state_db)))
    env_permissions_ok, env_permissions_detail = config_manager.env_file_permission_status()
    checks.append(DoctorCheck(".env-permissions", env_permissions_ok, env_permissions_detail))

    try:
        config = config_manager.load()
        checks.append(DoctorCheck("config-load", True, f"loaded workspace {config.get('workspace', {}).get('id', 'unknown')}"))
    except Exception as exc:  # pragma: no cover - defensive
        checks.append(DoctorCheck("config-load", False, str(exc)))

    try:
        with state_db.connect() as conn:
            conn.execute("SELECT 1 FROM schema_info LIMIT 1").fetchone()
        checks.append(DoctorCheck("state-schema", True, "schema initialized"))
    except sqlite3.Error as exc:
        checks.append(DoctorCheck("state-schema", False, str(exc)))

    try:
        with state_db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM workspace_roles WHERE human_id = 'local-operator' AND role = 'operator_admin' LIMIT 1"
            ).fetchone()
        checks.append(DoctorCheck("operator-authority", bool(row), "local operator present"))
    except sqlite3.Error as exc:
        checks.append(DoctorCheck("operator-authority", False, str(exc)))

    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    if auth_report.providers:
        unresolved = [
            (
                f"{provider.provider_id}:{provider.status}"
                if provider.auth_method == "oauth"
                else f"{provider.provider_id}:{provider.secret_ref.ref_id if provider.secret_ref else 'missing'}"
            )
            for provider in auth_report.providers
            if not provider.secret_present
        ]
        checks.append(
            DoctorCheck(
                "provider-auth",
                auth_report.ok,
                "all provider auth profiles resolved" if not unresolved else ", ".join(unresolved),
            )
        )
    else:
        checks.append(DoctorCheck("provider-auth", True, "no providers configured yet"))
    provider_runtime_ok, provider_runtime_detail = runtime_provider_health(
        config_manager=config_manager,
        state_db=state_db,
    )
    checks.append(DoctorCheck("provider-runtime", provider_runtime_ok, provider_runtime_detail))
    oauth_maintenance_ok, oauth_maintenance_detail = oauth_maintenance_health(
        config_manager=config_manager,
        state_db=state_db,
    )
    checks.append(DoctorCheck("oauth-maintenance", oauth_maintenance_ok, oauth_maintenance_detail))
    provider_execution_ok, provider_execution_detail = provider_execution_health(
        config_manager=config_manager,
        state_db=state_db,
        auth_report=auth_report,
    )
    checks.append(DoctorCheck("provider-execution", provider_execution_ok, provider_execution_detail))

    researcher_enabled = bool(config_manager.get_path("spark.researcher.enabled", default=True))
    researcher_root, researcher_source = discover_researcher_runtime_root(config_manager)
    if not researcher_enabled:
        checks.append(
            DoctorCheck(
                "researcher-bridge",
                True,
                "disabled by operator",
            )
        )
    elif researcher_root:
        researcher_config_path = resolve_researcher_config_path(config_manager, researcher_root)
        checks.append(
            DoctorCheck(
                "researcher-bridge",
                researcher_config_path.exists(),
                (
                    f"{researcher_source}:{researcher_root} config={researcher_config_path}"
                    if researcher_config_path.exists()
                    else f"{researcher_source}:{researcher_root} missing config at {researcher_config_path}"
                ),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "researcher-bridge",
                True,
                "not connected yet; advisory bridge will stay in stub mode",
            )
        )

    swarm = swarm_status(config_manager, state_db)
    swarm_hosted_fields = [swarm.api_url, swarm.workspace_id, swarm.access_token_env]
    hosted_field_count = sum(1 for field in swarm_hosted_fields if field)

    if not swarm.enabled:
        checks.append(DoctorCheck("swarm-bridge", True, "disabled by operator"))
    elif not swarm.configured:
        checks.append(DoctorCheck("swarm-bridge", True, "not connected yet; Swarm sync is optional"))
    elif swarm.runtime_root and hosted_field_count == 0:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                True,
                f"local swarm repo connected at {swarm.runtime_root}; hosted sync not configured yet",
            )
        )
    elif not swarm.researcher_ready:
        checks.append(DoctorCheck("swarm-bridge", False, "configured but Spark Researcher is not ready for payload export"))
    elif 0 < hosted_field_count < 3:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                False,
                "configured but missing api_url, workspace_id, or access_token_env",
            )
        )
    elif not swarm.api_ready:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                True,
                "API config present; upload readiness depends on access token resolution and latest payload availability",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                True,
                f"ready api={swarm.api_url} workspace={swarm.workspace_id}",
            )
        )

    attachments = attachment_status(config_manager)
    attachment_count = len(attachments.records)
    if attachments.warnings:
        checks.append(
            DoctorCheck(
                "attachments",
                True,
                f"{attachment_count} discovered with {len(attachments.warnings)} warning(s)",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "attachments",
                True,
                f"{attachment_count} discovered ({len([r for r in attachments.records if r.kind == 'chip'])} chips, {len([r for r in attachments.records if r.kind == 'path'])} paths)",
            )
        )

    checks.append(_telegram_runtime_check(config_manager=config_manager, state_db=state_db))
    checks.append(_discord_runtime_check(config_manager=config_manager, state_db=state_db))
    checks.append(_whatsapp_runtime_check(config_manager=config_manager, state_db=state_db))
    stop_ship_issues = evaluate_stop_ship_issues(config_manager=config_manager, state_db=state_db, emit_contradictions=True)
    checks.extend(_watchtower_health_checks(config_manager=config_manager, state_db=state_db))
    for issue in stop_ship_issues:
        checks.append(DoctorCheck(issue.name, issue.ok, issue.detail))

    return DoctorReport(checks=checks)


def _telegram_runtime_check(*, config_manager: ConfigManager, state_db: StateDB) -> DoctorCheck:
    summary = build_telegram_runtime_summary(config_manager, state_db)
    if not summary.configured:
        return DoctorCheck("telegram-runtime", True, "not configured")

    health = read_telegram_runtime_health(state_db)
    if not summary.auth_ref:
        return DoctorCheck("telegram-runtime", False, "configured but auth_ref is missing")
    if health.auth_status in {"failed", "missing"}:
        return DoctorCheck(
            "telegram-runtime",
            False,
            f"auth={health.auth_status} error={health.auth_error or 'unknown'}",
        )
    if health.consecutive_failures > 0 and health.last_failure_at:
        return DoctorCheck(
            "telegram-runtime",
            False,
            (
                f"poll_failures={health.consecutive_failures} "
                f"last_type={health.last_failure_type or 'unknown'} "
                f"backoff={health.last_backoff_seconds}s"
            ),
        )
    detail_parts = [
        f"status={summary.status or 'unknown'}",
        f"pairing_mode={summary.pairing_mode or 'unknown'}",
        f"auth_ref={summary.auth_ref}",
        f"auth={health.auth_status or 'not_checked'}",
        f"allowed_users={summary.allowed_user_count}",
    ]
    if health.bot_username:
        detail_parts.append(f"bot=@{health.bot_username}")
    return DoctorCheck("telegram-runtime", True, " ".join(detail_parts))


def _discord_runtime_check(*, config_manager: ConfigManager, state_db: StateDB) -> DoctorCheck:
    summary = build_discord_runtime_summary(config_manager, state_db)
    if not summary.configured:
        return DoctorCheck("discord-runtime", True, "not configured")

    detail_parts = [
        f"status={summary.status or 'unknown'}",
        f"pairing_mode={summary.pairing_mode or 'unknown'}",
        f"auth_ref={summary.auth_ref or 'missing'}",
        f"allowed_users={summary.allowed_user_count}",
        f"ingress={summary.ingress_mode()}",
    ]
    if summary.interaction_public_key_configured:
        detail_parts.append("interaction_public_key=configured")
    if summary.legacy_message_webhook_enabled:
        detail_parts.append(f"webhook_auth_ref={summary.webhook_auth_ref or 'missing'}")
    if summary.ingress_ready():
        return DoctorCheck("discord-runtime", True, " ".join(detail_parts))
    if summary.legacy_message_webhook_enabled:
        return DoctorCheck("discord-runtime", False, " ".join(detail_parts))
    return DoctorCheck(
        "discord-runtime",
        False,
        (
            "no signed interaction public key configured and "
            "legacy message webhook compatibility is disabled"
        ),
    )


def _whatsapp_runtime_check(*, config_manager: ConfigManager, state_db: StateDB) -> DoctorCheck:
    summary = build_whatsapp_runtime_summary(config_manager, state_db)
    if not summary.configured:
        return DoctorCheck("whatsapp-runtime", True, "not configured")

    detail_parts = [
        f"status={summary.status or 'unknown'}",
        f"pairing_mode={summary.pairing_mode or 'unknown'}",
        f"auth_ref={summary.auth_ref or 'missing'}",
        f"allowed_users={summary.allowed_user_count}",
        f"ingress={summary.ingress_mode()}",
    ]
    if summary.webhook_auth_ref:
        detail_parts.append(f"webhook_auth_ref={summary.webhook_auth_ref}")
    if summary.webhook_verify_token_ref:
        detail_parts.append(f"webhook_verify_token_ref={summary.webhook_verify_token_ref}")
    if summary.ingress_ready():
        return DoctorCheck("whatsapp-runtime", True, " ".join(detail_parts))
    return DoctorCheck(
        "whatsapp-runtime",
        False,
        " ".join(detail_parts),
    )


def provider_execution_health(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    auth_report: object,
) -> tuple[bool, str]:
    providers = getattr(auth_report, "providers", [])
    if not providers:
        return True, "no providers configured yet"
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    blocked: list[str] = []
    ready: list[str] = []
    for provider in providers:
        spec = get_provider_spec(provider.provider_id)
        if spec.execution_transport == "external_cli_wrapper":
            if not researcher.available:
                blocked.append(
                    f"{provider.provider_id}:{spec.execution_transport}:researcher_{researcher.mode}"
                )
            else:
                ready.append(f"{provider.provider_id}:{spec.execution_transport}")
        else:
            ready.append(f"{provider.provider_id}:{spec.execution_transport}")
    if blocked:
        return False, ", ".join(blocked)
    return True, ", ".join(ready) if ready else "no provider execution transports active"


def _watchtower_health_checks(*, config_manager: ConfigManager, state_db: StateDB) -> list[DoctorCheck]:
    snapshot = build_watchtower_snapshot(state_db)
    dimensions = snapshot.get("health_dimensions") or {}
    mapping = (
        ("watchtower-ingress", "ingress_health"),
        ("watchtower-execution", "execution_health"),
        ("watchtower-delivery", "delivery_health"),
        ("watchtower-freshness", "scheduler_freshness"),
        ("watchtower-parity", "environment_parity"),
    )
    checks: list[DoctorCheck] = []
    failing_states = {"execution_impaired", "delivery_impaired", "stalled", "parity_broken", "degraded"}
    for check_name, dimension_key in mapping:
        dimension = dimensions.get(dimension_key) or {}
        state = str(dimension.get("state") or "unknown")
        detail = str(dimension.get("detail") or "No detail recorded.")
        checks.append(
            DoctorCheck(
                check_name,
                state not in failing_states,
                f"state={state} {detail}",
            )
        )
    contradictions = snapshot.get("contradictions") or {}
    open_count = int(((contradictions.get("counts") or {}).get("open")) or 0)
    checks.append(
        DoctorCheck(
            "watchtower-contradictions",
            open_count == 0,
            f"open={open_count} resolved={int(((contradictions.get('counts') or {}).get('resolved')) or 0)}",
        )
    )
    memory_shadow = (snapshot.get("panels") or {}).get("memory_shadow") or {}
    memory_counts = memory_shadow.get("counts") or {}
    read_requests = int(memory_counts.get("read_requests") or 0)
    read_hits = int(memory_counts.get("read_hits") or 0)
    shadow_only_reads = int(memory_counts.get("shadow_only_reads") or 0)
    if read_requests == 0:
        checks.append(
            DoctorCheck(
                "watchtower-memory-shadow",
                True,
                "no memory-shadow traffic recorded yet",
            )
        )
    elif read_hits == 0 and shadow_only_reads == 0:
        checks.append(
            DoctorCheck(
                "watchtower-memory-shadow",
                False,
                f"state=memory_abstaining read_requests={read_requests} read_hits=0 shadow_only_reads=0",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "watchtower-memory-shadow",
                True,
                (
                    f"read_requests={read_requests} read_hits={read_hits} "
                    f"shadow_only_reads={shadow_only_reads}"
                ),
            )
        )
    contract_violations = int(memory_counts.get("contract_violations") or 0)
    invalid_role_events = int(memory_counts.get("invalid_role_events") or 0)
    checks.append(
        DoctorCheck(
            "watchtower-memory-contract",
            contract_violations == 0,
            f"contract_violations={contract_violations} invalid_role_events={invalid_role_events}",
        )
    )
    observer_incidents = (snapshot.get("panels") or {}).get("observer_incidents") or {}
    observer_counts = observer_incidents.get("counts") or {}
    observer_total = int(observer_counts.get("total") or 0)
    checks.append(
        DoctorCheck(
            "watchtower-observer-incidents",
            observer_total == 0,
            (
                f"total={observer_total} "
                f"distinct_classes={int(observer_counts.get('distinct_classes') or 0)}"
            ),
        )
    )
    observer_packets = (snapshot.get("panels") or {}).get("observer_packets") or {}
    packet_counts = observer_packets.get("counts") or {}
    packet_kinds = observer_packets.get("counts_by_kind") or {}
    packet_total = int(packet_counts.get("total") or 0)
    checks.append(
        DoctorCheck(
            "watchtower-observer-packets",
            packet_total >= observer_total,
            f"packets={packet_total} incidents={observer_total}",
        )
    )
    checks.append(
        DoctorCheck(
            "watchtower-observer-packet-kinds",
            packet_total == 0 or int(packet_counts.get("distinct_kinds") or 0) >= 3,
            (
                f"distinct_kinds={int(packet_counts.get('distinct_kinds') or 0)} "
                f"self_observation={int(packet_kinds.get('self_observation') or 0)} "
                f"incident_report={int(packet_kinds.get('incident_report') or 0)} "
                f"repair_plan={int(packet_kinds.get('repair_plan') or 0)} "
                f"reflection_digest={int(packet_kinds.get('reflection_digest') or 0)}"
            ),
        )
    )
    observer_handoffs = (snapshot.get("panels") or {}).get("observer_handoffs") or {}
    handoff_counts = observer_handoffs.get("counts") or {}
    checks.append(
        DoctorCheck(
            "watchtower-observer-handoffs",
            int(handoff_counts.get("problematic") or 0) == 0,
            (
                f"total={int(handoff_counts.get('total') or 0)} "
                f"completed={int(handoff_counts.get('completed') or 0)} "
                f"failed={int(handoff_counts.get('failed') or 0)} "
                f"blocked={int(handoff_counts.get('blocked') or 0)} "
                f"stalled={int(handoff_counts.get('stalled') or 0)}"
            ),
        )
    )
    personality_panel = (snapshot.get("panels") or {}).get("personality") or {}
    personality_counts = personality_panel.get("counts") or {}
    checks.append(
        DoctorCheck(
            "watchtower-personality-mirrors",
            int(personality_counts.get("mirror_drift") or 0) == 0,
            (
                f"trait_profiles={int(personality_counts.get('trait_profiles') or 0)} "
                f"observation_rows={int(personality_counts.get('observation_rows') or 0)} "
                f"evolution_rows={int(personality_counts.get('evolution_rows') or 0)} "
                f"mirror_drift={int(personality_counts.get('mirror_drift') or 0)}"
            ),
        )
    )
    personality_import = personality_panel.get("personality_import") or {}
    identity_panel = (snapshot.get("panels") or {}).get("agent_identity") or {}
    identity_counts = identity_panel.get("counts") or {}
    personality_enabled = bool(config_manager.get_path("spark.personality.enabled", default=True))
    personality_import_ready = bool(personality_import.get("ready"))
    canonical_agent_count = int(identity_counts.get("canonical_agents") or 0)
    personality_import_required = personality_enabled and canonical_agent_count > 0
    checks.append(
        DoctorCheck(
            "watchtower-personality-import",
            (not personality_import_required) or personality_import_ready,
            (
                f"enabled={'yes' if personality_enabled else 'no'} "
                f"required={'yes' if personality_import_required else 'no'} "
                f"canonical_agents={canonical_agent_count} "
                f"personality_import_ready={'yes' if personality_import_ready else 'no'} "
                f"active_personality_hook_chips={len(personality_import.get('active_chip_keys') or [])}"
            ),
        )
    )
    checks.append(
        DoctorCheck(
            "watchtower-agent-identity",
            int(identity_counts.get("identity_conflicts") or 0) == 0,
            (
                f"canonical_agents={int(identity_counts.get('canonical_agents') or 0)} "
                f"builder_local={int(identity_counts.get('builder_local') or 0)} "
                f"spark_swarm={int(identity_counts.get('spark_swarm') or 0)} "
                f"aliases={int(identity_counts.get('aliases') or 0)} "
                f"identity_conflicts={int(identity_counts.get('identity_conflicts') or 0)}"
            ),
        )
    )
    identity_import = identity_panel.get("identity_import") or {}
    builder_local_count = int(identity_counts.get("builder_local") or 0)
    identity_import_ready = bool(identity_import.get("ready"))
    checks.append(
        DoctorCheck(
            "watchtower-agent-identity-import",
            builder_local_count == 0 or identity_import_ready,
            (
                f"builder_local={builder_local_count} "
                f"identity_import_ready={'yes' if identity_import_ready else 'no'} "
                f"active_identity_hook_chips={len(identity_import.get('active_chip_keys') or [])}"
            ),
        )
    )
    return checks
