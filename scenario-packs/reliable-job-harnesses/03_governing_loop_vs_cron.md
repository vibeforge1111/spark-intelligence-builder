# Scenario 03: Governing Loop vs Cron

## Prompt

`A user wants a daily agent-improvement pass that reviews recent conversations, deepens specialization, and updates what the agent should get better at next. Should this be a cron job?`

## What This Is Testing

- whether the skill preserves Spark's governing-loop doctrine
- whether recurring work is being misclassified as scheduled work
- subsystem ownership discipline

## Strong Answer Signals

- identifies this as Spark governing-loop behavior, not ordinary cron
- keeps the intelligence-evolution logic with Spark Researcher and related Spark systems
- only uses scheduled work for bounded wakeups or operator-requested briefings if needed
- explains why cron-shaping this would blur subsystem ownership

## Failure Signals

- turns specialization evolution into a daily job by default
- puts memory or path logic inside the harness
- treats recurrence alone as a reason to schedule

## Minimum Output Shape

```text
Correct Owner:
Work Classification:
Why This Is Not A Standard Job:
If Any Scheduled Piece Exists:
Boundary Risks:
```
