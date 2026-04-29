from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.local_project_index import build_local_project_index
from spark_intelligence.state.db import StateDB


_PAYLOAD_KEY_SIGNALS = (
    "repo",
    "repo_key",
    "repo_name",
    "repo_root",
    "repository",
    "target_repo",
    "target_repository",
    "workspace",
    "workspace_path",
    "project",
    "project_key",
)
_SPAWNER_STATE_KEY_PATTERNS = (
    "spawner",
    "mission",
    "workflow",
    "builder",
    "target",
)
_IGNORED_REFERENCE_VALUES = {
    "",
    "default",
    "local",
    "unknown",
    "none",
    "null",
}


@dataclass(frozen=True)
class SpawnerPayloadDriftRecord:
    source_kind: str
    source_ref: str
    payload_key: str
    reference: str
    reason_code: str
    summary: str
    matched_repo_key: str | None = None
    payload_preview: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "payload_key": self.payload_key,
            "reference": self.reference,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "matched_repo_key": self.matched_repo_key,
            "payload_preview": self.payload_preview,
        }


@dataclass(frozen=True)
class SpawnerPayloadDriftReport:
    status: str
    drift_count: int
    checked_payload_count: int
    records: list[SpawnerPayloadDriftRecord]

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "drift_count": self.drift_count,
            "checked_payload_count": self.checked_payload_count,
            "records": [record.to_payload() for record in self.records],
        }


def detect_spawner_payload_drift(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    limit: int = 40,
) -> SpawnerPayloadDriftReport:
    """Find payloads that still point at an unknown, missing, or stale local repo."""

    project_records = build_local_project_index(config_manager, probe_git=False).to_payload().get("records") or []
    payloads = _read_candidate_payloads(state_db, limit=limit)
    drift_records: list[SpawnerPayloadDriftRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for source in payloads:
        payload = source.get("payload")
        source_kind = str(source.get("source_kind") or "runtime_state")
        source_ref = str(source.get("source_ref") or "")
        preview = _preview_payload(payload)
        for payload_key, reference in _extract_repo_references(payload):
            normalized_reference = _normalize_reference(reference)
            if normalized_reference in _IGNORED_REFERENCE_VALUES:
                continue
            match = _match_project_reference(reference, project_records)
            if match is not None:
                continue
            reason_code, summary = _classify_unmatched_reference(
                payload_key=payload_key,
                reference=reference,
                project_records=project_records,
            )
            dedupe_key = (source_kind, source_ref, payload_key, normalized_reference)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            drift_records.append(
                SpawnerPayloadDriftRecord(
                    source_kind=source_kind,
                    source_ref=source_ref,
                    payload_key=payload_key,
                    reference=reference,
                    reason_code=reason_code,
                    summary=summary,
                    payload_preview=preview,
                )
            )
    return SpawnerPayloadDriftReport(
        status="drift_detected" if drift_records else "ok",
        drift_count=len(drift_records),
        checked_payload_count=len(payloads),
        records=drift_records[:10],
    )


def _read_candidate_payloads(state_db: StateDB, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with state_db.connect() as conn:
        runtime_rows = conn.execute(
            """
            SELECT state_key, value
            FROM runtime_state
            WHERE lower(state_key) LIKE '%spawner%'
               OR lower(state_key) LIKE '%mission%'
               OR lower(state_key) LIKE '%workflow%'
               OR lower(state_key) LIKE '%builder%'
               OR lower(state_key) LIKE '%target%'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in runtime_rows:
            rows.append(
                {
                    "source_kind": "runtime_state",
                    "source_ref": str(row["state_key"]),
                    "payload": _parse_payload(row["value"]),
                }
            )
        event_rows = conn.execute(
            """
            SELECT event_id, event_type, summary, facts_json
            FROM builder_events
            WHERE lower(component) LIKE '%spawner%'
               OR lower(target_surface) LIKE '%spawner%'
               OR lower(summary) LIKE '%spawner%'
               OR lower(facts_json) LIKE '%repo%'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in event_rows:
            rows.append(
                {
                    "source_kind": "builder_event",
                    "source_ref": str(row["event_id"]),
                    "payload": {
                        "event_type": row["event_type"],
                        "summary": row["summary"],
                        "facts": _parse_payload(row["facts_json"]),
                    },
                }
            )
    return rows


def _parse_payload(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _extract_repo_references(payload: Any, *, prefix: str = "") -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key or "").strip()
            next_prefix = f"{prefix}.{key}" if prefix else key
            normalized_key = _normalize_key(key)
            if _is_repo_reference_key(normalized_key):
                references.extend(_string_values(value, payload_key=next_prefix))
            references.extend(_extract_repo_references(value, prefix=next_prefix))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            references.extend(_extract_repo_references(item, prefix=f"{prefix}[{index}]"))
    return references


def _string_values(value: Any, *, payload_key: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(payload_key, value.strip())] if value.strip() else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [(payload_key, str(value))]
    if isinstance(value, dict):
        values: list[tuple[str, str]] = []
        for nested_key in ("key", "name", "repo", "repoKey", "repo_root", "path", "root"):
            if nested_key in value:
                values.extend(_string_values(value[nested_key], payload_key=f"{payload_key}.{nested_key}"))
        return values
    if isinstance(value, list):
        values = []
        for index, item in enumerate(value):
            values.extend(_string_values(item, payload_key=f"{payload_key}[{index}]"))
        return values
    return []


def _is_repo_reference_key(normalized_key: str) -> bool:
    return any(signal == normalized_key or normalized_key.endswith(f"_{signal}") for signal in _PAYLOAD_KEY_SIGNALS)


def _match_project_reference(reference: str, project_records: list[Any]) -> dict[str, Any] | None:
    normalized_reference = _normalize_reference(reference)
    if not normalized_reference:
        return None
    reference_path = _path_from_reference(reference)
    for record in project_records:
        if not isinstance(record, dict):
            continue
        terms = [
            str(record.get("key") or ""),
            str(record.get("label") or ""),
            *[str(item) for item in (record.get("aliases") or [])],
        ]
        record_path = str(record.get("path") or "").strip()
        if record_path:
            terms.append(record_path)
            terms.append(Path(record_path).name)
        if normalized_reference in {_normalize_reference(term) for term in terms if term}:
            return record
        if reference_path is not None and record_path:
            try:
                if reference_path == Path(record_path).expanduser().resolve():
                    return record
            except Exception:
                continue
    return None


def _classify_unmatched_reference(
    *,
    payload_key: str,
    reference: str,
    project_records: list[Any],
) -> tuple[str, str]:
    reference_path = _path_from_reference(reference)
    if reference_path is not None:
        if not reference_path.exists():
            return (
                "missing_repo_path",
                f"Payload `{payload_key}` points at missing repo path `{reference}`.",
            )
        return (
            "unindexed_repo_path",
            f"Payload `{payload_key}` points at `{reference}`, which exists but is not in the local project index.",
        )
    known_keys = sorted(
        str(record.get("key") or "")
        for record in project_records
        if isinstance(record, dict) and str(record.get("key") or "")
    )
    if _looks_like_stale_spark_repo(reference):
        return (
            "stale_spark_repo_reference",
            f"Payload `{payload_key}` still references `{reference}`, which is not a current indexed Spark repo.",
        )
    return (
        "unknown_repo_reference",
        f"Payload `{payload_key}` references unknown repo `{reference}`; known repos: {', '.join(known_keys[:6]) or 'none'}.",
    )


def _looks_like_stale_spark_repo(reference: str) -> bool:
    normalized = _normalize_reference(reference)
    return "spark" in normalized or "spawner" in normalized or "vibeship" in normalized


def _path_from_reference(reference: str) -> Path | None:
    text = str(reference or "").strip()
    if not text:
        return None
    looks_pathy = "\\" in text or "/" in text or re.match(r"^[A-Za-z]:", text) is not None
    if not looks_pathy:
        return None
    try:
        return Path(text).expanduser().resolve()
    except Exception:
        return Path(text).expanduser()


def _preview_payload(payload: Any) -> str:
    try:
        return json.dumps(payload, sort_keys=True)[:240]
    except TypeError:
        return str(payload)[:240]


def _normalize_key(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", str(value or "")).replace("-", "_").casefold()


def _normalize_reference(value: str) -> str:
    text = str(value or "").strip().casefold().replace("\\", "/")
    text = text.rstrip("/")
    if "/" in text:
        text = text.split("/")[-1]
    text = text.replace("_", "-").replace(" ", "-")
    return re.sub(r"[^a-z0-9.-]+", "-", text).strip("-")
