from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from spark_intelligence.config.loader import ConfigManager


VALID_INBOX_STATUSES = {"candidate", "verified", "all"}
PROMOTION_GATE_NAMES = (
    "schema_gate",
    "lineage_gate",
    "complexity_gate",
    "memory_hygiene_gate",
    "autonomy_gate",
)
NON_OVERRIDE_RULES = [
    "candidate_notes_are_not_runtime_truth",
    "wiki_is_supporting_not_authoritative",
    "live_status_traces_tests_and_current_state_outrank_wiki_notes",
    "mutable_user_facts_must_come_from_governed_current_state_memory",
    "conversational_residue_is_not_promotion_evidence",
]


@dataclass(frozen=True)
class LlmWikiCandidateInboxResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        notes = [note for note in self.payload.get("notes") or [] if isinstance(note, dict)]
        lines = [
            "Spark LLM wiki candidate inbox",
            f"- output_dir: {self.output_dir}",
            f"- status_filter: {self.payload.get('status_filter') or 'candidate'}",
            f"- candidates: {self.payload.get('candidate_count', 0)}",
            f"- verified: {self.payload.get('verified_count', 0)}",
            f"- returned: {self.payload.get('returned_count', 0)}",
            f"- authority: {self.payload.get('authority') or 'supporting_not_authoritative'}",
        ]
        if notes:
            lines.append("Notes")
            for note in notes[:12]:
                title = str(note.get("title") or note.get("path") or "").strip()
                path = str(note.get("path") or "").strip()
                status = str(note.get("promotion_status") or "candidate").strip()
                age_days = note.get("age_days")
                evidence_count = len(note.get("evidence_refs") or [])
                source_count = len(note.get("source_refs") or [])
                lines.append(
                    f"- {path}: {title} "
                    f"status={status} age_days={age_days} evidence={evidence_count} sources={source_count}"
                )
        warnings = [str(item) for item in self.payload.get("warnings") or [] if str(item).strip()]
        if warnings:
            lines.append("Warnings")
            lines.extend(f"- {item}" for item in warnings[:8])
        lines.append("Rule: inbox notes are review candidates, not live Spark truth.")
        return "\n".join(lines)


def build_llm_wiki_candidate_inbox(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    status: str = "candidate",
    limit: int = 40,
) -> LlmWikiCandidateInboxResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    status_filter = _normalize_status(status)
    improvements_dir = root / "improvements"
    warnings: list[str] = []
    if not root.exists():
        warnings.append("wiki_root_missing")
    if root.exists() and not improvements_dir.exists():
        warnings.append("improvements_dir_missing")

    markdown_files = sorted(
        (path for path in improvements_dir.glob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if improvements_dir.exists() else []
    all_notes = [_note_payload(root, path) for path in markdown_files]
    filtered_notes = [
        note
        for note in all_notes
        if status_filter == "all" or str(note.get("promotion_status") or "") == status_filter
    ]
    returned_notes = filtered_notes[: max(0, int(limit or 0))]
    payload = {
        "output_dir": str(root),
        "checked_at": _utc_timestamp(),
        "exists": root.exists(),
        "improvements_dir_exists": improvements_dir.exists(),
        "status_filter": status_filter,
        "total_improvement_count": len(all_notes),
        "candidate_count": sum(1 for note in all_notes if note.get("promotion_status") == "candidate"),
        "verified_count": sum(1 for note in all_notes if note.get("promotion_status") == "verified"),
        "returned_count": len(returned_notes),
        "notes": returned_notes,
        "authority": "supporting_not_authoritative",
        "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
        "non_override_rules": NON_OVERRIDE_RULES,
        "warnings": warnings,
    }
    return LlmWikiCandidateInboxResult(output_dir=root, payload=payload)


def _note_payload(root: Path, path: Path) -> dict[str, Any]:
    relative_path = path.relative_to(root).as_posix()
    content = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _frontmatter(content)
    promotion_status = _normalize_note_status(frontmatter.get("promotion_status") or frontmatter.get("status"))
    evidence_refs = _list_value(frontmatter.get("evidence_refs"))
    source_refs = _list_value(frontmatter.get("source_refs"))
    source_packet_refs = _list_value(frontmatter.get("source_packet_refs"))
    probe_refs = _list_value(frontmatter.get("probe_refs"))
    request_id = str(frontmatter.get("request_id") or "").strip()
    route_decision = str(frontmatter.get("route_decision") or "").strip()
    proposal_kind = str(frontmatter.get("proposal_kind") or "none").strip()
    proposal = _proposal_payload(
        proposal_kind=proposal_kind,
        weak_spot=str(frontmatter.get("weak_spot") or "").strip(),
        hypothesis=str(frontmatter.get("hypothesis") or "").strip(),
        expected_eval=str(frontmatter.get("expected_eval") or "").strip(),
        rollback_condition=str(frontmatter.get("rollback_condition") or "").strip(),
        evidence_refs=evidence_refs,
        probe_refs=probe_refs,
        next_probe=_section_bullet(content, "Next Probe"),
        recorded_missing_fields=_list_value(frontmatter.get("proposal_missing_fields")),
        recorded_promotion_ready=frontmatter.get("proposal_promotion_ready"),
    )
    gate_ledger = _gate_ledger_payload(frontmatter=frontmatter)
    eval_coverage = _eval_coverage_payload(
        status=frontmatter.get("eval_coverage_status"),
        eval_refs=_list_value(frontmatter.get("eval_refs")),
        evidence_refs=evidence_refs,
        source_refs=source_refs,
        probe_refs=probe_refs,
    )
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0)
    created_at = str(frontmatter.get("date_created") or "").strip()
    return {
        "path": relative_path,
        "title": str(frontmatter.get("title") or _heading_title(content) or path.stem).strip(),
        "summary": str(frontmatter.get("summary") or "").strip(),
        "type": str(frontmatter.get("type") or "").strip(),
        "status": str(frontmatter.get("status") or promotion_status).strip(),
        "promotion_status": promotion_status,
        "review_state": "candidate_needs_probe" if promotion_status == "candidate" else "verified_supporting_only",
        "authority": str(frontmatter.get("authority") or "supporting_not_authoritative").strip(),
        "freshness": str(frontmatter.get("freshness") or "revalidatable_improvement_note").strip(),
        "source_class": str(frontmatter.get("source_class") or "").strip(),
        "evidence_refs": evidence_refs,
        "source_refs": source_refs,
        "request_id": request_id,
        "route_decision": route_decision,
        "source_packet_refs": source_packet_refs,
        "probe_refs": probe_refs,
        "proposal_kind": proposal_kind,
        "proposal": proposal,
        "proposal_gate": proposal["gate"],
        "gate_ledger": gate_ledger,
        "eval_coverage": eval_coverage,
        "eval_coverage_status": eval_coverage["status"],
        "eval_refs": eval_coverage["source_refs"],
        "lineage": {
            "evidence_ref_count": len(evidence_refs),
            "source_ref_count": len(source_refs),
            "source_packet_ref_count": len(source_packet_refs),
            "probe_ref_count": len(probe_refs),
            "request_id": request_id,
            "route_decision": route_decision,
            "has_source_or_evidence": bool(evidence_refs or source_refs),
            "has_route_trace_lineage": bool(request_id or route_decision or source_packet_refs or probe_refs),
        },
        "next_probe": _section_bullet(content, "Next Probe"),
        "invalidation_trigger": _section_bullet(content, "Invalidation Trigger"),
        "created_at": created_at,
        "modified_at": modified_at.isoformat(),
        "age_days": _age_days(created_at=created_at, modified_at=modified_at),
        "can_override_runtime_truth": False,
    }


def _frontmatter(content: str) -> dict[str, Any]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    block: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block.append(line)
    try:
        parsed = yaml.safe_load("\n".join(block)) or {}
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _heading_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_status(value: str) -> str:
    status = str(value or "candidate").strip().casefold()
    if status not in VALID_INBOX_STATUSES:
        raise ValueError("candidate inbox status must be one of: candidate, verified, all")
    return status


def _normalize_note_status(value: Any) -> str:
    status = str(value or "candidate").strip().casefold()
    return status if status in {"candidate", "verified"} else "candidate"


def _proposal_payload(
    *,
    proposal_kind: str,
    weak_spot: str,
    hypothesis: str,
    expected_eval: str,
    rollback_condition: str,
    evidence_refs: list[str],
    probe_refs: list[str],
    next_probe: str,
    recorded_missing_fields: list[str],
    recorded_promotion_ready: Any,
) -> dict[str, Any]:
    is_proposal = proposal_kind == "self_improvement"
    gate_checks = {
        "weak_spot": bool(weak_spot),
        "hypothesis": bool(hypothesis),
        "evidence": bool(evidence_refs),
        "probe": bool(probe_refs or next_probe),
        "rollback": bool(rollback_condition),
        "expected_eval": bool(expected_eval),
    }
    missing_fields = recorded_missing_fields if is_proposal and recorded_missing_fields else [
        field for field, passed in gate_checks.items() if not passed
    ]
    promotion_ready = (
        bool(recorded_promotion_ready)
        if is_proposal and recorded_promotion_ready is not None
        else bool(is_proposal and not missing_fields)
    )
    return {
        "is_proposal": is_proposal,
        "proposal_kind": proposal_kind if is_proposal else "none",
        "weak_spot": weak_spot,
        "hypothesis": hypothesis,
        "expected_eval": expected_eval,
        "rollback_condition": rollback_condition,
        "gate": {
            "checks": gate_checks,
            "missing_fields": missing_fields if is_proposal else [],
            "promotion_ready": promotion_ready,
            "authority_boundary": "proposal_is_not_runtime_mutation_until_probe_eval_and_rollback_pass",
        },
    }


def _gate_ledger_payload(*, frontmatter: dict[str, Any]) -> dict[str, Any]:
    gates = {
        gate_name: {
            "status": _normalize_gate_status(frontmatter.get(gate_name)),
            "reason": "frontmatter_gate_status",
        }
        for gate_name in PROMOTION_GATE_NAMES
    }
    failed_gates = _list_value(frontmatter.get("failed_gates")) or [
        name for name, gate in gates.items() if gate["status"] == "fail"
    ]
    warning_gates = _list_value(frontmatter.get("warning_gates")) or [
        name for name, gate in gates.items() if gate["status"] == "warn"
    ]
    return {
        "gates": gates,
        "failed_gates": failed_gates,
        "warning_gates": warning_gates,
        "verified_promotion_allowed": not failed_gates,
        "authority_boundary": "promotion_gate_ledger_is_review_evidence_not_runtime_truth",
    }


def _eval_coverage_payload(
    *,
    status: Any,
    eval_refs: list[str],
    evidence_refs: list[str],
    source_refs: list[str],
    probe_refs: list[str],
) -> dict[str, Any]:
    normalized_status = _normalize_eval_coverage_status(status)
    eval_like_refs = [
        ref
        for ref in [*eval_refs, *evidence_refs, *probe_refs]
        if _looks_like_eval_ref(ref)
    ]
    if not status:
        if eval_like_refs:
            normalized_status = "covered"
        elif evidence_refs or source_refs or probe_refs:
            normalized_status = "observed"
    source_refs_for_status = list(dict.fromkeys([*eval_refs, *eval_like_refs]))
    if normalized_status == "observed" and not source_refs_for_status:
        source_refs_for_status = list(dict.fromkeys([*evidence_refs, *probe_refs, *source_refs]))[:6]
    return {
        "status": normalized_status,
        "source_refs": source_refs_for_status[:10],
        "authority_boundary": "eval_coverage_is_evidence_status_not_runtime_truth",
    }


def _looks_like_eval_ref(value: str) -> bool:
    text = str(value or "").casefold()
    return any(token in text for token in ("pytest", "test", "eval", "coverage", "regression", "smoke"))


def _normalize_eval_coverage_status(value: Any) -> str:
    status = str(value or "missing").strip().casefold()
    return status if status in {"missing", "observed", "covered"} else "missing"


def _normalize_gate_status(value: Any) -> str:
    status = str(value or "pass").strip().casefold()
    return status if status in {"pass", "warn", "fail"} else "pass"


def _section_bullet(content: str, heading: str) -> str:
    lines = content.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"## {heading}":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            return stripped[2:].strip()
    return ""


def _age_days(*, created_at: str, modified_at: datetime) -> int:
    created = _parse_created_at(created_at)
    base = created or modified_at
    return max(0, (datetime.now(timezone.utc) - base).days)


def _parse_created_at(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
