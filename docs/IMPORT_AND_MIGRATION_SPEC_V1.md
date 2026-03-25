# Spark Intelligence v1 Import and Migration Spec

## 1. Purpose

This document defines how `Spark Intelligence` should import and migrate useful state from external agent systems, especially:

- OpenClaw
- Hermes Agent

The goal is continuity without corruption.

We want users to move into Spark Intelligence quickly while keeping the system:

- lightweight
- auditable
- maintainable
- safe

This spec is intentionally strict.

We should import:

- deterministic state
- auditable state
- operator-meaningful state
- state that maps cleanly into Spark Intelligence

We should not import:

- opaque state
- unverifiable state
- foreign memory semantics
- internal traces whose meaning does not survive translation

## 2. Design Goals

### 2.1 Preserve User Continuity

Migration should preserve the user's sense that they are still talking to the same agent identity.

### 2.2 Stay Safe by Default

No imported state should become active until it passes validation.

### 2.3 Keep the Runtime Clean

Import should not contaminate Spark Intelligence with foreign runtime assumptions.

### 2.4 One Migration Path

There should be one migration harness and one migration record model, not custom scripts for each source.

### 2.5 Reversible Where Possible

Before activation, migration artifacts should be diffable and reviewable.

## 3. Core Rule

Import what is useful, deterministic, and auditable.

Do not import mystery state.

## 4. Migration Philosophy

Spark Intelligence is not a wrapper around OpenClaw or Hermes.

It is a Spark-native runtime.

Migration therefore means:

- extract what the user should not have to redo
- translate it into Spark-native structures
- reject the rest

We are not trying to perfectly recreate the foreign runtime.

We are trying to preserve continuity while keeping our own architecture clean.

## 5. Migration Scope

### 5.1 Safe v1 Import Targets

These are the most realistic and useful import targets:

- adapter configuration that maps cleanly
- channel identifiers and pairings
- allowlists and authorization surfaces
- basic user identity records
- basic session-to-user mapping
- operator configuration where meaning is clear
- simple scheduled tasks whose intent is clear

### 5.2 Conditional v1 Import Targets

These may be importable if the source format is stable and validation is strong:

- recurring reminder jobs
- user-facing scheduled workflows
- lightweight agent profile metadata
- safe transport-level history summaries

### 5.3 Explicitly Unsupported v1 Import Targets

These should not be imported in v1:

- foreign memory stores with incompatible promotion logic
- internal agent traces with ambiguous semantics
- opaque runtime caches
- undocumented per-tool hidden state
- unsafe secrets without clear provenance
- job history that cannot be replayed or interpreted safely
- prompt stacks that depend on foreign runtime layering we do not implement

## 6. Source-System Lessons

### 6.1 OpenClaw

OpenClaw appears to take migration and repair seriously through doctor and legacy migration surfaces. That is worth borrowing operationally.

But Spark should avoid importing state simply because OpenClaw persisted it.

Good candidates from OpenClaw:

- channel and gateway config
- allowlists
- pairing state
- clear recurring jobs

Bad candidates from OpenClaw:

- opaque gateway runtime leftovers
- uncertain thread state
- delivery repair artifacts with unclear semantics

### 6.2 Hermes Agent

Hermes appears cleaner in shared runtime and SQLite-oriented state patterns, which may make migration easier.

Good candidates from Hermes:

- session mappings
- channel config
- install/runtime config with clear meaning
- cron intent when expressed clearly

Bad candidates from Hermes:

- internal runtime state that assumes Hermes-specific provider wiring
- memory-like derived state without clear contract

## 7. Migration Model

Migration should run in four phases.

### 7.1 Discover

Read source system files, schemas, and relevant config.

Output:

- source type
- version if detectable
- discovered artifacts
- unsupported artifacts

### 7.2 Normalize

Convert source artifacts into a source-agnostic migration model.

This intermediate model should be the same whether the source is OpenClaw or Hermes.

### 7.3 Validate

Validate:

- structural correctness
- referential integrity
- adapter compatibility
- schedule safety
- identity coherence
- secret availability

Nothing becomes live before this phase passes.

### 7.4 Activate

Write validated records into Spark Intelligence stores and generate an activation report.

## 8. Migration Record Model

Use one canonical migration record format.

Recommended v1 fields:

```text
id
source_system
source_version
artifact_kind
source_path
source_identifier
normalized_payload
validation_status
validation_errors
transform_notes
activation_status
created_at
updated_at
```

## 9. Import Artifacts

Every migration run should produce artifacts.

Required artifacts:

- discovery report
- normalized manifest
- validation report
- activation report
- diff summary

Recommended file shape:

```text
migrations/
|- run-<timestamp>/
|  |- discovery.json
|  |- normalized.json
|  |- validation.json
|  |- activation.json
|  `- summary.md
```

## 10. CLI Shape

Recommended v1 CLI:

- `spark-intelligence import discover --source <path> --type <openclaw|hermes>`
- `spark-intelligence import normalize --run <id>`
- `spark-intelligence import validate --run <id>`
- `spark-intelligence import activate --run <id>`
- `spark-intelligence import inspect --run <id>`

Recommended convenience flow:

- `spark-intelligence import run --source <path> --type <openclaw|hermes> --dry-run`

### 10.1 Dry Run

Dry run should be the default posture.

Dry run must:

- not mutate live state
- still perform normalization
- still perform validation
- still generate artifacts

## 11. Identity Migration

Identity continuity matters more than internal runtime continuity.

### 11.1 What To Preserve

- user id mappings where source meaning is clear
- channel user identifiers
- DM or thread pairing relationships
- preferred channel or home channel if available

### 11.2 What To Avoid

- merging multiple users into one identity because of bad heuristics
- treating a group thread id as a personal identity
- inheriting context from mixed-participant channels by accident

### 11.3 Conflict Handling

When identity mapping is ambiguous:

- do not auto-merge
- mark for operator review
- keep the normalized artifact inactive

## 12. Adapter Config Migration

Adapter migration should focus on intent, not raw copy.

### 12.1 Safe Config Fields

Usually safe to import:

- enabled adapter list
- channel metadata
- non-secret routing defaults
- allowlist identifiers

### 12.2 Sensitive Config Fields

Handle carefully:

- tokens
- session keys
- bot credentials
- device pairing artifacts

Rules:

- validate presence
- validate format where possible
- never silently activate broken secrets
- surface missing secrets clearly

## 13. Job Migration

Scheduled work must migrate through the same central job harness.

### 13.1 Safe Job Imports

Good v1 candidates:

- reminders
- recurring syncs with clear targets
- periodic user-facing jobs with explicit intent

### 13.2 Unsafe Job Imports

Do not import:

- source jobs with unknown side effects
- jobs that depend on foreign runtime internals
- jobs that assume different locking or retry semantics
- jobs that cannot be made idempotent

### 13.3 Translation Rule

Imported jobs should become Spark job records, not foreign job records copied verbatim.

## 14. Memory Migration Boundary

Memory belongs to the memory chip.

Spark Intelligence should not attempt to directly import foreign memory systems in v1 unless the memory chip explicitly defines a safe translation contract.

Default v1 rule:

- do not import memory blobs
- do not import foreign memory ranks
- do not import semantic retrieval state

Allowed exception:

- import a lightweight, explicit user profile summary if it is human-readable, auditable, and treated as profile metadata rather than memory doctrine

## 15. Validation Rules

Every migration run should validate:

- source type is recognized
- source files are readable
- required fields exist
- identities do not conflict
- adapter mappings are valid
- secrets are available or explicitly missing
- jobs are safe and idempotent enough to translate
- no unsupported state was accidentally marked for activation

## 16. Activation Rules

Activation should be conservative.

### 16.1 Allowed Activation

Activate only records that are:

- validated
- non-conflicting
- representable in Spark Intelligence

### 16.2 Blocked Activation

Do not activate records that are:

- ambiguous
- unsafe
- unsupported
- dependent on missing secrets

## 17. Rollback

Rollback should be simple.

Recommended v1 posture:

- import into isolated Spark Intelligence tables or namespaces
- activation is versioned
- latest activation can be disabled or reverted

At minimum, the operator should be able to:

- see what was activated
- disable imported jobs
- disable imported adapter routes
- remove imported identity links before they are deeply entangled

## 18. Observability

The operator should be able to answer:

- what source was imported?
- what was discovered?
- what was rejected?
- what was transformed?
- what is live now because of the import?

Required views:

- migration run list
- artifact counts by kind
- validation failures
- activation summary

## 19. Smoke Tests

Required migration smoke tests:

- discover OpenClaw sample config
- discover Hermes sample config
- normalize known-good source
- reject malformed source
- validate missing secret case
- validate identity conflict case
- activate known-good import
- ensure dry-run makes no live mutations

## 20. Anti-Patterns To Reject

### 20.1 Blind Copy

Do not copy source files into Spark and call that migration.

### 20.2 Magic Merge

Do not automatically merge ambiguous identities because it “probably looks right.”

### 20.3 Memory Smuggling

Do not smuggle foreign memory systems into Spark under a different name.

### 20.4 Unsafe Activation

Do not activate imported jobs, routes, or pairings before validation.

### 20.5 Source-Locked Architecture

Do not bend Spark Intelligence architecture just to preserve a foreign source format.

## 21. Recommended v1 Outcome

V1 migration should make users feel:

- setup is faster
- continuity is preserved
- important settings were carried over
- the new system is cleaner than the old one

It should not make the system feel like a museum of imported legacy assumptions.

## 22. Final Decision

Spark Intelligence v1 should support structured, dry-run-first, manifest-based imports from OpenClaw and Hermes for identities, channels, allowlists, config, and safe jobs.

It should refuse opaque state, refuse foreign memory semantics, and preserve Spark-native cleanliness over perfect historical imitation.
