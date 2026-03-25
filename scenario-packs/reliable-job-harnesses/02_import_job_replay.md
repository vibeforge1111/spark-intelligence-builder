# Scenario 02: Import Job Replay

## Prompt

`Design the migration replay job for importing safe Telegram pairing state from Hermes into Spark Intelligence without importing opaque runtime state.`

## What This Is Testing

- import job ownership
- dry-run and validation discipline
- non-retryable failure handling
- replay safety

## Strong Answer Signals

- classifies this as a harness-owned import job
- keeps dry-run, validate, and activate as explicit stages
- rejects foreign opaque state
- marks schema mismatches as non-retryable until operator action
- uses the canonical job store

## Failure Signals

- imports foreign memory semantics
- retries validation failures forever
- bypasses the shared harness with ad hoc scripts
- activates migrated state before validation

## Minimum Output Shape

```text
Job Owner:
Work Classification:
Canonical Payload:
Validation Gate:
Retry Policy:
Failure Visibility:
Smoke Tests:
```
