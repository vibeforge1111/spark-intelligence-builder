from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class RecentConversationTurn:
    role: str
    text: str


def load_recent_conversation_turns(
    *,
    state_db: StateDB,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    turn_limit: int = 3,
    config_manager: ConfigManager | None = None,
    current_user_message: str = "",
) -> list[RecentConversationTurn]:
    if not session_id or not channel_kind or turn_limit <= 0:
        return []

    transcript = _load_builder_event_turns(
        state_db=state_db,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        turn_limit=turn_limit,
    )
    if transcript:
        return transcript[-(turn_limit * 2) :]

    if config_manager is None:
        return []
    return _load_gateway_log_turns(
        config_manager=config_manager,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        turn_limit=turn_limit,
        current_user_message=current_user_message,
    )


def _load_builder_event_turns(
    *,
    state_db: StateDB,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    turn_limit: int,
) -> list[RecentConversationTurn]:
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, request_id, facts_json
                FROM builder_events
                WHERE component = 'telegram_runtime'
                  AND channel_id = ?
                  AND session_id = ?
                  AND (
                        event_type = 'intent_committed'
                     OR (event_type = 'delivery_succeeded' AND reason_code = 'telegram_bridge_outbound')
                  )
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (channel_kind, session_id, max(turn_limit * 4, 12)),
            ).fetchall()
    except Exception:
        return []

    transcript: list[RecentConversationTurn] = []
    for row in reversed(rows):
        if request_id and str(row["request_id"] or "") == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except json.JSONDecodeError:
            facts = {}
        event_type = str(row["event_type"] or "")
        if event_type == "intent_committed":
            text = str(facts.get("message_text") or "").strip()
            if text:
                transcript.append(RecentConversationTurn("user", text))
        elif event_type == "delivery_succeeded":
            text = str(facts.get("delivered_text") or "").strip()
            if text:
                transcript.append(RecentConversationTurn("assistant", text))
    return transcript


@dataclass(frozen=True)
class _GatewayTurnRecord:
    sort_key: tuple[str, int]
    request_id: str
    user_text: str
    assistant_text: str


def _load_gateway_log_turns(
    *,
    config_manager: ConfigManager,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    turn_limit: int,
    current_user_message: str,
) -> list[RecentConversationTurn]:
    records = _gateway_turn_records(
        config_manager=config_manager,
        session_id=session_id,
        channel_kind=channel_kind,
        limit=max(turn_limit * 24, 120),
    )
    if not records:
        return []

    current_index = _current_gateway_record_index(
        records=records,
        request_id=request_id,
        current_user_message=current_user_message,
    )
    if current_index is not None:
        records = records[:current_index]
    elif request_id:
        records = [record for record in records if record.request_id != request_id]

    transcript: list[RecentConversationTurn] = []
    seen_pairs: set[tuple[str, str]] = set()
    for record in records:
        pair_key = (record.user_text, record.assistant_text)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        if record.user_text:
            transcript.append(RecentConversationTurn("user", record.user_text))
        if record.assistant_text:
            transcript.append(RecentConversationTurn("assistant", record.assistant_text))
    return transcript[-(turn_limit * 2) :]


def _gateway_turn_records(
    *,
    config_manager: ConfigManager,
    session_id: str,
    channel_kind: str,
    limit: int,
) -> list[_GatewayTurnRecord]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for row in _read_jsonl_tail(config_manager.paths.logs_dir / "gateway-trace.jsonl", limit=limit):
        rows.append((len(rows), row))
    for row in _read_jsonl_tail(config_manager.paths.logs_dir / "gateway-outbound.jsonl", limit=limit):
        rows.append((len(rows), row))

    records: list[_GatewayTurnRecord] = []
    for order, row in rows:
        if str(row.get("channel_id") or "").strip() != channel_kind:
            continue
        if str(row.get("session_id") or "").strip() != session_id:
            continue
        event = str(row.get("event") or "").strip()
        if event not in {"telegram_update_processed", "telegram_bridge_outbound"}:
            continue
        user_text = str(row.get("user_message_preview") or row.get("message_text") or "").strip()
        assistant_text = str(row.get("response_preview") or row.get("delivered_text") or "").strip()
        if not user_text and not assistant_text:
            continue
        records.append(
            _GatewayTurnRecord(
                sort_key=(_sortable_timestamp(row.get("recorded_at")), order),
                request_id=str(row.get("request_id") or "").strip(),
                user_text=user_text,
                assistant_text=assistant_text,
            )
        )
    return sorted(records, key=lambda record: record.sort_key)


def _current_gateway_record_index(
    *,
    records: list[_GatewayTurnRecord],
    request_id: str | None,
    current_user_message: str,
) -> int | None:
    normalized_request_id = str(request_id or "").strip()
    if normalized_request_id:
        for index, record in enumerate(records):
            if record.request_id == normalized_request_id:
                return index

    current_preview = _compact_preview(current_user_message)
    if not current_preview:
        return None
    for index in range(len(records) - 1, -1, -1):
        if _preview_matches_current(records[index].user_text, current_preview):
            return index
    return None


def _preview_matches_current(candidate: str, current_preview: str) -> bool:
    candidate_preview = _compact_preview(candidate)
    if not candidate_preview:
        return False
    if candidate_preview == current_preview:
        return True
    if candidate_preview.endswith("..."):
        return current_preview.startswith(candidate_preview[:-3].rstrip())
    return False


def _compact_preview(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _sortable_timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw


def _read_jsonl_tail(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    selected = lines[-limit:] if limit > 0 else lines
    records: list[dict[str, Any]] = []
    for line in selected:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records
