from __future__ import annotations

import json
import time
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.policy import looks_secret_like
from spark_intelligence.observability.store import record_event, record_policy_gate_block, record_quarantine
from spark_intelligence.state.db import StateDB


def load_channel_security_policy(
    config_manager: ConfigManager,
    *,
    channel_id: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    configured = config_manager.get_path(f"security.{channel_id}", default={}) or {}
    policy: dict[str, Any] = {}
    for key, default_value in defaults.items():
        value = configured.get(key, default_value)
        if isinstance(default_value, bool):
            policy[key] = bool(value)
        elif isinstance(default_value, int):
            policy[key] = int(value)
        else:
            policy[key] = value
    return policy


def is_duplicate_event(
    *,
    state_db: StateDB,
    channel_id: str,
    event_id: int,
    window_size: int,
) -> bool:
    state_key = f"{channel_id}:recent_event_ids"
    recent_ids = _load_json_list(state_db=state_db, state_key=state_key)
    if event_id in recent_ids:
        return True
    trimmed = (recent_ids + [event_id])[-max(window_size, 1) :]
    set_runtime_state_value(state_db=state_db, state_key=state_key, value=json.dumps(trimmed))
    return False


def apply_inbound_rate_limit(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    limit_per_minute: int,
    notice_cooldown_seconds: int,
) -> dict[str, Any]:
    state_key = f"{channel_id}:rate_limit:{external_user_id}"
    raw = _load_json_object(state_db=state_db, state_key=state_key)
    now = int(time.time())
    timestamps = [int(item) for item in raw.get("timestamps", []) if isinstance(item, (int, float))]
    timestamps = [item for item in timestamps if item > now - 60]
    last_notice_at = int(raw.get("last_notice_at", 0) or 0)
    if len(timestamps) >= max(limit_per_minute, 1):
        retry_after_seconds = max(1, 60 - (now - timestamps[0]))
        notice_allowed = now - last_notice_at >= max(notice_cooldown_seconds, 1)
        if notice_allowed:
            last_notice_at = now
        set_runtime_state_value(
            state_db=state_db,
            state_key=state_key,
            value=json.dumps({"timestamps": timestamps, "last_notice_at": last_notice_at}, sort_keys=True),
        )
        return {
            "allowed": False,
            "retry_after_seconds": retry_after_seconds,
            "notice_allowed": notice_allowed,
        }
    timestamps.append(now)
    set_runtime_state_value(
        state_db=state_db,
        state_key=state_key,
        value=json.dumps({"timestamps": timestamps, "last_notice_at": last_notice_at}, sort_keys=True),
    )
    return {"allowed": True, "retry_after_seconds": 0, "notice_allowed": False}


def prepare_outbound_text(
    *,
    config_manager: ConfigManager | None = None,
    state_db: StateDB | None = None,
    text: str,
    bridge_mode: str | None,
    max_reply_chars: int,
    redact_secret_like_replies: bool,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    channel_id: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    actions: list[str] = []
    cleaned = "".join(character for character in text if character == "\n" or character == "\t" or ord(character) >= 32)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").strip()
    if cleaned != text:
        actions.append("sanitize_control_chars")
    if bridge_mode == "bridge_error":
        cleaned = "Spark Intelligence hit an internal bridge error. The operator can inspect local gateway traces."
        actions.append("replace_bridge_error")
    if redact_secret_like_replies and looks_secret_like(cleaned):
        if state_db is not None:
            event_id = record_event(
                state_db,
                event_type="secret_boundary_violation",
                component="outbound_guardrails",
                summary="Secret-like material was blocked before outbound delivery.",
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                actor_id=actor_id,
                reason_code="outbound_secret_like_reply",
                severity="high",
                facts={"channel_id": channel_id, "blocked_stage": "delivery"},
                provenance={"source_kind": "outbound_text"},
            )
            quarantine_id = record_quarantine(
                state_db,
                event_id=event_id,
                run_id=run_id,
                request_id=request_id,
                source_kind="outbound_text",
                source_ref=channel_id,
                policy_domain="outbound_guardrails",
                reason_code="outbound_secret_like_reply",
                summary="Outbound delivery content was quarantined after secret-like detection.",
                payload_preview=cleaned[:160],
                provenance={"trace_ref": trace_ref, "session_id": session_id, "channel_id": channel_id},
            )
            record_policy_gate_block(
                state_db,
                component="outbound_guardrails",
                policy_domain="outbound_guardrails",
                gate_name="secret_boundary",
                source_kind="outbound_text",
                source_ref=channel_id,
                summary="Outbound delivery content was blocked by the secret boundary.",
                action="quarantine_blocked",
                reason_code="outbound_secret_like_reply",
                blocked_stage="delivery",
                input_ref=str(request_id or trace_ref or channel_id or ""),
                output_ref=quarantine_id,
                severity="high",
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_id,
                session_id=session_id,
                actor_id=actor_id,
                provenance={"source_kind": "outbound_text"},
                facts={"secret_event_id": event_id},
            )
        cleaned = "Spark Intelligence withheld this reply because it appeared to contain sensitive credential material. The operator can inspect local traces."
        actions.append("block_secret_like_reply")
    if len(cleaned) > max(max_reply_chars, 32):
        cleaned = f"{cleaned[: max(max_reply_chars, 32) - 28].rstrip()}\n\n[truncated for delivery]"
        actions.append("truncate_reply")
    if not cleaned:
        cleaned = "Spark Intelligence produced an empty reply."
        actions.append("replace_empty_reply")
    return {"text": cleaned, "actions": actions}
def set_runtime_state_value(*, state_db: StateDB, state_key: str, value: str) -> None:
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_state(state_key, value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (state_key, value),
        )
        conn.commit()


def _load_json_list(*, state_db: StateDB, state_key: str) -> list[int]:
    with state_db.connect() as conn:
        row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return []
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [int(item) for item in payload if isinstance(item, (int, float))]


def _load_json_object(*, state_db: StateDB, state_key: str) -> dict[str, Any]:
    with state_db.connect() as conn:
        row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return {}
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
