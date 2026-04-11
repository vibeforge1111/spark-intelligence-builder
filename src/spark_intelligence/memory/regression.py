from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import approve_pairing, consume_pairing_welcome
from spark_intelligence.memory.architecture_benchmark import benchmark_memory_architectures
from spark_intelligence.memory.architecture_live_comparison import compare_telegram_memory_architectures
from spark_intelligence.memory.knowledge_base import build_telegram_state_knowledge_base
from spark_intelligence.memory.orchestrator import inspect_human_memory_in_memory
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class TelegramMemoryRegressionCase:
    case_id: str
    category: str
    message: str
    expected_bridge_mode: str | None = None
    expected_routing_decision: str | None = None
    expected_response_contains: tuple[str, ...] = ()
    expected_response_excludes: tuple[str, ...] = ()
    benchmark_tags: tuple[str, ...] = ()
    isolate_memory: bool = False


QUALITY_LANE_KEYS: tuple[str, ...] = ("staleness", "overwrite", "abstention")


DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES: tuple[TelegramMemoryRegressionCase, ...] = (
    TelegramMemoryRegressionCase(
        case_id="name_write",
        category="profile_write",
        message="My name is Sarah.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Sarah",),
    ),
    TelegramMemoryRegressionCase(
        case_id="name_query",
        category="profile_query",
        message="What is my name?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Sarah",),
    ),
    TelegramMemoryRegressionCase(
        case_id="occupation_write",
        category="profile_write",
        message="I am an entrepreneur.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("entrepreneur",),
    ),
    TelegramMemoryRegressionCase(
        case_id="occupation_query",
        category="profile_query",
        message="What do I do?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("entrepreneur",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_write",
        category="profile_write",
        message="I live in Dubai.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Dubai",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_explanation",
        category="explanation",
        message="How do you know where I live?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "Dubai"),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_write",
        category="profile_write",
        message="My startup is Seedify.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Seedify",),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_explanation",
        category="explanation",
        message="How do you know my startup?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "Seedify"),
    ),
    TelegramMemoryRegressionCase(
        case_id="founder_write",
        category="profile_write",
        message="I am the founder of Spark Swarm.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Spark Swarm",),
    ),
    TelegramMemoryRegressionCase(
        case_id="founder_query",
        category="profile_query",
        message="What company did I found?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Spark Swarm",),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_query_after_founder",
        category="staleness",
        message="What startup did I create?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Spark Swarm",),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_explanation_after_founder",
        category="staleness",
        message="How do you know my startup?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "Seedify"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mission_write",
        category="profile_write",
        message="I am trying to survive the hack and revive the companies.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("revive the companies",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mission_query",
        category="profile_query",
        message="What am I trying to do now?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("revive the companies",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mission_explanation",
        category="explanation",
        message="How do you know what I'm trying to do now?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "revive the companies"),
    ),
    TelegramMemoryRegressionCase(
        case_id="hack_actor_write",
        category="profile_write",
        message="We were hacked by North Korea.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("North Korea",),
    ),
    TelegramMemoryRegressionCase(
        case_id="hack_actor_query",
        category="profile_query",
        message="Who hacked us?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("North Korea",),
    ),
    TelegramMemoryRegressionCase(
        case_id="spark_role_write",
        category="profile_write",
        message="Spark will be an important part of the rebuild.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("important part of the rebuild",),
    ),
    TelegramMemoryRegressionCase(
        case_id="spark_role_query",
        category="profile_query",
        message="What role will Spark play in this?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("important part of the rebuild",),
    ),
    TelegramMemoryRegressionCase(
        case_id="spark_role_abstention",
        category="abstention",
        message="What role will Spark play in this?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have that saved",),
        isolate_memory=True,
    ),
    TelegramMemoryRegressionCase(
        case_id="hack_actor_query_missing",
        category="abstention",
        message="Who hacked us?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have that saved",),
        isolate_memory=True,
    ),
    TelegramMemoryRegressionCase(
        case_id="timezone_write",
        category="profile_write",
        message="My timezone is Asia/Dubai.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Asia/Dubai",),
    ),
    TelegramMemoryRegressionCase(
        case_id="timezone_query",
        category="profile_query",
        message="What timezone do you have for me?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Asia/Dubai",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_write",
        category="profile_write",
        message="My country is UAE.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("UAE",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_query",
        category="profile_query",
        message="What country do you have for me?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("UAE",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_explanation",
        category="explanation",
        message="How do you know my country?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "UAE"),
    ),
    TelegramMemoryRegressionCase(
        case_id="identity_summary",
        category="identity",
        message="What do you know about me?",
        expected_bridge_mode="memory_profile_identity",
        expected_routing_decision="memory_profile_identity_summary",
        expected_response_contains=("entrepreneur", "Seedify"),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_overwrite",
        category="overwrite",
        message="I live in Abu Dhabi now.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Abu Dhabi",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_overwrite",
        category="overwrite",
        message="I moved to Canada.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Canada",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_query_after_overwrite",
        category="overwrite",
        message="What country do you have for me?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Canada",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_query_after_overwrite",
        category="overwrite",
        message="Where do I live now?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Abu Dhabi",),
    ),
)


@dataclass(frozen=True)
class TelegramMemoryRegressionResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark memory Telegram regression"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- status: {summary.get('status') or 'unknown'}")
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- matched: {summary.get('matched_case_count', 0)}")
            lines.append(f"- mismatched: {summary.get('mismatched_case_count', 0)}")
            lines.append(f"- selected_user_id: {summary.get('selected_user_id') or 'unknown'}")
            lines.append(f"- selected_chat_id: {summary.get('selected_chat_id') or 'unknown'}")
            lines.append(f"- kb_has_probe_coverage: {'yes' if summary.get('kb_has_probe_coverage') else 'no'}")
            lines.append(
                f"- kb_current_state_hits: "
                f"{summary.get('kb_current_state_hits', 0)}/{summary.get('kb_current_state_total', 0)}"
            )
            lines.append(
                f"- kb_evidence_hits: "
                f"{summary.get('kb_evidence_hits', 0)}/{summary.get('kb_evidence_total', 0)}"
            )
            blocked_reason = str(summary.get("blocked_reason") or "").strip()
            if blocked_reason:
                lines.append(f"- blocked_reason: {blocked_reason}")
        mismatches = self.payload.get("mismatches") if isinstance(self.payload, dict) else None
        if isinstance(mismatches, list) and mismatches:
            lines.append("- mismatch_cases: " + ", ".join(str(item.get("case_id") or "unknown") for item in mismatches[:8]))
        return "\n".join(lines)


def run_telegram_memory_regression(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    kb_limit: int = 25,
    validator_root: str | Path | None = None,
    write_path: str | Path | None = None,
    case_ids: list[str] | None = None,
    categories: list[str] | None = None,
    cases: list[TelegramMemoryRegressionCase] | tuple[TelegramMemoryRegressionCase, ...] | None = None,
    baseline_names: list[str] | tuple[str, ...] | None = None,
) -> TelegramMemoryRegressionResult:
    from spark_intelligence.gateway.runtime import gateway_ask_telegram

    resolved_output_dir = Path(output_dir) if output_dir else _default_output_dir(config_manager)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_kb_output_dir = resolved_output_dir / "kb"
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-regression.json"
    kb_write_path = resolved_output_dir / "telegram-memory-kb.json"
    regression_summary_markdown_path = resolved_output_dir / "regression-summary.md"
    regression_cases_json_path = resolved_output_dir / "regression-cases.json"
    architecture_benchmark_output_dir = resolved_output_dir / "architecture-benchmark"
    architecture_live_comparison_output_dir = resolved_output_dir / "architecture-live-comparison"

    case_payloads: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    selected_user_id = str(user_id or "").strip() or None
    selected_chat_id = str(chat_id or "").strip() or None
    requested_case_ids = [str(item).strip() for item in (case_ids or []) if str(item).strip()]
    requested_categories = [str(item).strip() for item in (categories or []) if str(item).strip()]
    requested_baseline_names = [str(item).strip() for item in (baseline_names or []) if str(item).strip()]
    selected_cases = _select_regression_cases(
        case_ids=requested_case_ids,
        categories=requested_categories,
        cases_source=cases,
    )
    selection_summary = _selection_summary(selected_cases)
    filter_summary = {
        "requested_case_ids": requested_case_ids,
        "requested_categories": requested_categories,
        "requested_baseline_names": requested_baseline_names,
    }
    if selected_user_id is None:
        selected_user_id, selected_chat_id = _allocate_regression_identity(chat_id=selected_chat_id)
        _prepare_regression_identity(
            state_db=state_db,
            external_user_id=selected_user_id,
            username=username or "memory-regression",
        )
    elif selected_chat_id is None:
        selected_chat_id = selected_user_id

    if not selected_cases:
        payload = {
            "summary": {
                "status": "invalid_request",
                "case_count": 0,
                "matched_case_count": 0,
                "mismatched_case_count": 0,
                "selected_user_id": selected_user_id,
                "selected_chat_id": selected_chat_id,
                "human_id": f"human:telegram:{selected_user_id}" if selected_user_id else None,
                "kb_has_probe_coverage": False,
                "kb_issue_labels": [],
                "kb_current_state_hits": 0,
                "kb_current_state_total": 0,
                "kb_evidence_hits": 0,
                "kb_evidence_total": 0,
                **filter_summary,
                **selection_summary,
            },
            "errors": ["no_regression_cases_selected"],
            "cases": [],
            "mismatches": [],
            "inspection": None,
            "kb_compile": None,
            "artifact_paths": {
                "summary_json": str(resolved_write_path),
                "kb_output_dir": str(resolved_kb_output_dir),
                "kb_json": str(kb_write_path),
                "regression_report_markdown": str(regression_summary_markdown_path),
                "regression_cases_json": str(regression_cases_json_path),
            },
        }
        resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)

    for case in selected_cases:
        case_user_id = selected_user_id
        case_chat_id = selected_chat_id
        if case.isolate_memory and selected_user_id:
            case_user_id = f"{selected_user_id}-{case.case_id}"
            case_chat_id = f"{(selected_chat_id or selected_user_id)}-{case.case_id}"
            _prepare_regression_identity(
                state_db=state_db,
                external_user_id=case_user_id,
                username=username or f"Regression {case.case_id}",
            )
        raw = gateway_ask_telegram(
            config_manager=config_manager,
            state_db=state_db,
            message=case.message,
            user_id=case_user_id,
            username=username,
            chat_id=case_chat_id,
            as_json=True,
        )
        gateway_payload = _parse_json_object(raw)
        if selected_user_id is None:
            candidate_user_id = str(gateway_payload.get("user_id") or "").strip()
            if candidate_user_id:
                selected_user_id = candidate_user_id
        if selected_chat_id is None:
            candidate_chat_id = str(gateway_payload.get("chat_id") or "").strip()
            if candidate_chat_id:
                selected_chat_id = candidate_chat_id
            elif selected_user_id:
                selected_chat_id = selected_user_id
        case_result = _build_case_result(case=case, payload=gateway_payload)
        if case_result.get("decision") != "allowed":
            blocked_reason = _blocked_reason_for_case(case_result)
            payload = {
                "summary": {
                    "status": "blocked_precondition",
                    "case_count": len(selected_cases),
                    "matched_case_count": 0,
                    "mismatched_case_count": 0,
                    "selected_user_id": selected_user_id,
                    "selected_chat_id": selected_chat_id,
                    "human_id": f"human:telegram:{selected_user_id}" if selected_user_id else None,
                    "kb_has_probe_coverage": False,
                    "kb_issue_labels": [],
                    "kb_current_state_hits": 0,
                    "kb_current_state_total": 0,
                    "kb_evidence_hits": 0,
                    "kb_evidence_total": 0,
                    "blocked_reason": blocked_reason,
                    **filter_summary,
                    **selection_summary,
                },
                "cases": [case_result],
                "mismatches": [],
                "inspection": None,
                "kb_compile": None,
                "artifact_paths": {
                    "summary_json": str(resolved_write_path),
                    "kb_output_dir": str(resolved_kb_output_dir),
                    "kb_json": str(kb_write_path),
                    "regression_report_markdown": str(regression_summary_markdown_path),
                    "regression_cases_json": str(regression_cases_json_path),
                },
            }
            resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)
        case_payloads.append(case_result)
        if not case_result.get("matched_expectations", True):
            mismatches.append(case_result)

    resolved_human_id = f"human:telegram:{selected_user_id}" if selected_user_id else None
    inspection_payload: dict[str, Any] | None = None
    if resolved_human_id:
        inspection_result = inspect_human_memory_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            human_id=resolved_human_id,
            actor_id="memory_regression",
        )
        inspection_payload = _parse_json_object(inspection_result.to_json())

    architecture_benchmark_result = benchmark_memory_architectures(
        config_manager=config_manager,
        output_dir=architecture_benchmark_output_dir,
        validator_root=validator_root,
        baseline_names=requested_baseline_names or None,
    )
    architecture_benchmark_payload = architecture_benchmark_result.payload
    architecture_summary_path = (
        (architecture_benchmark_payload.get("artifact_paths") or {}).get("summary_markdown")
        if isinstance(architecture_benchmark_payload, dict)
        else None
    )
    architecture_live_comparison_result = compare_telegram_memory_architectures(
        config_manager=config_manager,
        case_payloads=case_payloads,
        selected_cases=selected_cases,
        output_dir=architecture_live_comparison_output_dir,
        validator_root=validator_root,
        baseline_names=requested_baseline_names or None,
    )
    architecture_live_comparison_payload = architecture_live_comparison_result.payload
    architecture_live_comparison_summary_path = (
        (architecture_live_comparison_payload.get("artifact_paths") or {}).get("summary_markdown")
        if isinstance(architecture_live_comparison_payload, dict)
        else None
    )
    regression_summary_markdown_path.write_text(
        _build_regression_summary_markdown(
            selected_user_id=selected_user_id,
            selected_chat_id=selected_chat_id,
            case_payloads=case_payloads,
            mismatches=mismatches,
            inspection_payload=inspection_payload,
            architecture_benchmark_payload=architecture_benchmark_payload,
            architecture_live_comparison_payload=architecture_live_comparison_payload,
        ),
        encoding="utf-8",
    )
    regression_cases_json_path.write_text(
        json.dumps(
            {
                "selected_user_id": selected_user_id,
                "selected_chat_id": selected_chat_id,
                "human_id": resolved_human_id,
                "cases": case_payloads,
                "mismatches": mismatches,
                "inspection": inspection_payload,
                "architecture_live_comparison": architecture_live_comparison_payload,
                **filter_summary,
                **selection_summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    repo_sources = [str(regression_summary_markdown_path), str(regression_cases_json_path)]
    if architecture_summary_path:
        repo_sources.append(str(architecture_summary_path))
    if architecture_live_comparison_summary_path:
        repo_sources.append(str(architecture_live_comparison_summary_path))
    kb_result = build_telegram_state_knowledge_base(
        config_manager=config_manager,
        output_dir=resolved_kb_output_dir,
        limit=max(int(kb_limit), 1),
        chat_id=selected_chat_id,
        repo_sources=repo_sources,
        write_path=kb_write_path,
        validator_root=validator_root,
    )
    kb_payload = kb_result.payload
    current_probe = _probe_row(kb_payload, "current_state")
    evidence_probe = _probe_row(kb_payload, "evidence")
    summary = {
        "status": "ok",
        "case_count": len(case_payloads),
        "matched_case_count": len(case_payloads) - len(mismatches),
        "mismatched_case_count": len(mismatches),
        "selected_user_id": selected_user_id,
        "selected_chat_id": selected_chat_id,
        "human_id": resolved_human_id,
        "architecture_runtime_sdk_class": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "runtime_sdk_class",
            default=None,
        ),
        "architecture_runtime_memory_architecture": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "runtime_memory_architecture",
            default=None,
        ),
        "architecture_compared_baselines": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "baseline_names",
            default=[],
        ),
        "architecture_documented_frontier": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "documented_frontier_architecture",
            default=None,
        ),
        "architecture_runtime_matches_documented_frontier": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "runtime_matches_documented_frontier",
            default=False,
        ),
        "architecture_product_memory_leaders": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "product_memory_leader_names",
            default=[],
        ),
        "live_architecture_case_count": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "case_count",
            default=0,
        ),
        "live_architecture_compared_baselines": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "baseline_names",
            default=[],
        ),
        "live_architecture_leaders": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "leader_names",
            default=[],
        ),
        "live_architecture_recommended_runtime": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "recommended_runtime_architecture",
            default=None,
        ),
        "live_architecture_runtime_matches_leader": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "runtime_matches_live_leader",
            default=False,
        ),
        "kb_has_probe_coverage": _nested_get(kb_payload, "failure_taxonomy", "summary", "has_probe_coverage", default=False),
        "kb_issue_labels": _nested_get(kb_payload, "failure_taxonomy", "summary", "issue_labels", default=[]),
        "kb_current_state_hits": current_probe.get("hits", 0),
        "kb_current_state_total": current_probe.get("total", 0),
        "kb_evidence_hits": evidence_probe.get("hits", 0),
        "kb_evidence_total": evidence_probe.get("total", 0),
        **filter_summary,
        **selection_summary,
    }
    payload = {
        "summary": summary,
        "cases": case_payloads,
        "mismatches": mismatches,
        "inspection": inspection_payload,
        "architecture_benchmark": architecture_benchmark_payload,
        "architecture_live_comparison": architecture_live_comparison_payload,
        "kb_compile": kb_payload,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
            "kb_output_dir": str(resolved_kb_output_dir),
            "kb_json": str(kb_write_path),
            "regression_report_markdown": str(regression_summary_markdown_path),
            "regression_cases_json": str(regression_cases_json_path),
            "architecture_benchmark_dir": str(architecture_benchmark_output_dir),
            "architecture_benchmark_markdown": str(architecture_summary_path) if architecture_summary_path else None,
            "architecture_live_comparison_dir": str(architecture_live_comparison_output_dir),
            "architecture_live_comparison_markdown": (
                str(architecture_live_comparison_summary_path) if architecture_live_comparison_summary_path else None
            ),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)


def _allocate_regression_identity(*, chat_id: str | None) -> tuple[str, str]:
    run_suffix = uuid4().hex[:8]
    user_id = f"spark-memory-regression-user-{run_suffix}"
    resolved_chat_id = str(chat_id or "").strip() or user_id
    return user_id, resolved_chat_id


def _prepare_regression_identity(
    *,
    state_db: StateDB,
    external_user_id: str,
    username: str,
) -> None:
    approve_pairing(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
        display_name=username,
    )
    consume_pairing_welcome(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
    )


def _build_case_result(*, case: TelegramMemoryRegressionCase, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    bridge_mode = str(detail.get("bridge_mode") or payload.get("bridge_mode") or "").strip()
    routing_decision = str(detail.get("routing_decision") or payload.get("routing_decision") or "").strip()
    response_text = str(detail.get("response_text") or payload.get("response_text") or "").strip()
    mismatches: list[str] = []
    if case.expected_bridge_mode and bridge_mode != case.expected_bridge_mode:
        mismatches.append(f"bridge_mode:{bridge_mode or 'missing'}")
    if case.expected_routing_decision and routing_decision != case.expected_routing_decision:
        mismatches.append(f"routing_decision:{routing_decision or 'missing'}")
    lowered_response = response_text.lower()
    for expected_fragment in case.expected_response_contains:
        if expected_fragment.lower() not in lowered_response:
            mismatches.append(f"response_missing:{expected_fragment}")
    for forbidden_fragment in case.expected_response_excludes:
        if forbidden_fragment.lower() in lowered_response:
            mismatches.append(f"response_forbidden:{forbidden_fragment}")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "message": case.message,
        "expected_response_contains": list(case.expected_response_contains),
        "expected_response_excludes": list(case.expected_response_excludes),
        "benchmark_tags": list(case.benchmark_tags),
        "decision": str(result.get("decision") or payload.get("decision") or "").strip(),
        "bridge_mode": bridge_mode,
        "routing_decision": routing_decision,
        "response_text": response_text,
        "trace_ref": str(detail.get("trace_ref") or payload.get("trace_ref") or "").strip(),
        "matched_expectations": not mismatches,
        "mismatches": mismatches,
        "gateway_payload": payload,
    }


def _default_output_dir(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "telegram-memory-regression"


def _select_regression_cases(
    *,
    case_ids: list[str] | None,
    categories: list[str] | None,
    cases_source: list[TelegramMemoryRegressionCase] | tuple[TelegramMemoryRegressionCase, ...] | None = None,
) -> tuple[TelegramMemoryRegressionCase, ...]:
    requested_case_ids = {str(item).strip() for item in (case_ids or []) if str(item).strip()}
    requested_categories = {str(item).strip() for item in (categories or []) if str(item).strip()}
    available_cases = tuple(cases_source or DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES)
    if not requested_case_ids and not requested_categories:
        return available_cases
    selected: list[TelegramMemoryRegressionCase] = []
    for case in available_cases:
        if requested_case_ids and case.case_id not in requested_case_ids:
            continue
        if requested_categories and case.category not in requested_categories:
            continue
        selected.append(case)
    return tuple(selected)


def _selection_summary(cases: tuple[TelegramMemoryRegressionCase, ...]) -> dict[str, Any]:
    category_counts = _build_category_counts(case.category for case in cases)
    return {
        "selected_case_ids": [case.case_id for case in cases],
        "selected_categories": sorted(category_counts),
        "category_counts": category_counts,
        "quality_lanes": _build_quality_lanes(category_counts),
    }


def _nested_get(payload: dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_output": raw}
    return parsed if isinstance(parsed, dict) else {"raw_output": raw}


def _probe_row(payload: dict[str, Any], probe_type: str) -> dict[str, Any]:
    rows = _nested_get(payload, "failure_taxonomy", "probe_rows", default=[])
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("probe_type") or "").strip() == probe_type:
            return row
    return {}


def _blocked_reason_for_case(case_result: dict[str, Any]) -> str:
    decision = str(case_result.get("decision") or "").strip()
    response_text = str(case_result.get("response_text") or "").strip()
    case_id = str(case_result.get("case_id") or "").strip() or "unknown_case"
    if response_text:
        return f"{case_id}:{decision}:{response_text}"
    return f"{case_id}:{decision}"


def _build_regression_summary_markdown(
    *,
    selected_user_id: str | None,
    selected_chat_id: str | None,
    case_payloads: list[dict[str, Any]],
    mismatches: list[dict[str, Any]],
    inspection_payload: dict[str, Any] | None,
    architecture_benchmark_payload: dict[str, Any] | None,
    architecture_live_comparison_payload: dict[str, Any] | None,
) -> str:
    category_counts = _build_category_counts(str(case.get("category") or "unknown") for case in case_payloads)
    bridge_mode_counts = _build_category_counts(str(case.get("bridge_mode") or "missing") for case in case_payloads)
    routing_decision_counts = _build_category_counts(
        str(case.get("routing_decision") or "missing") for case in case_payloads
    )
    quality_lanes = _build_quality_lanes(category_counts)
    inspection_records = _inspection_records(inspection_payload)
    live_architecture_summary = (
        architecture_live_comparison_payload.get("summary")
        if isinstance(architecture_live_comparison_payload, dict)
        and isinstance(architecture_live_comparison_payload.get("summary"), dict)
        else {}
    )
    benchmark_summary = (
        architecture_benchmark_payload.get("summary")
        if isinstance(architecture_benchmark_payload, dict)
        and isinstance(architecture_benchmark_payload.get("summary"), dict)
        else {}
    )

    lines = [
        "# Telegram Memory Regression Summary",
        "",
        f"- Selected user id: `{selected_user_id or 'unknown'}`",
        f"- Selected chat id: `{selected_chat_id or 'unknown'}`",
        f"- Total cases: `{len(case_payloads)}`",
        f"- Matched cases: `{len(case_payloads) - len(mismatches)}`",
        f"- Mismatched cases: `{len(mismatches)}`",
        "",
        "## Live Architecture Comparison",
        "",
        f"- ProductMemory contenders: `{', '.join(benchmark_summary.get('baseline_names') or []) or 'none'}`",
        f"- Live Telegram contenders: `{', '.join(live_architecture_summary.get('baseline_names') or []) or 'none'}`",
        f"- Compared cases: `{live_architecture_summary.get('case_count', 0)}`",
        f"- Leaders: `{', '.join(live_architecture_summary.get('leader_names') or []) or 'unknown'}`",
        f"- Recommended runtime architecture: `{live_architecture_summary.get('recommended_runtime_architecture') or 'undecided'}`",
        f"- Current runtime architecture: `{live_architecture_summary.get('current_runtime_memory_architecture') or 'unknown'}`",
        f"- Runtime matches live leader: `{'yes' if live_architecture_summary.get('runtime_matches_live_leader') else 'no'}`",
        "",
        "## Category Coverage",
        "",
    ]
    for category in sorted(category_counts):
        lines.append(f"- `{category}`: `{category_counts[category]}`")
    lines.extend(
        [
            "",
            "## Route Coverage",
            "",
            "### Bridge Modes",
            "",
        ]
    )
    for bridge_mode in sorted(bridge_mode_counts):
        lines.append(f"- `{bridge_mode}`: `{bridge_mode_counts[bridge_mode]}`")
    lines.extend(
        [
            "",
            "### Routing Decisions",
            "",
        ]
    )
    for routing_decision in sorted(routing_decision_counts):
        lines.append(f"- `{routing_decision}`: `{routing_decision_counts[routing_decision]}`")
    lines.extend(
        [
            "",
            "## Quality Lanes",
            "",
            f"- `staleness`: `{'yes' if quality_lanes['staleness'] else 'no'}`",
            f"- `overwrite`: `{'yes' if quality_lanes['overwrite'] else 'no'}`",
            f"- `abstention`: `{'yes' if quality_lanes['abstention'] else 'no'}`",
            "",
            "## Current Memory Snapshot",
            "",
        ]
    )
    if inspection_records:
        for record in inspection_records[:12]:
            predicate = str(record.get("predicate") or "unknown")
            value = str(record.get("normalized_value") or record.get("value") or "").strip() or "missing"
            lines.append(f"- `{predicate}`: `{value}`")
    else:
        lines.append("- No current-state records were available from the inspection step.")
    lines.extend(
        [
            "",
            "## Recommended Next Actions",
            "",
        ]
    )
    if mismatches:
        lines.append("- Fix the mismatched cases before promoting wider runtime memory behavior.")
    else:
        lines.append("- Keep this regression bundle as a green baseline and add the next benchmark-style lane.")
    lines.append("- Only promote a memory change after it stays green on both ProductMemory scorecards and live Telegram regression packs.")
    if live_architecture_summary.get("recommended_runtime_architecture"):
        lines.append(
            f"- Promote `{live_architecture_summary.get('recommended_runtime_architecture')}` into the Builder runtime selector and rerun this bundle."
        )
    elif live_architecture_summary.get("leader_names"):
        lines.append("- Break the live architecture tie with more cases before pinning the Builder runtime selector.")
    if not quality_lanes["abstention"]:
        lines.append("- Add abstention cases before widening memory promotion beyond the current lane.")
    if not quality_lanes["overwrite"]:
        lines.append("- Add overwrite cases so newer facts keep winning without regressions.")
    if not quality_lanes["staleness"]:
        lines.append("- Add staleness cases to confirm explanation and query routing stay stable over time.")
    lines.extend(
        [
            "",
            "## Cases",
            "",
        ]
    )
    for case in case_payloads:
        case_id = str(case.get("case_id") or "unknown")
        category = str(case.get("category") or "unknown")
        decision = str(case.get("decision") or "unknown")
        matched = "yes" if case.get("matched_expectations", False) else "no"
        response_text = " ".join(str(case.get("response_text") or "").split())
        lines.append(f"### {case_id}")
        lines.append(f"- Category: `{category}`")
        lines.append(f"- Decision: `{decision}`")
        lines.append(f"- Matched: `{matched}`")
        if response_text:
            lines.append(f"- Response: {response_text}")
        mismatch_items = case.get("mismatches")
        if isinstance(mismatch_items, list) and mismatch_items:
            rendered = ", ".join(str(item) for item in mismatch_items)
            lines.append(f"- Mismatches: `{rendered}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_category_counts(categories: Any) -> dict[str, int]:
    category_counts: dict[str, int] = {}
    for category in categories:
        rendered = str(category or "unknown")
        category_counts[rendered] = category_counts.get(rendered, 0) + 1
    return category_counts


def _build_quality_lanes(category_counts: dict[str, int]) -> dict[str, bool]:
    return {lane: bool(category_counts.get(lane)) for lane in QUALITY_LANE_KEYS}


def _inspection_records(inspection_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(inspection_payload, dict):
        return []
    read_result = inspection_payload.get("read_result")
    if not isinstance(read_result, dict):
        return []
    records = read_result.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]
