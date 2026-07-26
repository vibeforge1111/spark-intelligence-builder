from __future__ import annotations

import os
import re
from typing import Any

from spark_intelligence.intent_boundary import denies_intent, has_conversation_only_boundary
from spark_intelligence.security.spawner_endpoint import (
    classify_local_spawner_failure,
    request_local_spawner_json,
    resolve_local_spawner_endpoint,
)


_SPAWNER_URL = os.environ.get("SPAWNER_UI_URL") or "http://127.0.0.1:4174"


# Natural language for the mission board. Keep disjoint from schedule_list by
# anchoring on mission/board/running vocabulary specifically.
_BOARD_PATTERNS = (
    # "what's running right now" / "is anything running" / "what am I running"
    re.compile(r"\b(?:whats?|what\s+is|what\s+am\s+i|is\s+(?:anything|there))\b.{0,30}\brunning\b", re.IGNORECASE),
    # "show missions", "list missions", "show me the missions"
    re.compile(r"\b(?:show|list|display|see|view|get|tell\s+me)\b.{0,30}\b(?:missions?|board|kanban|live\s+tasks?)\b", re.IGNORECASE),
    # "mission status", "board status", "kanban"
    re.compile(r"\b(?:missions?|board|kanban)\b.{0,20}\b(?:status|list|right\s+now|active|live)\b", re.IGNORECASE),
    # "any live missions", "any running missions"
    re.compile(r"\b(?:any|are\s+there)\b.{0,20}\b(?:live|running|active)\b.{0,20}\b(?:missions?|tasks?|jobs?)\b", re.IGNORECASE),
    # Bare "missions?", "board", "kanban"
    re.compile(r"^\s*(?:my\s+)?(?:missions?|board|kanban)\s*\??\s*$", re.IGNORECASE),
)


def detect_board_intent(message: str) -> dict | None:
    text = str(message or "").strip()
    if not text:
        return None
    if has_conversation_only_boundary(text) or denies_intent(
        text,
        ("show board", "show missions", "open board", "open mission", "start", "run", "route"),
    ):
        return None
    for pat in _BOARD_PATTERNS:
        if pat.search(text):
            return {"action": "list"}
    return None


def fetch_board(spawner_url: str | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    board, _ = _fetch_board_with_error(spawner_url, timeout=timeout)
    return board


def _fetch_board_with_error(
    spawner_url: str | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[dict[str, Any], str | None]:
    try:
        endpoint = resolve_local_spawner_endpoint(
            configured_url=_SPAWNER_URL,
            requested_url=spawner_url,
            route_path="/api/mission-control/board",
        )
        data = request_local_spawner_json(
            endpoint,
            method="GET",
            timeout_seconds=timeout,
            max_response_bytes=1024 * 1024,
        )
    except RuntimeError as exc:
        return {"ok": False, "board": {}}, classify_local_spawner_failure(exc)
    if not isinstance(data, dict):
        return {"ok": False, "board": {}}, "invalid_response"
    return data, None


def has_live_missions(spawner_url: str | None = None) -> bool:
    data = fetch_board(spawner_url)
    board = data.get("board") or {}
    running = board.get("running") or []
    paused = board.get("paused") or []
    return len(running) > 0 or len(paused) > 0


def _entry_summary(e: dict[str, Any]) -> str:
    name = str(e.get("missionName") or e.get("taskName") or e.get("missionId") or "mission")
    summary = str(e.get("lastSummary") or "")
    if summary:
        return f"{name} - {summary[:100]}"
    return name


def format_board(board_payload: dict[str, Any]) -> str:
    board = board_payload.get("board") or {}
    running = board.get("running") or []
    paused = board.get("paused") or []
    completed = board.get("completed") or []
    failed = board.get("failed") or []

    if not running and not paused and not completed and not failed:
        return (
            "Nothing on the mission board right now - no active, paused, or "
            "recently completed missions. Want to kick one off? Use /run or "
            "just tell me the goal."
        )

    parts: list[str] = []
    if running:
        if len(running) == 1:
            parts.append(f"One mission running right now: {_entry_summary(running[0])}")
        else:
            parts.append(f"{len(running)} missions running right now:")
            for m in running[:5]:
                parts.append(f"  - {_entry_summary(m)}")
    if paused:
        parts.append(f"{len(paused)} paused.")
    if completed:
        if not running and not paused:
            parts.append(f"Nothing active, but {len(completed)} recently completed:")
            for m in completed[:3]:
                parts.append(f"  - {_entry_summary(m)}")
        else:
            parts.append(f"Recently completed: {len(completed)}.")
    if failed:
        parts.append(f"Failed: {len(failed)} - worth a look.")
    return "\n".join(parts).strip()


def format_board_from_spawner(spawner_url: str | None = None) -> str:
    data, error = _fetch_board_with_error(spawner_url)
    if not data.get("ok", True):
        if error == "endpoint_policy_blocked":
            return (
                "I couldn't read the mission board because the local endpoint policy blocked the request. "
                "Check the configured Spawner endpoint, then try /board again."
            )
        if error in {"redirect_blocked", "response_too_large", "invalid_response"}:
            return (
                "The local Spawner returned a response Spark couldn't safely use. "
                "Check Spawner health, then try /board again."
            )
        if error == "unavailable":
            return "The local Spawner is unavailable right now. Try /board again once it's back."
        return "Couldn't reach mission board right now. Try /board directly."
    return format_board(data)
