from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB

logger = logging.getLogger(__name__)


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecentConversationTurn:
    role: str
    text: str
    recorded_at: str = ""


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

    if config_manager is not None:
        gateway_transcript = _load_gateway_log_turns(
            config_manager=config_manager,
            session_id=session_id,
            channel_kind=channel_kind,
            request_id=request_id,
            turn_limit=turn_limit,
            current_user_message=current_user_message,
        )
        if gateway_transcript:
            return gateway_transcript[-(turn_limit * 2) :]

    transcript = _load_builder_event_turns(
        state_db=state_db,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        turn_limit=turn_limit,
    )
    if transcript:
        return transcript[-(turn_limit * 2) :]

    return []


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
                SELECT event_type, request_id, created_at, facts_json
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
    except Exception as exc:
        _log.warning("Failed to load recent conversation turns from DB: %s", exc)
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
                transcript.append(RecentConversationTurn("user", text, str(row["created_at"] or "").strip()))
        elif event_type == "delivery_succeeded":
            text = str(facts.get("delivered_text") or "").strip()
            if text:
                transcript.append(RecentConversationTurn("assistant", text, str(row["created_at"] or "").strip()))
    return transcript


@dataclass(frozen=True)
class _GatewayTurnRecord:
    sort_key: tuple[str, int]
    request_id: str
    user_text: str
    assistant_text: str
    recorded_at: str


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
            transcript.append(RecentConversationTurn("user", record.user_text, record.recorded_at))
        if record.assistant_text:
            transcript.append(RecentConversationTurn("assistant", record.assistant_text, record.recorded_at))
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
        recorded_at = str(row.get("recorded_at") or "").strip()
        records.append(
            _GatewayTurnRecord(
                sort_key=(_sortable_timestamp(recorded_at), order),
                request_id=str(row.get("request_id") or "").strip(),
                user_text=user_text,
                assistant_text=assistant_text,
                recorded_at=recorded_at,
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
        return None

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
    skipped = 0
    first_error: str | None = None
    for line in selected:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            # Corrupted lines in the recent-conversation JSONL tail are
            # silently dropped today, so a partial-coverage capsule looks
            # identical to a healthy one. Aggregate the count + first
            # parse error so operators triaging a thin context window can
            # see the file actually has bad lines.
            skipped += 1
            if first_error is None:
                first_error = f"line {len(records) + skipped}: {exc}"
            continue
        if isinstance(payload, dict):
            records.append(payload)
    if skipped:
        logger.warning(
            "recent_conversation: dropped %d malformed JSONL line(s) from %s (first error %s); "
            "capsule recent-turn window is partial",
            skipped,
            path,
            first_error,
        )
    return records
