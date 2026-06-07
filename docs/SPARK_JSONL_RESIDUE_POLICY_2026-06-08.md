# Spark JSONL Residue Policy - 2026-06-08

This policy covers loose `.jsonl` files under `C:\Users\USER\.spark` that are
outside the canonical Builder `state.db` observability store. It is a
classification and handling policy only; no archive, move, or deletion was run
as part of this document.

## Latest Read-Only Snapshot

Command:

```powershell
python -m spark_intelligence.cli jobs observability-report `
  --home C:\Users\USER\.spark\state\spark-intelligence `
  --include-unowned-jsonl `
  --jsonl-min-bytes 1000000 `
  --jsonl-limit 20 `
  --json
```

Observed on 2026-06-08:

| Metric | Value |
| --- | ---: |
| `state.db` bytes | 235,540,480 |
| `event_log` rows | 16,064 |
| `builder_events` rows | 16,064 |
| `tool_call_ledger` rows | 97 |
| Loose JSONL files | 251 |
| Loose JSONL bytes | 190,025,951 |

Largest residue classes:

| Class | Examples | Policy |
| --- | --- | --- |
| `legacy_runtime_river` | `recursion\mutations.jsonl`, `recursion\dspy\*\training_examples.jsonl`, `queue\events.jsonl`, `advisor\*.jsonl` | Archive as dated legacy evidence before any quarantine. Do not import into memory or prompts. |
| `root_log_river` | `logs\observe_hook_telemetry.jsonl`, `logs\codex_hook_bridge_telemetry.jsonl` | Archive as operational logs with redaction boundary. Do not treat as authority. |
| `root_unowned_jsonl` | `outcomes.jsonl`, `predictions.jsonl` | Freeze until a reference scan proves they are not active runtime input. |
| canonical store | `state\spark-intelligence\state.db` | Governed store. Use retention/VACUUM procedure, not JSONL quarantine. |

The report is intentionally metadata-only: size, path, modification time,
classification, and recommendation. It does not open or summarize file
contents.

## Handling Rules

1. Report before moving anything.
   Run `jobs observability-report --include-unowned-jsonl` and save the JSON
   output as the evidence manifest for the pass.

2. Scan references before changing paths.
   For each candidate, search the installed Spark trees and `.spark` config for
   the relative path or filename. Root files such as `outcomes.jsonl` and
   `predictions.jsonl` are blocked from movement until this scan is clean or an
   owner explicitly signs off.

3. Back up before archive or quarantine.
   Archive/quarantine passes must create a dated manifest containing original
   path, destination path, byte size, modified time, classification, reason, and
   rollback command. The manifest is the restore map.

4. Archive before quarantine.
   Legacy runtime rivers are evidence first. Move them to a dated archive bundle
   only after confirming no active reader depends on their original path.
   Quarantine means "not active runtime input"; it does not mean delete.

5. Canonicalize only with a schema and importer.
   Do not bulk-import legacy JSONL into `state.db`. A file can be canonicalized
   only when it has a declared schema, an owner, an importer, tests, and a
   retention policy.

6. Never promote residue into authority.
   Loose JSONL can be audit evidence. It must not become memory, prompt
   context, training data, or runtime authority without explicit provenance,
   redaction, and promotion gates.

7. Delete only after a retention window.
   Deletion requires a prior archive/quarantine manifest, successful doctor
   check after movement, and a separately documented retention window. The
   default window is at least 30 days.

## Archive/Quarantine Pass Template

Use this shape for a future execution pass:

```text
run_id: jsonl-residue-YYYYMMDD-HHMMSS
spark_root: C:\Users\USER\.spark
report_command: <exact observability-report command>
reference_scan: <commands and results>
archive_root: C:\Users\USER\.spark\archive\jsonl-residue\<run_id>
quarantine_root: C:\Users\USER\.spark\quarantine\jsonl-residue\<run_id>
doctor_after: <doctor command and verdict>
restore_plan: move each destination back to original path from manifest
```

Minimum verification after any movement:

```powershell
python -m spark_intelligence.cli doctor --home C:\Users\USER\.spark\state\spark-intelligence --json
python -m spark_intelligence.cli jobs observability-report --home C:\Users\USER\.spark\state\spark-intelligence --include-unowned-jsonl --json
```

## Current Decision

No loose JSONL files were moved or deleted in this pass. The current state is:

- canonical `state.db` retention is handled separately by
  `SPARK_STATE_DB_RETENTION_RUN_2026-06-08.md`;
- loose JSONL residue is reportable and governed by this policy;
- root `outcomes.jsonl` and `predictions.jsonl` remain frozen until active
  reader checks are complete;
- large `recursion`, `logs`, `queue`, and `advisor` rivers are archive
  candidates, not deletion candidates.
