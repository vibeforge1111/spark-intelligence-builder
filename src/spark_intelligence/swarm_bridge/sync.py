from __future__ import annotations

import importlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import build_attachment_context
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, resolve_researcher_config_path
from spark_intelligence.state.db import StateDB


@dataclass
class SwarmStatus:
    enabled: bool
    configured: bool
    researcher_ready: bool
    payload_ready: bool
    api_ready: bool
    runtime_root: str | None
    researcher_runtime_root: str | None
    researcher_config_path: str | None
    api_url: str | None
    workspace_id: str | None
    access_token_env: str | None
    attachment_context: dict[str, Any]
    last_sync: dict[str, Any] | None
    last_decision: dict[str, Any] | None
    failure_count: int
    last_failure: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "configured": self.configured,
                "enabled": self.enabled,
                "researcher_ready": self.researcher_ready,
                "payload_ready": self.payload_ready,
                "api_ready": self.api_ready,
                "runtime_root": self.runtime_root,
                "researcher_runtime_root": self.researcher_runtime_root,
                "researcher_config_path": self.researcher_config_path,
                "api_url": self.api_url,
                "workspace_id": self.workspace_id,
                "access_token_env": self.access_token_env,
                "attachment_context": self.attachment_context,
                "last_sync": self.last_sync,
                "last_decision": self.last_decision,
                "failure_count": self.failure_count,
                "last_failure": self.last_failure,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Swarm enabled: {'yes' if self.enabled else 'no'}",
            f"Swarm configured: {'yes' if self.configured else 'no'}",
            f"- researcher_ready: {'yes' if self.researcher_ready else 'no'}",
            f"- payload_ready: {'yes' if self.payload_ready else 'no'}",
            f"- api_ready: {'yes' if self.api_ready else 'no'}",
            f"- runtime_root: {self.runtime_root or 'missing'}",
            f"- researcher_runtime_root: {self.researcher_runtime_root or 'missing'}",
            f"- researcher_config_path: {self.researcher_config_path or 'missing'}",
            f"- api_url: {self.api_url or 'missing'}",
            f"- workspace_id: {self.workspace_id or 'missing'}",
            f"- access_token_env: {self.access_token_env or 'missing'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
            f"- last_sync_mode: {(self.last_sync or {}).get('mode', 'none')}",
            f"- last_decision_mode: {(self.last_decision or {}).get('mode', 'none')}",
            f"- failure_count: {self.failure_count}",
        ]
        if self.last_failure:
            lines.append(
                f"- last_failure: mode={self.last_failure.get('mode') or 'unknown'} "
                f"message={self.last_failure.get('message') or 'unknown'}"
            )
            response_body = self.last_failure.get("response_body")
            if isinstance(response_body, dict) and response_body.get("error"):
                lines.append(f"- last_failure_error: {response_body.get('error')}")
        return "\n".join(lines)


@dataclass
class SwarmSyncResult:
    ok: bool
    mode: str
    message: str
    payload_path: str | None
    api_url: str | None
    workspace_id: str | None
    accepted: bool | None
    response_body: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "mode": self.mode,
                "message": self.message,
                "payload_path": self.payload_path,
                "api_url": self.api_url,
                "workspace_id": self.workspace_id,
                "accepted": self.accepted,
                "response_body": self.response_body,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Swarm sync: {'ok' if self.ok else 'failed'}", f"- mode: {self.mode}", f"- message: {self.message}"]
        if self.payload_path:
            lines.append(f"- payload_path: {self.payload_path}")
        if self.api_url:
            lines.append(f"- api_url: {self.api_url}")
        if self.workspace_id:
            lines.append(f"- workspace_id: {self.workspace_id}")
        if self.accepted is not None:
            lines.append(f"- accepted: {'yes' if self.accepted else 'no'}")
        if self.response_body is not None:
            lines.append(f"- response_body: {json.dumps(self.response_body, sort_keys=True)}")
        return "\n".join(lines)


@dataclass
class SwarmDecisionResult:
    ok: bool
    escalate: bool
    mode: str
    reason: str
    triggers: list[str]
    task: str
    attachment_context: dict[str, Any]
    swarm_available: bool
    api_ready: bool

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "escalate": self.escalate,
                "mode": self.mode,
                "reason": self.reason,
                "triggers": self.triggers,
                "task": self.task,
                "attachment_context": self.attachment_context,
                "swarm_available": self.swarm_available,
                "api_ready": self.api_ready,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Swarm escalation: {'recommended' if self.escalate else 'hold local'}",
            f"- mode: {self.mode}",
            f"- reason: {self.reason}",
            f"- triggers: {', '.join(self.triggers) if self.triggers else 'none'}",
            f"- swarm_available: {'yes' if self.swarm_available else 'no'}",
            f"- api_ready: {'yes' if self.api_ready else 'no'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
        ]
        return "\n".join(lines)


def swarm_status(config_manager: ConfigManager, state_db: StateDB | None = None) -> SwarmStatus:
    enabled = bool(config_manager.get_path("spark.swarm.enabled", default=True))
    runtime_root, _ = _discover_swarm_runtime_root(config_manager)
    researcher_root, _ = discover_researcher_runtime_root(config_manager)
    researcher_config_path = resolve_researcher_config_path(config_manager, researcher_root) if researcher_root else None
    api_url = _resolve_swarm_api_url(config_manager)
    workspace_id = _resolve_swarm_workspace_id(config_manager)
    access_token_env = _resolve_swarm_access_token_env(config_manager)
    access_token = _resolve_swarm_access_token(config_manager)
    attachment_context = build_attachment_context(config_manager)
    runtime_state = _read_swarm_runtime_state(state_db) if state_db is not None else {}
    researcher_ready = enabled and bool(researcher_root and researcher_config_path and researcher_config_path.exists())
    payload_ready = researcher_ready and _researcher_has_ledger(researcher_config_path)
    api_ready = enabled and bool(api_url and workspace_id and access_token)
    return SwarmStatus(
        enabled=enabled,
        configured=bool(runtime_root or api_url or workspace_id or access_token_env),
        researcher_ready=researcher_ready,
        payload_ready=payload_ready,
        api_ready=api_ready,
        runtime_root=str(runtime_root) if runtime_root else None,
        researcher_runtime_root=str(researcher_root) if researcher_root else None,
        researcher_config_path=str(researcher_config_path) if researcher_config_path else None,
        api_url=api_url,
        workspace_id=workspace_id,
        access_token_env=access_token_env,
        attachment_context=attachment_context,
        last_sync=_loads_json_object(runtime_state.get("swarm:last_sync")),
        last_decision=_loads_json_object(runtime_state.get("swarm:last_decision")),
        failure_count=_parse_int(runtime_state.get("swarm:failure_count")),
        last_failure=_loads_json_object(runtime_state.get("swarm:last_failure")),
    )


def sync_swarm_collective(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    dry_run: bool = False,
) -> SwarmSyncResult:
    status = swarm_status(config_manager, state_db)
    if not status.enabled:
        result = SwarmSyncResult(
            ok=False,
            mode="disabled",
            message="Spark Swarm bridge is disabled by operator.",
            payload_path=None,
            api_url=status.api_url,
            workspace_id=status.workspace_id,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(state_db, kind="sync", result=result)
        return result
    if not status.researcher_ready or not status.researcher_config_path or not status.researcher_runtime_root:
        result = SwarmSyncResult(
            ok=False,
            mode="researcher_missing",
            message="Spark Researcher runtime/config is missing; cannot build a Swarm payload.",
            payload_path=None,
            api_url=status.api_url,
            workspace_id=status.workspace_id,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(state_db, kind="sync", result=result)
        return result

    researcher_root = Path(status.researcher_runtime_root)
    researcher_config_path = Path(status.researcher_config_path)
    workspace_id = status.workspace_id
    if not workspace_id:
        result = SwarmSyncResult(
            ok=False,
            mode="workspace_id_missing",
            message="spark.swarm.workspace_id is required before Swarm sync.",
            payload_path=None,
            api_url=status.api_url,
            workspace_id=None,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(state_db, kind="sync", result=result)
        return result

    payload, payload_path = _build_collective_payload(
        config_manager=config_manager,
        researcher_root=researcher_root,
        researcher_config_path=researcher_config_path,
        workspace_id=workspace_id,
    )
    _record_swarm_sync_state(
        state_db,
        mode="payload_built",
        payload_path=str(payload_path),
        api_url=status.api_url,
        workspace_id=workspace_id,
        accepted=None,
    )

    if dry_run:
        return SwarmSyncResult(
            ok=True,
            mode="dry_run",
            message="Built the latest Spark Swarm collective payload without uploading it.",
            payload_path=str(payload_path),
            api_url=status.api_url,
            workspace_id=workspace_id,
            accepted=None,
            response_body={"payload_keys": sorted(payload.keys())},
        )

    api_url = status.api_url
    access_token = _resolve_swarm_access_token(config_manager)
    if not api_url or not access_token:
        result = SwarmSyncResult(
            ok=False,
            mode="api_not_configured",
            message="Swarm API URL or access token is missing; payload was built but not uploaded.",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=None,
            response_body=None,
        )
        _record_swarm_failure_state(state_db, kind="sync", result=result)
        return result

    try:
        response_body = _post_collective_payload(api_url=api_url, workspace_id=workspace_id, access_token=access_token, payload=payload)
    except urllib.error.HTTPError as exc:
        body = _read_http_error_body(exc)
        _record_swarm_sync_state(
            state_db,
            mode="http_error",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
        )
        result = SwarmSyncResult(
            ok=False,
            mode="http_error",
            message=f"Swarm API rejected the sync with HTTP {exc.code}.",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            response_body=body,
        )
        _record_swarm_failure_state(state_db, kind="sync", result=result)
        return result
    except urllib.error.URLError as exc:
        _record_swarm_sync_state(
            state_db,
            mode="network_error",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
        )
        result = SwarmSyncResult(
            ok=False,
            mode="network_error",
            message=f"Could not reach Swarm API: {exc.reason}",
            payload_path=str(payload_path),
            api_url=api_url,
            workspace_id=workspace_id,
            accepted=False,
            response_body=None,
        )
        _record_swarm_failure_state(state_db, kind="sync", result=result)
        return result

    accepted = bool(response_body.get("accepted"))
    _record_swarm_sync_state(
        state_db,
        mode="uploaded",
        payload_path=str(payload_path),
        api_url=api_url,
        workspace_id=workspace_id,
        accepted=accepted,
    )
    result = SwarmSyncResult(
        ok=accepted,
        mode="uploaded",
        message="Uploaded the latest Spark Researcher collective payload to Spark Swarm.",
        payload_path=str(payload_path),
        api_url=api_url,
        workspace_id=workspace_id,
        accepted=accepted,
        response_body=response_body,
    )
    if not accepted:
        _record_swarm_failure_state(state_db, kind="sync", result=result)
    return result


def evaluate_swarm_escalation(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    task: str,
) -> SwarmDecisionResult:
    status = swarm_status(config_manager, state_db)
    if not status.enabled:
        result = SwarmDecisionResult(
            ok=False,
            escalate=False,
            mode="disabled",
            reason="Spark Swarm is disabled by operator for this workspace.",
            triggers=[],
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=False,
            api_ready=False,
        )
        _record_swarm_decision_state(state_db, result=result)
        _record_swarm_failure_state(state_db, kind="decision", result=result)
        return result
    lowered = task.lower()
    triggers: list[str] = []
    auto_recommend_enabled = bool(
        config_manager.get_path("spark.swarm.routing.auto_recommend_enabled", default=True)
    )
    long_task_word_count = int(
        config_manager.get_path("spark.swarm.routing.long_task_word_count", default=40)
    )
    keyword_groups = {
        "explicit_swarm": ["swarm", "delegate", "delegation"],
        "parallel_work": ["parallel", "multi-agent", "multi agent", "coordinate"],
        "deep_research": ["research deeply", "investigate deeply", "comprehensive"],
        "multi_step": ["break down", "multi-step", "orchestrate", "workflow"],
    }
    for trigger, phrases in keyword_groups.items():
        if any(phrase in lowered for phrase in phrases):
            triggers.append(trigger)
    if len(task.split()) >= long_task_word_count:
        triggers.append("long_task")
    if len(status.attachment_context.get("active_chip_keys", [])) >= 2:
        triggers.append("multi_chip_context")

    if not status.payload_ready:
        result = SwarmDecisionResult(
            ok=False,
            escalate=False,
            mode="unavailable",
            reason="Spark Swarm cannot be recommended because the local payload path is not ready.",
            triggers=triggers,
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=False,
            api_ready=status.api_ready,
        )
        _record_swarm_failure_state(state_db, kind="decision", result=result)
    elif triggers and auto_recommend_enabled:
        result = SwarmDecisionResult(
            ok=True,
            escalate=True,
            mode="manual_recommended",
            reason="This task shows explicit escalation signals and Spark Swarm is available.",
            triggers=triggers,
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=True,
            api_ready=status.api_ready,
        )
    else:
        result = SwarmDecisionResult(
            ok=True,
            escalate=False,
            mode="hold_local",
            reason="No strong escalation signals were detected; keep the task on the primary agent.",
            triggers=triggers,
            task=task,
            attachment_context=status.attachment_context,
            swarm_available=True,
            api_ready=status.api_ready,
        )

    _record_swarm_decision_state(state_db, result=result)
    return result


def _discover_swarm_runtime_root(config_manager: ConfigManager) -> tuple[Path | None, str]:
    configured_root = config_manager.get_path("spark.swarm.runtime_root")
    if configured_root:
        path = Path(str(configured_root)).expanduser()
        return (path if path.exists() else None, "configured")
    autodetect = Path.home() / "Desktop" / "spark-swarm"
    if autodetect.exists():
        return autodetect, "autodiscovered"
    return None, "missing"


def _resolve_swarm_api_url(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.api_url")
    if configured:
        return str(configured).rstrip("/")
    return None


def _resolve_swarm_workspace_id(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.workspace_id")
    if configured:
        return str(configured)
    env_value = config_manager.read_env_map().get("SPARK_SWARM_WORKSPACE_ID")
    return env_value or None


def _resolve_swarm_access_token_env(config_manager: ConfigManager) -> str | None:
    configured = config_manager.get_path("spark.swarm.access_token_env")
    if configured:
        return str(configured)
    env_map = config_manager.read_env_map()
    if "SPARK_SWARM_ACCESS_TOKEN" in env_map:
        return "SPARK_SWARM_ACCESS_TOKEN"
    return None


def _resolve_swarm_access_token(config_manager: ConfigManager) -> str | None:
    env_ref = _resolve_swarm_access_token_env(config_manager)
    if not env_ref:
        return None
    return config_manager.read_env_map().get(env_ref)


def _researcher_has_ledger(config_path: Path) -> bool:
    try:
        resolve_runtime_root = _import_researcher_symbol(config_path.parent.resolve(), "spark_researcher.paths", "resolve_runtime_root")
        ledger_path = _import_researcher_symbol(config_path.parent.resolve(), "spark_researcher.paths", "ledger_path")
        runtime_root = resolve_runtime_root(config_path)
        return ledger_path(runtime_root).exists()
    except Exception:
        return False


def _build_collective_payload(
    *,
    config_manager: ConfigManager,
    researcher_root: Path,
    researcher_config_path: Path,
    workspace_id: str,
) -> tuple[dict[str, Any], Path]:
    load_config = _import_researcher_symbol(researcher_root, "spark_researcher.config", "load_config")
    resolve_runtime_root = _import_researcher_symbol(researcher_root, "spark_researcher.paths", "resolve_runtime_root")
    write_payload = _import_researcher_symbol(
        researcher_root,
        "spark_researcher.collective",
        "write_spark_swarm_collective_payload_from_latest",
    )

    config = load_config(researcher_config_path)
    runtime_root = resolve_runtime_root(researcher_config_path)
    with _temporary_env("SPARK_SWARM_WORKSPACE_ID", workspace_id):
        export_info = write_payload(researcher_root, runtime_root, config)
    payload_path = Path(str(export_info["payload_path"]))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if _normalize_runtime_source(payload):
        payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, payload_path


def _post_collective_payload(
    *,
    api_url: str,
    workspace_id: str,
    access_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url=urllib.parse.urljoin(f"{api_url}/", f"api/workspaces/{workspace_id}/collective/sync"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _normalize_runtime_source(payload: dict[str, Any]) -> bool:
    runtime_source = payload.get("runtimeSource")
    if not isinstance(runtime_source, dict):
        runtime_source = {}
        payload["runtimeSource"] = runtime_source

    changed = False
    agent_id = str(payload.get("agentId") or "").strip()
    if agent_id and not str(runtime_source.get("sourceInstanceId") or "").strip():
        runtime_source["sourceInstanceId"] = agent_id
        changed = True

    emitted_at = str(payload.get("emittedAt") or "").strip()
    runtime_kind = str(runtime_source.get("kind") or "spark_researcher").strip() or "spark_researcher"
    run_prefix = "spark-researcher" if runtime_kind == "spark_researcher" else runtime_kind.replace("_", "-")
    if emitted_at and not str(runtime_source.get("sourceRunId") or "").strip():
        runtime_source["sourceRunId"] = f"{run_prefix}:{emitted_at}"
        changed = True

    return changed


def _record_swarm_sync_state(
    state_db: StateDB,
    *,
    mode: str,
    payload_path: str,
    api_url: str | None,
    workspace_id: str | None,
    accepted: bool | None,
) -> None:
    with state_db.connect() as conn:
        _set_runtime_state(
            conn,
            "swarm:last_sync",
            json.dumps(
                {
                    "mode": mode,
                    "payload_path": payload_path,
                    "api_url": api_url,
                    "workspace_id": workspace_id,
                    "accepted": accepted,
                },
                sort_keys=True,
            ),
        )
        conn.commit()


def _record_swarm_decision_state(state_db: StateDB, *, result: SwarmDecisionResult) -> None:
    with state_db.connect() as conn:
        _set_runtime_state(
            conn,
            "swarm:last_decision",
            json.dumps(
                {
                    "mode": result.mode,
                    "escalate": result.escalate,
                    "reason": result.reason,
                    "triggers": result.triggers,
                    "task": result.task,
                },
                sort_keys=True,
            ),
        )
        conn.commit()


def _record_swarm_failure_state(
    state_db: StateDB,
    *,
    kind: str,
    result: SwarmSyncResult | SwarmDecisionResult,
) -> None:
    with state_db.connect() as conn:
        failure_count = _read_failure_count(conn, "swarm:failure_count")
        _set_runtime_state(conn, "swarm:failure_count", str(failure_count + 1))
        if isinstance(result, SwarmSyncResult):
            payload = {
                "kind": kind,
                "mode": result.mode,
                "message": result.message,
                "api_url": result.api_url,
                "workspace_id": result.workspace_id,
                "payload_path": result.payload_path,
                "response_body": result.response_body,
                "recorded_at": _utc_now_iso(),
            }
        else:
            payload = {
                "kind": kind,
                "mode": result.mode,
                "message": result.reason,
                "api_ready": result.api_ready,
                "swarm_available": result.swarm_available,
                "triggers": result.triggers,
                "recorded_at": _utc_now_iso(),
            }
        _set_runtime_state(conn, "swarm:last_failure", json.dumps(payload, sort_keys=True))
        conn.commit()


def _read_swarm_runtime_state(state_db: StateDB) -> dict[str, str]:
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'swarm:%'"
        ).fetchall()
    return {str(row["state_key"]): str(row["value"] or "") for row in rows}


def _loads_json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_int(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _read_failure_count(conn: Any, state_key: str) -> int:
    row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return 0
    try:
        return int(str(row["value"]))
    except ValueError:
        return 0


def _set_runtime_state(conn: Any, state_key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO runtime_state(state_key, value)
        VALUES (?, ?)
        ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (state_key, value),
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _import_researcher_symbol(runtime_root: Path, module_name: str, symbol: str):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module(module_name)
    return getattr(module, symbol)


def _read_http_error_body(exc: urllib.error.HTTPError) -> dict[str, Any] | None:
    try:
        raw = exc.read().decode("utf-8")
    except Exception:
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


@contextmanager
def _temporary_env(key: str, value: str):
    previous = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
