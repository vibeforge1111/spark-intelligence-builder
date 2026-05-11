from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


RouteConfidenceLabel = Literal["high", "medium", "low", "blocked", "unknown"]


@dataclass(frozen=True)
class RouteConfidenceReport:
    recommended_route: str
    confidence: RouteConfidenceLabel
    score: int
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    source_policy: str = (
        "Route confidence combines task fit, live runner capability, access, and route health; "
        "it is evidence for routing, not proof the route succeeded this turn."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "recommended_route": self.recommended_route,
            "confidence": self.confidence,
            "score": self.score,
            "evidence": list(self.evidence),
            "risks": list(self.risks),
            "source_policy": self.source_policy,
        }


def build_route_confidence(
    *,
    task_fit: dict[str, Any],
    routes: list[dict[str, Any]],
    runner: dict[str, Any],
    access: dict[str, Any],
) -> RouteConfidenceReport:
    recommended_route = str(task_fit.get("recommended_route") or "unknown")
    route_by_key = {str(route.get("key") or ""): route for route in routes if isinstance(route, dict)}
    evidence = _base_evidence(task_fit=task_fit, runner=runner, access=access)
    risks = [str(item) for item in list(task_fit.get("blocked_here_by") or []) if str(item or "").strip()]

    if recommended_route == "chat":
        return RouteConfidenceReport(
            recommended_route=recommended_route,
            confidence="high",
            score=90,
            evidence=[*evidence, "Current task fit says chat can answer before route changes."],
            risks=risks,
        )
    if recommended_route == "current_writable_runner":
        score = 86 if runner.get("writable") is True else 55
        if runner.get("writable") is not True:
            risks.append("runner_writability_not_confirmed")
        return _report(
            recommended_route=recommended_route,
            score=score,
            evidence=[*evidence, "Current runner is the proposed execution route."],
            risks=risks,
        )
    if recommended_route in {"writable_spawner_codex_mission", "probe_runner_or_spawner_codex_mission"}:
        spawner = route_by_key.get("spark_spawner") or {}
        score = 76 if recommended_route == "writable_spawner_codex_mission" else 64
        evidence.append(_route_health_evidence(spawner, label="Spawner"))
        if bool(spawner.get("available")):
            score += 10
        else:
            score -= 24
            risks.append("spawner_not_available")
        if str(spawner.get("status") or "") in {"degraded", "unavailable", "missing", "planned"}:
            score -= 12
            risks.append(f"spawner_status_{spawner.get('status') or 'unknown'}")
        if runner.get("writable") is False:
            evidence.append("Current runner is read-only, so routing away is justified.")
        if runner.get("writable") is None:
            risks.append("runner_unknown_requires_preflight")
        return _report(recommended_route=recommended_route, score=score, evidence=evidence, risks=risks)
    if recommended_route == "ask_for_access_or_route":
        return RouteConfidenceReport(
            recommended_route=recommended_route,
            confidence="blocked",
            score=20,
            evidence=evidence,
            risks=[*risks, "local_workspace_access_not_confirmed"],
        )
    return RouteConfidenceReport(
        recommended_route=recommended_route,
        confidence="unknown",
        score=0,
        evidence=evidence,
        risks=[*risks, "unknown_recommended_route"],
    )


def _report(*, recommended_route: str, score: int, evidence: list[str], risks: list[str]) -> RouteConfidenceReport:
    bounded_score = max(0, min(100, score))
    return RouteConfidenceReport(
        recommended_route=recommended_route,
        confidence=_confidence_label(bounded_score),
        score=bounded_score,
        evidence=_dedupe_text(evidence),
        risks=_dedupe_text(risks),
    )


def _base_evidence(*, task_fit: dict[str, Any], runner: dict[str, Any], access: dict[str, Any]) -> list[str]:
    lines = [str(item) for item in list(task_fit.get("why") or []) if str(item or "").strip()]
    access_label = str(access.get("label") or "").strip()
    runner_label = str(runner.get("label") or "").strip()
    if access_label:
        lines.append(f"Access: {access_label}.")
    if runner_label:
        lines.append(f"Runner: {runner_label}.")
    return lines


def _route_health_evidence(route: dict[str, Any], *, label: str) -> str:
    status = str(route.get("status") or "unknown").strip() or "unknown"
    available = "available" if bool(route.get("available")) else "not available"
    last_success = str(route.get("last_success_at") or "").strip()
    suffix = f", last success {last_success}" if last_success else ""
    return f"{label} route is {status} and {available}{suffix}."


def _confidence_label(score: int) -> RouteConfidenceLabel:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    if score >= 30:
        return "low"
    return "blocked"


def _dedupe_text(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        output.append(text)
        seen.add(text)
    return output
