# Phase A Architecture Decision 2026-04-22

**Status:** Phase A complete and post-flip validation passed.
**Decision state:** the runtime pin was flipped to `summary_synthesis_memory` and the full internal gate was rerun successfully.
**Reason:** internal live recommendation already favored `summary_synthesis_memory`, the completed external head-to-head showed a decisive multi-benchmark win, and the post-flip regression rerun removed `architecture_promotion_gap` without introducing new mismatches.

## Snapshot

- Builder commit: `472f3c6aacdc13f18c164fde0eb5cbae0546d09a`
- Substrate commit when this checkpoint was written: `b1cc017`
- Internal artifact root:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head`
- Run manifest:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\run-summary.json`
- External artifact root:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks`
- Pin-flip builder commit:
  `54a01eb`
- Post-flip validation artifact root:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-post-flip\2026-04-22-summary-synthesis-pin`

## What Was Measured This Turn

### Internal head-to-head

| Surface | `summary_synthesis_memory` | `dual_store_event_calendar_hybrid` | Takeaway |
|---|---:|---:|---|
| Offline ProductMemory | `1156/1266` (`91.31%`) | `1156/1266` (`91.31%`) | Exact tie on overall score |
| Offline alignment rate | `1150/1266` (`90.84%`) | `1156/1266` (`91.31%`) | Slight dual-store edge on alignment |
| Live Telegram regression leader | leader | not leader | `summary_synthesis_memory` is still recommended runtime |
| Live Telegram regression runtime pin | no | yes | Current runtime still pinned to dual-store |
| Live Telegram regression score | n/a | `200/202` runtime result | Same 2 known mismatches, no new ones |
| 14-pack soak aggregate | n/a | `238/244` runtime result | Same 6 known mismatches, no new ones |

### External head-to-head

| Surface | `summary_synthesis_memory` | `dual_store_event_calendar_hybrid` | Takeaway |
|---|---:|---:|---|
| BEAM public `128K` | `400/400` (`100.00%`) | `66/400` (`16.50%`) | Summary-synthesis is decisively stronger on the first completed external lane |
| LoCoMo | `173/1986` (`8.71%`) | `163/1986` (`8.21%`) | Both contenders are weak here, but summary-synthesis still edges dual-store |
| LongMemEval_s | `468/500` (`93.60%`) | `232/500` (`46.40%`) | Summary-synthesis wins LongMemEval_s by a wide margin |
| BEAM public `500K` | `700/700` (`100.00%`) | `49/700` (`7.00%`) | Summary-synthesis repeated the same perfect pattern while dual-store collapsed again |
| BEAM public `1M` | `700/700` (`100.00%`) | `49/700` (`7.00%`) | Summary-synthesis completed another perfect lane while dual-store collapsed again |
| BEAM public `10M` | `200/200` (`100.00%`) | `2/200` (`1.00%`) | Summary-synthesis stays perfect at the largest completed BEAM scale while dual-store nearly zeros out |

### Post-flip validation

After approval, Builder pin `54a01eb` flipped the runtime to
`summary_synthesis_memory` and reran the full internal gate into:

`C:\Users\USER\.spark-intelligence\artifacts\phase-a-post-flip\2026-04-22-summary-synthesis-pin`

Results on the flipped pin:

| Surface | Result | Takeaway |
|---|---:|---|
| Offline ProductMemory | unchanged leader set | runtime now matches one of the offline leaders |
| Live Telegram regression | `200/202` | same 2 known mismatches, no new ones |
| Live Telegram regression issue labels | `probe_quality_gap` only | `architecture_promotion_gap` disappeared |
| Live Telegram runtime recommendation | `summary_synthesis_memory` | runtime now matches the recommended live leader |
| 14-pack soak | `238/244` | same 6 known mismatches, no new ones |

### Internal regression details

- Runtime architecture: `dual_store_event_calendar_hybrid`
- Recommended live runtime: `summary_synthesis_memory`
- Runtime matches live leader: `false`
- Issue labels:
  - `probe_quality_gap`
  - `architecture_promotion_gap`
- Known regression mismatches reproduced exactly:
  - `belief_recall_after_evidence_override_onboarding`
  - `evidence_consolidation_belief_recall_onboarding`

### Internal soak details

- Aggregate: `238 matched / 6 mismatched / 14 runs`
- Mismatches only appeared in the same two packs called out in the handoff:
  - `anti_personalization_guardrails`
  - `loaded_context_abstention`
- No new soak packs regressed.

## External Benchmark State

Phase A requires external benchmark data for both contenders across:

- `BEAM`
- `LoCoMo`
- `LongMemEval`

The substrate checkout references these local inputs:

- `benchmark_data/official/LongMemEval/data/longmemeval_s_cleaned.json`
- `benchmark_data/official/LoCoMo/data/locomo10.json`
- `benchmark_data/official/BEAM-upstream/chats`

Observed on this machine during this turn:

- `C:\Users\USER\Desktop\domain-chip-memory\benchmark_data` is restored locally
- `benchmark_data/` is gitignored in
  `C:\Users\USER\Desktop\domain-chip-memory\.gitignore`
- fresh Phase A external artifacts now exist for:
  - `BEAM` public `128K` for both contenders
  - `BEAM` public `500K` for both contenders
  - `BEAM` public `1M` for both contenders
  - `BEAM` public `10M` for both contenders
  - `LoCoMo` for both contenders
  - `LongMemEval_s` for both contenders

Two substrate fixes were required to get the external suite moving on current HEAD:

- `cce6e83` `Handle event calendar ids in yes-no ranking`
- `b1cc017` `Normalize observation id sorting for external benchmarks`

That means the external half of Phase A is complete from this checkout.

## Recommendation

The Phase A recommendation was approved and executed:

`PINNED_RUNTIME_MEMORY_ARCHITECTURE` was flipped from
`dual_store_event_calendar_hybrid` to `summary_synthesis_memory`.

Current evidence says:

- offline internal data is a tie
- live internal regression still prefers `summary_synthesis_memory`
- current runtime is now pinned to `summary_synthesis_memory`
- the completed `BEAM` `128K` head-to-head strongly favors `summary_synthesis_memory`
- the fully completed `LongMemEval_s` head-to-head strongly favors `summary_synthesis_memory`
- the completed `LoCoMo` head-to-head is weak for both contenders, with only a slight summary-synthesis edge
- the completed `BEAM` `500K` head-to-head continues the same summary-synthesis pattern with another perfect lane and another dual-store collapse
- the completed `BEAM` `1M` head-to-head continues the same pattern with another perfect-vs-collapse split
- the completed `BEAM` `10M` head-to-head extends that same pattern to the largest finished scale
- post-flip regression shows `live_architecture_runtime_matches_leader: true`
- post-flip regression issue labels no longer include `architecture_promotion_gap`

That closes Phase A mechanically. The internal live recommendation, the external benchmark matrix, and the post-flip rerun all point in the same direction: `summary_synthesis_memory` is the stronger runtime architecture and the promotion gap is closed.

## Next Actions

1. Move into Phase B mismatch work.
2. Target the 2 regression mismatches:
   - `belief_recall_after_evidence_override_onboarding`
   - `evidence_consolidation_belief_recall_onboarding`
3. Target the 6 soak mismatches in:
   - `anti_personalization_guardrails`
   - `loaded_context_abstention`
4. Preserve the current pin and use the post-flip artifact set as the new architecture baseline.

## Artifact Paths

- Offline benchmark:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\memory-architecture-benchmark\memory-architecture-benchmark.json`
- Live regression:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\telegram-memory-regression\telegram-memory-regression.json`
- Live soak:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\telegram-memory-architecture-soak\telegram-memory-architecture-soak.json`
- External BEAM `128K` summary-synthesis:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\summary_synthesis_memory\beam_128k.json`
- External BEAM `128K` dual-store:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\dual_store_event_calendar_hybrid\beam_128k.json`
- External LoCoMo dual-store:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\dual_store_event_calendar_hybrid\locomo10.json`
- External LoCoMo summary-synthesis:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\summary_synthesis_memory\locomo10.json`
- External LongMemEval_s summary-synthesis:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\summary_synthesis_memory\longmemeval_s.json`
- External LongMemEval_s dual-store:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\dual_store_event_calendar_hybrid\longmemeval_s.json`
- External BEAM `500K` summary-synthesis:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\summary_synthesis_memory\beam_500k.json`
- External BEAM `500K` dual-store:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\dual_store_event_calendar_hybrid\beam_500k.json`
- External BEAM `10M` summary-synthesis:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\summary_synthesis_memory\beam_10m.json`
- External BEAM `10M` dual-store:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\dual_store_event_calendar_hybrid\beam_10m.json`
- External BEAM `1M` summary-synthesis:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\summary_synthesis_memory\beam_1m.json`
- External BEAM `1M` dual-store:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks\dual_store_event_calendar_hybrid\beam_1m.json`
- Post-flip validation run summary:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-post-flip\2026-04-22-summary-synthesis-pin\run-summary.json`
- Post-flip regression:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-post-flip\2026-04-22-summary-synthesis-pin\telegram-memory-regression\telegram-memory-regression.json`
- Post-flip soak:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-post-flip\2026-04-22-summary-synthesis-pin\telegram-memory-architecture-soak\telegram-memory-architecture-soak.json`
