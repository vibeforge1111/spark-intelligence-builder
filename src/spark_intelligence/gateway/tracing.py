from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


def trace_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-trace.jsonl"


def outbound_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-outbound.jsonl"


def append_gateway_trace(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = trace_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"recorded_at": _utc_now_iso(), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def append_outbound_audit(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = outbound_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"recorded_at": _utc_now_iso(), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def read_gateway_traces(config_manager: ConfigManager, *, limit: int = 20) -> list[dict[str, Any]]:
    path = trace_log_path(config_manager)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = lines[-limit:] if limit > 0 else lines
    traces: list[dict[str, Any]] = []
    for line in selected:
        if not line.strip():
            continue
        traces.append(json.loads(line))
    return traces


def read_outbound_audit(config_manager: ConfigManager, *, limit: int = 20) -> list[dict[str, Any]]:
    path = outbound_log_path(config_manager)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = lines[-limit:] if limit > 0 else lines
    records: list[dict[str, Any]] = []
    for line in selected:
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
