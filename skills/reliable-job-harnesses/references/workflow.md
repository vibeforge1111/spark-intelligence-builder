# Reliable Job Harnesses Reference

## Use This Skill For

- cron architecture
- scheduler design
- retry policy
- background maintenance jobs
- health check jobs
- migration jobs
- harness smoke tests

## Trigger Examples

- "Design the scheduler for Telegram reconnect and health checks"
- "Review this retry loop for duplicate execution risk"
- "How should import jobs run safely and be observable?"
- "Define smoke tests for our cron harness"

## Detailed Workflow

### 1. Clarify The Job

Identify:

- what the job does
- who owns it
- why it exists
- what triggers it
- whether it must run on a schedule or could be inline/event-driven

If it does not need scheduling, do not turn it into a job.

### 2. Force It Through The Central Harness

Check:

- does it reuse the central scheduler?
- does it reuse the canonical job record?
- does it reuse the common retry policy framework?
- does it expose status through the same observability surface?

If not, explain why that is a design problem.

### 3. Define Safety

For every job, define:

- idempotence rule
- retryable failures
- non-retryable failures
- side effects
- locking needs
- stale lock recovery rule

### 4. Define Observability

Minimum:

- current status
- next run
- last success
- last failure
- last error
- attempt count

### 5. Define Smoke Tests

At minimum, consider:

- startup
- one successful run
- one safe retry
- one non-retryable failure
- one stale lock recovery path

## Anti-Patterns

- a job implemented as a hidden background thread
- adapter-specific scheduling logic
- manual retries that bypass the harness
- duplicate scheduler loops
- many timers instead of one due-job loop
- jobs that mutate state without leaving records

## Output Template

```text
Job Owner:
Why This Should Exist:
Trigger Type:
Canonical Payload:
Idempotence Rule:
Retry Policy:
Locking Rule:
Failure Visibility:
Smoke Tests:
Why This Does Not Create A Competing Subsystem:
```

## Escalate If

- the job requires a second scheduler
- the job cannot be made idempotent enough to retry safely
- the design hides state outside the canonical job store
- the job depends on foreign runtime behavior we do not control
