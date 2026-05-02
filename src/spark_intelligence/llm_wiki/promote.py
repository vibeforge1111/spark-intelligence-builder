from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


VALID_PROMOTION_STATUSES = {"candidate", "verified"}
VALID_GATE_STATUSES = {"pass", "warn", "fail"}
PROMOTION_GATE_NAMES = (
    "schema_gate",
    "lineage_gate",
    "complexity_gate",
    "memory_hygiene_gate",
    "autonomy_gate",
)


@dataclass(frozen=True)
class LlmWikiImprovementPromotionResult:
    output_dir: Path
    relative_path: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = [
            "Spark LLM wiki improvement promotion",
            f"- status: {self.payload.get('promotion_status') or 'candidate'}",
            f"- title: {self.payload.get('title') or ''}",
            f"- path: {self.relative_path}",
            f"- authority: {self.payload.get('authority') or 'supporting_not_authoritative'}",
        ]
        evidence_refs = [str(item) for item in self.payload.get("evidence_refs") or [] if str(item).strip()]
        source_refs = [str(item) for item in self.payload.get("source_refs") or [] if str(item).strip()]
        if evidence_refs:
            lines.append(f"- evidence_refs: {len(evidence_refs)}")
        if source_refs:
            lines.append(f"- source_refs: {len(source_refs)}")
        proposal_gate = self.payload.get("proposal_gate") if isinstance(self.payload.get("proposal_gate"), dict) else {}
        if self.payload.get("proposal_kind") == "self_improvement":
            lines.append(f"- proposal_ready: {'yes' if proposal_gate.get('promotion_ready') else 'no'}")
        gate_ledger = self.payload.get("gate_ledger") if isinstance(self.payload.get("gate_ledger"), dict) else {}
        failed_gates = [str(item) for item in gate_ledger.get("failed_gates") or [] if str(item).strip()]
        if gate_ledger:
            lines.append(f"- gate_status: {'blocked' if failed_gates else 'clear'}")
        warnings = [str(item) for item in self.payload.get("warnings") or [] if str(item).strip()]
        if warnings:
            lines.append("Warnings")
            lines.extend(f"- {item}" for item in warnings[:6])
        lines.append("Rule: this note is retrievable project knowledge, not live runtime truth.")
        return "\n".join(lines)


def promote_llm_wiki_improvement(
    *,
    config_manager: ConfigManager,
    title: str,
    summary: str = "",
    output_dir: str | Path | None = None,
    promotion_status: str = "candidate",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    source_refs: list[str] | tuple[str, ...] | None = None,
    request_id: str = "",
    route_decision: str = "",
    source_packet_refs: list[str] | tuple[str, ...] | None = None,
    probe_refs: list[str] | tuple[str, ...] | None = None,
    proposal: bool = False,
    weak_spot: str = "",
    hypothesis: str = "",
    expected_eval: str = "",
    rollback_condition: str = "",
    schema_gate: str = "",
    lineage_gate: str = "",
    complexity_gate: str = "",
    memory_hygiene_gate: str = "",
    autonomy_gate: str = "",
    next_probe: str = "",
    invalidation_trigger: str = "",
    overwrite: bool = False,
) -> LlmWikiImprovementPromotionResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    normalized_title = _compact(title)
    normalized_summary = _compact(summary) or normalized_title
    status = _normalize_status(promotion_status)
    evidence = _clean_list(evidence_refs)
    sources = _clean_list(source_refs)
    source_packets = _clean_list(source_packet_refs)
    probes = _clean_list(probe_refs)
    lineage_request_id = _compact(request_id)
    lineage_route_decision = _compact(route_decision)
    proposal_packet = _proposal_packet(
        proposal=proposal,
        weak_spot=weak_spot,
        hypothesis=hypothesis,
        evidence_refs=evidence,
        probe_refs=probes,
        next_probe=next_probe,
        rollback_condition=rollback_condition,
        expected_eval=expected_eval,
    )
    gate_ledger = _gate_ledger(
        evidence_refs=evidence,
        source_refs=sources,
        schema_gate=schema_gate,
        lineage_gate=lineage_gate,
        complexity_gate=complexity_gate,
        memory_hygiene_gate=memory_hygiene_gate,
        autonomy_gate=autonomy_gate,
    )
    if not normalized_title:
        raise ValueError("title is required for a wiki improvement note.")
    if not normalized_summary:
        raise ValueError("summary is required for a wiki improvement note.")
    if not evidence and not sources:
        raise ValueError("at least one --evidence-ref or --source is required to avoid residue promotion.")
    if status == "verified" and proposal_packet["is_proposal"] and not proposal_packet["gate"]["promotion_ready"]:
        missing = ", ".join(proposal_packet["gate"]["missing_fields"])
        raise ValueError(f"verified self-improvement proposals require falsifiable fields: {missing}")
    if status == "verified" and gate_ledger["failed_gates"]:
        failed = ", ".join(gate_ledger["failed_gates"])
        raise ValueError(f"verified improvement notes cannot have failing promotion gates: {failed}")

    generated_at = _utc_timestamp()
    relative_path = _relative_note_path(title=normalized_title, generated_at=generated_at, root=root, overwrite=overwrite)
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"wiki improvement note already exists: {relative_path}")

    warnings: list[str] = []
    if status == "verified" and len(evidence) == 0:
        warnings.append("verified_status_without_explicit_evidence_ref")
    if status == "candidate":
        warnings.append("candidate_status_requires_probe_before_runtime_truth")
    if proposal_packet["is_proposal"] and not proposal_packet["gate"]["promotion_ready"]:
        warnings.append("proposal_missing_falsifiable_fields")
    if gate_ledger["failed_gates"]:
        warnings.append("promotion_gate_failure_blocks_verified_status")

    content = _render_improvement_note(
        title=normalized_title,
        summary=normalized_summary,
        promotion_status=status,
        generated_at=generated_at,
        evidence_refs=evidence,
        source_refs=sources,
        request_id=lineage_request_id,
        route_decision=lineage_route_decision,
        source_packet_refs=source_packets,
        probe_refs=probes,
        proposal_packet=proposal_packet,
        gate_ledger=gate_ledger,
        next_probe=_compact(next_probe) or "Run the relevant live probe, test, or trace check before treating this as current truth.",
        invalidation_trigger=_compact(invalidation_trigger) or "Invalidate or downgrade if a newer live trace, test, or source contradicts this note.",
        warnings=warnings,
    )
    path.write_text(content, encoding="utf-8")
    payload = {
        "output_dir": str(root),
        "relative_path": relative_path,
        "path": str(path),
        "title": normalized_title,
        "summary": normalized_summary,
        "promotion_status": status,
        "authority": "supporting_not_authoritative",
        "source_class": f"spark_llm_wiki_improvement_{status}",
        "evidence_refs": evidence,
        "source_refs": sources,
        "request_id": lineage_request_id,
        "route_decision": lineage_route_decision,
        "source_packet_refs": source_packets,
        "probe_refs": probes,
        "proposal_kind": proposal_packet["proposal_kind"],
        "proposal": proposal_packet,
        "proposal_gate": proposal_packet["gate"],
        "gate_ledger": gate_ledger,
        "trace_lineage": {
            "request_id": lineage_request_id,
            "route_decision": lineage_route_decision,
            "source_packet_refs": source_packets,
            "probe_refs": probes,
        },
        "next_probe": _compact(next_probe) or "Run the relevant live probe, test, or trace check before treating this as current truth.",
        "invalidation_trigger": _compact(invalidation_trigger) or "Invalidate or downgrade if a newer live trace, test, or source contradicts this note.",
        "created_at": generated_at,
        "warnings": warnings,
        "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
    }
    return LlmWikiImprovementPromotionResult(output_dir=root, relative_path=relative_path, payload=payload)


def _render_improvement_note(
    *,
    title: str,
    summary: str,
    promotion_status: str,
    generated_at: str,
    evidence_refs: list[str],
    source_refs: list[str],
    request_id: str,
    route_decision: str,
    source_packet_refs: list[str],
    probe_refs: list[str],
    proposal_packet: dict[str, Any],
    gate_ledger: dict[str, Any],
    next_probe: str,
    invalidation_trigger: str,
    warnings: list[str],
) -> str:
    tags = ["spark-wiki", "improvement", "self-awareness", promotion_status]
    source_class = f"spark_llm_wiki_improvement_{promotion_status}"
    body = [
        f"# {title}",
        "",
        "## Learning",
        summary,
        "",
        "## Evidence Boundary",
        "- This is retrievable project knowledge for Spark agents.",
        "- It supports reasoning, but it does not override live status, route traces, tests, or current runtime state.",
        "- This note is not live runtime truth.",
        f"- promotion_status: `{promotion_status}`",
        "",
        "## Evidence Refs",
        *[f"- {item}" for item in evidence_refs],
        "",
        "## Source Refs",
        *[f"- {item}" for item in source_refs],
        "",
        "## Trace Lineage",
        f"- request_id: {request_id or 'unknown'}",
        f"- route_decision: {route_decision or 'unknown'}",
        "- source_packet_refs:",
        *[f"  - {item}" for item in source_packet_refs],
        "- probe_refs:",
        *[f"  - {item}" for item in probe_refs],
        "",
    ]
    if proposal_packet["is_proposal"]:
        body.extend(
            [
                "## Self-Improvement Proposal",
                f"- weak_spot: {proposal_packet['weak_spot'] or 'missing'}",
                f"- hypothesis: {proposal_packet['hypothesis'] or 'missing'}",
                f"- expected_eval: {proposal_packet['expected_eval'] or 'missing'}",
                f"- rollback_condition: {proposal_packet['rollback_condition'] or 'missing'}",
                f"- promotion_ready: {str(proposal_packet['gate']['promotion_ready']).lower()}",
                "- missing_fields:",
                *[f"  - {item}" for item in proposal_packet["gate"]["missing_fields"]],
                "",
            ]
        )
    body.extend(
        [
            "## Promotion Gate Ledger",
            *[
                f"- {gate_name}: {gate_ledger['gates'][gate_name]['status']} - {gate_ledger['gates'][gate_name]['reason']}"
                for gate_name in PROMOTION_GATE_NAMES
            ],
            f"- verified_promotion_allowed: {str(gate_ledger['verified_promotion_allowed']).lower()}",
            "",
        ]
    )
    body.extend(
        [
            "## Next Probe",
            f"- {next_probe}",
            "",
            "## Invalidation Trigger",
            f"- {invalidation_trigger}",
        ]
    )
    if warnings:
        body.extend(["", "## Warnings", *[f"- {item}" for item in warnings]])
    if not evidence_refs:
        body.insert(body.index("## Source Refs") - 1, "- No explicit evidence refs were supplied; keep this note candidate-only until tested.")
    if not source_refs:
        body.append("")
        body.append("## Source Gap")
        body.append("- No source refs were supplied beyond evidence refs; add one before broad reuse.")
    return "\n".join(
        [
            "---",
            f'title: "{_frontmatter_string(title)}"',
            f'date_created: "{generated_at[:10]}"',
            f'date_modified: "{generated_at[:10]}"',
            f'summary: "{_frontmatter_string(summary)}"',
            f"tags: [{', '.join(tags)}]",
            "type: llm_wiki_improvement",
            f"status: {promotion_status}",
            f"promotion_status: {promotion_status}",
            "authority: supporting_not_authoritative",
            "freshness: revalidatable_improvement_note",
            f"source_class: {source_class}",
            f"evidence_refs: [{', '.join(_frontmatter_list(evidence_refs))}]",
            f"source_refs: [{', '.join(_frontmatter_list(source_refs))}]",
            f'request_id: "{_frontmatter_string(request_id)}"',
            f'route_decision: "{_frontmatter_string(route_decision)}"',
            f"source_packet_refs: [{', '.join(_frontmatter_list(source_packet_refs))}]",
            f"probe_refs: [{', '.join(_frontmatter_list(probe_refs))}]",
            f"proposal_kind: {proposal_packet['proposal_kind'] or 'none'}",
            f'weak_spot: "{_frontmatter_string(proposal_packet["weak_spot"])}"',
            f'hypothesis: "{_frontmatter_string(proposal_packet["hypothesis"])}"',
            f'expected_eval: "{_frontmatter_string(proposal_packet["expected_eval"])}"',
            f'rollback_condition: "{_frontmatter_string(proposal_packet["rollback_condition"])}"',
            f"proposal_promotion_ready: {str(proposal_packet['gate']['promotion_ready']).lower()}",
            f"proposal_missing_fields: [{', '.join(_frontmatter_list(proposal_packet['gate']['missing_fields']))}]",
            f"schema_gate: {gate_ledger['gates']['schema_gate']['status']}",
            f"lineage_gate: {gate_ledger['gates']['lineage_gate']['status']}",
            f"complexity_gate: {gate_ledger['gates']['complexity_gate']['status']}",
            f"memory_hygiene_gate: {gate_ledger['gates']['memory_hygiene_gate']['status']}",
            f"autonomy_gate: {gate_ledger['gates']['autonomy_gate']['status']}",
            f"failed_gates: [{', '.join(_frontmatter_list(gate_ledger['failed_gates']))}]",
            f"warning_gates: [{', '.join(_frontmatter_list(gate_ledger['warning_gates']))}]",
            "---",
            "",
            "\n".join(line.rstrip() for line in body),
            "",
        ]
    )


def _relative_note_path(*, title: str, generated_at: str, root: Path, overwrite: bool) -> str:
    slug = _slugify(title)
    date_prefix = generated_at[:10]
    base = f"improvements/{date_prefix}-{slug}.md"
    if overwrite or not (root / base).exists():
        return base
    for index in range(2, 100):
        candidate = f"improvements/{date_prefix}-{slug}-{index}.md"
        if not (root / candidate).exists():
            return candidate
    raise FileExistsError(f"too many colliding wiki improvement notes for {base}")


def _normalize_status(value: str) -> str:
    status = str(value or "candidate").strip().casefold()
    if status not in VALID_PROMOTION_STATUSES:
        raise ValueError("promotion status must be one of: candidate, verified")
    return status


def _clean_list(value: list[str] | tuple[str, ...] | None) -> list[str]:
    if not value:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _compact(str(item))
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned[:20]


def _proposal_packet(
    *,
    proposal: bool,
    weak_spot: str,
    hypothesis: str,
    evidence_refs: list[str],
    probe_refs: list[str],
    next_probe: str,
    rollback_condition: str,
    expected_eval: str,
) -> dict[str, Any]:
    normalized = {
        "weak_spot": _compact(weak_spot),
        "hypothesis": _compact(hypothesis),
        "expected_eval": _compact(expected_eval),
        "rollback_condition": _compact(rollback_condition),
    }
    is_proposal = bool(proposal or any(normalized.values()))
    gate_checks = {
        "weak_spot": bool(normalized["weak_spot"]),
        "hypothesis": bool(normalized["hypothesis"]),
        "evidence": bool(evidence_refs),
        "probe": bool(probe_refs or _compact(next_probe)),
        "rollback": bool(normalized["rollback_condition"]),
        "expected_eval": bool(normalized["expected_eval"]),
    }
    missing_fields = [field for field, passed in gate_checks.items() if not passed]
    return {
        "is_proposal": is_proposal,
        "proposal_kind": "self_improvement" if is_proposal else "none",
        **normalized,
        "gate": {
            "checks": gate_checks,
            "missing_fields": missing_fields if is_proposal else [],
            "promotion_ready": bool(is_proposal and not missing_fields),
            "authority_boundary": "proposal_is_not_runtime_mutation_until_probe_eval_and_rollback_pass",
        },
    }


def _gate_ledger(
    *,
    evidence_refs: list[str],
    source_refs: list[str],
    schema_gate: str,
    lineage_gate: str,
    complexity_gate: str,
    memory_hygiene_gate: str,
    autonomy_gate: str,
) -> dict[str, Any]:
    gates = {
        "schema_gate": _gate_payload(schema_gate, default="pass", reason="note_schema_fields_rendered"),
        "lineage_gate": _gate_payload(
            lineage_gate,
            default="pass" if evidence_refs or source_refs else "fail",
            reason="source_or_evidence_refs_present" if evidence_refs or source_refs else "missing_source_or_evidence_refs",
        ),
        "complexity_gate": _gate_payload(complexity_gate, default="pass", reason="no_complexity_increase_recorded"),
        "memory_hygiene_gate": _gate_payload(
            memory_hygiene_gate,
            default="pass",
            reason="note_remains_supporting_not_authoritative_and_residue_bounded",
        ),
        "autonomy_gate": _gate_payload(autonomy_gate, default="pass", reason="no_autonomous_runtime_mutation"),
    }
    failed_gates = [name for name, gate in gates.items() if gate["status"] == "fail"]
    warning_gates = [name for name, gate in gates.items() if gate["status"] == "warn"]
    return {
        "gates": gates,
        "failed_gates": failed_gates,
        "warning_gates": warning_gates,
        "verified_promotion_allowed": not failed_gates,
        "authority_boundary": "promotion_gate_ledger_is_review_evidence_not_runtime_truth",
    }


def _gate_payload(value: str, *, default: str, reason: str) -> dict[str, str]:
    status = _normalize_gate_status(value or default)
    return {"status": status, "reason": reason}


def _normalize_gate_status(value: str) -> str:
    status = str(value or "pass").strip().casefold()
    if status not in VALID_GATE_STATUSES:
        raise ValueError("promotion gate status must be one of: pass, warn, fail")
    return status


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "spark-improvement")[:72].strip("-") or "spark-improvement"


def _frontmatter_string(value: str) -> str:
    return _compact(value).replace("\\", "\\\\").replace('"', '\\"')


def _frontmatter_list(values: list[str]) -> list[str]:
    return [f'"{_frontmatter_string(value)}"' for value in values]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
