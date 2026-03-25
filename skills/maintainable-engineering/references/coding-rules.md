# Maintainable Engineering Coding Rules Reference

Use this reference when the task is not just a review, but a decision about how Spark Intelligence code should be structured.

## Core Rules

- one runtime path
- one governing loop for intelligence evolution
- one scheduler for true scheduled work
- one source of truth per concern
- thin adapters
- local-first inspectable state
- explicit state transitions
- doctor-first repair posture
- smoke tests before heavy test infrastructure

## Review Questions

Ask:

1. Did this add a second way to do the same thing?
2. Did this move logic into the wrong subsystem?
3. Did this make debugging easier on a local machine?
4. Did this create a hidden scheduler, retry loop, or state machine?
5. Did this create documentation burden that exceeds the feature value?

## Spark-Specific Checks

- If it belongs in Spark Researcher, do not rebuild it here.
- If it belongs in Spark Swarm, do not simulate swarm logic locally.
- If it belongs in the memory chip, do not smuggle memory doctrine into runtime modules.
- If it is part of the governing flywheel, do not reshape it into cron.

## Default Output

Return:

- boundary mistakes
- complexity debt
- maintainability risks
- doctorability gaps
- missing smoke coverage
