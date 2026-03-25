---
name: reliable-job-harnesses
description: Build and review cron jobs, schedulers, retries, smoke tests, and operational harnesses for Spark Intelligence in a lightweight and highly reliable way. Use when designing job systems, background work, health checks, retry logic, cron behavior, migration jobs, or smoke-test coverage for scheduled work.
---

# Reliable Job Harnesses

Use this skill when work touches:

- cron jobs
- schedulers
- retries
- background jobs
- health checks
- migration jobs
- smoke tests for scheduled systems

## Read First

- `docs/CRON_JOB_HARNESS_SPEC_V1.md`
- `docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md`
- `docs/CODING_RULESET_V1.md`
- `docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md`

Then read:

- `references/workflow.md`

## Core Doctrine

The correct shape is:

- one scheduler
- one job record model
- one retry model
- one observability model
- one manual debug path
- one clear distinction between scheduled work and the Spark governing loop

Reject:

- competing timers
- hidden background workers
- job-specific state forks
- retries that are not visible
- any architecture that needs frequent operator babysitting

## Workflow

1. Identify the exact job owner and why the job exists.
2. Check whether the work should be a scheduled job at all.
3. Force the design into the central harness.
4. Define idempotence, retry policy, and observability.
5. Define the operator repair and doctor path.
6. Define the minimum smoke tests.
7. Check for scheduler duplication, hidden loops, or fake cron-shaped work.
8. Keep the implementation lightweight by default.

If the task is deeper than a quick pass, use the output template and anti-pattern checklist in `references/workflow.md`.

## Required Outputs

Return these explicitly:

- job owner
- trigger type
- payload shape
- idempotence rule
- retry rule
- failure visibility
- smoke tests required
- reasons this does not create a competing subsystem

## Spark-Specific Rules

- Use Spark Researcher and Spark Swarm through integration boundaries, not job-local copies.
- Keep memory logic out of the harness unless the memory chip exposes a clear contract.
- Prefer SQLite-backed metadata in v1.
- Prefer one internal scheduler over external orchestration in v1.
- Do not turn Spark flywheels, specialization evolution, or inline event handling into cron jobs unless they are truly time-based.
- Prefer doctorable systems over clever auto-healing that hides failure state.

## Review Lens

When reviewing harness code, optimize for:

- boring reliability
- clean failure modes
- low maintenance burden
- easy manual debugging

Findings should prioritize:

- double-run risk
- silent failure risk
- stale lock risk
- retry storms
- hidden state
- missing smoke coverage

## Default Deliverable

Give a concrete answer, not generic principles. The result should usually end in a small harness plan, review finding set, or smoke-test checklist.
