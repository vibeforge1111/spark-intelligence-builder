# Memory Runtime Integration Plan 2026-04-10

## Current Reality

Spark Builder is already using the memory SDK in the live Telegram path. It is not starting from zero.

What is live today:

- `write_profile_fact_to_memory(...)` persists structured Telegram facts into the configured memory SDK.
- `lookup_current_state_in_memory(...)` reads single current-state facts back through Builder.
- `inspect_human_memory_in_memory(...)` reads a subject-wide current-state view for identity-style summaries.
- The default SDK module is `domain_chip_memory`.

What is not yet fully promoted into Builder:

- richer answer explanation reads
- evidence retrieval reads
- event retrieval reads
- broader benchmark-backed answer selection for open memory questions

That means the substrate is live, but Builder still uses a narrower slice of it than the benchmark stack supports.

## Architectural Read

This is primarily a promotion and calibration problem, not a rebuild problem.

The benchmark work in `domain-chip-memory` proved stronger retrieval strategies and answer-selection paths. Builder currently relies mostly on typed current-state reads for Telegram memory questions. That is why the system can already store and answer many profile facts correctly, while still feeling narrower than the benchmarked memory behavior.

Working interpretation:

- architecture: fundamentally sound
- live integration: partially promoted
- current gap: Builder orchestration does not yet expose enough of the richer SDK read surface
- immediate risk: quality ceilings on open memory questions, evidence-backed answers, and introspection-style prompts

## Phase Plan

### Phase 1: Promote richer Builder read wrappers

Goal: expose the existing SDK read surface through first-class Builder helpers.

Scope:

- add `explain_memory_answer_in_memory(...)`
- add Builder tests for fake-client and live `domain_chip_memory` explanation reads
- keep observability parity with existing read wrappers

Exit criteria:

- Builder can request answer explanations without calling SDK adapters directly
- explanation payloads are normalized into `MemoryReadResult.answer_explanation`
- read events clearly show `method=explain_answer`

### Phase 2: Route broader memory questions through richer reads

Goal: stop treating all memory reads like plain current-state lookups.

Scope:

- identify Telegram and advisory prompts that want explanation instead of only latest value
- route those prompts through `explain_answer`
- preserve current-state lookup for direct factual reads where the simpler path is enough

Exit criteria:

- broader memory-summary questions can return supported answers plus evidence/explanation
- direct fact reads remain stable and low-latency

### Phase 3: Promote benchmark-backed retrieval behavior

Goal: bring benchmark-winning memory behavior into Builder answer selection where it materially improves outcomes.

Scope:

- map benchmarked strategies to runtime-safe Builder use cases
- decide when to use current-state only versus richer retrieval
- add guardrails against noisy over-promotion into conversational answers

Exit criteria:

- Builder answer selection uses more of the benchmark-proven retrieval stack
- promotion rules are explicit and testable

### Phase 4: Telegram validation and rollout gates

Goal: validate runtime quality on real Telegram-style interactions before broadening defaults.

Scope:

- probe-home replay suites
- direct Telegram bridge validation
- regression coverage for geography, founder/startup, identity summaries, and explanation queries

Exit criteria:

- stable answers on real Telegram probes
- no regression in existing profile-fact capture and retrieval flows

## Immediate Next Moves

1. Land the Builder `explain_answer` wrapper and tests.
2. Route one narrow class of broader memory questions through that wrapper.
3. Add evidence-sensitive Telegram probes before promoting more retrieval paths.

## Recommendation

Do not rewrite the memory architecture. Continue promoting the existing SDK surface into Builder in small, testable slices. The system is already working; it is just underexposing the richer retrieval behavior that the benchmark work established.
