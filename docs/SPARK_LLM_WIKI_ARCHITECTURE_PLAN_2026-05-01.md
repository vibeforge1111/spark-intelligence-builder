# Spark LLM Wiki Architecture Plan

Date: 2026-05-01
Repo: `spark-intelligence-builder`

## Goal

Build Spark's LLM wiki as a persistent, local, source-backed knowledge layer that helps Spark understand:

- what systems exist
- what each system can do
- what is live, recently verified, unverified, degraded, or missing
- how user intent maps to routes, tools, chips, providers, and improvement loops
- how to improve weak areas without drift or unsupported confidence

The wiki must make Spark feel more aware and capable, but it must not become a script of deterministic answers. It is evidence and operating knowledge for the LLM, not a replacement for live reasoning, registry inspection, traces, or current-state memory.

## Existing Substrate

We are not starting from zero.

Already present:

- `domain-chip-memory` owns the Spark KB compiler and wiki packet reader.
- `spark-intelligence-builder memory compile-telegram-kb` calls domain-chip-memory and produces a Karpathy-style vault with:
  - `wiki/current-state`
  - `wiki/evidence`
  - `wiki/events`
  - `wiki/sources`
  - `wiki/syntheses`
  - `wiki/outputs`
- Builder hybrid memory retrieval already has a `wiki_packets` lane.
- Wiki packets are explicitly `supporting_not_authoritative`.
- Builder automatically checks `$SPARK_HOME/wiki`, `$SPARK_HOME/knowledge`, and `$SPARK_HOME/diagnostics` as wiki packet paths when present.
- Session, daily, and project summaries already compact event ledgers into durable structured evidence.
- Memory retention classes already distinguish `active_state`, `durable_profile`, `episodic_archive`, `derived_belief`, and `ops_residue`.

First implemented slice in this change:

- `spark-intelligence wiki bootstrap`
- Bootstrap pages under `$SPARK_HOME/wiki`
- System/self-awareness/routing/tracing/recursive-improvement/tool/user-environment pages
- Tests proving the bootstrapped wiki is retrievable through `hybrid_memory_retrieve.wiki_packets`

## Core Design

The LLM wiki should be a layered knowledge system:

1. Bootstrap wiki
   - Installed with Spark.
   - Stable pages about system maps, self-awareness contracts, route maps, tracing, recursive improvement, and memory policy.
   - Lives in `$SPARK_HOME/wiki`.

2. Runtime wiki
   - Generated from registry snapshots, route traces, diagnostics, health checks, memory status, and KB maintenance reports.
   - Answers "how are systems working right now?" with live trace references.

3. User/environment wiki
   - Personalized to the local user and machine.
   - Tracks projects, repo maps, stable preferences, active goals, recurring workflows, and local constraints.
   - Must not promote one-off conversation residue into stable identity.
   - Keeps user-specific knowledge separate from global Spark system doctrine unless the user explicitly promotes it.

4. Domain wiki
   - Capability cards for each domain chip, provider, path, tool, and major route.
   - Includes last verified success/failure, natural-language intents, safe probe commands, known limits, and improvement path.

5. Builder/project wiki
   - Tracks active repos, shipped project context, Spawner missions, build/test outcomes, and project-specific lessons.
   - Helps Spark answer "what are we building, what changed, what passed, and what should be improved next?"
   - Must distinguish durable project knowledge from temporary planning chatter.

6. Self-improvement wiki
   - Tracks weak spots, candidate learnings, verified learnings, failed mutations, eval coverage, and rollback conditions.
   - Gives Spark a growing memory of how to improve without silently changing itself.

7. Evidence ledger
   - Sources, traces, snapshots, exact commands, eval results, provenance links, timestamps, confidence, freshness, invalidation triggers, and next probes.

## Authority Model

The wiki should never be treated as one truth blob.

Authority order for self/system answers:

1. Live current-turn context and user intent.
2. Live registry, state DB, route traces, health checks, and tool results.
3. Current-state memory for mutable user facts.
4. Recently verified route/chip/provider outcomes.
5. LLM wiki pages as supporting context.
6. Older historical notes and inferred beliefs.

Required answer behavior:

- Cite the kind of evidence being used: live, recently verified, wiki-backed, inferred, stale, or unknown.
- Use the wiki to understand structure and options.
- Use live traces for current health.
- Use deep search or probes when the wiki is stale, missing, high-stakes, or contradicted by live state.

## Compaction Model

Do not use one global compressor. Different knowledge types need different retention pressure.

Preserve high detail:

- system contracts
- route diagrams
- trace schemas
- safe commands
- failure modes
- eval cases
- benchmark results
- source manifests
- recursive improvement decisions
- provider and chip debugging notes

Compact aggressively:

- casual conversational residue
- repeated status chatter
- failed intermediate guesses
- low-value operational noise
- transient preferences unless repeated or explicitly durable

Use a summary pyramid:

1. Raw evidence: logs, traces, snapshots, transcripts, repo files, run artifacts.
2. Extracts: facts, route outcomes, failures, decisions, source snippets.
3. Cards: tool, route, chip, provider, and system cards.
4. Pages: stable operating knowledge with provenance and freshness.
5. Dossiers: deep pages where heavy compaction would destroy useful detail.

Every durable page should eventually carry:

- `source_refs`
- `freshness`
- `confidence`
- `authority`
- `owner_system`
- `last_verified_at`
- `invalidation_triggers`
- `next_probe`

## First Wiki Pages

Bootstrap pages already added:

- `index.md`
- `system/spark-self-awareness-contract.md`
- `system/spark-system-map.md`
- `system/natural-language-route-map.md`
- `system/tracing-and-observability-map.md`
- `system/recursive-self-improvement-loops.md`
- `memory/llm-wiki-memory-policy.md`
- `tools/tool-and-chip-inventory-contract.md`
- `user/user-environment-profile-template.md`

Next generated pages:

- `tools/domain-chip-memory.md`
- `tools/researcher-bridge.md`
- `tools/system-registry.md`
- `tools/self-awareness-capsule.md`
- `channels/telegram-bot.md`
- `routes/telegram-message-to-builder.md`
- `routes/self-awareness-query.md`
- `routes/memory-query.md`
- `diagnostics/trace-ledger-map.md`
- `providers/provider-health-and-failure-modes.md`

## Retrieval Behavior

When a user asks a question, Spark should combine:

- conversational intent
- current context capsule
- live registry/traces when relevant
- memory current-state/evidence when relevant
- wiki packets for stable system/project knowledge
- deep search or probes when evidence is thin

Examples:

- "Where do you lack?"
  - Use self-awareness capsule first.
  - Pull wiki self-awareness contract and system map as supporting context.
  - Offer concrete probes or docs to add.

- "How do recursive self-improvement loops work here?"
  - Use wiki page first.
  - If user wants execution, inspect current repo/tests and produce a bounded mutation plan.

- "Can you use the memory chip?"
  - Check registry and recent route results.
  - Pull domain-chip-memory card.
  - If unverified, run a safe probe.

- "Improve your knowledge of Telegram routes."
  - Create or update route pages from source code, traces, and docs.
  - Add an eval/probe for the route.
  - Promote only after tests and live validation.

## Missing Surfaces To Add

1. Wiki runtime compiler in Builder
   - Build generated system pages from registry, self-awareness capsule, diagnostics, and route traces.
   - Should not replace the domain-chip-memory KB compiler; it should enrich `$SPARK_HOME/wiki`.

2. Capability card generator
   - One card per chip/provider/tool/route.
   - Include natural-language intents, last success, last failure, safe probe, known limits, and upgrade options.

3. Route trace coverage
   - Ensure Telegram natural-language requests emit enough trace metadata to reconstruct:
     - detected intent
     - route candidates
     - selected route
     - invoked tool/chip/provider
     - result status
     - confidence and reason

4. Wiki promotion inbox
   - Candidate pages are generated automatically.
   - Risky or inferred updates are queued for review instead of promoted silently.

5. Scheduled maintenance
   - Periodically refresh wiki health, stale pages, broken links, missing cards, and unverified capability claims.
   - Use a heartbeat/cron style job only after the compiler is stable.

6. Deep search trigger policy
   - Ask a researcher/browser route when:
     - wiki is stale or absent
     - current facts may have changed
     - the answer affects architecture, money, safety, infra, or user commitments
     - local traces contradict stored docs

## Build Plan

Phase 1: Bootstrap and retrieval hook

- Add `spark-intelligence wiki bootstrap`.
- Seed `$SPARK_HOME/wiki` with system pages.
- Verify hybrid memory retrieves the pages as `wiki_packets`.
- Status: implemented.

Phase 2: Runtime system wiki compiler

- Add `spark-intelligence wiki compile-system`.
- Read:
  - system registry
  - self-awareness capsule
  - memory status
  - diagnostics reports
  - recent route traces
- Generate:
  - current system map
  - capability cards
  - lack/improvement index
  - trace coverage index
- Add health report and tests.

Phase 3: Telegram natural-language integration

- Ensure self/system/improvement questions retrieve wiki packets when helpful.
- Keep `/self` and future `/wiki` as shortcuts, not required surfaces.
- Add tests for natural-language "where do you lack?", "how can you improve memory?", and "what routes can you use?"

Phase 4: Wiki growth loop

- Generate wiki candidates after missions, diagnostics scans, memory regressions, and major route failures.
- Gate promotions using keepability, provenance, source-boundary, and drift checks.
- Add reviewable candidate metadata.

Phase 5: Scheduling and maintenance

- Add recurring wiki health checks.
- Refresh stale current system pages.
- Produce "what Spark should learn next" from gaps, failures, and user goals.

## Regression Guardrails

Stop ship if:

- wiki packets override live current-state or route traces
- generated pages lack provenance or authority metadata
- conversational residue becomes stable user truth
- a route claims verified status from registry visibility alone
- natural-language routing becomes command-only
- compaction removes commands, source refs, failure modes, or eval criteria from system pages

## Product Standard

The user should be able to ask:

- "What do you know about yourself?"
- "Where do you lack?"
- "What systems can you call?"
- "Show me your weakest traces."
- "Improve your knowledge of Telegram routes."
- "Create a capability card for this chip."
- "What should I add so you can do this better?"

Spark should answer with grounded confidence: what it knows, what it can try, what evidence it has, what is missing, and the next concrete improvement path.
