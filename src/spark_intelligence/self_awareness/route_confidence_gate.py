from __future__ import annotations

from typing import Any

from spark_intelligence.self_awareness.route_confidence_doctrine import (
    ROUTE_CONFIDENCE_DOCTRINE_SCHEMA_VERSION,
    build_route_confidence_doctrine,
)


ROUTE_CONFIDENCE_GATE_SCHEMA_VERSION = "spark.route_confidence_gate.v1"
REPAIR_ACTION_ROUTES = frozenset({"spawner.repair", "spark.repair"})
MEMORY_ACTION_ROUTES = frozenset(
    {
        "memory.write",
        "memory.correct",
        "memory.forget",
        "memory.decay",
        "memory.promote",
        "memory.deepen",
    }
)
PUBLISH_ACTION_ROUTES = frozenset({"spark.publish", "swarm.publish", "artifact.publish", "release.publish"})
CONFIRMATION_REQUIRED_RISKS = frozenset(
    {"high", "destructive", "external", "credential", "secret", "publication", "filesystem_delete", "broad_mutation"}
)
FORBIDDEN_DATA_FLAGS = (
    "raw_prompt_exported",
    "provider_output_exported",
    "chat_or_user_id_exported",
    "memory_body_exported",
    "artifact_body_exported",
    "transcript_or_audio_exported",
    "env_or_secret_exported",
)
DATA_BOUNDARY_EXPORT_FLAGS = frozenset(
    {
        "exports_raw_prompt",
        "exports_provider_output",
        "exports_chat_id",
        "exports_memory_body",
        "exports_transcript_body",
        "exports_audio",
        "exports_env_value",
        "exports_secret",
    }
)


def build_route_confidence_gate(
    *,
    intent: str,
    candidate_route: str,
    latest_spawner_job: dict[str, Any] | None = None,
    route_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_intent = _safe_label(intent) or "unknown"
    safe_route = _safe_label(candidate_route) or "unknown"
    context = _dict(route_context)
    if _is_action_route(safe_route):
        return _build_action_route_gate(intent=safe_intent, candidate_route=safe_route, route_context=context)

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


def _is_action_route(route: str) -> bool:
    return route in {
        "spawner.build",
        "spawner.default_build",
        "spawner.contextual_mission",
        "spawner.prd_bridge",
        "spawner.project_iteration",
        *REPAIR_ACTION_ROUTES,
        *MEMORY_ACTION_ROUTES,
        *PUBLISH_ACTION_ROUTES,
    }


def _build_action_route_gate(
    *,
    intent: str,
    candidate_route: str,
    route_context: dict[str, Any],
) -> dict[str, Any]:
    privacy_violations = _privacy_violations(route_context)
    permission_required = _safe_label(route_context.get("permission_required")) or "none"
    authority_verdict, authority_valid, authority_missing = _authority_verdict_status(
        route_context.get("authority_verdict"),
        permission_required=permission_required,
    )
    capability_state = _safe_label(route_context.get("capability_state")) or "unknown"
    runner_state = _safe_label(route_context.get("runner_state")) or "unknown"
    intent_clarity = _safe_label(route_context.get("intent_clarity")) or "unknown"
    route_fit = _safe_label(route_context.get("route_fit")) or "unknown"
    consequence_risk = _safe_label(route_context.get("consequence_risk")) or "unknown"
    confirmation_state = _safe_label(route_context.get("confirmation_state")) or "unknown"
    latest_instruction = _safe_label(route_context.get("latest_instruction")) or "unknown"
    reversibility = _safe_label(route_context.get("reversibility")) or "unknown"
    repair_target = _safe_label(route_context.get("repair_target")) or "unknown"
    repair_scope = _safe_label(route_context.get("repair_scope")) or "unknown"
    health_evidence = _safe_label(route_context.get("health_evidence") or route_context.get("fresh_health_status")) or "unknown"
    publication_target = _safe_label(route_context.get("publication_target")) or "unknown"
    memory_action_verdict, memory_action_valid, memory_action_missing = _memory_action_verdict_status(
        route_context.get("memory_action_verdict"),
        candidate_route=candidate_route,
    )
    source_status = _safe_label(route_context.get("source_status")) or "present"
    freshness = _safe_label(route_context.get("freshness")) or "current_turn"
    missing_evidence = [str(item) for item in _list(route_context.get("missing_evidence")) if str(item or "").strip()]
    missing_evidence.extend(authority_missing)
    missing_evidence.extend(memory_action_missing)

    required_context_missing = []
    if latest_instruction == "unknown":
        required_context_missing.append("latest_instruction_missing")
    if intent_clarity == "unknown":
        required_context_missing.append("intent_clarity_missing")
    if route_fit == "unknown":
        required_context_missing.append("route_fit_missing")
    if consequence_risk == "unknown":
        required_context_missing.append("consequence_risk_missing")
    if confirmation_state == "unknown":
        required_context_missing.append("confirmation_state_missing")
    if capability_state == "unknown":
        required_context_missing.append("runner_capability_state_missing")
    if runner_state == "unknown":
        required_context_missing.append("runner_state_missing")
    if not authority_valid:
        required_context_missing.append("structured_authority_verdict_missing")
    required_context_missing.extend(
        _route_family_missing_context(
            candidate_route=candidate_route,
            repair_target=repair_target,
            repair_scope=repair_scope,
            health_evidence=health_evidence,
            publication_target=publication_target,
            consequence_risk=consequence_risk,
            memory_action_valid=memory_action_valid,
        )
    )

    if privacy_violations:
        decision = "refuse"
        confidence = "blocked"
        safe_reply_policy = "refuse_privacy_violation"
        missing_evidence = [*missing_evidence, *privacy_violations]
    elif _bool(route_context.get("explicit_no_execution")) or latest_instruction == "no_execution":
        decision = "explain"
        confidence = "high"
        safe_reply_policy = "explain_no_execution_boundary"
    elif authority_verdict in {"blocked", "denied"}:
        decision = "refuse"
        confidence = "blocked"
        safe_reply_policy = "refuse_authority_blocked"
        missing_evidence.append("authority_blocked")
    elif memory_action_verdict in {"blocked", "denied"}:
        decision = "refuse"
        confidence = "blocked"
        safe_reply_policy = "refuse_authority_blocked"
        missing_evidence.append("memory_action_blocked")
    elif required_context_missing:
        decision = "ask"
        confidence = "blocked"
        safe_reply_policy = "ask_for_route_evidence"
        missing_evidence.extend(required_context_missing)
    elif capability_state in {"unavailable", "missing"} or runner_state in {"unavailable", "missing"}:
        decision = "ask"
        confidence = "blocked"
        safe_reply_policy = "ask_for_capability_repair"
        if capability_state in {"unavailable", "missing"}:
            missing_evidence.append("runner_capability_unavailable")
        if runner_state in {"unavailable", "missing"}:
            missing_evidence.append("runner_state_unavailable")
    elif authority_verdict == "confirm_required" and confirmation_state != "confirmed":
        decision = "ask"
        confidence = "medium"
        safe_reply_policy = "ask_for_confirmation"
        missing_evidence.append("authority_confirmation_required")
    elif memory_action_verdict == "confirm_required" and confirmation_state != "confirmed":
        decision = "ask"
        confidence = "medium"
        safe_reply_policy = "ask_for_confirmation"
        missing_evidence.append("memory_action_confirmation_required")
    elif (
        candidate_route in REPAIR_ACTION_ROUTES
        and repair_target == "none_needed"
        and health_evidence == "fresh_healthy"
    ):
        decision = "explain"
        confidence = "high"
        safe_reply_policy = "explain_no_repair_needed"
    elif consequence_risk in CONFIRMATION_REQUIRED_RISKS and confirmation_state != "confirmed":
        decision = "ask"
        confidence = "medium"
        safe_reply_policy = "ask_for_confirmation"
        missing_evidence.append("confirmation_required")
    elif intent_clarity in {"underspecified", "weak"} or route_fit in {"weak", "blocked"}:
        decision = "ask"
        confidence = "medium" if intent_clarity == "underspecified" else "low"
        safe_reply_policy = "ask_for_scope"
        missing_evidence.append("intent_or_route_fit_insufficient")
    else:
        decision = "act"
        confidence = "high" if authority_verdict in {"allowed", "not_required"} and capability_state == "available" else "medium"
        safe_reply_policy = "execute_with_trace"

    return {
        "schema_version": ROUTE_CONFIDENCE_GATE_SCHEMA_VERSION,
        "intent": intent,
        "candidate_route": candidate_route,
        "owner_system": "spark-intelligence-builder",
        "source_owner_system": "spark-telegram-bot + spawner-ui",
        "decision": decision,
        "confidence": confidence,
        "source_status": source_status,
        "freshness": freshness,
        "provider": None,
        "model": None,
        "provider_source": None,
        "required_sources": ["latest_instruction", "authority_verdict", "runner_capability"],
        "joined_sources": [_safe_label(item) for item in _list(route_context.get("joined_sources")) if _safe_label(item)],
        "missing_evidence": _dedupe(missing_evidence),
        "source_refs_redacted": [
            str(value)
            for value in _list(route_context.get("source_refs_redacted"))
            if isinstance(value, str) and value.strip()
        ],
        "authority_required": permission_required != "none",
        "permission_required": permission_required,
        "authority_verdict": authority_verdict,
        "capability_state": capability_state,
        "runner_state": runner_state,
        "intent_clarity": intent_clarity,
        "route_fit": route_fit,
        "consequence_risk": consequence_risk,
        "confirmation_state": confirmation_state,
        "latest_instruction": latest_instruction,
        "reversibility": reversibility,
        "repair_target": repair_target if candidate_route in REPAIR_ACTION_ROUTES else None,
        "repair_scope": repair_scope if candidate_route in REPAIR_ACTION_ROUTES else None,
        "health_evidence": health_evidence if candidate_route in REPAIR_ACTION_ROUTES else None,
        "publication_target": publication_target if candidate_route in PUBLISH_ACTION_ROUTES else None,
        "memory_action_verdict": memory_action_verdict if candidate_route in MEMORY_ACTION_ROUTES else None,
        "safe_reply_policy": safe_reply_policy,
        "human_next_action": _action_human_next_action(safe_reply_policy, _dedupe(missing_evidence)),
        "verification_command": _safe_label(route_context.get("verification_command")) or "spark os trace --json",
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
            "RouteConfidenceGateV1 decides whether Spark is justified in taking an action route right now. "
            "Adapters provide metadata-only route context; Builder owns the act/ask/explain/refuse verdict."
        ),
        "doctrine": {
            "schema_version": ROUTE_CONFIDENCE_DOCTRINE_SCHEMA_VERSION,
            "definition": "Route Confidence means whether Spark is justified in taking this route right now.",
            "decision_values": ["act", "ask", "explain", "refuse"],
            "hard_precedence_rules": build_route_confidence_doctrine()["hard_precedence_rules"],
        },
    }


def _route_family_missing_context(
    *,
    candidate_route: str,
    repair_target: str,
    repair_scope: str,
    health_evidence: str,
    publication_target: str,
    consequence_risk: str,
    memory_action_valid: bool,
) -> list[str]:
    missing: list[str] = []
    if candidate_route in REPAIR_ACTION_ROUTES:
        if repair_target == "unknown":
            missing.append("repair_target_missing")
        if repair_scope == "unknown":
            missing.append("repair_scope_missing")
        if health_evidence == "unknown":
            missing.append("repair_health_evidence_missing")
    if candidate_route in PUBLISH_ACTION_ROUTES:
        if publication_target == "unknown":
            missing.append("publication_target_missing")
        if consequence_risk not in CONFIRMATION_REQUIRED_RISKS:
            missing.append("publication_risk_classification_missing")
    if candidate_route in MEMORY_ACTION_ROUTES and not memory_action_valid:
        missing.append("memory_action_verdict_missing")
    return missing


def _action_human_next_action(policy: str, missing_evidence: list[str]) -> str:
    if policy == "execute_with_trace":
        return "Proceed through the source-owned route and keep request/trace evidence metadata-only."
    if policy == "explain_no_execution_boundary":
        return "Do not execute; answer in chat because the latest instruction blocks action."
    if policy == "refuse_authority_blocked":
        return "Do not execute; surface the authority/access blocker."
    if policy == "ask_for_capability_repair":
        return "Ask the human to inspect runner capability before dispatch."
    if policy == "ask_for_confirmation":
        return "Ask one confirmation question before taking the side-effecting action."
    if policy == "ask_for_scope":
        return "Ask one clarifying question before dispatch."
    if policy == "ask_for_route_evidence":
        return "Ask for or refresh source-owned route evidence before dispatch."
    if policy == "explain_no_repair_needed":
        return "Explain that fresh health evidence shows no repair is needed; do not restart or mutate anything."
    if missing_evidence:
        return "Inspect missing route evidence before acting: " + ", ".join(_dedupe(missing_evidence)[:4])
    return "Pause and explain the route boundary."


def _missing_evidence(evidence: dict[str, Any]) -> list[str]:
    missing = [str(item) for item in _list(evidence.get("missing_sources")) if str(item or "").strip()]
    missing.extend(str(item) for item in _list(evidence.get("blockers")) if str(item or "").strip())
    if evidence and not evidence.get("provider"):
        missing.append("missing_executed_provider_model")
    return _dedupe(missing)


def _privacy_violations(evidence: dict[str, Any]) -> list[str]:
    boundary = _dict(evidence.get("data_boundary"))
    violations = [
        f"privacy_violation:{flag}"
        for flag in FORBIDDEN_DATA_FLAGS
        if _truthy(boundary.get(flag))
    ]
    export_flags = {
        "exports_raw_prompt": "raw_prompt_exported",
        "exports_provider_output": "provider_output_exported",
        "exports_chat_id": "chat_or_user_id_exported",
        "exports_memory_body": "memory_body_exported",
        "exports_transcript_body": "transcript_or_audio_exported",
        "exports_audio": "transcript_or_audio_exported",
        "exports_env_value": "env_or_secret_exported",
        "exports_secret": "env_or_secret_exported",
    }
    for flag, canonical in export_flags.items():
        if _truthy(boundary.get(flag)):
            violations.append(f"privacy_violation:{canonical}")
    for key in _forbidden_payload_keys(evidence):
        violations.append(f"privacy_violation:forbidden_payload_key:{key}")
    return _dedupe(violations)


def _authority_verdict_status(value: Any, *, permission_required: str) -> tuple[str, bool, list[str]]:
    if isinstance(value, dict):
        schema = _safe_label(value.get("schema_version"))
        status = _safe_label(value.get("decision") or value.get("status") or value.get("verdict")) or "missing"
        owner = _safe_label(value.get("source_owner") or value.get("source_owner_system") or value.get("owner_system"))
        action_family = _safe_label(value.get("action_family") or value.get("action") or value.get("route"))
        valid = (
            schema == "spark.authority_verdict.v1"
            and status in {"allowed", "not_required", "blocked", "denied", "confirm_required"}
            and bool(owner)
            and bool(action_family)
        )
        missing: list[str] = []
        if not valid:
            missing.append("invalid_authority_verdict_v1")
        return status, valid, missing

    status = _safe_label(value) or "missing"
    if status in {"blocked", "denied"}:
        return status, True, []
    if status == "not_required" and permission_required == "none":
        return status, True, []
    return status, False, ["structured_authority_verdict_missing"]


def _memory_action_verdict_status(value: Any, *, candidate_route: str) -> tuple[str, bool, list[str]]:
    if candidate_route not in MEMORY_ACTION_ROUTES:
        return "not_applicable", True, []
    if not isinstance(value, dict):
        return "missing", False, ["structured_memory_action_verdict_missing"]
    schema = _safe_label(value.get("schema_version"))
    verdict = _safe_label(value.get("verdict") or value.get("decision") or value.get("status")) or "missing"
    owner = _safe_label(value.get("owner_system") or value.get("source_owner") or value.get("source_owner_system"))
    action_family = _safe_label(value.get("action_family") or value.get("action") or value.get("route"))
    valid = (
        schema == "spark.memory_action_verdict.v1"
        and verdict in {"allowed", "not_required", "blocked", "denied", "confirm_required"}
        and bool(owner)
        and bool(action_family)
    )
    if valid:
        return verdict, True, []
    return verdict, False, ["invalid_memory_action_verdict_v1"]


def _forbidden_payload_keys(value: Any, *, prefix: str = "") -> list[str]:
    forbidden = {
        "raw_prompt",
        "prompt",
        "current_message",
        "currentmessage",
        "user_message",
        "chat_id",
        "userid",
        "user_id",
        "provider_output",
        "memory_body",
        "transcript_body",
        "audio",
        "env",
        "secret",
        "token",
        "api_key",
        "authorization",
    }
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip().lower()
            normalized = key.replace("-", "_")
            if prefix == "data_boundary." and normalized in DATA_BOUNDARY_EXPORT_FLAGS:
                continue
            if normalized in forbidden or normalized.endswith("_secret") or normalized.endswith("_token"):
                found.append(f"{prefix}{raw_key}"[:120])
            found.extend(_forbidden_payload_keys(raw_value, prefix=f"{prefix}{raw_key}."))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            found.extend(_forbidden_payload_keys(item, prefix=f"{prefix}{index}."))
    return _dedupe(found)


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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


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
