# Phase A Architecture Decision 2026-04-22

**Status:** partial Phase A complete, external suite in progress.
**Decision state:** no pin change recommended yet.
**Reason:** internal head-to-head is captured on current HEAD and external data restore is done, but only the first external BEAM lane is complete so the full Phase A decision is still incomplete.

## Snapshot

- Builder commit: `472f3c6aacdc13f18c164fde0eb5cbae0546d09a`
- Substrate commit when this checkpoint was written: `b1cc017`
- Internal artifact root:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head`
- Run manifest:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\run-summary.json`
- External artifact root:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-external-benchmarks`

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

### External progress so far

| Surface | `summary_synthesis_memory` | `dual_store_event_calendar_hybrid` | Takeaway |
|---|---:|---:|---|
| BEAM public `128K` | `400/400` (`100.00%`) | `66/400` (`16.50%`) | Summary-synthesis is decisively stronger on the first completed external lane |
| LoCoMo | in progress | in progress | waiting on fresh reruns |
| LongMemEval_s | in progress | in progress | rerunning after a substrate sort-key fix |

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
  - `BEAM` public `128K`
- fresh reruns are still active for:
  - `LoCoMo`
  - `LongMemEval_s`

Two substrate fixes were required to get the external suite moving on current HEAD:

- `cce6e83` `Handle event calendar ids in yes-no ranking`
- `b1cc017` `Normalize observation id sorting for external benchmarks`

That means the external half of Phase A is now runnable from this checkout, but still incomplete.

## Recommendation

Do **not** flip `PINNED_RUNTIME_MEMORY_ARCHITECTURE` yet.

Current evidence says:

- offline internal data is a tie
- live internal regression still prefers `summary_synthesis_memory`
- current runtime remains pinned to `dual_store_event_calendar_hybrid`
- the first completed external lane (`BEAM` public `128K`) strongly favors `summary_synthesis_memory`
- `architecture_promotion_gap` still reproduces on fresh artifacts

That is enough to confirm the gap is real and to increase confidence in `summary_synthesis_memory`, but not enough to close Phase A mechanically because the remaining external lanes are still running.

## Next Actions

1. Finish the fresh external head-to-head for both contenders:
   - `LongMemEval`
   - `LoCoMo`
   - remaining `BEAM` public scales if Phase A requires the full public reproduction rather than the completed `128K` lane
2. Append those scores to this doc.
3. Re-evaluate whether the founder should keep or flip the runtime pin.
4. Only after explicit approval, update
   `src/spark_intelligence/memory/orchestrator.py`
   and rerun the full internal gate.

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
