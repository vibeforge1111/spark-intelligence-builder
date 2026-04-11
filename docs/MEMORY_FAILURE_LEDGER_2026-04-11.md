# Memory Failure Ledger

Date: 2026-04-11
Status: generated from the latest validation run

## Baseline run

- wrapper output root: `C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\20260412-000813`
- manifest: `C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\20260412-000813\run-summary.json`
- stable pointer: `C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\latest-full-run.json`

Pinned code state behind this run:

- Builder: `1c4b4b2d03e96edf1f2da9c6b233d77943fb6954`
- domain-chip-memory: `29381adcdc787cfe4c7438b7def58afe5031f994`

## Baseline verdict

- offline runtime architecture: `summary_synthesis_memory`
- offline ProductMemory leaders:
  - summary_synthesis_memory
  - dual_store_event_calendar_hybrid
- live regression: `34/34`
- live regression leader:
  - summary_synthesis_memory
- live soak: `14/14`
- live soak leader:
  - summary_synthesis_memory
- live soak recommended top two:
  - summary_synthesis_memory
  - dual_store_event_calendar_hybrid

## Open failures

Current honest count:

- open live mismatches: `0`
- open selector-pack gaps requiring work: `0`

That means there is no current artifact-backed reason to claim a broken live memory surface on the active Builder/Telegram suite.

## Current watchlist

These are not current failures. They are the live separator packs and health gates that should keep being monitored during the 48-hour cycle.

### Selector packs

- `core_profile_baseline`
  - current state: `honest tie`
  - bucket if it regresses: `routing`
- `contradiction_and_recency`
  - current leader: `summary_synthesis_memory`
  - bucket if it regresses: `overwrite_history_retrieval`
- `provenance_audit`
  - current state: `honest tie`
  - bucket if it regresses: `explanation_provenance`
- `interleaved_noise_resilience`
  - current state: `honest tie`
  - bucket if it regresses: `identity_synthesis`
- `quality_lane_gauntlet`
  - current state: `honest tie`
  - bucket if it regresses: `routing`
- `temporal_conflict_gauntlet`
  - current leader: `summary_synthesis_memory`
  - bucket if it regresses: `event_history_chronology`
- `event_calendar_lineage_proxy`
  - current leader: `summary_synthesis_memory`
  - bucket if it regresses: `event_history_chronology`
- `explanation_pressure_suite`
  - current state: `honest tie`
  - bucket if it regresses: `explanation_provenance`

### Health gates

- `long_horizon_recall`
- `boundary_abstention`
- `anti_personalization_guardrails`
- `identity_synthesis`
- `loaded_context_abstention`
- `identity_under_recency_pressure`

These must stay green, but they should not be treated as runtime-selection votes unless they stop tying honestly.

## What counts as a real new issue

Do not add an item to this ledger unless one of these is true:

1. live regression produces a real mismatch
2. full soak produces a real failed run
3. a selector pack changes leader or loses correctness on a rerun
4. offline ProductMemory ceases to tie on the current two-contender head-to-head
5. the harness or wrapper stops producing trustworthy artifacts

## Next action from this ledger

Because there are no open live failures right now, the next work should not be speculative architecture changes.

The next valid actions are:

1. keep rerunning the full wrapper as the cycle baseline
2. only patch behavior when a real artifact-backed miss appears
3. improve operator reliability and artifact quality where that reduces ambiguity
