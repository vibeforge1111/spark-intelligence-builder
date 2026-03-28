# Agent Identity Implementation Plan — March 28, 2026

## 1. Purpose

This plan turns the canonical agent identity policy into an implementation sequence that is clear to operators and safe to migrate.

This plan is intentionally opinionated:

- recommended path: Spark Swarm first
- supported fallback: Builder first, link later
- repair path: canonicalize to Spark Swarm and import Builder state

## 2. User-Facing Product Copy

### 2.1 Recommended Path

Use:

- `Recommended: connect your existing Spark Swarm agent first so your identity, name, and personality stay unified from the start.`

### 2.2 Supported Fallback

Use:

- `No Spark Swarm agent yet? Create your agent here now and link it later.`

### 2.3 Repair Path

Use:

- `We found both a Builder-created agent and a Spark Swarm agent for you.`
- `Recommended: keep the Spark Swarm identity as the main one and import your Builder setup into it.`

## 3. Delivery Phases

### Phase A. First-Class Canonical Agent Storage

Goal:

- stop treating `agent_id` as only a derived helper from `human_id`

Required work:

- add canonical Builder-side agent identity storage
- add mutable agent profile storage for name
- add Spark Swarm external reference storage
- add alias/tombstone support for superseded local IDs

Definition of done:

- Builder can represent:
  - local-only agent
  - Swarm-imported agent
  - linked agent
  - identity conflict

### Phase B. Agent-Scoped Persona Base

Goal:

- move canonical persona authority from human-scoped state to agent-scoped state

Required work:

- introduce agent-scoped persona base tables
- keep current human-scoped style shaping as overlays
- update runtime merge order to:
  - defaults
  - external/base chip state
  - agent persona base
  - human overlay

Definition of done:

- persona base survives rename and link operations

### Phase C. Builder-First Agent Creation Flow

Goal:

- let a Telegram-first user create the agent from chat if Spark Swarm is not connected yet

Required work:

- add Builder-side local agent creation path
- capture:
  - agent name
  - initial persona description
  - initial base traits
- record that the created identity is Builder-local

Definition of done:

- a first-time Telegram user can create a local agent and begin shaping its persona

### Phase D. Swarm Import And Linking

Goal:

- allow existing Spark Swarm users to import and use the same agent in Builder

Required work:

- import:
  - external `agent_id`
  - `agent_name`
  - optional external persona metadata
- link existing local Builder identity if present
- canonicalize to Spark Swarm identity when both exist

Definition of done:

- Builder and Spark Swarm point to one canonical agent

### Phase E. Rename And Sync Contract

Goal:

- let users rename from either side without identity breakage

Required work:

- add rename mutation logging
- add sync policy for latest confirmed name
- preserve prior names in history

Definition of done:

- rename is metadata-only and never creates a new agent

### Phase F. Conflict Handling

Goal:

- make duplicate identity creation recoverable without hidden split-brain state

Required work:

- detect separate IDs for the same human
- freeze automatic canonical writes when unresolved
- surface recommended repair path
- support alias migration after canonicalization

Definition of done:

- duplicate local-versus-Swarm identities can be repaired deterministically

## 4. Runtime Resolution Rules

### 4.1 Agent Identity Resolution

When an inbound message arrives:

1. resolve human
2. resolve canonical agent for that human
3. if linked to Spark Swarm, use linked canonical identity
4. if not linked, use Builder-local canonical identity
5. if conflict exists, avoid silent canonical persona mutation until repaired

### 4.2 Persona Resolution

Current effective persona should be built in this order:

1. default trait baseline
2. chip/evolver base profile if available
3. agent-scoped persona base
4. human-scoped overlay
5. temporary session-scoped tone hints

## 5. Telegram UX Plan

### 5.1 First-Time User With Swarm Connected

Bot behavior:

- recognize connected Spark Swarm agent
- present it as the default agent identity
- let the user continue directly into persona shaping

### 5.2 First-Time User Without Swarm

Bot behavior:

- offer local creation
- ask for:
  - agent name
  - desired voice/persona
- store a Builder-local canonical agent
- show later recommendation to connect Spark Swarm

### 5.3 User Who Created In Both Places

Bot behavior:

- explain the conflict simply
- avoid saying “two IDs” unless needed for operator tooling
- offer the recommended path:
  - keep Spark Swarm identity
  - import Builder setup

## 6. Recommended Operator Surfaces

Builder should expose:

- canonical agent status
- local-versus-Swarm link state
- current agent name
- current canonical persona base
- current human overlay
- unresolved identity conflicts
- rename history
- link history

## 7. Migration Notes

### 7.1 Current State

Current Builder personality state is effectively human-scoped.

This is acceptable only as an interim compatibility stage because Spark Swarm is currently one-human-one-agent.

### 7.2 Migration Approach

Migration should:

- create agent-scoped persona base from existing human-scoped state
- preserve current human-scoped deltas as overlays if needed
- avoid losing observation/evolution history
- log migration provenance

## 8. Edge Cases To Handle Explicitly

- local Builder creation followed by later Swarm import
- different names for the same human across Builder and Swarm
- separate agent IDs for the same human
- duplicate inbound create/link attempts
- rename in Builder while a Swarm sync is in flight
- persona edits from Telegram before linking
- reset commands that should target:
  - canonical agent persona
  - human overlay
  - adaptive drift only

## 9. Recommended Build Order

Implement in this order:

1. canonical agent tables and link tables
2. agent-scoped persona base
3. Telegram local-create flow
4. Swarm import/link flow
5. rename sync
6. conflict repair surfaces

This order keeps migration and runtime behavior understandable while preserving the recommended user path.
