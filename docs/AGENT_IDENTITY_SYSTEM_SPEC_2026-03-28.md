# Agent Identity System Spec — March 28, 2026

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

Builder must match that model.

Within one workspace, one human may own:

- one canonical agent identity

Builder may temporarily create a local-first agent before Spark Swarm is connected, but that local identity must be linkable into the same final canonical agent later.

### 3.2 Agent Name Is Mutable, Agent ID Is Not

`agent_id` is the stable identity key.

`agent_name` is user-facing metadata and may change:

- in Builder
- in Spark Swarm
- via bot-facing flows later

Renames must never create a new agent identity by themselves.

### 3.3 Persona Belongs To The Agent

The canonical base persona belongs to the agent, not the channel, not the session, and not the current display name.

This means:

- renaming the agent does not reset persona
- linking Builder and Swarm should merge into one agent persona history
- per-user style preferences must remain overlays, not the core persona

### 3.4 Builder And Swarm Are Two Front Doors To One Agent

Builder and Spark Swarm should be treated as two entry points to the same eventual agent record.

The system should never force users to choose a permanent “birthplace” for the agent.

## 4. Canonical Model

### 4.1 Canonical Agent Identity

Recommended fields:

```text
agent_id
human_id
canonical_source
status
created_at
updated_at
```

`canonical_source` values:

- `builder_local`
- `spark_swarm`
- `linked`

### 4.2 Mutable Agent Profile

Recommended fields:

```text
agent_id
agent_name
name_updated_at
name_source
created_at
updated_at
```

The display name should be stored separately from the canonical identity anchor.

### 4.3 External Agent References

Recommended fields:

```text
link_id
agent_id
external_system
external_agent_id
external_human_id
status
linked_at
last_synced_at
created_at
updated_at
```

For Spark Swarm:

- `external_system = spark_swarm`

This table is the authority for import, linking, and reconciliation.

### 4.4 Agent Persona Base

Recommended fields:

```text
agent_id
persona_name
persona_summary
traits_json
behavioral_rules_json
source
created_at
updated_at
```

The current Builder personality system stores live customization per `human_id`.

That is acceptable only as an interim model while one-human-one-agent holds.

The canonical base persona should move to `agent_id`.

### 4.5 Human-Agent Overlay

Recommended fields:

```text
agent_id
human_id
overlay_traits_json
source
created_at
updated_at
```

This captures relationship-scoped or user-specific style shaping without redefining the agent’s core persona.

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

### 5.3 Persona

Canonical base persona should be agent-scoped.

Explicit persona authoring beats passive inferred drift.

Passive evolution should remain bounded and reversible.

## 6. Recommended User Paths

### 6.1 Recommended Path: Swarm First

User-facing guidance:

- connect your existing Spark Swarm agent first so your identity, name, and personality stay unified from the start

Builder behavior:

- import `agent_id`
- import `agent_name`
- create or refresh the local external reference
- attach Builder-side persona state to the imported canonical agent

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
- migrate Builder persona/history
- retain alias and audit history for the local Builder identity

## 7. Conflict Cases

### 7.1 Separate Agent IDs For The Same Human

If two different agent IDs appear for the same human:

- detect `identity_conflict`
- stop automatic canonical writes
- do not merge by name alone
- choose a canonical agent according to policy
- migrate the losing identity into an alias/tombstone record

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
- log a merge record
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
- per-user pacing/directness/warmth adjustments

### 9.3 Session-Scoped Tone

Defines:

- temporary conversational adjustments
- should not silently become global persona

## 10. Adapter Rule

Telegram is the first Builder-first authoring surface.

Later:

- Discord
- WhatsApp
- future chat surfaces

should use the same identity and persona contract.

Adapters must not invent separate identity rules.

## 11. Recommended Internal States

Suggested lifecycle states:

- `builder_local`
- `swarm_imported`
- `linked`
- `identity_conflict`
- `link_pending`
- `revoked`

## 12. Non-Goals

This spec does not currently authorize:

- one human owning multiple active agents in the same workspace
- automatic merge by name alone
- session-scoped style residue silently becoming canonical persona

## 13. Immediate Implication For Builder

The current Builder-side human-scoped personality storage is not the correct final authority for canonical persona.

Builder should migrate toward:

- canonical agent-scoped persona base
- separate human-scoped overlays
- explicit linking to Spark Swarm external identities
