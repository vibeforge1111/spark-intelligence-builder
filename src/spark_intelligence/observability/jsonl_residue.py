from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


@dataclass(frozen=True)
class JsonlResidueFile:
    path: str
    relative_path: str
    bytes: int
    modified_at: str | None
    classification: str
    recommendation: str


@dataclass(frozen=True)
class JsonlResidueReport:
    root: str
    total_files: int
    total_bytes: int
    reported_files: list[JsonlResidueFile]
    omitted_files: int
    min_bytes: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "reported_files": [file.__dict__ for file in self.reported_files],
            "reported_count": len(self.reported_files),
            "omitted_files": self.omitted_files,
            "min_bytes": self.min_bytes,
            "note": "Read-only size/classification report; no files were opened, moved, or deleted.",
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
) -> JsonlResidueReport:
    spark_root = infer_spark_root(config_manager, override=root).resolve()
    bounded_limit = max(1, min(int(limit), 500))
    bounded_min_bytes = max(0, int(min_bytes))
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
            files.append(
                JsonlResidueFile(
                    path=str(path),
                    relative_path=_relative_path(path, spark_root),
                    bytes=size,
                    modified_at=_timestamp(stat.st_mtime),
                    classification=_classify_jsonl_path(path, spark_root, config_manager),
                    recommendation=_recommendation(path, spark_root, config_manager),
                )
            )
    files.sort(key=lambda item: (-item.bytes, item.relative_path.lower()))
    reported = files[:bounded_limit]
    return JsonlResidueReport(
        root=str(spark_root),
        total_files=total_files,
        total_bytes=total_bytes,
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
    if classification == "root_unowned_jsonl":
        return "Archive or quarantine after confirming it is not active runtime input."
    if classification in {"legacy_runtime_river", "root_log_river"}:
        return "Treat as legacy evidence; archive by dated bundle before deleting."
    if classification in {"surface_state_jsonl", "module_local_jsonl"}:
        return "Coordinate with the owning surface before moving; prefer canonical ingestion first."
    return "Inspect ownership before moving or deleting."
