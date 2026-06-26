from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


CAPABILITY_LEDGER_SCHEMA_VERSION = "spark.capability_ledger.v1"
CAPABILITY_LEDGER_STATES = (
    "proposed",
    "scaffolded",
    "probed",
    "approved",
    "activated",
    "disabled",
    "rolled_back",
)
CAPABILITY_PROPOSAL_SCHEMA_VERSION = "spark.capability_proposal.v1"
CAPABILITY_PROPOSAL_ROUTES = {
    "domain_chip",
    "runtime_patch",
    "capability_connector",
    "mission_artifact",
    "workflow_automation",
}
CAPABILITY_PROPOSAL_REQUIRED_FIELDS = (
    "capability_goal",
    "recipient",
    "implementation_route",
    "owner_system",
    "permissions_required",
    "safe_probe",
    "human_approval_boundary",
    "rollback_path",
    "activation_path",
    "eval_or_smoke_test",
    "capability_ledger_key",
    "claim_boundary",
)
CONNECTOR_HARNESS_SCHEMA_VERSION = "spark.connector_harness.v1"
CONNECTOR_HARNESS_KEYS = {"email", "calendar", "voice", "browser", "files", "workflow", "api"}
CONNECTOR_HARNESS_REQUIRED_FIELDS = (
    "schema_version",
    "authority_stage",
    "connector_key",
    "permissions_required",
    "dry_run_probe",
    "redaction_policy",
    "approval_prompt",
    "live_access_blocked_until",
    "blocked_live_actions",
    "trace_fields",
    "truth_boundary",
)

_FORWARD_TRANSITIONS = {
    "proposed": {"scaffolded", "probed", "approved", "disabled", "rolled_back"},
    "scaffolded": {"probed", "approved", "disabled", "rolled_back"},
    "probed": {"approved", "disabled", "rolled_back"},
    "approved": {"activated", "disabled", "rolled_back"},
    "activated": {"disabled", "rolled_back"},
    "disabled": {"rolled_back"},
    "rolled_back": set(),
}


@dataclass(frozen=True)
class CapabilityLedgerResult:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        if self.payload.get("schema_version") == "spark.capability_activation_preflight.v1":
            lines = [
                "Capability activation preflight",
                f"Key: {self.payload.get('capability_ledger_key') or 'unknown'}",
                f"Status: {self.payload.get('status') or 'unknown'}",
                f"Active: {'yes' if self.payload.get('active') else 'no'}",
                f"Activation blocked: {'yes' if self.payload.get('activation_blocked') else 'no'}",
            ]
            missing_lifecycle = [str(item) for item in (self.payload.get("missing_lifecycle") or []) if str(item).strip()]
            missing_evidence = [str(item) for item in (self.payload.get("missing_evidence") or []) if str(item).strip()]
            lines.append(f"Missing lifecycle: {', '.join(missing_lifecycle) if missing_lifecycle else 'none'}")
            lines.append(f"Missing evidence: {', '.join(missing_evidence) if missing_evidence else 'none'}")
            counts = self.payload.get("evidence_counts") if isinstance(self.payload.get("evidence_counts"), dict) else {}
            lines.append(
                "Evidence counts: "
                f"total={counts.get('total', 0)} approval={counts.get('approval', 0)} probe_or_eval={counts.get('probe_or_eval', 0)}"
            )
            harness = self.payload.get("connector_harness") if isinstance(self.payload.get("connector_harness"), dict) else {}
            if harness.get("connector_key"):
                lines.append(f"Connector: {harness.get('connector_key')} ({harness.get('authority_stage') or 'unknown'})")
                lines.append(f"Live access blocked until: {harness.get('live_access_blocked_until') or 'approval_and_probe_evidence'}")
            for label in ("safe_probe", "approval_boundary", "eval_or_smoke_test", "rollback_path"):
                value = str(self.payload.get(label) or "").strip()
                if value:
                    lines.append(f"{label}: {value}")
            lines.append("Boundary: preflight is read-only; activation still requires a separate ledger event with approval plus probe/eval evidence.")
            return "\n".join(lines).strip()
        if self.payload.get("schema_version") == "spark.capability_activation_dry_run.v1":
            lines = [
                "Capability activation dry-run",
                f"Key: {self.payload.get('capability_ledger_key') or 'unknown'}",
                f"Status: {self.payload.get('status') or 'unknown'}",
                f"Active: {'yes' if self.payload.get('active') else 'no'}",
                f"Dry-run ok: {'yes' if self.payload.get('activation_dry_run_ok') else 'no'}",
                "Would mutate: no",
            ]
            missing_lifecycle = [str(item) for item in (self.payload.get("missing_lifecycle") or []) if str(item).strip()]
            missing_evidence = [str(item) for item in (self.payload.get("missing_evidence") or []) if str(item).strip()]
            validation_errors = [str(item) for item in (self.payload.get("validation_errors") or []) if str(item).strip()]
            lines.append(f"Missing lifecycle: {', '.join(missing_lifecycle) if missing_lifecycle else 'none'}")
            lines.append(f"Missing evidence: {', '.join(missing_evidence) if missing_evidence else 'none'}")
            if validation_errors:
                lines.append(f"Validation errors: {'; '.join(validation_errors)}")
            harness = self.payload.get("connector_harness") if isinstance(self.payload.get("connector_harness"), dict) else {}
            if harness.get("connector_key"):
                lines.append(f"Connector: {harness.get('connector_key')} ({harness.get('authority_stage') or 'unknown'})")
                lines.append(f"Live access blocked until: {harness.get('live_access_blocked_until') or 'approval_and_probe_evidence'}")
            lines.append("Boundary: dry-run is read-only; activation still requires a separate activated ledger event.")
            return "\n".join(lines).strip()
        if isinstance(self.payload.get("entries"), dict):
            entries = [item for item in (self.payload.get("entries") or {}).values() if isinstance(item, dict)]
        elif self.payload.get("capability_ledger_key"):
            entries = [self.payload]
        else:
            entries = []
        lines = [
            "Spark capability ledger",
            f"Path: {self.payload.get('ledger_path') or 'unknown'}",
            f"Entries: {len(entries)}",
            "",
        ]
        if not entries:
            lines.append("No capability proposals have been recorded yet.")
        for entry in sorted(entries, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:12]:
            proposal = entry.get("proposal_packet") if isinstance(entry.get("proposal_packet"), dict) else {}
            harness = proposal.get("connector_harness") if isinstance(proposal.get("connector_harness"), dict) else {}
            lines.append(f"- {entry.get('capability_ledger_key')}: {entry.get('status')}")
            route = entry.get("implementation_route")
            if route:
                lines.append(f"  route: {route}")
            goal = str(proposal.get("capability_goal") or "").strip()
            if goal:
                lines.append(f"  goal: {goal}")
            permissions = [str(item) for item in (proposal.get("permissions_required") or []) if str(item).strip()]
            if permissions:
                lines.append(f"  permissions: {', '.join(permissions)}")
            if harness:
                lines.append(f"  connector: {harness.get('connector_key') or 'unknown'} ({harness.get('authority_stage') or 'unknown'})")
                lines.append(f"  live_access_blocked_until: {harness.get('live_access_blocked_until') or 'approval_and_probe_evidence'}")
            for label, field in (
                ("safe_probe", "safe_probe"),
                ("approval_boundary", "human_approval_boundary"),
                ("eval_or_smoke_test", "eval_or_smoke_test"),
                ("rollback_path", "rollback_path"),
            ):
                value = str(proposal.get(field) or "").strip()
                if value:
                    lines.append(f"  {label}: {value}")
            evidence_count = len(entry.get("activation_evidence") or [])
            lines.append(f"  activation_evidence: {evidence_count}")
            lines.append("  activation_boundary: not live until approved and activated with separate probe/eval evidence")
        lines.extend(
            [
                "",
                "Boundary: proposal packets are plans only; activation requires ledger state plus separate evidence.",
            ]
        )
        return "\n".join(lines).strip()


def capability_ledger_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "capability-ledger" / "capability-ledger.json"


def _unknown_capability_ledger_key_message(capability_ledger_key: str, entries: dict[str, Any]) -> str:
    known = sorted(name for name, value in entries.items() if isinstance(value, dict))
    if known:
        return (
            f"Unknown capability ledger key: {capability_ledger_key}. "
            f"Known keys: {', '.join(known)}."
        )
    return (
        f"Unknown capability ledger key: {capability_ledger_key}. "
        "The capability ledger has no entries yet -- propose a capability first."
    )


def load_capability_ledger(config_manager: ConfigManager) -> CapabilityLedgerResult:
    path = capability_ledger_path(config_manager)
    ledger = _read_ledger(path)
    ledger["ledger_path"] = str(path)
    return CapabilityLedgerResult(payload=ledger)


def build_capability_activation_preflight(
    *,
    config_manager: ConfigManager,
    capability_ledger_key: str,
) -> CapabilityLedgerResult:
    path = capability_ledger_path(config_manager)
    ledger = _read_ledger(path)
    entries = ledger.setdefault("entries", {})
    entry = entries.get(capability_ledger_key)
    if not isinstance(entry, dict):
        raise ValueError(_unknown_capability_ledger_key_message(capability_ledger_key, entries))

    status = str(entry.get("status") or "proposed")
    evidence_records = [item for item in (entry.get("activation_evidence") or []) if isinstance(item, dict)]
    evidence_payloads = [
        item.get("evidence")
        for item in evidence_records
        if isinstance(item.get("evidence"), dict)
    ]
    has_approval = any(payload.get("approval_ref") or payload.get("approved_by") for payload in evidence_payloads)
    has_probe_or_eval = any(payload.get("probe_ref") or payload.get("eval_ref") or payload.get("smoke_ref") for payload in evidence_payloads)
    missing_evidence: list[str] = []
    if not has_approval:
        missing_evidence.append("approval_ref_or_approved_by")
    if not has_probe_or_eval:
        missing_evidence.append("probe_ref_or_eval_ref_or_smoke_ref")
    missing_lifecycle: list[str] = []
    if status not in {"approved", "activated"}:
        missing_lifecycle.append("approved")
    active = capability_is_active(entry)
    activation_ready = (status == "approved") and has_approval and has_probe_or_eval
    activation_blocked = not active and not activation_ready
    proposal = entry.get("proposal_packet") if isinstance(entry.get("proposal_packet"), dict) else {}
    harness = proposal.get("connector_harness") if isinstance(proposal.get("connector_harness"), dict) else {}
    payload = {
        "schema_version": "spark.capability_activation_preflight.v1",
        "capability_ledger_key": capability_ledger_key,
        "status": status,
        "active": active,
        "activation_ready": activation_ready,
        "activation_blocked": activation_blocked,
        "missing_lifecycle": missing_lifecycle,
        "missing_evidence": missing_evidence,
        "evidence_counts": {
            "total": len(evidence_records),
            "approval": 1 if has_approval else 0,
            "probe_or_eval": 1 if has_probe_or_eval else 0,
        },
        "safe_probe": str(proposal.get("safe_probe") or ""),
        "approval_boundary": str(proposal.get("human_approval_boundary") or ""),
        "eval_or_smoke_test": str(proposal.get("eval_or_smoke_test") or ""),
        "rollback_path": str(proposal.get("rollback_path") or ""),
        "connector_harness": {
            "connector_key": str(harness.get("connector_key") or ""),
            "authority_stage": str(harness.get("authority_stage") or ""),
            "live_access_blocked_until": str(harness.get("live_access_blocked_until") or ""),
        },
        "activation_event_required_fields": [
            "approval_ref_or_approved_by",
            "probe_ref_or_eval_ref_or_smoke_ref",
        ],
        "truth_boundary": "preflight_is_read_only_and_not_activation_proof",
        "ledger_path": str(path),
    }
    return CapabilityLedgerResult(payload=payload)


def build_capability_activation_dry_run(
    *,
    config_manager: ConfigManager,
    capability_ledger_key: str,
    evidence: dict[str, Any] | None = None,
) -> CapabilityLedgerResult:
    path = capability_ledger_path(config_manager)
    ledger = _read_ledger(path)
    entries = ledger.setdefault("entries", {})
    entry = entries.get(capability_ledger_key)
    if not isinstance(entry, dict):
        raise ValueError(_unknown_capability_ledger_key_message(capability_ledger_key, entries))

    status = str(entry.get("status") or "proposed")
    evidence_payload = dict(evidence or {})
    missing_lifecycle: list[str] = []
    if "activated" not in _FORWARD_TRANSITIONS.get(status, set()):
        missing_lifecycle.append("approved")
    missing_evidence = _activation_evidence_gaps(evidence_payload)
    validation_errors: list[str] = []
    try:
        _validate_activation_evidence("activated", evidence_payload)
    except ValueError as exc:
        validation_errors.append(str(exc))
    activation_dry_run_ok = not missing_lifecycle and not missing_evidence and not validation_errors
    proposal = entry.get("proposal_packet") if isinstance(entry.get("proposal_packet"), dict) else {}
    harness = proposal.get("connector_harness") if isinstance(proposal.get("connector_harness"), dict) else {}
    payload = {
        "schema_version": "spark.capability_activation_dry_run.v1",
        "capability_ledger_key": capability_ledger_key,
        "status": status,
        "active": capability_is_active(entry),
        "activation_dry_run_ok": activation_dry_run_ok,
        "would_mutate": False,
        "target_lifecycle_state": "activated",
        "missing_lifecycle": missing_lifecycle,
        "missing_evidence": missing_evidence,
        "validation_errors": validation_errors,
        "proposed_activation_evidence": evidence_payload,
        "connector_harness": {
            "connector_key": str(harness.get("connector_key") or ""),
            "authority_stage": str(harness.get("authority_stage") or ""),
            "live_access_blocked_until": str(harness.get("live_access_blocked_until") or ""),
        },
        "truth_boundary": "activation_dry_run_is_not_activation_proof_and_does_not_mutate_ledger",
        "ledger_path": str(path),
    }
    return CapabilityLedgerResult(payload=payload)


def record_capability_proposal(
    *,
    config_manager: ConfigManager,
    proposal_packet: dict[str, Any],
    actor_id: str = "operator",
    source_ref: str = "",
) -> CapabilityLedgerResult:
    packet = _validate_proposal_packet(proposal_packet)
    ledger_key = str(packet["capability_ledger_key"])
    path = capability_ledger_path(config_manager)
    ledger = _read_ledger(path)
    now = _utc_now()
    entries = ledger.setdefault("entries", {})
    existing = entries.get(ledger_key) if isinstance(entries.get(ledger_key), dict) else None
    if existing:
        status = str(existing.get("status") or "proposed")
        entry = dict(existing)
        entry["proposal_packet"] = packet
        entry["proposal_schema_version"] = str(packet.get("schema_version") or "")
        entry["implementation_route"] = str(packet.get("implementation_route") or "")
        entry["owner_system"] = str(packet.get("owner_system") or "")
        entry["updated_at"] = now
        entry.setdefault("activation_evidence", [])
        entry.setdefault("events", [])
        entry["events"].append(_event("proposal_recorded", status, actor_id=actor_id, source_ref=source_ref, created_at=now))
    else:
        entry = {
            "capability_ledger_key": ledger_key,
            "status": "proposed",
            "proposal_schema_version": str(packet.get("schema_version") or ""),
            "proposal_packet": packet,
            "implementation_route": str(packet.get("implementation_route") or ""),
            "owner_system": str(packet.get("owner_system") or ""),
            "created_at": now,
            "updated_at": now,
            "activation_evidence": [],
            "events": [_event("proposal_recorded", "proposed", actor_id=actor_id, source_ref=source_ref, created_at=now)],
            "truth_boundary": "proposal_packet_is_not_activation_proof",
        }
    entries[ledger_key] = entry
    _write_ledger(path, ledger)
    entry_payload = dict(entry)
    entry_payload["ledger_path"] = str(path)
    return CapabilityLedgerResult(payload=entry_payload)


def record_capability_ledger_event(
    *,
    config_manager: ConfigManager,
    capability_ledger_key: str,
    lifecycle_state: str,
    actor_id: str = "operator",
    source_ref: str = "",
    evidence: dict[str, Any] | None = None,
) -> CapabilityLedgerResult:
    status = _validate_state(lifecycle_state)
    path = capability_ledger_path(config_manager)
    ledger = _read_ledger(path)
    entries = ledger.setdefault("entries", {})
    entry = entries.get(capability_ledger_key)
    if not isinstance(entry, dict):
        raise ValueError(_unknown_capability_ledger_key_message(capability_ledger_key, entries))
    previous = str(entry.get("status") or "proposed")
    if status != previous and status not in _FORWARD_TRANSITIONS.get(previous, set()):
        raise ValueError(f"Invalid capability lifecycle transition: {previous} -> {status}")
    evidence_payload = _validate_activation_evidence(status, evidence or {})
    now = _utc_now()
    entry = dict(entry)
    entry["status"] = status
    entry["updated_at"] = now
    event = _event(
        "lifecycle_state_recorded",
        status,
        actor_id=actor_id,
        source_ref=source_ref,
        created_at=now,
        previous_status=previous,
    )
    if evidence_payload:
        evidence_record = {
            "recorded_at": now,
            "lifecycle_state": status,
            "actor_id": actor_id,
            "source_ref": source_ref,
            "evidence": evidence_payload,
        }
        entry.setdefault("activation_evidence", []).append(evidence_record)
        event["evidence_ref"] = evidence_payload.get("evidence_ref") or evidence_payload.get("eval_ref") or evidence_payload.get("probe_ref")
    entry.setdefault("events", []).append(event)
    if status == "activated":
        entry["current_activation"] = entry["activation_evidence"][-1]
    if status in {"disabled", "rolled_back"}:
        entry["current_activation"] = None
    entries[capability_ledger_key] = entry
    _write_ledger(path, ledger)
    entry_payload = dict(entry)
    entry_payload["ledger_path"] = str(path)
    return CapabilityLedgerResult(payload=entry_payload)


def capability_is_active(entry: dict[str, Any]) -> bool:
    return str(entry.get("status") or "") == "activated" and bool(entry.get("current_activation"))


def _validate_proposal_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise ValueError("Capability proposal packet must be a JSON object")
    schema_version = str(packet.get("schema_version") or "")
    if schema_version != CAPABILITY_PROPOSAL_SCHEMA_VERSION:
        raise ValueError(f"Unsupported capability proposal schema_version: {schema_version or 'missing'}")
    status = str(packet.get("status") or "")
    if status != "proposal_plan_only":
        raise ValueError("Capability proposal packet status must remain proposal_plan_only")
    missing = [field for field in CAPABILITY_PROPOSAL_REQUIRED_FIELDS if field not in packet]
    if missing:
        raise ValueError(f"Capability proposal packet missing required field(s): {', '.join(missing)}")
    _validate_required_text_fields(packet, CAPABILITY_PROPOSAL_REQUIRED_FIELDS, skip={"permissions_required"})
    route = str(packet.get("implementation_route") or "").strip()
    if route not in CAPABILITY_PROPOSAL_ROUTES:
        raise ValueError(f"Unsupported capability implementation_route: {route or 'missing'}")
    ledger_key = str(packet.get("capability_ledger_key") or "").strip()
    if not ledger_key:
        raise ValueError("Capability proposal packet requires capability_ledger_key")
    permissions = packet.get("permissions_required")
    if not _is_non_empty_string_list(permissions):
        raise ValueError("Capability proposal packet requires non-empty permissions_required")
    if "connector_harness" in packet:
        _validate_connector_harness(packet.get("connector_harness"))
    return dict(packet)


def _validate_connector_harness(harness: Any) -> None:
    if not isinstance(harness, dict):
        raise ValueError("connector_harness must be a JSON object when present")
    missing = [field for field in CONNECTOR_HARNESS_REQUIRED_FIELDS if field not in harness]
    if missing:
        raise ValueError(f"connector_harness missing required field(s): {', '.join(missing)}")
    schema_version = str(harness.get("schema_version") or "").strip()
    if schema_version != CONNECTOR_HARNESS_SCHEMA_VERSION:
        raise ValueError(f"Unsupported connector_harness schema_version: {schema_version or 'missing'}")
    authority_stage = str(harness.get("authority_stage") or "").strip()
    if authority_stage != "proposal_only":
        raise ValueError("connector_harness authority_stage must remain proposal_only")
    connector_key = str(harness.get("connector_key") or "").strip()
    if connector_key not in CONNECTOR_HARNESS_KEYS:
        raise ValueError(f"Unsupported connector_harness connector_key: {connector_key or 'missing'}")
    if not _is_non_empty_string_list(harness.get("permissions_required")):
        raise ValueError("connector_harness requires non-empty permissions_required")
    if not _is_non_empty_string_list(harness.get("blocked_live_actions")):
        raise ValueError("connector_harness requires non-empty blocked_live_actions")
    if not _is_non_empty_string_list(harness.get("trace_fields")):
        raise ValueError("connector_harness requires non-empty trace_fields")
    _validate_required_text_fields(
        harness,
        CONNECTOR_HARNESS_REQUIRED_FIELDS,
        skip={"permissions_required", "blocked_live_actions", "trace_fields"},
        label="connector_harness",
    )


def _validate_required_text_fields(
    payload: dict[str, Any],
    fields: tuple[str, ...],
    *,
    skip: set[str] | None = None,
    label: str = "Capability proposal packet",
) -> None:
    skipped = skip or set()
    for field in fields:
        if field in skipped:
            continue
        if not str(payload.get(field) or "").strip():
            raise ValueError(f"{label} requires non-empty {field}")


def _is_non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and any(str(item or "").strip() for item in value)


def _validate_state(state: str) -> str:
    normalized = str(state or "").strip()
    if normalized not in CAPABILITY_LEDGER_STATES:
        raise ValueError(f"Unsupported capability lifecycle state: {state}")
    return normalized


def _validate_activation_evidence(status: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if not evidence:
        if status == "activated":
            raise ValueError("Activation requires separate evidence with approval and probe/eval references")
        return {}
    payload = dict(evidence)
    if status == "activated":
        if _activation_evidence_gaps(payload):
            raise ValueError("Activation evidence requires approval_ref/approved_by and probe_ref/eval_ref/smoke_ref")
    return payload


def _activation_evidence_gaps(evidence: dict[str, Any]) -> list[str]:
    payload = dict(evidence or {})
    gaps: list[str] = []
    if not (payload.get("approval_ref") or payload.get("approved_by")):
        gaps.append("approval_ref_or_approved_by")
    if not (payload.get("probe_ref") or payload.get("eval_ref") or payload.get("smoke_ref")):
        gaps.append("probe_ref_or_eval_ref_or_smoke_ref")
    return gaps


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": CAPABILITY_LEDGER_SCHEMA_VERSION,
            "entries": {},
            "truth_boundary": "capability_ledger_state_plus_evidence_outranks_proposal_packet_status",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Capability ledger is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Capability ledger must be a JSON object: {path}")
    payload.setdefault("schema_version", CAPABILITY_LEDGER_SCHEMA_VERSION)
    payload.setdefault("entries", {})
    payload.setdefault("truth_boundary", "capability_ledger_state_plus_evidence_outranks_proposal_packet_status")
    return payload


def _write_ledger(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp_path.replace(path)


def _event(
    event_type: str,
    status: str,
    *,
    actor_id: str,
    source_ref: str,
    created_at: str,
    previous_status: str = "",
) -> dict[str, Any]:
    payload = {
        "event_type": event_type,
        "status": status,
        "actor_id": actor_id,
        "source_ref": source_ref,
        "created_at": created_at,
    }
    if previous_status:
        payload["previous_status"] = previous_status
    return payload


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
