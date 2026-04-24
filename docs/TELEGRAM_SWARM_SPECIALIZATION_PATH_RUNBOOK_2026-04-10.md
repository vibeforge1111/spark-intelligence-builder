# Telegram Swarm Specialization-Path Runbook 2026-04-10

## Purpose

This runbook records the proven integration shape for running a real specialization-path autoloop from Telegram through `spark-intelligence-builder`, syncing the resulting collective payload into hosted Spark Swarm, and preparing the result for downstream GitHub delivery.

It is written for:

- operators wiring a new workspace
- agents reasoning about the current integration contract
- future product work in Builder and Spark Swarm

The validated architecture is:

`Telegram -> Builder -> spark-swarm bridge -> specialization-path repo -> hosted Spark Swarm`

Not:

`Telegram -> spark-swarm directly`

and not:

`Telegram -> Builder -> vendored fallback repo unless explicitly intended`

## Proven Working Flow

The following flow is now working for the `trading-crypto` specialization path:

1. Telegram invokes `/swarm autoloop trading-crypto rounds 1 force`.
2. Builder resolves the attached specialization-path repo and launches the local Spark Swarm bridge.
3. The bridge mutates the repo-owned benchmark target `benchmarks/trading-crypto-candidate.json`.
4. The benchmark evaluates the candidate and either keeps or reverts it.
5. The specialization path writes `.spark-swarm/collective-sync.json`.
6. Telegram invokes `/swarm sync`.
7. Builder uploads the collective payload to the hosted Swarm workspace.
8. Telegram can inspect `/swarm upgrades` and invoke `/swarm deliver <upgrade_id>`.

This was validated against the real repo-backed path:

- repo: `<workspace>/domain-chip-trading-crypto`
- path key: `trading-crypto`
- benchmark target: `benchmarks/trading-crypto-candidate.json`

## Required Setup Contract

For a specialization path to work end to end from Telegram, all of the following must be true.

### 1. Builder Config

Builder must know:

- `spark.swarm.runtime_root`
- `spark.swarm.api_url`
- `spark.swarm.supabase_url`
- `spark.swarm.workspace_id`
- `spark.swarm.access_token_env`
- `spark.swarm.refresh_token_env`
- `spark.swarm.auth_client_key_env`
- `spark.specialization_paths.roots`
- `spark.specialization_paths.active_path_key`

Important operational truth:

- Builder resolves Swarm auth from the workspace `.env` file, not just the shell environment.

### 2. Specialization-Path Repo

The attached repo must contain:

- `spark-chip.json`
- `specialization-path.json`
- repo-owned mutation target templates
- repo-owned benchmark scenario files
- an `AUTORESEARCH.md` that the bridge can update with specialization-path mutation metadata

For the trading path, the minimum repo-owned files are:

- `specialization-path.json`
- `docs/TRADING_BACKTEST_OPERATOR_GUIDE.md`
- `benchmarks/scenarios/trend-ema-btceth-4h.json`
- `templates/benchmarks/trading-crypto-candidate.json`
- `benchmarks/trading-crypto-candidate.json`

### 3. Runtime Wiring

Builder must export:

- `SPARK_RESEARCHER_REPO`
- `SPARK_SWARM_STATE_DIR`
- `SPARK_SWARM_SPECIALIZATION_PATH_<PATH_KEY>_REPO`

That last variable is critical. Without it, the Spark Swarm bridge may silently resolve the specialization path to a sibling fallback repo instead of the repo Builder actually attached.

### 4. Hosted Sync Contract

The collective payload must be uploadable to the configured hosted workspace.

Important payload rule:

- if `workspaceId` is blank in the local payload, Builder must normalize it to the configured workspace id before upload

## Failure Modes Found

These were the important real-world failure modes from this run.

### 1. Workspace `.env` vs Process Env

Observed behavior:

- `swarm status` still reported `auth_state=missing` even when the shell had exported valid Swarm tokens

Cause:

- Builder reads Swarm secrets from its workspace `.env` file

Fix:

- migrate the Swarm auth variables into the Builder home `.env`

### 2. Vendored Repo Resolution Drift

Observed behavior:

- Builder was pointed at the real specialization-path repo, but the bridge still benchmarked the vendored trading repo

Cause:

- Spark Swarm specialization-path repo resolution defaulted relative to the bridge repo unless `SPARK_SWARM_SPECIALIZATION_PATH_<KEY>_REPO` was set

Fix:

- Builder now exports that env var in `src/spark_intelligence/swarm_bridge/local.py`

### 3. Researcher-Only Sync Assumption

Observed behavior:

- hosted `swarm sync` initially required `spark-researcher` readiness even though the active path already had a valid `.spark-swarm/collective-sync.json`

Cause:

- Builder sync logic only knew how to build a payload from `spark-researcher`

Fix:

- Builder sync now accepts the active specialization-path collective payload as a first-class payload source

### 4. Empty Payload Workspace Id

Observed behavior:

- hosted sync returned `workspace_mismatch`

Cause:

- the specialization-path payload had `workspaceId: ""`

Fix:

- Builder normalizes blank `workspaceId` to the configured workspace id before upload

### 5. Repo Write Boundary

Observed behavior:

- autoloop and specialization-path scaffold steps failed against the real repo when run inside the sandbox

Cause:

- the bridge needed to write `AUTORESEARCH.md` and repo-owned mutation targets in a sibling repo

Fix:

- rerun the affected commands with elevated filesystem permission

### 6. GitHub Delivery Ambiguity

Observed behavior:

- `/swarm deliver <upgrade_id>` recorded delivery state but did not open a PR

Cause:

- the available GitHub app in this session is not installed on `vibeforge1111/domain-chip-trading-crypto`

Implication:

- Swarm delivery state and GitHub PR creation are currently decoupled in practice

## Current Validated Trading Result

The validated kept mutation for the real repo-backed `trading-crypto` path is:

- `doctrine_id = breakout_volatility_expansion`
- `strategy_id = bollinger_squeeze_breakout`
- `market_regime = high_vol`
- `timeframe = 1h`
- `venue = hyperliquid`
- `asset_universe = BTC,ETH`
- `paper_gate = balanced`

Observed round result:

- baseline score: `0.4086`
- kept candidate score: `0.5326`
- delta: `+0.1240`

## Product Improvements To Build

These are the highest-value product learnings to carry back into Spark Swarm and Builder.

### Builder

Builder should add:

- a `/swarm doctor` operator command that reports:
  - hosted auth source
  - workspace id
  - active specialization path repo
  - scenario path
  - mutation target path
  - repo write readiness
  - GitHub delivery readiness
- a clear status field for `payload_source_repo`
- a clear status field for `benchmark_repo`
- a clear status field for `mutation_target_repo`
- explicit warnings when the attached path repo and the bridge-resolved repo differ
- a built-in migration helper that imports Swarm auth from a previous working home into the current workspace `.env`

### Spark Swarm

Spark Swarm should add:

- specialization-path repo resolution diagnostics in CLI output
- a first-class explicit `--repo` or bound repo display in all specialization-path commands
- a payload validator that prints the effective `workspaceId` before upload
- a single-step specialization-path bootstrap that scaffolds:
  - `specialization-path.json`
  - default scenario
  - default mutation template
  - operator guide
  - AUTORESEARCH specialization-path block
- clearer delivery states that separate:
  - upgrade recorded
  - PR opened
  - PR awaiting review
  - PR merged

### Agent-Readable Documentation

Spark Swarm and Builder should ship concise agent-readable docs that define:

- the specialization-path required file contract
- the hosted sync payload contract
- the environment variable contract
- the repo-resolution contract
- the expected delivery lifecycle

Those docs should be short enough for agents to load directly during execution, not just for humans to read as long-form plans.

## Recommended Knowledgebase Surfaces

The following docs or pages would materially reduce operator friction next time:

- `Builder Telegram + Swarm quickstart`
- `Specialization-path repo contract`
- `Swarm doctor troubleshooting matrix`
- `Hosted sync auth and workspace troubleshooting`
- `Upgrade delivery and GitHub PR state model`
- `Agent-readable specialization-path execution checklist`

## Practical Operator Checklist

Before expecting Telegram autoloops to work on a new path, verify:

- Builder `swarm status` shows `payload_ready=true`
- Builder `swarm status` shows `api_ready=true`
- the attached path repo is the repo you expect
- the repo contains `specialization-path.json`
- the repo contains the benchmark scenario and mutation target files
- the bridge can write `AUTORESEARCH.md`
- `/swarm run <path_key>` succeeds once
- `/swarm autoloop <path_key> rounds 1 force` can keep or revert a real candidate
- `/swarm sync` uploads successfully
- `/swarm upgrades` shows the upgrade you expect
- GitHub installation or push credentials exist before expecting PR automation
