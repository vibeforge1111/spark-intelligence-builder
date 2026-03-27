# Memory Phase Plan

Date: 2026-03-28

Scope:
- Spark Intelligence Builder
- Domain Chip Memory
- shadow mode and operator-safe evaluation only

This document is the stop-condition plan for the current memory pass. The goal is to keep progress concrete while preventing scope drift.

## Operating Rules

- Keep memory shadow-only unless a later phase explicitly says otherwise.
- Do not widen into generic memory QA.
- Do not widen into broad automatic extraction from arbitrary user turns.
- Do not widen into live primary-memory rollout.
- Prefer evidence from replay, reports, and operator inspection over assumptions.
- Add only one new memory family at a time after the current evaluation phase is complete.

## Phase 1: Stabilize Current Shadow Pass

Status: Done

Completed:
- direct Spark to Domain Chip Memory bridge works
- shadow replay export, validation, and report work
- SDK maintenance replay works
- operator commands exist:
  - `memory status`
  - `memory direct-smoke`
  - `memory lookup-current-state`
  - `memory inspect-human`
- current direct fact families are in place:
  - `profile.city`
  - `profile.timezone`
  - `profile.home_country`
  - `profile.preferred_name`
- mixed shadow batch validates and replays cleanly
- delete replay contract is tightened so delete turns emit evidence probes only under the current contract

Exit criteria:
- mixed shadow batch passes validation
- mixed shadow report stays clean on current-state, evidence, and historical probes
- delete turns do not create false-negative historical probes

## Phase 2: Shadow Evaluation Period

Status: Current phase

Goal:
- stop adding new memory families temporarily
- collect evidence from real usage on the other terminals

Use during this phase:
- `python -m spark_intelligence.cli memory status --home <spark-home> --json`
- `python -m spark_intelligence.cli memory inspect-human --home <spark-home> --human-id <human-id> --json`
- `python -m spark_intelligence.cli memory lookup-current-state --home <spark-home> --subject <subject> --predicate <predicate> --json`
- `python -m spark_intelligence.cli memory export-shadow-replay-batch ...`
- `python -m domain_chip_memory.cli validate-spark-shadow-replay-batch ...`
- `python -m domain_chip_memory.cli run-spark-shadow-report-batch ...`

Watch for:
- false accepts
- wrong normalization
- missing structured writes
- missing abstentions
- query-path uncertainty failures
- replay/report regressions under broader traffic

Exit criteria:
- repeated shadow batches stay structurally valid
- no new contract-level replay bugs appear
- current fact families remain stable under broader mixed traffic
- operator surfaces are sufficient to inspect memory behavior without DB spelunking

## Phase 3: One New Narrow Family Only If Phase 2 Holds

Status: Pending

Allowed work:
- add one explicit durable profile fact family
or
- add one narrow event-memory operator path

Required shape for any new family:
- explicit write detection
- direct memory write path
- narrow query path if appropriate
- operator visibility through inspect/status
- focused tests
- rerun mixed shadow batch after shipping

Not allowed:
- multiple new families in one batch
- generic “ask memory anything”
- multi-write-per-turn expansion without direct evidence it is needed

Exit criteria:
- the new family behaves as well as the existing direct fact families
- mixed shadow batch still validates and replays cleanly

## Phase 4: Improve Operator Confidence Before Broader Memory Ambition

Status: Pending

Goal:
- make memory easier to inspect and trust from one terminal

Likely work:
- improve per-human inspect output
- show recent write and read summaries more clearly
- surface abstention and rejection reasons more directly
- add small operator summaries for change history if needed

Exit criteria:
- debugging memory behavior is easy from operator commands
- most memory questions can be answered from the CLI surfaces already in repo

## Phase 5: Re-evaluate Promotion Readiness

Status: Pending

Goal:
- decide whether the system remains shadow-only
or
- whether one tiny internal non-primary read path is safe enough

Rules:
- no primary memory takeover
- no broad production rollout
- no promotion without evidence from repeated shadow batches

Exit criteria:
- enough shadow evidence exists to justify any behavior change
- abstention quality remains intact
- provenance and normalization remain reliable

## Done For Now

For the current pass, “done” means:
- Phase 1 is complete
- Phase 2 is active
- no new memory family work starts until Phase 2 has enough evidence

That is the current stop condition.
