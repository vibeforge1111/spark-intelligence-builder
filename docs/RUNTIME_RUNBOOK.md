# Spark Intelligence Builder Runtime Runbook

Last updated: 2026-06-08

This runbook is for local operators and future implementation sessions. It captures the safe checks to run before calling Builder healthy.

## Quick Health

```powershell
spark-intelligence status
spark-intelligence doctor
spark-intelligence auth status
spark-intelligence operator review-pairings
```

Use JSON output when wiring automation or CI-like checks:

```powershell
spark-intelligence status --json
spark-intelligence doctor --json
spark-intelligence harness tool-ledgers --limit 10 --json
```

## Local Development

The current live Builder source-truth line is:

```text
C:\Users\USER\.spark\modules\spark-intelligence-builder\source
```

The Desktop checkout is backlog/historical evidence until curated. See
[SOURCE_TRUTH.md](./SOURCE_TRUTH.md) before porting behavior between Builder
trees.

```powershell
python -m pip install -e .
python -m pytest tests/test_builder_prelaunch_contracts.py tests/test_harness_cli.py tests/test_observability_retention.py -q
uv lock --check
```

The CI baseline uses the same focused test slice plus `pip-audit` and secret scanning.

## Telegram-Agent Bootstrap

Most production installs should be driven by `spark setup`. For direct Builder checks:

```powershell
spark-intelligence bootstrap telegram-agent `
  --provider custom `
  --api-key-env YOUR_PROVIDER_API_KEY `
  --model your-model-name `
  --base-url https://your-provider.example/v1 `
  --bot-token-env TELEGRAM_BOT_TOKEN
```

Rules:

- Prefer `--api-key-env` and `--bot-token-env` over literal secret values.
- Do not run Builder as a second live Telegram ingress if `spark-telegram-bot` owns the token.
- Run `spark-intelligence doctor` after bootstrap and before declaring the runtime ready.

## Telegram Runtime Refresh Policy

Do not restart Telegram profiles after every Builder change by habit. The live
`spark-telegram-bot` profiles are Node/ts-node processes, but normal
Builder-backed chat calls `spark_intelligence.cli gateway simulate-telegram-update`
through a fresh Python process per Telegram update. That Python process gets
`PYTHONPATH` pointed at the installed Builder source.

For Python-side `spark-intelligence-builder` changes, usually do this first:

```powershell
git pull origin main
```

from:

```text
C:\Users\USER\.spark\modules\spark-intelligence-builder\source
```

Then test one live Telegram turn. Restart Telegram only when:

- `spark-telegram-bot` TypeScript/Node code changed.
- Environment variables or profile config loaded at Node startup changed.
- The bot appears to be in a stale or unhealthy process state.
- A live test must eliminate process-state doubt across both Telegram profiles.

If only Builder Python prompt/context logic changed, pulling the installed source
should be enough because the next bridge call starts a fresh Python process.

## Harness Authority And Ledger Checks

After authority or observability changes, verify the canonical ledger path:

```powershell
spark-intelligence harness import-cli-ledgers --ledger-dir $env:USERPROFILE\.spark\state\approval-ledgers --json
spark-intelligence harness tool-ledgers --surface spark_cli --limit 5 --json
spark-intelligence harness trace-turn --turn-id <turn-id> --json
spark-intelligence jobs observability-report --older-than 2026-01-01T00:00:00Z --include-gateway-logs --include-unowned-jsonl --json
```

Use `harness trace-turn` when debugging one governed turn. It returns exact
canonical ledger rows plus Builder/event mirror rows that carry the turn id in
indexed columns or JSON payloads. Older rows without a turn id are still outside
the join and should not be claimed as fully traceable.

Run `jobs observability-report` before any future prune/VACUUM pass. It reports
`state.db` size, table counts, prunable row counts for the cutoff, and optional
gateway JSONL sizes without deleting anything.
With `--include-unowned-jsonl`, it also lists loose `.jsonl` files under the
Spark root by size and ownership class without opening, moving, or deleting
them.

For gateway integrations that cannot call Python APIs directly, use the stdio ingest seam:

```json
{"request_id":"req-ledger","command":"ingest_tool_ledger","row":{"ledger_id":"ledger:...","turn_id":"turn:...","action_id":"action:...","capability_id":"capability:...","authorization_decision_id":"decision:...","surface":"surface-name","ledger_json":{}}}
```

Rows missing the authority join fields must be rejected. Ingest is audit persistence only; it does not authorize execution.

## Self-Evolution Checks

Builder self-evolution remains supervised. The safe baseline is a no-op
manifest drill, not autonomous mutation.

For observe-only readiness:

```powershell
spark-intelligence harness self-evolution-snapshot --json
```

For a supervised no-op private-promotion drill:

```powershell
spark-intelligence harness change-manifest-runner `
  --manifest <change-manifest-v1.json> `
  --requested-verdict promote_private `
  --run-tests `
  --allow-private-promotion `
  --cwd C:\Users\USER\.spark\modules\spark-intelligence-builder\source `
  --json
```

The change-manifest runner writes a canonical `surface=builder`
`tool_call_ledger` row for the runner execution. After a drill, verify it:

```powershell
spark-intelligence harness tool-ledgers --surface builder --limit 5 --json
```

Reference evidence: [SPARK_SELF_EVOLUTION_NOOP_DRILL_2026-06-08.md](./SPARK_SELF_EVOLUTION_NOOP_DRILL_2026-06-08.md).

Do not claim autonomous self-evolution until a real manifest has exact artifact
refs, protected-component approvals where needed, dry-run apply proof, test
proof, and rollback proof.

## Provider Rotation

Provider keys should be rotation-friendly:

1. Rotate the key at the provider.
2. Update the env var or local secret store.
3. Restart only the process that needs the new secret.
4. Run `spark-intelligence auth status`.
5. Run a provider execution smoke through the gateway path.

Long-lived workers should resolve secrets per request or at clearly documented refresh points. Avoid caching secrets at import time.

## Pairing And Identity Checks

Before enabling a new channel/user path:

```powershell
spark-intelligence pairings list
spark-intelligence operator review-pairings
spark-intelligence sessions list
```

Security rules:

- External user ids must remain typed and channel-scoped.
- Usernames and display names are metadata, not identity proof.
- Pairing errors should return the same public denial shape regardless of the internal reason.

## Memory Checks

For current memory behavior:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_automation_tests.ps1
```

For full validation:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_validated_full_cycle.ps1
```

Treat recalled memory as untrusted data. It must be fenced, capped, and separated from system/developer instructions before reaching an LLM prompt.

## Release Checklist

Before pushing Builder changes that affect production behavior:

1. Run the focused CI slice locally.
2. Run `uv lock --check` if dependencies changed.
3. Confirm no `.env`, `.tmp-*`, token, key, JWT, local-home files, or private JSONL rivers are staged.
4. Confirm `spark.toml`, `pyproject.toml`, and `LICENSE` agree on `AGPL-3.0-only`, and `spark.toml` declares `spark-harness-core` in `[needs].modules`.
5. Push Builder.
6. Update the Builder commit pin in `spark-cli/registry.json`.
7. Run `spark verify --registry-pins` and `spark verify --provenance` from `spark-cli`.

## Common Failure Modes

| Symptom | First check |
|---|---|
| Telegram responds twice | Confirm only one gateway owns the bot token |
| Provider works in shell but not gateway | Check env inheritance and per-request secret resolution |
| Unknown user reaches runtime | Inspect pairing/allowlist state and external id typing |
| Memory answer follows hostile recall text | Check prompt fencing and memory envelope path |
| Fresh install gets old behavior | Check `spark-cli/registry.json` pins |
| Governed tool call is not queryable | Run `spark-intelligence harness tool-ledgers --turn-id <turn-id> --json` and check ingest fallback logs |
| `state.db` grows quickly | Run `jobs prune-observability` and confirm gateway JSONL rotation is enabled |

## Operational Redlines

- No committed secrets.
- No hidden daemon loops.
- No production floating git dependencies.
- No chat-owned runtime restart/config mutation.
- No destructive host action without explicit policy and approval-engine coverage.
- No high-agency tool execution without Harness Core authority and a canonical ledger path.
