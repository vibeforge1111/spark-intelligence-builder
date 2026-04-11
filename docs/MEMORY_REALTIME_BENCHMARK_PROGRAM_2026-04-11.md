# Memory Realtime Benchmark Program

Date: 2026-04-11
Repo: `spark-intelligence-builder`
Related substrate repo: `C:\Users\USER\Desktop\domain-chip-memory`

## Goal

Keep memory improvements honest by forcing every serious architecture change through both:

- offline benchmark scorecards from `domain-chip-memory`
- live Telegram runtime validation through Spark AGI

This prevents us from only optimizing for BEAM, LongMemEval, or LoCoMo-style score movement while missing live chat behavior, routing quality, overwrites, abstention, explanation quality, or noisy real-time failures.

## Active contenders

The active two-architecture race is now:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

Those two are now the default contenders for:

- `memory benchmark-architectures`
- `memory run-telegram-regression`
- `memory soak-architectures`

Other architectures still exist in the substrate and can be passed explicitly with `--baseline`, but they are no longer the default serious comparison loop.

## Required evaluation loop

Every meaningful memory upgrade should follow this order:

1. Run ProductMemory scorecards against the two active contenders.
2. Run the live Telegram regression bundle against the same contenders.
3. Run the rotating Telegram soak suite against the same contenders.
4. Only then decide whether a runtime selector change is justified.

Promotion rule:

- no architecture change gets promoted on benchmark wins alone
- no architecture change gets promoted on one green Telegram replay alone
- the change has to stay green on both offline scorecards and live Telegram packs

Live leader rule:

- live matched-case accuracy stays primary
- trustworthiness is the first tie-break
- grounding is the second tie-break
- substantive scorecard correctness only breaks ties after the live metrics are equal
- explanation-only exact-string scorecard differences do not decide the winner on their own
- scorecard alignment stays the last-resort tie-break, but it is now used intentionally for explanation provenance lanes

## Live Telegram benchmark structure

The real-time harness already covers more than a single fixed replay. It includes:

- core profile baseline
- long-horizon recall
- contradiction and recency
- provenance audit
- boundary abstention
- anti-personalization guardrails
- identity synthesis
- interleaved noise resilience
- quality lane gauntlet
- loaded-context abstention
- temporal conflict gauntlet
- explanation pressure suite
- identity under recency pressure

That means the live loop already tests:

- direct writes and direct recall
- overwrite and recency handling
- abstention when nothing valid is stored
- explanation and provenance behavior
- identity synthesis across multiple facts
- noisy interleaved real-time interaction pressure
- loaded-context anti-hallucination pressure
- temporal lineage proxies under overwrite pressure
- profile-summary coherence after recency conflicts

The newest recency-heavy packs now also force:

- older stable facts to survive overwrite noise
- identity recall to preserve occupation, timezone, founder, and mission under recency pressure
- temporal conflict packs to prove retention of non-overwritten facts, not just the freshest overwrite
- targeted pack runs to execute custom variant cases directly from the CLI instead of falling back to default regression ids
- explanation cases to carry explicit `expected_answer_candidate_source = evidence_memory` so live provenance alignment is measured, not just surface phrasing

## Operator commands

Default serious contender comparison:

```bash
spark-intelligence memory benchmark-architectures
spark-intelligence memory run-telegram-regression
spark-intelligence memory soak-architectures --runs 27
```

Explicit two-contender comparison:

```bash
spark-intelligence memory benchmark-architectures \
  --baseline summary_synthesis_memory \
  --baseline dual_store_event_calendar_hybrid

spark-intelligence memory run-telegram-regression \
  --baseline summary_synthesis_memory \
  --baseline dual_store_event_calendar_hybrid

spark-intelligence memory soak-architectures \
  --runs 27 \
  --baseline summary_synthesis_memory \
  --baseline dual_store_event_calendar_hybrid
```

Focused live slice while keeping the same architecture race:

```bash
spark-intelligence memory soak-architectures \
  --category overwrite \
  --baseline summary_synthesis_memory \
  --baseline dual_store_event_calendar_hybrid
```

Focused benchmark-pack run with custom Telegram variants:

```bash
spark-intelligence memory run-telegram-regression \
  --benchmark-pack identity_under_recency_pressure \
  --baseline summary_synthesis_memory \
  --baseline dual_store_event_calendar_hybrid
```

Focused benchmark-pack soak:

```bash
spark-intelligence memory soak-architectures \
  --runs 5 \
  --benchmark-pack identity_under_recency_pressure \
  --baseline summary_synthesis_memory \
  --baseline dual_store_event_calendar_hybrid
```

## What counts as a real win

A contender is only meaningfully better if it improves the combined picture:

- ProductMemory accuracy and alignment
- live Telegram matched-case accuracy
- grounding and provenance behavior
- abstention quality
- forbidden-memory cleanliness
- overwrite and staleness stability
- soak consistency across multiple benchmark packs

If one architecture wins BEAM or LongMemEval style lanes but loses abstention, provenance, overwrite, or live Telegram robustness, it is not the production winner yet.

Current live separation note:

- the broad live suite now separates the contenders again on explanation provenance alignment
- `dual_store_event_calendar_hybrid` is the current whole-suite live soak leader because it preserves `evidence_memory` alignment on explanation-heavy packs where `summary_synthesis_memory` still falls back to `aggregate_memory`
- the identity-under-recency targeted pack still ties, so it should be treated as a health gate, not as the deciding promotion signal

## Next benchmark expansions

The current live pack suite is broad, but the next useful additions should target separation between the two active contenders:

- event ordering
- calendar or schedule recall
- temporal conflict resolution
- delayed recall after many irrelevant turns
- explanation phrasing quality under evidence pressure

Those additions should be made as new Telegram benchmark packs so future substrate upgrades keep flowing through the same live gate.
