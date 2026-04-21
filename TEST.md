# Memory Test Program

Date: 2026-04-22
Status: active
Repo: `C:\Users\USER\Desktop\spark-intelligence-builder`

## Purpose

This file defines how we test the memory system while keeping the live Telegram bot safe.

The goal is not just "tests pass."
The goal is:

- memory writes are correct
- recall is grounded
- lifecycle corrections work
- retention behavior stays governed
- live validation is trustworthy
- no test work causes Telegram ownership conflicts

## Telegram Safety Rules

These rules are mandatory during all memory testing:

1. Do not start any competing Telegram receiver.
2. Do not start the old Builder Telegram poller.
3. Do not change Telegram webhook or polling ownership from this repo.
4. Treat `spark-telegram-bot` as the single canonical owner of `@SparkAGI_bot`.
5. Run memory tests behind the existing receiver posture, never around it.

## Current Memory Surfaces

The test program must cover all current runtime lanes:

- `current_state`
- `structured_evidence`
- `raw_episode`
- `event`
- `belief`

It must also cover the current active-state consolidation surfaces:

- `current_blocker`
- `current_dependency`
- `current_constraint`
- `current_risk`
- `current_status`
- `current_owner`

## Test Levels

### Level 1: Fast Local Guard

Run on every meaningful memory change.

Commands:

```powershell
pytest tests/test_memory_orchestrator.py -q
pytest tests/test_telegram_generic_memory.py -q
pytest tests/test_memory_benchmark_packs.py tests/test_memory_regression.py -q
```

Pass criteria:

- all targeted memory unit tests pass
- all Telegram memory integration tests pass
- benchmark pack definitions and regression expectations stay aligned

### Level 2: Focused Slice Validation

Run when changing one narrow memory behavior.

Examples:

- belief lifecycle slice
- evidence consolidation slice
- active-state promotion slice
- archive or decay slice

Command pattern:

```powershell
pytest tests/test_memory_orchestrator.py -k "<focused_area>" -q
pytest tests/test_telegram_generic_memory.py -k "<focused_area>" -q
pytest tests/test_memory_benchmark_packs.py tests/test_memory_regression.py -q
```

Pass criteria:

- the changed lane passes in isolation
- no regression pack metadata breaks

### Level 3: Focused Live Regression

Run after local validation for any memory change that affects user-facing recall or lifecycle behavior.

Command pattern:

```powershell
python -m spark_intelligence.cli memory run-telegram-regression --home .tmp-home-live-telegram-real --benchmark-pack <pack_id> --json
```

Current important packs:

- `telegram_generic_profile_lifecycle`
- `telegram_open_memory_recall`
- `telegram_belief_memory_recall`
- `telegram_evidence_consolidation`
- `telegram_evidence_current_state_consolidation`
- `telegram_evidence_active_state_consolidation`

Pass criteria:

- all cases match
- `issue_labels: []`
- `kb_issue_labels: []` when present

If the live pack hangs silently:

1. do not start another Telegram process
2. do not broaden the test scope
3. record the hang as an operability failure
4. fall back to local coverage only for that turn
5. prioritize fixing live validation reliability in the next work slice

### Level 4: Manual Truth Smoke

Use after major routing or lifecycle changes.

Manual prompts to verify:

1. Current truth
- `What is our blocker?`
- `What is our dependency?`
- `What is our constraint?`
- `What is our risk?`
- `What is our status?`
- `Who is the owner?`

2. Source-grounded recall
- `What evidence do you have about onboarding?`
- `What happened during the demo?`

3. Derived memory
- `What is your current belief about onboarding?`
- verify the answer reads as inferred, not as source truth

4. Lifecycle
- overwrite a current fact
- ask for current truth
- ask for prior truth
- delete the fact
- confirm it does not resurrect from older evidence incorrectly

## Required Invariants

Every serious memory change must preserve these:

1. `belief` must remain visibly derived.
2. `current_state` must not resurrect deleted truth from older residue.
3. `structured_evidence` and `raw_episode` must not pollute current-truth answers.
4. current truth, prior truth, and event history must stay distinct.
5. per-user scope must hold on both write and read.
6. archive or decay behavior must remain explainable.

## Failure Classes To Track

When a test fails, classify it as one of these:

1. `promotion_false_positive`
- something became durable memory that should not have

2. `promotion_false_negative`
- something important stayed ephemeral when it should have been promoted

3. `lifecycle_regression`
- overwrite, supersession, invalidation, delete, restore, or prior-truth behavior broke

4. `retrieval_routing_regression`
- the right memory exists but the wrong retrieval path answered

5. `derived_truth_leak`
- belief answered as if it were source truth

6. `operability_failure`
- live regression hangs, flakes, or cannot be trusted as a gate

## Definition Of Proud

We should say the memory system is something we are proud of only when:

1. local suites are stable and fast
2. focused live packs are reliable enough to act as real gates
3. current truth, prior truth, source evidence, and belief are all distinct in behavior
4. promotion is governed by reusable policy more than one-off lane logic
5. archive, decay, invalidation, and rebuild are all explainable
6. long-run trace audits show low residue promotion and low stale-state drift

## Next Test Priorities

1. Fix the silent-hang behavior in focused live Telegram regression runs.
2. Add explicit trace-audit packs for false-positive and false-negative promotion behavior.
3. Add rebuild verification that reconstructs active current state from durable evidence and lifecycle history.
4. Add maintenance-loop tests for archive, revalidation, and decay across `belief`, `structured_evidence`, and `raw_episode`.
