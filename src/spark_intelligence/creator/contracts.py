from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


CREATOR_INTENT_SCHEMA_VERSION = "spark-creator-intent.v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "spark-artifact-manifest.v1"
CREATOR_TRACE_SCHEMA_VERSION = "spark-creator-trace.v1"

PRIVACY_MODES = {"local_only", "github_pr", "swarm_shared"}
RISK_LEVELS = {"low", "medium", "high"}
ARTIFACT_TYPES = {
    "domain_chip",
    "benchmark_pack",
    "specialization_path",
    "autoloop_policy",
    "tool_integration",
    "swarm_publish_packet",
    "creator_report",
}
PROMOTION_GATES = {
    "schema_gate",
    "lineage_gate",
    "benchmark_gate",
    "transfer_gate",
    "complexity_gate",
    "memory_hygiene_gate",
    "autonomy_gate",
    "rollback_gate",
    "risk_gate",
}
TASK_STATUSES = {"planned", "running", "blocked", "passed", "failed", "skipped"}
PUBLISH_READINESS = {
    "private_draft",
    "workspace_validated",
    "pr_ready",
    "pr_submitted",
    "reviewed_candidate",
    "network_absorbable",
    "canonical",
}
NETWORK_CONTRIBUTION_POLICIES = {"workspace_only", "github_pr_required", "manual_review_required"}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    severity: str = "error"

    def to_text(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: str = ARTIFACT_MANIFEST_SCHEMA_VERSION
    artifact_id: str = ""
    artifact_type: str = ""
    repo: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    promotion_gates: list[str] = field(default_factory=list)
    rollback_plan: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreatorTraceTask:
    task_id: str
    status: str
    evidence: list[str] = field(default_factory=list)
    risk: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CreatorTrace:
    schema_version: str = CREATOR_TRACE_SCHEMA_VERSION
    trace_id: str = ""
    intent_id: str = ""
    tasks: list[CreatorTraceTask] = field(default_factory=list)
    repo_changes: list[str] = field(default_factory=list)
    benchmarks: list[str] = field(default_factory=list)
    publish_readiness: str = "private_draft"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tasks"] = [
            task.to_dict() if hasattr(task, "to_dict") else dict(task)
            for task in self.tasks
        ]
        return payload


def validate_creator_intent_packet(payload: Mapping[str, Any] | Any) -> list[ValidationIssue]:
    data = _payload_to_mapping(payload)
    if data is None:
        return [ValidationIssue("$", "expected object-like creator intent packet")]

    issues: list[ValidationIssue] = []
    _require_exact(data, "schema_version", CREATOR_INTENT_SCHEMA_VERSION, issues)
    _require_non_empty_string(data, "intent_id", issues)
    _require_non_empty_string(data, "user_goal", issues)
    _require_non_empty_string(data, "target_domain", issues)
    _require_non_empty_string(data, "target_operator_surface", issues)
    _require_non_empty_string(data, "expected_agent_capability", issues)
    _require_non_empty_string(data, "success_claim", issues)
    _require_allowed(data, "privacy_mode", PRIVACY_MODES, issues)
    _require_allowed(data, "risk_level", RISK_LEVELS, issues)
    _require_allowed(data, "network_contribution_policy", NETWORK_CONTRIBUTION_POLICIES, issues)
    _require_string_list(data, "success_examples", issues)
    _require_string_list(data, "failure_examples", issues)
    _require_string_list(data, "tools_in_scope", issues)
    _require_string_list(data, "data_sources_allowed", issues)
    _require_string_list(data, "artifact_targets", issues, allowed=ARTIFACT_TYPES, require_non_empty=True)
    _require_string_list(data, "usage_surfaces", issues, require_non_empty=True)
    _require_string_list(data, "capabilities_to_prove", issues, require_non_empty=True)
    _require_bool_map(data, "desired_outputs", issues, require_non_empty=True)
    _require_bool_or_int_map(data, "benchmark_requirements", issues, require_non_empty=True)
    return issues


def validate_artifact_manifest(payload: Mapping[str, Any] | ArtifactManifest) -> list[ValidationIssue]:
    data = _payload_to_mapping(payload)
    if data is None:
        return [ValidationIssue("$", "expected object-like artifact manifest")]

    issues: list[ValidationIssue] = []
    _require_exact(data, "schema_version", ARTIFACT_MANIFEST_SCHEMA_VERSION, issues)
    _require_non_empty_string(data, "artifact_id", issues)
    _require_allowed(data, "artifact_type", ARTIFACT_TYPES, issues)
    _require_non_empty_string(data, "repo", issues)
    _require_string_list(data, "inputs", issues)
    _require_string_list(data, "outputs", issues, require_non_empty=True)
    _require_string_list(data, "validation_commands", issues, require_non_empty=True)
    _require_string_list(data, "promotion_gates", issues, allowed=PROMOTION_GATES, require_non_empty=True)
    _require_non_empty_string(data, "rollback_plan", issues)
    return issues


def validate_creator_trace(payload: Mapping[str, Any] | CreatorTrace) -> list[ValidationIssue]:
    data = _payload_to_mapping(payload)
    if data is None:
        return [ValidationIssue("$", "expected object-like creator trace")]

    issues: list[ValidationIssue] = []
    _require_exact(data, "schema_version", CREATOR_TRACE_SCHEMA_VERSION, issues)
    _require_non_empty_string(data, "trace_id", issues)
    _require_non_empty_string(data, "intent_id", issues)
    _require_allowed(data, "publish_readiness", PUBLISH_READINESS, issues)
    _require_string_list(data, "repo_changes", issues)
    _require_string_list(data, "benchmarks", issues)

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        issues.append(ValidationIssue("tasks", "expected a non-empty list"))
    else:
        for index, raw_task in enumerate(tasks):
            task = _payload_to_mapping(raw_task)
            path = f"tasks[{index}]"
            if task is None:
                issues.append(ValidationIssue(path, "expected task object"))
                continue
            _require_non_empty_string(task, f"{path}.task_id", issues, source_key="task_id")
            _require_allowed(task, f"{path}.status", TASK_STATUSES, issues, source_key="status")
            _require_string_list(task, f"{path}.evidence", issues, source_key="evidence")
            _require_string_list(task, f"{path}.risk", issues, source_key="risk")
    return issues


def _payload_to_mapping(payload: Mapping[str, Any] | Any) -> Mapping[str, Any] | None:
    if isinstance(payload, Mapping):
        return payload
    if hasattr(payload, "to_dict"):
        value = payload.to_dict()
        return value if isinstance(value, Mapping) else None
    return None


def _require_exact(
    data: Mapping[str, Any],
    key: str,
    expected: str,
    issues: list[ValidationIssue],
    *,
    path: str | None = None,
) -> None:
    value = data.get(key)
    if value != expected:
        issues.append(ValidationIssue(path or key, f"expected {expected!r}"))


def _require_non_empty_string(
    data: Mapping[str, Any],
    path: str,
    issues: list[ValidationIssue],
    *,
    source_key: str | None = None,
) -> None:
    key = source_key or path
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue(path, "expected non-empty string"))


def _require_allowed(
    data: Mapping[str, Any],
    path: str,
    allowed: set[str],
    issues: list[ValidationIssue],
    *,
    source_key: str | None = None,
) -> None:
    key = source_key or path
    value = data.get(key)
    if not isinstance(value, str) or value not in allowed:
        allowed_list = ", ".join(sorted(allowed))
        issues.append(ValidationIssue(path, f"expected one of: {allowed_list}"))


def _require_string_list(
    data: Mapping[str, Any],
    path: str,
    issues: list[ValidationIssue],
    *,
    source_key: str | None = None,
    allowed: set[str] | None = None,
    require_non_empty: bool = False,
) -> None:
    key = source_key or path
    value = data.get(key)
    if not isinstance(value, list):
        issues.append(ValidationIssue(path, "expected list"))
        return
    if require_non_empty and not value:
        issues.append(ValidationIssue(path, "expected a non-empty list"))
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(ValidationIssue(f"{path}[{index}]", "expected non-empty string"))
            continue
        if allowed is not None and item not in allowed:
            allowed_list = ", ".join(sorted(allowed))
            issues.append(ValidationIssue(f"{path}[{index}]", f"expected one of: {allowed_list}"))


def _require_bool_map(
    data: Mapping[str, Any],
    path: str,
    issues: list[ValidationIssue],
    *,
    require_non_empty: bool = False,
) -> None:
    value = data.get(path)
    if not isinstance(value, Mapping):
        issues.append(ValidationIssue(path, "expected object"))
        return
    if require_non_empty and not value:
        issues.append(ValidationIssue(path, "expected a non-empty object"))
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            issues.append(ValidationIssue(f"{path}.{key}", "expected non-empty string key"))
        if not isinstance(item, bool):
            issues.append(ValidationIssue(f"{path}.{key}", "expected boolean"))


def _require_bool_or_int_map(
    data: Mapping[str, Any],
    path: str,
    issues: list[ValidationIssue],
    *,
    require_non_empty: bool = False,
) -> None:
    value = data.get(path)
    if not isinstance(value, Mapping):
        issues.append(ValidationIssue(path, "expected object"))
        return
    if require_non_empty and not value:
        issues.append(ValidationIssue(path, "expected a non-empty object"))
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            issues.append(ValidationIssue(f"{path}.{key}", "expected non-empty string key"))
        if not isinstance(item, (bool, int)):
            issues.append(ValidationIssue(f"{path}.{key}", "expected boolean or integer"))
