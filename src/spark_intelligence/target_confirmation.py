from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.local_project_index import build_local_project_index


_WRITE_TARGET_SIGNALS = {
    "add",
    "build",
    "change",
    "commit",
    "create",
    "edit",
    "fix",
    "implement",
    "install",
    "migrate",
    "move",
    "patch",
    "push",
    "refactor",
    "rename",
    "route",
    "ship",
    "update",
    "wire",
}


@dataclass(frozen=True)
class TargetRepoCandidate:
    repo_key: str
    label: str
    path: str
    score: int
    matched_terms: list[str]
    components: list[str]
    capabilities: list[str]
    owner_system: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_key": self.repo_key,
            "label": self.label,
            "path": self.path,
            "score": self.score,
            "matched_terms": self.matched_terms,
            "components": self.components,
            "capabilities": self.capabilities,
            "owner_system": self.owner_system,
        }


@dataclass(frozen=True)
class TargetRepoConfirmation:
    task: str
    applies: bool
    required: bool
    reason_code: str
    selected_repo_key: str | None
    selected_repo_path: str | None
    candidates: list[TargetRepoCandidate]
    blockers: list[str]
    next_actions: list[str]
    prompt: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "applies": self.applies,
            "required": self.required,
            "reason_code": self.reason_code,
            "selected_repo_key": self.selected_repo_key,
            "selected_repo_path": self.selected_repo_path,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "blockers": self.blockers,
            "next_actions": self.next_actions,
            "prompt": self.prompt,
        }


def evaluate_target_repo_confirmation(
    config_manager: ConfigManager,
    *,
    task: str,
    forced_repo_key: str | None = None,
    probe_git: bool = False,
) -> TargetRepoConfirmation:
    normalized_task = str(task or "").strip()
    applies = _looks_like_write_or_build_task(normalized_task)
    index = build_local_project_index(config_manager, probe_git=probe_git).to_payload()
    candidates = _rank_repo_candidates(index.get("records") or [], query=normalized_task)
    forced = _find_forced_candidate(index.get("records") or [], forced_repo_key)
    if forced is not None:
        return TargetRepoConfirmation(
            task=normalized_task,
            applies=applies,
            required=False,
            reason_code="forced_target_repo_confirmed",
            selected_repo_key=forced.repo_key,
            selected_repo_path=forced.path,
            candidates=[forced],
            blockers=[],
            next_actions=[f"Proceed with confirmed target repo `{forced.repo_key}`."],
            prompt="",
        )
    if not applies:
        selected = candidates[0] if candidates else None
        return TargetRepoConfirmation(
            task=normalized_task,
            applies=False,
            required=False,
            reason_code="not_build_or_file_writing_task",
            selected_repo_key=selected.repo_key if selected else None,
            selected_repo_path=selected.path if selected else None,
            candidates=candidates[:3],
            blockers=[],
            next_actions=[],
            prompt="",
        )
    if not candidates:
        return TargetRepoConfirmation(
            task=normalized_task,
            applies=True,
            required=True,
            reason_code="target_repo_missing",
            selected_repo_key=None,
            selected_repo_path=None,
            candidates=[],
            blockers=["Target repo is missing; confirm the repo before building or writing files."],
            next_actions=["Ask the operator which local repo/component this build should touch."],
            prompt="Which repo should this build/file-writing task target?",
        )
    top = candidates[0]
    close_candidates = [candidate for candidate in candidates if top.score - candidate.score <= 2]
    explicit_top_match = _has_explicit_repo_mention(normalized_task, top)
    if len(close_candidates) > 1 and not explicit_top_match:
        choices = ", ".join(candidate.repo_key for candidate in close_candidates[:4])
        return TargetRepoConfirmation(
            task=normalized_task,
            applies=True,
            required=True,
            reason_code="target_repo_ambiguous",
            selected_repo_key=top.repo_key,
            selected_repo_path=top.path,
            candidates=close_candidates[:4],
            blockers=[f"Target repo is ambiguous among: {choices}."],
            next_actions=["Ask the operator to confirm the target repo before dispatching file-writing work."],
            prompt=f"Confirm target repo before building: {choices}.",
        )
    if not explicit_top_match:
        return TargetRepoConfirmation(
            task=normalized_task,
            applies=True,
            required=True,
            reason_code="target_repo_unconfirmed",
            selected_repo_key=top.repo_key,
            selected_repo_path=top.path,
            candidates=candidates[:3],
            blockers=[f"Target repo `{top.repo_key}` was inferred, not explicitly confirmed."],
            next_actions=[f"Ask the operator to confirm `{top.repo_key}` before file-writing work begins."],
            prompt=f"Confirm target repo before building: `{top.repo_key}`?",
        )
    return TargetRepoConfirmation(
        task=normalized_task,
        applies=True,
        required=False,
        reason_code="target_repo_explicitly_confirmed",
        selected_repo_key=top.repo_key,
        selected_repo_path=top.path,
        candidates=[top],
        blockers=[],
        next_actions=[f"Proceed with explicitly named target repo `{top.repo_key}`."],
        prompt="",
    )


def _looks_like_write_or_build_task(task: str) -> bool:
    tokens = _tokens(task)
    if tokens & _WRITE_TARGET_SIGNALS:
        return True
    lowered = task.casefold()
    signals = (
        "file-writing",
        "make a route",
        "new route",
        "new page",
        "pull request",
        "run tests",
        "ship it",
    )
    return any(signal in lowered for signal in signals)


def _rank_repo_candidates(records: list[Any], *, query: str) -> list[TargetRepoCandidate]:
    query_tokens = _tokens(query)
    ranked: list[TargetRepoCandidate] = []
    for record in records:
        if not isinstance(record, dict) or not bool(record.get("exists")):
            continue
        candidate = _candidate_from_record(record, query_tokens=query_tokens)
        if candidate.score > 0:
            ranked.append(candidate)
    ranked.sort(key=lambda item: (not _has_explicit_repo_mention(query, item), -item.score, item.repo_key))
    return ranked


def _candidate_from_record(record: dict[str, Any], *, query_tokens: set[str]) -> TargetRepoCandidate:
    key = str(record.get("key") or "").strip()
    label = str(record.get("label") or key).strip()
    path = str(record.get("path") or "").strip()
    aliases = [str(item) for item in (record.get("aliases") or []) if str(item)]
    components = [str(item) for item in (record.get("components") or []) if str(item)]
    capabilities = [str(item) for item in (record.get("capabilities") or []) if str(item)]
    terms = [key, label, *aliases, *components, *capabilities]
    matched: list[str] = []
    score = 0
    for term in terms:
        term_tokens = _tokens(term)
        overlap = query_tokens & term_tokens
        if not overlap:
            continue
        matched.append(term)
        score += len(overlap)
        if term in {key, label} or term in aliases:
            score += 3
    return TargetRepoCandidate(
        repo_key=key,
        label=label,
        path=path,
        score=score,
        matched_terms=_dedupe(matched),
        components=components,
        capabilities=capabilities,
        owner_system=str(record.get("owner_system") or "").strip(),
    )


def _find_forced_candidate(records: list[Any], forced_repo_key: str | None) -> TargetRepoCandidate | None:
    normalized = str(forced_repo_key or "").strip().casefold()
    if not normalized:
        return None
    for record in records:
        if not isinstance(record, dict) or not bool(record.get("exists")):
            continue
        keys = [
            str(record.get("key") or ""),
            str(record.get("label") or ""),
            *[str(item) for item in (record.get("aliases") or [])],
        ]
        if normalized not in {item.casefold() for item in keys if item}:
            continue
        return _candidate_from_record(record, query_tokens=_tokens(" ".join(keys)))
    return None


def _has_explicit_repo_mention(task: str, candidate: TargetRepoCandidate) -> bool:
    lowered = f" {task.casefold()} "
    for term in [candidate.repo_key, candidate.label, *candidate.matched_terms]:
        normalized = str(term or "").strip().casefold()
        if not normalized:
            continue
        if normalized in lowered:
            return True
        if normalized.replace("-", " ") in lowered:
            return True
    return False


def _tokens(text: str) -> set[str]:
    normalized = str(text or "").casefold().replace("-", " ").replace("_", " ")
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9]*", normalized)
        if token not in {"a", "an", "and", "for", "in", "it", "of", "on", "the", "to", "with"}
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
