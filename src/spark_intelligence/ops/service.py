from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import LOCAL_OPERATOR_HUMAN_ID
from spark_intelligence.identity.service import review_pairings
from spark_intelligence.researcher_bridge import researcher_bridge_status
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import swarm_status


@dataclass
class OperatorEventReport:
    rows: list[dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps({"rows": self.rows}, indent=2)

    def to_text(self) -> str:
        if not self.rows:
            return "No operator events recorded."
        lines = ["Operator history:"]
        for row in self.rows:
            lines.append(
                f"- {row['created_at']} actor={row['actor_human_id']} action={row['action']} "
                f"target={row['target_kind']}:{row['target_ref']} "
                f"reason={row['reason'] or 'none'}"
            )
        return "\n".join(lines)


@dataclass
class OperatorInboxReport:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        counts = self.payload["counts"]
        if counts["total"] == 0:
            return "Operator inbox is clear."

        lines = ["Operator inbox:"]
        lines.append(
            "- counts: "
            f"total={counts['total']} "
            f"pending_pairings={counts['pending_pairings']} "
            f"held_pairings={counts['held_pairings']} "
            f"channel_alerts={counts['channel_alerts']} "
            f"bridge_alerts={counts['bridge_alerts']}"
        )

        items = self.payload.get("items") or []
        if items:
            lines.append("- actions:")
            for item in items:
                lines.append(
                    f"  [{item['priority']}] {item['summary']} "
                    f"command={item['recommended_command']}"
                )

        return "\n".join(lines)


def log_operator_event(
    *,
    state_db: StateDB,
    action: str,
    target_kind: str,
    target_ref: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    actor_human_id: str = LOCAL_OPERATOR_HUMAN_ID,
) -> None:
    details_json = json.dumps(details, sort_keys=True) if details else None
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO operator_events(actor_human_id, action, target_kind, target_ref, reason, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor_human_id, action, target_kind, target_ref, reason, details_json),
        )
        conn.commit()


def list_operator_events(state_db: StateDB, *, limit: int = 20) -> OperatorEventReport:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT event_id, actor_human_id, action, target_kind, target_ref, reason, details_json, created_at
            FROM operator_events
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    payload: list[dict[str, Any]] = []
    for row in rows:
        details = None
        if row["details_json"]:
            try:
                details = json.loads(row["details_json"])
            except json.JSONDecodeError:
                details = {"raw": row["details_json"]}
        payload.append(
            {
                "event_id": row["event_id"],
                "actor_human_id": row["actor_human_id"],
                "action": row["action"],
                "target_kind": row["target_kind"],
                "target_ref": row["target_ref"],
                "reason": row["reason"],
                "details": details,
                "created_at": row["created_at"],
            }
        )
    return OperatorEventReport(rows=payload)


def build_operator_inbox(*, config_manager: ConfigManager, state_db: StateDB) -> OperatorInboxReport:
    pairing_rows = review_pairings(state_db).rows
    pending_pairings = [row for row in pairing_rows if row.get("status") == "pending"]
    held_pairings = [row for row in pairing_rows if row.get("status") == "held"]
    channel_alerts = _load_channel_alerts(state_db)
    bridge_alerts = _build_bridge_alerts(config_manager=config_manager, state_db=state_db)
    items = _build_inbox_items(
        pending_pairings=pending_pairings,
        held_pairings=held_pairings,
        channel_alerts=channel_alerts,
        bridge_alerts=bridge_alerts,
    )

    payload = {
        "counts": {
            "pending_pairings": len(pending_pairings),
            "held_pairings": len(held_pairings),
            "channel_alerts": len(channel_alerts),
            "bridge_alerts": len(bridge_alerts),
            "total": len(pending_pairings) + len(held_pairings) + len(channel_alerts) + len(bridge_alerts),
        },
        "pairings": {
            "pending": pending_pairings,
            "held": held_pairings,
        },
        "channels": channel_alerts,
        "bridges": bridge_alerts,
        "items": items,
    }
    return OperatorInboxReport(payload=payload)


def _load_channel_alerts(state_db: StateDB) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT channel_id, channel_kind, status, pairing_mode, updated_at
            FROM channel_installations
            WHERE status IN ('paused', 'disabled')
            ORDER BY
                CASE status WHEN 'paused' THEN 0 WHEN 'disabled' THEN 1 ELSE 2 END,
                updated_at DESC,
                channel_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _build_bridge_alerts(*, config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    if not researcher.enabled:
        alerts.append(
            {
                "bridge": "researcher",
                "severity": "info",
                "status": "disabled",
                "summary": "Spark Researcher bridge is disabled by operator.",
            }
        )
    elif not researcher.available:
        alerts.append(
            {
                "bridge": "researcher",
                "severity": "warning",
                "status": researcher.mode,
                "summary": "Spark Researcher bridge is enabled but not ready.",
            }
        )
    if researcher.last_mode == "bridge_error":
        alerts.append(
            {
                "bridge": "researcher",
                "severity": "critical",
                "status": "bridge_error",
                "summary": "The last Spark Researcher bridge call failed closed.",
            }
        )

    swarm = swarm_status(config_manager, state_db)
    if not swarm.enabled:
        alerts.append(
            {
                "bridge": "swarm",
                "severity": "info",
                "status": "disabled",
                "summary": "Spark Swarm bridge is disabled by operator.",
            }
        )
    elif not swarm.payload_ready:
        alerts.append(
            {
                "bridge": "swarm",
                "severity": "warning",
                "status": "not_ready",
                "summary": "Spark Swarm bridge cannot build a usable payload yet.",
            }
        )

    last_sync = swarm.last_sync or {}
    last_sync_mode = str(last_sync.get("mode") or "")
    if last_sync_mode in {"http_error", "network_error", "api_not_configured"}:
        alerts.append(
            {
                "bridge": "swarm",
                "severity": "critical" if last_sync_mode in {"http_error", "network_error"} else "warning",
                "status": last_sync_mode,
                "summary": "The last Spark Swarm sync did not complete successfully.",
            }
        )
    elif last_sync and last_sync.get("accepted") is False:
        alerts.append(
            {
                "bridge": "swarm",
                "severity": "warning",
                "status": last_sync_mode or "rejected",
                "summary": "The last Spark Swarm sync was uploaded but not accepted.",
            }
        )

    last_decision = swarm.last_decision or {}
    last_decision_mode = str(last_decision.get("mode") or "")
    if last_decision_mode == "unavailable":
        alerts.append(
            {
                "bridge": "swarm",
                "severity": "warning",
                "status": "unavailable",
                "summary": "The last Spark Swarm escalation evaluation reported Swarm unavailable.",
            }
        )

    return alerts


def _build_inbox_items(
    *,
    pending_pairings: list[dict[str, Any]],
    held_pairings: list[dict[str, Any]],
    channel_alerts: list[dict[str, Any]],
    bridge_alerts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for row in pending_pairings:
        channel_id = str(row["channel_id"])
        external_user_id = str(row["external_user_id"])
        items.append(
            {
                "kind": "pairing",
                "status": "pending",
                "priority": "high",
                "sort_order": 20,
                "item_ref": f"{channel_id}:{external_user_id}",
                "summary": f"Pending pairing for {channel_id}:{external_user_id}.",
                "recommended_command": f"spark-intelligence operator approve-pairing {channel_id} {external_user_id}",
            }
        )

    for row in held_pairings:
        channel_id = str(row["channel_id"])
        external_user_id = str(row["external_user_id"])
        items.append(
            {
                "kind": "pairing",
                "status": "held",
                "priority": "medium",
                "sort_order": 30,
                "item_ref": f"{channel_id}:{external_user_id}",
                "summary": f"Held pairing for {channel_id}:{external_user_id}.",
                "recommended_command": f"spark-intelligence operator approve-pairing {channel_id} {external_user_id}",
            }
        )

    for row in channel_alerts:
        channel_id = str(row["channel_id"])
        status = str(row["status"])
        items.append(
            {
                "kind": "channel",
                "status": status,
                "priority": "medium" if status == "paused" else "high",
                "sort_order": 35 if status == "paused" else 25,
                "item_ref": channel_id,
                "summary": f"Channel {channel_id} is {status}.",
                "recommended_command": f"spark-intelligence operator set-channel {channel_id} enabled",
            }
        )

    severity_order = {"critical": 10, "warning": 15, "info": 40}
    for row in bridge_alerts:
        bridge = str(row["bridge"])
        status = str(row["status"])
        severity = str(row["severity"])
        enable_mode = "enabled"
        recommended_command = f"spark-intelligence operator set-bridge {bridge} {enable_mode}"
        if bridge == "swarm" and status in {"http_error", "network_error", "api_not_configured"}:
            recommended_command = "spark-intelligence swarm status"
        elif bridge == "researcher" and status == "bridge_error":
            recommended_command = "spark-intelligence researcher status"
        items.append(
            {
                "kind": "bridge",
                "status": status,
                "priority": severity,
                "sort_order": severity_order.get(severity, 50),
                "item_ref": bridge,
                "summary": row["summary"],
                "recommended_command": recommended_command,
            }
        )

    items.sort(key=lambda item: (int(item["sort_order"]), str(item["item_ref"])))
    for item in items:
        item.pop("sort_order", None)
    return items
