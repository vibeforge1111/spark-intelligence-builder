from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.orchestrator import inspect_memory_sdk_runtime


DEFAULT_DOMAIN_CHIP_MEMORY_ROOT = Path.home() / "Desktop" / "domain-chip-memory"
DEFAULT_FRONTIER_STATUS_DOC = "CURRENT_STATUS_BENCHMARKS_AND_KB_2026-04-09.md"
DEFAULT_VARIATION_LOOP_DOC = "ARCHITECTURE_VARIATION_LOOP_2026-03-29.md"
PRODUCT_MEMORY_BASELINES: tuple[str, ...] = (
    "observational_temporal_memory",
    "dual_store_event_calendar_hybrid",
    "summary_synthesis_memory",
    "contradiction_aware_summary_synthesis_memory",
    "stateful_event_reconstruction",
    "typed_state_update_memory",
)
ACTIVE_MEMORY_ARCHITECTURE_CONTENDERS: tuple[str, ...] = (
    "summary_synthesis_memory",
    "dual_store_event_calendar_hybrid",
)
DOCUMENTED_FRONTIER_ARCHITECTURE = "summary_synthesis_memory"
DOCUMENTED_FRONTIER_PROVIDER = "heuristic_v1"


@dataclass(frozen=True)
class MemoryArchitectureBenchmarkResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark memory architecture benchmark"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- runtime_sdk: {summary.get('runtime_sdk_class') or 'unknown'}")
            lines.append(
                f"- documented_frontier: "
                f"{summary.get('documented_frontier_architecture') or 'unknown'}"
            )
            lines.append(
                f"- product_memory_leaders: "
                f"{', '.join(summary.get('product_memory_leader_names') or []) or 'unknown'}"
            )
            lines.append(
                f"- runtime_matches_documented_frontier: "
                f"{'yes' if summary.get('runtime_matches_documented_frontier') else 'no'}"
            )
            lines.append(
                f"- runtime_matches_best_product_memory: "
                f"{'yes' if summary.get('runtime_matches_best_product_memory') else 'no'}"
            )
        errors = self.payload.get("errors") if isinstance(self.payload, dict) else None
        if isinstance(errors, list) and errors:
            lines.append(f"- errors: {len(errors)}")
        return "\n".join(lines)


def benchmark_memory_architectures(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    validator_root: str | Path | None = None,
    baseline_names: Sequence[str] | None = None,
) -> MemoryArchitectureBenchmarkResult:
    resolved_output_dir = Path(output_dir) if output_dir else _default_output_dir(config_manager)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = resolved_output_dir / "memory-architecture-benchmark.json"
    resolved_summary_path = resolved_output_dir / "memory-architecture-benchmark.md"

    runtime = inspect_memory_sdk_runtime(config_manager=config_manager)
    validator_path = Path(validator_root) if validator_root else DEFAULT_DOMAIN_CHIP_MEMORY_ROOT
    errors: list[str] = []
    try:
        resolved_baseline_names = resolve_memory_architecture_baselines(baseline_names)
    except ValueError as exc:
        resolved_baseline_names = ()
        errors.append(str(exc))
    benchmark_rows: list[dict[str, Any]] = []
    runtime_sdk_class = "unknown"

    if not resolved_baseline_names:
        errors.append("no_baselines_selected")
    elif not validator_path.exists():
        errors.append(f"validator_root_missing:{validator_path}")
    else:
        try:
            benchmark_rows, runtime_sdk_class = _run_product_memory_scorecards(
                validator_path,
                baseline_names=resolved_baseline_names,
            )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            errors.append(f"benchmark_execution_failed:{type(exc).__name__}:{exc}")

    product_memory_leaders = _product_memory_leaders(benchmark_rows)
    documented_frontier_sources = [
        str(validator_path / "docs" / DEFAULT_FRONTIER_STATUS_DOC),
        str(validator_path / "docs" / DEFAULT_VARIATION_LOOP_DOC),
    ]
    summary = {
        "configured_module": runtime.get("configured_module"),
        "resolved_module": runtime.get("resolved_module"),
        "client_kind": runtime.get("client_kind"),
        "runtime_sdk_class": runtime_sdk_class,
        "baseline_names": list(resolved_baseline_names),
        "documented_frontier_architecture": DOCUMENTED_FRONTIER_ARCHITECTURE,
        "documented_frontier_provider": DOCUMENTED_FRONTIER_PROVIDER,
        "documented_frontier_source_files": documented_frontier_sources,
        "product_memory_leader_names": [row["baseline_name"] for row in product_memory_leaders],
        "runtime_matches_documented_frontier": runtime_sdk_class == DOCUMENTED_FRONTIER_ARCHITECTURE,
        "runtime_matches_best_product_memory": runtime_sdk_class
        in {row["baseline_name"] for row in product_memory_leaders},
        "kb_connection_model": "downstream_compile_only",
        "kb_connection_status": "Builder compiles KB/wiki from governed memory snapshots but does not route runtime reads through the KB.",
        "assessment": _assessment_text(
            runtime_sdk_class=runtime_sdk_class,
            product_memory_leaders=product_memory_leaders,
        ),
    }
    payload = {
        "summary": summary,
        "runtime": runtime,
        "product_memory_scorecards": benchmark_rows,
        "errors": errors,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
            "summary_markdown": str(resolved_summary_path),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    resolved_summary_path.write_text(
        _build_summary_markdown(
            summary=summary,
            runtime=runtime,
            benchmark_rows=benchmark_rows,
            errors=errors,
        ),
        encoding="utf-8",
    )
    return MemoryArchitectureBenchmarkResult(output_dir=resolved_output_dir, payload=payload)


def _default_output_dir(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "memory-architecture-benchmark"


def resolve_memory_architecture_baselines(
    baseline_names: Sequence[str] | None,
    *,
    allowed_baselines: Sequence[str] = PRODUCT_MEMORY_BASELINES,
    default_baselines: Sequence[str] = ACTIVE_MEMORY_ARCHITECTURE_CONTENDERS,
) -> tuple[str, ...]:
    allowed = set(allowed_baselines)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in baseline_names or ():
        rendered = str(item or "").strip()
        if not rendered or rendered in seen:
            continue
        normalized.append(rendered)
        seen.add(rendered)
    if not normalized:
        return tuple(default_baselines)
    invalid = [name for name in normalized if name not in allowed]
    if invalid:
        raise ValueError(f"unsupported_baselines:{','.join(invalid)}")
    return tuple(normalized)


def _assessment_text(
    *,
    runtime_sdk_class: str,
    product_memory_leaders: list[dict[str, Any]],
) -> str:
    if runtime_sdk_class == DOCUMENTED_FRONTIER_ARCHITECTURE:
        return (
            "Builder runtime is using the same named architecture that the domain-chip-memory repo "
            "documents as the current BEAM and LongMemEval leader."
        )
    leader_names = ", ".join(row["baseline_name"] for row in product_memory_leaders) or "unknown"
    return (
        "Builder runtime is using the governed SparkMemorySDK substrate, not a named benchmark-frontier "
        f"architecture selector. The current ProductMemory leaders are {leader_names}, while the "
        f"repo-documented BEAM and LongMemEval leader remains {DOCUMENTED_FRONTIER_ARCHITECTURE}. "
        "That means Builder is benchmark-grounded but not frontier-matched yet."
    )


def _build_summary_markdown(
    *,
    summary: dict[str, Any],
    runtime: dict[str, Any],
    benchmark_rows: list[dict[str, Any]],
    errors: list[str],
) -> str:
    lines = [
        "# Memory Architecture Benchmark Summary",
        "",
        "## Runtime",
        "",
        f"- Configured module: `{summary.get('configured_module') or 'unknown'}`",
        f"- Resolved module: `{summary.get('resolved_module') or 'unknown'}`",
        f"- Client kind: `{summary.get('client_kind') or 'unknown'}`",
        f"- Runtime SDK class: `{summary.get('runtime_sdk_class') or 'unknown'}`",
        f"- Memory enabled: `{'yes' if runtime.get('memory_enabled') else 'no'}`",
        f"- Shadow mode: `{'yes' if runtime.get('shadow_mode') else 'no'}`",
        "",
        "## Frontier Comparison",
        "",
        f"- Repo-documented frontier architecture: `{summary.get('documented_frontier_architecture')}`",
        f"- Repo-documented frontier provider: `{summary.get('documented_frontier_provider')}`",
        f"- Runtime matches documented frontier: `{'yes' if summary.get('runtime_matches_documented_frontier') else 'no'}`",
        f"- Runtime matches best ProductMemory architecture: `{'yes' if summary.get('runtime_matches_best_product_memory') else 'no'}`",
        "",
        "## ProductMemory Benchmark",
        "",
        f"- Compared baselines: `{', '.join(summary.get('baseline_names') or []) or 'none'}`",
        "",
    ]
    for row in benchmark_rows:
        overall = row.get("overall") or {}
        alignment = row.get("alignment") or {}
        lines.append(
            f"- `{row.get('baseline_name')}`: "
            f"`{overall.get('correct', 0)}/{overall.get('total', 0)}` "
            f"(accuracy `{overall.get('accuracy', 0):.4f}`), "
            f"alignment `{alignment.get('aligned', 0)}/{alignment.get('total', 0)}` "
            f"(rate `{alignment.get('rate', 0):.4f}`)"
        )
    lines.extend(
        [
            "",
            "## KB Connection",
            "",
            f"- Connection mode: `{summary.get('kb_connection_model')}`",
            f"- Status: {summary.get('kb_connection_status')}",
            "",
            "## Assessment",
            "",
            summary.get("assessment") or "No assessment available.",
            "",
            "## Sources",
            "",
        ]
    )
    for item in summary.get("documented_frontier_source_files") or []:
        lines.append(f"- `{item}`")
    if errors:
        lines.extend(["", "## Errors", ""])
        for item in errors:
            lines.append(f"- `{item}`")
    return "\n".join(lines) + "\n"


def _product_memory_leaders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    best_accuracy = max(float((row.get("overall") or {}).get("accuracy") or 0.0) for row in rows)
    accuracy_leaders = [
        row for row in rows if float((row.get("overall") or {}).get("accuracy") or 0.0) == best_accuracy
    ]
    best_alignment = max(float((row.get("alignment") or {}).get("rate") or 0.0) for row in accuracy_leaders)
    return [
        row for row in accuracy_leaders if float((row.get("alignment") or {}).get("rate") or 0.0) == best_alignment
    ]


@contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    inserted = False
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(path_str)
            except ValueError:  # pragma: no cover - defensive cleanup
                pass


def _run_product_memory_scorecards(
    validator_path: Path,
    *,
    baseline_names: Sequence[str],
) -> tuple[list[dict[str, Any]], str]:
    src_path = validator_path / "src"
    with _prepend_sys_path(src_path):
        from domain_chip_memory.providers import get_provider
        from domain_chip_memory.runner import run_baseline
        from domain_chip_memory.sample_data import product_memory_samples
        from domain_chip_memory.sdk import build_sdk_contract_summary

        provider = get_provider(DOCUMENTED_FRONTIER_PROVIDER)
        samples = product_memory_samples()
        rows: list[dict[str, Any]] = []
        for baseline_name in baseline_names:
            scorecard = run_baseline(
                samples,
                baseline_name=baseline_name,
                provider=provider,
                top_k_sessions=2,
                fallback_sessions=1,
            )
            rows.append(
                {
                    "baseline_name": baseline_name,
                    "overall": dict(scorecard.get("overall") or {}),
                    "alignment": dict(
                        (scorecard.get("product_memory_summary") or {})
                        .get("measured_metrics", {})
                        .get("primary_answer_candidate_source_alignment", {})
                    ),
                }
            )
        runtime_sdk_class = str(build_sdk_contract_summary().get("runtime_class") or "unknown")
        return rows, runtime_sdk_class
