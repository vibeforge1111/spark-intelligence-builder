from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.inbox import NON_OVERRIDE_RULES, build_llm_wiki_candidate_inbox


@dataclass(frozen=True)
class LlmWikiCandidateScanResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        findings = [item for item in self.payload.get("findings") or [] if isinstance(item, dict)]
        lines = [
            "Spark LLM wiki candidate contradiction scan",
            f"- output_dir: {self.output_dir}",
            f"- scanned: {self.payload.get('scanned_count', 0)}",
            f"- keep: {self.payload.get('recommendation_counts', {}).get('keep', 0)}",
            f"- rewrite: {self.payload.get('recommendation_counts', {}).get('rewrite', 0)}",
            f"- drop: {self.payload.get('recommendation_counts', {}).get('drop', 0)}",
            f"- authority: {self.payload.get('authority') or 'supporting_not_authoritative'}",
        ]
        for finding in findings[:12]:
            path = str(finding.get("path") or "").strip()
            recommendation = str(finding.get("recommendation") or "keep").strip()
            issue_codes = [
                str(issue.get("code") or "").strip()
                for issue in finding.get("issues") or []
                if isinstance(issue, dict) and str(issue.get("code") or "").strip()
            ]
            lines.append(f"- {path}: {recommendation} issues={', '.join(issue_codes) if issue_codes else 'none'}")
        lines.append("Rule: scan findings are review guidance, not automatic promotion.")
        return "\n".join(lines)


def build_llm_wiki_candidate_scan(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    status: str = "all",
    limit: int = 80,
) -> LlmWikiCandidateScanResult:
    inbox = build_llm_wiki_candidate_inbox(
        config_manager=config_manager,
        output_dir=output_dir,
        status=status,
        limit=limit,
    )
    notes = [note for note in inbox.payload.get("notes") or [] if isinstance(note, dict)]
    findings = [_scan_note(note) for note in notes]
    recommendation_counts = {"keep": 0, "rewrite": 0, "drop": 0}
    issue_counts: dict[str, int] = {}
    for finding in findings:
        recommendation = str(finding.get("recommendation") or "keep")
        recommendation_counts[recommendation] = recommendation_counts.get(recommendation, 0) + 1
        for issue in finding.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "").strip()
            if code:
                issue_counts[code] = issue_counts.get(code, 0) + 1

    payload = {
        "output_dir": str(inbox.output_dir),
        "checked_at": inbox.payload.get("checked_at"),
        "exists": inbox.payload.get("exists"),
        "status_filter": inbox.payload.get("status_filter"),
        "scanned_count": len(findings),
        "findings": findings,
        "recommendation_counts": recommendation_counts,
        "issue_counts": dict(sorted(issue_counts.items())),
        "authority": "supporting_not_authoritative",
        "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
        "non_override_rules": NON_OVERRIDE_RULES,
        "live_evidence_boundary": {
            "wiki": "supporting_not_authoritative",
            "mutable_user_facts": "current_state_memory_outranks_wiki",
            "graph_sidecar": "advisory_until_evals_pass",
            "promotion": "requires_source_evidence_probe_and_review",
        },
        "warnings": inbox.payload.get("warnings") or [],
    }
    return LlmWikiCandidateScanResult(output_dir=inbox.output_dir, payload=payload)


def _scan_note(note: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    authority = str(note.get("authority") or "").strip()
    promotion_status = str(note.get("promotion_status") or "candidate").strip()
    evidence_refs = [str(item).strip() for item in note.get("evidence_refs") or [] if str(item).strip()]
    source_refs = [str(item).strip() for item in note.get("source_refs") or [] if str(item).strip()]
    title = str(note.get("title") or "").strip()
    summary = str(note.get("summary") or "").strip()
    combined = f"{title}\n{summary}".casefold()

    if authority != "supporting_not_authoritative":
        issues.append(_issue("authority_boundary_violation", "critical", "Wiki improvement notes must remain supporting_not_authoritative."))
    if bool(note.get("can_override_runtime_truth")):
        issues.append(_issue("runtime_truth_override_claim", "critical", "No wiki note can override live runtime truth."))
    if not evidence_refs and not source_refs:
        issues.append(_issue("missing_lineage", "critical", "Candidate notes need at least one source or evidence ref."))
    if promotion_status == "verified" and not evidence_refs:
        issues.append(_issue("verified_without_explicit_evidence", "critical", "Verified notes need explicit test, trace, command, PR, or run evidence."))
    if _looks_like_conversation_residue(source_refs) and not evidence_refs:
        issues.append(_issue("conversation_residue_without_evidence", "critical", "Raw conversation source alone is not promotion evidence."))
    if _looks_like_mutable_user_fact(combined):
        issues.append(_issue("mutable_user_fact_requires_user_memory_lane", "critical", "Mutable user facts belong in governed user current-state memory, not global Spark doctrine."))
    if _looks_like_live_health_claim(combined) and not _has_live_probe_ref(evidence_refs):
        issues.append(_issue("live_health_claim_needs_probe", "warning", "Live health claims need a current trace, test, status, or command ref."))
    if promotion_status == "candidate" and int(note.get("age_days") or 0) >= 30:
        issues.append(_issue("stale_candidate_needs_review", "warning", "Old candidates should be revalidated, rewritten, or dropped."))

    recommendation = _recommendation(issues)
    return {
        "path": note.get("path"),
        "title": title,
        "promotion_status": promotion_status,
        "authority": authority,
        "recommendation": recommendation,
        "issues": issues,
        "evidence_refs": evidence_refs,
        "source_refs": source_refs,
        "age_days": note.get("age_days"),
        "can_override_runtime_truth": False,
    }


def _issue(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


def _recommendation(issues: list[dict[str, str]]) -> str:
    if not issues:
        return "keep"
    critical_codes = {str(issue.get("code") or "") for issue in issues if issue.get("severity") == "critical"}
    if critical_codes & {"authority_boundary_violation", "runtime_truth_override_claim"}:
        return "drop"
    return "rewrite"


def _looks_like_conversation_residue(source_refs: list[str]) -> bool:
    residue_patterns = ("conversation", "chat_log", "raw_turn", "transcript", "telegram_update", "message:")
    return any(any(pattern in source.casefold() for pattern in residue_patterns) for source in source_refs)


def _looks_like_mutable_user_fact(text: str) -> bool:
    return bool(
        re.search(
            r"\b(user|human|they|i)\s+(prefers|prefer|likes|like|wants|want|works|is|am)\b|\bmy preference\b",
            text,
        )
    )


def _looks_like_live_health_claim(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "all chips work",
            "provider is ready",
            "gateway is ready",
            "system is healthy",
            "route is live",
            "capability is verified",
        )
    )


def _has_live_probe_ref(evidence_refs: list[str]) -> bool:
    probe_terms = ("pytest", "trace", "status", "doctor", "smoke", "commit:", "run:", "gateway")
    return any(any(term in ref.casefold() for term in probe_terms) for ref in evidence_refs)
