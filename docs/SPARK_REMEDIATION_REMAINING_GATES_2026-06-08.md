# Spark Remediation Remaining Gates - 2026-06-08

This is the post-remediation gate list after the 2026-06-08 Builder
verification pass. It is deliberately conservative: it records what can be
claimed from current evidence, what is delegated to the parallel
Spawner/Telegram session, and what must stay out of product claims until the
next proof pass lands.

## Fresh Verification

Commands run from installed Builder:

```powershell
python -m spark_intelligence.cli doctor --home C:\Users\USER\.spark\state\spark-intelligence --json
python -m spark_intelligence.cli jobs observability-report --home C:\Users\USER\.spark\state\spark-intelligence --include-gateway-logs --include-unowned-jsonl --jsonl-min-bytes 1000000 --jsonl-reference-scan --json
python -m spark_intelligence.cli harness tool-ledgers --home C:\Users\USER\.spark\state\spark-intelligence --surface spawner --limit 5 --json
```

Results:

- `doctor`: `ok: true`
- `builder-source-truth`: installed Builder is canonical; Desktop Builder is
  `desktop_backlog`
- `tool-call-ledger-adoption`: all expected surfaces present.
  Detail: `total=118 surfaces=builder=1, spark_cli=69, spawner=1, telegram=47; all_expected_surfaces_present`
- `state.db`: `253,333,504` bytes, `event_log=17,330`,
  `builder_events=17,330`, `tool_call_ledger=118`,
  `provider_runtime_events=0`
- loose JSONL report: 251 files / 190,472,313 bytes under `.spark`; 24 files
  at or above the 1 MB reporting threshold
- Spawner ledger detail: the single Spawner row is `tool_name=spawner.dispatch`
  with `status=not_started`; it proves Builder ingestion shape, not mission
  execution completion

## Landed Work To Preserve

Do not regress these commits:

- Builder `412fc59`: JSONL residue reference scan
- Builder `35b1a5b`: Desktop Builder backlog source-truth marker support
- Spawner `41ce79a`: HMAC Governor auth/signature hardening
- Telegram `93a8d6b`: signed Governor verification
- Spawner `97fbed2`: opaque command payload blocking
- Spawner `9f08b88`: canonical Spawner ledger ingestion into Builder
- Spawner `128bc2e`: API keys on Spawner control routes
- Spawner `8d80630`: remaining tracked loopback auth bypass closure

## Remaining Stop-Ship Gates

### 1. Spawner Watchdog Auth

The remaining known route work is delegated to the parallel Spawner session:

```text
src/routes/api/harness-watchdog/probe/+server.ts
```

Gate: after that session lands, run:

```powershell
git grep "allowLoopbackWithoutKey: true" -- src
```

The result should show only intentionally read-only surfaces with documented
reason codes. Mutating/control routes must require API key or hosted-session
auth even from loopback.

### 2. Spawner Runtime Ledger Depth

Builder now sees `spawner=1`, but the row is a pre-execution authorization row.

Gate: Spawner mission execution must persist or ingest canonical rows that reach
result status, with `turn_id`, `action_id`, `capability_id`,
`authorization_decision_id`, `surface=spawner`, and full `tool-call-ledger-v1`
payload. Use `harness trace-turn` to prove joinability.

### 3. Cross-Process Governor Trust

HMAC signing/verification code exists, but deployment truth still depends on
the settled Spawner/Telegram worktrees and shared-key configuration.

Gate: with `SPARK_GOVERNOR_HMAC_KEY` configured, unsigned, tampered,
wrong-key-id, and replayed decisions are rejected by consumers. If timestamp
freshness is enabled, stale decisions must also fail closed.

### 4. Telegram JSONL Fallback

Telegram still has local JSONL residue and fallback behavior visible to the
loose JSONL report.

Gate: normal runtime should either ingest governed tool-call ledgers directly
into Builder or produce an owner-approved import/retention path. Do not move or
delete the local JSONL files without owner signoff and a restore map.

### 5. Self-Evolution Honesty

The no-op private-promotion drill is real, and the guarded manifest runner is
real. Autonomous mutation is not real yet.

Gate: do not claim self-improvement until an executor can dry-run apply,
execute rollback, record exact mutation provenance, run required tests, and
persist final governed ledger evidence. Protected components still require
explicit human approval evidence.

### 6. Authority Runtime Claim

Harness Core is a strong authority model and Builder/CLI/Telegram/Spawner now
have meaningful governed adapters, but Spark still lacks a long-lived universal
governor host/scheduler/enforcer.

Gate: do not claim a complete authority runtime until there is a callable
runtime boundary with durable persistence, scheduler integration, consumer SDK
adoption, and fail-closed enforcement across high-agency surfaces.

### 7. JSONL Archive/Quarantine

The JSONL policy is now evidence-first, but movement has not happened.

Gate: before moving any loose JSONL, run the reference scan, create a dated
archive bundle, record owner signoff for surface-owned files, and keep
`delete_allowed=false` unless a separate restore-tested deletion plan exists.

## Next Check Sequence

1. Pull the final Spawner/Telegram result from the parallel session.
2. Re-run doctor, observability report, and Spawner/Telegram dirty status.
3. Re-run the Spawner route-auth grep and focused route tests.
4. Re-run Spawner and Telegram signature tests with shared-key env coverage.
5. Query `harness tool-ledgers --surface spawner` and `harness trace-turn` for
   a real execution turn, not only the pre-execution test row.
6. Update this document with the new evidence and only then relax any gate.

## Current Claim Boundary

Spark can now honestly claim a governed evidence spine with first-row adoption
across Builder, Spark CLI, Telegram, and Spawner. It cannot yet claim complete
Spawner runtime coverage, autonomous self-evolution, or a universal authority
runtime. Keep that boundary visible until the gates above are green.
