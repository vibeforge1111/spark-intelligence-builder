# Memory Live Validation Results 2026-04-11

## Scope

This run validated the active two-contender program:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

The goal was to keep offline ProductMemory comparison and live Telegram validation tied together so promotions do not happen on scorecards alone.

## Confirmed Results

- Latest clean full validation root: `C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\20260412-001858`
- Stable latest full-run pointer: `C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\latest-full-run.json`
- Stable previous full-run pointer: `C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\previous-full-run.json`
- Live Telegram regression: `34/34` matched
- KB compile: valid
- KB probe coverage: `38/38` current-state and `38/38` evidence hits
- Clean Telegram soak status: `14/14` completed, `0` failed
- Latest clean whole-suite soak leader: `summary_synthesis_memory`
- Latest clean whole-suite aggregate: `92/92` for `summary_synthesis_memory` vs `89/92` for `dual_store_event_calendar_hybrid`
- Latest clean selector-pack aggregate: `64/64` for `summary_synthesis_memory` vs `61/64` for `dual_store_event_calendar_hybrid`
- Offline ProductMemory result: tied at `1156/1266` between `summary_synthesis_memory` and `dual_store_event_calendar_hybrid`
- Current runtime selector: `summary_synthesis_memory`
- Latest clean timed validation cost:
  - benchmark: `12.21s`
  - regression: `22.934s`
  - soak: `350.911s`
  - total: `386.479s`

## Soak Aggregate

- earlier live soak snapshot: `summary_synthesis_memory` at `36/72` vs `33/72`
- several later soak verdicts that appeared to favor `dual_store_event_calendar_hybrid` were contaminated by concurrent soak processes writing to the same artifact path
- those stale soak processes were terminated and the `14`-pack suite was rerun clean
- the latest clean rerun finished `14/14`, `0` failed, with `summary_synthesis_memory` leading the full suite by three matched cases and the selector subset by three matched cases
- a later post-repin whole-suite soak attempt did stall mid-run after `9/14`, which exposed the need for a per-pack soak timeout instead of trusting the old long-running path
- after adding the soak timeout, the fresh whole-suite rerun at `telegram-memory-architecture-soak-post-timeout-v1` completed cleanly at `14/14`, `0` failed, and preserved the same live leader and aggregate margins
- the live-comparison tie-break still ignores explanation-only exact-string scorecard differences when live accuracy, trustworthiness, and grounding already tie
- explanation prompts still carry explicit `expected_answer_candidate_source = evidence_memory`, so provenance alignment is measured directly instead of being hidden behind surface phrasing
- alignment-only scorecard differences no longer pick a winner when there is no substantive non-explanation scorecard signal, so the explanation-heavy packs no longer manufacture a live leader on phrasing/internal provenance alone

## What Tightened

The live suite is now stricter in the places that were still underreporting ambiguity:

- recency-heavy benchmark packs now include retention checks for occupation, timezone, founder, and mission
- temporal conflict packs now test whether non-overwritten facts survive overwrite noise
- live leader selection now breaks ties in this order: matched-case accuracy, trustworthiness, grounding, substantive scorecard correctness, then scorecard alignment
- soak summary leader labels now use that same full tie-break signature instead of flattening back to raw accuracy only
- explanation prompts now carry explicit source-alignment expectations, which means provenance behavior can create a real live separator instead of staying invisible behind surface-text ties
- native Telegram history and event-history questions are now part of the chronology-sensitive packs instead of living only in proxy prompts

## Pack Readout

- `summary_synthesis_memory` now leads the clean whole-suite live soak overall by a material margin rather than by a one-case edge
- `contradiction_and_recency` and `temporal_conflict_gauntlet` moved toward `summary_synthesis_memory` after the chip-side history/query fixes were aligned with Builder prompts
- direct profile fact explanation support in the chip-side benchmark runtime removed the shared `mission_explanation` failure and cleaned up `startup_explanation_after_founder`
- adding the missing `occupation_write` prerequisite to `provenance_audit` converted that pack from a manufactured failure into a clean tie
- normalizing `location` entries into the history-answer path then cleared `city_history_query_after_overwrite`, which removed the last remaining selector-pack gaps from the live suite
- `long_horizon_recall`, `boundary_abstention`, `anti_personalization_guardrails`, `identity_synthesis`, `loaded_context_abstention`, and `identity_under_recency_pressure` are now health gates rather than selector packs because both contenders are fully green there
- `identity_under_recency_pressure` remains a targeted `11/11` tie after the chip-side extraction and profile-query routing fixes

## Historical Targeted Pack Validation

These targeted reruns explain how the live suite moved over time. The current source of truth is still the clean full-suite rerun summarized above.

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
- because of that, `identity_under_recency_pressure` is now classified as a soak health gate rather than a selector pack: it still must stay green, but it no longer contributes to whole-suite runtime-pinning votes
- the same rule now covers the other fully green tied packs too, so whole-suite leader selection is driven by the remaining separator packs rather than by sanity lanes that both contenders already pass perfectly
- the `event_calendar_lineage_proxy` pack now includes native Telegram history and event-history questions for overwritten profile facts instead of staying proxy-only
- the first filtered live rerun failed `0/3` only because it skipped the prerequisite write cases and hit an empty synthetic namespace
- after adding inspection-backed chronology fallback, the full pack rerun went `20/20` on live Telegram with `23/23` current-state and `23/23` evidence probe hits
- the new green chronology cases are `city_history_query_after_overwrite`, `country_history_query_after_overwrite`, and `city_event_history_query_after_overwrite`
- the latest post-repin isolated pack reruns stayed green too:
  `temporal_conflict_gauntlet` at `23/23`, `event_calendar_lineage_proxy` at `20/20`, `explanation_pressure_suite` at `13/13`, and `identity_under_recency_pressure` at `21/21`
- those post-repin isolated reruns kept `summary_synthesis_memory` as the live leader on `temporal_conflict_gauntlet` and `event_calendar_lineage_proxy`, while `explanation_pressure_suite` and `identity_under_recency_pressure` remained honest ties
- the timeout-hardened whole-suite rerun confirmed the same pack-level story:
  `contradiction_and_recency`, `temporal_conflict_gauntlet`, and `event_calendar_lineage_proxy` still favor `summary_synthesis_memory`, while the health gates and explanation-heavy lanes remain honest ties
- an earlier corrected full 14-pack soak finished `14/14`, `0` failed, with `dual_store_event_calendar_hybrid` as the then-current live leader
- that completed soak now covers `event_history` and `native_history` explicitly in the rotating live suite
- the chronology pack itself is green at the Telegram runtime layer, but the architecture comparison inside the soak still only separates the contenders weakly on that pack at `6/11`, with `dual_store_event_calendar_hybrid` taking the tie-break
- the temporal conflict pack was then tightened with the same native history cases, and the targeted live rerun went `23/23` with `20/20` KB probe coverage
- that tighter temporal conflict rerun now compares `13` live architecture cases and cleanly favors `dual_store_event_calendar_hybrid`, so chronology is now helping a previously tied conflict lane separate the contenders
- the contradiction-and-recency pack was then tightened with the same overwrite-history questions, and its targeted live rerun went `16/16` with `13/13` KB probe coverage
- that tightened contradiction-and-recency rerun now compares `10` live architecture cases and also favors `dual_store_event_calendar_hybrid`, which removes another previously tied live lane
- a later soak investigation showed that some whole-suite reruns were still reusing stale soak namespaces and occasionally falling back into the Telegram agent-name onboarding path
- regression setup now seeds the canonical agent name to `Atlas`, and each soak invocation now uses a fresh suite token in its user/chat namespace
- after that harness fix, the fresh contradiction-and-recency soak run also moved to `dual_store_event_calendar_hybrid`, so the previous contradiction tie was not a stable runtime verdict
- the architecture comparison now only requires the historical fragment for explicit `previous state` questions like `Where did I live before?` and `What was my previous country?`, while still keeping full chronology requirements for true event-listing prompts
- the scorer now also strips style-only fragments like `saved memory record` from explanation-like staleness prompts such as `How do you know my startup?`, so those lanes no longer penalize factual evidence answers for missing narration boilerplate
- the live architecture comparison for that proxy pack compares `8` cases and currently favors `dual_store_event_calendar_hybrid`
- those intermediate dual-leading soak outputs were later superseded by the clean rerun described above, after stale concurrent soak jobs were removed from the shared artifact path

## Interpretation

The current decision is no longer a hard offline-vs-live split:

1. The offline ProductMemory benchmark is now tied at `1156/1266` for both contenders.
2. `summary_synthesis_memory` still leads the latest clean `14`-pack live Telegram soak.
3. The identity-heavy health gates are no longer useful promotion signals on their own because both contenders already stay green there.
4. Promotion still should not happen on offline scorecards alone, but it also should not happen on a live-only win.
5. The runtime selector is now moved to `summary_synthesis_memory`: it leads the clean live suite and no longer loses the offline ProductMemory benchmark on accuracy.
6. Native Telegram chronology queries are now part of the live benchmark surface, and they materially helped `summary_synthesis_memory` recover on contradiction and temporal-conflict packs.
7. When explanation-heavy packs and overwrite-heavy packs need targeted reruns, they should be run as isolated per-pack regressions or a soak, not merged into one synthetic namespace.
8. The soak harness itself is now safer: one hung pack should fail that run instead of freezing the full benchmark suite.
9. The most useful next work is now the same as before: keep tightening remaining benchmark quality rather than forcing a repin off a partial verdict.

## Runtime Selector

The Builder runtime contract now explicitly reports `summary_synthesis_memory` as the active memory architecture alongside the governed `SparkMemorySDK` substrate. That repin is justified by the current state: the offline ProductMemory benchmark is tied on accuracy, while the latest clean live soak favors `summary_synthesis_memory`. The promotion rule stays unchanged: any future repin still has to justify itself across both scorecards and live Telegram.

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
- `.spark-intelligence/artifacts/telegram-memory-regression-explanation-pack-v2/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-explanation-pack-v2/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-event-calendar-proxy-v1/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression-event-calendar-proxy-v1/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/post-repin-pack-runs/temporal_conflict_gauntlet/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/post-repin-pack-runs/event_calendar_lineage_proxy/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/post-repin-pack-runs/explanation_pressure_suite/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/post-repin-pack-runs/identity_under_recency_pressure/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak-post-timeout-v1/telegram-memory-architecture-soak.json`
- `.spark-intelligence/artifacts/telegram-memory-regression/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression/regression-summary.md`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak/telegram-memory-architecture-soak.json`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak/`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak-post-repin-v1/telegram-memory-architecture-soak.json`
