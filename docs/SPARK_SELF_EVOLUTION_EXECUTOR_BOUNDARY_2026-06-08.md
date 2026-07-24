# Spark Self-Evolution Executor Boundary - 2026-06-08

Status: design boundary only. No mutation executor exists in Builder yet.

This document defines the minimum contract that must be satisfied before Spark
can safely add a self-evolution patch executor. It keeps the current supervised
change-manifest runner honest: the runner can evaluate manifests and tests, but
it must not be described as autonomous self-improvement.

## Current Proven Baseline

Builder currently proves:

- observe-only self-evolution snapshots from canonical `tool_call_ledger`
  evidence;
- guarded `change-manifest-v1` evaluation through Harness Core;
- allowlisted test execution through the change-manifest runner;
- explicit `--allow-private-promotion` for private promotion;
- required `rollback_plan` in every valid manifest;
- required `human_approval_ref` for protected components such as
  `authority_policy`;
- canonical `surface=builder` tool ledger rows for the runner itself.

Builder does not yet prove:

- patch artifact resolution;
- dry-run patch application;
- runtime mutation;
- rollback execution;
- release-candidate or public promotion.

## Executor Non-Goals

A future executor must not:

- infer a patch from chat text;
- mutate the live installed runtime directly on first apply;
- run arbitrary shell commands from a manifest;
- treat successful private promotion as production release approval;
- import loose JSONL, memory, or historical residue as authority;
- bypass protected-component approval because the operator is local.

## Required Inputs

The executor input should be a single immutable execution packet containing:

- accepted `change-manifest-v1` records;
- exact patch or artifact refs with content digest, source path, and expected
  target path;
- required tests copied from the manifest;
- explicit working directory and clean-worktree requirement;
- rollback plan plus a concrete rollback artifact or command plan;
- protected-component `human_approval_ref` where the manifest target component
  type requires it;
- operator approval for the executor invocation itself;
- expected output ledger destination.

Missing or ambiguous inputs must produce `not_ready`, not a partial mutation.

## Required Phases

1. Preflight
   Validate schemas, authority records, approval refs, worktree cleanliness,
   patch digests, and test allowlist.

2. Dry-run apply
   Apply the patch only to a throwaway worktree or temporary copy. Record exact
   files changed and reject hidden generated state.

3. Verification
   Run allowlisted tests from the manifest. Refuse promotion if any required
   test is missing, blocked, timed out, or failed.

4. Rollback drill
   Revert the dry-run worktree using the rollback artifact or command plan, then
   verify the worktree returns clean.

5. Evidence write
   Write a canonical `tool_call_ledger` row with `turn_id`, `action_id`,
   `capability_id`, `authorization_decision_id`, executor status, patch digest,
   test result refs, and rollback proof ref.

6. Promotion decision
   Emit only the narrowest justified verdict. Private promotion may mean
   "safe to inspect or manually port"; it does not mean live runtime release.

## Stop Rules

The executor must fail closed when:

- the live installed worktree is dirty and the executor is not explicitly in a
  throwaway directory;
- a manifest is not `accepted`;
- `rollback_plan` is missing or not matched to a concrete rollback proof path;
- a protected component lacks `human_approval_ref`;
- a patch digest does not match the supplied artifact;
- required tests are absent, blocked, failed, or not allowlisted;
- the dry-run worktree is not clean after rollback;
- Harness Core is missing or schema validation fails;
- no canonical ledger row can be written.

## Minimum Tests Before Implementation Is Trusted

The first executor implementation needs focused tests for:

- malformed packet rejection;
- dirty live worktree refusal;
- patch digest mismatch rejection;
- blocked shell syntax in required tests;
- successful dry-run apply to a temporary worktree;
- failed test prevents promotion;
- rollback returns the temporary worktree clean;
- protected component requires `human_approval_ref`;
- canonical ledger row contains all four authority join ids;
- no live installed files change during dry-run mode.

## Claim Boundary

Until these phases and tests exist, Spark may claim only supervised
change-manifest evaluation. It must not claim autonomous self-evolution, safe
runtime mutation, or rollback-capable self-improvement.
