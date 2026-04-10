# Spark Memory KB Rollout Plan

Date: 2026-04-10
Repo: `spark-intelligence-builder`

## Current State

The memory architecture is working. We are not rebuilding from scratch.

What is already live:
- Direct profile-fact writes from Telegram into Domain Chip Memory current state.
- Direct profile-fact reads from Telegram memory queries.
- Direct provenance answers for memory explanation queries.
- Builder-native KB compilation from `state.db` through `spark-intelligence memory compile-telegram-kb`.
- Karpathy-style wiki vault generation with `wiki/current-state`, `wiki/evidence`, `wiki/outputs`, `wiki/sources`, and `wiki/syntheses`.

What the latest live probe showed:
- Accepted writes: `5`
- Rejected writes: `0`
- Skipped turns: `0`
- Current-state pages generated: name, occupation, city, startup, mission
- KB health: valid

## Diagnosis

The main issue is coverage and calibration, not a broken substrate.

What still needs work:
- We are not yet exporting enough retrieval probes, so the KB failure taxonomy still reports a `probe_coverage_gap`.
- We have strong fact/query/explanation behavior for profile memory, but not yet a broad regression matrix across identity summary, evidence retrieval, contradiction handling, and non-profile memory lanes.
- The KB compile path is now available in Builder, but the broader benchmark loop is still manual.

## Phase Plan

### Phase 1: Builder KB Entry Points
Status: done

- Add Builder-native Telegram KB compile command.
- Verify against a real Telegram-backed Builder home.
- Confirm generated vault contains correct current-state and evidence pages.

### Phase 2: Retrieval Probe Coverage
Status: next

- Export explicit shadow probes for:
  - fact lookup
  - explanation lookup
  - identity summary
  - evidence retrieval
- Re-run KB compile and require the failure taxonomy to move past `probe_coverage_gap`.

Acceptance:
- KB output includes filed query pages for read and explanation behavior.
- Failure taxonomy reflects real retrieval quality instead of write-only coverage.

### Phase 3: Automated Telegram Memory Regression Runner
Status: next

- Add a Builder command that runs a fixed Telegram memory matrix against a probe home.
- Cover:
  - profile writes
  - fact queries
  - explanation queries
  - identity summary
  - contradiction / overwrite cases
  - no-memory / abstention cases
- Emit one artifact bundle:
  - probe transcript
  - memory inspect output
  - compiled KB vault
  - summary JSON

Acceptance:
- One command can reproduce the end-to-end memory benchmark loop from Builder.

### Phase 4: KB Enrichment
Status: planned

- Promote successful retrieval and explanation outputs into richer filed outputs.
- Add more source pages from Builder runtime artifacts and selected repo manifests.
- Extend synthesis pages beyond the default runtime overview and timeline.

Acceptance:
- The wiki is useful as an operator-readable memory notebook, not only a contract artifact.

### Phase 5: Promotion and Memory Quality Gates
Status: planned

- Tighten keepability and contradiction handling.
- Separate durable personal facts from conversational residue.
- Add stop-ship checks for noisy memory promotions.

Acceptance:
- Memory writes remain durable for stable facts without polluting long-lived knowledge with operational residue.

## Immediate Next Steps

1. Add retrieval probe export for fact, explanation, and identity flows.
2. Add one Builder command to run the Telegram memory regression matrix and compile the KB vault.
3. Re-run the live probe and require non-empty probe coverage in the failure taxonomy.
