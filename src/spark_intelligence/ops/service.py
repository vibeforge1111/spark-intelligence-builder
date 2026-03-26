from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from spark_intelligence.adapters.telegram.runtime import read_telegram_runtime_health
from spark_intelligence.auth.runtime import build_auth_status_report
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.tracing import read_gateway_traces, read_outbound_audit
from spark_intelligence.identity.service import LOCAL_OPERATOR_HUMAN_ID
from spark_intelligence.identity.service import review_pairings
from spark_intelligence.researcher_bridge import researcher_bridge_status
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import swarm_status

WEBHOOK_ALERT_SUSTAINED_THRESHOLD = 3
WEBHOOK_ALERT_RECENT_WINDOW = timedelta(minutes=15)
WEBHOOK_ALERT_EVENT_SPECS = {
    "discord_webhook_auth_failed": {
        "status": "auth_failed",
        "summary_prefix": "Discord webhook auth rejected",
        "recommended_command": "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
    },
    "whatsapp_webhook_auth_failed": {
        "status": "auth_failed",
        "summary_prefix": "WhatsApp webhook auth rejected",
        "recommended_command": "spark-intelligence gateway traces --event whatsapp_webhook_auth_failed --limit 20",
    },
    "whatsapp_webhook_verification_failed": {
        "status": "verification_failed",
        "summary_prefix": "WhatsApp webhook verification rejected",
        "recommended_command": "spark-intelligence gateway traces --event whatsapp_webhook_verification_failed --limit 20",
    },
}


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
            f"bridge_alerts={counts['bridge_alerts']} "
            f"auth_alerts={counts['auth_alerts']} "
            f"webhook_alerts={counts['webhook_alerts']}"
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


@dataclass
class OperatorSecurityReport:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        counts = self.payload["counts"]
        lines = ["Operator security summary:"]
        lines.append(
            "- counts: "
            f"bridge_alerts={counts['bridge_alerts']} "
            f"channel_alerts={counts['channel_alerts']} "
            f"auth_alerts={counts['auth_alerts']} "
            f"webhook_alerts={counts['webhook_alerts']} "
            f"duplicates={counts['duplicate_updates']} "
            f"rate_limited={counts['rate_limited_updates']} "
            f"delivery_failures={counts['delivery_failures']} "
            f"guardrail_hits={counts['guardrail_hits']}"
        )
        items = self.payload.get("items") or []
        if not items:
            lines.append("- status: no recent security actions required")
            return "\n".join(lines)
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


def list_operator_events(
    state_db: StateDB,
    *,
    limit: int = 20,
    action: str | None = None,
    target_kind: str | None = None,
    contains: str | None = None,
) -> OperatorEventReport:
    filters: list[str] = []
    params: list[Any] = []
    if action:
        filters.append("action = ?")
        params.append(action)
    if target_kind:
        filters.append("target_kind = ?")
        params.append(target_kind)
    if contains:
        needle = f"%{contains.lower()}%"
        filters.append(
            "("
            "LOWER(target_ref) LIKE ? OR "
            "LOWER(COALESCE(reason, '')) LIKE ? OR "
            "LOWER(COALESCE(details_json, '')) LIKE ?"
            ")"
        )
        params.extend([needle, needle, needle])
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_id, actor_human_id, action, target_kind, target_ref, reason, details_json, created_at
            FROM operator_events
            {where_clause}
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (*params, limit),
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


def list_webhook_alert_events() -> tuple[str, ...]:
    return tuple(WEBHOOK_ALERT_EVENT_SPECS.keys())


def snooze_webhook_alert(*, state_db: StateDB, event_name: str, minutes: int) -> str:
    if event_name not in WEBHOOK_ALERT_EVENT_SPECS:
        raise ValueError(f"Unsupported webhook alert event: {event_name}")
    if minutes <= 0:
        raise ValueError("Webhook alert snooze minutes must be greater than zero.")
    snooze_until = _utc_now() + timedelta(minutes=minutes)
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_state(state_key, value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                value=excluded.value,
                updated_at=CURRENT_TIMESTAMP
            """,
            (_webhook_alert_snooze_state_key(event_name), snooze_until.isoformat(timespec="seconds")),
        )
        conn.commit()
    return snooze_until.isoformat(timespec="seconds")


def build_operator_inbox(*, config_manager: ConfigManager, state_db: StateDB) -> OperatorInboxReport:
    pairing_rows = review_pairings(state_db).rows
    pending_pairings = [row for row in pairing_rows if row.get("status") == "pending"]
    held_pairings = [row for row in pairing_rows if row.get("status") == "held"]
    channel_alerts = _load_channel_alerts(config_manager=config_manager, state_db=state_db)
    bridge_alerts = _build_bridge_alerts(config_manager=config_manager, state_db=state_db)
    auth_alerts = _build_auth_alerts(config_manager=config_manager, state_db=state_db)
    webhook_alerts = _build_webhook_alerts(traces=read_gateway_traces(config_manager, limit=100), state_db=state_db)
    items = _build_inbox_items(
        pending_pairings=pending_pairings,
        held_pairings=held_pairings,
        channel_alerts=channel_alerts,
        bridge_alerts=bridge_alerts,
        auth_alerts=auth_alerts,
        webhook_alerts=webhook_alerts,
    )

    payload = {
        "counts": {
            "pending_pairings": len(pending_pairings),
            "held_pairings": len(held_pairings),
            "channel_alerts": len(channel_alerts),
            "bridge_alerts": len(bridge_alerts),
            "auth_alerts": len(auth_alerts),
            "webhook_alerts": len(webhook_alerts),
            "total": (
                len(pending_pairings)
                + len(held_pairings)
                + len(channel_alerts)
                + len(bridge_alerts)
                + len(auth_alerts)
                + len(webhook_alerts)
            ),
        },
        "pairings": {
            "pending": pending_pairings,
            "held": held_pairings,
        },
        "channels": channel_alerts,
        "bridges": bridge_alerts,
        "auth": auth_alerts,
        "webhooks": webhook_alerts,
        "items": items,
    }
    return OperatorInboxReport(payload=payload)


def build_operator_security_report(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    limit: int = 100,
) -> OperatorSecurityReport:
    channel_alerts = _load_channel_alerts(config_manager=config_manager, state_db=state_db)
    bridge_alerts = _build_bridge_alerts(config_manager=config_manager, state_db=state_db)
    auth_alerts = _build_auth_alerts(config_manager=config_manager, state_db=state_db)
    traces = read_gateway_traces(config_manager, limit=limit)
    outbound = read_outbound_audit(config_manager, limit=limit)
    webhook_alerts = _build_webhook_alerts(traces=traces, state_db=state_db)

    duplicate_updates = [trace for trace in traces if trace.get("event") == "telegram_update_duplicate"]
    rate_limited_updates = [trace for trace in traces if trace.get("event") == "telegram_rate_limited"]
    delivery_failures = [record for record in outbound if record.get("delivery_ok") is False]
    guardrail_hits = [record for record in outbound if record.get("guardrail_actions")]
    secret_reply_blocks = [
        record for record in outbound if "block_secret_like_reply" in (record.get("guardrail_actions") or [])
    ]
    truncated_replies = [
        record for record in outbound if "truncate_reply" in (record.get("guardrail_actions") or [])
    ]

    items = _build_security_items(
        bridge_alerts=bridge_alerts,
        channel_alerts=channel_alerts,
        auth_alerts=auth_alerts,
        webhook_alerts=webhook_alerts,
        duplicate_updates=duplicate_updates,
        rate_limited_updates=rate_limited_updates,
        delivery_failures=delivery_failures,
        secret_reply_blocks=secret_reply_blocks,
        truncated_replies=truncated_replies,
    )
    payload = {
        "counts": {
            "bridge_alerts": len(bridge_alerts),
            "channel_alerts": len(channel_alerts),
            "auth_alerts": len(auth_alerts),
            "webhook_alerts": len(webhook_alerts),
            "duplicate_updates": len(duplicate_updates),
            "rate_limited_updates": len(rate_limited_updates),
            "delivery_failures": len(delivery_failures),
            "guardrail_hits": len(guardrail_hits),
            "secret_reply_blocks": len(secret_reply_blocks),
            "truncated_replies": len(truncated_replies),
        },
        "bridge_alerts": bridge_alerts,
        "channel_alerts": channel_alerts,
        "auth_alerts": auth_alerts,
        "webhook_alerts": webhook_alerts,
        "recent": {
            "duplicates": duplicate_updates,
            "rate_limited": rate_limited_updates,
            "delivery_failures": delivery_failures,
            "guardrail_hits": guardrail_hits,
            "webhook_rejections": webhook_alerts,
        },
        "items": items,
        "log_limit": limit,
    }
    return OperatorSecurityReport(payload=payload)


def _load_channel_alerts(*, config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
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
    alerts = [dict(row) for row in rows]
    telegram_health = read_telegram_runtime_health(state_db)
    if telegram_health.auth_status in {"failed", "missing"}:
        alerts.append(
            {
                "channel_id": "telegram",
                "channel_kind": "telegram",
                "status": f"auth_{telegram_health.auth_status}",
                "pairing_mode": None,
                "updated_at": telegram_health.auth_checked_at,
                "summary": (
                    f"Telegram auth status is {telegram_health.auth_status}; "
                    f"last error: {telegram_health.auth_error or 'unknown'}."
                ),
                "recommended_command": "spark-intelligence channel telegram-onboard --bot-token <token>",
            }
        )
    if telegram_health.consecutive_failures > 0 and telegram_health.last_failure_at:
        alerts.append(
            {
                "channel_id": "telegram",
                "channel_kind": "telegram",
                "status": "poll_failure",
                "pairing_mode": None,
                "updated_at": telegram_health.last_failure_at,
                "summary": (
                    f"Telegram polling has {telegram_health.consecutive_failures} consecutive failure(s); "
                    f"last type={telegram_health.last_failure_type or 'unknown'} "
                    f"backoff={telegram_health.last_backoff_seconds}s."
                ),
                "recommended_command": "spark-intelligence gateway traces --limit 20",
            }
        )
    return alerts


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
    if researcher.last_failure:
        alerts.append(
            {
                "bridge": "researcher",
                "severity": "warning",
                "status": str(researcher.last_failure.get("mode") or "failure_recorded"),
                "summary": (
                    f"Spark Researcher has recorded {researcher.failure_count} failure(s); "
                    f"last failure at {researcher.last_failure.get('recorded_at') or 'unknown time'}."
                ),
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
    if swarm.last_failure:
        alerts.append(
            {
                "bridge": "swarm",
                "severity": "warning",
                "status": str(swarm.last_failure.get("mode") or "failure_recorded"),
                "summary": (
                    f"Spark Swarm has recorded {swarm.failure_count} failure(s); "
                    f"last failure at {swarm.last_failure.get('recorded_at') or 'unknown time'}."
                ),
            }
        )

    return alerts


def _build_inbox_items(
    *,
    pending_pairings: list[dict[str, Any]],
    held_pairings: list[dict[str, Any]],
    channel_alerts: list[dict[str, Any]],
    bridge_alerts: list[dict[str, Any]],
    auth_alerts: list[dict[str, Any]],
    webhook_alerts: list[dict[str, Any]],
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
        summary = str(row.get("summary") or f"Channel {channel_id} is {status}.")
        items.append(
            {
                "kind": "channel",
                "status": status,
                "priority": "medium" if status in {"paused", "poll_failure"} else "high",
                "sort_order": 35 if status in {"paused", "poll_failure"} else 25,
                "item_ref": channel_id,
                "summary": summary,
                "recommended_command": str(row.get("recommended_command") or f"spark-intelligence operator set-channel {channel_id} enabled"),
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

    auth_priority = {"critical": "high", "warning": "medium", "info": "info"}
    auth_sort_order = {"critical": 18, "warning": 28, "info": 42}
    for row in auth_alerts:
        severity = str(row["severity"])
        items.append(
            {
                "kind": "auth",
                "status": str(row["status"]),
                "priority": auth_priority.get(severity, "medium"),
                "sort_order": auth_sort_order.get(severity, 45),
                "item_ref": str(row["provider_id"]),
                "summary": str(row["summary"]),
                "recommended_command": str(row["recommended_command"]),
            }
        )

    webhook_priority = {"critical": "high", "warning": "medium", "info": "info"}
    webhook_sort_order = {"critical": 22, "warning": 32, "info": 44}
    for row in webhook_alerts:
        severity = str(row["severity"])
        items.append(
            {
                "kind": "webhook",
                "status": str(row["status"]),
                "priority": webhook_priority.get(severity, "medium"),
                "sort_order": webhook_sort_order.get(severity, 46),
                "item_ref": str(row["event"]),
                "summary": str(row["summary"]),
                "recommended_command": str(row["recommended_command"]),
            }
        )

    items.sort(key=lambda item: (int(item["sort_order"]), str(item["item_ref"])))
    for item in items:
        item.pop("sort_order", None)
    return items


def _build_security_items(
    *,
    bridge_alerts: list[dict[str, Any]],
    channel_alerts: list[dict[str, Any]],
    auth_alerts: list[dict[str, Any]],
    webhook_alerts: list[dict[str, Any]],
    duplicate_updates: list[dict[str, Any]],
    rate_limited_updates: list[dict[str, Any]],
    delivery_failures: list[dict[str, Any]],
    secret_reply_blocks: list[dict[str, Any]],
    truncated_replies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    severity_order = {"critical": 10, "high": 20, "warning": 30, "medium": 40, "info": 50}

    for row in bridge_alerts:
        bridge = str(row["bridge"])
        severity = str(row["severity"])
        status = str(row["status"])
        recommended_command = f"spark-intelligence operator set-bridge {bridge} enabled"
        if bridge == "researcher" and status in {"bridge_error", "failure_recorded"}:
            recommended_command = "spark-intelligence researcher status"
        if bridge == "researcher" and severity == "warning":
            recommended_command = "spark-intelligence researcher status"
        if bridge == "swarm" and status in {"http_error", "network_error", "api_not_configured", "unavailable", "workspace_id_missing", "researcher_missing", "failure_recorded"}:
            recommended_command = "spark-intelligence swarm status"
        if bridge == "swarm" and severity == "warning":
            recommended_command = "spark-intelligence swarm status"
        items.append(
            {
                "priority": "high" if severity == "critical" else severity,
                "sort_order": severity_order.get("high" if severity == "critical" else severity, 60),
                "summary": row["summary"],
                "recommended_command": recommended_command,
            }
        )

    for row in channel_alerts:
        channel_id = str(row["channel_id"])
        status = str(row["status"])
        summary = str(row.get("summary") or f"Channel {channel_id} remains {status}; ingress is constrained.")
        items.append(
            {
                "priority": "medium" if status in {"paused", "poll_failure"} else "high",
                "sort_order": severity_order.get("medium" if status in {"paused", "poll_failure"} else "high", 60),
                "summary": summary,
                "recommended_command": str(row.get("recommended_command") or f"spark-intelligence operator set-channel {channel_id} enabled"),
            }
        )

    for row in auth_alerts:
        severity = str(row["severity"])
        items.append(
            {
                "priority": "high" if severity == "critical" else ("medium" if severity == "warning" else "info"),
                "sort_order": severity_order.get("high" if severity == "critical" else ("medium" if severity == "warning" else "info"), 60),
                "summary": str(row["summary"]),
                "recommended_command": str(row["recommended_command"]),
            }
        )

    for row in webhook_alerts:
        severity = str(row["severity"])
        items.append(
            {
                "priority": "high" if severity == "critical" else ("medium" if severity == "warning" else "info"),
                "sort_order": severity_order.get(
                    "high" if severity == "critical" else ("medium" if severity == "warning" else "info"),
                    60,
                ),
                "summary": str(row["summary"]),
                "recommended_command": str(row["recommended_command"]),
            }
        )

    if delivery_failures:
        latest = delivery_failures[-1]
        items.append(
            {
                "priority": "high",
                "sort_order": severity_order["high"],
                "summary": f"{len(delivery_failures)} outbound delivery attempt(s) failed in recent logs.",
                "recommended_command": "spark-intelligence gateway outbound --limit 20",
            }
        )

    if rate_limited_updates:
        latest = rate_limited_updates[-1]
        user_ref = latest.get("telegram_user_id", "unknown")
        items.append(
            {
                "priority": "medium",
                "sort_order": severity_order["medium"],
                "summary": f"Recent rate limiting triggered for Telegram user {user_ref}.",
                "recommended_command": "spark-intelligence gateway traces --limit 20",
            }
        )

    if duplicate_updates:
        items.append(
            {
                "priority": "info",
                "sort_order": severity_order["info"],
                "summary": f"Recent duplicate Telegram update suppression count: {len(duplicate_updates)}.",
                "recommended_command": "spark-intelligence gateway traces --limit 20",
            }
        )

    if secret_reply_blocks:
        items.append(
            {
                "priority": "high",
                "sort_order": severity_order["high"],
                "summary": f"Secret-like outbound reply blocking triggered {len(secret_reply_blocks)} time(s).",
                "recommended_command": "spark-intelligence gateway outbound --limit 20",
            }
        )

    if truncated_replies:
        items.append(
            {
                "priority": "medium",
                "sort_order": severity_order["medium"],
                "summary": f"Oversized outbound replies were truncated {len(truncated_replies)} time(s).",
                "recommended_command": "spark-intelligence gateway outbound --limit 20",
            }
        )

    items.sort(key=lambda item: (int(item["sort_order"]), item["summary"]))
    for item in items:
        item.pop("sort_order", None)
    return items


def _build_auth_alerts(*, config_manager: ConfigManager, state_db: StateDB) -> list[dict[str, Any]]:
    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    alerts: list[dict[str, Any]] = []

    for provider in auth_report.providers:
        if provider.status == "active" and provider.secret_present and not provider.last_refresh_error:
            continue

        if provider.auth_method == "oauth":
            if provider.status == "expired":
                summary = (
                    f"Provider {provider.provider_id} OAuth access token is expired. "
                    "Try a refresh first; if that fails, re-run OAuth login."
                )
                if provider.last_refresh_error:
                    summary += f" Last refresh error: {provider.last_refresh_error}."
                alerts.append(
                    {
                        "provider_id": provider.provider_id,
                        "status": "expired",
                        "severity": "critical",
                        "summary": summary,
                        "recommended_command": f"spark-intelligence auth refresh {provider.provider_id}",
                    }
                )
                continue

            if provider.status == "refresh_error":
                summary = f"Provider {provider.provider_id} recorded an OAuth refresh failure."
                if provider.last_refresh_error:
                    summary += f" Last refresh error: {provider.last_refresh_error}."
                alerts.append(
                    {
                        "provider_id": provider.provider_id,
                        "status": "refresh_error",
                        "severity": "warning",
                        "summary": summary,
                        "recommended_command": f"spark-intelligence auth refresh {provider.provider_id}",
                    }
                )
                continue

            if provider.status == "expiring_soon":
                alerts.append(
                    {
                        "provider_id": provider.provider_id,
                        "status": "expiring_soon",
                        "severity": "info",
                        "summary": (
                            f"Provider {provider.provider_id} OAuth access token expires soon. "
                            "Run scheduled maintenance now or refresh manually before runtime use degrades."
                        ),
                        "recommended_command": "spark-intelligence jobs tick",
                    }
                )
                continue

            if provider.status in {"revoked", "pending_oauth"} or not provider.secret_present:
                summary = (
                    f"Provider {provider.provider_id} needs OAuth login before runtime use."
                    if provider.status != "revoked"
                    else f"Provider {provider.provider_id} OAuth credentials were revoked and must be reconnected."
                )
                alerts.append(
                    {
                        "provider_id": provider.provider_id,
                        "status": provider.status,
                        "severity": "critical" if provider.status == "revoked" else "warning",
                        "summary": summary,
                        "recommended_command": f"spark-intelligence auth login {provider.provider_id} --listen",
                    }
                )
                continue

            if provider.last_refresh_error:
                alerts.append(
                    {
                        "provider_id": provider.provider_id,
                        "status": "refresh_error",
                        "severity": "warning",
                        "summary": (
                            f"Provider {provider.provider_id} is still usable but the last OAuth refresh failed. "
                            f"Last refresh error: {provider.last_refresh_error}."
                        ),
                        "recommended_command": f"spark-intelligence auth refresh {provider.provider_id}",
                    }
                )
                continue

        elif not provider.secret_present:
            ref_id = provider.secret_ref.ref_id if provider.secret_ref else "missing"
            alerts.append(
                {
                    "provider_id": provider.provider_id,
                    "status": provider.status,
                    "severity": "warning",
                    "summary": f"Provider {provider.provider_id} is missing its configured secret ref {ref_id}.",
                    "recommended_command": f"spark-intelligence auth connect {provider.provider_id} --api-key <key>",
                }
            )

    return alerts


def _build_webhook_alerts(*, traces: list[dict[str, Any]], state_db: StateDB) -> list[dict[str, Any]]:
    now = _utc_now()
    snoozed_events = _load_snoozed_webhook_events(state_db=state_db, now=now)
    alerts: list[dict[str, Any]] = []
    for event_name, spec in WEBHOOK_ALERT_EVENT_SPECS.items():
        if event_name in snoozed_events:
            continue
        matching = [
            trace
            for trace in traces
            if trace.get("event") == event_name and _webhook_trace_is_recent(trace, now=now)
        ]
        if not matching:
            continue
        latest = matching[-1]
        count = len(matching)
        latest_reason = str(latest.get("reason") or "unknown")
        latest_status_code = int(latest.get("status_code") or 0)
        sustained = count >= WEBHOOK_ALERT_SUSTAINED_THRESHOLD
        alerts.append(
            {
                "event": event_name,
                "status": "sustained_rejections" if sustained else spec["status"],
                "severity": "critical" if latest_status_code >= 500 or sustained else "warning",
                "count": count,
                "summary": (
                    f"{spec['summary_prefix']} {count} time(s); latest reason: {latest_reason}."
                    if not sustained
                    else f"Sustained {spec['summary_prefix'].lower()} detected: {count} recent rejection(s); latest reason: {latest_reason}."
                ),
                "recommended_command": spec["recommended_command"],
            }
        )
    return alerts


def _webhook_trace_is_recent(trace: dict[str, Any], *, now: datetime) -> bool:
    recorded_at = _parse_iso_datetime(trace.get("recorded_at"))
    if recorded_at is None:
        return False
    return now - recorded_at <= WEBHOOK_ALERT_RECENT_WINDOW


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_snoozed_webhook_events(*, state_db: StateDB, now: datetime) -> set[str]:
    state_keys = [_webhook_alert_snooze_state_key(event_name) for event_name in WEBHOOK_ALERT_EVENT_SPECS]
    if not state_keys:
        return set()
    placeholders = ",".join("?" for _ in state_keys)
    with state_db.connect() as conn:
        rows = conn.execute(
            f"SELECT state_key, value FROM runtime_state WHERE state_key IN ({placeholders})",
            tuple(state_keys),
        ).fetchall()
    snoozed: set[str] = set()
    for row in rows:
        snooze_until = _parse_iso_datetime(row["value"])
        if snooze_until is None or snooze_until < now:
            continue
        key = str(row["state_key"])
        if key.startswith("ops:webhook_alert_snooze:"):
            snoozed.add(key.removeprefix("ops:webhook_alert_snooze:"))
    return snoozed


def _webhook_alert_snooze_state_key(event_name: str) -> str:
    return f"ops:webhook_alert_snooze:{event_name}"
