# Spark Memory System Feedback Packet

Date: 2026-05-01
Primary repo: `spark-intelligence-builder`
Review partner repo: `domain-chip-memory`
Related repos: `spark-telegram-bot`, `spark-memory-quality-dashboard`

## Why This Exists

We are building Spark self-awareness, LLM wiki support, and memory cognition as one connected system.

Another terminal reviewing `domain-chip-memory` should understand what Builder/Telegram are trying to do before changing or pushing memory-system work. This packet explains:

- how Builder plugs into Telegram, memory, wiki, and dashboard surfaces
- how the LLM wiki currently works
- how Builder currently uses memory
- how we want memory and wiki to cooperate next
- what we need from `domain-chip-memory`
- what the memory dashboard should receive

## Current Intent

Spark should not answer self-awareness questions from deterministic scripts.

Spark should answer by combining:

1. current user intent
2. live registry/system status
3. current-state and historical memory
4. structured evidence and event traces
5. recent conversation when query-gated
6. pending tasks and procedural lessons
7. LLM wiki project/system knowledge
8. graph sidecar hints when enabled and still supporting
9. natural LLM synthesis over source-labeled context

The answer should sound aware because Spark can reason over evidence and surrounding systems, not because a fixed template says "I know myself."

## Plugs And Adapters

### Telegram To Builder

`spark-telegram-bot` is the channel adapter.

Important surfaces:

- `/self` calls Builder self-awareness status.
- `/wiki` calls Builder LLM wiki status.
- `/wiki pages` calls wiki inventory.
- `/wiki search <topic>` calls wiki query.
- `/wiki answer <question>` and natural wiki-answer phrasing call wiki-backed answer.
- natural self-awareness and self-improvement phrases route to Builder.

Bridge file:

- `spark-telegram-bot/src/builderBridge.ts`

Telegram should remain a router and formatter. It should not own memory doctrine, wiki authority, or self-awareness truth.

### Builder As Runtime Integrator

`spark-intelligence-builder` owns the runtime integration surface:

- system registry
- context capsule
- self-awareness capsule
- observability events
- provider/channel/route status
- Builder CLI commands
- memory bridge calls into `domain-chip-memory`
- LLM wiki bootstrap/query/answer/promotion commands

Important Builder commands:

```bash
python -m spark_intelligence.cli self status --refresh-wiki --json
python -m spark_intelligence.cli self improve "Improve memory/wiki cognition" --refresh-wiki --json
python -m spark_intelligence.cli wiki status --refresh --json
python -m spark_intelligence.cli wiki inventory --refresh --json
python -m spark_intelligence.cli wiki query "route tracing" --refresh --json
python -m spark_intelligence.cli wiki answer "How should memory and wiki work together?" --refresh --json
python -m spark_intelligence.cli memory inspect-capsule --query "what do you know about me?" --json
```

Builder should not absorb the internals of `domain-chip-memory`. It should call memory through contracts and preserve authority labels.

### Memory Chip

`domain-chip-memory` owns:

- SDK memory substrate
- current-state and historical state
- structured evidence and events
- salience, promotion, retention, supersession, decay, resurrection, and maintenance semantics
- benchmark and memory architecture experiments
- wiki packet reader contract
- graph sidecar adapters

Builder should treat this as the memory authority and should not duplicate durable memory logic.

### LLM Wiki

Builder's LLM wiki lives under:

```text
$SPARK_HOME/wiki
```

Current Builder wiki commands:

- `wiki bootstrap`
- `wiki compile-system`
- `wiki status`
- `wiki inventory`
- `wiki query`
- `wiki answer`
- `wiki promote-improvement`

Current retrieval path:

```text
Builder wiki command
-> build_llm_wiki_query
-> hybrid_memory_retrieve
-> wiki_packets lane
-> domain-chip-memory retrieve_markdown_knowledge_packets
-> source-labeled hits
-> LLM answer synthesis or Telegram formatter
```

The wiki is `supporting_not_authoritative`.

It can explain systems, routes, tools, projects, and improvement doctrine. It cannot decide mutable user facts or live provider/channel health.

### Memory Dashboard

`spark-memory-quality-dashboard` should be the human/agent observability surface for memory movement.

It should receive:

- memory write decisions
- salience scores and reasons
- accepted and blocked candidates
- lane decisions
- promotion disposition
- lifecycle transitions
- supersession/archive/resurrection samples
- context packet source mix
- wiki packet usage
- graph sidecar hits and status
- recall answers and source explanations

The dashboard should help users and agents see memory as movement, not as static tables.

## How Builder Uses Memory Right Now

Builder currently uses `domain-chip-memory` through:

- `inspect_memory_sdk_runtime`
- `write_profile_fact_to_memory`
- `write_structured_evidence_to_memory`
- `write_raw_episode_to_memory`
- `write_belief_to_memory`
- `lookup_current_state_in_memory`
- `lookup_historical_state_in_memory`
- `retrieve_memory_evidence_in_memory`
- `retrieve_memory_events_in_memory`
- `inspect_human_memory_in_memory`
- `hybrid_memory_retrieve`
- `run_memory_sdk_maintenance`
- session/day/project summary writers

`hybrid_memory_retrieve` currently combines:

- current state
- entity current state
- historical state
- evidence
- events
- recent conversation
- pending tasks
- procedural lessons
- typed temporal graph sidecar hits
- wiki packets

Important authority behavior:

- current-state memory outranks wiki for mutable user/project facts
- wiki packets enter as `compiled_project_knowledge`
- wiki packets are `supporting_not_authoritative`
- Graphiti/typed graph hits are supporting/shadow unless explicitly promoted by evals
- context packet gates currently trace source-swamp, stale-current conflict, recent-conversation noise, and source-mix stability

## How We Want To Use Memory Further

Next direction:

1. Add memory cognition to self-awareness.
   - active memory architecture
   - runtime provider
   - memory lane map
   - wiki packet status
   - graph sidecar status
   - memory KB vault status
   - current memory gaps

2. Add wiki packet family metadata.
   - `builder_llm_wiki`
   - `memory_kb_current_state`
   - `memory_kb_evidence`
   - `memory_kb_synthesis`
   - `diagnostics`
   - `improvement_candidate`

3. Make current-state vs wiki conflicts testable.
   - wiki can support a memory answer
   - wiki cannot override mutable user facts
   - wiki cannot claim provider/channel health without live probes

4. Make candidate wiki notes reviewable.
   - candidate inbox
   - contradiction scan
   - stale page health
   - invalidation ledger
   - promotion gate evidence

5. Connect memory lifecycle movement to dashboard.
   - accepted/blocked/promoted/decayed/archived/resurrected
   - source explanation for recalls
   - context packet packetization and dropped items
   - graph sidecar advisory hits
   - wiki packet participation in answers

## What We Need From `domain-chip-memory`

Please review whether `domain-chip-memory` can expose or confirm these contracts:

1. Rich wiki packet metadata
   - parsed frontmatter
   - `authority`
   - `owner_system`
   - `type`
   - `status`
   - `freshness`
   - `wiki_family`
   - source path

2. Memory KB vault family detection
   - current-state pages
   - evidence pages
   - event pages
   - syntheses
   - outputs
   - raw snapshot provenance

3. Lifecycle status API/export
   - stale preserved
   - superseded
   - archived
   - deleted
   - resurrected
   - decay delta
   - replacement/deletion pointers

4. Sidecar health and provenance
   - Graphiti/Kuzu enabled/configured/disabled status
   - validity windows
   - provenance episodes
   - advisory/supporting authority
   - fallback mode

5. Source-aware episodic recall support
   - session/day/project summaries
   - raw event reservoir
   - source labels
   - "what changed today?"
   - "what did we promise?"
   - "what else do you remember?"

6. Dashboard export shape
   - stable JSON fields for lifecycle transitions
   - salience decisions
   - context packet source mix
   - selected/dropped recall items
   - graph/wiki participation

7. Acceptance gates
   - current-state beats wiki for mutable facts
   - supporting-only source mix warns before promotion
   - Graphiti remains additive until evals pass
   - candidate wiki notes cannot become verified memory without evidence

## Feedback Requested From The Other Terminal

Please answer:

1. Does the current `domain-chip-memory` ahead-of-main work align with this integration model?
2. Which contract should Builder implement first: wiki family metadata, memory KB discovery, memory cognition in self-status, or dashboard export wiring?
3. Are any Builder assumptions wrong about memory authority, SDK lanes, Graphiti sidecar status, or wiki packet retrieval?
4. What fields should `domain-chip-memory` expose so Builder does not parse paths or infer families ad hoc?
5. What dashboard export fields are stable enough to depend on?
6. Which tests should become cross-repo stop-ship gates?
7. Is it safe to push the current `domain-chip-memory` ahead commits to `main` after the focused test pass?

## Current Validation Evidence

Latest read-only memory check:

```text
repo: C:\Users\USER\Desktop\domain-chip-memory
status: main...origin/main [ahead 15]
validation: python -m pytest tests/test_sdk.py tests/test_memory_sidecars.py
result: 43 passed, 1 warning
```

The warning came from Graphiti/Pydantic deprecation noise, not a failed Spark contract.

## Relevant Builder Docs

- `docs/SPARK_MEMORY_WIKI_COGNITION_INTEGRATION_2026-05-01.md`
- `docs/SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md`
- `docs/SPARK_LLM_WIKI_ARCHITECTURE_PLAN_2026-05-01.md`
- `docs/SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md`

## Suggested Prompt For Other Terminal

```text
You are reviewing Spark memory/wiki/self-awareness integration across:
- C:\Users\USER\Desktop\domain-chip-memory
- C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574
- C:\Users\USER\AppData\Local\Temp\spark-telegram-live-wiki-answer-clean-f7a2e254

Read first:
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574\docs\SPARK_MEMORY_SYSTEM_FEEDBACK_PACKET_2026-05-01.md
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574\docs\SPARK_MEMORY_WIKI_COGNITION_INTEGRATION_2026-05-01.md
C:\Users\USER\Desktop\domain-chip-memory\docs\SPARK_BUILDER_MEMORY_WIKI_FEEDBACK_REQUEST_2026-05-01.md

Goal:
Review whether domain-chip-memory is exposing the right contracts for Builder self-awareness, LLM wiki retrieval, memory KB discovery, Graphiti/sidecar status, and dashboard traceability.

Please produce:
1. Contract mismatches.
2. Missing fields/APIs.
3. Highest-leverage next implementation slice.
4. Cross-repo tests that should block promotion.
5. Whether domain-chip-memory ahead commits are safe to push to main.

Guardrails:
- Do not make wiki authoritative over current-state memory.
- Do not promote conversational residue.
- Do not treat Graphiti sidecar hits as authoritative until acceptance probes pass.
- Preserve user-specific memory separate from global Spark doctrine.
- Prefer source-labeled LLM synthesis over canned deterministic answers.
```

