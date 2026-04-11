from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import approve_pairing
from spark_intelligence.memory.benchmark_packs import (
    default_telegram_memory_benchmark_packs,
)
from spark_intelligence.memory.regression import QUALITY_LANE_KEYS, run_telegram_memory_regression
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class TelegramArchitectureSoakResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark memory architecture soak"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- requested_runs: {summary.get('requested_runs', 0)}")
            lines.append(f"- completed_runs: {summary.get('completed_runs', 0)}")
            lines.append(f"- failed_runs: {summary.get('failed_runs', 0)}")
            lines.append(f"- benchmark_mode: {summary.get('benchmark_mode') or 'unknown'}")
            lines.append(f"- benchmark_pack_count: {summary.get('benchmark_pack_count', 0)}")
            lines.append(f"- leaders: {', '.join(summary.get('overall_leader_names') or []) or 'unknown'}")
            lines.append(
                f"- recommended_top_two: {', '.join(summary.get('recommended_top_two') or []) or 'unknown'}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class _BenchmarkRunSpec:
    pack_id: str
    title: str
    description: str
    cases: tuple[Any, ...] | None = None
    case_ids: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()


def run_telegram_memory_architecture_soak(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    runs: int = 50,
    sleep_seconds: float = 0.0,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    kb_limit: int = 25,
    validator_root: str | Path | None = None,
    write_path: str | Path | None = None,
    case_ids: list[str] | None = None,
    categories: list[str] | None = None,
    baseline_names: list[str] | None = None,
) -> TelegramArchitectureSoakResult:
    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-architecture-soak"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-architecture-soak.json"

    requested_runs = max(int(runs), 1)
    requested_baseline_names = [str(item).strip() for item in (baseline_names or []) if str(item).strip()]
    resolved_baseline_names = list(requested_baseline_names)
    run_specs = _build_run_specs(case_ids=case_ids, categories=categories)
    benchmark_pack_rows = [_run_spec_payload(spec) for spec in run_specs]
    baseline_aggregate: dict[str, dict[str, Any]] = {}
    category_aggregate: dict[str, dict[str, dict[str, Any]]] = {}
    pack_aggregate: dict[str, dict[str, Any]] = {}
    run_payloads: list[dict[str, Any]] = []
    errors: list[str] = []

    for index in range(1, requested_runs + 1):
        run_spec = run_specs[(index - 1) % len(run_specs)]
        run_output_dir = resolved_output_dir / f"run-{index:04d}"
        run_user_id, run_chat_id = _resolve_run_namespace(
            run_index=index,
            run_spec=run_spec,
            user_id=user_id,
            chat_id=chat_id,
        )
        try:
            approve_pairing(
                state_db=state_db,
                channel_id="telegram",
                external_user_id=run_user_id,
                display_name=username or f"Memory Soak {run_spec.pack_id}",
            )
            regression_result = run_telegram_memory_regression(
                config_manager=config_manager,
                state_db=state_db,
                output_dir=run_output_dir,
                user_id=run_user_id,
                username=username or "memory-soak",
                chat_id=run_chat_id,
                kb_limit=kb_limit,
                validator_root=validator_root,
                write_path=run_output_dir / "telegram-memory-regression.json",
                case_ids=list(run_spec.case_ids) or None,
                categories=list(run_spec.categories) or None,
                cases=list(run_spec.cases) if run_spec.cases is not None else None,
                baseline_names=requested_baseline_names or None,
            )
            regression_payload = regression_result.payload if isinstance(regression_result.payload, dict) else {}
            live_comparison_payload = (
                regression_payload.get("architecture_live_comparison")
                if isinstance(regression_payload.get("architecture_live_comparison"), dict)
                else {}
            )
            if not resolved_baseline_names:
                resolved_baseline_names = [
                    str(item).strip()
                    for item in (
                        ((live_comparison_payload.get("summary") or {}).get("baseline_names") or [])
                        if isinstance(live_comparison_payload.get("summary"), dict)
                        else []
                    )
                    if str(item).strip()
                ]
            baseline_rows = [
                item
                for item in list(live_comparison_payload.get("baseline_results") or [])
                if isinstance(item, dict)
            ]
            leader_names = list(
                ((live_comparison_payload.get("summary") or {}).get("leader_names") or [])
                if isinstance(live_comparison_payload.get("summary"), dict)
                else []
            )
            run_payloads.append(
                {
                    "run_index": index,
                    "output_dir": str(run_output_dir),
                    "benchmark_pack": _run_spec_payload(run_spec),
                    "selected_user_id": run_user_id,
                    "selected_chat_id": run_chat_id,
                    "matched_case_count": ((regression_payload.get("summary") or {}).get("matched_case_count") or 0),
                    "mismatched_case_count": ((regression_payload.get("summary") or {}).get("mismatched_case_count") or 0),
                    "leader_names": leader_names,
                    "baseline_results": baseline_rows,
                }
            )
            for row in baseline_rows:
                baseline_name = str(row.get("baseline_name") or "unknown")
                _update_aggregate(
                    aggregate=baseline_aggregate.setdefault(
                        baseline_name,
                        _new_aggregate_row(baseline_name),
                    ),
                    row=row,
                    is_leader=baseline_name in leader_names,
                )
                _update_pack_aggregate(
                    pack_aggregate=pack_aggregate,
                    run_spec=run_spec,
                    baseline_name=baseline_name,
                    row=row,
                    is_leader=baseline_name in leader_names,
                )
                _update_category_aggregate(
                    category_aggregate=category_aggregate,
                    baseline_name=baseline_name,
                    category_rows=list(row.get("live_by_category") or []),
                    is_leader=baseline_name in leader_names,
                )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            errors.append(f"run_{index:04d}:{type(exc).__name__}:{exc}")
            run_payloads.append(
                {
                    "run_index": index,
                    "output_dir": str(run_output_dir),
                    "benchmark_pack": _run_spec_payload(run_spec),
                    "selected_user_id": run_user_id,
                    "selected_chat_id": run_chat_id,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
        payload = _build_soak_payload(
            requested_runs=requested_runs,
            benchmark_pack_rows=benchmark_pack_rows,
            baseline_aggregate=baseline_aggregate,
            category_aggregate=category_aggregate,
            pack_aggregate=pack_aggregate,
            run_payloads=run_payloads,
            errors=errors,
            resolved_write_path=resolved_write_path,
            status="running" if index < requested_runs else "completed",
            baseline_names=resolved_baseline_names,
        )
        resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if sleep_seconds > 0 and index < requested_runs:
            time.sleep(float(sleep_seconds))

    payload = _build_soak_payload(
        requested_runs=requested_runs,
        benchmark_pack_rows=benchmark_pack_rows,
        baseline_aggregate=baseline_aggregate,
        category_aggregate=category_aggregate,
        pack_aggregate=pack_aggregate,
        run_payloads=run_payloads,
        errors=errors,
        resolved_write_path=resolved_write_path,
        status="completed",
        baseline_names=resolved_baseline_names,
    )
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramArchitectureSoakResult(output_dir=resolved_output_dir, payload=payload)


def _build_run_specs(
    *,
    case_ids: list[str] | None,
    categories: list[str] | None,
) -> tuple[_BenchmarkRunSpec, ...]:
    requested_case_ids = tuple(str(item).strip() for item in (case_ids or []) if str(item).strip())
    requested_categories = tuple(str(item).strip() for item in (categories or []) if str(item).strip())
    if requested_case_ids or requested_categories:
        return (
            _BenchmarkRunSpec(
                pack_id="user_selected_slice",
                title="User Selected Slice",
                description="Repeatedly evaluates the exact case/category filters requested on the command line.",
                cases=None,
                case_ids=requested_case_ids,
                categories=requested_categories,
            ),
        )
    return tuple(
        _BenchmarkRunSpec(
            pack_id=pack.pack_id,
            title=pack.title,
            description=pack.description,
            cases=pack.cases,
            case_ids=tuple(case.case_id for case in pack.cases),
            categories=tuple(sorted({case.category for case in pack.cases})),
        )
        for pack in default_telegram_memory_benchmark_packs()
    )


def _resolve_run_namespace(
    *,
    run_index: int,
    run_spec: _BenchmarkRunSpec,
    user_id: str | None,
    chat_id: str | None,
) -> tuple[str, str]:
    base_user_id = str(user_id or "").strip() or "spark-memory-soak-user"
    base_chat_id = str(chat_id or "").strip() or "spark-memory-soak-chat"
    suffix = f"{run_spec.pack_id}-{run_index:04d}"
    return f"{base_user_id}-{suffix}", f"{base_chat_id}-{suffix}"


def _run_spec_payload(run_spec: _BenchmarkRunSpec) -> dict[str, Any]:
    return {
        "pack_id": run_spec.pack_id,
        "title": run_spec.title,
        "description": run_spec.description,
        "case_ids": list(run_spec.case_ids),
        "categories": list(run_spec.categories),
    }


def _new_aggregate_row(name: str) -> dict[str, Any]:
    return {
        "baseline_name": name,
        "run_count": 0,
        "leader_run_count": 0,
        "matched": 0,
        "total": 0,
        "accuracy_sum": 0.0,
        "trust_matched": 0,
        "trust_total": 0,
        "trust_accuracy_sum": 0.0,
        "grounding_matched": 0,
        "grounding_total": 0,
        "grounding_accuracy_sum": 0.0,
        "abstention_matched": 0,
        "abstention_total": 0,
        "abstention_accuracy_sum": 0.0,
        "forbidden_clean": 0,
        "forbidden_total": 0,
        "forbidden_accuracy_sum": 0.0,
    }


def _update_aggregate(
    *,
    aggregate: dict[str, Any],
    row: Any,
    is_leader: bool,
) -> None:
    overall = row if isinstance(row, dict) else {}
    live_overall = overall.get("live_integration_overall") if isinstance(overall.get("live_integration_overall"), dict) else overall
    aggregate["run_count"] += 1
    aggregate["matched"] += int(live_overall.get("matched") or 0)
    aggregate["total"] += int(live_overall.get("total") or 0)
    aggregate["accuracy_sum"] += float(live_overall.get("accuracy") or 0.0)
    if isinstance(row, dict):
        trust = row.get("trustworthiness_overall") or {}
        grounding = row.get("grounding_overall") or {}
        abstention = row.get("abstention_overall") or {}
        forbidden = row.get("forbidden_memory_overall") or {}
        aggregate["trust_matched"] += int(trust.get("matched") or 0)
        aggregate["trust_total"] += int(trust.get("total") or 0)
        aggregate["trust_accuracy_sum"] += float(trust.get("accuracy") or 0.0)
        aggregate["grounding_matched"] += int(grounding.get("matched") or 0)
        aggregate["grounding_total"] += int(grounding.get("total") or 0)
        aggregate["grounding_accuracy_sum"] += float(grounding.get("accuracy") or 0.0)
        aggregate["abstention_matched"] += int(abstention.get("matched") or 0)
        aggregate["abstention_total"] += int(abstention.get("total") or 0)
        aggregate["abstention_accuracy_sum"] += float(abstention.get("accuracy") or 0.0)
        aggregate["forbidden_clean"] += int(forbidden.get("clean") or 0)
        aggregate["forbidden_total"] += int(forbidden.get("total") or 0)
        aggregate["forbidden_accuracy_sum"] += float(forbidden.get("accuracy") or 0.0)
    if is_leader:
        aggregate["leader_run_count"] += 1


def _update_pack_aggregate(
    *,
    pack_aggregate: dict[str, dict[str, Any]],
    run_spec: _BenchmarkRunSpec,
    baseline_name: str,
    row: Any,
    is_leader: bool,
) -> None:
    pack_entry = pack_aggregate.setdefault(
        run_spec.pack_id,
        {
            "pack": _run_spec_payload(run_spec),
            "baseline_aggregate": {},
        },
    )
    aggregate = pack_entry["baseline_aggregate"].setdefault(
        baseline_name,
        _new_aggregate_row(baseline_name),
    )
    _update_aggregate(aggregate=aggregate, row=row, is_leader=is_leader)


def _update_category_aggregate(
    *,
    category_aggregate: dict[str, dict[str, dict[str, Any]]],
    baseline_name: str,
    category_rows: list[dict[str, Any]],
    is_leader: bool,
) -> None:
    for row in category_rows:
        if not isinstance(row, dict):
            continue
        category_name = str(row.get("category") or "unknown")
        aggregate = category_aggregate.setdefault(category_name, {}).setdefault(
            baseline_name,
            _new_aggregate_row(baseline_name),
        )
        _update_aggregate(aggregate=aggregate, row=row, is_leader=is_leader)


def _build_soak_payload(
    *,
    requested_runs: int,
    benchmark_pack_rows: list[dict[str, Any]],
    baseline_aggregate: dict[str, dict[str, Any]],
    category_aggregate: dict[str, dict[str, dict[str, Any]]],
    pack_aggregate: dict[str, dict[str, Any]],
    run_payloads: list[dict[str, Any]],
    errors: list[str],
    resolved_write_path: Path,
    status: str,
    baseline_names: list[str],
) -> dict[str, Any]:
    aggregate_rows = _finalize_aggregate_rows(baseline_aggregate)
    pack_results = _build_pack_results(
        benchmark_pack_rows=benchmark_pack_rows,
        pack_aggregate=pack_aggregate,
    )
    category_results = _build_category_results(category_aggregate)
    best_accuracy = float(aggregate_rows[0].get("aggregate_accuracy") or 0.0) if aggregate_rows else 0.0
    overall_leader_names = (
        [
            row["baseline_name"]
            for row in aggregate_rows
            if float(row.get("aggregate_accuracy") or 0.0) == best_accuracy
        ]
        if best_accuracy > 0.0
        else []
    )
    covered_categories = sorted(
        {
            str(category)
            for pack in benchmark_pack_rows
            for category in list(pack.get("categories") or [])
            if str(category).strip()
        }
    )
    quality_lane_coverage = {lane: lane in set(covered_categories) for lane in QUALITY_LANE_KEYS}
    summary = {
        "status": status,
        "requested_runs": requested_runs,
        "completed_runs": sum(1 for run in run_payloads if not run.get("error")),
        "failed_runs": sum(1 for run in run_payloads if run.get("error")),
        "benchmark_mode": "varied_pack_suite" if len(benchmark_pack_rows) > 1 else "fixed_selection",
        "memory_namespace_mode": "isolated_per_run",
        "baseline_names": list(baseline_names),
        "benchmark_pack_count": len(benchmark_pack_rows),
        "benchmark_pack_ids": [str(pack.get("pack_id") or "unknown") for pack in benchmark_pack_rows],
        "covered_categories": covered_categories,
        "quality_lane_coverage": quality_lane_coverage,
        "overall_leader_names": overall_leader_names,
        "recommended_top_two": [row["baseline_name"] for row in aggregate_rows[:2]],
    }
    return {
        "summary": summary,
        "aggregate_results": aggregate_rows,
        "benchmark_packs": benchmark_pack_rows,
        "benchmark_pack_results": pack_results,
        "category_results": category_results,
        "runs": run_payloads,
        "errors": errors,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
        },
    }


def _finalize_aggregate_rows(baseline_aggregate: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in baseline_aggregate.values():
        run_count = int(item.get("run_count") or 0)
        total = int(item.get("total") or 0)
        matched = int(item.get("matched") or 0)
        trust_total = int(item.get("trust_total") or 0)
        trust_matched = int(item.get("trust_matched") or 0)
        grounding_total = int(item.get("grounding_total") or 0)
        grounding_matched = int(item.get("grounding_matched") or 0)
        abstention_total = int(item.get("abstention_total") or 0)
        abstention_matched = int(item.get("abstention_matched") or 0)
        forbidden_total = int(item.get("forbidden_total") or 0)
        forbidden_clean = int(item.get("forbidden_clean") or 0)
        rows.append(
            {
                "baseline_name": item["baseline_name"],
                "run_count": run_count,
                "leader_run_count": int(item.get("leader_run_count") or 0),
                "matched": matched,
                "total": total,
                "aggregate_accuracy": (matched / total) if total else 0.0,
                "mean_run_accuracy": (float(item.get("accuracy_sum") or 0.0) / run_count) if run_count else 0.0,
                "trustworthiness_accuracy": (trust_matched / trust_total) if trust_total else 0.0,
                "mean_trustworthiness_accuracy": (
                    float(item.get("trust_accuracy_sum") or 0.0) / run_count
                ) if run_count else 0.0,
                "grounding_accuracy": (grounding_matched / grounding_total) if grounding_total else 0.0,
                "mean_grounding_accuracy": (
                    float(item.get("grounding_accuracy_sum") or 0.0) / run_count
                ) if run_count else 0.0,
                "abstention_accuracy": (abstention_matched / abstention_total) if abstention_total else 0.0,
                "mean_abstention_accuracy": (
                    float(item.get("abstention_accuracy_sum") or 0.0) / run_count
                ) if run_count else 0.0,
                "forbidden_clean_accuracy": (forbidden_clean / forbidden_total) if forbidden_total else 0.0,
                "mean_forbidden_clean_accuracy": (
                    float(item.get("forbidden_accuracy_sum") or 0.0) / run_count
                ) if run_count else 0.0,
            }
        )
    rows.sort(
        key=lambda row: (
            float(row.get("aggregate_accuracy") or 0.0),
            float(row.get("trustworthiness_accuracy") or 0.0),
            float(row.get("grounding_accuracy") or 0.0),
            float(row.get("abstention_accuracy") or 0.0),
            float(row.get("forbidden_clean_accuracy") or 0.0),
            int(row.get("leader_run_count") or 0),
            float(row.get("mean_run_accuracy") or 0.0),
        ),
        reverse=True,
    )
    return rows


def _build_pack_results(
    *,
    benchmark_pack_rows: list[dict[str, Any]],
    pack_aggregate: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pack in benchmark_pack_rows:
        pack_id = str(pack.get("pack_id") or "unknown")
        aggregate_rows = _finalize_aggregate_rows(
            (pack_aggregate.get(pack_id) or {}).get("baseline_aggregate") or {}
        )
        best_accuracy = float(aggregate_rows[0].get("aggregate_accuracy") or 0.0) if aggregate_rows else 0.0
        rows.append(
            {
                **pack,
                "leader_names": (
                    [
                        row["baseline_name"]
                        for row in aggregate_rows
                        if float(row.get("aggregate_accuracy") or 0.0) == best_accuracy
                    ]
                    if best_accuracy > 0.0
                    else []
                ),
                "recommended_top_two": [row["baseline_name"] for row in aggregate_rows[:2]],
                "baseline_results": aggregate_rows,
            }
        )
    return rows


def _build_category_results(
    category_aggregate: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category_name in sorted(category_aggregate):
        aggregate_rows = _finalize_aggregate_rows(category_aggregate[category_name])
        best_accuracy = float(aggregate_rows[0].get("aggregate_accuracy") or 0.0) if aggregate_rows else 0.0
        rows.append(
            {
                "category": category_name,
                "leader_names": (
                    [
                        row["baseline_name"]
                        for row in aggregate_rows
                        if float(row.get("aggregate_accuracy") or 0.0) == best_accuracy
                    ]
                    if best_accuracy > 0.0
                    else []
                ),
                "baseline_results": aggregate_rows,
            }
        )
    return rows
