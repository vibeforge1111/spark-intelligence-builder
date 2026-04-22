# Phase A Architecture Decision 2026-04-22

**Status:** partial Phase A complete.
**Decision state:** no pin change recommended yet.
**Reason:** internal head-to-head is now captured on current HEAD, but the required external benchmark suite is blocked locally because the substrate checkout does not contain `benchmark_data/`.

## Snapshot

- Builder commit: `472f3c6aacdc13f18c164fde0eb5cbae0546d09a`
- Substrate commit: `b3e8fdacba3f6597d4ed565b949b0e401cc992ab`
- Internal artifact root:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head`
- Run manifest:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\run-summary.json`

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

## What Is Still Blocked

Phase A requires external benchmark data for both contenders across:

- `BEAM`
- `LoCoMo`
- `LongMemEval`

The substrate checkout currently references these local inputs:

- `benchmark_data/official/LongMemEval/data/longmemeval_s_cleaned.json`
- `benchmark_data/official/LoCoMo/data/locomo10.json`
- `benchmark_data/official/BEAM-upstream/chats`

Observed on this machine during this turn:

- `C:\Users\USER\Desktop\domain-chip-memory\benchmark_data` is absent
- `benchmark_data/` is gitignored in
  `C:\Users\USER\Desktop\domain-chip-memory\.gitignore`
- historical artifact files exist, but they are not sufficient to claim a fresh two-contender external rerun on current HEAD

That means the external half of Phase A is not runnable from this checkout alone.

## Recommendation

Do **not** flip `PINNED_RUNTIME_MEMORY_ARCHITECTURE` yet.

Current evidence says:

- offline internal data is a tie
- live internal regression still prefers `summary_synthesis_memory`
- current runtime remains pinned to `dual_store_event_calendar_hybrid`
- `architecture_promotion_gap` still reproduces on fresh artifacts

That is enough to confirm the gap is real, but not enough to close Phase A mechanically because the required external suite is still missing.

## Next Actions

1. Restore or mount substrate `benchmark_data/` locally.
2. Run external head-to-head for both contenders:
   - `LongMemEval`
   - `LoCoMo`
   - `BEAM`
3. Append those scores to this doc.
4. Re-evaluate whether the founder should keep or flip the runtime pin.
5. Only after explicit approval, update
   `src/spark_intelligence/memory/orchestrator.py`
   and rerun the full internal gate.

## Artifact Paths

- Offline benchmark:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\memory-architecture-benchmark\memory-architecture-benchmark.json`
- Live regression:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\telegram-memory-regression\telegram-memory-regression.json`
- Live soak:
  `C:\Users\USER\.spark-intelligence\artifacts\phase-a-internal\2026-04-22-head-to-head\telegram-memory-architecture-soak\telegram-memory-architecture-soak.json`
