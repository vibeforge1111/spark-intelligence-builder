# Spark Memory And LLM Wiki Cognition Integration

Date: 2026-05-01
Repo: `spark-intelligence-builder`
Related systems: `domain-chip-memory`, `spark-telegram-bot`

## Objective

Make Spark's memory layers and LLM wiki work as one cognitive system without collapsing their authority boundaries.

The goal is not to make the wiki another memory store. The goal is:

- memory keeps user, time, evidence, current-state, and historical truth grounded
- the LLM wiki keeps system, project, route, tool, and operating knowledge navigable
- hybrid retrieval assembles both into source-labeled context packets
- the LLM reasons over the packet conversationally, while naming what is live, remembered, wiki-backed, inferred, stale, or unknown

## Current Live Shape

Builder already has a meaningful integration path:

- `domain-chip-memory` owns the runtime memory substrate and SDK.
- Builder pins runtime memory to `summary_synthesis_memory` with `heuristic_v1`.
- Builder memory exposes current state, historical state, evidence, events, recent conversation, pending tasks, procedural lessons, Graphiti shadow sidecar hits, and wiki packets through `hybrid_memory_retrieve`.
- The new LLM wiki writes Markdown pages under `$SPARK_HOME/wiki`.
- `hybrid_memory_retrieve` automatically checks `$SPARK_HOME/wiki`, `$SPARK_HOME/knowledge`, and `$SPARK_HOME/diagnostics` when present.
- Wiki packets enter the context packet as lane `wiki_packets`, source class `obsidian_llm_wiki_packets`, section `compiled_project_knowledge`, authority `supporting_not_authoritative`.
- For project/system questions, wiki packets get a project-knowledge intent boost, while generic events/evidence/recent conversation get a penalty.
- Telegram cold memory formatting already filters broad wiki/diagnostic packets from ordinary chat context so project diagnostics do not hijack normal conversation.

This is the right foundation. The missing work is cohesion, review, and explicit routing policy between the old memory KB compiler and the newer LLM wiki pages.

## Layer Ownership

| Layer | Owns | Does Not Own | Authority |
| --- | --- | --- | --- |
| Hot turn context | newest user intent, exact current request, attached artifacts | durable user truth | highest for the current turn |
| Current-state memory | mutable user/project facts that are currently true | system doctrine | authoritative for scoped mutable facts |
| Historical memory | facts as of a past time | current truth | authoritative only for the requested time window |
| Structured evidence | source-backed observations and event support | final answer policy | supporting, stronger when linked to current state |
| Recent conversation | continuity and immediate references | stable identity or system truth | supporting and query-gated |
| Pending tasks | interrupted work and resumable obligations | general memory | workflow recovery authority |
| Procedural lessons | operating corrections from prior failures | user identity | advisory |
| Graphiti sidecar | entity/time relationship hints | authoritative current state | shadow/supporting until promoted |
| LLM wiki | system maps, route contracts, tool cards, project docs, improvement doctrine | live health, mutable user facts, provider truth | supporting_not_authoritative |
| Memory KB vault | visible compiled memory pages from governed SDK snapshots | independent truth outside memory | downstream of governed memory |

## Existing Memory KB And LLM Wiki Relationship

There are two wiki-like surfaces now:

1. Memory KB vault from `domain-chip-memory`
   - Builder command: `memory compile-telegram-kb`.
   - Output shape: Karpathy-style vault with `raw/` and `wiki/`.
   - Pages: `wiki/current-state`, `wiki/evidence`, `wiki/events`, `wiki/sources`, `wiki/syntheses`, `wiki/outputs`.
   - Purpose: user-visible compiled memory from governed SDK snapshots.
   - Authority: downstream of memory, not independent.

2. Builder LLM wiki
   - Builder commands: `wiki bootstrap`, `wiki compile-system`, `wiki query`, `wiki answer`, `wiki promote-improvement`.
   - Output shape: `$SPARK_HOME/wiki`.
   - Pages: system maps, route maps, self-awareness contract, generated system status, capability index, live route index, self-awareness gaps, candidate improvement notes.
   - Purpose: persistent system/project/route/tool knowledge for LLM retrieval.
   - Authority: supporting_not_authoritative.

These should interoperate through one retrieval mental model, but they should not be merged into one undifferentiated truth store.

## Desired Cognitive Flow

When Spark answers a user:

1. Interpret the user's intent and stakes.
2. Load hot turn context.
3. If user/project facts matter, query current-state or historical memory first.
4. If system/project/tool/route knowledge matters, query LLM wiki packets.
5. If evidence or past decisions matter, query structured evidence/events.
6. Build a context packet with sections and authority labels.
7. Let the LLM synthesize from the packet, not recite deterministic scripts.
8. State uncertainty and next probes when evidence is missing, stale, or supporting-only.
9. Only promote new memory/wiki knowledge through the right lane and gate.

The answer should feel intelligent because Spark can combine context, memory, wiki, and live evidence. It should not sound canned because no single layer gets to supply a fixed answer.

## Required Separation Rules

- Wiki pages can explain how memory works, but cannot answer "what do you remember about me?" unless current-state memory or scoped evidence is attached.
- Current-state memory can answer mutable user facts, but cannot become global Spark doctrine.
- Recent conversation can help with references, but cannot promote itself into user identity without salience and retention gates.
- Candidate wiki notes can capture possible system improvements, but cannot become verified doctrine until they have source refs, evidence refs, tests/probes, and rollback/invalidation conditions.
- Memory KB pages compiled from SDK snapshots should carry memory authority metadata, while Builder LLM wiki pages carry system/project/supporting metadata.
- Graph sidecars can improve relational recall, but stay shadow/supporting until evaluation proves they improve the selected memory architecture.

## Objective Gaps

1. Unified navigation is missing.
   - The old Memory KB vault and the new Builder LLM wiki are both retrievable as Markdown packets, but there is no manifest that tells Spark which page families represent memory-derived current state versus system/project doctrine.

2. Source class is too coarse.
   - Both old and new pages can enter as `obsidian_llm_wiki_packets`.
   - We need metadata such as `wiki_family`, `owner_system`, `authority`, `freshness`, `scope`, `last_verified_at`, and `source_of_truth`.

3. User-aware wiki is not consent-gated yet.
   - The plan names user/environment wiki, but user-specific wiki writes must be separate from global doctrine and should require explicit consent or a stable memory-derived source.

4. Promotion lanes are incomplete.
   - `wiki promote-improvement` writes candidate notes with evidence refs, but there is not yet a review inbox, invalidation ledger, or contradiction scan that compares candidates against memory/current state.

5. Context packet gates are trace-only.
   - Hybrid retrieval emits promotion gates like source-swamp resistance, stale-current conflict, and recent-conversation noise. These are currently trace evidence and should become acceptance gates for memory/wiki promotion paths.

6. Old KB compiler outputs are not part of the new self-awareness inventory.
   - `self status` knows the wiki can be refreshed and queried, but it does not yet report whether the memory KB vault exists, how fresh it is, or which memory page families are retrievable.

## Cohesion Tasks

| ID | Owner | Task | Acceptance |
| --- | --- | --- | --- |
| MWI-001 | builder | Add `wiki_family` metadata when Builder converts wiki packet hits into memory records | context packets can distinguish `builder_llm_wiki`, `memory_kb_current_state`, `memory_kb_evidence`, `memory_kb_synthesis`, and `diagnostics` |
| MWI-002 | builder | Add memory KB vault discovery to `wiki status` and `self status` | Spark reports whether `$SPARK_HOME/artifacts/spark-memory-kb/wiki` or configured KB paths are present and retrievable |
| MWI-003 | builder | Add a unified memory/wiki inventory surface | one command lists page families, authority, freshness, owner system, and source path counts |
| MWI-004 | builder | Add a context-packet authority policy test for wiki/current-state conflicts | wiki packets cannot outrank current-state memory for mutable user facts |
| MWI-005 | builder | Add candidate wiki inbox and contradiction scan | candidate notes that conflict with current-state memory or verified system status stay candidate/blocked |
| MWI-006 | telegram | Add live natural-language tests for memory/wiki combined questions | "what do you know about me vs your systems?" routes to memory plus wiki without blending the authorities |
| MWI-007 | domain-chip-memory | Expose richer wiki packet contract metadata | packet reader returns parsed frontmatter authority, owner_system, type, status, freshness, and source_class/family |
| MWI-008 | builder | Add self-awareness fields for memory system cognition | `self status` names active runtime architecture, memory lanes, wiki packet status, graph sidecar status, and current gaps |
| MWI-009 | builder | Add source-mix acceptance gates for wiki-backed answer promotion | wiki-backed answers with supporting-only packets cannot be promoted as verified memory |
| MWI-010 | builder | Add user consent lane for personalized wiki pages | user/environment wiki writes are labeled user-scoped and never become global Spark doctrine by default |

## Natural-Language Behaviors To Test

- "What do you know about me and what is only recent context?"
  - Must use current-state memory and recent conversation labels.
  - Must not answer from system wiki alone.

- "How does your memory work with your wiki?"
  - Should use LLM wiki/system docs plus memory runtime status.
  - Should name authority boundaries.

- "Can you improve your memory of this project?"
  - Should inspect project context, memory lanes, and wiki pages.
  - Should propose a probe-first plan before writing durable memory.

- "Save this as a permanent rule about me."
  - Should route through user memory salience/consent.
  - Should not write global wiki doctrine.

- "Promote this wiki note: I always prefer X."
  - Should reject or redirect to user-scoped memory/wiki candidate lane.

- "Use the wiki as the final truth for what I currently want."
  - Should refuse the authority inversion and query current-state memory.

## Recommended Next Build Slice

1. Add Builder-side wiki packet family classification.
2. Add tests proving memory current-state outranks wiki packets for user facts.
3. Add memory KB vault discovery to `wiki status`.
4. Add self-awareness memory cognition fields:
   - runtime architecture
   - runtime provider
   - memory lanes available
   - wiki packet retrieval status
   - graph sidecar status
   - memory KB vault freshness
5. Add Telegram natural-language live cases for combined memory/wiki questions.

This is the best next slice because it creates visible intelligence without mutating memory doctrine or touching the dirty `domain-chip-memory` worktree.

## Stop-Ship Gates

- Do not let `obsidian_llm_wiki_packets` overwrite current-state memory.
- Do not promote wiki candidate notes into verified memory without evidence and lane checks.
- Do not store user preferences as global Spark system doctrine.
- Do not treat Graphiti shadow hits as authoritative.
- Do not make wiki-backed answers deterministic scripts.
- Do not merge Memory KB and Builder LLM wiki pages without source family metadata.
- Do not claim live health from generated pages; run live status/probes.

