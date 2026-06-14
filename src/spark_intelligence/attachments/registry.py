from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from spark_intelligence.config.loader import ConfigManager

CHIP_MANIFEST_CURRENT_VERSION = 2
CHIP_MANIFEST_MIN_SUPPORTED_VERSION = CHIP_MANIFEST_CURRENT_VERSION - 1
CHIP_MANIFEST_SUPPORTED_VERSIONS = set(range(CHIP_MANIFEST_MIN_SUPPORTED_VERSION, CHIP_MANIFEST_CURRENT_VERSION + 1))
CHIP_COMPATIBILITY_UPGRADE_COMMAND = "spark chip upgrade"
CHIP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$")
TASK_TOPIC_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")


@dataclass
class AttachmentRecord:
    kind: str
    key: str
    label: str
    repo_root: str
    manifest_path: str
    hook_manifest_path: str | None
    manifest_version: int | None
    schema_version: str | None
    io_protocol: str | None
    status: str
    source: str
    capabilities: list[str]
    commands: dict[str, list[str]]
    description: str | None
    frontier: dict[str, Any] | None
    requires_runtime: dict[str, str]
    task_topics: list[str]
    task_keywords: list[str]
    combine_with: list[str]
    onboarding: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.label,
            "repo_root": self.repo_root,
            "manifest_path": self.manifest_path,
            "hook_manifest_path": self.hook_manifest_path,
            "manifest_version": self.manifest_version,
            "schema_version": self.schema_version,
            "io_protocol": self.io_protocol,
            "status": self.status,
            "source": self.source,
            "capabilities": self.capabilities,
            "commands": self.commands,
            "description": self.description,
            "frontier": self.frontier,
            "requires_runtime": self.requires_runtime,
            "task_topics": self.task_topics,
            "task_keywords": self.task_keywords,
            "combine_with": self.combine_with,
            "onboarding": self.onboarding,
        }


@dataclass
class AttachmentScanResult:
    chip_source: str
    path_source: str
    chip_roots: list[str]
    path_roots: list[str]
    records: list[AttachmentRecord]
    warnings: list[str]

    def to_json(self) -> str:
        return json.dumps(
            {
                "chip_source": self.chip_source,
                "path_source": self.path_source,
                "chip_roots": self.chip_roots,
                "path_roots": self.path_roots,
                "warnings": self.warnings,
                "records": [record.to_dict() for record in self.records],
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = ["Attachment scan"]
        lines.append(
            f"- chip_roots ({self.chip_source}): {', '.join(self.chip_roots) if self.chip_roots else 'none'}"
        )
        lines.append(
            f"- path_roots ({self.path_source}): {', '.join(self.path_roots) if self.path_roots else 'none'}"
        )
        lines.append(f"- records: {len(self.records)}")
        if self.warnings:
            lines.append(f"- warnings: {len(self.warnings)}")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        for record in self.records:
            lines.append(
                f"- [{record.kind}] {record.key} status={record.status} source={record.source} root={record.repo_root}"
            )
        return "\n".join(lines)


def attachment_status(config_manager: ConfigManager) -> AttachmentScanResult:
    chip_roots, chip_source = _resolve_chip_roots(config_manager)
    path_roots, path_source = _resolve_roots(config_manager, "spark.specialization_paths.roots", "specialization-path-*")
    records: list[AttachmentRecord] = []
    warnings: list[str] = []
    records.extend(_scan_chip_roots(chip_roots, chip_source, warnings))
    records.extend(_scan_path_roots(path_roots, path_source, warnings))
    records.sort(key=lambda item: (item.kind, item.key))
    _append_duplicate_warnings(records, warnings)
    return AttachmentScanResult(
        chip_source=chip_source,
        path_source=path_source,
        chip_roots=[str(path) for path in chip_roots],
        path_roots=[str(path) for path in path_roots],
        records=records,
        warnings=warnings,
    )


def list_attachments(config_manager: ConfigManager, *, kind: str = "all") -> AttachmentScanResult:
    result = attachment_status(config_manager)
    if kind == "all":
        return result
    return AttachmentScanResult(
        chip_source=result.chip_source,
        path_source=result.path_source,
        chip_roots=result.chip_roots,
        path_roots=result.path_roots,
        warnings=result.warnings,
        records=[record for record in result.records if record.kind == kind],
    )


def add_attachment_root(config_manager: ConfigManager, *, target: str, root: str) -> list[str]:
    dotted_path = "spark.chips.roots" if target == "chips" else "spark.specialization_paths.roots"
    existing = config_manager.get_path(dotted_path, default=[]) or []
    normalized_path = config_manager.normalize_runtime_path(root)
    normalized = str(normalized_path or Path(root).expanduser())
    values = [
        str(config_manager.normalize_runtime_path(item) or Path(str(item)).expanduser())
        for item in existing
        if str(item).strip()
    ]
    if normalized not in values:
        values.append(normalized)
        config_manager.set_path(dotted_path, values)
    return values


def _resolve_chip_roots(config_manager: ConfigManager) -> tuple[list[Path], str]:
    ignored_roots = _resolve_ignored_roots(config_manager, "spark.chips.ignored_roots")
    configured = config_manager.get_path("spark.chips.roots", default=[]) or []
    normalized = [
        config_manager.normalize_runtime_path(item) or Path(str(item)).expanduser()
        for item in configured
        if str(item).strip()
    ]
    if normalized:
        return _filter_ignored_roots(normalized, ignored_roots), "configured"

    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        return [], "missing"

    autodetected: list[Path] = []
    seen: set[str] = set()
    candidates = list(desktop.glob("domain-chip-*"))
    candidates.extend(
        path
        for path in desktop.iterdir()
        if path.is_dir() and (path / "spark-chip.json").exists()
    )
    for candidate in sorted(candidates):
        if _is_ignored_root(candidate, ignored_roots):
            continue
        key = str(candidate.resolve())
        if key not in seen:
            seen.add(key)
            autodetected.append(candidate)
    return autodetected, "autodiscovered" if autodetected else "missing"


def _resolve_roots(config_manager: ConfigManager, dotted_path: str, default_glob: str) -> tuple[list[Path], str]:
    ignored_roots = _resolve_ignored_roots(config_manager, dotted_path.replace(".roots", ".ignored_roots"))
    configured = config_manager.get_path(dotted_path, default=[]) or []
    normalized = [
        config_manager.normalize_runtime_path(item) or Path(str(item)).expanduser()
        for item in configured
        if str(item).strip()
    ]
    if normalized:
        return _filter_ignored_roots(normalized, ignored_roots), "configured"
    desktop = Path.home() / "Desktop"
    autodetected = sorted(
        path for path in desktop.glob(default_glob) if path.is_dir() and not _is_ignored_root(path, ignored_roots)
    )
    return autodetected, "autodiscovered" if autodetected else "missing"


def _resolve_ignored_roots(config_manager: ConfigManager, dotted_path: str) -> set[str]:
    configured = config_manager.get_path(dotted_path, default=[]) or []
    ignored: set[str] = set()
    for item in configured:
        if not str(item).strip():
            continue
        path = config_manager.normalize_runtime_path(item) or Path(str(item)).expanduser()
        ignored.add(_root_key(path))
    return ignored


def _filter_ignored_roots(roots: list[Path], ignored_roots: set[str]) -> list[Path]:
    if not ignored_roots:
        return roots
    return [root for root in roots if not _is_ignored_root(root, ignored_roots)]


def _is_ignored_root(root: Path, ignored_roots: set[str]) -> bool:
    return _root_key(root) in ignored_roots


def _root_key(root: Path) -> str:
    return str(root.expanduser().resolve(strict=False)).casefold()


def _scan_chip_roots(roots: list[Path], source: str, warnings: list[str]) -> list[AttachmentRecord]:
    records: list[AttachmentRecord] = []
    for repo_root in _expand_repo_roots(roots, manifest_name="spark-chip.json"):
        manifest_path = repo_root / "spark-chip.json"
        payload = _read_json_manifest(manifest_path, warnings)
        if not payload:
            continue
        manifest_issues = _chip_manifest_issues(payload)
        if manifest_issues:
            _append_chip_manifest_warning(manifest_path, manifest_issues, warnings)
            continue
        if (repo_root / "specialization-path.json").exists():
            continue
        chip_name = str(payload.get("chip_name") or payload.get("domain") or repo_root.name).strip()
        capabilities = [str(item) for item in payload.get("capabilities", []) if str(item).strip()]
        records.append(
            AttachmentRecord(
                kind="chip",
                key=chip_name,
                label=str(payload.get("description") or chip_name),
                repo_root=str(repo_root),
                manifest_path=str(manifest_path),
                hook_manifest_path=None,
                manifest_version=_chip_manifest_version(payload),
                schema_version=_normalize_optional_string(payload.get("schema_version")),
                io_protocol=_normalize_optional_string(payload.get("io_protocol")),
                status="available",
                source=source,
                capabilities=capabilities,
                commands=_normalize_commands(payload.get("commands")),
                description=str(payload.get("description") or "").strip() or None,
                frontier=payload.get("frontier") if isinstance(payload.get("frontier"), dict) else None,
                requires_runtime=_normalize_runtime_requirements(payload.get("requires_runtime")),
                task_topics=_normalize_string_list(payload.get("task_topics")),
                task_keywords=_normalize_string_list(payload.get("task_keywords")),
                combine_with=_normalize_string_list(payload.get("combine_with")),
                onboarding=_normalize_onboarding(payload.get("onboarding")),
            )
        )
    return records


def _scan_path_roots(roots: list[Path], source: str, warnings: list[str]) -> list[AttachmentRecord]:
    records: list[AttachmentRecord] = []
    for repo_root in _expand_repo_roots(roots, manifest_name="specialization-path.json"):
        manifest_path = repo_root / "specialization-path.json"
        payload = _read_json_manifest(manifest_path, warnings)
        if not payload:
            continue
        hook_manifest_path = repo_root / "spark-chip.json"
        hook_manifest = _read_json_manifest(hook_manifest_path, warnings) if hook_manifest_path.exists() else {}
        if hook_manifest:
            manifest_issues = _chip_manifest_issues(hook_manifest)
            if manifest_issues:
                _append_chip_manifest_warning(hook_manifest_path, manifest_issues, warnings)
                continue
        path_key = str(payload.get("pathKey") or repo_root.name).strip()
        capabilities = [str(item) for item in hook_manifest.get("capabilities", []) if str(item).strip()]
        label = path_key
        benchmark_profile = payload.get("benchmarkProfile")
        if isinstance(benchmark_profile, dict):
            default_scenario = str(benchmark_profile.get("defaultScenario") or "").strip()
            if default_scenario:
                label = f"{path_key} ({default_scenario})"
        records.append(
            AttachmentRecord(
                kind="path",
                key=path_key,
                label=label,
                repo_root=str(repo_root),
                manifest_path=str(manifest_path),
                hook_manifest_path=str(hook_manifest_path) if hook_manifest_path.exists() else None,
                manifest_version=_chip_manifest_version(hook_manifest) if hook_manifest else None,
                schema_version=_normalize_optional_string(hook_manifest.get("schema_version")),
                io_protocol=_normalize_optional_string(hook_manifest.get("io_protocol")),
                status="available",
                source=source,
                capabilities=capabilities,
                commands=_normalize_commands(hook_manifest.get("commands")),
                description=None,
                frontier=hook_manifest.get("frontier") if isinstance(hook_manifest.get("frontier"), dict) else None,
                requires_runtime=_normalize_runtime_requirements(hook_manifest.get("requires_runtime")),
                task_topics=_normalize_string_list(hook_manifest.get("task_topics")),
                task_keywords=_normalize_string_list(hook_manifest.get("task_keywords")),
                combine_with=_normalize_string_list(hook_manifest.get("combine_with")),
                onboarding=_normalize_onboarding(hook_manifest.get("onboarding") or payload.get("onboarding")),
            )
        )
    return records


def _normalize_onboarding(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, Any] = {}
    role = _normalize_optional_string(value.get("role"))
    if role:
        normalized["role"] = role
    for field in ("surfaces", "permissions", "harnesses", "health_checks", "example_intents", "limitations"):
        normalized_items: list[str] = []
        raw_items = value.get(field)
        for item in raw_items if isinstance(raw_items, list) else []:
            normalized_item = str(item).strip()
            if normalized_item and normalized_item not in normalized_items:
                normalized_items.append(normalized_item)
        if normalized_items:
            normalized[field] = normalized_items
    return normalized or None


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _normalize_runtime_requirements(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(runtime).strip(): str(requirement).strip()
        for runtime, requirement in value.items()
        if str(runtime).strip() and str(requirement).strip()
    }


def _chip_manifest_version(payload: dict[str, Any]) -> int | None:
    raw = payload.get("manifest_version")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    schema_version = _normalize_optional_string(payload.get("schema_version"))
    if schema_version:
        match = re.fullmatch(r"spark-chip\.v(\d+)", schema_version)
        if match:
            return int(match.group(1))
    return 1


def _chip_manifest_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    version = _chip_manifest_version(payload)
    raw_version = payload.get("manifest_version")
    if raw_version is not None and (version is None or not isinstance(raw_version, int) or isinstance(raw_version, bool)):
        issues.append("manifest_version must be a positive integer")
    if version is None or version < 1:
        issues.append("manifest_version must be a positive integer")
        return issues
    schema_version = _normalize_optional_string(payload.get("schema_version"))
    if schema_version and schema_version != f"spark-chip.v{version}":
        issues.append(f"schema_version must be spark-chip.v{version}")
    if version not in CHIP_MANIFEST_SUPPORTED_VERSIONS:
        supported = ", ".join(str(item) for item in sorted(CHIP_MANIFEST_SUPPORTED_VERSIONS))
        issues.append(f"chip manifest_version {version} is unsupported; supported versions: {supported}")
        return issues
    if version == 2:
        issues.extend(_chip_v2_manifest_issues(payload))
    return issues


def _chip_v2_manifest_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key in (
        "manifest_version",
        "schema_version",
        "io_protocol",
        "chip_name",
        "version",
        "domain",
        "description",
        "requires_runtime",
        "capabilities",
        "commands",
        "task_topics",
        "task_keywords",
    ):
        if key not in payload:
            issues.append(f"missing {key}")
    if payload.get("manifest_version") != 2:
        issues.append("manifest_version must be 2")
    if payload.get("schema_version") != "spark-chip.v2":
        issues.append("schema_version must be spark-chip.v2")
    if payload.get("io_protocol") != "spark-hook-io.v1":
        issues.append("io_protocol must be spark-hook-io.v1")
    chip_name = str(payload.get("chip_name") or "")
    if not CHIP_NAME_PATTERN.fullmatch(chip_name):
        issues.append("chip_name must be router-safe kebab-case")
    if not SEMVER_PATTERN.fullmatch(str(payload.get("version") or "")):
        issues.append("version must be semver")
    for key in ("domain", "description"):
        if not str(payload.get(key) or "").strip():
            issues.append(f"{key} must be non-empty")
    if not _normalize_runtime_requirements(payload.get("requires_runtime")):
        issues.append("requires_runtime must name at least one runtime")
    if not _normalize_string_list(payload.get("capabilities")):
        issues.append("capabilities must be a non-empty string list")
    if not _normalize_commands(payload.get("commands")):
        issues.append("commands must use non-empty argv arrays")
    topics = _normalize_string_list(payload.get("task_topics"))
    if not topics or any(not TASK_TOPIC_PATTERN.fullmatch(topic) for topic in topics):
        issues.append("task_topics must be non-empty router topics")
    if not _normalize_string_list(payload.get("task_keywords")):
        issues.append("task_keywords must be a non-empty string list")
    return issues


def _append_chip_manifest_warning(path: Path, issues: list[str], warnings: list[str]) -> None:
    detail = "; ".join(issues)
    warnings.append(f"unsupported chip manifest: {path} ({detail}). Run `{CHIP_COMPATIBILITY_UPGRADE_COMMAND}`.")


def _expand_repo_roots(roots: Iterable[Path], *, manifest_name: str) -> list[Path]:
    discovered: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        expanded_root = root.expanduser()
        candidates: list[Path]
        if (expanded_root / manifest_name).exists():
            candidates = [expanded_root]
        else:
            candidates = [path for path in expanded_root.iterdir() if path.is_dir() and (path / manifest_name).exists()] if expanded_root.exists() else []
        for candidate in candidates:
            key = str(candidate.resolve())
            if key not in seen:
                seen.add(key)
                discovered.append(candidate)
    return discovered


def _read_json_manifest(path: Path, warnings: list[str]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        warnings.append(f"missing manifest: {path}")
        return {}
    except json.JSONDecodeError as exc:
        warnings.append(f"invalid json manifest: {path} ({exc})")
        return {}
    if not isinstance(data, dict):
        warnings.append(f"unexpected manifest shape: {path}")
        return {}
    return data


def _append_duplicate_warnings(records: list[AttachmentRecord], warnings: list[str]) -> None:
    seen: dict[tuple[str, str], list[str]] = {}
    for record in records:
        key = (record.kind, record.key)
        seen.setdefault(key, []).append(record.repo_root)
    for (kind, key), roots in sorted(seen.items()):
        if len(roots) > 1:
            warnings.append(f"duplicate {kind} key '{key}' found in: {', '.join(roots)}")


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_commands(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for hook, command in value.items():
        hook_name = str(hook).strip()
        if not hook_name or not isinstance(command, list):
            continue
        parts = [str(item).strip() for item in command if str(item).strip()]
        if parts:
            normalized[hook_name] = parts
    return normalized
