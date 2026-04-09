from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from spark_intelligence.attachments.registry import AttachmentRecord, attachment_status
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


SNAPSHOT_FILE_NAME = "attachments.snapshot.json"


@dataclass
class AttachmentSnapshot:
    generated_at: str
    workspace_id: str
    snapshot_path: str
    chip_source: str
    path_source: str
    chip_roots: list[str]
    path_roots: list[str]
    active_chip_keys: list[str]
    pinned_chip_keys: list[str]
    active_path_key: str | None
    warnings: list[str]
    records: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "snapshot_path": self.snapshot_path,
            "chip_source": self.chip_source,
            "path_source": self.path_source,
            "chip_roots": self.chip_roots,
            "path_roots": self.path_roots,
            "active_chip_keys": self.active_chip_keys,
            "pinned_chip_keys": self.pinned_chip_keys,
            "active_path_key": self.active_path_key,
            "warnings": self.warnings,
            "records": self.records,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        lines = [
            "Attachment snapshot",
            f"- generated_at: {self.generated_at}",
            f"- workspace_id: {self.workspace_id}",
            f"- snapshot_path: {self.snapshot_path}",
            f"- chip_roots ({self.chip_source}): {len(self.chip_roots)}",
            f"- path_roots ({self.path_source}): {len(self.path_roots)}",
            f"- active_chip_keys: {', '.join(self.active_chip_keys) if self.active_chip_keys else 'none'}",
            f"- pinned_chip_keys: {', '.join(self.pinned_chip_keys) if self.pinned_chip_keys else 'none'}",
            f"- active_path_key: {self.active_path_key or 'none'}",
            f"- records: {len(self.records)}",
            f"- warnings: {len(self.warnings)}",
        ]
        return "\n".join(lines)


def build_attachment_snapshot(config_manager: ConfigManager) -> AttachmentSnapshot:
    scan = attachment_status(config_manager)
    workspace_id = str(config_manager.get_path("workspace.id", default="default"))
    active_chip_keys = _existing_keys(
        _get_string_list(config_manager, "spark.chips.active_keys"),
        {record.key for record in scan.records if record.kind == "chip"},
    )
    pinned_chip_keys = _existing_keys(
        _get_string_list(config_manager, "spark.chips.pinned_keys"),
        {record.key for record in scan.records if record.kind == "chip"},
    )
    active_path_key = _normalize_optional_string(config_manager.get_path("spark.specialization_paths.active_path_key"))
    path_keys = {record.key for record in scan.records if record.kind == "path"}
    if active_path_key and active_path_key not in path_keys:
        active_path_key = None

    warnings = list(scan.warnings)
    missing_active = sorted(set(_get_string_list(config_manager, "spark.chips.active_keys")) - {record.key for record in scan.records if record.kind == "chip"})
    missing_pinned = sorted(set(_get_string_list(config_manager, "spark.chips.pinned_keys")) - {record.key for record in scan.records if record.kind == "chip"})
    if missing_active:
        warnings.append(f"configured active chip keys not found: {', '.join(missing_active)}")
    if missing_pinned:
        warnings.append(f"configured pinned chip keys not found: {', '.join(missing_pinned)}")
    configured_path = _normalize_optional_string(config_manager.get_path("spark.specialization_paths.active_path_key"))
    if configured_path and configured_path not in path_keys:
        warnings.append(f"configured active path key not found: {configured_path}")

    pinned_set = set(pinned_chip_keys)
    active_set = set(active_chip_keys)
    records = [_snapshot_record(record, active_set=active_set, pinned_set=pinned_set, active_path_key=active_path_key) for record in scan.records]

    return AttachmentSnapshot(
        generated_at=_now_iso(),
        workspace_id=workspace_id,
        snapshot_path=str(config_manager.paths.home / SNAPSHOT_FILE_NAME),
        chip_source=scan.chip_source,
        path_source=scan.path_source,
        chip_roots=scan.chip_roots,
        path_roots=scan.path_roots,
        active_chip_keys=active_chip_keys,
        pinned_chip_keys=pinned_chip_keys,
        active_path_key=active_path_key,
        warnings=warnings,
        records=records,
    )


def sync_attachment_snapshot(*, config_manager: ConfigManager, state_db: StateDB) -> AttachmentSnapshot:
    snapshot = build_attachment_snapshot(config_manager)
    snapshot_path = Path(snapshot.snapshot_path)
    snapshot_path.write_text(snapshot.to_json(), encoding="utf-8")
    summary = {
        "workspace_id": snapshot.workspace_id,
        "record_count": len(snapshot.records),
        "warning_count": len(snapshot.warnings),
        "chip_source": snapshot.chip_source,
        "path_source": snapshot.path_source,
        "identity_import": _build_hook_import_summary(snapshot.records, hook="identity"),
        "personality_import": _build_hook_import_summary(snapshot.records, hook="personality"),
    }
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO attachment_state_snapshots(
                snapshot_id,
                workspace_id,
                snapshot_path,
                chip_source,
                path_source,
                active_chip_keys_json,
                pinned_chip_keys_json,
                active_path_key,
                warning_count,
                record_count,
                generated_at,
                summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"attachment-snapshot-{uuid4().hex}",
                snapshot.workspace_id,
                snapshot.snapshot_path,
                snapshot.chip_source,
                snapshot.path_source,
                json.dumps(snapshot.active_chip_keys, sort_keys=True),
                json.dumps(snapshot.pinned_chip_keys, sort_keys=True),
                snapshot.active_path_key,
                len(snapshot.warnings),
                len(snapshot.records),
                snapshot.generated_at,
                json.dumps(summary, sort_keys=True),
            ),
        )
        _set_runtime_state(conn, "attachments:last_snapshot_path", snapshot.snapshot_path)
        _set_runtime_state(conn, "attachments:last_snapshot_generated_at", snapshot.generated_at)
        _set_runtime_state(conn, "attachments:active_chip_keys", json.dumps(snapshot.active_chip_keys))
        _set_runtime_state(conn, "attachments:pinned_chip_keys", json.dumps(snapshot.pinned_chip_keys))
        _set_runtime_state(conn, "attachments:active_path_key", snapshot.active_path_key or "")
        _set_runtime_state(
            conn,
            "attachments:last_snapshot_summary",
            json.dumps(summary, sort_keys=True),
        )
        conn.commit()
    return snapshot


def build_attachment_context(config_manager: ConfigManager) -> dict[str, Any]:
    snapshot = build_attachment_snapshot(config_manager)
    chip_records = [record for record in snapshot.records if str(record.get("kind") or "") == "chip"]
    path_records = [record for record in snapshot.records if str(record.get("kind") or "") == "path"]
    return {
        "active_chip_keys": snapshot.active_chip_keys,
        "pinned_chip_keys": snapshot.pinned_chip_keys,
        "attached_chip_keys": [str(record.get("key") or "") for record in chip_records if str(record.get("key") or "")],
        "attached_path_keys": [str(record.get("key") or "") for record in path_records if str(record.get("key") or "")],
        "attached_chip_records": [
            {
                "key": str(record.get("key") or ""),
                "label": str(record.get("label") or ""),
                "description": str(record.get("description") or ""),
                "attachment_mode": str(record.get("attachment_mode") or "available"),
                "capabilities": [str(item) for item in (record.get("capabilities") or []) if str(item)],
                "hook_names": sorted(str(item) for item in ((record.get("commands") or {}).keys()) if str(item)),
                "repo_root": str(record.get("repo_root") or ""),
            }
            for record in chip_records
        ],
        "attached_path_records": [
            {
                "key": str(record.get("key") or ""),
                "label": str(record.get("label") or ""),
                "attachment_mode": str(record.get("attachment_mode") or "available"),
                "capabilities": [str(item) for item in (record.get("capabilities") or []) if str(item)],
                "hook_names": sorted(str(item) for item in ((record.get("commands") or {}).keys()) if str(item)),
                "repo_root": str(record.get("repo_root") or ""),
            }
            for record in path_records
        ],
        "active_path_key": snapshot.active_path_key,
        "warning_count": len(snapshot.warnings),
        "snapshot_path": snapshot.snapshot_path,
    }


def activate_chip(config_manager: ConfigManager, *, chip_key: str) -> list[str]:
    available = {record.key for record in attachment_status(config_manager).records if record.kind == "chip"}
    _require_known_key(chip_key, available, "chip")
    values = _get_string_list(config_manager, "spark.chips.active_keys")
    if chip_key not in values:
        values.append(chip_key)
        config_manager.set_path("spark.chips.active_keys", values)
    return values


def deactivate_chip(config_manager: ConfigManager, *, chip_key: str) -> list[str]:
    values = [value for value in _get_string_list(config_manager, "spark.chips.active_keys") if value != chip_key]
    config_manager.set_path("spark.chips.active_keys", values)
    pinned_values = [value for value in _get_string_list(config_manager, "spark.chips.pinned_keys") if value != chip_key]
    config_manager.set_path("spark.chips.pinned_keys", pinned_values)
    return values


def pin_chip(config_manager: ConfigManager, *, chip_key: str) -> list[str]:
    available = {record.key for record in attachment_status(config_manager).records if record.kind == "chip"}
    _require_known_key(chip_key, available, "chip")
    active_values = activate_chip(config_manager, chip_key=chip_key)
    pinned_values = _get_string_list(config_manager, "spark.chips.pinned_keys")
    if chip_key not in pinned_values:
        pinned_values.append(chip_key)
        config_manager.set_path("spark.chips.pinned_keys", pinned_values)
    return active_values


def unpin_chip(config_manager: ConfigManager, *, chip_key: str) -> list[str]:
    values = [value for value in _get_string_list(config_manager, "spark.chips.pinned_keys") if value != chip_key]
    config_manager.set_path("spark.chips.pinned_keys", values)
    return values


def set_active_path(config_manager: ConfigManager, *, path_key: str) -> str:
    available = {record.key for record in attachment_status(config_manager).records if record.kind == "path"}
    _require_known_key(path_key, available, "path")
    config_manager.set_path("spark.specialization_paths.active_path_key", path_key)
    return path_key


def clear_active_path(config_manager: ConfigManager) -> None:
    config_manager.set_path("spark.specialization_paths.active_path_key", None)


def _snapshot_record(
    record: AttachmentRecord,
    *,
    active_set: set[str],
    pinned_set: set[str],
    active_path_key: str | None,
) -> dict[str, Any]:
    payload = record.to_dict()
    attachment_mode = "available"
    if record.kind == "chip":
        if record.key in pinned_set:
            attachment_mode = "pinned"
        elif record.key in active_set:
            attachment_mode = "active"
    elif record.kind == "path" and active_path_key and record.key == active_path_key:
        attachment_mode = "active"
    payload["attachment_mode"] = attachment_mode
    return payload


def _build_hook_import_summary(records: list[dict[str, Any]], *, hook: str) -> dict[str, Any]:
    identity_records = [
        record
        for record in records
        if str(record.get("kind") or "") == "chip" and hook in (record.get("commands") or {})
    ]
    available_chip_keys = sorted(str(record.get("key") or "") for record in identity_records if str(record.get("key") or ""))
    active_chip_keys = sorted(
        str(record.get("key") or "")
        for record in identity_records
        if str(record.get("key") or "") and str(record.get("attachment_mode") or "") in {"active", "pinned"}
    )
    return {
        "available_chip_keys": available_chip_keys,
        "available_chip_count": len(available_chip_keys),
        "active_chip_keys": active_chip_keys,
        "active_chip_count": len(active_chip_keys),
        "ready": bool(active_chip_keys),
    }


def _require_known_key(key: str, known_keys: set[str], kind: str) -> None:
    if key not in known_keys:
        known = ", ".join(sorted(known_keys)) if known_keys else "none"
        raise ValueError(f"Unknown {kind} key '{key}'. Known {kind} keys: {known}")


def _get_string_list(config_manager: ConfigManager, dotted_path: str) -> list[str]:
    values = config_manager.get_path(dotted_path, default=[]) or []
    normalized: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _existing_keys(values: list[str], known_keys: set[str]) -> list[str]:
    return [value for value in values if value in known_keys]


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _set_runtime_state(conn: Any, state_key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO runtime_state(state_key, value)
        VALUES (?, ?)
        ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (state_key, value),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
