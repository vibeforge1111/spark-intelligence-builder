from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.feedback import build_memory_feedback_benchmark_payload
from spark_intelligence.memory.regression import _allocate_regression_identity, _prepare_regression_identity
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class MemoryFeedbackBenchmarkRunResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark memory feedback benchmark run"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- status: {summary.get('status') or 'unknown'}")
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- executed: {summary.get('executed_case_count', 0)}")
            lines.append(f"- automated_pass: {summary.get('automated_pass_count', 0)}")
            lines.append(f"- automated_fail: {summary.get('automated_fail_count', 0)}")
            lines.append(f"- needs_operator_judgment: {summary.get('needs_operator_judgment_count', 0)}")
            lines.append(f"- automated_correction_score: {_format_score(summary.get('automated_correction_score'))}")
        failures = self.payload.get("automated_failures") if isinstance(self.payload, dict) else None
        if isinstance(failures, list) and failures:
            lines.append("- failure_cases: " + ", ".join(str(item.get("case_id") or "unknown") for item in failures[:8]))
        return "\n".join(lines)


def run_memory_feedback_benchmark_regression(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str | None = None,
    agent_id: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    limit: int = 20,
    output_dir: str | Path | None = None,
    write_path: str | Path | None = None,
) -> MemoryFeedbackBenchmarkRunResult:
    from spark_intelligence.gateway.runtime import gateway_ask_telegram

    def _emit_progress(stage: str) -> None:
        print(f"[memory-feedback-benchmark] {stage}", file=sys.stderr, flush=True)

    normalized_limit = max(1, min(int(limit or 20), 100))
    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "memory-feedback-benchmarks"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "memory-feedback-benchmark-run.json"
    run_markdown_path = resolved_output_dir / "memory-feedback-benchmark-run.md"

    benchmark_pack = build_memory_feedback_benchmark_payload(
        state_db=state_db,
        human_id=human_id,
        agent_id=agent_id,
        limit=normalized_limit,
    )
    selected_user_id = str(user_id or "").strip() or _telegram_user_id_from_human_id(human_id)
    selected_chat_id = str(chat_id or "").strip() or selected_user_id or None
    synthetic_identity = False
    if not selected_user_id:
        selected_user_id, selected_chat_id = _allocate_regression_identity(chat_id=selected_chat_id)
        _prepare_regression_identity(
            state_db=state_db,
            external_user_id=selected_user_id,
            username=username or "memory-feedback-benchmark",
        )
        synthetic_identity = True

    case_results: list[dict[str, Any]] = []
    cases = list(benchmark_pack.get("cases") if isinstance(benchmark_pack.get("cases"), list) else [])
    _emit_progress(f"running {len(cases)} feedback benchmark cases")
    for case in cases:
        case_results.append(
            _run_feedback_case(
                case=case,
                gateway_ask_telegram=gateway_ask_telegram,
                config_manager=config_manager,
                state_db=state_db,
                user_id=selected_user_id,
                username=username,
                chat_id=selected_chat_id,
            )
        )

    automated_failures = [case for case in case_results if case.get("automated_status") == "fail"]
    needs_operator_judgment = [case for case in case_results if case.get("judgment_status") == "operator_review_required"]
    skipped = [case for case in case_results if case.get("execution_status") == "skipped"]
    executed_count = sum(1 for case in case_results if case.get("execution_status") == "executed")
    automated_pass_count = sum(1 for case in case_results if case.get("automated_status") == "pass")
    automated_fail_count = len(automated_failures)
    needs_operator_judgment_count = len(needs_operator_judgment)
    automated_correction_score = _ratio(automated_pass_count, executed_count)
    automated_failure_rate = _ratio(automated_fail_count, executed_count)
    operator_judgment_rate = _ratio(needs_operator_judgment_count, executed_count)
    summary = {
        "status": "ok" if not automated_failures else "review_required",
        "case_count": len(case_results),
        "executed_case_count": executed_count,
        "skipped_case_count": len(skipped),
        "automated_pass_count": automated_pass_count,
        "automated_fail_count": automated_fail_count,
        "needs_operator_judgment_count": needs_operator_judgment_count,
        "automated_correction_score": automated_correction_score,
        "automated_failure_rate": automated_failure_rate,
        "operator_judgment_rate": operator_judgment_rate,
        "selected_user_id": selected_user_id,
        "selected_chat_id": selected_chat_id,
        "human_id": human_id or (f"human:telegram:{selected_user_id}" if selected_user_id else None),
        "agent_id": agent_id,
        "synthetic_identity": synthetic_identity,
        "benchmark_rule": benchmark_pack.get("benchmark_rule"),
    }
    payload = {
        "view": "memory_feedback_benchmark_run",
        "summary": summary,
        "benchmark_pack": benchmark_pack,
        "cases": case_results,
        "automated_failures": automated_failures,
        "operator_judgment_queue": needs_operator_judgment,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
            "summary_markdown": str(run_markdown_path),
        },
    }
    run_markdown_path.write_text(_build_feedback_run_markdown(payload), encoding="utf-8")
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    record_event(
        state_db,
        event_type="memory_feedback_benchmark_run",
        component="memory_feedback",
        summary=(
            "Memory feedback benchmark run completed "
            f"with {summary['automated_fail_count']} automated failure(s)."
        ),
        human_id=summary.get("human_id"),
        agent_id=agent_id,
        actor_id="memory_feedback_benchmark",
        status=summary["status"],
        reason_code="feedback_benchmark_run",
        evidence_lane="operator_feedback_eval",
        facts={
            "case_count": summary["case_count"],
            "executed_case_count": summary["executed_case_count"],
            "automated_pass_count": summary["automated_pass_count"],
            "automated_fail_count": summary["automated_fail_count"],
            "needs_operator_judgment_count": summary["needs_operator_judgment_count"],
            "automated_correction_score": automated_correction_score,
            "automated_failure_rate": automated_failure_rate,
            "operator_judgment_rate": operator_judgment_rate,
            "artifact_paths": payload["artifact_paths"],
            "feedback_case_ids": [case.get("case_id") for case in case_results],
        },
        provenance={
            "source_kind": "operator_feedback_eval",
            "source_ref": str(resolved_write_path),
        },
    )
    return MemoryFeedbackBenchmarkRunResult(output_dir=resolved_output_dir, payload=payload)


def _run_feedback_case(
    *,
    case: dict[str, Any],
    gateway_ask_telegram: Any,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_id: str,
    username: str | None,
    chat_id: str | None,
) -> dict[str, Any]:
    prompt = str(case.get("evaluation_prompt") or "").strip()
    base = {
        "case_id": case.get("case_id"),
        "feedback_event_id": case.get("feedback_event_id"),
        "target_event_id": case.get("target_event_id"),
        "benchmark_kind": case.get("benchmark_kind"),
        "benchmark_tags": case.get("benchmark_tags") if isinstance(case.get("benchmark_tags"), list) else [],
        "verdict": case.get("verdict"),
        "note": case.get("note"),
        "expected_outcome": case.get("expected_outcome"),
        "required_judgment": case.get("required_judgment"),
        "authority_boundary": case.get("authority_boundary"),
        "evaluation_prompt": prompt,
        "original_source_packet": case.get("source_packet") if isinstance(case.get("source_packet"), dict) else {},
    }
    if not prompt:
        return {
            **base,
            "execution_status": "skipped",
            "automated_status": "skip",
            "judgment_status": "missing_evaluation_prompt",
            "mismatches": ["missing_evaluation_prompt"],
        }
    raw = gateway_ask_telegram(
        config_manager=config_manager,
        state_db=state_db,
        message=prompt,
        user_id=user_id,
        username=username,
        chat_id=chat_id,
        as_json=True,
    )
    gateway_payload = _parse_json_object(raw)
    result = gateway_payload.get("result") if isinstance(gateway_payload.get("result"), dict) else {}
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    response_text = str(detail.get("response_text") or gateway_payload.get("response_text") or "").strip()
    trace_ref = str(detail.get("trace_ref") or gateway_payload.get("trace_ref") or "").strip()
    replay_read_event = _load_replay_read_event(
        state_db=state_db,
        human_id=f"human:telegram:{user_id}",
        trace_ref=trace_ref,
    )
    replay_source_packet = _source_packet_from_event(replay_read_event)
    mismatches = _automated_mismatches(
        case=case,
        decision=str(result.get("decision") or gateway_payload.get("decision") or "").strip(),
        response_text=response_text,
        replay_source_packet=replay_source_packet,
    )
    return {
        **base,
        "execution_status": "executed",
        "automated_status": "fail" if mismatches else "pass",
        "judgment_status": _judgment_status(case, mismatches),
        "mismatches": mismatches,
        "response_text": response_text,
        "decision": str(result.get("decision") or gateway_payload.get("decision") or "").strip(),
        "bridge_mode": str(detail.get("bridge_mode") or gateway_payload.get("bridge_mode") or "").strip(),
        "routing_decision": str(detail.get("routing_decision") or gateway_payload.get("routing_decision") or "").strip(),
        "trace_ref": trace_ref,
        "replay_source_packet": replay_source_packet,
        "replay_read_event_id": replay_read_event.get("event_id") if replay_read_event else None,
        "gateway_payload": gateway_payload,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _automated_mismatches(
    *,
    case: dict[str, Any],
    decision: str,
    response_text: str,
    replay_source_packet: dict[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    if decision != "allowed":
        mismatches.append(f"decision:{decision or 'missing'}")
    if not response_text:
        mismatches.append("response_missing")
    if _contains_feedback_note(response_text, str(case.get("note") or "")):
        mismatches.append("feedback_note_echoed_as_answer")
    kind = str(case.get("benchmark_kind") or "")
    if kind == "coverage_gap" and _looks_like_generic_abstention(response_text):
        mismatches.append("coverage_gap_still_generic_abstention")
    if kind == "positive_control" and _looks_like_generic_abstention(response_text):
        mismatches.append("positive_control_abstained")
    if kind == "source_quality_regression":
        source_class = str(replay_source_packet.get("source_class") or "none")
        if source_class == "none":
            mismatches.append("source_packet_missing")
        if str(replay_source_packet.get("stale_current_status") or "").lower() == "fail":
            mismatches.append("stale_current_conflict_still_failing")
    return mismatches


def _judgment_status(case: dict[str, Any], mismatches: list[str]) -> str:
    if mismatches:
        return "automated_failure"
    if str(case.get("benchmark_kind") or "") == "positive_control":
        return "automated_preservation_check_passed"
    return "operator_review_required"


def _load_replay_read_event(
    *,
    state_db: StateDB,
    human_id: str,
    trace_ref: str,
) -> dict[str, Any] | None:
    conditions = ["event_type IN ('memory_read_succeeded', 'memory_read_abstained')", "human_id = ?"]
    params: list[Any] = [human_id]
    if trace_ref:
        conditions.append("(request_id = ? OR trace_ref = ?)")
        params.extend([trace_ref, trace_ref])
    with state_db.connect() as conn:
        row = conn.execute(
            f"""
            SELECT event_id, event_type, component, request_id, trace_ref, session_id, human_id, agent_id,
                   actor_id, status, summary, reason_code, facts_json, provenance_json, created_at
            FROM builder_events
            WHERE {" AND ".join(f"({condition})" for condition in conditions)}
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    return _event_row(row) if row is not None else None


def _source_packet_from_event(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {"source_class": "none", "source_mix": {}, "selected_count": 0, "candidate_count": 0}
    facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
    answer = facts.get("answer_explanation") if isinstance(facts.get("answer_explanation"), dict) else {}
    retrieval_trace = facts.get("retrieval_trace") if isinstance(facts.get("retrieval_trace"), dict) else {}
    hybrid = retrieval_trace.get("hybrid_memory_retrieve") if isinstance(retrieval_trace.get("hybrid_memory_retrieve"), dict) else {}
    source_mix = answer.get("context_packet_source_mix") if isinstance(answer.get("context_packet_source_mix"), dict) else {}
    promotion_gates = (
        answer.get("context_packet_promotion_gates")
        if isinstance(answer.get("context_packet_promotion_gates"), dict)
        else {}
    )
    return {
        "source_class": _dominant_source(source_mix) or "none",
        "source_mix": source_mix,
        "selected_count": answer.get("selected_count") or hybrid.get("selected_count"),
        "candidate_count": hybrid.get("candidate_count"),
        "stale_current_status": _gate_status(promotion_gates, "stale_current_conflict"),
        "source_mix_status": _gate_status(promotion_gates, "source_mix_stability"),
        "context_sections": answer.get("context_packet_sections") if isinstance(answer.get("context_packet_sections"), list) else [],
    }


def _event_row(row: Any) -> dict[str, Any]:
    event = {key: row[key] for key in row.keys()}
    event["facts"] = _json_object(event.pop("facts_json", None))
    event["provenance"] = _json_object(event.pop("provenance_json", None))
    return event


def _telegram_user_id_from_human_id(human_id: str | None) -> str | None:
    prefix = "human:telegram:"
    value = str(human_id or "").strip()
    if value.startswith(prefix):
        user_id = value.removeprefix(prefix).strip()
        return user_id or None
    return None


def _contains_feedback_note(response_text: str, note: str) -> bool:
    clean_note = " ".join(str(note or "").lower().split())
    clean_response = " ".join(str(response_text or "").lower().split())
    if len(clean_note) < 24:
        return False
    return clean_note[:160] in clean_response


def _looks_like_generic_abstention(response_text: str) -> bool:
    lowered = response_text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "don't currently have that saved",
            "do not currently have that saved",
            "i don't have that saved",
            "i do not have that saved",
        )
    )


def _dominant_source(source_mix: dict[str, Any]) -> str | None:
    if not source_mix:
        return None
    return max(source_mix, key=lambda key: int(source_mix.get(key) or 0))


def _gate_status(promotion_gates: dict[str, Any], gate_name: str) -> str:
    gates = promotion_gates.get("gates") if isinstance(promotion_gates.get("gates"), dict) else {}
    gate = gates.get(gate_name) if isinstance(gates.get(gate_name), dict) else {}
    return str(gate.get("status") or promotion_gates.get("status") or "unknown")


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return decoded if isinstance(decoded, dict) else {"raw": raw}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _build_feedback_run_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Memory Feedback Benchmark Run",
        "",
        f"- Status: `{summary.get('status') or 'unknown'}`",
        f"- Cases: `{summary.get('case_count', 0)}`",
        f"- Executed: `{summary.get('executed_case_count', 0)}`",
        f"- Automated pass: `{summary.get('automated_pass_count', 0)}`",
        f"- Automated fail: `{summary.get('automated_fail_count', 0)}`",
        f"- Needs operator judgment: `{summary.get('needs_operator_judgment_count', 0)}`",
        f"- Automated correction score: `{_format_score(summary.get('automated_correction_score'))}`",
        "",
        "Feedback remains eval evidence only. Passing this run does not promote feedback notes into durable memory truth.",
        "",
        "## Cases",
    ]
    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    for case in cases:
        lines.extend(
            [
                "",
                f"### `{case.get('case_id') or 'unknown'}`",
                "",
                f"- Kind: `{case.get('benchmark_kind') or 'unknown'}`",
                f"- Automated status: `{case.get('automated_status') or 'unknown'}`",
                f"- Judgment status: `{case.get('judgment_status') or 'unknown'}`",
                f"- Prompt: {case.get('evaluation_prompt') or 'missing'}",
            ]
        )
        mismatches = case.get("mismatches")
        if isinstance(mismatches, list) and mismatches:
            lines.append("- Mismatches: " + ", ".join(str(item) for item in mismatches))
    return "\n".join(lines) + "\n"


def _format_score(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return "n/a"
