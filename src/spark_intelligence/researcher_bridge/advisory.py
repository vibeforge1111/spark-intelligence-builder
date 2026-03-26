from __future__ import annotations

import importlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.attachments import build_attachment_context
from spark_intelligence.auth.runtime import RuntimeProviderResolution, resolve_runtime_provider
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


@dataclass
class ResearcherBridgeResult:
    request_id: str
    reply_text: str
    evidence_summary: str
    escalation_hint: str | None
    trace_ref: str
    mode: str
    runtime_root: str | None
    config_path: str | None
    attachment_context: dict[str, object] | None
    provider_id: str | None = None
    provider_auth_profile_id: str | None = None
    provider_auth_method: str | None = None
    provider_model: str | None = None
    provider_model_family: str | None = None
    provider_base_url: str | None = None
    provider_source: str | None = None


@dataclass
class ResearcherBridgeStatus:
    enabled: bool
    configured: bool
    available: bool
    mode: str
    runtime_root: str | None
    config_path: str | None
    attachment_context: dict[str, object]
    last_mode: str | None
    last_trace_ref: str | None
    last_request_id: str | None
    last_runtime_root: str | None
    last_config_path: str | None
    last_evidence_summary: str | None
    last_attachment_context: dict[str, Any] | None
    last_provider_id: str | None
    last_provider_model: str | None
    last_provider_model_family: str | None
    last_provider_auth_method: str | None
    failure_count: int
    last_failure: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "configured": self.configured,
                "available": self.available,
                "enabled": self.enabled,
                "mode": self.mode,
                "runtime_root": self.runtime_root,
                "config_path": self.config_path,
                "attachment_context": self.attachment_context,
                "last_mode": self.last_mode,
                "last_trace_ref": self.last_trace_ref,
                "last_request_id": self.last_request_id,
                "last_runtime_root": self.last_runtime_root,
                "last_config_path": self.last_config_path,
                "last_evidence_summary": self.last_evidence_summary,
                "last_attachment_context": self.last_attachment_context,
                "last_provider_id": self.last_provider_id,
                "last_provider_model": self.last_provider_model,
                "last_provider_model_family": self.last_provider_model_family,
                "last_provider_auth_method": self.last_provider_auth_method,
                "failure_count": self.failure_count,
                "last_failure": self.last_failure,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Researcher bridge enabled: {'yes' if self.enabled else 'no'}",
            f"Researcher bridge configured: {'yes' if self.configured else 'no'}",
            f"- available: {'yes' if self.available else 'no'}",
            f"- mode: {self.mode}",
            f"- runtime_root: {self.runtime_root or 'missing'}",
            f"- config_path: {self.config_path or 'missing'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- pinned_chip_keys: {', '.join(self.attachment_context.get('pinned_chip_keys', [])) if self.attachment_context.get('pinned_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
            f"- last_mode: {self.last_mode or 'none'}",
            f"- last_trace_ref: {self.last_trace_ref or 'none'}",
            f"- last_request_id: {self.last_request_id or 'none'}",
        ]
        if self.last_runtime_root:
            lines.append(f"- last_runtime_root: {self.last_runtime_root}")
        if self.last_config_path:
            lines.append(f"- last_config_path: {self.last_config_path}")
        if self.last_evidence_summary:
            lines.append(f"- last_evidence_summary: {self.last_evidence_summary}")
        if self.last_provider_id:
            lines.append(f"- last_provider_id: {self.last_provider_id}")
        if self.last_provider_model:
            lines.append(f"- last_provider_model: {self.last_provider_model}")
        if self.last_provider_model_family:
            lines.append(f"- last_provider_model_family: {self.last_provider_model_family}")
        if self.last_provider_auth_method:
            lines.append(f"- last_provider_auth_method: {self.last_provider_auth_method}")
        lines.append(f"- failure_count: {self.failure_count}")
        if self.last_failure:
            lines.append(
                f"- last_failure: mode={self.last_failure.get('mode')} "
                f"at={self.last_failure.get('recorded_at')} "
                f"message={self.last_failure.get('message')}"
            )
        return "\n".join(lines)


def discover_researcher_runtime_root(config_manager: ConfigManager) -> tuple[Path | None, str]:
    config = config_manager.load()
    configured_root = config.get("spark", {}).get("researcher", {}).get("runtime_root")
    if configured_root:
        path = Path(str(configured_root)).expanduser()
        return (path if path.exists() else None, "configured")

    autodetect = Path.home() / "Desktop" / "spark-researcher"
    if autodetect.exists():
        return autodetect, "autodiscovered"
    return None, "missing"


def resolve_researcher_config_path(config_manager: ConfigManager, runtime_root: Path) -> Path:
    config = config_manager.load()
    configured_path = config.get("spark", {}).get("researcher", {}).get("config_path")
    if configured_path:
        return Path(str(configured_path)).expanduser()
    return runtime_root / "spark-researcher.project.json"


def _import_build_advisory(runtime_root: Path):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module("spark_researcher.advisory")
    return getattr(module, "build_advisory")


def _import_execute_with_research(runtime_root: Path):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module("spark_researcher.research")
    return getattr(module, "execute_with_research")


def _render_reply_from_advisory(advisory: dict) -> tuple[str, str, str]:
    guidance = advisory.get("guidance") or []
    epistemic = advisory.get("epistemic_status") or {}
    selected_packet_ids = advisory.get("selected_packet_ids") or []
    trace_ref = advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing"

    guidance_lines = guidance[:2] if isinstance(guidance, list) else []
    if guidance_lines:
        reply_text = " ".join(str(item).strip() for item in guidance_lines if str(item).strip())
    else:
        reply_text = "Spark Researcher returned no concrete guidance for this message."

    evidence_summary = (
        f"status={epistemic.get('status', 'unknown')} "
        f"packets={len(selected_packet_ids)} "
        f"stability={((epistemic.get('packet_stability') or {}).get('status', 'unknown'))}"
    )
    return reply_text, evidence_summary, str(trace_ref)


def _render_reply_from_execution(execution: dict[str, Any], advisory: dict[str, Any]) -> tuple[str, str, str]:
    response = execution.get("response")
    reply_text = ""
    if isinstance(response, dict):
        raw = response.get("raw_response")
        if isinstance(raw, str):
            reply_text = raw.strip()
    elif isinstance(response, str):
        reply_text = response.strip()

    if not reply_text:
        reply_text, _, _ = _render_reply_from_advisory(advisory)

    decision = str(execution.get("decision") or execution.get("status") or "unknown")
    trace_ref = (
        str(execution.get("research_trace_path") or "")
        or str(execution.get("trace_path") or "")
        or str(advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing")
    )
    evidence_summary = f"status={decision} provider_execution=yes"
    return reply_text, evidence_summary, trace_ref


def _build_contextual_task(*, user_message: str, attachment_context: dict[str, object]) -> str:
    active_chip_keys = attachment_context.get("active_chip_keys") or []
    pinned_chip_keys = attachment_context.get("pinned_chip_keys") or []
    active_path_key = attachment_context.get("active_path_key") or None
    lines = [
        "[Spark Intelligence context]",
        f"active_chip_keys={','.join(str(item) for item in active_chip_keys) if active_chip_keys else 'none'}",
        f"pinned_chip_keys={','.join(str(item) for item in pinned_chip_keys) if pinned_chip_keys else 'none'}",
        f"active_path_key={active_path_key or 'none'}",
        "",
        "[User message]",
        user_message,
    ]
    return "\n".join(lines)


def researcher_bridge_status(*, config_manager: ConfigManager, state_db: StateDB) -> ResearcherBridgeStatus:
    attachment_context = build_attachment_context(config_manager)
    runtime_root, runtime_source = discover_researcher_runtime_root(config_manager)
    config_path = resolve_researcher_config_path(config_manager, runtime_root) if runtime_root else None
    enabled = bool(config_manager.get_path("spark.researcher.enabled", default=True))
    available = enabled and bool(runtime_root and config_path and config_path.exists())
    mode = "disabled" if not enabled else (f"external_{runtime_source}" if available else "stub")
    runtime_state = _read_runtime_state(state_db)
    return ResearcherBridgeStatus(
        enabled=enabled,
        configured=runtime_root is not None,
        available=available,
        mode=mode,
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(config_path) if config_path else None,
        attachment_context=attachment_context,
        last_mode=runtime_state.get("researcher:last_mode"),
        last_trace_ref=runtime_state.get("researcher:last_trace_ref"),
        last_request_id=runtime_state.get("researcher:last_request_id"),
        last_runtime_root=runtime_state.get("researcher:last_runtime_root"),
        last_config_path=runtime_state.get("researcher:last_config_path"),
        last_evidence_summary=runtime_state.get("researcher:last_evidence_summary"),
        last_attachment_context=_loads_json(runtime_state.get("researcher:last_attachment_context")),
        last_provider_id=runtime_state.get("researcher:last_provider_id"),
        last_provider_model=runtime_state.get("researcher:last_provider_model"),
        last_provider_model_family=runtime_state.get("researcher:last_provider_model_family"),
        last_provider_auth_method=runtime_state.get("researcher:last_provider_auth_method"),
        failure_count=_parse_int(runtime_state.get("researcher:failure_count")),
        last_failure=_loads_json(runtime_state.get("researcher:last_failure")),
    )


def record_researcher_bridge_result(*, state_db: StateDB, result: ResearcherBridgeResult) -> None:
    with state_db.connect() as conn:
        _set_runtime_state(conn, "researcher:last_mode", result.mode)
        _set_runtime_state(conn, "researcher:last_trace_ref", result.trace_ref)
        _set_runtime_state(conn, "researcher:last_request_id", result.request_id)
        _set_runtime_state(conn, "researcher:last_runtime_root", result.runtime_root or "")
        _set_runtime_state(conn, "researcher:last_config_path", result.config_path or "")
        _set_runtime_state(conn, "researcher:last_evidence_summary", result.evidence_summary)
        _set_runtime_state(conn, "researcher:last_provider_id", result.provider_id or "")
        _set_runtime_state(conn, "researcher:last_provider_model", result.provider_model or "")
        _set_runtime_state(conn, "researcher:last_provider_model_family", result.provider_model_family or "")
        _set_runtime_state(conn, "researcher:last_provider_auth_method", result.provider_auth_method or "")
        _set_runtime_state(
            conn,
            "researcher:last_attachment_context",
            json.dumps(result.attachment_context or {}, sort_keys=True),
        )
        if result.mode == "bridge_error":
            failure_count = _read_failure_count(conn, "researcher:failure_count")
            _set_runtime_state(conn, "researcher:failure_count", str(failure_count + 1))
            _set_runtime_state(
                conn,
                "researcher:last_failure",
                json.dumps(
                    {
                        "mode": result.mode,
                        "request_id": result.request_id,
                        "runtime_root": result.runtime_root,
                        "config_path": result.config_path,
                        "message": result.reply_text,
                        "recorded_at": _utc_now_iso(),
                    },
                    sort_keys=True,
                ),
            )
        conn.commit()


def build_researcher_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    request_id: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    channel_kind: str,
    user_message: str,
) -> ResearcherBridgeResult:
    attachment_context = build_attachment_context(config_manager)
    contextual_task = _build_contextual_task(user_message=user_message, attachment_context=attachment_context)
    provider_selection = _resolve_bridge_provider(config_manager=config_manager, state_db=state_db)
    if not bool(config_manager.get_path("spark.researcher.enabled", default=True)):
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text="[Spark Researcher disabled] The operator has disabled the Spark Researcher bridge for this workspace.",
            evidence_summary="Spark Researcher bridge disabled by operator.",
            escalation_hint=None,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="disabled",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
        )
    if provider_selection.error:
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=f"[Spark Researcher provider auth error] {provider_selection.error}",
            evidence_summary="Provider resolution failed closed before bridge execution.",
            escalation_hint="provider_auth_error",
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="bridge_error",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
        )
    runtime_root, runtime_source = discover_researcher_runtime_root(config_manager)
    if runtime_root is not None:
        config_path = resolve_researcher_config_path(config_manager, runtime_root)
        if config_path.exists():
            try:
                build_advisory = _import_build_advisory(runtime_root)
                execute_with_research = _import_execute_with_research(runtime_root)
                advisory = build_advisory(
                    config_path,
                    contextual_task,
                    model=provider_selection.model_family,
                    limit=3,
                    domain=None,
                )
                if provider_selection.provider and _supports_direct_or_cli_execution(provider_selection):
                    with _temporary_provider_env(provider_selection.provider):
                        execution = execute_with_research(
                            runtime_root,
                            advisory=advisory,
                            model=provider_selection.model_family,
                            command_override=_command_override_for_provider(provider_selection),
                            dry_run=False,
                        )
                    reply_text, evidence_summary, trace_ref = _render_reply_from_execution(execution, advisory)
                else:
                    reply_text, evidence_summary, trace_ref = _render_reply_from_advisory(advisory)
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=reply_text,
                    evidence_summary=evidence_summary,
                    escalation_hint=None,
                    trace_ref=trace_ref,
                    mode=f"external_{runtime_source}",
                    runtime_root=str(runtime_root),
                    config_path=str(config_path),
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
                    provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                    provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                    provider_model_family=provider_selection.model_family,
                    provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                    provider_source=provider_selection.provider.source if provider_selection.provider else None,
                )
            except Exception as exc:  # pragma: no cover - external bridge safety
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=f"[Spark Researcher bridge error] {exc}",
                    evidence_summary="External bridge failed closed.",
                    escalation_hint="bridge_error",
                    trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                    mode="bridge_error",
                    runtime_root=str(runtime_root),
                    config_path=str(config_path),
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
                    provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                    provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                    provider_model_family=provider_selection.model_family,
                    provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                    provider_source=provider_selection.provider.source if provider_selection.provider else None,
                )

    reply_text = (
        f"[Spark Researcher stub] I received your message in {channel_kind} "
        f"for {session_id}: {user_message}"
    )
    return ResearcherBridgeResult(
        request_id=request_id,
        reply_text=reply_text,
        evidence_summary=(
            "No external Spark Researcher runtime was configured or discovered. "
            f"active_chips={len(attachment_context.get('active_chip_keys') or [])} "
            f"active_path={attachment_context.get('active_path_key') or 'none'}"
        ),
        escalation_hint=None,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        mode="stub",
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(resolve_researcher_config_path(config_manager, runtime_root)) if runtime_root else None,
        attachment_context=attachment_context,
        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
        provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
        provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
        provider_model_family=provider_selection.model_family,
        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
        provider_source=provider_selection.provider.source if provider_selection.provider else None,
    )


@dataclass(frozen=True)
class ResearcherProviderSelection:
    provider: RuntimeProviderResolution | None
    model_family: str
    error: str | None = None


def _resolve_bridge_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> ResearcherProviderSelection:
    provider_records = config_manager.load().get("providers", {}).get("records", {}) or {}
    if not provider_records:
        return ResearcherProviderSelection(provider=None, model_family="generic")
    try:
        provider = resolve_runtime_provider(config_manager=config_manager, state_db=state_db)
    except RuntimeError as exc:
        return ResearcherProviderSelection(provider=None, model_family="generic", error=str(exc))
    return ResearcherProviderSelection(
        provider=provider,
        model_family=_model_family_for_provider(provider),
    )


def _model_family_for_provider(provider: RuntimeProviderResolution) -> str:
    provider_id = provider.provider_id.lower()
    model_name = (provider.default_model or "").lower()
    if provider_id == "openai-codex" or "codex" in model_name:
        return "codex"
    if provider.api_mode == "anthropic_messages" or provider_id == "anthropic" or "claude" in model_name:
        return "claude"
    if "openclaw" in model_name:
        return "openclaw"
    return "generic"


def _supports_direct_or_cli_execution(selection: ResearcherProviderSelection) -> bool:
    provider = selection.provider
    if provider is None:
        return False
    if selection.model_family == "codex":
        return True
    return provider.api_mode in {"chat_completions", "anthropic_messages"}


def _command_override_for_provider(selection: ResearcherProviderSelection) -> list[str] | None:
    provider = selection.provider
    if provider is None:
        return None
    if selection.model_family == "codex":
        return None
    return [
        sys.executable,
        "-m",
        "spark_intelligence.llm.provider_wrapper",
        "{system_prompt_path}",
        "{user_prompt_path}",
        "{response_path}",
    ]


@contextmanager
def _temporary_provider_env(provider: RuntimeProviderResolution):
    values = {
        "SPARK_INTELLIGENCE_PROVIDER_ID": provider.provider_id,
        "SPARK_INTELLIGENCE_PROVIDER_KIND": provider.provider_kind,
        "SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD": provider.auth_method,
        "SPARK_INTELLIGENCE_PROVIDER_API_MODE": provider.api_mode,
        "SPARK_INTELLIGENCE_PROVIDER_MODEL": provider.default_model or "",
        "SPARK_INTELLIGENCE_PROVIDER_BASE_URL": provider.base_url or "",
        "SPARK_INTELLIGENCE_PROVIDER_SECRET": provider.secret_value,
    }
    original = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_runtime_state(state_db: StateDB) -> dict[str, str]:
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'researcher:%'"
        ).fetchall()
    return {str(row["state_key"]): str(row["value"] or "") for row in rows}


def _loads_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_runtime_state(conn: Any, state_key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO runtime_state(state_key, value)
        VALUES (?, ?)
        ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (state_key, value),
    )
