from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.self_awareness.agent_events import AGENT_EVENT_SCHEMA_VERSION, BlackBoxEntry


DEFAULT_SPAWNER_LEDGER_RELATIVE_PATH = Path(".spawner") / "agent-events.jsonl"


def resolve_spawner_agent_event_ledger_path(config_manager: ConfigManager) -> Path | None:
    explicit = _optional_path(config_manager.get_path("spark.spawner.agent_event_ledger_path"))
    if explicit:
        return explicit

    state_dir = _optional_path(config_manager.get_path("spark.spawner.state_dir"))
    if state_dir:
        return state_dir / "agent-events.jsonl"

    repo_root = _optional_path(config_manager.get_path("spark.spawner.repo_root"))
    if repo_root:
        return repo_root / DEFAULT_SPAWNER_LEDGER_RELATIVE_PATH

    return None


def read_spawner_black_box_entries(
    ledger_path: Path | str | None,
    *,
    request_id: str | None = None,
    limit: int = 20,
) -> list[BlackBoxEntry]:
    if ledger_path is None:
        return []

    path = Path(ledger_path).expanduser()
    if not path.exists() or not path.is_file():
        return []

    entries: list[BlackBoxEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parsed = _parse_json_object(raw)
        if not parsed:
            continue
        if parsed.get("schema_version") != AGENT_EVENT_SCHEMA_VERSION:
            continue
        if request_id and parsed.get("request_id") != request_id:
            continue
        entry = _black_box_entry_from_spawner_event(parsed)
        if entry:
            entries.append(entry)

    entries.sort(key=lambda entry: entry.created_at or "", reverse=True)
    return entries[: max(1, int(limit))]


def read_configured_spawner_black_box_entries(
    config_manager: ConfigManager,
    *,
    request_id: str | None = None,
    limit: int = 20,
) -> list[BlackBoxEntry]:
    return read_spawner_black_box_entries(
        resolve_spawner_agent_event_ledger_path(config_manager),
        request_id=request_id,
        limit=limit,
    )


def _black_box_entry_from_spawner_event(event: dict[str, Any]) -> BlackBoxEntry | None:
    event_type = str(event.get("event_type") or "").strip()
    if not event_type:
        return None
    facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
    return BlackBoxEntry(
        event_id=str(event.get("event_id") or ""),
        event_type=event_type,
        created_at=str(event.get("created_at") or "") or None,
        perceived_intent=_optional_text(event.get("user_intent")),
        route_chosen=_optional_text(event.get("selected_route")),
        sources_used=_payload_list(event.get("sources")),
        assumptions=_text_list(event.get("assumptions")),
        blockers=_text_list(event.get("blockers")),
        changed=_text_list(event.get("changed")),
        memory_candidate=event.get("memory_candidate") if isinstance(event.get("memory_candidate"), dict) else None,
        summary=str(event.get("summary") or facts.get("mission_event_type") or ""),
    )


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _optional_path(value: object) -> Path | None:
    text = str(value or "").strip()
    return Path(text).expanduser() if text else None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _payload_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
