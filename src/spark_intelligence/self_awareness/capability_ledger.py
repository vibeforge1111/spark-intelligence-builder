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
            lines.append(f"- {entry.get('capability_ledger_key')}: {entry.get('status')}")
            route = entry.get("implementation_route")
            if route:
                lines.append(f"  route: {route}")
            evidence_count = len(entry.get("activation_evidence") or [])
            lines.append(f"  activation_evidence: {evidence_count}")
        lines.extend(
            [
                "",
                "Boundary: proposal packets are plans only; activation requires ledger state plus separate evidence.",
            ]
        )
        return "\n".join(lines).strip()


def capability_ledger_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "capability-ledger" / "capability-ledger.json"


def load_capability_ledger(config_manager: ConfigManager) -> CapabilityLedgerResult:
    path = capability_ledger_path(config_manager)
    ledger = _read_ledger(path)
    ledger["ledger_path"] = str(path)
    return CapabilityLedgerResult(payload=ledger)


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
        raise ValueError(f"Unknown capability ledger key: {capability_ledger_key}")
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
    ledger_key = str(packet.get("capability_ledger_key") or "").strip()
    if not ledger_key:
        raise ValueError("Capability proposal packet requires capability_ledger_key")
    return dict(packet)


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
        has_approval = bool(payload.get("approval_ref") or payload.get("approved_by"))
        has_probe_or_eval = bool(payload.get("probe_ref") or payload.get("eval_ref") or payload.get("smoke_ref"))
        if not has_approval or not has_probe_or_eval:
            raise ValueError("Activation evidence requires approval_ref/approved_by and probe_ref/eval_ref/smoke_ref")
    return payload


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
