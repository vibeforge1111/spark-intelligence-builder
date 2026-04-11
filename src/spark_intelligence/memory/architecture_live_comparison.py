from __future__ import annotations

import json
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.architecture_benchmark import (
    ACTIVE_MEMORY_ARCHITECTURE_CONTENDERS,
    DEFAULT_DOMAIN_CHIP_MEMORY_ROOT,
    DOCUMENTED_FRONTIER_PROVIDER,
    PRODUCT_MEMORY_BASELINES,
    resolve_memory_architecture_baselines,
)


LIVE_COMPARISON_BASELINES: tuple[str, ...] = ACTIVE_MEMORY_ARCHITECTURE_CONTENDERS
_WRITE_BRIDGE_MODE = "memory_profile_fact_update"
_DEFAULT_BENCHMARK_NAME = "ProductMemory"
_DEFAULT_OUTPUT_DIR_NAME = "telegram-memory-architecture-live-comparison"
_BASE_TIMESTAMP = datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc)
_EXPLANATION_STYLE_ONLY_FRAGMENTS = frozenset({"saved memory record"})
_ABSTENTION_EQUIVALENT_ANSWERS = frozenset(
    {
        "unknown",
        "i don't know",
        "i do not know",
        "don't currently have that saved",
        "do not currently have that saved",
        "i don't currently have that saved",
        "i do not currently have that saved",
        "not enough information",
    }
)


@dataclass(frozen=True)
class TelegramArchitectureLiveComparisonResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark memory live architecture comparison"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- compared_cases: {summary.get('case_count', 0)}")
            lines.append(
                f"- leaders: {', '.join(summary.get('leader_names') or []) or 'unknown'}"
            )
            lines.append(
                f"- recommended_runtime_architecture: "
                f"{summary.get('recommended_runtime_architecture') or 'undecided'}"
            )
            lines.append(
                f"- current_runtime_memory_architecture: "
                f"{summary.get('current_runtime_memory_architecture') or 'unknown'}"
            )
            lines.append(
                f"- runtime_matches_live_leader: "
                f"{'yes' if summary.get('runtime_matches_live_leader') else 'no'}"
            )
        errors = self.payload.get("errors") if isinstance(self.payload, dict) else None
        if isinstance(errors, list) and errors:
            lines.append(f"- errors: {len(errors)}")
        return "\n".join(lines)


def compare_telegram_memory_architectures(
    *,
    config_manager: ConfigManager,
    case_payloads: Sequence[dict[str, Any]],
    selected_cases: Sequence[Any],
    output_dir: str | Path | None = None,
    validator_root: str | Path | None = None,
    baseline_names: Sequence[str] | None = None,
) -> TelegramArchitectureLiveComparisonResult:
    resolved_output_dir = Path(output_dir) if output_dir else _default_output_dir(config_manager)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = resolved_output_dir / "telegram-memory-architecture-live-comparison.json"
    resolved_summary_path = resolved_output_dir / "telegram-memory-architecture-live-comparison.md"

    sample_specs = build_telegram_regression_sample_specs(
        case_payloads=case_payloads,
        selected_cases=selected_cases,
    )
    validator_path = Path(validator_root) if validator_root else DEFAULT_DOMAIN_CHIP_MEMORY_ROOT
    errors: list[str] = []
    try:
        resolved_baseline_names = resolve_memory_architecture_baselines(
            baseline_names,
            allowed_baselines=PRODUCT_MEMORY_BASELINES,
            default_baselines=LIVE_COMPARISON_BASELINES,
        )
    except ValueError as exc:
        resolved_baseline_names = ()
        errors.append(str(exc))
    baseline_rows: list[dict[str, Any]] = []
    current_runtime_sdk_class = "unknown"
    current_runtime_memory_architecture = "unknown"

    if not sample_specs:
        errors.append("no_comparable_cases")
    elif not resolved_baseline_names:
        errors.append("no_baselines_selected")
    elif not validator_path.exists():
        errors.append(f"validator_root_missing:{validator_path}")
    else:
        try:
            baseline_rows, contract_summary = _run_live_comparison_scorecards(
                sample_specs=sample_specs,
                validator_path=validator_path,
                baseline_names=resolved_baseline_names,
            )
            current_runtime_sdk_class = str(contract_summary.get("runtime_class") or "unknown")
            current_runtime_memory_architecture = str(
                contract_summary.get("runtime_memory_architecture") or "unknown"
            )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            errors.append(f"live_comparison_failed:{type(exc).__name__}:{exc}")

    leader_rows = _leader_rows(baseline_rows)
    case_summaries = [_sample_case_summary(spec) for spec in sample_specs]
    summary = {
        "baseline_names": list(resolved_baseline_names),
        "case_count": len(sample_specs),
        "case_ids": [str(item.get("sample_id") or "unknown") for item in sample_specs],
        "categories": sorted(
            {
                str((item.get("questions") or [{}])[0].get("category") or "unknown")
                for item in sample_specs
                if item.get("questions")
            }
        ),
        "leader_names": [row["baseline_name"] for row in leader_rows],
        "leader_accuracy": _safe_accuracy((leader_rows[0].get("live_integration_overall") or {}).get("accuracy"))
        if leader_rows
        else 0.0,
        "recommended_runtime_architecture": leader_rows[0]["baseline_name"] if len(leader_rows) == 1 else None,
        "current_runtime_sdk_class": current_runtime_sdk_class,
        "current_runtime_memory_architecture": current_runtime_memory_architecture,
        "runtime_matches_live_leader": current_runtime_memory_architecture
        in {row["baseline_name"] for row in leader_rows},
        "assessment": _assessment_text(
            leader_rows=leader_rows,
            current_runtime_memory_architecture=current_runtime_memory_architecture,
        ),
    }
    payload = {
        "summary": summary,
        "cases": case_summaries,
        "baseline_results": baseline_rows,
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
            case_summaries=case_summaries,
            baseline_rows=baseline_rows,
            errors=errors,
        ),
        encoding="utf-8",
    )
    return TelegramArchitectureLiveComparisonResult(output_dir=resolved_output_dir, payload=payload)


def build_telegram_regression_sample_specs(
    *,
    case_payloads: Sequence[dict[str, Any]],
    selected_cases: Sequence[Any],
) -> list[dict[str, Any]]:
    sample_specs: list[dict[str, Any]] = []
    history_by_namespace: dict[str, list[dict[str, Any]]] = {}
    for index, (case, payload) in enumerate(zip(selected_cases, case_payloads), start=1):
        namespace = str(getattr(case, "case_id", f"case-{index}")) if getattr(case, "isolate_memory", False) else "shared"
        prior_sessions = list(history_by_namespace.get(namespace, []))
        if _is_comparable_case(case=case, payload=payload):
            question_spec = _build_question_spec(case=case, payload=payload, prior_sessions=prior_sessions)
            sample_specs.append(
                {
                    "benchmark_name": _DEFAULT_BENCHMARK_NAME,
                    "sample_id": str(getattr(case, "case_id", f"case-{index}")),
                    "sessions": prior_sessions,
                    "questions": [question_spec],
                    "metadata": {
                        "source_surface": "spark_telegram_memory_regression",
                        "namespace": namespace,
                        "case_category": str(getattr(case, "category", "unknown")),
                        "bridge_mode": str(payload.get("bridge_mode") or ""),
                        "routing_decision": str(payload.get("routing_decision") or ""),
                    },
                }
            )
        history_by_namespace.setdefault(namespace, []).append(
            _build_session_spec(case=case, payload=payload, index=index, namespace=namespace)
        )
    return sample_specs


def _default_output_dir(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / _DEFAULT_OUTPUT_DIR_NAME


def _is_comparable_case(*, case: Any, payload: dict[str, Any]) -> bool:
    if str(payload.get("decision") or "").strip() != "allowed":
        return False
    if not tuple(getattr(case, "expected_response_contains", ()) or ()):
        return False
    return str(getattr(case, "expected_bridge_mode", "")).strip() != _WRITE_BRIDGE_MODE


def _build_session_spec(*, case: Any, payload: dict[str, Any], index: int, namespace: str) -> dict[str, Any]:
    response_text = str(payload.get("response_text") or "").strip()
    message = str(getattr(case, "message", "") or "").strip()
    user_timestamp = _iso_timestamp(index * 2)
    assistant_timestamp = _iso_timestamp(index * 2 + 1)
    case_id = str(getattr(case, "case_id", f"case-{index}"))
    return {
        "session_id": f"{namespace}:{case_id}",
        "timestamp": user_timestamp,
        "turns": [
            {
                "turn_id": f"{case_id}:user",
                "speaker": "user",
                "text": message,
                "timestamp": user_timestamp,
                "metadata": {"case_id": case_id, "role": "query"},
            },
            {
                "turn_id": f"{case_id}:assistant",
                "speaker": "assistant",
                "text": response_text,
                "timestamp": assistant_timestamp,
                "metadata": {
                    "case_id": case_id,
                    "bridge_mode": str(payload.get("bridge_mode") or ""),
                    "routing_decision": str(payload.get("routing_decision") or ""),
                    "trace_ref": str(payload.get("trace_ref") or ""),
                },
            },
        ],
        "metadata": {
            "case_id": case_id,
            "category": str(getattr(case, "category", "unknown")),
            "bridge_mode": str(payload.get("bridge_mode") or ""),
            "routing_decision": str(payload.get("routing_decision") or ""),
            "matched_expectations": bool(payload.get("matched_expectations", False)),
        },
    }


def _build_question_spec(
    *,
    case: Any,
    payload: dict[str, Any],
    prior_sessions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    case_id = str(getattr(case, "case_id", "unknown"))
    expected_fragments = [str(item).strip() for item in tuple(getattr(case, "expected_response_contains", ()) or ()) if str(item).strip()]
    should_abstain = str(getattr(case, "category", "")).strip() == "abstention" or any(
        "don't currently have that saved" in item.lower() for item in expected_fragments
    )
    evidence_session_ids, evidence_turn_ids = _find_evidence_references(
        prior_sessions=prior_sessions,
        expected_fragments=expected_fragments,
    )
    if not evidence_session_ids and not should_abstain:
        evidence_session_ids = [str(session.get("session_id") or "") for session in prior_sessions if session.get("session_id")]
    if not evidence_turn_ids and not should_abstain:
        evidence_turn_ids = [
            str(turn.get("turn_id") or "")
            for session in prior_sessions
            for turn in list(session.get("turns") or [])
            if isinstance(turn, dict) and turn.get("turn_id")
        ]
    return {
        "question_id": case_id,
        "question": str(getattr(case, "message", "") or "").strip(),
        "category": str(getattr(case, "category", "unknown")),
        "expected_answers": _expected_answer_variants(expected_fragments),
        "evidence_session_ids": evidence_session_ids,
        "evidence_turn_ids": evidence_turn_ids,
        "question_date": _iso_timestamp(500 + len(prior_sessions)),
        "should_abstain": should_abstain,
        "metadata": {
            "sample_id": case_id,
            "product_memory_task": _product_memory_task(case),
            "memory_operation": _memory_operation(case),
            "memory_scope": _memory_scope(case),
            "expected_answer_candidate_source": _expected_answer_candidate_source(case=case, payload=payload),
            "expected_fragments": expected_fragments,
            "expected_forbidden_fragments": list(getattr(case, "expected_response_excludes", ()) or ()),
            "benchmark_tags": list(getattr(case, "benchmark_tags", ()) or ()),
            "bridge_mode": str(payload.get("bridge_mode") or ""),
            "routing_decision": str(payload.get("routing_decision") or ""),
            "trace_ref": str(payload.get("trace_ref") or ""),
        },
    }


def _expected_answer_variants(expected_fragments: Sequence[str]) -> list[str]:
    variants: list[str] = []
    if expected_fragments:
        variants.append(" ".join(expected_fragments))
    variants.extend(expected_fragments)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        normalized = item.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def _find_evidence_references(
    *,
    prior_sessions: Sequence[dict[str, Any]],
    expected_fragments: Sequence[str],
) -> tuple[list[str], list[str]]:
    if not expected_fragments:
        return [], []
    lowered_fragments = [fragment.lower() for fragment in expected_fragments]
    session_ids: list[str] = []
    turn_ids: list[str] = []
    for session in prior_sessions:
        if not isinstance(session, dict):
            continue
        session_id = str(session.get("session_id") or "").strip()
        matched_session = False
        for turn in list(session.get("turns") or []):
            if not isinstance(turn, dict):
                continue
            text = str(turn.get("text") or "").lower()
            if any(fragment in text for fragment in lowered_fragments):
                turn_id = str(turn.get("turn_id") or "").strip()
                if session_id and session_id not in session_ids:
                    session_ids.append(session_id)
                if turn_id and turn_id not in turn_ids:
                    turn_ids.append(turn_id)
                matched_session = True
        if matched_session and session_id and session_id not in session_ids:
            session_ids.append(session_id)
    return session_ids, turn_ids


def _product_memory_task(case: Any) -> str:
    category = str(getattr(case, "category", "unknown"))
    if category == "explanation":
        return "provenance_explanation"
    if category == "identity":
        return "identity_summary"
    if category == "identity_synthesis":
        return "identity_synthesis"
    if category == "abstention":
        return "abstention_guard"
    if category == "inappropriate_memory_use":
        return "anti_overpersonalization"
    if category == "long_term_memory":
        return "long_horizon_retrieval"
    if category == "short_term_memory":
        return "short_horizon_retrieval"
    return "profile_retrieval"


def _memory_operation(case: Any) -> str:
    if str(getattr(case, "category", "")).strip() == "explanation":
        return "explain"
    return "query"


def _memory_scope(case: Any) -> str:
    if str(getattr(case, "category", "")).strip() in {"identity", "identity_synthesis"}:
        return "multi_fact"
    return "single_fact"


def _expected_answer_candidate_source(*, case: Any, payload: dict[str, Any]) -> str | None:
    category = str(getattr(case, "category", "") or "").strip()
    bridge_mode = str(payload.get("bridge_mode") or getattr(case, "expected_bridge_mode", "") or "").strip()
    if category == "explanation" or bridge_mode == "memory_profile_fact_explanation":
        return "evidence_memory"
    return None


def _safe_accuracy(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _leader_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    best_accuracy = max(_safe_accuracy((row.get("live_integration_overall") or {}).get("accuracy")) for row in rows)
    if best_accuracy <= 0.0:
        return []
    leaders = [
        row
        for row in rows
        if _safe_accuracy((row.get("live_integration_overall") or {}).get("accuracy")) == best_accuracy
    ]
    best_trust = max(_safe_accuracy((row.get("trustworthiness_overall") or {}).get("accuracy")) for row in leaders)
    leaders = [
        row
        for row in leaders
        if _safe_accuracy((row.get("trustworthiness_overall") or {}).get("accuracy")) == best_trust
    ]
    best_grounding = max(_safe_accuracy((row.get("grounding_overall") or {}).get("accuracy")) for row in leaders)
    leaders = [
        row
        for row in leaders
        if _safe_accuracy((row.get("grounding_overall") or {}).get("accuracy")) == best_grounding
    ]
    best_scorecard_substantive = max(
        _safe_accuracy((row.get("scorecard_substantive_overall") or {}).get("accuracy")) for row in leaders
    )
    if best_scorecard_substantive > 0.0:
        leaders = [
            row
            for row in leaders
            if _safe_accuracy((row.get("scorecard_substantive_overall") or {}).get("accuracy"))
            == best_scorecard_substantive
        ]
    best_alignment = max(_safe_accuracy((row.get("scorecard_alignment") or {}).get("rate")) for row in leaders)
    return [
        row for row in leaders if _safe_accuracy((row.get("scorecard_alignment") or {}).get("rate")) == best_alignment
    ]


def _assessment_text(*, leader_rows: Sequence[dict[str, Any]], current_runtime_memory_architecture: str) -> str:
    if not leader_rows:
        return (
            "No architecture achieved a positive live signal on the comparable cases, "
            "so there is no runtime recommendation yet."
        )
    leader_names = [row["baseline_name"] for row in leader_rows]
    if current_runtime_memory_architecture in leader_names:
        return (
            f"The current runtime selector already matches the best live integration architecture: "
            f"{', '.join(leader_names)}."
        )
    if len(leader_names) == 1:
        return (
            f"Live Telegram regression favors `{leader_names[0]}` over the other named variants. "
            f"The current runtime selector remains `{current_runtime_memory_architecture}`, so Builder is still "
            "not pinned to the live leader."
        )
    return (
        "Live Telegram regression ended in a tie between "
        f"{', '.join(f'`{name}`' for name in leader_names)}. "
        f"The current runtime selector is `{current_runtime_memory_architecture}`, so Builder is still not pinned "
        "to a live winner."
    )


def _build_summary_markdown(
    *,
    summary: dict[str, Any],
    case_summaries: Sequence[dict[str, Any]],
    baseline_rows: Sequence[dict[str, Any]],
    errors: Sequence[str],
) -> str:
    lines = [
        "# Telegram Memory Architecture Live Comparison",
        "",
        f"- Compared cases: `{summary.get('case_count', 0)}`",
        f"- Compared baselines: `{', '.join(summary.get('baseline_names') or []) or 'none'}`",
        f"- Leaders: `{', '.join(summary.get('leader_names') or []) or 'unknown'}`",
        f"- Recommended runtime architecture: `{summary.get('recommended_runtime_architecture') or 'undecided'}`",
        f"- Current runtime SDK class: `{summary.get('current_runtime_sdk_class') or 'unknown'}`",
        f"- Current runtime memory architecture: `{summary.get('current_runtime_memory_architecture') or 'unknown'}`",
        f"- Runtime matches live leader: `{'yes' if summary.get('runtime_matches_live_leader') else 'no'}`",
        "",
        "## Assessment",
        "",
        summary.get("assessment") or "No assessment available.",
        "",
        "## Case Coverage",
        "",
    ]
    for case in case_summaries:
        lines.append(
            f"- `{case.get('case_id')}`: category `{case.get('category')}`, "
            f"context sessions `{case.get('context_session_count')}`, "
            f"expected fragments `{', '.join(case.get('expected_fragments') or []) or 'none'}`"
        )
    lines.extend(["", "## Baselines", ""])
    for row in baseline_rows:
        overall = row.get("live_integration_overall") or {}
        trust = row.get("trustworthiness_overall") or {}
        grounding = row.get("grounding_overall") or {}
        abstention = row.get("abstention_overall") or {}
        forbidden = row.get("forbidden_memory_overall") or {}
        lines.append(
            f"- `{row.get('baseline_name')}`: matched `{overall.get('matched', 0)}/{overall.get('total', 0)}` "
            f"(accuracy `{_safe_accuracy(overall.get('accuracy')):.4f}`), "
            f"trust `{_safe_accuracy(trust.get('accuracy')):.4f}`, "
            f"grounding `{_safe_accuracy(grounding.get('accuracy')):.4f}`, "
            f"abstention `{_safe_accuracy(abstention.get('accuracy')):.4f}`, "
            f"forbidden-clean `{_safe_accuracy(forbidden.get('accuracy')):.4f}`, "
            f"alignment `{_safe_accuracy((row.get('scorecard_alignment') or {}).get('rate')):.4f}`"
        )
        for category_row in row.get("live_by_category") or []:
            lines.append(
                f"  - {category_row.get('category')}: "
                f"{category_row.get('matched', 0)}/{category_row.get('total', 0)} "
                f"({ _safe_accuracy(category_row.get('accuracy')):.4f})"
            )
    if errors:
        lines.extend(["", "## Errors", ""])
        for item in errors:
            lines.append(f"- `{item}`")
    return "\n".join(lines).rstrip() + "\n"


def _sample_case_summary(sample_spec: dict[str, Any]) -> dict[str, Any]:
    question = (sample_spec.get("questions") or [{}])[0]
    metadata = question.get("metadata") or {}
    return {
        "case_id": str(sample_spec.get("sample_id") or "unknown"),
        "category": str(question.get("category") or "unknown"),
        "question": str(question.get("question") or ""),
        "expected_fragments": list(metadata.get("expected_fragments") or []),
        "forbidden_fragments": list(metadata.get("expected_forbidden_fragments") or []),
        "benchmark_tags": list(metadata.get("benchmark_tags") or []),
        "context_session_count": len(sample_spec.get("sessions") or []),
        "evidence_session_ids": list(question.get("evidence_session_ids") or []),
        "should_abstain": bool(question.get("should_abstain")),
    }


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
            except ValueError:  # pragma: no cover
                pass


def _run_live_comparison_scorecards(
    *,
    sample_specs: Sequence[dict[str, Any]],
    validator_path: Path,
    baseline_names: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    src_path = validator_path / "src"
    with _prepend_sys_path(src_path):
        from domain_chip_memory.contracts import (
            NormalizedBenchmarkSample,
            NormalizedQuestion,
            NormalizedSession,
            NormalizedTurn,
        )
        from domain_chip_memory.providers import get_provider
        from domain_chip_memory.runner import run_baseline
        from domain_chip_memory.sdk import build_sdk_contract_summary

        provider = get_provider(DOCUMENTED_FRONTIER_PROVIDER)
        samples = [
            NormalizedBenchmarkSample(
                benchmark_name=str(spec.get("benchmark_name") or _DEFAULT_BENCHMARK_NAME),
                sample_id=str(spec.get("sample_id") or "unknown"),
                sessions=[
                    NormalizedSession(
                        session_id=str(session.get("session_id") or "unknown"),
                        timestamp=session.get("timestamp"),
                        metadata=dict(session.get("metadata") or {}),
                        turns=[
                            NormalizedTurn(
                                turn_id=str(turn.get("turn_id") or "unknown"),
                                speaker=str(turn.get("speaker") or "unknown"),
                                text=str(turn.get("text") or ""),
                                timestamp=turn.get("timestamp"),
                                metadata=dict(turn.get("metadata") or {}),
                            )
                            for turn in list(session.get("turns") or [])
                        ],
                    )
                    for session in list(spec.get("sessions") or [])
                ],
                questions=[
                    NormalizedQuestion(
                        question_id=str(question.get("question_id") or "unknown"),
                        question=str(question.get("question") or ""),
                        category=str(question.get("category") or "unknown"),
                        expected_answers=list(question.get("expected_answers") or []),
                        evidence_session_ids=list(question.get("evidence_session_ids") or []),
                        evidence_turn_ids=list(question.get("evidence_turn_ids") or []),
                        question_date=question.get("question_date"),
                        should_abstain=bool(question.get("should_abstain")),
                        metadata=dict(question.get("metadata") or {}),
                    )
                    for question in list(spec.get("questions") or [])
                ],
                metadata=dict(spec.get("metadata") or {}),
            )
            for spec in sample_specs
        ]
        contract_summary = dict(build_sdk_contract_summary() or {})
        baseline_rows: list[dict[str, Any]] = []
        for baseline_name in baseline_names:
            scorecard = run_baseline(
                samples,
                baseline_name=baseline_name,
                provider=provider,
                top_k_sessions=2,
                fallback_sessions=1,
            )
            baseline_rows.append(
                _baseline_row(
                    baseline_name=baseline_name,
                    scorecard=scorecard,
                    sample_specs=sample_specs,
                )
            )
        return baseline_rows, contract_summary


def _baseline_row(
    *,
    baseline_name: str,
    scorecard: dict[str, Any],
    sample_specs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    question_lookup = {
        str((spec.get("questions") or [{}])[0].get("question_id") or spec.get("sample_id") or "unknown"): spec
        for spec in sample_specs
        if spec.get("questions")
    }
    prediction_lookup = {
        str(item.get("question_id") or ""): item
        for item in list(scorecard.get("predictions") or [])
        if isinstance(item, dict)
    }
    category_total: Counter[str] = Counter()
    category_matched: Counter[str] = Counter()
    trust_total = 0
    trust_matched = 0
    grounding_total = 0
    grounding_matched = 0
    substantive_scorecard_total = 0
    substantive_scorecard_correct = 0
    abstention_total = 0
    abstention_matched = 0
    forbidden_total = 0
    forbidden_clean = 0
    rendered_predictions: list[dict[str, Any]] = []
    for question_id, spec in question_lookup.items():
        question = (spec.get("questions") or [{}])[0]
        metadata = question.get("metadata") or {}
        category = str(question.get("category") or "unknown")
        predicted = prediction_lookup.get(question_id) or {}
        predicted_answer = str(predicted.get("predicted_answer") or "")
        expected_fragments = [str(item) for item in list(metadata.get("expected_fragments") or [])]
        forbidden_fragments = [str(item) for item in list(metadata.get("expected_forbidden_fragments") or [])]
        required_fragments = _required_live_match_fragments(
            category=category,
            expected_fragments=expected_fragments,
        )
        missing_fragments = [
            fragment for fragment in required_fragments if fragment.lower() not in predicted_answer.lower()
        ]
        forbidden_hits = [
            fragment for fragment in forbidden_fragments if fragment.lower() in predicted_answer.lower()
        ]
        abstention_expected = bool(question.get("should_abstain"))
        abstention_respected = abstention_expected and _is_truthful_abstention_answer(predicted_answer) and not forbidden_hits
        matched = abstention_respected if abstention_expected else not missing_fragments
        retrieved_memory_roles = (
            (predicted.get("metadata") or {}).get("retrieved_memory_roles") or []
            if isinstance(predicted.get("metadata"), dict)
            else []
        )
        evidence_expected = bool(question.get("evidence_session_ids") or question.get("evidence_turn_ids"))
        retrieval_support_present = bool(retrieved_memory_roles)
        grounding_ok = matched and (not evidence_expected or retrieval_support_present)
        trust_total += 1
        if matched and not forbidden_hits and (not abstention_expected or abstention_respected):
            trust_matched += 1
        if evidence_expected:
            grounding_total += 1
            if grounding_ok:
                grounding_matched += 1
        if abstention_expected:
            abstention_total += 1
            if abstention_respected:
                abstention_matched += 1
        if forbidden_fragments:
            forbidden_total += 1
            if not forbidden_hits:
                forbidden_clean += 1
        category_total[category] += 1
        if matched:
            category_matched[category] += 1
        if not _is_explanation_like_prediction(category=category, question_text=str(question.get("question") or "")):
            substantive_scorecard_total += 1
            if bool(predicted.get("is_correct", False)):
                substantive_scorecard_correct += 1
        rendered_predictions.append(
            {
                "case_id": str(spec.get("sample_id") or question_id),
                "question_id": question_id,
                "category": category,
                "question": str(question.get("question") or ""),
                "predicted_answer": predicted_answer,
                "expected_fragments": expected_fragments,
                "forbidden_fragments": forbidden_fragments,
                "matched_expectations": matched,
                "missing_fragments": missing_fragments,
                "forbidden_hits": forbidden_hits,
                "abstention_expected": abstention_expected,
                "abstention_respected": abstention_respected,
                "evidence_expected": evidence_expected,
                "retrieval_support_present": retrieval_support_present,
                "grounding_ok": grounding_ok,
                "scorecard_is_correct": bool(predicted.get("is_correct", False)),
                "retrieved_memory_roles": retrieved_memory_roles,
            }
        )
    matched_total = sum(category_matched.values())
    total = sum(category_total.values())
    return {
        "baseline_name": baseline_name,
        "live_integration_overall": {
            "matched": matched_total,
            "total": total,
            "accuracy": (matched_total / total) if total else 0.0,
        },
        "trustworthiness_overall": {
            "matched": trust_matched,
            "total": trust_total,
            "accuracy": (trust_matched / trust_total) if trust_total else 0.0,
        },
        "grounding_overall": {
            "matched": grounding_matched,
            "total": grounding_total,
            "accuracy": (grounding_matched / grounding_total) if grounding_total else 0.0,
        },
        "abstention_overall": {
            "matched": abstention_matched,
            "total": abstention_total,
            "accuracy": (abstention_matched / abstention_total) if abstention_total else 0.0,
        },
        "forbidden_memory_overall": {
            "clean": forbidden_clean,
            "total": forbidden_total,
            "accuracy": (forbidden_clean / forbidden_total) if forbidden_total else 0.0,
        },
        "live_by_category": [
            {
                "category": category,
                "matched": category_matched[category],
                "total": category_total[category],
                "accuracy": (category_matched[category] / category_total[category]) if category_total[category] else 0.0,
            }
            for category in sorted(category_total)
        ],
        "scorecard_overall": dict(scorecard.get("overall") or {}),
        "scorecard_substantive_overall": {
            "correct": substantive_scorecard_correct,
            "total": substantive_scorecard_total,
            "accuracy": (substantive_scorecard_correct / substantive_scorecard_total)
            if substantive_scorecard_total
            else 0.0,
        },
        "scorecard_alignment": dict(
            ((scorecard.get("product_memory_summary") or {}).get("measured_metrics") or {}).get(
                "primary_answer_candidate_source_alignment", {}
            )
        ),
        "predictions": rendered_predictions,
    }


def _iso_timestamp(index: int) -> str:
    return (_BASE_TIMESTAMP + timedelta(minutes=index)).isoformat()


def _required_live_match_fragments(*, category: str, expected_fragments: Sequence[str]) -> list[str]:
    if category != "explanation":
        return list(expected_fragments)
    return [
        fragment
        for fragment in expected_fragments
        if fragment.strip().lower() not in _EXPLANATION_STYLE_ONLY_FRAGMENTS
    ]


def _is_explanation_like_prediction(*, category: str, question_text: str) -> bool:
    question_lower = str(question_text or "").strip().lower()
    if category == "explanation":
        return True
    return question_lower.startswith("how do you know")


def _is_truthful_abstention_answer(predicted_answer: str) -> bool:
    lowered = str(predicted_answer or "").strip().lower()
    return lowered in _ABSTENTION_EQUIVALENT_ANSWERS
