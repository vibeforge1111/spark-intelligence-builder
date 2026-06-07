from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import os
import shlex
from subprocess import TimeoutExpired
import time
from typing import Any

from spark_intelligence.execution.governed import run_governed_command
from spark_intelligence.harness_contract import _ensure_harness_core_importable
from spark_intelligence.observability.store import recent_tool_call_ledgers, record_event
from spark_intelligence.state.db import StateDB


_EVOLUTION_MODES = {"observe", "propose", "sandbox", "live_qa", "promote", "rollback"}
_PROMOTION_VERDICTS = {"promote_private", "promote_release_candidate", "rollback"}
_SHELL_META_CHARS = set("&|;<>()`$")
_MAX_COMMAND_OUTPUT_CHARS = 4000
_CHANGE_MANIFEST_RUNNER_TOOL = "harness.change_manifest_runner"
_CHANGE_MANIFEST_RUNNER_OWNER = "spark-intelligence-builder"
_CHANGE_MANIFEST_RUNNER_MUTATION = "writes_files"


def build_harness_self_evolution_snapshot(
    state_db: StateDB,
    *,
    limit: int = 20,
    persist_event: bool = True,
) -> dict[str, Any]:
    """Build an observe-only Harness Core self-evolution run from canonical ledger evidence."""

    _ensure_harness_core_importable()
    from spark_harness_core import HarnessKernel, artifact_ref, evidence_ref

    bounded_limit = max(1, min(int(limit), 100))
    ledgers = recent_tool_call_ledgers(state_db, limit=bounded_limit)
    kernel = HarnessKernel(surface="builder", actor_id_ref="system:spark-intelligence-builder")
    status_counts = Counter(str(row.get("status") or "unknown") for row in ledgers)
    surface_counts = Counter(str(row.get("surface") or "unknown") for row in ledgers)
    evidence = [
        evidence_ref(
            "runtime_state",
            "state.db:tool_call_ledger",
            f"Canonical tool-call ledger rows available: {len(ledgers)}.",
            confidence=1.0 if ledgers else 0.2,
        )
    ]
    entries = [
        kernel.experience_entry(
            entry_type="tool_ledger",
            summary=_ledger_summary(row),
            artifact=artifact_ref(
                "tool_ledger",
                f"state.db:tool_call_ledger/{row.get('ledger_id') or 'unknown'}",
                "Canonical bound tool-call ledger row.",
            ),
            tags=[_tag("surface", row.get("surface")), _tag("status", row.get("status"))],
        )
        for row in ledgers[:bounded_limit]
    ]
    experience_index = kernel.experience_index(entries=entries)
    category_scores = _category_scores(len(ledgers))
    readiness_score = kernel.readiness_score(
        target_kind="capability",
        target_id="capability:spark:harness:self-evolution",
        owner_repo="spark-intelligence-builder",
        category_scores=category_scores,
        category_evidence={name: evidence for name in category_scores},
        category_blockers=_category_blockers(len(ledgers)),
        promotion_gates={
            "telegram_live_proven": bool(ledgers),
            "startup_benchmark_proven": False,
            "performance_budget_proven": False,
            "governance_rulesets_proven": False,
            "zero_high_agency_legacy_local_gates": False,
        },
        summary=(
            "Observe-only self-evolution snapshot from canonical tool ledgers; "
            "not ready to promote changes."
        ),
    )
    evolution_run = kernel.self_evolution_run(
        mode="observe",
        experience_index=experience_index,
        readiness_score=readiness_score,
        commands=["observe-only: no commands executed"],
        summary="Canonical tool-ledger evidence was harvested for self-evolution observation.",
    )
    event_id = None
    if persist_event:
        event_id = record_event(
            state_db,
            event_type="harness_self_evolution_observed",
            component="harness",
            summary="Harness self-evolution observe snapshot recorded from canonical tool ledgers.",
            correlation_id=evolution_run["evolution_id"],
            reason_code="observe_only_self_evolution",
            facts={
                "source_kind": "spark_harness_core_self_evolution_run",
                "ledger_count": len(ledgers),
                "status_counts": dict(status_counts),
                "surface_counts": dict(surface_counts),
                "readiness_status": readiness_score["overall"]["status"],
                "readiness_score": readiness_score["overall"]["score"],
                "self_evolution_run": evolution_run,
            },
        )
    return {
        "ok": True,
        "mode": "observe",
        "event_id": event_id,
        "ledger_count": len(ledgers),
        "status_counts": dict(status_counts),
        "surface_counts": dict(surface_counts),
        "experience_index": experience_index,
        "readiness_score": readiness_score,
        "self_evolution_run": evolution_run,
    }


def run_harness_change_manifest_runner(
    state_db: StateDB,
    *,
    manifest_paths: list[str],
    mode: str = "promote",
    requested_verdict: str | None = None,
    commands: list[str] | None = None,
    run_tests: bool = False,
    allow_private_promotion: bool = False,
    cwd: str | None = None,
    timeout_seconds: int = 120,
    limit: int = 20,
    live_surface_required: bool = False,
    persist_event: bool = True,
) -> dict[str, Any]:
    """Invoke Harness Core's gated change-manifest runner from Builder evidence."""

    _ensure_harness_core_importable()
    from spark_harness_core import HarnessKernel, artifact_ref, evidence_ref, validate_instance

    normalized_mode = str(mode or "promote").strip()
    if normalized_mode not in _EVOLUTION_MODES:
        raise ValueError(f"Unsupported self-evolution mode: {normalized_mode}")

    normalized_requested = str(requested_verdict or "").strip() or None
    if normalized_requested is not None and normalized_requested not in _PROMOTION_VERDICTS:
        raise ValueError(f"Unsupported requested verdict: {normalized_requested}")

    manifests = _load_change_manifests(manifest_paths)
    for manifest in manifests:
        validate_instance("change-manifest-v1", manifest)

    bounded_limit = max(1, min(int(limit), 100))
    ledgers = recent_tool_call_ledgers(state_db, limit=bounded_limit)
    status_counts = Counter(str(row.get("status") or "unknown") for row in ledgers)
    surface_counts = Counter(str(row.get("surface") or "unknown") for row in ledgers)
    selected_commands = _select_runner_commands(commands=commands, manifests=manifests, run_tests=run_tests)
    runner_request_id = _runner_request_id(manifests)
    runner_authority = (
        _authorize_change_manifest_runner(
            state_db,
            manifests=manifests,
            mode=normalized_mode,
            requested_verdict=normalized_requested,
            request_id=runner_request_id,
        )
        if persist_event
        else None
    )
    test_results = (
        _run_test_commands(selected_commands, cwd=cwd, timeout_seconds=timeout_seconds)
        if run_tests
        else []
    )
    all_tests_passed = bool(test_results) and all(item["status"] == "passed" for item in test_results)
    kernel = HarnessKernel(surface="builder", actor_id_ref="system:spark-intelligence-builder")
    evidence = _runner_evidence_refs(
        evidence_ref=evidence_ref,
        ledgers=ledgers,
        manifests=manifests,
        test_results=test_results,
        run_tests=run_tests,
    )
    experience_index = kernel.experience_index(
        entries=[
            *[
                kernel.experience_entry(
                    entry_type="tool_ledger",
                    summary=_ledger_summary(row),
                    artifact=artifact_ref(
                        "tool_ledger",
                        f"state.db:tool_call_ledger/{row.get('ledger_id') or 'unknown'}",
                        "Canonical bound tool-call ledger row.",
                    ),
                    tags=[_tag("surface", row.get("surface")), _tag("status", row.get("status"))],
                )
                for row in ledgers[:bounded_limit]
            ],
            *[
                kernel.experience_entry(
                    entry_type="route_decision",
                    summary=f"Change manifest {manifest.get('change_id') or 'unknown'} is {manifest.get('verdict') or 'unknown'}.",
                    artifact=artifact_ref(
                        "change_manifest",
                        f"harness:change_manifest/{manifest.get('change_id') or 'unknown'}",
                        "Validated Harness Core change manifest supplied to Builder runner.",
                    ),
                    tags=[_tag("verdict", manifest.get("verdict"))],
                )
                for manifest in manifests
            ],
            *[
                kernel.experience_entry(
                    entry_type="test_result",
                    summary=f"Self-evolution test command {result['status']}: {result['command']}",
                    artifact=artifact_ref(
                        "test_result",
                        f"harness:test_command/{index + 1}",
                        "Builder-executed self-evolution test command result.",
                    ),
                    tags=[_tag("status", result["status"])],
                )
                for index, result in enumerate(test_results)
            ],
        ]
    )
    readiness_score = kernel.readiness_score(
        target_kind="capability",
        target_id="capability:spark:harness:self-evolution",
        owner_repo="spark-intelligence-builder",
        category_scores=_runner_category_scores(
            ledger_count=len(ledgers),
            manifest_count=len(manifests),
            run_tests=run_tests,
            all_tests_passed=all_tests_passed,
        ),
        category_evidence={name: evidence for name in _readiness_category_names()},
        category_blockers=_runner_category_blockers(
            ledger_count=len(ledgers),
            manifests=manifests,
            run_tests=run_tests,
            test_results=test_results,
            allow_private_promotion=allow_private_promotion,
            live_surface_required=live_surface_required,
        ),
        promotion_gates={
            "telegram_live_proven": bool(ledgers),
            "startup_benchmark_proven": False,
            "performance_budget_proven": all_tests_passed,
            "governance_rulesets_proven": bool(manifests) and _all_manifests_accepted(manifests),
            "zero_high_agency_legacy_local_gates": (
                allow_private_promotion
                and bool(manifests)
                and _all_manifests_accepted(manifests)
                and all_tests_passed
                and not live_surface_required
            ),
        },
        summary="Builder invoked Harness Core change-manifest runner from canonical ledger and test evidence.",
    )
    evolution_run = kernel.change_manifest_runner(
        mode=normalized_mode,
        experience_index=experience_index,
        readiness_score=readiness_score,
        commands=selected_commands,
        change_manifests=manifests,
        requested_verdict=normalized_requested,
        live_surface_required=live_surface_required,
        roles={
            "harness_scientist": "spark-intelligence-builder",
            "surface_operator": "builder-cli",
            "verifier": "spark-harness-core",
        },
    )
    event_id = None
    result_event_id = None
    if persist_event:
        event_id = record_event(
            state_db,
            event_type="harness_change_manifest_runner_evaluated",
            component="harness",
            summary="Harness change-manifest runner evaluated self-evolution manifests.",
            correlation_id=evolution_run["evolution_id"],
            reason_code="change_manifest_runner_evaluated",
            facts={
                "source_kind": "spark_harness_core_change_manifest_runner",
                "mode": normalized_mode,
                "requested_verdict": normalized_requested,
                "promotion_verdict": evolution_run["promotion_decision"]["verdict"],
                "manifest_count": len(manifests),
                "change_ids": [manifest.get("change_id") for manifest in manifests],
                "run_tests": run_tests,
                "test_statuses": [result["status"] for result in test_results],
                "readiness_status": readiness_score["overall"]["status"],
                "readiness_score": readiness_score["overall"]["score"],
                "builder_ledger_id": _authority_ledger_id(runner_authority),
                "builder_ledger_event_id": getattr(runner_authority, "ledger_event_id", None),
                "self_evolution_run": evolution_run,
            },
        )
        result_event_id = _record_change_manifest_runner_result_ledger(
            state_db,
            authority=runner_authority,
            event_id=event_id,
            request_id=runner_request_id,
            promotion_verdict=str(evolution_run["promotion_decision"]["verdict"]),
            all_tests_passed=all_tests_passed,
            run_tests=run_tests,
        )
    return {
        "ok": True,
        "mode": normalized_mode,
        "requested_verdict": normalized_requested,
        "event_id": event_id,
        "builder_ledger_id": _authority_ledger_id(runner_authority),
        "builder_ledger_event_id": getattr(runner_authority, "ledger_event_id", None),
        "builder_result_event_id": result_event_id,
        "manifest_count": len(manifests),
        "ledger_count": len(ledgers),
        "status_counts": dict(status_counts),
        "surface_counts": dict(surface_counts),
        "commands": selected_commands,
        "run_tests": run_tests,
        "test_results": test_results,
        "all_tests_passed": all_tests_passed,
        "readiness_score": readiness_score,
        "experience_index": experience_index,
        "self_evolution_run": evolution_run,
    }


def _category_scores(ledger_count: int) -> dict[str, float]:
    if ledger_count <= 0:
        return {
            "execution": 0.0,
            "tools": 0.0,
            "context": 0.0,
            "lifecycle": 0.0,
            "observability": 0.0,
            "verification": 0.0,
            "governance": 0.0,
        }
    return {
        "execution": 0.4,
        "tools": 0.5,
        "context": 0.3,
        "lifecycle": 0.45,
        "observability": 0.65,
        "verification": 0.25,
        "governance": 0.35,
    }


def _category_blockers(ledger_count: int) -> dict[str, list[str]]:
    common = [
        "self_evolution_observe_only",
        "no_change_manifest_execution",
        "promotion_gates_not_proven",
    ]
    if ledger_count <= 0:
        common.append("missing_tool_ledger_experience")
    return {
        "execution": list(common),
        "tools": [] if ledger_count else ["missing_tool_ledger_experience"],
        "context": ["turn_context_not_fully_threaded"],
        "lifecycle": ["observe_only_no_scheduler"],
        "observability": [] if ledger_count else ["missing_tool_ledger_experience"],
        "verification": ["no_evaluation_pack_run"],
        "governance": ["out_of_process_authority_not_required"],
    }


def _load_change_manifests(paths: list[str]) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            manifests.extend(_expect_manifest(item, path=path) for item in payload)
        elif isinstance(payload, dict) and isinstance(payload.get("change_manifests"), list):
            manifests.extend(_expect_manifest(item, path=path) for item in payload["change_manifests"])
        else:
            manifests.append(_expect_manifest(payload, path=path))
    if not manifests:
        raise ValueError("At least one change manifest is required.")
    return manifests


def _expect_manifest(payload: Any, *, path: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"Change manifest file {path} must contain an object or array of objects.")
    if payload.get("schema_version") != "change-manifest-v1":
        raise ValueError(f"Change manifest file {path} contains unsupported schema_version.")
    return payload


def _select_runner_commands(
    *,
    commands: list[str] | None,
    manifests: list[dict[str, Any]],
    run_tests: bool,
) -> list[str]:
    selected = [str(item).strip() for item in (commands or []) if str(item).strip()]
    if selected:
        return selected
    required = [
        str(command).strip()
        for manifest in manifests
        for command in (manifest.get("required_tests") if isinstance(manifest.get("required_tests"), list) else [])
        if str(command).strip()
    ]
    if run_tests and required:
        return list(dict.fromkeys(required))
    return required or ["observe-only: no commands executed"]


def _authorize_change_manifest_runner(
    state_db: StateDB,
    *,
    manifests: list[dict[str, Any]],
    mode: str,
    requested_verdict: str | None,
    request_id: str,
) -> Any:
    from spark_intelligence.bridge_authority import authorize_builder_bridge_action
    from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

    payload = build_vnext_tool_intent_envelope(
        surface="builder",
        actor_id_ref="human:local-operator",
        request_id=request_id,
        source_kind="builder_cli_harness_change_manifest_runner",
        tool_name=_CHANGE_MANIFEST_RUNNER_TOOL,
        owner_system=_CHANGE_MANIFEST_RUNNER_OWNER,
        mutation_class=_CHANGE_MANIFEST_RUNNER_MUTATION,
        intent_summary=(
            "Local operator invoked the supervised Builder change-manifest runner."
        ),
        raw_turn_summary=(
            "Builder CLI change-manifest runner invocation; raw operator shell "
            f"context remains offloaded. mode={mode}; requested_verdict={requested_verdict or 'auto'}; "
            f"manifest_count={len(manifests)}."
        ),
        confidence=0.99,
        requires_confirmation=False,
        args_path=f"builder://harness/change-manifest-runner/{request_id}",
    )
    if not isinstance(payload, dict):
        raise ValueError("Builder change-manifest runner could not build Harness authority payload.")
    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload},
        tool_name=_CHANGE_MANIFEST_RUNNER_TOOL,
        owner_system=_CHANGE_MANIFEST_RUNNER_OWNER,
        mutation_class=_CHANGE_MANIFEST_RUNNER_MUTATION,
        state_db=state_db,
        request_id=request_id,
        channel_id="builder",
        actor_id="builder_cli",
        component="harness_evolution",
    )
    if not getattr(verdict, "allowed", False):
        reasons = ", ".join(str(item) for item in getattr(verdict, "reason_codes", ()) or ())
        raise ValueError(f"Builder change-manifest runner authority denied: {reasons or 'unknown'}")
    return verdict


def _record_change_manifest_runner_result_ledger(
    state_db: StateDB,
    *,
    authority: Any,
    event_id: str | None,
    request_id: str,
    promotion_verdict: str,
    all_tests_passed: bool,
    run_tests: bool,
) -> str | None:
    if authority is None:
        return None
    from spark_intelligence.bridge_authority import record_bridge_tool_call_result_ledger

    status = "success" if (not run_tests or all_tests_passed) else "partial"
    return record_bridge_tool_call_result_ledger(
        state_db,
        authority,
        status=status,
        summary=f"Builder change-manifest runner completed with {promotion_verdict}.",
        output_path=f"builder://harness/change-manifest-runner/events/{event_id or 'unrecorded'}",
        component="harness_evolution",
        request_id=request_id,
        channel_id="builder",
        actor_id="builder_cli",
        initial_ledger_event_id=getattr(authority, "ledger_event_id", None),
    )


def _runner_request_id(manifests: list[dict[str, Any]]) -> str:
    change_ids = [str(manifest.get("change_id") or "").strip() for manifest in manifests]
    first_change_id = next((change_id for change_id in change_ids if change_id), "change:unknown")
    return f"builder:harness-change-manifest-runner:{first_change_id}"


def _authority_ledger_id(authority: Any) -> str | None:
    ledger = getattr(authority, "tool_call_ledger", None)
    if not isinstance(ledger, dict):
        return None
    return str(ledger.get("ledger_id") or "") or None


def _run_test_commands(commands: list[str], *, cwd: str | None, timeout_seconds: int) -> list[dict[str, Any]]:
    root = Path(cwd).expanduser() if cwd else None
    return [_run_single_test_command(command, cwd=root, timeout_seconds=timeout_seconds) for command in commands]


def _run_single_test_command(command: str, *, cwd: Path | None, timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        argv = _safe_test_argv(command)
    except ValueError as exc:
        return {
            "command": command,
            "argv": [],
            "status": "blocked",
            "exit_code": None,
            "duration_ms": 0,
            "reason": str(exc),
            "stdout_tail": "",
            "stderr_tail": "",
        }
    try:
        completed = run_governed_command(
            command=argv,
            cwd=cwd or Path.cwd(),
            timeout_seconds=max(1, int(timeout_seconds)),
        )
    except TimeoutExpired as exc:
        return {
            "command": command,
            "argv": argv,
            "status": "failed",
            "exit_code": None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "reason": f"timeout after {timeout_seconds}s",
            "stdout_tail": _tail(exc.stdout or ""),
            "stderr_tail": _tail(exc.stderr or ""),
        }
    return {
        "command": command,
        "argv": argv,
        "status": "passed" if completed.exit_code == 0 else "failed",
        "exit_code": completed.exit_code,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "reason": "",
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def _safe_test_argv(command: str) -> list[str]:
    stripped = str(command or "").strip()
    if not stripped:
        raise ValueError("empty test command")
    if any(char in stripped for char in _SHELL_META_CHARS) or "\n" in stripped or "\r" in stripped:
        raise ValueError("test command contains shell control syntax")
    argv = shlex.split(stripped, posix=(os.name != "nt"))
    if not argv:
        raise ValueError("empty test command")
    if not _is_allowed_test_command(argv):
        raise ValueError("test command is not in the self-evolution allowlist")
    return argv


def _is_allowed_test_command(argv: list[str]) -> bool:
    head = [_normalize_argv_token(item) for item in argv[:3]]
    if len(head) >= 3 and head[0] in {"python", "py"} and head[1] == "-m" and head[2] in {"pytest", "unittest"}:
        return True
    if head[0] == "pytest":
        return True
    if len(head) >= 2 and head[0] in {"npm", "pnpm", "yarn"} and head[1] == "test":
        return True
    if len(head) >= 3 and head[0] in {"npm", "pnpm", "yarn"} and head[1] == "run" and head[2] == "test":
        return True
    return len(head) >= 2 and head[0] == "node" and head[1] == "--test"


def _normalize_argv_token(value: str) -> str:
    name = Path(str(value)).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _tail(value: str, *, limit: int = _MAX_COMMAND_OUTPUT_CHARS) -> str:
    text = str(value or "")
    return text[-limit:] if len(text) > limit else text


def _runner_evidence_refs(
    *,
    evidence_ref: Any,
    ledgers: list[dict[str, Any]],
    manifests: list[dict[str, Any]],
    test_results: list[dict[str, Any]],
    run_tests: bool,
) -> list[dict[str, Any]]:
    evidence = [
        evidence_ref(
            "runtime_state",
            "state.db:tool_call_ledger",
            f"Canonical tool-call ledger rows available: {len(ledgers)}.",
            confidence=1.0 if ledgers else 0.2,
        ),
        evidence_ref(
            "policy",
            "builder:harness-change-manifest-runner",
            f"Validated change manifests supplied: {len(manifests)}.",
            confidence=1.0 if manifests else 0.0,
        ),
    ]
    if run_tests:
        passed = len([item for item in test_results if item.get("status") == "passed"])
        evidence.append(
            evidence_ref(
                "test_result",
                "builder:harness-change-manifest-runner/tests",
                f"Executed allowlisted test commands: {passed}/{len(test_results)} passed.",
                confidence=1.0 if test_results and passed == len(test_results) else 0.3,
            )
        )
    return evidence


def _runner_category_scores(
    *,
    ledger_count: int,
    manifest_count: int,
    run_tests: bool,
    all_tests_passed: bool,
) -> dict[str, float]:
    if ledger_count <= 0 or manifest_count <= 0:
        return _category_scores(ledger_count)
    verification = 0.85 if run_tests and all_tests_passed else 0.25
    governance = 0.75 if run_tests and all_tests_passed else 0.35
    return {
        "execution": 0.75,
        "tools": 0.7,
        "context": 0.72,
        "lifecycle": 0.75,
        "observability": 0.72,
        "verification": verification,
        "governance": governance,
    }


def _runner_category_blockers(
    *,
    ledger_count: int,
    manifests: list[dict[str, Any]],
    run_tests: bool,
    test_results: list[dict[str, Any]],
    allow_private_promotion: bool,
    live_surface_required: bool,
) -> dict[str, list[str]]:
    blockers = {name: [] for name in _readiness_category_names()}
    if ledger_count <= 0:
        blockers["execution"].append("missing_tool_ledger_experience")
        blockers["tools"].append("missing_tool_ledger_experience")
        blockers["observability"].append("missing_tool_ledger_experience")
    if not manifests:
        blockers["lifecycle"].append("missing_change_manifest")
    if manifests and not _all_manifests_accepted(manifests):
        blockers["lifecycle"].append("non_accepted_change_manifests")
    if not run_tests:
        blockers["verification"].append("required_tests_not_executed")
    elif not test_results:
        blockers["verification"].append("missing_required_test_commands")
    elif any(item.get("status") != "passed" for item in test_results):
        blockers["verification"].append("required_tests_failed_or_blocked")
    if not allow_private_promotion:
        blockers["governance"].append("private_promotion_not_explicitly_allowed")
    if live_surface_required or any(bool(manifest.get("live_proof_required")) for manifest in manifests):
        blockers["governance"].append("live_proof_still_required")
    return blockers


def _all_manifests_accepted(manifests: list[dict[str, Any]]) -> bool:
    return bool(manifests) and all(manifest.get("verdict") == "accepted" for manifest in manifests)


def _readiness_category_names() -> list[str]:
    return ["execution", "tools", "context", "lifecycle", "observability", "verification", "governance"]


def _ledger_summary(row: dict[str, Any]) -> str:
    tool = str(row.get("tool_name") or "unknown tool")
    status = str(row.get("status") or "unknown")
    surface = str(row.get("surface") or "unknown")
    return f"{surface} ledger {row.get('ledger_id') or 'unknown'} recorded {tool} as {status}."


def _tag(prefix: str, value: Any) -> str:
    text = str(value or "unknown").strip().replace(" ", "_")
    return f"{prefix}:{text or 'unknown'}"
