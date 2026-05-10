from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


SYSTEM_MAP_CONTEXT_SCHEMA_VERSION = "spark.aoc_system_map_context.v1"

_RAW_READ_FLAGS = (
    "raw_secret_values_read",
    "raw_logs_read",
    "raw_conversation_content_read",
    "raw_memory_evidence_read",
    "sqlite_row_contents_read",
)


def build_spark_system_map_context(config_manager: ConfigManager) -> dict[str, Any]:
    output_dir, resolution = _resolve_system_map_dir(config_manager)
    files = {
        "system_map": output_dir / "system-map.json",
        "authority_view": output_dir / "authority-view.json",
        "capability_catalog": output_dir / "capability-catalog.json",
        "trace_index": output_dir / "trace-index.json",
        "gaps": output_dir / "gaps.md",
    }
    system_map = _read_json_object(files["system_map"])
    authority_view = _read_json_object(files["authority_view"])
    capability_catalog = _read_json_object(files["capability_catalog"])
    trace_index = _read_json_object(files["trace_index"])
    present = bool(system_map)

    if not present:
        return {
            "schema_version": SYSTEM_MAP_CONTEXT_SCHEMA_VERSION,
            "present": False,
            "source": "spark_cli.os_compile",
            "source_ref": "spark os compile",
            "output_dir": str(output_dir),
            "resolution": resolution,
            "freshness": "unknown",
            "counts": {},
            "warnings": ["spark_os_system_map_missing"],
            "next_action": "Run `spark os compile` from spark-cli before using cross-repo system truth in AOC.",
            "claim_boundary": _claim_boundary(),
            "authority": "observability_non_authoritative",
        }

    privacy = _dict(system_map.get("privacy"))
    warnings = _warnings(
        system_map=system_map,
        authority_view=authority_view,
        capability_catalog=capability_catalog,
        trace_index=trace_index,
        privacy=privacy,
    )
    counts = {
        "modules": len(_list(system_map.get("modules"))),
        "repos": len(_list(system_map.get("discovered_repos"))),
        "gaps": len(_list(system_map.get("gaps"))),
        "chip_manifests": len(_list(capability_catalog.get("chip_manifests"))),
        "skill_graphs": len(_list(capability_catalog.get("skill_graphs"))),
        "authority_sources": _authority_source_count(authority_view),
        "builder_event_rows": _builder_event_rows(trace_index),
    }
    return {
        "schema_version": SYSTEM_MAP_CONTEXT_SCHEMA_VERSION,
        "present": True,
        "source": "spark_cli.os_compile",
        "source_ref": "spark os compile",
        "output_dir": str(output_dir),
        "resolution": resolution,
        "freshness": "fresh" if system_map.get("generated_at") else "unknown",
        "generated_at": system_map.get("generated_at"),
        "counts": counts,
        "privacy": {key: privacy.get(key) for key in _RAW_READ_FLAGS if key in privacy},
        "files": {
            name: {
                "exists": path.exists(),
                "schema_version": _schema_for(name, system_map, authority_view, capability_catalog, trace_index),
            }
            for name, path in files.items()
        },
        "warnings": warnings,
        "next_action": "Use as read-only AOC source evidence; rerun `spark os compile` after install or repo changes.",
        "claim_boundary": _claim_boundary(),
        "authority": "observability_non_authoritative",
    }


def summarize_spark_system_map_context(context: dict[str, Any]) -> str:
    if not context.get("present"):
        return "missing; run spark os compile"
    counts = _dict(context.get("counts"))
    parts = [
        f"{int(counts.get('modules') or 0)} modules",
        f"{int(counts.get('repos') or 0)} repos",
        f"{int(counts.get('chip_manifests') or 0)} chips",
        f"{int(counts.get('gaps') or 0)} gaps",
    ]
    return ", ".join(parts)


def _resolve_system_map_dir(config_manager: ConfigManager) -> tuple[Path, str]:
    configured = config_manager.get_path("spark.system_map.output_dir")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser(), "config:spark.system_map.output_dir"

    home = config_manager.paths.home
    if home.name == "spark-intelligence" and home.parent.name == "state":
        return home.parent / "system-map", "spark_home_sibling"

    return home / "artifacts" / "system-map", "builder_artifacts_default"


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size > 5_000_000:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _warnings(
    *,
    system_map: dict[str, Any],
    authority_view: dict[str, Any],
    capability_catalog: dict[str, Any],
    trace_index: dict[str, Any],
    privacy: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if system_map.get("schema_version") != "spark.system_map.compiled.v0":
        warnings.append("unexpected_system_map_schema")
    if authority_view.get("schema_version") != "spark.authority_view.compiled.v0":
        warnings.append("unexpected_authority_view_schema")
    if capability_catalog.get("schema_version") != "spark.capability_catalog.compiled.v0":
        warnings.append("unexpected_capability_catalog_schema")
    if trace_index.get("schema_version") != "spark.trace_index.compiled.v0":
        warnings.append("unexpected_trace_index_schema")
    if any(privacy.get(key) is not False for key in _RAW_READ_FLAGS):
        warnings.append("privacy_flags_not_all_false")
    return warnings


def _schema_for(
    name: str,
    system_map: dict[str, Any],
    authority_view: dict[str, Any],
    capability_catalog: dict[str, Any],
    trace_index: dict[str, Any],
) -> str | None:
    source = {
        "system_map": system_map,
        "authority_view": authority_view,
        "capability_catalog": capability_catalog,
        "trace_index": trace_index,
    }.get(name, {})
    value = source.get("schema_version") if isinstance(source, dict) else None
    return str(value) if value else None


def _authority_source_count(authority_view: dict[str, Any]) -> int:
    sources = _dict(authority_view.get("observed_sources"))
    return sum(1 for item in sources.values() if _dict(item).get("exists") is True)


def _builder_event_rows(trace_index: dict[str, Any]) -> int:
    builder_events = _dict(trace_index.get("builder_events"))
    try:
        return int(builder_events.get("row_count") or 0)
    except (TypeError, ValueError):
        return 0


def _claim_boundary() -> str:
    return (
        "Compiled Spark OS maps are metadata snapshots. They prove source visibility, not live route success, "
        "not permission, and not memory truth."
    )


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []
