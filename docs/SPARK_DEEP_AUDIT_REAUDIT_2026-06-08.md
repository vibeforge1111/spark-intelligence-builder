# Spark Deep Audit Reaudit - 2026-06-08

This document is the next hardening plan after the 2026-06-07 deep audit and
the first remediation pass. It is intentionally operational: what is now fixed,
what is only partially mitigated, what still needs hardening, and what to check
before claiming the authority runtime is real.

## Goal

Continue the active Spark deep-audit remediation goal by turning the remaining
authority, observability, source-of-truth, and runtime-hardening gaps into a
verifiable next-pass checklist. The current thread already has an active
paused `/goal` for the full deep-audit remediation, so this document is the
phase goal under that umbrella rather than a second standalone goal.

## Scope And Boundary

Evidence checked on 2026-06-08:

- `spark-harness-core` installed source at
  `C:\Users\USER\.spark\modules\spark-harness-core\source`
- `spark-intelligence-builder` installed source at
  `C:\Users\USER\.spark\modules\spark-intelligence-builder\source`
- `spark-cli` working tree at `C:\tmp\s20bu\tools\spark-cli`
- live Builder state at `C:\Users\USER\.spark\state\spark-intelligence`
- Spawner and Telegram source trees in read-only audit mode only, because their
  worktrees are dirty from a parallel test/fix session

Do not use this document as proof that the Spawner or Telegram in-flight diffs
are ready. Recheck those repos after their owning session commits or reverts.

## Executive Verdict

The first remediation pass closed the cheap and structural gaps in the core
ledger spine, but Spark is not done.

- Fixed: Harness Core now has the missing
  `governor-consumer-verification-v1` schema, validated verifier output,
  `bound_ledger_row` / `boundLedgerRow`, signed Governor decision contracts,
  and docs.
- Fixed: Builder now owns a canonical `state.db:tool_call_ledger` table with
  query/import/ingest surfaces and doctor adoption visibility.
- Fixed: Spark CLI approval hardening now recurses into inline `-c` payloads,
  fails closed for unclassified sensitive command shapes, keeps
  `approval classify` report-only, lazy-loads keyring, and writes/imports
  governed ledgers.
- Fixed: installed Builder manifest provenance is back to AGPL-3.0-only and
  declares `spark-harness-core`.
- Partially fixed: self-evolution now has Builder evidence plumbing and a
  guarded change-manifest test runner, but no automatic code mutation,
  rollback executor, or production promotion loop.
- Partially fixed: observability has a canonical governed tool ledger, but the
  live ledger currently contains only `spark_cli` and `telegram` rows. Builder
  and Spawner execution are not yet fully represented as first-class surfaces.
- Still open: `state.db` is still large, about 655 MB, and needs a controlled
  retention plus VACUUM pass with backup and before/after counts.
- Still open: source-of-truth is not globally solved. The installed Builder is
  fixed, but `C:\Users\USER\Desktop\spark-intelligence-builder` remains dirty
  and divergent.
- Still open or in-flight: Spawner loopback API-key hardening and Governor
  signature verification must be rechecked after the parallel Spawner/Telegram
  session settles.
- Still open: the authority kernel is still mostly a library plus adapters, not
  a long-lived universal governor runtime.

## Evidence Snapshot

| Area | Current evidence | Reaudit status |
| --- | --- | --- |
| Harness missing verifier schema | `schemas/governor-consumer-verification-v1.schema.json`; `kernel.py` validates verifier records | Closed |
| Harness ledger row contract | `bound_ledger_row` in Python and `boundLedgerRow` in TS exports | Closed |
| Harness signed decisions | signed Governor decision contract and verification tests exist | Closed in core |
| CLI approval holes | tests cover `bash -c`, `sh -c`, `python -c`, PowerShell wrappers, fail-closed unknowns | Closed in CLI |
| CLI keyring cold import | `load_keyring()` lazy import and regression test | Closed in CLI |
| Builder canonical ledger | `tool_call_ledger` table, indexes, import/query/ingest commands | Closed for store |
| Live ledger adoption | live doctor reports `total=82 surfaces=spark_cli=69, telegram=13` | Partial |
| Live DB size | `state.db` is 654,868,480 bytes; `builder_events=61022`, `event_log=61022` | Open |
| Orphaned root rivers | root `.spark` now has only `outcomes.jsonl` and `predictions.jsonl` from March 2026 | Mostly closed, verify archive policy |
| Builder source truth | installed `spark.toml`, `pyproject.toml`, and `LICENSE` are AGPL-3.0-only; Desktop tree remains dirty | Partial |
| Self-evolution | Builder has observe snapshot and change-manifest runner; no automatic mutation executor | Partial |
| Spawner loopback | current dirty worktree still contains many `allowLoopbackWithoutKey: true` routes | In-flight / open |
| Telegram signature minting | `SPARK_GOVERNOR_HMAC_KEY` signer and nonce test exist | Present, recheck after dirty worktree settles |

## Completed Since Original Audit

### Harness Core

- Added `governor-consumer-verification-v1` schema.
- Validated `verify_governor_execution_authority()` output.
- Added standalone validation for previously transitive-only fragments.
- Added signed Governor decision contract with nonce and HMAC fields.
- Added `bound_ledger_row()` / `boundLedgerRow()` as the shared canonical row
  shape for governed tool-call persistence.
- Updated docs and quick checks.

### Builder

- Added canonical `tool_call_ledger` table and indexes in `state.db`.
- Added persistence/query helpers for bound ledger rows.
- Added `gateway ingest-tool-ledger` and stdio ingestion.
- Added `harness tool-ledgers` query surface.
- Added `harness import-cli-ledgers` for Spark CLI approval ledger files.
- Added retention/prune plumbing for canonical ledgers and gateway logs.
- Added live doctor visibility for ledger adoption by surface.
- Restored installed Builder AGPL-3.0-only provenance and declared
  `spark-harness-core` in `needs.modules`.
- Added docs for authority contracts, runtime operations, and source-truth
  expectations.
- Added Builder self-evolution observation and change-manifest runner surfaces
  that consume canonical ledger evidence and can run guarded test commands.

### Spark CLI

- Made keyring lazy-loaded.
- Recurred approval classification into `-c` and command-string wrappers.
- Kept `spark approval classify` report-only.
- Failed closed for unclassified sensitive command shapes.
- Removed residual spike branding.
- Documented approval authority and ledger import path.
- Verified focused CLI suite: `579 passed, 2 skipped, 155 subtests`.

### Telegram And Spawner

Previous committed work introduced signed Telegram Governor decisions and
Spawner-side Harness Core dependencies, but both repos are currently dirty from
a separate session. Treat current Telegram/Spawner readiness as provisional
until that session completes and the final diff is tested.

## Remaining Hardening Plan

### P0 - Finish Security And Authority Closure

1. Recheck and finish Spawner loopback hardening.
   - Audit every `requireControlAuth(... allowLoopbackWithoutKey: true)` route.
   - Keep `true` only for explicitly read-only, non-mutating local surfaces with
     documented reason codes.
   - Mutation/control routes must require API key or valid hosted session even
     from loopback.
   - Add a route matrix test that fails if a new mutating route omits
     `allowLoopbackWithoutKey: false`.

2. Recheck Spawner Governor signature verification.
   - Spawner must reject unsigned Governor decisions when
     `SPARK_GOVERNOR_HMAC_KEY` is configured.
   - Spawner must reject bad signatures, mismatched key ids, stale timestamps if
     timestamps are adopted, and replayed nonces inside the configured window.
   - Telegram should continue to sign with `SPARK_GOVERNOR_HMAC_KEY` and a
     per-decision nonce.

3. Make the canonical ledger a required consumer contract for governed
   execution.
   - Every governed tool-call path should produce one canonical
     `tool_call_ledger` row with `turn_id`, `action_id`, `capability_id`, and
     `authorization_decision_id`.
   - Current live adoption lacks first-class `builder` and `spawner` surfaces.
   - Add doctor warnings for expected surfaces with zero rows after configured
     runtime activity.

4. Preserve fail-closed behavior at import/runtime boundaries.
   - Builder already declares `spark-harness-core`; keep the boot-time import
     check loud.
   - Remove silent stub fallbacks from production paths once packaging is stable
     enough to make missing Harness Core a startup failure.

### P1 - Observability And State Hygiene

5. Run a controlled state retention and VACUUM pass.
   - Back up `state.db`.
   - Run `jobs prune-observability` with explicit cutoff and
     `--include-gateway-logs`.
   - Run SQLite `VACUUM` only after before/after row counts are captured.
   - Verify doctor stays green and ledger rows newer than the cutoff remain.
   - Record final DB size. Current DB size is about 655 MB.

6. Backfill or map the cross-surface join key.
   - `turn_id` is now canonical for tool ledgers, but SIB still has many older
     event rows keyed by `request_id`, `trace_ref`, and `correlation_id`.
   - Add explicit `turn_id` mapping to new bridge/gateway events where the
     Governor decision is present.
   - Add a trace query that can answer: "show everything for this turn id."

7. Finish canonical ingestion for Telegram JSONL and Spawner execution.
   - Telegram still has local JSONL fallback behavior; canonical ingestion must
     be proven in normal runtime, not just imported later.
   - Spawner must emit canonical ledger rows for mission execution steps or POST
     them to Builder's gateway.

8. Keep orphaned rivers quarantined.
   - Root `.spark` now only shows `outcomes.jsonl` and `predictions.jsonl`.
   - Decide whether these two March 2026 files are evidence to archive or dead
     residue to quarantine.
   - Add a scheduled safe report that lists large unowned JSONL files without
     deleting them.

### P1 - Self-Evolution Reality

9. Keep self-evolution in supervised mode until mutation is real.
   - Current Builder runner can observe ledgers and evaluate manifests with
     guarded tests.
   - It must not claim autonomous self-improvement until it has a sandboxed
     apply step, rollback step, manifest provenance, and maintainer approval for
     protected components.

10. Add a no-op mutation drill.
    - Create a harmless change manifest that changes no production code.
    - Run required tests through the Builder change-manifest runner.
    - Verify promotion can reach `promote_private` only with explicit flags and
      cannot touch protected components without approval evidence.

11. Define the mutation executor boundary before writing one.
    - Executor input: accepted manifest, exact patch/artifact refs, required
      tests, rollback path, protected-component approvals.
    - Executor output: applied/reverted status, test results, final ledger row,
      and rollback proof.
    - No live runtime mutation without a dry-run and rollback proof.

### P1 - Source Of Truth

12. Resolve the Desktop Builder divergence.
    - The installed Builder is the live runtime and is now AGPL-aligned.
    - The Desktop Builder tree remains dirty and divergent.
    - Choose one canonical source tree, archive or relabel the stale one, and
      update operator memory/docs so future audits do not read the wrong tree.

13. Recheck release mirrors and vendored Harness Core copies.
    - Ensure release mirrors carry correct AGPL/MIT boundaries.
    - Document whether vendored Harness Core copies are temporary snapshots or
      should be removed in favor of the package dependency.

### P2 - Performance And Maintainability

14. Measure cold starts again.
    - CLI keyring import is fixed, but measure current CLI cold import.
    - Telegram still needs a warm Builder bridge process if fresh Python startup
      remains on the hot path.

15. Split only the riskiest monolith seams.
    - Avoid broad refactors.
    - Best candidates: Spark CLI approval/security module boundaries are already
      partially extracted; continue there only when tests pin behavior.

16. Improve operator help density.
    - CLI and Telegram command surfaces remain large.
    - Add role-based help grouping without changing command behavior.

## Stop-Ship Gates For Next Pass

- No mutating Spawner control route may rely on loopback-without-key by default.
- No Governor decision may be accepted across process boundaries without a
  signature when a shared key is configured.
- No governed execution path may claim full authority unless it writes or
  ingests a canonical `tool_call_ledger` row.
- No source-truth claim may cite the Desktop Builder tree while live runtime is
  installed Builder, unless the document explicitly says it is inspecting the
  stale tree.
- No self-evolution claim may say "improves itself" unless a manifest was
  applied, tested, and either promoted or rolled back with evidence.
- No DB cleanup may run without a backup, before/after counts, and doctor green
  afterward.

## Concrete Check Commands

Run these after the Spawner/Telegram session settles:

```powershell
git -C C:\Users\USER\.spark\modules\spawner-ui\source status --short
git -C C:\Users\USER\.spark\modules\spark-telegram-bot\source status --short
git -C C:\Users\USER\.spark\modules\spark-intelligence-builder\source status --short
git -C C:\Users\USER\.spark\modules\spark-harness-core\source status --short
git -C C:\tmp\s20bu\tools\spark-cli status --short
```

```powershell
python -m spark_intelligence.cli doctor --home C:\Users\USER\.spark\state\spark-intelligence --json
python -m spark_intelligence.cli harness tool-ledgers --home C:\Users\USER\.spark\state\spark-intelligence --limit 20 --json
```

```powershell
git -C C:\Users\USER\.spark\modules\spawner-ui\source grep -n "allowLoopbackWithoutKey: true" -- src
git -C C:\Users\USER\.spark\modules\spawner-ui\source grep -n "SPARK_GOVERNOR_HMAC_KEY\|signature\|nonce" -- src tests spark.toml
git -C C:\Users\USER\.spark\modules\spark-telegram-bot\source grep -n "SPARK_GOVERNOR_HMAC_KEY\|signature\|nonce" -- src tests spark.toml
```

```powershell
python -m pytest tests/test_kernel_contracts.py tests/test_typescript_contracts.py -q
npm run build
```

```powershell
python -c "import sys; sys.path.insert(0, 'src'); import pytest; raise SystemExit(pytest.main(['tests/test_cli.py', '-q']))"
```

Use the Spark CLI test command from `C:\tmp\s20bu\tools\spark-cli`; the explicit
`src` insertion avoids accidentally importing the older installed CLI package.

## Suggested Next Execution Order

1. Wait for the parallel Spawner/Telegram session to finish, then re-run the
   dirty-repo and route-auth checks above.
2. Close Spawner loopback and signature verification gaps, with route matrix
   tests.
3. Add canonical ledger emission for Spawner and verify live adoption includes
   `spawner`.
4. Add Builder self-persist coverage for the busiest governed Builder tool paths
   and verify live adoption includes `builder`.
5. Run controlled `state.db` prune plus VACUUM with backup and before/after
   counts.
6. Resolve the Desktop Builder source-of-truth decision and update operator
   docs/memory to stop future stale-tree audits.
7. Run the self-evolution no-op mutation drill and document the claim boundary.

## Current Claim Boundary

Spark now has a usable authority and observability spine for governed records,
but not a complete authority runtime. The next honest milestone is:

> Every high-agency surface signs or verifies Governor decisions, writes a
> canonical bound ledger row keyed on `turn_id`, and can be traced in Builder
> from request to authorization to execution result.

Do not claim autonomous self-evolution or complete cross-surface traceability
until the remaining P0/P1 gates above pass.
