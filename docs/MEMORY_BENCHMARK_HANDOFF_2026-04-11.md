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
- The latest clean live `14`-pack soak now favors `summary_synthesis_memory`, while the offline ProductMemory benchmark still favors `dual_store_event_calendar_hybrid`.
- Because the two sides disagree, the runtime stays pinned to `dual_store_event_calendar_hybrid` until one contender wins both offline and live.

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

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

Latest clean live validation:

- `.spark-intelligence/artifacts/telegram-memory-architecture-soak/telegram-memory-architecture-soak.json`
- status: `14/14` completed, `0` failed
- full-suite aggregate: `90/92` for `summary_synthesis_memory` vs `86/92` for `dual_store_event_calendar_hybrid`
- selector-pack aggregate: `62/64` for `summary_synthesis_memory` vs `58/64` for `dual_store_event_calendar_hybrid`
- that rerun was performed after terminating stale concurrent soak jobs that had been contaminating the shared artifact path
- a later chip/runtime scoring pass then removed the shared explanation misses and eliminated alignment-only live tiebreaks, which is why the clean live margin widened materially
- the latest pack-definition cleanup also converted `provenance_audit` into a clean tie by adding the missing `occupation_write` prerequisite it implicitly depended on

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

- `dual_store_event_calendar_hybrid` still wins the offline ProductMemory side
- `summary_synthesis_memory` now wins the latest clean live whole-suite soak
- `summary_synthesis_memory` recovered specifically after the chip-side history/query fixes aligned Builder prompts and chronology scoring
- `observational_temporal_memory` remains useful as a control or explicit extra baseline, but is no longer the default second contender

## Important benchmark interpretation

- There is no current combined winner for the hardened offline-plus-live suite.
- The current honest result is a split verdict: offline points to `dual_store_event_calendar_hybrid`, live points to `summary_synthesis_memory`.
- The absolute performance is still weak.
- The benchmark now honestly reports unresolved lanes instead of inventing ties.

Still unresolved / weak:
- `event_calendar_lineage_proxy`
- `temporal_conflict_gauntlet`

Observed trust metrics:
- both contenders stayed clean on the current forbidden-memory lanes in the latest live soak
- the current disagreement is concentrated in selector-pack quality rather than in the fully green health gates

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

1. Keep `dual_store_event_calendar_hybrid` as the pinned runtime architecture until the same contender wins both offline and live.
2. Keep `summary_synthesis_memory` as the active challenger in the default live program.
3. Use `observational_temporal_memory` as a control lane when we want a third comparison, not as the default second contender.
4. Add new benchmark packs specifically for:
   - event ordering
   - schedule/calendar recall
   - temporal conflict resolution
   - abstention under tempting but irrelevant stored facts
   - provenance/explanation phrasing quality
5. Keep rerunning the top two through those real-time packs, especially the current clean-soak selector packs that still require work: `provenance_audit`, `explanation_pressure_suite`, `event_calendar_lineage_proxy`, `temporal_conflict_gauntlet`, `quality_lane_gauntlet`, and `contradiction_and_recency`.
6. Keep rerunning the pinned `dual_store_event_calendar_hybrid` runtime against the active challenger and only repin if the challenger wins both offline and live.

Promotion rule:

- no memory change is promoted on offline benchmark wins alone
- no memory change is promoted on a single live Telegram replay alone
- both the offline scorecards and the live Telegram packs have to stay green

## Good continuation point

Tomorrow, resume from:
- the latest clean `14/14` soak artifact at `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-architecture-soak\telegram-memory-architecture-soak.json`
- the refreshed explanation-pack rerun at `C:\Users\USER\.spark-intelligence\artifacts\telegram-memory-regression-explanation-pack-v2`
- the current default two-contender program in `docs/MEMORY_REALTIME_BENCHMARK_PROGRAM_2026-04-11.md`
- the offline benchmark artifact at `C:\Users\USER\.spark-intelligence\artifacts\memory-architecture-benchmark\memory-architecture-benchmark.json`
- new benchmark design focused on the unsolved selector lanes and on separating offline-vs-live disagreement more directly

## Relevant commits from this session

- `9a07b50` `Expand memory soak into benchmark pack suite`
- `793f908` `Auto-approve isolated memory soak users`
- `7f51f39` `Harden memory benchmark trust scoring`
- `61d0b6a` `Fix zero-signal ties in memory benchmark soak`
