# Memory Benchmark Handoff

Date: 2026-04-11
Repo: `spark-intelligence-builder`
Related memory substrate repo: `C:\Users\USER\Desktop\domain-chip-memory`

## Current state

- Builder is now pinned to `dual_store_event_calendar_hybrid` as the named runtime architecture in the SDK contract summary.
- The live runtime still runs through the governed `SparkMemorySDK` / `domain_chip_memory` stack.
- The benchmark harness was expanded from a fixed Telegram replay into a varied pack suite.
- The soak/reporting logic was then hardened so zero-signal categories no longer look like meaningful ties.
- The current default serious comparison loop is now governed by `docs/MEMORY_REALTIME_BENCHMARK_PROGRAM_2026-04-11.md`.

## Historical soak snapshot

Artifact root:
- `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-architecture-trust-soak-27-fixed`

Final summary:
- `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-architecture-trust-soak-27-fixed\telegram-memory-architecture-soak.json`

Run status:
- completed `27/27`
- failed `0/27`

Historical aggregate leaderboard:
1. `summary_synthesis_memory` = `27/138` = `19.57%`
2. `observational_temporal_memory` = `21/138` = `15.22%`
3. `dual_store_event_calendar_hybrid` = `21/138` = `15.22%`

Historical top-two recommendation from that soak:
1. `summary_synthesis_memory`
2. `observational_temporal_memory`

Current whole-suite decision:

1. `dual_store_event_calendar_hybrid`
2. `summary_synthesis_memory`

## Current operating decision

The current default operating program is:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

The current pinned runtime selector is:

1. `dual_store_event_calendar_hybrid`

This is now the default contender pair for:

- ProductMemory scorecards
- live Telegram regression
- live Telegram soak

Why the contender pair changed:

- `dual_store_event_calendar_hybrid` now wins the combined offline and live whole-suite program
- `summary_synthesis_memory` remains the closest serious challenger and still ties on some targeted live packs
- `observational_temporal_memory` remains useful as a control or explicit extra baseline, but is no longer the default second contender

## Important benchmark interpretation

- The current combined winner is real for the hardened offline-plus-live suite: `dual_store_event_calendar_hybrid`.
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

1. Keep `dual_store_event_calendar_hybrid` as the pinned runtime architecture.
2. Keep `summary_synthesis_memory` as the active challenger in the default live program.
3. Use `observational_temporal_memory` as a control lane when we want a third comparison, not as the default second contender.
4. Add new benchmark packs specifically for:
   - event ordering
   - schedule/calendar recall
   - temporal conflict resolution
   - abstention under tempting but irrelevant stored facts
   - provenance/explanation phrasing quality
5. Run the top two through those new real-time packs.
6. Keep rerunning the pinned `dual_store_event_calendar_hybrid` runtime against the active challenger and only repin if the challenger wins both offline and live.

Promotion rule:

- no memory change is promoted on offline benchmark wins alone
- no memory change is promoted on a single live Telegram replay alone
- both the offline scorecards and the live Telegram packs have to stay green

## Good continuation point

Tomorrow, resume from:
- the completed fixed soak artifact
- the refreshed explanation-pack rerun at `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-regression-explanation-pack-v2`
- the current default two-contender program in `docs/MEMORY_REALTIME_BENCHMARK_PROGRAM_2026-04-11.md`
- new benchmark design focused on the unsolved lanes and on separating observational vs event-calendar behavior in a more targeted way

## Relevant commits from this session

- `9a07b50` `Expand memory soak into benchmark pack suite`
- `793f908` `Auto-approve isolated memory soak users`
- `7f51f39` `Harden memory benchmark trust scoring`
- `61d0b6a` `Fix zero-signal ties in memory benchmark soak`
