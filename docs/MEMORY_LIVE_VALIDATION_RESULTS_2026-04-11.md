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
- Latest whole-suite soak leader: `dual_store_event_calendar_hybrid`
- Offline ProductMemory leader: `dual_store_event_calendar_hybrid`

## Soak Aggregate

- earlier live soak snapshot: `summary_synthesis_memory` at `36/72` vs `33/72`
- earlier post-extraction whole-suite soak briefly reported `dual_store_event_calendar_hybrid` as leader, but that turned out to be a comparison-harness artifact
- the live-comparison tie-break now ignores explanation-only exact-string scorecard differences when live accuracy, trustworthiness, and grounding already tie
- the live-comparison harness now also sets explicit `expected_answer_candidate_source = evidence_memory` for explanation cases, so live provenance alignment is measured directly
- latest corrected whole-suite soak: both contenders are still tied at `66/75`, `88.00%` aggregate accuracy, but `dual_store_event_calendar_hybrid` is the live leader by pack-level provenance alignment on explanation-heavy lanes

## What Tightened

The live suite is now stricter in the places that were still underreporting ambiguity:

- recency-heavy benchmark packs now include retention checks for occupation, timezone, founder, and mission
- temporal conflict packs now test whether non-overwritten facts survive overwrite noise
- live leader selection now breaks ties in this order: matched-case accuracy, trustworthiness, grounding, substantive scorecard correctness, then scorecard alignment
- soak summary leader labels now use that same full tie-break signature instead of flattening back to raw accuracy only
- explanation prompts now carry explicit source-alignment expectations, which means provenance behavior can create a real live leader instead of staying invisible behind surface-text ties

## Pack Readout

- `core_profile_baseline`: `dual_store_event_calendar_hybrid` now leads on explanation-source alignment while matched-case metrics stay tied
- `provenance_audit`: `dual_store_event_calendar_hybrid` leads on explanation-source alignment
- `explanation_pressure_suite`: `dual_store_event_calendar_hybrid` leads on explanation-source alignment
- `interleaved_noise_resilience`: `dual_store_event_calendar_hybrid` leads after the same provenance alignment signal is carried through the noisy pack
- `quality_lane_gauntlet`: `dual_store_event_calendar_hybrid` leads when explanation provenance is mixed with abstention and overwrite pressure
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

The current decision now has a real live separator again:

1. `summary_synthesis_memory` is still the pinned runtime architecture and still matches the targeted live regression leader set.
2. `dual_store_event_calendar_hybrid` leads the offline ProductMemory benchmark.
3. `dual_store_event_calendar_hybrid` also leads the corrected 13-pack live soak once explanation provenance alignment is measured explicitly.
4. The targeted identity pack still does not separate the contenders, so it should remain a regression gate, not the sole promotion driver.
5. Promotion still should not happen on offline scorecards alone, but the live suite now has a meaningful provenance-based separator instead of only harness noise.

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
- `.spark-intelligence/artifacts/telegram-memory-regression-explanation-pack-v1/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-explanation-pack-v1/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak/telegram-memory-architecture-soak.json`
