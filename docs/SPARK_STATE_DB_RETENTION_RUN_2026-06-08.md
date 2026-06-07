# Spark State DB Retention Run - 2026-06-08

This records the controlled retention/VACUUM pass run for the Spark deep-audit
remediation goal. The purpose was to reduce unbounded observability growth
without losing canonical governed tool ledgers.

## Scope

- Live DB: `C:\Users\USER\.spark\state\spark-intelligence\state.db`
- Cutoff used: `2026-06-01 00:00:00`
- Included tables: `event_log`, `builder_events`, `tool_call_ledger`,
  `provider_runtime_events`
- Included gateway JSONL retention: yes
- VACUUM: yes

The cutoff keeps the active June 2026 authority remediation window and removes
older observability rows from April and May 2026.

## Backup

SQLite online backup was created before pruning:

```text
C:\Users\USER\Documents\Codex\2026-06-07\let-s-improve-fix-all-of\work\state-db-backups\state-20260607T205442Z.db
```

Backup size: `654,905,344` bytes.

Pre-run integrity check: `ok`.

## Command

```powershell
python -m spark_intelligence.cli jobs prune-observability `
  --home C:\Users\USER\.spark\state\spark-intelligence `
  --older-than "2026-06-01 00:00:00" `
  --include-builder-events `
  --include-gateway-logs `
  --vacuum `
  --json
```

## Result

The command completed successfully:

```json
{
  "deleted_counts": {
    "event_log": 45684,
    "tool_call_ledger": 0,
    "provider_runtime_events": 0,
    "builder_events": 45684
  },
  "total_deleted": 91368,
  "vacuumed": true,
  "gateway_logs": {
    "deleted_counts": {
      "gateway_trace": 2184,
      "gateway_outbound": 1
    },
    "kept_counts": {
      "gateway_trace": 55,
      "gateway_outbound": 0
    },
    "total_deleted": 2185
  }
}
```

## Before And After

| Metric | Before | After |
| --- | ---: | ---: |
| `state.db` bytes | 654,905,344 | 224,800,768 |
| `builder_events` rows | 61,027 | 15,346 |
| `event_log` rows | 61,027 | 15,346 |
| `tool_call_ledger` rows | 82 | 82 |
| `provider_runtime_events` rows | 0 | 0 |

Ledger surface counts after cleanup:

| Surface | Rows |
| --- | ---: |
| `spark_cli` | 69 |
| `telegram` | 13 |

After the later Builder self-evolution no-op drill and Telegram test activity,
live adoption became `builder=1`, `spark_cli=69`, and `telegram=17`.

Post-run integrity check: `ok`.

## Post-Run Verification

`python -m spark_intelligence.cli doctor --home C:\Users\USER\.spark\state\spark-intelligence --json`
returned `ok: true`.

Expected consequence: older Watchtower history was pruned, so some historical
panels now report `state=unknown` until new live ingress/background activity is
recorded. This is acceptable for the cleanup because stop-ship checks still
pass, canonical ledgers were preserved, and doctor has no follow-up surfaces.

## Remaining Work

- Add first-class `spawner` rows to `tool_call_ledger`; current live adoption
  has `builder`, `spark_cli`, and `telegram`.
- Re-run this retention procedure only with a fresh backup and before/after
  counts.
- Use `spark-intelligence jobs observability-report --older-than <cutoff>
  --include-gateway-logs --include-unowned-jsonl --json` before pruning to
  capture `state.db` bytes, table counts, prunable row counts, gateway JSONL
  sizes, and loose JSONL residue without deleting anything.
- Follow `SPARK_JSONL_RESIDUE_POLICY_2026-06-08.md` before moving loose JSONL
  files; the state DB retention procedure does not authorize JSONL deletion.
