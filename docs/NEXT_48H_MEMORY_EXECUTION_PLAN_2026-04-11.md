# Next 48H Memory Execution Plan

Date: 2026-04-11
Repo: `spark-intelligence-builder`
Related substrate repo: `C:\Users\USER\Desktop\domain-chip-memory`
Status: active execution plan

## Objective

Over the next 48 hours, make the current Builder-connected memory system:

- repeatably strong on live Telegram
- benchmark-grounded instead of intuition-grounded
- operator-safe to run quickly
- easy to inspect without hallucinating progress

This plan assumes the current starting point is:

- runtime pin: `summary_synthesis_memory`
- active challenger: `dual_store_event_calendar_hybrid`
- offline head-to-head ProductMemory: tied at `1156/1266`
- latest clean timeout-hardened live whole-suite soak:
  - `summary_synthesis_memory`: `92/92`
  - `dual_store_event_calendar_hybrid`: `89/92`
- latest clean selector-pack aggregate:
  - `summary_synthesis_memory`: `64/64`
  - `dual_store_event_calendar_hybrid`: `61/64`

## What "great working memory" means

For this cycle, "great" does not mean universal AGI memory.

It means:

1. live Telegram regression stays green
2. full live soak stays green
3. offline benchmark does not regress
4. changes are tied to artifacts, not impressions
5. the runtime behaves correctly on:
   - long-horizon recall
   - overwrite and correction
   - contradiction and recency
   - explanation and provenance
   - identity synthesis
   - event and history recall
   - abstention and anti-hallucination lanes

## Hard rules

These rules are part of the plan, not optional suggestions.

1. Do not expand the default contender set.
The only serious contenders remain:
   - `summary_synthesis_memory`
   - `dual_store_event_calendar_hybrid`

2. Do not promote on offline wins alone.

3. Do not trust ad hoc mixed-pack runs as promotion evidence.
Use:
   - isolated targeted pack reruns
   - full regression
   - full soak

4. Do not count a result that is not preserved in artifacts.

5. Do not mix multiple memory-behavior fixes into one commit when avoidable.
One behavior slice, one validation slice, one commit.

6. Do not treat health-gate ties as selector signals.

## Operating entrypoint

Default entrypoint for serious validation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_two_contender_validation.ps1
```

What it already gives us:

- fixed contender pair
- timestamped artifact root
- per-run manifest
- latest-run pointer
- verdict summary
- builder and chip repo SHAs
- soak timeout protection

Stable pointer for the latest run:

```text
C:\Users\USER\.spark-intelligence\artifacts\memory-validation-runs\latest-run.json
```

## Core loop

This is the exact loop to repeat.

1. Run the wrapper.
2. Read the latest run manifest and the produced soak/regression artifacts.
3. Identify only real live misses or weak selector-pack separations.
4. Bucket each issue into a small failure taxonomy.
5. Fix one bucket at a time.
6. Rerun only the affected targeted pack or regression slice.
7. If the targeted result improves, rerun the full wrapper.
8. If the full wrapper stays green, keep the change.
9. If the full wrapper regresses, revert the idea or narrow the patch.

## Failure taxonomy

Every real miss should be classified before fixing it.

Allowed buckets:

- `routing`
- `overwrite_history_retrieval`
- `explanation_provenance`
- `identity_synthesis`
- `event_history_chronology`
- `abstention_guardrails`
- `harness_or_operator_surface`

Every fix should name one primary bucket.

## Phase plan

### Phase 1: Freeze and Baseline

Goal:
- start from one current, trusted, reproducible baseline

Actions:
- run the wrapper end-to-end
- verify `latest-run.json` points to the new run
- verify `run-summary.json` has:
  - repo SHAs
  - baselines
  - soak timeout
  - verdict fields
- record the starting baseline artifact paths

Success criteria:
- one clean wrapper run exists
- one stable pointer exists
- one stable manifest exists
- one current failure ledger exists, even if it records zero open live failures

Do not move on if:
- any step failed
- manifests are incomplete
- soak hung or timed out unexpectedly

### Phase 2: Build The Failure Ledger

Goal:
- make all remaining live weakness concrete and inspectable

Actions:
- create a compact failure ledger from the latest regression and soak artifacts
- regenerate it with:
  `python scripts/render_memory_failure_ledger.py --write docs/MEMORY_FAILURE_LEDGER_2026-04-11.md`
- for each miss or weak selector pack, record:
  - pack id
  - case id
  - expected behavior
  - actual behavior
  - bucket
  - suspected root cause
  - current status

Priority order:
- real live mismatches
- selector packs that still provide separation
- harness/operator issues
- health gates last

Success criteria:
- no open issue remains unbucketed
- no fix is started without a concrete case id or artifact reference

### Phase 3: Attack The Highest-Value Bucket

Goal:
- improve real runtime quality, not just metrics churn

Actions:
- select the single highest-value open bucket
- patch only that behavior
- keep the change minimal
- commit the change separately

Fix order:

1. live user-facing wrong answers
2. history or overwrite mistakes
3. explanation/provenance mistakes
4. identity synthesis mistakes
5. harness reliability issues

Success criteria:
- targeted pack improves or stays green
- no new targeted regressions appear in the same area

### Phase 4: Revalidate Targeted Scope

Goal:
- prove the fix helped the exact thing it was meant to help

Actions:
- rerun only the affected benchmark pack or slice
- use isolated outputs
- verify:
  - matched count
  - live architecture leaders
  - KB probe coverage if applicable

Success criteria:
- the targeted issue is fixed
- the targeted issue did not just move into a neighboring case

### Phase 5: Revalidate Full Runtime

Goal:
- prove the system still works as a whole

Actions:
- rerun the wrapper end-to-end
- compare against the previous manifest
- check:
  - offline tie did not regress
  - live regression stayed green
  - live soak stayed green
  - selector-pack leader story did not degrade

Success criteria:
- no regression in the current runtime pin justification

### Phase 6: Repeat Or Stop

Goal:
- keep momentum without churn

Repeat only if one of these is true:

- there is still a real live mismatch
- there is still a selector-pack weakness worth improving
- the harness still has reliability gaps

Stop for the cycle if:

- live regression is green
- full soak is green
- no high-value open failures remain
- the next work item would just be speculative architecture churn

## 48-hour schedule

### Block A: First half day

Deliverables:

- one fresh full wrapper run
- one failure ledger
- one prioritized top-3 issue list

Expected focus:

- make sure the environment is clean
- make sure artifacts are readable
- do not change architecture yet

### Block B: Second half day

Deliverables:

- 1 to 2 tightly scoped behavior fixes
- targeted reruns for each
- commits for each slice

Expected focus:

- only issues with direct live impact

### Block C: Day 2 first half

Deliverables:

- full wrapper rerun after the previous fixes
- updated ledger
- decision on whether another fix is justified

Expected focus:

- full-system confirmation
- no speculative tuning

### Block D: Day 2 second half

Deliverables:

- either one final high-value fix plus rerun
- or freeze the current state and document the new baseline

Expected focus:

- preserve reliability
- avoid unnecessary churn once the suite is already strong

## What to optimize for

Optimize for:

- real Telegram correctness
- history and overwrite reliability
- provenance/explanation honesty
- stable validation operations

Do not optimize for:

- making offline charts look better if live does not improve
- adding more contenders
- adding more benchmark surface area before the current loop is stable
- re-explaining old architecture debates

## Concrete commands

Full validation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_two_contender_validation.ps1
```

Wrapper with custom soak runs:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_two_contender_validation.ps1 `
  -SoakRuns 14 `
  -SoakTimeoutSeconds 180
```

Targeted pack rerun:

```powershell
spark-intelligence memory run-telegram-regression `
  --home C:\Users\USER\.spark-intelligence `
  --output-dir C:\Users\USER\.spark-intelligence\artifacts\targeted-pack-rerun `
  --benchmark-pack temporal_conflict_gauntlet `
  --baseline summary_synthesis_memory `
  --baseline dual_store_event_calendar_hybrid
```

Timeout-hardened soak:

```powershell
spark-intelligence memory soak-architectures `
  --home C:\Users\USER\.spark-intelligence `
  --output-dir C:\Users\USER\.spark-intelligence\artifacts\manual-soak-rerun `
  --runs 14 `
  --baseline summary_synthesis_memory `
  --baseline dual_store_event_calendar_hybrid `
  --run-timeout-seconds 180
```

## Decision gates

Keep `summary_synthesis_memory` pinned if:

- offline remains tied or better
- live regression remains green
- live soak still favors `summary_synthesis_memory`

Consider repin only if:

- `dual_store_event_calendar_hybrid` wins offline
- and wins the live whole-suite result
- and the win survives a clean rerun

## Most likely near-term work

The most likely next improvements should come from:

- tightening remaining explanation/provenance quality
- sharpening event-history and chronology behavior where it still matters
- reducing operator mistakes and harness ambiguity

Not from:

- broad new architecture speculation
- adding a third default contender
- re-running stale benchmark lanes without a concrete reason

## End state for this 48H plan

By the end of this cycle, we want:

- one reliable operator command
- one trusted latest-run pointer
- one self-describing manifest
- one trustworthy whole-suite live artifact
- one current pinned runtime justified by both offline and live evidence
- one small, current list of real remaining issues instead of vague uncertainty
