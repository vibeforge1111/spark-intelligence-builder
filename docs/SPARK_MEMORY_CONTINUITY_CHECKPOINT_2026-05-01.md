# Spark Memory Continuity Checkpoint

Date: 2026-05-01
Status: implementation checkpoint

## Research Input

This checkpoint treats SOTA research as pressure, not as a template to copy.

Useful external patterns:

- Hermes separates tiny hot prompt memory from searchable SQLite session recall.
- Mem0 emphasizes multi-level user, session, and agent memory plus hybrid retrieval.
- Letta/MemGPT separates in-context core memory from out-of-context archival and recall memory.
- Generative Agents separates observation, reflection, and planning over a complete experience stream.
- MemGovern turns raw human experience into governed, reusable experience cards instead of dumping unstructured history into prompts.

Spark's version should stay Spark-native:

- raw turns are source-aware episodic evidence, not durable truth
- durable state moves through typed roles, retention classes, promotion, decay, and rebuild
- procedural improvement comes from ACP/domain-chip/specialization/autoloop outputs that survive benchmark and transfer gates
- dashboards must show movement and lineage, not only totals

## What Is Already Built

- Style-aware self-awareness shipped in Builder and Telegram.
- Telegram runtime now captures user and assistant turns as `memory_turn_captured` raw episodes.
- Recent captured turns feed hybrid recent-conversation recall, context capsules, researcher prompt context, and session summaries.
- The memory dashboard shows human and agent views of captured, blocked, promoted, saved, decayed, summarized, and retrieved movement.
- Dashboard movement paths now expose lineage such as captured -> summarized and saved -> retrieved.
- Structured evidence promotion into current state now emits explicit policy decisions, including held-as-evidence blocks and corroborated promotions.
- `memory audit-promotions` reviews those policy decisions for held evidence, later-resolved holds, false-positive risk, false-negative risk, and trace gaps.
- `memory record-feedback` and `memory review-feedback` give operator judgments their own traceable event lane, so real feedback can join back to a memory decision without becoming durable memory truth.
- `memory feedback-benchmarks` turns real memory feedback into correction, coverage-gap, source-quality, and positive-control benchmark cases without promoting feedback into memory truth.
- `memory explain-source` returns a compact answer-source packet for a query, including selected source class, authority, confidence, source mix, stale-current gates, selected evidence, ignored evidence, and promotion trace.
- Telegram `/memory` renders movement counts and movement paths concisely.
- Existing runtime lanes include raw episodes, structured evidence, current state, events, and beliefs.
- Existing plans already lock the right invariants: source evidence, current/prior truth, derived-belief labeling, compact hot path, per-user scope, lifecycle operations, and product/benchmark architecture parity.

## What Remains

- Generic candidate capture still needs to replace too much route-shaped predicate logic.
- Explicit session search needs to become a first-class cold episodic recall surface.
- Source-aware recall now has a Builder packet surface, but Telegram and dashboard still need first-class answer-source views.
- Promotion needs broader conversion from scattered heuristics into reusable policy objects.
- Promotion trace audits now have a first CLI surface, but still need deeper item-level review packs and benchmark integration.
- Feedback now has a benchmark-case Builder packet, but the dashboard and regression runner still need to consume it as correction-fidelity evidence.
- Retention, decay, revalidation, archival, and rebuild need one inspectable maintenance loop.
- False-positive promotion, false-negative promotion, and stale-state drift need trace-audit packs.
- Runtime promotion of typed conversational lanes needs real-LLM answer evaluation, not only heuristic shadow passes.
- ACP/specialization outputs need to connect to memory as governed procedural evidence, not self-mutating skills.

## Completed Implementation Steps

First-class Builder session search:

- search source-aware captured turns and memory summaries
- group results by session
- return snippets with matched terms, role, source, event id, and authority
- record the search as `memory_read_succeeded` so dashboards show retrieved movement

This is the minimal cold-recall layer Spark needs before richer promotion and compaction distillation.

Promotion policy traceability:

- evaluate structured evidence against `structured_evidence_current_state_v1`
- record `memory_promotion_evaluated` events for blocked and promoted decisions
- expose promotion policy fields in the memory dashboard agent trace
- preserve the rule that uncorroborated volatile state stays as structured evidence unless the field is explicitly high-confidence
- audit promotion decisions with `memory audit-promotions`

Traceable operator feedback:

- record memory feedback as `memory_feedback_recorded` Builder events with verdict, note, surface, target event id, target trace ref, and expected outcome
- keep feedback separate from movement counts so dashboard reviews do not confuse feedback residue with captured/saved memory
- expose a feedback summary inside `memory dashboard`
- expose a focused feedback review packet with recent feedback and unreviewed memory decisions
- convert feedback into benchmarkable cases with expected outcomes, source packets, target event lineage, and authority boundaries

Source-aware answer packets:

- explain which source class would support an answer for a query
- label source authority as authoritative, episodic/supporting, advisory, or none
- expose why a source won, source mix counts, stale-current/source-mix gates, and selected/ignored evidence previews
- keep the packet as evidence metadata only; it does not become durable truth by itself

## Current Next Step

Wire feedback benchmark cases into the dashboard and regression runner, then connect promotion audits to maintenance/rebuild checks so Spark can prove correction fidelity, stale-state drift handling, and promotion quality over long runs.
