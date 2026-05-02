from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


@dataclass(frozen=True)
class LlmWikiBootstrapResult:
    output_dir: Path
    created_files: tuple[str, ...]
    preserved_files: tuple[str, ...]
    overwritten_files: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "created_files": list(self.created_files),
            "preserved_files": list(self.preserved_files),
            "overwritten_files": list(self.overwritten_files),
            "file_count": len(self.created_files) + len(self.preserved_files) + len(self.overwritten_files),
            "created_count": len(self.created_files),
            "preserved_count": len(self.preserved_files),
            "overwritten_count": len(self.overwritten_files),
            "authority": "supporting_not_authoritative",
            "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        payload = self.to_payload()
        lines = [
            "Spark LLM wiki bootstrap",
            f"- output_dir: {payload['output_dir']}",
            f"- created: {payload['created_count']}",
            f"- preserved: {payload['preserved_count']}",
            f"- overwritten: {payload['overwritten_count']}",
            "- authority: supporting_not_authoritative",
            "- runtime hook: hybrid_memory_retrieve.wiki_packets",
        ]
        if self.created_files:
            lines.append("- first created files:")
            lines.extend(f"  - {item}" for item in self.created_files[:8])
        return "\n".join(lines)


@dataclass(frozen=True)
class _WikiPage:
    relative_path: str
    title: str
    summary: str
    tags: tuple[str, ...]
    body: str


def bootstrap_llm_wiki(
    *,
    config_manager: ConfigManager,
    output_dir: str | Path | None = None,
    overwrite: bool = False,
) -> LlmWikiBootstrapResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    generated_at = _utc_timestamp()
    pages = _bootstrap_pages(generated_at=generated_at)
    created: list[str] = []
    preserved: list[str] = []
    overwritten: list[str] = []
    for page in pages:
        path = root / page.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        relative = path.relative_to(root).as_posix()
        if path.exists() and not overwrite:
            preserved.append(relative)
            continue
        existed = path.exists()
        path.write_text(_render_page(page=page, generated_at=generated_at), encoding="utf-8")
        if existed:
            overwritten.append(relative)
        else:
            created.append(relative)
    return LlmWikiBootstrapResult(
        output_dir=root,
        created_files=tuple(created),
        preserved_files=tuple(preserved),
        overwritten_files=tuple(overwritten),
    )


def _bootstrap_pages(*, generated_at: str) -> tuple[_WikiPage, ...]:
    return (
        _WikiPage(
            relative_path="index.md",
            title="Spark LLM Wiki",
            summary="Bootstrap navigation for Spark's local LLM-readable knowledge layer.",
            tags=("spark-wiki", "index", "bootstrap"),
            body="""
## Purpose
- This wiki is a grounded knowledge layer for Spark agents.
- It is retrieved as supporting project knowledge through the `wiki_packets` memory lane.
- It should help Spark know its systems, routes, tools, lacks, and improvement options without turning stale notes into truth.

## Core Pages
- [[system/index]]
- [[system/spark-self-awareness-contract]]
- [[system/spark-system-map]]
- [[system/natural-language-route-map]]
- [[system/tracing-and-observability-map]]
- [[system/recursive-self-improvement-loops]]
- [[routes/index]]
- [[memory/llm-wiki-memory-policy]]
- [[tools/index]]
- [[tools/tool-and-chip-inventory-contract]]
- [[user/index]]
- [[user/user-environment-profile-template]]
- [[projects/index]]
- [[improvements/index]]

## Grounding Rule
For current health, live registry/state/traces outrank this wiki. For stable contracts and operating doctrine, this wiki can provide reusable context.
""",
        ),
        _WikiPage(
            relative_path="system/index.md",
            title="System Index",
            summary="Navigation page for Spark system-awareness contracts and generated status snapshots.",
            tags=("spark-wiki", "index", "system"),
            body="""
## Stable System Pages
- [[spark-self-awareness-contract]]
- [[spark-system-map]]
- [[natural-language-route-map]]
- [[tracing-and-observability-map]]
- [[recursive-self-improvement-loops]]

## Generated System Pages
- [[current-system-status]]
- [[../diagnostics/self-awareness-gaps]]

## Boundary
Use this index for navigation. Newer live traces, registry state, health checks, and current-state memory outrank any linked page.
""",
        ),
        _WikiPage(
            relative_path="system/spark-self-awareness-contract.md",
            title="Spark Self-Awareness Contract",
            summary="How Spark should reason about itself without pretending unverified capabilities are live.",
            tags=("spark-wiki", "self-awareness", "contract"),
            body="""
## Definition
Spark self-awareness means grounded runtime inspection plus source-backed knowledge, not hidden self-modification.

## Evidence Buckets
- observed_now: live state, registry, configuration, and context visible this turn.
- recently_verified: recent successful tool, chip, provider, route, or health events.
- available_unverified: installed or registered surfaces that have not succeeded recently.
- degraded_or_missing: explicit failures, missing docs, absent traces, timeouts, or disabled systems.
- inferred: reasoned strengths or lacks derived from the above, clearly labeled as inference.

## Answer Behavior
- Name what Spark can do confidently.
- Name where evidence is thin.
- Prefer a safe next probe when confidence depends on live state.
- Explain how the user can improve the weak spot: add docs, run a health check, create a route, add traces, add evals, or promote a domain chip.

## Non-Override Rule
The wiki can explain the self-awareness contract. Live `self status`, registry, memory status, traces, and tool results decide the current state.
""",
        ),
        _WikiPage(
            relative_path="system/spark-system-map.md",
            title="Spark System Map",
            summary="Stable map of the main Spark surfaces an agent should reason across.",
            tags=("spark-wiki", "system-map", "architecture"),
            body="""
## Main Surfaces
- spark-telegram-bot: Telegram ingress, commands, natural-language handoff, and user-visible replies.
- spark-intelligence-builder: runtime registry, memory orchestration, self-awareness capsules, provider routing, diagnostics, and CLI operations.
- domain-chip-memory: benchmark-first memory research, SDK contracts, KB compiler, retention classes, and wiki packet reader.
- domain chips: specialized capability modules invoked through router hooks.
- researcher bridge: natural-language advisory surface that can call direct system routes, chips, providers, or self-awareness.
- spawner and mission control: longer-running workflow execution when available.

## Current-State Rule
Use this page for orientation. For exact live availability, call the registry, self-awareness capsule, channel status, memory status, or route-specific health check.

## Missing Knowledge To Fill
- deployment/infrastructure topology.
- provider failure modes and debugging docs.
- all domain-chip capability cards.
- route traces from Telegram message to Builder decision to tool result.
""",
        ),
        _WikiPage(
            relative_path="system/natural-language-route-map.md",
            title="Natural-Language Route Map",
            summary="How Spark should connect user intent to the right system without requiring slash commands.",
            tags=("spark-wiki", "routing", "natural-language"),
            body="""
## Principle
Slash commands are shortcuts. The agent should understand natural-language requests and map them to safe routes when intent is clear.

## Route Families
- self-awareness questions: use the self-awareness capsule and recent traces.
- memory questions: use current-state, evidence, and wiki packet lanes with provenance.
- system status questions: use registry, diagnostics, health, and recent failures.
- domain work: select a chip only when the task matches its capability and activation evidence is sufficient.
- improvement requests: convert the lack into an action such as doc ingest, route probe, eval case, tracing addition, or chip creation.

## Confidence Rule
Route confidence should combine user intent, current context, recent success evidence, and available docs. Registered capability alone is not enough.
""",
        ),
        _WikiPage(
            relative_path="system/tracing-and-observability-map.md",
            title="Tracing And Observability Map",
            summary="What Spark should inspect before making claims about system behavior.",
            tags=("spark-wiki", "tracing", "observability"),
            body="""
## Trace Sources
- state.db event ledger: tool results, dispatch failures, memory events, diagnostics, and route decisions.
- system registry: available chips, paths, providers, gateway, browser, memory, and route surfaces.
- memory context packet: selected candidates, lane summaries, source mix, and provenance.
- diagnostics reports: recurring failures and maintenance notes.
- KB maintenance report: stale pages, contradiction candidates, source gaps, and generated coverage.

## Trace Questions
- What route was chosen?
- What evidence supported that route?
- Did the tool or chip actually run?
- What failed, timed out, or abstained?
- What should be probed next?

## Upgrade Target
Every major natural-language route should emit enough trace metadata that Spark can later explain why it acted and how to improve the route.
""",
        ),
        _WikiPage(
            relative_path="system/recursive-self-improvement-loops.md",
            title="Recursive Self-Improvement Loops",
            summary="Safe way for Spark to improve itself through evidence, gates, and rollback-aware changes.",
            tags=("spark-wiki", "recursive-improvement", "quality-gates"),
            body="""
## Loop
1. Detect a lack from user intent, trace failure, benchmark failure, or missing knowledge.
2. Convert the lack into a bounded hypothesis.
3. Add or update evidence: docs, probes, eval cases, route traces, or benchmark slices.
4. Implement the smallest scoped change.
5. Verify with tests and, when needed, live Telegram or Builder validation.
6. Promote only when evidence beats the baseline and no stop-ship gate fires.

## Stop-Ship Gates
- No provenance for the claimed improvement.
- Offline eval win without runtime validation for runtime behavior.
- Memory or wiki promotion from conversational residue.
- New route that bypasses source boundaries or current-state precedence.
- Drift from a stable contract without a documented reason.

## Output
The loop should produce a traceable improvement packet, not just a confident answer.
""",
        ),
        _WikiPage(
            relative_path="routes/index.md",
            title="Routes Index",
            summary="Navigation page for natural-language route maps, live route snapshots, and route trace expectations.",
            tags=("spark-wiki", "index", "routes"),
            body="""
## Route Pages
- [[../system/natural-language-route-map]]
- [[live-route-index]]
- [[../system/tracing-and-observability-map]]

## Review Questions
- What user intent was detected?
- Which route fired?
- Which source packets and probes supported the route?
- What stale evidence was ignored?
- What missing probe would improve confidence?

## Boundary
Route documentation supports explanation. Actual route decisions come from current intent, registry state, route traces, and tool results.
""",
        ),
        _WikiPage(
            relative_path="memory/llm-wiki-memory-policy.md",
            title="LLM Wiki Memory Policy",
            summary="Retention and compaction policy for wiki pages, memory facts, and conversational residue.",
            tags=("spark-wiki", "memory", "compaction", "policy"),
            body="""
## Layering
- raw evidence: traces, snapshots, transcripts, repo files, logs, and run artifacts.
- extracts: typed facts, route outcomes, failures, decisions, and source snippets.
- cards: concise capability, route, tool, and system summaries.
- pages: stable operating knowledge with provenance and freshness metadata.
- dossiers: deep references where compaction would destroy useful detail.

## Compaction Rules
- Compact ordinary conversational residue aggressively.
- Preserve system contracts, route diagrams, eval cases, failure modes, and exact commands with more detail.
- Keep derived beliefs labeled as inferred and revalidatable.
- Do not let wiki pages override live current-state memory for mutable facts.
- Prefer page-level freshness, source refs, confidence, invalidation triggers, and next probes.

## Promotion Rules
Only promote a wiki update when it is reusable, source-backed, bounded to a domain, and useful for future reasoning.
""",
        ),
        _WikiPage(
            relative_path="tools/index.md",
            title="Tools Index",
            summary="Navigation page for Spark tool, chip, provider, and capability inventory contracts.",
            tags=("spark-wiki", "index", "tools", "chips"),
            body="""
## Tool Pages
- [[tool-and-chip-inventory-contract]]
- [[capability-index]]

## Capability Questions
- Is the tool configured?
- Is it available in the current environment?
- Did it succeed recently?
- What safe probe should run before claiming confidence?

## Boundary
Installed or registered tools are not the same as recently verified tools. Recent successful invocation evidence should be named separately.
""",
        ),
        _WikiPage(
            relative_path="tools/tool-and-chip-inventory-contract.md",
            title="Tool And Chip Inventory Contract",
            summary="How Spark should represent tools and domain chips in the wiki.",
            tags=("spark-wiki", "tools", "chips", "inventory"),
            body="""
## Inventory Card Fields
- name and owning repo.
- natural-language intents handled.
- hooks, commands, or APIs.
- last verified success and last failure.
- known limits and missing docs.
- required secrets or environment.
- safe probe command.
- upgrade path if the user wants better behavior.

## Evidence Boundary
Installed, pinned, or registered means available. It does not mean recently working. Recent successful invocation is a separate claim.

## First Cards To Generate
- memory chip.
- self-awareness capsule.
- researcher bridge.
- Telegram bot surface.
- diagnostics scanner.
- system registry.
""",
        ),
        _WikiPage(
            relative_path="user/index.md",
            title="User Index",
            summary="Navigation page for user-specific context lanes, consent rules, and environment profile templates.",
            tags=("spark-wiki", "index", "user", "consent"),
            body="""
## User Context Pages
- [[user-environment-profile-template]]
- [[../memory/llm-wiki-memory-policy]]

## User Lane Rules
- User/environment context belongs under `wiki/users/<human>/...`.
- User notes require explicit consent or configured policy metadata.
- User notes do not become global Spark doctrine.
- Governed current-state memory outranks wiki notes for mutable user facts.

## Boundary
This index describes the lane. It does not grant consent, promote conversational residue, or override governed user memory.
""",
        ),
        _WikiPage(
            relative_path="user/user-environment-profile-template.md",
            title="User Environment Profile Template",
            summary="Template for personalized wiki knowledge that helps Spark adapt without over-promoting noise.",
            tags=("spark-wiki", "user", "environment", "template"),
            body="""
## Stable Profile
- durable user preferences.
- important projects and repos.
- working style and desired answer shape.
- preferred risk posture.

## Environment
- local repo map.
- active channels.
- available providers.
- installed chips and paths.
- common commands.

## Current Focus
- active goals.
- open decisions.
- blockers.
- next actions.

## Promotion Boundary
Do not promote one-off phrasing, frustration, transient debugging residue, or stale operational state as stable user truth.
""",
        ),
        _WikiPage(
            relative_path="projects/index.md",
            title="Projects Index",
            summary="Navigation page for project-specific wiki context and source-backed working maps.",
            tags=("spark-wiki", "index", "projects"),
            body="""
## Project Context
- active repos and missions.
- source-backed decisions and constraints.
- environment-specific setup notes.
- project-specific capability gaps and probes.

## Storage Rule
Project notes should name their repo, source refs, freshness, and invalidation trigger. They should not override live git, filesystem, CI, or current tool output.

## Boundary
Use this page as a placeholder index until project pages are generated or promoted with source metadata.
""",
        ),
        _WikiPage(
            relative_path="improvements/index.md",
            title="Improvements Index",
            summary="Navigation page for candidate and verified Spark improvement notes.",
            tags=("spark-wiki", "index", "improvements"),
            body="""
## Improvement Lanes
- `wiki/improvements/*.md`: global Spark improvement notes.
- `wiki/users/<human>/candidate-notes/*.md`: user-specific candidate context.

## Review Commands
- `spark-intelligence wiki candidates --status all --json`
- `spark-intelligence wiki scan-candidates --json`
- `spark-intelligence wiki status --json`

## Promotion Boundary
Improvement notes are supporting and revalidatable. They need evidence refs, source refs, stale checks, contradiction scans, and live probes before they influence runtime confidence.
""",
        ),
    )


def _render_page(*, page: _WikiPage, generated_at: str) -> str:
    tags = ", ".join(page.tags)
    body = "\n".join(line.rstrip() for line in page.body.strip().splitlines())
    return "\n".join(
        [
            "---",
            f'title: "{page.title}"',
            f'date_created: "{generated_at[:10]}"',
            f'date_modified: "{generated_at[:10]}"',
            f'summary: "{page.summary}"',
            f"tags: [{tags}]",
            "type: llm_wiki_bootstrap",
            "status: bootstrap",
            "authority: supporting_not_authoritative",
            "freshness: bootstrap_static",
            "source_class: spark_llm_wiki_bootstrap",
            "---",
            "",
            f"# {page.title}",
            "",
            body,
            "",
        ]
    )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
