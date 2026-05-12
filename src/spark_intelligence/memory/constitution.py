from __future__ import annotations

import hashlib
import re
from typing import Any


MEMORY_CONSTITUTION_SCHEMA_VERSION = "spark.memory_constitution.v1"
MEMORY_PROOF_CARD_SCHEMA_VERSION = "spark.memory_proof_card.v1"
MEMORY_REVIEW_CARD_SCHEMA_VERSION = "spark.memory_review_card.v1"

MEMORY_TYPES = frozenset(
    {
        "fact",
        "preference",
        "doctrine",
        "project_state",
        "warning",
        "open_loop",
        "relationship",
        "failure_pattern",
        "taste_rule",
    }
)

DURABILITY_TIERS = frozenset(
    {
        "ephemeral_context",
        "supporting_memory",
        "durable_user_memory",
        "durable_intelligence_memory",
        "not_keepable",
    }
)

FORBIDDEN_EXPORT_KEYS = frozenset(
    {
        "raw_prompt",
        "prompt",
        "message_text",
        "memory_body",
        "chat_id",
        "user_id",
        "provider_output",
        "transcript",
        "transcript_body",
        "audio",
        "env",
        "secret",
        "token",
    }
)


def build_memory_constitution_summary() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_CONSTITUTION_SCHEMA_VERSION,
        "memory_types": sorted(MEMORY_TYPES),
        "durability_tiers": sorted(DURABILITY_TIERS),
        "priority_order": [
            "current_user_message",
            "live_runtime_probe",
            "authority_verdict",
            "current_state_memory",
            "fresh_source_backed_recall",
            "supporting_memory",
            "wiki_or_doctrine",
            "conversation_summary",
        ],
        "privacy": {
            "raw_prompt_export_allowed": False,
            "memory_payload_export_allowed": False,
            "channel_identifier_export_allowed": False,
            "provider_response_export_allowed": False,
            "raw_audio_export_allowed": False,
            "transcript_payload_export_allowed": False,
            "secret_value_export_allowed": False,
        },
    }


def build_memory_preflight_proof_card(
    *,
    source: str,
    role: str,
    freshness: str,
    source_ref: str = "",
    selected_route: str = "",
    confidence: str = "",
    request_id: str = "",
    trace_ref: str = "",
    summary: str = "",
) -> dict[str, Any]:
    safe_source = _safe_label(source, "memory_preflight")
    safe_role = _safe_label(role, "memory_boundary")
    safe_freshness = _safe_label(freshness, "unknown")
    safe_route = _safe_label(selected_route, "unknown_route")
    source_refs = [_safe_source_ref(source_ref or request_id or trace_ref or safe_source)]
    card_seed = "|".join([trace_ref, request_id, safe_source, safe_role, safe_route])
    return {
        "schema_version": MEMORY_PROOF_CARD_SCHEMA_VERSION,
        "card_id": f"memory-proof:{_short_hash(card_seed)}",
        "trace_ref": _safe_trace_ref(trace_ref),
        "owner_system": "spark-intelligence-builder",
        "surface": "builder",
        "operation": "save_preflight" if "write" in safe_route or "save" in safe_route else "recall_preflight",
        "decision": "blocked" if safe_freshness in {"stale", "contradicted"} else "support_only",
        "memory_type": _infer_memory_type(selected_route=selected_route, summary=summary),
        "durability_tier": "ephemeral_context",
        "freshness": safe_freshness,
        "confidence": _confidence_score(confidence),
        "source_refs": source_refs,
        "relations": _relations_for_preflight(selected_route=selected_route, role=role),
        "blocked_reasons": [] if safe_freshness not in {"stale", "contradicted"} else [f"memory_source_{safe_freshness}"],
        "human_next_action": "Review Builder or Cockpit memory lanes before treating this as durable memory.",
        "correction_path": "Use the memory review queue or an explicit corrected memory request.",
        "data_boundary": (
            "No raw memory body, chat id, provider output, transcript, audio, env value, or secret is exported."
        ),
    }


def build_memory_review_card_from_resolution(
    resolution: Any,
    *,
    request_id: str = "",
    trace_ref: str = "",
) -> dict[str, Any]:
    payload = resolution.to_payload() if hasattr(resolution, "to_payload") else dict(resolution or {})
    claim_key = _safe_label(str(payload.get("claim_key") or "memory_claim"), "memory_claim")
    winner = payload.get("winner") if isinstance(payload.get("winner"), dict) else {}
    stale_claims = payload.get("stale_claims") if isinstance(payload.get("stale_claims"), list) else []
    contradicted_claims = (
        payload.get("contradicted_claims") if isinstance(payload.get("contradicted_claims"), list) else []
    )
    review_type = "contradiction" if contradicted_claims else "source_freshness"
    freshness = "contradicted" if contradicted_claims else "stale"
    source_refs = [
        _safe_source_ref(str(claim.get("source_ref") or claim.get("source") or "memory_source"))
        for claim in [*stale_claims, *contradicted_claims]
        if isinstance(claim, dict)
    ]
    winner_source = _safe_label(str(winner.get("source") or "unknown"), "unknown")
    card_seed = "|".join([trace_ref, request_id, claim_key, winner_source, review_type])
    return {
        "schema_version": MEMORY_REVIEW_CARD_SCHEMA_VERSION,
        "card_id": f"memory-review:{_short_hash(card_seed)}",
        "trace_ref": _safe_trace_ref(trace_ref),
        "owner_system": "spark-intelligence-builder",
        "surface": "builder",
        "review_type": review_type,
        "decision": "needs_review",
        "claim_key": claim_key,
        "freshness": freshness,
        "winner_source": winner_source,
        "stale_source_count": len(stale_claims),
        "contradicted_source_count": len(contradicted_claims),
        "source_refs": source_refs[:8],
        "relations": ["source_hierarchy", claim_key, review_type],
        "blocked_reasons": [f"memory_source_{freshness}"],
        "human_next_action": _review_human_next_action(review_type),
        "correction_path": "Use Builder memory review or a current-source correction before reuse.",
        "data_boundary": (
            "No claim values, memory bodies, raw prompts, chat ids, provider output, transcripts, audio, env values, "
            "or secrets are exported."
        ),
    }


def validate_memory_review_card_export(card: dict[str, Any]) -> list[str]:
    serialized = _flatten_keys(card)
    return sorted(key for key in serialized if _forbidden_key(key))


def memory_preflight_facts(
    *,
    source: str,
    role: str,
    freshness: str,
    source_ref: str = "",
    selected_route: str = "",
    confidence: str = "",
    request_id: str = "",
    trace_ref: str = "",
    summary: str = "",
) -> dict[str, Any]:
    return {
        "memory_constitution": build_memory_constitution_summary(),
        "save_gate": {
            "schema_version": "spark.save_gate.v1",
            "owner_system": "spark-intelligence-builder",
            "decision": "needs_review",
            "durability_tier": "ephemeral_context",
            "blocked_reasons": ["preflight_is_not_durable_memory"],
        },
        "recall_gate": {
            "schema_version": "spark.recall_gate.v1",
            "owner_system": "spark-intelligence-builder",
            "decision": "support_only",
            "current_context_wins": True,
            "source_backed": bool(source_ref or request_id or trace_ref),
            "blocked_reasons": [],
        },
        "memory_proof_card": build_memory_preflight_proof_card(
            source=source,
            role=role,
            freshness=freshness,
            source_ref=source_ref,
            selected_route=selected_route,
            confidence=confidence,
            request_id=request_id,
            trace_ref=trace_ref,
            summary=summary,
        ),
    }


def validate_memory_proof_card_export(card: dict[str, Any]) -> list[str]:
    serialized = _flatten_keys(card)
    return sorted(key for key in serialized if _forbidden_key(key))


def _safe_label(value: str | None, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9:_-]+", "_", text).strip("_")
    return text or fallback


def _safe_source_ref(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "memory_preflight"
    return re.sub(r"[^a-zA-Z0-9:._/-]+", "_", text)[:160]


def _safe_trace_ref(value: str | None) -> str | None:
    text = str(value or "").strip()
    return _safe_source_ref(text) if text else None


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _confidence_score(value: str | None) -> float:
    label = _safe_label(value, "medium")
    return {"explicit": 0.95, "high": 0.85, "medium": 0.65, "low": 0.35, "blocked": 0.0}.get(label, 0.55)


def _infer_memory_type(*, selected_route: str, summary: str) -> str:
    text = f"{selected_route} {summary}".lower()
    if any(token in text for token in ("preference", "preferred", "like", "style")):
        return "preference"
    if any(token in text for token in ("plan", "focus", "status", "current")):
        return "project_state"
    if any(token in text for token in ("warning", "block", "risk", "failure")):
        return "warning"
    if any(token in text for token in ("doctrine", "rule", "principle")):
        return "doctrine"
    return "fact"


def _relations_for_preflight(*, selected_route: str, role: str) -> list[str]:
    relations = ["memory_boundary"]
    route = _safe_label(selected_route, "")
    if route:
        relations.append(route)
    role_label = _safe_label(role, "")
    if role_label and role_label not in relations:
        relations.append(role_label)
    return relations[:6]


def _review_human_next_action(review_type: str) -> str:
    if review_type == "contradiction":
        return "Resolve the conflicting source before letting recalled memory guide action."
    return "Confirm the current source, then mark stale lower-authority memory as stale or support-only."


def _flatten_keys(value: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            keys.add(path)
            keys.update(_flatten_keys(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            keys.update(_flatten_keys(nested, f"{prefix}[{index}]"))
    return keys


def _forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in FORBIDDEN_EXPORT_KEYS)
