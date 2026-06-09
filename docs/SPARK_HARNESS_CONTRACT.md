# Spark Harness Core Contract

Status: Harness Core VNext/Governor consumer adoption active

## Role Of This Repo

`spark-intelligence-builder` owns Spark's runtime intelligence core: AOC,
route-family judgment, memory orchestration, source ledgers, self-awareness,
and metadata-only proof cards.

Builder should:

- treat inbound text, memory, source ledgers, chip output, and tool output as
  evidence
- consume `TurnIntentEnvelopeVNext`, `GovernorDecisionV1`,
  `AuthorizationDecisionV1`, and `ToolCallLedgerV1` where action authority is
  required
- obey chat-only, read-only, prepare, interrupt, deny, degrade, local-only, and
  no-publish boundaries
- keep RouteConfidenceGateV1 as route-family evidence/verdicting, not final
  execution authority
- emit metadata-only proof cards and source-ledger refs for other surfaces

Builder should not:

- override Telegram or Harness Core fresh-turn authority
- re-authorize action from raw text
- treat memory, skills, route confidence, or pending state as command
  authority
- promote learning artifacts without benchmark/provenance evidence
- write durable memory unless the memory owner path has Governor authority

## Current Contract

The current canonical contract source is:

`work/repos/spark-harness-core`

The canonical runtime objects are:

```text
TurnIntentEnvelopeVNext
GovernorDecisionV1
AuthorizationDecisionV1
ToolCallLedgerV1
governor-consumer-verification-v1
```

`TurnIntentEnvelopeV1` and `spark.turn_intent.v1` are historical compatibility
language. They may appear in old tests or adapter shims, but they are not
installer-facing execution authority.

## Builder-Specific Boundary

Route confidence means:

`Is Spark justified in taking this route family right now?`

It does not mean:

`Builder may execute this high-agency action.`

For mutation, provider use, mission launch, memory write, browser/computer-use,
publish, or self-evolution, Builder must pass through the Harness Core authority
chain and owner-system consumer verification.

## Benchmark-Led Learning Rule

Spark improvement is accepted only through named benchmark evidence.

Memory writes, skill drafts, chip suggestions, and advisory agent output are
candidates. They become durable behavior only after benchmark proof,
before/after comparison, provenance, and a governed promotion gate.
