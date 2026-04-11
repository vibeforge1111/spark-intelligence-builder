from __future__ import annotations

import importlib
import json
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory_contracts import (
    annotate_contract_trace,
    memory_contract_reason,
    normalize_memory_role,
)
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


DEFAULT_SDK_MODULE = "domain_chip_memory"
DEFAULT_DOMAIN_CHIP_MEMORY_ROOT = Path.home() / "Desktop" / "domain-chip-memory"
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


@dataclass(frozen=True)
class MemorySdkSmokeResult:
    sdk_module: str
    subject: str
    predicate: str
    value: str
    write_result: MemoryWriteResult
    read_result: MemoryReadResult
    cleanup_result: MemoryWriteResult | None
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "subject": self.subject,
                "predicate": self.predicate,
                "value": self.value,
                "shadow_only_eval": self.shadow_only_eval,
                "write_result": self._write_payload(self.write_result),
                "read_result": self._read_payload(self.read_result),
                "cleanup_result": self._write_payload(self.cleanup_result) if self.cleanup_result is not None else None,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory direct smoke"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- predicate: {self.predicate}")
        lines.append(f"- value: {self.value}")
        lines.append(f"- shadow_only_eval: {'yes' if self.shadow_only_eval else 'no'}")
        lines.append(
            f"- write: status={self.write_result.status} accepted={self.write_result.accepted_count} "
            f"rejected={self.write_result.rejected_count}"
        )
        lines.append(
            f"- read: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.cleanup_result is not None:
            lines.append(
                f"- cleanup: status={self.cleanup_result.status} accepted={self.cleanup_result.accepted_count} "
                f"rejected={self.cleanup_result.rejected_count}"
            )
        return "\n".join(lines)

    @staticmethod
    def _write_payload(result: MemoryWriteResult) -> dict[str, Any]:
        return {
            "status": result.status,
            "operation": result.operation,
            "method": result.method,
            "memory_role": result.memory_role,
            "accepted_count": result.accepted_count,
            "rejected_count": result.rejected_count,
            "skipped_count": result.skipped_count,
            "abstained": result.abstained,
            "reason": result.reason,
            "retrieval_trace": result.retrieval_trace,
            "provenance": result.provenance,
        }

    @staticmethod
    def _read_payload(result: MemoryReadResult) -> dict[str, Any]:
        return {
            "status": result.status,
            "method": result.method,
            "memory_role": result.memory_role,
            "records": result.records,
            "provenance": result.provenance,
            "retrieval_trace": result.retrieval_trace,
            "answer_explanation": result.answer_explanation,
            "abstained": result.abstained,
            "reason": result.reason,
            "shadow_only": result.shadow_only,
        }


@dataclass(frozen=True)
class MemoryCurrentStateLookupResult:
    sdk_module: str
    subject: str
    predicate: str
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "subject": self.subject,
                "predicate": self.predicate,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory current-state lookup"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- predicate: {self.predicate}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- read: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.read_result.records:
            lines.append(f"- value: {self.read_result.records[0].get('value')}")
        if self.read_result.reason:
            lines.append(f"- reason: {self.read_result.reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class MemoryHistoricalStateLookupResult:
    sdk_module: str
    subject: str
    predicate: str
    as_of: str
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "subject": self.subject,
                "predicate": self.predicate,
                "as_of": self.as_of,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory historical-state lookup"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- predicate: {self.predicate}")
        lines.append(f"- as_of: {self.as_of}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- read: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.read_result.records:
            lines.append(f"- value: {self.read_result.records[0].get('value')}")
        if self.read_result.reason:
            lines.append(f"- reason: {self.read_result.reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class HumanMemoryInspectionResult:
    sdk_module: str
    human_id: str
    subject: str
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "human_id": self.human_id,
                "subject": self.subject,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory human inspection"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- human_id: {self.human_id}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- current_state: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.read_result.reason:
            lines.append(f"- reason: {self.read_result.reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class MemoryAnswerExplanationResult:
    sdk_module: str
    subject: str
    predicate: str
    question: str
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "subject": self.subject,
                "predicate": self.predicate,
                "question": self.question,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory answer explanation"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- predicate: {self.predicate}")
        lines.append(f"- question: {self.question}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- explain_answer: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.read_result.records:
            lines.append(f"- answer: {self.read_result.records[0].get('answer')}")
        explanation = self.read_result.answer_explanation or {}
        if explanation.get("explanation"):
            lines.append(f"- explanation: {explanation.get('explanation')}")
        if self.read_result.reason:
            lines.append(f"- reason: {self.read_result.reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class MemoryRetrievalQueryResult:
    sdk_module: str
    method: str
    query: str
    subject: str | None
    predicate: str | None
    limit: int
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "method": self.method,
                "query": self.query,
                "subject": self.subject,
                "predicate": self.predicate,
                "limit": self.limit,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Spark memory {self.method} retrieval"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- query: {self.query}")
        lines.append(f"- subject: {self.subject or 'none'}")
        lines.append(f"- predicate: {self.predicate or 'none'}")
        lines.append(f"- limit: {self.limit}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- {self.method}: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.read_result.reason:
            lines.append(f"- reason: {self.read_result.reason}")
        return "\n".join(lines)


class _DomainChipMemoryClientAdapter:
    def __init__(self, sdk: Any, module: ModuleType, *, persistence_path: Path | None = None) -> None:
        self._sdk = sdk
        self._module = module
        self._sdk_module = self._resolve_sdk_module(module)
        self._persistence_path = persistence_path

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
        self._persist_manual_state()
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
        self._persist_manual_state()
        return _normalize_domain_write_result(result=result, default_role="event")

    def get_current_state(self, **payload: Any) -> dict[str, Any]:
        subject = _optional_string(payload.get("subject"))
        predicate = _optional_string(payload.get("predicate"))
        raw_predicate_prefix = payload.get("predicate_prefix")
        predicate_prefix = None if raw_predicate_prefix is None else str(raw_predicate_prefix)
        if subject and raw_predicate_prefix is not None and not predicate:
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
        return _normalize_domain_retrieval_result(result=result, method="retrieve_evidence")

    def retrieve_events(self, **payload: Any) -> dict[str, Any]:
        request = self._module.EventRetrievalRequest(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            limit=int(payload.get("limit") or 5),
        )
        result = self._sdk.retrieve_events(request)
        return _normalize_domain_retrieval_result(result=result, method="retrieve_events")

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
        raw_memory_role = result.memory_role
        memory_role = normalize_memory_role(raw_memory_role, allow_unknown=not bool(result.answer))
        retrieval_trace = dict(result.trace or {})
        contract_reason = memory_contract_reason(
            memory_role=raw_memory_role,
            method="get_current_state",
            allow_unknown=not bool(result.answer),
        )
        if contract_reason:
            return {
                "status": "abstained",
                "memory_role": "unknown",
                "records": [],
                "provenance": [_domain_record_to_dict(item) for item in result.provenance],
                "retrieval_trace": annotate_contract_trace(
                    retrieval_trace,
                    scope="domain_answer_explanation",
                    reason=contract_reason,
                    observed_role=memory_role,
                    method="get_current_state",
                ),
                "answer_explanation": None,
                "reason": contract_reason,
            }
        return {
            "status": "supported" if bool(result.found) else "not_found",
            "memory_role": memory_role,
            "records": [{"answer": result.answer}] if result.answer else [],
            "provenance": [_domain_record_to_dict(item) for item in result.provenance],
            "retrieval_trace": retrieval_trace,
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
        return _import_memory_sdk_module(nested_name)

    def _persist_manual_state(self) -> None:
        if self._persistence_path is None:
            return
        payload = {
            "manual_observations": [asdict(entry) for entry in list(getattr(self._sdk, "_manual_observations", []) or [])],
            "manual_events": [asdict(entry) for entry in list(getattr(self._sdk, "_manual_events", []) or [])],
            "manual_current_state_snapshot": [
                asdict(entry) for entry in list(getattr(self._sdk, "_manual_current_state_snapshot", []) or [])
            ],
        }
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_memory_sdk_smoke_test(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    sdk_module: str | None = None,
    subject: str = "human:smoke:test",
    predicate: str = "system.memory.smoke",
    value: str = "ok",
    cleanup: bool = True,
    actor_id: str = "memory_cli",
) -> MemorySdkSmokeResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        write_result = _normalize_write_result(
            raw={"status": "abstained", "reason": "sdk_unavailable"},
            operation="update",
        )
        read_result = _disabled_read_result(reason="sdk_unavailable")
        cleanup_result = _disabled_write_result(operation="delete", reason="sdk_unavailable") if cleanup else None
        result = MemorySdkSmokeResult(
            sdk_module=module_name,
            subject=subject,
            predicate=predicate,
            value=value,
            write_result=write_result,
            read_result=read_result,
            cleanup_result=cleanup_result,
        )
        _record_memory_smoke_event(state_db=state_db, result=result, actor_id=actor_id)
        return result
    session_id = f"memory-smoke:{actor_id}"
    write_turn_id = f"{actor_id}:write"
    read_turn_id = f"{actor_id}:read"
    cleanup_turn_id = f"{actor_id}:cleanup"
    payload = {
        "operation": "update",
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "memory_role": "current_state",
        "session_id": session_id,
        "turn_id": write_turn_id,
        "timestamp": _now_iso(),
        "metadata": {
            "source_surface": "memory_cli_smoke",
            "smoke_test": True,
            "value": value,
        },
    }
    raw_write = _call_sdk_method(client, "write_observation", payload)
    write_result = _normalize_write_result(raw=raw_write, operation="update")
    raw_read = _call_sdk_method(
        client,
        "get_current_state",
        {
            "subject": subject,
            "predicate": predicate,
            "session_id": session_id,
            "turn_id": read_turn_id,
            "timestamp": _now_iso(),
            "metadata": {"source_surface": "memory_cli_smoke", "smoke_test": True},
        },
    )
    read_result = _normalize_read_result(raw=raw_read, method="get_current_state", shadow_only=False)
    cleanup_result: MemoryWriteResult | None = None
    if cleanup:
        raw_cleanup = _call_sdk_method(
            client,
            "write_observation",
            {
                "operation": "delete",
                "subject": subject,
                "predicate": predicate,
                "memory_role": "current_state",
                "session_id": session_id,
                "turn_id": cleanup_turn_id,
                "timestamp": _now_iso(),
                "metadata": {"source_surface": "memory_cli_smoke", "smoke_test": True},
            },
        )
        cleanup_result = _normalize_write_result(raw=raw_cleanup, operation="delete")
    result = MemorySdkSmokeResult(
        sdk_module=module_name,
        subject=subject,
        predicate=predicate,
        value=value,
        write_result=write_result,
        read_result=read_result,
        cleanup_result=cleanup_result,
    )
    _record_memory_smoke_event(state_db=state_db, result=result, actor_id=actor_id)
    return result


@contextmanager
def _prepend_sys_path(path: Path):
    inserted = False
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path_str)
            except ValueError:
                pass


def _local_domain_chip_memory_src() -> Path:
    return DEFAULT_DOMAIN_CHIP_MEMORY_ROOT / "src"


def _import_memory_sdk_module(module_name: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        root_name = module_name.split(".", 1)[0]
        local_src = _local_domain_chip_memory_src()
        if exc.name != root_name or root_name != "domain_chip_memory" or not local_src.exists():
            raise
        with _prepend_sys_path(local_src):
            return importlib.import_module(module_name)


def inspect_memory_sdk_runtime(
    *,
    config_manager: ConfigManager,
    sdk_module: str | None = None,
) -> dict[str, Any]:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    payload: dict[str, Any] = {
        "configured_module": module_name,
        "memory_enabled": _memory_enabled(config_manager),
        "shadow_mode": _memory_shadow_mode(config_manager),
        "ready": False,
        "resolved_module": None,
        "client_kind": None,
        "runtime_class": None,
        "runtime_memory_architecture": None,
        "runtime_memory_provider": None,
        "reason": None,
    }
    try:
        module = _import_memory_sdk_module(module_name)
    except Exception as exc:
        payload["reason"] = f"import_failed:{type(exc).__name__}"
        return payload
    payload["resolved_module"] = module.__name__
    if hasattr(module, "SparkMemorySDK"):
        sdk_factory = getattr(module, "SparkMemorySDK")
        try:
            client = sdk_factory()
        except Exception as exc:
            payload["reason"] = f"sdk_init_failed:{type(exc).__name__}"
            return payload
        if hasattr(module, "build_sdk_contract_summary"):
            try:
                contract_summary = getattr(module, "build_sdk_contract_summary")() or {}
            except Exception as exc:
                payload["reason"] = f"sdk_contract_summary_failed:{type(exc).__name__}"
                return payload
            payload["runtime_class"] = contract_summary.get("runtime_class")
            payload["runtime_memory_architecture"] = contract_summary.get("runtime_memory_architecture")
            payload["runtime_memory_provider"] = contract_summary.get("runtime_memory_provider")
        if _supports_domain_chip_memory_adapter(module):
            payload["client_kind"] = _DomainChipMemoryClientAdapter.__name__
        else:
            payload["client_kind"] = type(client).__name__
        payload["ready"] = True
        return payload
    payload["reason"] = "sdk_factory_missing"
    return payload


def lookup_current_state_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    subject: str,
    predicate: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
) -> MemoryCurrentStateLookupResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method="get_current_state",
            memory_role="current_state",
            records=[],
            provenance=[],
            retrieval_trace=None,
            answer_explanation=None,
            abstained=True,
            reason="sdk_unavailable",
            shadow_only=False,
        )
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=_human_id_from_subject(subject),
            session_id=f"memory-lookup:{actor_id}",
            turn_id=f"{actor_id}:lookup",
            actor_id=actor_id,
        )
        return MemoryCurrentStateLookupResult(
            sdk_module=module_name,
            subject=subject,
            predicate=predicate,
            runtime=runtime,
            read_result=read_result,
        )
    _, read_result = _get_current_state_with_subject_fallback(
        client=client,
        state_db=state_db,
        subject_candidates=_subject_fallback_candidates(subject),
        predicate=predicate,
        predicate_prefix=None,
        session_id=f"memory-lookup:{actor_id}",
        turn_id=f"{actor_id}:lookup",
        actor_id=actor_id,
        source_surface="memory_cli_lookup",
    )
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=_human_id_from_subject(subject),
        session_id=f"memory-lookup:{actor_id}",
        turn_id=f"{actor_id}:lookup",
        actor_id=actor_id,
    )
    return MemoryCurrentStateLookupResult(
        sdk_module=module_name,
        subject=subject,
        predicate=predicate,
        runtime=runtime,
        read_result=read_result,
    )


def lookup_historical_state_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    subject: str,
    predicate: str,
    as_of: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
) -> MemoryHistoricalStateLookupResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method="get_historical_state",
            memory_role="current_state",
            records=[],
            provenance=[],
            retrieval_trace=None,
            answer_explanation=None,
            abstained=True,
            reason="sdk_unavailable",
            shadow_only=False,
        )
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=_human_id_from_subject(subject),
            session_id=f"memory-history:{actor_id}",
            turn_id=f"{actor_id}:lookup-history",
            actor_id=actor_id,
        )
        return MemoryHistoricalStateLookupResult(
            sdk_module=module_name,
            subject=subject,
            predicate=predicate,
            as_of=as_of,
            runtime=runtime,
            read_result=read_result,
        )
    _, read_result = _get_historical_state_with_subject_fallback(
        client=client,
        state_db=state_db,
        subject_candidates=_subject_fallback_candidates(subject),
        predicate=predicate,
        as_of=as_of,
        session_id=f"memory-history:{actor_id}",
        turn_id=f"{actor_id}:lookup-history",
        actor_id=actor_id,
        source_surface="memory_cli_lookup_historical",
    )
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=_human_id_from_subject(subject),
        session_id=f"memory-history:{actor_id}",
        turn_id=f"{actor_id}:lookup-history",
        actor_id=actor_id,
    )
    return MemoryHistoricalStateLookupResult(
        sdk_module=module_name,
        subject=subject,
        predicate=predicate,
        as_of=as_of,
        runtime=runtime,
        read_result=read_result,
    )


def inspect_human_memory_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
) -> HumanMemoryInspectionResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    subject = _subject_for_human_id(human_id)
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method="get_current_state",
            memory_role="current_state",
            records=[],
            provenance=[],
            retrieval_trace=None,
            answer_explanation=None,
            abstained=True,
            reason="sdk_unavailable",
            shadow_only=False,
        )
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=human_id,
            session_id=f"memory-inspect:{actor_id}",
            turn_id=f"{actor_id}:inspect-human",
            actor_id=actor_id,
        )
        return HumanMemoryInspectionResult(
            sdk_module=module_name,
            human_id=human_id,
            subject=subject,
            runtime=runtime,
            read_result=read_result,
        )
    _, read_result = _get_current_state_with_subject_fallback(
        client=client,
        state_db=state_db,
        subject_candidates=_subject_fallback_candidates(subject),
        predicate=None,
        predicate_prefix="",
        session_id=f"memory-inspect:{actor_id}",
        turn_id=f"{actor_id}:inspect-human",
        actor_id=actor_id,
        source_surface="memory_cli_inspect_human",
    )
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=human_id,
        session_id=f"memory-inspect:{actor_id}",
        turn_id=f"{actor_id}:inspect-human",
        actor_id=actor_id,
    )
    return HumanMemoryInspectionResult(
        sdk_module=module_name,
        human_id=human_id,
        subject=subject,
        runtime=runtime,
        read_result=read_result,
    )


def explain_memory_answer_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    subject: str,
    predicate: str,
    question: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
    as_of: str | None = None,
    evidence_limit: int = 3,
    event_limit: int = 3,
) -> MemoryAnswerExplanationResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method="explain_answer",
            memory_role="current_state",
            records=[],
            provenance=[],
            retrieval_trace=None,
            answer_explanation=None,
            abstained=True,
            reason="sdk_unavailable",
            shadow_only=False,
        )
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=_human_id_from_subject(subject),
            session_id=f"memory-explain:{actor_id}",
            turn_id=f"{actor_id}:explain-answer",
            actor_id=actor_id,
        )
        return MemoryAnswerExplanationResult(
            sdk_module=module_name,
            subject=subject,
            predicate=predicate,
            question=question,
            runtime=runtime,
            read_result=read_result,
        )
    _record_memory_read_requested_subject(
        state_db=state_db,
        method="explain_answer",
        subject=subject,
        predicate=predicate,
        query=question,
        session_id=f"memory-explain:{actor_id}",
        turn_id=f"{actor_id}:explain-answer",
        actor_id=actor_id,
    )
    raw = _call_sdk_method(
        client,
        "explain_answer",
        {
            "question": question,
            "subject": subject,
            "predicate": predicate,
            "as_of": as_of,
            "evidence_limit": evidence_limit,
            "event_limit": event_limit,
            "session_id": f"memory-explain:{actor_id}",
            "turn_id": f"{actor_id}:explain-answer",
            "timestamp": _now_iso(),
            "metadata": {"source_surface": "memory_cli_explain_answer"},
        },
    )
    read_result = _normalize_read_result(raw=raw, method="explain_answer", shadow_only=False)
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=_human_id_from_subject(subject),
        session_id=f"memory-explain:{actor_id}",
        turn_id=f"{actor_id}:explain-answer",
        actor_id=actor_id,
    )
    return MemoryAnswerExplanationResult(
        sdk_module=module_name,
        subject=subject,
        predicate=predicate,
        question=question,
        runtime=runtime,
        read_result=read_result,
    )


def retrieve_memory_evidence_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    query: str,
    subject: str | None = None,
    predicate: str | None = None,
    limit: int = 5,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
) -> MemoryRetrievalQueryResult:
    return _retrieve_memory_query_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        method="retrieve_evidence",
        query=query,
        subject=subject,
        predicate=predicate,
        limit=limit,
        sdk_module=sdk_module,
        actor_id=actor_id,
    )


def retrieve_memory_events_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    query: str,
    subject: str | None = None,
    predicate: str | None = None,
    limit: int = 5,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
) -> MemoryRetrievalQueryResult:
    return _retrieve_memory_query_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        method="retrieve_events",
        query=query,
        subject=subject,
        predicate=predicate,
        limit=limit,
        sdk_module=sdk_module,
        actor_id=actor_id,
    )


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


def write_profile_fact_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    value: str,
    evidence_text: str,
    fact_name: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "profile_fact_loader",
) -> MemoryWriteResult:
    if not predicate or not str(value or "").strip():
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
            reason="no_durable_profile_fact",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="update")
    if not bool(config_manager.get_path("spark.memory.write_profile_facts", default=True)):
        return _disabled_write_result(operation="update", reason="profile_fact_memory_writes_disabled")
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="update",
            method="write_observation",
            memory_role="current_state",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
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
    subject = _subject_for_human_id(human_id)
    observation = {
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "operation": "update",
        "memory_role": "current_state",
        "text": evidence_text,
    }
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="update",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "update",
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "text": evidence_text,
            "memory_role": "current_state",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": _now_iso(),
            "metadata": {
                "entity_type": "human",
                "field_name": predicate.rsplit(".", 1)[-1],
                "channel_kind": channel_kind,
                "memory_role": "current_state",
                "source_surface": "researcher_bridge",
                "fact_name": fact_name,
                "normalized_value": value,
            },
        },
    )
    result = _normalize_write_result(raw=raw, operation="update")
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    return result


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
    subject = _subject_for_human_id(human_id)
    _record_memory_read_requested(
        state_db=state_db,
        method="get_current_state",
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    _, result = _get_current_state_with_subject_fallback(
        client=client,
        state_db=state_db,
        subject_candidates=_subject_fallback_candidates(subject),
        predicate=None,
        predicate_prefix=PREFERENCE_PREDICATE_PREFIX,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        source_surface="personality_preferences",
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
    subject = _subject_for_human_id(human_id)
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
    first_reason: str | None = None
    for trait, value in trait_values.items():
        payload = {
            "operation": "update" if operation != "delete" else "delete",
            "subject": subject,
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
        item_result = _normalize_write_result(raw=raw, operation=payload["operation"])
        accepted += item_result.accepted_count
        rejected += item_result.rejected_count
        skipped += item_result.skipped_count
        provenance.extend(item_result.provenance)
        if retrieval_trace is None and item_result.retrieval_trace is not None:
            retrieval_trace = dict(item_result.retrieval_trace)
        if first_reason is None and item_result.reason:
            first_reason = item_result.reason
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
        reason=None if accepted > 0 else (first_reason or "sdk_abstained_or_rejected"),
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
    return _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)


def _load_sdk_client_for_module(*, module_name: str, home_path: Any) -> Any | None:
    cache_key = (module_name, str(home_path))
    if cache_key in _SDK_CLIENT_CACHE:
        return _SDK_CLIENT_CACHE[cache_key]
    try:
        module = _import_memory_sdk_module(module_name)
    except Exception:
        return None
    if hasattr(module, "SparkMemorySDK"):
        sdk_factory = getattr(module, "SparkMemorySDK")
        try:
            client = sdk_factory()
        except Exception:
            return None
        if _supports_domain_chip_memory_adapter(module):
            persistence_path = _domain_chip_memory_persistence_path(home_path)
            _hydrate_domain_chip_memory_sdk(client=client, module=module, persistence_path=persistence_path)
            adapted = _DomainChipMemoryClientAdapter(client, module, persistence_path=persistence_path)
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


def _domain_chip_memory_persistence_path(home_path: Any) -> Path:
    return Path(str(home_path)) / "artifacts" / "domain_chip_memory_sdk_state.json"


def _hydrate_domain_chip_memory_sdk(*, client: Any, module: ModuleType, persistence_path: Path) -> None:
    if not persistence_path.exists():
        return
    try:
        payload = json.loads(persistence_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    sdk_module = module if hasattr(module, "ObservationEntry") else _import_memory_sdk_module(f"{module.__name__}.sdk")
    observation_cls = getattr(sdk_module, "ObservationEntry", None)
    event_cls = getattr(sdk_module, "EventCalendarEntry", None)
    if observation_cls is None or event_cls is None:
        return
    client._manual_observations = _hydrate_domain_entries(payload.get("manual_observations"), observation_cls)
    client._manual_events = _hydrate_domain_entries(payload.get("manual_events"), event_cls)
    client._manual_current_state_snapshot = _hydrate_domain_entries(
        payload.get("manual_current_state_snapshot"),
        observation_cls,
    )


def _hydrate_domain_entries(raw_entries: Any, entry_cls: Any) -> list[Any]:
    if not isinstance(raw_entries, list):
        return []
    hydrated: list[Any] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        try:
            hydrated.append(entry_cls(**raw))
        except Exception:
            continue
    return hydrated


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


def _human_id_from_subject(subject: str) -> str | None:
    text = _optional_string(subject)
    if not text:
        return None
    if text.startswith("human:"):
        return text.split(":", 1)[1] or None
    return text


def _subject_for_human_id(human_id: str) -> str:
    text = _optional_string(human_id) or ""
    if text.startswith("human:"):
        return text
    return f"human:{text}"


def _subject_fallback_candidates(subject: str) -> tuple[str, ...]:
    normalized_subject = _optional_string(subject)
    if not normalized_subject:
        return ()
    candidates = [normalized_subject]
    if normalized_subject.startswith("human:") and not normalized_subject.startswith("human:human:"):
        candidates.append(f"human:{normalized_subject}")
    return tuple(dict.fromkeys(candidates))


def _get_current_state_with_subject_fallback(
    *,
    client: Any,
    state_db: StateDB,
    subject_candidates: tuple[str, ...],
    predicate: str | None,
    predicate_prefix: str | None,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
    source_surface: str,
    shadow_only: bool = False,
) -> tuple[str, MemoryReadResult]:
    last_subject = ""
    last_result = _disabled_read_result(reason="invalid_subject")
    for subject in subject_candidates:
        last_subject = subject
        _record_memory_read_requested_subject(
            state_db=state_db,
            method="get_current_state",
            subject=subject,
            predicate=predicate,
            predicate_prefix=predicate_prefix,
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
        payload = {
            "subject": subject,
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": _now_iso(),
            "metadata": {"source_surface": source_surface},
        }
        if predicate is not None:
            payload["predicate"] = predicate
        if predicate_prefix is not None:
            payload["predicate_prefix"] = predicate_prefix
        raw = _call_sdk_method(client, "get_current_state", payload)
        last_result = _normalize_read_result(raw=raw, method="get_current_state", shadow_only=shadow_only)
        if last_result.records:
            return subject, last_result
    return last_subject, last_result


def _get_historical_state_with_subject_fallback(
    *,
    client: Any,
    state_db: StateDB,
    subject_candidates: tuple[str, ...],
    predicate: str,
    as_of: str,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
    source_surface: str,
    shadow_only: bool = False,
) -> tuple[str, MemoryReadResult]:
    last_subject = ""
    last_result = _disabled_read_result(reason="invalid_subject")
    for subject in subject_candidates:
        last_subject = subject
        _record_memory_read_requested_subject(
            state_db=state_db,
            method="get_historical_state",
            subject=subject,
            predicate=predicate,
            as_of=as_of,
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
        raw = _call_sdk_method(
            client,
            "get_historical_state",
            {
                "subject": subject,
                "predicate": predicate,
                "as_of": as_of,
                "session_id": session_id,
                "turn_id": turn_id,
                "timestamp": _now_iso(),
                "metadata": {"source_surface": source_surface},
            },
        )
        last_result = _normalize_read_result(raw=raw, method="get_historical_state", shadow_only=shadow_only)
        if last_result.records:
            return subject, last_result
    return last_subject, last_result


def _normalize_domain_write_result(*, result: Any, default_role: str) -> dict[str, Any]:
    provenance = [
        _domain_record_to_dict(item)
        for item in [*list(getattr(result, "observations", []) or []), *list(getattr(result, "events", []) or [])]
    ]
    accepted = bool(getattr(result, "accepted", False))
    unsupported_reason = _optional_string(getattr(result, "unsupported_reason", None))
    operation = default_role if default_role == "event" else str(getattr(result, "operation", None) or "update")
    memory_role = provenance[0]["memory_role"] if provenance else "unknown"
    if memory_role == "unknown" and accepted:
        memory_role = default_role
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    contract_reason = memory_contract_reason(
        memory_role=memory_role,
        operation=operation,
        allow_unknown=not accepted,
    )
    if contract_reason:
        return {
            "status": "abstained",
            "memory_role": "unknown",
            "provenance": provenance,
            "retrieval_trace": annotate_contract_trace(
                retrieval_trace,
                scope="domain_write_result",
                reason=contract_reason,
                observed_role=memory_role,
                operation=operation,
            ),
            "reason": contract_reason,
            "accepted_count": 0,
            "rejected_count": 1 if accepted else 0,
            "skipped_count": 0,
        }
    return {
        "status": "accepted" if accepted else ("unsupported" if unsupported_reason else "rejected"),
        "memory_role": memory_role,
        "provenance": provenance,
        "retrieval_trace": retrieval_trace,
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
    raw_memory_role = getattr(result, "memory_role", "unknown")
    memory_role = normalize_memory_role(raw_memory_role, allow_unknown=not records)
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    contract_reason = memory_contract_reason(
        memory_role=raw_memory_role,
        method="get_current_state",
        allow_unknown=not records,
    )
    if contract_reason:
        return {
            "status": "abstained",
            "memory_role": "unknown",
            "records": [],
            "provenance": provenance,
            "retrieval_trace": annotate_contract_trace(
                retrieval_trace,
                scope="domain_lookup_result",
                reason=contract_reason,
                observed_role=memory_role,
                method="get_current_state",
            ),
            "reason": contract_reason,
        }
    return {
        "status": "supported" if bool(getattr(result, "found", False)) else "not_found",
        "memory_role": memory_role,
        "records": records,
        "provenance": provenance,
        "retrieval_trace": retrieval_trace,
    }


def _normalize_domain_retrieval_result(*, result: Any, method: str) -> dict[str, Any]:
    records = [_domain_record_to_dict(item) for item in list(getattr(result, "items", []) or [])]
    memory_role = records[0]["memory_role"] if records else "unknown"
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    contract_reason = memory_contract_reason(
        memory_role=memory_role,
        method=method,
        allow_unknown=not records,
    )
    if contract_reason:
        return {
            "status": "abstained",
            "memory_role": "unknown",
            "records": [],
            "provenance": records,
            "retrieval_trace": annotate_contract_trace(
                retrieval_trace,
                scope="domain_retrieval_result",
                reason=contract_reason,
                observed_role=memory_role,
                method=method,
            ),
            "reason": contract_reason,
        }
    return {
        "status": "supported" if records else "not_found",
        "memory_role": memory_role,
        "records": records,
        "provenance": records,
        "retrieval_trace": retrieval_trace,
    }


def _domain_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "memory_role": normalize_memory_role(getattr(record, "memory_role", "unknown"), allow_unknown=True),
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
    raw_memory_role = raw.get("memory_role")
    memory_role = normalize_memory_role(raw_memory_role, allow_unknown=abstained)
    retrieval_trace = dict(raw["retrieval_trace"]) if isinstance(raw.get("retrieval_trace"), dict) else None
    reason = str(raw.get("reason") or "") or None
    contract_reason = memory_contract_reason(
        memory_role=raw_memory_role,
        method=method,
        allow_unknown=abstained,
    )
    if contract_reason:
        return MemoryReadResult(
            status="abstained",
            method=method,
            memory_role="unknown",
            records=[],
            provenance=_normalize_provenance(raw),
            retrieval_trace=annotate_contract_trace(
                retrieval_trace,
                scope="read_result",
                reason=contract_reason,
                observed_role=memory_role,
                method=method,
            ),
            answer_explanation=None,
            abstained=True,
            reason=contract_reason,
            shadow_only=shadow_only,
        )
    return MemoryReadResult(
        status=status or "abstained",
        method=method,
        memory_role=memory_role,
        records=_normalize_records(raw),
        provenance=_normalize_provenance(raw),
        retrieval_trace=retrieval_trace,
        answer_explanation=dict(raw["answer_explanation"]) if isinstance(raw.get("answer_explanation"), dict) else None,
        abstained=abstained,
        reason=reason,
        shadow_only=shadow_only,
    )


def _normalize_write_result(
    *,
    raw: dict[str, Any],
    operation: str,
    method: str = "write_observation",
    default_role: str = "current_state",
) -> MemoryWriteResult:
    status = str(raw.get("status") or "").lower()
    accepted_count = int(raw.get("accepted_count") or 0)
    rejected_count = int(raw.get("rejected_count") or 0)
    skipped_count = int(raw.get("skipped_count") or 0)
    if status in {"accepted", "written", "created", "updated", "deleted"} and accepted_count <= 0:
        accepted_count = 1
    abstained = status in {"abstained", "invalid_request", "unsupported", "not_found", "", "disabled"} or accepted_count == 0
    raw_memory_role = raw.get("memory_role")
    memory_role = normalize_memory_role(raw_memory_role, allow_unknown=accepted_count == 0)
    retrieval_trace = dict(raw["retrieval_trace"]) if isinstance(raw.get("retrieval_trace"), dict) else None
    reason = str(raw.get("reason") or "") or None
    contract_reason = memory_contract_reason(
        memory_role=raw_memory_role,
        operation=operation,
        allow_unknown=accepted_count == 0,
    )
    if contract_reason:
        return MemoryWriteResult(
            status="abstained",
            operation=operation,
            method=method,
            memory_role="unknown",
            accepted_count=0,
            rejected_count=max(rejected_count, 1 if accepted_count > 0 else 0),
            skipped_count=skipped_count,
            abstained=True,
            retrieval_trace=annotate_contract_trace(
                retrieval_trace,
                scope="write_result",
                reason=contract_reason,
                observed_role=memory_role,
                operation=operation,
            ),
            provenance=_normalize_provenance(raw),
            reason=contract_reason,
        )
    normalized_status = "succeeded" if accepted_count > 0 and rejected_count == 0 else ("partial" if accepted_count > 0 else (status or "abstained"))
    return MemoryWriteResult(
        status=normalized_status,
        operation=operation,
        method=method,
        memory_role=memory_role if memory_role != "unknown" else default_role,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        skipped_count=skipped_count,
        abstained=abstained,
        retrieval_trace=retrieval_trace,
        provenance=_normalize_provenance(raw),
        reason=reason,
    )


def _normalize_records(raw: dict[str, Any]) -> list[dict[str, Any]]:
    records = raw.get("records")
    if isinstance(records, list):
        return [_normalize_record_dict(record) for record in records if isinstance(record, dict)]
    if isinstance(raw.get("record"), dict):
        return [_normalize_record_dict(raw["record"])]
    if isinstance(raw.get("state"), dict):
        return [_normalize_record_dict(raw["state"])]
    return []


def _normalize_provenance(raw: dict[str, Any]) -> list[dict[str, Any]]:
    provenance = raw.get("provenance")
    if isinstance(provenance, list):
        return [_normalize_record_dict(item) for item in provenance if isinstance(item, dict)]
    if isinstance(provenance, dict):
        return [_normalize_record_dict(provenance)]
    return []


def _normalize_record_dict(raw: dict[str, Any]) -> dict[str, Any]:
    record = dict(raw)
    if "memory_role" in record:
        record["memory_role"] = normalize_memory_role(record.get("memory_role"), allow_unknown=True)
    return record


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


def _retrieve_memory_query_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    method: str,
    query: str,
    subject: str | None,
    predicate: str | None,
    limit: int,
    sdk_module: str | None,
    actor_id: str,
) -> MemoryRetrievalQueryResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    normalized_query = str(query or "").strip()
    normalized_subject = _optional_string(subject)
    normalized_predicate = _optional_string(predicate)
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method=method,
            memory_role="current_state",
            records=[],
            provenance=[],
            retrieval_trace=None,
            answer_explanation=None,
            abstained=True,
            reason="sdk_unavailable",
            shadow_only=False,
        )
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=_human_id_from_subject(normalized_subject or ""),
            session_id=f"memory-retrieval:{actor_id}",
            turn_id=f"{actor_id}:{method}",
            actor_id=actor_id,
        )
        return MemoryRetrievalQueryResult(
            sdk_module=module_name,
            method=method,
            query=normalized_query,
            subject=normalized_subject,
            predicate=normalized_predicate,
            limit=limit,
            runtime=runtime,
            read_result=read_result,
        )
    _record_memory_read_requested_subject(
        state_db=state_db,
        method=method,
        subject=normalized_subject or "",
        predicate=normalized_predicate,
        query=normalized_query,
        session_id=f"memory-retrieval:{actor_id}",
        turn_id=f"{actor_id}:{method}",
        actor_id=actor_id,
    )
    raw = _call_sdk_method(
        client,
        method,
        {
            "query": normalized_query,
            "subject": normalized_subject,
            "predicate": normalized_predicate,
            "limit": limit,
            "session_id": f"memory-retrieval:{actor_id}",
            "turn_id": f"{actor_id}:{method}",
            "timestamp": _now_iso(),
            "metadata": {"source_surface": f"memory_cli_{method}"},
        },
    )
    read_result = _normalize_read_result(raw=raw, method=method, shadow_only=False)
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=_human_id_from_subject(normalized_subject or ""),
        session_id=f"memory-retrieval:{actor_id}",
        turn_id=f"{actor_id}:{method}",
        actor_id=actor_id,
    )
    return MemoryRetrievalQueryResult(
        sdk_module=module_name,
        method=method,
        query=normalized_query,
        subject=normalized_subject,
        predicate=normalized_predicate,
        limit=limit,
        runtime=runtime,
        read_result=read_result,
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
    subject = _subject_for_human_id(human_id)
    observations = [
        {
            "subject": subject,
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
            "subject": subject,
            "predicate_count": len(trait_values),
            "predicates": [f"{PREFERENCE_PREDICATE_PREFIX}{trait}" for trait in sorted(trait_values)],
            "observations": observations,
        },
        provenance={"memory_role": "current_state"},
    )


def _record_memory_write_requested_observations(
    *,
    state_db: StateDB,
    operation: str,
    human_id: str,
    observations: list[dict[str, Any]],
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    subject = _subject_for_human_id(human_id)
    record_event(
        state_db,
        event_type="memory_write_requested",
        component="memory_orchestrator",
        summary="Spark memory write requested for durable structured facts.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        facts={
            "operation": operation,
            "method": "write_observation",
            "memory_role": "current_state",
            "subject": subject,
            "predicate_count": len(observations),
            "predicates": [str(item.get("predicate") or "") for item in observations if item.get("predicate")],
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
    subject = _subject_for_human_id(human_id)
    record_event(
        state_db,
        event_type="memory_read_requested",
        component="memory_orchestrator",
        summary="Spark memory current-state lookup requested.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        facts={"method": method, "memory_role": "current_state", "subject": subject},
        provenance={"memory_role": "current_state"},
    )


def _record_memory_read_requested_subject(
    *,
    state_db: StateDB,
    method: str,
    subject: str,
    predicate: str | None,
    query: str | None = None,
    predicate_prefix: str | None = None,
    as_of: str | None = None,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    facts = {"method": method, "memory_role": "current_state", "subject": subject}
    if predicate is not None:
        facts["predicate"] = predicate
    if query is not None:
        facts["query"] = query
    if predicate_prefix is not None:
        facts["predicate_prefix"] = predicate_prefix
    if as_of is not None:
        facts["as_of"] = as_of
    record_event(
        state_db,
        event_type="memory_read_requested",
        component="memory_orchestrator",
        summary="Spark memory read requested.",
        request_id=turn_id,
        session_id=session_id,
        human_id=_human_id_from_subject(subject),
        actor_id=actor_id,
        facts=facts,
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


def _record_memory_smoke_event(
    *,
    state_db: StateDB,
    result: MemorySdkSmokeResult,
    actor_id: str,
) -> None:
    succeeded = (
        result.write_result.accepted_count > 0
        and not result.read_result.abstained
        and bool(result.read_result.records)
        and (result.cleanup_result is None or result.cleanup_result.accepted_count > 0)
    )
    record_event(
        state_db,
        event_type="memory_smoke_succeeded" if succeeded else "memory_smoke_failed",
        component="memory_orchestrator",
        summary="Spark direct memory smoke completed." if succeeded else "Spark direct memory smoke failed.",
        actor_id=actor_id,
        status="recorded" if succeeded else "abstained",
        severity="medium" if succeeded else "high",
        facts={
            "sdk_module": result.sdk_module,
            "subject": result.subject,
            "predicate": result.predicate,
            "value": result.value,
            "shadow_only_eval": result.shadow_only_eval,
            "write_result": MemorySdkSmokeResult._write_payload(result.write_result),
            "read_result": MemorySdkSmokeResult._read_payload(result.read_result),
            "cleanup_result": (
                MemorySdkSmokeResult._write_payload(result.cleanup_result)
                if result.cleanup_result is not None
                else None
            ),
        },
        provenance={"memory_role": "current_state"},
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
