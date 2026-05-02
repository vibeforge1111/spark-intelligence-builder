from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


VALID_PROMOTION_STATUSES = {"candidate", "verified"}


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
    if not normalized_title:
        raise ValueError("title is required for a wiki improvement note.")
    if not normalized_summary:
        raise ValueError("summary is required for a wiki improvement note.")
    if not evidence and not sources:
        raise ValueError("at least one --evidence-ref or --source is required to avoid residue promotion.")

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
        "## Next Probe",
        f"- {next_probe}",
        "",
        "## Invalidation Trigger",
        f"- {invalidation_trigger}",
    ]
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
