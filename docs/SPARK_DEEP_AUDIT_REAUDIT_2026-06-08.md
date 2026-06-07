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
- `spark-cli` installed tool tree at `C:\Users\USER\.spark\tools\spark-cli`
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
- Partially fixed: self-evolution now has Builder evidence plumbing, a guarded
  change-manifest test runner, and a supervised no-op private-promotion drill,
  plus regression proof for required rollback plans and protected-component
  approval evidence. It still has no automatic code mutation, rollback
  executor, or production promotion loop.
- Partially fixed: observability has a canonical governed tool ledger and live
  rows for `builder`, `spark_cli`, and `telegram`. Spawner execution is still
  not represented as a first-class surface, and additional Builder high-agency
  paths should adopt the same ledger seam as they execute.
- Closed for this pass: `state.db` retention and VACUUM were run with backup,
  before/after counts, and doctor verification. The DB shrank from about 655 MB
  to about 225 MB and is now about 228 MB after later live activity while
  preserving canonical tool ledgers.
- Closed for the live Builder runtime: installed Builder is the canonical
  source-truth line and now has `docs/SOURCE_TRUTH.md`. Desktop Builder remains
  dirty backlog/historical evidence until curated.
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
| Live ledger adoption | live doctor reports `total=87 surfaces=builder=1, spark_cli=69, telegram=17` | Partial; Spawner missing |
| Live DB size | retention run recorded `state.db` 654,905,344 -> 224,800,768 bytes; latest report shows 228,089,856 bytes with `builder_events=15,525`, `event_log=15,525`, and `tool_call_ledger=87` | Closed for this pass |
| Loose JSONL residue | `jobs observability-report --include-unowned-jsonl --jsonl-min-bytes 1000000` reports 251 JSONL files / 190,025,951 bytes under `.spark`, with root `outcomes.jsonl` plus larger legacy rivers under `recursion`, `logs`, `queue`, and `advisor`; policy doc `SPARK_JSONL_RESIDUE_POLICY_2026-06-08.md` is written | Policy written; archive/quarantine execution pending |
| Builder source truth | installed `docs/SOURCE_TRUTH.md` declares the live runtime line; installed `spark.toml`, `pyproject.toml`, and `LICENSE` are AGPL-3.0-only; Desktop tree remains dirty backlog | Closed for live Builder; archive/relabel pending |
| Self-evolution | Builder has observe snapshot, change-manifest runner, supervised no-op drill `evt-458006fab354`, Builder ledger `ledger:fd24aee5b4fb46e08bc36925`, and commit `7bf79b5` proving rollback-plan/protected-approval boundaries; no automatic mutation executor | Partial |
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
- Added read-only loose JSONL residue reporting to `jobs observability-report`
  so large legacy rivers can be classified before any archive/quarantine pass.
- Added `SPARK_JSONL_RESIDUE_POLICY_2026-06-08.md` so loose JSONL handling is
  report-first, archive-before-quarantine, and never deletion-by-size.
- Restored installed Builder AGPL-3.0-only provenance and declared
  `spark-harness-core` in `needs.modules`.
- Added installed `docs/SOURCE_TRUTH.md` so future audits treat
  `C:\Users\USER\.spark\modules\spark-intelligence-builder\source` as the live
  Builder runtime line and treat the Desktop checkout as backlog until curated.
- Added docs for authority contracts, runtime operations, and source-truth
  expectations.
- Added Builder self-evolution observation and change-manifest runner surfaces
  that consume canonical ledger evidence and can run guarded test commands.
- Made the Builder change-manifest runner persist its own canonical
  `surface=builder` tool ledger before recording the final runner result.
- Ran the supervised no-op change-manifest drill documented in
  `SPARK_SELF_EVOLUTION_NOOP_DRILL_2026-06-08.md`.
- Added self-evolution boundary regression tests for missing rollback plans,
  missing protected-component approval evidence, and protected promotion with
  explicit `human_approval_ref`.
- Ran the controlled state retention/VACUUM pass documented in
  `SPARK_STATE_DB_RETENTION_RUN_2026-06-08.md`.

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
   - Current live adoption has first-class `builder`, `spark_cli`, and
     `telegram` rows.
   - Spawner execution remains the missing first-class surface.
   - Broaden Builder coverage beyond the change-manifest runner as new
     high-agency Builder tool paths execute.
   - Add doctor warnings for expected surfaces with zero rows after configured
     runtime activity.

4. Preserve fail-closed behavior at import/runtime boundaries.
   - Builder already declares `spark-harness-core`; keep the boot-time import
     check loud.
   - Remove silent stub fallbacks from production paths once packaging is stable
     enough to make missing Harness Core a startup failure.

### P1 - Observability And State Hygiene

5. Keep the controlled state retention procedure repeatable.
   - Completed on 2026-06-08 with backup, explicit cutoff, before/after
     counts, `--include-gateway-logs`, `VACUUM`, and doctor verification.
   - Re-run only with a fresh backup and a new before/after evidence record.
   - Added `jobs observability-report` so operators can capture `state.db`
     bytes, table counts, prunable row counts, and optional gateway JSONL sizes
     before future pruning without deleting anything.

6. Backfill or map the cross-surface join key.
   - `turn_id` is now canonical for tool ledgers, but SIB still has many older
     event rows keyed by `request_id`, `trace_ref`, and `correlation_id`.
   - Add explicit `turn_id` mapping to new bridge/gateway events where the
     Governor decision is present.
   - Added `harness trace-turn --turn-id <turn-id>` for canonical ledgers plus
     Builder/event mirror rows that already carry the turn id.
   - Remaining work: broaden new event producers so more rows carry `turn_id`
     directly instead of relying on legacy request/trace fields.

7. Finish canonical ingestion for Telegram JSONL and Spawner execution.
   - Telegram still has local JSONL fallback behavior; canonical ingestion must
     be proven in normal runtime, not just imported later.
   - Spawner must emit canonical ledger rows for mission execution steps or POST
     them to Builder's gateway.

8. Keep orphaned rivers quarantined.
   - Root `.spark` now only shows `outcomes.jsonl` and `predictions.jsonl`,
     but recursive `.spark` JSONL residue is about 190 MB across 251 files.
   - Added `jobs observability-report --include-unowned-jsonl` to list loose
     `.jsonl` files by size and ownership class without opening, moving, or
     deleting them.
   - Added `SPARK_JSONL_RESIDUE_POLICY_2026-06-08.md`.
   - Remaining work: run a backed-up archive/quarantine pass only after
     reference scans prove root `outcomes.jsonl` and `predictions.jsonl` are not
     active runtime inputs.

### P1 - Self-Evolution Reality

9. Keep self-evolution in supervised mode until mutation is real.
   - Current Builder runner can observe ledgers and evaluate manifests with
     guarded tests.
   - Rollback-plan and protected-component approval boundaries are now covered
     by focused regression tests.
   - It must not claim autonomous self-improvement until it has a sandboxed
     apply step, rollback execution proof, exact mutation provenance, and
     maintainer approval for protected components.

10. Preserve the no-op mutation drill as the private-promotion baseline.
    - Completed on 2026-06-08 with latest event `evt-458006fab354`.
    - Builder canonical ledger row:
      `ledger:fd24aee5b4fb46e08bc36925`.
    - Required test passed through the Builder change-manifest runner.
    - The result reached `promote_private` only with explicit
      `--allow-private-promotion`.
    - Next proof must cover dry-run apply and rollback execution before any
      real mutation claim.

11. Define the mutation executor boundary before writing one.
    - Executor input: accepted manifest, exact patch/artifact refs, required
      tests, rollback path, protected-component approvals.
    - Executor output: applied/reverted status, test results, final ledger row,
      and rollback execution proof.
    - No live runtime mutation without a dry-run and rollback execution proof.

### P1 - Source Of Truth

12. Enforce the Builder source-truth rule.
    - The installed Builder is the live runtime, is AGPL-aligned, declares
      `spark-harness-core`, and now carries `docs/SOURCE_TRUTH.md`.
    - The Desktop Builder tree remains dirty, divergent, and backlog-only until
      curated.
    - Remaining housekeeping: archive, relabel, or clean the Desktop tree so it
      cannot be mistaken for live runtime truth in future sessions.

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
  installed Builder, unless the document explicitly says it is inspecting
  backlog/historical evidence rather than live runtime truth.
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
git -C C:\Users\USER\.spark\tools\spark-cli status --short
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

Use the Spark CLI test command from `C:\Users\USER\.spark\tools\spark-cli`; the
explicit `src` insertion avoids accidentally importing a different CLI package.

## Suggested Next Execution Order

1. Wait for the parallel Spawner/Telegram session to finish, then re-run the
   dirty-repo and route-auth checks above.
2. Close Spawner loopback and signature verification gaps, with route matrix
   tests.
3. Add canonical ledger emission for Spawner and verify live adoption includes
   `spawner`.
4. Broaden Builder self-persist coverage for governed Builder tool paths beyond
   the change-manifest runner.
5. Run `jobs observability-report` before the next retention pass so future
   cleanup remains evidence-first.
   - Include `--include-unowned-jsonl` when checking loose JSONL residue.
   - Follow `SPARK_JSONL_RESIDUE_POLICY_2026-06-08.md`; no JSONL deletion by
     size alone.
6. Archive, relabel, or clean the Desktop Builder backlog tree so future stale
   tree audits stop at the source-truth warning instead of re-deriving runtime
   truth from it.
7. Define the self-evolution executor boundary, then prove dry-run apply and
   rollback before touching production code.

## Current Claim Boundary

Spark now has a usable authority and observability spine for governed records,
but not a complete authority runtime. The next honest milestone is:

> Every high-agency surface signs or verifies Governor decisions, writes a
> canonical bound ledger row keyed on `turn_id`, and can be traced in Builder
> from request to authorization to execution result.

Do not claim autonomous self-evolution or complete cross-surface traceability
until the remaining P0/P1 gates above pass.
