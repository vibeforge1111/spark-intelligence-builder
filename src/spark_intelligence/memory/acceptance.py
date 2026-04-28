from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import approve_pairing, consume_pairing_welcome, rename_agent_identity
from spark_intelligence.memory.orchestrator import hybrid_memory_retrieve
from spark_intelligence.memory.regression import _allocate_regression_identity
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class TelegramMemoryAcceptanceCase:
    case_id: str
    category: str
    message: str
    expected_bridge_mode: str | None = None
    expected_routing_decision: str | None = None
    expected_response_contains: tuple[str, ...] = ()
    expected_response_excludes: tuple[str, ...] = ()


DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES: tuple[TelegramMemoryAcceptanceCase, ...] = (
    TelegramMemoryAcceptanceCase(
        case_id="seed_focus",
        category="current_state",
        message="Set my current focus to persistent memory quality evaluation.",
        expected_response_contains=("persistent memory quality evaluation",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="seed_old_plan",
        category="stale_current_conflict",
        message="Set my current plan to verify scheduled memory cleanup.",
        expected_response_contains=("verify scheduled memory cleanup",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="replace_plan",
        category="stale_current_conflict",
        message="Set my current plan to evaluate open-ended persistent memory recall.",
        expected_response_contains=("evaluate open-ended persistent memory recall",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_seed",
        category="natural_recall",
        message="For later, the tiny desk plant is named Mira.",
        expected_response_contains=("Mira",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="current_focus_plan_query",
        category="current_state",
        message="What is my current focus and plan?",
        expected_bridge_mode="memory_current_focus_plan",
        expected_routing_decision="memory_current_focus_plan_query",
        expected_response_contains=(
            "persistent memory quality evaluation",
            "evaluate open-ended persistent memory recall",
        ),
        expected_response_excludes=("verify scheduled memory cleanup",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="open_ended_next_step",
        category="open_ended_recall",
        message="What should we focus on next?",
        expected_bridge_mode="memory_kernel_next_step",
        expected_routing_decision="memory_kernel_next_step",
        expected_response_contains=(
            "persistent memory quality evaluation",
            "evaluate open-ended persistent memory recall",
        ),
        expected_response_excludes=("verify scheduled memory cleanup",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="source_explanation",
        category="source_explanation",
        message="Why did you answer that?",
        expected_bridge_mode="context_source_debug",
        expected_routing_decision="context_source_debug",
        expected_response_contains=(
            "memory kernel next-step route",
            "promotion gates",
            "current_state",
        ),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_recall",
        category="natural_recall",
        message="What did I name the plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("Mira",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_replace",
        category="stale_current_conflict",
        message="Actually, the tiny desk plant is named Sol.",
        expected_response_contains=("Sol",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_updated_recall",
        category="natural_recall",
        message="What did I name the plant?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("Sol",),
        expected_response_excludes=("Mira",),
    ),
    TelegramMemoryAcceptanceCase(
        case_id="natural_fact_history_recall",
        category="stale_current_conflict",
        message="What was the plant called before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Mira", "Sol"),
    ),
)


@dataclass(frozen=True)
class TelegramMemoryAcceptanceResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark Telegram memory acceptance"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- status: {summary.get('status') or 'unknown'}")
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- matched: {summary.get('matched_case_count', 0)}")
            lines.append(f"- mismatched: {summary.get('mismatched_case_count', 0)}")
            lines.append(f"- gate_status: {summary.get('promotion_gate_status') or 'unknown'}")
            lines.append(f"- selected_user_id: {summary.get('selected_user_id') or 'unknown'}")
        mismatches = self.payload.get("mismatches") if isinstance(self.payload, dict) else []
        if mismatches:
            lines.append("- mismatches:")
            for mismatch in mismatches[:8]:
                if not isinstance(mismatch, dict):
                    continue
                lines.append(
                    f"  - {mismatch.get('case_id') or 'unknown'}: "
                    + ", ".join(str(item) for item in mismatch.get("mismatches", []))
                )
        gate_assertions = self.payload.get("gate_assertions") if isinstance(self.payload, dict) else {}
        gate_mismatches = gate_assertions.get("mismatches") if isinstance(gate_assertions, dict) else []
        if gate_mismatches:
            lines.append("- gate mismatches:")
            lines.extend(f"  - {item}" for item in gate_mismatches)
        return "\n".join(lines)


@dataclass(frozen=True)
class TelegramMemoryAcceptancePackExportResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        artifact_paths = self.payload.get("artifact_paths") if isinstance(self.payload, dict) else {}
        lines = ["Spark Telegram memory acceptance pack export"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- pack_kind: {summary.get('pack_kind') or 'unknown'}")
        if isinstance(artifact_paths, dict):
            if artifact_paths.get("pack_json"):
                lines.append(f"- pack_json: {artifact_paths['pack_json']}")
            if artifact_paths.get("operator_markdown"):
                lines.append(f"- operator_markdown: {artifact_paths['operator_markdown']}")
        return "\n".join(lines)


def export_telegram_memory_acceptance_pack(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    write_path: str | Path | None = None,
    markdown_path: str | Path | None = None,
    cases: tuple[TelegramMemoryAcceptanceCase, ...] = DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES,
) -> TelegramMemoryAcceptancePackExportResult:
    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-acceptance-supervised"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-acceptance-pack.json"
    resolved_markdown_path = Path(markdown_path) if markdown_path else resolved_output_dir / "telegram-memory-acceptance-pack.md"

    case_payloads = [_case_to_operator_payload(index=index, case=case) for index, case in enumerate(cases, start=1)]
    payload = {
        "summary": {
            "pack_kind": "telegram_memory_acceptance_supervised",
            "case_count": len(case_payloads),
            "operator_flow": "send_each_prompt_to_spark_agi_or_tester_and_capture_the_exact_reply",
            "acceptance_gate": "compare_live_replies_against_expected_fragments_then_run_local_blocking_acceptance",
        },
        "cases": case_payloads,
        "shadow_telegram_pack": [
            {
                "message": case.message,
                "case_id": case.case_id,
                "category": case.category,
            }
            for case in cases
        ],
        "capture_template": [
            {
                "case_id": case.case_id,
                "prompt": case.message,
                "live_response": "",
                "operator_notes": "",
            }
            for case in cases
        ],
        "artifact_paths": {
            "pack_json": str(resolved_write_path),
            "operator_markdown": str(resolved_markdown_path),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    resolved_markdown_path.write_text(_render_operator_acceptance_markdown(payload), encoding="utf-8")
    return TelegramMemoryAcceptancePackExportResult(output_dir=resolved_output_dir, payload=payload)


def run_telegram_memory_acceptance(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    write_path: str | Path | None = None,
    cases: tuple[TelegramMemoryAcceptanceCase, ...] = DEFAULT_TELEGRAM_MEMORY_ACCEPTANCE_CASES,
) -> TelegramMemoryAcceptanceResult:
    from spark_intelligence.gateway.runtime import gateway_ask_telegram

    resolved_output_dir = Path(output_dir) if output_dir else config_manager.paths.home / "artifacts" / "telegram-memory-acceptance"
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-acceptance.json"

    selected_user_id = str(user_id or "").strip() or None
    selected_chat_id = str(chat_id or "").strip() or None
    if selected_user_id is None:
        selected_user_id, selected_chat_id = _allocate_regression_identity(chat_id=selected_chat_id)
    elif selected_chat_id is None:
        selected_chat_id = selected_user_id
    _prepare_acceptance_identity(
        state_db=state_db,
        external_user_id=selected_user_id,
        username=username or "memory-acceptance",
    )

    case_payloads: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for case in cases:
        print(f"[memory-acceptance] case:{case.case_id}", file=sys.stderr, flush=True)
        raw = gateway_ask_telegram(
            config_manager=config_manager,
            state_db=state_db,
            message=case.message,
            user_id=selected_user_id,
            username=username,
            chat_id=selected_chat_id,
            as_json=True,
        )
        case_result = _build_acceptance_case_result(case=case, payload=_parse_json_object(raw))
        case_payloads.append(case_result)
        if not case_result.get("matched_expectations", True):
            mismatches.append(case_result)

    memory_subject = f"human:telegram:{selected_user_id}"
    gate_assertions = _build_acceptance_gate_assertions(
        config_manager=config_manager,
        state_db=state_db,
        memory_subject=memory_subject,
    )
    gate_mismatches = gate_assertions.get("mismatches") if isinstance(gate_assertions, dict) else []
    gate_enforcement = gate_assertions.get("enforcement") if isinstance(gate_assertions, dict) else {}
    status = "passed" if not mismatches and not gate_mismatches else "failed"
    summary = {
        "status": status,
        "case_count": len(case_payloads),
        "matched_case_count": len(case_payloads) - len(mismatches),
        "mismatched_case_count": len(mismatches),
        "promotion_gate_status": gate_assertions.get("status"),
        "promotion_gate_mismatch_count": len(gate_mismatches or []),
        "promotion_gate_enforcement": (gate_enforcement or {}).get("mode") or "blocking_acceptance",
        "promotion_gate_blocking": bool((gate_enforcement or {}).get("blocking")),
        "selected_user_id": selected_user_id,
        "selected_chat_id": selected_chat_id,
        "human_id": memory_subject,
        "quality_lanes": _acceptance_quality_lanes(case_payloads),
    }
    payload = {
        "summary": summary,
        "cases": case_payloads,
        "mismatches": mismatches,
        "gate_assertions": gate_assertions,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramMemoryAcceptanceResult(output_dir=resolved_output_dir, payload=payload)


def _case_to_operator_payload(*, index: int, case: TelegramMemoryAcceptanceCase) -> dict[str, Any]:
    return {
        "index": index,
        "case_id": case.case_id,
        "category": case.category,
        "prompt": case.message,
        "expected_bridge_mode": case.expected_bridge_mode,
        "expected_routing_decision": case.expected_routing_decision,
        "expected_response_contains": list(case.expected_response_contains),
        "expected_response_excludes": list(case.expected_response_excludes),
    }


def _render_operator_acceptance_markdown(payload: dict[str, Any]) -> str:
    cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    lines = [
        "# Telegram Memory Acceptance Pack",
        "",
        "Send each prompt to Spark AGI or Tester in order. Capture the exact reply under each case.",
        "",
        "Pass rule: every expected fragment must appear, every excluded fragment must be absent, and source/gate explanations must stay aligned with the local blocking acceptance run.",
        "",
    ]
    for item in cases:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"## {item.get('index')}. {item.get('case_id')}",
                "",
                f"Category: `{item.get('category')}`",
                "",
                "Prompt:",
                "",
                "```text",
                str(item.get("prompt") or ""),
                "```",
                "",
            ]
        )
        expected_contains = [str(value) for value in item.get("expected_response_contains") or []]
        expected_excludes = [str(value) for value in item.get("expected_response_excludes") or []]
        if item.get("expected_bridge_mode") or item.get("expected_routing_decision"):
            lines.append("Expected route:")
            if item.get("expected_bridge_mode"):
                lines.append(f"- bridge_mode: `{item.get('expected_bridge_mode')}`")
            if item.get("expected_routing_decision"):
                lines.append(f"- routing_decision: `{item.get('expected_routing_decision')}`")
            lines.append("")
        if expected_contains:
            lines.append("Expected response fragments:")
            lines.extend(f"- `{fragment}`" for fragment in expected_contains)
            lines.append("")
        if expected_excludes:
            lines.append("Forbidden response fragments:")
            lines.extend(f"- `{fragment}`" for fragment in expected_excludes)
            lines.append("")
        lines.extend(["Live response:", "", "```text", "", "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def _prepare_acceptance_identity(*, state_db: StateDB, external_user_id: str, username: str) -> None:
    approve_pairing(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
        display_name=username,
    )
    rename_agent_identity(
        state_db=state_db,
        human_id=f"human:telegram:{external_user_id}",
        new_name="Atlas",
        source_surface="memory_acceptance",
        source_ref="memory-acceptance-setup",
    )
    consume_pairing_welcome(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
    )


def _build_acceptance_case_result(*, case: TelegramMemoryAcceptanceCase, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    bridge_mode = str(detail.get("bridge_mode") or payload.get("bridge_mode") or "").strip()
    routing_decision = str(detail.get("routing_decision") or payload.get("routing_decision") or "").strip()
    response_text = str(detail.get("response_text") or payload.get("response_text") or "").strip()
    mismatches: list[str] = []
    if str(result.get("decision") or payload.get("decision") or "").strip() != "allowed":
        mismatches.append(f"decision:{str(result.get('decision') or payload.get('decision') or 'missing').strip()}")
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
        "decision": str(result.get("decision") or payload.get("decision") or "").strip(),
        "bridge_mode": bridge_mode,
        "routing_decision": routing_decision,
        "response_text": response_text,
        "trace_ref": str(detail.get("trace_ref") or payload.get("trace_ref") or "").strip(),
        "matched_expectations": not mismatches,
        "mismatches": mismatches,
        "gateway_payload": payload,
    }


def _build_acceptance_gate_assertions(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    memory_subject: str,
) -> dict[str, Any]:
    result = hybrid_memory_retrieve(
        config_manager=config_manager,
        state_db=state_db,
        query="What should we focus on next?",
        subject=memory_subject,
        predicate="profile.current_focus",
        limit=5,
        actor_id="memory_acceptance",
        source_surface="telegram_memory_acceptance_gate_probe",
        record_activity=False,
    )
    packet = result.context_packet.to_payload() if result.context_packet is not None else {}
    trace = packet.get("trace") if isinstance(packet, dict) else {}
    promotion_gates = trace.get("promotion_gates") if isinstance(trace, dict) else {}
    gates = promotion_gates.get("gates") if isinstance(promotion_gates.get("gates"), dict) else {}
    mismatches: list[str] = []
    if promotion_gates.get("status") != "pass":
        mismatches.append(f"promotion_gate_status:{promotion_gates.get('status') or 'missing'}")
    for gate_name in (
        "source_swamp_resistance",
        "stale_current_conflict",
        "recent_conversation_noise",
        "source_mix_stability",
    ):
        gate = gates.get(gate_name) if isinstance(gates, dict) else None
        status = gate.get("status") if isinstance(gate, dict) else None
        if status != "pass":
            mismatches.append(f"{gate_name}:{status or 'missing'}")
    source_mix = packet.get("source_mix") if isinstance(packet, dict) else {}
    try:
        current_state_count = int((source_mix or {}).get("current_state") or 0)
    except (TypeError, ValueError):
        current_state_count = 0
    if current_state_count < 1:
        mismatches.append("source_mix:current_state_missing")
    enforcement = {
        "mode": "blocking_acceptance",
        "accepted_statuses": ["pass"],
        "blocking": bool(mismatches),
        "blockers": list(mismatches),
    }
    return {
        "status": promotion_gates.get("status") or "missing",
        "mismatches": mismatches,
        "enforcement": enforcement,
        "promotion_gates": promotion_gates,
        "source_mix": source_mix if isinstance(source_mix, dict) else {},
        "context_packet_sections": [
            section.get("section")
            for section in (packet.get("sections") if isinstance(packet, dict) else []) or []
            if isinstance(section, dict)
        ],
    }


def _acceptance_quality_lanes(case_payloads: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in case_payloads:
        category = str(case.get("category") or "unknown").strip() or "unknown"
        counts[category] = counts.get(category, 0) + 1
    return counts


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_output": raw}
    return parsed if isinstance(parsed, dict) else {"raw_output": raw}
