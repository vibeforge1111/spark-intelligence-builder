from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

from spark_intelligence.config.loader import ConfigManager


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


def _discover_researcher_root(config_manager: ConfigManager) -> tuple[Path | None, str]:
    config = config_manager.load()
    configured_root = config.get("spark", {}).get("researcher", {}).get("runtime_root")
    if configured_root:
        path = Path(str(configured_root)).expanduser()
        return (path if path.exists() else None, "configured")

    autodetect = Path.home() / "Desktop" / "spark-researcher"
    if autodetect.exists():
        return autodetect, "autodiscovered"
    return None, "missing"


def _resolve_researcher_config_path(config_manager: ConfigManager, runtime_root: Path) -> Path:
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


def build_researcher_reply(
    *,
    config_manager: ConfigManager,
    request_id: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    channel_kind: str,
    user_message: str,
) -> ResearcherBridgeResult:
    runtime_root, runtime_source = _discover_researcher_root(config_manager)
    if runtime_root is not None:
        config_path = _resolve_researcher_config_path(config_manager, runtime_root)
        if config_path.exists():
            try:
                build_advisory = _import_build_advisory(runtime_root)
                advisory = build_advisory(config_path, user_message, model="generic", limit=3, domain=None)
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
                )

    reply_text = (
        f"[Spark Researcher stub] I received your message in {channel_kind} "
        f"for {session_id}: {user_message}"
    )
    return ResearcherBridgeResult(
        request_id=request_id,
        reply_text=reply_text,
        evidence_summary="No external Spark Researcher runtime was configured or discovered.",
        escalation_hint=None,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        mode="stub",
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(_resolve_researcher_config_path(config_manager, runtime_root)) if runtime_root else None,
    )
