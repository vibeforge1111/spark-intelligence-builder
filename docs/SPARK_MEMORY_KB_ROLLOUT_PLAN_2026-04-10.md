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
- Probe coverage after the `2026-04-10` state-db adapter fix: `has_probe_coverage = true`
- Current-state probes on the live Builder replay: `10/10`
- Evidence probes on the live Builder replay: `10/10`

## Diagnosis

The main issue is coverage and calibration, not a broken substrate.

What still needs work:
- We have fixed Builder-state retrieval probe export for the profile fact, explanation, and identity paths.
- We have strong fact/query/explanation behavior for profile memory, but not yet a broad regression matrix across identity summary, evidence retrieval, contradiction handling, and non-profile memory lanes.
- The KB compile path is now available in Builder, but the broader benchmark loop is still manual.

## Phase Plan

### Phase 1: Builder KB Entry Points
Status: done

- Add Builder-native Telegram KB compile command.
- Verify against a real Telegram-backed Builder home.
- Confirm generated vault contains correct current-state and evidence pages.

### Phase 2: Retrieval Probe Coverage
Status: done

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
Status: in progress

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

Current implementation target:
- `spark-intelligence memory run-telegram-regression`
- default matrix should cover:
  - name write + query
  - occupation write + query
  - city write + explanation
  - startup write + explanation
  - founder write + query
  - mission write + explanation
  - timezone write + query
  - country write + query
  - identity summary
  - stable abstention on an unwritten fact lane
  - overwrite/update case for city
- summary should score:
  - expected bridge mode
  - expected routing decision
  - expected response substrings
  - KB probe coverage totals

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

1. Land the Builder Telegram regression runner and commit it.
2. Run the full matrix against a real Builder home and save the bundle.
3. Expand from profile-only routes into abstention, contradiction, and non-profile memory lanes.
4. Start the KB enrichment pass for repo-source and filed-output pages.
