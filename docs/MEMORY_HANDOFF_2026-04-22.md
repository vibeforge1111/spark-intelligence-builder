# Memory Handoff 2026-04-22

Status: active  
Primary repo: `C:\Users\USER\Desktop\spark-intelligence-builder`  
Companion repo: `C:\Users\USER\Desktop\domain-chip-memory`

## Purpose

This document is the canonical restart point for the current memory-system upgrade work.

It is meant to answer, without re-discovery:

- what state the system is actually in
- what changed in the latest phase
- what is already validated
- what remains unfinished
- what to do first tomorrow
- what not to touch while continuing

## Current Heads

### Builder

- repo: `spark-intelligence-builder`
- current memory head: `a086d58` `Add active-state revalidation to memory recall`

### Memory substrate

- repo: `domain-chip-memory`
- latest relevant memory-export head: `b3e8fda` `Surface belief lifecycle identifiers in builder reads`

## Non-Negotiable Safety Rules

These rules still apply tomorrow:

1. Do not start any competing Telegram receiver.
2. Do not start the old Builder Telegram poller.
3. Do not take webhook or polling ownership away from `spark-telegram-bot`.
4. Treat `spark-telegram-bot` as the single canonical owner of `@SparkAGI_bot`.
5. Run memory work behind the existing Telegram gateway posture, never around it.
6. Do not touch unrelated dirty files in either repo unless the task explicitly changes scope.

## What Was Completed In This Phase

The memory system moved from a narrower persistent-memory stack into a more governed multi-lane system with explicit lifecycle behavior.

### Major capability gains

1. `raw_episode`, `structured_evidence`, `current_state`, `event`, and `belief` are now real runtime lanes, not just conceptual buckets.
2. Open recall works for:
- current truth
- structured evidence
- raw episodes
- belief recall
3. Belief lifecycle now includes:
- supersession
- invalidation
- revalidation
- archive after stale invalidation
4. `structured_evidence` now promotes into stronger memory rather than only being stored.
5. Active-state promotion is no longer only handwritten one-off logic; it now has a real policy layer.
6. Focused Telegram live regression operability was improved so packs no longer hang as a black box.
7. Active-state memory now has freshness metadata and stale recall downgrade behavior.

## Recent Commit Chain

These commits are the practical story of the current phase.

### Planning and operability

- `0ddabb7` `Document memory execution plan and test program`
- `996a5ed` `Stabilize focused Telegram memory regressions`

### Evidence, recall, and belief lane

- `bbfeb68` `Acknowledge Telegram belief writes directly`
- `94d7dc8` `Supersede stale Telegram beliefs in recall`
- `296c92a` `Downgrade stale beliefs when newer evidence exists`
- `02bd036` `Mark beliefs invalidated by newer evidence`
- `fdc023b` `Revalidate stale beliefs before recall`
- `0082afc` `Archive invalidated beliefs after revalidation lapse`

### Retention and archival for residue lanes

- `3cf3385` `Archive stale raw episodes after evidence promotion`
- `e2afe2f` `Archive stale structured evidence after newer evidence lands`

### Consolidation into stronger memory

- `3aa12a7` `Promote corroborated evidence into derived beliefs`
- `6dbb44a` `Avoid probed belief consolidation lookups`
- `7acc764` `Promote corroborated evidence into current state`
- `2c97b38` `Promote corroborated evidence into active state`
- `86410ee` `Expand active-state owner and status capture`
- `f4fce45` `Generalize evidence-to-state promotion policy`
- `41a2065` `Add source-backed project-state consolidation pack`
- `a086d58` `Add active-state revalidation to memory recall`

### Substrate-side support

- `6e5455f` in `domain-chip-memory` allowed explicit raw-turn persistence
- `b3e8fda` in `domain-chip-memory` surfaced `observation_id`, `event_id`, `retention_class`, and `lifecycle` in Builder-facing reads

## Current Runtime Shape

### First-class memory roles

1. `working_memory`
2. `raw_episode`
3. `structured_evidence`
4. `current_state`
5. `event`
6. `belief`

### Current-state surfaces now covered

- `profile.current_blocker`
- `profile.current_dependency`
- `profile.current_constraint`
- `profile.current_risk`
- `profile.current_status`
- `profile.current_owner`
- `profile.current_plan`
- `profile.current_focus`
- `profile.current_decision`
- `profile.current_commitment`
- `profile.current_milestone`
- `profile.current_assumption`

### Current lifecycle behavior now present

- overwrite
- delete
- prior-truth recovery
- event-history recovery
- belief supersession
- belief invalidation
- belief revalidation
- stale belief downgrade
- stale belief archival
- stale raw-episode archival
- stale structured-evidence archival
- stale active-state downgrade on recall

## Latest Completed Slice

Latest completed slice: `a086d58`

### What it changed

Active-state memory is no longer treated as permanently current.

Current-state writes now store freshness metadata:

- `revalidate_after_days`
- `revalidate_at`

Current-state recall now downgrades stale answers instead of asserting them as current truth.

Example behavior:

- before: `Your current plan is to simplify onboarding approvals.`
- after stale threshold: `I have an older saved current plan: "simplify onboarding approvals" but it has not been revalidated recently, so I would not treat it as current.`

### Main files

- `src/spark_intelligence/memory/profile_facts.py`
- `src/spark_intelligence/memory/orchestrator.py`
- `src/spark_intelligence/researcher_bridge/advisory.py`
- `tests/test_memory_orchestrator.py`
- `tests/test_telegram_generic_memory.py`

## Benchmark Packs That Matter Right Now

Current important focused packs:

- `telegram_open_memory_recall`
- `telegram_belief_memory_recall`
- `telegram_evidence_consolidation`
- `telegram_evidence_current_state_consolidation`
- `telegram_evidence_active_state_consolidation`
- `telegram_evidence_project_state_consolidation`

These are defined in:

- `src/spark_intelligence/memory/benchmark_packs.py`
- `src/spark_intelligence/memory/regression.py`

## Validation State

### Latest local validation that passed

Commands:

```powershell
pytest tests/test_memory_orchestrator.py -k "profile_current_state_predicates_request_active_state_retention or profile_current_state_predicates_store_revalidation_metadata" -q
pytest tests/test_telegram_generic_memory.py -k "stale_current_plan_recall or promotes_high_confidence_project_state_fields_from_single_evidence_turn" -q
pytest tests/test_telegram_generic_memory.py -q
pytest tests/test_memory_benchmark_packs.py tests/test_memory_regression.py -q
```

Results:

- focused orchestrator slice: passed
- focused Telegram stale-current slice: passed
- full Telegram memory suite: `70 passed, 9 subtests passed`
- benchmark/regression slice: `17 passed`

### Latest live validation that passed

Command:

```powershell
python -m spark_intelligence.cli memory run-telegram-regression --home C:/Users/USER/Desktop/spark-intelligence-builder/.tmp-home-live-telegram-real --benchmark-pack telegram_evidence_active_state_consolidation --json
```

Result:

- `15/15` matched
- `kb_issue_labels: []`
- focused-slice architecture skip labels remained expected:
  - `architecture_benchmark_skipped_for_focused_slice`
  - `architecture_live_comparison_skipped_for_focused_slice`

### Important interpretation

At the end of this session:

- local memory coverage is strong
- focused live regression is workable again
- active-state freshness has started to become governed
- but maintenance is still not unified end to end

## Honest Current Assessment

Working estimate:

- Telegram-first production-useful memory: about `75%`
- full governed memory methodology target: about `60%`

This is no longer a memory demo.
It is a real memory subsystem.
But it is still partially route-shaped and not yet a finished general memory operating system.

## What Is Still Missing

### Highest-value unfinished work

1. Active-state maintenance loop
- we now stamp and downgrade stale current-state records
- we do not yet have a unified maintenance step that archives, demotes, or rebuilds them systematically

2. Rebuild fidelity
- current-state promotion is stronger
- but full reconstruction of current truth from evidence plus lifecycle history is not yet proven as a first-class test target

3. Promotion audit depth
- policy is better than before
- but false-positive and false-negative promotion audits are still not broad enough

4. Generality
- more logic is policy-shaped now
- but some routing and extraction paths are still narrower than the final target

5. Cross-lane maintenance unification
- beliefs have richer lifecycle treatment than active-state
- evidence and episode archival exist
- the system still lacks one clearly unified maintenance model across all durable roles

## Recommended Next Phase

Tomorrow’s best next move is:

### Phase: Active-state maintenance and revalidation governance

Goal:

- stop at-read stale labeling from being the only protection
- make active-state memory governed over time the same way belief memory now is

### Concrete next steps

1. Add policy-driven active-state maintenance rules
- decide which `profile.current_*` fields should:
  - only downgrade at read time
  - archive after long staleness
  - require corroboration to stay current
  - get rebuilt from newer evidence

2. Add explicit maintenance behavior
- active-state stale records should become explainably:
  - still current
  - stale but preserved
  - archived
  - superseded

3. Add focused tests
- orchestrator tests for active-state maintenance metadata and lifecycle transitions
- Telegram integration tests for stale current-state plus post-maintenance behavior

4. Add a focused benchmark pack
- likely something like active-state staleness / refresh / archive behavior

5. Run focused live regression again
- verify no recall regressions on current-state queries

## First Move Tomorrow

If resuming cold, do this first:

```powershell
cd C:\Users\USER\Desktop\spark-intelligence-builder
git log --oneline -n 12
Get-Content TASK.md | Select-Object -First 220
Get-Content TEST.md | Select-Object -First 220
Get-Content docs/MEMORY_HANDOFF_2026-04-22.md | Select-Object -First 260
```

Then begin from:

- `src/spark_intelligence/memory/profile_facts.py`
- `src/spark_intelligence/memory/orchestrator.py`
- `src/spark_intelligence/researcher_bridge/advisory.py`

The likely next code entry point is:

- current-state revalidation and lifecycle policy in `profile_facts.py`
- maintenance/archive behavior in `orchestrator.py`
- recall semantics in `advisory.py`

## Files Most Worth Reopening Tomorrow

### Planning / ground truth

- `TASK.md`
- `TEST.md`
- `docs/MEMORY_HANDOFF_2026-04-22.md`

### Core Builder memory files

- `src/spark_intelligence/memory/profile_facts.py`
- `src/spark_intelligence/memory/orchestrator.py`
- `src/spark_intelligence/researcher_bridge/advisory.py`
- `src/spark_intelligence/memory/benchmark_packs.py`
- `src/spark_intelligence/memory/regression.py`

### Core tests

- `tests/test_memory_orchestrator.py`
- `tests/test_telegram_generic_memory.py`
- `tests/test_memory_benchmark_packs.py`
- `tests/test_memory_regression.py`

### Companion repo files

- `C:\Users\USER\Desktop\domain-chip-memory\src\domain_chip_memory\builder_read_adapter.py`
- `C:\Users\USER\Desktop\domain-chip-memory\tests\test_builder_read_adapter.py`
- `C:\Users\USER\Desktop\domain-chip-memory\tests\test_builder_read_adapter_memory_roles.py`

## Dirty Worktree Boundaries

### Builder repo

There are unrelated modified and untracked files in `spark-intelligence-builder`.
They were intentionally left alone.

Do not revert or absorb them by accident.

Known unrelated modified files include:

- `README.md`
- `docs/TELEGRAM_GATEWAY_MIGRATION_PLAN_2026-04-21.md`
- `src/spark_intelligence/adapters/telegram/runtime.py`
- `src/spark_intelligence/bot_drafts/__init__.py`
- `src/spark_intelligence/bot_drafts/service.py`
- `tests/test_cli_smoke.py`
- `tests/test_draft_runtime_integration.py`
- `tests/test_researcher_bridge_provider_resolution.py`

There are also many unrelated untracked files and directories.

### Companion repo

`domain-chip-memory` also has unrelated local changes and many artifact files.

Known modified files there include:

- `src/domain_chip_memory/__init__.py`
- `src/domain_chip_memory/memory_dual_store_builder.py`

Do not normalize that worktree unless the task explicitly expands into it.

## Resume Discipline

When continuing:

1. keep the Telegram ownership rules intact
2. stage only focused memory files
3. run focused tests first
4. only widen to full Telegram suite after the narrow slice is green
5. run a focused live regression pack after user-facing recall changes
6. commit small, atomic slices

## Definition Of A Good Next Session

A good next session does not add random new predicates.

A good next session:

- improves active-state governance
- increases generality
- tightens lifecycle explanation
- preserves live Telegram safety
- leaves the regression surface cleaner than it found it

## Bottom Line

The memory system is now genuinely useful and increasingly governed.
The next real quality bar is not wider capture.
It is making active current-state memory behave like a mature managed system:

- promoted deliberately
- refreshed deliberately
- downgraded deliberately
- archived deliberately
- explainable at every step

