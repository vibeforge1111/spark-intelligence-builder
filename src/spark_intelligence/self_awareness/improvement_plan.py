from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.self_awareness.capsule import build_self_awareness_capsule
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class SelfImprovementPlanResult:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = [
            "Spark self-improvement plan",
            "",
            f"Goal: {self.payload.get('goal') or 'Improve Spark self-awareness and capability confidence.'}",
            f"Mode: {self.payload.get('mode') or 'plan_only'}",
            f"Evidence: {self.payload.get('evidence_level') or 'unknown'}",
            "",
            str(self.payload.get("summary") or "").strip(),
        ]
        actions = [item for item in self.payload.get("priority_actions") or [] if isinstance(item, dict)]
        if actions:
            lines.extend(["", "Priority actions"])
            for index, action in enumerate(actions[:5], start=1):
                title = str(action.get("title") or f"Action {index}").strip()
                lines.append(f"{index}. {title}")
                for key in ("weak_spot", "next_probe", "improvement_action", "evidence_to_collect"):
                    value = str(action.get(key) or "").strip()
                    if value:
                        label = key.replace("_", " ")
                        lines.append(f"   - {label}: {value}")
        invocations = [str(item) for item in self.payload.get("natural_language_invocations") or [] if str(item).strip()]
        if invocations:
            lines.extend(["", "Natural language invocations"])
            lines.extend(f"- {item}" for item in invocations[:4])
        sources = [item for item in self.payload.get("wiki_sources") or [] if isinstance(item, dict)]
        if sources:
            lines.extend(["", "Wiki sources"])
            for source in sources[:4]:
                title = str(source.get("title") or "wiki source").strip()
                path = str(source.get("source_path") or "").strip()
                lines.append(f"- {title}: {path}" if path else f"- {title}")
        lines.extend(["", "Guardrail", str(self.payload.get("guardrail") or "").strip()])
        return "\n".join(line for line in lines if line is not None).strip()


def build_self_improvement_plan(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    goal: str = "",
    human_id: str = "",
    session_id: str = "",
    channel_kind: str = "",
    request_id: str | None = None,
    user_message: str = "",
    refresh_wiki: bool = False,
    limit: int = 5,
) -> SelfImprovementPlanResult:
    normalized_goal = _normalize_goal(goal or user_message)
    capsule = build_self_awareness_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message or normalized_goal,
    ).to_payload()
    from spark_intelligence.llm_wiki.query import build_llm_wiki_query

    wiki_result = build_llm_wiki_query(
        config_manager=config_manager,
        state_db=state_db,
        query=_wiki_query_for_goal(normalized_goal),
        refresh=refresh_wiki,
        limit=limit,
    )
    wiki_hits = [hit for hit in wiki_result.payload.get("hits") or [] if isinstance(hit, dict)]
    actions = _priority_actions(capsule=capsule, goal=normalized_goal)
    payload = {
        "goal": normalized_goal,
        "mode": "plan_only_probe_first",
        "summary": _summary(actions, wiki_hits),
        "evidence_level": _evidence_level(actions=actions, wiki_hits=wiki_hits),
        "priority_actions": actions,
        "natural_language_invocations": _natural_language_invocations(actions),
        "wiki_sources": [_wiki_source(hit) for hit in wiki_hits[:5]],
        "live_self_awareness": {
            "generated_at": capsule.get("generated_at"),
            "workspace_id": capsule.get("workspace_id"),
            "observed_now": _claim_rows(capsule.get("observed_now"), 4),
            "recently_verified": _claim_rows(capsule.get("recently_verified"), 3),
            "lacks": _claim_rows(capsule.get("lacks"), 5),
            "improvement_options": _claim_rows(capsule.get("improvement_options"), 5),
            "source_ledger": [item for item in capsule.get("source_ledger") or [] if isinstance(item, dict)][:4],
        },
        "wiki_retrieval_status": wiki_result.payload.get("wiki_retrieval_status"),
        "wiki_hit_count": len(wiki_hits),
        "project_knowledge_first": bool(wiki_result.payload.get("project_knowledge_first")),
        "guardrail": (
            "This is not autonomous self-modification. Spark should run the named probes, collect evidence, "
            "then make a bounded code/config/wiki change only when the user asks for that specific improvement."
        ),
        "authority": "current_snapshot_plus_supporting_wiki_not_execution",
    }
    return SelfImprovementPlanResult(payload=payload)


def _normalize_goal(goal: str) -> str:
    compact = re.sub(r"\s+", " ", goal).strip(" .?!")
    return compact or "Improve Spark self-awareness, capability confidence, and weak-spot handling"


def _wiki_query_for_goal(goal: str) -> str:
    return (
        f"{goal} Spark self-awareness gaps improvement options route tracing capability confidence "
        "LLM wiki recursive self-improvement"
    )


def _priority_actions(*, capsule: dict[str, Any], goal: str) -> list[dict[str, Any]]:
    lacks = _claim_rows(capsule.get("lacks"), 8)
    improvements = _claim_rows(capsule.get("improvement_options"), 8)
    rows: list[dict[str, Any]] = []
    for index, lack in enumerate(lacks):
        improvement = _best_improvement_for_lack(lack, improvements, fallback_index=index)
        score = _goal_score(goal, lack.get("claim", ""), improvement.get("claim", ""))
        rows.append(
            {
                "title": _action_title(lack.get("claim", ""), improvement.get("claim", "")),
                "weak_spot": lack.get("claim", ""),
                "improvement_action": improvement.get("claim", "") or lack.get("improvement_action", ""),
                "next_probe": lack.get("next_probe", "") or improvement.get("next_probe", ""),
                "evidence_to_collect": _evidence_to_collect(lack.get("claim", "")),
                "source": lack.get("source", ""),
                "confidence": lack.get("confidence", ""),
                "verification_status": lack.get("verification_status", ""),
                "score": score,
                "execution_state": "needs_probe_before_change",
            }
        )
    rows.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("title") or "")))
    return rows[:5]


def _best_improvement_for_lack(
    lack: dict[str, str],
    improvements: list[dict[str, str]],
    *,
    fallback_index: int,
) -> dict[str, str]:
    lack_source = lack.get("source", "")
    for improvement in improvements:
        if improvement.get("source") and improvement.get("source") == lack_source:
            return improvement
    if fallback_index < len(improvements):
        return improvements[fallback_index]
    return {}


def _goal_score(goal: str, *texts: str) -> int:
    goal_terms = {term for term in re.findall(r"[a-z0-9]+", goal.casefold()) if len(term) >= 4}
    text_terms = set()
    for text in texts:
        text_terms.update(term for term in re.findall(r"[a-z0-9]+", text.casefold()) if len(term) >= 4)
    return len(goal_terms & text_terms)


def _action_title(weak_spot: str, improvement: str) -> str:
    text = improvement or weak_spot
    compact = re.sub(r"\s+", " ", text).strip()
    compact = compact.removeprefix("Improve ")
    if len(compact) > 88:
        compact = f"{compact[:85].rstrip()}..."
    return compact[:1].upper() + compact[1:] if compact else "Probe and improve a weak spot"


def _evidence_to_collect(weak_spot: str) -> str:
    lowered = weak_spot.casefold()
    if "natural-language" in lowered or "natural language" in lowered or "route" in lowered:
        return "Route-selection eval cases, last selected route, authorization result, and emitted trace id."
    if "registry visibility" in lowered or "succeeded this turn" in lowered:
        return "Per-capability last_success_at, last_failure_reason, latency, and exact invocation result."
    if "secret" in lowered or "private infrastructure" in lowered:
        return "Redacted diagnostic summary that proves health without exposing secret values."
    if "provider" in lowered:
        return "Provider auth freshness, model availability, latency, quota/rate-limit status, and fallback path."
    return "Current snapshot, direct probe output, source ledger entry, and regression/eval coverage."


def _natural_language_invocations(actions: list[dict[str, Any]]) -> list[str]:
    invocations = [
        "Spark, run the safest probe for the top weak spot before changing anything.",
        "Spark, show me the evidence you collected for that capability.",
    ]
    if actions:
        probe = str(actions[0].get("next_probe") or "").strip()
        if probe:
            invocations.insert(1, f"Spark, {probe}")
    invocations.append("Spark, after the probe passes, make the smallest code or wiki update that removes the gap.")
    return invocations


def _summary(actions: list[dict[str, Any]], wiki_hits: list[dict[str, Any]]) -> str:
    if not actions:
        return "I did not find a compact weak-spot list in the live self-awareness capsule. Run self status first, then retry with a specific goal."
    wiki_part = "with supporting wiki context" if wiki_hits else "without matching wiki context"
    return (
        f"I found {len(actions)} improvement action(s) from the live self-awareness capsule {wiki_part}. "
        "The right move is probe-first: prove the gap, collect route evidence, then make the smallest bounded improvement."
    )


def _evidence_level(*, actions: list[dict[str, Any]], wiki_hits: list[dict[str, Any]]) -> str:
    if actions and wiki_hits:
        return "live_self_snapshot_with_wiki_support"
    if actions:
        return "live_self_snapshot_only"
    if wiki_hits:
        return "wiki_support_without_live_actions"
    return "insufficient_evidence"


def _claim_rows(value: object, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in (value if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        rows.append(
            {
                "claim": claim,
                "source": str(item.get("source") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
                "verification_status": str(item.get("verification_status") or "").strip(),
                "next_probe": str(item.get("next_probe") or "").strip(),
                "improvement_action": str(item.get("improvement_action") or "").strip(),
            }
        )
    return rows[:limit]


def _wiki_source(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(hit.get("title") or "").strip(),
        "source_path": str(hit.get("source_path") or "").strip(),
        "authority": str(hit.get("authority") or "supporting_not_authoritative").strip(),
        "score": hit.get("score"),
    }
