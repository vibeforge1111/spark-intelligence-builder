# Focused Memory Execution Plan

Date: 2026-04-21
Status: active
Repos:
- `<workspace>\\spark-intelligence-builder`
- `<workspace>\\domain-chip-memory`

## Mission

Turn the current benchmark-strong Telegram memory into a governed persistent memory system that:

- remembers crucial user and project state across sessions
- preserves source truth and prior truth
- avoids overfitting to hand-written predicate lanes
- avoids storing conversational residue indiscriminately
- stays compact, explainable, and maintainable at scale

This plan does not restart the system.
It upgrades the current `dual_store_event_calendar_hybrid` runtime into a more general memory operating model.

## Current Truth

What is already strong:

- live Telegram current-state memory works on covered lanes
- overwrite, delete, previous-state, and event-history behavior are real
- mixed churn regression is live and green
- runtime, benchmark contract, and docs are aligned on `dual_store_event_calendar_hybrid`
- `raw_episode`, `structured_evidence`, `current_state`, `event`, and `belief` are all real runtime lanes
- open recall now exists for current truth, evidence, raw episodes, and beliefs
- belief supersession, invalidation, revalidation, and archival are live
- structured evidence can now consolidate into active-state memory for:
  - `current_blocker`
  - `current_dependency`
  - `current_constraint`
  - `current_risk`
  - `current_status`
  - `current_owner`
- local Telegram memory coverage is broad and increasingly lifecycle-aware

What is still missing:

- memory capture is still too shaped by named predicates and route-specific logic
- consolidation is still too targeted and heuristic-shaped rather than policy-shaped
- long-run retention, compaction, and rebuild are not yet unified into a clean maintenance loop
- live regression reliability is not strong enough; some focused live packs still hang silently
- false-positive and false-negative promotion audits are not yet strong enough
- current-state rebuild from durable evidence is not yet proven end to end

## Completion Estimate

Working estimate as of `2026-04-22`:

- Telegram-first production-useful memory: `~75%`
- full governed memory methodology target: `~60%`

This means the system has crossed out of "memory demo" territory and into "real memory subsystem" territory, but it is not yet a finished general memory architecture.

## Right Now Plan

The next sequence should optimize for pride-worthy system quality, not just more covered predicates.

1. Generalize promotion policy
- replace the growing pile of evidence-to-state special cases with a cleaner promotion policy for active state
- keep the existing successful lanes, but stop adding isolated one-off logic where a reusable promotion rule would do

2. Make maintenance a first-class loop
- unify belief invalidation, belief decay, evidence archival, and raw-episode archival into one governed maintenance model
- make it easy to explain why a record is still active, archived, or deleted

3. Prove rebuild and correction fidelity
- ensure current state can be reconstructed from evidence and lifecycle history
- make corrections, deletes, supersession, and restores survive rebuild cleanly

4. Fix live validation operability
- investigate why some focused live regression packs hang silently
- make live validation trustworthy enough to use as a real gate, not just a best-effort smoke

5. Expand trace auditing
- add explicit audits for:
  - false-positive promotions
  - false-negative promotions
  - stale state that should have been downgraded
  - residue that should never have become durable memory

## Telegram Gateway Note

Current production Telegram ingress remains in the standalone private repo:

- `spark-telegram-bot`

Builder should not take over Telegram webhook ownership until it reaches full parity with the live gateway contract. The migration shape is documented in:

- `docs/TELEGRAM_GATEWAY_MIGRATION_PLAN_2026-04-21.md`

## Diagnosis

The current system has improved from narrow profile/event memory into a broader governed lifecycle system.
But the remaining risk is the same one the research warned about:

- too much route-shaped capture
- not enough generic keepability logic
- not enough retention and compaction policy

The target is not "remember everything."
The target is:

- capture broadly at the episode layer
- promote selectively into typed memory roles
- route retrieval by memory intent
- decay or compact what no longer deserves durable salience

## Locked Invariants

These are non-negotiable:

1. Every durable memory must trace back to source evidence.
2. Current truth and prior truth must both remain recoverable.
3. Derived belief must never masquerade as source truth.
4. The hot path must stay small.
5. Per-user scope must be enforced at write and read time.
6. Deletes, supersession, and restores must be first-class lifecycle operations.
7. Product memory and benchmark memory must stay the same architecture.

## Memory Roles

The runtime memory system has these first-class roles:

1. `working_memory`
- small, visible, session-local
- short TTL

2. `raw_episode`
- append-only interaction ground truth
- source for reconstruction, provenance, and audits

3. `structured_evidence`
- extracted memory units with source turn, source session, timestamps, and confidence
- main retrieval unit for most durable memory

4. `current_state`
- latest stable truth for mutable or profile facts
- rebuilt from evidence plus lifecycle operators

5. `event`
- plans, deadlines, meetings, trips, commitments, chronology
- time-aware and history-preserving

6. `belief`
- derived reflections, summaries, or lessons
- always marked derived
- must be invalidatable and re-verifiable

## Retention Classes

Every stored unit must carry a retention class.

1. `session_ephemeral`
- keep in `working_memory` only
- do not persist beyond session unless promoted
- examples: momentary tactics, transient tool state, greeting context

2. `episodic_archive`
- persist as `raw_episode`
- cheap append-only storage
- not always retrieved
- examples: user turns, assistant turns, exact interaction history

3. `active_state`
- persist as `current_state`
- high retrieval priority while live
- examples: current goal, current blocker, current owner, current risk

4. `durable_profile`
- persist as `structured_evidence` plus optional `current_state`
- long retention, low churn
- examples: identity facts, stable preferences, relationships, timezone

5. `time_bound_event`
- persist as `event`
- active while upcoming/relevant, demoted after staleness threshold
- examples: meeting dates, launch deadlines, trips, commitments with dates

6. `derived_belief`
- persist only if high value and source-grounded
- shorter TTL than evidence
- examples: recurring user style preference summary, project pattern summary

7. `ops_residue`
- reject or quarantine
- never promote into user memory
- examples: tool noise, hashes, runtime residue, generic operational chatter

## Keepability Contract

No unit should become durable memory unless it passes all applicable gates:

1. `relevance`
- matters to future assistance, planning, personalization, or recall

2. `specificity`
- concrete enough to retrieve usefully later

3. `attribution`
- tied to a source turn or source event

4. `scope`
- belongs to the right user, agent, app, and session context

5. `role_fit`
- clearly belongs to `event`, `current_state`, `structured_evidence`, or `belief`

6. `retention_fit`
- has an explicit reason to survive beyond the immediate moment

7. `safety`
- not pure residue, unsafe PII handling, or irrelevant speculative clutter

If a unit fails:

- drop it
- or keep it only as `raw_episode`
- but do not promote it into durable salience

## Promotion Rules

These rules should govern every turn.

### Turn intake

On each meaningful user turn:

- store or reference the turn in `raw_episode`
- run generic candidate extraction
- classify candidates by memory role

### Promotion from `raw_episode` to `structured_evidence`

Promote when:

- the candidate is likely useful later
- it is concrete and attributable
- it is not merely conversational filler

### Promotion from `structured_evidence` to `current_state`

Promote when:

- the candidate states current truth
- it is mutable or profile-relevant
- the system can define overwrite/supersession behavior

### Promotion to `event`

Promote when:

- the candidate encodes chronology, scheduling, deadline, commitment, or sequence-sensitive change

### Promotion to `belief`

Promote only offline or asynchronously when:

- multiple evidence items support a higher-order summary
- the summary is useful enough to retrieve
- derivation and invalidation remain explicit

## Query Routing Contract

Route memory questions by intent, not by predicate family.

The first required intents are:

- `current_truth`
- `prior_truth`
- `event_history`
- `preference`
- `identity_or_profile`
- `relationship`
- `summary`
- `why_do_you_think_that`
- `abstain`

Retrieval order should be:

1. `current_state` for current truth
2. `event` and temporal filters for chronology
3. `structured_evidence` for grounded factual recovery
4. `raw_episode` only for exact reconstruction or conflict resolution
5. `belief` only when marked derived and source-supported

## Repo Split

`domain-chip-memory` owns:

- memory object model
- role-clean contracts
- lifecycle operators
- temporal and supersession semantics
- retention and compaction primitives
- consolidation and maintenance logic
- provenance surfaces
- benchmark harnesses

`spark-intelligence-builder` owns:

- Telegram ingestion policy
- generic keepability gating
- per-user scope wiring
- query classification and routing
- reply formatting and abstention behavior
- live regression and soak validation

## Phase Plan

### Phase 1: Lock The Methodology Into Runtime Contracts

Main repo:
- `domain-chip-memory`

Deliverables:
- explicit role contract for every stored and retrieved unit
- retention class contract
- lifecycle metadata contract:
  - `created_at`
  - `document_time`
  - `event_time`
  - `valid_from`
  - `valid_to`
  - `supersedes`
  - `conflicts_with`
  - `deleted_at`
- explanation payloads that reveal role, provenance, and derivation status

Exit:
- no retrieved unit is untyped
- current state, evidence, event, and belief are distinct at runtime

### Phase 2: Replace Predicate-Shaped Capture With Generic Candidate Capture

Main repo:
- `spark-intelligence-builder`

Deliverables:
- generic turn-to-candidate extraction
- keepability scoring or rule gating
- promotion outcomes:
  - drop
  - `raw_episode` only
  - `structured_evidence`
  - `current_state`
  - `event`
  - `belief_candidate`
- first domain packs:
  - people and relationships
  - goals and priorities
  - project state
  - plans and commitments
  - dates and deadlines
  - preferences and identity
  - corrections and reversals

Exit:
- live Telegram capture no longer depends mainly on named predicate lanes

### Phase 3: Make Retention And Decay Real

Main repo:
- both

Deliverables:
- explicit TTL or review windows per retention class
- event staleness policy
- belief revalidation policy
- low-value evidence compaction rules
- delete and restore behavior that survives compaction

Exit:
- the system can say why something is still retained
- stale state no longer survives only because it was once written

### Phase 4: Consolidation And Rebuild

Main repo:
- `domain-chip-memory`

Deliverables:
- offline promotion from episode to evidence
- evidence-to-current-state rebuild
- derived belief generation with provenance
- rebuild tooling for one user or all users

Exit:
- current state can be reconstructed from the durable evidence trail

### Phase 5: Query Routing By Intent

Main repo:
- `spark-intelligence-builder`

Deliverables:
- intent classifier for memory queries
- route-specific retrieval plans
- compact grounded answer formatting
- explicit abstention when evidence is weak

Exit:
- fewer handcrafted fact-family branches
- broader phrasing still resolves to the right operator

### Phase 6: Long-Run Proof

Main repo:
- both

Deliverables:
- multi-day Telegram trace audits
- retention-behavior regression packs
- mixed event plus profile plus state churn packs
- quality gates for noise, drift, correction fidelity, and delete fidelity

Exit:
- memory quality remains coherent under longer realistic use

## Validation Loop

Every serious phase must pass:

1. targeted repo tests
2. benchmark checks in `domain-chip-memory`
3. Builder regression packs
4. focused Telegram smoke on the live home
5. trace audit for false-positive durable writes

Core commands:

```powershell
python -m pytest
python -m spark_intelligence.cli memory run-telegram-regression --home .tmp-home-live-telegram-real --json
python -m spark_intelligence.cli memory benchmark-architectures
python -m spark_intelligence.cli memory soak-architectures --runs 5
```

## Stop-Ship Gates

Do not call the system "great persistent memory" until all are true:

- generic candidate capture is live
- retention classes are explicit
- belief is first-class and clearly derived
- current truth, prior truth, and exact source recall all work
- delete and restore semantics are reliable
- long-run trace audits show low residue promotion
- benchmark and product memory remain the same architecture

## Immediate Next Build Order

1. `spark-intelligence-builder`: investigate and fix the silent-hang behavior in focused live Telegram regression packs
2. `spark-intelligence-builder`: replace targeted evidence-to-state heuristics with a reusable active-state promotion policy
3. `domain-chip-memory`: add maintenance primitives for archive, decay, revalidation, and rebuild that are role-clean and inspectable
4. `spark-intelligence-builder`: add trace-audit packs for false-positive promotion, false-negative promotion, and stale-state drift
5. `domain-chip-memory` plus `spark-intelligence-builder`: prove current-state rebuild from durable evidence plus lifecycle history

## Reference

Detailed methodology and rationale:

- [MEMORY_METHODOLOGY_UPGRADE_2026-04-21](docs/MEMORY_METHODOLOGY_UPGRADE_2026-04-21.md)
