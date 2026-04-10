# Memory Benchmark Handoff

Date: 2026-04-11
Repo: `spark-intelligence-builder`
Related memory substrate repo: `C:\Users\USER\Desktop\domain-chip-memory`

## Current state

- Builder is still not pinned to a named winning runtime architecture.
- The generic live runtime remains the governed `SparkMemorySDK` / `domain_chip_memory` stack.
- The benchmark harness was expanded from a fixed Telegram replay into a varied pack suite.
- The soak/reporting logic was then hardened so zero-signal categories no longer look like meaningful ties.

## Final completed soak

Artifact root:
- `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-architecture-trust-soak-27-fixed`

Final summary:
- `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-architecture-trust-soak-27-fixed\telegram-memory-architecture-soak.json`

Run status:
- completed `27/27`
- failed `0/27`

Final aggregate leaderboard:
1. `summary_synthesis_memory` = `27/138` = `19.57%`
2. `observational_temporal_memory` = `21/138` = `15.22%`
3. `dual_store_event_calendar_hybrid` = `21/138` = `15.22%`

Current top-two recommendation:
1. `summary_synthesis_memory`
2. `observational_temporal_memory`

## Important benchmark interpretation

- The winner is real for this hardened benchmark suite: `summary_synthesis_memory`.
- The absolute performance is still weak.
- The benchmark now honestly reports unresolved lanes instead of inventing ties.

Still unresolved / weak:
- `abstention`: no leader
- `explanation`: no leader
- `quality_lane_gauntlet`: no leader

Observed trust metrics:
- `forbidden_clean_accuracy` was `1.0` across all three on the anti-personalization lane that was actually scored.
- `abstention_accuracy` remained `0.0` across all three.

## What changed in the harness

Main files:
- `src/spark_intelligence/memory/benchmark_packs.py`
- `src/spark_intelligence/memory/regression.py`
- `src/spark_intelligence/memory/architecture_live_comparison.py`
- `src/spark_intelligence/memory/architecture_soak.py`

Key improvements already made:
- varied benchmark packs instead of one fixed replay
- isolated Telegram namespace per run
- negative checks for forbidden memory use
- trust, grounding, abstention, and forbidden-clean metrics
- zero-signal lanes no longer reported as leaders

## Architecture note

`observational_temporal_memory` and `dual_store_event_calendar_hybrid` are not the same architecture.

What is true:
- they are close relatives
- they share a large amount of observation/reflection/current-state scaffolding

What differs:
- `observational_temporal_memory` is observation/reflection-centric with preference support, question-aware observation limits, and aggregate support
- `dual_store_event_calendar_hybrid` adds an explicit event-calendar retrieval and answer path

Why they may keep tying:
- the current packs stress profile/state recall more than event-heavy temporal reconstruction
- the event-calendar advantage may need stronger event-sequencing benchmarks to separate clearly

## Recommended next steps tomorrow

1. Keep `summary_synthesis_memory` as the primary candidate.
2. Keep `observational_temporal_memory` as the active challenger.
3. De-prioritize `dual_store_event_calendar_hybrid` unless we add stronger event/calendar-specific packs.
4. Add new benchmark packs specifically for:
   - event ordering
   - schedule/calendar recall
   - temporal conflict resolution
   - abstention under tempting but irrelevant stored facts
   - provenance/explanation phrasing quality
5. Run the top two through those new real-time packs.
6. If `summary_synthesis_memory` still leads, start wiring Builder toward it behind a controlled runtime selector.

## Good continuation point

Tomorrow, resume from:
- the completed fixed soak artifact
- the top-two comparison decision
- new benchmark design focused on the unsolved lanes and on separating observational vs event-calendar behavior in a more targeted way

## Relevant commits from this session

- `9a07b50` `Expand memory soak into benchmark pack suite`
- `793f908` `Auto-approve isolated memory soak users`
- `7f51f39` `Harden memory benchmark trust scoring`
- `61d0b6a` `Fix zero-signal ties in memory benchmark soak`
