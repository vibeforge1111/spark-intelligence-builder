from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime, timezone
from types import ModuleType
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


DEFAULT_SDK_MODULE = "domain_chip_memory"
PREFERENCE_PREDICATE_PREFIX = "personality.preference."
_SDK_CLIENT_CACHE: dict[tuple[str, str], Any] = {}


@dataclass(frozen=True)
class MemoryWriteResult:
    status: str
    operation: str
    method: str
    memory_role: str
    accepted_count: int
    rejected_count: int
    skipped_count: int
    abstained: bool
    retrieval_trace: dict[str, Any] | None
    provenance: list[dict[str, Any]]
    reason: str | None = None


@dataclass(frozen=True)
class MemoryReadResult:
    status: str
    method: str
    memory_role: str
    records: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    retrieval_trace: dict[str, Any] | None
    answer_explanation: dict[str, Any] | None
    abstained: bool
    reason: str | None = None
    shadow_only: bool = False


class _DomainChipMemoryClientAdapter:
    def __init__(self, sdk: Any, module: ModuleType) -> None:
        self._sdk = sdk
        self._module = module
        self._sdk_module = self._resolve_sdk_module(module)

    def write_observation(self, **payload: Any) -> dict[str, Any]:
        request = self._module.MemoryWriteRequest(
            text=_memory_write_text(payload),
            speaker=str(payload.get("speaker") or "user"),
            timestamp=_optional_string(payload.get("timestamp")),
            session_id=_optional_string(payload.get("session_id")),
            turn_id=_optional_string(payload.get("turn_id")),
            operation=str(payload.get("operation") or "auto"),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            value=None if payload.get("value") is None else str(payload.get("value")),
            metadata=dict(payload.get("metadata") or {}),
        )
        result = self._sdk.write_observation(request)
        return _normalize_domain_write_result(result=result, default_role=str(payload.get("memory_role") or "current_state"))

    def write_event(self, **payload: Any) -> dict[str, Any]:
        request = self._module.MemoryWriteRequest(
            text=_memory_write_text(payload),
            speaker=str(payload.get("speaker") or "user"),
            timestamp=_optional_string(payload.get("timestamp")),
            session_id=_optional_string(payload.get("session_id")),
            turn_id=_optional_string(payload.get("turn_id")),
            operation=str(payload.get("operation") or "event"),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            value=None if payload.get("value") is None else str(payload.get("value")),
            metadata=dict(payload.get("metadata") or {}),
        )
        result = self._sdk.write_event(request)
        return _normalize_domain_write_result(result=result, default_role="event")

    def get_current_state(self, **payload: Any) -> dict[str, Any]:
        subject = _optional_string(payload.get("subject"))
        predicate = _optional_string(payload.get("predicate"))
        predicate_prefix = _optional_string(payload.get("predicate_prefix"))
        if subject and predicate_prefix and not predicate:
            return self._get_current_state_prefix(subject=subject, predicate_prefix=predicate_prefix)
        if not subject or not predicate:
            return {"status": "abstained", "reason": "invalid_request", "memory_role": "unknown"}
        request = self._module.CurrentStateRequest(subject=subject, predicate=predicate)
        result = self._sdk.get_current_state(request)
        return _normalize_domain_lookup_result(result=result, subject=subject, predicate=predicate)

    def get_historical_state(self, **payload: Any) -> dict[str, Any]:
        subject = _optional_string(payload.get("subject"))
        predicate = _optional_string(payload.get("predicate"))
        as_of = _optional_string(payload.get("as_of"))
        if not subject or not predicate or not as_of:
            return {"status": "abstained", "reason": "invalid_request", "memory_role": "unknown"}
        request = self._module.HistoricalStateRequest(subject=subject, predicate=predicate, as_of=as_of)
        result = self._sdk.get_historical_state(request)
        return _normalize_domain_lookup_result(result=result, subject=subject, predicate=predicate)

    def retrieve_evidence(self, **payload: Any) -> dict[str, Any]:
        request = self._module.EvidenceRetrievalRequest(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            limit=int(payload.get("limit") or 5),
        )
        result = self._sdk.retrieve_evidence(request)
        return _normalize_domain_retrieval_result(result=result)

    def retrieve_events(self, **payload: Any) -> dict[str, Any]:
        request = self._module.EventRetrievalRequest(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            limit=int(payload.get("limit") or 5),
        )
        result = self._sdk.retrieve_events(request)
        return _normalize_domain_retrieval_result(result=result)

    def explain_answer(self, **payload: Any) -> dict[str, Any]:
        subject = _optional_string(payload.get("subject"))
        predicate = _optional_string(payload.get("predicate"))
        question = _optional_string(payload.get("question")) or f"What is {predicate} for {subject}?"
        if not subject or not predicate:
            return {"status": "abstained", "reason": "invalid_request", "memory_role": "unknown"}
        request = self._module.AnswerExplanationRequest(
            question=question,
            subject=subject,
            predicate=predicate,
            as_of=_optional_string(payload.get("as_of")),
            evidence_limit=int(payload.get("evidence_limit") or 3),
            event_limit=int(payload.get("event_limit") or 3),
        )
        result = self._sdk.explain_answer(request)
        return {
            "status": "supported" if bool(result.found) else "not_found",
            "memory_role": str(result.memory_role or "unknown"),
            "records": [{"answer": result.answer}] if result.answer else [],
            "provenance": [_domain_record_to_dict(item) for item in result.provenance],
            "retrieval_trace": dict(result.trace or {}),
            "answer_explanation": {
                "answer": result.answer,
                "explanation": result.explanation,
                "evidence": [_domain_record_to_dict(item) for item in result.evidence],
                "events": [_domain_record_to_dict(item) for item in result.events],
            },
        }

    def _get_current_state_prefix(self, *, subject: str, predicate_prefix: str) -> dict[str, Any]:
        observations = self._sdk._current_state_observations()
        normalized_subject = self._sdk._normalize_subject(subject)
        reflected = self._sdk_module.build_current_state_view(observations)
        matches = [
            entry
            for entry in reflected
            if entry.subject == normalized_subject and str(entry.predicate or "").startswith(predicate_prefix)
        ]
        records = [_domain_current_state_record(entry) for entry in matches]
        provenance = [
            _domain_record_to_dict(self._sdk._observation_record(entry, memory_role="current_state"))
            for entry in matches
        ]
        return {
            "status": "supported" if records else "not_found",
            "memory_role": "current_state" if records else "unknown",
            "records": records,
            "provenance": provenance,
            "retrieval_trace": {
                "operation": "get_current_state",
                "subject": subject,
                "predicate_prefix": predicate_prefix,
                "record_count": len(records),
            },
            "answer_explanation": {
                "method": "get_current_state",
                "predicate_prefix": predicate_prefix,
            },
        }

    def _resolve_sdk_module(self, module: ModuleType) -> ModuleType:
        if hasattr(module, "build_current_state_view"):
            return module
        nested_name = f"{module.__name__}.sdk"
        return importlib.import_module(nested_name)


def write_personality_preferences_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    detected_deltas: dict[str, float],
    merged_deltas: dict[str, float],
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "personality_loader",
) -> MemoryWriteResult:
    if not detected_deltas:
        return MemoryWriteResult(
            status="skipped",
            operation="update",
            method="write_observation",
            memory_role="current_state",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_durable_preference_changes",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="update")
    if not bool(config_manager.get_path("spark.memory.write_personality_preferences", default=True)):
        return _disabled_write_result(operation="update", reason="personality_memory_writes_disabled")
    return _write_personality_observations(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        operation="update",
        trait_values={trait: merged_deltas[trait] for trait in sorted(detected_deltas)},
        detected_deltas=detected_deltas,
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        actor_id=actor_id,
    )


def delete_personality_preferences_from_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    existing_deltas: dict[str, float],
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "personality_loader",
) -> MemoryWriteResult:
    if not existing_deltas:
        return MemoryWriteResult(
            status="skipped",
            operation="delete",
            method="write_observation",
            memory_role="current_state",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_existing_preferences",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="delete")
    if not bool(config_manager.get_path("spark.memory.write_personality_preferences", default=True)):
        return _disabled_write_result(operation="delete", reason="personality_memory_writes_disabled")
    return _write_personality_observations(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        operation="delete",
        trait_values={trait: existing_deltas[trait] for trait in sorted(existing_deltas)},
        detected_deltas=None,
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        actor_id=actor_id,
    )


def read_personality_preferences_from_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str = "personality_loader",
) -> MemoryReadResult:
    if not _memory_enabled(config_manager):
        return _disabled_read_result()
    if not bool(config_manager.get_path("spark.memory.read_personality_preferences", default=True)):
        return _disabled_read_result(reason="personality_memory_reads_disabled")
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryReadResult(
            status="abstained",
            method="get_current_state",
            memory_role="current_state",
            records=[],
            provenance=[],
            retrieval_trace=None,
            answer_explanation=None,
            abstained=True,
            reason="sdk_unavailable",
            shadow_only=_memory_shadow_mode(config_manager),
        )
        _record_memory_read_event(
            state_db=state_db,
            result=result,
            human_id=human_id,
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
        return result
    payload = {
        "subject": f"human:{human_id}",
        "predicate_prefix": PREFERENCE_PREDICATE_PREFIX,
        "session_id": session_id,
        "turn_id": turn_id,
        "timestamp": _now_iso(),
        "metadata": {
            "entity_type": "human",
            "field_group": "personality_preferences",
            "memory_role": "current_state",
        },
    }
    _record_memory_read_requested(
        state_db=state_db,
        method="get_current_state",
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    raw = _call_sdk_method(client, "get_current_state", payload)
    result = _normalize_read_result(
        raw=raw,
        method="get_current_state",
        shadow_only=_memory_shadow_mode(config_manager),
    )
    _record_memory_read_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    return result


def _write_personality_observations(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    operation: str,
    trait_values: dict[str, float],
    detected_deltas: dict[str, float] | None,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str,
) -> MemoryWriteResult:
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation=operation,
            method="write_observation",
            memory_role="current_state",
            accepted_count=0,
            rejected_count=0,
            skipped_count=len(trait_values),
            abstained=True,
            retrieval_trace=None,
            provenance=[],
            reason="sdk_unavailable",
        )
        _record_memory_write_event(
            state_db=state_db,
            result=result,
            human_id=human_id,
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
        return result
    _record_memory_write_requested(
        state_db=state_db,
        operation=operation,
        human_id=human_id,
        trait_values=trait_values,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    accepted = 0
    rejected = 0
    skipped = 0
    provenance: list[dict[str, Any]] = []
    retrieval_trace: dict[str, Any] | None = None
    for trait, value in trait_values.items():
        payload = {
            "operation": "update" if operation != "delete" else "delete",
            "subject": f"human:{human_id}",
            "predicate": f"{PREFERENCE_PREDICATE_PREFIX}{trait}",
            "value": None if operation == "delete" else value,
            "memory_role": "current_state",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": _now_iso(),
            "metadata": {
                "entity_type": "human",
                "field_name": trait,
                "channel_kind": channel_kind,
                "memory_role": "current_state",
                "source_surface": "researcher_bridge",
                "detected_delta": detected_deltas.get(trait) if detected_deltas else None,
            },
        }
        raw = _call_sdk_method(client, "write_observation", payload)
        status = str(raw.get("status") or "").lower()
        if status in {"accepted", "written", "created", "updated", "deleted"}:
            accepted += 1
        elif status in {"rejected", "invalid_request", "unsupported"}:
            rejected += 1
        else:
            skipped += 1
        provenance.extend(_normalize_provenance(raw))
        if retrieval_trace is None and isinstance(raw.get("retrieval_trace"), dict):
            retrieval_trace = dict(raw["retrieval_trace"])
    result_status = "succeeded" if accepted > 0 and rejected == 0 else ("abstained" if accepted == 0 else "partial")
    result = MemoryWriteResult(
        status=result_status,
        operation=operation,
        method="write_observation",
        memory_role="current_state",
        accepted_count=accepted,
        rejected_count=rejected,
        skipped_count=skipped,
        abstained=accepted == 0,
        retrieval_trace=retrieval_trace,
        provenance=provenance,
        reason=None if accepted > 0 else "sdk_abstained_or_rejected",
    )
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    return result


def _load_sdk_client(config_manager: ConfigManager) -> Any | None:
    module_name = str(config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    cache_key = (module_name, str(config_manager.paths.home))
    if cache_key in _SDK_CLIENT_CACHE:
        return _SDK_CLIENT_CACHE[cache_key]
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    if hasattr(module, "SparkMemorySDK"):
        sdk_factory = getattr(module, "SparkMemorySDK")
        try:
            client = sdk_factory()
        except Exception:
            return None
        if _supports_domain_chip_memory_adapter(module):
            adapted = _DomainChipMemoryClientAdapter(client, module)
            _SDK_CLIENT_CACHE[cache_key] = adapted
            return adapted
        _SDK_CLIENT_CACHE[cache_key] = client
        return client
    _SDK_CLIENT_CACHE[cache_key] = module
    return module


def _call_sdk_method(client: Any, method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    target = getattr(client, method_name, None)
    if callable(target):
        result = target(**payload)
    elif isinstance(client, ModuleType):
        target = getattr(client, method_name, None)
        if not callable(target):
            return {"status": "abstained", "reason": "method_missing"}
        result = target(**payload)
    else:
        return {"status": "abstained", "reason": "method_missing"}
    if isinstance(result, dict):
        return result
    return {"status": "accepted", "result": result}


def _supports_domain_chip_memory_adapter(module: ModuleType) -> bool:
    return all(
        hasattr(module, attribute)
        for attribute in (
            "MemoryWriteRequest",
            "CurrentStateRequest",
            "HistoricalStateRequest",
            "EvidenceRetrievalRequest",
            "EventRetrievalRequest",
            "AnswerExplanationRequest",
        )
    )


def _memory_write_text(payload: dict[str, Any]) -> str:
    explicit = _optional_string(payload.get("text"))
    if explicit:
        return explicit
    subject = _optional_string(payload.get("subject")) or "unknown-subject"
    predicate = _optional_string(payload.get("predicate")) or "unknown-predicate"
    operation = _optional_string(payload.get("operation")) or "auto"
    value = payload.get("value")
    if value is None:
        return f"{operation} {subject} {predicate}"
    return f"{operation} {subject} {predicate} {value}"


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_domain_write_result(*, result: Any, default_role: str) -> dict[str, Any]:
    provenance = [
        _domain_record_to_dict(item)
        for item in [*list(getattr(result, "observations", []) or []), *list(getattr(result, "events", []) or [])]
    ]
    accepted = bool(getattr(result, "accepted", False))
    unsupported_reason = _optional_string(getattr(result, "unsupported_reason", None))
    return {
        "status": "accepted" if accepted else ("unsupported" if unsupported_reason else "rejected"),
        "memory_role": provenance[0]["memory_role"] if provenance else default_role,
        "provenance": provenance,
        "retrieval_trace": dict(getattr(result, "trace", {}) or {}),
        "reason": unsupported_reason,
        "accepted_count": int(getattr(result, "observations_written", 0) or 0) + int(getattr(result, "events_written", 0) or 0),
        "rejected_count": 0 if accepted else 1,
        "skipped_count": 0,
    }


def _normalize_domain_lookup_result(*, result: Any, subject: str, predicate: str) -> dict[str, Any]:
    provenance = [_domain_record_to_dict(item) for item in list(getattr(result, "provenance", []) or [])]
    records = []
    if bool(getattr(result, "found", False)):
        records.append(
            {
                "subject": subject,
                "predicate": predicate,
                "value": getattr(result, "value", None),
                "text": getattr(result, "text", None),
            }
        )
    return {
        "status": "supported" if bool(getattr(result, "found", False)) else "not_found",
        "memory_role": str(getattr(result, "memory_role", "unknown") or "unknown"),
        "records": records,
        "provenance": provenance,
        "retrieval_trace": dict(getattr(result, "trace", {}) or {}),
    }


def _normalize_domain_retrieval_result(*, result: Any) -> dict[str, Any]:
    records = [_domain_record_to_dict(item) for item in list(getattr(result, "items", []) or [])]
    return {
        "status": "supported" if records else "not_found",
        "memory_role": records[0]["memory_role"] if records else "unknown",
        "records": records,
        "provenance": records,
        "retrieval_trace": dict(getattr(result, "trace", {}) or {}),
    }


def _domain_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "memory_role": str(getattr(record, "memory_role", "unknown") or "unknown"),
        "subject": _optional_string(getattr(record, "subject", None)),
        "predicate": _optional_string(getattr(record, "predicate", None)),
        "text": _optional_string(getattr(record, "text", None)),
        "session_id": _optional_string(getattr(record, "session_id", None)),
        "turn_ids": list(getattr(record, "turn_ids", []) or []),
        "timestamp": _optional_string(getattr(record, "timestamp", None)),
        "metadata": dict(getattr(record, "metadata", {}) or {}),
        "value": getattr(record, "metadata", {}).get("value") if isinstance(getattr(record, "metadata", {}), dict) else None,
    }


def _domain_current_state_record(entry: Any) -> dict[str, Any]:
    metadata = dict(getattr(entry, "metadata", {}) or {})
    return {
        "subject": _optional_string(getattr(entry, "subject", None)),
        "predicate": _optional_string(getattr(entry, "predicate", None)),
        "value": metadata.get("value"),
        "text": _optional_string(getattr(entry, "text", None)),
        "session_id": _optional_string(getattr(entry, "session_id", None)),
        "turn_ids": list(getattr(entry, "turn_ids", []) or []),
        "timestamp": _optional_string(getattr(entry, "timestamp", None)),
        "metadata": metadata,
    }


def _normalize_read_result(
    *,
    raw: dict[str, Any],
    method: str,
    shadow_only: bool,
) -> MemoryReadResult:
    status = str(raw.get("status") or "").lower()
    abstained = status in {"abstained", "invalid_request", "unsupported", "not_found", ""}
    return MemoryReadResult(
        status=status or "abstained",
        method=method,
        memory_role=str(raw.get("memory_role") or "current_state"),
        records=_normalize_records(raw),
        provenance=_normalize_provenance(raw),
        retrieval_trace=dict(raw["retrieval_trace"]) if isinstance(raw.get("retrieval_trace"), dict) else None,
        answer_explanation=dict(raw["answer_explanation"]) if isinstance(raw.get("answer_explanation"), dict) else None,
        abstained=abstained,
        reason=str(raw.get("reason") or "") or None,
        shadow_only=shadow_only,
    )


def _normalize_records(raw: dict[str, Any]) -> list[dict[str, Any]]:
    records = raw.get("records")
    if isinstance(records, list):
        return [dict(record) for record in records if isinstance(record, dict)]
    if isinstance(raw.get("record"), dict):
        return [dict(raw["record"])]
    if isinstance(raw.get("state"), dict):
        return [dict(raw["state"])]
    return []


def _normalize_provenance(raw: dict[str, Any]) -> list[dict[str, Any]]:
    provenance = raw.get("provenance")
    if isinstance(provenance, list):
        return [dict(item) for item in provenance if isinstance(item, dict)]
    if isinstance(provenance, dict):
        return [dict(provenance)]
    return []


def _memory_enabled(config_manager: ConfigManager) -> bool:
    return bool(config_manager.get_path("spark.memory.enabled", default=False))


def _memory_shadow_mode(config_manager: ConfigManager) -> bool:
    return bool(config_manager.get_path("spark.memory.shadow_mode", default=True))


def _disabled_write_result(*, operation: str, reason: str = "memory_disabled") -> MemoryWriteResult:
    return MemoryWriteResult(
        status="disabled",
        operation=operation,
        method="write_observation",
        memory_role="current_state",
        accepted_count=0,
        rejected_count=0,
        skipped_count=0,
        abstained=True,
        retrieval_trace=None,
        provenance=[],
        reason=reason,
    )


def _disabled_read_result(reason: str = "memory_disabled") -> MemoryReadResult:
    return MemoryReadResult(
        status="disabled",
        method="get_current_state",
        memory_role="current_state",
        records=[],
        provenance=[],
        retrieval_trace=None,
        answer_explanation=None,
        abstained=True,
        reason=reason,
        shadow_only=False,
    )


def _record_memory_write_requested(
    *,
    state_db: StateDB,
    operation: str,
    human_id: str,
    trait_values: dict[str, float],
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    observations = [
        {
            "subject": f"human:{human_id}",
            "predicate": f"{PREFERENCE_PREDICATE_PREFIX}{trait}",
            "value": trait_values[trait],
            "operation": operation,
            "memory_role": "current_state",
        }
        for trait in sorted(trait_values)
    ]
    record_event(
        state_db,
        event_type="memory_write_requested",
        component="memory_orchestrator",
        summary="Spark memory write requested for durable personality preferences.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        facts={
            "operation": operation,
            "method": "write_observation",
            "memory_role": "current_state",
            "subject": f"human:{human_id}",
            "predicate_count": len(trait_values),
            "predicates": [f"{PREFERENCE_PREDICATE_PREFIX}{trait}" for trait in sorted(trait_values)],
            "observations": observations,
        },
        provenance={"memory_role": "current_state"},
    )


def _record_memory_write_event(
    *,
    state_db: StateDB,
    result: MemoryWriteResult,
    human_id: str,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    record_event(
        state_db,
        event_type="memory_write_succeeded" if result.accepted_count > 0 else "memory_write_abstained",
        component="memory_orchestrator",
        summary="Spark memory write completed." if result.accepted_count > 0 else "Spark memory write abstained.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        status="abstained" if result.abstained else "recorded",
        facts={
            "operation": result.operation,
            "method": result.method,
            "memory_role": result.memory_role,
            "accepted_count": result.accepted_count,
            "rejected_count": result.rejected_count,
            "skipped_count": result.skipped_count,
            "reason": result.reason,
            "retrieval_trace": result.retrieval_trace,
        },
        provenance={"memory_role": result.memory_role, "sdk_provenance": result.provenance},
    )


def _record_memory_read_requested(
    *,
    state_db: StateDB,
    method: str,
    human_id: str,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    record_event(
        state_db,
        event_type="memory_read_requested",
        component="memory_orchestrator",
        summary="Spark memory current-state lookup requested.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        facts={"method": method, "memory_role": "current_state", "subject": f"human:{human_id}"},
        provenance={"memory_role": "current_state"},
    )


def _record_memory_read_event(
    *,
    state_db: StateDB,
    result: MemoryReadResult,
    human_id: str,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    record_event(
        state_db,
        event_type="memory_read_succeeded" if not result.abstained else "memory_read_abstained",
        component="memory_orchestrator",
        summary="Spark memory read completed." if not result.abstained else "Spark memory read abstained.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        status="abstained" if result.abstained else "recorded",
        facts={
            "method": result.method,
            "memory_role": result.memory_role,
            "record_count": len(result.records),
            "reason": result.reason,
            "shadow_only": result.shadow_only,
            "retrieval_trace": result.retrieval_trace,
            "answer_explanation": result.answer_explanation,
        },
        provenance={"memory_role": result.memory_role, "sdk_provenance": result.provenance},
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
