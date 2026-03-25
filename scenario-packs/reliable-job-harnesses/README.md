# Reliable Job Harnesses Scenario Pack

This pack exists to pressure-test `reliable-job-harnesses` against realistic Spark Intelligence decisions.

The point is not to generate prose.

The point is to force clear answers to:

- should this be a scheduled job at all
- where does ownership live
- what makes the repair path trustworthy
- how do we keep the harness lightweight

## How To Use

Take one scenario at a time and answer it using:

- `skills/reliable-job-harnesses/SKILL.md`
- `skills/reliable-job-harnesses/references/workflow.md`
- `docs/CRON_JOB_HARNESS_SPEC_V1.md`
- `docs/CODING_RULESET_V1.md`

## Success Criteria

A good answer should:

- classify the work correctly
- reject fake cron usage where needed
- assign one clear owner
- define retry and doctor behavior
- define a small smoke-test set
- preserve Spark subsystem boundaries

## Included Scenarios

- `01_adapter_reconnect_reliability.md`
- `02_import_job_replay.md`
- `03_governing_loop_vs_cron.md`
