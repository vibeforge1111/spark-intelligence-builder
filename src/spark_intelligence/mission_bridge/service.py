from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


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
    for pat in _BOARD_PATTERNS:
        if pat.search(text):
            return {"action": "list"}
    return None


def fetch_board(spawner_url: str | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    base = (spawner_url or _SPAWNER_URL).rstrip("/")
    req = urllib.request.Request(f"{base}/api/mission-control/board", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return {"ok": False, "board": {}}
    if not isinstance(data, dict):
        return {"ok": False, "board": {}}
    return data


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
    data = fetch_board(spawner_url)
    if not data.get("ok", True):
        return "Couldn't reach mission board right now. Try /board directly."
    return format_board(data)
