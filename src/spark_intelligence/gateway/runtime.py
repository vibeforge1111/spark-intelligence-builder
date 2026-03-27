from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any

from spark_intelligence.adapters.discord.runtime import build_discord_runtime_summary, simulate_discord_message
from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.runtime import (
    build_telegram_runtime_summary,
    poll_telegram_updates_once,
    read_telegram_runtime_health,
    record_telegram_auth_result,
    record_telegram_poll_failure,
    record_telegram_poll_success,
    simulate_telegram_update,
)
from spark_intelligence.adapters.whatsapp.runtime import build_whatsapp_runtime_summary, simulate_whatsapp_message
from spark_intelligence.auth.providers import get_provider_spec
from spark_intelligence.auth.runtime import build_auth_status_report, runtime_provider_health
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import provider_execution_health, run_doctor
from spark_intelligence.gateway.tracing import append_gateway_trace, outbound_log_path, read_gateway_traces, read_outbound_audit, trace_log_path
from spark_intelligence.jobs.service import oauth_maintenance_health_from_report
from spark_intelligence.observability.store import record_environment_snapshot
from spark_intelligence.researcher_bridge import researcher_bridge_status
from spark_intelligence.state.db import StateDB


@dataclass
class GatewayStatus:
    ready: bool
    configured_channels: list[str]
    configured_providers: list[str]
    doctor_ok: bool
    provider_runtime_ok: bool
    provider_runtime_detail: str
    provider_execution_ok: bool
    provider_execution_detail: str
    oauth_maintenance_ok: bool
    oauth_maintenance_detail: str
    repair_hints: list[str]
    provider_lines: list[str]
    adapter_lines: list[str]

    def to_json(self) -> str:
        return json.dumps(
            {
                "ready": self.ready,
                "configured_channels": self.configured_channels,
                "configured_providers": self.configured_providers,
                "doctor_ok": self.doctor_ok,
                "provider_runtime_ok": self.provider_runtime_ok,
                "provider_runtime_detail": self.provider_runtime_detail,
                "provider_execution_ok": self.provider_execution_ok,
                "provider_execution_detail": self.provider_execution_detail,
                "oauth_maintenance_ok": self.oauth_maintenance_ok,
                "oauth_maintenance_detail": self.oauth_maintenance_detail,
                "repair_hints": self.repair_hints,
                "provider_lines": self.provider_lines,
                "adapter_lines": self.adapter_lines,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Gateway ready: {'yes' if self.ready else 'no'}"]
        lines.append(f"- providers: {', '.join(self.configured_providers) if self.configured_providers else 'none'}")
        lines.append(f"- channels: {', '.join(self.configured_channels) if self.configured_channels else 'none'}")
        lines.append(f"- doctor: {'ok' if self.doctor_ok else 'degraded'}")
        lines.append(
            f"- provider-runtime: {'ok' if self.provider_runtime_ok else 'degraded'} {self.provider_runtime_detail}"
        )
        lines.append(
            f"- provider-execution: {'ok' if self.provider_execution_ok else 'degraded'} {self.provider_execution_detail}"
        )
        lines.append(
            f"- oauth-maintenance: {'ok' if self.oauth_maintenance_ok else 'degraded'} {self.oauth_maintenance_detail}"
        )
        lines.extend(f"- repair-hint: {hint}" for hint in self.repair_hints)
        lines.extend(f"- {line}" for line in self.provider_lines)
        lines.extend(self.adapter_lines)
        return "\n".join(lines)


@dataclass
class GatewayStartReport:
    ok: bool
    text: str

    def __str__(self) -> str:
        return self.text


def gateway_status(config_manager: ConfigManager, state_db: StateDB) -> GatewayStatus:
    config = config_manager.load()
    raw_channel_records = config.get("channels", {}).get("records", {})
    channel_records = list(raw_channel_records.keys())
    active_channel_records = [
        channel_id
        for channel_id, record in raw_channel_records.items()
        if isinstance(record, dict) and str(record.get("status") or "enabled") in {"enabled", "configured"}
    ]
    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    configured_providers = [provider.provider_id for provider in auth_report.providers]
    provider_runtime_ok, provider_runtime_detail = runtime_provider_health(
        config_manager=config_manager,
        state_db=state_db,
    )
    oauth_maintenance_ok, oauth_maintenance_detail = oauth_maintenance_health_from_report(
        state_db=state_db,
        auth_report=auth_report,
    )
    provider_execution_ok, provider_execution_detail = provider_execution_health(
        config_manager=config_manager,
        state_db=state_db,
        auth_report=auth_report,
    )
    doctor_report = run_doctor(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    telegram_summary = build_telegram_runtime_summary(config_manager, state_db)
    discord_summary = build_discord_runtime_summary(config_manager, state_db)
    whatsapp_summary = build_whatsapp_runtime_summary(config_manager, state_db)
    ready = bool(configured_providers) and bool(active_channel_records) and doctor_report.ok
    return GatewayStatus(
        ready=ready,
        configured_channels=channel_records,
        configured_providers=configured_providers,
        doctor_ok=doctor_report.ok,
        provider_runtime_ok=provider_runtime_ok,
        provider_runtime_detail=provider_runtime_detail,
        provider_execution_ok=provider_execution_ok,
        provider_execution_detail=provider_execution_detail,
        oauth_maintenance_ok=oauth_maintenance_ok,
        oauth_maintenance_detail=oauth_maintenance_detail,
        repair_hints=_gateway_repair_hints(
            config=config,
            auth_report=auth_report,
            provider_runtime_ok=provider_runtime_ok,
            provider_execution_ok=provider_execution_ok,
            oauth_maintenance_ok=oauth_maintenance_ok,
            provider_execution_detail=provider_execution_detail,
        ),
        provider_lines=[
            _provider_status_line(
                provider.provider_id,
                provider.auth_method,
                provider.status,
                researcher_available=researcher.available,
                researcher_mode=researcher.mode,
            )
            for provider in auth_report.providers
        ],
        adapter_lines=[telegram_summary.to_line(), discord_summary.to_line(), whatsapp_summary.to_line()],
    )


def gateway_start(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    once: bool = False,
    continuous: bool = False,
    max_cycles: int | None = None,
    poll_timeout_seconds: int = 5,
) -> GatewayStartReport:
    status = gateway_status(config_manager, state_db)
    record_environment_snapshot(
        state_db,
        surface="gateway_runtime",
        summary="Gateway runtime environment snapshot recorded.",
        provider_id=str(config_manager.get_path("providers.default_provider")) if config_manager.get_path("providers.default_provider") else None,
        runtime_root=str(config_manager.get_path("spark.researcher.runtime_root")) if config_manager.get_path("spark.researcher.runtime_root") else None,
        config_path=str(config_manager.get_path("spark.researcher.config_path")) if config_manager.get_path("spark.researcher.config_path") else None,
        env_refs={
            "configured_channels": status.configured_channels,
            "configured_providers": status.configured_providers,
        },
        facts={
            "provider_runtime_ok": status.provider_runtime_ok,
            "provider_execution_ok": status.provider_execution_ok,
            "oauth_maintenance_ok": status.oauth_maintenance_ok,
        },
    )
    lines = ["Spark Intelligence gateway start"]
    lines.append(status.to_text())
    lines.append("")
    ok = True
    config = config_manager.load()
    telegram_record = config.get("channels", {}).get("records", {}).get("telegram")
    discord_record = config.get("channels", {}).get("records", {}).get("discord")
    whatsapp_record = config.get("channels", {}).get("records", {}).get("whatsapp")
    if discord_record:
        lines.append("Discord adapter is configured, but a foreground runtime is not implemented yet.")
    if whatsapp_record:
        lines.append("WhatsApp adapter is configured, but a foreground runtime is not implemented yet.")
    if status.configured_providers and not status.provider_runtime_ok:
        lines.append("Provider runtime readiness is degraded. Gateway did not start polling.")
        return GatewayStartReport(ok=False, text="\n".join(lines))
    if status.configured_providers and not status.provider_execution_ok:
        lines.append("Provider execution readiness is degraded. Gateway did not start polling.")
        return GatewayStartReport(ok=False, text="\n".join(lines))
    if not telegram_record:
        lines.append("No Telegram adapter configured. Gateway is idle.")
        return GatewayStartReport(ok=True, text="\n".join(lines))
    telegram_summary = build_telegram_runtime_summary(config_manager, state_db)
    telegram_status = str(telegram_summary.status or "enabled")
    if telegram_status == "disabled":
        lines.append("Telegram adapter is disabled by operator. Gateway did not start polling.")
        lines.append("- repair-hint: spark-intelligence operator set-channel telegram enabled")
        return GatewayStartReport(ok=True, text="\n".join(lines))
    if telegram_status == "paused":
        lines.append("Telegram adapter is paused by operator. Gateway did not start polling.")
        lines.append("- repair-hint: spark-intelligence operator set-channel telegram enabled")
        return GatewayStartReport(ok=True, text="\n".join(lines))

    auth_ref = telegram_record.get("auth_ref")
    env_map = config_manager.read_env_map()
    token = env_map.get(auth_ref) if auth_ref else None
    if not token:
        record_telegram_auth_result(state_db=state_db, status="missing", error="Telegram auth ref is missing or unresolved.")
        lines.append("Telegram auth ref is missing or unresolved. Gateway did not start polling.")
        return GatewayStartReport(ok=False, text="\n".join(lines))

    client = TelegramBotApiClient(token=token)
    try:
        me = client.get_me().get("result", {})
    except urllib.error.HTTPError as exc:
        record_telegram_auth_result(state_db=state_db, status="failed", error=f"HTTP {exc.code}")
        lines.append(f"Telegram auth check failed with HTTP {exc.code}. Gateway did not start polling.")
        return GatewayStartReport(ok=False, text="\n".join(lines))
    except urllib.error.URLError as exc:
        record_telegram_auth_result(state_db=state_db, status="failed", error=str(exc.reason))
        lines.append(f"Telegram auth check failed: {exc.reason}. Gateway did not start polling.")
        return GatewayStartReport(ok=False, text="\n".join(lines))
    except RuntimeError as exc:
        record_telegram_auth_result(state_db=state_db, status="failed", error=str(exc))
        lines.append(f"Telegram auth check failed: {exc}. Gateway did not start polling.")
        return GatewayStartReport(ok=False, text="\n".join(lines))
    record_telegram_auth_result(
        state_db=state_db,
        status="ok",
        bot_username=str(me.get("username")) if me.get("username") else None,
        error=None,
    )
    lines.append(f"Telegram bot authenticated: @{me.get('username', 'unknown')}")
    lines.append(f"Gateway trace log: {trace_log_path(config_manager)}")
    lines.append(f"Gateway outbound audit log: {outbound_log_path(config_manager)}")

    cycle_limit = 1 if once else max_cycles
    cycle_index = 0
    try:
        while True:
            if cycle_limit is not None and cycle_index >= cycle_limit:
                break
            try:
                poll_result = poll_telegram_updates_once(
                    config_manager=config_manager,
                    state_db=state_db,
                    client=client,
                    timeout_seconds=poll_timeout_seconds,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
                failure_type = _classify_telegram_failure(exc)
                failure_message = _telegram_failure_message(exc)
                backoff_seconds = _record_telegram_poll_failure(
                    config_manager=config_manager,
                    state_db=state_db,
                    failure_type=failure_type,
                    message=failure_message,
                )
                lines.append(f"Cycle {cycle_index + 1}:")
                lines.append(f"  Telegram polling failure: type={failure_type} message={failure_message}")
                lines.append(f"  Backoff seconds: {backoff_seconds}")
                ok = False
                cycle_index += 1
                if once or not continuous:
                    break
                sleep(max(backoff_seconds, 1))
                continue
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                failure_type = "unexpected_error"
                failure_message = str(exc)
                backoff_seconds = _record_telegram_poll_failure(
                    config_manager=config_manager,
                    state_db=state_db,
                    failure_type=failure_type,
                    message=failure_message,
                )
                lines.append(f"Cycle {cycle_index + 1}:")
                lines.append(f"  Telegram polling failure: type={failure_type} message={failure_message}")
                lines.append(f"  Backoff seconds: {backoff_seconds}")
                ok = False
                cycle_index += 1
                if once or not continuous:
                    break
                sleep(max(backoff_seconds, 1))
                continue
            record_telegram_poll_success(state_db=state_db)
            lines.append(f"Cycle {cycle_index + 1}:")
            lines.extend(f"  {line}" for line in poll_result.to_text().splitlines())
            cycle_index += 1
            if once or not continuous:
                break
            if poll_timeout_seconds <= 0:
                sleep(1)
    except KeyboardInterrupt:
        lines.append("")
        lines.append(f"Gateway interrupted by operator after {cycle_index} cycle(s).")
    else:
        lines.append("")
        lines.append(f"Gateway exited cleanly after {cycle_index} cycle(s).")
    return GatewayStartReport(ok=ok, text="\n".join(lines))


def _gateway_repair_hints(
    *,
    config: dict[str, Any],
    auth_report: Any,
    provider_runtime_ok: bool,
    provider_execution_ok: bool,
    oauth_maintenance_ok: bool,
    provider_execution_detail: str,
) -> list[str]:
    hints: list[str] = []
    for hint in _channel_repair_hints(config):
        if hint not in hints:
            hints.append(hint)
    if not oauth_maintenance_ok:
        hints.append("spark-intelligence jobs tick")
    if not provider_runtime_ok:
        runtime_hint = _primary_runtime_repair_hint(auth_report)
        if runtime_hint not in hints:
            hints.append(runtime_hint)
    if not provider_execution_ok:
        execution_hint = _provider_execution_repair_hint(provider_execution_detail)
        if execution_hint not in hints:
            hints.append(execution_hint)
    return hints


def _channel_repair_hints(config: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    channel_records = config.get("channels", {}).get("records", {}) or {}
    for channel_id, record in sorted(channel_records.items()):
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "enabled")
        if status in {"paused", "disabled"}:
            hints.append(f"spark-intelligence operator set-channel {channel_id} enabled")
    return hints


def _primary_runtime_repair_hint(auth_report: Any) -> str:
    providers = getattr(auth_report, "providers", [])
    for provider in providers:
        if provider.auth_method == "oauth":
            if provider.status in {"expired", "refresh_error"} or provider.last_refresh_error:
                return f"spark-intelligence auth refresh {provider.provider_id}"
            if provider.status == "expiring_soon":
                return "spark-intelligence jobs tick"
            if provider.status in {"revoked", "pending_oauth"} or not provider.secret_present:
                return f"spark-intelligence auth login {provider.provider_id} --listen"
        elif not provider.secret_present:
            return f"spark-intelligence auth connect {provider.provider_id} --api-key <key>"
    return "spark-intelligence auth status"


def _provider_execution_repair_hint(provider_execution_detail: str) -> str:
    if "researcher_" in provider_execution_detail:
        return "spark-intelligence researcher status"
    return "spark-intelligence operator security"


def gateway_simulate_telegram_update(
    config_manager: ConfigManager,
    state_db: StateDB,
    update_path: Path,
    *,
    as_json: bool = False,
) -> str:
    payload: dict[str, Any] = json.loads(update_path.read_text(encoding="utf-8-sig"))
    result = simulate_telegram_update(
        config_manager=config_manager,
        state_db=state_db,
        update_payload=payload,
    )
    return result.to_json() if as_json else result.to_text()


def gateway_simulate_discord_message(
    config_manager: ConfigManager,
    state_db: StateDB,
    payload_path: Path,
    *,
    as_json: bool = False,
) -> str:
    payload: dict[str, Any] = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    result = simulate_discord_message(
        config_manager=config_manager,
        state_db=state_db,
        payload=payload,
    )
    return result.to_json() if as_json else result.to_text()


def gateway_simulate_whatsapp_message(
    config_manager: ConfigManager,
    state_db: StateDB,
    payload_path: Path,
    *,
    as_json: bool = False,
) -> str:
    payload: dict[str, Any] = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    result = simulate_whatsapp_message(
        config_manager=config_manager,
        state_db=state_db,
        payload=payload,
    )
    return result.to_json() if as_json else result.to_text()


def gateway_trace_view(
    config_manager: ConfigManager,
    *,
    limit: int = 20,
    channel_id: str | None = None,
    event: str | None = None,
    user: str | None = None,
    decision: str | None = None,
    as_json: bool = False,
) -> str:
    traces = _filter_log_records(
        read_gateway_traces(config_manager, limit=limit),
        channel_id=channel_id,
        event=event,
        user=user,
        decision=decision,
    )
    if as_json:
        return json.dumps(traces, indent=2)
    if not traces:
        return "No gateway traces recorded."
    lines = ["Gateway traces:"]
    for trace in traces:
        reason_suffix = f" reason={trace.get('reason')}" if trace.get("reason") else ""
        lines.append(
            f"- at={trace.get('recorded_at')} channel={_record_channel_id(trace)} "
            f"event={trace.get('event')} update_id={trace.get('update_id')} "
            f"user={_record_user_ref(trace)} decision={trace.get('decision', 'n/a')} "
            f"trace_ref={trace.get('trace_ref', 'n/a')} mode={trace.get('bridge_mode', 'n/a')} "
            f"route={trace.get('routing_decision', 'n/a')} "
            f"chip={trace.get('active_chip_key', 'none')} "
            f"delivery_ok={trace.get('delivery_ok', 'n/a')} "
            f"guardrails={','.join(trace.get('guardrail_actions', [])) if trace.get('guardrail_actions') else 'none'}"
            f"{reason_suffix}"
        )
    return "\n".join(lines)


def gateway_outbound_view(
    config_manager: ConfigManager,
    *,
    limit: int = 20,
    channel_id: str | None = None,
    event: str | None = None,
    user: str | None = None,
    decision: str | None = None,
    delivery: str | None = None,
    contains: str | None = None,
    as_json: bool = False,
) -> str:
    records = _filter_log_records(
        read_outbound_audit(config_manager, limit=limit),
        channel_id=channel_id,
        event=event,
        user=user,
        decision=decision,
        delivery=delivery,
        contains=contains,
    )
    if as_json:
        return json.dumps(records, indent=2)
    if not records:
        return "No gateway outbound audit records."
    lines = ["Gateway outbound audit:"]
    for record in records:
        lines.append(
            f"- at={record.get('recorded_at')} channel={_record_channel_id(record)} "
            f"event={record.get('event')} update_id={record.get('update_id')} "
            f"user={_record_user_ref(record)} ok={record.get('delivery_ok')} "
            f"mode={record.get('bridge_mode', 'n/a')} "
            f"route={record.get('routing_decision', 'n/a')} "
            f"chip={record.get('active_chip_key', 'none')} "
            f"guardrails={','.join(record.get('guardrail_actions', [])) if record.get('guardrail_actions') else 'none'} "
            f"preview={record.get('response_preview', '')}"
        )
    return "\n".join(lines)


def _filter_log_records(
    records: list[dict[str, Any]],
    *,
    channel_id: str | None = None,
    event: str | None = None,
    user: str | None = None,
    decision: str | None = None,
    delivery: str | None = None,
    contains: str | None = None,
) -> list[dict[str, Any]]:
    filtered = records
    if channel_id:
        filtered = [record for record in filtered if _record_channel_id(record) == channel_id]
    if event:
        filtered = [record for record in filtered if str(record.get("event") or "") == event]
    if user:
        filtered = [record for record in filtered if _record_user_ref(record) == user]
    if decision:
        filtered = [record for record in filtered if str(record.get("decision") or "") == decision]
    if delivery:
        delivery_ok = delivery == "ok"
        filtered = [record for record in filtered if bool(record.get("delivery_ok")) is delivery_ok]
    if contains:
        needle = contains.lower()
        filtered = [
            record
            for record in filtered
            if needle in str(record.get("response_preview") or record.get("failure_message") or "").lower()
        ]
    return filtered


def _record_channel_id(record: dict[str, Any]) -> str:
    channel_id = record.get("channel_id")
    if channel_id:
        return str(channel_id)
    event = str(record.get("event") or "")
    if event.startswith("telegram_"):
        return "telegram"
    if event.startswith("discord_"):
        return "discord"
    if event.startswith("whatsapp_"):
        return "whatsapp"
    return "unknown"


def _record_user_ref(record: dict[str, Any]) -> str:
    for key in ("telegram_user_id", "external_user_id", "user_id", "chat_id"):
        value = record.get(key)
        if value not in {None, ""}:
            return str(value)
    return "unknown"


def _classify_telegram_failure(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "network_error"
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        if "timed out" in message:
            return "timeout"
        if "unauthorized" in message or "token" in message:
            return "auth_error"
        return "runtime_error"
    return "unexpected_error"


def _telegram_failure_message(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return str(exc)


def _record_telegram_poll_failure(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    failure_type: str,
    message: str,
) -> int:
    health = read_telegram_runtime_health(state_db)
    backoff_seconds = min(10, max(1, 2 ** min(health.consecutive_failures, 3)))
    record_telegram_poll_failure(
        state_db=state_db,
        failure_type=failure_type,
        message=message,
        backoff_seconds=backoff_seconds,
    )
    append_gateway_trace(
        config_manager,
        {
            "event": "telegram_poll_failure",
            "channel_id": "telegram",
            "failure_type": failure_type,
            "failure_message": message,
            "backoff_seconds": backoff_seconds,
            "consecutive_failures": health.consecutive_failures + 1,
        },
    )
    return backoff_seconds


def _provider_status_line(
    provider_id: str,
    auth_method: str,
    status: str,
    *,
    researcher_available: bool,
    researcher_mode: str,
) -> str:
    try:
        transport = get_provider_spec(provider_id).execution_transport
    except ValueError:
        transport = "unknown"
    if transport == "external_cli_wrapper":
        exec_ready = "yes" if researcher_available else "no"
        dependency = "researcher_bridge" if researcher_available else f"researcher_{researcher_mode}"
    else:
        exec_ready = "yes"
        dependency = "direct_http"
    return (
        f"provider={provider_id} method={auth_method} status={status} "
        f"transport={transport} exec_ready={exec_ready} dependency={dependency}"
    )
