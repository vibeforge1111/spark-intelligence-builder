from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


RUNTIME_STATE_STORAGE_KIND = "runtime_state"
JSON_RICHNESS_MERGE_GUARD = "json_richness_merge"


@dataclass(frozen=True)
class RuntimeStateWriteResult:
    action: str
    stored_value: str
    existing_richness: int
    incoming_richness: int
    stored_richness: int


def register_reset_sensitive_state(
    conn: Any,
    *,
    state_key: str,
    component: str,
    scope_kind: str,
    scope_ref: str,
    reset_reason: str,
    storage_kind: str = RUNTIME_STATE_STORAGE_KIND,
) -> None:
    now = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO reset_sensitive_state_registry(
            registry_id,
            state_key,
            component,
            storage_kind,
            scope_kind,
            scope_ref,
            reset_reason,
            active,
            last_registered_at,
            last_cleared_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)
        ON CONFLICT(state_key) DO UPDATE SET
            component=excluded.component,
            storage_kind=excluded.storage_kind,
            scope_kind=excluded.scope_kind,
            scope_ref=excluded.scope_ref,
            reset_reason=excluded.reset_reason,
            active=1,
            last_registered_at=excluded.last_registered_at,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            f"rgr:{uuid4().hex}",
            state_key,
            component,
            storage_kind,
            scope_kind,
            scope_ref,
            reset_reason,
            now,
        ),
    )


def clear_reset_sensitive_scope(
    conn: Any,
    *,
    scope_kind: str,
    scope_ref: str,
    component: str | None = None,
    storage_kind: str = RUNTIME_STATE_STORAGE_KIND,
) -> list[str]:
    filters = [
        "scope_kind = ?",
        "scope_ref = ?",
        "storage_kind = ?",
        "active = 1",
    ]
    params: list[Any] = [scope_kind, scope_ref, storage_kind]
    if component:
        filters.append("component = ?")
        params.append(component)
    rows = conn.execute(
        f"""
        SELECT state_key
        FROM reset_sensitive_state_registry
        WHERE {' AND '.join(filters)}
        ORDER BY state_key
        """,
        tuple(params),
    ).fetchall()
    state_keys = [str(row["state_key"]) for row in rows if row["state_key"]]
    if not state_keys:
        return []
    clear_registered_state_keys(conn, state_keys=state_keys)
    return state_keys


def clear_registered_state_keys(conn: Any, *, state_keys: list[str]) -> list[str]:
    unique_keys = [key for key in dict.fromkeys(state_keys) if key]
    if not unique_keys:
        return []
    conn.executemany(
        "DELETE FROM runtime_state WHERE state_key = ?",
        [(state_key,) for state_key in unique_keys],
    )
    now = _utc_now_iso()
    placeholders = ", ".join("?" for _ in unique_keys)
    conn.execute(
        f"""
        UPDATE reset_sensitive_state_registry
        SET active = 0,
            last_cleared_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE state_key IN ({placeholders})
        """,
        (now, *unique_keys),
    )
    return unique_keys


def upsert_runtime_state(
    conn: Any,
    *,
    state_key: str,
    value: str,
    component: str,
    guard_strategy: str | None = None,
    reset_sensitive_scope: tuple[str, str, str] | None = None,
) -> RuntimeStateWriteResult:
    existing_row = conn.execute(
        "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
        (state_key,),
    ).fetchone()
    existing_value = str(existing_row["value"]) if existing_row and existing_row["value"] is not None else None

    incoming_richness = _json_richness(value)
    existing_richness = _json_richness(existing_value)
    stored_value = value
    stored_richness = incoming_richness
    action = "written"

    if guard_strategy == JSON_RICHNESS_MERGE_GUARD:
        incoming_object = _loads_json_object(value)
        existing_object = _loads_json_object(existing_value)
        if incoming_object is not None and existing_object is not None:
            merged_object = _merge_preserving_richer(existing=existing_object, incoming=incoming_object)
            merged_value = json.dumps(merged_object, sort_keys=True)
            merged_richness = _object_richness(merged_object)
            if merged_value != value and existing_richness > incoming_richness:
                stored_value = merged_value
                stored_richness = merged_richness
                action = "merged_richer_state"
                _record_resume_richness_guard(
                    conn,
                    state_key=state_key,
                    component=component,
                    action=action,
                    existing_richness=existing_richness,
                    incoming_richness=incoming_richness,
                    stored_richness=stored_richness,
                    existing_object=existing_object,
                    incoming_object=incoming_object,
                    stored_object=merged_object,
                )

    conn.execute(
        """
        INSERT INTO runtime_state(state_key, value)
        VALUES (?, ?)
        ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (state_key, stored_value),
    )

    if reset_sensitive_scope is not None:
        scope_kind, scope_ref, reset_reason = reset_sensitive_scope
        register_reset_sensitive_state(
            conn,
            state_key=state_key,
            component=component,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            reset_reason=reset_reason,
        )

    return RuntimeStateWriteResult(
        action=action,
        stored_value=stored_value,
        existing_richness=existing_richness,
        incoming_richness=incoming_richness,
        stored_richness=stored_richness,
    )


def _record_resume_richness_guard(
    conn: Any,
    *,
    state_key: str,
    component: str,
    action: str,
    existing_richness: int,
    incoming_richness: int,
    stored_richness: int,
    existing_object: dict[str, Any],
    incoming_object: dict[str, Any],
    stored_object: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO resume_richness_guard_records(
            guard_record_id,
            state_key,
            component,
            action,
            existing_richness,
            incoming_richness,
            stored_richness,
            evidence_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"rgg:{uuid4().hex}",
            state_key,
            component,
            action,
            existing_richness,
            incoming_richness,
            stored_richness,
            json.dumps(
                {
                    "existing": existing_object,
                    "incoming": incoming_object,
                    "stored": stored_object,
                    "preserved_keys": sorted(
                        key
                        for key, value in existing_object.items()
                        if key not in incoming_object and not _is_empty(value)
                    ),
                },
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            ),
        ),
    )


def _loads_json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_richness(value: str | None) -> int:
    parsed = _loads_json_object(value)
    if parsed is None:
        normalized = str(value or "").strip()
        return 0 if not normalized else min(len(normalized), 32)
    return _object_richness(parsed)


def _object_richness(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_object_richness(item) for item in value.values()) + sum(
            1 for item in value.values() if not _is_empty(item)
        )
    if isinstance(value, list):
        return sum(_object_richness(item) for item in value) + sum(1 for item in value if not _is_empty(item))
    if _is_empty(value):
        return 0
    if isinstance(value, str):
        return 1 + min(len(value.strip()) // 8, 8)
    return 1


def _merge_preserving_richer(*, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, incoming_value in incoming.items():
        existing_value = merged.get(key)
        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            merged[key] = _merge_preserving_richer(existing=existing_value, incoming=incoming_value)
            continue
        if _is_empty(incoming_value) and not _is_empty(existing_value):
            continue
        merged[key] = incoming_value
    return merged


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
