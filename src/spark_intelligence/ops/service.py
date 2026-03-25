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

        pending_pairings = self.payload["pairings"]["pending"]
        if pending_pairings:
            lines.append("- pending pairings:")
            for row in pending_pairings:
                lines.append(
                    f"  {row['channel_id']}:{row['external_user_id']} "
                    f"updated_at={row['updated_at']}"
                )

        held_pairings = self.payload["pairings"]["held"]
        if held_pairings:
            lines.append("- held pairings:")
            for row in held_pairings:
                lines.append(
                    f"  {row['channel_id']}:{row['external_user_id']} "
                    f"updated_at={row['updated_at']}"
                )

        channel_alerts = self.payload["channels"]
        if channel_alerts:
            lines.append("- channel alerts:")
            for row in channel_alerts:
                lines.append(
                    f"  {row['channel_id']} status={row['status']} "
                    f"pairing_mode={row['pairing_mode']} updated_at={row['updated_at']}"
                )

        bridge_alerts = self.payload["bridges"]
        if bridge_alerts:
            lines.append("- bridge alerts:")
            for row in bridge_alerts:
                lines.append(
                    f"  {row['bridge']} severity={row['severity']} "
                    f"status={row['status']} summary={row['summary']}"
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
