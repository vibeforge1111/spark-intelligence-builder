# Agent Identity System Spec - March 28, 2026

## 1. Purpose

This document defines the canonical agent identity model for Spark Intelligence Builder when the system may be used:

- directly from Builder-first adapters such as Telegram
- from Spark Swarm first
- from both over time, with later linking into one final agent

The goal is to keep the identity model simple for users and strict for the system.

## 2. Product Rule

Recommended path:

- connect an existing Spark Swarm agent first

Supported fallback:

- create the agent in Builder first and link it to Spark Swarm later

Conflict repair path:

- if both Builder and Spark Swarm created separate agent identities for the same human, keep the Spark Swarm identity as canonical and import Builder state into it

## 3. Core Principles

### 3.1 One Human, One Canonical Agent

Spark Swarm currently operates as one human to one agent.

Builder matches that model.

Within one workspace, one human may own:

- one canonical agent identity

Builder may temporarily create a local-first agent before Spark Swarm is connected, but that local identity must be linkable into the same final canonical agent later.

### 3.2 Agent Name Is Mutable, Agent ID Is Not

`agent_id` is the stable identity key.

`agent_name` is user-facing metadata and may change:

- in Builder
- in Spark Swarm
- via bot-facing flows

Renames must never create a new agent identity by themselves.

### 3.3 Persona Belongs To The Agent

The canonical base persona belongs to the agent, not the channel, not the session, and not the current display name.

This means:

- renaming the agent does not reset persona
- linking Builder and Swarm merges into one agent persona history
- human-specific style preferences remain overlays, not the core persona

### 3.4 Builder And Swarm Are Two Front Doors To One Agent

Builder and Spark Swarm are treated as two entry points to the same eventual agent record.

The system should never force users to choose a permanent birthplace for the agent.

## 4. Builder-Side Canonical Model

Builder now ships the following local identity records.

### 4.1 Canonical Agent Identity

Implemented in `canonical_agent_links`:

```text
human_id
canonical_agent_id
preferred_source
status
conflict_agent_id
conflict_reason
created_at
updated_at
```

`preferred_source` values:

- `builder_local`
- `spark_swarm`
- `linked`

### 4.2 Mutable Agent Profile

Implemented in `agent_profiles`:

```text
agent_id
human_id
agent_name
origin
status
external_system
external_agent_id
metadata_json
name_updated_at
name_source
created_at
updated_at
```

The display name is stored separately from the canonical identity anchor.

### 4.3 Alias And Supersession Mapping

Implemented in `agent_identity_aliases`:

```text
alias_agent_id
canonical_agent_id
alias_kind
reason_code
created_at
```

This is how Builder preserves provisional local ids after canonicalizing onto a Spark Swarm id.

### 4.4 Rename History

Implemented in `agent_rename_history`:

```text
rename_id
agent_id
human_id
old_name
new_name
source_surface
source_ref
created_at
```

### 4.5 Agent Persona Base

Implemented in `agent_persona_profiles`:

```text
agent_id
persona_name
persona_summary
base_traits_json
behavioral_rules_json
provenance_json
updated_at
created_at
```

Builder also records explicit persona mutations in `agent_persona_mutations`.

### 4.6 Human Overlay Compatibility

Implemented today as a compatibility overlay in `personality_trait_profiles` plus a runtime mirror:

```text
human_id
deltas_json
updated_at
created_at
```

This captures human-scoped style shaping without redefining the agent's core persona.

## 5. Source Of Truth Rules

### 5.1 Identity

If Spark Swarm is already connected:

- Spark Swarm identity is authoritative for external identity

If Spark Swarm is not connected yet:

- Builder may create a local canonical agent

If Builder created first and Swarm appears later:

- Builder links to Swarm
- the Spark Swarm external identity becomes the canonical external reference
- local Builder state is migrated into the canonical linked agent

### 5.2 Name

Agent names are editable on both sides.

Rules:

- do not treat a rename as identity creation
- keep audit history for name changes
- resolve visible current name by latest confirmed write
- preserve prior names in mutation history

Builder implements this with:

- `agent_profiles.name_updated_at`
- `agent_profiles.name_source`
- `agent_rename_history`

Current operator surfaces:

- `spark-intelligence agent inspect`
- `spark-intelligence agent rename --human-id <human_id> --name <name>`
- `spark-intelligence agent link-swarm --human-id <human_id> --swarm-agent-id <id> --agent-name <name> [--confirmed-at <timestamp>]`
- `spark-intelligence agent import-swarm --human-id <human_id> [--chip-key <chip-key>]`

Current precedence behavior:

- stale Swarm names do not overwrite a newer Builder rename
- fresher Swarm names can replace an older Builder name
- the winning visible name is recorded with `name_source` and `name_updated_at`

### 5.3 Persona

Canonical base persona is agent-scoped.

Explicit persona authoring beats passive inferred drift.

Passive evolution remains bounded and reversible.

Builder currently supports:

- explicit conversational persona authoring from the bridge
- agent-scoped persona persistence
- human-scoped overlay compatibility
- migration of legacy human-only overlays via:
  - `spark-intelligence agent migrate-legacy-personality --human-id <human_id>`

## 6. Recommended User Paths

### 6.1 Recommended Path: Swarm First

User-facing guidance:

- connect your existing Spark Swarm agent first so your identity, name, and personality stay unified from the start

Builder behavior:

- import `agent_id`
- import `agent_name`
- create or refresh the local external reference
- attach Builder-side persona state to the imported canonical agent
- support hook-backed live imports from an external Spark Swarm runtime through the `identity` hook contract

### 6.2 Supported Fallback: Builder First

User-facing guidance:

- no Spark Swarm agent yet? create your agent here now and link it later

Builder behavior:

- create a local canonical agent
- allow persona creation and shaping from Telegram or future adapters
- mark the identity as Builder-local until an external link exists

### 6.3 Repair Path: Both Exist

User-facing guidance:

- we found both a Builder-created agent and a Spark Swarm agent for you
- recommended: keep the Spark Swarm identity as the main one and import your Builder setup into it

Builder behavior:

- do not silently merge by name
- detect conflict
- canonicalize to Spark Swarm
- migrate Builder persona and history
- retain alias and audit history for the local Builder identity

## 7. Conflict Cases

### 7.1 Separate Agent IDs For The Same Human

If two different agent IDs appear for the same human:

- detect `identity_conflict`
- stop automatic canonical writes
- do not merge by name alone
- choose a canonical agent according to policy
- migrate the losing identity into an alias record

Preferred rule:

- if one ID is a Builder-local provisional identity and the other is Spark Swarm-backed, keep the Spark Swarm ID as canonical

### 7.2 Name Mismatch

If local Builder and Spark Swarm names differ:

- keep the same canonical `agent_id`
- treat the mismatch as mutable metadata conflict
- resolve by latest confirmed write
- preserve both values in history

### 7.3 Persona Mismatch

If both Builder and Spark Swarm already have meaningful persona state:

- do not silently overwrite
- log a merge record or explicit migration provenance
- prefer explicit confirmation or deterministic precedence
- preserve mutation history from both sides

## 8. Rename Semantics

Renaming the agent should:

- update display metadata
- not change `agent_id`
- not reset persona
- not break session continuity
- sync across Builder and Spark Swarm when linked

## 9. Persona Semantics

### 9.1 Agent Base Persona

Defines:

- who the agent is
- its stable style and voice
- its base behavioral rules

### 9.2 Human-Specific Overlay

Defines:

- how the agent should adapt to one human
- per-human pacing, directness, warmth, playfulness, and assertiveness adjustments

### 9.3 Session-Scoped Tone

Defines:

- temporary conversational adjustments
- should not silently become canonical persona

## 10. Adapter Rule

Telegram is the first Builder-first authoring surface.

Later:

- Discord
- WhatsApp
- future chat surfaces

should use the same identity and persona contract.

Adapters must not invent separate identity rules.

Current Builder-first authoring support already exists through the bridge for:

- explicit agent rename
- explicit agent persona updates
- human-scoped style overlays
- multi-turn Telegram onboarding for first-time Builder-local users

## 11. Lifecycle States

Builder-side lifecycle states in use or reserved:

- `builder_local`
- `spark_swarm`
- `linked`
- `identity_conflict`
- `link_pending`
- `revoked`

## 12. Non-Goals

This spec does not currently authorize:

- one human owning multiple active agents in the same workspace
- automatic merge by name alone
- session-scoped style residue silently becoming canonical persona

## 13. Current Builder Status

Builder-side canonical agent identity is now shipped locally.

Completed locally:

- canonical link storage
- mutable agent profiles with name provenance
- alias preservation for superseded local ids
- rename history
- agent-scoped persona base
- human-overlay compatibility
- legacy human-overlay migration command
- operator inspect, rename, and Swarm-link surfaces
- hook-backed Swarm identity import surface
- doctor and Watchtower identity visibility
- persisted import-readiness visibility for the external `identity` hook
- explicit conflict-repair harness coverage

Remaining external dependency:

- the external Spark Swarm runtime implementing the `identity` hook contract
