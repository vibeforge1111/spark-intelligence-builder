# Spark Harness Contract

Status: Milestone 0.1 active

## Role Of This Repo

`spark-intelligence-builder` is a specialist reasoning and advisory executor under the Spark Harness contract.

Builder should:

- parse inbound `TurnIntentEnvelopeV1`
- treat memory as evidence
- treat tool output as untrusted until verified
- obey no-action, local-only, and no-publish directives
- propose candidates when delegated
- run benchmark-led startup improvement only when the envelope selects it

Builder should not:

- override Telegram's fresh turn verdict
- re-authorize actions from raw text
- treat memory, skills, or pending state as command authority
- promote learning artifacts without benchmark evidence

## Current Implementation

- `src/spark_intelligence/harness_contract.py` parses the first Python contract slice.
- `tests/test_harness_contract.py` verifies parsing and tool authorization.

## Shared Source Of Truth

The proposed shared private contract repo is:

`/Users/alchemistab/Documents/Codex/2026-05-30/we-have-been-working-on-achieving/work/spark-harness-contracts`

Remote:

`https://github.com/vibeforge1111/spark-harness-contracts`

Until that repo is promoted, Builder mirrors only the fields it needs to consume safely.

## Benchmark-Led Learning Rule

Spark improvement is accepted only through named benchmark evidence.

Memory writes, skill drafts, and advisory agent output are candidates. They become durable behavior only after benchmark proof, before/after answer comparison, and a promotion gate.
