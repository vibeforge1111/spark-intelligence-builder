from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from spark_intelligence.auth.runtime import build_runtime_provider_reference_payload
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import close_run, open_run, record_event
from spark_intelligence.state.db import StateDB


_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_VOICE_SPEAK_RE = re.compile(
    r"^(?:say|speak|voice|read(?:\s+this)?|send(?:\s+this)?\s+as\s+voice|reply(?:\s+with)?\s+voice)[:\s-]+(?P<text>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_VOICE_AUDIO_BASE64_RE = re.compile(
    r"(?P<prefix>\baudio_base64\s*[:=]\s*)(?P<audio>[^\s]+)",
    re.IGNORECASE,
)
_VOICE_MIME_TYPE_RE = re.compile(r"\bmime_type\s*[:=]\s*(?P<mime>[A-Za-z0-9.+/-]+)", re.IGNORECASE)
_VOICE_FILENAME_RE = re.compile(r"\bfilename\s*[:=]\s*(?P<filename>[^\s]+)", re.IGNORECASE)


@dataclass(frozen=True)
class HarnessTaskEnvelope:
    envelope_id: str
    task: str
    harness_id: str
    owner_system: str
    backend_kind: str
    session_scope: str
    prompt_strategy: str
    route_mode: str
    required_capabilities: list[str]
    artifacts_expected: list[str]
    next_actions: list[str]
    limitations: list[str]
    channel_kind: str | None
    session_id: str | None
    human_id: str | None
    agent_id: str | None
    turn_intent_payload: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "task": _redact_harness_task_payload(self.task),
            "harness_id": self.harness_id,
            "owner_system": self.owner_system,
            "backend_kind": self.backend_kind,
            "session_scope": self.session_scope,
            "prompt_strategy": self.prompt_strategy,
            "route_mode": self.route_mode,
            "required_capabilities": self.required_capabilities,
            "artifacts_expected": self.artifacts_expected,
            "next_actions": self.next_actions,
            "limitations": self.limitations,
            "channel_kind": self.channel_kind,
            "session_id": self.session_id,
            "human_id": self.human_id,
            "agent_id": self.agent_id,
            "turn_intent_payload": self.turn_intent_payload,
        }

    def with_turn_intent_payload(self, payload: dict[str, Any] | None) -> "HarnessTaskEnvelope":
        return replace(self, turn_intent_payload=payload)


@dataclass(frozen=True)
class HarnessExecutionResult:
    envelope: HarnessTaskEnvelope
    run_id: str
    status: str
    summary: str
    artifacts: dict[str, Any]
    next_actions: list[str]
    chain_status: str | None = None
    chained_results: list["HarnessExecutionResult"] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "envelope": self.envelope.to_payload(),
            "run_id": self.run_id,
            "status": self.status,
            "summary": self.summary,
            "artifacts": self.artifacts,
            "next_actions": self.next_actions,
            "chain_status": self.chain_status,
            "chained_results": [item.to_payload() for item in (self.chained_results or [])],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


@dataclass(frozen=True)
class HarnessRuntimeSnapshot:
    generated_at: str
    workspace_id: str
    summary: dict[str, Any]
    recent_runs: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "summary": self.summary,
            "recent_runs": self.recent_runs,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


def build_harness_task_envelope(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
    forced_harness_id: str | None = None,
    channel_kind: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
    turn_intent_payload: dict[str, Any] | None = None,
) -> HarnessTaskEnvelope:
    from spark_intelligence.harness_registry import build_harness_registry, build_harness_selection

    normalized_forced_harness_id = str(forced_harness_id or "").strip()
    if normalized_forced_harness_id:
        registry = build_harness_registry(config_manager=config_manager, state_db=state_db)
        contracts = {contract.harness_id: contract for contract in registry.contracts}
        contract = contracts.get(normalized_forced_harness_id)
        if contract is None:
            available = ", ".join(sorted(contracts))
            raise ValueError(
                f"Unknown harness id '{normalized_forced_harness_id}'. Available harnesses: {available}"
            )
        selection_payload = {
            "harness_id": contract.harness_id,
            "owner_system": contract.owner_system,
            "backend_kind": contract.backend_kind,
            "session_scope": contract.session_scope,
            "prompt_strategy": contract.prompt_strategy,
            "route_mode": "forced_harness",
            "required_capabilities": list(contract.required_capabilities),
            "artifacts": list(contract.artifacts),
            "next_actions": [f"Operator forced harness selection to {contract.harness_id}."],
            "limitations": list(contract.limitations),
        }
    else:
        selection = build_harness_selection(
            config_manager=config_manager,
            state_db=state_db,
            task=task,
        )
        selection_payload = {
            "harness_id": selection.harness_id,
            "owner_system": selection.owner_system,
            "backend_kind": selection.backend_kind,
            "session_scope": selection.session_scope,
            "prompt_strategy": selection.prompt_strategy,
            "route_mode": selection.route_mode,
            "required_capabilities": list(selection.required_capabilities),
            "artifacts": list(selection.artifacts),
            "next_actions": list(selection.next_actions),
            "limitations": list(selection.limitations),
        }
    return HarnessTaskEnvelope(
        envelope_id=f"htask:{uuid4().hex[:12]}",
        task=str(task or "").strip(),
        harness_id=str(selection_payload["harness_id"]),
        owner_system=str(selection_payload["owner_system"]),
        backend_kind=str(selection_payload["backend_kind"]),
        session_scope=str(selection_payload["session_scope"]),
        prompt_strategy=str(selection_payload["prompt_strategy"]),
        route_mode=str(selection_payload["route_mode"]),
        required_capabilities=list(selection_payload["required_capabilities"]),
        artifacts_expected=list(selection_payload["artifacts"]),
        next_actions=list(selection_payload["next_actions"]),
        limitations=list(selection_payload["limitations"]),
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        turn_intent_payload=turn_intent_payload,
    )


def build_harness_local_operator_turn_intent(envelope: HarnessTaskEnvelope) -> dict[str, Any] | None:
    if envelope.harness_id == "builder.direct":
        from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

        return build_vnext_tool_intent_envelope(
            surface=envelope.channel_kind or "cli",
            actor_id_ref=envelope.human_id or "human:local-operator",
            request_id=envelope.envelope_id,
            source_kind="local_operator_harness_execute",
            tool_name="builder.direct",
            owner_system="spark-intelligence-builder",
            mutation_class="read_only",
            intent_summary="Local operator explicitly requested governed Builder direct execution through the harness runtime.",
            raw_turn_summary=f"Builder harness runtime summarized local operator task {envelope.envelope_id}; raw task stays offloaded.",
            confidence=0.95,
        )

    if envelope.harness_id == "researcher.advisory":
        from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

        return build_vnext_tool_intent_envelope(
            surface=envelope.channel_kind or "cli",
            actor_id_ref=envelope.human_id or "human:local-operator",
            request_id=envelope.envelope_id,
            source_kind="local_operator_harness_execute",
            tool_name="researcher.advisory",
            owner_system="spark-researcher",
            mutation_class="external_network",
            external_network=True,
            intent_summary="Local operator explicitly requested governed researcher advisory execution through the Builder harness runtime.",
            raw_turn_summary=f"Builder harness runtime summarized local operator task {envelope.envelope_id}; raw task stays offloaded.",
            confidence=0.95,
        )

    if envelope.harness_id == "browser.grounded":
        from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

        return build_vnext_tool_intent_envelope(
            surface=envelope.channel_kind or "cli",
            actor_id_ref=envelope.human_id or "human:local-operator",
            request_id=envelope.envelope_id,
            source_kind="local_operator_harness_execute",
            tool_name="browser.navigate",
            owner_system="spark-browser",
            mutation_class="external_network",
            external_network=True,
            intent_summary="Local operator explicitly requested governed browser navigation through the harness runtime.",
            raw_turn_summary=f"Builder harness runtime summarized local operator browser task {envelope.envelope_id}; raw task stays offloaded.",
            confidence=0.95,
        )

    if envelope.harness_id == "swarm.escalation":
        from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

        return build_vnext_tool_intent_envelope(
            surface=envelope.channel_kind or "cli",
            actor_id_ref=envelope.human_id or "human:local-operator",
            request_id=envelope.envelope_id,
            source_kind="local_operator_harness_execute",
            tool_name="swarm.sync.dry_run",
            owner_system="spark-swarm",
            mutation_class="writes_files",
            intent_summary="Local operator explicitly requested governed Swarm dry-run payload preparation through the Builder harness runtime.",
            raw_turn_summary=f"Builder harness runtime summarized local operator Swarm task {envelope.envelope_id}; raw task stays offloaded.",
            confidence=0.95,
        )

    if envelope.harness_id != "voice.io":
        return None

    from spark_intelligence.harness_contract import build_vnext_action_intent_envelope

    task_mode, _ = _classify_voice_task(envelope.task)
    actions: list[dict[str, Any]] = [
        {
            "tool_name": "voice.status",
            "owner_system": "spark-voice-comms",
            "mutation_class": "read_only",
            "summary": "Local operator requested voice status before any voice I/O action.",
        }
    ]
    if task_mode == "speak":
        actions.append(
            {
                "tool_name": "voice.speak",
                "owner_system": "spark-voice-comms",
                "mutation_class": "external_network",
                "external_network": True,
                "summary": "Local operator explicitly requested speech synthesis through voice I/O.",
            }
        )
    elif task_mode == "transcribe":
        actions.append(
            {
                "tool_name": "voice.transcribe",
                "owner_system": "spark-voice-comms",
                "mutation_class": "external_network",
                "external_network": True,
                "summary": "Local operator explicitly requested transcription through voice I/O.",
            }
        )

    return build_vnext_action_intent_envelope(
        surface=envelope.channel_kind or "cli",
        actor_id_ref=envelope.human_id or "human:local-operator",
        request_id=envelope.envelope_id,
        source_kind="local_operator_harness_execute",
        intent_summary="Local operator explicitly requested governed voice I/O through the Builder harness runtime.",
        raw_turn_summary=f"Builder harness runtime summarized local operator task {envelope.envelope_id}; raw task stays offloaded.",
        actions=actions,
        confidence=0.95,
    )


def with_harness_local_operator_turn_intent(envelope: HarnessTaskEnvelope) -> HarnessTaskEnvelope:
    payload = build_harness_local_operator_turn_intent(envelope)
    if payload is None:
        return envelope
    return envelope.with_turn_intent_payload(payload)


def execute_harness_task(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    parent_run_id: str | None = None,
) -> HarnessExecutionResult:
    run = open_run(
        state_db,
        run_kind=f"harness:{envelope.harness_id}",
        origin_surface="harness_runtime",
        summary=f"Harness execution opened for {envelope.harness_id}.",
        request_id=envelope.envelope_id,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        parent_run_id=parent_run_id,
        reason_code="harness_execution_started",
        facts={
            "harness_id": envelope.harness_id,
            "backend_kind": envelope.backend_kind,
            "route_mode": envelope.route_mode,
        },
    )
    record_event(
        state_db,
        event_type="harness_execution_started",
        component="harness_runtime",
        summary=f"Started {envelope.harness_id} for task envelope {envelope.envelope_id}.",
        run_id=run.run_id,
        request_id=envelope.envelope_id,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        reason_code="harness_execution_started",
        facts=envelope.to_payload(),
    )
    try:
        if envelope.harness_id == "builder.direct":
            artifacts, summary, status = _execute_builder_direct_harness(
                state_db=state_db,
                envelope=envelope,
                run_id=run.run_id,
            )
        elif envelope.harness_id == "researcher.advisory":
            artifacts, summary, status = _execute_researcher_advisory_harness(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
                run_id=run.run_id,
            )
        elif envelope.harness_id == "browser.grounded":
            artifacts, summary, status = _execute_browser_grounded_harness(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
                run_id=run.run_id,
            )
        elif envelope.harness_id == "voice.io":
            artifacts, summary, status = _execute_voice_io_harness(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
                run_id=run.run_id,
            )
        elif envelope.harness_id == "swarm.escalation":
            artifacts, summary, status = _execute_swarm_escalation_harness(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
                run_id=run.run_id,
            )
        else:
            summary = f"{envelope.harness_id} execution contract prepared but no active runner exists yet."
            artifacts = {
                "execution_contract": {
                    "owner_system": envelope.owner_system,
                    "backend_kind": envelope.backend_kind,
                    "session_scope": envelope.session_scope,
                    "required_capabilities": envelope.required_capabilities,
                }
            }
            status = "planned"
        close_run(
            state_db,
            run_id=run.run_id,
            status="closed",
            close_reason="harness_execution_completed",
            summary=summary,
            facts={
                "harness_id": envelope.harness_id,
                "execution_status": status,
                "artifact_keys": sorted(artifacts.keys()),
            },
        )
        record_event(
            state_db,
            event_type="harness_execution_completed",
            component="harness_runtime",
            summary=summary,
            run_id=run.run_id,
            request_id=envelope.envelope_id,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
            actor_id="harness_runtime",
            reason_code="harness_execution_completed",
            facts={
                "harness_id": envelope.harness_id,
                "execution_status": status,
                "artifact_keys": sorted(artifacts.keys()),
            },
        )
        return HarnessExecutionResult(
            envelope=envelope,
            run_id=run.run_id,
            status=status,
            summary=summary,
            artifacts=artifacts,
            next_actions=list(envelope.next_actions),
        )
    except Exception as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="harness_execution_failed",
            summary=f"Harness execution failed for {envelope.harness_id}.",
            facts={"error": str(exc), "harness_id": envelope.harness_id},
        )
        record_event(
            state_db,
            event_type="harness_execution_failed",
            component="harness_runtime",
            summary=f"Harness execution failed for {envelope.harness_id}.",
            run_id=run.run_id,
            request_id=envelope.envelope_id,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
            actor_id="harness_runtime",
            reason_code="harness_execution_failed",
            facts={"error": str(exc), "harness_id": envelope.harness_id},
        )
        raise


def execute_harness_chain(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    follow_up_harness_ids: list[str] | None = None,
) -> HarnessExecutionResult:
    primary_result = execute_harness_task(
        config_manager=config_manager,
        state_db=state_db,
        envelope=envelope,
    )
    normalized_follow_ups = [str(item or "").strip() for item in (follow_up_harness_ids or []) if str(item or "").strip()]
    if not normalized_follow_ups:
        return primary_result

    chained_results: list[HarnessExecutionResult] = []
    chain_status = "completed"
    current_result = primary_result
    for harness_id in normalized_follow_ups:
        if current_result.status not in {"completed", "prepared"}:
            chain_status = "blocked"
            break
        derived_task = _derive_follow_up_task(
            current_result=current_result,
            target_harness_id=harness_id,
        )
        next_envelope = build_harness_task_envelope(
            config_manager=config_manager,
            state_db=state_db,
            task=derived_task,
            forced_harness_id=harness_id,
            channel_kind=envelope.channel_kind,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
        )
        next_envelope = with_harness_local_operator_turn_intent(next_envelope)
        current_result = execute_harness_task(
            config_manager=config_manager,
            state_db=state_db,
            envelope=next_envelope,
            parent_run_id=primary_result.run_id,
        )
        chained_results.append(current_result)
        if current_result.status not in {"completed", "prepared"}:
            chain_status = "blocked"
            break

    return HarnessExecutionResult(
        envelope=primary_result.envelope,
        run_id=primary_result.run_id,
        status=primary_result.status,
        summary=primary_result.summary,
        artifacts=primary_result.artifacts,
        next_actions=primary_result.next_actions,
        chain_status=chain_status,
        chained_results=chained_results,
    )


def build_harness_runtime_snapshot(
    config_manager: ConfigManager,
    state_db: StateDB,
    *,
    limit: int = 8,
) -> HarnessRuntimeSnapshot:
    workspace_id = str(config_manager.get_path("workspace.id", default="default"))
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, run_kind, status, request_id, session_id, opened_at, closed_at, close_reason, summary_json
            FROM builder_runs
            WHERE run_kind LIKE 'harness:%'
            ORDER BY opened_at DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    recent_runs: list[dict[str, Any]] = []
    for row in rows:
        run_kind = str(row["run_kind"] or "")
        recent_runs.append(
            {
                "run_id": str(row["run_id"]),
                "harness_id": run_kind.split("harness:", 1)[1] if "harness:" in run_kind else run_kind,
                "status": str(row["status"] or ""),
                "request_id": str(row["request_id"]) if row["request_id"] else None,
                "session_id": str(row["session_id"]) if row["session_id"] else None,
                "opened_at": str(row["opened_at"]) if row["opened_at"] else None,
                "closed_at": str(row["closed_at"]) if row["closed_at"] else None,
                "close_reason": str(row["close_reason"]) if row["close_reason"] else None,
                "summary_json": json.loads(str(row["summary_json"])) if row["summary_json"] else {},
            }
        )
    summary = {
        "recent_run_count": len(recent_runs),
        "open_run_count": len([item for item in recent_runs if item.get("status") == "open"]),
        "failed_run_count": len([item for item in recent_runs if item.get("status") == "failed"]),
        "last_harness_id": recent_runs[0]["harness_id"] if recent_runs else None,
    }
    return HarnessRuntimeSnapshot(
        generated_at=_now_iso(),
        workspace_id=workspace_id,
        summary=summary,
        recent_runs=recent_runs,
    )


def _execute_browser_grounded_harness(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
) -> tuple[dict[str, Any], str, str]:
    from spark_intelligence.browser import build_browser_navigate_payload, build_browser_status_payload

    url = _extract_first_url(envelope.task)
    if not url:
        return (
            {
                "browser_status_payload": build_browser_status_payload(config_manager=config_manager),
                "needs_input": {
                    "reason": "Browser grounded harness requires an explicit URL for the first executable runner.",
                    "task": envelope.task,
                },
            },
            "Browser grounded harness needs an explicit URL before it can prepare a navigate payload.",
            "needs_input",
        )

    authority = _authorize_harness_browser_navigate(
        state_db=state_db,
        envelope=envelope,
        run_id=run_id,
    )
    if not authority.allowed:
        return (
            {
                "harness_authority": _harness_authority_artifact(authority, fallback_tool_name="browser.navigate"),
            },
            "Browser grounded harness is blocked because no executable Harness authority was present.",
            "blocked",
        )

    navigate_payload = build_browser_navigate_payload(
        config_manager=config_manager,
        url=url,
        agent_id=envelope.agent_id,
        request_id=envelope.envelope_id,
    )
    if isinstance(authority.governor_decision, dict):
        navigate_payload["governor_decision"] = authority.governor_decision
    if isinstance(envelope.turn_intent_payload, dict):
        navigate_payload["turn_intent_envelope_vnext"] = envelope.turn_intent_payload
    _record_harness_browser_navigate_result_ledger(
        state_db=state_db,
        verdict=authority,
        envelope=envelope,
        run_id=run_id,
        status="partial",
        summary="Browser grounded harness prepared a governed navigate payload.",
    )
    return (
        {
            "harness_authority": _harness_authority_artifact(authority, fallback_tool_name="browser.navigate"),
            "browser_navigate_payload": navigate_payload,
        },
        f"Prepared a governed browser navigate payload for {url}.",
        "prepared",
    )


def _authorize_harness_browser_navigate(
    *,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
):
    from spark_intelligence.bridge_authority import authorize_builder_bridge_action

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": envelope.turn_intent_payload},
        tool_name="browser.navigate",
        owner_system="spark-browser",
        mutation_class="external_network",
        external_network=True,
        state_db=state_db,
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        component="harness_runtime",
    )
    if not verdict.allowed:
        return verdict
    if not isinstance(verdict.governor_decision, dict):
        return replace(verdict, allowed=False, reason_codes=(*verdict.reason_codes, "missing_governor_decision"))
    return verdict


def _record_harness_browser_navigate_result_ledger(
    *,
    state_db: StateDB,
    verdict: Any,
    envelope: HarnessTaskEnvelope,
    run_id: str,
    status: str,
    summary: str,
) -> None:
    from spark_intelligence.bridge_authority import record_bridge_tool_call_result_ledger

    record_bridge_tool_call_result_ledger(
        state_db,
        verdict,
        status=status,
        summary=summary,
        component="harness_runtime",
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        initial_ledger_event_id=getattr(verdict, "ledger_event_id", None),
    )


def _execute_builder_direct_harness(
    *,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
) -> tuple[dict[str, Any], str, str]:
    authority = _authorize_harness_builder_direct(
        state_db=state_db,
        envelope=envelope,
        run_id=run_id,
    )
    if not authority.allowed:
        return (
            {
                "harness_authority": _harness_authority_artifact(authority, fallback_tool_name="builder.direct"),
            },
            "Builder direct harness is blocked because no executable Harness authority was present.",
            "blocked",
        )

    _record_harness_builder_direct_result_ledger(
        state_db=state_db,
        verdict=authority,
        envelope=envelope,
        run_id=run_id,
        status="success",
        summary="Builder direct harness prepared.",
    )
    return (
        {
            "harness_authority": _harness_authority_artifact(authority, fallback_tool_name="builder.direct"),
            "execution_contract": {
                "reply_mode": "builder_local_runtime",
                "owner_system": envelope.owner_system,
                "prompt_strategy": envelope.prompt_strategy,
                "required_capabilities": envelope.required_capabilities,
            },
        },
        "Task retained in Builder direct harness.",
        "prepared",
    )


def _authorize_harness_builder_direct(
    *,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
):
    from spark_intelligence.bridge_authority import authorize_builder_bridge_action

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": envelope.turn_intent_payload},
        tool_name="builder.direct",
        owner_system="spark-intelligence-builder",
        mutation_class="read_only",
        state_db=state_db,
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        component="harness_runtime",
    )
    if not verdict.allowed:
        return verdict
    if not isinstance(verdict.governor_decision, dict):
        return replace(verdict, allowed=False, reason_codes=(*verdict.reason_codes, "missing_governor_decision"))
    return verdict


def _record_harness_builder_direct_result_ledger(
    *,
    state_db: StateDB,
    verdict: Any,
    envelope: HarnessTaskEnvelope,
    run_id: str,
    status: str,
    summary: str,
) -> None:
    from spark_intelligence.bridge_authority import record_bridge_tool_call_result_ledger

    record_bridge_tool_call_result_ledger(
        state_db,
        verdict,
        status=status,
        summary=summary,
        component="harness_runtime",
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        initial_ledger_event_id=getattr(verdict, "ledger_event_id", None),
    )


def _execute_researcher_advisory_harness(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
) -> tuple[dict[str, Any], str, str]:
    authority = _authorize_harness_researcher_advisory(
        state_db=state_db,
        envelope=envelope,
        run_id=run_id,
    )
    if not authority.allowed:
        return (
            {
                "harness_authority": _harness_authority_artifact(authority, fallback_tool_name="researcher.advisory"),
            },
            "Researcher advisory harness is blocked because no executable Harness authority was present.",
            "blocked",
        )

    result = _run_researcher_bridge_reply(
        config_manager=config_manager,
        state_db=state_db,
        envelope=envelope,
    )
    artifacts = {
        "reply_text": _bridge_result_reply_text(result),
        "evidence_summary": result.evidence_summary,
        "trace_ref": result.trace_ref,
        "mode": result.mode,
        "provider_id": result.provider_id,
        "provider_model": result.provider_model,
        "provider_transport": result.provider_execution_transport,
        "routing_decision": result.routing_decision,
        "active_chip_key": result.active_chip_key,
        "harness_authority": _harness_authority_artifact(authority, fallback_tool_name="researcher.advisory"),
    }
    _record_harness_researcher_result_ledger(
        state_db=state_db,
        verdict=authority,
        envelope=envelope,
        run_id=run_id,
        status="success",
        summary="Researcher advisory harness completed.",
    )
    return (
        artifacts,
        "Executed the researcher advisory harness and captured the reply/result trace.",
        "completed",
    )


def _authorize_harness_researcher_advisory(
    *,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
):
    from spark_intelligence.bridge_authority import authorize_builder_bridge_action

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": envelope.turn_intent_payload},
        tool_name="researcher.advisory",
        owner_system="spark-researcher",
        mutation_class="external_network",
        external_network=True,
        state_db=state_db,
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        component="harness_runtime",
    )
    if not verdict.allowed:
        return verdict
    if not isinstance(verdict.governor_decision, dict):
        return replace(verdict, allowed=False, reason_codes=(*verdict.reason_codes, "missing_governor_decision"))
    return verdict


def _record_harness_researcher_result_ledger(
    *,
    state_db: StateDB,
    verdict: Any,
    envelope: HarnessTaskEnvelope,
    run_id: str,
    status: str,
    summary: str,
) -> None:
    from spark_intelligence.bridge_authority import record_bridge_tool_call_result_ledger

    record_bridge_tool_call_result_ledger(
        state_db,
        verdict,
        status=status,
        summary=summary,
        component="harness_runtime",
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        initial_ledger_event_id=getattr(verdict, "ledger_event_id", None),
    )


def _harness_authority_artifact(verdict: Any, *, fallback_tool_name: str = "researcher.advisory") -> dict[str, Any]:
    ledger = verdict.tool_call_ledger if isinstance(getattr(verdict, "tool_call_ledger", None), dict) else {}
    governor = verdict.governor_decision if isinstance(getattr(verdict, "governor_decision", None), dict) else {}
    return {
        "allowed": bool(getattr(verdict, "allowed", False)),
        "reason_codes": list(getattr(verdict, "reason_codes", ()) or ()),
        "decision_id": governor.get("decision_id"),
        "turn_id": ledger.get("turn_id") or governor.get("turn_id"),
        "ledger_id": ledger.get("ledger_id"),
        "tool_name": ledger.get("tool_name") or fallback_tool_name,
        "ledger_event_id": getattr(verdict, "ledger_event_id", None),
    }


def _bridge_result_reply_text(result: Any) -> str:
    value = getattr(result, "reply_text", "")
    return str(value or "")


def _run_researcher_bridge_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
):
    from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

    return build_researcher_reply(
        config_manager=config_manager,
        state_db=state_db,
        request_id=envelope.envelope_id,
        agent_id=envelope.agent_id or "agent:builder-local",
        human_id=envelope.human_id or "human:local-operator",
        session_id=envelope.session_id or f"session:{envelope.envelope_id}",
        channel_kind=envelope.channel_kind or "cli",
        user_message=envelope.task,
        turn_intent_payload=envelope.turn_intent_payload,
        allow_memory_adapter_envelope=False,
    )


def _execute_voice_io_harness(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
) -> tuple[dict[str, Any], str, str]:
    try:
        status_output, status_chip_key = _run_voice_hook(
            config_manager=config_manager,
            state_db=state_db,
            envelope=envelope,
            hook="voice.status",
            payload=_build_voice_hook_payload(config_manager=config_manager, state_db=state_db, envelope=envelope),
            run_id=run_id,
        )
    except Exception as exc:
        return (
            {
                "voice_status": {
                    "chip_key": None,
                    "ready": False,
                    "reason": str(exc),
                    "reply_text": "",
                },
                "resume_token": _build_harness_resume_token(
                    config_manager=config_manager,
                    envelope=envelope,
                    step="voice_status_repair",
                ),
            },
            "Voice I/O harness is blocked because no healthy voice status hook is available.",
            "blocked",
        )
    status_result = status_output.get("result") if isinstance(status_output.get("result"), dict) else {}
    ready = bool(status_result.get("ready"))
    artifacts: dict[str, Any] = {
        "voice_status": {
            "chip_key": status_chip_key,
            "ready": ready,
            "reason": str(status_result.get("reason") or ""),
            "reply_text": str(status_result.get("reply_text") or ""),
        },
    }
    task_mode, task_payload = _classify_voice_task(envelope.task)
    if task_mode == "speak":
        if not ready:
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_status_repair",
            )
            return (
                artifacts,
                "Voice I/O harness is not ready to synthesize speech yet.",
                "blocked",
            )
        try:
            speak_output, speak_chip_key = _run_voice_hook(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
                hook="voice.speak",
                payload=_build_voice_hook_payload(
                    config_manager=config_manager,
                    state_db=state_db,
                    envelope=envelope,
                    text=task_payload,
                ),
                run_id=run_id,
            )
        except Exception as exc:
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_speak_retry",
            )
            artifacts["retry_token"] = {
                "retry_command": f"python -m spark_intelligence.cli harness execute {json.dumps(envelope.task)} --home {config_manager.paths.home} --harness-id voice.io",
                "reason": str(exc),
            }
            return (
                artifacts,
                "Voice I/O harness could not synthesize speech with the current provider/hook state.",
                "blocked",
            )
        speak_result = speak_output.get("result") if isinstance(speak_output.get("result"), dict) else {}
        audio_base64 = str(speak_result.get("audio_base64") or "")
        audio_bytes = base64.b64decode(audio_base64.encode("ascii")) if audio_base64 else b""
        artifacts["spoken_audio"] = {
            "chip_key": speak_chip_key,
            "provider_id": str(speak_result.get("provider_id") or ""),
            "voice_id": str(speak_result.get("voice_id") or ""),
            "model_id": str(speak_result.get("model_id") or ""),
            "mime_type": str(speak_result.get("mime_type") or ""),
            "filename": str(speak_result.get("filename") or ""),
            "voice_compatible": bool(speak_result.get("voice_compatible")),
            "audio_bytes": len(audio_bytes),
            "audio_sha256": hashlib.sha256(audio_bytes).hexdigest() if audio_bytes else None,
        }
        artifacts["resume_token"] = _build_harness_resume_token(
            config_manager=config_manager,
            envelope=envelope,
            step="voice_repeat",
        )
        return (
            artifacts,
            "Executed the voice harness and synthesized spoken audio through the active voice chip.",
            "completed",
        )

    if task_mode == "transcribe":
        if not ready:
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_status_repair",
            )
            return (
                artifacts,
                "Voice I/O harness is not ready to transcribe audio yet.",
                "blocked",
            )
        if not task_payload:
            artifacts["needs_input"] = {
                "reason": "Voice transcription requires an explicit audio_base64 payload.",
                "mode": task_mode,
                "task": _redact_harness_task_payload(envelope.task),
            }
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_input_required",
            )
            return (
                artifacts,
                "Voice I/O harness is ready but needs explicit audio bytes before transcription can continue.",
                "needs_input",
            )
        try:
            audio_bytes = _decode_voice_audio_base64(task_payload)
        except ValueError as exc:
            artifacts["needs_input"] = {
                "reason": str(exc),
                "mode": task_mode,
                "task": _redact_harness_task_payload(envelope.task),
            }
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_input_required",
            )
            return (
                artifacts,
                "Voice I/O harness received invalid transcription audio input.",
                "needs_input",
            )
        try:
            transcribe_output, transcribe_chip_key = _run_voice_hook(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
                hook="voice.transcribe",
                payload=_build_voice_hook_payload(
                    config_manager=config_manager,
                    state_db=state_db,
                    envelope=envelope,
                    audio_base64=task_payload,
                    mime_type=_extract_voice_mime_type(envelope.task),
                    filename=_extract_voice_filename(envelope.task),
                ),
                run_id=run_id,
            )
        except Exception as exc:
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_transcribe_retry",
            )
            artifacts["retry_token"] = {
                "retry_command": (
                    f"python -m spark_intelligence.cli harness execute "
                    f"{json.dumps(_redact_harness_task_payload(envelope.task))} "
                    f"--home {config_manager.paths.home} --harness-id voice.io"
                ),
                "reason": str(exc),
            }
            return (
                artifacts,
                "Voice I/O harness could not transcribe audio with the current provider/hook state.",
                "blocked",
            )
        transcribe_result = transcribe_output.get("result") if isinstance(transcribe_output.get("result"), dict) else {}
        transcript_text = str(transcribe_result.get("transcript_text") or transcribe_result.get("text") or "").strip()
        if not transcript_text:
            artifacts["resume_token"] = _build_harness_resume_token(
                config_manager=config_manager,
                envelope=envelope,
                step="voice_transcribe_retry",
            )
            return (
                artifacts,
                "Voice I/O harness completed the transcription hook but received no transcript text.",
                "blocked",
            )
        artifacts["transcription"] = {
            "chip_key": transcribe_chip_key,
            "provider_id": str(transcribe_result.get("provider_id") or ""),
            "model": str(transcribe_result.get("model") or transcribe_result.get("model_id") or ""),
            "mode": str(transcribe_result.get("mode") or ""),
            "mime_type": _extract_voice_mime_type(envelope.task),
            "filename": _extract_voice_filename(envelope.task),
            "audio_bytes": len(audio_bytes),
            "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
            "transcript_text": transcript_text,
        }
        artifacts["resume_token"] = _build_harness_resume_token(
            config_manager=config_manager,
            envelope=envelope,
            step="voice_transcribe_repeat",
        )
        return (
            artifacts,
            "Executed the voice harness and transcribed audio through the active voice chip.",
            "completed",
        )

    artifacts["needs_input"] = {
        "reason": (
            "Voice I/O harness needs explicit speech text for synthesis or real audio bytes for transcription."
            if task_mode == "unspecified"
            else "Voice transcription requires audio bytes from a voice-capable surface before execution can continue."
        ),
        "mode": task_mode,
        "task": envelope.task,
    }
    artifacts["resume_token"] = _build_harness_resume_token(
        config_manager=config_manager,
        envelope=envelope,
        step="voice_input_required",
    )
    return (
        artifacts,
        "Voice I/O harness is ready but needs explicit speech text or audio input before execution can continue.",
        "needs_input",
    )


def _execute_swarm_escalation_harness(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
) -> tuple[dict[str, Any], str, str]:
    status = _load_swarm_status(config_manager=config_manager, state_db=state_db)
    artifacts: dict[str, Any] = {
        "swarm_status": {
            "enabled": bool(status.enabled),
            "configured": bool(status.configured),
            "researcher_ready": bool(status.researcher_ready),
            "payload_ready": bool(status.payload_ready),
            "api_ready": bool(status.api_ready),
            "auth_state": str(status.auth_state or ""),
            "workspace_id": status.workspace_id,
            "api_url": status.api_url,
            "last_decision": status.last_decision,
            "last_failure": status.last_failure,
        }
    }
    if not status.enabled:
        artifacts["resume_token"] = _build_harness_resume_token(
            config_manager=config_manager,
            envelope=envelope,
            step="swarm_enablement_required",
        )
        return (
            artifacts,
            "Swarm escalation is blocked because the Spark Swarm bridge is disabled.",
            "blocked",
        )
    if not status.researcher_ready or not status.payload_ready:
        artifacts["resume_token"] = _build_harness_resume_token(
            config_manager=config_manager,
            envelope=envelope,
            step="swarm_payload_repair",
        )
        artifacts["retry_token"] = {
            "retry_command": f"python -m spark_intelligence.cli swarm status --home {config_manager.paths.home}",
            "reason": "Inspect Swarm bridge readiness before retrying this harness.",
        }
        return (
            artifacts,
            "Swarm escalation needs the Researcher collective payload path repaired before the task can continue.",
            "needs_input",
        )
    authority = _authorize_harness_swarm_sync_dry_run(
        state_db=state_db,
        envelope=envelope,
        run_id=run_id,
    )
    if not authority.allowed:
        artifacts["harness_authority"] = _harness_authority_artifact(
            authority,
            fallback_tool_name="swarm.sync.dry_run",
        )
        return (
            artifacts,
            "Swarm escalation harness is blocked because no executable Harness authority was present.",
            "blocked",
        )
    sync_result = _run_swarm_sync_dry_run(
        config_manager=config_manager,
        state_db=state_db,
        envelope=envelope,
        run_id=run_id,
    )
    artifacts["swarm_sync_result"] = {
        "ok": bool(sync_result.ok),
        "mode": str(sync_result.mode or ""),
        "message": str(sync_result.message or ""),
        "payload_path": sync_result.payload_path,
        "api_url": sync_result.api_url,
        "workspace_id": sync_result.workspace_id,
        "accepted": sync_result.accepted,
        "response_body": sync_result.response_body,
    }
    artifacts["harness_authority"] = _harness_authority_artifact(
        authority,
        fallback_tool_name="swarm.sync.dry_run",
    )
    _record_harness_swarm_sync_result_ledger(
        state_db=state_db,
        verdict=authority,
        envelope=envelope,
        run_id=run_id,
        status="partial" if sync_result.ok else "failure",
        summary=(
            "Swarm escalation dry-run prepared a collective payload."
            if sync_result.ok
            else "Swarm escalation dry-run could not prepare a collective payload."
        ),
    )
    artifacts["resume_token"] = {
        "resume_kind": "swarm_dispatch",
        "resume_command": f"python -m spark_intelligence.cli swarm sync --home {config_manager.paths.home}",
        "reason": "Dispatch the prepared Spark Swarm collective payload when you want to move from dry-run to upload.",
    }
    artifacts["retry_token"] = {
        "retry_command": f"python -m spark_intelligence.cli swarm sync --dry-run --home {config_manager.paths.home}",
        "reason": "Rebuild the latest collective payload before retrying if the Swarm state changes.",
    }
    if sync_result.ok:
        return (
            artifacts,
            "Executed the Swarm harness by building the latest collective payload in dry-run mode.",
            "prepared",
        )
    return (
        artifacts,
        "Swarm escalation runner could not prepare a collective payload yet.",
        "needs_input",
    )


def _authorize_harness_swarm_sync_dry_run(
    *,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
):
    from spark_intelligence.bridge_authority import authorize_builder_bridge_action

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": envelope.turn_intent_payload},
        tool_name="swarm.sync.dry_run",
        owner_system="spark-swarm",
        mutation_class="writes_files",
        state_db=state_db,
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        component="harness_runtime",
    )
    if not verdict.allowed:
        return verdict
    if not isinstance(verdict.governor_decision, dict):
        return replace(verdict, allowed=False, reason_codes=(*verdict.reason_codes, "missing_governor_decision"))
    return verdict


def _record_harness_swarm_sync_result_ledger(
    *,
    state_db: StateDB,
    verdict: Any,
    envelope: HarnessTaskEnvelope,
    run_id: str,
    status: str,
    summary: str,
) -> None:
    from spark_intelligence.bridge_authority import record_bridge_tool_call_result_ledger

    record_bridge_tool_call_result_ledger(
        state_db,
        verdict,
        status=status,
        summary=summary,
        component="harness_runtime",
        request_id=envelope.envelope_id,
        run_id=run_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
        initial_ledger_event_id=getattr(verdict, "ledger_event_id", None),
    )


def _build_voice_hook_payload(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    text: str | None = None,
    audio_base64: str | None = None,
    mime_type: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "surface": envelope.channel_kind or "cli",
        "human_id": envelope.human_id,
        "agent_id": envelope.agent_id,
    }
    try:
        payload["provider"] = build_runtime_provider_reference_payload(config_manager=config_manager, state_db=state_db)
    except Exception as exc:
        payload["provider_error"] = str(exc)
    if isinstance(envelope.turn_intent_payload, dict):
        payload["turn_intent_envelope_vnext"] = envelope.turn_intent_payload
    if text is not None:
        payload["text"] = text
    if audio_base64 is not None:
        payload["audio_base64"] = audio_base64
    if mime_type:
        payload["mime_type"] = mime_type
    if filename:
        payload["filename"] = filename
    return payload


def _run_voice_hook(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    hook: str,
    payload: dict[str, Any],
    run_id: str,
) -> tuple[dict[str, Any], str]:
    authorization = _authorize_harness_voice_hook(envelope=envelope, hook=hook)
    authority = None
    if authorization is not None:
        from spark_intelligence.bridge_authority import BridgeAuthorityVerdict, record_bridge_tool_call_ledger

        authority = BridgeAuthorityVerdict(
            allowed=True,
            reason_codes=authorization.reason_codes,
            envelope=None,
            harness_core_envelope=authorization.turn_intent_envelope_vnext,
            proposed_action=authorization.proposed_action,
            authorization_decision=authorization.authorization_decision,
            tool_call_ledger=authorization.tool_call_ledger,
        )
        ledger_event_id = record_bridge_tool_call_ledger(
            state_db,
            authority,
            component="harness_runtime",
            request_id=envelope.envelope_id,
            run_id=run_id,
            channel_id=envelope.channel_kind,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
            actor_id="harness_runtime",
        )
        if ledger_event_id is not None:
            authority = replace(authority, ledger_event_id=ledger_event_id)

    if authority is not None and hook in {"voice.install", "voice.speak", "voice.transcribe"}:
        from spark_intelligence.bridge_authority import build_governor_decision_from_bridge_authority

        governor_decision = build_governor_decision_from_bridge_authority(
            authority,
            reply_instruction=f"Execute authorized Harness Runtime voice hook {hook}.",
        )
        if isinstance(governor_decision, dict):
            payload = dict(payload)
            payload["governor_decision"] = governor_decision
            if isinstance(authorization.turn_intent_envelope_vnext, dict):
                payload["turn_intent_envelope_vnext"] = authorization.turn_intent_envelope_vnext
    from spark_intelligence.attachments import record_chip_hook_execution, run_first_chip_hook_supporting

    execution = run_first_chip_hook_supporting(
        config_manager,
        hook=hook,
        payload=payload,
        governor_decision=payload.get("governor_decision") if isinstance(payload.get("governor_decision"), dict) else None,
    )
    if not execution:
        raise RuntimeError(f"No attached chip supports `{hook}`.")
    if not execution.ok:
        raise RuntimeError(str(execution.stderr or execution.stdout or f"{hook} failed.").strip())
    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="harness_runtime",
        actor_id="harness_runtime",
        summary=f"Harness runtime executed {hook} through chip {execution.chip_key}.",
        reason_code=f"harness_{hook.replace('.', '_')}",
        keepability="ephemeral_context",
        run_id=run_id,
        request_id=envelope.envelope_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
    )
    if authority is not None:
        from spark_intelligence.bridge_authority import record_bridge_tool_call_result_ledger

        record_bridge_tool_call_result_ledger(
            state_db,
            authority,
            status="success",
            summary=f"Voice hook {hook} completed through chip {execution.chip_key}.",
            component="harness_runtime",
            request_id=envelope.envelope_id,
            run_id=run_id,
            channel_id=envelope.channel_kind,
            session_id=envelope.session_id,
            human_id=envelope.human_id,
            agent_id=envelope.agent_id,
            actor_id="harness_runtime",
            initial_ledger_event_id=getattr(authority, "ledger_event_id", None),
        )
    output = execution.output if isinstance(execution.output, dict) else {}
    return output, execution.chip_key


def _authorize_harness_voice_hook(*, envelope: HarnessTaskEnvelope, hook: str) -> Any | None:
    from spark_intelligence.harness_contract import (
        authorize_tool_call,
        authorize_vnext_tool_call,
        parse_turn_intent_envelope,
    )

    payload = envelope.turn_intent_payload
    mutation_class = "external_network" if hook in {"voice.speak", "voice.transcribe"} else "read_only"
    external_network = hook in {"voice.speak", "voice.transcribe"}
    if isinstance(payload, dict) and payload.get("schema_version") == "turn-intent-envelope-vnext":
        authorization = authorize_vnext_tool_call(
            payload,
            tool_name=hook,
            owner_system="spark-voice-comms",
            mutation_class=mutation_class,
            external_network=external_network,
        )
        if authorization.verdict == "allowed":
            return authorization
        reason_text = ", ".join(authorization.reason_codes) if authorization.reason_codes else "turn_not_authorized"
        raise RuntimeError(f"Harness runtime missing Spark authority for `{hook}`: {reason_text}")

    if isinstance(payload, dict):
        try:
            parsed_envelope = parse_turn_intent_envelope(payload)
        except ValueError:
            parsed_envelope = None
    else:
        parsed_envelope = None
    verdict, reasons = authorize_tool_call(
        parsed_envelope,
        tool_name=hook,
        owner_system="spark-voice-comms",
        mutation_class=mutation_class,
        external_network=external_network,
    )
    if verdict != "allowed":
        reason_text = ", ".join(reasons) if reasons else "turn_not_authorized"
        raise RuntimeError(f"Harness runtime missing Spark authority for `{hook}`: {reason_text}")
    return None


def _load_swarm_status(*, config_manager: ConfigManager, state_db: StateDB):
    from spark_intelligence.swarm_bridge import swarm_status

    return swarm_status(config_manager, state_db)


def _run_swarm_sync_dry_run(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
    run_id: str,
):
    from spark_intelligence.swarm_bridge import sync_swarm_collective

    return sync_swarm_collective(
        config_manager=config_manager,
        state_db=state_db,
        dry_run=True,
        run_id=run_id,
        request_id=envelope.envelope_id,
        channel_id=envelope.channel_kind,
        session_id=envelope.session_id,
        human_id=envelope.human_id,
        agent_id=envelope.agent_id,
        actor_id="harness_runtime",
    )


def _classify_voice_task(task: str) -> tuple[str, str | None]:
    stripped = str(task or "").strip()
    lowered = stripped.lower()
    match = _VOICE_SPEAK_RE.match(stripped)
    if match:
        spoken_text = str(match.group("text") or "").strip()
        return ("speak", spoken_text or None)
    if "transcribe" in lowered or "transcription" in lowered:
        return ("transcribe", _extract_voice_audio_base64(stripped))
    return ("unspecified", None)


def _extract_voice_audio_base64(task: str) -> str | None:
    match = _VOICE_AUDIO_BASE64_RE.search(str(task or ""))
    if not match:
        return None
    return str(match.group("audio") or "").strip() or None


def _extract_voice_mime_type(task: str) -> str | None:
    match = _VOICE_MIME_TYPE_RE.search(str(task or ""))
    if not match:
        return None
    return str(match.group("mime") or "").strip() or None


def _extract_voice_filename(task: str) -> str | None:
    match = _VOICE_FILENAME_RE.search(str(task or ""))
    if not match:
        return None
    return str(match.group("filename") or "").strip() or None


def _decode_voice_audio_base64(audio_base64: str) -> bytes:
    try:
        audio_bytes = base64.b64decode(str(audio_base64).encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("Voice transcription audio_base64 is not valid base64.") from exc
    if not audio_bytes:
        raise ValueError("Voice transcription audio_base64 is empty.")
    return audio_bytes


def _redact_harness_task_payload(task: str) -> str:
    return _VOICE_AUDIO_BASE64_RE.sub(
        lambda match: f"{match.group('prefix')}<audio_base64:redacted>",
        str(task or "").strip(),
    )


def _build_harness_resume_token(
    *,
    config_manager: ConfigManager,
    envelope: HarnessTaskEnvelope,
    step: str,
) -> dict[str, Any]:
    command = (
        f"python -m spark_intelligence.cli harness execute {json.dumps(_redact_harness_task_payload(envelope.task))} "
        f"--home {config_manager.paths.home} --harness-id {envelope.harness_id}"
    )
    if envelope.channel_kind:
        command += f" --channel-kind {envelope.channel_kind}"
    return {
        "resume_kind": step,
        "resume_command": command,
        "harness_id": envelope.harness_id,
        "envelope_id": envelope.envelope_id,
    }


def _derive_follow_up_task(
    *,
    current_result: HarnessExecutionResult,
    target_harness_id: str,
) -> str:
    normalized_target = str(target_harness_id or "").strip()
    if normalized_target == "voice.io":
        reply_text = _extract_reply_text_from_result(current_result)
        if reply_text:
            return f"Say: {reply_text}"
        return f"Say: {current_result.summary}"
    if normalized_target == "swarm.escalation":
        reply_text = _extract_reply_text_from_result(current_result)
        base = [
            "Coordinate this through Swarm.",
            f"Original harness: {current_result.envelope.harness_id}",
            f"Original task: {current_result.envelope.task}",
        ]
        if reply_text:
            base.append(f"Upstream reply: {reply_text}")
        else:
            base.append(f"Upstream summary: {current_result.summary}")
        return "\n".join(base)
    if normalized_target == "researcher.advisory":
        reply_text = _extract_reply_text_from_result(current_result)
        return reply_text or current_result.envelope.task
    if normalized_target == "builder.direct":
        reply_text = _extract_reply_text_from_result(current_result)
        return reply_text or current_result.summary
    return current_result.envelope.task


def _extract_reply_text_from_result(result: HarnessExecutionResult) -> str | None:
    reply_text = result.artifacts.get("reply_text")
    if isinstance(reply_text, str) and reply_text.strip():
        return reply_text.strip()
    spoken_audio = result.artifacts.get("spoken_audio")
    if isinstance(spoken_audio, dict):
        transcript_like = spoken_audio.get("text")
        if isinstance(transcript_like, str) and transcript_like.strip():
            return transcript_like.strip()
    return None


def _extract_first_url(text: str) -> str | None:
    match = _URL_RE.search(str(text or ""))
    return match.group(0) if match else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
