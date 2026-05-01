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
- Telegram `/memory` renders movement counts and movement paths concisely.
- Existing runtime lanes include raw episodes, structured evidence, current state, events, and beliefs.
- Existing plans already lock the right invariants: source evidence, current/prior truth, derived-belief labeling, compact hot path, per-user scope, lifecycle operations, and product/benchmark architecture parity.

## What Remains

- Generic candidate capture still needs to replace too much route-shaped predicate logic.
- Explicit session search needs to become a first-class cold episodic recall surface.
- Promotion needs broader conversion from scattered heuristics into reusable policy objects.
- Promotion trace audits still need a dedicated false-positive/false-negative report over the new policy events.
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

## Current Next Step

Build a trace-audit report over promotion-policy events so Spark can find false-positive promotions, false-negative holds, and stale state drift from item-level evidence.
