from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.self_awareness.capsule import build_self_awareness_capsule
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class CapabilityDriftHeartbeatResult:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload.get("summary"), dict) else {}
        lines = [
            "Spark capability drift heartbeat",
            f"- status: {self.payload.get('status') or 'unknown'}",
            f"- drifted_capabilities: {summary.get('drifted_capability_count', 0)}",
            f"- unverified_configured: {summary.get('unverified_configured_count', 0)}",
            f"- report_path: {self.payload.get('report_path') or 'not_written'}",
            "- authority: observability_non_authoritative",
            "- memory_policy: typed_report_not_chat_memory",
        ]
        probes = [item for item in self.payload.get("probe_plan") or [] if isinstance(item, dict)]
        if probes:
            lines.append("Probe plan")
            for item in probes[:8]:
                lines.append(f"- {item.get('capability_key')}: {item.get('safe_probe')}")
        return "\n".join(lines)


def build_capability_drift_heartbeat(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str = "",
    session_id: str = "",
    channel_kind: str = "",
    user_message: str = "",
    write_report: bool = True,
) -> CapabilityDriftHeartbeatResult:
    capsule = build_self_awareness_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        user_message=user_message or "capability drift heartbeat",
    )
    capsule_payload = capsule.to_payload()
    evidence_rows = [row for row in capsule_payload.get("capability_evidence") or [] if isinstance(row, dict)]
    probe_registry = [row for row in capsule_payload.get("capability_probe_registry") or [] if isinstance(row, dict)]
    probe_by_key = _probe_registry_by_key(probe_registry)
    capabilities_needing_probe = [
        _drift_row(row, probe_by_key.get(str(row.get("capability_key") or "").strip()))
        for row in evidence_rows
        if _needs_probe(row)
    ]
    unverified_configured = _unverified_configured_capabilities(
        evidence_rows=evidence_rows,
        probe_registry=probe_registry,
    )
    probe_plan = _probe_plan(capabilities_needing_probe, unverified_configured)
    summary = {
        "evidenced_capability_count": len(evidence_rows),
        "drifted_capability_count": len(capabilities_needing_probe),
        "unverified_configured_count": len(unverified_configured),
        "stale_success_count": sum(1 for row in capabilities_needing_probe if row.get("drift_kind") == "stale_success"),
        "recent_failure_count": sum(1 for row in capabilities_needing_probe if row.get("drift_kind") == "recent_failure"),
        "observed_without_success_count": sum(
            1 for row in capabilities_needing_probe if row.get("drift_kind") == "observed_without_success"
        ),
        "missing_eval_coverage_count": sum(
            1 for row in capabilities_needing_probe if row.get("eval_coverage_status") == "missing"
        ),
    }
    status = "warn" if capabilities_needing_probe or unverified_configured else "pass"
    payload = {
        "kind": "capability_drift_heartbeat",
        "checked_at": _utc_timestamp(),
        "status": status,
        "healthy": status == "pass",
        "summary": summary,
        "capabilities_needing_probe": capabilities_needing_probe,
        "unverified_configured_capabilities": unverified_configured,
        "probe_plan": probe_plan,
        "authority": "observability_non_authoritative",
        "memory_policy": "typed_report_not_chat_memory",
        "truth_boundary": "capability_drift_reports_do_not_promote_runtime_success_or_failure",
        "source_refs": [
            "self_awareness.capability_evidence",
            "self_awareness.capability_probe_registry",
            "builder_events.observability",
        ],
        "warnings": _warnings(summary=summary, unverified_count=len(unverified_configured)),
    }
    if write_report:
        report_path = _report_path(config_manager=config_manager, checked_at=str(payload["checked_at"]))
        payload["report_path"] = str(report_path)
        payload["report_written"] = True
        _write_report(config_manager=config_manager, report_path=report_path, payload=payload)
    else:
        payload["report_path"] = ""
        payload["report_written"] = False
    return CapabilityDriftHeartbeatResult(payload=payload)


def _probe_registry_by_key(probe_registry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    priority = {"chip": 5, "provider": 4, "adapter": 4, "system": 3, "path": 2, "repo": 1}
    selected: dict[str, dict[str, Any]] = {}
    selected_priority: dict[str, int] = {}
    for row in probe_registry:
        key = str(row.get("target_key") or "").strip()
        if not key:
            continue
        row_priority = priority.get(str(row.get("target_kind") or "").strip(), 0)
        if key not in selected or row_priority > selected_priority.get(key, -1):
            selected[key] = row
            selected_priority[key] = row_priority
    return selected


def _needs_probe(row: dict[str, Any]) -> bool:
    confidence = str(row.get("confidence_level") or "").strip()
    freshness = str(row.get("freshness_status") or "").strip()
    if confidence in {"recent_failure", "observed_without_success", "stale_success"}:
        return True
    return freshness in {"aging", "stale", "unknown"} and not bool(row.get("can_claim_confidently"))


def _drift_row(row: dict[str, Any], probe_record: dict[str, Any] | None) -> dict[str, Any]:
    key = str(row.get("capability_key") or "").strip()
    return {
        "capability_key": key,
        "drift_kind": _drift_kind(row),
        "confidence_level": str(row.get("confidence_level") or "unknown"),
        "freshness_status": str(row.get("freshness_status") or "unknown"),
        "last_success_at": row.get("last_success_at"),
        "last_failure_at": row.get("last_failure_at"),
        "last_failure_reason": row.get("last_failure_reason"),
        "last_success_age_days": _age_days(row.get("last_success_at")),
        "eval_coverage_status": str(row.get("eval_coverage_status") or "missing"),
        "eval_coverage_sources": list(row.get("eval_coverage_sources") or []),
        "evidence_count": int(row.get("evidence_count") or 0),
        "safe_probe": _safe_probe(key=key, probe_record=probe_record),
        "probe_access_boundary": str((probe_record or {}).get("access_boundary") or "read_only_probe"),
        "claim_boundary": "run_probe_before_confident_current_success_claim",
        "source": str(row.get("source") or "observability_events"),
    }


def _drift_kind(row: dict[str, Any]) -> str:
    confidence = str(row.get("confidence_level") or "").strip()
    if confidence in {"recent_failure", "observed_without_success", "stale_success"}:
        return confidence
    freshness = str(row.get("freshness_status") or "").strip()
    if freshness in {"aging", "stale", "unknown"}:
        return f"{freshness}_freshness"
    return "needs_probe"


def _unverified_configured_capabilities(
    *,
    evidence_rows: list[dict[str, Any]],
    probe_registry: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidenced_keys = {str(row.get("capability_key") or "").strip() for row in evidence_rows}
    rows: list[dict[str, Any]] = []
    for probe in probe_registry:
        key = str(probe.get("target_key") or "").strip()
        if not key or key in evidenced_keys:
            continue
        rows.append(
            {
                "capability_key": key,
                "target_kind": str(probe.get("target_kind") or "unknown"),
                "status": str(probe.get("status") or "unknown"),
                "available": bool(probe.get("available")),
                "safe_probe": str(probe.get("safe_probe") or "spark-intelligence status --json"),
                "probe_access_boundary": str(probe.get("access_boundary") or "read_only_probe"),
                "claim_boundary": "configured_or_available_is_not_recent_success",
            }
        )
    return rows[:40]


def _probe_plan(
    capabilities_needing_probe: list[dict[str, Any]],
    unverified_configured: list[dict[str, Any]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in [*capabilities_needing_probe, *unverified_configured]:
        key = str(row.get("capability_key") or "").strip()
        safe_probe = str(row.get("safe_probe") or "").strip()
        if not key or not safe_probe:
            continue
        rows.append(
            {
                "capability_key": key,
                "safe_probe": safe_probe,
                "reason": str(row.get("drift_kind") or "missing_recent_success_evidence"),
                "records_current_success": "false",
            }
        )
    return rows[:20]


def _warnings(*, summary: dict[str, int], unverified_count: int) -> list[str]:
    warnings: list[str] = []
    if int(summary.get("stale_success_count") or 0) > 0:
        warnings.append("capability_last_success_stale")
    if int(summary.get("recent_failure_count") or 0) > 0:
        warnings.append("capability_recent_failure_needs_probe")
    if int(summary.get("observed_without_success_count") or 0) > 0:
        warnings.append("capability_observed_without_success")
    if int(summary.get("missing_eval_coverage_count") or 0) > 0:
        warnings.append("capability_eval_coverage_missing")
    if unverified_count > 0:
        warnings.append("configured_capabilities_missing_recent_success")
    return warnings


def _safe_probe(*, key: str, probe_record: dict[str, Any] | None) -> str:
    if probe_record and str(probe_record.get("safe_probe") or "").strip():
        return str(probe_record.get("safe_probe") or "").strip()
    return f"spark-intelligence self status --user-message 'probe {key}' --json"


def _age_days(value: Any) -> int | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(0, (datetime.now(UTC) - parsed).days)


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _report_path(*, config_manager: ConfigManager, checked_at: str) -> Path:
    reports_dir = config_manager.paths.home / "artifacts" / "capability-drift-heartbeat"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = checked_at.replace(":", "").replace("+", "Z")
    return reports_dir / f"{timestamp}.json"


def _write_report(*, config_manager: ConfigManager, report_path: Path, payload: dict[str, Any]) -> None:
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path = config_manager.paths.home / "artifacts" / "capability-drift-heartbeat" / "latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
