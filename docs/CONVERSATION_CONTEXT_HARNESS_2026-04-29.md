# Conversation Context Harness

Date: 2026-04-29

## Purpose

Spark needs a conversation harness so short human follow-ups keep their meaning:

- "change it to 4" after an access-level turn
- "the second one" after Spark listed options
- "continue that" after a failed mission
- "what were we building?" after a long planning thread

The harness is not another long-term memory store. It is the per-turn context assembler that sits between channel adapters and Spark reasoning/routing.

## Ownership

- `spark-intelligence-builder` owns the canonical harness primitives and policy.
- `spark-telegram-bot` keeps a thin TypeScript adapter with the same frame shape for low-latency local routing.
- `domain-chip-memory` owns durable memory, typed temporal facts, exact evidence retrieval, and shadow evals.

## SOTA-Informed Budget Policy

The harness is token-budgeted, not character-budgeted.

Default policy in `spark_intelligence.context.harness.ContextBudgetPolicy`:

- `model_context_window_tokens = 400_000`
- `target_effective_context_tokens = 200_000`
- `reliable_fraction = 0.65`
- `output_reserve_tokens = 24_000`
- `tool_reserve_tokens = 16_000`
- `hot_target_tokens = 24_000`
- `hot_min_turns = 12`
- `compact_trigger_fraction = 0.65`

The important product rule: 200k reliable input requires a larger advertised model window. On a 200k max-window model, Spark should not pretend it can reliably assemble a full 200k prompt. It should compact earlier and mark `requires_larger_model_for_full_target = true`.

This follows the pattern used by current agent systems:

- Keep the closest turns verbatim.
- Preserve exact artifacts separately from summaries.
- Compact older conversation into structured summaries.
- Retrieve cold evidence from memory by relevance and provenance.
- Reserve output/tool space before filling the context window.

## Conversation Frame

Each turn builds a `ConversationFrame`:

- `current_message`: newest user message
- `hot_turns`: recent verbatim messages
- `warm_summary`: compacted older turns
- `artifacts`: exact lists, access levels, plans, missions, and other referents
- `focus_stack`: current topics with confidence and source
- `reference_resolution`: resolved local reference, if any
- `budget`: token policy and assembled estimates
- `source_ledger`: why each source is present

Frame prompt rule:

```text
Newest explicit user message wins. Exact artifacts win over summaries.
```

## Compaction Tiers

| Tier | Shape | Rule |
|---|---|---|
| Hot | Verbatim recent turns | Never summarize the closest turns unless the model cannot accept even the minimum hot window. |
| Warm | Structured summary | Summarize older goals, decisions, plans, and open loops. |
| Artifact | Exact extracted objects | Preserve lists, selected options, access state, mission IDs, file paths, commands, errors, and decisions. |
| Cold | Memory retrieval | Use `domain-chip-memory` for exact spans, typed temporal graph hits, current state, historical state, and evidence. |

## Reference Resolution

The first implementation resolves:

- access-level shorthand from recent access context
- numbered/ordinal list references against the most recent list artifact

This is deliberately deterministic. LLM-based disambiguation can be added later, but actions must still pass deterministic gates before execution.

Examples:

```text
User: Change my access level to three please
Spark: Done - I changed this chat to Level 3 - Research + Build.
User: Change it to 4
=> reference_resolution.kind = access_level
=> reference_resolution.value = 4
```

```text
Spark: A few directions:
1. Spark Command Palette
2. Domain Chip Workbench
3. Spark Timeline
User: I like the second one
=> reference_resolution.kind = list_item
=> reference_resolution.value = Domain Chip Workbench
```

## Cold Memory Retrieval

Builder exposes `retrieve_domain_chip_cold_context(...)` and `build_conversation_frame_with_cold_context(...)`.

These functions optionally read from `domain-chip-memory` through its Builder read adapter. They are designed to fail soft: if the SDK or adapter is unavailable, the frame still builds from hot/warm context. Retrieved cold items are supporting evidence and do not override hot exact artifacts.

Current cold methods:

- `retrieve_evidence`
- `retrieve_events`

The live Telegram path also calls Builder's hybrid memory capsule through:

```powershell
spark-intelligence memory inspect-capsule --no-record-activity --json
```

That path can use current-state, historical-state, recent-conversation, evidence, event, pending-task, procedural-lesson, graph-sidecar, and knowledge-packet lanes. Telegram injects only conversation-safe cold items into prompts, and deliberately filters broad wiki/diagnostic packets so unrelated project diagnostics do not hijack a normal chat turn.

## Runtime Observability

Adapters should expose a context diagnostic surface. Telegram now has `/context`, which reports:

- hot turn count
- warm summary token estimate
- artifact count
- compaction event count
- safe input budget
- whether the selected policy needs a larger model window for the 200k target

This mirrors ForgeCode's explicit conversation commands such as conversation stats, dump, compact, and info, and Warp's visible conversation/session management surfaces.

## Anti-Drift Rules

- Do not promote raw conversational residue into long-term memory.
- Do not let summaries replace exact artifacts.
- Do not let stale focus override the newest user message.
- Do not use retrieved cold memory when a hot exact artifact answers the reference.
- Do not execute actions from LLM-only resolution; deterministic gates must verify the resolved action.

## Validation

Current tests:

```powershell
python -m pytest tests/test_conversation_harness.py
```

Telegram adapter tests:

```powershell
npx ts-node tests/conversationFrame.test.ts
npx ts-node tests/conversationMemory.test.ts
npx ts-node tests/conversationIntent.test.ts
```

Next eval layer:

- long access-level conversations with shorthand changes
- repeated numbered lists where newest list must win
- "that/it/continue" after failed missions
- hot/warm/cold compaction equivalence against full context
- cross-user leakage checks
- domain-chip-memory LoCoMo-style exact-span recall checks

## Source Patterns Reviewed

- ReMe memory management kit: `128k` max input, compaction around high utilization, recent-message reserve, structured history summaries, and tool-output compaction.
- Google ADK context compaction: sliding-window preservation plus summarization.
- Claude Code context management: old tool output cleared before broader summarization; recent work and decisions preserved.
- Cline context-window guidance: effective quality often degrades before the advertised max context window.
- OpenCode compaction config: auto-compaction plus reserved token buffers.
- MemGPT/Letta: virtual context management and explicit memory tiers.
- Mem0 and Zep/Graphiti: long-term memory and graph retrieval patterns, useful as sidecars rather than hot-turn replacements.
- Warp: persistent agent/conversation UI, codebase indexing, agent events, agent SDK, explicit skills, and feature-flagged rollout discipline.
- ForgeCode: saved/resumable conversations, `:compact`, `conversation stats`, role-specific agents (`forge`, `sage`, `muse`), workspace semantic search, sandboxed worktrees, and session-vs-persistent config separation.

Primary references:

- https://github.com/agentscope-ai/ReMe
- https://adk.dev/context/compaction/
- https://code.claude.com/docs/en/how-claude-code-works
- https://docs.cline.bot/model-config/context-windows
- https://opencode.ai/docs/config/#compaction
- https://arxiv.org/abs/2310.08560
- https://arxiv.org/abs/2504.19413
- https://arxiv.org/abs/2501.13956
- https://github.com/warpdotdev/warp
- https://github.com/tailcallhq/forgecode
