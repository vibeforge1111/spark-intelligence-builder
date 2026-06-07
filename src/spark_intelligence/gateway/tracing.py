from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
GATEWAY_JSONL_MAX_BYTES = 25 * 1024 * 1024
GATEWAY_JSONL_BACKUPS = 3


@dataclass(frozen=True)
class GatewayLogPruneResult:
    cutoff: str
    deleted_counts: dict[str, int]
    kept_counts: dict[str, int]

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted_counts.values())

    def to_payload(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff,
            "deleted_counts": self.deleted_counts,
            "kept_counts": self.kept_counts,
            "total_deleted": self.total_deleted,
        }


def trace_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-trace.jsonl"


def outbound_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-outbound.jsonl"


def append_gateway_trace(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = trace_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_jsonl_if_oversized(path, max_bytes=GATEWAY_JSONL_MAX_BYTES, backups=GATEWAY_JSONL_BACKUPS)
    payload = redact_trace_payload({"recorded_at": _utc_now_iso(), **record})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def append_outbound_audit(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = outbound_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_jsonl_if_oversized(path, max_bytes=GATEWAY_JSONL_MAX_BYTES, backups=GATEWAY_JSONL_BACKUPS)
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
            traces.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
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
            records.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return records


def prune_gateway_logs(config_manager: ConfigManager, *, older_than: str | datetime) -> GatewayLogPruneResult:
    cutoff = _normalize_cutoff_datetime(older_than)
    deleted_counts: dict[str, int] = {}
    kept_counts: dict[str, int] = {}
    for label, path in (
        ("gateway_trace", trace_log_path(config_manager)),
        ("gateway_outbound", outbound_log_path(config_manager)),
    ):
        deleted, kept = _prune_jsonl_path(path, cutoff)
        deleted_counts[label] = deleted
        kept_counts[label] = kept
    return GatewayLogPruneResult(
        cutoff=cutoff.isoformat(timespec="seconds"),
        deleted_counts=deleted_counts,
        kept_counts=kept_counts,
    )


def gateway_log_report(config_manager: ConfigManager, *, older_than: str | datetime | None = None) -> dict[str, Any]:
    cutoff = _normalize_cutoff_datetime(older_than) if older_than is not None else None
    logs = {
        label: _jsonl_path_report(path, cutoff)
        for label, path in (
            ("gateway_trace", trace_log_path(config_manager)),
            ("gateway_outbound", outbound_log_path(config_manager)),
        )
    }
    return {
        "cutoff": cutoff.isoformat(timespec="seconds") if cutoff is not None else None,
        "logs": logs,
        "total_bytes": sum(int(item["bytes"]) for item in logs.values()),
        "total_records": sum(int(item["records"]) for item in logs.values()),
        "total_old_records": sum(int(item["old_records"]) for item in logs.values()),
        "total_invalid_records": sum(int(item["invalid_records"]) for item in logs.values()),
    }


def rotate_gateway_logs_if_oversized(
    config_manager: ConfigManager,
    *,
    max_bytes: int = GATEWAY_JSONL_MAX_BYTES,
    backups: int = GATEWAY_JSONL_BACKUPS,
) -> dict[str, bool]:
    return {
        "gateway_trace": _rotate_jsonl_if_oversized(trace_log_path(config_manager), max_bytes=max_bytes, backups=backups),
        "gateway_outbound": _rotate_jsonl_if_oversized(outbound_log_path(config_manager), max_bytes=max_bytes, backups=backups),
    }


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


def _normalize_cutoff_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        cutoff = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("older_than is required")
        cutoff = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return cutoff.astimezone(timezone.utc)


def _parse_recorded_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jsonl_path_report(path: Path, cutoff: datetime | None) -> dict[str, Any]:
    exists = path.exists()
    size_bytes = 0
    records = 0
    old_records = 0
    invalid_records = 0
    if exists:
        try:
            size_bytes = int(path.stat().st_size)
        except OSError:
            size_bytes = 0
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    invalid_records += 1
                    continue
                if not isinstance(payload, dict):
                    invalid_records += 1
                    continue
                records += 1
                recorded_at = _parse_recorded_at(payload.get("recorded_at"))
                if cutoff is not None and recorded_at is not None and recorded_at < cutoff:
                    old_records += 1
    return {
        "path": str(path),
        "exists": exists,
        "bytes": size_bytes,
        "records": records,
        "old_records": old_records,
        "invalid_records": invalid_records,
    }


def _prune_jsonl_path(path: Path, cutoff: datetime) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    tmp_path = path.with_name(f"{path.name}.tmp")
    deleted = 0
    kept = 0
    with path.open("r", encoding="utf-8", errors="replace") as source, tmp_path.open("w", encoding="utf-8") as target:
        for line in source:
            keep_line = True
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                recorded_at = _parse_recorded_at(payload.get("recorded_at"))
                if recorded_at is not None and recorded_at < cutoff:
                    keep_line = False
            if keep_line:
                target.write(line)
                kept += 1
            else:
                deleted += 1
    if deleted:
        tmp_path.replace(path)
    else:
        tmp_path.unlink(missing_ok=True)
    return deleted, kept


def _rotate_jsonl_if_oversized(path: Path, *, max_bytes: int, backups: int) -> bool:
    if max_bytes <= 0 or backups <= 0 or not path.exists():
        return False
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    for index in range(backups, 0, -1):
        current = path.with_name(f"{path.name}.{index}")
        if index == backups:
            current.unlink(missing_ok=True)
            continue
        next_path = path.with_name(f"{path.name}.{index + 1}")
        if current.exists():
            current.replace(next_path)
    path.replace(path.with_name(f"{path.name}.1"))
    return True


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
