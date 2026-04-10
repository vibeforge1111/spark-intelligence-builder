from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.regression import run_telegram_memory_regression
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
            lines.append(f"- leaders: {', '.join(summary.get('overall_leader_names') or []) or 'unknown'}")
            lines.append(
                f"- recommended_top_two: {', '.join(summary.get('recommended_top_two') or []) or 'unknown'}"
            )
        return "\n".join(lines)


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
) -> TelegramArchitectureSoakResult:
    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-architecture-soak"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-architecture-soak.json"

    requested_runs = max(int(runs), 1)
    baseline_aggregate: dict[str, dict[str, Any]] = {}
    run_payloads: list[dict[str, Any]] = []
    errors: list[str] = []

    for index in range(1, requested_runs + 1):
        run_output_dir = resolved_output_dir / f"run-{index:04d}"
        try:
            regression_result = run_telegram_memory_regression(
                config_manager=config_manager,
                state_db=state_db,
                output_dir=run_output_dir,
                user_id=user_id,
                username=username,
                chat_id=chat_id,
                kb_limit=kb_limit,
                validator_root=validator_root,
                write_path=run_output_dir / "telegram-memory-regression.json",
                case_ids=case_ids,
                categories=categories,
            )
            regression_payload = regression_result.payload if isinstance(regression_result.payload, dict) else {}
            live_comparison_payload = (
                regression_payload.get("architecture_live_comparison")
                if isinstance(regression_payload.get("architecture_live_comparison"), dict)
                else {}
            )
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
                    "matched_case_count": ((regression_payload.get("summary") or {}).get("matched_case_count") or 0),
                    "mismatched_case_count": ((regression_payload.get("summary") or {}).get("mismatched_case_count") or 0),
                    "leader_names": leader_names,
                    "baseline_results": baseline_rows,
                }
            )
            for row in baseline_rows:
                baseline_name = str(row.get("baseline_name") or "unknown")
                aggregate = baseline_aggregate.setdefault(
                    baseline_name,
                    {
                        "baseline_name": baseline_name,
                        "run_count": 0,
                        "leader_run_count": 0,
                        "matched": 0,
                        "total": 0,
                        "accuracy_sum": 0.0,
                    },
                )
                overall = row.get("live_integration_overall") or {}
                aggregate["run_count"] += 1
                aggregate["matched"] += int(overall.get("matched") or 0)
                aggregate["total"] += int(overall.get("total") or 0)
                aggregate["accuracy_sum"] += float(overall.get("accuracy") or 0.0)
                if baseline_name in leader_names:
                    aggregate["leader_run_count"] += 1
        except Exception as exc:  # pragma: no cover - defensive runtime path
            errors.append(f"run_{index:04d}:{type(exc).__name__}:{exc}")
            run_payloads.append(
                {
                    "run_index": index,
                    "output_dir": str(run_output_dir),
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
        payload = _build_soak_payload(
            requested_runs=requested_runs,
            baseline_aggregate=baseline_aggregate,
            run_payloads=run_payloads,
            errors=errors,
            resolved_write_path=resolved_write_path,
            status="running" if index < requested_runs else "completed",
        )
        resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if sleep_seconds > 0 and index < requested_runs:
            time.sleep(float(sleep_seconds))

    payload = _build_soak_payload(
        requested_runs=requested_runs,
        baseline_aggregate=baseline_aggregate,
        run_payloads=run_payloads,
        errors=errors,
        resolved_write_path=resolved_write_path,
        status="completed",
    )
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramArchitectureSoakResult(output_dir=resolved_output_dir, payload=payload)


def _build_soak_payload(
    *,
    requested_runs: int,
    baseline_aggregate: dict[str, dict[str, Any]],
    run_payloads: list[dict[str, Any]],
    errors: list[str],
    resolved_write_path: Path,
    status: str,
) -> dict[str, Any]:
    aggregate_rows = []
    for item in baseline_aggregate.values():
        run_count = int(item.get("run_count") or 0)
        total = int(item.get("total") or 0)
        matched = int(item.get("matched") or 0)
        aggregate_rows.append(
            {
                "baseline_name": item["baseline_name"],
                "run_count": run_count,
                "leader_run_count": int(item.get("leader_run_count") or 0),
                "matched": matched,
                "total": total,
                "aggregate_accuracy": (matched / total) if total else 0.0,
                "mean_run_accuracy": (float(item.get("accuracy_sum") or 0.0) / run_count) if run_count else 0.0,
            }
        )
    aggregate_rows.sort(
        key=lambda row: (
            float(row.get("aggregate_accuracy") or 0.0),
            int(row.get("leader_run_count") or 0),
            float(row.get("mean_run_accuracy") or 0.0),
        ),
        reverse=True,
    )
    best_accuracy = float(aggregate_rows[0].get("aggregate_accuracy") or 0.0) if aggregate_rows else 0.0
    overall_leader_names = [
        row["baseline_name"]
        for row in aggregate_rows
        if float(row.get("aggregate_accuracy") or 0.0) == best_accuracy
    ]
    summary = {
        "status": status,
        "requested_runs": requested_runs,
        "completed_runs": sum(1 for run in run_payloads if not run.get("error")),
        "failed_runs": sum(1 for run in run_payloads if run.get("error")),
        "overall_leader_names": overall_leader_names,
        "recommended_top_two": [row["baseline_name"] for row in aggregate_rows[:2]],
    }
    return {
        "summary": summary,
        "aggregate_results": aggregate_rows,
        "runs": run_payloads,
        "errors": errors,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
        },
    }
