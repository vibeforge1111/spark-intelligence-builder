from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager

_REFERENCE_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    "__pycache__",
    "archive",
    "dist",
    "logs",
    "node_modules",
    "quarantine",
    "state",
}
_REFERENCE_TEXT_SUFFIXES = {
    ".cmd",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svelte",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
_REFERENCE_TEXT_NAMES = {"Dockerfile", "Makefile"}
_REFERENCE_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class JsonlResidueFile:
    path: str
    relative_path: str
    bytes: int
    modified_at: str | None
    classification: str
    manifest_action: str
    movement_blocker: str | None
    requires_reference_scan: bool
    requires_owner_signoff: bool
    archive_before_quarantine: bool
    delete_allowed: bool
    reference_scan: dict[str, Any] | None
    recommendation: str


@dataclass(frozen=True)
class JsonlResidueReport:
    root: str
    total_files: int
    total_bytes: int
    candidate_files: int
    below_min_bytes_files: int
    candidate_classification_counts: dict[str, int]
    candidate_manifest_action_counts: dict[str, int]
    candidate_movement_blocker_counts: dict[str, int]
    candidate_reference_scan_status_counts: dict[str, int]
    reference_scan_enabled: bool
    reference_scan_roots: list[str]
    reported_files: list[JsonlResidueFile]
    omitted_files: int
    min_bytes: int

    def to_payload(self) -> dict[str, Any]:
        note = "Read-only size/classification report; JSONL files were not opened, moved, or deleted."
        if self.reference_scan_enabled:
            note = (
                "Read-only size/classification report; reference scan opened non-JSONL text files only. "
                "JSONL files were not opened, moved, or deleted."
            )
        return {
            "root": self.root,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "candidate_files": self.candidate_files,
            "below_min_bytes_files": self.below_min_bytes_files,
            "candidate_classification_counts": self.candidate_classification_counts,
            "candidate_manifest_action_counts": self.candidate_manifest_action_counts,
            "candidate_movement_blocker_counts": self.candidate_movement_blocker_counts,
            "candidate_reference_scan_status_counts": self.candidate_reference_scan_status_counts,
            "reference_scan_enabled": self.reference_scan_enabled,
            "reference_scan_roots": self.reference_scan_roots,
            "reported_files": [file.__dict__ for file in self.reported_files],
            "reported_count": len(self.reported_files),
            "omitted_files": self.omitted_files,
            "min_bytes": self.min_bytes,
            "note": note,
        }


def infer_spark_root(config_manager: ConfigManager, *, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    env_home = str(os.environ.get("SPARK_HOME") or "").strip()
    if env_home:
        return Path(env_home).expanduser()
    home = config_manager.paths.home.resolve()
    parts = [part.lower() for part in home.parts]
    if ".spark" in parts:
        index = parts.index(".spark")
        return Path(*home.parts[: index + 1])
    return home


def build_jsonl_residue_report(
    config_manager: ConfigManager,
    *,
    root: str | None = None,
    limit: int = 40,
    min_bytes: int = 0,
    reference_scan: bool = False,
    reference_roots: list[str] | None = None,
) -> JsonlResidueReport:
    spark_root = infer_spark_root(config_manager, override=root).resolve()
    bounded_limit = max(1, min(int(limit), 500))
    bounded_min_bytes = max(0, int(min_bytes))
    reference_root_paths = _reference_roots(spark_root, config_manager, configured=reference_roots)
    reference_index = _build_reference_index(reference_root_paths) if reference_scan else []
    files: list[JsonlResidueFile] = []
    total_files = 0
    total_bytes = 0
    if spark_root.exists():
        for path in spark_root.rglob("*.jsonl"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            size = int(stat.st_size)
            total_files += 1
            total_bytes += size
            if size < bounded_min_bytes:
                continue
            manifest_policy = _manifest_policy(path, spark_root, config_manager)
            file_reference_scan = (
                _reference_scan_payload(
                    path,
                    spark_root,
                    reference_index,
                    required=bool(manifest_policy["requires_reference_scan"]),
                )
                if reference_scan
                else None
            )
            files.append(
                JsonlResidueFile(
                    path=str(path),
                    relative_path=_relative_path(path, spark_root),
                    bytes=size,
                    modified_at=_timestamp(stat.st_mtime),
                    **manifest_policy,
                    reference_scan=file_reference_scan,
                    recommendation=_recommendation(path, spark_root, config_manager),
                )
            )
    files.sort(key=lambda item: (-item.bytes, item.relative_path.lower()))
    reported = files[:bounded_limit]
    return JsonlResidueReport(
        root=str(spark_root),
        total_files=total_files,
        total_bytes=total_bytes,
        candidate_files=len(files),
        below_min_bytes_files=max(total_files - len(files), 0),
        candidate_classification_counts=_count_by(files, "classification"),
        candidate_manifest_action_counts=_count_by(files, "manifest_action"),
        candidate_movement_blocker_counts=_count_by(files, "movement_blocker", none_label="none"),
        candidate_reference_scan_status_counts=_reference_scan_status_counts(files),
        reference_scan_enabled=reference_scan,
        reference_scan_roots=[str(path) for path in reference_root_paths] if reference_scan else [],
        reported_files=reported,
        omitted_files=max(len(files) - len(reported), 0),
        min_bytes=bounded_min_bytes,
    )


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _timestamp(value: float) -> str | None:
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def _count_by(files: list[JsonlResidueFile], attribute: str, *, none_label: str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for file in files:
        value = getattr(file, attribute)
        if value is None:
            if none_label is None:
                continue
            value = none_label
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _reference_scan_status_counts(files: list[JsonlResidueFile]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for file in files:
        if file.reference_scan is None:
            continue
        status = str(file.reference_scan.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _reference_roots(spark_root: Path, config_manager: ConfigManager, *, configured: list[str] | None) -> list[Path]:
    raw_roots = [Path(item).expanduser() for item in configured or []]
    if not raw_roots:
        raw_roots = [
            Path(__file__).resolve().parents[3],
            spark_root / "config",
            spark_root / "modules",
            spark_root / "tools",
        ]
    roots: list[Path] = []
    config_home = config_manager.paths.home.resolve()
    for root in raw_roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if not resolved.exists() or not resolved.is_dir():
            continue
        if resolved == config_home or _path_is_relative_to(resolved, config_home):
            continue
        _append_reference_root(roots, resolved)
    return roots


def _append_reference_root(roots: list[Path], resolved: Path) -> None:
    for existing in roots:
        if resolved == existing or _path_is_relative_to(resolved, existing):
            return
    roots[:] = [existing for existing in roots if not _path_is_relative_to(existing, resolved)]
    roots.append(resolved)


def _build_reference_index(roots: list[Path]) -> list[tuple[Path, str]]:
    index: list[tuple[Path, str]] = []
    for root in roots:
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in _REFERENCE_SKIP_DIRS]
            current_path = Path(current)
            for filename in filenames:
                path = current_path / filename
                if not _is_reference_text_file(path):
                    continue
                try:
                    if path.stat().st_size > _REFERENCE_MAX_BYTES:
                        continue
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                index.append((path, text))
    return index


def _is_reference_text_file(path: Path) -> bool:
    if path.name in _REFERENCE_TEXT_NAMES:
        return True
    return path.suffix.lower() in _REFERENCE_TEXT_SUFFIXES


def _reference_scan_payload(
    path: Path,
    root: Path,
    reference_index: list[tuple[Path, str]],
    *,
    required: bool,
) -> dict[str, Any]:
    if not required:
        return {"status": "not_required", "patterns": [], "matches": [], "match_count": 0}
    patterns = _reference_patterns(path, root)
    matches: list[dict[str, Any]] = []
    match_count = 0
    for reference_path, text in reference_index:
        matched_patterns = [pattern for pattern in patterns if pattern and pattern in text]
        if not matched_patterns:
            continue
        match_count += 1
        if len(matches) < 25:
            matches.append({"path": str(reference_path), "patterns": matched_patterns[:5]})
    return {
        "status": "matches_found" if match_count else "no_matches",
        "patterns": patterns,
        "matches": matches,
        "match_count": match_count,
    }


def _reference_patterns(path: Path, root: Path) -> list[str]:
    relative = _relative_path(path, root)
    forward_relative = relative.replace("\\", "/")
    patterns = [relative, forward_relative]
    if len(Path(relative).parts) == 1:
        patterns.append(path.name)
    try:
        patterns.extend([str(path.resolve()), path.resolve().as_posix()])
    except OSError:
        pass
    deduped: list[str] = []
    for pattern in patterns:
        if pattern and pattern not in deduped:
            deduped.append(pattern)
    return deduped


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _classify_jsonl_path(path: Path, root: Path, config_manager: ConfigManager) -> str:
    relative = Path(_relative_path(path, root))
    parts = [part.lower() for part in relative.parts]
    try:
        if path.resolve().is_relative_to(config_manager.paths.logs_dir.resolve()):
            return "builder_gateway_log" if path.name.startswith("gateway-") else "builder_local_log"
    except OSError:
        pass
    if len(relative.parts) == 1:
        return "root_unowned_jsonl"
    if parts[:1] in (["recursion"], ["recursive_research"], ["advisor"], ["queue"], ["advisory_quarantine"]):
        return "legacy_runtime_river"
    if parts[:1] == ["logs"]:
        return "root_log_river"
    if parts[:1] == ["state"]:
        return "surface_state_jsonl"
    if parts[:1] == ["modules"]:
        return "module_local_jsonl"
    return "unclassified_jsonl"


def _recommendation(path: Path, root: Path, config_manager: ConfigManager) -> str:
    classification = _classify_jsonl_path(path, root, config_manager)
    if classification == "builder_gateway_log":
        return "Covered by gateway JSONL report/prune; do not quarantine separately."
    if classification == "builder_local_log":
        return "Builder-owned local log; coordinate with Builder retention before moving."
    if classification == "root_unowned_jsonl":
        return "Freeze until reference scan or owner signoff proves it is not active runtime input."
    if classification in {"legacy_runtime_river", "root_log_river"}:
        return "Treat as legacy evidence; archive by dated bundle before quarantine or retention deletion."
    if classification in {"surface_state_jsonl", "module_local_jsonl"}:
        return "Coordinate with the owning surface before moving; prefer canonical ingestion first."
    return "Inspect ownership before moving or deleting."


def _manifest_policy(path: Path, root: Path, config_manager: ConfigManager) -> dict[str, Any]:
    classification = _classify_jsonl_path(path, root, config_manager)
    if classification == "builder_gateway_log":
        return {
            "classification": classification,
            "manifest_action": "canonical_retention_path",
            "movement_blocker": "owned_by_gateway_retention",
            "requires_reference_scan": False,
            "requires_owner_signoff": False,
            "archive_before_quarantine": False,
            "delete_allowed": False,
        }
    if classification == "builder_local_log":
        return {
            "classification": classification,
            "manifest_action": "owner_required",
            "movement_blocker": "builder_retention_owner_required",
            "requires_reference_scan": True,
            "requires_owner_signoff": True,
            "archive_before_quarantine": True,
            "delete_allowed": False,
        }
    if classification == "root_unowned_jsonl":
        return {
            "classification": classification,
            "manifest_action": "freeze_pending_reference_scan",
            "movement_blocker": "reference_scan_or_owner_signoff_required",
            "requires_reference_scan": True,
            "requires_owner_signoff": True,
            "archive_before_quarantine": True,
            "delete_allowed": False,
        }
    if classification in {"legacy_runtime_river", "root_log_river"}:
        return {
            "classification": classification,
            "manifest_action": "archive_candidate",
            "movement_blocker": None,
            "requires_reference_scan": True,
            "requires_owner_signoff": False,
            "archive_before_quarantine": True,
            "delete_allowed": False,
        }
    if classification in {"surface_state_jsonl", "module_local_jsonl"}:
        return {
            "classification": classification,
            "manifest_action": "owner_required",
            "movement_blocker": "owning_surface_signoff_required",
            "requires_reference_scan": True,
            "requires_owner_signoff": True,
            "archive_before_quarantine": True,
            "delete_allowed": False,
        }
    return {
        "classification": classification,
        "manifest_action": "inspect_owner_first",
        "movement_blocker": "unclassified_owner_unknown",
        "requires_reference_scan": True,
        "requires_owner_signoff": True,
        "archive_before_quarantine": True,
        "delete_allowed": False,
    }
