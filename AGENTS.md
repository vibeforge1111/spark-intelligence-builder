# Spark Agent Engineering Guide

This repo should not invent agent memory in isolation. For any non-trivial change to Spark memory, identity, self-awareness, Telegram runtime behavior, Researcher prompts, dashboards, or workflow recovery, do a short research pass before the architecture hardens.

## Research Before Building

- Check current strong implementations and papers first. Start with Mem0, Letta/MemGPT, Engram, Cortex, LangGraph/LangMem patterns, recent agent-memory papers, and high-signal GitHub repos.
- Record the research influence in the PR, commit notes, design doc, or test names: what was borrowed, what was rejected, and why Spark's local constraints differ.
- Prefer primary sources: official docs, GitHub repos, papers, and benchmark repos. Use blog posts only as supporting context.
- Do not cargo-cult. External patterns must pass Spark's boundaries: source-aware recall, human/agent scoping, provenance, dashboard traceability, Telegram readability, and anti-residue memory discipline.
- If network research is unavailable, say that explicitly and continue from local docs and cached knowledge.

## Karpathy Bar

Use Karpathy-style engineering as the taste filter:

- Make the core idea small enough to read. Prefer a direct reference implementation over a clever framework-shaped abstraction.
- Keep the important path executable and testable end to end.
- Use names that teach the mechanism.
- Avoid accidental complexity, hidden magic, and premature generalization.
- Add comments only where they make the algorithm or authority boundary clearer.
- Keep one simple path working before adding optimizers, sidecars, or parallel machinery.

## Memory Architecture Principles

- Separate episodic, semantic/current-state, procedural, and working/session memory. Do not flatten everything into one vector-store-shaped bucket.
- Treat raw conversation turns as source-aware episodic evidence, not durable truth.
- Promote only when there is salience, transfer value, provenance, and a clear reason to survive decay.
- Retrieval must report source, scope, recency, confidence, and whether a memory is authoritative or merely supporting.
- Dashboard views should show movement, not only totals: captured, blocked, promoted, saved, decayed, summarized, and retrieved.
- Telegram replies should stay concise and human-readable even when the memory machinery underneath is rich.

## External Inspiration Watchlist

- Mem0: https://github.com/mem0ai/mem0
- Letta/MemGPT: https://github.com/letta-ai/letta
- Engram: https://engram.to/
- Cortex: https://github.com/prem-research/cortex
- Karpathy llm.c: https://github.com/karpathy/llm.c
- Karpathy nanoGPT: https://github.com/karpathy/nanoGPT
- Karpathy micrograd: https://github.com/karpathy/micrograd

Refresh this list as better SOTA examples appear.
