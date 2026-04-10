from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.attachments.registry import list_attachments
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.execution.governed import GovernedCommandExecution, run_governed_command


@dataclass(frozen=True)
class SwarmBridgeCommandResult:
    ok: bool
    action: str
    command: list[str]
    cwd: str
    runtime_root: str
    repo_root: str | None
    path_key: str | None
    exit_code: int
    stdout: str
    stderr: str
    session_id: str | None = None
    session_summary_path: str | None = None
    session_summary: dict[str, Any] | None = None
    latest_round_summary_path: str | None = None
    latest_round_summary: dict[str, Any] | None = None
    round_history_path: str | None = None
    round_history: dict[str, Any] | None = None
    artifacts_path: str | None = None
    payload_path: str | None = None


def swarm_bridge_list_paths(config_manager: ConfigManager) -> dict[str, Any]:
    runtime_root = _resolve_swarm_runtime_root(config_manager)
    attachments = list_attachments(config_manager, kind="path")
    active_path_key = str(config_manager.get_path("spark.specialization_paths.active_path_key", default="") or "").strip()
    paths: list[dict[str, str]] = []
    for record in attachments.records:
        paths.append(
            {
                "key": record.key,
                "label": _normalize_path_label(record.label, fallback=record.key),
                "repo_root": record.repo_root,
                "active": "yes" if record.key == active_path_key else "no",
            }
        )
    return {
        "runtime_root": str(runtime_root),
        "active_path_key": active_path_key or None,
        "paths": paths,
    }


def swarm_bridge_run_specialization_path(
    config_manager: ConfigManager,
    *,
    path_key: str,
) -> SwarmBridgeCommandResult:
    repo_root = _resolve_specialization_path_repo_root(config_manager, path_key)
    return _run_swarm_bridge_command(
        config_manager,
        action="run",
        repo_root=repo_root,
        path_key=path_key,
        command=[
            sys.executable,
            "-m",
            "spark_swarm_bridge.cli",
            "specialization-path",
            "run",
            path_key,
            str(repo_root),
        ],
    )


def swarm_bridge_autoloop(
    config_manager: ConfigManager,
    *,
    path_key: str,
    rounds: int = 1,
    session_id: str | None = None,
    allow_fallback_planner: bool = False,
    force: bool = False,
) -> SwarmBridgeCommandResult:
    repo_root = _resolve_specialization_path_repo_root(config_manager, path_key)
    command = [
        sys.executable,
        "-m",
        "spark_swarm_bridge.cli",
        "specialization-path",
        "autoloop",
        path_key,
        str(repo_root),
        "--rounds",
        str(max(int(rounds or 1), 1)),
    ]
    if session_id:
        command.extend(["--session-id", session_id])
    if allow_fallback_planner:
        command.append("--allow-fallback-planner")
    if force:
        command.append("--force")
    return _run_swarm_bridge_command(
        config_manager,
        action="autoloop",
        repo_root=repo_root,
        path_key=path_key,
        command=command,
    )


def swarm_bridge_execute_rerun_request(
    config_manager: ConfigManager,
    *,
    path_key: str | None = None,
) -> SwarmBridgeCommandResult:
    repo_root = _resolve_specialization_path_repo_root(config_manager, path_key) if path_key else None
    command = [
        sys.executable,
        "-m",
        "spark_swarm_bridge.cli",
        "specialization-path",
        "execute-rerun-request",
    ]
    if path_key:
        command.append(path_key)
    if repo_root:
        command.extend(["--path", str(repo_root)])
    return _run_swarm_bridge_command(
        config_manager,
        action="execute-rerun-request",
        repo_root=repo_root,
        path_key=path_key,
        command=command,
    )


def swarm_bridge_read_autoloop_session(
    config_manager: ConfigManager,
    *,
    path_key: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    repo_root = _resolve_specialization_path_repo_root(config_manager, path_key)
    sessions_root = repo_root / ".spark-swarm" / "specialization-paths" / path_key / "sessions"
    if not sessions_root.exists():
        return {
            "path_key": path_key,
            "repo_root": str(repo_root),
            "session_id": None,
            "session_summary_path": None,
            "session_summary": None,
            "latest_round_summary_path": None,
            "latest_round_summary": None,
            "round_history_path": None,
            "round_history": _load_round_history(repo_root, path_key),
            "available_session_ids": [],
        }
    summary_path = _resolve_session_summary_path(sessions_root, session_id=session_id)
    if summary_path is None:
        return {
            "path_key": path_key,
            "repo_root": str(repo_root),
            "session_id": None,
            "session_summary_path": None,
            "session_summary": None,
            "latest_round_summary_path": None,
            "latest_round_summary": None,
            "round_history_path": None,
            "round_history": _load_round_history(repo_root, path_key),
            "available_session_ids": _list_session_ids(sessions_root),
        }
    summary = _load_json_file(summary_path)
    latest_round_summary_path = _resolve_latest_round_summary_path(summary)
    latest_round_summary = _load_json_file(latest_round_summary_path) if latest_round_summary_path is not None else None
    round_history = _load_round_history(repo_root, path_key)
    return {
        "path_key": path_key,
        "repo_root": str(repo_root),
        "session_id": str(summary.get("sessionId") or summary_path.parent.name),
        "session_summary_path": str(summary_path),
        "session_summary": summary,
        "latest_round_summary_path": str(latest_round_summary_path) if latest_round_summary_path is not None else None,
        "latest_round_summary": latest_round_summary,
        "round_history_path": str(_round_history_path(repo_root, path_key)) if _round_history_path(repo_root, path_key).exists() else None,
        "round_history": round_history,
        "available_session_ids": _list_session_ids(sessions_root),
    }


def swarm_bridge_list_autoloop_sessions(
    config_manager: ConfigManager,
    *,
    path_key: str,
    limit: int = 3,
) -> dict[str, Any]:
    repo_root = _resolve_specialization_path_repo_root(config_manager, path_key)
    sessions_root = repo_root / ".spark-swarm" / "specialization-paths" / path_key / "sessions"
    entries: list[dict[str, Any]] = []
    if sessions_root.exists():
        session_paths = sorted(
            sessions_root.glob("*/summary.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[: max(int(limit or 3), 1)]
        for summary_path in session_paths:
            payload = _load_json_file(summary_path)
            entries.append(
                {
                    "session_id": str(payload.get("sessionId") or summary_path.parent.name),
                    "summary_path": str(summary_path),
                    "updated_at": str(payload.get("updatedAt") or ""),
                    "completed_rounds": int(payload.get("completedRounds") or 0),
                    "requested_rounds_total": int(payload.get("requestedRoundsTotal") or 0),
                    "stop_reason": str(payload.get("stopReason") or "").strip() or None,
                    "planner_status": str(payload.get("plannerStatus") or "").strip() or None,
                    "best_score": payload.get("bestScore"),
                }
            )
    return {
        "path_key": path_key,
        "repo_root": str(repo_root),
        "sessions": entries,
        "round_history": _load_round_history(repo_root, path_key),
    }


def _run_swarm_bridge_command(
    config_manager: ConfigManager,
    *,
    action: str,
    command: list[str],
    repo_root: Path | None,
    path_key: str | None,
) -> SwarmBridgeCommandResult:
    runtime_root = _resolve_swarm_runtime_root(config_manager)
    bridge_cwd = runtime_root / "apps" / "bridge"
    if not bridge_cwd.exists():
        raise RuntimeError(f"Spark Swarm bridge runtime is missing at {bridge_cwd}")
    bridge_src = bridge_cwd / "src"
    env = dict(os.environ)
    existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    pythonpath_entries = [str(bridge_src)] if bridge_src.exists() else []
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    if pythonpath_entries:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["SPARK_SWARM_STATE_DIR"] = str((runtime_root / ".state").resolve())
    researcher_repo = (runtime_root.parent / "spark-researcher").resolve()
    if researcher_repo.exists():
        env["SPARK_RESEARCHER_REPO"] = str(researcher_repo)
    if repo_root is not None and path_key:
        env[_specialization_repo_env_var(path_key)] = str(repo_root.resolve())
    execution = run_governed_command(command=command, cwd=bridge_cwd, env=env)
    session_id = _extract_session_id(execution.stdout)
    session_summary = None
    session_summary_path = None
    round_history = None
    round_history_path = None
    latest_round_summary = None
    latest_round_summary_path = None
    if repo_root is not None and path_key:
        session_payload = swarm_bridge_read_autoloop_session(config_manager, path_key=path_key, session_id=session_id)
        session_id = str(session_payload.get("session_id") or session_id or "").strip() or None
        session_summary = session_payload.get("session_summary") if isinstance(session_payload.get("session_summary"), dict) else None
        session_summary_path = (
            str(session_payload.get("session_summary_path") or "").strip() or None
        )
        latest_round_summary = (
            session_payload.get("latest_round_summary")
            if isinstance(session_payload.get("latest_round_summary"), dict)
            else None
        )
        latest_round_summary_path = str(session_payload.get("latest_round_summary_path") or "").strip() or None
        round_history = session_payload.get("round_history") if isinstance(session_payload.get("round_history"), dict) else None
        round_history_path = str(session_payload.get("round_history_path") or "").strip() or None
    return SwarmBridgeCommandResult(
        ok=execution.ok,
        action=action,
        command=list(command),
        cwd=str(bridge_cwd),
        runtime_root=str(runtime_root),
        repo_root=str(repo_root) if repo_root else None,
        path_key=path_key,
        exit_code=execution.exit_code,
        stdout=execution.stdout.strip(),
        stderr=execution.stderr.strip(),
        session_id=session_id,
        session_summary_path=session_summary_path,
        session_summary=session_summary,
        latest_round_summary_path=latest_round_summary_path,
        latest_round_summary=latest_round_summary,
        round_history_path=round_history_path,
        round_history=round_history,
        artifacts_path=_extract_labeled_path(execution.stdout, "Artifacts"),
        payload_path=_extract_labeled_path(execution.stdout, "Collective payload"),
    )


def _resolve_swarm_runtime_root(config_manager: ConfigManager) -> Path:
    configured = config_manager.get_path("spark.swarm.runtime_root")
    if configured:
        candidate = Path(str(configured)).expanduser().resolve()
        if candidate.exists():
            return candidate
    candidate = (Path.home() / "Desktop" / "spark-swarm").resolve()
    if candidate.exists():
        return candidate
    raise RuntimeError("Spark Swarm runtime root is not configured.")


def _specialization_repo_env_var(path_key: str) -> str:
    normalized = str(path_key or "").strip().upper().replace("-", "_")
    return f"SPARK_SWARM_SPECIALIZATION_PATH_{normalized}_REPO"


def _resolve_specialization_path_repo_root(config_manager: ConfigManager, path_key: str | None) -> Path:
    normalized_key = _normalize_path_key(path_key or "")
    attachments = list_attachments(config_manager, kind="path")
    for record in attachments.records:
        if _normalize_path_key(record.key) == normalized_key:
            return Path(record.repo_root).expanduser().resolve()
    if normalized_key:
        raise RuntimeError(
            f"Spark Swarm specialization path `{path_key}` is not attached locally. "
            "Use `/swarm paths` to inspect available path keys."
        )
    active_key = str(config_manager.get_path("spark.specialization_paths.active_path_key", default="") or "").strip()
    if active_key:
        return _resolve_specialization_path_repo_root(config_manager, active_key)
    raise RuntimeError("No active Spark Swarm specialization path is configured.")


def _resolve_session_summary_path(sessions_root: Path, *, session_id: str | None) -> Path | None:
    if session_id:
        summary_path = sessions_root / session_id / "summary.json"
        return summary_path if summary_path.exists() else None
    session_paths = sorted(
        sessions_root.glob("*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return session_paths[0] if session_paths else None


def _list_session_ids(sessions_root: Path) -> list[str]:
    return [path.parent.name for path in sorted(sessions_root.glob("*/summary.json"), key=lambda item: item.stat().st_mtime, reverse=True)]


def _resolve_latest_round_summary_path(session_summary: dict[str, Any]) -> Path | None:
    rounds = session_summary.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        return None
    latest = rounds[-1]
    if not isinstance(latest, dict):
        return None
    summary_path = str(latest.get("summaryPath") or "").strip()
    if not summary_path:
        return None
    candidate = Path(summary_path).expanduser()
    return candidate.resolve() if candidate.exists() else None


def _round_history_path(repo_root: Path, path_key: str) -> Path:
    return repo_root / ".spark-swarm" / "specialization-paths" / path_key / "round-history.json"


def _load_round_history(repo_root: Path, path_key: str) -> dict[str, Any] | None:
    history_path = _round_history_path(repo_root, path_key)
    if not history_path.exists():
        return None
    return _load_json_file(history_path)


def _load_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return payload


def _extract_session_id(stdout: str) -> str | None:
    match = re.search(r"^Session id:\s*(?P<session_id>\S+)\s*$", stdout or "", flags=re.MULTILINE)
    if not match:
        return None
    return str(match.group("session_id")).strip() or None


def _extract_labeled_path(stdout: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}:\s*(?P<value>.+?)\s*$", stdout or "", flags=re.MULTILINE)
    if not match:
        return None
    return str(match.group("value")).strip() or None


def _normalize_path_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _normalize_path_label(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"\s+\([^)]*\)\s*$", "", str(value or "").strip())
    return normalized or fallback
