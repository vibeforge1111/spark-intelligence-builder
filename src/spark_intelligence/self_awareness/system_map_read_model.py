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
        "memory_movement_index": output_dir / "memory-movement-index.json",
        "gaps": output_dir / "gaps.md",
    }
    system_map = _read_json_object(files["system_map"])
    authority_view = _read_json_object(files["authority_view"])
    capability_catalog = _read_json_object(files["capability_catalog"])
    trace_index = _read_json_object(files["trace_index"])
    memory_movement_index = _read_json_object(files["memory_movement_index"])
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
        memory_movement_index=memory_movement_index,
        privacy=privacy,
    )
    memory_movement = _memory_movement_context(memory_movement_index)
    trace_health = _trace_health_context(trace_index)
    trace_topology = _trace_topology_context(trace_index)
    cross_system_trace = _cross_system_trace_context(trace_index)
    capability_garden = _capability_garden_context(capability_catalog)
    counts = {
        "modules": len(_list(system_map.get("modules"))),
        "repos": len(_list(system_map.get("discovered_repos"))),
        "gaps": len(_list(system_map.get("gaps"))),
        "chip_manifests": len(_list(capability_catalog.get("chip_manifests"))),
        "skill_graphs": len(_list(capability_catalog.get("skill_graphs"))),
        "creator_system_surfaces": _int(capability_garden.get("creator_system_surfaces")),
        "specialization_path_surfaces": _int(capability_garden.get("specialization_path_surfaces")),
        "capability_cards": _int(capability_garden.get("card_count")),
        "authority_sources": _authority_source_count(authority_view),
        "builder_event_rows": _builder_event_rows(trace_index),
        "builder_event_samples": _builder_event_sample_count(trace_index),
        "builder_trace_groups": _builder_trace_group_count(trace_index),
        "builder_trace_topology_groups": _int(trace_topology.get("group_count")),
        "trace_health_flags": len(_list(trace_health.get("health_flags"))),
        "spawner_prd_request_ids": _int(cross_system_trace.get("spawner_prd_request_id_count")),
        "spawner_prd_derived_trace_refs": _int(cross_system_trace.get("spawner_prd_derived_trace_ref_count")),
        "spawner_builder_trace_ref_overlaps": _int(cross_system_trace.get("spawner_builder_trace_ref_overlap_count")),
        "memory_movement_rows": memory_movement.get("row_count"),
        "builder_memory_table_count": memory_movement.get("builder_memory_table_count"),
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
        "memory_movement": memory_movement,
        "trace_health": trace_health,
        "trace_topology": trace_topology,
        "cross_system_trace": cross_system_trace,
        "capability_garden": capability_garden,
        "privacy": {key: privacy.get(key) for key in _RAW_READ_FLAGS if key in privacy},
        "files": {
            name: {
                "exists": path.exists(),
                "schema_version": _schema_for(
                    name,
                    system_map,
                    authority_view,
                    capability_catalog,
                    trace_index,
                    memory_movement_index,
                ),
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
    memory_movement = _dict(context.get("memory_movement"))
    if memory_movement.get("present"):
        parts.append(
            f"memory movement {memory_movement.get('status') or 'unknown'} "
            f"({int(memory_movement.get('row_count') or 0)} rows)"
        )
    sample_count = int(counts.get("builder_event_samples") or 0)
    if sample_count:
        parts.append(f"black-box samples {sample_count}")
    trace_group_count = int(counts.get("builder_trace_groups") or 0)
    if trace_group_count:
        parts.append(f"trace groups {trace_group_count}")
    trace_health = _dict(context.get("trace_health"))
    health_flags = _list(trace_health.get("health_flags"))
    if health_flags:
        parts.append(f"trace health flags {len(health_flags)}")
    trace_topology = _dict(context.get("trace_topology"))
    if trace_topology.get("present"):
        parts.append(f"trace topology {int(trace_topology.get('group_count') or 0)} groups")
    cross_system_trace = _dict(context.get("cross_system_trace"))
    derived_spawner_refs = int(cross_system_trace.get("spawner_prd_derived_trace_ref_count") or 0)
    if derived_spawner_refs:
        parts.append(f"spawner trace refs {derived_spawner_refs}")
    capability_garden = _dict(context.get("capability_garden"))
    card_count = int(capability_garden.get("card_count") or 0)
    if card_count:
        parts.append(f"capability cards {card_count}")
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
    memory_movement_index: dict[str, Any],
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
    if memory_movement_index and memory_movement_index.get("schema_version") != "spark.memory_movement_index.compiled.v0":
        warnings.append("unexpected_memory_movement_index_schema")
    if any(privacy.get(key) is not False for key in _RAW_READ_FLAGS):
        warnings.append("privacy_flags_not_all_false")
    return warnings


def _schema_for(
    name: str,
    system_map: dict[str, Any],
    authority_view: dict[str, Any],
    capability_catalog: dict[str, Any],
    trace_index: dict[str, Any],
    memory_movement_index: dict[str, Any],
) -> str | None:
    source = {
        "system_map": system_map,
        "authority_view": authority_view,
        "capability_catalog": capability_catalog,
        "trace_index": trace_index,
        "memory_movement_index": memory_movement_index,
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


def _builder_event_sample_count(trace_index: dict[str, Any]) -> int:
    builder_event_samples = _dict(trace_index.get("builder_event_samples"))
    return _int(builder_event_samples.get("sample_count"))


def _builder_trace_group_count(trace_index: dict[str, Any]) -> int:
    builder_trace_groups = _dict(trace_index.get("builder_trace_groups"))
    return _int(builder_trace_groups.get("group_count"))


def _trace_health_context(trace_index: dict[str, Any]) -> dict[str, Any]:
    trace_health = _dict(trace_index.get("builder_trace_health"))
    return {
        "present": bool(trace_health),
        "health_flags": _list(trace_health.get("health_flags")),
        "missing_trace_ref_count": _int(trace_health.get("missing_trace_ref_count")),
        "high_severity_open_count": _int(trace_health.get("high_severity_open_count")),
        "orphan_parent_event_id_count": _int(trace_health.get("orphan_parent_event_id_count")),
        "trace_group_count": _int(trace_health.get("trace_group_count")),
        "missing_trace_ref_sources": _missing_trace_ref_sources(trace_health),
        "orphan_parent_event_sources": _orphan_parent_event_sources(trace_health),
        "recent_windows": _trace_health_recent_windows(trace_health),
        "claim_boundary": (
            "Trace health flags are black-box diagnostics. They show observability gaps and open severity, "
            "not final task outcome or memory truth."
        ),
    }


def _trace_topology_context(trace_index: dict[str, Any]) -> dict[str, Any]:
    trace_groups = _dict(trace_index.get("builder_trace_groups"))
    groups: list[dict[str, Any]] = []
    parent_link_count = 0
    orphan_parent_event_count = 0
    edge_sample_count = 0
    for raw_group in _list(trace_groups.get("groups"))[:10]:
        group = _dict(raw_group)
        topology = _dict(group.get("topology"))
        edges = [_dict(edge) for edge in _list(topology.get("edge_sample"))[:5]]
        parent_links = _int(topology.get("parent_link_count"))
        orphan_count = _int(topology.get("orphan_parent_event_count"))
        edge_count = _int(topology.get("edge_sample_count"))
        parent_link_count += parent_links
        orphan_parent_event_count += orphan_count
        edge_sample_count += edge_count
        groups.append(
            {
                "trace_ref": str(group.get("trace_ref") or ""),
                "event_count": _int(group.get("event_count")),
                "first_seen_at": group.get("first_seen_at"),
                "last_seen_at": group.get("last_seen_at"),
                "topology": {
                    "available": bool(topology.get("available")),
                    "root_event_count": _int(topology.get("root_event_count")),
                    "parent_link_count": parent_links,
                    "orphan_parent_event_count": orphan_count,
                    "edge_sample_count": edge_count,
                    "edge_sample": [
                        {
                            "parent_event_id": edge.get("parent_event_id"),
                            "child_event_id": edge.get("child_event_id"),
                            "parent_event_type": edge.get("parent_event_type"),
                            "child_event_type": edge.get("child_event_type"),
                            "child_component": edge.get("child_component"),
                            "parent_exists": bool(edge.get("parent_exists")),
                            "parent_in_same_trace": bool(edge.get("parent_in_same_trace")),
                        }
                        for edge in edges
                    ],
                },
            }
        )
    return {
        "present": bool(trace_groups),
        "group_count": _int(trace_groups.get("group_count")) or len(groups),
        "projected_group_count": len(groups),
        "parent_link_count": parent_link_count,
        "orphan_parent_event_count": orphan_parent_event_count,
        "edge_sample_count": edge_sample_count,
        "groups": groups,
        "claim_boundary": (
            "Trace topology is a redacted event graph projection. It can guide repair and debugging; "
            "it is not event body evidence, memory truth, or proof of task success."
        ),
    }


def _cross_system_trace_context(trace_index: dict[str, Any]) -> dict[str, Any]:
    spawner = _dict(trace_index.get("spawner_prd_auto_trace_samples"))
    spawner_join = _dict(spawner.get("join_keys"))
    spawner_derived = _dict(spawner.get("derived_trace_contract"))
    spawner_request_overlap = _dict(spawner.get("builder_request_overlap"))
    spawner_trace_overlap = _dict(spawner.get("builder_trace_ref_overlap"))
    telegram_final_gate = _dict(trace_index.get("telegram_final_answer_gate_samples"))
    telegram_join = _dict(telegram_final_gate.get("trace_join"))
    return {
        "present": bool(spawner or telegram_final_gate),
        "spawner_prd_request_id_count": _int(spawner_join.get("request_id_count")),
        "spawner_prd_mission_id_count": _int(spawner_join.get("mission_id_count")),
        "spawner_prd_trace_ref_count": _int(spawner_join.get("trace_ref_count")),
        "spawner_prd_derived_trace_ref_count": _int(spawner_join.get("derived_trace_ref_count")),
        "spawner_trace_contract_status": str(spawner_derived.get("status") or "unknown"),
        "spawner_builder_request_overlap_count": _int(
            spawner_request_overlap.get("matched_builder_request_id_count")
        ),
        "spawner_builder_trace_ref_overlap_count": _int(
            spawner_trace_overlap.get("matched_builder_trace_ref_count")
        ),
        "telegram_final_answer_trace_join_status": str(telegram_join.get("status") or "unknown"),
        "claim_boundary": (
            "Cross-system trace context is metadata-only join shape. It shows whether traces can be stitched; "
            "it is not action success, permission evidence, or user-message content."
        ),
    }


def _capability_garden_context(capability_catalog: dict[str, Any]) -> dict[str, Any]:
    raw_cards = [_dict(card) for card in _list(capability_catalog.get("capability_cards"))]
    status_counts: dict[str, int] = {}
    surface_counts: dict[str, int] = {}
    cards: list[dict[str, Any]] = []
    for card in raw_cards[:12]:
        status = _short(card.get("status") or "unknown")
        surface = _short(card.get("surface_type") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        surface_counts[surface] = surface_counts.get(surface, 0) + 1
        cards.append(
            {
                "id": _short(card.get("id")),
                "name": _short(card.get("name")),
                "owner_repo": _short(card.get("owner_repo")),
                "surface_type": surface,
                "status": status,
                "requested_authority": [_short(item) for item in _list(card.get("requested_authority"))[:8]],
                "memory_policy": _short(card.get("memory_policy")),
                "evidence_summary": _safe_summary_mapping(card.get("evidence_summary")),
                "benchmark_summary": _safe_summary_mapping(card.get("benchmark_summary")),
                "review_summary": _safe_summary_mapping(card.get("review_summary")),
                "blockers": [_short(item) for item in _list(card.get("blockers"))[:5]],
                "next_action": _short(card.get("next_action"), limit=240),
                "privacy_boundary": _short(card.get("privacy_boundary"), limit=240),
                "public_boundary": _short(card.get("public_boundary"), limit=240),
            }
        )

    return {
        "present": bool(capability_catalog),
        "creator_system_surfaces": len(_list(capability_catalog.get("creator_system_surfaces"))),
        "specialization_path_surfaces": len(_list(capability_catalog.get("specialization_path_surfaces"))),
        "card_count": len(raw_cards),
        "projected_card_count": len(cards),
        "status_counts": dict(sorted(status_counts.items())),
        "surface_counts": dict(sorted(surface_counts.items())),
        "cards": cards,
        "claim_boundary": (
            "Capability garden is a metadata-only projection. A card shows observed surfaces and blockers; "
            "it does not grant trust, memory authority, tool authority, or publication approval."
        ),
    }


def _missing_trace_ref_sources(trace_health: dict[str, Any]) -> dict[str, Any]:
    sources = _dict(trace_health.get("missing_trace_ref_sources"))
    rows: list[dict[str, Any]] = []
    for raw_row in _list(sources.get("rows"))[:10]:
        row = _dict(raw_row)
        rows.append(
            {
                "component": str(row.get("component") or "[missing]"),
                "event_type": str(row.get("event_type") or "[missing]"),
                "status": str(row.get("status") or "[missing]"),
                "severity": str(row.get("severity") or "[missing]"),
                "target_surface": str(row.get("target_surface") or "[missing]"),
                "evidence_lane": str(row.get("evidence_lane") or "[missing]"),
                "event_count": _int(row.get("event_count")),
            }
        )
    return {
        "group_by": [str(item) for item in _list(sources.get("group_by"))],
        "row_count": len(rows),
        "rows": rows,
        "claim_boundary": "Ranked repair queue for trace propagation only; not memory truth or task outcome.",
    }


def _orphan_parent_event_sources(trace_health: dict[str, Any]) -> dict[str, Any]:
    sources = _dict(trace_health.get("orphan_parent_event_sources"))
    rows: list[dict[str, Any]] = []
    for raw_row in _list(sources.get("rows"))[:10]:
        row = _dict(raw_row)
        rows.append(
            {
                "component": str(row.get("component") or "[missing]"),
                "event_type": str(row.get("event_type") or "[missing]"),
                "status": str(row.get("status") or "[missing]"),
                "severity": str(row.get("severity") or "[missing]"),
                "target_surface": str(row.get("target_surface") or "[missing]"),
                "evidence_lane": str(row.get("evidence_lane") or "[missing]"),
                "event_count": _int(row.get("event_count")),
            }
        )
    return {
        "group_by": [str(item) for item in _list(sources.get("group_by"))],
        "row_count": len(rows),
        "rows": rows,
        "claim_boundary": "Ranked repair queue for missing parent links only; not memory truth or task outcome.",
    }


def _trace_health_recent_windows(trace_health: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_row in _list(trace_health.get("recent_windows")):
        row = _dict(raw_row)
        rows.append(
            {
                "window": str(row.get("window") or ""),
                "threshold": str(row.get("threshold") or ""),
                "row_count": _int(row.get("row_count")),
                "missing_trace_ref_count": _int(row.get("missing_trace_ref_count")),
                "missing_trace_ref_ratio": _float(row.get("missing_trace_ref_ratio")),
            }
        )
    return rows


def _memory_movement_context(memory_movement_index: dict[str, Any]) -> dict[str, Any]:
    if not memory_movement_index:
        return {
            "present": False,
            "status": "missing",
            "row_count": 0,
            "builder_memory_table_count": 0,
            "movement_counts": {},
            "authority": "observability_non_authoritative",
        }

    status_export = _dict(memory_movement_index.get("safe_status_export"))
    status = _dict(status_export.get("status"))
    builder_tables = _dict(memory_movement_index.get("builder_memory_tables"))
    return {
        "present": True,
        "schema_version": memory_movement_index.get("schema_version"),
        "status": str(status.get("status") or ("status_export_missing" if not status_export.get("exists") else "unknown")),
        "row_count": _int(status.get("row_count")),
        "builder_memory_table_count": _int(builder_tables.get("table_count")),
        "movement_counts": _int_mapping(status.get("movement_counts")),
        "authority": str(memory_movement_index.get("authority") or status.get("authority") or "observability_non_authoritative"),
        "claim_boundary": (
            "Memory movement index is observability evidence. It explains movement counts and status; "
            "it is not memory truth and cannot override current-state records."
        ),
    }


def _claim_boundary() -> str:
    return (
        "Compiled Spark OS maps are metadata snapshots. They prove source visibility, not live route success, "
        "not permission, and not memory truth."
    )


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _int(item) for key, item in value.items()}


def _safe_summary_mapping(value: object, *, depth: int = 0) -> dict[str, Any]:
    if depth > 3 or not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}
    for key, item in list(value.items())[:40]:
        clean_key = _short(key, limit=80)
        if isinstance(item, dict):
            output[clean_key] = _safe_summary_mapping(item, depth=depth + 1)
        elif isinstance(item, list):
            output[clean_key] = [_short(entry, limit=120) for entry in item[:20]]
        elif isinstance(item, (bool, int, float)) or item is None:
            output[clean_key] = item
        else:
            output[clean_key] = _short(item)
    return output


def _short(value: object, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
