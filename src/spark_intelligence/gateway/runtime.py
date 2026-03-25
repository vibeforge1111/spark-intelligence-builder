from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.adapters.telegram.client import TelegramBotApiClient
from spark_intelligence.adapters.telegram.runtime import (
    build_telegram_runtime_summary,
    poll_telegram_updates_once,
    simulate_telegram_update,
)
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.gateway.tracing import read_gateway_traces
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
    channel_records = list(config.get("channels", {}).get("records", {}).keys())
    doctor_report = run_doctor(config_manager, state_db)
    telegram_summary = build_telegram_runtime_summary(config_manager, state_db)
    ready = bool(provider_records) and bool(channel_records) and doctor_report.ok
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

    auth_ref = telegram_record.get("auth_ref")
    env_map = config_manager.read_env_map()
    token = env_map.get(auth_ref) if auth_ref else None
    if not token:
        lines.append("Telegram auth ref is missing or unresolved. Gateway did not start polling.")
        return "\n".join(lines)

    client = TelegramBotApiClient(token=token)
    me = client.get_me().get("result", {})
    lines.append(f"Telegram bot authenticated: @{me.get('username', 'unknown')}")

    cycles = 1 if once else (max_cycles or 1)
    for cycle in range(cycles):
        poll_result = poll_telegram_updates_once(
            config_manager=config_manager,
            state_db=state_db,
            client=client,
            timeout_seconds=poll_timeout_seconds,
        )
        lines.append(f"Cycle {cycle + 1}:")
        lines.extend(f"  {line}" for line in poll_result.to_text().splitlines())
        if once:
            break
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
            f"- event={trace.get('event')} update_id={trace.get('update_id')} "
            f"user={trace.get('telegram_user_id')} trace_ref={trace.get('trace_ref', 'n/a')} "
            f"mode={trace.get('bridge_mode', 'n/a')}"
        )
    return "\n".join(lines)
