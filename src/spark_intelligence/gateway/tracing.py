from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager

SENSITIVE_TEXT_PATTERNS = [
    re.compile(r"\b(?:bot)?\d{7,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\b(?:sk|sk-proj|sk-ant|gho|ghp|glpat|xoxb|xoxp|AIza)[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"(?i)(api[_-]?key|bot[_-]?token|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{16,})"),
]
SENSITIVE_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|bot[_-]?token|token|secret|password|authorization)")


def trace_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-trace.jsonl"


def outbound_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-outbound.jsonl"


def append_gateway_trace(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = trace_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_trace_payload({"recorded_at": _utc_now_iso(), **record})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def append_outbound_audit(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = outbound_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_trace_payload({"recorded_at": _utc_now_iso(), **record})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def read_gateway_traces(config_manager: ConfigManager, *, limit: int = 20) -> list[dict[str, Any]]:
    path = trace_log_path(config_manager)
    if not path.exists():
        return []
    traces: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                traces.append(record)
        except json.JSONDecodeError:
            continue
    return traces


def read_outbound_audit(config_manager: ConfigManager, *, limit: int = 20) -> list[dict[str, Any]]:
    path = outbound_log_path(config_manager)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except json.JSONDecodeError:
            continue
    return records


def _tail_lines(path: Path, n: int) -> list[str]:
    """Read the last *n* lines from a file without loading it entirely into memory."""
    if n <= 0:
        return []
    buf_size = 8192
    lines: list[str] = []
    with path.open("rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        block_end = remaining
        blocks: list[bytes] = []
        while remaining > 0 and len(lines) <= n:
            read_size = min(buf_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            blocks.append(f.read(read_size))
            lines = b"".join(reversed(blocks)).split(b"\n")
        if lines and lines[-1] == b"":
            lines = lines[:-1]
        return [l.decode("utf-8", errors="replace") for l in lines[-n:]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_trace_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY_PATTERN.search(key_text) and item not in (None, "", [], {}):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_trace_payload(item)
        return result
    if isinstance(value, list):
        return [redact_trace_payload(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in SENSITIVE_TEXT_PATTERNS:
            if pattern.pattern.startswith("(?i)(api"):
                redacted = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted)
            elif pattern.pattern.startswith("(?i)(bearer"):
                redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value
