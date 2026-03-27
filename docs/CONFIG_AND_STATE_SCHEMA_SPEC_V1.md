# Spark Intelligence v1 Config And State Schema Spec

## 1. Purpose

This document defines the canonical config and state model for `Spark Intelligence` v1.

The goal is to avoid the slow drift into:

- multiple config truths
- adapter-owned hidden state
- undocumented runtime metadata
- repair work that only exists because the storage model became unclear

## 2. Design Rules

### 2.1 One Canonical Config Model

There should be one main config file for durable product configuration.

It should describe:

- enabled providers
- enabled adapters
- safe runtime options
- pairing and authorization defaults
- Spark integration options

### 2.2 One Canonical State Store

Durable runtime state should live in one SQLite database.

It should own:

- identities
- agents
- session bindings
- pairings
- jobs
- adapter health metadata
- migration records
- config mutation audit
- execution-lineage events and runs
- runtime environment snapshots
- quarantine records for policy-blocked material

### 2.3 Secrets Are Not General Config

Secrets should not be duplicated into normal config blobs.

Config may reference secrets, but not casually inline them into multiple surfaces.

### 2.4 Adapters Do Not Own Truth

Adapters may cache ephemeral transport details, but they do not own:

- identity truth
- pairing truth
- authorization truth
- session truth

## 3. Recommended File Layout

```text
~/.spark-intelligence/
|- config.yaml
|- .env
|- state.db
|- logs/
|- migrations/
`- adapters/
   `- <adapter-kind>/
```

### 3.1 `config.yaml`

Owns durable product configuration.

### 3.2 `.env`

Owns env-backed secret references and operator-provided secret values where needed in v1.

### 3.3 `state.db`

Owns canonical runtime state.

### 3.4 `adapters/`

Owns adapter-local ephemeral files only when strictly required by the transport library.

Anything here should be treated as transport cache, not core product truth.

## 4. Config Domains

Recommended top-level domains:

```yaml
workspace:
runtime:
providers:
channels:
identity:
jobs:
spark:
security:
```

### 4.1 `workspace`

- workspace id
- install metadata
- local paths

### 4.2 `runtime`

- foreground defaults
- autostart policy
- log verbosity
- feature flags that truly belong to runtime behavior

### 4.3 `providers`

- configured provider records
- default provider id
- endpoint compatibility mode
- secret refs

### 4.4 `channels`

- enabled adapters
- adapter config refs
- DM/group policy
- home channel metadata

### 4.5 `identity`

- default pairing mode
- linking policy
- session isolation defaults

### 4.6 `jobs`

- scheduler policy
- retry defaults
- wakeup behavior

### 4.7 `spark`

- Spark Researcher integration options
- Spark Swarm escalation options
- chip-attachment policy

### 4.8 `security`

- dangerous-action approval policy
- logging redaction policy
- allowed runtime features

## 5. State Domains

Recommended core tables:

- `humans`
- `agent_identities`
- `channel_installations`
- `channel_accounts`
- `conversation_surfaces`
- `identity_bindings`
- `session_bindings`
- `pairing_records`
- `allowlist_entries`
- `provider_records`
- `job_records`
- `job_runs`
- `adapter_health_events`
- `doctor_runs`
- `migration_runs`
- `config_mutation_audit`
- `builder_runs`
- `builder_events`
- `runtime_environment_snapshots`
- `quarantine_records`

## 6. Clear Ownership Rules

### 6.1 What Belongs In Config

- operator intent
- stable product settings
- safe defaults
- references to secrets

### 6.2 What Belongs In State

- runtime-created entities
- durable mappings
- health metadata
- audit history
- pairing approvals
- execution proof and delivery proof
- provenance for chip, plugin, personality, and swarm influence
- quarantine outcomes for blocked material

### 6.3 What Does Not Belong In Either

- duplicated secret copies
- adapter-owned shadow allowlists
- fuzzy cached identity guesses
- undocumented JSON blobs with shifting meaning
- critical product truth that exists only as generic `runtime_state` keys

## 7. Repair And Inspection Rules

The operator must be able to answer:

- what config is active
- what state is canonical
- what pairings exist
- what session owns a surface
- what adapter is unhealthy

without inspecting adapter internals.

## 8. Anti-Patterns To Reject

- `.env`, `config.yaml`, and `adapter.json` all carrying overlapping auth truth
- runtime writing back derived guesses into primary config
- per-adapter copies of identity and authorization state
- storing operator-facing truth only in logs

## 9. Final Rule

If a new feature needs a new storage surface, the default answer should be no.

It must prove why the existing config or state surfaces are insufficient.
