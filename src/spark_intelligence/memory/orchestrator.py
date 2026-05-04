from __future__ import annotations

import importlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.generic_observations import (
    detect_telegram_generic_observation,
    parse_entity_state_deletion,
    parse_entity_state_fact,
)
from spark_intelligence.memory.profile_facts import (
    active_state_revalidate_at,
    active_state_revalidation_days,
    parse_named_object_fact,
)
from spark_intelligence.memory.salience import MemorySalienceDecision, evaluate_memory_salience
from spark_intelligence.memory_contracts import (
    annotate_contract_trace,
    effective_memory_role,
    memory_contract_reason,
    normalize_memory_role,
)
from spark_intelligence.observability.store import record_event, record_policy_gate_block
from spark_intelligence.state.db import StateDB
from spark_intelligence.workflow_recovery import latest_pending_tasks, latest_procedural_lessons


from spark_intelligence.memory.retention_policy import (
    BELIEF_REVALIDATION_DAYS,
    RAW_EPISODE_ARCHIVE_DAYS,
    STRUCTURED_EVIDENCE_ARCHIVE_DAYS,
)

DEFAULT_SDK_MODULE = "domain_chip_memory"
DEFAULT_DOMAIN_CHIP_MEMORY_ROOT = Path.home() / "Desktop" / "domain-chip-memory"
PREFERENCE_PREDICATE_PREFIX = "personality.preference."
_SDK_CLIENT_CACHE: dict[tuple[str, str], Any] = {}

# Builder-side source of truth for the runtime memory architecture. Substrate
# (`domain_chip_memory.sdk`) also defines a default, but relying on it meant
# the live runtime would silently flip on env-var loss or a substrate-side
# refactor. Applying the pin from Builder makes the choice explicit and
# auditable here. Pre-existing env vars are respected so tests can override.
#
# 2026-04-22: Phase A head-to-head validation is complete. Internal live
# regression and the completed external benchmark matrix both favor
# `summary_synthesis_memory`, so Builder now pins that architecture directly.
PINNED_RUNTIME_MEMORY_ARCHITECTURE = "summary_synthesis_memory"
PINNED_RUNTIME_MEMORY_PROVIDER = "heuristic_v1"
_ARCHITECTURE_PIN_ENV_VAR = "SPARK_MEMORY_RUNTIME_ARCHITECTURE"
_PROVIDER_PIN_ENV_VAR = "SPARK_MEMORY_RUNTIME_PROVIDER"
WIKI_PACKET_METADATA_FIELDS: tuple[str, ...] = (
    "wiki_family",
    "owner_system",
    "authority",
    "scope_kind",
    "source_of_truth",
    "freshness",
    "status",
    "generated_at",
    "last_verified_at",
    "source_path",
)
MEMORY_KB_WIKI_FAMILY_PREFIX = "memory_kb_"
MEMORY_DASHBOARD_MOVEMENT_STATES: tuple[str, ...] = (
    "captured",
    "blocked",
    "promoted",
    "saved",
    "decayed",
    "summarized",
    "retrieved",
)


def _apply_runtime_architecture_pin() -> None:
    if not os.environ.get(_ARCHITECTURE_PIN_ENV_VAR):
        os.environ[_ARCHITECTURE_PIN_ENV_VAR] = PINNED_RUNTIME_MEMORY_ARCHITECTURE
    if not os.environ.get(_PROVIDER_PIN_ENV_VAR):
        os.environ[_PROVIDER_PIN_ENV_VAR] = PINNED_RUNTIME_MEMORY_PROVIDER


_apply_runtime_architecture_pin()


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
class MemoryMaintenanceRunResult:
    sdk_module: str
    status: str
    runtime: dict[str, Any]
    maintenance: dict[str, Any]
    trace: dict[str, Any]
    reason: str | None = None
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "status": self.status,
                "reason": self.reason,
                "runtime": self.runtime,
                "maintenance": self.maintenance,
                "trace": self.trace,
                "shadow_only_eval": self.shadow_only_eval,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory SDK maintenance"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- status: {self.status}")
        if self.reason:
            lines.append(f"- reason: {self.reason}")
        for field in (
            "manual_observations_before",
            "manual_observations_after",
            "active_deletion_count",
            "active_state_still_current_count",
            "active_state_stale_preserved_count",
            "active_state_superseded_count",
            "active_state_archived_count",
        ):
            lines.append(f"- {field}: {self.maintenance.get(field, 0)}")
        return "\n".join(lines)


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
class MemoryTaskRecoveryResult:
    sdk_module: str
    human_id: str
    subject: str
    query: str
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "human_id": self.human_id,
                "subject": self.subject,
                "query": self.query,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory task recovery"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- human_id: {self.human_id}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- recover_task_context: status={self.read_result.status} records={len(self.read_result.records)} "
            f"abstained={'yes' if self.read_result.abstained else 'no'}"
        )
        if self.read_result.reason:
            lines.append(f"- reason: {self.read_result.reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class MemoryEpisodicRecallResult:
    sdk_module: str
    human_id: str
    subject: str
    query: str
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    shadow_only_eval: bool = True

    def to_json(self) -> str:
        return json.dumps(
            {
                "sdk_module": self.sdk_module,
                "human_id": self.human_id,
                "subject": self.subject,
                "query": self.query,
                "runtime": self.runtime,
                "shadow_only_eval": self.shadow_only_eval,
                "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Spark memory episodic recall"]
        lines.append(f"- sdk_module: {self.sdk_module}")
        lines.append(f"- human_id: {self.human_id}")
        lines.append(f"- subject: {self.subject}")
        lines.append(f"- ready: {'yes' if self.runtime.get('ready') else 'no'}")
        lines.append(
            f"- recall_episodic_context: status={self.read_result.status} records={len(self.read_result.records)} "
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


@dataclass(frozen=True)
class MemoryKernelReadResult:
    sdk_module: str
    query: str
    subject: str | None
    predicate: str | None
    read_method: str
    source_class: str
    answer: str | None
    records: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    runtime: dict[str, Any]
    abstained: bool
    reason: str | None
    ignored_stale_records: list[dict[str, Any]]
    read_result: MemoryReadResult

    def to_payload(self) -> dict[str, Any]:
        return {
            "sdk_module": self.sdk_module,
            "query": self.query,
            "subject": self.subject,
            "predicate": self.predicate,
            "read_method": self.read_method,
            "source_class": self.source_class,
            "answer": self.answer,
            "records": self.records,
            "provenance": self.provenance,
            "runtime": self.runtime,
            "abstained": self.abstained,
            "reason": self.reason,
            "ignored_stale_records": self.ignored_stale_records,
            "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class HybridMemoryCandidate:
    lane: str
    source_class: str
    score: float
    selected: bool
    record: dict[str, Any]
    reason_selected: str | None = None
    reason_discarded: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "source_class": self.source_class,
            "score": self.score,
            "selected": self.selected,
            "reason_selected": self.reason_selected,
            "reason_discarded": self.reason_discarded,
            "record": self.record,
        }


@dataclass(frozen=True)
class HybridMemoryContextPacket:
    query: str
    max_chars: int
    used_chars: int
    sections: list[dict[str, Any]]
    source_mix: dict[str, int]
    dropped_count: int
    conflict_notes: list[dict[str, Any]]
    trace: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "max_chars": self.max_chars,
            "used_chars": self.used_chars,
            "sections": self.sections,
            "source_mix": self.source_mix,
            "dropped_count": self.dropped_count,
            "conflict_notes": self.conflict_notes,
            "trace": self.trace,
        }


@dataclass(frozen=True)
class HybridMemoryRetrievalResult:
    sdk_module: str
    query: str
    subject: str | None
    predicate: str | None
    entity_key: str | None
    as_of: str | None
    runtime: dict[str, Any]
    read_result: MemoryReadResult
    candidates: list[HybridMemoryCandidate]
    lane_summaries: list[dict[str, Any]]
    context_packet: HybridMemoryContextPacket | None = None
    shadow_only_eval: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "sdk_module": self.sdk_module,
            "query": self.query,
            "subject": self.subject,
            "predicate": self.predicate,
            "entity_key": self.entity_key,
            "as_of": self.as_of,
            "runtime": self.runtime,
            "shadow_only_eval": self.shadow_only_eval,
            "lane_summaries": self.lane_summaries,
            "candidates": [candidate.to_payload() for candidate in self.candidates],
            "context_packet": self.context_packet.to_payload() if self.context_packet is not None else None,
            "read_result": MemorySdkSmokeResult._read_payload(self.read_result),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class StructuredEvidenceCurrentStateRule:
    fact_name: str
    canonical_template: str
    extract_patterns: tuple[re.Pattern[str], ...]
    direct_promotion_without_corroboration: bool = False


_STRUCTURED_EVIDENCE_CURRENT_STATE_RULES: tuple[StructuredEvidenceCurrentStateRule, ...] = (
    StructuredEvidenceCurrentStateRule(
        fact_name="current_plan",
        canonical_template="Our current plan is to {value}.",
        extract_patterns=(
            re.compile(r"\b(?:i|we)\s+plan\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+plan\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_focus",
        canonical_template="Our current focus is {value}.",
        extract_patterns=(
            re.compile(r"\b(?:i(?:'m| am)|we(?:'re| are))\s+focusing\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+priority\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_decision",
        canonical_template="Our current decision is {value}.",
        extract_patterns=(
            re.compile(r"\b(?:i|we)\s+decided\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\b(?:i|we)\s+decided\s+that\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+decision\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+decision\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\b(?:i|we)(?:'re| are)\s+going\s+with\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\b(?:i|we)\s+chose\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_status",
        canonical_template="Our current status is {value}.",
        extract_patterns=(
            re.compile(r"^status\s+update[:,-]?\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bcurrent\s+status\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_owner",
        canonical_template="Our current owner is {value}.",
        extract_patterns=(
            re.compile(r"\bowner\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bowned\s+by\s+(.+?)(?:\s+because\b.+?|\s+during\b.+?)?[.!]?$", re.IGNORECASE),
            re.compile(r"\bhandled\s+by\s+(.+?)(?:\s+because\b.+?|\s+during\b.+?)?[.!]?$", re.IGNORECASE),
            re.compile(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+owns\b", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_dependency",
        canonical_template="Our current dependency is {value}.",
        extract_patterns=(
            re.compile(r"\bdependency\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bdependent\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bwaiting\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bpending\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bdepends\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_constraint",
        canonical_template="Our current constraint is {value}.",
        extract_patterns=(
            re.compile(r"\bconstraint\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\blimited\s+by\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bbudget\s+only\s+lets\s+us\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bonly\s+have\s+bandwidth\s+for\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\b(limited\s+founder\s+bandwidth)\b", re.IGNORECASE),
        ),
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_commitment",
        canonical_template="Our current commitment is to {value}.",
        extract_patterns=(
            re.compile(r"\b(?:i|we)\s+committed\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+commitment\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+commitment\s+is\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_milestone",
        canonical_template="Our current milestone is {value}.",
        extract_patterns=(
            re.compile(r"\bour\s+next\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+next\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+current\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+current\s+milestone\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_risk",
        canonical_template="Our current risk is {value}.",
        extract_patterns=(
            re.compile(r"\b(?:current|main|biggest)\s+risk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\brisk\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\brisk\s+of\s+(.+?)(?:\s+because|[.!]?$)", re.IGNORECASE),
            re.compile(r"\bat\s+risk\s+of\s+(.+?)(?:\s+because|[.!]?$)", re.IGNORECASE),
        ),
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_assumption",
        canonical_template="Our current assumption is {value}.",
        extract_patterns=(
            re.compile(r"\bour\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+current\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+current\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bour\s+main\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bthe\s+main\s+assumption\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
        direct_promotion_without_corroboration=True,
    ),
    StructuredEvidenceCurrentStateRule(
        fact_name="current_blocker",
        canonical_template="Our blocker is {value}.",
        extract_patterns=(
            re.compile(r"\b(?:current|main)\s+blocker\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bbottleneck\s+is\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bblocked\s+on\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
    ),
)

_STRUCTURED_EVIDENCE_CURRENT_STATE_RULES_BY_FACT = {
    rule.fact_name: rule for rule in _STRUCTURED_EVIDENCE_CURRENT_STATE_RULES
}


def _extract_first_value(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = str(match.group(1) or "").strip().rstrip(".,! ")
        if value:
            return value
    return None


def _derive_current_state_observation_from_evidence(text: str) -> Any | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    for rule in _STRUCTURED_EVIDENCE_CURRENT_STATE_RULES:
        explicit_value = _extract_first_value(normalized, rule.extract_patterns)
        if explicit_value:
            return detect_telegram_generic_observation(rule.canonical_template.format(value=explicit_value))
    lowered = normalized.casefold()
    ongoing_issue_signal = any(
        token in lowered
        for token in (
            "keep ",
            "keeps ",
            "still ",
            "dropping",
            "drop ",
            "drops ",
            "fail",
            "fails",
            "failure",
            "blocked",
            "bottleneck",
            "retry flow",
            "friction",
        )
    )
    if not ongoing_issue_signal:
        return None
    blocker_value = _extract_first_value(
        normalized,
        (
            re.compile(r"\bbecause\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bdue\s+to\s+(.+?)[.!]?$", re.IGNORECASE),
            re.compile(r"\bfrom\s+(.+?)[.!]?$", re.IGNORECASE),
        ),
    )
    if not blocker_value:
        blocker_value = normalized.rstrip(".,! ")
    return detect_telegram_generic_observation("Our blocker is {value}.".format(value=blocker_value))


def _should_promote_current_state_from_evidence(
    *,
    observation: Any | None,
    corroborating_evidence_records: list[dict[str, Any]],
) -> bool:
    if observation is None or not str(getattr(observation, "value", "") or "").strip():
        return False
    fact_name = str(getattr(observation, "fact_name", "") or "").strip()
    if not fact_name:
        return False
    rule = _STRUCTURED_EVIDENCE_CURRENT_STATE_RULES_BY_FACT.get(fact_name)
    if rule is None:
        return False
    return bool(corroborating_evidence_records) or rule.direct_promotion_without_corroboration


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
            retention_class=_optional_string(payload.get("retention_class")),
            document_time=_optional_string(payload.get("document_time")),
            event_time=_optional_string(payload.get("event_time")),
            valid_from=_optional_string(payload.get("valid_from")),
            valid_to=_optional_string(payload.get("valid_to")),
            supersedes=_optional_string(payload.get("supersedes")),
            conflicts_with=[str(item) for item in list(payload.get("conflicts_with") or [])],
            deleted_at=_optional_string(payload.get("deleted_at")),
            metadata=dict(payload.get("metadata") or {}),
        )
        result = self._sdk.write_observation(request)
        self._persist_manual_state()
        return _normalize_domain_write_result(
            result=result,
            operation=str(payload.get("operation") or "update"),
            default_role=str(payload.get("memory_role") or "current_state"),
        )

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
            retention_class=_optional_string(payload.get("retention_class")),
            document_time=_optional_string(payload.get("document_time")),
            event_time=_optional_string(payload.get("event_time")),
            valid_from=_optional_string(payload.get("valid_from")),
            valid_to=_optional_string(payload.get("valid_to")),
            supersedes=_optional_string(payload.get("supersedes")),
            conflicts_with=[str(item) for item in list(payload.get("conflicts_with") or [])],
            deleted_at=_optional_string(payload.get("deleted_at")),
            metadata=dict(payload.get("metadata") or {}),
        )
        result = self._sdk.write_event(request)
        self._persist_manual_state()
        return _normalize_domain_write_result(
            result=result,
            operation=str(payload.get("operation") or "event"),
            default_role="event",
        )

    def get_current_state(self, **payload: Any) -> dict[str, Any]:
        subject = _optional_string(payload.get("subject"))
        predicate = _optional_string(payload.get("predicate"))
        entity_key = _optional_string(payload.get("entity_key"))
        raw_predicate_prefix = payload.get("predicate_prefix")
        predicate_prefix = None if raw_predicate_prefix is None else str(raw_predicate_prefix)
        if subject and raw_predicate_prefix is not None and not predicate:
            return self._get_current_state_prefix(subject=subject, predicate_prefix=predicate_prefix)
        if not subject or not predicate:
            return {"status": "abstained", "reason": "invalid_request", "memory_role": "unknown"}
        request_kwargs = {"subject": subject, "predicate": predicate}
        if entity_key:
            request_kwargs["entity_key"] = entity_key
        try:
            request = self._module.CurrentStateRequest(**request_kwargs)
        except TypeError:
            request_kwargs.pop("entity_key", None)
            request = self._module.CurrentStateRequest(**request_kwargs)
        result = self._sdk.get_current_state(request)
        self._persist_manual_state()
        return _normalize_domain_lookup_result(
            result=result,
            subject=subject,
            predicate=predicate,
            method="get_current_state",
        )

    def get_historical_state(self, **payload: Any) -> dict[str, Any]:
        subject = _optional_string(payload.get("subject"))
        predicate = _optional_string(payload.get("predicate"))
        as_of = _optional_string(payload.get("as_of"))
        entity_key = _optional_string(payload.get("entity_key"))
        if not subject or not predicate or not as_of:
            return {"status": "abstained", "reason": "invalid_request", "memory_role": "unknown"}
        request_kwargs = {"subject": subject, "predicate": predicate, "as_of": as_of}
        if entity_key:
            request_kwargs["entity_key"] = entity_key
        try:
            request = self._module.HistoricalStateRequest(**request_kwargs)
        except TypeError:
            request_kwargs.pop("entity_key", None)
            request = self._module.HistoricalStateRequest(**request_kwargs)
        result = self._sdk.get_historical_state(request)
        self._persist_manual_state()
        return _normalize_domain_lookup_result(
            result=result,
            subject=subject,
            predicate=predicate,
            method="get_historical_state",
        )

    def retrieve_evidence(self, **payload: Any) -> dict[str, Any]:
        request = self._module.EvidenceRetrievalRequest(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            limit=int(payload.get("limit") or 5),
        )
        result = self._sdk.retrieve_evidence(request)
        self._persist_manual_state()
        return _normalize_domain_retrieval_result(result=result, method="retrieve_evidence")

    def retrieve_events(self, **payload: Any) -> dict[str, Any]:
        request = self._module.EventRetrievalRequest(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            predicate=_optional_string(payload.get("predicate")),
            limit=int(payload.get("limit") or 5),
        )
        result = self._sdk.retrieve_events(request)
        self._persist_manual_state()
        return _normalize_domain_retrieval_result(result=result, method="retrieve_events")

    def recover_task_context(self, **payload: Any) -> dict[str, Any]:
        request_cls = getattr(self._module, "TaskRecoveryRequest", None)
        recover = getattr(self._sdk, "recover_task_context", None)
        if request_cls is None or not callable(recover):
            return {"status": "abstained", "reason": "method_missing", "memory_role": "unknown"}
        request = request_cls(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            limit=int(payload.get("limit") or 5),
        )
        result = recover(request)
        self._persist_manual_state()
        return _normalize_domain_task_recovery_result(result=result)

    def recall_episodic_context(self, **payload: Any) -> dict[str, Any]:
        request_cls = getattr(self._module, "EpisodicRecallRequest", None)
        recall = getattr(self._sdk, "recall_episodic_context", None)
        if request_cls is None or not callable(recall):
            return {"status": "abstained", "reason": "method_missing", "memory_role": "unknown"}
        request = request_cls(
            query=_optional_string(payload.get("query")),
            subject=_optional_string(payload.get("subject")),
            since=_optional_string(payload.get("since")),
            until=_optional_string(payload.get("until")),
            limit=int(payload.get("limit") or 5),
        )
        result = recall(request)
        self._persist_manual_state()
        return _normalize_domain_episodic_recall_result(result=result)

    def export_knowledge_base_snapshot(self, **payload: Any) -> dict[str, Any]:
        export_snapshot = getattr(self._sdk, "export_knowledge_base_snapshot", None)
        if not callable(export_snapshot):
            return {"status": "abstained", "reason": "method_missing"}
        result = export_snapshot()
        if isinstance(result, dict):
            return result
        return {"status": "accepted", "result": result}

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
        self._persist_manual_state()
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

    def reconsolidate_manual_memory(self, **payload: Any) -> Any:
        now = _optional_string(payload.get("now"))
        if now:
            result = self._sdk.reconsolidate_manual_memory(now=now)
        else:
            result = self._sdk.reconsolidate_manual_memory()
        self._persist_manual_state()
        return result

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
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._persistence_path.with_name(f"{self._persistence_path.name}.lock")
        with _exclusive_path_lock(lock_path):
            existing_payload = _load_domain_chip_memory_payload(self._persistence_path)
            merged_movement_events = _merge_domain_movement_rows(
                existing_payload.get("dashboard_movement_events"),
                list(getattr(self._sdk, "_dashboard_movement_events", []) or []),
            )
            merged_observation_payloads = _merge_domain_entry_payloads(
                existing_payload.get("manual_observations"),
                [asdict(entry) for entry in list(getattr(self._sdk, "_manual_observations", []) or [])],
            )
            merged_event_payloads = _merge_domain_entry_payloads(
                existing_payload.get("manual_events"),
                [asdict(entry) for entry in list(getattr(self._sdk, "_manual_events", []) or [])],
            )
            observation_cls = getattr(self._sdk_module, "ObservationEntry", None)
            event_cls = getattr(self._sdk_module, "EventCalendarEntry", None)
            merged_observations = _hydrate_domain_entries(merged_observation_payloads, observation_cls) if observation_cls else []
            merged_events = _hydrate_domain_entries(merged_event_payloads, event_cls) if event_cls else []
            current_snapshot = []
            build_snapshot = getattr(self._sdk, "_build_manual_current_state_snapshot", None)
            if callable(build_snapshot):
                try:
                    current_snapshot = list(build_snapshot(merged_observations) or [])
                except Exception:
                    current_snapshot = []
            elif observation_cls:
                current_snapshot = _hydrate_domain_entries(
                    existing_payload.get("manual_current_state_snapshot"),
                    observation_cls,
                )
            self._sdk._manual_observations = merged_observations
            self._sdk._manual_events = merged_events
            self._sdk._manual_current_state_snapshot = current_snapshot
            self._sdk._dashboard_movement_events = merged_movement_events
            self._sdk._dashboard_movement_counter = _domain_movement_counter(
                merged_movement_events,
                existing_payload.get("dashboard_movement_counter"),
            )
            payload = {
                "manual_observations": [asdict(entry) for entry in merged_observations],
                "manual_events": [asdict(entry) for entry in merged_events],
                "manual_current_state_snapshot": [asdict(entry) for entry in current_snapshot],
                "dashboard_movement_events": merged_movement_events,
                "dashboard_movement_counter": int(getattr(self._sdk, "_dashboard_movement_counter", 0) or 0),
            }
            temp_path = self._persistence_path.with_name(f"{self._persistence_path.name}.{os.getpid()}.tmp")
            temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(self._persistence_path)


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
    smoke_token = re.sub(r"[^a-z0-9]+", "_", f"{subject}:{predicate}".lower()).strip("_") or "default"
    session_id = f"memory-smoke:{actor_id}:{smoke_token}"
    write_turn_id = f"{actor_id}:write:{smoke_token}"
    read_turn_id = f"{actor_id}:read:{smoke_token}"
    cleanup_turn_id = f"{actor_id}:cleanup:{smoke_token}"
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
            "memory_role": "current_state",
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


def run_memory_sdk_maintenance(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    sdk_module: str | None = None,
    now: str | None = None,
    actor_id: str = "memory_cli",
) -> MemoryMaintenanceRunResult:
    module_name = str(
        sdk_module
        or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE)
        or DEFAULT_SDK_MODULE
    )
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        result = MemoryMaintenanceRunResult(
            sdk_module=module_name,
            status="abstained",
            runtime=runtime,
            maintenance={},
            trace={},
            reason="sdk_unavailable",
        )
        _record_memory_maintenance_event(state_db=state_db, result=result, actor_id=actor_id)
        return result
    target = getattr(client, "reconsolidate_manual_memory", None)
    if not callable(target):
        result = MemoryMaintenanceRunResult(
            sdk_module=module_name,
            status="abstained",
            runtime=runtime,
            maintenance={},
            trace={},
            reason="method_missing",
        )
        _record_memory_maintenance_event(state_db=state_db, result=result, actor_id=actor_id)
        return result
    try:
        raw = target(now=now) if now else target()
    except Exception as exc:
        result = MemoryMaintenanceRunResult(
            sdk_module=module_name,
            status="failed",
            runtime=runtime,
            maintenance={},
            trace={},
            reason=f"{type(exc).__name__}: {exc}",
        )
        _record_memory_maintenance_event(state_db=state_db, result=result, actor_id=actor_id)
        return result
    maintenance = _normalize_memory_maintenance_payload(raw)
    trace = maintenance.get("trace") if isinstance(maintenance.get("trace"), dict) else {}
    result = MemoryMaintenanceRunResult(
        sdk_module=module_name,
        status="succeeded",
        runtime=runtime,
        maintenance=maintenance,
        trace=dict(trace),
    )
    _record_memory_maintenance_event(state_db=state_db, result=result, actor_id=actor_id)
    _record_memory_maintenance_lifecycle_transitions(state_db=state_db, result=result, actor_id=actor_id)
    return result


def export_memory_dashboard_movement_in_memory(
    *,
    config_manager: ConfigManager,
    sdk_module: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    module_name = str(
        sdk_module
        or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE)
        or DEFAULT_SDK_MODULE
    )
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        return {
            "status": "abstained",
            "reason": "sdk_unavailable",
            "sdk_module": module_name,
            "runtime": runtime,
            "authority": "observability_non_authoritative",
            "movement_counts": {},
            "rows": [],
        }
    snapshot = _call_sdk_method(client, "export_knowledge_base_snapshot", {})
    movement = snapshot.get("dashboard_movement") if isinstance(snapshot, dict) else None
    if not isinstance(movement, dict):
        return {
            "status": "abstained",
            "reason": "dashboard_movement_missing",
            "sdk_module": module_name,
            "runtime": runtime,
            "authority": "observability_non_authoritative",
            "movement_counts": {},
            "rows": [],
        }
    rows = [row for row in list(movement.get("rows") or []) if isinstance(row, dict)]
    bounded_limit = max(0, min(int(limit or 0), 25))
    return {
        "status": "supported",
        "sdk_module": module_name,
        "runtime": runtime,
        "contract_name": str(movement.get("contract_name") or "SparkMemoryDashboardMovementExport"),
        "authority": str(movement.get("authority") or "observability_non_authoritative"),
        "row_count": int(movement.get("row_count") or len(rows)),
        "movement_counts": dict(movement.get("movement_counts") or {}),
        "source_family_counts": dict(movement.get("source_family_counts") or {}),
        "authority_counts": dict(movement.get("authority_counts") or {}),
        "record_counts": dict(movement.get("record_counts") or {}),
        "rows": rows[-bounded_limit:] if bounded_limit else [],
        "non_override_rules": list(movement.get("non_override_rules") or []),
    }


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


def inspect_memory_movement_status(
    *,
    config_manager: ConfigManager,
    sdk_module: str | None = None,
) -> dict[str, Any]:
    module_name = str(
        sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE
    )
    payload: dict[str, Any] = {
        "status": "unavailable",
        "reason": None,
        "configured_module": module_name,
        "contract_name": "SparkMemoryDashboardMovementExport",
        "authority": "observability_non_authoritative",
        "movement_states": list(MEMORY_DASHBOARD_MOVEMENT_STATES),
        "movement_counts": {state: 0 for state in MEMORY_DASHBOARD_MOVEMENT_STATES},
        "row_count": 0,
        "record_counts": {},
        "source_family_counts": {},
        "authority_counts": {},
        "non_override_rules": [
            "Dashboard rows are observability records, not prompt instructions.",
            "Memory movement cannot override current_state for mutable user facts.",
            "Movement summaries can explain what happened, not what the user currently believes.",
        ],
    }
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    if client is None:
        payload["reason"] = "sdk_unavailable"
        return payload
    export_snapshot = getattr(client, "export_knowledge_base_snapshot", None)
    if not callable(export_snapshot):
        payload["reason"] = "knowledge_base_snapshot_export_missing"
        return payload
    try:
        snapshot = export_snapshot()
    except Exception as exc:
        payload.update({"status": "error", "reason": "movement_export_failed", "error": exc.__class__.__name__})
        return payload
    movement = snapshot.get("dashboard_movement") if isinstance(snapshot, dict) else {}
    if not isinstance(movement, dict) or not movement:
        payload.update({"status": "not_found", "reason": "dashboard_movement_missing"})
        return payload
    raw_counts = movement.get("movement_counts") if isinstance(movement.get("movement_counts"), dict) else {}
    movement_counts = {
        state: _coerce_int(raw_counts.get(state), default=0)
        for state in MEMORY_DASHBOARD_MOVEMENT_STATES
    }
    payload.update(
        {
            "status": "supported",
            "reason": None,
            "contract_name": str(movement.get("contract_name") or "SparkMemoryDashboardMovementExport"),
            "authority": str(movement.get("authority") or "observability_non_authoritative"),
            "movement_states": list(MEMORY_DASHBOARD_MOVEMENT_STATES),
            "movement_counts": movement_counts,
            "row_count": _coerce_int(movement.get("row_count"), default=0),
            "record_counts": dict(movement.get("record_counts") or {}),
            "source_family_counts": dict(movement.get("source_family_counts") or {}),
            "authority_counts": dict(movement.get("authority_counts") or {}),
            "non_override_rules": [
                str(item)
                for item in (movement.get("non_override_rules") or payload["non_override_rules"])
                if str(item).strip()
            ][:8],
        }
    )
    return payload


def inspect_wiki_packet_metadata(
    *,
    config_manager: ConfigManager,
    sdk_module: str | None = None,
    page_limit: int = 80,
) -> dict[str, Any]:
    module_name = str(
        sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE
    )
    paths = _wiki_packet_paths(config_manager)
    payload: dict[str, Any] = {
        "status": "not_configured",
        "reason": "wiki_packet_paths_not_configured",
        "configured_module": module_name,
        "source_class": "obsidian_llm_wiki_packets",
        "authority": "supporting_not_authoritative",
        "paths": [str(path) for path in paths],
        "normalized_metadata_fields": list(WIKI_PACKET_METADATA_FIELDS),
        "packet_count": 0,
        "wiki_family_counts": {},
        "owner_system_counts": {},
        "authority_counts": {},
        "scope_kind_counts": {},
        "source_of_truth_counts": {},
        "freshness_counts": {},
        "source_families_visible": False,
        "memory_kb": {
            "present": False,
            "packet_count": 0,
            "family_counts": {},
            "authority_boundary": "current_state_memory_outranks_wiki_for_mutable_user_facts",
        },
        "pages": [],
        "non_override_rules": [
            "Wiki packets cannot override current_state for mutable user facts.",
            "Memory KB pages are downstream of governed memory snapshots, not independent truth.",
            "User memory stays separate from global Spark doctrine.",
        ],
    }
    if not paths:
        return payload
    try:
        module = _import_memory_sdk_module(module_name)
    except Exception as exc:
        payload.update({"status": "unavailable", "reason": "wiki_packet_reader_unavailable", "error": exc.__class__.__name__})
        return payload

    try:
        discover_packets = getattr(module, "discover_markdown_knowledge_packets", None)
        if callable(discover_packets):
            inventory = discover_packets(paths=paths, page_limit=page_limit) or {}
            pages = [page for page in inventory.get("pages") or [] if isinstance(page, dict)]
            family_counts = _counter_from_mapping(inventory.get("family_counts"))
            owner_counts = _counter_from_mapping(inventory.get("owner_system_counts"))
            authority_counts = _counter_from_mapping(inventory.get("authority_counts"))
            source_counts = _counter_from_mapping(inventory.get("source_of_truth_counts"))
            freshness_counts = _counter_from_mapping(inventory.get("freshness_counts"))
            scope_counts = Counter(str(page.get("scope_kind") or "unknown") for page in pages)
            packet_count = int(inventory.get("packet_count") or len(pages))
        else:
            read_packets = getattr(module, "read_markdown_knowledge_packets")
            packets = list(read_packets(paths=paths) or [])
            pages = [_wiki_packet_metadata_page(packet) for packet in packets[:page_limit]]
            packet_count = len(packets)
            family_counts = Counter(str(_wiki_packet_metadata_value(packet, "wiki_family") or "unknown") for packet in packets)
            owner_counts = Counter(str(_wiki_packet_metadata_value(packet, "owner_system") or "unknown") for packet in packets)
            authority_counts = Counter(str(_wiki_packet_metadata_value(packet, "authority") or "unknown") for packet in packets)
            scope_counts = Counter(str(_wiki_packet_metadata_value(packet, "scope_kind") or "unknown") for packet in packets)
            source_counts = Counter(str(_wiki_packet_metadata_value(packet, "source_of_truth") or "unknown") for packet in packets)
            freshness_counts = Counter(str(_wiki_packet_metadata_value(packet, "freshness") or "unknown") for packet in packets)
    except Exception as exc:
        payload.update({"status": "error", "reason": "wiki_packet_metadata_inspection_failed", "error": exc.__class__.__name__})
        return payload

    memory_kb_family_counts = {
        family: count for family, count in sorted(family_counts.items()) if family.startswith(MEMORY_KB_WIKI_FAMILY_PREFIX)
    }
    memory_kb_packet_count = sum(memory_kb_family_counts.values())
    payload.update(
        {
            "status": "supported" if packet_count else "not_found",
            "reason": None if packet_count else "no_wiki_packets_found",
            "packet_count": packet_count,
            "wiki_family_counts": dict(sorted(family_counts.items())),
            "owner_system_counts": dict(sorted(owner_counts.items())),
            "authority_counts": dict(sorted(authority_counts.items())),
            "scope_kind_counts": dict(sorted(scope_counts.items())),
            "source_of_truth_counts": dict(sorted(source_counts.items())),
            "freshness_counts": dict(sorted(freshness_counts.items())),
            "source_families_visible": bool(family_counts),
            "memory_kb": {
                "present": bool(memory_kb_family_counts),
                "packet_count": memory_kb_packet_count,
                "family_counts": memory_kb_family_counts,
                "owner_system": "domain-chip-memory" if memory_kb_packet_count else None,
                "source_of_truth": "SparkMemorySDK" if memory_kb_packet_count else None,
                "authority": "supporting_not_authoritative",
                "scope_kind": "governed_memory" if memory_kb_packet_count else None,
                "authority_boundary": "current_state_memory_outranks_wiki_for_mutable_user_facts",
            },
            "pages": pages[:page_limit],
        }
    )
    return payload


def _counter_from_mapping(raw: Any) -> Counter[str]:
    if not isinstance(raw, dict):
        return Counter()
    return Counter({str(key): int(value or 0) for key, value in raw.items()})


def _wiki_packet_metadata_value(packet: Any, key: str) -> Any:
    metadata = getattr(packet, "metadata", {}) or {}
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _wiki_packet_metadata_page(packet: Any) -> dict[str, Any]:
    metadata = getattr(packet, "metadata", {}) or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "packet_id": str(getattr(packet, "packet_id", "") or ""),
        "title": str(getattr(packet, "title", "") or ""),
        **{field: metadata.get(field) for field in WIKI_PACKET_METADATA_FIELDS},
    }


class MemoryKernelAdapter:
    """Single read surface for Builder's live memory kernel."""

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        state_db: StateDB,
        sdk_module: str | None = None,
        actor_id: str = "memory_cli",
    ) -> None:
        self.config_manager = config_manager
        self.state_db = state_db
        self.actor_id = actor_id
        self.sdk_module = str(
            sdk_module
            or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE)
            or DEFAULT_SDK_MODULE
        )
        self.runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=self.sdk_module)
        self.client = _load_sdk_client_for_module(module_name=self.sdk_module, home_path=config_manager.paths.home)

    def read_current_state(
        self,
        *,
        subject: str,
        predicate: str | None,
        entity_key: str | None = None,
        predicate_prefix: str | None = None,
        query: str | None = None,
        session_id: str | None,
        turn_id: str | None,
        source_surface: str,
        record_activity: bool = True,
        shadow_only: bool = False,
    ) -> MemoryKernelReadResult:
        method = "get_current_state"
        if self.client is None:
            read_result = _memory_kernel_abstained_read_result(method=method, reason="sdk_unavailable", shadow_only=shadow_only)
            if record_activity:
                _record_memory_read_requested_subject(
                    state_db=self.state_db,
                    method=method,
                    subject=subject,
                    predicate=predicate,
                    predicate_prefix=predicate_prefix,
                    query=query,
                    session_id=session_id,
                    turn_id=turn_id,
                    actor_id=self.actor_id,
                )
                _record_memory_read_event(
                    state_db=self.state_db,
                    result=read_result,
                    human_id=_human_id_from_subject(subject),
                    session_id=session_id,
                    turn_id=turn_id,
                    actor_id=self.actor_id,
                )
            return _memory_kernel_result(
                sdk_module=self.sdk_module,
                runtime=self.runtime,
                query=query or "",
                subject=subject,
                predicate=predicate,
                read_result=read_result,
            )
        _, read_result = _get_current_state_with_subject_fallback(
            client=self.client,
            state_db=self.state_db,
            subject_candidates=_subject_fallback_candidates(subject),
            predicate=predicate,
            entity_key=entity_key,
            predicate_prefix=predicate_prefix,
            session_id=session_id,
            turn_id=turn_id,
            actor_id=self.actor_id,
            source_surface=source_surface,
            shadow_only=shadow_only,
        )
        if record_activity:
            _record_memory_read_event(
                state_db=self.state_db,
                result=read_result,
                human_id=_human_id_from_subject(subject),
                session_id=session_id,
                turn_id=turn_id,
                actor_id=self.actor_id,
            )
        return _memory_kernel_result(
            sdk_module=self.sdk_module,
            runtime=self.runtime,
            query=query or "",
            subject=subject,
            predicate=predicate,
            read_result=read_result,
        )

    def read_historical_state(
        self,
        *,
        subject: str,
        predicate: str,
        as_of: str,
        entity_key: str | None = None,
        query: str | None = None,
        session_id: str | None,
        turn_id: str | None,
        source_surface: str,
        record_activity: bool = True,
        shadow_only: bool = False,
    ) -> MemoryKernelReadResult:
        method = "get_historical_state"
        if self.client is None:
            read_result = _memory_kernel_abstained_read_result(method=method, reason="sdk_unavailable", shadow_only=shadow_only)
            if record_activity:
                _record_memory_read_event(
                    state_db=self.state_db,
                    result=read_result,
                    human_id=_human_id_from_subject(subject),
                    session_id=session_id,
                    turn_id=turn_id,
                    actor_id=self.actor_id,
                )
            return _memory_kernel_result(
                sdk_module=self.sdk_module,
                runtime=self.runtime,
                query=query or "",
                subject=subject,
                predicate=predicate,
                read_result=read_result,
            )
        _, read_result = _get_historical_state_with_subject_fallback(
            client=self.client,
            state_db=self.state_db,
            subject_candidates=_subject_fallback_candidates(subject),
            predicate=predicate,
            as_of=as_of,
            entity_key=entity_key,
            session_id=session_id,
            turn_id=turn_id,
            actor_id=self.actor_id,
            source_surface=source_surface,
            shadow_only=shadow_only,
        )
        if record_activity:
            _record_memory_read_event(
                state_db=self.state_db,
                result=read_result,
                human_id=_human_id_from_subject(subject),
                session_id=session_id,
                turn_id=turn_id,
                actor_id=self.actor_id,
            )
        return _memory_kernel_result(
            sdk_module=self.sdk_module,
            runtime=self.runtime,
            query=query or "",
            subject=subject,
            predicate=predicate,
            read_result=read_result,
        )

    def search(
        self,
        *,
        method: str,
        query: str,
        subject: str | None = None,
        predicate: str | None = None,
        limit: int = 5,
        session_id: str | None,
        turn_id: str | None,
        source_surface: str,
        record_activity: bool = True,
        shadow_only: bool = False,
    ) -> MemoryKernelReadResult:
        normalized_query = str(query or "").strip()
        normalized_subject = _optional_string(subject)
        normalized_predicate = _optional_string(predicate)
        if self.client is None:
            read_result = _memory_kernel_abstained_read_result(method=method, reason="sdk_unavailable", shadow_only=shadow_only)
            if record_activity:
                _record_memory_read_event(
                    state_db=self.state_db,
                    result=read_result,
                    human_id=_human_id_from_subject(normalized_subject or ""),
                    session_id=session_id,
                    turn_id=turn_id,
                    actor_id=self.actor_id,
                )
            return _memory_kernel_result(
                sdk_module=self.sdk_module,
                runtime=self.runtime,
                query=normalized_query,
                subject=normalized_subject,
                predicate=normalized_predicate,
                read_result=read_result,
            )
        if record_activity:
            _record_memory_read_requested_subject(
                state_db=self.state_db,
                method=method,
                subject=normalized_subject or "",
                predicate=normalized_predicate,
                query=normalized_query,
                session_id=session_id,
                turn_id=turn_id,
                actor_id=self.actor_id,
            )
        raw = _call_sdk_method(
            self.client,
            method,
            {
                "query": normalized_query,
                "subject": normalized_subject,
                "predicate": normalized_predicate,
                "limit": limit,
                "session_id": session_id,
                "turn_id": turn_id,
                "timestamp": _now_iso(),
                "metadata": {"source_surface": source_surface},
            },
        )
        read_result = _normalize_read_result(raw=raw, method=method, shadow_only=shadow_only)
        if record_activity:
            _record_memory_read_event(
                state_db=self.state_db,
                result=read_result,
                human_id=_human_id_from_subject(normalized_subject or ""),
                session_id=session_id,
                turn_id=turn_id,
                actor_id=self.actor_id,
            )
        return _memory_kernel_result(
            sdk_module=self.sdk_module,
            runtime=self.runtime,
            query=normalized_query,
            subject=normalized_subject,
            predicate=normalized_predicate,
            read_result=read_result,
        )


def read_memory_kernel(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    method: str,
    query: str = "",
    subject: str | None = None,
    predicate: str | None = None,
    entity_key: str | None = None,
    predicate_prefix: str | None = None,
    as_of: str | None = None,
    limit: int = 5,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
    session_id: str | None = None,
    turn_id: str | None = None,
    source_surface: str = "memory_kernel",
    record_activity: bool = True,
) -> MemoryKernelReadResult:
    adapter = MemoryKernelAdapter(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=sdk_module,
        actor_id=actor_id,
    )
    normalized_method = str(method or "").strip().lower()
    normalized_subject = _optional_string(subject) or ""
    if normalized_method == "get_current_state":
        return adapter.read_current_state(
            subject=normalized_subject,
            predicate=_optional_string(predicate),
            entity_key=_optional_string(entity_key),
            predicate_prefix=predicate_prefix,
            query=query,
            session_id=session_id or f"memory-kernel:{actor_id}",
            turn_id=turn_id or f"{actor_id}:get-current-state",
            source_surface=source_surface,
            record_activity=record_activity,
        )
    if normalized_method == "get_historical_state":
        return adapter.read_historical_state(
            subject=normalized_subject,
            predicate=_optional_string(predicate) or "",
            as_of=_optional_string(as_of) or "",
            entity_key=_optional_string(entity_key),
            query=query,
            session_id=session_id or f"memory-kernel:{actor_id}",
            turn_id=turn_id or f"{actor_id}:get-historical-state",
            source_surface=source_surface,
            record_activity=record_activity,
        )
    if normalized_method in {"retrieve_evidence", "retrieve_events"}:
        return adapter.search(
            method=normalized_method,
            query=query,
            subject=_optional_string(subject),
            predicate=_optional_string(predicate),
            limit=limit,
            session_id=session_id or f"memory-kernel:{actor_id}",
            turn_id=turn_id or f"{actor_id}:{normalized_method}",
            source_surface=source_surface,
            record_activity=record_activity,
        )
    if normalized_method in {"hybrid_memory_retrieve", "hybrid_retrieve"}:
        hybrid = hybrid_memory_retrieve(
            config_manager=config_manager,
            state_db=state_db,
            query=query,
            subject=_optional_string(subject),
            predicate=_optional_string(predicate),
            entity_key=_optional_string(entity_key),
            as_of=_optional_string(as_of),
            limit=limit,
            sdk_module=sdk_module,
            actor_id=actor_id,
            session_id=session_id or f"memory-kernel:{actor_id}",
            turn_id=turn_id or f"{actor_id}:hybrid-memory-retrieve",
            source_surface=source_surface,
            record_activity=record_activity,
        )
        return _memory_kernel_result(
            sdk_module=hybrid.sdk_module,
            runtime=hybrid.runtime,
            query=hybrid.query,
            subject=hybrid.subject,
            predicate=hybrid.predicate,
            read_result=hybrid.read_result,
        )
    read_result = _memory_kernel_abstained_read_result(
        method=normalized_method or "unknown",
        reason="unsupported_memory_kernel_method",
        shadow_only=False,
    )
    return _memory_kernel_result(
        sdk_module=adapter.sdk_module,
        runtime=adapter.runtime,
        query=query,
        subject=_optional_string(subject),
        predicate=_optional_string(predicate),
        read_result=read_result,
    )


def hybrid_memory_retrieve(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    query: str,
    subject: str | None = None,
    predicate: str | None = None,
    entity_key: str | None = None,
    as_of: str | None = None,
    limit: int = 5,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
    session_id: str | None = None,
    turn_id: str | None = None,
    source_surface: str = "hybrid_memory_retrieve",
    record_activity: bool = True,
) -> HybridMemoryRetrievalResult:
    adapter = MemoryKernelAdapter(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=sdk_module,
        actor_id=actor_id,
    )
    normalized_query = str(query or "").strip()
    normalized_subject = _optional_string(subject)
    normalized_predicate = _optional_string(predicate)
    normalized_entity_key = _optional_string(entity_key)
    normalized_as_of = _optional_string(as_of)
    normalized_limit = max(1, int(limit or 1))
    session_id = session_id or f"hybrid-memory:{actor_id}"
    turn_id = turn_id or f"{actor_id}:hybrid-memory-retrieve"
    lane_summaries: list[dict[str, Any]] = []
    ranked_entries: list[dict[str, Any]] = []

    def add_lane(lane: str, kernel: MemoryKernelReadResult) -> None:
        lane_summaries.append(
            {
                "lane": lane,
                "read_method": kernel.read_method,
                "source_class": kernel.source_class,
                "status": kernel.read_result.status,
                "abstained": kernel.abstained,
                "record_count": len(kernel.records),
                "reason": kernel.reason,
            }
        )
        for index, record in enumerate(kernel.records):
            source_class = _hybrid_memory_source_class(lane=lane, kernel=kernel, record=record)
            score, score_reasons = _score_hybrid_memory_record(
                lane=lane,
                source_class=source_class,
                record=record,
                query=normalized_query,
                predicate=normalized_predicate,
                entity_key=normalized_entity_key,
            )
            ranked_entries.append(
                {
                    "lane": lane,
                    "source_class": source_class,
                    "score": score,
                    "score_reasons": score_reasons,
                    "record": record,
                    "record_key": _hybrid_memory_record_key(record),
                    "rank_tiebreak": index,
                }
            )

    if normalized_subject and normalized_predicate:
        add_lane(
            "current_state",
            adapter.read_current_state(
                subject=normalized_subject,
                predicate=normalized_predicate,
                entity_key=normalized_entity_key,
                query=normalized_query,
                session_id=session_id,
                turn_id=f"{turn_id}:current-state",
                source_surface=f"{source_surface}:current_state",
                record_activity=False,
            ),
        )
    if normalized_subject and normalized_entity_key and not normalized_predicate:
        add_lane(
            "entity_current_state",
            adapter.read_current_state(
                subject=normalized_subject,
                predicate=None,
                predicate_prefix="entity.",
                entity_key=normalized_entity_key,
                query=normalized_query,
                session_id=session_id,
                turn_id=f"{turn_id}:entity-current-state",
                source_surface=f"{source_surface}:entity_current_state",
                record_activity=False,
            ),
        )
    if normalized_subject and normalized_predicate and normalized_as_of:
        add_lane(
            "historical_state",
            adapter.read_historical_state(
                subject=normalized_subject,
                predicate=normalized_predicate,
                as_of=normalized_as_of,
                entity_key=normalized_entity_key,
                query=normalized_query,
                session_id=session_id,
                turn_id=f"{turn_id}:historical-state",
                source_surface=f"{source_surface}:historical_state",
                record_activity=False,
            ),
        )
    add_lane(
        "evidence",
        adapter.search(
            method="retrieve_evidence",
            query=normalized_query,
            subject=normalized_subject,
            predicate=normalized_predicate,
            limit=normalized_limit,
            session_id=session_id,
            turn_id=f"{turn_id}:evidence",
            source_surface=f"{source_surface}:evidence",
            record_activity=False,
        ),
    )
    add_lane(
        "events",
        adapter.search(
            method="retrieve_events",
            query=normalized_query,
            subject=normalized_subject,
            predicate=normalized_predicate,
            limit=normalized_limit,
            session_id=session_id,
            turn_id=f"{turn_id}:events",
            source_surface=f"{source_surface}:events",
            record_activity=False,
        ),
    )
    recent_lane, recent_records = _build_recent_conversation_lane(
        state_db=state_db,
        query=normalized_query,
        subject=normalized_subject,
        session_id=session_id,
        limit=normalized_limit,
    )
    lane_summaries.append(recent_lane)
    _extend_hybrid_ranked_entries(
        ranked_entries,
        lane="recent_conversation",
        source_class="recent_conversation",
        records=recent_records,
        query=normalized_query,
        predicate=normalized_predicate,
        entity_key=normalized_entity_key,
    )
    pending_lane, pending_records = _build_pending_task_lane(
        state_db=state_db,
        query=normalized_query,
        subject=normalized_subject,
        limit=normalized_limit,
    )
    lane_summaries.append(pending_lane)
    _extend_hybrid_ranked_entries(
        ranked_entries,
        lane="pending_tasks",
        source_class="pending_task",
        records=pending_records,
        query=normalized_query,
        predicate=normalized_predicate,
        entity_key=normalized_entity_key,
    )
    procedural_lane, procedural_records = _build_procedural_lesson_lane(
        state_db=state_db,
        query=normalized_query,
        limit=normalized_limit,
    )
    lane_summaries.append(procedural_lane)
    _extend_hybrid_ranked_entries(
        ranked_entries,
        lane="procedural_lessons",
        source_class="procedural_lesson",
        records=procedural_records,
        query=normalized_query,
        predicate=normalized_predicate,
        entity_key=normalized_entity_key,
    )
    graph_sidecar_lane = _build_graph_sidecar_shadow_lane(
        config_manager=config_manager,
        sdk_module=adapter.sdk_module,
        query=normalized_query,
        subject=normalized_subject,
        predicate=normalized_predicate,
        entity_key=normalized_entity_key,
        as_of=normalized_as_of,
        limit=normalized_limit,
        local_records=[entry["record"] for entry in ranked_entries],
    )
    graph_sidecar_records = graph_sidecar_lane.pop("hit_records", [])
    lane_summaries.append(graph_sidecar_lane)
    _extend_hybrid_ranked_entries(
        ranked_entries,
        lane="typed_temporal_graph",
        source_class="graphiti_temporal_graph",
        records=graph_sidecar_records,
        query=normalized_query,
        predicate=normalized_predicate,
        entity_key=normalized_entity_key,
    )
    wiki_lane, wiki_records = _build_wiki_packet_lane(
        config_manager=config_manager,
        sdk_module=adapter.sdk_module,
        query=normalized_query,
        limit=normalized_limit,
    )
    lane_summaries.append(wiki_lane)
    for index, record in enumerate(wiki_records):
        source_class = "obsidian_llm_wiki_packets"
        score, score_reasons = _score_hybrid_memory_record(
            lane="wiki_packets",
            source_class=source_class,
            record=record,
            query=normalized_query,
            predicate=normalized_predicate,
            entity_key=normalized_entity_key,
        )
        ranked_entries.append(
            {
                "lane": "wiki_packets",
                "source_class": source_class,
                "score": score,
                "score_reasons": score_reasons,
                "record": record,
                "record_key": _hybrid_memory_record_key(record),
                "rank_tiebreak": index,
            }
        )

    ranked_entries.sort(
        key=lambda entry: (
            float(entry["score"]),
            -int(entry["rank_tiebreak"]),
        ),
        reverse=True,
    )
    selected_records: list[dict[str, Any]] = []
    candidates: list[HybridMemoryCandidate] = []
    seen_keys: set[str] = set()
    for entry in ranked_entries:
        record = entry["record"]
        record_key = str(entry["record_key"])
        stale = entry["lane"] != "historical_state" and _memory_kernel_record_is_stale(record)
        selected = False
        reason_selected = None
        reason_discarded = None
        if record_key in seen_keys:
            reason_discarded = "duplicate_lower_rank"
        elif stale:
            reason_discarded = "stale_or_superseded"
            seen_keys.add(record_key)
        elif len(selected_records) >= normalized_limit:
            reason_discarded = "outside_context_budget"
            seen_keys.add(record_key)
        else:
            selected = True
            reason_selected = ",".join(entry["score_reasons"]) or "ranked_candidate"
            selected_records.append(record)
            seen_keys.add(record_key)
        candidates.append(
            HybridMemoryCandidate(
                lane=str(entry["lane"]),
                source_class=str(entry["source_class"]),
                score=round(float(entry["score"]), 3),
                selected=selected,
                record=record,
                reason_selected=reason_selected,
                reason_discarded=reason_discarded,
            )
        )

    max_context_chars = int(config_manager.get_path("spark.memory.hybrid_context.max_chars", default=3600) or 3600)
    context_packet = _build_hybrid_memory_context_packet(
        query=normalized_query,
        candidates=candidates,
        lane_summaries=lane_summaries,
        max_chars=max_context_chars,
    )
    packet_candidate_keys = {
        str(item.get("candidate_key") or "")
        for section in context_packet.sections
        for item in list(section.get("items") or [])
        if isinstance(item, dict)
    }
    retrieval_trace = {
        "hybrid_memory_retrieve": {
            "query": normalized_query,
            "subject": normalized_subject,
            "predicate": normalized_predicate,
            "entity_key": normalized_entity_key,
            "as_of": normalized_as_of,
            "limit": normalized_limit,
            "lane_summaries": lane_summaries,
            "context_packet": context_packet.to_payload(),
            "candidate_count": len(candidates),
            "selected_count": len(selected_records),
            "candidates": [
                {
                    "lane": candidate.lane,
                    "source_class": candidate.source_class,
                    "score": candidate.score,
                    "selected": candidate.selected,
                    "survived_context_budget": _hybrid_memory_candidate_key(candidate) in packet_candidate_keys,
                    "reason_selected": candidate.reason_selected,
                    "reason_discarded": candidate.reason_discarded,
                    "predicate": candidate.record.get("predicate"),
                    "memory_role": candidate.record.get("memory_role"),
                }
                for candidate in candidates
            ],
        }
    }
    read_result = MemoryReadResult(
        status="supported" if selected_records else "abstained",
        method="hybrid_memory_retrieve",
        memory_role="hybrid",
        records=selected_records,
        provenance=[
            {
                "lane": candidate.lane,
                "source_class": candidate.source_class,
                "score": candidate.score,
                "predicate": candidate.record.get("predicate"),
                "memory_role": candidate.record.get("memory_role"),
            }
            for candidate in candidates
            if candidate.selected
        ],
        retrieval_trace=retrieval_trace,
        answer_explanation={
            "method": "hybrid_memory_retrieve",
            "selected_count": len(selected_records),
            "authority_order": [
                "current_state",
                "historical_state",
                "events",
                "evidence",
                "wiki_packets",
                "typed_temporal_graph",
            ],
            "context_packet_sections": [section["section"] for section in context_packet.sections],
            "context_packet_source_mix": context_packet.source_mix,
            "context_packet_promotion_gates": context_packet.trace.get("promotion_gates"),
        },
        abstained=not bool(selected_records),
        reason=None if selected_records else ("no_hybrid_candidates" if not candidates else "all_candidates_discarded"),
        shadow_only=False,
    )
    if record_activity:
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=_human_id_from_subject(normalized_subject or ""),
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
    return HybridMemoryRetrievalResult(
        sdk_module=adapter.sdk_module,
        query=normalized_query,
        subject=normalized_subject,
        predicate=normalized_predicate,
        entity_key=normalized_entity_key,
        as_of=normalized_as_of,
        runtime=adapter.runtime,
        read_result=read_result,
        candidates=candidates,
        lane_summaries=lane_summaries,
        context_packet=context_packet,
        shadow_only_eval=False,
    )


def lookup_current_state_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    subject: str,
    predicate: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
) -> MemoryCurrentStateLookupResult:
    kernel = MemoryKernelAdapter(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=sdk_module,
        actor_id=actor_id,
    ).read_current_state(
        subject=subject,
        predicate=predicate,
        entity_key=_default_current_state_entity_key(predicate),
        session_id=f"memory-lookup:{actor_id}",
        turn_id=f"{actor_id}:lookup",
        source_surface="memory_cli_lookup",
    )
    return MemoryCurrentStateLookupResult(
        sdk_module=kernel.sdk_module,
        subject=subject,
        predicate=predicate,
        runtime=kernel.runtime,
        read_result=kernel.read_result,
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
    kernel = MemoryKernelAdapter(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=sdk_module,
        actor_id=actor_id,
    ).read_historical_state(
        subject=subject,
        predicate=predicate,
        as_of=as_of,
        entity_key=_default_current_state_entity_key(predicate),
        session_id=f"memory-history:{actor_id}",
        turn_id=f"{actor_id}:lookup-history",
        source_surface="memory_cli_lookup_historical",
    )
    return MemoryHistoricalStateLookupResult(
        sdk_module=kernel.sdk_module,
        subject=subject,
        predicate=predicate,
        as_of=as_of,
        runtime=kernel.runtime,
        read_result=kernel.read_result,
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


def recover_task_context_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    query: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
    limit: int = 5,
) -> MemoryTaskRecoveryResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    subject = _subject_for_human_id(human_id)
    session_id = f"memory-task-recovery:{actor_id}"
    turn_id = f"{actor_id}:recover-task-context"
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method="recover_task_context",
            memory_role="unknown",
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
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
        return MemoryTaskRecoveryResult(
            sdk_module=module_name,
            human_id=human_id,
            subject=subject,
            query=query,
            runtime=runtime,
            read_result=read_result,
        )

    _record_memory_read_requested_subject(
        state_db=state_db,
        method="recover_task_context",
        subject=subject,
        predicate=None,
        query=query,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    raw = _call_sdk_method(
        client,
        "recover_task_context",
        {
            "query": query,
            "subject": subject,
            "limit": max(1, min(10, int(limit or 1))),
        },
    )
    read_result = _normalize_read_result(raw=raw, method="recover_task_context", shadow_only=False)
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    return MemoryTaskRecoveryResult(
        sdk_module=module_name,
        human_id=human_id,
        subject=subject,
        query=query,
        runtime=runtime,
        read_result=read_result,
    )


def recall_episodic_context_in_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    query: str,
    sdk_module: str | None = None,
    actor_id: str = "memory_cli",
    since: str | None = None,
    until: str | None = None,
    limit: int = 5,
) -> MemoryEpisodicRecallResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    subject = _subject_for_human_id(human_id)
    session_id = f"memory-episodic-recall:{actor_id}"
    turn_id = f"{actor_id}:recall-episodic-context"
    if client is None:
        read_result = MemoryReadResult(
            status="abstained",
            method="recall_episodic_context",
            memory_role="unknown",
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
            session_id=session_id,
            turn_id=turn_id,
            actor_id=actor_id,
        )
        return MemoryEpisodicRecallResult(
            sdk_module=module_name,
            human_id=human_id,
            subject=subject,
            query=query,
            runtime=runtime,
            read_result=read_result,
        )

    _record_memory_read_requested_subject(
        state_db=state_db,
        method="recall_episodic_context",
        subject=subject,
        predicate=None,
        query=query,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    raw = _call_sdk_method(
        client,
        "recall_episodic_context",
        {
            "query": query,
            "subject": subject,
            "since": since,
            "until": until,
            "limit": max(1, min(10, int(limit or 1))),
        },
    )
    read_result = _normalize_read_result(raw=raw, method="recall_episodic_context", shadow_only=False)
    _record_memory_read_event(
        state_db=state_db,
        result=read_result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    return MemoryEpisodicRecallResult(
        sdk_module=module_name,
        human_id=human_id,
        subject=subject,
        query=query,
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
    record_activity: bool = True,
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
        record_activity=record_activity,
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
    record_activity: bool = True,
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
        record_activity=record_activity,
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
    return _write_profile_fact_memory_operation(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        predicate=predicate,
        value=value,
        evidence_text=evidence_text,
        fact_name=fact_name,
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        actor_id=actor_id,
        operation="update",
    )


def write_structured_evidence_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    evidence_text: str,
    domain_pack: str,
    evidence_kind: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "structured_evidence_loader",
    salience_decision: MemorySalienceDecision | None = None,
) -> MemoryWriteResult:
    def _belief_record_observation_id(record: dict[str, Any]) -> str | None:
        return _optional_string(record.get("observation_id")) or _optional_string(
            (record.get("metadata") or {}).get("observation_id")
        )

    def _belief_record_supersedes(record: dict[str, Any]) -> str | None:
        lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return _optional_string(lifecycle.get("supersedes")) or _optional_string(metadata.get("supersedes"))

    def _belief_record_text(record: dict[str, Any]) -> str:
        text = str(record.get("text") or "").strip()
        if text:
            return text
        return str((record.get("metadata") or {}).get("value") or record.get("value") or "").strip()

    def _belief_record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
        return (
            str(record.get("timestamp") or ""),
            str(_belief_record_observation_id(record) or ""),
        )

    def _evidence_record_observation_id(record: dict[str, Any]) -> str | None:
        return _optional_string(record.get("observation_id")) or _optional_string(
            (record.get("metadata") or {}).get("observation_id")
        )

    def _evidence_record_supersedes(record: dict[str, Any]) -> str | None:
        lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return _optional_string(lifecycle.get("supersedes")) or _optional_string(metadata.get("supersedes"))

    def _evidence_record_text(record: dict[str, Any]) -> str:
        text = str(record.get("text") or "").strip()
        if text:
            return text
        return str((record.get("metadata") or {}).get("value") or record.get("value") or "").strip()

    def _evidence_record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
        return (
            str(record.get("timestamp") or ""),
            str(_evidence_record_observation_id(record) or ""),
        )

    def _select_active_evidence_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matching = [
            record
            for record in records
            if str(record.get("memory_role") or "").strip() == "structured_evidence"
            or str(record.get("predicate") or "").strip() == predicate
        ]
        if not matching:
            return []
        superseded_ids = {
            superseded_id
            for superseded_id in (_evidence_record_supersedes(record) for record in matching)
            if superseded_id
        }
        active = [
            record for record in matching if (_evidence_record_observation_id(record) or "") not in superseded_ids
        ]
        return sorted(active or matching, key=_evidence_record_sort_key)

    def _select_active_belief_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matching = [
            record
            for record in records
            if str(record.get("memory_role") or "").strip() == "belief"
            or str(record.get("predicate") or "").strip().startswith("belief.telegram.")
        ]
        if not matching:
            return []
        superseded_ids = {
            superseded_id
            for superseded_id in (_belief_record_supersedes(record) for record in matching)
            if superseded_id
        }
        active = [
            record for record in matching if (_belief_record_observation_id(record) or "") not in superseded_ids
        ]
        return sorted(active or matching, key=_belief_record_sort_key)

    def _overlap_tokens(text: str) -> set[str]:
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "they",
            "have",
            "need",
            "during",
            "because",
            "users",
            "user",
            "keep",
            "asked",
            "asks",
            "help",
            "support",
            "said",
            "says",
            "think",
            "belief",
            "about",
            "current",
            "will",
        }
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.casefold())
            if len(token) >= 4 and token not in stopwords
        }

    def _evidence_invalidates_belief(evidence: str, belief: str) -> bool:
        evidence_tokens = _overlap_tokens(evidence)
        belief_tokens = _overlap_tokens(belief)
        if not evidence_tokens or not belief_tokens:
            return False
        overlap = evidence_tokens & belief_tokens
        return len(overlap) >= 2 or any(len(token) >= 8 for token in overlap)

    def _derive_belief_pack_from_evidence(text: str, corroborating_records: list[dict[str, Any]]) -> str:
        overlap_candidates: set[str] = set()
        for record in corroborating_records:
            overlap_candidates.update(_overlap_tokens(text) & _overlap_tokens(_evidence_record_text(record)))
        tokens = [token for token in re.findall(r"[a-z0-9]+", text.casefold()) if len(token) >= 4]
        topic_tokens: list[str] = []
        for token in tokens:
            if token in {
                "this",
                "that",
                "with",
                "from",
                "because",
                "during",
                "they",
                "have",
                "users",
                "user",
                "keep",
                "need",
                "still",
                "drop",
                "drops",
                "fails",
                "flow",
                "retry",
            }:
                continue
            if overlap_candidates and token not in overlap_candidates:
                continue
            if token not in topic_tokens:
                topic_tokens.append(token)
            if len(topic_tokens) >= 3:
                break
        suffix = "_".join(topic_tokens).strip("_")
        return f"evidence_{suffix}" if suffix else "beliefs_and_inferences"

    def _belief_text_from_evidence(text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return normalized
        if normalized.casefold().startswith("i think "):
            return normalized
        lowered_initial = normalized[0].lower() + normalized[1:] if len(normalized) > 1 else normalized.lower()
        return f"I think {lowered_initial}"

    normalized_text = str(evidence_text or "").strip()
    if not normalized_text:
        return MemoryWriteResult(
            status="skipped",
            operation="create",
            method="write_observation",
            memory_role="structured_evidence",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_structured_evidence_text",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(
            operation="create",
            method="write_observation",
            default_role="structured_evidence",
        )
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="create",
            method="write_observation",
            memory_role="structured_evidence",
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
    timestamp = _now_iso()
    archive_at = (
        datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(days=STRUCTURED_EVIDENCE_ARCHIVE_DAYS)
    ).isoformat()
    normalized_pack = re.sub(r"[^a-z0-9]+", "_", str(domain_pack or "generic").strip().lower()).strip("_") or "generic"
    predicate = f"evidence.telegram.{normalized_pack}"
    existing_beliefs = retrieve_memory_evidence_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        query=normalized_text,
        subject=subject,
        limit=10,
        actor_id=actor_id,
    )
    existing_evidence = retrieve_memory_evidence_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        query=normalized_pack,
        subject=subject,
        predicate=predicate,
        limit=10,
        actor_id=actor_id,
    )
    active_beliefs: list[dict[str, Any]] = []
    if not existing_beliefs.read_result.abstained and existing_beliefs.read_result.records:
        active_beliefs = _select_active_belief_records(existing_beliefs.read_result.records)
    active_evidence: list[dict[str, Any]] = []
    if not existing_evidence.read_result.abstained and existing_evidence.read_result.records:
        active_evidence = _select_active_evidence_records(existing_evidence.read_result.records)
    corroborating_evidence_records = [
        record
        for record in active_evidence
        if _evidence_invalidates_belief(normalized_text, _evidence_record_text(record))
    ]
    invalidated_belief_ids = [
        belief_id
        for belief_id in (
            _belief_record_observation_id(record)
            for record in active_beliefs
            if _evidence_invalidates_belief(normalized_text, _belief_record_text(record))
        )
        if belief_id
    ]
    invalidated_belief_texts = [
        _belief_record_text(record)
        for record in active_beliefs
        if (_belief_record_observation_id(record) or "") in invalidated_belief_ids
    ]
    observation = {
        "subject": subject,
        "predicate": predicate,
        "value": normalized_text,
        "operation": "create",
        "memory_role": "structured_evidence",
        "retention_class": "episodic_archive",
        "text": normalized_text,
        **(salience_decision.metadata() if salience_decision is not None else {}),
    }
    if invalidated_belief_ids:
        observation["conflicts_with"] = list(invalidated_belief_ids)
        observation["belief_lifecycle_action"] = "invalidated"
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="create",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        memory_role="structured_evidence",
        summary="Spark memory write requested for structured evidence.",
        salience_decision=salience_decision,
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "create",
            "subject": subject,
            "predicate": predicate,
            "value": normalized_text,
            "text": normalized_text,
            "memory_role": "structured_evidence",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "conflicts_with": list(invalidated_belief_ids),
            "retention_class": "episodic_archive",
            "document_time": timestamp,
            "valid_from": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "structured_evidence",
                "source_surface": "researcher_bridge",
                "evidence_kind": evidence_kind,
                "domain_pack": normalized_pack,
                "normalized_value": normalized_text,
                "value": normalized_text,
                "belief_lifecycle_action": "invalidated" if invalidated_belief_ids else None,
                "invalidated_belief_ids": list(invalidated_belief_ids),
                "invalidated_belief_texts": list(invalidated_belief_texts),
                "archive_after_days": STRUCTURED_EVIDENCE_ARCHIVE_DAYS,
                "archive_at": archive_at,
                **(salience_decision.metadata() if salience_decision is not None else {}),
            },
        },
    )
    result = _normalize_write_result(
        raw=raw,
        operation="create",
        method="write_observation",
        default_role="structured_evidence",
    )
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    current_state_observation = _derive_current_state_observation_from_evidence(normalized_text)
    if result.accepted_count > 0 and corroborating_evidence_records:
        try:
            write_belief_to_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                belief_text=_belief_text_from_evidence(normalized_text),
                domain_pack=_derive_belief_pack_from_evidence(normalized_text, corroborating_evidence_records),
                belief_kind="evidence_consolidation",
                session_id=session_id,
                turn_id=turn_id,
                channel_kind=channel_kind,
                actor_id=f"{actor_id}_belief_consolidator",
            )
        except Exception:
            pass
    if result.accepted_count > 0 and _should_promote_current_state_from_evidence(
        observation=current_state_observation,
        corroborating_evidence_records=corroborating_evidence_records,
    ):
        if current_state_observation is not None and str(current_state_observation.value or "").strip():
            try:
                write_profile_fact_to_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    predicate=current_state_observation.predicate,
                    value=current_state_observation.value,
                    evidence_text=normalized_text,
                    fact_name=current_state_observation.fact_name,
                    session_id=session_id,
                    turn_id=turn_id,
                    channel_kind=channel_kind,
                    actor_id=f"{actor_id}_current_state_consolidator",
                )
            except Exception:
                pass
    return result


def archive_structured_evidence_from_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    evidence_text: str,
    evidence_observation_id: str | None,
    archive_reason: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "structured_evidence_archiver",
) -> MemoryWriteResult:
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="delete", default_role="structured_evidence")
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="delete",
            method="write_observation",
            memory_role="structured_evidence",
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
    timestamp = _now_iso()
    normalized_text = str(evidence_text or "").strip()
    observation = {
        "subject": subject,
        "predicate": predicate,
        "value": normalized_text,
        "operation": "delete",
        "memory_role": "structured_evidence",
        "retention_class": "episodic_archive",
        "text": normalized_text,
        "structured_evidence_lifecycle_action": "archived",
    }
    if evidence_observation_id:
        observation["supersedes"] = evidence_observation_id
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="delete",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        memory_role="structured_evidence",
        summary="Spark memory write requested for structured evidence archive.",
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "delete",
            "subject": subject,
            "predicate": predicate,
            "value": normalized_text,
            "text": normalized_text,
            "memory_role": "structured_evidence",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "supersedes": evidence_observation_id,
            "retention_class": "episodic_archive",
            "document_time": timestamp,
            "valid_to": timestamp,
            "deleted_at": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "structured_evidence",
                "source_surface": "researcher_bridge",
                "value": normalized_text,
                "archive_reason": archive_reason,
                "structured_evidence_lifecycle_action": "archived",
                "archived_structured_evidence_observation_id": evidence_observation_id,
            },
        },
    )
    result = _normalize_write_result(
        raw=raw,
        operation="delete",
        method="write_observation",
        default_role="structured_evidence",
    )
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    if result.accepted_count > 0:
        _record_memory_lifecycle_transition(
            state_db=state_db,
            human_id=human_id,
            transition_kind="archive",
            memory_role="structured_evidence",
            source_predicate=predicate,
            source_text=normalized_text,
            source_observation_id=evidence_observation_id,
            reason=archive_reason,
            retention_class="episodic_archive",
            lifecycle_action="archived",
            destination="archive_tombstone",
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            actor_id=actor_id,
        )
    return result


def write_belief_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    belief_text: str,
    domain_pack: str,
    belief_kind: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "belief_loader",
    salience_decision: MemorySalienceDecision | None = None,
) -> MemoryWriteResult:
    def _belief_record_observation_id(record: dict[str, Any]) -> str | None:
        return _optional_string(record.get("observation_id")) or _optional_string(
            (record.get("metadata") or {}).get("observation_id")
        )

    def _belief_record_supersedes(record: dict[str, Any]) -> str | None:
        lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return _optional_string(lifecycle.get("supersedes")) or _optional_string(metadata.get("supersedes"))

    def _belief_record_text(record: dict[str, Any]) -> str:
        text = str(record.get("text") or "").strip()
        if text:
            return text
        return str((record.get("metadata") or {}).get("value") or record.get("value") or "").strip()

    def _belief_record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
        return (
            str(record.get("timestamp") or ""),
            str(_belief_record_observation_id(record) or ""),
        )

    def _select_latest_active_belief_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        matching = [
            record
            for record in records
            if str(record.get("predicate") or "").strip() == predicate
            and str(record.get("memory_role") or "").strip() == "belief"
        ]
        if not matching:
            return None
        superseded_ids = {
            superseded_id
            for superseded_id in (_belief_record_supersedes(record) for record in matching)
            if superseded_id
        }
        active = [
            record for record in matching if (_belief_record_observation_id(record) or "") not in superseded_ids
        ]
        candidates = active or matching
        return sorted(candidates, key=_belief_record_sort_key)[-1]

    normalized_text = str(belief_text or "").strip()
    if not normalized_text:
        return MemoryWriteResult(
            status="skipped",
            operation="create",
            method="write_observation",
            memory_role="belief",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_belief_text",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(
            operation="create",
            method="write_observation",
            default_role="belief",
        )
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="create",
            method="write_observation",
            memory_role="belief",
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
    timestamp = _now_iso()
    revalidate_at = (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(days=BELIEF_REVALIDATION_DAYS)).isoformat()
    normalized_pack = re.sub(r"[^a-z0-9]+", "_", str(domain_pack or "belief").strip().lower()).strip("_") or "belief"
    predicate = f"belief.telegram.{normalized_pack}"
    existing_beliefs = retrieve_memory_evidence_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        query=normalized_pack,
        subject=subject,
        predicate=predicate,
        limit=10,
        actor_id=actor_id,
        record_activity=False,
    )
    prior_belief = None
    if not existing_beliefs.read_result.abstained and existing_beliefs.read_result.records:
        prior_belief = _select_latest_active_belief_record(existing_beliefs.read_result.records)
    supersedes = _belief_record_observation_id(prior_belief) if prior_belief else None
    prior_belief_text = _belief_record_text(prior_belief) if prior_belief else ""
    conflicts_with = (
        [supersedes]
        if supersedes and prior_belief_text and prior_belief_text.casefold() != normalized_text.casefold()
        else []
    )
    observation = {
        "subject": subject,
        "predicate": predicate,
        "value": normalized_text,
        "operation": "create",
        "memory_role": "belief",
        "retention_class": "derived_belief",
        "text": normalized_text,
        **(salience_decision.metadata() if salience_decision is not None else {}),
    }
    if supersedes:
        observation["supersedes"] = supersedes
    if conflicts_with:
        observation["conflicts_with"] = list(conflicts_with)
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="create",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        memory_role="belief",
        summary="Spark memory write requested for derived belief capture.",
        salience_decision=salience_decision,
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "create",
            "subject": subject,
            "predicate": predicate,
            "value": normalized_text,
            "text": normalized_text,
            "memory_role": "belief",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "supersedes": supersedes,
            "conflicts_with": conflicts_with,
            "retention_class": "derived_belief",
            "document_time": timestamp,
            "valid_from": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "belief",
                "source_surface": "researcher_bridge",
                "belief_kind": belief_kind,
                "domain_pack": normalized_pack,
                "normalized_value": normalized_text,
                "value": normalized_text,
                "supersedes_previous_belief": bool(supersedes),
                "previous_belief_text": prior_belief_text or None,
                "revalidate_after_days": BELIEF_REVALIDATION_DAYS,
                "revalidate_at": revalidate_at,
                **(salience_decision.metadata() if salience_decision is not None else {}),
            },
        },
    )
    result = _normalize_write_result(
        raw=raw,
        operation="create",
        method="write_observation",
        default_role="belief",
    )
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    if result.accepted_count > 0 and supersedes:
        _record_memory_lifecycle_transition(
            state_db=state_db,
            human_id=human_id,
            transition_kind="supersession",
            memory_role="belief",
            source_predicate=predicate,
            source_text=prior_belief_text or normalized_text,
            source_observation_id=supersedes,
            reason="newer_belief_supersedes_prior_belief",
            retention_class="derived_belief",
            lifecycle_action="superseded",
            destination="historical_belief_state",
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            actor_id=actor_id,
            old_value=prior_belief_text or None,
            new_value=normalized_text,
            extra_facts={
                "new_source_predicate": predicate,
                "new_belief_text": _preview_memory_text(normalized_text),
                "conflicts_with": list(conflicts_with),
            },
        )
    return result


def archive_belief_from_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    belief_text: str,
    belief_observation_id: str | None,
    archive_reason: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "belief_archiver",
) -> MemoryWriteResult:
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="delete", default_role="belief")
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="delete",
            method="write_observation",
            memory_role="belief",
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
    timestamp = _now_iso()
    observation = {
        "subject": subject,
        "predicate": predicate,
        "value": belief_text,
        "operation": "delete",
        "memory_role": "belief",
        "retention_class": "derived_belief",
        "text": belief_text,
        "belief_lifecycle_action": "archived",
    }
    if belief_observation_id:
        observation["supersedes"] = belief_observation_id
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="delete",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        memory_role="belief",
        summary="Spark memory write requested for belief archive.",
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "delete",
            "subject": subject,
            "predicate": predicate,
            "value": belief_text,
            "text": belief_text,
            "memory_role": "belief",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "retention_class": "derived_belief",
            "document_time": timestamp,
            "valid_to": timestamp,
            "deleted_at": timestamp,
            "supersedes": belief_observation_id,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "belief",
                "source_surface": "researcher_bridge",
                "archive_reason": archive_reason,
                "belief_lifecycle_action": "archived",
                "archived_belief_observation_id": belief_observation_id,
                "value": belief_text,
            },
        },
    )
    result = _normalize_write_result(raw=raw, operation="delete", default_role="belief")
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    if result.accepted_count > 0:
        _record_memory_lifecycle_transition(
            state_db=state_db,
            human_id=human_id,
            transition_kind="archive",
            memory_role="belief",
            source_predicate=predicate,
            source_text=belief_text,
            source_observation_id=belief_observation_id,
            reason=archive_reason,
            retention_class="derived_belief",
            lifecycle_action="archived",
            destination="archive_tombstone",
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            actor_id=actor_id,
        )
    return result


def archive_raw_episode_from_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    episode_text: str,
    raw_episode_observation_id: str | None,
    archive_reason: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "raw_episode_archiver",
) -> MemoryWriteResult:
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="delete", default_role="episodic")
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="delete",
            method="write_observation",
            memory_role="episodic",
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
    timestamp = _now_iso()
    normalized_text = str(episode_text or "").strip()
    observation = {
        "subject": subject,
        "predicate": "raw_turn",
        "value": normalized_text,
        "operation": "delete",
        "memory_role": "episodic",
        "retention_class": "episodic_archive",
        "text": normalized_text,
        "raw_episode": True,
        "raw_episode_lifecycle_action": "archived",
    }
    if raw_episode_observation_id:
        observation["supersedes"] = raw_episode_observation_id
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="delete",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        memory_role="episodic",
        summary="Spark memory write requested for raw episode archive.",
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "delete",
            "subject": subject,
            "predicate": "raw_turn",
            "value": normalized_text,
            "text": normalized_text,
            "memory_role": "episodic",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "supersedes": raw_episode_observation_id,
            "retention_class": "episodic_archive",
            "document_time": timestamp,
            "valid_to": timestamp,
            "deleted_at": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "episodic",
                "source_surface": "researcher_bridge",
                "raw_episode": True,
                "value": normalized_text,
                "archive_reason": archive_reason,
                "raw_episode_lifecycle_action": "archived",
                "archived_raw_episode_observation_id": raw_episode_observation_id,
            },
        },
    )
    result = _normalize_write_result(
        raw=raw,
        operation="delete",
        method="write_observation",
        default_role="episodic",
    )
    _record_memory_write_event(
        state_db=state_db,
        result=result,
        human_id=human_id,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )
    if result.accepted_count > 0:
        _record_memory_lifecycle_transition(
            state_db=state_db,
            human_id=human_id,
            transition_kind="archive",
            memory_role="episodic",
            source_predicate="raw_turn",
            source_text=normalized_text,
            source_observation_id=raw_episode_observation_id,
            reason=archive_reason,
            retention_class="episodic_archive",
            lifecycle_action="archived",
            destination="archive_tombstone",
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            actor_id=actor_id,
        )
    return result


def write_raw_episode_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    episode_text: str,
    domain_pack: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "raw_episode_loader",
    salience_decision: MemorySalienceDecision | None = None,
) -> MemoryWriteResult:
    normalized_text = str(episode_text or "").strip()
    if not normalized_text:
        return MemoryWriteResult(
            status="skipped",
            operation="create",
            method="write_observation",
            memory_role="episodic",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_raw_episode_text",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(
            operation="create",
            method="write_observation",
            default_role="episodic",
        )
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="create",
            method="write_observation",
            memory_role="episodic",
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
    timestamp = _now_iso()
    archive_at = (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(days=RAW_EPISODE_ARCHIVE_DAYS)).isoformat()
    normalized_pack = re.sub(r"[^a-z0-9]+", "_", str(domain_pack or "raw_episode").strip().lower()).strip("_") or "raw_episode"
    observation = {
        "subject": subject,
        "predicate": "raw_turn",
        "value": normalized_text,
        "operation": "create",
        "memory_role": "episodic",
        "retention_class": "episodic_archive",
        "text": normalized_text,
        **(salience_decision.metadata() if salience_decision is not None else {}),
    }
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation="create",
        human_id=human_id,
        observations=[observation],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        memory_role="episodic",
        summary="Spark memory write requested for raw episode capture.",
        salience_decision=salience_decision,
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "create",
            "subject": subject,
            "predicate": "raw_turn",
            "value": normalized_text,
            "text": normalized_text,
            "memory_role": "episodic",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "retention_class": "episodic_archive",
            "document_time": timestamp,
            "valid_from": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "episodic",
                "source_surface": "researcher_bridge",
                "domain_pack": normalized_pack,
                "raw_episode": True,
                "normalized_value": normalized_text,
                "value": normalized_text,
                "archive_after_days": RAW_EPISODE_ARCHIVE_DAYS,
                "archive_at": archive_at,
                **(salience_decision.metadata() if salience_decision is not None else {}),
            },
        },
    )
    result = _normalize_write_result(
        raw=raw,
        operation="create",
        method="write_observation",
        default_role="episodic",
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


def delete_profile_fact_from_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    evidence_text: str,
    fact_name: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "profile_fact_deleter",
) -> MemoryWriteResult:
    if not predicate:
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
            reason="no_profile_fact_predicate",
        )
    return _write_profile_fact_memory_operation(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        predicate=predicate,
        value=None,
        evidence_text=evidence_text,
        fact_name=fact_name,
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        actor_id=actor_id,
        operation="delete",
    )


def _write_profile_fact_memory_operation(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    value: str | None,
    evidence_text: str,
    fact_name: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str,
    operation: str,
) -> MemoryWriteResult:
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation=operation)
    if not bool(config_manager.get_path("spark.memory.write_profile_facts", default=True)):
        return _disabled_write_result(operation=operation, reason="profile_fact_memory_writes_disabled")
    salience_decision = evaluate_memory_salience(
        predicate=predicate,
        value=value,
        evidence_text=evidence_text,
        operation=operation,
    )
    if not salience_decision.should_write:
        result = MemoryWriteResult(
            status="skipped",
            operation=operation,
            method="write_observation",
            memory_role="current_state",
            accepted_count=0,
            rejected_count=1,
            skipped_count=0,
            abstained=False,
            retrieval_trace={"memory_salience": salience_decision.metadata()},
            provenance=[],
            reason=salience_decision.reason_code,
        )
        _record_memory_salience_policy_block(
            state_db=state_db,
            human_id=human_id,
            predicate=predicate,
            value=value,
            evidence_text=evidence_text,
            decision=salience_decision,
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            actor_id=actor_id,
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
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation=operation,
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
    timestamp = _now_iso()
    retention_class = _profile_fact_retention_class(predicate)
    revalidate_after_days = active_state_revalidation_days(predicate)
    revalidate_at = active_state_revalidate_at(predicate=predicate, timestamp=timestamp)
    metadata = {
        "entity_type": "human",
        "field_name": predicate.rsplit(".", 1)[-1],
        "channel_kind": channel_kind,
        "memory_role": "current_state",
        "source_surface": "researcher_bridge",
        "fact_name": fact_name,
        "normalized_value": value,
        "evidence_text": evidence_text,
        **salience_decision.metadata(),
    }
    entity_state_fact = None
    entity_state_deletion = None
    if predicate.startswith("entity."):
        if operation == "delete":
            entity_state_deletion = parse_entity_state_deletion(evidence_text)
        else:
            entity_state_fact = parse_entity_state_fact(evidence_text)
    if predicate.startswith("profile.current_") or predicate == "profile.preferred_name":
        metadata["entity_key"] = predicate
    if predicate == "profile.preferred_name" and salience_decision.why_saved == "identity_correction_supersession":
        metadata.update(
            {
                "authority": "identity_current_state",
                "supersession_kind": "identity_correction",
                "stale_prior_values": True,
            }
        )
    if predicate.startswith("entity.") and entity_state_fact is not None:
        metadata.update(
            {
                "entity_type": "named_object",
                "entity_key": entity_state_fact.entity_key,
                "entity_label": entity_state_fact.entity_label,
                "entity_attribute": entity_state_fact.attribute,
            }
        )
        if entity_state_fact.location_preposition:
            metadata["location_preposition"] = entity_state_fact.location_preposition
    if predicate.startswith("entity.") and entity_state_deletion is not None:
        metadata.update(
            {
                "entity_type": "named_object",
                "entity_key": entity_state_deletion.entity_key,
                "entity_label": entity_state_deletion.entity_label,
                "entity_attribute": entity_state_deletion.attribute,
            }
        )
    if operation != "delete" and revalidate_after_days is not None and revalidate_at:
        metadata["revalidate_after_days"] = revalidate_after_days
        metadata["revalidate_at"] = revalidate_at
    observation = {
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "operation": operation,
        "memory_role": "current_state",
        "retention_class": retention_class,
        "text": evidence_text,
        "document_time": timestamp,
        "valid_from": None if operation == "delete" else timestamp,
        "valid_to": timestamp if operation == "delete" else None,
        "deleted_at": timestamp if operation == "delete" else None,
        "metadata": metadata,
    }
    projected_observation = _named_object_profile_fact_projection(
        subject=subject,
        predicate=predicate,
        value=value,
        source_text=evidence_text,
        fact_name=fact_name,
        timestamp=timestamp,
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        operation=operation,
    )
    observations = [observation]
    if projected_observation is not None:
        observations.append(projected_observation)
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation=operation,
        human_id=human_id,
        observations=observations,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        salience_decision=salience_decision,
    )
    raw = _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": operation,
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "text": evidence_text,
            "memory_role": "current_state",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "retention_class": retention_class,
            "document_time": timestamp,
            "valid_from": None if operation == "delete" else timestamp,
            "valid_to": timestamp if operation == "delete" else None,
            "deleted_at": timestamp if operation == "delete" else None,
            "metadata": metadata,
        },
    )
    result = _normalize_write_result(raw=raw, operation=operation)
    if result.accepted_count > 0 and projected_observation is not None:
        projection_turn_id = f"{turn_id}:entity-projection" if turn_id else None
        _call_sdk_method(
            client,
            "write_observation",
            {
                "operation": projected_observation["operation"],
                "subject": projected_observation["subject"],
                "predicate": projected_observation["predicate"],
                "value": projected_observation["value"],
                "text": projected_observation["text"],
                "memory_role": projected_observation["memory_role"],
                "session_id": session_id,
                "turn_id": projection_turn_id,
                "timestamp": timestamp,
                "retention_class": projected_observation["retention_class"],
                "document_time": projected_observation["document_time"],
                "valid_from": projected_observation["valid_from"],
                "valid_to": projected_observation["valid_to"],
                "deleted_at": projected_observation["deleted_at"],
                "metadata": projected_observation["metadata"],
            },
        )
    if result.accepted_count > 0 and operation == "update":
        _write_profile_fact_history_event(
            client=client,
            human_id=human_id,
            predicate=predicate,
            value=value,
            evidence_text=evidence_text,
            fact_name=fact_name,
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
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


def _named_object_profile_fact_projection(
    *,
    subject: str,
    predicate: str,
    value: str | None,
    source_text: str,
    fact_name: str,
    timestamp: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    operation: str,
) -> dict[str, Any] | None:
    if operation == "delete" or predicate != "profile.current_low_stakes_test_fact":
        return None
    named_fact = parse_named_object_fact(str(value or ""))
    if named_fact is None:
        return None
    text = f"{named_fact.entity_label} is named {named_fact.value}."
    metadata = {
        "entity_type": "named_object",
        "entity_key": named_fact.entity_key,
        "entity_label": named_fact.entity_label,
        "entity_attribute": named_fact.attribute,
        "field_name": named_fact.attribute,
        "channel_kind": channel_kind,
        "memory_role": "current_state",
        "source_surface": "researcher_bridge",
        "source_predicate": predicate,
        "source_fact_name": fact_name,
        "source_value": value,
        "source_text": source_text,
        "session_id": session_id,
        "source_turn_id": turn_id,
    }
    return {
        "subject": subject,
        "predicate": f"entity.{named_fact.attribute}",
        "value": named_fact.value,
        "operation": "update",
        "memory_role": "current_state",
        "retention_class": "active_state",
        "text": text,
        "document_time": timestamp,
        "valid_from": timestamp,
        "valid_to": None,
        "deleted_at": None,
        "metadata": metadata,
    }


def write_telegram_event_to_memory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    value: str,
    evidence_text: str,
    event_name: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str = "telegram_event_loader",
) -> MemoryWriteResult:
    if not predicate or not str(value or "").strip():
        return MemoryWriteResult(
            status="skipped",
            operation="event",
            method="write_event",
            memory_role="event",
            accepted_count=0,
            rejected_count=0,
            skipped_count=1,
            abstained=False,
            retrieval_trace=None,
            provenance=[],
            reason="no_durable_telegram_event",
        )
    if not _memory_enabled(config_manager):
        return _disabled_write_result(operation="event", method="write_event", default_role="event")
    if not bool(config_manager.get_path("spark.memory.write_telegram_events", default=True)):
        return _disabled_write_result(
            operation="event",
            method="write_event",
            default_role="event",
            reason="telegram_event_memory_writes_disabled",
        )
    salience_decision = evaluate_memory_salience(
        predicate=predicate,
        value=value,
        evidence_text=evidence_text,
        operation="update",
    )
    if not salience_decision.should_write:
        result = MemoryWriteResult(
            status="skipped",
            operation="event",
            method="write_event",
            memory_role="event",
            accepted_count=0,
            rejected_count=1,
            skipped_count=0,
            abstained=False,
            retrieval_trace={"memory_salience": salience_decision.metadata()},
            provenance=[],
            reason=salience_decision.reason_code,
        )
        _record_memory_salience_policy_block(
            state_db=state_db,
            human_id=human_id,
            predicate=predicate,
            value=value,
            evidence_text=evidence_text,
            decision=salience_decision,
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            actor_id=actor_id,
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
    client = _load_sdk_client(config_manager)
    if client is None:
        result = MemoryWriteResult(
            status="abstained",
            operation="event",
            method="write_event",
            memory_role="event",
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
    timestamp = _now_iso()
    event_payload = {
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "operation": "event",
        "memory_role": "event",
        "retention_class": "time_bound_event",
        "text": evidence_text,
        **salience_decision.metadata(),
    }
    _record_memory_write_requested_events(
        state_db=state_db,
        human_id=human_id,
        events=[event_payload],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
        salience_decision=salience_decision,
    )
    raw = _call_sdk_method(
        client,
        "write_event",
        {
            "operation": "event",
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "text": evidence_text,
            "memory_role": "event",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "retention_class": "time_bound_event",
            "document_time": timestamp,
            "valid_from": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "event",
                "source_surface": "researcher_bridge",
                "event_name": event_name,
                "normalized_value": value,
                "value": value,
                **salience_decision.metadata(),
            },
        },
    )
    result = _normalize_write_result(
        raw=raw,
        operation="event",
        method="write_event",
        default_role="event",
    )
    if result.accepted_count > 0 and bool(
        config_manager.get_path("spark.memory.consolidate_telegram_events", default=True)
    ):
        _consolidate_telegram_event_summary_observation(
            client=client,
            predicate=predicate,
            value=value,
            evidence_text=evidence_text,
            session_id=session_id,
            turn_id=turn_id,
            channel_kind=channel_kind,
            event_name=event_name,
            subject=subject,
            salience_decision=salience_decision,
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


def _write_profile_fact_history_event(
    *,
    client: Any,
    human_id: str,
    predicate: str,
    value: str,
    evidence_text: str,
    fact_name: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
) -> None:
    timestamp = _now_iso()
    metadata = {
        "entity_type": "human",
        "channel_kind": channel_kind,
        "memory_role": "event",
        "source_surface": "profile_fact_history_capture",
        "fact_name": fact_name,
        "normalized_value": value,
        "value": value,
        "source_predicate": predicate,
    }
    entity_state_fact = parse_entity_state_fact(evidence_text) if predicate.startswith("entity.") else None
    if entity_state_fact is not None:
        metadata.update(
            {
                "entity_type": "named_object",
                "entity_key": entity_state_fact.entity_key,
                "entity_label": entity_state_fact.entity_label,
                "entity_attribute": entity_state_fact.attribute,
            }
        )
        if entity_state_fact.location_preposition:
            metadata["location_preposition"] = entity_state_fact.location_preposition
    _call_sdk_method(
        client,
        "write_event",
        {
            "operation": "event",
            "subject": _subject_for_human_id(human_id),
            "predicate": predicate,
            "value": value,
            "text": evidence_text,
            "memory_role": "event",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "retention_class": _profile_fact_retention_class(predicate),
            "document_time": timestamp,
            "valid_from": timestamp,
            "metadata": metadata,
        },
    )


def _consolidate_telegram_event_summary_observation(
    *,
    client: Any,
    predicate: str,
    value: str,
    evidence_text: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    event_name: str,
    subject: str,
    salience_decision: MemorySalienceDecision | None = None,
) -> None:
    suffix = str(predicate or "").strip().removeprefix("telegram.event.")
    if not suffix:
        return
    summary_predicate = f"telegram.summary.latest_{suffix}"
    timestamp = _now_iso()
    _call_sdk_method(
        client,
        "write_observation",
        {
            "operation": "update",
            "subject": subject,
            "predicate": summary_predicate,
            "value": value,
            "text": evidence_text,
            "memory_role": "current_state",
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "retention_class": "active_state",
            "document_time": timestamp,
            "valid_from": timestamp,
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "current_state",
                "source_surface": "telegram_event_consolidation",
                "event_name": event_name,
                "source_event_predicate": predicate,
                "entity_key": summary_predicate,
                "normalized_value": value,
                "value": value,
                "consolidated_from_event": True,
                **(salience_decision.metadata() if salience_decision is not None else {}),
            },
        },
    )


def _profile_fact_retention_class(predicate: str | None) -> str:
    normalized = str(predicate or "").strip().lower()
    if normalized.startswith("profile.current_"):
        return "active_state"
    if normalized.startswith("entity."):
        return "active_state"
    if normalized.startswith("telegram.summary.latest_"):
        return "active_state"
    return "durable_profile"


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


@contextmanager
def _exclusive_path_lock(lock_path: Path, *, timeout_seconds: float = 15.0, poll_seconds: float = 0.05):
    fd: int | None = None
    deadline = time.monotonic() + timeout_seconds
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for persistence lock: {lock_path}")
            time.sleep(poll_seconds)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _load_domain_chip_memory_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _merge_domain_entry_payloads(existing_entries: Any, new_entries: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw_entry in list(existing_entries or []) + list(new_entries or []):
        if not isinstance(raw_entry, dict):
            continue
        merged[_domain_entry_payload_key(raw_entry)] = raw_entry
    return sorted(
        merged.values(),
        key=lambda entry: (
            str(entry.get("timestamp") or ""),
            str(entry.get("observation_id") or entry.get("event_id") or ""),
        ),
    )


def _domain_entry_payload_key(entry: dict[str, Any]) -> str:
    observation_id = _optional_string(entry.get("observation_id"))
    if observation_id:
        return f"observation:{observation_id}"
    event_id = _optional_string(entry.get("event_id"))
    if event_id:
        return f"event:{event_id}"
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _merge_domain_movement_rows(existing_rows: Any, new_rows: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw_row in list(existing_rows or []) + list(new_rows or []):
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        merged[_domain_movement_row_key(row)] = row
    return sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("timestamp") or ""),
            str(row.get("id") or ""),
        ),
    )[-5000:]


def _domain_movement_row_key(row: dict[str, Any]) -> str:
    row_id = _optional_string(row.get("id"))
    if row_id:
        return row_id
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _domain_movement_counter(rows: list[dict[str, Any]], existing_counter: Any) -> int:
    counter = 0
    try:
        counter = max(counter, int(existing_counter or 0))
    except (TypeError, ValueError):
        counter = 0
    for row in rows:
        row_id = _optional_string(row.get("id")) or ""
        match = re.fullmatch(r"sdk-movement-(\d+)", row_id)
        if not match:
            continue
        counter = max(counter, int(match.group(1)))
    return counter


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
    movement_rows = _merge_domain_movement_rows(payload.get("dashboard_movement_events"), [])
    client._dashboard_movement_events = movement_rows
    client._dashboard_movement_counter = _domain_movement_counter(
        movement_rows,
        payload.get("dashboard_movement_counter"),
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


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _default_current_state_entity_key(predicate: str | None) -> str | None:
    normalized = str(predicate or "").strip()
    if normalized.startswith("profile.current_") or normalized == "profile.preferred_name":
        return normalized
    return None


def _get_current_state_with_subject_fallback(
    *,
    client: Any,
    state_db: StateDB,
    subject_candidates: tuple[str, ...],
    predicate: str | None,
    entity_key: str | None = None,
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
        if entity_key is not None:
            payload["entity_key"] = entity_key
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
    entity_key: str | None = None,
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
        payload = {
            "subject": subject,
            "predicate": predicate,
            "as_of": as_of,
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": _now_iso(),
            "metadata": {"source_surface": source_surface},
        }
        if entity_key is not None:
            payload["entity_key"] = entity_key
        raw = _call_sdk_method(client, "get_historical_state", payload)
        last_result = _normalize_read_result(raw=raw, method="get_historical_state", shadow_only=shadow_only)
        if last_result.records:
            return subject, last_result
    return last_subject, last_result


def _normalize_domain_write_result(*, result: Any, operation: str, default_role: str) -> dict[str, Any]:
    provenance = [
        _domain_record_to_dict(item)
        for item in [*list(getattr(result, "observations", []) or []), *list(getattr(result, "events", []) or [])]
    ]
    accepted = bool(getattr(result, "accepted", False))
    unsupported_reason = _optional_string(getattr(result, "unsupported_reason", None))
    normalized_operation = str(operation or ("event" if default_role == "event" else "update")).strip().lower() or (
        "event" if default_role == "event" else "update"
    )
    memory_role = provenance[0]["memory_role"] if provenance else "unknown"
    if memory_role == "unknown" and accepted:
        memory_role = default_role
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    contract_reason = memory_contract_reason(
        memory_role=memory_role,
        operation=normalized_operation,
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
                operation=normalized_operation,
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


def _domain_lookup_matching_provenance(
    *,
    provenance: list[dict[str, Any]],
    subject: str,
    predicate: str,
    value: Any,
) -> dict[str, Any] | None:
    normalized_value = str(value or "").strip()
    matches = [
        record
        for record in provenance
        if str(record.get("subject") or "").strip() == subject
        and str(record.get("predicate") or "").strip() == predicate
    ]
    if normalized_value:
        exact_matches = [
            record
            for record in matches
            if str(record.get("value") or (record.get("metadata") or {}).get("value") or "").strip() == normalized_value
        ]
        if exact_matches:
            return exact_matches[-1]
    return matches[-1] if matches else None


def _normalize_domain_lookup_result(
    *,
    result: Any,
    subject: str,
    predicate: str,
    method: str = "get_current_state",
) -> dict[str, Any]:
    provenance = [_domain_record_to_dict(item) for item in list(getattr(result, "provenance", []) or [])]
    records = []
    if bool(getattr(result, "found", False)):
        value = getattr(result, "value", None)
        matching_provenance = _domain_lookup_matching_provenance(
            provenance=provenance,
            subject=subject,
            predicate=predicate,
            value=value,
        )
        metadata = dict((matching_provenance or {}).get("metadata") or {})
        if value is not None:
            metadata.setdefault("value", value)
        records.append(
            {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "text": getattr(result, "text", None),
                "memory_role": (matching_provenance or {}).get("memory_role"),
                "session_id": (matching_provenance or {}).get("session_id"),
                "turn_ids": list((matching_provenance or {}).get("turn_ids") or []),
                "timestamp": (matching_provenance or {}).get("timestamp"),
                "observation_id": (matching_provenance or {}).get("observation_id"),
                "event_id": (matching_provenance or {}).get("event_id"),
                "retention_class": (matching_provenance or {}).get("retention_class"),
                "lifecycle": dict((matching_provenance or {}).get("lifecycle") or {}),
                "metadata": metadata,
            }
        )
    raw_memory_role = getattr(result, "memory_role", "unknown")
    memory_role = normalize_memory_role(raw_memory_role, allow_unknown=not records)
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    contract_reason = memory_contract_reason(
        memory_role=raw_memory_role,
        method=method,
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
                method=method,
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


def _normalize_domain_task_recovery_result(*, result: Any) -> dict[str, Any]:
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    records: list[dict[str, Any]] = []
    for bucket, raw_records in (
        ("active_goal", [getattr(result, "active_goal", None)]),
        ("completed_steps", list(getattr(result, "completed_steps", []) or [])),
        ("blockers", list(getattr(result, "blockers", []) or [])),
        ("next_actions", list(getattr(result, "next_actions", []) or [])),
        ("episodic_context", list(getattr(result, "episodic_context", []) or [])),
    ):
        for record in raw_records:
            if record is None:
                continue
            payload = _domain_record_to_dict(record)
            payload["task_recovery_bucket"] = bucket
            payload["authority"] = _task_recovery_label_value(
                retrieval_trace,
                record=payload,
                key="authority",
            )
            payload["source_family"] = _task_recovery_label_value(
                retrieval_trace,
                record=payload,
                key="source_family",
            )
            records.append(payload)
    memory_role = records[0]["memory_role"] if records else "unknown"
    return {
        "status": "supported" if records else str(getattr(result, "status", "") or "not_found"),
        "memory_role": memory_role,
        "records": records,
        "provenance": records,
        "retrieval_trace": retrieval_trace,
        "answer_explanation": {
            "authority_order": retrieval_trace.get("authority_order") if isinstance(retrieval_trace, dict) else None,
            "selected_counts": retrieval_trace.get("selected_counts") if isinstance(retrieval_trace, dict) else None,
            "promotes_memory": retrieval_trace.get("promotes_memory") if isinstance(retrieval_trace, dict) else False,
        },
    }


def _normalize_domain_episodic_recall_result(*, result: Any) -> dict[str, Any]:
    retrieval_trace = dict(getattr(result, "trace", {}) or {})
    records: list[dict[str, Any]] = []
    for bucket, raw_records in (
        ("current_state", list(getattr(result, "current_state", []) or [])),
        ("session_summaries", list(getattr(result, "session_summaries", []) or [])),
        ("matching_turns", list(getattr(result, "matching_turns", []) or [])),
        ("evidence", list(getattr(result, "evidence", []) or [])),
        ("events", list(getattr(result, "events", []) or [])),
    ):
        for record in raw_records:
            payload = _domain_record_to_dict(record)
            payload["episodic_recall_bucket"] = bucket
            payload["authority"] = _episodic_recall_label_value(
                retrieval_trace,
                record=payload,
                key="authority",
            )
            payload["source_family"] = _episodic_recall_label_value(
                retrieval_trace,
                record=payload,
                key="source_family",
            )
            records.append(payload)
    memory_role = records[0]["memory_role"] if records else "unknown"
    return {
        "status": "supported" if records else str(getattr(result, "status", "") or "not_found"),
        "memory_role": memory_role,
        "records": records,
        "provenance": records,
        "retrieval_trace": retrieval_trace,
        "answer_explanation": {
            "authority_order": retrieval_trace.get("authority_order") if isinstance(retrieval_trace, dict) else None,
            "selected_counts": retrieval_trace.get("selected_counts") if isinstance(retrieval_trace, dict) else None,
            "promotes_memory": retrieval_trace.get("promotes_memory") if isinstance(retrieval_trace, dict) else False,
        },
    }


def _episodic_recall_label_value(trace: dict[str, Any], *, record: dict[str, Any], key: str) -> str | None:
    labels = trace.get("source_labels")
    if not isinstance(labels, list):
        return None
    observation_id = str(record.get("observation_id") or "").strip()
    event_id = str(record.get("event_id") or "").strip()
    bucket = str(record.get("episodic_recall_bucket") or "").strip()
    session_id = str(record.get("session_id") or "").strip()
    turn_ids = {str(item).strip() for item in (record.get("turn_ids") or []) if str(item).strip()}
    for label in labels:
        if not isinstance(label, dict):
            continue
        if observation_id and str(label.get("observation_id") or "").strip() == observation_id:
            return _optional_string(label.get(key))
        if event_id and str(label.get("event_id") or "").strip() == event_id:
            return _optional_string(label.get(key))
        if bucket and str(label.get("bucket") or "").strip() != bucket:
            continue
        if session_id and str(label.get("session_id") or "").strip() == session_id:
            label_turn_ids = {str(item).strip() for item in (label.get("turn_ids") or []) if str(item).strip()}
            if not turn_ids or turn_ids.intersection(label_turn_ids):
                return _optional_string(label.get(key))
    return None


def _task_recovery_label_value(trace: dict[str, Any], *, record: dict[str, Any], key: str) -> str | None:
    labels = trace.get("source_labels")
    if not isinstance(labels, list):
        return None
    observation_id = str(record.get("observation_id") or "").strip()
    event_id = str(record.get("event_id") or "").strip()
    bucket = str(record.get("task_recovery_bucket") or "").strip()
    for label in labels:
        if not isinstance(label, dict):
            continue
        if observation_id and str(label.get("observation_id") or "").strip() == observation_id:
            return _optional_string(label.get(key))
        if event_id and str(label.get("event_id") or "").strip() == event_id:
            return _optional_string(label.get(key))
        if bucket and str(label.get("bucket") or "").strip() == bucket and not observation_id and not event_id:
            return _optional_string(label.get(key))
    return None


def _domain_record_to_dict(record: Any) -> dict[str, Any]:
    metadata = dict(getattr(record, "metadata", {}) or {})
    return {
        "memory_role": effective_memory_role(
            getattr(record, "memory_role", "unknown"),
            allow_unknown=True,
            metadata=metadata,
        ),
        "subject": _optional_string(getattr(record, "subject", None)),
        "predicate": _optional_string(getattr(record, "predicate", None)),
        "text": _optional_string(getattr(record, "text", None)),
        "session_id": _optional_string(getattr(record, "session_id", None)),
        "turn_ids": list(getattr(record, "turn_ids", []) or []),
        "timestamp": _optional_string(getattr(record, "timestamp", None)),
        "observation_id": _optional_string(getattr(record, "observation_id", None)),
        "event_id": _optional_string(getattr(record, "event_id", None)),
        "retention_class": _optional_string(getattr(record, "retention_class", None)),
        "lifecycle": dict(getattr(record, "lifecycle", {}) or {}),
        "metadata": metadata,
        "value": metadata.get("value"),
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


def _normalize_memory_maintenance_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if is_dataclass(raw):
        return asdict(raw)
    payload: dict[str, Any] = {}
    for field in (
        "manual_observations_before",
        "manual_observations_after",
        "current_state_snapshot_count",
        "active_deletion_count",
        "manual_events_count",
        "active_state_still_current_count",
        "active_state_stale_preserved_count",
        "active_state_superseded_count",
        "active_state_archived_count",
        "active_state_resurrected_count",
        "audit_samples",
        "trace",
    ):
        if hasattr(raw, field):
            payload[field] = getattr(raw, field)
    return payload


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


def _memory_kernel_abstained_read_result(*, method: str, reason: str, shadow_only: bool) -> MemoryReadResult:
    return MemoryReadResult(
        status="abstained",
        method=method,
        memory_role="current_state" if method in {"get_current_state", "get_historical_state"} else "unknown",
        records=[],
        provenance=[],
        retrieval_trace=None,
        answer_explanation=None,
        abstained=True,
        reason=reason,
        shadow_only=shadow_only,
    )


def _memory_kernel_result(
    *,
    sdk_module: str,
    runtime: dict[str, Any],
    query: str,
    subject: str | None,
    predicate: str | None,
    read_result: MemoryReadResult,
) -> MemoryKernelReadResult:
    source_class = _memory_kernel_source_class(read_result)
    answer = _memory_kernel_answer(read_result)
    ignored_stale_records = _memory_kernel_stale_records(read_result)
    enriched_read_result = _memory_kernel_enrich_read_result(
        read_result=read_result,
        source_class=source_class,
        answer=answer,
        ignored_stale_records=ignored_stale_records,
    )
    return MemoryKernelReadResult(
        sdk_module=sdk_module,
        query=query,
        subject=subject,
        predicate=predicate,
        read_method=enriched_read_result.method,
        source_class=source_class,
        answer=answer,
        records=enriched_read_result.records,
        provenance=enriched_read_result.provenance,
        runtime=runtime,
        abstained=enriched_read_result.abstained,
        reason=enriched_read_result.reason,
        ignored_stale_records=ignored_stale_records,
        read_result=enriched_read_result,
    )


def _memory_kernel_enrich_read_result(
    *,
    read_result: MemoryReadResult,
    source_class: str,
    answer: str | None,
    ignored_stale_records: list[dict[str, Any]],
) -> MemoryReadResult:
    trace = dict(read_result.retrieval_trace or {})
    trace["memory_kernel"] = {
        "read_method": read_result.method,
        "source_class": source_class,
        "answer_present": bool(answer),
        "ignored_stale_record_count": len(ignored_stale_records),
    }
    return MemoryReadResult(
        status=read_result.status,
        method=read_result.method,
        memory_role=read_result.memory_role,
        records=read_result.records,
        provenance=read_result.provenance,
        retrieval_trace=trace,
        answer_explanation=read_result.answer_explanation,
        abstained=read_result.abstained,
        reason=read_result.reason,
        shadow_only=read_result.shadow_only,
    )


def _memory_kernel_source_class(read_result: MemoryReadResult) -> str:
    if read_result.abstained:
        return "abstained"
    if read_result.method == "get_current_state":
        return "current_state"
    if read_result.method == "get_historical_state":
        return "historical_state"
    if read_result.method == "retrieve_events":
        return "event"
    if read_result.method == "retrieve_evidence":
        if read_result.memory_role in {"structured_evidence", "belief_candidate", "raw_episode"}:
            return read_result.memory_role
        return "evidence"
    if read_result.method == "explain_answer":
        return read_result.memory_role if read_result.memory_role != "unknown" else "answer_explanation"
    return read_result.memory_role if read_result.memory_role != "unknown" else "memory"


def _memory_kernel_answer(read_result: MemoryReadResult) -> str | None:
    explanation = read_result.answer_explanation or {}
    for key in ("answer", "explanation"):
        value = _optional_string(explanation.get(key))
        if value:
            return value
    if not read_result.records:
        return None
    first = read_result.records[0]
    for key in ("answer", "value", "text", "summary"):
        value = _optional_string(first.get(key))
        if value:
            return value
    return None


def _memory_kernel_stale_records(read_result: MemoryReadResult) -> list[dict[str, Any]]:
    candidates = [*read_result.records, *read_result.provenance]
    stale: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in candidates:
        if not _memory_kernel_record_is_stale(record):
            continue
        key = json.dumps(record, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        stale.append(record)
    return stale


def _memory_kernel_record_is_stale(record: dict[str, Any]) -> bool:
    lifecycle = record.get("lifecycle")
    metadata = record.get("metadata")
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    if not isinstance(metadata, dict):
        metadata = {}
    text_fields = " ".join(
        str(value or "").strip().lower()
        for value in (
            lifecycle.get("status"),
            lifecycle.get("state"),
            metadata.get("status"),
            metadata.get("state"),
            metadata.get("current_state_status"),
            record.get("status"),
            record.get("state"),
        )
    )
    if any(marker in text_fields for marker in ("archived", "deleted", "superseded", "historical", "stale")):
        return True
    if record.get("deleted_at") or record.get("archived_at") or record.get("invalidated_at"):
        return True
    if lifecycle.get("deleted_at") or lifecycle.get("archived_at") or lifecycle.get("invalidated_at"):
        return True
    if metadata.get("deleted_at") or metadata.get("archived_at") or metadata.get("invalidated_at"):
        return True
    if record.get("is_current") is False or metadata.get("is_current") is False:
        return True
    return False


def _build_hybrid_memory_context_packet(
    *,
    query: str,
    candidates: list[HybridMemoryCandidate],
    lane_summaries: list[dict[str, Any]],
    max_chars: int,
) -> HybridMemoryContextPacket:
    normalized_budget = max(600, int(max_chars or 3600))
    project_knowledge_first = _hybrid_memory_query_prefers_project_knowledge(query)
    sections_by_name: dict[str, dict[str, Any]] = {}
    used_chars = 0
    dropped_count = 0
    source_mix: dict[str, int] = {}
    selected_candidates = [candidate for candidate in candidates if candidate.selected]
    for candidate in selected_candidates:
        section_name = _hybrid_memory_context_section(candidate)
        section = sections_by_name.setdefault(
            section_name,
            {
                "section": section_name,
                "authority": _hybrid_memory_context_authority(candidate),
                "items": [],
                "used_chars": 0,
            },
        )
        remaining_total = normalized_budget - used_chars
        if remaining_total <= 0:
            dropped_count += 1
            continue
        section_budget = _hybrid_memory_context_section_budget(section_name, normalized_budget)
        remaining_section = section_budget - int(section["used_chars"])
        allowance = max(0, min(remaining_total, remaining_section))
        if allowance < 80 and section["items"]:
            dropped_count += 1
            continue
        item = _hybrid_memory_context_item(candidate, max_chars=max(80, allowance))
        item_chars = int(item["char_count"])
        if item_chars > remaining_total:
            dropped_count += 1
            continue
        section["items"].append(item)
        section["used_chars"] = int(section["used_chars"]) + item_chars
        used_chars += item_chars
        source_mix[candidate.source_class] = source_mix.get(candidate.source_class, 0) + 1

    section_order = {
        "active_current_state": 0,
        "entity_state": 1,
        "historical_state": 2,
        "pending_tasks": 3,
        "procedural_lessons": 4,
        "compiled_project_knowledge": 5 if project_knowledge_first else 9,
        "recent_conversation": 6,
        "diagnostics": 7,
        "relevant_events": 8,
        "relevant_evidence": 10 if project_knowledge_first else 8,
        "graph_sidecar_hits": 11,
        "supporting_context": 12,
    }
    sections = sorted(
        (section for section in sections_by_name.values() if section["items"]),
        key=lambda section: section_order.get(str(section["section"]), 99),
    )
    promotion_gates = _hybrid_memory_context_promotion_gates(
        candidates=candidates,
        sections=sections,
        source_mix=source_mix,
        lane_summaries=lane_summaries,
    )
    conflict_notes = [
        {
            "lane": candidate.lane,
            "source_class": candidate.source_class,
            "reason_discarded": candidate.reason_discarded,
            "predicate": candidate.record.get("predicate"),
            "summary": _clip_text(_hybrid_memory_record_text(candidate.record), max_chars=180),
        }
        for candidate in candidates
        if candidate.reason_discarded in {"stale_or_superseded", "duplicate_lower_rank"}
    ][:6]
    return HybridMemoryContextPacket(
        query=query,
        max_chars=normalized_budget,
        used_chars=used_chars,
        sections=sections,
        source_mix=source_mix,
        dropped_count=dropped_count,
        conflict_notes=conflict_notes,
        trace={
            "operation": "build_hybrid_memory_context_packet",
            "lane_count": len(lane_summaries),
            "selected_candidate_count": len(selected_candidates),
            "section_count": len(sections),
            "score_adaptive_truncation": True,
            "project_knowledge_first": project_knowledge_first,
            "promotion_gates": promotion_gates,
        },
    )


_AUTHORITY_MEMORY_SOURCE_CLASSES: set[str] = {"current_state", "historical_state"}
_RECENT_CONVERSATION_NOISE_MARKERS: tuple[str, ...] = (
    "diagnostic",
    "maintenance",
    "config",
    "policy",
    "doctor",
)


def _hybrid_memory_context_promotion_gates(
    *,
    candidates: list[HybridMemoryCandidate],
    sections: list[dict[str, Any]],
    source_mix: dict[str, int],
    lane_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    packet_candidate_keys = {
        str(item.get("candidate_key") or "")
        for section in sections
        for item in list(section.get("items") or [])
        if isinstance(item, dict)
    }
    packet_candidates = [
        candidate for candidate in candidates if candidate.selected and _hybrid_memory_candidate_key(candidate) in packet_candidate_keys
    ]
    total_packet_items = len(packet_candidates)
    authority_count = sum(1 for candidate in packet_candidates if candidate.source_class in _AUTHORITY_MEMORY_SOURCE_CLASSES)
    supporting_count = max(0, total_packet_items - authority_count)
    stale_selected_count = sum(1 for candidate in packet_candidates if _memory_kernel_record_is_stale(candidate.record))
    stale_discarded_count = sum(1 for candidate in candidates if candidate.reason_discarded == "stale_or_superseded")
    recent_candidates = [candidate for candidate in packet_candidates if candidate.source_class == "recent_conversation"]
    recent_lane = next((lane for lane in lane_summaries if lane.get("lane") == "recent_conversation"), {})
    recent_noise_count = sum(1 for candidate in recent_candidates if _recent_conversation_candidate_is_noise(candidate))
    dominant_source = max(source_mix, key=source_mix.get) if source_mix else None
    dominant_count = int(source_mix.get(dominant_source, 0)) if dominant_source else 0
    dominant_fraction = round(dominant_count / max(1, sum(source_mix.values())), 3) if source_mix else 0.0
    clean_authority_anchor = authority_count > 0 and stale_selected_count == 0 and recent_noise_count == 0

    gates = {
        "source_swamp_resistance": _promotion_gate(
            status=(
                "pass"
                if authority_count > 0 or supporting_count <= 2
                else "warn"
            ),
            reason=(
                "authority_present_or_small_supporting_packet"
                if authority_count > 0 or supporting_count <= 2
                else "supporting_sources_without_authority"
            ),
            evidence={
                "authority_count": authority_count,
                "supporting_count": supporting_count,
                "packet_candidate_count": total_packet_items,
            },
        ),
        "stale_current_conflict": _promotion_gate(
            status="pass" if stale_selected_count == 0 else "fail",
            reason="stale_candidates_discarded" if stale_selected_count == 0 else "stale_candidate_survived_packet",
            evidence={
                "stale_selected_count": stale_selected_count,
                "stale_discarded_count": stale_discarded_count,
                "current_state_selected": any(candidate.source_class == "current_state" for candidate in packet_candidates),
            },
        ),
        "recent_conversation_noise": _promotion_gate(
            status=(
                "pass"
                if not recent_candidates or (recent_noise_count == 0 and bool(recent_lane.get("query_overlap_required")))
                else "warn"
            ),
            reason=(
                "no_recent_conversation_selected"
                if not recent_candidates
                else (
                    "recent_conversation_query_gated_and_clean"
                    if recent_noise_count == 0 and bool(recent_lane.get("query_overlap_required"))
                    else "recent_conversation_needs_stronger_filtering"
                )
            ),
            evidence={
                "recent_selected_count": len(recent_candidates),
                "recent_noise_count": recent_noise_count,
                "query_overlap_required": bool(recent_lane.get("query_overlap_required")),
            },
        ),
        "source_mix_stability": _promotion_gate(
            status=(
                "pass"
                if total_packet_items <= 2
                or dominant_fraction <= 0.75
                or dominant_source in _AUTHORITY_MEMORY_SOURCE_CLASSES
                or clean_authority_anchor
                else "warn"
            ),
            reason=(
                "small_or_balanced_packet"
                if total_packet_items <= 2 or dominant_fraction <= 0.75
                else (
                    "authority_source_dominates"
                    if dominant_source in _AUTHORITY_MEMORY_SOURCE_CLASSES
                    else (
                        "authority_anchor_with_clean_supporting_mix"
                        if clean_authority_anchor
                        else "single_supporting_source_dominates_packet"
                    )
                )
            ),
            evidence={
                "source_mix": dict(source_mix),
                "dominant_source": dominant_source,
                "dominant_fraction": dominant_fraction,
                "authority_count": authority_count,
            },
        ),
    }
    statuses = {str(gate.get("status") or "warn") for gate in gates.values()}
    overall_status = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")
    return {
        "status": overall_status,
        "mode": "trace_only",
        "gates": gates,
        "rollback_condition": "treat warn/fail gates as acceptance-test blockers before promoting the memory layer",
    }


def _promotion_gate(*, status: str, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "evidence": evidence,
    }


def _recent_conversation_candidate_is_noise(candidate: HybridMemoryCandidate) -> bool:
    metadata = candidate.record.get("metadata") if isinstance(candidate.record.get("metadata"), dict) else {}
    event_type = str(metadata.get("event_type") or "").casefold()
    component = str(metadata.get("component") or "").casefold()
    return any(marker in event_type or marker in component for marker in _RECENT_CONVERSATION_NOISE_MARKERS)


def _build_recent_conversation_lane(
    *,
    state_db: StateDB,
    query: str,
    subject: str | None,
    session_id: str | None,
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    human_id = _human_id_from_subject(subject or "")
    row_limit = max(20, int(limit or 5) * 12)
    sql = """
        SELECT event_id, event_type, component, session_id, human_id, summary, facts_json, created_at
        FROM builder_events
    """
    conditions: list[str] = []
    params: list[Any] = []
    if human_id:
        conditions.append("human_id = ?")
        params.append(human_id)
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    if conditions:
        sql += " WHERE " + " OR ".join(f"({condition})" for condition in conditions)
    sql += " ORDER BY created_at DESC, event_id DESC LIMIT ?"
    params.append(row_limit)

    records: list[dict[str, Any]] = []
    scanned = 0
    query_tokens = _hybrid_memory_query_tokens(query)
    try:
        with state_db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:
        return (
            {
                "lane": "recent_conversation",
                "read_method": "retrieve_recent_conversation",
                "source_class": "recent_conversation",
                "status": "error",
                "abstained": True,
                "record_count": 0,
                "reason": "recent_conversation_query_error",
                "error": exc.__class__.__name__,
            },
            [],
        )
    for row in rows:
        scanned += 1
        facts = _json_object(row["facts_json"])
        if not _is_recent_conversation_row(row=row, facts=facts):
            continue
        text = _recent_conversation_text(row=row, facts=facts)
        if not text:
            continue
        if query_tokens and not _text_overlaps_query(text, query_tokens):
            continue
        record = _recent_conversation_record(row=row, facts=facts, text=text)
        records.append(record)
        if len(records) >= max(1, int(limit or 1)):
            break
    return (
        {
            "lane": "recent_conversation",
            "read_method": "retrieve_recent_conversation",
            "source_class": "recent_conversation",
            "status": "supported" if records else "not_found",
            "abstained": not bool(records),
            "record_count": len(records),
            "reason": None if records else "no_matching_recent_conversation",
            "scanned_count": scanned,
            "query_overlap_required": bool(query_tokens),
        },
        records,
    )


def _build_pending_task_lane(
    *,
    state_db: StateDB,
    query: str,
    subject: str | None,
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    human_id = subject if str(subject or "").startswith("human:") else None
    tasks = latest_pending_tasks(state_db, human_id=human_id, open_only=True, limit=max(1, limit))
    if not tasks and human_id:
        tasks = latest_pending_tasks(state_db, open_only=True, limit=max(1, limit))
    records = [_pending_task_to_hybrid_record(task.to_dict()) for task in tasks]
    return (
        {
            "lane": "pending_tasks",
            "read_method": "pending_task_ledger",
            "source_class": "pending_task",
            "status": "supported" if records else "abstained",
            "abstained": not records,
            "record_count": len(records),
            "reason": "open_pending_tasks" if records else "no_open_pending_tasks",
            "query_overlap_required": _workflow_recovery_query_hint(query),
        },
        records,
    )


def _build_procedural_lesson_lane(
    *,
    state_db: StateDB,
    query: str,
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lessons = latest_procedural_lessons(state_db, active_only=True, limit=max(1, limit))
    records = [_procedural_lesson_to_hybrid_record(lesson.to_dict()) for lesson in lessons]
    return (
        {
            "lane": "procedural_lessons",
            "read_method": "procedural_lesson_ledger",
            "source_class": "procedural_lesson",
            "status": "supported" if records else "abstained",
            "abstained": not records,
            "record_count": len(records),
            "reason": "active_procedural_lessons" if records else "no_active_procedural_lessons",
            "query_overlap_required": _workflow_recovery_query_hint(query),
        },
        records,
    )


def _extend_hybrid_ranked_entries(
    ranked_entries: list[dict[str, Any]],
    *,
    lane: str,
    source_class: str,
    records: list[dict[str, Any]],
    query: str,
    predicate: str | None,
    entity_key: str | None,
) -> None:
    for index, record in enumerate(records):
        score, score_reasons = _score_hybrid_memory_record(
            lane=lane,
            source_class=source_class,
            record=record,
            query=query,
            predicate=predicate,
            entity_key=entity_key,
        )
        ranked_entries.append(
            {
                "lane": lane,
                "source_class": source_class,
                "score": score,
                "score_reasons": score_reasons,
                "record": record,
                "record_key": _hybrid_memory_record_key(record),
                "rank_tiebreak": index,
            }
        )


def _pending_task_to_hybrid_record(task: dict[str, Any]) -> dict[str, Any]:
    task_key = str(task.get("task_key") or "")
    original_request = str(task.get("original_request") or "")
    next_retry = str(task.get("next_retry_step") or "")
    timeout_point = str(task.get("timeout_point") or "")
    last_evidence = str(task.get("last_evidence") or "")
    target = " ".join(str(task.get(key) or "") for key in ("target_repo", "target_component")).strip()
    text_parts = [
        f"Pending task {task_key}: {original_request}",
        f"status={task.get('status') or 'unknown'}",
        f"target={target}" if target else "",
        f"interrupted_at={timeout_point}" if timeout_point else "",
        f"last_evidence={last_evidence}" if last_evidence else "",
        f"next_retry={next_retry}" if next_retry else "",
    ]
    text = " ".join(part for part in text_parts if part)
    return {
        "observation_id": str(task.get("pending_task_id") or task_key),
        "subject": task.get("human_id"),
        "predicate": "workflow.pending_task",
        "value": original_request,
        "summary": text,
        "text": text,
        "timestamp": task.get("updated_at"),
        "memory_role": "pending_task",
        "metadata": {
            "memory_role": "pending_task",
            "source_surface": "pending_task_ledger",
            "source_ref": task_key,
            "task_key": task_key,
            "status": task.get("status"),
            "target_repo": task.get("target_repo"),
            "target_component": task.get("target_component"),
            "mission_id": task.get("mission_id"),
            "timeout_point": timeout_point,
            "last_evidence": last_evidence,
            "next_retry_step": next_retry,
        },
        "provenance": [{"source": "pending_task_ledger", "task_key": task_key}],
    }


def _procedural_lesson_to_hybrid_record(lesson: dict[str, Any]) -> dict[str, Any]:
    lesson_key = str(lesson.get("lesson_key") or "")
    text_parts = [
        f"Procedural lesson {lesson.get('lesson_kind') or 'unknown'}:",
        f"trigger={lesson.get('trigger_pattern') or ''}",
        f"corrective_action={lesson.get('corrective_action') or ''}",
        f"failure={lesson.get('failure_summary') or ''}",
        f"applies_to={lesson.get('applies_to_component') or ''}",
    ]
    text = " ".join(part for part in text_parts if part and not part.endswith("="))
    return {
        "observation_id": str(lesson.get("lesson_id") or lesson_key),
        "subject": lesson.get("target_repo"),
        "predicate": f"procedural.{lesson.get('lesson_kind') or 'lesson'}",
        "value": lesson.get("corrective_action"),
        "summary": text,
        "text": text,
        "timestamp": lesson.get("updated_at"),
        "memory_role": "procedural_lesson",
        "metadata": {
            "memory_role": "procedural_lesson",
            "source_surface": "procedural_lesson_ledger",
            "source_ref": lesson_key,
            "lesson_key": lesson_key,
            "lesson_kind": lesson.get("lesson_kind"),
            "applies_to_component": lesson.get("applies_to_component"),
            "target_repo": lesson.get("target_repo"),
            "target_component": lesson.get("target_component"),
            "confidence": lesson.get("confidence"),
            "occurrence_count": lesson.get("occurrence_count"),
        },
        "provenance": [{"source": "procedural_lesson_ledger", "lesson_key": lesson_key}],
    }


def _workflow_recovery_query_hint(query: str) -> bool:
    tokens = _hybrid_memory_query_tokens(query)
    return bool(tokens & {"resume", "continue", "timeout", "timed", "interrupted", "stuck", "next", "retry"})


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        payload = json.loads(str(raw))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _is_recent_conversation_row(*, row: Any, facts: dict[str, Any]) -> bool:
    event_type = str(row["event_type"] or "").casefold()
    component = str(row["component"] or "").casefold()
    if event_type.startswith("memory_"):
        return False
    if any(marker in event_type for marker in ("diagnostic", "maintenance", "config", "policy", "doctor")):
        return False
    if any(marker in component for marker in ("diagnostic", "maintenance", "config", "policy", "doctor")):
        return False
    if any(_optional_string(facts.get(key)) for key in _RECENT_CONVERSATION_TEXT_KEYS):
        return True
    if _optional_string(facts.get("role")) or _optional_string(facts.get("conversation_role")):
        return True
    return any(marker in event_type for marker in ("telegram", "message", "conversation", "user_turn", "assistant_turn"))


_RECENT_CONVERSATION_TEXT_KEYS: tuple[str, ...] = (
    "message_text",
    "text",
    "content",
    "user_text",
    "assistant_text",
    "response_text",
    "input_text",
    "output_text",
    "prompt",
)


def _recent_conversation_text(*, row: Any, facts: dict[str, Any]) -> str:
    for key in _RECENT_CONVERSATION_TEXT_KEYS:
        value = _optional_string(facts.get(key))
        if value:
            return value
    return str(row["summary"] or "").strip()


def _recent_conversation_role(*, row: Any, facts: dict[str, Any]) -> str:
    role = str(facts.get("role") or facts.get("conversation_role") or "").strip().lower()
    if role in {"user", "assistant", "system"}:
        return role
    event_type = str(row["event_type"] or "").casefold()
    if "assistant" in event_type or "reply" in event_type or "response" in event_type:
        return "assistant"
    return "user"


def _text_overlaps_query(text: str, query_tokens: set[str]) -> bool:
    haystack = str(text or "").casefold()
    return any(token in haystack for token in query_tokens)


def _recent_conversation_record(*, row: Any, facts: dict[str, Any], text: str) -> dict[str, Any]:
    event_id = str(row["event_id"] or "").strip()
    role = _recent_conversation_role(row=row, facts=facts)
    return {
        "subject": _optional_string(f"human:{row['human_id']}") if row["human_id"] else None,
        "predicate": "conversation.recent_turn",
        "value": text,
        "text": text,
        "memory_role": "raw_episode",
        "source_class": "recent_conversation",
        "event_id": event_id,
        "session_id": _optional_string(row["session_id"]),
        "timestamp": _optional_string(row["created_at"]),
        "metadata": {
            "source_surface": "recent_conversation",
            "event_type": row["event_type"],
            "component": row["component"],
            "role": role,
            "authority": "supporting_not_authoritative",
            "source_event_id": event_id,
        },
        "provenance": [
            {
                "source": "builder_events",
                "event_id": event_id,
                "created_at": row["created_at"],
            }
        ],
    }


def _hybrid_memory_context_section(candidate: HybridMemoryCandidate) -> str:
    record = candidate.record
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    predicate = str(record.get("predicate") or "").strip()
    memory_role = str(record.get("memory_role") or metadata.get("memory_role") or "").strip()
    source_surface = str(metadata.get("source_surface") or "").strip()
    if predicate.startswith("entity.") or memory_role == "entity_state":
        return "entity_state"
    if predicate.startswith("diagnostics.") or source_surface == "diagnostics":
        return "diagnostics"
    if memory_role in {"raw_episode", "episodic"} or candidate.source_class == "recent_conversation":
        return "recent_conversation"
    if candidate.lane == "current_state":
        return "active_current_state"
    if candidate.lane == "historical_state":
        return "historical_state"
    if candidate.lane == "events":
        return "relevant_events"
    if candidate.lane == "evidence":
        return "relevant_evidence"
    if candidate.lane == "wiki_packets":
        return "compiled_project_knowledge"
    if candidate.lane == "typed_temporal_graph":
        return "graph_sidecar_hits"
    if candidate.lane == "pending_tasks":
        return "pending_tasks"
    if candidate.lane == "procedural_lessons":
        return "procedural_lessons"
    return "supporting_context"


def _hybrid_memory_context_authority(candidate: HybridMemoryCandidate) -> str:
    if candidate.source_class in {"current_state", "historical_state"}:
        return "authority"
    if candidate.source_class in {"obsidian_llm_wiki_packets", "graphiti_temporal_graph"}:
        return "supporting_not_authoritative"
    if candidate.source_class == "pending_task":
        return "workflow_recovery"
    if candidate.source_class == "procedural_lesson":
        return "procedural_advisory"
    if _memory_kernel_record_is_stale(candidate.record):
        return "advisory_stale"
    return "supporting"


def _hybrid_memory_context_section_budget(section_name: str, max_chars: int) -> int:
    fractions = {
        "active_current_state": 0.34,
        "entity_state": 0.28,
        "historical_state": 0.24,
        "recent_conversation": 0.16,
        "diagnostics": 0.14,
        "relevant_events": 0.20,
        "relevant_evidence": 0.22,
        "compiled_project_knowledge": 0.18,
        "graph_sidecar_hits": 0.16,
        "pending_tasks": 0.22,
        "procedural_lessons": 0.20,
        "supporting_context": 0.12,
    }
    return max(240, int(max_chars * fractions.get(section_name, 0.12)))


def _hybrid_memory_context_item(candidate: HybridMemoryCandidate, *, max_chars: int) -> dict[str, Any]:
    record = candidate.record
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    text = _hybrid_memory_record_text(record) or str(record.get("value") or record.get("summary") or "").strip()
    clipped = _clip_text(text, max_chars=max_chars)
    item = {
        "candidate_key": _hybrid_memory_candidate_key(candidate),
        "lane": candidate.lane,
        "source_class": candidate.source_class,
        "score": candidate.score,
        "authority": _hybrid_memory_context_authority(candidate),
        "predicate": record.get("predicate"),
        "value": record.get("value"),
        "text": clipped,
        "source_path": metadata.get("source_path"),
        "entity_key": metadata.get("entity_key"),
        "source_ref": metadata.get("source_ref"),
        "reason_selected": candidate.reason_selected,
        "char_count": len(clipped),
    }
    if candidate.source_class == "obsidian_llm_wiki_packets":
        for field in WIKI_PACKET_METADATA_FIELDS:
            value = metadata.get(field)
            if value is not None and str(value).strip():
                item[field] = str(value).strip()
    return item


def _hybrid_memory_candidate_key(candidate: HybridMemoryCandidate) -> str:
    return f"{candidate.lane}:{candidate.source_class}:{_hybrid_memory_record_key(candidate.record)}"


def _clip_text(text: str, *, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)].rstrip() + "..."


def _build_graph_sidecar_shadow_lane(
    *,
    config_manager: ConfigManager,
    sdk_module: str,
    query: str,
    subject: str | None,
    predicate: str | None,
    entity_key: str | None,
    as_of: str | None,
    limit: int,
    local_records: list[dict[str, Any]],
) -> dict[str, Any]:
    enabled = bool(config_manager.get_path("spark.memory.sidecars.graphiti.enabled", default=False))
    lane = {
        "lane": "typed_temporal_graph",
        "read_method": "graph_sidecar_retrieve",
        "source_class": "graphiti_temporal_graph",
        "status": "unavailable",
        "abstained": True,
        "record_count": 0,
        "reason": "graph_sidecar_contract_unavailable",
        "shadow_only": True,
        "authority": "supporting_not_authoritative",
        "episode_export_count": 0,
        "sidecar_hit_count": 0,
    }
    try:
        module = _import_memory_sdk_module(sdk_module or DEFAULT_SDK_MODULE)
        build_sidecars = getattr(module, "build_default_memory_sidecars")
        request_cls = getattr(module, "MemorySidecarRetrievalRequest")
        to_episode = getattr(module, "memory_record_to_sidecar_episode")
    except Exception as exc:
        lane["error"] = exc.__class__.__name__
        return lane

    try:
        graphiti_backend = _optional_string(
            config_manager.get_path("spark.memory.sidecars.graphiti.backend", default=None)
        )
        graphiti_db_path = _graphiti_sidecar_db_path(config_manager)
        graphiti_group_id = str(
            config_manager.get_path("spark.memory.sidecars.graphiti.group_id", default="spark-memory")
            or "spark-memory"
        )
        graphiti_auto_build_indices = bool(
            config_manager.get_path("spark.memory.sidecars.graphiti.auto_build_indices", default=True)
        )
        graphiti_call_timeout_seconds = _coerce_float(
            config_manager.get_path("spark.memory.sidecars.graphiti.call_timeout_seconds", default=6.0),
            default=6.0,
        )
        graphiti_llm = _graphiti_sidecar_llm_settings(config_manager)
        try:
            sidecars = build_sidecars(
                enable_graphiti=enabled,
                enable_mem0_shadow=False,
                graphiti_backend=graphiti_backend,
                graphiti_db_path=graphiti_db_path,
                graphiti_group_id=graphiti_group_id,
                graphiti_auto_build_indices=graphiti_auto_build_indices,
                graphiti_call_timeout_seconds=graphiti_call_timeout_seconds,
                graphiti_llm_api_key_env=graphiti_llm.get("api_key_env"),
                graphiti_llm_api_key=graphiti_llm.get("api_key"),
                graphiti_llm_base_url=graphiti_llm.get("base_url"),
                graphiti_llm_model=graphiti_llm.get("model"),
                graphiti_llm_small_model=graphiti_llm.get("small_model"),
            )
        except TypeError:
            sidecars = build_sidecars(enable_graphiti=enabled, enable_mem0_shadow=False)
            graphiti_backend = None
            graphiti_db_path = None
            graphiti_llm = {}
        graph_sidecar = sidecars["graphiti_temporal_graph"]
        exportable_records = _prioritize_graph_sidecar_export_records(
            [record for record in local_records if _graph_sidecar_exportable_record(record)],
            entity_key=entity_key,
        )
        episodes = [
            to_episode(_sidecar_record_namespace(record), source_class=_hybrid_memory_dict_source_class(record))
            for record in exportable_records
        ]
        upsert_traces = []
        for episode in episodes[:limit]:
            upsert_trace = graph_sidecar.upsert_episode(episode).trace
            upsert_traces.append(upsert_trace)
            if upsert_trace.get("error") or upsert_trace.get("backend_status"):
                break
        upsert_error_trace = next(
            (
                trace
                for trace in upsert_traces
                if isinstance(trace, dict) and (trace.get("error") or trace.get("backend_status"))
            ),
            None,
        )
        if enabled and upsert_error_trace is not None and bool(upsert_error_trace.get("backend_configured")):
            health = graph_sidecar.health()
            lane.update(
                {
                    "status": "error",
                    "abstained": True,
                    "record_count": 0,
                    "reason": "graph_sidecar_shadow_upsert_error",
                    "mode": getattr(graph_sidecar, "mode", "disabled"),
                    "enabled": enabled,
                    "backend": graphiti_backend or "not_configured",
                    "backend_configured": bool(upsert_error_trace.get("backend_configured")),
                    "auto_build_indices": graphiti_auto_build_indices,
                    "call_timeout_seconds": graphiti_call_timeout_seconds,
                    "llm_provider": _graphiti_llm_trace(graphiti_llm),
                    "episode_export_count": len(episodes),
                    "raw_local_record_count": len(local_records),
                    "upsert_preview_count": len(upsert_traces),
                    "sidecar_hit_count": 0,
                    "retrieval_trace": {
                        "status": "skipped",
                        "reason": "upsert_error",
                        "error": upsert_error_trace.get("error") or upsert_error_trace.get("backend_status"),
                    },
                    "health": asdict(health) if is_dataclass(health) else health,
                    "upsert_trace_samples": upsert_traces[:2],
                    "hit_records": [],
                }
            )
            return lane
        request = request_cls(
            query=query,
            subject=subject,
            scope=predicate or "hybrid_memory_retrieve",
            time_window={"as_of": as_of} if as_of else {},
            entity_keys=[entity_key] if entity_key else [],
            top_k=limit,
        )
        retrieval = graph_sidecar.retrieve(request)
        health = graph_sidecar.health()
        sidecar_hits = list(getattr(retrieval, "hits", []) or [])
        hit_records = [_graph_sidecar_hit_record(hit) for hit in sidecar_hits]
        retrieval_trace = getattr(retrieval, "trace", {}) or {}
        backend_configured = bool(retrieval_trace.get("backend_configured"))
        if not enabled:
            reason = "graph_sidecar_shadow_disabled"
        elif backend_configured and sidecar_hits:
            reason = "graph_sidecar_shadow_live_hits"
        elif backend_configured:
            reason = "graph_sidecar_shadow_live_no_hits"
        else:
            reason = "graph_sidecar_shadow_prepared_backend_not_configured"
        lane.update(
            {
                "status": retrieval_trace.get("status") or getattr(health, "status", None) or "prepared",
                "abstained": True,
                "record_count": len(sidecar_hits),
                "reason": reason,
                "mode": getattr(graph_sidecar, "mode", "disabled"),
                "enabled": enabled,
                "backend": graphiti_backend or "not_configured",
                "backend_configured": backend_configured,
                "auto_build_indices": graphiti_auto_build_indices,
                "call_timeout_seconds": graphiti_call_timeout_seconds,
                "llm_provider": _graphiti_llm_trace(graphiti_llm),
                "episode_export_count": len(episodes),
                "raw_local_record_count": len(local_records),
                "upsert_preview_count": len(upsert_traces),
                "sidecar_hit_count": len(sidecar_hits),
                "retrieval_trace": retrieval_trace,
                "health": asdict(health) if is_dataclass(health) else health,
                "upsert_trace_samples": upsert_traces[:2],
                "hit_records": hit_records,
            }
        )
    except Exception as exc:
        lane.update(
            {
                "status": "error",
                "reason": "graph_sidecar_shadow_error",
                "error": exc.__class__.__name__,
            }
        )
    return lane


def _graphiti_sidecar_db_path(config_manager: ConfigManager) -> str | None:
    configured = _optional_string(config_manager.get_path("spark.memory.sidecars.graphiti.db_path", default=None))
    if not configured:
        return None
    home = str(config_manager.paths.home)
    return configured.replace("{home}", home).replace("$SPARK_HOME", home)


def _graphiti_sidecar_llm_settings(config_manager: ConfigManager) -> dict[str, Any]:
    provider_id = _optional_string(
        config_manager.get_path("spark.memory.sidecars.graphiti.llm.provider", default=None)
    ) or _optional_string(config_manager.get_path("providers.default_provider", default=None))
    provider_record = {}
    if provider_id:
        raw_record = config_manager.get_path(f"providers.records.{provider_id}", default={})
        if isinstance(raw_record, dict):
            provider_record = raw_record

    explicit_api_key_env = _optional_string(
        config_manager.get_path("spark.memory.sidecars.graphiti.llm.api_key_env", default=None)
    )
    api_key_env = explicit_api_key_env or _optional_string(provider_record.get("api_key_env"))
    env_map = config_manager.read_env_map()
    api_key = env_map.get(api_key_env) if api_key_env else None
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env)

    return {
        "provider_id": provider_id,
        "api_key_env": api_key_env,
        "api_key": api_key if api_key else None,
        "base_url": _optional_string(
            config_manager.get_path("spark.memory.sidecars.graphiti.llm.base_url", default=None)
        )
        or _optional_string(provider_record.get("base_url")),
        "model": _optional_string(
            config_manager.get_path("spark.memory.sidecars.graphiti.llm.model", default=None)
        )
        or _optional_string(provider_record.get("default_model")),
        "small_model": _optional_string(
            config_manager.get_path("spark.memory.sidecars.graphiti.llm.small_model", default=None)
        ),
    }


def _graphiti_llm_trace(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_id": settings.get("provider_id"),
        "api_key_env": settings.get("api_key_env"),
        "api_key_configured": bool(settings.get("api_key")),
        "base_url_configured": bool(settings.get("base_url")),
        "model": settings.get("model"),
        "small_model": settings.get("small_model") or settings.get("model"),
    }


def _graph_sidecar_exportable_record(record: dict[str, Any]) -> bool:
    predicate = _optional_string(record.get("predicate"))
    value = _optional_string(record.get("value")) or _optional_string(record.get("normalized_value"))
    memory_role = _optional_string(record.get("memory_role"))
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    if predicate == "raw_turn" or not predicate or not value:
        return False
    if memory_role in {"current_state", "structured_evidence", "state_deletion", "event"}:
        return True
    return bool(metadata.get("entity_key") or metadata.get("entity_keys"))


def _prioritize_graph_sidecar_export_records(
    records: list[dict[str, Any]],
    *,
    entity_key: str | None,
) -> list[dict[str, Any]]:
    normalized_entity_key = str(entity_key or "").strip()
    if not normalized_entity_key:
        return records
    matched: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for record in records:
        if normalized_entity_key in _graph_sidecar_record_entity_keys(record):
            matched.append(record)
        else:
            other.append(record)
    return matched + other


def _graph_sidecar_record_entity_keys(record: dict[str, Any]) -> set[str]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    values: list[Any] = [
        record.get("entity_key"),
        metadata.get("entity_key"),
        record.get("entity_keys"),
        metadata.get("entity_keys"),
    ]
    keys: set[str] = set()
    for value in values:
        if isinstance(value, list):
            keys.update(str(item).strip() for item in value if str(item or "").strip())
        elif str(value or "").strip():
            keys.add(str(value).strip())
    return keys


def _graph_sidecar_hit_record(hit: Any) -> dict[str, Any]:
    if is_dataclass(hit):
        payload = asdict(hit)
    elif isinstance(hit, dict):
        payload = dict(hit)
    else:
        payload = {
            "source_record_id": getattr(hit, "source_record_id", None),
            "source_class": getattr(hit, "source_class", "graphiti_temporal_graph"),
            "text": getattr(hit, "text", ""),
            "score": getattr(hit, "score", 0.0),
            "provenance": getattr(hit, "provenance", {}),
            "validity": getattr(hit, "validity", {}),
            "confidence": getattr(hit, "confidence", None),
            "entity_keys": getattr(hit, "entity_keys", []),
            "metadata": getattr(hit, "metadata", {}),
        }
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    validity = payload.get("validity") if isinstance(payload.get("validity"), dict) else {}
    entity_keys = payload.get("entity_keys") if isinstance(payload.get("entity_keys"), list) else []
    return {
        "source_record_id": str(payload.get("source_record_id") or provenance.get("uuid") or ""),
        "source_class": str(payload.get("source_class") or "graphiti_temporal_graph"),
        "memory_role": "graph_sidecar_hit",
        "predicate": metadata.get("scope"),
        "value": str(payload.get("text") or ""),
        "text": str(payload.get("text") or ""),
        "score": payload.get("score"),
        "confidence": payload.get("confidence"),
        "entity_key": entity_keys[0] if entity_keys else None,
        "metadata": {
            **metadata,
            "source_class": str(payload.get("source_class") or "graphiti_temporal_graph"),
            "memory_role": "graph_sidecar_hit",
            "source_surface": "graphiti_temporal_graph",
            "source_ref": provenance.get("uuid"),
            "provenance": provenance,
            "validity": validity,
        },
    }


def _build_wiki_packet_lane(
    *,
    config_manager: ConfigManager,
    sdk_module: str,
    query: str,
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paths = _wiki_packet_paths(config_manager)
    lane = {
        "lane": "wiki_packets",
        "read_method": "retrieve_markdown_knowledge_packets",
        "source_class": "obsidian_llm_wiki_packets",
        "status": "not_configured",
        "abstained": True,
        "record_count": 0,
        "reason": "wiki_packet_paths_not_configured",
        "authority": "supporting_not_authoritative",
        "paths": [str(path) for path in paths],
    }
    if not paths:
        return lane, []
    try:
        module = _import_memory_sdk_module(sdk_module or DEFAULT_SDK_MODULE)
        retrieve_packets = getattr(module, "retrieve_markdown_knowledge_packets")
    except Exception as exc:
        lane.update(
            {
                "status": "unavailable",
                "reason": "wiki_packet_reader_unavailable",
                "error": exc.__class__.__name__,
            }
        )
        return lane, []
    try:
        result = retrieve_packets(paths=paths, query=query, top_k=limit)
        hits = list(getattr(result, "hits", []) or [])
        records = [_wiki_packet_hit_to_record(hit) for hit in hits]
        lane.update(
            {
                "status": "supported" if records else "not_found",
                "abstained": not bool(records),
                "record_count": len(records),
                "reason": None if records else "no_matching_wiki_packets",
                "retrieval_trace": getattr(result, "trace", {}),
            }
        )
        return lane, records
    except Exception as exc:
        lane.update(
            {
                "status": "error",
                "reason": "wiki_packet_reader_error",
                "error": exc.__class__.__name__,
            }
        )
        return lane, []


def _wiki_packet_paths(config_manager: ConfigManager) -> list[Path]:
    raw_paths = config_manager.get_path("spark.memory.wiki_packet_paths", default=None)
    paths: list[Path] = []
    if isinstance(raw_paths, list):
        paths.extend(Path(str(item)).expanduser() for item in raw_paths if str(item or "").strip())
    elif isinstance(raw_paths, str) and raw_paths.strip():
        separators = [";", os.pathsep]
        chunks = [raw_paths]
        for separator in separators:
            chunks = [piece for chunk in chunks for piece in chunk.split(separator)]
        paths.extend(Path(chunk.strip()).expanduser() for chunk in chunks if chunk.strip())

    home = Path(config_manager.paths.home)
    for candidate in (
        home / "wiki",
        home / "knowledge",
        home / "diagnostics",
        home / "artifacts" / "spark-memory-kb" / "wiki",
    ):
        if candidate.exists():
            paths.append(candidate)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _wiki_packet_hit_to_record(hit: Any) -> dict[str, Any]:
    provenance = getattr(hit, "provenance", {}) or {}
    metadata = getattr(hit, "metadata", {}) or {}
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    title = str(getattr(hit, "title", "") or "").strip()
    text = str(getattr(hit, "text", "") or "").strip()
    packet_id = str(getattr(hit, "packet_id", "") or "").strip()
    source_path = str(metadata.get("source_path") or provenance.get("source_path") or "").strip()
    authority = str(metadata.get("authority") or "supporting_not_authoritative").strip()
    normalized_metadata = {
        **metadata,
        "packet_id": packet_id,
        "source_path": source_path,
        "source_surface": "obsidian_llm_wiki_packets",
        "authority": authority,
    }
    for field in WIKI_PACKET_METADATA_FIELDS:
        if field in normalized_metadata and normalized_metadata[field] is not None:
            normalized_metadata[field] = str(normalized_metadata[field]).strip()
    return {
        "subject": "spark:project_knowledge",
        "predicate": "knowledge.packet",
        "value": title,
        "text": text,
        "summary": title,
        "memory_role": "structured_evidence",
        "source_class": "obsidian_llm_wiki_packets",
        "metadata": normalized_metadata,
        "provenance": [dict(provenance)],
    }


def _sidecar_record_namespace(record: dict[str, Any]) -> SimpleNamespace:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
    turn_ids = record.get("turn_ids") or metadata.get("turn_ids") or []
    if not isinstance(turn_ids, list):
        turn_ids = [turn_ids]
    return SimpleNamespace(
        observation_id=record.get("observation_id") or metadata.get("observation_id") or record.get("id") or "",
        event_id=record.get("event_id") or metadata.get("event_id") or "",
        session_id=record.get("session_id") or metadata.get("session_id") or "",
        turn_ids=[str(item) for item in turn_ids if str(item or "").strip()],
        memory_role=record.get("memory_role") or metadata.get("memory_role") or "structured_evidence",
        text=_hybrid_memory_record_text(record),
        subject=record.get("subject") or metadata.get("subject") or "",
        predicate=record.get("predicate") or metadata.get("predicate") or "",
        timestamp=record.get("timestamp") or metadata.get("timestamp") or record.get("created_at") or "",
        metadata=metadata,
        lifecycle=lifecycle,
        retention_class=record.get("retention_class") or metadata.get("retention_class"),
    )


def _hybrid_memory_dict_source_class(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    memory_role = str(record.get("memory_role") or metadata.get("memory_role") or "").strip()
    if memory_role == "event":
        return "retrieved_events"
    if memory_role == "current_state":
        return "current_state"
    if memory_role == "state_deletion":
        return "state_deletion"
    if memory_role in {"episodic", "raw_episode"}:
        return "recent_conversation"
    return "retrieved_evidence"


def _hybrid_memory_source_class(*, lane: str, kernel: MemoryKernelReadResult, record: dict[str, Any]) -> str:
    if lane == "current_state":
        return "current_state"
    if lane == "entity_current_state":
        return "entity_current_state"
    if lane == "historical_state":
        return "historical_state"
    if lane == "events":
        return "event"
    if lane == "recent_conversation":
        return "recent_conversation"
    if lane == "wiki_packets":
        return "obsidian_llm_wiki_packets"
    if lane == "evidence":
        memory_role = str(record.get("memory_role") or kernel.read_result.memory_role or "").strip()
        if memory_role in {"structured_evidence", "belief", "belief_candidate", "raw_episode"}:
            return memory_role
        return "evidence"
    return kernel.source_class or lane


def _hybrid_memory_record_text(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    parts = [
        record.get("value"),
        record.get("normalized_value"),
        record.get("text"),
        record.get("summary"),
        record.get("answer"),
        metadata.get("value"),
        metadata.get("normalized_value"),
        metadata.get("entity_label"),
        metadata.get("source_text"),
        metadata.get("evidence_text"),
    ]
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip())


def _hybrid_memory_record_key(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    stable_id = _optional_string(record.get("observation_id")) or _optional_string(metadata.get("observation_id"))
    if stable_id:
        return stable_id
    return "|".join(
        [
            str(record.get("subject") or "").strip(),
            str(record.get("predicate") or "").strip(),
            str(metadata.get("entity_key") or "").strip(),
            _hybrid_memory_record_text(record).casefold(),
        ]
    )


def _hybrid_memory_query_tokens(query: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "about",
        "did",
        "do",
        "for",
        "from",
        "have",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "our",
        "should",
        "the",
        "this",
        "to",
        "was",
        "what",
        "where",
        "with",
        "you",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(query or "").casefold())
        if token and token not in stopwords
    }


def _hybrid_memory_query_prefers_project_knowledge(query: str) -> bool:
    lowered = str(query or "").casefold()
    if not lowered.strip():
        return False
    phrase_markers = (
        "self-awareness",
        "self awareness",
        "self-aware",
        "self aware",
        "self introspection",
        "self-introspection",
        "introspection",
        "where do you lack",
        "where you lack",
        "how can you improve",
        "what can you improve",
        "recursive self-improvement",
        "self-improvement loop",
        "self improvement loop",
        "llm wiki",
        "wiki",
        "knowledge base",
        "knowledge-base",
        "system map",
        "route map",
        "tracing path",
        "observability",
        "tool base",
        "capability",
        "capabilities",
        "chips",
        "providers",
        "what can spark",
        "what can you do",
        "how are your systems",
    )
    if any(marker in lowered for marker in phrase_markers):
        return True
    tokens = _hybrid_memory_query_tokens(lowered)
    system_tokens = {
        "spark",
        "system",
        "systems",
        "route",
        "routes",
        "tool",
        "tools",
        "chip",
        "chips",
        "provider",
        "providers",
        "trace",
        "traces",
        "tracing",
        "wiki",
        "kb",
        "capability",
        "capabilities",
        "introspection",
        "awareness",
        "improve",
        "improvement",
        "loops",
        "recursive",
    }
    return len(tokens.intersection(system_tokens)) >= 2


def _score_hybrid_memory_record(
    *,
    lane: str,
    source_class: str,
    record: dict[str, Any],
    query: str,
    predicate: str | None,
    entity_key: str | None,
) -> tuple[float, list[str]]:
    lane_authority = {
        "current_state": 100.0,
        "historical_state": 82.0,
        "events": 58.0,
        "evidence": 52.0,
        "recent_conversation": 50.0,
        "typed_temporal_graph": 46.0,
        "wiki_packets": 44.0,
        "pending_tasks": 86.0,
        "procedural_lessons": 64.0,
    }
    score = lane_authority.get(lane, 40.0)
    reasons = [f"authority:{lane}"]
    project_knowledge_first = _hybrid_memory_query_prefers_project_knowledge(query)
    if project_knowledge_first and lane == "wiki_packets":
        score += 38.0
        reasons.append("project_knowledge_intent_boost")
    elif project_knowledge_first and lane in {"events", "evidence", "recent_conversation"}:
        score -= 16.0
        reasons.append("project_knowledge_intent_generic_memory_penalty")
    record_predicate = str(record.get("predicate") or "").strip()
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    if predicate and record_predicate == predicate:
        score += 15.0
        reasons.append("predicate_match")
    if entity_key and str(metadata.get("entity_key") or "").strip() == entity_key:
        score += 18.0
        reasons.append("entity_match")
    query_tokens = _hybrid_memory_query_tokens(query)
    if query_tokens:
        text = _hybrid_memory_record_text(record).casefold()
        matched = {token for token in query_tokens if token in text}
        if matched:
            score += min(12.0, float(len(matched) * 3))
            reasons.append(f"query_overlap:{len(matched)}")
    if record.get("provenance") or metadata.get("source_surface") or metadata.get("source_text"):
        score += 2.0
        reasons.append("provenance_present")
    if lane != "historical_state" and _memory_kernel_record_is_stale(record):
        score -= 120.0
        reasons.append("stale_penalty")
    if source_class == "current_state":
        score += 4.0
        reasons.append("current_state_source")
    if source_class == "pending_task":
        score += 8.0
        reasons.append("workflow_recovery_source")
    if source_class == "procedural_lesson":
        score += 3.0
        reasons.append("procedural_memory_source")
    return score, reasons


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
    record_activity: bool,
) -> MemoryRetrievalQueryResult:
    normalized_query = str(query or "").strip()
    normalized_subject = _optional_string(subject)
    normalized_predicate = _optional_string(predicate)
    session_id = f"{'memory-retrieval' if record_activity else 'memory-internal'}:{actor_id}"
    turn_id = f"{actor_id}:{method}"
    kernel = MemoryKernelAdapter(
        config_manager=config_manager,
        state_db=state_db,
        sdk_module=sdk_module,
        actor_id=actor_id,
    ).search(
        method=method,
        query=normalized_query,
        subject=normalized_subject,
        predicate=normalized_predicate,
        limit=limit,
        session_id=session_id,
        turn_id=turn_id,
        source_surface=f"memory_cli_{method}" if record_activity else f"memory_internal_{method}",
        record_activity=record_activity,
    )
    return MemoryRetrievalQueryResult(
        sdk_module=kernel.sdk_module,
        method=method,
        query=normalized_query,
        subject=normalized_subject,
        predicate=normalized_predicate,
        limit=limit,
        runtime=kernel.runtime,
        read_result=kernel.read_result,
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


def _record_memory_maintenance_event(
    *,
    state_db: StateDB,
    result: MemoryMaintenanceRunResult,
    actor_id: str,
) -> None:
    record_event(
        state_db,
        event_type="memory_maintenance_run",
        component="memory_orchestrator",
        summary="Spark memory SDK maintenance run.",
        actor_id=actor_id,
        facts={
            "sdk_module": result.sdk_module,
            "status": result.status,
            "reason": result.reason,
            "maintenance": result.maintenance,
            "trace": result.trace,
        },
        provenance={"memory_role": "maintenance"},
    )


def _record_memory_maintenance_lifecycle_transitions(
    *,
    state_db: StateDB,
    result: MemoryMaintenanceRunResult,
    actor_id: str,
) -> None:
    if result.status != "succeeded":
        return
    maintenance = result.maintenance if isinstance(result.maintenance, dict) else {}
    transition_specs = [
        (
            "active_deletion_count",
            "delete",
            "deleted",
            "maintenance_delete_tombstone",
            "active deletion",
            "deleted",
        ),
        (
            "active_state_stale_preserved_count",
            "decay",
            "stale_preserved",
            "active_state_stale_preserved",
            "stale current-state preserved",
            "stale_preserved",
        ),
        (
            "active_state_superseded_count",
            "supersession",
            "superseded",
            "active_state_superseded",
            "current-state supersession",
            "superseded",
        ),
        (
            "active_state_archived_count",
            "archive",
            "archived",
            "maintenance_archive",
            "current-state archived",
            "archived",
        ),
        (
            "active_state_resurrected_count",
            "resurrection",
            "resurrected",
            "active_state_resurrected",
            "current-state resurrected",
            "resurrected",
        ),
    ]
    audit_samples = maintenance.get("audit_samples") if isinstance(maintenance.get("audit_samples"), dict) else {}
    for field, transition_kind, lifecycle_action, destination, readable_name, sample_bucket in transition_specs:
        count = _int_or_zero(maintenance.get(field))
        if count <= 0:
            continue
        samples = audit_samples.get(sample_bucket) if isinstance(audit_samples.get(sample_bucket), list) else []
        _record_memory_lifecycle_transition(
            state_db=state_db,
            human_id=None,
            transition_kind=transition_kind,
            memory_role="current_state",
            source_predicate=f"memory.maintenance.{field}",
            source_text=f"SDK maintenance reported {count} {readable_name} transition(s).",
            source_observation_id=None,
            reason=f"sdk_maintenance_{field}",
            retention_class="maintenance_summary",
            lifecycle_action=lifecycle_action,
            destination=destination,
            session_id=None,
            turn_id=None,
            channel_kind=None,
            actor_id=actor_id,
            transition_count=count,
            extra_facts={
                "sdk_module": result.sdk_module,
                "maintenance_field": field,
                "manual_observations_before": maintenance.get("manual_observations_before"),
                "manual_observations_after": maintenance.get("manual_observations_after"),
                "audit_sample_bucket": sample_bucket,
                "audit_sample_count": len(samples),
                "audit_samples": samples[:5],
                "trace": result.trace,
            },
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
    memory_role: str = "current_state",
    summary: str = "Spark memory write requested for durable structured facts.",
    salience_decision: MemorySalienceDecision | None = None,
) -> None:
    subject = _subject_for_human_id(human_id)
    salience_facts = salience_decision.metadata() if salience_decision is not None else {}
    record_event(
        state_db,
        event_type="memory_write_requested",
        component="memory_orchestrator",
        summary=summary,
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        facts={
            "operation": operation,
            "method": "write_observation",
            "memory_role": memory_role,
            "subject": subject,
            "predicate_count": len(observations),
            "predicates": [str(item.get("predicate") or "") for item in observations if item.get("predicate")],
            "observations": observations,
            **salience_facts,
        },
        provenance={"memory_role": memory_role},
    )
    _record_memory_salience_lifecycle_transition_from_candidate(
        state_db=state_db,
        human_id=human_id,
        candidates=observations,
        salience_decision=salience_decision,
        operation=operation,
        fallback_memory_role=memory_role,
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
    )


def _preview_memory_text(value: str | None, *, limit: int = 500) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}..."


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _record_memory_lifecycle_transition(
    *,
    state_db: StateDB,
    human_id: str | None,
    transition_kind: str,
    memory_role: str,
    source_predicate: str,
    source_text: str,
    source_observation_id: str | None,
    reason: str,
    retention_class: str,
    lifecycle_action: str,
    destination: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str,
    old_value: str | None = None,
    new_value: str | None = None,
    transition_count: int | None = None,
    extra_facts: dict[str, Any] | None = None,
) -> None:
    normalized_reason = str(reason or "unknown").strip() or "unknown"
    facts = {
        "transition_kind": transition_kind,
        "memory_role": memory_role,
        "source_predicate": source_predicate,
        "source_text": _preview_memory_text(source_text),
        "source_observation_id": source_observation_id,
        "reason": normalized_reason,
        "archive_reason": normalized_reason,
        "retention_class": retention_class,
        "lifecycle_action": lifecycle_action,
        "destination": destination,
        "readable_summary": (
            f"{memory_role.replace('_', ' ').title()} was {lifecycle_action} "
            f"because {normalized_reason.replace('_', ' ')}."
        ),
    }
    if old_value is not None:
        facts["old_value"] = _preview_memory_text(old_value)
    if new_value is not None:
        facts["new_value"] = _preview_memory_text(new_value)
    if transition_count is not None:
        facts["transition_count"] = transition_count
    if extra_facts:
        facts.update(extra_facts)
    record_event(
        state_db,
        event_type="memory_lifecycle_transition",
        component="memory_orchestrator",
        summary=f"Spark memory lifecycle transition: {memory_role} {lifecycle_action}.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        channel_id=channel_kind,
        actor_id=actor_id,
        status="recorded",
        reason_code=normalized_reason,
        facts=facts,
        provenance={
            "memory_role": memory_role,
            "source": "memory_orchestrator_lifecycle",
            "lifecycle_action": lifecycle_action,
        },
    )


def _salience_lifecycle_action(decision: MemorySalienceDecision) -> str:
    if not decision.should_write:
        return "blocked_by_salience"
    if decision.promotion_disposition == "capture_raw_episode":
        return "captured_by_salience"
    return "promoted_by_salience"


def _salience_lifecycle_destination(decision: MemorySalienceDecision) -> str:
    if not decision.should_write:
        return "policy_gate_trace_only"
    if decision.promotion_disposition:
        return decision.promotion_disposition
    if decision.promotion_stage:
        return decision.promotion_stage
    return "memory_lane"


def _record_memory_salience_lifecycle_transition(
    *,
    state_db: StateDB,
    human_id: str | None,
    predicate: str,
    value: str | None,
    evidence_text: str,
    decision: MemorySalienceDecision,
    memory_role: str,
    retention_class: str,
    operation: str,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str,
) -> None:
    lifecycle_action = _salience_lifecycle_action(decision)
    reason = decision.why_saved or decision.reason_code or lifecycle_action
    readable_action = lifecycle_action.replace("_", " ")
    readable_reason = reason.replace("_", " ")
    _record_memory_lifecycle_transition(
        state_db=state_db,
        human_id=human_id,
        transition_kind="salience_block" if not decision.should_write else "salience_promotion",
        memory_role=memory_role,
        source_predicate=predicate,
        source_text=evidence_text or str(value or ""),
        source_observation_id=None,
        reason=reason,
        retention_class=retention_class,
        lifecycle_action=lifecycle_action,
        destination=_salience_lifecycle_destination(decision),
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        actor_id=actor_id,
        old_value=None,
        new_value=value,
        transition_count=1,
        extra_facts={
            "operation": operation,
            "keepability": decision.keepability,
            "promotion_disposition": decision.promotion_disposition,
            "promotion_stage": decision.promotion_stage,
            "salience_score": decision.salience_score,
            "confidence": decision.confidence,
            "why_saved": decision.why_saved,
            "salience_reasons": list(decision.reasons),
            "readable_summary": (
                f"Memory candidate was {readable_action} "
                f"because {readable_reason}."
            ),
        },
    )


def _record_memory_salience_lifecycle_transition_from_candidate(
    *,
    state_db: StateDB,
    human_id: str | None,
    candidates: list[dict[str, Any]],
    salience_decision: MemorySalienceDecision | None,
    operation: str,
    fallback_memory_role: str,
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    if salience_decision is None or not candidates:
        return
    candidate = candidates[0]
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    _record_memory_salience_lifecycle_transition(
        state_db=state_db,
        human_id=human_id,
        predicate=str(candidate.get("predicate") or "unknown"),
        value=None if candidate.get("value") is None else str(candidate.get("value")),
        evidence_text=str(
            candidate.get("text")
            or metadata.get("evidence_text")
            or candidate.get("value")
            or ""
        ),
        decision=salience_decision,
        memory_role=str(candidate.get("memory_role") or metadata.get("memory_role") or fallback_memory_role),
        retention_class=str(candidate.get("retention_class") or metadata.get("retention_class") or "unknown"),
        operation=operation,
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=None if metadata.get("channel_kind") is None else str(metadata.get("channel_kind")),
        actor_id=actor_id,
    )


def _record_memory_salience_policy_block(
    *,
    state_db: StateDB,
    human_id: str,
    predicate: str,
    value: str | None,
    evidence_text: str,
    decision: MemorySalienceDecision,
    session_id: str | None,
    turn_id: str | None,
    channel_kind: str | None,
    actor_id: str,
) -> None:
    record_policy_gate_block(
        state_db,
        component="memory_orchestrator",
        policy_domain="memory_salience",
        gate_name="memory.salience",
        source_kind="memory_candidate",
        source_ref=turn_id,
        summary="Spark memory salience gate blocked a durable memory write.",
        action="blocked",
        reason_code=decision.reason_code,
        blocked_stage="before_sdk_write",
        input_ref=turn_id,
        severity="high" if decision.reason_code == "salience_secret_like_material" else "medium",
        request_id=turn_id,
        session_id=session_id,
        actor_id=actor_id,
        provenance={"memory_role": "current_state", "human_id": human_id},
        facts={
            "subject": _subject_for_human_id(human_id),
            "predicate": predicate,
            "value": value,
            "evidence_text": evidence_text,
            "keepability": decision.keepability,
            "promotion_disposition": decision.promotion_disposition,
            "promotion_stage": decision.promotion_stage,
            "salience_score": decision.salience_score,
            "confidence": decision.confidence,
            "why_saved": decision.why_saved,
            "salience_reasons": list(decision.reasons),
        },
    )
    _record_memory_salience_lifecycle_transition(
        state_db=state_db,
        human_id=human_id,
        predicate=predicate,
        value=value,
        evidence_text=evidence_text,
        decision=decision,
        memory_role="current_state",
        retention_class=decision.keepability or "not_keepable",
        operation="blocked",
        session_id=session_id,
        turn_id=turn_id,
        channel_kind=channel_kind,
        actor_id=actor_id,
    )


def _record_memory_write_requested_events(
    *,
    state_db: StateDB,
    human_id: str,
    events: list[dict[str, Any]],
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
    salience_decision: MemorySalienceDecision | None = None,
) -> None:
    subject = _subject_for_human_id(human_id)
    salience_facts = salience_decision.metadata() if salience_decision is not None else {}
    record_event(
        state_db,
        event_type="memory_write_requested",
        component="memory_orchestrator",
        summary="Spark memory event write requested for durable Telegram events.",
        request_id=turn_id,
        session_id=session_id,
        human_id=human_id,
        actor_id=actor_id,
        facts={
            "operation": "event",
            "method": "write_event",
            "memory_role": "event",
            "subject": subject,
            "predicate_count": len(events),
            "predicates": [str(item.get("predicate") or "") for item in events if item.get("predicate")],
            "events": events,
            **salience_facts,
        },
        provenance={"memory_role": "event"},
    )
    _record_memory_salience_lifecycle_transition_from_candidate(
        state_db=state_db,
        human_id=human_id,
        candidates=events,
        salience_decision=salience_decision,
        operation="event",
        fallback_memory_role="event",
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
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
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
