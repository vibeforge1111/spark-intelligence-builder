# Memory Live Validation Results 2026-04-11

## Scope

This run validated the active two-contender program:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

The goal was to keep offline ProductMemory comparison and live Telegram validation tied together so promotions do not happen on scorecards alone.

## Confirmed Results

- Live Telegram regression: `31/31` matched
- KB compile: valid
- KB probe coverage: `38/38` current-state and `38/38` evidence hits
- Telegram soak status: `13/13` completed, `0` failed
- Live overall leader: `summary_synthesis_memory`
- Offline ProductMemory leader: `dual_store_event_calendar_hybrid`

## Soak Aggregate

- `summary_synthesis_memory`: `36/72` matched, `50.00%` aggregate accuracy
- `dual_store_event_calendar_hybrid`: `33/72` matched, `45.83%` aggregate accuracy

## What Tightened

The live suite is now stricter in the places that were still underreporting ambiguity:

- recency-heavy benchmark packs now include retention checks for occupation, timezone, founder, and mission
- temporal conflict packs now test whether non-overwritten facts survive overwrite noise
- live leader selection now breaks ties in this order: matched-case accuracy, trustworthiness, grounding, scorecard correctness, then scorecard alignment
- soak summary leader labels now use that same full tie-break signature instead of flattening back to raw accuracy only

## Pack Readout

- `temporal_conflict_gauntlet`: `dual_store_event_calendar_hybrid` wins the pack tie-break at `4/10` vs `4/10`
- `explanation_pressure_suite`: `dual_store_event_calendar_hybrid` wins the pack tie-break at `2/5` vs `2/5`
- `identity_under_recency_pressure`: the latest targeted rerun is now a full `11/11` tie after chip-side extraction and profile-query routing fixes

## Targeted Pack Validation

The benchmark-pack CLI path now runs custom Telegram variants directly:

- `memory run-telegram-regression --benchmark-pack identity_under_recency_pressure` executed `21` cases end-to-end
- the first targeted run matched `19/21`, and the only misses were the two richer profile-summary prompts
- the follow-up routing fix widened identity-summary detection for explicit prompts like `Summarize my profile in one sentence.` and `Give me a full profile summary with my latest location too.`
- the corrected targeted rerun is now `21/21` matched on live Telegram with `48/48` current-state and `48/48` evidence probe hits
- the shadow-replay contract now also preserves the original profile-summary prompt text for benchmark reconstruction instead of flattening every identity-summary query to `What do you remember about me?`
- the latest chip-side scorecard extraction fix now captures `preferred_name`, `timezone`, `home_country`, cleaned `location`, and profile-mission routing for benchmark replay too
- the newest targeted rerun at `telegram-memory-regression-identity-pack-v9` still matched `21/21` on live Telegram with `48/48` current-state and `48/48` evidence hits
- the live architecture comparison for the same targeted pack now compares `11` cases and ties `summary_synthesis_memory` vs `dual_store_event_calendar_hybrid` at `11/11`
- this is a materially better tie than the earlier `2/11`: the targeted identity pack is no longer exposing synthesis misses, but it also no longer separates the two contenders on its own

## Interpretation

The current decision should hold:

1. Keep `summary_synthesis_memory` as the live runtime favorite.
2. Keep `dual_store_event_calendar_hybrid` as the active offline challenger.
3. Do not promote on offline scorecards alone.
4. Keep live Telegram regression and soak as mandatory gates.

## Runtime Selector

The Builder runtime contract now explicitly reports `summary_synthesis_memory` as the active memory architecture alongside the governed `SparkMemorySDK` substrate. That means the live leader is now pinned in the runtime summary layer while `dual_store_event_calendar_hybrid` remains the offline challenger that still has to win both scorecards and live Telegram before any promotion decision changes.

## Artifacts

- `.spark-intelligence/artifacts/telegram-memory-regression/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression/regression-summary.md`
- `.spark-intelligence/artifacts/telegram-memory-regression/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak/telegram-memory-architecture-soak.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-identity-pack-v2/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-identity-pack-v2/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-identity-pack-v3/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-identity-pack-v3/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-identity-pack-v9/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-identity-pack-v9/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
