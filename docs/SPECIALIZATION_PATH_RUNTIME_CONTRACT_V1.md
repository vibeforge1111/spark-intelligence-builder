# Spark Intelligence v1 Specialization Path Runtime Contract

## 1. Purpose

This document defines how `Spark Intelligence` should discover, validate, and use specialization-path repos.

The goal is to let Builder, Telegram, and future agents run real path-backed workflows without:

- hardcoding repo-specific assumptions into the runtime shell
- treating specialization paths as opaque folders
- making each client rediscover the same repo contract differently

## 2. Core Rule

A specialization path should be discoverable through one explicit runtime contract.

Agents should not need repo-specific tribal knowledge to answer basic questions such as:

- which repo is active
- which scenario file is the default input
- which file is the mutation target
- whether the path can sync into Spark Swarm
- whether the path is ready for an autoloop run

## 3. Design Goals

### 3.1 One Contract Across Clients

Builder CLI, Telegram, and future Spark clients should resolve the same path metadata the same way.

### 3.2 Repo-Backed Operation

When a specialization path is attached to a real repo, Spark should use that repo directly instead of silently falling back to vendored defaults.

### 3.3 Machine-Readable Readiness

The path should expose enough structured information for agents to decide whether execution is safe.

### 3.4 Narrow Ownership

The path owns domain workflow metadata.

It does not own transport auth, user identity, or Swarm workspace auth.

## 4. Required Runtime Inputs

Recommended v1 specialization-path contract:

```text
specialization-path.json
default scenario path
default mutation target path
optional collective sync payload
optional benchmark or autoloop outputs
```

## 5. Canonical Repo Marker

The repo root should contain `specialization-path.json`.

Recommended minimum fields:

```json
{
  "schema_version": "spark-specialization-path.v1",
  "path_key": "trading-crypto",
  "label": "Trading Crypto",
  "default_scenario_path": "benchmarks/scenarios/trend-ema-btceth-4h.json",
  "default_mutation_target_path": "benchmarks/trading-crypto-candidate.json",
  "collective_sync_path": ".spark-swarm/collective-sync.json"
}
```

## 6. Runtime Resolution Rules

### 6.1 Active Path Resolution

Spark Intelligence should resolve an active specialization path from the local attachment state first.

### 6.2 Repo Preference Rule

If the active specialization path maps to a real repo root, Spark should export and use that repo root as the canonical path runtime.

Do not silently prefer bundled fallback copies when the attached repo is available.

### 6.3 Default File Resolution

When `specialization-path.json` declares:

- `default_scenario_path`
- `default_mutation_target_path`
- `collective_sync_path`

Spark should resolve them relative to the specialization-path repo root.

### 6.4 Fail-Closed Rule

If the active path is missing critical files, Spark should report the missing contract fields explicitly instead of guessing.

## 7. Required Readiness Surface

Every client should be able to ask for a normalized readiness report.

Recommended v1 response shape:

```json
{
  "enabled": true,
  "configured": true,
  "active_path_key": "trading-crypto",
  "active_path_repo_root": "/abs/path/to/domain-chip-trading-crypto",
  "scenario_path": "/abs/path/to/benchmarks/scenarios/trend-ema-btceth-4h.json",
  "mutation_target_path": "/abs/path/to/benchmarks/trading-crypto-candidate.json",
  "payload_source": "specialization_path",
  "payload_ready": true,
  "blockers": [],
  "recommendations": []
}
```

This should be the contract surfaced by `swarm doctor` and reused by Telegram or any future agent runtime.

## 8. Collective Sync Rule

If `.spark-swarm/collective-sync.json` exists under the active specialization-path repo, Spark should treat it as a valid collective payload source.

The client should not need a second copy of the payload inside Builder state just to sync Swarm.

## 9. Autoloop Readiness Rule

For a specialization path to be considered autoloop-ready, Spark should be able to answer:

- is the path attached and active
- is the repo root present
- does `specialization-path.json` exist
- does the default scenario path exist
- does the default mutation target path exist
- is the collective sync payload present or creatable
- is Spark Swarm auth configured

If any answer is no, the readiness report should expose the blocker by name.

## 10. Ownership Boundaries

### 10.1 Specialization Path Owns

- default scenario selection
- default mutation target
- path-level workflow metadata
- path-level collective payload location

### 10.2 Spark Intelligence Owns

- path attachment and activation
- runtime diagnostics
- Telegram and CLI operator surfaces
- Swarm auth and sync invocation

### 10.3 Spark Swarm Owns

- collective ingestion
- merge lineage
- downstream multi-agent execution

## 11. v1 Practical Rule

For v1:

- require `specialization-path.json` at the repo root
- normalize readiness through `swarm doctor`
- use repo-backed files directly
- keep the contract small enough for agents to parse reliably

## 12. Final Decision

Specialization paths in Spark Intelligence should be treated as explicit repo-backed runtime contracts, not implicit folder conventions.

That contract should be stable enough that Telegram, Builder, and future agents can all run the same path without custom glue.
