# Spark Self-Awareness and LLM Wiki Handoff

Date: 2026-05-01  
Primary repos:
- `C:\Users\USER\Desktop\spark-intelligence-builder`
- `C:\Users\USER\Desktop\spark-telegram-bot`

## Purpose

This handoff captures the work completed to make Spark agents more genuinely self-aware and introspective through grounded runtime inspection, an Obsidian-compatible LLM wiki, and natural-language Telegram access.

The goal is not canned self-description. The goal is for Spark to know what it can inspect, what is live, what is only registered, where it lacks evidence, where it can improve, and which system or knowledge path to call next.

## Core Principle

Spark self-awareness must remain evidence-layered:

1. Live status, registry, traces, and current runtime state decide mutable truth.
2. Recently verified route/tool evidence proves what worked.
3. LLM wiki pages provide supporting project/system knowledge.
4. Memory and conversation context help with continuity, but should not override live state.
5. When evidence is missing, Spark should say what is missing and suggest the next probe.

The LLM wiki is supporting knowledge, not authority over current truth.

## 2026-05-02 Hardening Addendum

The self-awareness/wiki work now includes typed maintenance surfaces that keep Spark's confidence grounded without turning reports into memory truth:

- `wiki heartbeat --json` writes an LLM wiki health report for stale pages, broken local links, and candidate backlog.
- `self heartbeat --json` writes a capability drift report for stale successes, recent failures, observed-without-success routes, and configured capabilities missing last-success evidence.
- `self live-telegram-cadence --json` names the live Telegram prompt pack, route matrix, verifier command, artifact directory, and real-trace evidence boundary.
- `self handoff-check --json` checks whether major self-awareness/wiki source changes moved this handoff, the architecture plan, and the hardening task list together.
- Builder self-status now distinguishes aggregate Builder readiness warnings from concrete provider/channel blockers. Generic gateway/provider/channel readiness no longer tells the operator to repair Builder directly; it tells Spark to inspect the provider/channel rows and only treat those concrete rows as precise blockers.

All four reports are `observability_non_authoritative` and use the same boundary: reports can recommend probes or documentation updates, but they do not promote runtime truth, durable memory, or verified release claims by themselves.

## What Was Built

### 1. Builder Self-Awareness Capsule

Spark Intelligence Builder now has a first-class self-awareness capsule and CLI status surface.

Important files:
- `src/spark_intelligence/self_awareness/capsule.py`
- `src/spark_intelligence/self_awareness/__init__.py`
- `src/spark_intelligence/cli.py`
- `tests/test_self_awareness.py`

CLI:

```bash
python -m spark_intelligence.cli self status --refresh-wiki --json
```

Current behavior:
- Separates observed systems, recently verified actions, unverified capabilities, lacks, and improvement options.
- Includes style/personality lens without dumping raw trait internals.
- Can refresh generated wiki system pages and include wiki retrieval context.
- Natural self-awareness queries in Builder route to `self_awareness_direct`.

### 2. LLM Wiki Bootstrap

Builder can seed a local Obsidian-compatible Markdown vault at:

```text
$SPARK_HOME/wiki
```

On this machine the real vault is:

```text
C:\Users\USER\.spark-intelligence\wiki
```

Important files:
- `src/spark_intelligence/llm_wiki/bootstrap.py`
- `src/spark_intelligence/llm_wiki/__init__.py`
- `tests/test_llm_wiki_bootstrap.py`

CLI:

```bash
python -m spark_intelligence.cli wiki bootstrap --json
```

Seeded pages:
- `index.md`
- `system/spark-self-awareness-contract.md`
- `system/spark-system-map.md`
- `system/natural-language-route-map.md`
- `system/tracing-and-observability-map.md`
- `system/recursive-self-improvement-loops.md`
- `memory/llm-wiki-memory-policy.md`
- `tools/tool-and-chip-inventory-contract.md`
- `user/user-environment-profile-template.md`

### 3. Runtime System Wiki Compiler

Builder can generate live system pages into the same vault.

Important files:
- `src/spark_intelligence/llm_wiki/compile_system.py`
- `src/spark_intelligence/cli.py`
- `tests/test_llm_wiki_bootstrap.py`

CLI:

```bash
python -m spark_intelligence.cli wiki compile-system --json
```

Generated pages:
- `system/current-system-status.md`
- `tools/capability-index.md`
- `routes/live-route-index.md`
- `diagnostics/self-awareness-gaps.md`

These pages are generated from:
- system registry
- self-awareness capsule
- memory runtime status

### 4. Hybrid Memory Wiki Packet Priority

Builder hybrid memory retrieval now treats wiki packets as project/system knowledge for self-awareness, system, route, tool, and wiki intents.

Important file:
- `src/spark_intelligence/memory/orchestrator.py`

Behavior:
- `wiki_packets` lane reads Markdown knowledge packets from `$SPARK_HOME/wiki`, `$SPARK_HOME/knowledge`, and `$SPARK_HOME/diagnostics` when present.
- Project/system/wiki intents boost `obsidian_llm_wiki_packets`.
- Generic recent events and conversational residue are demoted for these queries.
- Context packet trace includes `project_knowledge_first`.

This is a key anti-drift guard: stable system knowledge can help, but raw conversation residue should not become agent doctrine.

### 5. Builder Wiki Health Surface

Builder now has a health/status check for the LLM wiki.

Important files:
- `src/spark_intelligence/llm_wiki/status.py`
- `src/spark_intelligence/cli.py`
- `tests/test_llm_wiki_bootstrap.py`

CLI:

```bash
python -m spark_intelligence.cli wiki status --refresh --json
```

Checks:
- vault exists
- expected bootstrap files exist
- expected generated system files exist
- Markdown page count
- newest modification time
- `wiki_packets` retrieval status
- wiki hit count
- `project_knowledge_first`
- warnings for missing/unhealthy retrieval

Live smoke result from this machine:

```text
healthy: True
wiki_retrieval_status: supported
wiki_record_count: 3
markdown_page_count: 13
project_knowledge_first: True
output_dir: C:\Users\USER\.spark-intelligence\wiki
```

### 6. Builder Wiki Inventory Surface

Builder now has a deterministic vault inventory surface.

Important files:
- `src/spark_intelligence/llm_wiki/inventory.py`
- `src/spark_intelligence/cli.py`
- `tests/test_llm_wiki_bootstrap.py`

CLI:

```bash
python -m spark_intelligence.cli wiki inventory --refresh --limit 12 --json
```

Returns:
- total page count
- returned page count
- section counts
- expected/missing files
- page path
- title
- summary
- tags
- type/status/authority/freshness/source class
- modified timestamp
- byte count

Live smoke result from this machine:

```text
page_count: 13
returned_page_count: 5
missing_expected: 0
sections: diagnostics=1, memory=1, root=1, routes=1, system=6, tools=2, user=1
output_dir: C:\Users\USER\.spark-intelligence\wiki
```

### 7. Builder Wiki Query Surface

Builder now has an intent-specific retrieval surface over the wiki packet lane.

Important files:
- `src/spark_intelligence/llm_wiki/query.py`
- `src/spark_intelligence/cli.py`
- `tests/test_llm_wiki_bootstrap.py`

CLI:

```bash
python -m spark_intelligence.cli wiki query "recursive self-improvement loops" --refresh --limit 3 --json
```

Returns:
- query
- `wiki_retrieval_status`
- hit count
- relevant wiki packet hits
- title
- text
- source path
- packet id
- selected/discarded status
- score
- source mix
- context sections
- `project_knowledge_first`
- warnings

Live smoke result from this machine:

```text
retrieval: supported
hit_count: 3
project_knowledge_first: True
first_title: Recursive Self-Improvement Loops
first_source: C:\Users\USER\.spark-intelligence\wiki\system\recursive-self-improvement-loops.md
```

### 8. Telegram `/self`

Spark Telegram Bot now exposes Builder self-awareness.

Important files:
- `src/builderBridge.ts`
- `src/index.ts`
- `tests/builderBridge.test.ts`
- `tests/conversationIntent.test.ts`

Command:

```text
/self
```

Behavior:
- Calls Builder `self status --refresh-wiki --json`.
- Formats observed capabilities, recently verified actions, lacks, improvement options, wiki context, and the core evidence rule.
- Uses a longer timeout path for Builder self-awareness.

### 9. Telegram `/wiki`

Spark Telegram Bot now exposes wiki health, inventory, and query flows.

Important files:
- `src/builderBridge.ts`
- `src/index.ts`
- `src/conversationIntent.ts`
- `tests/builderBridge.test.ts`
- `tests/conversationIntent.test.ts`

Commands:

```text
/wiki
/wiki pages
/wiki search recursive self-improvement loops
/wiki query route tracing
```

Behavior:
- `/wiki` calls Builder `wiki status --refresh --json`.
- `/wiki pages` calls Builder `wiki inventory --refresh --limit 12 --json`.
- `/wiki search ...` and `/wiki query ...` call Builder `wiki query ... --refresh --limit 5 --json`.

### 10. Natural-Language Telegram Access

Telegram now recognizes natural wiki/status/inventory/query intents.

Examples:

```text
is your LLM wiki active right now?
can you check whether the Spark knowledge base is retrievable?
show me the Obsidian vault status
what pages are in your LLM wiki?
list the Spark knowledge base contents
search your wiki for recursive self-improvement loops
what does the Spark knowledge base say about route tracing?
```

Intent safeguards:
- Build prompts like `build me a wiki app for my team` should remain build prompts.
- Status questions should not be stolen by inventory.
- Inventory questions should not be stolen by query.
- Query phrases should not steal status or inventory.

## Current Validation

Validation commands that passed during this session:

```bash
python -m pytest tests/test_llm_wiki_bootstrap.py
```

Result:

```text
14 passed
```

```bash
npm test -- tests/builderBridge.test.ts tests/conversationIntent.test.ts
```

Result:

```text
passed
```

```bash
npm run build
```

Result:

```text
passed
```

Earlier focused checks also passed:

```bash
python -m pytest tests/test_self_awareness.py tests/test_memory_orchestrator.py::MemoryOrchestratorTests::test_hybrid_memory_retrieve_reads_wiki_packets_as_supporting_context
```

Result:

```text
7 passed, 1 warning
```

## Current Working Tree Notes

Builder repo:

```text
C:\Users\USER\Desktop\spark-intelligence-builder
```

Expected changed/new areas from this work:
- `src/spark_intelligence/cli.py`
- `src/spark_intelligence/llm_wiki/__init__.py`
- `src/spark_intelligence/llm_wiki/status.py`
- `src/spark_intelligence/llm_wiki/inventory.py`
- `src/spark_intelligence/llm_wiki/query.py`
- `src/spark_intelligence/memory/orchestrator.py`
- `src/spark_intelligence/researcher_bridge/advisory.py`
- `src/spark_intelligence/self_awareness/capsule.py`
- `tests/test_llm_wiki_bootstrap.py`
- `tests/test_self_awareness.py`

Telegram repo:

```text
C:\Users\USER\Desktop\spark-telegram-bot
```

Expected changed/new areas from this work:
- `src/builderBridge.ts`
- `src/conversationIntent.ts`
- `src/index.ts`
- `tests/builderBridge.test.ts`
- `tests/conversationIntent.test.ts`

Known unrelated/untracked item:
- `C:\Users\USER\Desktop\spark-telegram-bot\PROJECT.md`

Do not delete or overwrite unrelated/user changes without inspecting them.

## What Remains To Improve

The planned hardening backlog is now tracked here:

```text
C:\Users\USER\Desktop\spark-intelligence-builder\docs\SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md
```

That backlog splits the next work into user awareness, Spark/system awareness, environment awareness, builder/project awareness, wiki growth, route explainability, self-improvement governance, and live Telegram testing.

### A. Conversational Synthesis Over Wiki Query Results

Current query output is evidence-forward and operational. It lists relevant packets and source paths.

Next improvement:
- Add an LLM synthesis layer that answers the user naturally using retrieved wiki packets.
- Preserve source boundaries in the answer.
- Show when the answer is wiki-backed versus live-verified.
- Avoid dumping raw packet text unless the user asks for the evidence.

Suggested shape:

```text
Short answer:
...

Wiki-backed context:
- source: ...

What I still need to verify live:
- ...
```

### B. Unified Self-Awareness Answer Planner

Right now, self-awareness uses the capsule and system routes, while wiki query/status/inventory are separate surfaces.

Next improvement:
- Build an internal planner that chooses among:
  - self capsule
  - registry
  - wiki status
  - wiki inventory
  - wiki query
  - diagnostics scan
  - route-specific probes
- Then emits one coherent answer.

This should support questions like:

```text
what do you know about your tools, what are you missing, and what should we improve next?
```

without producing three separate status reports.

### C. Capability Last-Success Telemetry

The self-awareness capsule still needs better proof fields per capability.

Add:
- `last_success_at`
- `last_failure_at`
- `last_failure_reason`
- `last_latency_ms`
- `last_eval_status`
- `last_probe_command`
- `confidence`
- `evidence_refs`

This should be attached to:
- tools
- chips
- providers
- browser/local workspace routes
- memory routes
- wiki routes
- diagnostics routes
- Spawner/mission routes

### D. Route Trace Explainability

Spark should be able to answer:

```text
why did you choose that route?
what did you call?
what evidence did you use?
what did you ignore?
```

Current systems have pieces of this through routing decisions, evidence summaries, and context packet traces, but there is no unified end-user route explanation for all paths.

Next improvement:
- Add `spark-intelligence route explain <request-id>` or equivalent.
- Telegram natural phrase: `why did you answer that way?`
- Include route, evidence, discarded stale records, wiki/source mix, and missing probes.

### E. Wiki Update / Promotion Workflow

Current wiki bootstrap and generated pages exist, but Spark does not yet have a mature workflow for proposing new wiki pages from work.

Need:
- candidate wiki update generator
- keep/rewrite/drop gate
- source-backed promotion policy
- review queue
- stale page detection
- broken wiki link detection
- compaction policy by page class

Critical anti-pattern guard:
- Never promote conversational residue directly.
- Require source-backed, reusable, bounded knowledge.
- Keep user-specific notes separated from system doctrine.

### F. Obsidian Operator Experience

The vault is Obsidian-compatible Markdown, but operator polish can improve.

Add:
- better index pages
- backlinks/wikilinks between generated pages
- `diagnostics/wiki-health.md`
- `routes/route-trace-examples.md`
- `tools/provider-capability-matrix.md`
- `tools/chip-inventory.md`
- `user/preferences.md` only when explicitly consented/promoted

### G. Install-Time Bootstrap

The LLM wiki should be set up automatically during Spark install/bootstrap.

Need:
- run `wiki bootstrap` during supported install profiles
- optionally run `wiki compile-system`
- make `SPARK_HOME/wiki` visible in setup output
- document Obsidian opening path
- verify wiki packet lane after install

### H. Scheduling and Watchtower

User asked about intelligent scheduling and tracing surfaces.

Need recurring/triggered checks for:
- wiki health
- stale generated pages
- capability last-success drift
- provider degradation
- failed route clusters
- missing trace fields

Possible commands:

```bash
spark-intelligence wiki status --refresh --json
spark-intelligence diagnostics scan --json
spark-intelligence self status --refresh-wiki --json
```

### I. Real Evaluation Suite

Current tests prove mechanics. Need behavioral evals that score whether Spark feels aware and useful.

Create evaluation prompts:
- self-knowledge
- lack admission
- route selection
- wiki-backed answer
- contradiction handling
- stale wiki vs live status
- user asks how to improve Spark
- user asks for current tool status
- user asks for source explanation

Each eval should check:
- evidence layering
- no false certainty
- correct route called
- missing info named
- useful next action suggested
- no canned status dump when a conversational answer is better

### J. Live Telegram End-to-End Smoke

The current validation covers unit/focused tests, Builder CLI smoke checks, a live route matrix, and a typed live Telegram cadence contract.

Still needed:
- run actual Telegram bot locally
- run `python -m spark_intelligence.cli self live-telegram-cadence --json`
- use the printed `-PrintPromptsOnly` command to send the prompt pack to the real bot
- run the printed verifier command so `artifacts/live-telegram-regression/latest.json` captures real Telegram runtime traces
- inspect node outbound audit and Telegram formatting when the verifier flags a mismatch

### K. Commit/PR Hygiene

Before shipping:
- inspect diffs in both repos
- separate unrelated changes
- run focused and broader tests
- commit Builder and Telegram changes separately or in a coordinated branch pair
- run `python -m spark_intelligence.cli self handoff-check --json`
- include this handoff doc, the architecture plan, and the hardening task list when self-awareness/wiki behavior changes

## Things Potentially Overlooked

1. Full integration with the older domain-chip-memory KB compiler.
   - We used the existing `wiki_packets` lane and local `$SPARK_HOME/wiki`.
   - We did not yet unify all older KB compiler outputs, `wiki/current-state`, `wiki/evidence`, `wiki/syntheses`, and new LLM wiki pages into one navigation model.
   - Follow-up now mapped in `docs/SPARK_MEMORY_WIKI_COGNITION_INTEGRATION_2026-05-01.md`.
   - Cross-terminal feedback packet now lives at `docs/SPARK_MEMORY_SYSTEM_FEEDBACK_PACKET_2026-05-01.md`.

2. User consent and privacy policy for personalized wiki pages.
   - A `user/user-environment-profile-template.md` exists.
   - Actual user-specific wiki writes should require clear promotion/consent policy.

3. Multi-agent or multi-install wiki namespaces.
   - Current default path is per Spark home.
   - Need clearer namespacing if multiple Spark identities or workspaces share one machine.

4. Security and prompt-injection hardening for wiki pages.
   - Markdown pages are local and trusted-ish, but any generated/imported page should be treated as data, not instructions.
   - Need explicit prompt-boundary sanitization when wiki snippets enter LLM synthesis.

5. Freshness semantics.
   - Bootstrap pages are `bootstrap_static`.
   - Generated pages refresh on command.
   - Need stale thresholds and warnings per page class.

6. Source citations inside natural answers.
   - The query surface returns source paths.
   - The next synthesis layer should preserve compact citations without becoming too verbose.

7. Handling failed Builder CLI exits in Telegram.
   - `runBuilderWikiStatus` and `runBuilderWikiQuery` can parse stdout from non-zero exits when present.
   - `runBuilderWikiInventory` currently assumes success. That is acceptable for healthy inventory, but could be hardened like status/query.

8. Docs manifest coverage.
   - This handoff should be added to `docs/manifests/spark_memory_kb_repo_sources.json` so future KB/wiki compiles can ingest it as project context.

## Recommended Next Slice

Run the live Telegram self-awareness/wiki regression on a real bot home, then use the resulting evidence to decide whether Telegram formatting, route traces, or wiki answer synthesis needs the next repair.

Suggested commands:

```bash
python -m spark_intelligence.cli self handoff-check --json
python -m spark_intelligence.cli self live-telegram-cadence --json
powershell -ExecutionPolicy Bypass -File scripts/run_live_telegram_self_awareness_wiki_probe.ps1 -SparkHome C:\Users\USER\.spark-intelligence -PrintPromptsOnly
powershell -ExecutionPolicy Bypass -File scripts/run_live_telegram_self_awareness_wiki_probe.ps1 -SparkHome C:\Users\USER\.spark-intelligence -OutputDir C:\Users\USER\.spark-intelligence\artifacts\live-telegram-regression -Json
```

## 2026-05-02 Live Evidence Status

The live Telegram self-awareness/wiki verifier has been hardened to write trace-eligibility diagnostics on failed runs. The latest run against `C:\Users\USER\.spark-intelligence` produced:

- `ok=false`
- `scanned_traces=80`
- `scanned_runtime_traces=0`
- `ignored_simulation_traces=80`
- `ignored_non_runtime_surface_traces=80`
- `ignored_non_telegram_request_traces=80`
- `missing=slash self`
- artifact: `C:\Users\USER\.spark-intelligence\artifacts\live-telegram-regression\latest.json`

Interpretation: the gate is working and refusing simulation/soak evidence. The next operator step is to run `-PrintPromptsOnly`, send the 12 prompts to the real Spark Telegram bot in order, then rerun the verifier with `-OutputDir ... -Json`.

`self live-telegram-cadence --json` also writes a ready-to-use operator runbook at:

```text
C:\Users\USER\.spark-intelligence\artifacts\live-telegram-regression\prompt-pack-latest.txt
```

Use that file for the live bot prompt sequence; it includes the 12 prompts, the wait-for-reply instruction, the anti-simulation evidence boundary, and the verifier command to run afterward.

The non-JSON `self live-telegram-cadence` output now also names the runbook path, latest missing case, eligible live trace count, and next action for operator-friendly handoff.

The generated verifier command includes `-SinceUtc <cadence checked_at>`. This is intentional: live evidence for a fresh run must come from Telegram traces recorded after the runbook/cadence was generated, not from older live traces.

## Continuation Prompt

Use this prompt in the next coding session:

```text
We are continuing Spark self-awareness and LLM wiki work across:
- C:\Users\USER\Desktop\spark-intelligence-builder
- C:\Users\USER\Desktop\spark-telegram-bot

Read this handoff first:
C:\Users\USER\Desktop\spark-intelligence-builder\docs\SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md

Current goal:
Run and harden the live Telegram self-awareness/wiki regression cadence. Spark should prove `/self`, `/wiki`, wiki candidate review, memory cognition boundaries, governed user memory, build-quality routing, and route explanations through real Telegram runtime traces before claiming a self-awareness release is live-green.

Important existing Builder surfaces:
- python -m spark_intelligence.cli self status --refresh-wiki --json
- python -m spark_intelligence.cli self heartbeat --json
- python -m spark_intelligence.cli self live-telegram-cadence --json
- python -m spark_intelligence.cli self handoff-check --json
- python -m spark_intelligence.cli wiki status --refresh --json
- python -m spark_intelligence.cli wiki heartbeat --json
- python -m spark_intelligence.cli wiki inventory --refresh --json
- python -m spark_intelligence.cli wiki query "<topic>" --refresh --json
- python -m spark_intelligence.cli wiki answer "<question>" --refresh --json

Important existing Telegram surfaces:
- /self
- /wiki
- /wiki candidates
- /wiki scan-candidates
- natural status/inventory/query phrases in src/conversationIntent.ts

Guardrails:
- Do not make canned deterministic self-awareness answers.
- Wiki is supporting project/system knowledge, not live truth.
- Live status, registry, traces, and route probes override wiki pages for mutable facts.
- Do not promote conversational residue into durable wiki/memory.
- Keep user-specific wiki notes separate and consent-gated.
- Preserve unrelated working tree changes, especially C:\Users\USER\Desktop\spark-telegram-bot\PROJECT.md.

Recommended next implementation:
1. Inspect current diffs and tests in both repos.
2. Run `self handoff-check --json` before code changes.
3. Run `self live-telegram-cadence --json` and follow its printed prompt/verifier commands against the real Spark Telegram bot.
4. Prefer the generated `artifacts\live-telegram-regression\prompt-pack-latest.txt` runbook for the live prompt sequence.
5. If the live verifier fails with `scanned_runtime_traces=0`, collect the real Telegram prompt pack first; do not repair code or claim live health from simulation traces.
6. If the live verifier fails after live traces exist, repair the exact route, formatting, or trace mismatch and rerun focused Builder/Telegram tests.
7. If the live verifier passes, save the artifact path in the handoff/hardening docs and only then claim live Telegram evidence.
8. Keep wiki/supporting reports separate from live runtime truth and governed current-state memory.
9. Run focused Builder and Telegram tests plus npm build when Telegram repo changes.
```

## Quick Verification Commands

Builder:

```bash
cd C:\Users\USER\Desktop\spark-intelligence-builder
python -m pytest tests/test_llm_wiki_bootstrap.py tests/test_self_awareness.py
python -m pytest tests/test_natural_language_route_eval_matrix.py
python -m spark_intelligence.cli self handoff-check --json
python -m spark_intelligence.cli self heartbeat --json
python -m spark_intelligence.cli self live-telegram-cadence --json
python -m spark_intelligence.cli wiki status --refresh --json
python -m spark_intelligence.cli wiki heartbeat --json
python -m spark_intelligence.cli wiki inventory --refresh --limit 5 --json
python -m spark_intelligence.cli wiki query "recursive self-improvement loops" --refresh --limit 3 --json
```

Telegram:

```bash
cd C:\Users\USER\Desktop\spark-telegram-bot
npm test -- tests/builderBridge.test.ts tests/conversationIntent.test.ts
npm run build
```

## Correct Handoff Path

```text
C:\Users\USER\Desktop\spark-intelligence-builder\docs\SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md
```
