from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.observability.store import close_run, open_run, record_event
from spark_intelligence.state.db import StateDB


_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_VOICE_SPEAK_RE = re.compile(
    r"^(?:say|speak|voice|read(?:\s+this)?|send(?:\s+this)?\s+as\s+voice|reply(?:\s+with)?\s+voice)[:\s-]+(?P<text>.+)$",
    re.IGNORECASE | re.DOTALL,
)


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

    def to_payload(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "task": self.task,
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
        }


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
    )


def execute_harness_task(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
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
            summary = "Task retained in Builder direct harness."
            artifacts = {
                "execution_contract": {
                    "reply_mode": "builder_local_runtime",
                    "owner_system": envelope.owner_system,
                    "prompt_strategy": envelope.prompt_strategy,
                    "required_capabilities": envelope.required_capabilities,
                }
            }
            status = "prepared"
        elif envelope.harness_id == "researcher.advisory":
            artifacts, summary, status = _execute_researcher_advisory_harness(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
            )
        elif envelope.harness_id == "browser.grounded":
            artifacts, summary, status = _execute_browser_grounded_harness(
                config_manager=config_manager,
                envelope=envelope,
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
        current_result = execute_harness_task(
            config_manager=config_manager,
            state_db=state_db,
            envelope=next_envelope,
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
    envelope: HarnessTaskEnvelope,
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
    return (
        {
            "browser_navigate_payload": build_browser_navigate_payload(
                config_manager=config_manager,
                url=url,
            )
        },
        f"Prepared a governed browser navigate payload for {url}.",
        "prepared",
    )


def _execute_researcher_advisory_harness(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    envelope: HarnessTaskEnvelope,
) -> tuple[dict[str, Any], str, str]:
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
    }
    return (
        artifacts,
        "Executed the researcher advisory harness and captured the reply/result trace.",
        "completed",
    )


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
            payload=_build_voice_hook_payload(config_manager=config_manager, envelope=envelope),
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


def _build_voice_hook_payload(
    *,
    config_manager: ConfigManager,
    envelope: HarnessTaskEnvelope,
    text: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "surface": envelope.channel_kind or "cli",
        "builder_env_file_path": str(config_manager.paths.env_file.resolve()),
        "human_id": envelope.human_id,
        "agent_id": envelope.agent_id,
    }
    if text is not None:
        payload["text"] = text
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
    from spark_intelligence.attachments import record_chip_hook_execution, run_first_chip_hook_supporting

    execution = run_first_chip_hook_supporting(config_manager, hook=hook, payload=payload)
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
    output = execution.output if isinstance(execution.output, dict) else {}
    return output, execution.chip_key


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
        return ("transcribe", None)
    return ("unspecified", None)


def _build_harness_resume_token(
    *,
    config_manager: ConfigManager,
    envelope: HarnessTaskEnvelope,
    step: str,
) -> dict[str, Any]:
    command = (
        f"python -m spark_intelligence.cli harness execute {json.dumps(envelope.task)} "
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
