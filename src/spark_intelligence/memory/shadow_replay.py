from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.execution import run_governed_command
from spark_intelligence.state.db import StateDB


DEFAULT_WRITABLE_ROLES = ["user"]
DEFAULT_EVENT_LIMIT = 2000
DEFAULT_VALIDATOR_ROOT = Path.home() / "Desktop" / "domain-chip-memory"


@dataclass(frozen=True)
class ShadowReplayExportResult:
    path: Path
    payload: dict[str, Any]
    conversation_count: int
    turn_count: int
    probe_count: int
    validation: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "path": str(self.path),
                "conversation_count": self.conversation_count,
                "turn_count": self.turn_count,
                "probe_count": self.probe_count,
                "validation": self.validation,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory shadow replay export"]
        lines.append(f"- path: {self.path}")
        lines.append(f"- conversations: {self.conversation_count}")
        lines.append(f"- turns: {self.turn_count}")
        lines.append(f"- probes: {self.probe_count}")
        if self.validation is None:
            lines.append("- validation: skipped")
        else:
            lines.append(f"- validation: {'valid' if self.validation.get('valid') else 'invalid'}")
            lines.append(f"- validator errors: {len(self.validation.get('errors') or [])}")
            lines.append(f"- validator warnings: {len(self.validation.get('warnings') or [])}")
        return "\n".join(lines)


def export_shadow_replay(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    write_path: str | Path | None = None,
    conversation_limit: int = 20,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    writable_roles: list[str] | None = None,
    validate: bool = True,
    validator_root: str | Path | None = None,
) -> ShadowReplayExportResult:
    payload = build_shadow_replay_payload(
        state_db=state_db,
        conversation_limit=conversation_limit,
        event_limit=event_limit,
        writable_roles=writable_roles or list(DEFAULT_WRITABLE_ROLES),
    )
    output_path = Path(write_path) if write_path else _default_output_path(config_manager)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    validation = None
    if validate:
        validation = validate_shadow_replay(
            replay_path=output_path,
            validator_root=validator_root,
        )
    return ShadowReplayExportResult(
        path=output_path,
        payload=payload,
        conversation_count=len(payload.get("conversations") or []),
        turn_count=sum(len(conversation.get("turns") or []) for conversation in payload.get("conversations") or []),
        probe_count=sum(len(conversation.get("probes") or []) for conversation in payload.get("conversations") or []),
        validation=validation,
    )


def build_shadow_replay_payload(
    *,
    state_db: StateDB,
    conversation_limit: int = 20,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    writable_roles: list[str] | None = None,
) -> dict[str, Any]:
    writable_roles = writable_roles or list(DEFAULT_WRITABLE_ROLES)
    turn_rows = _load_turn_rows(state_db=state_db, event_limit=event_limit)
    accepted_observations = _load_accepted_observations(state_db=state_db, event_limit=event_limit)
    observations_by_session = _group_observations_by_session(accepted_observations)
    histories = _build_observation_histories(accepted_observations)

    conversations: dict[str, dict[str, Any]] = {}
    for row in turn_rows:
        conversation_id = _conversation_id(row)
        content = _turn_content(row)
        if not content:
            continue
        conversation = conversations.setdefault(
            conversation_id,
            {
                "conversation_id": conversation_id,
                "session_id": row.get("session_id") or None,
                "metadata": _conversation_metadata(row),
                "turns": [],
                "_request_keys": set(),
            },
        )
        conversation["turns"].append(_build_turn(row, observations_by_session))
        if row.get("request_id"):
            conversation["_request_keys"].add((str(row.get("session_id") or ""), str(row.get("request_id") or "")))
        _merge_conversation_metadata(conversation["metadata"], row)

    selected = sorted(
        (
            conversation
            for conversation in conversations.values()
            if conversation.get("turns")
        ),
        key=lambda item: max(str(turn.get("timestamp") or "") for turn in item["turns"]),
        reverse=True,
    )[: max(conversation_limit, 1)]

    rendered_conversations: list[dict[str, Any]] = []
    for conversation in reversed(selected):
        session_id = str(conversation.get("session_id") or "")
        probes = _build_conversation_probes(
            session_id=session_id,
            request_keys=conversation.get("_request_keys") or set(),
            observations_by_session=observations_by_session,
            histories=histories,
        )
        rendered: dict[str, Any] = {
            "conversation_id": conversation["conversation_id"],
            "turns": list(conversation["turns"]),
        }
        if conversation.get("session_id"):
            rendered["session_id"] = conversation["session_id"]
        metadata = {key: value for key, value in (conversation.get("metadata") or {}).items() if value not in (None, [], {}, "")}
        if metadata:
            rendered["metadata"] = metadata
        if probes:
            rendered["probes"] = probes
        rendered_conversations.append(rendered)

    return {
        "writable_roles": writable_roles,
        "conversations": rendered_conversations,
    }


def validate_shadow_replay(
    *,
    replay_path: str | Path,
    validator_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(validator_root) if validator_root else DEFAULT_VALIDATOR_ROOT
    if not root.exists():
        return {
            "valid": False,
            "errors": [f"validator_root_missing:{root}"],
            "warnings": [],
        }
    execution = run_governed_command(
        command=[
            sys.executable,
            "-m",
            "domain_chip_memory.cli",
            "validate-spark-shadow-replay",
            str(Path(replay_path)),
        ],
        cwd=str(root),
    )
    stdout = execution.stdout.strip()
    parsed: dict[str, Any] | None = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            parsed = payload
    if parsed is not None:
        parsed.setdefault("stderr", execution.stderr.strip())
        return parsed
    return {
        "valid": execution.exit_code == 0,
        "errors": [] if execution.exit_code == 0 else [execution.stderr.strip() or stdout or "validator_failed"],
        "warnings": [],
        "stdout": stdout,
        "stderr": execution.stderr.strip(),
    }


def _default_output_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "spark-shadow-replay.json"


def _load_turn_rows(*, state_db: StateDB, event_limit: int) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT
                    rowid AS row_order,
                    event_id,
                    event_type,
                    created_at,
                    run_id,
                    request_id,
                    trace_ref,
                    channel_id,
                    session_id,
                    human_id,
                    component,
                    facts_json,
                    provenance_json
                FROM builder_events
                WHERE event_type IN ('intent_committed', 'delivery_succeeded')
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, row_order ASC
            """,
            (max(event_limit, 1),),
        ).fetchall()
    return [_normalize_event_row(row) for row in rows]


def _load_accepted_observations(*, state_db: StateDB, event_limit: int) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        request_rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT event_id, request_id, session_id, created_at, facts_json
                FROM builder_events
                WHERE event_type = 'memory_write_requested'
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, event_id ASC
            """,
            (max(event_limit, 1),),
        ).fetchall()
        result_rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT request_id, session_id, created_at, facts_json
                FROM builder_events
                WHERE event_type = 'memory_write_succeeded'
                ORDER BY created_at DESC, event_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (max(event_limit, 1),),
        ).fetchall()

    accepted_request_keys = {
        (str(row["session_id"] or ""), str(row["request_id"] or ""))
        for row in result_rows
        if _json_field(row["facts_json"], "accepted_count", default=0) > 0
    }
    observations: list[dict[str, Any]] = []
    for row in request_rows:
        request_key = (str(row["session_id"] or ""), str(row["request_id"] or ""))
        if request_key not in accepted_request_keys:
            continue
        facts = _loads_json_value(row["facts_json"])
        requested = facts.get("observations")
        if not isinstance(requested, list):
            continue
        for item in requested:
            if not isinstance(item, dict):
                continue
            observations.append(
                {
                    "session_id": request_key[0] or None,
                    "request_id": request_key[1] or None,
                    "timestamp": str(row["created_at"] or ""),
                    "subject": str(item.get("subject") or ""),
                    "predicate": str(item.get("predicate") or ""),
                    "value": item.get("value"),
                    "operation": str(item.get("operation") or ""),
                    "memory_role": str(item.get("memory_role") or "current_state"),
                }
            )
    return observations


def _group_observations_by_session(observations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        session_id = str(observation.get("session_id") or "")
        if not session_id:
            continue
        output.setdefault(session_id, []).append(observation)
    return output


def _build_observation_histories(observations: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    histories: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in observations:
        key = (str(observation.get("subject") or ""), str(observation.get("predicate") or ""))
        if not key[0] or not key[1]:
            continue
        histories.setdefault(key, []).append(observation)
    for key in histories:
        histories[key] = sorted(histories[key], key=lambda item: (str(item.get("timestamp") or ""), str(item.get("request_id") or "")))
    return histories


def _build_turn(
    row: dict[str, Any],
    observations_by_session: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    facts = row.get("facts_json") or {}
    role = "user" if row.get("event_type") == "intent_committed" else "assistant"
    turn: dict[str, Any] = {
        "message_id": _message_id(row),
        "role": role,
        "content": _turn_content(row),
        "timestamp": row.get("created_at"),
    }
    metadata = {
        "channel_kind": row.get("channel_id"),
        "component": row.get("component"),
        "run_id": row.get("run_id"),
        "request_id": row.get("request_id"),
        "trace_ref": row.get("trace_ref"),
        "source_event_id": row.get("event_id"),
    }
    if role == "assistant":
        metadata["keepability"] = facts.get("keepability")
        metadata["promotion_disposition"] = facts.get("promotion_disposition")
        metadata["delivery_target"] = facts.get("delivery_target")
    else:
        session_id = str(row.get("session_id") or "")
        request_id = str(row.get("request_id") or "")
        observation_hints = [
            observation
            for observation in observations_by_session.get(session_id, [])
            if str(observation.get("request_id") or "") == request_id
        ]
        if observation_hints:
            metadata["memory_kind"] = "current_state"
            metadata["predicate_hints"] = [str(item.get("predicate") or "") for item in observation_hints if item.get("predicate")]
            metadata["source_tags"] = ["spark_memory_sdk_shadow_candidate"]
    metadata = {key: value for key, value in metadata.items() if value not in (None, [], {}, "")}
    if metadata:
        turn["metadata"] = metadata
    return turn


def _build_conversation_probes(
    *,
    session_id: str,
    request_keys: set[tuple[str, str]],
    observations_by_session: dict[str, list[dict[str, Any]]],
    histories: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    if not session_id:
        return probes
    session_observations = [
        observation
        for observation in observations_by_session.get(session_id, [])
        if (str(observation.get("session_id") or ""), str(observation.get("request_id") or "")) in request_keys
    ]
    for index, observation in enumerate(session_observations, start=1):
        subject = str(observation.get("subject") or "")
        predicate = str(observation.get("predicate") or "")
        if not subject or not predicate:
            continue
        probes.append(
            {
                "probe_id": f"{session_id}:current:{index}",
                "probe_type": "current_state",
                "subject": subject,
                "predicate": predicate,
                "expected_value": observation.get("value"),
            }
        )
        probes.append(
            {
                "probe_id": f"{session_id}:evidence:{index}",
                "probe_type": "evidence",
                "subject": subject,
                "predicate": predicate,
                "expected_value": observation.get("value"),
                "min_results": 1,
            }
        )
        history = histories.get((subject, predicate)) or []
        distinct_values = []
        for item in history:
            value = item.get("value")
            if value not in distinct_values:
                distinct_values.append(value)
        if len(distinct_values) < 2:
            continue
        latest = history[-1]
        probes.append(
            {
                "probe_id": f"{session_id}:historical:{index}",
                "probe_type": "historical_state",
                "subject": subject,
                "predicate": predicate,
                "as_of": latest.get("timestamp"),
                "expected_value": latest.get("value"),
            }
        )
    return probes


def _conversation_id(row: dict[str, Any]) -> str:
    session_id = str(row.get("session_id") or "").strip()
    if session_id:
        return session_id
    channel_id = str(row.get("channel_id") or "").strip()
    human_id = str(row.get("human_id") or "").strip()
    if channel_id and human_id:
        return f"{channel_id}:{human_id}"
    run_id = str(row.get("run_id") or "").strip()
    if run_id:
        return f"run:{run_id}"
    request_id = str(row.get("request_id") or "").strip()
    if request_id:
        return f"request:{request_id}"
    return f"event:{row.get('event_id')}"


def _conversation_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel_kind": row.get("channel_id"),
        "component": row.get("component"),
        "run_ids": [row.get("run_id")] if row.get("run_id") else [],
        "request_ids": [row.get("request_id")] if row.get("request_id") else [],
        "trace_refs": [row.get("trace_ref")] if row.get("trace_ref") else [],
    }


def _merge_conversation_metadata(metadata: dict[str, Any], row: dict[str, Any]) -> None:
    for key, field in (("run_ids", "run_id"), ("request_ids", "request_id"), ("trace_refs", "trace_ref")):
        value = row.get(field)
        if value and value not in metadata[key]:
            metadata[key].append(value)


def _turn_content(row: dict[str, Any]) -> str:
    facts = row.get("facts_json") or {}
    if row.get("event_type") == "intent_committed":
        return str(facts.get("message_text") or "").strip()
    return str(facts.get("delivered_text") or "").strip()


def _message_id(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "")
    facts = row.get("facts_json") or {}
    if event_type == "intent_committed":
        candidate = facts.get("message_ref") or row.get("request_id")
        suffix = "user"
    else:
        candidate = facts.get("ack_ref") or facts.get("message_ref") or row.get("request_id")
        suffix = "assistant"
    text = str(candidate or "").strip()
    return text if text else f"{row.get('event_id')}:{suffix}"


def _normalize_event_row(row: Any) -> dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "row_order": int(row["row_order"]),
        "event_type": str(row["event_type"]),
        "created_at": str(row["created_at"]),
        "run_id": str(row["run_id"]) if row["run_id"] else None,
        "request_id": str(row["request_id"]) if row["request_id"] else None,
        "trace_ref": str(row["trace_ref"]) if row["trace_ref"] else None,
        "channel_id": str(row["channel_id"]) if row["channel_id"] else None,
        "session_id": str(row["session_id"]) if row["session_id"] else None,
        "human_id": str(row["human_id"]) if row["human_id"] else None,
        "component": str(row["component"]),
        "facts_json": _loads_json_value(row["facts_json"]),
        "provenance_json": _loads_json_value(row["provenance_json"]),
    }


def _loads_json_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_field(value: Any, field: str, default: Any = None) -> Any:
    payload = _loads_json_value(value)
    return payload.get(field, default)
