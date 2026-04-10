from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
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
        case_id="mission_write",
        category="profile_write",
        message="I am trying to survive the hack and revive the companies.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
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
) -> TelegramMemoryRegressionResult:
    from spark_intelligence.gateway.runtime import gateway_ask_telegram

    resolved_output_dir = Path(output_dir) if output_dir else _default_output_dir(config_manager)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_kb_output_dir = resolved_output_dir / "kb"
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-regression.json"
    kb_write_path = resolved_output_dir / "telegram-memory-kb.json"

    case_payloads: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    selected_user_id = str(user_id or "").strip() or None
    selected_chat_id = str(chat_id or "").strip() or None

    for case in DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES:
        raw = gateway_ask_telegram(
            config_manager=config_manager,
            state_db=state_db,
            message=case.message,
            user_id=selected_user_id,
            username=username,
            chat_id=selected_chat_id,
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

    kb_result = build_telegram_state_knowledge_base(
        config_manager=config_manager,
        output_dir=resolved_kb_output_dir,
        limit=max(int(kb_limit), 1),
        chat_id=selected_chat_id,
        write_path=kb_write_path,
        validator_root=validator_root,
    )
    kb_payload = kb_result.payload
    current_probe = _probe_row(kb_payload, "current_state")
    evidence_probe = _probe_row(kb_payload, "evidence")
    summary = {
        "case_count": len(case_payloads),
        "matched_case_count": len(case_payloads) - len(mismatches),
        "mismatched_case_count": len(mismatches),
        "selected_user_id": selected_user_id,
        "selected_chat_id": selected_chat_id,
        "human_id": resolved_human_id,
        "kb_has_probe_coverage": _nested_get(kb_payload, "failure_taxonomy", "summary", "has_probe_coverage", default=False),
        "kb_issue_labels": _nested_get(kb_payload, "failure_taxonomy", "summary", "issue_labels", default=[]),
        "kb_current_state_hits": current_probe.get("hits", 0),
        "kb_current_state_total": current_probe.get("total", 0),
        "kb_evidence_hits": evidence_probe.get("hits", 0),
        "kb_evidence_total": evidence_probe.get("total", 0),
    }
    payload = {
        "summary": summary,
        "cases": case_payloads,
        "mismatches": mismatches,
        "inspection": inspection_payload,
        "kb_compile": kb_payload,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
            "kb_output_dir": str(resolved_kb_output_dir),
            "kb_json": str(kb_write_path),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)


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
    return {
        "case_id": case.case_id,
        "category": case.category,
        "message": case.message,
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
