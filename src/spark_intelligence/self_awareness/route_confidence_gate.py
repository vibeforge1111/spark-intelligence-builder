from __future__ import annotations

from typing import Any

from spark_intelligence.self_awareness.route_confidence_doctrine import (
    ROUTE_CONFIDENCE_DOCTRINE_SCHEMA_VERSION,
    build_route_confidence_doctrine,
)


ROUTE_CONFIDENCE_GATE_SCHEMA_VERSION = "spark.route_confidence_gate.v1"
FORBIDDEN_DATA_FLAGS = (
    "raw_prompt_exported",
    "provider_output_exported",
    "chat_or_user_id_exported",
    "memory_body_exported",
    "artifact_body_exported",
    "transcript_or_audio_exported",
    "env_or_secret_exported",
)


def build_route_confidence_gate(
    *,
    intent: str,
    candidate_route: str,
    latest_spawner_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_intent = _safe_label(intent) or "unknown"
    safe_route = _safe_label(candidate_route) or "unknown"
    evidence = _dict(latest_spawner_job)
    missing_evidence = _missing_evidence(evidence)
    privacy_violations = _privacy_violations(evidence)
    provider = _safe_label(evidence.get("provider"))
    model = _safe_label(evidence.get("model"))
    freshness = _safe_label(evidence.get("freshness")) or "unknown"
    source_status = _safe_label(evidence.get("status")) or "missing"
    evidence_confidence = _safe_label(evidence.get("confidence")) or "blocked"

    if privacy_violations:
        decision = "refuse"
        confidence = "blocked"
        safe_reply_policy = "refuse_privacy_violation"
        missing_evidence = [*missing_evidence, *privacy_violations]
    elif not evidence:
        decision = "ask"
        confidence = "blocked"
        safe_reply_policy = "ask_for_trace_compile"
        missing_evidence = ["latest_spawner_job_evidence_missing"]
    elif provider and freshness in {"current", "recent"}:
        decision = "explain"
        confidence = "high" if evidence_confidence == "high" else "medium"
        safe_reply_policy = "answer_live"
    elif provider:
        decision = "explain"
        confidence = "low"
        safe_reply_policy = "explain_stale_or_partial"
        if "fresh_spawner_job" not in missing_evidence:
            missing_evidence.append("fresh_spawner_job")
    else:
        decision = "explain"
        confidence = "low" if source_status != "missing" else "blocked"
        safe_reply_policy = "explain_missing"

    return {
        "schema_version": ROUTE_CONFIDENCE_GATE_SCHEMA_VERSION,
        "intent": safe_intent,
        "candidate_route": safe_route,
        "owner_system": "spark-intelligence-builder",
        "source_owner_system": "spawner-ui",
        "decision": decision,
        "confidence": confidence,
        "source_status": source_status,
        "freshness": freshness,
        "provider": provider or None,
        "model": model or None,
        "provider_source": _safe_label(evidence.get("provider_source")) or None,
        "required_sources": ["mission-control", "spawner-prd-trace", "agent-events"],
        "joined_sources": [_safe_label(item) for item in _list(evidence.get("joined_sources")) if _safe_label(item)],
        "missing_evidence": _dedupe(missing_evidence),
        "source_refs_redacted": [
            str(value)
            for value in (
                evidence.get("request_ref_redacted"),
                evidence.get("trace_ref_redacted"),
                evidence.get("mission_ref_redacted"),
            )
            if isinstance(value, str) and value.strip()
        ],
        "authority_required": False,
        "safe_reply_policy": safe_reply_policy,
        "human_next_action": _human_next_action(safe_reply_policy, missing_evidence),
        "verification_command": _safe_label(evidence.get("verification_command")) or "spark os trace --json",
        "data_boundary": {
            "exports_raw_prompt": False,
            "exports_chat_id": False,
            "exports_provider_output": False,
            "exports_memory_body": False,
            "exports_transcript_body": False,
            "exports_audio": False,
            "exports_env_value": False,
            "exports_secret": False,
        },
        "source_policy": (
            "RouteConfidenceGateV1 answers latest route/status questions from source-owned compiled evidence. "
            "Memory and LLM wiki are supporting only and cannot answer current runtime facts."
        ),
        "doctrine": {
            "schema_version": ROUTE_CONFIDENCE_DOCTRINE_SCHEMA_VERSION,
            "definition": "Route Confidence means whether Spark is justified in taking this route right now.",
            "decision_values": ["act", "ask", "explain", "refuse"],
            "hard_precedence_rules": build_route_confidence_doctrine()["hard_precedence_rules"],
        },
    }


def _missing_evidence(evidence: dict[str, Any]) -> list[str]:
    missing = [str(item) for item in _list(evidence.get("missing_sources")) if str(item or "").strip()]
    missing.extend(str(item) for item in _list(evidence.get("blockers")) if str(item or "").strip())
    if evidence and not evidence.get("provider"):
        missing.append("missing_executed_provider_model")
    return _dedupe(missing)


def _privacy_violations(evidence: dict[str, Any]) -> list[str]:
    boundary = _dict(evidence.get("data_boundary"))
    return [f"privacy_violation:{flag}" for flag in FORBIDDEN_DATA_FLAGS if boundary.get(flag) is True]


def _human_next_action(policy: str, missing_evidence: list[str]) -> str:
    if policy == "answer_live":
        return "Answer from the compiled latest Spawner job evidence and keep trace refs redacted."
    if policy == "ask_for_trace_compile":
        return "Run `spark os compile` or `spark os trace --json`, then ask again."
    if missing_evidence:
        return "Inspect the missing source-owned evidence before answering: " + ", ".join(_dedupe(missing_evidence)[:4])
    return "Use `/board` or `/diagnose` if live Spawner status is still unclear."


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_label(value: Any, *, limit: int = 160) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    for marker in ("token", "secret", "api_key", "authorization", "chat_id", "user_id"):
        text = text.replace(marker, marker.replace("_", "-"))
    return text[:limit]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out
