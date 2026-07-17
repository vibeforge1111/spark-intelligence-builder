from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from spark_intelligence.config.loader import ConfigManager


def spark_module_roots(config_manager: ConfigManager | None = None) -> list[Path]:
    roots: list[Path] = []
    if config_manager is not None:
        configured = config_manager.get_path("spark.local_projects.module_roots", default=[]) or []
        for item in configured:
            path = config_manager.normalize_runtime_path(item)
            if path is not None:
                roots.append(path)
        roots.append(config_manager.paths.home / ".spark" / "modules")
        for parent in [config_manager.paths.home, *config_manager.paths.home.parents]:
            if parent.name == ".spark":
                roots.append(parent / "modules")
                break
    roots.extend(home / "modules" for home in spark_home_candidates())
    return _dedupe_paths(roots)


def installed_module_source_candidates(
    module_name: str,
    *,
    config_manager: ConfigManager | None = None,
) -> list[Path]:
    normalized = str(module_name or "").strip()
    if not normalized or Path(normalized).name != normalized:
        return []
    candidates: list[Path] = []
    for modules_root in spark_module_roots(config_manager):
        module_root = modules_root / normalized
        candidates.extend((module_root / "source", module_root))
    return [path for path in _dedupe_paths(candidates) if path.is_dir()]


def resolve_installed_module_source(
    module_name: str,
    *,
    config_manager: ConfigManager | None = None,
) -> Path | None:
    candidates = installed_module_source_candidates(module_name, config_manager=config_manager)
    return candidates[0] if candidates else None


def installed_chip_parent_candidates() -> list[Path]:
    return [path for path in _dedupe_paths(home / "chips" for home in spark_home_candidates()) if path.is_dir()]


def spark_home_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = str(os.environ.get("SPARK_HOME") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path.home() / ".spark")
    return _dedupe_paths(candidates)


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        expanded = path.expanduser()
        try:
            canonical = expanded.resolve(strict=False)
        except OSError:
            canonical = expanded
        key = str(canonical).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(canonical)
    return deduped
