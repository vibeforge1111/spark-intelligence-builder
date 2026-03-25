# Spark Intelligence v1 Cron and Job Harness Spec

## 1. Purpose

This document defines the v1 scheduler, cron, and job harness architecture for `Spark Intelligence`.

The goal is simple:

- scheduled work should run reliably
- jobs should be easy to understand
- jobs should be easy to debug
- jobs should not fight each other
- the operator should be able to trust what the system is doing

This spec is intentionally conservative.

We want:

- one scheduler
- one job record model
- one retry model
- one observability model
- one way to run a job manually

We do not want:

- multiple competing timers
- hidden background workers
- silent failures
- job-specific state models
- flaky scheduling logic that requires constant babysitting

## 2. Design Goals

### 2.1 Reliability Over Cleverness

Scheduled work must be boring and dependable.

### 2.2 One Obvious Way

All recurring work should pass through one harness.

### 2.3 Idempotence by Default

Jobs should be safe to retry.

### 2.4 Visibility by Default

Every important job should have:

- last run time
- last success time
- last failure time
- last error
- current status

### 2.5 Manual Debuggability

Every important job should be runnable on demand from the CLI.

### 2.6 Lightweight Operation

v1 should avoid external schedulers, message brokers, and separate queue platforms unless they solve a real problem we actually have.

## 3. Competitive Learning

This spec is informed by patterns and failure signals from OpenClaw and Hermes.

### 3.1 What OpenClaw Gets Right

OpenClaw has a serious `doctor` flow and treats operational state as something worth auditing and repairing. Its docs explicitly cover:

- config normalization
- legacy state migrations
- legacy cron store migrations
- state integrity checks
- gateway health checks
- service repair
- runtime best practices

This is strong and worth borrowing as an operational philosophy. Source: [OpenClaw Doctor](https://docs.openclaw.ai/gateway/doctor).

### 3.2 What OpenClaw Signals We Should Avoid

OpenClaw issue and doc signals suggest recurring pain around:

- cron timers not firing
- out-of-order delivery
- watchdog and reconnect flap loops
- typing or long-running status getting stuck
- thread context leaks
- orphan processes and memory pressure

These are not reasons to dismiss OpenClaw. They are reasons to tighten our harness model. Sources:

- [OpenClaw issues search](https://github.com/openclaw/openclaw/issues)
- [OpenClaw Doctor](https://docs.openclaw.ai/gateway/doctor)

### 3.3 What Hermes Gets Right

Hermes appears cleaner in a few architecture areas:

- shared runtime provider resolution across CLI, gateway, cron, and auxiliary calls
- SQLite-backed session and state persistence
- cron jobs as first-class agent tasks, not just shell tasks
- install, doctor, and status flows built into the normal user path

These patterns fit Spark Intelligence well. Sources:

- [Hermes Installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation/)
- [Hermes Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture/)

### 3.4 Spark Conclusion

We should combine:

- OpenClaw's seriousness about operational audit and repair
- Hermes' cleaner shared runtime shape

But keep the implementation lighter than both where possible.

## 4. Core Decision

Spark Intelligence v1 should have exactly one internal scheduler and one job harness.

That harness should own:

- cron evaluation
- delayed retries
- periodic health checks
- maintenance jobs
- import and migration jobs
- adapter reconciliation jobs

It should not own:

- the internals of Spark Researcher
- the internals of Spark Swarm
- memory doctrine
- arbitrary app-specific background systems outside the job contract

## 5. Job Types

### 5.1 Scheduled Jobs

Examples:

- daily briefings
- periodic wakeups
- recurring syncs
- periodic diagnostics

### 5.2 Retry Jobs

Examples:

- transient adapter reconnect attempts
- deferred delivery retries
- failed import retry

### 5.3 Maintenance Jobs

Examples:

- session cleanup
- adapter state cleanup
- migration cleanup
- old artifact cleanup

### 5.4 Health Jobs

Examples:

- gateway self-check
- adapter heartbeat
- scheduler self-check
- state integrity check

### 5.5 Operator-Triggered Jobs

Examples:

- run import now
- re-run migration
- retry failed delivery
- trigger smoke-safe probe

## 6. Job Ownership Model

Every job must have a single clear owner.

Owner examples:

- gateway
- adapter
- import subsystem
- maintenance subsystem

A job may call Spark Researcher or Spark Swarm, but the harness still owns scheduling and status.

## 7. Job Record Model

Every job should use one canonical record shape.

Recommended v1 fields:

```text
id
kind
owner
schedule_type
schedule_expr
payload
status
attempt_count
max_attempts
next_run_at
last_started_at
last_finished_at
last_succeeded_at
last_failed_at
last_error
last_error_code
locked_by
locked_at
created_at
updated_at
disabled
```

### 7.1 Status Values

Recommended v1 status set:

- `pending`
- `running`
- `succeeded`
- `failed`
- `retry_scheduled`
- `disabled`

Do not add many near-duplicate states in v1.

## 8. Storage Model

Use one SQLite-backed job store in v1.

Reasons:

- simple deployment
- simple inspection
- local-first
- easy to back up
- good enough for the expected scale of v1

Do not add:

- Redis queue
- Kafka
- separate scheduler service
- job system split across multiple storage backends

## 9. Scheduler Model

The scheduler should run inside the main runtime process in v1.

Recommended model:

- one polling loop
- one due-job selector
- one execution path
- one locking strategy

### 9.1 Due Job Selection

Each tick:

1. read jobs whose `next_run_at <= now`
2. skip disabled jobs
3. skip jobs already locked and still alive
4. lock one job
5. execute
6. persist result
7. compute next state

### 9.2 Tick Frequency

Keep this simple.

Recommended v1:

- coarse periodic polling
- not many per-job timers

This avoids timer drift chaos and “why didn’t that one timer fire?” behavior.

### 9.3 Locking

Use one lock model.

Recommended v1:

- row-level logical lock fields in SQLite
- stale-lock recovery rules
- explicit lock ownership metadata

## 10. Retry Model

Retries must be explicit.

Every retryable job must define:

- which errors are retryable
- maximum retry count
- retry delay or backoff
- terminal failure behavior

### 10.1 Retry Rules

Good retry candidates:

- temporary network failures
- adapter reconnect windows
- transient provider failures

Bad retry candidates:

- invalid configuration
- malformed payloads
- permission failures that require operator action
- migration transforms that fail validation

### 10.2 Backoff

Use one simple retry strategy in v1.

Recommended:

- bounded exponential backoff with jitter for network-like failures
- fixed short delays for safe local probes

## 11. Manual Run Mode

Every important job should support a manual run path.

Recommended CLI shape:

- `spark-intelligence jobs list`
- `spark-intelligence jobs run <job-id>`
- `spark-intelligence jobs retry <job-id>`
- `spark-intelligence jobs disable <job-id>`
- `spark-intelligence jobs inspect <job-id>`

Manual execution should:

- use the same codepath as scheduled execution
- not bypass validation
- mark results in the same job store

## 12. Observability

The operator should be able to answer:

- what jobs exist?
- which jobs are due?
- which jobs are stuck?
- which jobs are failing repeatedly?
- what ran recently?
- what failed and why?

### 12.1 Required Signals

For v1, every important job should expose:

- job id
- job owner
- current status
- next run
- last success
- last failure
- last error
- attempt count

### 12.2 Logs

Job execution logs should be:

- structured
- minimal but sufficient
- correlated by job id
- easy to inspect locally

### 12.3 Health Surfaces

The scheduler itself should have health signals:

- loop alive
- last tick
- due jobs backlog
- stale locks count
- failed jobs count

## 13. Health and Repair

Borrowing from OpenClaw's operational posture, Spark Intelligence should eventually have `doctor` checks that include job harness health.

Recommended checks:

- job store readable and writable
- stale job schema detection
- stale locks
- jobs stuck in running
- impossible next-run timestamps
- retry storms
- disabled critical jobs
- duplicate scheduler detection

## 14. Anti-Patterns To Reject

### 14.1 Per-Job Timers Everywhere

This increases drift, invisibility, and debugging pain.

### 14.2 Multiple Schedulers

Do not have one scheduler in the gateway, another in an adapter, and another in a sidecar.

### 14.3 Silent Background Threads

If a background loop exists, it must be visible and owned.

### 14.4 Job Logic Inside Adapters

Adapters should not invent their own scheduling semantics.

### 14.5 Hidden Auto-Retry

Retries must be centrally visible.

### 14.6 State Forking

A job must not keep private state that disagrees with the canonical job store.

## 15. Import and Migration Jobs

Spark Intelligence should support import jobs for OpenClaw and Hermes migration.

These should be handled by the same harness, not by ad hoc scripts with separate behavior.

### 15.1 Safe Import Scope

Safe import targets for v1:

- adapter configuration that maps cleanly
- allowlists and pairing metadata
- basic session or identity mapping when structurally compatible

### 15.2 Unsafe Import Scope

Unsafe import targets for v1:

- opaque memory semantics
- undocumented internal state blobs
- anything that cannot be validated before activation

### 15.3 Import Guarantees

Import jobs should be:

- dry-runnable
- diffable
- reversible where possible
- auditable

## 16. Smoke Tests

The harness must have a focused smoke suite.

### 16.1 Required Smoke Tests

- install smoke
- setup smoke
- doctor smoke
- gateway startup smoke
- scheduler startup smoke
- due job execution smoke
- retry scheduling smoke
- stale lock recovery smoke
- adapter handshake smoke
- send and receive smoke
- persistent session smoke
- import job smoke

### 16.2 Smoke Philosophy

These tests should be:

- short
- deterministic
- runnable locally
- high-signal

Do not create a huge flaky suite and call it reliability.

## 17. Failure Modes We Intend To Prevent

This spec should prevent:

- cron jobs that never fire
- jobs firing twice unexpectedly
- reconnect loops that flap forever
- background work that silently dies
- cross-thread or cross-channel context leakage
- zombie worker accumulation
- state split across multiple stores
- imports that activate bad state without validation

## 18. Recommended v1 Implementation Shape

### 18.1 Modules

Recommended future module shape:

```text
spark_intelligence/
|- jobs/
|  |- scheduler.py
|  |- store.py
|  |- runner.py
|  |- locks.py
|  |- retry.py
|  |- health.py
|  |- import_jobs.py
|  `- smoke.py
```

### 18.2 Execution Rule

One job runner.
One scheduler.
One store.
One retry policy framework.

## 19. Final Decision

Spark Intelligence v1 should have a small, central, SQLite-backed job harness with one scheduler and strong smoke coverage.

It should borrow:

- OpenClaw's seriousness about repair and diagnostics
- Hermes' cleaner shared runtime discipline

But it should remain more lightweight than both by default.
