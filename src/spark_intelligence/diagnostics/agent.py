from __future__ import annotations

import hashlib
import json
import re
import socket
import urllib.error
import urllib.request
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from spark_intelligence.config.loader import ConfigManager


DEFAULT_MAX_LINES_PER_FILE = 2000
DEFAULT_RECURRING_THRESHOLD = 2
FAILURE_LEVELS = {"error", "exception", "critical", "fatal", "failed", "failure"}
LOG_GLOBS = ("*.log", "*.jsonl", "*.out", "*.err")
TIMESTAMP_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


@dataclass(frozen=True)
class LogSource:
    path: Path
    subsystem: str
    source_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "subsystem": self.subsystem,
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True)
class LogEntry:
    source_path: Path
    line_number: int
    subsystem: str
    source_kind: str
    raw: str
    timestamp: str | None = None
    level: str | None = None
    event: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "line_number": self.line_number,
            "subsystem": self.subsystem,
            "source_kind": self.source_kind,
            "timestamp": self.timestamp,
            "level": self.level,
            "event": self.event,
            "message": self.message,
            "raw": self.raw,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ClassifiedFailure:
    failure_class: str
    severity: str
    signature: str
    summary: str


@dataclass(frozen=True)
class ServiceCheck:
    service: str
    status: str
    target: str
    detail: str
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "status": self.status,
            "target": self.target,
            "detail": self.detail,
            "required": self.required,
        }


@dataclass
class DiagnosticFinding:
    signature: str
    failure_class: str
    severity: str
    subsystem: str
    count: int = 0
    sources: Counter[str] = field(default_factory=Counter)
    first_seen: str | None = None
    last_seen: str | None = None
    examples: list[LogEntry] = field(default_factory=list)

    @property
    def recurring(self) -> bool:
        return self.count >= DEFAULT_RECURRING_THRESHOLD

    def is_recurring(self, threshold: int = DEFAULT_RECURRING_THRESHOLD) -> bool:
        return self.count >= threshold

    def add(self, entry: LogEntry) -> None:
        self.count += 1
        self.sources[str(entry.source_path)] += 1
        if entry.timestamp:
            if self.first_seen is None or entry.timestamp < self.first_seen:
                self.first_seen = entry.timestamp
            if self.last_seen is None or entry.timestamp > self.last_seen:
                self.last_seen = entry.timestamp
        if len(self.examples) < 3:
            self.examples.append(entry)

    def to_dict(self, *, recurring_threshold: int = DEFAULT_RECURRING_THRESHOLD) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "failure_class": self.failure_class,
            "severity": self.severity,
            "subsystem": self.subsystem,
            "count": self.count,
            "recurring": self.is_recurring(recurring_threshold),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sources": dict(self.sources),
            "examples": [entry.to_dict() for entry in self.examples],
        }


@dataclass(frozen=True)
class DiagnosticReport:
    generated_at: str
    home: str
    sources: list[LogSource]
    service_checks: list[ServiceCheck]
    scanned_line_count: int
    failure_line_count: int
    findings: list[DiagnosticFinding]
    counts_by_subsystem: dict[str, int]
    counts_by_failure_class: dict[str, int]
    recurring_threshold: int = DEFAULT_RECURRING_THRESHOLD
    markdown_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "home": self.home,
            "sources": [source.to_dict() for source in self.sources],
            "service_checks": [check.to_dict() for check in self.service_checks],
            "scanned_line_count": self.scanned_line_count,
            "failure_line_count": self.failure_line_count,
            "counts_by_subsystem": self.counts_by_subsystem,
            "counts_by_failure_class": self.counts_by_failure_class,
            "recurring_threshold": self.recurring_threshold,
            "markdown_path": self.markdown_path,
            "findings": [
                finding.to_dict(recurring_threshold=self.recurring_threshold)
                for finding in self.findings
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        lines = ["Spark diagnostics"]
        lines.append(f"- generated_at: {self.generated_at}")
        lines.append(f"- sources: {len(self.sources)}")
        lines.append(f"- service_checks: {len(self.service_checks)}")
        lines.append(f"- scanned_lines: {self.scanned_line_count}")
        lines.append(f"- failure_lines: {self.failure_line_count}")
        lines.append(f"- findings: {len(self.findings)}")
        recurring = [finding for finding in self.findings if finding.is_recurring(self.recurring_threshold)]
        lines.append(f"- recurring: {len(recurring)}")
        if self.markdown_path:
            lines.append(f"- markdown: {self.markdown_path}")
        for finding in self.findings[:10]:
            marker = " recurring" if finding.is_recurring(self.recurring_threshold) else ""
            lines.append(
                f"  [{finding.severity}] {finding.subsystem}/{finding.failure_class} x{finding.count}{marker}: "
                f"{finding.signature}"
            )
        return "\n".join(lines)


def build_diagnostic_report(
    config_manager: ConfigManager,
    *,
    logs_root: Path | None = None,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
    recurring_threshold: int = DEFAULT_RECURRING_THRESHOLD,
    write_markdown: bool = True,
    output_dir: Path | None = None,
) -> DiagnosticReport:
    generated_at = _utc_now_iso()
    sources = discover_log_sources(config_manager, logs_root=logs_root)
    service_checks = discover_service_checks(config_manager)
    findings_by_key: dict[tuple[str, str, str], DiagnosticFinding] = {}
    counts_by_subsystem: Counter[str] = Counter()
    counts_by_failure_class: Counter[str] = Counter()
    scanned_line_count = 0
    failure_line_count = 0

    for source in sources:
        for entry in iter_log_entries(source, max_lines_per_file=max_lines_per_file):
            scanned_line_count += 1
            classified = classify_log_entry(entry)
            if classified is None:
                continue
            failure_line_count += 1
            counts_by_subsystem[entry.subsystem] += 1
            counts_by_failure_class[classified.failure_class] += 1
            key = (entry.subsystem, classified.failure_class, classified.signature)
            finding = findings_by_key.get(key)
            if finding is None:
                finding = DiagnosticFinding(
                    signature=classified.signature,
                    failure_class=classified.failure_class,
                    severity=classified.severity,
                    subsystem=entry.subsystem,
                )
                findings_by_key[key] = finding
            finding.add(entry)

    findings = sorted(
        findings_by_key.values(),
        key=lambda item: (
            0 if item.count >= recurring_threshold else 1,
            _severity_sort_key(item.severity),
            -item.count,
            item.subsystem,
            item.failure_class,
            item.signature,
        ),
    )
    report = DiagnosticReport(
        generated_at=generated_at,
        home=str(config_manager.paths.home),
        sources=sources,
        service_checks=service_checks,
        scanned_line_count=scanned_line_count,
        failure_line_count=failure_line_count,
        findings=findings,
        counts_by_subsystem=dict(sorted(counts_by_subsystem.items())),
        counts_by_failure_class=dict(sorted(counts_by_failure_class.items())),
        recurring_threshold=recurring_threshold,
    )
    if not write_markdown:
        return report
    markdown_path = write_diagnostic_markdown(report, output_dir=output_dir or config_manager.paths.home / "diagnostics")
    return DiagnosticReport(
        generated_at=report.generated_at,
        home=report.home,
        sources=report.sources,
        service_checks=report.service_checks,
        scanned_line_count=report.scanned_line_count,
        failure_line_count=report.failure_line_count,
        findings=report.findings,
        counts_by_subsystem=report.counts_by_subsystem,
        counts_by_failure_class=report.counts_by_failure_class,
        recurring_threshold=report.recurring_threshold,
        markdown_path=str(markdown_path),
    )


def discover_service_checks(config_manager: ConfigManager) -> list[ServiceCheck]:
    checks: list[ServiceCheck] = []
    paths = config_manager.paths
    checks.append(_path_check("spark_intelligence_home", paths.home, required=True))
    checks.append(_path_check("spark_intelligence_state_db", paths.state_db, required=True))
    checks.append(_path_check("spark_intelligence_logs", paths.logs_dir, required=True))

    builder_root = _current_builder_source_root()
    checks.append(_path_check("spark_intelligence_builder_source", builder_root, required=True, marker="pyproject.toml"))

    telegram_root = _discover_module_root(config_manager, "spark-telegram-bot")
    checks.append(_module_root_check("spark_telegram_bot_source", telegram_root, marker="package.json"))
    checks.append(_tcp_check("spark_telegram_bot_spark_agi", "127.0.0.1", 8789))
    checks.append(_tcp_check("spark_telegram_bot_testerthebester", "127.0.0.1", 8788))

    spawner_url = _config_string(config_manager.get_path("spark.spawner.api_url")) or "http://127.0.0.1:5173/api/providers"
    checks.append(_http_check("spawner_ui_api", spawner_url))

    memory_root = _discover_module_root(config_manager, "domain-chip-memory")
    checks.append(_module_root_check("domain_chip_memory_source", memory_root, marker="pyproject.toml"))

    researcher_root = ConfigManager.normalize_runtime_path(config_manager.get_path("spark.researcher.runtime_root"))
    if researcher_root is None:
        researcher_root = _discover_module_root(config_manager, "spark-researcher")
    checks.append(_module_root_check("spark_researcher_source", researcher_root, marker="pyproject.toml"))
    if researcher_root is not None:
        checks.append(_path_check("spark_researcher_traces", researcher_root / "artifacts" / "traces"))
    else:
        checks.append(ServiceCheck(
            service="spark_researcher_traces",
            status="missing",
            target="spark-researcher/artifacts/traces",
            detail="spark-researcher source root was not discovered",
        ))

    return checks


def discover_log_sources(config_manager: ConfigManager, *, logs_root: Path | None = None) -> list[LogSource]:
    roots: list[Path] = []
    explicit_root = logs_root.expanduser() if logs_root else None
    if explicit_root is not None:
        roots.append(explicit_root)
    roots.append(config_manager.paths.logs_dir)

    researcher_root = ConfigManager.normalize_runtime_path(config_manager.get_path("spark.researcher.runtime_root"))
    if researcher_root is not None:
        roots.extend([researcher_root / "logs", researcher_root / "artifacts"])
    memory_root = _discover_module_root(config_manager, "domain-chip-memory")
    if memory_root is not None:
        roots.extend([memory_root / "logs", memory_root / "artifacts"])

    sources: dict[Path, LogSource] = {}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            sources[root.resolve()] = LogSource(
                path=root.resolve(),
                subsystem=infer_subsystem(root),
                source_kind=_source_kind(root),
            )
            continue
        for pattern in LOG_GLOBS:
            for path in root.rglob(pattern):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                sources[resolved] = LogSource(
                    path=resolved,
                    subsystem=infer_subsystem(path),
                    source_kind=_source_kind(path),
                )
    return sorted(sources.values(), key=lambda source: str(source.path).lower())


def iter_log_entries(source: LogSource, *, max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE) -> Iterable[LogEntry]:
    lines = _tail_lines(source.path, max_lines=max_lines_per_file)
    first_line_number = max(1, _line_count(source.path) - len(lines) + 1)
    for offset, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        yield parse_log_line(
            source=source,
            raw=line,
            line_number=first_line_number + offset,
        )


def parse_log_line(*, source: LogSource, raw: str, line_number: int) -> LogEntry:
    payload = _parse_json_object(raw)
    if payload is not None:
        message = _first_present_string(
            payload,
            "message",
            "summary",
            "detail",
            "error",
            "error_message",
            "stderr",
            "stdout",
            "reason",
        )
        if isinstance(payload.get("error"), dict):
            message = _first_present_string(payload["error"], "message", "code") or message
        level = _first_present_string(payload, "level", "severity", "status")
        event = _first_present_string(payload, "event", "event_type", "type", "kind")
        timestamp = _first_present_string(payload, "timestamp", "created_at", "recorded_at", "time", "ts")
        subsystem = _infer_subsystem_from_payload(payload) or source.subsystem
        return LogEntry(
            source_path=source.path,
            line_number=line_number,
            subsystem=subsystem,
            source_kind=source.source_kind,
            raw=raw,
            timestamp=timestamp,
            level=_normalize_level(level),
            event=event,
            message=message,
            payload=payload,
        )

    timestamp_match = TIMESTAMP_RE.search(raw)
    level_match = re.search(r"\b(DEBUG|INFO|WARN|WARNING|ERROR|EXCEPTION|CRITICAL|FATAL|FAILED)\b", raw, re.IGNORECASE)
    return LogEntry(
        source_path=source.path,
        line_number=line_number,
        subsystem=source.subsystem,
        source_kind=source.source_kind,
        raw=raw,
        timestamp=timestamp_match.group("ts") if timestamp_match else None,
        level=_normalize_level(level_match.group(1) if level_match else None),
        message=raw,
    )


def classify_log_entry(entry: LogEntry) -> ClassifiedFailure | None:
    text = _entry_text(entry)
    lowered = text.lower()
    if not _looks_like_failure(entry, lowered):
        return None

    failure_class = "unknown_failure"
    severity = "medium"
    rules = (
        ("secret_or_policy_block", "high", ("secret", "credential", "policy_gate", "policy gate", "quarantine", "blocked")),
        ("auth_or_permission", "high", ("auth", "unauthorized", "forbidden", "permission", "access denied", "token", "oauth")),
        ("network_or_provider", "high", ("connection", "connecttimeout", "readtimeout", "timeout", "http", "502", "503", "504", "network", "provider")),
        ("memory_failure", "high", ("memory_", "memory ", "domain_chip_memory", "sqlite", "database is locked", "db locked")),
        ("researcher_bridge_failure", "high", ("researcher", "advisory", "provider_resolution", "bridge_error")),
        ("builder_runtime_failure", "high", ("builder", "gateway", "telegram", "discord", "whatsapp", "delivery", "dispatch")),
        ("json_or_schema_failure", "medium", ("jsondecodeerror", "invalid json", "schema", "validation", "pydantic")),
        ("import_or_dependency", "medium", ("modulenotfounderror", "importerror", "no module named", "dependency")),
        ("process_crash", "high", ("traceback", "exception", "fatal", "crash", "non-zero", "exit code")),
        ("rate_limit", "medium", ("rate limit", "429", "too many requests")),
    )
    for candidate, candidate_severity, needles in rules:
        if any(needle in lowered for needle in needles):
            failure_class = candidate
            severity = candidate_severity
            break
    if "critical" in lowered or entry.level == "critical":
        severity = "critical"
    signature = _normalize_signature(text)
    summary = entry.message or entry.event or entry.raw
    return ClassifiedFailure(
        failure_class=failure_class,
        severity=severity,
        signature=signature,
        summary=_collapse_whitespace(summary)[:240],
    )


def infer_subsystem(path: Path) -> str:
    value = str(path).replace("\\", "/").lower()
    name = path.name.lower()
    if "spark-researcher" in value or "researcher" in name:
        return "researcher"
    if "domain-chip-memory" in value or "memory" in name or "semantic_retrieval" in name:
        return "memory"
    if "spark-intelligence-builder" in value or "builder" in name or "gateway" in name:
        return "builder"
    if "telegram" in value or "discord" in value or "whatsapp" in value:
        return "channel_adapter"
    if "scheduler" in name or "watchdog" in name or "pulse" in name or "jobs" in value:
        return "scheduler"
    if "swarm" in value:
        return "swarm"
    return "system"


def render_diagnostic_markdown(report: DiagnosticReport) -> str:
    recurring = [finding for finding in report.findings if finding.is_recurring(report.recurring_threshold)]
    generated_stamp = report.generated_at.replace(":", "-")
    lines = [
        "---",
        "type: spark-diagnostic-report",
        f"generated_at: {report.generated_at}",
        "tags:",
        "  - spark/diagnostics",
        "  - spark/passive-monitoring",
        "---",
        "",
        f"# Spark Diagnostic Report {generated_stamp}",
        "",
        "## Links",
        "",
        "- [[Spark Diagnostics]]",
        "- [[Builder]]",
        "- [[Memory]]",
        "- [[Researcher]]",
        "",
        "## Summary",
        "",
        f"- home: `{report.home}`",
        f"- log sources: `{len(report.sources)}`",
        f"- scanned lines: `{report.scanned_line_count}`",
        f"- failure lines: `{report.failure_line_count}`",
        f"- finding signatures: `{len(report.findings)}`",
        f"- recurring signatures: `{len(recurring)}`",
        f"- recurring threshold: `{report.recurring_threshold}`",
        "",
        "## Subsystems",
        "",
    ]
    if report.counts_by_subsystem:
        lines.extend(f"- {subsystem}: `{count}`" for subsystem, count in report.counts_by_subsystem.items())
    else:
        lines.append("- no failures classified")
    lines.extend(["", "## Failure Classes", ""])
    if report.counts_by_failure_class:
        lines.extend(f"- {failure_class}: `{count}`" for failure_class, count in report.counts_by_failure_class.items())
    else:
        lines.append("- no failures classified")
    lines.extend(["", "## Recurring Bugs", ""])
    if recurring:
        for finding in recurring:
            lines.extend(_render_finding_block(finding, recurring_threshold=report.recurring_threshold))
    else:
        lines.append("No recurring signatures met the threshold.")
    lines.extend(["", "## Recent Findings", ""])
    if report.findings:
        for finding in report.findings[:20]:
            lines.extend(_render_finding_block(finding, recurring_threshold=report.recurring_threshold))
    else:
        lines.append("No failures found in scanned log windows.")
    lines.extend(["", "## Sources", ""])
    if report.sources:
        for source in report.sources[:100]:
            lines.append(f"- `{source.subsystem}` `{source.source_kind}` {source.path}")
    else:
        lines.append("- no log sources discovered")
    lines.extend(["", "## Connector Health", ""])
    if report.service_checks:
        for check in report.service_checks:
            required = " required" if check.required else ""
            lines.append(
                f"- `{check.status}` `{check.service}`{required} -> {check.target} - {check.detail}"
            )
    else:
        lines.append("- no connector checks ran")
    return "\n".join(lines).rstrip() + "\n"


def write_diagnostic_markdown(report: DiagnosticReport, *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "-")
    output_path = output_dir / f"spark-diagnostic-{stamp}.md"
    output_path.write_text(render_diagnostic_markdown(report), encoding="utf-8")
    latest_path = output_dir / "Spark Diagnostics.md"
    latest_path.write_text(render_diagnostic_markdown(report), encoding="utf-8")
    return output_path


def _render_finding_block(
    finding: DiagnosticFinding,
    *,
    recurring_threshold: int = DEFAULT_RECURRING_THRESHOLD,
) -> list[str]:
    lines = [
        f"### {finding.subsystem} / {finding.failure_class}",
        "",
        f"- severity: `{finding.severity}`",
        f"- count: `{finding.count}`",
        f"- recurring: `{'yes' if finding.is_recurring(recurring_threshold) else 'no'}`",
        f"- signature: `{finding.signature}`",
    ]
    if finding.first_seen or finding.last_seen:
        lines.append(f"- window: `{finding.first_seen or 'unknown'}` to `{finding.last_seen or 'unknown'}`")
    if finding.sources:
        top_sources = finding.sources.most_common(3)
        lines.append("- top sources: " + "; ".join(f"`{Path(path).name}` x{count}" for path, count in top_sources))
    if finding.examples:
        lines.extend(["", "Evidence:"])
        for entry in finding.examples:
            snippet = _collapse_whitespace(entry.message or entry.raw)
            lines.append(f"- `{Path(entry.source_path).name}:{entry.line_number}` {snippet[:300]}")
    lines.append("")
    return lines


def _looks_like_failure(entry: LogEntry, lowered: str) -> bool:
    if entry.level in FAILURE_LEVELS:
        return True
    if entry.payload:
        if str(entry.payload.get("ok")).lower() == "false":
            return True
        if str(entry.payload.get("status")).lower() in {"failed", "failure", "blocked", "error", "rejected", "abstained"}:
            return True
        event_text = str(entry.event or "").lower()
        if any(needle in event_text for needle in ("failed", "failure", "error", "exception", "blocked", "abstained")):
            return True
        if entry.payload.get("error") not in (None, "", False):
            return True
        message_text = str(entry.message or "").lower()
        if message_text:
            return _text_contains_failure_needle(message_text)
        return False
    return _text_contains_failure_needle(lowered)


def _text_contains_failure_needle(lowered: str) -> bool:
    needles = (
        "traceback",
        " exception",
        "error",
        "failed",
        "failure",
        "timeout",
        "unauthorized",
        "forbidden",
        "database is locked",
        "jsondecodeerror",
        "modulenotfounderror",
        "importerror",
        "rate limit",
        "policy_gate",
        "quarantine",
    )
    return any(needle in lowered for needle in needles)


def _normalize_signature(text: str) -> str:
    value = _collapse_whitespace(text).lower()
    value = re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", value)
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:z|[+-]\d{2}:?\d{2})?\b", "<timestamp>", value)
    value = re.sub(r"\d+", "<num>", value)
    value = re.sub(r"[A-Za-z]:[\\/][^\s'\"`]+", "<path>", value)
    value = re.sub(r"/(?:[\w.-]+/)+[\w.-]+", "<path>", value)
    if len(value) > 220:
        value = value[:220].rstrip() + "..."
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"{value} #{digest}"


def _entry_text(entry: LogEntry) -> str:
    parts = [entry.event, entry.level, entry.message, entry.raw]
    if entry.payload:
        for key in ("error", "reason", "status", "summary", "detail", "failure_family"):
            value = entry.payload.get(key)
            if isinstance(value, dict):
                parts.append(json.dumps(value, sort_keys=True))
            elif value is not None:
                parts.append(str(value))
    return " ".join(str(part) for part in parts if part)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    if not raw.startswith("{"):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _tail_lines(path: Path, *, max_lines: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=max(1, max_lines)))
    except OSError:
        return []


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _first_present_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if not isinstance(value, (dict, list)):
            return str(value)
    return None


def _normalize_level(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized == "warning":
        return "warn"
    if normalized == "fatal":
        return "critical"
    if normalized in {"failed", "failure"}:
        return "failed"
    return normalized


def _infer_subsystem_from_payload(payload: dict[str, Any]) -> str | None:
    text = " ".join(
        str(payload.get(key) or "")
        for key in ("component", "subsystem", "surface", "event_type", "source", "logger")
    ).lower()
    if not text.strip():
        return None
    if "researcher" in text:
        return "researcher"
    if "memory" in text:
        return "memory"
    if "telegram" in text or "discord" in text or "whatsapp" in text:
        return "channel_adapter"
    if "swarm" in text:
        return "swarm"
    if "scheduler" in text or "jobs" in text:
        return "scheduler"
    if "builder" in text or "gateway" in text or "runtime" in text:
        return "builder"
    return None


def _source_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".out", ".err"}:
        return suffix.removeprefix(".")
    return "text"


def _path_check(service: str, target: Path, *, required: bool = False, marker: str | None = None) -> ServiceCheck:
    target = target.expanduser()
    if not target.exists():
        return ServiceCheck(
            service=service,
            status="missing",
            target=str(target),
            detail="path does not exist",
            required=required,
        )
    if marker is not None and not (target / marker).exists():
        return ServiceCheck(
            service=service,
            status="partial",
            target=str(target),
            detail=f"path exists but {marker} is missing",
            required=required,
        )
    return ServiceCheck(
        service=service,
        status="ok",
        target=str(target),
        detail="path discovered",
        required=required,
    )


def _module_root_check(service: str, root: Path | None, *, marker: str) -> ServiceCheck:
    if root is None:
        return ServiceCheck(
            service=service,
            status="missing",
            target=marker,
            detail="module source root was not discovered",
        )
    return _path_check(service, root, marker=marker)


def _tcp_check(service: str, host: str, port: int, *, timeout_seconds: float = 0.25) -> ServiceCheck:
    target = f"{host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return ServiceCheck(service=service, status="ok", target=target, detail="tcp port is reachable")
    except OSError as exc:
        return ServiceCheck(service=service, status="unreachable", target=target, detail=type(exc).__name__)


def _http_check(service: str, url: str, *, timeout_seconds: float = 0.75) -> ServiceCheck:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 0)
            if 200 <= int(status) < 500:
                return ServiceCheck(service=service, status="ok", target=url, detail=f"http {status}")
            return ServiceCheck(service=service, status="unreachable", target=url, detail=f"http {status}")
    except (OSError, urllib.error.URLError) as exc:
        return ServiceCheck(service=service, status="unreachable", target=url, detail=type(exc).__name__)


def _current_builder_source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _discover_module_root(config_manager: ConfigManager, module_name: str) -> Path | None:
    home = config_manager.paths.home
    candidates: list[Path] = []
    for search_root in _configured_module_search_roots(config_manager):
        candidates.append(search_root / module_name / "source")
        candidates.append(search_root / module_name)
    if not candidates:
        candidates = [
            home / "modules" / module_name / "source",
            home.parent / "modules" / module_name / "source",
            Path.home() / ".spark" / "modules" / module_name / "source",
            Path.home() / "Desktop" / module_name,
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _configured_module_search_roots(config_manager: ConfigManager) -> list[Path]:
    raw = config_manager.get_path("spark.diagnostics.module_search_roots")
    if not raw:
        return []
    if isinstance(raw, (str, Path)):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        return []
    return [Path(str(value)).expanduser() for value in values if str(value).strip()]


def _config_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def _severity_sort_key(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(severity, 5)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
