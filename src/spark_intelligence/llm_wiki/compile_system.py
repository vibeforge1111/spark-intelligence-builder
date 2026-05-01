from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory import inspect_memory_sdk_runtime
from spark_intelligence.self_awareness import build_self_awareness_capsule
from spark_intelligence.state.db import StateDB
from spark_intelligence.system_registry import build_system_registry


@dataclass(frozen=True)
class LlmWikiSystemCompileResult:
    output_dir: Path
    generated_files: tuple[str, ...]
    registry_record_count: int
    lack_count: int
    improvement_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "generated_files": list(self.generated_files),
            "generated_file_count": len(self.generated_files),
            "registry_record_count": self.registry_record_count,
            "lack_count": self.lack_count,
            "improvement_count": self.improvement_count,
            "authority": "supporting_not_authoritative",
            "source_refs": ["system_registry", "self_awareness_capsule", "memory_runtime_status"],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        payload = self.to_payload()
        lines = [
            "Spark LLM wiki system compile",
            f"- output_dir: {payload['output_dir']}",
            f"- generated_files: {payload['generated_file_count']}",
            f"- registry_records: {payload['registry_record_count']}",
            f"- lacks: {payload['lack_count']}",
            f"- improvements: {payload['improvement_count']}",
            "- authority: supporting_not_authoritative",
        ]
        lines.extend(f"  - {item}" for item in self.generated_files[:8])
        return "\n".join(lines)


def compile_system_wiki(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
) -> LlmWikiSystemCompileResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    generated_at = _utc_timestamp()
    registry = build_system_registry(config_manager, state_db, probe_browser=False, probe_git=False).to_payload()
    self_capsule = build_self_awareness_capsule(config_manager=config_manager, state_db=state_db).to_payload()
    memory_runtime = inspect_memory_sdk_runtime(config_manager=config_manager)
    records = [record for record in registry.get("records") or [] if isinstance(record, dict)]
    lacks = [item for item in self_capsule.get("lacks") or [] if isinstance(item, dict)]
    improvements = [item for item in self_capsule.get("improvement_options") or [] if isinstance(item, dict)]

    pages = {
        "system/current-system-status.md": _current_system_status_page(
            generated_at=generated_at,
            registry=registry,
            memory_runtime=memory_runtime,
        ),
        "tools/capability-index.md": _capability_index_page(generated_at=generated_at, records=records),
        "routes/live-route-index.md": _route_index_page(generated_at=generated_at, self_capsule=self_capsule),
        "diagnostics/self-awareness-gaps.md": _self_awareness_gaps_page(
            generated_at=generated_at,
            lacks=lacks,
            improvements=improvements,
        ),
    }
    generated_files: list[str] = []
    for relative_path, content in pages.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        generated_files.append(relative_path)
    return LlmWikiSystemCompileResult(
        output_dir=root,
        generated_files=tuple(generated_files),
        registry_record_count=len(records),
        lack_count=len(lacks),
        improvement_count=len(improvements),
    )


def _current_system_status_page(*, generated_at: str, registry: dict[str, Any], memory_runtime: dict[str, Any]) -> str:
    summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
    capabilities = [str(item) for item in summary.get("current_capabilities") or [] if str(item).strip()]
    memory_lines = [
        f"- ready: `{bool(memory_runtime.get('ready'))}`",
        f"- runtime_class: `{memory_runtime.get('runtime_class') or 'unknown'}`",
        f"- architecture: `{memory_runtime.get('runtime_memory_architecture') or 'unknown'}`",
        f"- provider: `{memory_runtime.get('runtime_memory_provider') or 'unknown'}`",
    ]
    body = [
        "## Snapshot",
        f"- generated_at: `{generated_at}`",
        f"- workspace: `{registry.get('workspace_id') or 'default'}`",
        f"- registry records: `{registry.get('record_count') or 0}`",
        f"- systems: `{summary.get('system_count', 0)}`",
        f"- adapters: `{summary.get('adapter_count', 0)}`",
        f"- providers: `{summary.get('provider_count', 0)}`",
        f"- chips: `{summary.get('chip_count', 0)}`",
        f"- paths: `{summary.get('path_count', 0)}`",
        f"- repos: `{summary.get('repo_count', 0)}`",
        "",
        "## Memory Runtime",
        *memory_lines,
        "",
        "## Current Capability Summary",
    ]
    body.extend(f"- {item}" for item in capabilities[:12])
    if not capabilities:
        body.append("- No derived capabilities were available in this snapshot.")
    body.extend(
        [
            "",
            "## Authority Boundary",
            "- This page is generated from registry and runtime status.",
            "- Live health checks and route traces outrank this page when they are newer.",
        ]
    )
    return _render_page(
        title="Current System Status",
        summary="Generated Spark system snapshot for LLM wiki retrieval.",
        tags=("spark-wiki", "generated", "system-status"),
        generated_at=generated_at,
        body="\n".join(body),
    )


def _capability_index_page(*, generated_at: str, records: list[dict[str, Any]]) -> str:
    rows = [
        record
        for record in records
        if str(record.get("kind") or "") in {"system", "adapter", "provider", "chip", "path", "repo"}
    ]
    body = [
        "## Capability Cards",
        "- Registry visibility is not proof of recent successful invocation.",
        "- Use `status`, `available`, `active`, and recent route traces together.",
        "",
    ]
    for record in rows[:80]:
        capabilities = ", ".join(str(item) for item in (record.get("capabilities") or [])[:5])
        limitations = "; ".join(str(item) for item in (record.get("limitations") or [])[:3])
        body.extend(
            [
                f"### {record.get('label') or record.get('key')}",
                f"- kind: `{record.get('kind')}`",
                f"- key: `{record.get('key')}`",
                f"- status: `{record.get('status')}`",
                f"- available: `{bool(record.get('available'))}`",
                f"- active: `{bool(record.get('active'))}`",
                f"- role: {record.get('role') or 'unknown'}",
                f"- capabilities: {capabilities or 'none listed'}",
                f"- limitations: {limitations or 'none listed'}",
                f"- next_probe: run a route-specific health or invocation check before claiming `{record.get('key')}` recently worked.",
                "",
            ]
        )
    return _render_page(
        title="Capability Index",
        summary="Generated inventory of Spark systems, chips, paths, providers, and repos.",
        tags=("spark-wiki", "generated", "capabilities", "tools"),
        generated_at=generated_at,
        body="\n".join(body),
    )


def _route_index_page(*, generated_at: str, self_capsule: dict[str, Any]) -> str:
    routes = [str(item) for item in self_capsule.get("natural_language_routes") or [] if str(item).strip()]
    probes = [str(item) for item in self_capsule.get("recommended_probes") or [] if str(item).strip()]
    body = [
        "## Natural-Language Routes",
    ]
    body.extend(f"- {item}" for item in routes)
    if not routes:
        body.append("- No route suggestions were generated in this snapshot.")
    body.extend(["", "## Recommended Probes"])
    body.extend(f"- {item}" for item in probes)
    if not probes:
        body.append("- No probes were recommended in this snapshot.")
    body.extend(
        [
            "",
            "## Routing Rule",
            "- Natural-language route confidence should combine user intent, current context, live registry, recent route evidence, and wiki support.",
            "- Slash commands remain shortcuts, not the primary mental model.",
        ]
    )
    return _render_page(
        title="Live Route Index",
        summary="Generated natural-language route and probe index for Spark.",
        tags=("spark-wiki", "generated", "routes", "natural-language"),
        generated_at=generated_at,
        body="\n".join(body),
    )


def _self_awareness_gaps_page(*, generated_at: str, lacks: list[dict[str, Any]], improvements: list[dict[str, Any]]) -> str:
    body = ["## Where Spark Lacks"]
    if lacks:
        for item in lacks[:20]:
            body.extend(
                [
                    f"- {item.get('claim')}",
                    f"  - source: `{item.get('source')}`",
                    f"  - confidence: `{item.get('confidence')}`",
                    f"  - next_probe: {item.get('next_probe') or 'none recorded'}",
                ]
            )
    else:
        body.append("- No explicit lacks were generated in this snapshot.")
    body.extend(["", "## How Spark Can Improve"])
    if improvements:
        for item in improvements[:20]:
            body.extend(
                [
                    f"- {item.get('claim')}",
                    f"  - action: {item.get('improvement_action') or item.get('next_probe') or 'none recorded'}",
                ]
            )
    else:
        body.append("- No improvement options were generated in this snapshot.")
    return _render_page(
        title="Self-Awareness Gaps",
        summary="Generated lack and improvement index from Spark's grounded self-awareness capsule.",
        tags=("spark-wiki", "generated", "self-awareness", "gaps"),
        generated_at=generated_at,
        body="\n".join(body),
    )


def _render_page(
    *,
    title: str,
    summary: str,
    tags: tuple[str, ...],
    generated_at: str,
    body: str,
) -> str:
    tag_text = ", ".join(tags)
    return "\n".join(
        [
            "---",
            f'title: "{title}"',
            f'date_created: "{generated_at[:10]}"',
            f'date_modified: "{generated_at[:10]}"',
            f'summary: "{summary}"',
            f"tags: [{tag_text}]",
            "type: llm_wiki_system_compile",
            "status: generated",
            "authority: supporting_not_authoritative",
            "freshness: live_compile_snapshot",
            "source_class: spark_llm_wiki_system_compile",
            "---",
            "",
            f"# {title}",
            "",
            body.strip(),
            "",
        ]
    )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
