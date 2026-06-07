from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.gateway.routes import GatewayRouteRegistration
from spark_intelligence.observability.store import persist_bound_ledger
from spark_intelligence.state.db import StateDB


TOOL_LEDGER_INGEST_PATH = "/v1/tool-ledger"
TOOL_LEDGER_INGEST_COMMANDS = {"ingest_tool_ledger", "tool_ledger_ingest"}
TOOL_LEDGER_REQUIRED_FIELDS = (
    "ledger_id",
    "turn_id",
    "action_id",
    "capability_id",
    "authorization_decision_id",
    "surface",
)


@dataclass(frozen=True)
class GatewayToolLedgerIngestResult:
    ledger_id: str
    turn_id: str
    action_id: str
    capability_id: str
    authorization_decision_id: str
    surface: str
    status: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "turn_id": self.turn_id,
            "action_id": self.action_id,
            "capability_id": self.capability_id,
            "authorization_decision_id": self.authorization_decision_id,
            "surface": self.surface,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        lines = ["Tool ledger ingested"]
        lines.append(f"- ledger_id: {self.ledger_id}")
        lines.append(f"- turn_id: {self.turn_id}")
        lines.append(f"- action_id: {self.action_id}")
        lines.append(f"- capability_id: {self.capability_id}")
        lines.append(f"- authorization_decision_id: {self.authorization_decision_id}")
        lines.append(f"- surface: {self.surface}")
        if self.status:
            lines.append(f"- status: {self.status}")
        return "\n".join(lines)


def tool_ledger_ingest_route() -> GatewayRouteRegistration:
    return GatewayRouteRegistration(
        path=TOOL_LEDGER_INGEST_PATH,
        methods=("POST",),
        auth_mode="provider_internal",
        owner="gateway-core.tool-ledger",
        content_types=("application/json",),
    )


def ingest_tool_ledger_payload(
    state_db: StateDB,
    payload: dict[str, Any],
    *,
    component: str = "gateway_tool_ledger_ingest",
) -> GatewayToolLedgerIngestResult:
    row = _extract_ledger_row(payload)
    ledger_payload = _ledger_json_payload(row)
    authorization = ledger_payload.get("authorization") if isinstance(ledger_payload.get("authorization"), dict) else {}
    normalized = dict(row)
    extracted = {
        "ledger_id": _text(row.get("ledger_id") or ledger_payload.get("ledger_id")),
        "turn_id": _text(row.get("turn_id") or ledger_payload.get("turn_id")),
        "action_id": _text(row.get("action_id") or ledger_payload.get("action_id")),
        "capability_id": _text(row.get("capability_id") or ledger_payload.get("capability_id")),
        "authorization_decision_id": _text(
            row.get("authorization_decision_id") or authorization.get("decision_id")
        ),
        "surface": _text(row.get("surface")),
        "status": _text(row.get("status")),
    }
    missing = [field for field in TOOL_LEDGER_REQUIRED_FIELDS if not extracted.get(field)]
    if missing:
        raise ValueError(f"tool ledger ingest requires {', '.join(missing)}")
    for key, value in extracted.items():
        if value and not _text(normalized.get(key)):
            normalized[key] = value
    ledger_id = persist_bound_ledger(state_db, row=normalized, component=component)
    return GatewayToolLedgerIngestResult(
        ledger_id=ledger_id,
        turn_id=str(extracted["turn_id"]),
        action_id=str(extracted["action_id"]),
        capability_id=str(extracted["capability_id"]),
        authorization_decision_id=str(extracted["authorization_decision_id"]),
        surface=str(extracted["surface"]),
        status=extracted["status"],
    )


def _extract_ledger_row(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("tool ledger ingest payload must be a JSON object")
    for key in ("row", "ledger_row", "payload"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return dict(nested)
    return dict(payload)


def _ledger_json_payload(row: dict[str, Any]) -> dict[str, Any]:
    ledger_json = row.get("ledger_json")
    if isinstance(ledger_json, dict):
        return ledger_json
    if isinstance(ledger_json, str):
        try:
            parsed = json.loads(ledger_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
