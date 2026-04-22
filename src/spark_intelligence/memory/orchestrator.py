from __future__ import annotations

import importlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.generic_observations import detect_telegram_generic_observation
from spark_intelligence.memory.profile_facts import (
    active_state_revalidate_at,
    active_state_revalidation_days,
)
from spark_intelligence.memory_contracts import (
    annotate_contract_trace,
    effective_memory_role,
    memory_contract_reason,
    normalize_memory_role,
)
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


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
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._persistence_path.with_name(f"{self._persistence_path.name}.lock")
        with _exclusive_path_lock(lock_path):
            existing_payload = _load_domain_chip_memory_payload(self._persistence_path)
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
            payload = {
                "manual_observations": [asdict(entry) for entry in merged_observations],
                "manual_events": [asdict(entry) for entry in merged_events],
                "manual_current_state_snapshot": [asdict(entry) for entry in current_snapshot],
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
    }
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
    }
    _record_memory_write_requested_observations(
        state_db=state_db,
        operation=operation,
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
    }
    _record_memory_write_requested_events(
        state_db=state_db,
        human_id=human_id,
        events=[event_payload],
        session_id=session_id,
        turn_id=turn_id,
        actor_id=actor_id,
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
            "metadata": {
                "entity_type": "human",
                "channel_kind": channel_kind,
                "memory_role": "event",
                "source_surface": "profile_fact_history_capture",
                "fact_name": fact_name,
                "normalized_value": value,
                "value": value,
                "source_predicate": predicate,
            },
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
            },
        },
    )


def _profile_fact_retention_class(predicate: str | None) -> str:
    normalized = str(predicate or "").strip().lower()
    if normalized.startswith("profile.current_"):
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
    record_activity: bool,
) -> MemoryRetrievalQueryResult:
    module_name = str(sdk_module or config_manager.get_path("spark.memory.sdk_module", default=DEFAULT_SDK_MODULE) or DEFAULT_SDK_MODULE)
    runtime = inspect_memory_sdk_runtime(config_manager=config_manager, sdk_module=module_name)
    client = _load_sdk_client_for_module(module_name=module_name, home_path=config_manager.paths.home)
    normalized_query = str(query or "").strip()
    normalized_subject = _optional_string(subject)
    normalized_predicate = _optional_string(predicate)
    session_id = f"{'memory-retrieval' if record_activity else 'memory-internal'}:{actor_id}"
    turn_id = f"{actor_id}:{method}"
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
        if record_activity:
            _record_memory_read_event(
                state_db=state_db,
                result=read_result,
                human_id=_human_id_from_subject(normalized_subject or ""),
                session_id=session_id,
                turn_id=turn_id,
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
    if record_activity:
        _record_memory_read_requested_subject(
            state_db=state_db,
            method=method,
            subject=normalized_subject or "",
            predicate=normalized_predicate,
            query=normalized_query,
            session_id=session_id,
            turn_id=turn_id,
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
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": _now_iso(),
            "metadata": {
                "source_surface": (
                    f"memory_cli_{method}" if record_activity else f"memory_internal_{method}"
                )
            },
        },
    )
    read_result = _normalize_read_result(raw=raw, method=method, shadow_only=False)
    if record_activity:
        _record_memory_read_event(
            state_db=state_db,
            result=read_result,
            human_id=_human_id_from_subject(normalized_subject or ""),
            session_id=session_id,
            turn_id=turn_id,
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
    memory_role: str = "current_state",
    summary: str = "Spark memory write requested for durable structured facts.",
) -> None:
    subject = _subject_for_human_id(human_id)
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
        },
        provenance={"memory_role": memory_role},
    )


def _record_memory_write_requested_events(
    *,
    state_db: StateDB,
    human_id: str,
    events: list[dict[str, Any]],
    session_id: str | None,
    turn_id: str | None,
    actor_id: str,
) -> None:
    subject = _subject_for_human_id(human_id)
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
        },
        provenance={"memory_role": "event"},
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
