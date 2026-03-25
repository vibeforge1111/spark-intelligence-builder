from __future__ import annotations

from dataclasses import dataclass

from spark_intelligence.config.loader import ConfigManager


@dataclass
class ResearcherBridgeResult:
    request_id: str
    reply_text: str
    evidence_summary: str
    escalation_hint: str | None
    trace_ref: str


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
    config = config_manager.load()
    researcher_runtime_root = config.get("spark", {}).get("researcher", {}).get("runtime_root")
    researcher_state = "configured" if researcher_runtime_root else "stub"

    reply_text = (
        f"[Spark Researcher {researcher_state}] I received your message in {channel_kind} "
        f"for {session_id}: {user_message}"
    )
    evidence_summary = "No real Spark Researcher runtime connected yet; returning bridge-safe placeholder output."
    return ResearcherBridgeResult(
        request_id=request_id,
        reply_text=reply_text,
        evidence_summary=evidence_summary,
        escalation_hint=None,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
    )
