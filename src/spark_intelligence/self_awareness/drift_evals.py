from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.self_awareness.conversation_frame import (
    ActionGateResult,
    ConversationOperatingFrame,
    FinalAnswerDriftCheck,
    build_conversation_operating_frame,
    check_final_answer_drift,
    evaluate_action_gate,
)
from spark_intelligence.self_awareness.operating_context import build_agent_operating_context
from spark_intelligence.self_awareness.source_hierarchy import SourceClaim, resolve_source_claims
from spark_intelligence.state.db import StateDB


AGENT_DRIFT_EVAL_SCHEMA_VERSION = "spark.agent_drift_eval.v1"


@dataclass(frozen=True)
class AgentDriftEvalCase:
    case_id: str
    user_message: str
    active_reference_items: tuple[str, ...] = ()
    proposed_action: str | None = None
    draft_answer: str | None = None
    source_claims: tuple[SourceClaim, ...] = ()
    spark_access_level: str = ""
    runner_writable: bool | None = None
    runner_label: str = ""
    expected_mode: str | None = None
    expected_gate_decision: str | None = None
    expected_drift_type: str | None = None
    expected_reference_status: str | None = None
    expected_selected_item: str | None = None
    expected_winner_source: str | None = None
    expected_stale_sources: tuple[str, ...] = ()
    expected_recommended_route: str | None = None
    expected_summary_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentDriftEvalResult:
    case_id: str
    passed: bool
    checks: list[dict[str, Any]]
    frame: ConversationOperatingFrame
    action_gate: ActionGateResult | None = None
    final_answer_check: FinalAnswerDriftCheck | None = None
    source_resolutions: list[dict[str, Any]] = field(default_factory=list)
    aoc: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "checks": list(self.checks),
            "frame": self.frame.to_payload(),
            "action_gate": self.action_gate.to_payload() if self.action_gate else None,
            "final_answer_check": self.final_answer_check.to_payload() if self.final_answer_check else None,
            "source_resolutions": list(self.source_resolutions),
            "aoc": self.aoc,
        }


@dataclass(frozen=True)
class AgentDriftEvalReport:
    results: list[AgentDriftEvalResult]

    def to_payload(self) -> dict[str, Any]:
        passed = sum(1 for result in self.results if result.passed)
        failed = len(self.results) - passed
        return {
            "schema_version": AGENT_DRIFT_EVAL_SCHEMA_VERSION,
            "counts": {
                "total": len(self.results),
                "passed": passed,
                "failed": failed,
            },
            "results": [result.to_payload() for result in self.results],
        }

    def to_text(self) -> str:
        payload = self.to_payload()
        counts = payload["counts"]
        lines = [
            f"Agent drift evals: {counts['passed']}/{counts['total']} passed",
        ]
        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            lines.append(f"- {status} {result.case_id}")
            for check in result.checks:
                if not check["passed"]:
                    lines.append(
                        f"  expected {check['name']}={check['expected']!r}, got {check['actual']!r}"
                    )
        return "\n".join(lines)


def default_agent_drift_eval_cases() -> list[AgentDriftEvalCase]:
    return [
        AgentDriftEvalCase(
            case_id="concept_question_does_not_run_diagnostics",
            user_message="How would AOC connect to existing Spark systems without rebuilding what exists?",
            proposed_action="run_diagnostics",
            draft_answer="Spark self-improvement plan\nMode: plan_only_probe_first\nRun the safest probe first.",
            expected_mode="concept_chat",
            expected_gate_decision="blocked",
            expected_drift_type="unrequested_diagnostics_plan",
        ),
        AgentDriftEvalCase(
            case_id="user_says_drifted_resets_to_chat",
            user_message="drifted again lol",
            proposed_action="start_mission",
            expected_mode="concept_chat",
            expected_gate_decision="blocked",
        ),
        AgentDriftEvalCase(
            case_id="mission_control_mention_is_not_permission",
            user_message="How should the AOC panel mention Mission Control without confusing agents?",
            proposed_action="open_mission_control",
            expected_mode="concept_chat",
            expected_gate_decision="blocked",
        ),
        AgentDriftEvalCase(
            case_id="option_two_resolves_latest_list",
            user_message="option 2",
            active_reference_items=("AOC UI polish", "black box recorder", "memory approval inbox"),
            expected_mode="concept_chat",
            expected_reference_status="resolved",
            expected_selected_item="black box recorder",
        ),
        AgentDriftEvalCase(
            case_id="old_access_memory_goes_stale",
            user_message="Access looks different now.",
            source_claims=(
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 1",
                    source="approved_memory",
                    source_ref="memory:old-access",
                    freshness="stale",
                ),
                SourceClaim(
                    claim_key="spark_access_level",
                    value="Level 4",
                    source="current_diagnostics",
                    source_ref="diagnostics:now",
                    freshness="live_probed",
                ),
            ),
            expected_winner_source="current_diagnostics",
            expected_stale_sources=("approved_memory",),
        ),
        AgentDriftEvalCase(
            case_id="level_four_read_only_routes_to_writable_mission",
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
            expected_mode="patch_work",
            expected_recommended_route="writable_spawner_codex_mission",
            expected_summary_contains=(
                "cannot patch files in this runner",
                "route to writable Spawner/Codex",
            ),
        ),
        AgentDriftEvalCase(
            case_id="concept_chat_keywords_do_not_hijack_writable_route",
            user_message="How should local workspace access, Docker build, and tests fit into the AOC design?",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
            expected_mode="concept_chat",
            expected_recommended_route="chat",
        ),
        AgentDriftEvalCase(
            case_id="explicit_action_intent_can_route_local_work",
            user_message="run tests in the local workspace",
            spark_access_level="4",
            runner_writable=True,
            runner_label="workspace-write runner",
            expected_mode="concept_chat",
            expected_recommended_route="current_writable_runner",
        ),
    ]


def run_agent_drift_eval_suite(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    cases: list[AgentDriftEvalCase] | None = None,
) -> AgentDriftEvalReport:
    return AgentDriftEvalReport(
        results=[
            _run_case(config_manager=config_manager, state_db=state_db, case=case)
            for case in (cases or default_agent_drift_eval_cases())
        ]
    )


def _run_case(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    case: AgentDriftEvalCase,
) -> AgentDriftEvalResult:
    frame = build_conversation_operating_frame(
        user_message=case.user_message,
        active_reference_items=list(case.active_reference_items),
        source_turn_id=case.case_id,
    )
    gate = evaluate_action_gate(frame, proposed_action=case.proposed_action) if case.proposed_action else None
    drift_check = check_final_answer_drift(frame, draft_answer=case.draft_answer) if case.draft_answer is not None else None
    resolutions = resolve_source_claims(list(case.source_claims)) if case.source_claims else []
    aoc_payload: dict[str, Any] | None = None
    if case.expected_recommended_route or case.expected_summary_contains:
        aoc_payload = build_agent_operating_context(
            config_manager=config_manager,
            state_db=state_db,
            user_message=case.user_message,
            spark_access_level=case.spark_access_level,
            runner_writable=case.runner_writable,
            runner_label=case.runner_label,
        ).to_payload()

    checks = _case_checks(
        case=case,
        frame=frame,
        gate=gate,
        drift_check=drift_check,
        resolutions=[resolution.to_payload() for resolution in resolutions],
        aoc_payload=aoc_payload,
    )
    return AgentDriftEvalResult(
        case_id=case.case_id,
        passed=all(check["passed"] for check in checks),
        checks=checks,
        frame=frame,
        action_gate=gate,
        final_answer_check=drift_check,
        source_resolutions=[resolution.to_payload() for resolution in resolutions],
        aoc=aoc_payload,
    )


def _case_checks(
    *,
    case: AgentDriftEvalCase,
    frame: ConversationOperatingFrame,
    gate: ActionGateResult | None,
    drift_check: FinalAnswerDriftCheck | None,
    resolutions: list[dict[str, Any]],
    aoc_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if case.expected_mode:
        checks.append(_check("mode", case.expected_mode, frame.current_mode))
    if case.expected_gate_decision:
        checks.append(_check("gate_decision", case.expected_gate_decision, gate.decision if gate else None))
    if case.expected_drift_type:
        checks.append(_check("drift_type", case.expected_drift_type, drift_check.drift_type if drift_check else None))
    if case.expected_reference_status:
        checks.append(
            _check(
                "reference_status",
                case.expected_reference_status,
                frame.active_reference_list.get("resolution_status"),
            )
        )
    if case.expected_selected_item:
        checks.append(_check("selected_item", case.expected_selected_item, frame.active_reference_list.get("selected_item")))
    if case.expected_winner_source:
        winner_source = (resolutions[0].get("winner") or {}).get("source") if resolutions else None
        checks.append(_check("winner_source", case.expected_winner_source, winner_source))
    for expected_source in case.expected_stale_sources:
        stale_sources = [
            str(claim.get("source") or "")
            for resolution in resolutions
            for claim in resolution.get("stale_claims", [])
            if isinstance(claim, dict)
        ]
        checks.append(_check(f"stale_source:{expected_source}", True, expected_source in stale_sources))
    if case.expected_recommended_route:
        route = ((aoc_payload or {}).get("task_fit") or {}).get("recommended_route")
        checks.append(_check("recommended_route", case.expected_recommended_route, route))
    for expected_text in case.expected_summary_contains:
        summary = str((aoc_payload or {}).get("agent_facing_summary") or "")
        checks.append(_check(f"summary_contains:{expected_text}", True, expected_text in summary))
    return checks


def _check(name: str, expected: object, actual: object) -> dict[str, Any]:
    return {
        "name": name,
        "expected": expected,
        "actual": actual,
        "passed": expected == actual,
    }
