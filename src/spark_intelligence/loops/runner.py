from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import run_chip_hook


@dataclass
class RoundResult:
    round_index: int
    suggestions_count: int
    evaluations: list[dict[str, Any]]
    best_verdict: str | None = None
    best_metric: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LoopResult:
    ok: bool
    chip_key: str
    rounds_completed: int
    total_rounds: int
    history: list[dict[str, Any]] = field(default_factory=list)
    status_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _extract_best(evaluations: list[dict[str, Any]]) -> tuple[str | None, float | None]:
    best_verdict: str | None = None
    best_metric: float | None = None
    for ev in evaluations:
        verdict = ev.get("verdict")
        metrics = ev.get("metrics") or {}
        score = None
        for key in ("primary", "score", "fitness", "value"):
            score = _coerce_float(metrics.get(key) if isinstance(metrics, dict) else None)
            if score is not None:
                break
        if score is not None and (best_metric is None or score > best_metric):
            best_metric = score
            best_verdict = str(verdict) if verdict is not None else None
    return best_verdict, best_metric


def run_chip_autoloop(
    *,
    config_manager,
    chip_key: str,
    rounds: int = 3,
    suggest_limit: int = 3,
    artifacts_root: Path | None = None,
    pause_seconds: float = 0.0,
) -> LoopResult:
    rounds = max(1, int(rounds))
    suggest_limit = max(1, int(suggest_limit))
    artifacts_root = artifacts_root or Path.home() / ".spark-intelligence" / "loops"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    status_path = artifacts_root / f"{chip_key}.status.json"

    history: list[dict[str, Any]] = []

    def _write_status(round_idx: int) -> None:
        payload = {
            "chip_key": chip_key,
            "rounds_completed": round_idx,
            "total_rounds": rounds,
            "history": history,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for round_idx in range(1, rounds + 1):
        try:
            suggest_exec = run_chip_hook(
                config_manager,
                chip_key=chip_key,
                hook="suggest",
                payload={"history": history, "round": round_idx, "limit": suggest_limit},
            )
        except Exception as exc:
            return LoopResult(
                ok=False,
                chip_key=chip_key,
                rounds_completed=round_idx - 1,
                total_rounds=rounds,
                history=history,
                status_path=str(status_path),
                error=f"suggest failed at round {round_idx}: {exc}",
            )
        if not suggest_exec.ok:
            return LoopResult(
                ok=False,
                chip_key=chip_key,
                rounds_completed=round_idx - 1,
                total_rounds=rounds,
                history=history,
                status_path=str(status_path),
                error=f"suggest exit {suggest_exec.exit_code} at round {round_idx}: {suggest_exec.stderr[:200]}",
            )

        suggestions_raw = suggest_exec.output.get("suggestions")
        if not isinstance(suggestions_raw, list):
            suggestions_raw = []
        suggestions = suggestions_raw[:suggest_limit]

        evaluations: list[dict[str, Any]] = []
        for s in suggestions:
            try:
                eval_exec = run_chip_hook(
                    config_manager,
                    chip_key=chip_key,
                    hook="evaluate",
                    payload={"candidate": s, "round": round_idx},
                )
            except Exception as exc:
                evaluations.append({"candidate": s, "error": str(exc)})
                continue
            if not eval_exec.ok:
                evaluations.append({
                    "candidate": s,
                    "error": f"evaluate exit {eval_exec.exit_code}: {eval_exec.stderr[:200]}",
                })
                continue
            evaluations.append({
                "candidate": s,
                "verdict": eval_exec.output.get("verdict"),
                "metrics": eval_exec.output.get("metrics"),
                "confidence": eval_exec.output.get("confidence"),
            })

        best_verdict, best_metric = _extract_best(evaluations)
        round_record = RoundResult(
            round_index=round_idx,
            suggestions_count=len(suggestions),
            evaluations=evaluations,
            best_verdict=best_verdict,
            best_metric=best_metric,
        ).to_dict()
        history.append(round_record)
        _write_status(round_idx)

        if pause_seconds > 0 and round_idx < rounds:
            time.sleep(pause_seconds)

    return LoopResult(
        ok=True,
        chip_key=chip_key,
        rounds_completed=rounds,
        total_rounds=rounds,
        history=history,
        status_path=str(status_path),
    )
