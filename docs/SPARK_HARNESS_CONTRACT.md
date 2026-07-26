# Spark Harness Core Contract

Status: Harness Core VNext/Governor consumer adoption active

## Role Of This Repo

`spark-intelligence-builder` owns Spark's runtime intelligence core: AOC,
route-family judgment, memory orchestration, source ledgers, self-awareness,
and metadata-only proof cards.

Builder should:

- treat inbound text, memory, source ledgers, chip output, and tool output as evidence
- consume `TurnIntentEnvelopeVNext`, `GovernorDecisionV1`,
  `AuthorizationDecisionV1`, and `ToolCallLedgerV1` for action authority
- verify the owner consumer boundary before mutation, provider use, mission
  launch, memory write, browser/computer-use, publish, or self-evolution
- obey chat-only, read-only, prepare, interrupt, deny, degrade, local-only, and
  no-publish boundaries
- keep RouteConfidenceGateV1 as route-family evidence and verdicting, not final
  execution authority
- emit allowlisted proof metadata and source-ledger references for other surfaces

Builder should not:

- override Telegram or Harness Core fresh-turn authority
- re-authorize an action from raw text
- treat memory, skills, route confidence, or pending state as command authority
- promote learning artifacts without benchmark and provenance evidence
- write durable memory without the memory owner's verified authority path

## Current Contract

The current authority chain is:

```text
fresh adapter input
  -> schema-valid TurnIntentEnvelopeVNext
  -> AuthorizationDecisionV1
  -> pre-execution ToolCallLedgerV1
  -> GovernorDecisionV1 where required
  -> owner consumer verification
  -> execution or refusal
  -> finalized result evidence
```

`spark.turn_intent.v1` remains supported as adapter compatibility input. Harness
Core can parse it and derive the governed VNext authorization artifacts, but the
legacy envelope is not sufficient execution authority by itself.

The executable integration lives in:

- `src/spark_intelligence/harness_contract.py`
- `src/spark_intelligence/bridge_authority.py`
- the `spark_harness_core` installed distribution or a registered clean module source

Do not hard-code a developer checkout as Harness truth. Runtime truth comes from
the installed or registered source that the import resolver and Builder doctor
can verify.

## Builder-Specific Boundary

Route confidence asks:

> Is Spark justified in taking this route family right now?

It does not answer:

> May Builder execute this high-agency action?

The owner consumer must verify the matching tool, owner, mutation class,
freshness, authorization decision, ledger, and Governor boundary. Missing or
mismatched proof fails closed.

## Shared Source Of Truth

The local adoption and release rules are:

- `docs/TURNINTENT_HARNESS_RULESET.md`
- `docs/TURNINTENT_AGENTS_ADOPTION.md`
- `AGENTS.md`

These documents explain the boundary; the installed Harness schemas and
executable consumer verification remain machine authority.

## Benchmark-Led Learning Rule

Memory writes, skill drafts, chip suggestions, and advisory output are
candidates. They become durable behavior only after benchmark proof,
before/after comparison, provenance, owner authorization, and a governed
promotion gate.
