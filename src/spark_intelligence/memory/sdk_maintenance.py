from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.execution import run_governed_command
from spark_intelligence.state.db import StateDB


DEFAULT_MAINTENANCE_VALIDATOR_ROOT = Path.home() / "Desktop" / "domain-chip-memory"
DEFAULT_EVENT_LIMIT = 2000


@dataclass(frozen=True)
class SdkMaintenanceExportResult:
    path: Path
    payload: dict[str, Any]
    write_count: int
    current_state_check_count: int
    historical_state_check_count: int
    report: dict[str, Any] | None = None
    report_path: Path | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "path": str(self.path),
                "write_count": self.write_count,
                "current_state_check_count": self.current_state_check_count,
                "historical_state_check_count": self.historical_state_check_count,
                "report_path": str(self.report_path) if self.report_path else None,
                "report": self.report,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory SDK maintenance replay export"]
        lines.append(f"- path: {self.path}")
        lines.append(f"- writes: {self.write_count}")
        lines.append(f"- current_state checks: {self.current_state_check_count}")
        lines.append(f"- historical_state checks: {self.historical_state_check_count}")
        if self.report is None:
            lines.append("- report: skipped")
        else:
            lines.append(f"- report_path: {self.report_path}")
            maintenance = self.report.get("maintenance") or {}
            lines.append(
                f"- manual_observations_before: {maintenance.get('manual_observations_before', 0)}"
            )
            lines.append(
                f"- manual_observations_after: {maintenance.get('manual_observations_after', 0)}"
            )
            lines.append(
                f"- active_deletion_count: {maintenance.get('active_deletion_count', 0)}"
            )
        return "\n".join(lines)


def export_sdk_maintenance_replay(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    write_path: str | Path | None = None,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    run_report: bool = False,
    report_write_path: str | Path | None = None,
    validator_root: str | Path | None = None,
) -> SdkMaintenanceExportResult:
    payload = build_sdk_maintenance_payload(
        state_db=state_db,
        event_limit=event_limit,
    )
    output_path = Path(write_path) if write_path else _default_output_path(config_manager)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    report = None
    resolved_report_path = Path(report_write_path) if report_write_path else None
    if run_report:
        report = run_sdk_maintenance_report(
            replay_path=output_path,
            validator_root=validator_root,
            write_path=resolved_report_path,
        )
    checks = payload.get("checks") or {}
    current_checks = checks.get("current_state") or []
    historical_checks = checks.get("historical_state") or []
    return SdkMaintenanceExportResult(
        path=output_path,
        payload=payload,
        write_count=len(payload.get("writes") or []),
        current_state_check_count=len(current_checks),
        historical_state_check_count=len(historical_checks),
        report=report,
        report_path=resolved_report_path,
    )


def build_sdk_maintenance_payload(
    *,
    state_db: StateDB,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    accepted_writes = _load_accepted_memory_write_requests(state_db=state_db, event_limit=event_limit)
    writes = [_render_write(item) for item in accepted_writes]
    return {
        "writes": writes,
        "checks": _build_checks(writes),
    }


def run_sdk_maintenance_report(
    *,
    replay_path: str | Path,
    validator_root: str | Path | None = None,
    write_path: str | Path | None = None,
) -> dict[str, Any]:
    extra_args: list[str] = []
    if write_path:
        extra_args.extend(["--write", str(Path(write_path))])
    return _run_domain_chip_memory_cli(
        "run-sdk-maintenance-report",
        str(Path(replay_path)),
        *extra_args,
        validator_root=validator_root,
    )


def _run_domain_chip_memory_cli(
    command_name: str,
    *command_args: str,
    validator_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(validator_root) if validator_root else DEFAULT_MAINTENANCE_VALIDATOR_ROOT
    if not root.exists():
        return {
            "errors": [f"validator_root_missing:{root}"],
            "warnings": [],
            "valid": False,
        }
    command_env = _domain_chip_memory_cli_env(root)
    execution = run_governed_command(
        command=[
            sys.executable,
            "-m",
            "domain_chip_memory.cli",
            command_name,
            *command_args,
        ],
        cwd=str(root),
        env=command_env,
    )
    stdout = execution.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            payload.setdefault("stderr", execution.stderr.strip())
            return payload
    return {
        "valid": execution.exit_code == 0,
        "errors": [] if execution.exit_code == 0 else [execution.stderr.strip() or stdout or "sdk_maintenance_report_failed"],
        "warnings": [],
        "stdout": stdout,
        "stderr": execution.stderr.strip(),
    }


def _domain_chip_memory_cli_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    src_path = str((root / "src").resolve())
    current_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = src_path if not current_pythonpath else f"{src_path}{os.pathsep}{current_pythonpath}"
    return env


def _default_output_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "spark-sdk-maintenance-replay.json"


def _load_accepted_memory_write_requests(*, state_db: StateDB, event_limit: int) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        request_rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT event_id, request_id, session_id, human_id, created_at, facts_json
                FROM builder_events
                WHERE event_type = 'memory_write_requested'
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, event_id ASC
            """,
            (max(event_limit, 1),),
        ).fetchall()
        result_rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT event_id, request_id, session_id, created_at, facts_json
                FROM builder_events
                WHERE event_type = 'memory_write_succeeded'
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, event_id ASC
            """,
            (max(event_limit, 1),),
        ).fetchall()
        turn_rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT event_id, request_id, session_id, created_at, facts_json
                FROM builder_events
                WHERE event_type = 'intent_committed'
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, event_id ASC
            """,
            (max(event_limit, 1),),
        ).fetchall()

    accepted_request_keys = {
        (str(row["session_id"] or ""), str(row["request_id"] or ""))
        for row in result_rows
        if _json_field(row["facts_json"], "accepted_count", default=0) > 0
    }
    turn_text_by_key = {
        (str(row["session_id"] or ""), str(row["request_id"] or "")): str(_json_field(row["facts_json"], "message_text", default="") or "").strip()
        for row in turn_rows
    }

    writes: list[dict[str, Any]] = []
    for row in request_rows:
        request_key = (str(row["session_id"] or ""), str(row["request_id"] or ""))
        if request_key not in accepted_request_keys:
            continue
        facts = _loads_json_value(row["facts_json"])
        operation = str(facts.get("operation") or "auto")
        subject_hint = str(facts.get("subject") or f"human:{row['human_id']}" or "").strip()
        method = str(facts.get("method") or "write_observation").strip()
        observations = facts.get("observations")
        if not isinstance(observations, list):
            continue
        for index, item in enumerate(observations, start=1):
            if not isinstance(item, dict):
                continue
            writes.append(
                {
                    "write_kind": "event" if method == "write_event" else "observation",
                    "text": turn_text_by_key.get(request_key, ""),
                    "speaker": "user",
                    "timestamp": _normalized_timestamp(row["created_at"]),
                    "session_id": request_key[0] or None,
                    "turn_id": request_key[1] or None,
                    "operation": str(item.get("operation") or operation or "auto"),
                    "subject": str(item.get("subject") or subject_hint or "").strip() or None,
                    "predicate": str(item.get("predicate") or "").strip() or None,
                    "value": item.get("value"),
                    "metadata": {
                        "memory_role": str(item.get("memory_role") or facts.get("memory_role") or "current_state"),
                        "source": "spark_intelligence_builder",
                        "write_index": index,
                    },
                }
            )
    return writes


def _render_write(item: dict[str, Any]) -> dict[str, Any]:
    rendered = {
        "write_kind": item.get("write_kind") or "observation",
        "text": item.get("text") or "",
        "speaker": item.get("speaker") or "user",
        "timestamp": item.get("timestamp"),
        "session_id": item.get("session_id"),
        "turn_id": item.get("turn_id"),
        "operation": item.get("operation") or "auto",
        "subject": item.get("subject"),
        "predicate": item.get("predicate"),
        "value": item.get("value"),
        "metadata": item.get("metadata") or {},
    }
    return {key: value for key, value in rendered.items() if value not in (None, [], {}, "")}


def _build_checks(writes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    current_state: list[dict[str, Any]] = []
    historical_state: list[dict[str, Any]] = []
    seen_current: set[tuple[str, str]] = set()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for write in writes:
        if str(write.get("write_kind") or "observation") != "observation":
            continue
        subject = str(write.get("subject") or "").strip()
        predicate = str(write.get("predicate") or "").strip()
        if not subject or not predicate:
            continue
        key = (subject, predicate)
        grouped.setdefault(key, []).append(write)
        if key not in seen_current:
            current_state.append({"subject": subject, "predicate": predicate})
            seen_current.add(key)

    for (subject, predicate), items in grouped.items():
        values = []
        for item in items:
            value = item.get("value")
            if value not in values:
                values.append(value)
        if len(values) < 2:
            continue
        latest_timestamp = next(
            (item.get("timestamp") for item in reversed(items) if item.get("timestamp")),
            None,
        )
        if latest_timestamp:
            historical_state.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "as_of": latest_timestamp,
                }
            )
    checks: dict[str, list[dict[str, Any]]] = {}
    if current_state:
        checks["current_state"] = current_state
    if historical_state:
        checks["historical_state"] = historical_state
    return checks


def _loads_json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_field(value: Any, field: str, default: Any = None) -> Any:
    payload = _loads_json_value(value)
    return payload.get(field, default)


def _normalized_timestamp(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
