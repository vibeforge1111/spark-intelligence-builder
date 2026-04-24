# Memory Session Handoff

Date: 2026-04-12
Primary repo: `<workspace>\\spark-intelligence-builder`
Related substrate repo: `<workspace>\\domain-chip-memory`

> Historical snapshot note, updated April 21, 2026: this handoff reflects the April 12 operating state. The current pinned runtime has since moved to `dual_store_event_calendar_hybrid`, with the live mixed-session regression green at `16/16`.

## What We Are Trying To Achieve

The goal is not to win offline memory benchmarks in isolation.

The goal is to maintain a memory system for Spark Intelligence Builder that is:

- strong on offline benchmark pressure
- strong on live Telegram runtime behavior
- repeatable and artifact-backed
- hard to fool with stale docs, stale pointers, or smoke-run pollution
- safe to evolve without silently regressing the operator flow

The current operating doctrine is:

- use `summary_synthesis_memory` and `dual_store_event_calendar_hybrid` as the only serious contenders
- do not promote memory behavior on offline wins alone
- require live Builder/Telegram regression and soak evidence before trusting a change
- keep the runtime pinned to the contender that holds up best on the combined picture

## Current Truth

As of the latest serious full run, the state is:

- runtime pin: `summary_synthesis_memory`
- offline head-to-head `ProductMemory`: tied at `1156/1266`
- live Telegram regression: `34/34`
- live Telegram soak: `14/14`
- live whole-suite leader: `summary_synthesis_memory`

Current canonical full baseline:

- full validation root:
  `$SPARK_HOME\artifacts\memory-validation-runs\20260412-023241`
- latest full-run pointer:
  `$SPARK_HOME\artifacts\memory-validation-runs\latest-full-run.json`
- previous full-run pointer:
  `$SPARK_HOME\artifacts\memory-validation-runs\previous-full-run.json`
- validation delta for the latest run:
  `$SPARK_HOME\artifacts\memory-validation-runs\20260412-023241\validation-delta.md`

Latest timings from that canonical baseline:

- benchmark: `13.543s`
- regression: `23.724s`
- soak: `339.130s`
- total: `376.594s`

This is the baseline to trust tomorrow unless a newer serious full run replaces it.

## What Was Done Today

Today’s work was mostly about making the memory-validation/operator layer trustworthy and fast to iterate on, not about changing memory retrieval logic itself.

### 1. Locked down the automation layer with tests

Builder automation coverage now includes:

- baseline doc rendering
- failure-ledger rendering for clean and broken runs
- validation-delta rendering for pointer and direct-summary inputs
- wrapper no-op manifest behavior
- wrapper pointer recovery
- wrapper banner states:
  `unknown`, `clean`, `warning`
- fake full-run wrapper path with sibling chip repo
- fake full-run wrapper path without sibling chip repo
- chained validated full-cycle entrypoint
- non-publishing full-run behavior
- explicit `-PublishBaseline` override behavior

Chip automation coverage now includes:

- Builder baseline renderer with `warning` freshness
- Builder baseline renderer with `clean` freshness
- Builder baseline renderer with `unknown` freshness
- fallback formatting for missing selector rows and unknown timing data

Current fast automation suite status:

- Builder:
  `pytest tests/test_memory_baseline_doc_rendering.py tests/test_memory_validation_artifact_rendering.py tests/test_memory_validation_wrapper.py`
  -> `16 passed`
- Chip:
  `pytest tests/test_builder_baseline_doc_rendering.py`
  -> `3 passed`

### 2. Added fast and safe operator entrypoints

New scripts in Builder:

- `scripts/run_memory_automation_tests.ps1`
  fast harness-only validation
- `scripts/run_memory_validated_full_cycle.ps1`
  fast automation preflight, then real full validation

Existing full wrapper strengthened further:

- `scripts/run_memory_two_contender_validation.ps1`

Important operator safety change:

- non-default full-cycle runs now auto-skip baseline publishing
- that means custom output roots, short soaks, and ad hoc smokes no longer overwrite the canonical `latest-full-run.json` baseline by accident
- if you intentionally want a non-default run to become canonical, you must pass `-PublishBaseline`

### 3. Repeated real full validation runs to keep the baseline honest

Serious full Builder/Telegram runs were rerun multiple times during the session to keep the pointers and docs on real evidence.

The latest serious one is still:

- `20260412-023241`
- regression `34/34`
- soak `14/14`
- live leader `summary_synthesis_memory`

### 4. Kept both repos in sync

Builder full runs continue to auto-refresh:

- Builder README
- Builder memory live-validation doc
- Builder benchmark handoff doc
- Builder failure ledger

Builder full runs also continue to auto-refresh chip-side Builder-alignment docs when the sibling repo exists:

- chip README
- chip next-phase Builder alignment doc
- chip current-status Builder alignment doc

So the cross-repo state is synchronized again to the same canonical baseline.

## Key Files And Moving Parts

### Builder operator scripts

- `scripts/run_memory_two_contender_validation.ps1`
  serious full benchmark + regression + soak wrapper
- `scripts/run_memory_automation_tests.ps1`
  fast automation-only preflight
- `scripts/run_memory_validated_full_cycle.ps1`
  preflight first, full validation second

### Builder renderers

- `scripts/render_memory_baseline_docs.py`
- `scripts/render_memory_failure_ledger.py`
- `scripts/render_memory_validation_delta.py`

### Chip renderer

- `<workspace>\\domain-chip-memory\scripts\render_builder_baseline_docs.py`

### Builder tests

- `tests/test_memory_baseline_doc_rendering.py`
- `tests/test_memory_validation_artifact_rendering.py`
- `tests/test_memory_validation_wrapper.py`

### Chip tests

- `<workspace>\\domain-chip-memory\tests\test_builder_baseline_doc_rendering.py`

### Canonical artifact pointers

- latest run of any kind:
  `$SPARK_HOME\artifacts\memory-validation-runs\latest-run.json`
- latest serious full run:
  `$SPARK_HOME\artifacts\memory-validation-runs\latest-full-run.json`
- previous serious full run:
  `$SPARK_HOME\artifacts\memory-validation-runs\previous-full-run.json`

## Current Workflow Rules

### Rule 1: Only two real contenders

- `summary_synthesis_memory`
- `dual_store_event_calendar_hybrid`

No new default contenders should be introduced casually.

### Rule 2: Separate fast harness validation from real live validation

Use this for fast operator/harness checks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_automation_tests.ps1
```

Use this for the real full live Builder/Telegram validation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_two_contender_validation.ps1
```

Use this when you want both in one command:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_validated_full_cycle.ps1
```

### Rule 3: Do not accidentally republish the canonical baseline

The canonical baseline should only move on serious runs.

Safe behavior now:

- `run_memory_validated_full_cycle.ps1` auto-skips publishing on non-default runs
- ad hoc smokes can also pass `-SkipBaselinePublish` directly to the underlying full wrapper
- explicit publication on non-default runs now requires `-PublishBaseline`

If tomorrow’s run is a smoke, short soak, or custom-output experiment, it should not become the canonical baseline unless that is intentional.

### Rule 4: Memory logic changes still require real live evidence

If tomorrow we start changing the actual memory behavior again, the acceptance rule remains:

1. targeted fix
2. targeted rerun
3. full live Builder/Telegram rerun
4. only then trust the result

## What We Intend To Do Tomorrow

Tomorrow should not start with more harness invention unless a real issue appears.

The harness/operator layer is now in a much better place:

- strong regression coverage
- safe non-publishing smokes
- fast automation preflight
- chained full-cycle entrypoint
- cross-repo doc sync
- canonical baseline pointers

That means tomorrow should probably shift back toward one of these two tracks:

### Track A: Memory behavior work, if a real artifact-backed issue appears

If tomorrow’s serious full run or targeted pack rerun exposes a real memory weakness:

- identify the failing pack or exact case
- bucket it:
  routing, overwrite/history retrieval, chronology, explanation/provenance, identity synthesis, abstention, harness
- patch only that bucket
- rerun the smallest meaningful live slice
- if the slice improves, rerun the full validation

This is the preferred product path if the artifacts expose a real weakness.

### Track B: Keep the memory system stable and repeatable if no failures appear

If the system stays green:

- do not mutate memory logic speculatively
- keep running the real full wrapper periodically
- keep the baseline docs and pointers honest
- use the fast automation runner for any future wrapper/doc tooling changes

This is the preferred path if tomorrow starts from a clean baseline and there is no real product failure to fix.

## Recommended Start Sequence For Tomorrow

Use this exact order:

1. Read this file first.
2. Read:
   - `docs/MEMORY_FAILURE_LEDGER_2026-04-11.md`
   - `docs/MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md`
   - `docs/MEMORY_BENCHMARK_HANDOFF_2026-04-11.md`
3. Inspect:
   - `$SPARK_HOME\artifacts\memory-validation-runs\latest-full-run.json`
   - `$SPARK_HOME\artifacts\memory-validation-runs\previous-full-run.json`
4. Run the fast automation preflight if the morning changes are only wrapper/doc/test related:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_automation_tests.ps1
```

5. Run the real full cycle when you need fresh live truth:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_validated_full_cycle.ps1
```

6. If tomorrow’s run is a smoke or shortened cycle, keep it non-publishing unless explicitly intended:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_validated_full_cycle.ps1 `
  -OutputRoot "$env:TEMP\memory-smoke" `
  -SoakRuns 1 `
  -SoakTimeoutSeconds 180
```

That command now auto-skips baseline publishing because it is non-default.

## Session-Level Summary

The important truth from today is:

- the memory system itself remained stable
- the validated live winner remained `summary_synthesis_memory`
- the biggest real work was turning the benchmark/validation/operator layer into something reliable enough that tomorrow’s work can be faster and safer

In short:

- the product memory decision did not flip
- the live runtime stayed green
- the automation around that decision is much harder to regress now
- tomorrow should build on the current baseline, not rediscover it
