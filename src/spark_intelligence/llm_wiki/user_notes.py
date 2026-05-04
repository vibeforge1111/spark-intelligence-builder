from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.promote import (
    _clean_list,
    _compact,
    _frontmatter_list,
    _frontmatter_string,
    _slugify,
    _utc_timestamp,
)


VALID_USER_NOTE_STATUSES = {"candidate", "verified"}


@dataclass(frozen=True)
class LlmWikiUserNotePromotionResult:
    output_dir: Path
    relative_path: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = [
            "Spark LLM wiki user note promotion",
            f"- status: {self.payload.get('promotion_status') or 'candidate'}",
            f"- human_id: {self.payload.get('human_id') or ''}",
            f"- title: {self.payload.get('title') or ''}",
            f"- path: {self.relative_path}",
            f"- authority: {self.payload.get('authority') or 'supporting_not_authoritative'}",
            f"- scope_kind: {self.payload.get('scope_kind') or 'user_specific'}",
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
        lines.append("Rule: this is user-specific context, not global Spark doctrine.")
        return "\n".join(lines)


def promote_llm_wiki_user_note(
    *,
    config_manager: ConfigManager,
    human_id: str,
    title: str,
    summary: str = "",
    consent_ref: str = "",
    output_dir: str | Path | None = None,
    promotion_status: str = "candidate",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
    source_refs: list[str] | tuple[str, ...] | None = None,
    next_probe: str = "",
    invalidation_trigger: str = "",
    overwrite: bool = False,
) -> LlmWikiUserNotePromotionResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    normalized_human_id = _compact(human_id)
    normalized_title = _compact(title)
    normalized_summary = _compact(summary) or normalized_title
    normalized_consent_ref = _compact(consent_ref)
    status = _normalize_status(promotion_status)
    evidence = _clean_list(evidence_refs)
    sources = _clean_list(source_refs)
    if not normalized_human_id:
        raise ValueError("human_id is required for a user-specific wiki note.")
    if not normalized_title:
        raise ValueError("title is required for a user-specific wiki note.")
    if not normalized_summary:
        raise ValueError("summary is required for a user-specific wiki note.")
    if not normalized_consent_ref:
        raise ValueError("consent_ref is required before writing user-specific wiki context.")
    if not evidence and not sources:
        raise ValueError("at least one --evidence-ref or --source is required to avoid residue promotion.")

    generated_at = _utc_timestamp()
    relative_path = _relative_user_note_path(
        human_id=normalized_human_id,
        title=normalized_title,
        generated_at=generated_at,
        root=root,
        overwrite=overwrite,
    )
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"user-specific wiki note already exists: {relative_path}")

    warnings: list[str] = []
    if status == "verified" and len(evidence) == 0:
        warnings.append("verified_user_note_without_explicit_evidence_ref")
    if status == "candidate":
        warnings.append("candidate_user_note_requires_consent_review_before_reuse")

    content = _render_user_note(
        human_id=normalized_human_id,
        title=normalized_title,
        summary=normalized_summary,
        promotion_status=status,
        consent_ref=normalized_consent_ref,
        generated_at=generated_at,
        evidence_refs=evidence,
        source_refs=sources,
        next_probe=_compact(next_probe)
        or "Confirm the user still wants this context reused before relying on it in a new situation.",
        invalidation_trigger=_compact(invalidation_trigger)
        or "Invalidate or downgrade if governed current-state memory, explicit user correction, or newer evidence contradicts it.",
        warnings=warnings,
    )
    path.write_text(content, encoding="utf-8")
    payload = {
        "output_dir": str(root),
        "relative_path": relative_path,
        "path": str(path),
        "title": normalized_title,
        "summary": normalized_summary,
        "human_id": normalized_human_id,
        "consent_ref": normalized_consent_ref,
        "promotion_status": status,
        "authority": "supporting_not_authoritative",
        "owner_system": "spark-intelligence-builder",
        "wiki_family": "user_context_candidate",
        "scope_kind": "user_specific",
        "source_of_truth": "governed_user_memory_or_explicit_consent",
        "freshness": "revalidatable_user_context",
        "source_class": f"spark_llm_wiki_user_note_{status}",
        "evidence_refs": evidence,
        "source_refs": sources,
        "created_at": generated_at,
        "warnings": warnings,
        "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
        "can_override_global_doctrine": False,
        "can_override_current_state_memory": False,
    }
    return LlmWikiUserNotePromotionResult(output_dir=root, relative_path=relative_path, payload=payload)


def _render_user_note(
    *,
    human_id: str,
    title: str,
    summary: str,
    promotion_status: str,
    consent_ref: str,
    generated_at: str,
    evidence_refs: list[str],
    source_refs: list[str],
    next_probe: str,
    invalidation_trigger: str,
    warnings: list[str],
) -> str:
    tags = ["spark-wiki", "user-context", "self-awareness", promotion_status]
    source_class = f"spark_llm_wiki_user_note_{promotion_status}"
    body = [
        f"# {title}",
        "",
        "## User-Specific Context",
        summary,
        "",
        "## Boundary",
        "- This is user-specific context, not global Spark doctrine.",
        "- It supports personalization only inside the scoped human lane.",
        "- It does not override governed current-state memory for mutable user facts.",
        "- It does not override live status, route traces, tests, or current runtime state.",
        f"- consent_ref: `{consent_ref}`",
        f"- promotion_status: `{promotion_status}`",
        "",
        "## Evidence Refs",
        *[f"- {item}" for item in evidence_refs],
        "",
        "## Source Refs",
        *[f"- {item}" for item in source_refs],
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
    return "\n".join(
        [
            "---",
            f'title: "{_frontmatter_string(title)}"',
            f'date_created: "{generated_at[:10]}"',
            f'date_modified: "{generated_at[:10]}"',
            f'summary: "{_frontmatter_string(summary)}"',
            f"tags: [{', '.join(tags)}]",
            "type: llm_wiki_user_note",
            f"status: {promotion_status}",
            f"promotion_status: {promotion_status}",
            "authority: supporting_not_authoritative",
            "owner_system: spark-intelligence-builder",
            "wiki_family: user_context_candidate",
            "scope_kind: user_specific",
            "source_of_truth: governed_user_memory_or_explicit_consent",
            "freshness: revalidatable_user_context",
            f"source_class: {source_class}",
            f'human_id: "{_frontmatter_string(human_id)}"',
            f'consent_ref: "{_frontmatter_string(consent_ref)}"',
            f"evidence_refs: [{', '.join(_frontmatter_list(evidence_refs))}]",
            f"source_refs: [{', '.join(_frontmatter_list(source_refs))}]",
            "---",
            "",
            "\n".join(line.rstrip() for line in body),
            "",
        ]
    )


def _relative_user_note_path(*, human_id: str, title: str, generated_at: str, root: Path, overwrite: bool) -> str:
    human_slug = _human_slug(human_id)
    title_slug = _slugify(title)
    date_prefix = generated_at[:10]
    base = f"users/{human_slug}/candidate-notes/{date_prefix}-{title_slug}.md"
    if overwrite or not (root / base).exists():
        return base
    for index in range(2, 100):
        candidate = f"users/{human_slug}/candidate-notes/{date_prefix}-{title_slug}-{index}.md"
        if not (root / candidate).exists():
            return candidate
    raise FileExistsError(f"too many colliding user-specific wiki notes for {base}")


def _human_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "human")[:72].strip("-") or "human"


def _normalize_status(value: str) -> str:
    status = str(value or "candidate").strip().casefold()
    if status not in VALID_USER_NOTE_STATUSES:
        raise ValueError("user note promotion status must be one of: candidate, verified")
    return status
