from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from spark_intelligence.config.loader import ConfigManager


@dataclass
class AttachmentRecord:
    kind: str
    key: str
    label: str
    repo_root: str
    manifest_path: str
    hook_manifest_path: str | None
    schema_version: str | None
    io_protocol: str | None
    status: str
    source: str
    capabilities: list[str]
    commands: dict[str, list[str]]
    description: str | None
    frontier: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "label": self.label,
            "repo_root": self.repo_root,
            "manifest_path": self.manifest_path,
            "hook_manifest_path": self.hook_manifest_path,
            "schema_version": self.schema_version,
            "io_protocol": self.io_protocol,
            "status": self.status,
            "source": self.source,
            "capabilities": self.capabilities,
            "commands": self.commands,
            "description": self.description,
            "frontier": self.frontier,
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
    normalized = str(Path(root).expanduser())
    values = [str(Path(item).expanduser()) for item in existing if str(item).strip()]
    if normalized not in values:
        values.append(normalized)
        config_manager.set_path(dotted_path, values)
    return values


def _resolve_chip_roots(config_manager: ConfigManager) -> tuple[list[Path], str]:
    configured = config_manager.get_path("spark.chips.roots", default=[]) or []
    normalized = [Path(str(item)).expanduser() for item in configured if str(item).strip()]
    if normalized:
        return normalized, "configured"

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
        key = str(candidate.resolve())
        if key not in seen:
            seen.add(key)
            autodetected.append(candidate)
    return autodetected, "autodiscovered" if autodetected else "missing"


def _resolve_roots(config_manager: ConfigManager, dotted_path: str, default_glob: str) -> tuple[list[Path], str]:
    configured = config_manager.get_path(dotted_path, default=[]) or []
    normalized = [Path(str(item)).expanduser() for item in configured if str(item).strip()]
    if normalized:
        return normalized, "configured"
    desktop = Path.home() / "Desktop"
    autodetected = sorted(path for path in desktop.glob(default_glob) if path.is_dir())
    return autodetected, "autodiscovered" if autodetected else "missing"


def _scan_chip_roots(roots: list[Path], source: str, warnings: list[str]) -> list[AttachmentRecord]:
    records: list[AttachmentRecord] = []
    for repo_root in _expand_repo_roots(roots, manifest_name="spark-chip.json"):
        manifest_path = repo_root / "spark-chip.json"
        payload = _read_json_manifest(manifest_path, warnings)
        if not payload:
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
                schema_version=_normalize_optional_string(payload.get("schema_version")),
                io_protocol=_normalize_optional_string(payload.get("io_protocol")),
                status="available",
                source=source,
                capabilities=capabilities,
                commands=_normalize_commands(payload.get("commands")),
                description=str(payload.get("description") or "").strip() or None,
                frontier=payload.get("frontier") if isinstance(payload.get("frontier"), dict) else None,
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
                schema_version=_normalize_optional_string(hook_manifest.get("schema_version")),
                io_protocol=_normalize_optional_string(hook_manifest.get("io_protocol")),
                status="available",
                source=source,
                capabilities=capabilities,
                commands=_normalize_commands(hook_manifest.get("commands")),
                description=None,
                frontier=hook_manifest.get("frontier") if isinstance(hook_manifest.get("frontier"), dict) else None,
            )
        )
    return records


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
