from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.runtime import (
    build_telegram_runtime_summary,
    poll_telegram_updates_once,
    simulate_telegram_update,
)
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.gateway.tracing import outbound_log_path, read_gateway_traces, read_outbound_audit, trace_log_path
from spark_intelligence.state.db import StateDB


@dataclass
class GatewayStatus:
    ready: bool
    configured_channels: list[str]
    configured_providers: list[str]
    doctor_ok: bool
    adapter_lines: list[str]

    def to_json(self) -> str:
        return json.dumps(
            {
                "ready": self.ready,
                "configured_channels": self.configured_channels,
                "configured_providers": self.configured_providers,
                "doctor_ok": self.doctor_ok,
                "adapter_lines": self.adapter_lines,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Gateway ready: {'yes' if self.ready else 'no'}"]
        lines.append(f"- providers: {', '.join(self.configured_providers) if self.configured_providers else 'none'}")
        lines.append(f"- channels: {', '.join(self.configured_channels) if self.configured_channels else 'none'}")
        lines.append(f"- doctor: {'ok' if self.doctor_ok else 'degraded'}")
        lines.extend(self.adapter_lines)
        return "\n".join(lines)


def gateway_status(config_manager: ConfigManager, state_db: StateDB) -> GatewayStatus:
    config = config_manager.load()
    provider_records = list(config.get("providers", {}).get("records", {}).keys())
    raw_channel_records = config.get("channels", {}).get("records", {})
    channel_records = list(raw_channel_records.keys())
    active_channel_records = [
        channel_id
        for channel_id, record in raw_channel_records.items()
        if isinstance(record, dict) and str(record.get("status") or "enabled") in {"enabled", "configured"}
    ]
    doctor_report = run_doctor(config_manager, state_db)
    telegram_summary = build_telegram_runtime_summary(config_manager, state_db)
    ready = bool(provider_records) and bool(active_channel_records) and doctor_report.ok
    return GatewayStatus(
        ready=ready,
        configured_channels=channel_records,
        configured_providers=provider_records,
        doctor_ok=doctor_report.ok,
        adapter_lines=[telegram_summary.to_line()],
    )


def gateway_start(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    once: bool = False,
    continuous: bool = False,
    max_cycles: int | None = None,
    poll_timeout_seconds: int = 5,
) -> str:
    status = gateway_status(config_manager, state_db)
    lines = ["Spark Intelligence gateway start"]
    lines.append(status.to_text())
    lines.append("")
    config = config_manager.load()
    telegram_record = config.get("channels", {}).get("records", {}).get("telegram")
    if not telegram_record:
        lines.append("No Telegram adapter configured. Gateway is idle.")
        return "\n".join(lines)
    telegram_summary = build_telegram_runtime_summary(config_manager, state_db)
    telegram_status = str(telegram_summary.status or "enabled")
    if telegram_status == "disabled":
        lines.append("Telegram adapter is disabled by operator. Gateway did not start polling.")
        return "\n".join(lines)
    if telegram_status == "paused":
        lines.append("Telegram adapter is paused by operator. Gateway did not start polling.")
        return "\n".join(lines)

    auth_ref = telegram_record.get("auth_ref")
    env_map = config_manager.read_env_map()
    token = env_map.get(auth_ref) if auth_ref else None
    if not token:
        lines.append("Telegram auth ref is missing or unresolved. Gateway did not start polling.")
        return "\n".join(lines)

    client = TelegramBotApiClient(token=token)
    try:
        me = client.get_me().get("result", {})
    except urllib.error.HTTPError as exc:
        lines.append(f"Telegram auth check failed with HTTP {exc.code}. Gateway did not start polling.")
        return "\n".join(lines)
    except urllib.error.URLError as exc:
        lines.append(f"Telegram auth check failed: {exc.reason}. Gateway did not start polling.")
        return "\n".join(lines)
    lines.append(f"Telegram bot authenticated: @{me.get('username', 'unknown')}")
    lines.append(f"Gateway trace log: {trace_log_path(config_manager)}")
    lines.append(f"Gateway outbound audit log: {outbound_log_path(config_manager)}")

    cycle_limit = 1 if once else max_cycles
    cycle_index = 0
    try:
        while True:
            if cycle_limit is not None and cycle_index >= cycle_limit:
                break
            poll_result = poll_telegram_updates_once(
                config_manager=config_manager,
                state_db=state_db,
                client=client,
                timeout_seconds=poll_timeout_seconds,
            )
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
    return "\n".join(lines)


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


def gateway_trace_view(config_manager: ConfigManager, *, limit: int = 20, as_json: bool = False) -> str:
    traces = read_gateway_traces(config_manager, limit=limit)
    if as_json:
        return json.dumps(traces, indent=2)
    if not traces:
        return "No gateway traces recorded."
    lines = ["Gateway traces:"]
    for trace in traces:
        lines.append(
            f"- at={trace.get('recorded_at')} event={trace.get('event')} update_id={trace.get('update_id')} "
            f"user={trace.get('telegram_user_id')} decision={trace.get('decision', 'n/a')} "
            f"trace_ref={trace.get('trace_ref', 'n/a')} mode={trace.get('bridge_mode', 'n/a')} "
            f"delivery_ok={trace.get('delivery_ok', 'n/a')} "
            f"guardrails={','.join(trace.get('guardrail_actions', [])) if trace.get('guardrail_actions') else 'none'}"
        )
    return "\n".join(lines)


def gateway_outbound_view(config_manager: ConfigManager, *, limit: int = 20, as_json: bool = False) -> str:
    records = read_outbound_audit(config_manager, limit=limit)
    if as_json:
        return json.dumps(records, indent=2)
    if not records:
        return "No gateway outbound audit records."
    lines = ["Gateway outbound audit:"]
    for record in records:
        lines.append(
            f"- at={record.get('recorded_at')} event={record.get('event')} update_id={record.get('update_id')} "
            f"user={record.get('telegram_user_id')} ok={record.get('delivery_ok')} "
            f"mode={record.get('bridge_mode', 'n/a')} "
            f"guardrails={','.join(record.get('guardrail_actions', [])) if record.get('guardrail_actions') else 'none'} "
            f"preview={record.get('response_preview', '')}"
        )
    return "\n".join(lines)
