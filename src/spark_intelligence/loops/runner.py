from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import resolve_chip_record, run_chip_hook


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
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


_PRIMARY_METRIC_KEYS = (
    "primary", "score", "fitness", "value", "metric",
    "lab_research_quality_score", "portfolio_health", "quality_score",
)

_STATUS_KEYS = ("verdict", "status", "comparison_class", "outcome", "decision")
_HOOK_ERROR_TAIL_CHARS = 2000


def _clip_hook_text(value: Any, *, limit: int = _HOOK_ERROR_TAIL_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"...{text[-limit:]}"


def _hook_failure_summary(prefix: str, execution: Any) -> str:
    exit_code = getattr(execution, "exit_code", "unknown")
    stderr = _clip_hook_text(getattr(execution, "stderr", ""))
    stdout = _clip_hook_text(getattr(execution, "stdout", ""))
    parts = [f"{prefix} exit {exit_code}"]
    if stderr:
        parts.append(f"stderr: {stderr}")
    if stdout:
        parts.append(f"stdout: {stdout}")
    return "; ".join(parts)


def _extract_primary(output: dict[str, Any]) -> tuple[str | None, float | None]:
    """Find the most interesting status string + primary metric in a hook output.

    Tries common key names first, then falls back to the first top-level scalar.
    """
    if not isinstance(output, dict):
        return None, None
    status: str | None = None
    for key in _STATUS_KEYS:
        raw = output.get(key)
        if isinstance(raw, str) and raw.strip():
            status = raw.strip()
            break
    result_sub = output.get("result") if isinstance(output.get("result"), dict) else {}
    if status is None:
        for key in _STATUS_KEYS:
            raw = result_sub.get(key) if result_sub else None
            if isinstance(raw, str) and raw.strip():
                status = raw.strip()
                break
    metrics_sub = output.get("metrics") if isinstance(output.get("metrics"), dict) else {}
    metric: float | None = None
    for key in _PRIMARY_METRIC_KEYS:
        metric = _coerce_float(metrics_sub.get(key)) if metrics_sub else None
        if metric is not None:
            break
        metric = _coerce_float(output.get(key))
        if metric is not None:
            break
    if metric is None and metrics_sub:
        score_keys = [
            key for key in metrics_sub
            if isinstance(key, str) and "score" in key.lower()
        ]
        quality_keys = [
            key for key in metrics_sub
            if isinstance(key, str) and key.lower().endswith("_quality") and key not in score_keys
        ]
        for key in [*score_keys, *quality_keys, *metrics_sub.keys()]:
            metric = _coerce_float(metrics_sub.get(key))
            if metric is not None:
                break
    if metric is None and result_sub:
        for value in result_sub.values():
            metric = _coerce_float(value)
            if metric is not None:
                break
    if metric is None:
        for v in output.values():
            metric = _coerce_float(v)
            if metric is not None:
                break
    return status, metric


def _extract_best(evaluations: list[dict[str, Any]]) -> tuple[str | None, float | None]:
    best_verdict: str | None = None
    best_metric: float | None = None
    for ev in evaluations:
        output = ev.get("output") if isinstance(ev.get("output"), dict) else None
        if output is None:
            output = {
                "metrics": ev.get("metrics"),
                "verdict": ev.get("verdict"),
                "confidence": ev.get("confidence"),
            }
        status, metric = _extract_primary(output)
        if metric is not None and (best_metric is None or metric > best_metric):
            best_metric = metric
            best_verdict = status
    return best_verdict, best_metric


def _recent_mutations_from_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent: list[dict[str, Any]] = []
    for round_record in history:
        if not isinstance(round_record, dict):
            continue
        round_index = round_record.get("round_index")
        evaluations = round_record.get("evaluations")
        if not isinstance(evaluations, list):
            continue
        for evaluation in evaluations:
            if not isinstance(evaluation, dict):
                continue
            candidate = evaluation.get("candidate")
            if not isinstance(candidate, dict):
                continue
            mutations = candidate.get("mutations")
            if not isinstance(mutations, dict):
                continue
            output = evaluation.get("output") if isinstance(evaluation.get("output"), dict) else {}
            verdict, metric = _extract_primary(output)
            recent.append({
                "round_index": round_index,
                "mutations": dict(mutations),
                "metric": metric,
                "verdict": verdict,
            })
    return recent


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _round_eval_error_counts(history: list[dict[str, Any]]) -> list[int]:
    counts: list[int] = []
    for round_record in history:
        evaluations = round_record.get("evaluations") if isinstance(round_record, dict) else None
        if not isinstance(evaluations, list):
            counts.append(0)
            continue
        counts.append(sum(1 for item in evaluations if isinstance(item, dict) and "error" in item))
    return counts


def _round_candidate_mutations(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    for round_record in history:
        if not isinstance(round_record, dict):
            continue
        candidates: list[dict[str, Any]] = []
        evaluations = round_record.get("evaluations")
        if isinstance(evaluations, list):
            for evaluation in evaluations:
                candidate = evaluation.get("candidate") if isinstance(evaluation, dict) else None
                mutations = candidate.get("mutations") if isinstance(candidate, dict) else None
                if isinstance(mutations, dict):
                    candidates.append(dict(mutations))
        rounds.append({
            "round_index": round_record.get("round_index"),
            "candidate_mutations": candidates,
        })
    return rounds


def _best_metric_by_round(history: list[dict[str, Any]]) -> list[float | None]:
    return [
        _coerce_float(round_record.get("best_metric")) if isinstance(round_record, dict) else None
        for round_record in history
    ]


def _resolve_chip_root(config_manager: Any, chip_key: str) -> Path | None:
    try:
        record = resolve_chip_record(config_manager, chip_key=chip_key)
    except Exception:
        return None
    return Path(record.repo_root)


def _write_canonical_loop_evidence(
    *,
    config_manager: Any,
    chip_key: str,
    status_payload: dict[str, Any],
    status_path: Path,
) -> None:
    chip_root = _resolve_chip_root(config_manager, chip_key)
    if chip_root is None:
        return
    reports_dir = chip_root / "reports"
    history = status_payload.get("history") if isinstance(status_payload.get("history"), list) else []
    best_metrics = _best_metric_by_round(history)
    eval_errors = _round_eval_error_counts(history)
    best_metric = max([metric for metric in best_metrics if metric is not None], default=None)
    evidence = {
        "schema_version": "spark-domain-chip.loop_runner_evidence.v1",
        "chip_key": chip_key,
        "source": "spark_intelligence.loops.runner",
        "status_path": str(status_path),
        "rounds_completed": status_payload.get("rounds_completed"),
        "total_rounds": status_payload.get("total_rounds"),
        "updated_at": status_payload.get("updated_at"),
        "best_metric": best_metric,
        "best_metric_by_round": best_metrics,
        "best_verdict_by_round": [
            round_record.get("best_verdict") if isinstance(round_record, dict) else None
            for round_record in history
        ],
        "eval_errors_by_round": eval_errors,
        "candidate_mutations_by_round": _round_candidate_mutations(history),
        "promotion_blocked": True,
        "network_absorbable": False,
        "private_local_only": True,
        "claim_boundary": (
            "Loop-runner evidence binds local candidate evaluation state only; it does not prove "
            "self-improvement, transfer, publication, activation, network absorption, or release readiness."
        ),
        "claims_supported": [
            "local_loop_executed",
            "candidate_evaluations_recorded",
            "status_persisted",
        ],
        "claims_not_supported": [
            "self_improvement_proven",
            "consumer_transfer_supported",
            "sealed_hidden_evaluation_passed",
            "published",
            "activated",
            "network_absorbable",
            "release_ready",
        ],
    }
    evidence_path = reports_dir / "loop-runner-evidence.json"
    _write_json(evidence_path, evidence)

    long_loop_path = reports_dir / "long-loop-trend.json"
    long_loop = _read_json_dict(long_loop_path)
    long_loop["loop_runner_binding"] = {
        "schema_version": "spark-domain-chip.loop_runner_binding.v1",
        "evidence_ref": "reports/loop-runner-evidence.json",
        "rounds_completed": status_payload.get("rounds_completed"),
        "total_rounds": status_payload.get("total_rounds"),
        "best_metric_by_round": best_metrics,
        "eval_errors_by_round": eval_errors,
        "candidate_trend_only": True,
        "self_improvement_proven": False,
        "claim_boundary": evidence["claim_boundary"],
    }
    long_loop["promotion_blocked"] = True
    long_loop["network_absorbable"] = False
    _write_json(long_loop_path, long_loop)

    qa_path = reports_dir / "qa-evidence-lane-packet.json"
    qa_packet = _read_json_dict(qa_path)
    qa_packet["loop_runner_evidence"] = {
        "schema_version": "spark-domain-chip.qa_loop_runner_evidence_ref.v1",
        "evidence_ref": "reports/loop-runner-evidence.json",
        "status_path": str(status_path),
        "rounds_completed": status_payload.get("rounds_completed"),
        "total_rounds": status_payload.get("total_rounds"),
        "candidate_trend_only": True,
        "self_improvement_proven": False,
    }
    qa_packet["promotion_blocked"] = True
    qa_packet["network_absorbable"] = False
    _write_json(qa_path, qa_packet)

    proof_path = reports_dir / "proof-capsule-starter.json"
    proof_capsule = _read_json_dict(proof_path)
    proof = proof_capsule.get("proof") if isinstance(proof_capsule.get("proof"), dict) else {}
    proof["loop_runner_evidence"] = {
        "status": "candidate_trend_bound",
        "path": "reports/loop-runner-evidence.json",
        "rounds_completed": status_payload.get("rounds_completed"),
        "total_rounds": status_payload.get("total_rounds"),
        "best_metric_by_round": best_metrics,
        "eval_errors_by_round": eval_errors,
        "self_improvement_proven": False,
        "promotion_blocked": True,
    }
    proof_capsule["proof"] = proof
    proof_capsule["network_absorbable"] = False
    proof_capsule["promotion_blocked"] = True
    _write_json(proof_path, proof_capsule)


def run_chip_autoloop(
    *,
    config_manager,
    chip_key: str,
    rounds: int = 3,
    suggest_limit: int = 3,
    artifacts_root: Path | None = None,
    pause_seconds: float = 0.0,
    suggest_governor_decision: dict[str, Any] | None = None,
    evaluate_governor_decision: dict[str, Any] | None = None,
) -> LoopResult:
    rounds = max(1, int(rounds))
    suggest_limit = max(1, int(suggest_limit))
    if not isinstance(suggest_governor_decision, dict) or not isinstance(evaluate_governor_decision, dict):
        return LoopResult(
            ok=False,
            chip_key=chip_key,
            rounds_completed=0,
            total_rounds=rounds,
            error="chip autoloop requires Harness Core Governor authority for chip.suggest and chip.evaluate",
        )
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
        _write_canonical_loop_evidence(
            config_manager=config_manager,
            chip_key=chip_key,
            status_payload=payload,
            status_path=status_path,
        )

    for round_idx in range(1, rounds + 1):
        try:
            suggest_exec = run_chip_hook(
                config_manager,
                chip_key=chip_key,
                hook="suggest",
                payload={
                    "history": history,
                    "recent_mutations": _recent_mutations_from_history(history),
                    "round": round_idx,
                    "limit": suggest_limit,
                    "chip_key": chip_key,
                    "cold_start": round_idx == 1 and not history,
                },
                governor_decision=suggest_governor_decision,
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
                error=f"{_hook_failure_summary('suggest', suggest_exec)} at round {round_idx}",
            )

        suggestions_raw = suggest_exec.output.get("suggestions")
        if not isinstance(suggestions_raw, list):
            suggestions_raw = []
        suggestions = suggestions_raw[:suggest_limit]

        # Cold-start bootstrap: if a chip returns no suggestions on the first
        # round with empty history, inject a probe candidate so evaluate still
        # fires. Chips with sophisticated suggest logic typically need state
        # (research frontier, prior trials) that doesn't exist on first run.
        if not suggestions and round_idx == 1 and not history:
            suggestions = [
                {
                    "candidate_id": f"bootstrap-{chip_key}",
                    "candidate_summary": f"Cold-start bootstrap probe for {chip_key}",
                    "hypothesis": "No prior state; triggering evaluate to capture baseline metrics.",
                    "mutations": {"bootstrap": True},
                    "priority": "low",
                }
            ]

        evaluations: list[dict[str, Any]] = []
        for s in suggestions:
            try:
                eval_exec = run_chip_hook(
                    config_manager,
                    chip_key=chip_key,
                    hook="evaluate",
                    payload={"candidate": s, "round": round_idx},
                    governor_decision=evaluate_governor_decision,
                )
            except Exception as exc:
                evaluations.append({"candidate": s, "error": str(exc)})
                continue
            if not eval_exec.ok:
                evaluations.append({
                    "candidate": s,
                    "error": _hook_failure_summary("evaluate", eval_exec),
                })
                continue
            evaluations.append({
                "candidate": s,
                "output": eval_exec.output,
            })

        best_verdict, best_metric = _extract_best(evaluations)
        successful_evaluations = [
            ev for ev in evaluations
            if isinstance(ev.get("output"), dict) and "error" not in ev
        ]
        round_record = RoundResult(
            round_index=round_idx,
            suggestions_count=len(suggestions),
            evaluations=evaluations,
            best_verdict=best_verdict,
            best_metric=best_metric,
        ).to_dict()
        history.append(round_record)
        _write_status(round_idx)

        if suggestions and not successful_evaluations:
            return LoopResult(
                ok=False,
                chip_key=chip_key,
                rounds_completed=round_idx - 1,
                total_rounds=rounds,
                history=history,
                status_path=str(status_path),
                error=f"evaluate failed for all {len(suggestions)} candidate(s) at round {round_idx}",
            )
        if suggestions and best_metric is None:
            return LoopResult(
                ok=False,
                chip_key=chip_key,
                rounds_completed=round_idx - 1,
                total_rounds=rounds,
                history=history,
                status_path=str(status_path),
                error=f"evaluate returned no primary metric for {len(successful_evaluations)} candidate(s) at round {round_idx}",
            )

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
