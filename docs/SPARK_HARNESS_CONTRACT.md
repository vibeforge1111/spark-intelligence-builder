# Spark Harness Contract

Status: active runtime contract, updated 2026-06-08

## Role Of This Repo

`spark-intelligence-builder` is a specialist runtime and advisory executor under the Spark Harness contract. It consumes Harness Core authority records and persists governed execution evidence; it does not define global authority by itself.

Builder should:

- consume `TurnIntentEnvelopeVNext`, `AuthorizationDecisionV1`, and `GovernorDecisionV1` records from `spark-harness-core`;
- treat memory, retrieved context, pending state, and tool output as evidence, not authority;
- obey no-action, local-only, no-publish, and mutation-boundary directives;
- verify governed tool decisions before execution;
- persist bound `tool_call_ledger` rows into `state.db`;
- expose operator-readable ledger queries by `turn_id` and surface;
- run self-evolution observation from canonical ledger evidence before any promotion path.

Builder should not:

- override Telegram's fresh turn verdict;
- re-authorize actions from raw text;
- treat memory, skills, or pending state as command authority;
- promote learning artifacts without benchmark and ledger evidence;
- accept unbound tool-ledger rows missing the authority join fields.

## Current Implementation

- `src/spark_intelligence/harness_contract.py` imports Harness Core and builds/validates Builder-facing envelopes.
- `src/spark_intelligence/bridge_authority.py` mints governed Builder tool-call ledgers and persists them through `persist_bound_ledger`.
- `src/spark_intelligence/observability/store.py` owns the canonical `tool_call_ledger` table, retention pruning, and reader APIs.
- `src/spark_intelligence/gateway/tool_ledger.py` provides the minimal adapter ingest contract for external surfaces that need to publish governed rows.
- `src/spark_intelligence/cli_approval_ledgers.py` imports Spark CLI approval ledgers into the same canonical table.
- `src/spark_intelligence/harness_runtime/service.py` gates `builder.direct`, `browser.navigate`, `researcher.advisory`, `voice.status`, `voice.speak`, and `swarm.sync.dry_run` before execution and records canonical result ledgers; other runners need explicit ledger evidence before they are claimed covered.
- `src/spark_intelligence/harness_evolution.py` builds observe-only self-evolution snapshots, change-manifest runner evidence, and Builder-surface runner ledgers from canonical ledgers.

Operator commands:

```powershell
spark-intelligence gateway ingest-tool-ledger <ledger-row.json>
spark-intelligence gateway serve-stdio
spark-intelligence harness tool-ledgers --turn-id <turn-id> --json
spark-intelligence harness trace-turn --turn-id <turn-id> --json
spark-intelligence harness import-cli-ledgers --ledger-dir $env:USERPROFILE\.spark\state\approval-ledgers --json
spark-intelligence harness self-evolution-snapshot --json
spark-intelligence harness change-manifest-runner --manifest <change-manifest-v1.json> --run-tests --json
```

The 2026-06-08 supervised no-op drill is recorded in
`docs/SPARK_SELF_EVOLUTION_NOOP_DRILL_2026-06-08.md`. It proves private,
explicitly flagged no-op promotion through the guarded runner. Follow-up
regression tests prove that `rollback_plan` is required and that protected
components such as `authority_policy` require `human_approval_ref` evidence
before a protected manifest can promote. It still does not prove autonomous
mutation, dry-run patch application, rollback execution, or release-candidate
promotion. The runner itself is persisted as a canonical `surface=builder` tool
ledger.

## Ledger Row Contract

The canonical row must carry:

- `ledger_id`
- `turn_id`
- `action_id`
- `capability_id`
- `authorization_decision_id`
- `surface`
- `ledger_json`

`ledger_json` should be the validated `tool-call-ledger-v1` payload. The flat columns are the query and join surface; the embedded ledger is the provenance payload.

## Shared Source Of Truth

Spark-wide TurnIntent rules are documented locally in:

- `docs/TURNINTENT_HARNESS_RULESET.md`
- `docs/TURNINTENT_AGENTS_ADOPTION.md`

Harness Core is the schema/runtime source for the authority records. Builder must declare `spark-harness-core` in `spark.toml` and import it from the installed module path rather than vendoring local schema copies.

## Benchmark-Led Learning Rule

Spark improvement is accepted only through named benchmark and ledger evidence.

Memory writes, skill drafts, tool ledgers, and advisory output are candidates. They become durable behavior only after benchmark proof, before/after answer comparison, and a promotion gate.
