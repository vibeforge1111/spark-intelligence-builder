from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.adapters.telegram.runtime import build_telegram_runtime_summary, simulate_telegram_update
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import run_doctor
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


def gateway_start(config_manager: ConfigManager, state_db: StateDB) -> str:
    status = gateway_status(config_manager, state_db)
    lines = ["Spark Intelligence gateway start"]
    lines.append(status.to_text())
    lines.append("")
    lines.append("This is the Phase 0 foreground runtime stub.")
    lines.append("Telegram normalization and DM authorization routing are wired for simulation.")
    lines.append("Next implementation steps: real long-poll loop and Spark Researcher bridge.")
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
