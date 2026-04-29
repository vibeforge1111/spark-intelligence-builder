from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import build_attachment_context
from spark_intelligence.config.loader import ConfigManager


_KNOWN_SPARK_REPOS: dict[str, tuple[str, ...]] = {
    "spark-intelligence-builder": ("builder_runtime", "memory_kernel", "telegram_runtime"),
    "domain-chip-memory": ("memory_chip", "memory_eval_harness", "sidecar_contracts"),
    "spawner-ui": ("spawner_ui", "mission_control", "kanban", "operator_dashboard"),
    "spark-telegram-bot": ("telegram_gateway", "bot_runtime"),
    "spark-cli": ("installer_cli", "local_operator_cli"),
}


@dataclass(frozen=True)
class LocalProjectRecord:
    key: str
    label: str
    path: str
    exists: bool
    is_git: bool
    dirty: bool | None
    branch: str | None
    remote_url: str | None
    source: str
    owner_system: str
    components: list[str]
    capabilities: list[str]
    aliases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "path": self.path,
            "exists": self.exists,
            "is_git": self.is_git,
            "dirty": self.dirty,
            "branch": self.branch,
            "remote_url": self.remote_url,
            "source": self.source,
            "owner_system": self.owner_system,
            "components": self.components,
            "capabilities": self.capabilities,
            "aliases": self.aliases,
        }


@dataclass(frozen=True)
class LocalProjectIndex:
    records: list[LocalProjectRecord]

    def to_payload(self) -> dict[str, Any]:
        return {
            "record_count": len(self.records),
            "records": [record.to_dict() for record in self.records],
            "summary": {
                "known_repo_count": len(self.records),
                "git_repo_count": len([record for record in self.records if record.is_git]),
                "dirty_repo_count": len([record for record in self.records if record.dirty is True]),
                "known_repo_keys": [record.key for record in self.records],
            },
        }


def build_local_project_index(config_manager: ConfigManager, *, probe_git: bool = True) -> LocalProjectIndex:
    candidates = _candidate_project_roots(config_manager)
    records = [
        _build_project_record(path=path, source=source, config_record=config_record, probe_git=probe_git)
        for path, source, config_record in candidates
    ]
    records.sort(key=lambda record: (not record.exists, record.key))
    return LocalProjectIndex(records=records)


def _candidate_project_roots(config_manager: ConfigManager) -> list[tuple[Path, str, dict[str, Any]]]:
    candidates: list[tuple[Path, str, dict[str, Any]]] = []
    seen: set[str] = set()
    seen_slugs: set[str] = set()

    def add(path_value: Any, source: str, config_record: dict[str, Any] | None = None) -> None:
        record = config_record or {}
        path = ConfigManager.normalize_runtime_path(path_value)
        if path is None:
            return
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            resolved = path.expanduser()
        key = str(resolved).casefold()
        if key in seen:
            return
        slug = _slug(str(record.get("key") or resolved.name or "project"))
        if slug in seen_slugs:
            return
        seen.add(key)
        seen_slugs.add(slug)
        candidates.append((resolved, source, record))

    configured_records = config_manager.get_path("spark.local_projects.records", default={}) or {}
    if isinstance(configured_records, dict):
        for key, record in configured_records.items():
            if not isinstance(record, dict):
                continue
            config_record = dict(record)
            config_record.setdefault("key", str(key))
            add(record.get("path") or record.get("repo_root"), "config_record", config_record)

    for item in list(config_manager.get_path("spark.local_projects.roots", default=[]) or []):
        add(item, "config_root")

    try:
        attachment_context = build_attachment_context(config_manager)
    except Exception:
        attachment_context = {}
    for item in list(attachment_context.get("attached_chip_records") or []) + list(
        attachment_context.get("attached_path_records") or []
    ):
        if isinstance(item, dict):
            add(item.get("repo_root"), "attachment", item)

    current_repo = Path(__file__).resolve().parents[2]
    add(current_repo, "builder_runtime")
    parent = current_repo.parent
    for repo_name in _KNOWN_SPARK_REPOS:
        add(parent / repo_name, "known_spark_repo")
    desktop = Path.home() / "Desktop"
    if desktop != parent:
        for repo_name in _KNOWN_SPARK_REPOS:
            add(desktop / repo_name, "known_desktop_repo")
    return candidates


def _build_project_record(
    *,
    path: Path,
    source: str,
    config_record: dict[str, Any],
    probe_git: bool,
) -> LocalProjectRecord:
    exists = path.exists()
    name = str(config_record.get("key") or path.name or "project").strip()
    key = _slug(name)
    label = str(config_record.get("label") or path.name or key).strip()
    is_git = exists and (path / ".git").exists()
    git_info = _git_info(path) if probe_git and is_git else {}
    components = _components_for(path=path, config_record=config_record)
    capabilities = _capabilities_for(path=path, components=components, config_record=config_record)
    aliases = _aliases_for(key=key, path=path, config_record=config_record)
    owner_system = str(config_record.get("owner_system") or _owner_system_for(key)).strip()
    return LocalProjectRecord(
        key=key,
        label=label,
        path=str(path),
        exists=exists,
        is_git=is_git,
        dirty=git_info.get("dirty") if probe_git and is_git else None,
        branch=git_info.get("branch") if probe_git and is_git else None,
        remote_url=git_info.get("remote_url") if probe_git and is_git else None,
        source=source,
        owner_system=owner_system,
        components=components,
        capabilities=capabilities,
        aliases=aliases,
    )


def _git_info(path: Path) -> dict[str, Any]:
    timed_out = False

    def run_git(args: list[str]) -> str:
        nonlocal timed_out
        try:
            completed = subprocess.run(
                ["git", "-C", str(path), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            return ""
        except OSError:
            return ""
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "").strip()

    status = run_git(["status", "--short"])
    branch = run_git(["branch", "--show-current"]) or None
    remote_url = run_git(["remote", "get-url", "origin"]) or None
    return {"dirty": None if timed_out else bool(status), "branch": branch, "remote_url": remote_url}


def _components_for(*, path: Path, config_record: dict[str, Any]) -> list[str]:
    configured = [str(item).strip() for item in (config_record.get("components") or []) if str(item).strip()]
    components = list(configured)
    components.extend(_KNOWN_SPARK_REPOS.get(path.name, ()))
    if (path / "spark-chip.json").exists():
        components.append("spark_chip")
    if (path / "specialization-path.json").exists():
        components.append("specialization_path")
    if (path / "package.json").exists():
        components.append("node_app")
    if (path / "pyproject.toml").exists():
        components.append("python_package")
    if (path / "src").exists():
        components.append("source_tree")
    if (path / "tests").exists():
        components.append("test_suite")
    return _dedupe(components)


def _capabilities_for(*, path: Path, components: list[str], config_record: dict[str, Any]) -> list[str]:
    capabilities = [str(item).strip() for item in (config_record.get("capabilities") or []) if str(item).strip()]
    component_set = set(components)
    if "node_app" in component_set:
        capabilities.append("frontend_or_node_runtime")
    if "python_package" in component_set:
        capabilities.append("python_runtime")
    if "test_suite" in component_set:
        capabilities.append("local_tests")
    if "spawner_ui" in component_set:
        capabilities.extend(["spawner_routes", "operator_ui"])
    if "builder_runtime" in component_set:
        capabilities.extend(["memory_runtime", "telegram_runtime", "context_capsule"])
    if "memory_chip" in component_set:
        capabilities.extend(["memory_evaluation", "sidecar_contracts"])
    return _dedupe(capabilities)


def _aliases_for(*, key: str, path: Path, config_record: dict[str, Any]) -> list[str]:
    aliases = [str(item).strip() for item in (config_record.get("aliases") or []) if str(item).strip()]
    aliases.extend([key, path.name, path.name.replace("-", " "), path.name.replace("-", "_")])
    return _dedupe(aliases)


def _owner_system_for(key: str) -> str:
    if key == "spawner-ui":
        return "spark_spawner"
    if key == "spark-telegram-bot":
        return "telegram_gateway"
    if key == "domain-chip-memory":
        return "spark_memory"
    return "spark_local_work"


def _slug(value: str) -> str:
    return "-".join(part for part in str(value or "").strip().lower().replace("_", "-").split() if part)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
